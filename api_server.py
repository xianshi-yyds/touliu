from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html import escape as escape_html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import requests

from exhibitflow_lite import avatar, pipeline, platforms, publisher, qwen, render, social, stock, tencent_ads, topic_video, tts, video_agent
from exhibitflow_lite.config import settings
from exhibitflow_lite.storage import ensure_storage, latest_manifest, manifest_path, safe_stem, write_json


FRONTEND_DIR = settings.project_root / "frontend"
TASK_DIR = settings.storage_dir / "api_tasks"
ARTIFACT_DIR = settings.storage_dir / "artifacts"
HANDOFF_DIR = settings.storage_dir / "handoffs"
BROWSER_WORKER_DIR = settings.storage_dir / "browser_workers"
MATERIAL_INDEX_FILE = settings.storage_dir / "material_index.json"
MATERIAL_THUMB_DIR = settings.storage_dir / "material_thumbs"
BGM_ROOT = settings.storage_dir / "bgms"
DIGITAL_HUMAN_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TASKS: dict[str, dict[str, Any]] = {}
TASK_LOCK = threading.Lock()
RECORD_LOCK = threading.Lock()
BROWSER_WORKER_LOCK = threading.Lock()
RESUMABLE_TASK_KINDS = {
    "search",
    "import-links",
    "download",
    "creator-pipeline",
    "customer-report",
    "copy",
    "tts",
    "render",
    "caption-style",
    "sample-analysis",
}
_DASHSCOPE_HEALTH: dict[str, Any] = {"checked_at": 0.0, "valid": False, "status": "unchecked", "checking": False}
_DASHSCOPE_HEALTH_LOCK = threading.Lock()
_TEXT_LLM_HEALTH: dict[str, Any] = {"checked_at": 0.0, "valid": False, "status": "unchecked", "checking": False}
_TEXT_LLM_HEALTH_LOCK = threading.Lock()

OCEAN_BASE_URL = "https://api.oceanengine.com"
OCEAN_TOKEN_FILE = settings.storage_dir / "oceanengine" / "oauth.json"

EMPLOYEE_IDS = {"hunter", "creator", "buyer"}
ARTIFACT_TYPES = {"sample_pack", "video_package", "delivery_plan", "delivery_report"}



def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def task_path(task_id: str) -> Path:
    """Return a portable task filename.

    Task IDs historically included the full user-facing task title.  On
    macOS that can fit in a filename while the same UTF-8 name can exceed
    Linux/ext4's 255-byte component limit.  Keep short IDs backward
    compatible and use a deterministic hash for long IDs so migrated task
    history remains readable on every filesystem.
    """
    value = str(task_id or "")
    direct = TASK_DIR / f"{value}.json"
    if len(value.encode("utf-8")) <= 180:
        return direct
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return TASK_DIR / f"task-{digest}.json"


def save_task(task: dict[str, Any]) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    write_json(task_path(task["id"]), task)


def record_path(directory: Path, record_id: str) -> Path:
    clean_id = safe_stem(record_id)
    if clean_id != record_id:
        raise ValueError("记录 ID 格式不正确")
    return directory / f"{clean_id}.json"


def load_record(directory: Path, record_id: str) -> dict[str, Any] | None:
    path = record_path(directory, record_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_record(directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(record_path(directory, str(record["id"])), record)
    return record


def list_records(directory: Path, filters: dict[str, str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    filters = {key: value for key, value in (filters or {}).items() if value}
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    paths = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        if any(str(item.get(key) or "") != value for key, value in filters.items()):
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


def require_employee(value: Any, label: str = "员工") -> str:
    employee = str(value or "").strip().lower()
    if employee not in EMPLOYEE_IDS:
        raise ValueError(f"{label}必须是 hunter、creator 或 buyer")
    return employee


def create_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    employee = require_employee(payload.get("employee"), "成果所属员工")
    artifact_type = str(payload.get("type") or "").strip().lower()
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError("成果类型必须是 sample_pack、video_package、delivery_plan 或 delivery_report")
    title = require_text(payload, "title", "成果名称")
    created_at = now()
    artifact_id = f"artifact-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{uuid4().hex[:6]}"
    record = {
        "id": artifact_id,
        "type": artifact_type,
        "employee": employee,
        "expo_id": str(payload.get("expo_id") or "").strip(),
        "title": title,
        "summary": str(payload.get("summary") or "").strip(),
        "status": str(payload.get("status") or "ready").strip().lower(),
        "version": max(1, int(payload.get("version") or 1)),
        "source_task_id": str(payload.get("source_task_id") or "").strip(),
        "source_artifact_ids": [str(item) for item in (payload.get("source_artifact_ids") or []) if str(item).strip()],
        "data": public_payload(payload.get("data") or {}),
        "created_at": created_at,
        "updated_at": created_at,
    }
    with RECORD_LOCK:
        saved = save_record(ARTIFACT_DIR, record)
    if artifact_type == "video_package" and record["source_task_id"]:
        mark_task_saved_to_video_package(record["source_task_id"], record["id"])
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        parent_task_id = str(data.get("parent_render_task_id") or "").strip()
        if parent_task_id and parent_task_id != record["source_task_id"]:
            mark_task_saved_to_video_package(parent_task_id, record["id"])
    return saved


def create_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    artifact_id = require_text(payload, "artifact_id", "待投递成果")
    with RECORD_LOCK:
        artifact = load_record(ARTIFACT_DIR, artifact_id)
        if not artifact:
            raise ValueError("待投递成果不存在")
        from_employee = require_employee(payload.get("from_employee") or artifact.get("employee"), "发送员工")
        to_employee = require_employee(payload.get("to_employee"), "接收员工")
        if from_employee == to_employee:
            raise ValueError("不能把成果投递给同一名员工")
        created_at = now()
        handoff = {
            "id": f"handoff-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{uuid4().hex[:6]}",
            "artifact_id": artifact_id,
            "artifact_type": artifact.get("type") or "",
            "artifact_title": artifact.get("title") or "",
            "from_employee": from_employee,
            "to_employee": to_employee,
            "expo_id": str(payload.get("expo_id") or artifact.get("expo_id") or "").strip(),
            "instruction": str(payload.get("instruction") or "").strip(),
            "status": "pending",
            "created_at": created_at,
            "updated_at": created_at,
        }
        return save_record(HANDOFF_DIR, handoff)


def update_handoff(handoff_id: str, action: str) -> dict[str, Any]:
    if action not in {"accept", "reject"}:
        raise ValueError("不支持的投递操作")
    with RECORD_LOCK:
        handoff = load_record(HANDOFF_DIR, handoff_id)
        if not handoff:
            raise ValueError("投递记录不存在")
        if handoff.get("status") not in {"pending", action + "ed"}:
            raise ValueError("该投递已经处理")
        timestamp = now()
        handoff["status"] = "accepted" if action == "accept" else "rejected"
        handoff[f"{action}ed_at"] = timestamp
        handoff["updated_at"] = timestamp
        save_record(HANDOFF_DIR, handoff)
        artifact = load_record(ARTIFACT_DIR, str(handoff.get("artifact_id") or ""))
    return {"ok": True, "handoff": handoff, "artifact": artifact or {}}


def load_tasks() -> list[str]:
    """Load persisted tasks and return safe jobs that should resume.

    Content-generation and retrieval jobs are safe to run again with the same
    payload. Publishing and OceanEngine creation jobs are deliberately not
    resumed automatically because replaying those requests could publish twice
    or create duplicate official projects/units.
    """
    ensure_storage()
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    pending_resume: list[str] = []
    for path in sorted(TASK_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = task.get("result") or {}
        legacy_empty_search = (
            task.get("status") == "failed"
            and task.get("kind") == "search"
            and "没有检索到可用视频" in str(task.get("error") or "")
        )
        if legacy_empty_search:
            # Older versions treated a valid zero-match crawl as an exception,
            # which left the UI showing a red failed task with a full progress
            # bar.  Normalize those historical records to the same durable
            # empty-result shape used by the current crawler.
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            platform = str(payload.get("platform") or "douyin")
            label = social.PLATFORM_LABELS.get(platform, platform)
            keyword = str(payload.get("keyword") or "")
            task["status"] = "succeeded"
            task["finished_at"] = task.get("finished_at") or now()
            task["result"] = {
                "action": "search",
                "source": "legacy_empty_result",
                "platform": platform,
                "platform_label": label,
                "query": keyword,
                "requested_limit": int(payload.get("limit") or 0),
                "count": 0,
                "actual_count": 0,
                "items": [],
                "empty_result": True,
                "warning": f"{label}检索已完成，但没有找到匹配视频。请换一个更宽泛的关键词或调整筛选条件后重试。",
            }
            task.pop("error", None)
            task.pop("traceback", None)
            save_task(task)
            result = task["result"]
        fake_success = (
            task.get("status") == "succeeded"
            and (
                result.get("ok") is False
                or result.get("source") == "missing_optional_crawler"
            )
        )
        if task.get("status") in {"queued", "running"}:
            task_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if (
                task.get("kind") == "search"
                and task_payload.get("execution") == "browser_extension"
            ):
                # The target browser, not the API process, owns this job. Do
                # not replay it on the server after a restart; leave it in a
                # durable queue for the extension to claim again.
                task["status"] = "queued"
                task["recovery_pending"] = True
                task["recovered_at"] = now()
                task["resume_count"] = int(task.get("resume_count") or 0) + 1
                task.pop("started_at", None)
                task.pop("finished_at", None)
                task.pop("error", None)
                task.pop("traceback", None)
                task.pop("worker_id", None)
                save_task(task)
            elif task.get("kind") in RESUMABLE_TASK_KINDS and isinstance(task.get("payload"), dict):
                task["status"] = "queued"
                task["recovery_pending"] = True
                task["recovered_at"] = now()
                task["resume_count"] = int(task.get("resume_count") or 0) + 1
                task.pop("started_at", None)
                task.pop("finished_at", None)
                task.pop("error", None)
                task.pop("traceback", None)
                save_task(task)
                pending_resume.append(str(task.get("id") or ""))
            else:
                task["status"] = "failed"
                task["finished_at"] = now()
                task["error"] = "服务重启时任务仍在执行。为避免重复发布或重复创建官方资产，系统未自动重放，请人工确认后重试。"
                save_task(task)
        elif fake_success:
            task["status"] = "failed"
            task["finished_at"] = task.get("finished_at") or now()
            task["error"] = result.get("log") or "任务没有实际执行成功。"
            save_task(task)
        elif "Incorrect API key" in str(task.get("error") or ""):
            task["error"] = "阿里云百炼 API Key 无效或已失效，请更新 .env 后重启服务。"
            save_task(task)
        TASKS[task["id"]] = task
    return [task_id for task_id in pending_resume if task_id]


SENSITIVE_KEYS = {"access_token", "token", "api_key", "secret", "password", "cookie"}


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in SENSITIVE_KEYS and item else public_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    return value


def browser_worker_path(worker_id: str) -> Path:
    digest = hashlib.sha256(str(worker_id).encode("utf-8")).hexdigest()
    return BROWSER_WORKER_DIR / f"{digest}.json"


def load_browser_worker(worker_id: str) -> dict[str, Any]:
    path = browser_worker_path(worker_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_browser_worker(worker: dict[str, Any]) -> None:
    BROWSER_WORKER_DIR.mkdir(parents=True, exist_ok=True)
    write_json(browser_worker_path(str(worker.get("worker_id") or "")), worker)


def browser_worker_public(worker: dict[str, Any]) -> dict[str, Any]:
    last_seen = str(worker.get("last_seen_at") or "")
    online = False
    if last_seen:
        try:
            online = (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds() <= 30
        except ValueError:
            online = False
    return {
        "worker_id": str(worker.get("worker_id") or ""),
        "name": str(worker.get("name") or "目标浏览器")[:80],
        "browser": str(worker.get("browser") or "Chrome"),
        "platform": str(worker.get("platform") or "douyin"),
        "status": "online" if online else "offline",
        "online": online,
        "first_seen_at": worker.get("first_seen_at") or "",
        "last_seen_at": last_seen,
        "last_task_id": worker.get("last_task_id") or "",
        "extension_version": worker.get("extension_version") or "",
    }


def register_browser_worker(payload: dict[str, Any]) -> dict[str, Any]:
    worker_id = require_text(payload, "worker_id", "目标浏览器标识")
    if len(worker_id) > 200:
        raise ValueError("目标浏览器标识过长")
    timestamp = now()
    with BROWSER_WORKER_LOCK:
        previous = load_browser_worker(worker_id)
        worker = {
            "worker_id": worker_id,
            "name": str(payload.get("name") or previous.get("name") or "目标浏览器")[:80],
            "browser": str(payload.get("browser") or previous.get("browser") or "Chrome")[:40],
            "platform": str(payload.get("platform") or previous.get("platform") or "douyin")[:40],
            "first_seen_at": previous.get("first_seen_at") or timestamp,
            "last_seen_at": timestamp,
            "last_task_id": previous.get("last_task_id") or "",
            "extension_version": str(payload.get("extension_version") or previous.get("extension_version") or "")[:40],
        }
        save_browser_worker(worker)
    return {"ok": True, "worker": browser_worker_public(worker)}


def claim_browser_worker_task(payload: dict[str, Any]) -> dict[str, Any]:
    worker_id = require_text(payload, "worker_id", "目标浏览器标识")
    register_browser_worker(payload)
    requested_task_id = str(payload.get("task_id") or "").strip()
    claimed: dict[str, Any] | None = None
    with TASK_LOCK:
        candidates = [TASKS.get(requested_task_id)] if requested_task_id else list(TASKS.values())
        for task in candidates:
            if not task or task.get("kind") != "search" or task.get("status") != "queued":
                continue
            task_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if task_payload.get("execution") != "browser_extension":
                continue
            if str(task_payload.get("worker_id") or "") != worker_id:
                continue
            task["status"] = "running"
            task["executor"] = "browser_extension"
            task["started_at"] = now()
            task["worker_id"] = worker_id
            save_task(task)
            claimed = dict(task)
            break
    if not claimed:
        return {"ok": True, "claimed": False, "worker": browser_worker_public(load_browser_worker(worker_id))}
    with BROWSER_WORKER_LOCK:
        worker = load_browser_worker(worker_id)
        worker["last_seen_at"] = now()
        worker["last_task_id"] = claimed["id"]
        save_browser_worker(worker)
    return {"ok": True, "claimed": True, "task": claimed, "worker": browser_worker_public(worker)}


def finish_browser_worker_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = require_text(payload, "task_id", "浏览器任务 ID")
    worker_id = require_text(payload, "worker_id", "目标浏览器标识")
    requested_status = str(payload.get("status") or "succeeded").strip().lower()
    if requested_status not in {"succeeded", "failed"}:
        raise ValueError("浏览器任务状态必须是 succeeded 或 failed")
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("浏览器任务不存在")
        assigned_worker = str(task.get("worker_id") or (task.get("payload") or {}).get("worker_id") or "")
        if assigned_worker and assigned_worker != worker_id:
            raise ValueError("该任务不属于当前目标浏览器")
        if task.get("status") not in {"queued", "running"}:
            return task
        if requested_status == "failed":
            task["status"] = "failed"
            task["finished_at"] = now()
            task["error"] = str(payload.get("error") or "目标浏览器抓取失败")[:1200]
            task["worker_id"] = worker_id
            task.pop("traceback", None)
            save_task(task)
        else:
            task_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            platform = social.canonical_platform(str(task_payload.get("platform") or "douyin"))
            keyword = str(task_payload.get("keyword") or "")
            limit = max(1, min(int(task_payload.get("limit") or 10), 50))
            raw_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            items = social.normalize_items(list(raw_result.get("items") or []), platform, limit)
            result = {
                "created_at": now(),
                "action": "search",
                "source": "browser_extension",
                "provider": "local_browser",
                "platform": platform,
                "platform_label": social.PLATFORM_LABELS.get(platform, platform),
                "query": keyword,
                "requested_limit": limit,
                "count": len(items),
                "actual_count": len(items),
                "items": items,
                "keyword_stats": raw_result.get("keyword_stats") or [],
                "log": str(raw_result.get("log") or "目标浏览器扩展已完成抓取")[:4000],
                "worker_id": worker_id,
                "empty_result": not items,
            }
            out = manifest_path("search", platform, keyword)
            result["_manifest_path"] = str(write_json(out, result))
            if not items:
                result["warning"] = (
                    f"{social.PLATFORM_LABELS.get(platform, platform)}检索已完成，但没有找到匹配视频。"
                    "请确认当前浏览器已经登录并换一个更宽泛的关键词。"
                )
            task["status"] = "succeeded"
            task["finished_at"] = now()
            task["result"] = result
            task["worker_id"] = worker_id
            task.pop("error", None)
            task.pop("traceback", None)
            save_task(task)
        finished = dict(task)
    with BROWSER_WORKER_LOCK:
        worker = load_browser_worker(worker_id)
        worker["last_seen_at"] = now()
        worker["last_task_id"] = task_id
        save_browser_worker(worker)
    return finished


def require_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"请填写{label}")
    return value


DEFAULT_VIDEO_CTA = "点击下方链接，立即报名吧"
VOICEOVER_CHARS_PER_SECOND = 3.8


def normalize_target_duration(value: Any, default: int = 30) -> int:
    try:
        seconds = int(float(value or default))
    except (TypeError, ValueError):
        seconds = default
    return max(10, min(120, seconds))


def voiceover_stats(text: str) -> dict[str, Any]:
    """Return stable metadata shared by the copy UI and generation history."""
    characters = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", str(text or "")))
    return {
        "character_count": characters,
        "estimated_duration_seconds": round(characters / VOICEOVER_CHARS_PER_SECOND, 1) if characters else 0,
    }


def final_script_with_cta(script: str, cta_text: str = DEFAULT_VIDEO_CTA) -> str:
    """Return the single source of truth used by copy, TTS, subtitles and render.

    CTA is appended server-side so a stale browser or a direct API request cannot
    accidentally create a video whose narration/subtitles omit the conversion line.
    """
    clean_script = str(script or "").strip()
    clean_cta = str(cta_text or "").strip().rstrip("。！？!?；;，,")
    if not clean_script or not clean_cta:
        return clean_script
    comparable = re.sub(r"[\s。！？!?；;，,]+", "", clean_script)
    cta_comparable = re.sub(r"[\s。！？!?；;，,]+", "", clean_cta)
    if comparable.endswith(cta_comparable):
        return clean_script
    return f"{clean_script.rstrip()}\n\n{clean_cta}。"


def synthesize_script_resilient(
    text: str,
    *,
    service: str,
    voice: str,
    output_stem: str,
) -> tuple[Path, str, str, list[dict[str, Any]], list[str]]:
    """Synthesize a script in sentence-sized segments and join its timings.

    Full-stop sentences remain intact; long sentences are split by commas by
    ``render.split_sentences``. This gives TTS providers real punctuation at
    every intended pause and lets the renderer map captions/materials to the
    same segment boundaries.
    """
    speech_segments = render.split_sentences(text)
    active_service = str(service or "qwen").strip().lower()
    active_voice = str(voice or tts.DEFAULT_QWEN_VOICE)
    stem = safe_stem(output_stem)
    sentence_audio: list[Path] = []
    attempts: list[dict[str, Any]] = []
    for index, sentence in enumerate(speech_segments, start=1):
        audio_part, used_service, used_voice, errors = tts.synthesize_resilient(
            sentence,
            service=active_service,
            voice=active_voice,
            output_name=f"{stem}-part-{index:02d}.mp3",
        )
        sentence_audio.append(audio_part)
        attempts.append({
            "sentence": index,
            "text": sentence,
            "requested_service": active_service,
            "used_service": used_service,
            "used_voice": used_voice,
            "fallback_errors": errors,
        })
        active_service = used_service
        active_voice = used_voice
    audio = tts.concat_segments(
        sentence_audio,
        output_name=f"{stem}.mp3",
        text=text,
        voice=active_voice,
    )
    return audio, active_service, active_voice, attempts, speech_segments


def media_url(path: str | Path) -> str:
    candidate = Path(path).expanduser().resolve()
    root = settings.project_root.resolve()
    if candidate != root and root not in candidate.parents:
        return ""
    return f"/media/{quote(str(candidate.relative_to(root)))}"


def project_file(value: str | Path, label: str) -> Path:
    """Resolve a media path while keeping post-processing inside this app."""
    candidate = Path(value).expanduser().resolve()
    root = settings.project_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{label}必须位于 ExhibitFlow 项目目录内")
    if not candidate.is_file():
        raise FileNotFoundError(f"{label}不存在：{candidate}")
    return candidate


def decorate_render_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Add browser-safe URLs without removing server-side paths from manifests."""
    for path_key, url_key in (
        ("final_video", "preview_url"),
        ("preview_base_video", "preview_base_url"),
        ("base_video", "base_video_url"),
        ("combined_video", "combined_video_url"),
        ("audio_file", "audio_url"),
        ("subtitle_vtt", "subtitle_preview_url"),
        ("subtitle", "subtitle_url"),
    ):
        value = str(summary.get(path_key) or "")
        if value:
            url = media_url(value)
            if url:
                summary[url_key] = url
    # A caption-only variant has a final video; a newly generated base render
    # has a preview-base video. Keep the generic preview URL stable for old UI.
    if not summary.get("preview_url") and summary.get("preview_base_url"):
        summary["preview_url"] = summary["preview_base_url"]
    return summary


CREATOR_RENDER_TASK_KINDS = {"creator-pipeline", "render", "caption-style"}


def _video_package_for_task(task_id: str, summary: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Find the video package that should lock a generated caption record.

    New artifacts carry ``source_task_id``.  The second comparison keeps old
    packages created before that field existed compatible by matching their
    stored final-video path.
    """
    task_id = str(task_id or "").strip()
    summary = summary or {}
    final_video = str(summary.get("final_video") or "").strip()
    for artifact in list_records(ARTIFACT_DIR, {"type": "video_package"}, limit=500):
        if task_id and str(artifact.get("source_task_id") or "").strip() == task_id:
            return artifact
        data = artifact.get("data") if isinstance(artifact.get("data"), dict) else {}
        if task_id and str(data.get("parent_render_task_id") or "").strip() == task_id:
            return artifact
        stored_video = str(data.get("video_file") or "").strip()
        if final_video and stored_video and stored_video == final_video:
            return artifact
    return None


def _ensure_history_preview_base(summary: dict[str, Any]) -> dict[str, Any]:
    """Backfill the clean preview base for manifests created by older builds."""
    if not isinstance(summary, dict):
        return summary
    preview = Path(str(summary.get("preview_base_video") or "")).expanduser() if summary.get("preview_base_video") else None
    if preview and preview.is_file():
        return summary
    source_value = str(summary.get("base_video") or summary.get("combined_video") or "").strip()
    audio_value = str(summary.get("audio_file") or "").strip()
    if not source_value or not audio_value:
        return summary
    source = Path(source_value).expanduser()
    audio = Path(audio_value).expanduser()
    if not source.is_file() or not audio.is_file():
        return summary
    run_dir = Path(str(summary.get("run_dir") or source.parent)).expanduser()
    target = run_dir / "preview-base.mp4"
    if target.resolve() == source.resolve():
        target = run_dir / "preview-base-repaired.mp4"
    if not target.is_file():
        try:
            total = float(summary.get("duration_seconds") or 0) or render.duration(audio) or render.duration(source) or 1.0
            render.mux_preview_video(source, audio, target, total)
        except Exception:
            return summary
    if target.is_file():
        summary["preview_base_video"] = str(target)
    return summary


def _attach_progress_eta(client_task: dict[str, Any]) -> None:
    """Derive an ETA for the delivery workbench progress card.

    A progress-derived projection (elapsed / percent) tracks a slow render more
    honestly than the static budget once the bar has moved; the budget only
    seeds the estimate while progress is still near zero.
    """
    if client_task.get("status") != "running":
        return
    started = client_task.get("started_at")
    total = int(client_task.get("estimated_total_seconds") or 0)
    if not started:
        return
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(str(started))).total_seconds()
    except (TypeError, ValueError):
        return
    progress = int(client_task.get("progress") or 0)
    if progress >= 18:
        remaining = max(0.0, elapsed / (progress / 100.0) - elapsed)
    elif total:
        remaining = max(0.0, total - elapsed)
    else:
        return
    client_task["elapsed_seconds"] = int(max(0.0, elapsed))
    client_task["estimated_remaining_seconds"] = int(remaining)


def decorate_task_for_client(task: dict[str, Any]) -> dict[str, Any]:
    """Expose durable caption-editability state for current and old tasks."""
    client_task = copy.deepcopy(task)
    _attach_progress_eta(client_task)
    if client_task.get("kind") not in CREATOR_RENDER_TASK_KINDS:
        return client_task
    result = client_task.get("result") if isinstance(client_task.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if summary:
        # A few early creator records kept the audio/script beside the result
        # wrapper instead of inside the render manifest.  Promote those fields
        # before repairing the preview base so they become editable as well.
        if not summary.get("audio_file") and result.get("audio_path"):
            summary["audio_file"] = result.get("audio_path")
        if not summary.get("script") and result.get("copy"):
            summary["script"] = result.get("copy")
        if not summary.get("caption_template"):
            summary["caption_template"] = (client_task.get("payload") or {}).get("caption_template") or "viral"
        if not summary.get("caption_animation"):
            summary["caption_animation"] = (client_task.get("payload") or {}).get("caption_animation") or "pop"
        dynamic_topic_video = summary.get("render_engine") == "remotion-topic" or summary.get("template") == "AutoPartsKinetic"
        if not dynamic_topic_video and not summary.get("caption_timeline") and summary.get("script") and summary.get("audio_file"):
            try:
                audio_path = Path(str(summary["audio_file"])).expanduser()
                raw_highlights = (client_task.get("payload") or {}).get("highlight_words") or []
                if isinstance(raw_highlights, str):
                    raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
                summary["caption_timeline"] = render.caption_timeline(
                    str(summary["script"]),
                    float(summary.get("duration_seconds") or render.duration(audio_path) or 1.0),
                    audio_path,
                    cta_text=str((client_task.get("payload") or {}).get("cta_text") or DEFAULT_VIDEO_CTA),
                    highlight_words=raw_highlights,
                )
            except Exception:
                pass
        _ensure_history_preview_base(summary)
        decorate_render_summary(summary)
        result["summary"] = summary
        client_task["result"] = result
    package = _video_package_for_task(str(client_task.get("id") or ""), summary)
    saved = bool(package)
    has_edit_source = bool(
        summary
        and summary.get("caption_timeline")
        and summary.get("audio_file")
        and summary.get("preview_base_video")
        and Path(str(summary.get("preview_base_video"))).is_file()
    )
    client_task["saved_to_video_package"] = saved
    client_task["saved_artifact_id"] = str(package.get("id") or "") if package else ""
    dynamic_topic_video = summary.get("render_engine") == "remotion-topic" or summary.get("template") == "AutoPartsKinetic"
    client_task["editable_captions"] = bool(
        not dynamic_topic_video and client_task.get("status") == "succeeded" and has_edit_source and not saved
    )
    client_task["caption_editable"] = client_task["editable_captions"]
    return client_task


def mark_task_saved_to_video_package(task_id: str, artifact_id: str) -> None:
    """Persist the lock immediately after a video package is created."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task["saved_to_video_package"] = True
        task["saved_artifact_id"] = str(artifact_id or "")
        task["editable_captions"] = False
        task["caption_editable"] = False
        task["updated_at"] = now()
        save_task(task)


def parse_render_result(log: str) -> dict[str, Any]:
    marker = "__SUMMARY_JSON__"
    if marker not in log:
        return {"log": log}
    raw = log.rsplit(marker, 1)[-1].strip()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError:
        return {"log": log}
    decorate_render_summary(summary)
    return {"summary": summary, "log": log[:4000]}


def requests_post_no_proxy(*args, **kwargs):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(*args, **kwargs)
    finally:
        session.close()


def _probe_dashscope_health() -> None:
    global _DASHSCOPE_HEALTH
    try:
        response = requests_post_no_proxy(
            settings.dashscope_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.qwen_text_model,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            },
            timeout=(3, 8),
        )
        valid = response.status_code == 200
        status = "ready" if valid else ("invalid_key" if response.status_code == 401 else f"http_{response.status_code}")
    except requests.RequestException as exc:
        valid = False
        status = f"unreachable:{exc.__class__.__name__}"
    with _DASHSCOPE_HEALTH_LOCK:
        _DASHSCOPE_HEALTH = {"checked_at": time.time(), "valid": valid, "status": status, "checking": False}


def dashscope_health() -> dict[str, Any]:
    """Return cached model health and refresh it without blocking page APIs."""
    global _DASHSCOPE_HEALTH
    if not settings.dashscope_api_key:
        return {"valid": False, "status": "missing", "checking": False}
    with _DASHSCOPE_HEALTH_LOCK:
        stale = time.time() - float(_DASHSCOPE_HEALTH.get("checked_at") or 0) >= 300
        if stale and not _DASHSCOPE_HEALTH.get("checking"):
            _DASHSCOPE_HEALTH["checking"] = True
            if _DASHSCOPE_HEALTH.get("status") == "unchecked":
                _DASHSCOPE_HEALTH["status"] = "checking"
            threading.Thread(target=_probe_dashscope_health, daemon=True, name="dashscope-health").start()
        return {key: value for key, value in _DASHSCOPE_HEALTH.items() if key != "checked_at"}


def _probe_text_llm_health() -> None:
    global _TEXT_LLM_HEALTH
    try:
        response = requests_post_no_proxy(
            settings.text_llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.text_llm_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.text_llm_model,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "enable_thinking": settings.text_llm_enable_thinking,
                "max_tokens": 1,
            },
            timeout=(3, 8),
        )
        valid = response.status_code == 200
        status = "ready" if valid else ("invalid_key" if response.status_code == 401 else f"http_{response.status_code}")
    except requests.RequestException as exc:
        valid = False
        status = f"unreachable:{exc.__class__.__name__}"
    with _TEXT_LLM_HEALTH_LOCK:
        _TEXT_LLM_HEALTH = {"checked_at": time.time(), "valid": valid, "status": status, "checking": False}


def text_llm_health() -> dict[str, Any]:
    """Return cached Qwen text-model health without blocking page APIs."""
    global _TEXT_LLM_HEALTH
    if not settings.text_llm_api_key:
        return {"valid": False, "status": "missing", "checking": False}
    with _TEXT_LLM_HEALTH_LOCK:
        stale = time.time() - float(_TEXT_LLM_HEALTH.get("checked_at") or 0) >= 300
        if stale and not _TEXT_LLM_HEALTH.get("checking"):
            _TEXT_LLM_HEALTH["checking"] = True
            if _TEXT_LLM_HEALTH.get("status") == "unchecked":
                _TEXT_LLM_HEALTH["status"] = "checking"
            threading.Thread(target=_probe_text_llm_health, daemon=True, name="text-llm-health").start()
        return {key: value for key, value in _TEXT_LLM_HEALTH.items() if key != "checked_at"}


def deepseek_health() -> dict[str, Any]:
    """Backward-compatible alias for integrations using the old function name."""
    return text_llm_health()


def public_config() -> dict[str, Any]:
    materials = render.media_files(settings.default_material_dir)
    dashscope_status = dashscope_health()
    text_health = text_llm_health()
    ocean_status = ocean_config_public()
    remotion_bin = settings.project_root / "remotion_exhibition_promo" / "node_modules" / ".bin" / "remotion"
    return {
        "project_root": str(settings.project_root),
        "storage_dir": str(settings.storage_dir),
        "material_dir": str(settings.default_material_dir),
        "crawler_configured": social.search_available(),
        "social_search": social.public_search_config(),
        "publisher_configured": publisher.sau_available(),
        "avatar": avatar.avatar_status(),
        "render_engine": settings.render_engine,
        "topic_video": {
            "configured": remotion_bin.is_file(),
            "template": "AutoPartsKinetic",
            "orientation": "landscape",
            "tts": "edge",
            "network_stock": bool(settings.pexels_api_key or settings.pixabay_api_key),
        },
        "caption_templates": {
            key: {
                "label": str(value.get("label") or key),
                "font": str(value.get("font") or ""),
            }
            for key, value in render.CAPTION_TEMPLATES.items()
        },
        "caption_animations": {
            key: {
                "label": str(value.get("label") or key),
                "description": str(value.get("description") or ""),
            }
            for key, value in render.CAPTION_ANIMATIONS.items()
        },
        "text_llm_provider": "qwen",
        "text_llm_model": settings.text_llm_model,
        "text_llm_key_present": bool(settings.text_llm_api_key),
        "text_llm_configured": bool(text_health.get("valid")),
        "text_llm_status": text_health.get("status"),
        # Legacy aliases retained for older clients.
        "deepseek_key_present": bool(settings.text_llm_api_key),
        "deepseek_configured": bool(text_health.get("valid")),
        "deepseek_status": text_health.get("status"),
        "qwen_text_model": settings.qwen_text_model,
        "qwen_vl_model": settings.qwen_vl_model,
        "qwen_tts_model": settings.qwen_tts_model,
        "dashscope_key_present": bool(settings.dashscope_api_key),
        "dashscope_configured": bool(dashscope_status.get("valid")),
        "dashscope_status": dashscope_status.get("status"),
        "material_count": len(materials),
        "online_stock_configured": bool(settings.pexels_api_key or settings.pixabay_api_key),
        "moneyprint_stock_configured": bool(settings.pexels_api_key),
        "pixabay_stock_configured": bool(settings.pixabay_api_key),
        "ffmpeg_configured": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "oceanengine": ocean_status,
        "tencent_ads": tencent_ads.public_status(),
        "platforms": platforms.public_capabilities(ocean_status),
    }


def _load_material_index() -> dict[str, Any]:
    try:
        if MATERIAL_INDEX_FILE.exists():
            raw = json.loads(MATERIAL_INDEX_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_material_index(items: dict[str, Any]) -> None:
    MATERIAL_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    MATERIAL_INDEX_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_material_tags(filename: str) -> list[str]:
    """A small, deterministic first-pass classifier for the material library.

    Users can overwrite these labels from the UI later.  Keeping this on the
    server makes the library usable before a multimodal tagging service is
    configured.
    """
    name = filename.lower()
    groups = {
        "汽车": ("汽车", "车展", "新能源", "电车", "充电", "car", "auto", "ev", "vehicle"),
        "食品": ("食品", "餐饮", "美食", "零食", "饮料", "咖啡", "food", "restaurant", "drink"),
        "科技": ("ai", "人工智能", "机器人", "芯片", "软件", "科技", "digital", "tech"),
    }
    tags = [label for label, words in groups.items() if any(word in name for word in words)]
    return tags or ["待识别"]


MATERIAL_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
BGM_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _ensure_material_thumb(path: Path, signature: str) -> str:
    """Return a media URL for a cached first-frame thumbnail of a video clip."""
    MATERIAL_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{path.name}:{signature}".encode("utf-8")).hexdigest()[:16]
    thumb = MATERIAL_THUMB_DIR / f"{digest}.jpg"
    if not thumb.is_file():
        base = [render.ffmpeg_bin(), "-y"]
        scale = ["-frames:v", "1", "-vf", "scale=320:-2", str(thumb)]
        try:
            # Grab a representative frame ~1s in; fall back to the very first
            # frame for clips shorter than the seek point.
            render.run(base + ["-ss", "1", "-i", str(path)] + scale)
        except Exception:
            try:
                render.run(base + ["-i", str(path)] + scale)
            except Exception:
                return ""
    return media_url(thumb) if thumb.is_file() else ""


def _material_media_meta(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """Resolve cached {kind, duration, thumbnail_url} for a material file.

    ffprobe/ffmpeg run only once per (name, mtime, size) signature; results are
    stashed on the passed-in index entry so the picker grid stays instant.
    """
    is_video = path.suffix.lower() in MATERIAL_VIDEO_EXTS
    stat = path.stat()
    signature = f"{int(stat.st_mtime)}:{stat.st_size}"
    cached = meta.get("media") if isinstance(meta.get("media"), dict) else {}
    if cached.get("signature") == signature and cached.get("kind"):
        return cached
    resolved = {
        "signature": signature,
        "kind": "video" if is_video else "image",
        "duration": 0.0,
        "thumbnail_url": "",
    }
    if is_video:
        try:
            resolved["duration"] = round(float(render.duration(path)) or 0.0, 2)
        except Exception:
            resolved["duration"] = 0.0
        resolved["thumbnail_url"] = _ensure_material_thumb(path, signature)
    else:
        resolved["thumbnail_url"] = media_url(path)
    meta["media"] = resolved
    return resolved


def list_materials(expo_id: str = "") -> dict[str, Any]:
    settings.default_material_dir.mkdir(parents=True, exist_ok=True)
    allowed = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
    index = _load_material_index()
    items = []
    index_dirty = False
    for path in sorted(settings.default_material_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        meta = index.get(path.name) or {}
        # Global legacy assets remain readable; materials uploaded for an
        # exhibition never leak into another exhibition's material picker.
        item_expo_id = str(meta.get("expo_id") or "")
        if expo_id and item_expo_id and item_expo_id != expo_id:
            continue
        prev_signature = (meta.get("media") or {}).get("signature") if isinstance(meta.get("media"), dict) else None
        media_meta = _material_media_meta(path, meta)
        if media_meta.get("signature") != prev_signature or path.name not in index:
            index[path.name] = meta
            index_dirty = True
        items.append({
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "expo_id": item_expo_id,
            "tags": meta.get("tags") or infer_material_tags(path.name),
            "source": meta.get("source") or "本地上传",
            "kind": media_meta.get("kind"),
            "duration": media_meta.get("duration") or 0.0,
            "thumbnail_url": media_meta.get("thumbnail_url") or "",
        })
    if index_dirty:
        try:
            _save_material_index(index)
        except OSError:
            pass
    return {"count": len(items), "items": items, "expo_id": expo_id}


def _bgm_scope(expo_id: str) -> str:
    return safe_stem(str(expo_id or "").strip()) or "global"


def list_bgms(expo_id: str = "") -> dict[str, Any]:
    """List BGM files without mixing them into the video material picker."""
    BGM_ROOT.mkdir(parents=True, exist_ok=True)
    scopes = []
    if str(expo_id or "").strip():
        scopes.append(_bgm_scope(expo_id))
    scopes.append("global")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope in scopes:
        directory = BGM_ROOT / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in BGM_EXTENSIONS:
                continue
            asset_id = path.relative_to(BGM_ROOT).as_posix()
            if asset_id in seen:
                continue
            seen.add(asset_id)
            try:
                duration = round(float(render.duration(path)) or 0.0, 2)
            except Exception:
                duration = 0.0
            items.append({
                "id": asset_id,
                "name": path.name,
                "scope": scope,
                "size": path.stat().st_size,
                "duration": duration,
                "url": media_url(path),
            })
    return {"count": len(items), "items": items, "expo_id": expo_id}


def _digital_human_scope(expo_id: str) -> str:
    return safe_stem(str(expo_id or "").strip()) or "global"


def list_digital_human_images(expo_id: str = "") -> dict[str, Any]:
    """List avatar images scoped to the current exhibition plus global images."""
    root = avatar.digital_human_image_root()
    scopes = [_digital_human_scope(expo_id)] if str(expo_id or "").strip() else []
    if "global" not in scopes:
        scopes.append("global")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope in scopes:
        directory = root / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file() or path.suffix.lower() not in DIGITAL_HUMAN_IMAGE_EXTENSIONS:
                continue
            asset_id = path.relative_to(root).as_posix()
            if asset_id in seen:
                continue
            seen.add(asset_id)
            items.append({
                "id": asset_id,
                "name": path.name,
                "scope": scope,
                "size": path.stat().st_size,
                "url": media_url(path),
            })
    return {"count": len(items), "items": items, "expo_id": expo_id}


MATERIAL_TOTAL_MIN_SECONDS = 10.0
MATERIAL_TOTAL_MAX_SECONDS = 600.0
IMAGE_NOMINAL_SECONDS = 3.0


def resolve_selected_clips(expo_id: str, selected_clips: list[Any]) -> tuple[list[str], float]:
    """Resolve ordered material picks to absolute paths and enforce total duration.

    Accepts library file names or absolute paths that already live under the
    shared library.  Stills count as a nominal duration so a photo slideshow
    still clears the lower bound.  Enforces the reference guard: total material
    duration must exceed 10s and stay within 10min.
    """
    index = _load_material_index()
    base = settings.default_material_dir.resolve()
    resolved: list[str] = []
    total = 0.0
    for raw in selected_clips or []:
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("path") or "").strip()
        else:
            name = str(raw or "").strip()
        if not name:
            continue
        candidate = Path(name).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate.name
        candidate = candidate.resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError(f"素材不在素材库内：{candidate.name}")
        if not candidate.is_file():
            raise FileNotFoundError(f"素材不存在：{candidate.name}")
        owner = str((index.get(candidate.name) or {}).get("expo_id") or "")
        if expo_id and owner and owner != expo_id:
            raise ValueError(f"素材属于其它展会：{candidate.name}")
        if candidate.suffix.lower() in MATERIAL_VIDEO_EXTS:
            total += float(render.duration(candidate) or 0.0)
        else:
            total += IMAGE_NOMINAL_SECONDS
        resolved.append(str(candidate))
    if not resolved:
        raise ValueError("请至少选择一个素材片段")
    total = round(total, 2)
    if total < MATERIAL_TOTAL_MIN_SECONDS:
        raise ValueError(
            f"所有素材总时长需超过 {int(MATERIAL_TOTAL_MIN_SECONDS)} 秒（当前约 {total:.0f} 秒），请再添加素材"
        )
    if total > MATERIAL_TOTAL_MAX_SECONDS:
        raise ValueError(
            f"所有素材总时长不超过 {int(MATERIAL_TOTAL_MAX_SECONDS / 60)} 分钟（当前约 {total / 60:.1f} 分钟），请精简素材"
        )
    return resolved, total


def scoped_material_dir(expo_id: str, requested_dir: str) -> Path:
    """Build an immutable material view for one exhibition video task.

    Legacy files without an exhibition id are treated as shared/public assets;
    files owned by another exhibition are excluded.  A per-task hard-link view
    avoids modifying the source library and keeps a running render stable even
    if users upload more files in another browser.
    """
    source_dir = Path(requested_dir).expanduser().resolve()
    default_dir = settings.default_material_dir.resolve()
    if not expo_id or source_dir != default_dir:
        return source_dir
    index = _load_material_index()
    allowed = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
    target_dir = settings.storage_dir / "scoped_materials" / safe_stem(expo_id) / uuid4().hex[:12]
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in source_dir.iterdir():
        if not source.is_file() or source.suffix.lower() not in allowed:
            continue
        owner = str((index.get(source.name) or {}).get("expo_id") or "")
        if owner and owner != expo_id:
            continue
        target = target_dir / source.name
        try:
            target.hardlink_to(source)
        except OSError:
            shutil.copy2(source, target)
    if not any(target_dir.iterdir()):
        raise RuntimeError("当前展会素材库为空，请先上传图片或视频素材。")
    return target_dir


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rate(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100 if denominator > 0 else 0.0


def build_delivery_dashboard(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize Ocean Engine report metrics and calculate user-facing indicators."""
    raw = raw or {}
    stat_cost = _number(raw.get("stat_cost", raw.get("cost")))
    show_cnt = _number(raw.get("show_cnt", raw.get("impressions")))
    click_cnt = _number(raw.get("click_cnt", raw.get("clicks")))
    convert_cnt = _number(raw.get("convert_cnt", raw.get("conversions")))
    # BASIC_DATA 的官方字段名是 total_play / valid_play。兼容旧版内部字段，
    # 但不再因为字段名不一致把有效播放率错误显示为 0。
    play_cnt = _number(raw.get("total_play", raw.get("play_cnt", raw.get("plays"))))
    valid_play_cnt = _number(raw.get("valid_play", raw.get("valid_play_cnt", raw.get("valid_plays"))))

    cpm = _number(raw.get("cpm_platform"))
    ctr = _number(raw.get("ctr"))
    avg_convert_cost = _number(raw.get("conversion_cost"))
    convert_rate = _number(raw.get("conversion_rate"))
    valid_play_rate = _number(raw.get("valid_play_rate"))

    return {
        "stat_cost": round(stat_cost, 2),
        "show_cnt": int(show_cnt),
        "cpm": round(cpm if cpm else (stat_cost / show_cnt * 1000 if show_cnt > 0 else 0.0), 2),
        "click_cnt": int(click_cnt),
        "ctr": round(ctr if ctr else _rate(click_cnt, show_cnt), 2),
        "avg_convert_cost": round(
            avg_convert_cost if avg_convert_cost else (stat_cost / convert_cnt if convert_cnt > 0 else 0.0), 2
        ),
        "convert_rate": round(convert_rate if convert_rate else _rate(convert_cnt, click_cnt), 2),
        "convert_cnt": int(convert_cnt),
        "valid_play_rate": round(valid_play_rate if valid_play_rate else _rate(valid_play_cnt, play_cnt), 2),
        # Keep the source counts for report reconciliation and later drill-down views.
        "play_cnt": int(play_cnt),
        "valid_play_cnt": int(valid_play_cnt),
    }



def ocean_config_public() -> dict[str, Any]:
    token = load_ocean_token()
    advertiser_id = token.get("advertiser_id") or settings.oceanengine_advertiser_id
    binding_verified = bool(
        token.get("binding_verified")
        and advertiser_id
        and str(token.get("binding_advertiser_id") or "") == str(advertiser_id)
    )
    return {
        "app_id_configured": bool(settings.oceanengine_app_id),
        "secret_configured": bool(settings.oceanengine_secret),
        "redirect_uri": settings.oceanengine_redirect_uri,
        "redirect_uri_valid": ocean_redirect_uri_valid(),
        "has_access_token": bool(token.get("access_token") or settings.oceanengine_access_token),
        "advertiser_id": advertiser_id,
        "advertiser_name": token.get("advertiser_name") or "",
        "account_type": token.get("account_type") or "",
        "account_role": token.get("account_role") or "",
        "advertiser_role": token.get("advertiser_role") or "",
        "workbench_account_id": token.get("workbench_account_id") or "",
        "workbench_account_name": token.get("workbench_account_name") or "",
        "last_authorized_at": token.get("authorized_at") or "",
        "binding_verified": binding_verified,
        "binding_verified_at": token.get("binding_verified_at") or "",
        "delivery_asset_verified": bool(token.get("delivery_asset_verified")),
    }


def ocean_redirect_uri_valid() -> bool:
    """The OAuth provider must return to the API callback, not the frontend root."""
    try:
        parsed = urllib.parse.urlparse(settings.oceanengine_redirect_uri)
    except ValueError:
        return False
    path = (parsed.path or "").rstrip("/")
    return parsed.scheme in {"http", "https"} and path.endswith("/api/oceanengine/callback")


def load_ocean_token() -> dict[str, Any]:
    try:
        if OCEAN_TOKEN_FILE.exists():
            data = json.loads(OCEAN_TOKEN_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def save_ocean_token(data: dict[str, Any]) -> dict[str, Any]:
    OCEAN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = load_ocean_token()
    current.update({k: v for k, v in data.items() if v not in (None, "")})
    current["updated_at"] = now()
    write_json(OCEAN_TOKEN_FILE, current)
    return current


def ocean_access_token(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    return str(
        payload.get("access_token")
        or load_ocean_token().get("access_token")
        or settings.oceanengine_access_token
        or ""
    ).strip()


def ocean_app_id() -> str:
    return settings.oceanengine_app_id.strip()


def ocean_secret() -> str:
    return settings.oceanengine_secret.strip()


def ocean_request(
    path: str,
    *,
    method: str = "GET",
    access_token: str = "",
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
    retry_on_expired: bool = True,
) -> dict[str, Any]:
    if path.startswith("http"):
        url = path
    else:
        url = OCEAN_BASE_URL + (path if path.startswith("/") else f"/{path}")
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    if params:
        # 巨量官方 GET 示例要求：数组/对象作为 JSON 字符串放入 query。
        query = urllib.parse.urlencode({
            k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            for k, v in params.items()
        })
        url += ("&" if "?" in url else "?") + query
    request_body = None
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Access-Token"] = access_token
    if body is not None:
        request_body = body
        method = method.upper() if method else "POST"
    last_network_error: requests.RequestException | None = None
    raw = ""
    result: dict[str, Any] = {}
    for attempt in range(5):
        try:
            resp = requests.request(method.upper(), url, headers=headers, json=request_body, timeout=timeout)
            raw = resp.text
            if resp.status_code >= 400:
                try:
                    parsed = resp.json()
                except Exception:
                    parsed = {"message": raw}
                raise RuntimeError(f"巨量接口 HTTP {resp.status_code}: {parsed}")
            last_network_error = None
            break
        except requests.RequestException as exc:
            last_network_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if last_network_error is not None:
        raise RuntimeError(f"巨量接口网络错误：{last_network_error}") from last_network_error
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"巨量接口返回非 JSON：{raw[:500]}") from exc
    if isinstance(result, dict) and result.get("code") not in (None, 0, "0"):
        # access_token 过期时自动刷新并重试一次，避免用户频繁重新授权。
        if str(result.get("code")) == "40102" and access_token and retry_on_expired:
            refreshed = ocean_refresh_token("")
            new_token = ((refreshed.get("token") or {}).get("access_token") or load_ocean_token().get("access_token") or "")
            if new_token and new_token != access_token:
                return ocean_request(
                    path,
                    method=method,
                    access_token=new_token,
                    params=params,
                    body=body,
                    timeout=timeout,
                    retry_on_expired=False,
                )
        raise RuntimeError(f"巨量接口错误：{result}")
    return result


def ocean_auth_url(state: str = "exhibitflow") -> str:
    app_id = ocean_app_id()
    redirect_uri = settings.oceanengine_redirect_uri
    if not app_id:
        raise ValueError("缺少 OCEANENGINE_APP_ID，请先在 .env 配置巨量应用 APP_ID")
    if not ocean_redirect_uri_valid():
        raise ValueError(
            "巨量回调地址配置错误：必须指向 /api/oceanengine/callback，"
            "不能只填写网站根地址。"
        )
    # 巨量授权页参数在不同应用类型间可能略有差异；redirect_uri 必须和开放平台应用后台一致。
    return "https://ad.oceanengine.com/openapi/audit/oauth.html?" + urllib.parse.urlencode(
        {"app_id": app_id, "redirect_uri": redirect_uri, "state": state}
    )


def ocean_callback_result(query: dict[str, list[str]]) -> dict[str, Any]:
    auth_code = (query.get("auth_code") or query.get("code") or [""])[0]
    if not auth_code:
        return {"ok": False, "error": "巨量回调缺少 auth_code/code"}
    try:
        result = ocean_exchange_token(auth_code)
        # 授权回调完成后立即同步账户，用户无需回到前端再填写或复制任何字段。
        try:
            accounts = ocean_advertisers({})
            result["account_sync"] = {
                "ok": True,
                "account_count": accounts.get("count", 0),
                "advertiser_count": accounts.get("advertiser_count", 0),
            }
        except Exception as exc:
            # Token 已经保存；账户同步可由前端状态轮询自动重试。
            result["account_sync"] = {"ok": False, "error": str(exc)}
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ocean_callback_html(result: dict[str, Any]) -> bytes:
    ok = bool(result.get("ok"))
    title = "巨量授权成功" if ok else "巨量授权失败"
    hint = "账户信息正在同步，本页面会自动关闭。" if ok else "请返回 ExhibitFlow 后重新发起绑定。"
    color = "#2f6b1d" if ok else "#9d2525"
    body = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f7fb;margin:0;padding:32px;color:#181b24">
  <main style="max-width:520px;margin:12vh auto 0;background:#fff;border:1px solid #dfe3ec;border-radius:18px;padding:36px;text-align:center;box-shadow:0 12px 30px rgba(20,24,35,.08)">
    <div style="width:52px;height:52px;border-radius:50%;display:grid;place-items:center;margin:0 auto 18px;background:{color};color:#fff;font-size:26px">{'✓' if ok else '!'}</div>
    <h1 style="margin:0 0 10px">{title}</h1>
    <p style="font-size:16px;color:#667085">{hint}</p>
  </main>
  <script>
    if (window.opener) window.opener.postMessage({{type:'ocean-auth-complete', ok:{str(ok).lower()}}}, window.location.origin);
    if ({str(ok).lower()}) setTimeout(() => window.close(), 1200);
  </script>
</body>
</html>"""
    return body.encode("utf-8")


def ocean_exchange_token(auth_code: str) -> dict[str, Any]:
    if not ocean_app_id() or not ocean_secret():
        raise ValueError("缺少 OCEANENGINE_APP_ID 或 OCEANENGINE_SECRET，请先在 .env 配置")
    result = ocean_request(
        "/open_api/oauth2/access_token/",
        method="POST",
        body={
            "app_id": ocean_app_id(),
            "secret": ocean_secret(),
            "auth_code": auth_code,
        },
    )
    data = result.get("data") or result
    token = save_ocean_token({
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "authorized_at": now(),
        # 新授权不能沿用上一次授权对应的广告主，必须重新从本次 Token 验证。
        "advertiser_id": "",
        "advertiser_name": "",
        "account_type": "",
        "account_role": "",
        "advertiser_role": "",
        "binding_verified": False,
        "binding_advertiser_id": "",
        "binding_verified_at": "",
        "delivery_asset_verified": False,
    })
    return {"ok": True, "token": public_payload(token), "raw": public_payload(result)}


def ocean_refresh_token(refresh_token: str = "") -> dict[str, Any]:
    refresh_token = refresh_token or load_ocean_token().get("refresh_token") or settings.oceanengine_refresh_token
    if not refresh_token:
        raise ValueError("缺少 refresh_token，请先完成巨量授权")
    result = ocean_request(
        "/open_api/oauth2/refresh_token/",
        method="POST",
        body={
            "app_id": ocean_app_id(),
            "secret": ocean_secret(),
            "refresh_token": refresh_token,
        },
    )
    data = result.get("data") or result
    token = save_ocean_token({
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_in": data.get("expires_in"),
        "authorized_at": now(),
    })
    return {"ok": True, "token": public_payload(token), "raw": public_payload(result)}


def ocean_advertisers(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    token = ocean_access_token(payload)
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    params = {}
    if ocean_app_id():
        params["app_id"] = ocean_app_id()
    if ocean_secret():
        params["secret"] = ocean_secret()
    result = ocean_request("/open_api/oauth2/advertiser/get/", access_token=token, params=params)
    items = ((result.get("data") or {}).get("list") or []) if isinstance(result, dict) else []
    advertiser_items = [
        item for item in items
        if str(item.get("account_type") or item.get("account_role") or "").upper() in {"ADVERTISER", "AD"}
    ]
    chosen = advertiser_items[0] if advertiser_items else {}
    if chosen:
        save_ocean_token({
            "access_token": token,
            "advertiser_id": chosen.get("advertiser_id") or chosen.get("account_id") or chosen.get("id"),
            "advertiser_name": chosen.get("advertiser_name") or chosen.get("account_name") or chosen.get("name"),
            "account_type": chosen.get("account_type"),
            "account_role": chosen.get("account_role"),
            "advertiser_role": chosen.get("advertiser_role"),
            "advertisers": items,
        })
    elif items:
        # 授权接口可能只返回“升级版巨量引擎工作台账户”。这不是投放广告主，
        # 不能覆盖已经识别出的真实 advertiser_id，只记录为管理/工作台账户。
        manager = items[0]
        save_ocean_token({
            "access_token": token,
            "workbench_account_id": manager.get("account_id") or manager.get("advertiser_id") or manager.get("id"),
            "workbench_account_name": manager.get("account_name") or manager.get("advertiser_name") or manager.get("name"),
            "manager_account_type": manager.get("account_type"),
            "manager_account_role": manager.get("account_role"),
            "manager_advertiser_role": manager.get("advertiser_role"),
            "advertisers": items,
        })
    return {"ok": True, "count": len(items), "advertiser_count": len(advertiser_items), "items": items, "raw": result}


def ocean_is_advertiser_account(item: dict[str, Any]) -> bool:
    marker = " ".join(
        str(item.get(key) or "").upper()
        for key in ("account_type", "account_role", "role", "account_category")
    )
    # 巨量的账号类型在不同版本/应用类型下返回值不完全一致，这里只把明确的投放账户视为可用。
    return any(token in marker for token in ("ADVERTISER", "AD_ACCOUNT", "ADVERTISER_ACCOUNT")) or marker in {"AD", "ADVERTISER"}


def ocean_permission_guide(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a beginner-friendly OAuth/ad-account readiness diagnosis.

    This endpoint is intentionally product-facing: the user should not need to
    understand access_token, advertiser_id, account_role, or report API errors.
    """
    payload = payload or {}
    # 每次检测都重新确认，避免旧 Token/旧广告主残留造成“假绑定”。
    save_ocean_token({"binding_verified": False, "delivery_asset_verified": False})
    status = ocean_config_public()
    steps: list[dict[str, Any]] = []

    def step(key: str, title: str, state: str, detail: str, action: str = "") -> None:
        steps.append({"key": key, "title": title, "state": state, "detail": detail, "action": action})

    app_ready = bool(
        settings.oceanengine_app_id
        and settings.oceanengine_secret
        and settings.oceanengine_redirect_uri
        and ocean_redirect_uri_valid()
    )
    if not app_ready:
        detail = (
            "后台还没有配置巨量 APP_ID、Secret 或回调地址。"
            if ocean_redirect_uri_valid()
            else "回调地址必须是完整的 /api/oceanengine/callback 地址，不能只填根地址。"
        )
        step("app", "系统应用配置", "failed", detail, "联系平台管理员配置开放平台应用")
        return {
            "ok": True,
            "ready": False,
            "stage": "needs_operator_config",
            "title": "系统还没接好巨量应用",
            "summary": "普通用户无需处理；这是平台后台配置问题。",
            "primary_action": "联系平台管理员",
            "status": status,
            "steps": steps,
        }

    step("app", "系统应用配置", "succeeded", f"巨量应用已配置，回调地址：{settings.oceanengine_redirect_uri}")

    token = ocean_access_token(payload)
    if not token:
        step("oauth", "用户授权", "running", "还没有完成巨量账号授权。", "点击“开始授权”")
        step("account", "广告主账户", "queued", "授权完成后系统会自动识别可投放账户。")
        step("report", "投放数据", "queued", "识别到广告主账户后才能拉取数据。")
        return {
            "ok": True,
            "ready": False,
            "stage": "needs_auth",
            "title": "请先授权巨量账号",
            "summary": "你只需要点击授权；Token 和广告主 ID 会由系统自动保存。",
            "primary_action": "开始授权",
            "status": status,
            "steps": steps,
        }

    step("oauth", "用户授权", "succeeded", "已拿到巨量授权 Token，用户无需手动填写 Token。")

    try:
        adv = ocean_advertisers(payload)
    except Exception as exc:
        step("account", "广告主账户", "failed", f"授权账户读取失败：{exc}", "重新授权，或确认授权时勾选了广告账户")
        return {
            "ok": True,
            "ready": False,
            "stage": "account_fetch_failed",
            "title": "授权成功，但账户读取失败",
            "summary": "请重新授权或检查应用权限。",
            "primary_action": "重新授权",
            "status": ocean_config_public(),
            "steps": steps,
        }

    items = adv.get("items") or []
    advertiser_items = [item for item in items if isinstance(item, dict) and ocean_is_advertiser_account(item)]
    saved = load_ocean_token()
    if not advertiser_items and saved.get("advertiser_id") and ocean_is_advertiser_account(saved):
        saved_item = {
            "advertiser_id": saved.get("advertiser_id"),
            "advertiser_name": saved.get("advertiser_name") or saved.get("advertiser_id"),
            "account_type": saved.get("account_type") or "ADVERTISER",
            "account_role": saved.get("account_role") or "ADVERTISER",
            "advertiser_role": saved.get("advertiser_role") or "ADVERTISER",
        }
        items = [saved_item]
        advertiser_items = [saved_item]
    if not items:
        step("account", "广告主账户", "failed", "当前授权下没有返回任何广告账户。", "在巨量后台开通广告主账户，再重新授权")
        step("report", "投放数据", "queued", "需要先有广告主账户。")
        return {
            "ok": True,
            "ready": False,
            "stage": "needs_ad_account",
            "title": "没有找到广告主账户",
            "summary": "这不是你填错了字段，而是当前巨量账号下面没有可投放广告主。",
            "primary_action": "去巨量后台开通/绑定广告主",
            "status": ocean_config_public(),
            "steps": steps,
            "accounts": public_payload(items),
        }

    if not advertiser_items:
        current = items[0]
        role = " / ".join(str(current.get(k) or "") for k in ("account_type", "account_role", "advertiser_role") if current.get(k))
        step("account", "广告主账户", "failed", f"已授权到账户，但角色是“{role or '未知角色'}”，不是可投放广告主账户。", "在巨量后台把真实广告主账户共享/授权给当前应用")
        step("report", "投放数据", "queued", "当前角色无法查询消耗、展示、点击和转化。")
        return {
            "ok": True,
            "ready": False,
            "stage": "wrong_account_role",
            "title": "授权到了管理账号，不是投放账号",
            "summary": "请把真正的广告主账户授权给应用；完成后再点一次授权。",
            "primary_action": "按引导开通/授权广告主",
            "status": ocean_config_public(),
            "steps": steps,
            "accounts": public_payload(items),
            "guide": [
                "进入巨量广告投放后台，确认该主体下存在“广告主/广告账户”。",
                "进入巨量开放平台应用后台，把广告主账户共享或授权给当前应用。",
                "重新回到本页点击“开始授权”，授权时选择广告主账户。",
            ],
        }

    chosen = advertiser_items[0]
    chosen_advertiser_id = chosen.get("advertiser_id") or chosen.get("account_id")
    save_ocean_token({
        "binding_verified": True,
        "binding_advertiser_id": chosen_advertiser_id,
        "binding_verified_at": now(),
    })
    step("account", "广告主账户", "succeeded", f"已识别可投放广告主：{chosen.get('advertiser_name') or chosen.get('account_name') or chosen_advertiser_id}")

    # 自动检查默认“销售线索 + 橙子落地页 + 表单”链路需要的业务资产。
    # OAuth 只能授权访问，不能凭空创建用户尚未拥有的落地页或优化目标。
    try:
        goals_result = ocean_request(
            "/open_api/v3.0/event_manager/optimized_goal/get_v2/",
            method="GET",
            access_token=token,
            params={
                "advertiser_id": int(chosen_advertiser_id),
                "landing_type": "LINK",
                "ad_type": "ALL",
                "asset_type": "ORANGE",
                "marketing_goal": "VIDEO_AND_IMAGE",
                "delivery_mode": "MANUAL",
                "delivery_type": "NORMAL",
            },
        )
        goals = (goals_result.get("data") or {}).get("goals") or []
        has_form_goal = any(item.get("external_action") == "AD_CONVERT_TYPE_FORM" for item in goals)
        sites_result = ocean_request(
            "/open_api/v3.0/tools/orange_site/get/",
            method="GET",
            access_token=token,
            params={
                "advertiser_id": int(chosen_advertiser_id),
                "page": 1,
                "page_size": 50,
                "status": "SITE_ONLINE",
                "optimize_goal": {"external_action": "AD_CONVERT_TYPE_FORM"},
            },
        )
        sites = (sites_result.get("data") or {}).get("list") or []
        if not has_form_goal or not sites:
            step("report", "投放资产", "failed", "账户已绑定，但缺少可用的表单优化目标或已上线落地页。", "前往巨量完善投放资产")
            return {
                "ok": True,
                "ready": False,
                "account_bound": True,
                "stage": "needs_delivery_asset",
                "title": "账户已绑定，投放资产待完善",
                "summary": "系统没有要求你填写参数，但巨量账户中需要至少有一个可用落地页。",
                "primary_action": "前往巨量",
                "status": ocean_config_public(),
                "steps": steps,
            }
        save_ocean_token({"delivery_asset_verified": True, "default_orange_site_url": sites[0].get("url") or ""})
    except Exception as exc:
        step("report", "投放资产", "failed", f"投放资产检查失败：{exc}", "稍后重试")
        return {
            "ok": True,
            "ready": False,
            "account_bound": True,
            "stage": "delivery_asset_probe_failed",
            "title": "账户已绑定，资产检查暂未通过",
            "summary": "系统会继续自动检查，无需手动填写技术参数。",
            "primary_action": "刷新状态",
            "status": ocean_config_public(),
            "steps": steps,
        }

    # Light report probe. We only use it as readiness diagnosis; no spend is required.
    try:
        report = ocean_report({
            "advertiser_id": chosen.get("advertiser_id") or chosen.get("account_id"),
            "access_token": token,
            "start_date": datetime.now().strftime("%Y-%m-%d"),
            "end_date": datetime.now().strftime("%Y-%m-%d"),
        })
        save_ocean_token({
            "binding_verified": True,
            "binding_advertiser_id": chosen_advertiser_id,
            "binding_verified_at": now(),
        })
        step("report", "投放数据", "succeeded", "报表接口已连通；没有投放时指标会显示为 0。")
        return {
            "ok": True,
            "ready": True,
            "stage": "ready",
            "title": "投放链路已准备好",
            "summary": "现在可以上传视频、创建项目/推广，并拉取 9 项投放指标。",
            "primary_action": "开始创建投放",
            "status": ocean_config_public(),
            "steps": steps,
            "accounts": public_payload(items),
            "report_probe": public_payload(report),
        }
    except Exception as exc:
        step("report", "投放数据", "failed", f"广告主已识别，但报表权限未通过：{exc}", "确认应用申请了数据报表权限")
        return {
            "ok": True,
            "ready": False,
            "stage": "needs_report_permission",
            "title": "广告主已识别，但报表权限还没通",
            "summary": "需要给应用开通数据报表权限；否则无法展示消耗、展示、点击、转化。",
            "primary_action": "检查报表权限",
            "status": ocean_config_public(),
            "steps": steps,
            "accounts": public_payload(items),
        }


def ocean_report(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    advertiser_id = str(payload.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id or "").strip()
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not advertiser_id:
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    start_date = str(payload.get("start_date") or payload.get("start_time") or datetime.now().strftime("%Y-%m-%d"))[:10]
    end_date = str(payload.get("end_date") or payload.get("end_time") or start_date)[:10]
    # 这些字段均来自当前广告主 BASIC_DATA 配置，正好覆盖投放看板的 9 项指标。
    metrics = payload.get("metrics") or [
        "stat_cost",
        "show_cnt",
        "cpm_platform",
        "click_cnt",
        "ctr",
        "conversion_cost",
        "conversion_rate",
        "convert_cnt",
        "total_play",
        "valid_play",
        "valid_play_rate",
    ]
    dimensions = payload.get("dimensions") or ["stat_time_day"]
    report_params = {
        "advertiser_id": int(advertiser_id) if str(advertiser_id).isdigit() else advertiser_id,
        "data_topic": payload.get("data_topic") or "BASIC_DATA",
        "start_time": payload.get("start_time") or f"{start_date} 00:00:00",
        "end_time": payload.get("end_time") or f"{end_date} 23:59:59",
        "metrics": metrics,
        "dimensions": dimensions,
        "order_by": payload.get("order_by") or [{"field": metrics[0], "type": "DESC"}],
        "page": int(payload.get("page") or 1),
        "page_size": int(payload.get("page_size") or 20),
        # 巨量 v3.0 自定义报表要求 filters 必传；无筛选时传空数组。
        "filters": payload.get("filters") if "filters" in payload else [],
    }
    # 官方文档：GET，数组/对象用 JSON 字符串放入 query。
    result = ocean_request("/open_api/v3.0/report/custom/get/", method="GET", access_token=token, params=report_params)
    data = result.get("data") or {}
    rows = data.get("rows") or data.get("list") or []
    totals: dict[str, float] = {}
    total_metrics = data.get("total_metrics") or {}
    if isinstance(total_metrics, dict) and total_metrics:
        for key in metrics:
            totals[key] = _number(total_metrics.get(key))
    else:
        for row in rows:
            metrics_map = row.get("metrics") if isinstance(row, dict) else None
            source = metrics_map if isinstance(metrics_map, dict) else row
            if not isinstance(source, dict):
                continue
            for key in metrics:
                totals[key] = totals.get(key, 0.0) + _number(source.get(key))
    dashboard = build_delivery_dashboard(totals)
    manifest = {
        "ok": True,
        "created_at": now(),
        "advertiser_id": advertiser_id,
        "start_date": start_date,
        "end_date": end_date,
        "metrics": metrics,
        "dimensions": dimensions,
        "dashboard": dashboard,
        "row_count": len(rows),
        "raw": result,
    }
    out = settings.storage_dir / "manifests" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-delivery-report-{safe_stem(advertiser_id)}.json"
    manifest["_manifest_path"] = str(write_json(out, manifest))
    return manifest


def ocean_report_config(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    advertiser_id = str(payload.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id or "").strip()
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not advertiser_id:
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    params = {
        "advertiser_id": int(advertiser_id) if advertiser_id.isdigit() else advertiser_id,
        "data_topics": payload.get("data_topics") or ["BASIC_DATA"],
    }
    return ocean_request("/open_api/v3.0/report/custom/config/get/", method="GET", access_token=token, params=params)


OCEAN_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def resolve_ocean_video_file(payload: dict[str, Any]) -> tuple[Path, str]:
    """Resolve an upload from a trusted creator task, with a legacy path fallback.

    The creator-page button sends only ``source_task_id``.  This prevents a
    browser caller from turning the upload endpoint into an arbitrary local
    file reader.  The explicit ``video_file`` fallback remains for the older
    buyer workbench, but is constrained to this project by ``project_file``.
    """
    source_task_id = str(payload.get("source_task_id") or "").strip()
    path_value = ""
    if source_task_id:
        with TASK_LOCK:
            source_task = copy.deepcopy(TASKS.get(source_task_id) or {})
        if not source_task:
            raise ValueError("找不到对应的成片任务，请刷新页面后重试")
        if source_task.get("status") != "succeeded" or source_task.get("kind") not in CREATOR_RENDER_TASK_KINDS:
            raise ValueError("只有已完成的生视频任务可以上传到巨量素材库")
        result = source_task.get("result") if isinstance(source_task.get("result"), dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        path_value = str(summary.get("final_video") or "").strip()
        if not path_value:
            raise ValueError("该任务没有可上传的最终成片")
    else:
        path_value = require_text(payload, "video_file", "视频文件")

    path = project_file(path_value, "视频文件")
    if path.suffix.lower() not in OCEAN_VIDEO_EXTENSIONS:
        raise ValueError(f"巨量视频上传不支持该格式：{path.suffix or '未知格式'}")
    if path.stat().st_size <= 0:
        raise ValueError("视频文件为空，无法上传")
    return path, source_task_id


def file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def ocean_video_upload(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    advertiser_id = str(payload.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id or "").strip()
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not advertiser_id:
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    path, source_task_id = resolve_ocean_video_file(payload)
    signature = file_md5(path)
    requested_name = Path(str(payload.get("filename") or path.name)).name
    upload_name = f"{safe_stem(Path(requested_name).stem)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{path.suffix.lower()}"
    url = OCEAN_BASE_URL + "/open_api/2/file/video/ad/"
    result: dict[str, Any] = {}
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            with path.open("rb") as video_handle:
                response = requests.post(
                    url,
                    headers={"Access-Token": token},
                    data={
                        "advertiser_id": advertiser_id,
                        "upload_type": "UPLOAD_BY_FILE",
                        "video_signature": signature,
                        "filename": upload_name,
                        "is_aigc": "true" if payload.get("is_aigc", True) else "false",
                    },
                    files={
                        "video_file": (
                            upload_name,
                            video_handle,
                            mimetypes.guess_type(path.name)[0] or "video/mp4",
                        )
                    },
                    timeout=(20, 300),
                )
            if response.status_code >= 400:
                try:
                    detail: Any = response.json()
                except ValueError:
                    detail = response.text[:500]
                raise RuntimeError(f"巨量视频上传 HTTP {response.status_code}: {detail}")
            result = response.json()
            last_error = None
            if result.get("code") in (40100, "40100") and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if last_error is not None:
        raise RuntimeError(f"巨量视频上传网络错误（已重试 3 次）：{last_error}") from last_error
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"巨量视频上传失败：{result}")
    data = result.get("data") or {}
    video_info = data.get("video_info") if isinstance(data.get("video_info"), dict) else {}
    video_id = data.get("video_id") or video_info.get("video_id") or data.get("id")
    material_id = data.get("material_id") or video_info.get("material_id")
    if not video_id:
        raise RuntimeError(f"巨量视频上传返回成功，但未返回 video_id：{result}")
    save_ocean_token({"last_video_id": video_id, "last_material_id": material_id})
    upload_record = {
        "ok": True,
        "provider": "oceanengine",
        "advertiser_id": advertiser_id,
        "source_task_id": source_task_id,
        "video_file": str(path),
        "filename": upload_name,
        "video_signature": signature,
        "video_id": video_id,
        "material_id": material_id,
        "uploaded_at": now(),
    }
    upload_dir = settings.storage_dir / "oceanengine" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    record_path = upload_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_stem(str(video_id or signature[:12]))}.json"
    upload_record["manifest_path"] = str(write_json(record_path, upload_record))
    return {**upload_record, "raw": result, **data}


def ocean_image_upload(payload: dict[str, Any]) -> dict[str, Any]:
    """Upload an advertising image and keep only its material identifier locally."""
    token = ocean_access_token(payload)
    advertiser_id = str(
        payload.get("advertiser_id")
        or load_ocean_token().get("advertiser_id")
        or settings.oceanengine_advertiser_id
        or ""
    ).strip()
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not advertiser_id:
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    image_file = require_text(payload, "image_file", "图片文件")
    path = Path(image_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片文件不存在：{path}")
    content = path.read_bytes()
    signature = hashlib.md5(content).hexdigest()
    boundary = "----ExhibitFlow" + uuid4().hex

    def field(name: str, value: Any) -> bytes:
        return (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode("utf-8")

    body = b"".join([
        field("advertiser_id", advertiser_id),
        field("upload_type", "UPLOAD_BY_FILE"),
        field("image_signature", signature),
        field("filename", payload.get("filename") or path.name),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image_file"; filename="{path.name}"\r\n'
            f"Content-Type: {mimetypes.guess_type(path.name)[0] or 'image/jpeg'}\r\n\r\n"
        ).encode("utf-8"),
        content,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ])
    url = OCEAN_BASE_URL + "/open_api/2/file/image/ad/"
    raw = ""
    last_error: Exception | None = None
    result: dict[str, Any] = {}
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Access-Token": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
            last_error = None
            result = json.loads(raw)
            if result.get("code") in (40100, "40100") and attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"巨量图片上传 HTTP {exc.code}: {raw}") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2 * (attempt + 1))
    if last_error is not None:
        raise RuntimeError(f"巨量图片上传网络错误（已重试 5 次）：{last_error}") from last_error
    result = result or json.loads(raw)
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"巨量图片上传失败：{result}")
    data = result.get("data") or {}
    store_key = str(payload.get("store_key") or "last_image_id")
    if store_key not in {"last_image_id", "last_video_cover_id"}:
        store_key = "last_image_id"
    save_ocean_token({store_key: data.get("id") or data.get("image_id")})
    return {"ok": True, "image_signature": signature, "raw": result, **data}


def ocean_project_create(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    body = payload.get("official_json") if isinstance(payload.get("official_json"), dict) else dict(payload)
    body.pop("access_token", None)
    body.pop("official_json", None)
    body["advertiser_id"] = body.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not body.get("advertiser_id"):
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    result = ocean_request("/open_api/v3.0/project/create/", method="POST", access_token=token, body=body)
    data = result.get("data") or {}
    if data.get("project_id"):
        save_ocean_token({"last_project_id": data.get("project_id")})
    return {"ok": True, "raw": result, **data}


def ocean_promotion_create(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    body = payload.get("official_json") if isinstance(payload.get("official_json"), dict) else dict(payload)
    body.pop("access_token", None)
    body.pop("official_json", None)
    body["advertiser_id"] = body.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id
    body["project_id"] = body.get("project_id") or load_ocean_token().get("last_project_id")
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not body.get("advertiser_id"):
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    if not body.get("project_id"):
        raise ValueError("缺少 project_id，请先创建项目或填写项目 ID")
    result = ocean_request("/open_api/v3.0/promotion/create/", method="POST", access_token=token, body=body)
    data = result.get("data") or {}
    if data.get("promotion_id"):
        save_ocean_token({"last_promotion_id": data.get("promotion_id")})
    return {"ok": True, "raw": result, **data}



def upsert_delivery_project_unit(draft: dict[str, Any]) -> dict[str, Any]:
    """Maintain a local mirror of the official Project -> Units relationship.

    Official IDs are kept when available: project_id for the project and
    promotion_id/unit_id for each video unit. This mirror lets the UI show a
    project page with multiple video units before/after official creation.
    """
    model = draft.get("official_model") or {}
    project = dict(model.get("project") or {})
    unit = dict(model.get("unit") or {})
    project_key = str(project.get("project_id") or project.get("name") or "展会投放项目")
    channel = str(
        draft.get("delivery_channel")
        or (draft.get("summary") or {}).get("delivery_channel")
        or "douyin"
    ).strip().lower()
    base = (
        settings.storage_dir / "oceanengine" / "projects"
        if channel == "douyin"
        else settings.storage_dir / "delivery" / safe_stem(channel) / "projects"
    )
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{safe_stem(project_key)}.json"
    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            record = {}
    else:
        record = {}
    record.setdefault("created_at", now())
    record["updated_at"] = now()
    record["project"] = {**(record.get("project") or {}), **project}
    units = record.get("units") if isinstance(record.get("units"), list) else []
    unit_key = str(unit.get("unit_id") or unit.get("promotion_id") or unit.get("video_file") or unit.get("name") or uuid4().hex)
    unit_record = {
        **unit,
        "unit_key": unit_key,
        "dashboard": draft.get("dashboard") or {},
        "summary": draft.get("summary") or {},
        "updated_at": now(),
    }
    replaced = False
    for idx, item in enumerate(units):
        item_key = str(item.get("unit_key") or item.get("unit_id") or item.get("promotion_id") or item.get("video_file") or "")
        if item_key == unit_key:
            units[idx] = {**item, **unit_record}
            replaced = True
            break
    if not replaced:
        units.append(unit_record)
    record["units"] = units
    write_json(path, record)
    return {"path": str(path), "project": record.get("project") or {}, "units": units}


def list_delivery_project_units() -> dict[str, Any]:
    base = settings.storage_dir / "oceanengine" / "projects"
    items: list[dict[str, Any]] = []
    if base.exists():
        for path in sorted(base.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                record["_path"] = str(path)
                items.append(record)
            except Exception:
                continue
    return {"ok": True, "count": len(items), "items": items}


def delivery_draft(payload: dict[str, Any]) -> dict[str, Any]:
    delivery_channel = str(payload.get("delivery_channel") or "douyin").strip().lower()
    if delivery_channel not in {"douyin", "xiaohongshu", "weixin_channels"}:
        raise ValueError("不支持的投放渠道")
    channel_label = {"douyin": "抖音", "xiaohongshu": "小红书", "weixin_channels": "视频号"}[delivery_channel]
    saved = load_ocean_token()
    advertiser_id = str(
        payload.get("advertiser_id")
        or saved.get("advertiser_id")
        or settings.oceanengine_advertiser_id
        or ""
    ).strip()
    access_token = ocean_access_token(payload)
    landing_url = str(payload.get("landing_url") or saved.get("default_orange_site_url") or "").strip()
    convert_id = str(payload.get("convert_id") or saved.get("default_convert_id") or "").strip()
    budget = str(payload.get("budget") or "300").strip()
    video_file = str(payload.get("video_file") or payload.get("video_id") or "").strip()
    # A local project/unit draft can be built before OAuth or platform review
    # finishes. Account authorization and audit approval are publish gates,
    # not prerequisites for recording the operator's strategy draft.
    required_state = {
        "landing_url": landing_url,
        "video_file": video_file,
        "budget": budget,
    }
    missing = [name for name, value in required_state.items() if not value]
    video_audit = str(payload.get("video_audit") or "").strip().lower()
    landing_audit = str(payload.get("landing_audit") or "").strip().lower()
    checks = [
        {"key": "auth", "label": f"{channel_label}渠道授权", "ok": bool(advertiser_id and access_token) if delivery_channel == "douyin" else False},
        {"key": "landing", "label": "落地页 / 留资页", "ok": bool(landing_url)},
        {"key": "video_audit", "label": "视频审核通过", "ok": not video_audit or video_audit == "passed"},
        {"key": "landing_audit", "label": "落地页审核通过", "ok": not landing_audit or landing_audit == "passed"},
        {"key": "convert", "label": "转化目标自动匹配", "ok": bool(convert_id or saved.get("delivery_asset_verified"))},
        {"key": "video", "label": "投放视频", "ok": bool(video_file)},
        {"key": "budget", "label": "日预算", "ok": bool(budget)},
    ]
    project_name = payload.get("project_name") or payload.get("subject") or "展会投放项目"
    unit_name = payload.get("unit_name") or Path(video_file).stem or "视频投放单元"
    # A new draft must not silently inherit IDs from an unrelated previous
    # launch. Official IDs are attached only when this draft explicitly
    # supplies them or after the official create APIs return them.
    project_id = str(payload.get("project_id") or "").strip()
    unit_id = str(payload.get("unit_id") or payload.get("promotion_id") or "").strip()
    video_id = str(payload.get("video_id") or "").strip()
    configuration_complete = not missing
    publish_ready = bool(
        delivery_channel == "douyin"
        and
        configuration_complete
        and advertiser_id
        and access_token
        and (not video_audit or video_audit == "passed")
        and (not landing_audit or landing_audit == "passed")
    )
    official_created = delivery_channel == "douyin" and bool(project_id and unit_id and video_id)
    official_model = {
        "model": "project_with_units",
        "delivery_channel": delivery_channel,
        "project": {
            "name": project_name,
            "project_id": project_id,
            "source": "official_project" if project_id and delivery_channel == "douyin" else "local_draft_project",
            "description": "已创建官方投放项目。" if project_id and delivery_channel == "douyin" else "本地项目草稿，尚未提交对应渠道官方创建。",
        },
        "unit": {
            "name": unit_name,
            "unit_id": unit_id,
            "promotion_id": unit_id,
            "video_file": video_file,
            "video_id": video_id,
            "source": "official_unit" if unit_id and delivery_channel == "douyin" else "local_draft_unit",
            "description": "已创建官方投放单元。" if unit_id and delivery_channel == "douyin" else "本地视频单元草稿，尚未提交对应渠道官方创建。",
        },
        "report_scope": "unit_first_project_summary",
    }
    draft = {
        "created_at": now(),
        "ok": configuration_complete,
        "status": "official_created" if official_created else ("draft_ready" if configuration_complete else "needs_config"),
        "configuration_complete": configuration_complete,
        "publish_ready": publish_ready,
        "official_created": official_created,
        "delivery_channel": delivery_channel,
        "missing": missing,
        "checks": checks,
        "official_model": official_model,
        "summary": {
            "project_name": project_name,
            "project_id": project_id,
            "unit_name": unit_name,
            "unit_id": unit_id,
            "promotion_id": unit_id,
            "subject": payload.get("subject") or "",
            "selling_points": payload.get("selling_points") or "",
            "budget": budget,
            "landing_url": landing_url,
            "convert_id": convert_id,
            "video_id": video_id,
            "video_file": video_file,
            "goal": payload.get("goal") or "lead",
            "delivery_channel": delivery_channel,
            "regions": payload.get("regions") or "",
            "age": payload.get("age") or "",
            "schedule": payload.get("schedule") or "",
            "audience_package": payload.get("audience_package") or "",
            "bid_range": payload.get("bid_range") or "",
            "cta": payload.get("cta") or "",
            "cover_title": payload.get("cover_title") or "",
            "video_audit": video_audit,
            "landing_audit": landing_audit,
        },
        "workbench": public_payload(payload.get("workbench") or {}),
        "dashboard": build_delivery_dashboard(payload.get("dashboard")),
        "recommendation": (
            f"{channel_label}官方项目和单元已创建，可按单元回流数据后汇总项目。"
            if official_created
            else (
                f"{channel_label}项目和单元草稿已保存；该渠道的官方创建接口仍需对应平台权限。"
                if delivery_channel != "douyin"
                else "项目和单元草稿已保存；账号与审核门禁全部通过后，可以创建关闭状态的官方安全草稿。"
                if not publish_ready
                else "本地参数检查已通过；下一步可以执行关闭状态的官方安全草稿。"
            )
        ),
        "next_steps": [
            f"检查{channel_label}渠道授权",
            "上传视频素材并回填 video_id",
            "确认或创建官方投放项目 project",
            "在项目下创建该视频对应的官方投放单元并查询单元报表",
        ],
    }
    out = settings.storage_dir / "manifests" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-delivery-draft-{safe_stem(draft['summary']['project_name'])}.json"
    project_record = upsert_delivery_project_unit(draft)
    draft["official_project_record"] = project_record
    draft["_manifest_path"] = str(write_json(out, draft))
    return draft


def ocean_safe_delivery_test(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the official real-API chain with project/promotion forced DISABLE.

    The helper script performs a second read-back verification of opt_status.
    This endpoint therefore proves upload/create/report connectivity without
    enabling delivery or creating spend.
    """
    delivery_channel = str(payload.get("delivery_channel") or "douyin").strip().lower()
    if delivery_channel != "douyin":
        raise RuntimeError("当前安全草稿实链仅支持抖音巨量引擎；其他渠道先保存投放方案并完成对应官方授权")
    video_file = require_text(payload, "video_file", "待投放视频")
    video = Path(video_file).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"待投放视频不存在：{video}")
    script = settings.project_root / "scripts" / "test_oceanengine_minimal_chain.py"
    cmd = [sys.executable, str(script), "--video", str(video)]
    if payload.get("fresh", True):
        cmd.append("--fresh")
    process = subprocess.run(
        cmd,
        cwd=str(settings.project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stdout.strip() or "安全投放测试失败")
    decoder = json.JSONDecoder()
    try:
        result, _ = decoder.raw_decode(process.stdout.lstrip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"安全投放测试已执行，但结果无法解析：{process.stdout[-2000:]}") from exc
    result["log"] = process.stdout[-4000:]
    return result


# --- Cooperative progress + cancellation for background jobs -----------------
# Jobs are plain closures with no handle to their own task id.  run_job binds
# the running task id to this thread-local so a job can stream progress and poll
# for a cancel request without threading the id through every call site.
_CURRENT_JOB = threading.local()

# Rough per-kind wall-clock budgets (seconds) used to seed the ETA for the
# delivery workbench progress card.  Real elapsed time refines it at read time.
TASK_DURATION_BUDGET = {
    "creator-pipeline": 660,
    "customer-report": 35,
    "render": 240,
    "caption-style": 90,
    "tts": 45,
    "copy": 20,
    "search": 40,
    "sample-analysis": 45,
    "ocean-video-upload": 300,
}

# Monotonic (percent, stage-label) checkpoints for the creator pipeline so the
# right-column status card advances through readable steps that mirror the
# reference "正在匹配画面文案…" copy.
CREATOR_STAGE_SCRIPT = (5, "解析脚本")
CREATOR_STAGE_VOICE = (18, "生成配音")
CREATOR_STAGE_MATCH = (42, "正在匹配画面文案")
CREATOR_STAGE_COMPOSE = (68, "合成视频")
CREATOR_STAGE_FINALIZE = (92, "整理成片")


class TaskCancelled(Exception):
    """Raised inside a job when the user requested cancellation."""


def report_progress(progress: int | None = None, stage: str | None = None) -> None:
    """Stream a monotonic progress/stage update for the running job (if any)."""
    task_id = getattr(_CURRENT_JOB, "task_id", "")
    if not task_id:
        return
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        if progress is not None:
            current = int(task.get("progress") or 0)
            # Never rewind: a late checkpoint keeps the bar moving forward and
            # 100 stays reserved for a confirmed success.
            task["progress"] = max(0, min(99, max(current, int(progress))))
        if stage is not None:
            task["stage"] = str(stage)
        task["updated_at"] = now()
        save_task(task)


def check_cancel() -> None:
    """Abort the running job if the user requested cancellation."""
    task_id = getattr(_CURRENT_JOB, "task_id", "")
    if not task_id:
        return
    with TASK_LOCK:
        task = TASKS.get(task_id)
        cancelled = bool(task and task.get("cancel_requested"))
    if cancelled:
        raise TaskCancelled("任务已被取消")


def run_job(task_id: str, fn: Callable[[], Any]) -> None:
    _CURRENT_JOB.task_id = task_id
    with TASK_LOCK:
        task = TASKS[task_id]
        task["status"] = "running"
        task["started_at"] = now()
        task["progress"] = int(task.get("progress") or 0)
        task["stage"] = task.get("stage") or "准备中"
        save_task(task)
    try:
        result = fn()
        if isinstance(result, dict) and result.get("ok") is False:
            missing = result.get("missing") or []
            detail = f"，缺少：{', '.join(missing)}" if missing else ""
            raise RuntimeError(f"任务未执行成功{detail}")
        with TASK_LOCK:
            task = TASKS[task_id]
            task["status"] = "succeeded"
            task["finished_at"] = now()
            task["progress"] = 100
            task["stage"] = "已完成"
            task["result"] = result
            save_task(task)
    except TaskCancelled as exc:
        with TASK_LOCK:
            task = TASKS[task_id]
            task["status"] = "cancelled"
            task["finished_at"] = now()
            task["stage"] = "已取消"
            task["error"] = str(exc)
            save_task(task)
    except Exception as exc:
        error = str(exc)
        if "Incorrect API key" in error or "invalid_api_key" in error:
            error = "阿里云百炼 API Key 无效或已失效，请更新 .env 后重启服务。"
        with TASK_LOCK:
            task = TASKS[task_id]
            task["status"] = "failed"
            task["finished_at"] = now()
            task["stage"] = "生成失败"
            task["error"] = error
            task["traceback"] = traceback.format_exc()
            save_task(task)
    finally:
        _CURRENT_JOB.task_id = ""


def request_task_cancel(task_id: str) -> dict[str, Any]:
    """Flag a running/queued task for cooperative cancellation.

    The job polls check_cancel() between stages, so cancellation lands at the
    next stage boundary rather than killing an in-flight ffmpeg mid-encode.
    """
    task_id = str(task_id or "").strip()
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"ok": False, "error": "task not found"}
        status = str(task.get("status") or "")
        if status in {"succeeded", "failed", "cancelled"}:
            return {"ok": False, "error": "任务已结束，无法取消", "status": status}
        task["cancel_requested"] = True
        task["stage"] = "正在取消…"
        task["updated_at"] = now()
        save_task(task)
    return {"ok": True, "status": status, "cancel_requested": True}


def employee_for_task(kind: str) -> str:
    if kind in {"search", "import-links", "download", "sample-analysis"}:
        return "hunter"
    if kind in {"customer-report", "copy", "tts", "render", "caption-style", "creator-pipeline"}:
        return "creator"
    if kind in {
        "publish",
        "delivery-draft",
        "delivery-report",
        "ocean-video-upload",
        "ocean-image-upload",
        "ocean-project-create",
        "ocean-promotion-create",
        "ocean-safe-launch",
    }:
        return "buyer"
    return ""


def enqueue(
    kind: str,
    payload: dict[str, Any],
    fn: Callable[[], Any] | None,
    *,
    existing_task_id: str = "",
    executor: str = "server",
    start: bool = True,
) -> dict[str, Any]:
    # Business copy belongs in payload/title, never in a filesystem-backed id.
    # Long Chinese topics previously exceeded ext4's filename component limit
    # before the task could even be queued.
    task_id = existing_task_id or f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{kind}-{uuid4().hex[:10]}"
    with TASK_LOCK:
        previous = TASKS.get(task_id) or {}
        task = {
            "id": task_id,
            "kind": kind,
            "employee": employee_for_task(kind),
            "expo_id": str(payload.get("expo_id") or "").strip(),
            "source_artifact_ids": [str(item) for item in (payload.get("source_artifact_ids") or []) if str(item).strip()],
            "status": "queued",
            "executor": executor,
            "created_at": previous.get("created_at") or now(),
            "progress": int(previous.get("progress") or 0),
            "stage": previous.get("stage") or "排队中",
            "cancel_requested": False,
            "estimated_total_seconds": TASK_DURATION_BUDGET.get(kind, 0)
            + (
                900
                if kind == "creator-pipeline"
                and (
                    payload.get("supplement_avatar")
                    or str(payload.get("production_mode") or "").strip().lower() in {"avatar", "hybrid"}
                )
                else 0
            ),
            "payload": public_payload(payload),
        }
        if existing_task_id:
            task["recovered_at"] = previous.get("recovered_at") or now()
            task["resume_count"] = int(previous.get("resume_count") or 1)
            task["recovery_pending"] = False
        TASKS[task_id] = task
        save_task(task)
    if start and fn is not None:
        thread = threading.Thread(target=run_job, args=(task_id, fn), daemon=True)
        thread.start()
    return task


def make_task(kind: str, payload: dict[str, Any], *, existing_task_id: str = "") -> dict[str, Any]:
    def submit(fn: Callable[[], Any]) -> dict[str, Any]:
        return enqueue(kind, payload, fn, existing_task_id=existing_task_id)

    if kind == "search":
        platform = payload.get("platform") or "douyin"
        keyword = require_text(payload, "keyword", "检索关键词")
        limit = int(payload.get("limit") or 10)
        if not 1 <= limit <= 50:
            raise ValueError("检索数量必须在 1 到 50 之间")
        deep = bool(payload.get("deep"))
        execution = str(payload.get("execution") or payload.get("search_mode") or "server").strip().lower()
        if execution in {"browser", "browser_extension", "local_browser", "target_host"}:
            if social.canonical_platform(str(platform)) != "douyin":
                raise ValueError("目标浏览器抓取当前只支持抖音")
            worker_id = require_text(payload, "worker_id", "目标浏览器标识")
            external_payload = dict(payload)
            external_payload["execution"] = "browser_extension"
            external_payload["provider"] = "local_browser"
            external_payload["worker_id"] = worker_id
            return enqueue(
                kind,
                external_payload,
                None,
                existing_task_id=existing_task_id,
                executor="browser_extension",
                start=False,
            )
        provider_override = ""
        if execution in {"tikhub", "managed", "server_api"}:
            provider_override = "tikhub"
        elif execution == "rnote":
            provider_override = "rnote"
        if provider_override and social.provider_for(platform, provider_override) != provider_override:
            label = social.PLATFORM_LABELS.get(str(platform), str(platform))
            if provider_override == "rnote":
                raise RuntimeError(f"{label} Rnote 检索未配置或不可用，请检查 RNOTE_API_KEY。")
            raise RuntimeError(f"{label} TikHub 检索未配置或不可用，请检查 TIKHUB_API_KEY。")
        if not provider_override and not social.search_available(platform):
            label = social.PLATFORM_LABELS.get(str(platform), str(platform))
            raise RuntimeError(f"{label}真实平台检索未配置。请配置 TikHub API，或安装本地抓取插件。")
        return submit(lambda: social.search(platform, keyword, limit, deep=deep, provider_override=provider_override))
    if kind == "import-links":
        platform = payload.get("platform") or "douyin"
        keyword = require_text(payload, "keyword", "样本主题")
        links = require_text(payload, "links", "至少一个视频链接")
        return submit(lambda: render.import_links(platform, keyword, links))
    if kind == "sample-analysis":
        platform = str(payload.get("platform") or "douyin").strip()
        keyword = str(payload.get("keyword") or "未命名检索").strip()
        raw_samples = payload.get("samples") or []
        if not isinstance(raw_samples, list) or not raw_samples:
            raise ValueError("请先选择至少一条参考样本")
        samples = [item for item in raw_samples if isinstance(item, dict)][:8]
        if not samples:
            raise ValueError("参考样本格式无效")

        def sample_analysis_job() -> dict[str, Any]:
            report_progress(12, "整理参考案例")
            check_cancel()
            logic = qwen.generate_viral_logic(samples, topic=keyword, platform=platform)
            check_cancel()
            report_progress(72, "生成爆款逻辑时间线")
            source_task_id = str(getattr(_CURRENT_JOB, "task_id", "") or "")
            timeline = logic.get("timeline") if isinstance(logic, dict) else []
            artifact = create_artifact({
                "employee": "hunter",
                "type": "sample_pack",
                "title": f"{keyword} · 爆款逻辑",
                "summary": f"{len(samples)} 条参考案例 · 已生成结构时间线",
                "source_task_id": source_task_id,
                "data": {
                    "keyword": keyword,
                    "platform": platform,
                    "samples": samples,
                    "viral_logic": logic,
                    "timeline_json": timeline,
                },
            })
            report_progress(92, "保存爆款逻辑成果")
            return {
                "count": len(samples),
                "artifact_id": artifact.get("id", ""),
                "viral_logic": logic,
                "timeline_json": timeline,
            }

        return submit(sample_analysis_job)
    if kind == "download":
        if not public_config()["crawler_configured"]:
            raise RuntimeError("下载能力未配置，请先安装抓取插件。")
        platform = payload.get("platform") or "douyin"
        keyword = payload.get("keyword") or ""
        urls = payload.get("urls") or []
        limit = int(payload.get("limit") or len(urls) or 1)
        return submit(lambda: social.download_selected(platform, keyword, urls, limit=limit))
    if kind == "customer-report":
        exhibition_name = require_text(payload, "exhibition_name", "展会名称")
        exhibition_category = str(payload.get("exhibition_category") or "").strip()
        exhibition_context = payload.get("exhibition_context")
        if not isinstance(exhibition_context, dict):
            exhibition_context = {}

        def customer_report_job() -> dict[str, Any]:
            report_progress(15, "读取展会档案")
            check_cancel()
            report_progress(38, "分析目标客户与决策痛点")
            report = qwen.generate_client_report(
                exhibition_name,
                exhibition_category,
                exhibition_context=exhibition_context,
            )
            check_cancel()
            report_progress(88, "整理客户报告")
            return {
                "report": report,
                "exhibition_name": exhibition_name,
                "exhibition_category": exhibition_category,
                "video_started": False,
            }

        return submit(customer_report_job)
    if kind == "creator-pipeline":
        topic = require_text(payload, "topic", "视频需求")
        sample = payload.get("sample") or {}
        target_duration_seconds = normalize_target_duration(payload.get("target_duration_seconds"))
        creative_direction = str(payload.get("creative_direction") or "").strip()
        source = str(payload.get("material_source") or "local").strip().lower()
        if source not in {"local", "pexels", "pixabay"}:
            raise ValueError("素材来源必须是 local、pexels 或 pixabay")
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)
        voice = str(payload.get("voice") or tts.DEFAULT_QWEN_VOICE)
        tts_service = str(payload.get("tts_service") or "qwen")
        reference_logic = payload.get("reference_logic") or payload.get("viral_logic") or {}
        caption_template = str(payload.get("caption_template") or "viral").strip().lower()
        if caption_template not in render.CAPTION_TEMPLATES:
            raise ValueError(f"未知字幕模板：{caption_template}")
        caption_animation = str(payload.get("caption_animation") or "pop").strip().lower()
        if caption_animation not in render.CAPTION_ANIMATIONS:
            raise ValueError(f"未知字幕动画：{caption_animation}")
        video_project_id = str(payload.get("video_project_id") or f"video-{uuid4().hex[:12]}").strip()
        payload["video_project_id"] = video_project_id
        # Ordered clip selection is validated synchronously so an out-of-bounds
        # total surfaces as a 400 instead of a failed background task.
        raw_selected_clips = payload.get("selected_clips") or []
        if source == "local" and raw_selected_clips:
            resolved_clips, selected_total = resolve_selected_clips(
                str(payload.get("expo_id") or "").strip(), raw_selected_clips
            )
            payload["selected_clips_resolved"] = resolved_clips
            payload["selected_clips_total_seconds"] = selected_total
        def creator_pipeline_job() -> dict[str, Any]:
            # 兼容前端的 manual_script 和直接 API 调用的 script。
            # 之前这里只读取 script，导致用户已经填写手动文案时仍然调用文本模型，
            # 也会让手动文案链路误报为模型生成失败。
            report_progress(*CREATOR_STAGE_SCRIPT)
            manual_script = str(payload.get("script") or payload.get("manual_script") or "").strip()
            dynamic_engine = str(
                payload.get("render_engine") or payload.get("video_template") or payload.get("template") or ""
            ).strip().lower()
            dynamic_mode = dynamic_engine in {"remotion-topic", "remotion", "autoparts-kinetic", "kinetic-exhibition"}
            agent_mode = str(
                payload.get("agent_mode") or ("internal-skill" if dynamic_mode else "legacy")
            ).strip().lower()
            copy_context = "\n".join(
                str(value).strip()
                for value in (
                    topic,
                    payload.get("background_context"),
                    payload.get("theme_text"),
                )
                if str(value or "").strip()
            )
            skill_plan: dict[str, Any] = {}
            if dynamic_mode and agent_mode in {"internal-skill", "internal", "skill"}:
                # The internal Agent is the only planner in this mode. It
                # reads the project Skill, returns a constrained VideoPlan,
                # and leaves the actual media work to the Remotion adapter.
                skill_payload = dict(payload)
                skill_payload["topic"] = topic
                skill_payload["target_duration_seconds"] = target_duration_seconds
                skill_plan = video_agent.generate_video_plan(skill_payload)
                payload["skill_plan"] = skill_plan
                payload["agent"] = skill_plan.get("agent") or {}
                generated_script = manual_script or str(skill_plan.get("voiceover") or "").strip()
            elif manual_script:
                generated_script = manual_script
            elif dynamic_mode:
                # The topic renderer has a deterministic theme-document
                # fallback when no text-model key is configured. Keep that
                # fallback available instead of failing before the renderer
                # receives the uploaded theme.
                try:
                    generated_script = qwen.generate_copy(
                        copy_context,
                        sample,
                        target_duration_seconds=target_duration_seconds,
                        creative_direction=creative_direction,
                        reference_logic=reference_logic if isinstance(reference_logic, dict) else None,
                    )
                except Exception:
                    generated_script = ""
            else:
                generated_script = qwen.generate_copy(
                    copy_context,
                    sample,
                    target_duration_seconds=target_duration_seconds,
                    creative_direction=creative_direction,
                    reference_logic=reference_logic if isinstance(reference_logic, dict) else None,
                )
            if not generated_script and dynamic_mode:
                generated_script = str(skill_plan.get("voiceover") or "").strip()
            script = final_script_with_cta(generated_script, cta_text)
            if skill_plan:
                # Store the exact script that went into TTS, including the
                # configured CTA, so the persisted plan remains auditable.
                skill_plan["voiceover"] = script
                payload["skill_plan"] = skill_plan
            if dynamic_engine in {"remotion-topic", "remotion", "autoparts-kinetic", "kinetic-exhibition"}:
                # Keep the existing creator-pipeline task contract so old
                # history, retry, cancellation and delivery handoff code keep
                # working. Only the renderer behind this explicit mode changes.
                def topic_progress(value: int, stage: str) -> None:
                    report_progress(value, stage)
                    check_cancel()

                topic_payload = dict(payload)
                topic_payload["topic"] = str(payload.get("event_name") or topic or "展会推广").strip()
                topic_payload["script"] = script
                topic_payload["cta_text"] = cta_text
                topic_payload["video_project_id"] = video_project_id
                if skill_plan:
                    topic_payload["screen_copy"] = skill_plan.get("screen_copy") or []
                    topic_payload["visual_plan"] = skill_plan.get("visual_plan") or {}
                    topic_payload["skill_plan"] = skill_plan
                    topic_payload["agent"] = skill_plan.get("agent") or {}
                topic_result = topic_video.generate_topic_video(topic_payload, progress=topic_progress)
                check_cancel()
                report_progress(98, "整理动态主题成片")
                final_video = Path(str(topic_result.get("final_video") or "")).resolve()
                audio_path = Path(str(topic_result.get("audio_path") or "")).resolve()
                if not final_video.is_file():
                    raise FileNotFoundError(f"动态主题成片不存在：{final_video}")
                manifest_data = topic_result.get("manifest_data") if isinstance(topic_result.get("manifest_data"), dict) else {}
                tts_info = manifest_data.get("tts") if isinstance(manifest_data.get("tts"), dict) else {}
                summary = {
                    "final_video": str(final_video),
                    "preview_url": media_url(final_video),
                    "preview_base_url": media_url(final_video),
                    "preview_base_video": str(final_video),
                    "base_video": str(final_video),
                    "combined_video": str(final_video),
                    "audio_file": str(audio_path),
                    "audio_preview_url": media_url(audio_path),
                    "video_project_id": video_project_id,
                    "template": topic_result.get("template") or "AutoPartsKinetic",
                    "render_engine": "remotion-topic",
                    "agent_mode": topic_result.get("agent_mode") or agent_mode,
                    "agent": manifest_data.get("agent") or payload.get("agent") or {},
                    "skill": manifest_data.get("skill") or {},
                    "video_plan": topic_result.get("video_plan") or "",
                    "duration_seconds": topic_result.get("duration_seconds") or 0,
                    "aspect_ratio": manifest_data.get("aspectRatio") or payload.get("aspect_ratio") or "16:9",
                    "font_style": manifest_data.get("fontStyle") or payload.get("font_style") or "impact",
                    "transition": manifest_data.get("transition") or payload.get("transition") or "cut",
                    "bgm": manifest_data.get("bgm") or None,
                    "digital_human": manifest_data.get("digitalHuman") or {"enabled": False, "status": "disabled"},
                    "production_plan": manifest_data.get("productionPlan") or {
                        "requested": payload.get("production_mode") or "auto",
                        "resolved": "montage",
                    },
                    "script": script,
                    "voiceover_script": manifest_data.get("voiceoverScript") or script,
                    "screen_copy": manifest_data.get("screenCopy") or [],
                    "background_context": manifest_data.get("backgroundContext") or payload.get("background_context") or "",
                    "manifest": topic_result.get("manifest") or "",
                    "summary_markdown": topic_result.get("summary_markdown") or "",
                    "manifest_url": media_url(topic_result.get("manifest") or ""),
                    "summary_markdown_url": media_url(topic_result.get("summary_markdown") or ""),
                    "network_search": topic_result.get("material") or {},
                    "kinetic_text_animation": [
                        "bottom-pop",
                        "character-spring",
                        "mask-wipe",
                        "light-sweep",
                        "numeric-counter-0.5s",
                        "cta-pulse",
                        "font-style",
                        "scene-transition",
                        "bgm-under-voiceover",
                        "production-mode-routing",
                        "digital-human-remotion-layer",
                    ],
                }
                return {
                    "copy": script,
                    "audio_path": str(audio_path),
                    "audio_preview_url": media_url(audio_path),
                    "tts": {
                        "requested_service": "edge",
                        "used_service": tts_info.get("service") or "edge",
                        "used_voice": tts_info.get("voice") or payload.get("voice") or tts.DEFAULT_EDGE_VOICE,
                        "attempts": tts_info.get("attempts") or [],
                    },
                    "speech_segments": tts_info.get("sentences") or [],
                    "summary": summary,
                    "screen_copy": manifest_data.get("screenCopy") or [],
                    "background_context": manifest_data.get("backgroundContext") or payload.get("background_context") or "",
                    "video_project_id": video_project_id,
                    "material": {
                        "source": payload.get("material_source") or payload.get("network_source") or "pexels",
                        **(topic_result.get("material") if isinstance(topic_result.get("material"), dict) else {}),
                    },
                    "cta_text": cta_text,
                    "caption_template": "kinetic",
                    "caption_animation": "bottom-pop",
                    "target_duration_seconds": target_duration_seconds,
                    "aspect_ratio": manifest_data.get("aspectRatio") or payload.get("aspect_ratio") or "16:9",
                    "render_engine": "remotion-topic",
                    **voiceover_stats(script),
                }
            check_cancel()
            report_progress(*CREATOR_STAGE_VOICE)
            audio, active_tts_service, active_voice, tts_attempts, speech_segments = synthesize_script_resilient(
                script,
                service=tts_service,
                voice=voice,
                output_stem=f"pipeline-{tts_service}-{uuid4().hex[:10]}",
            )
            check_cancel()
            report_progress(*CREATOR_STAGE_MATCH)
            material_info: dict[str, Any] = {"source": source}
            sentence_material_dirs: list[str] = []
            if source in {"pexels", "pixabay"}:
                # Build one exhibition-specific visual plan. Pain points remain
                # narration concepts; online retrieval only sees physical
                # venues, industry products and business actions.
                visual_plan = qwen.generate_visual_search_plan(topic, script)
                online_dir = settings.storage_dir / "online_materials" / f"moneyprint-{uuid4().hex[:10]}"
                downloaded = stock.download_moneyprinter_visual_plan(
                    visual_plan,
                    online_dir,
                    target_duration=max(5.0, render.duration(audio)),
                    source=source,
                    max_clip_duration=5,
                )
                sentence_material_dirs, sentence_visual_roles = stock.visual_role_dirs_for_sentences(
                    render.split_sentences(script),
                    downloaded.get("role_dirs") or {},
                    cta_text=cta_text,
                )
                material_info.update({
                    **downloaded,
                    "strategy": "moneyprinter_exhibition_visual_plan",
                    "visual_plan": visual_plan,
                    "search_term_source": visual_plan.get("source") or "unknown",
                    "search_term_error": visual_plan.get("error") or "",
                    "sentence_material_dirs": sentence_material_dirs,
                    "sentence_visual_roles": sentence_visual_roles,
                })
                material_dir = material_info["material_dir"]
            else:
                selected_files = payload.get("selected_clips_resolved") or []
                if selected_files:
                    # User hand-picked an ordered clip list; hand the exact files
                    # (in order) to the renderer instead of scanning a directory.
                    material_dir = str(settings.default_material_dir)
                    material_info.update({
                        "material_dir": material_dir,
                        "selected_clips": selected_files,
                        "selected_clip_count": len(selected_files),
                        "selected_clips_total_seconds": payload.get("selected_clips_total_seconds") or 0.0,
                        "expo_scoped": bool(payload.get("expo_id")),
                    })
                else:
                    requested_material_dir = require_text(payload, "material_dir", "本地素材目录")
                    if not Path(requested_material_dir).expanduser().exists():
                        raise FileNotFoundError(f"素材目录不存在：{requested_material_dir}")
                    scoped_dir = scoped_material_dir(str(payload.get("expo_id") or "").strip(), requested_material_dir)
                    material_dir = str(scoped_dir)
                    material_info.update({
                        "material_dir": material_dir,
                        "library_dir": requested_material_dir,
                        "expo_scoped": bool(payload.get("expo_id")),
                    })

            # Legacy renderer compatibility seam. The current Remotion topic
            # path handles the digital-human PiP in topic_video.py; this branch
            # remains for older direct creator-pipeline callers.
            avatar_clip_files: list[str] = []
            if payload.get("supplement_avatar"):
                check_cancel()
                report_progress(50, "生成数字人口播")
                avatar_clip = avatar.generate_avatar_clip(
                    script, settings.storage_dir / "avatar_clips", voice=None
                )
                avatar_clip_files = [str(avatar_clip)]
                material_info["avatar_clip"] = str(avatar_clip)

            raw_highlights = payload.get("highlight_words") or []
            if isinstance(raw_highlights, str):
                raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
            highlights = [str(word).strip() for word in raw_highlights if str(word).strip()]
            check_cancel()
            report_progress(*CREATOR_STAGE_COMPOSE)
            parsed = parse_render_result(
                pipeline.render_video(
                    keyword=topic,
                    competitor_dir=str(payload.get("competitor_dir") or ""),
                    material_dir=material_dir,
                    name=str(payload.get("name") or f"creator-pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"),
                    tts_mode=active_tts_service,
                    script_mode="manual",
                    script=script,
                    audio_file=str(audio),
                    caption_template=caption_template,
                    caption_animation=caption_animation,
                    highlight_words=highlights,
                    cta_text=cta_text,
                    sentence_material_dirs=sentence_material_dirs,
                    render_captions=False,
                    material_files=(avatar_clip_files + (payload.get("selected_clips_resolved") or [])) or None,
                )
            )
            report_progress(*CREATOR_STAGE_FINALIZE)
            render_summary = parsed.get("summary") or parsed
            if isinstance(render_summary, dict):
                render_summary["video_project_id"] = video_project_id
                if render_summary.get("run_dir"):
                    write_json(Path(str(render_summary["run_dir"])) / "manifest.json", render_summary)
            return {
                "copy": script,
                "audio_path": str(audio),
                "audio_preview_url": media_url(audio),
                "tts": {
                    "requested_service": tts_service,
                    "used_service": active_tts_service,
                    "used_voice": active_voice,
                    "attempts": tts_attempts,
                },
                "speech_segments": speech_segments,
                "summary": render_summary,
                "video_project_id": video_project_id,
                "material": material_info,
                "cta_text": cta_text,
                "caption_template": caption_template,
                "caption_animation": caption_animation,
                "target_duration_seconds": target_duration_seconds,
                **voiceover_stats(script),
            }

        return submit(creator_pipeline_job)
    if kind == "copy":
        topic = str(payload.get("topic") or payload.get("keyword") or "").strip()
        background_context = str(payload.get("background_context") or payload.get("theme_text") or "").strip()
        sample = payload.get("sample") or {}
        target_duration_seconds = normalize_target_duration(payload.get("target_duration_seconds"))
        creative_direction = str(payload.get("creative_direction") or "").strip()
        reference_logic = payload.get("reference_logic") or payload.get("viral_logic") or {}
        if not topic and not (sample.get("title") or sample.get("desc")):
            raise ValueError("请填写主题/卖点，或先选择一个参考样本")
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)

        def copy_job() -> dict[str, Any]:
            copy = final_script_with_cta(
                qwen.generate_copy(
                    "\n".join(value for value in (topic, background_context) if value),
                    sample,
                    target_duration_seconds=target_duration_seconds,
                    creative_direction=creative_direction,
                    reference_logic=reference_logic if isinstance(reference_logic, dict) else None,
                ),
                cta_text,
            )
            return {
                "copy": copy,
                "cta_text": cta_text,
                "target_duration_seconds": target_duration_seconds,
                **voiceover_stats(copy),
            }

        return submit(copy_job)
    if kind == "tts":
        text = final_script_with_cta(
            require_text(payload, "text", "口播稿"),
            str(payload.get("cta_text") or DEFAULT_VIDEO_CTA),
        )
        voice = payload.get("voice") or settings.qwen_tts_voice or tts.DEFAULT_QWEN_VOICE
        service = str(payload.get("service") or "qwen")
        def tts_job() -> dict[str, Any]:
            audio, used_service, used_voice, attempts, speech_segments = synthesize_script_resilient(
                text,
                service=service,
                voice=voice,
                output_stem=f"{service}-tts-{uuid4().hex[:10]}",
            )
            fallback_errors = [
                error
                for attempt in attempts
                for error in (attempt.get("fallback_errors") or [])
            ]
            return {
                "audio_path": str(audio),
                "preview_url": media_url(audio),
                "voice": used_voice,
                "service": used_service,
                "requested_service": service,
                "fallback_used": used_service != service,
                "fallback_errors": fallback_errors,
                "speech_segments": speech_segments,
                "segment_attempts": attempts,
                "text": text,
            }
        return submit(tts_job)
    if kind == "caption-style":
        source_video_value = str(
            payload.get("preview_base_video")
            or payload.get("base_video")
            or payload.get("source_video")
            or ""
        ).strip()
        if not source_video_value:
            raise ValueError("缺少字幕预览基底视频")
        audio_value = str(payload.get("audio_file") or "").strip()
        if not audio_value:
            raise ValueError("缺少字幕预览配音")
        source_video = project_file(source_video_value, "字幕预览基底视频")
        audio = project_file(audio_value, "字幕预览配音")
        raw_timeline = payload.get("caption_timeline") or []
        if not isinstance(raw_timeline, list) or not raw_timeline:
            raise ValueError("字幕预览时间轴为空，请重新生成视频")
        timeline = [item for item in raw_timeline if isinstance(item, dict) and str(item.get("text") or "").strip()]
        caption_template = str(payload.get("caption_template") or "viral").strip().lower()
        if caption_template not in render.CAPTION_TEMPLATES:
            raise ValueError(f"未知字幕模板：{caption_template}")
        caption_animation = str(payload.get("caption_animation") or "pop").strip().lower()
        if caption_animation not in render.CAPTION_ANIMATIONS:
            raise ValueError(f"未知字幕动画：{caption_animation}")
        caption_font_type = str(payload.get("caption_font_type") or "template").strip().lower()
        if caption_font_type not in render.CAPTION_FONT_TYPES:
            raise ValueError(f"未知字幕字体类型：{caption_font_type}")
        caption_alignment = str(payload.get("caption_alignment") or "center").strip().lower()
        if caption_alignment not in {"left", "center", "right"}:
            caption_alignment = "center"
        caption_color = str(payload.get("caption_color") or "default").strip().lower()
        if caption_color != "default" and caption_color not in render.CAPTION_COLOR_SCHEMES:
            caption_color = "default"
        raw_highlights = payload.get("highlight_words") or []
        if isinstance(raw_highlights, str):
            raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
        highlight_words = [str(word).strip() for word in raw_highlights if str(word).strip()]
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)
        source_task_id = str(payload.get("source_task_id") or payload.get("render_task_id") or "").strip()
        video_project_id = str(payload.get("video_project_id") or source_task_id or f"video-{uuid4().hex[:12]}").strip()
        payload["video_project_id"] = video_project_id
        try:
            caption_font_scale = max(70.0, min(150.0, float(payload.get("caption_font_scale") or 100)))
        except (TypeError, ValueError):
            caption_font_scale = 100.0
        try:
            caption_vertical_position = max(15.0, min(50.0, float(payload.get("caption_vertical_position") or 28)))
        except (TypeError, ValueError):
            caption_vertical_position = 28.0
        try:
            raw_caption_max_chars = payload.get("caption_max_chars")
            caption_max_chars = max(6, min(14, int(raw_caption_max_chars))) if raw_caption_max_chars not in (None, "") else None
        except (TypeError, ValueError):
            caption_max_chars = None

        def caption_style_job() -> dict[str, Any]:
            manifest = render.render_caption_variant(
                str(source_video),
                str(audio),
                timeline,
                name=str(payload.get("name") or f"caption-variant-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"),
                caption_template=caption_template,
                caption_animation=caption_animation,
                highlight_words=highlight_words,
                cta_text=cta_text,
                caption_sfx_enabled=bool(payload.get("caption_sfx_enabled", True)),
                caption_font_scale=caption_font_scale,
                caption_vertical_position=caption_vertical_position,
                caption_max_chars=caption_max_chars,
                caption_font_type=caption_font_type,
                caption_alignment=caption_alignment,
                caption_color=caption_color,
            )
            manifest["video_project_id"] = video_project_id
            manifest["parent_render_task_id"] = str(payload.get("parent_task_id") or source_task_id)
            write_json(Path(str(manifest["run_dir"])) / "manifest.json", manifest)
            return {
                "source_task_id": source_task_id,
                "video_project_id": video_project_id,
                "caption_template": caption_template,
                "caption_animation": caption_animation,
                "caption_font_scale": caption_font_scale,
                "caption_vertical_position": caption_vertical_position,
                "summary": decorate_render_summary(manifest),
            }

        return submit(caption_style_job)
    if kind == "render":
        script = final_script_with_cta(
            require_text(payload, "script", "成片脚本"),
            str(payload.get("cta_text") or DEFAULT_VIDEO_CTA),
        )
        material_dir = require_text(payload, "material_dir", "素材目录")
        if not Path(material_dir).expanduser().exists():
            raise FileNotFoundError(f"素材目录不存在：{material_dir}")
        render_name = payload.get("name") or f"saas-render-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        audio_file = str(payload.get("audio_file") or "").strip()
        caption_template = str(payload.get("caption_template") or "viral").strip().lower()
        if caption_template not in render.CAPTION_TEMPLATES:
            raise ValueError(f"未知字幕模板：{caption_template}")
        caption_animation = str(payload.get("caption_animation") or "pop").strip().lower()
        if caption_animation not in render.CAPTION_ANIMATIONS:
            raise ValueError(f"未知字幕动画：{caption_animation}")
        raw_highlights = payload.get("highlight_words") or []
        if isinstance(raw_highlights, str):
            raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
        highlight_words = [str(word).strip() for word in raw_highlights if str(word).strip()]
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)

        def render_job() -> dict[str, Any]:
            resolved_audio = audio_file
            actual_tts_service = str(payload.get("tts_service") or "qwen")
            if not resolved_audio and payload.get("auto_tts", True):
                generated_audio, actual_tts_service, _actual_voice, _attempts, _speech_segments = synthesize_script_resilient(
                    script,
                    service=actual_tts_service,
                    voice=str(payload.get("voice") or settings.qwen_tts_voice or tts.DEFAULT_QWEN_VOICE),
                    output_stem=f"render-tts-{uuid4().hex[:10]}",
                )
                resolved_audio = str(generated_audio)
            parsed = parse_render_result(
                pipeline.render_video(
                    keyword=payload.get("keyword") or payload.get("subject") or "展会推广",
                    competitor_dir=payload.get("competitor_dir") or "",
                    material_dir=material_dir,
                    name=render_name,
                    tts_mode=payload.get("tts_mode") or (actual_tts_service if resolved_audio else "silent"),
                    script_mode=payload.get("script_mode") or "formula",
                    script=script,
                    audio_file=resolved_audio,
                    caption_template=caption_template,
                    caption_animation=caption_animation,
                    highlight_words=highlight_words,
                    cta_text=cta_text,
                )
            )
            parsed["final_script"] = script
            parsed["cta_text"] = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)
            return parsed

        return submit(render_job)
    if kind == "publish":
        if not public_config()["publisher_configured"]:
            raise RuntimeError("发布器未配置，当前不能提交真实发布任务。")
        video_file = require_text(payload, "video_file", "视频文件")
        require_text(payload, "title", "发布标题")
        return submit(
            lambda: {
                "log": publisher.upload_video(
                    platform=payload.get("platform") or "douyin",
                    video_file=video_file,
                    title=payload.get("title") or "",
                    desc=payload.get("desc") or "",
                    account=payload.get("account") or "creator",
                )
            },
        )
    if kind == "delivery-draft":
        return submit(lambda: delivery_draft(payload))
    if kind == "delivery-report":
        return submit(lambda: ocean_report(payload))
    if kind == "ocean-video-upload":
        return submit(lambda: ocean_video_upload(payload))
    if kind == "ocean-image-upload":
        return submit(lambda: ocean_image_upload(payload))
    if kind == "ocean-project-create":
        return submit(lambda: ocean_project_create(payload))
    if kind == "ocean-promotion-create":
        return submit(lambda: ocean_promotion_create(payload))
    if kind == "ocean-safe-launch":
        return submit(lambda: ocean_safe_delivery_test(payload))
    raise ValueError(f"unsupported task kind: {kind}")


def resume_pending_tasks(task_ids: list[str]) -> None:
    """Rebuild safe background jobs from their persisted kind and payload."""
    for task_id in task_ids:
        with TASK_LOCK:
            task = dict(TASKS.get(task_id) or {})
        if not task:
            continue
        try:
            make_task(
                str(task.get("kind") or ""),
                dict(task.get("payload") or {}),
                existing_task_id=task_id,
            )
        except Exception as exc:
            with TASK_LOCK:
                current = TASKS.get(task_id) or task
                current["status"] = "failed"
                current["finished_at"] = now()
                current["recovery_pending"] = False
                current["error"] = f"服务重启后恢复任务失败：{exc}"
                current["traceback"] = traceback.format_exc()
                TASKS[task_id] = current
                save_task(current)


class Handler(BaseHTTPRequestHandler):
    server_version = "ExhibitFlowAPI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_json({"error": "not found"}, status=404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_media(self, relative: str) -> None:
        root = settings.project_root.resolve()
        path = (root / unquote(relative)).resolve()
        if root not in path.parents or not path.is_file():
            self.send_json({"error": "media not found"}, status=404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range") or ""
        match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        status = 200
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), end)
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            try:
                self.wfile.write(handle.read(length))
            except (BrokenPipeError, ConnectionResetError):
                # Browsers routinely cancel range requests when pausing,
                # seeking, switching panels, or closing the preview.
                return

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_OPTIONS(self) -> None:
        self.send_json({"ok": True})

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/media/"):
            root = settings.project_root.resolve()
            path = (root / unquote(parsed.path[len("/media/"):])).resolve()
            if root not in path.parents or not path.is_file():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        if parsed.path in ("", "/", "/api/health", "/api/config", "/api/tasks"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if parsed.path in ("", "/") else "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("", "/"):
            # 兼容巨量应用后台只填写 http://localhost:8610 或 http://localhost:8501
            # 的情况：授权完成后 auth_code/code 会被拼到根路径，这里直接完成换 token。
            root_query = parse_qs(parsed.query)
            if root_query.get("auth_code") or root_query.get("code"):
                data = ocean_callback_html(ocean_callback_result(root_query))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_static(FRONTEND_DIR / "index.html")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/media/"):
            self.send_media(path[len("/media/"):])
            return
        if path == "/api/health":
            self.send_json({"ok": True, "config": public_config()})
            return
        if path == "/api/config":
            self.send_json(public_config())
            return
        if path == "/api/materials":
            query = parse_qs(parsed.query)
            self.send_json(list_materials((query.get("expo_id") or [""])[0]))
            return
        if path == "/api/bgms":
            query = parse_qs(parsed.query)
            self.send_json(list_bgms((query.get("expo_id") or [""])[0]))
            return
        if path == "/api/digital-human/images":
            query = parse_qs(parsed.query)
            self.send_json(list_digital_human_images((query.get("expo_id") or [""])[0]))
            return
        if path == "/api/social/bindings":
            self.send_json({"ok": True, "items": social.public_bindings()})
            return
        if path == "/api/browser-workers/status":
            query = parse_qs(parsed.query)
            worker_id = (query.get("worker_id") or [""])[0]
            if not worker_id:
                self.send_json({"ok": True, "worker": {"status": "offline", "online": False}})
                return
            self.send_json({"ok": True, "worker": browser_worker_public(load_browser_worker(worker_id))})
            return
        if path == "/api/latest":
            query = parse_qs(parsed.query)
            self.send_json(latest_manifest(action=(query.get("action") or [""])[0]))
            return
        if path == "/api/artifacts":
            query = parse_qs(parsed.query)
            filters = {
                "employee": (query.get("employee") or [""])[0],
                "type": (query.get("type") or [""])[0],
                "status": (query.get("status") or [""])[0],
                "expo_id": (query.get("expo_id") or [""])[0],
            }
            items = list_records(ARTIFACT_DIR, filters)
            self.send_json({"count": len(items), "items": items})
            return
        if path == "/api/handoffs":
            query = parse_qs(parsed.query)
            filters = {
                "from_employee": (query.get("from_employee") or [""])[0],
                "to_employee": (query.get("to_employee") or [""])[0],
                "status": (query.get("status") or [""])[0],
                "expo_id": (query.get("expo_id") or [""])[0],
            }
            items = list_records(HANDOFF_DIR, filters)
            self.send_json({"count": len(items), "items": items})
            return
        if path == "/api/oceanengine/status":
            self.send_json({"ok": True, **ocean_config_public()})
            return
        if path == "/api/tencent/status":
            self.send_json({"ok": True, **tencent_ads.public_status()})
            return
        if path == "/api/platforms":
            self.send_json({"ok": True, "items": platforms.public_capabilities(ocean_config_public())})
            return
        if path == "/api/oceanengine/projects":
            self.send_json(list_delivery_project_units())
            return
        if path == "/api/oceanengine/auth-url":
            query = parse_qs(parsed.query)
            try:
                self.send_json({"ok": True, "url": ocean_auth_url((query.get("state") or ["exhibitflow"])[0])})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if path == "/api/oceanengine/callback":
            query = parse_qs(parsed.query)
            result = ocean_callback_result(query)
            data = ocean_callback_html(result)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/tasks":
            query = parse_qs(parsed.query)
            expo_id = str((query.get("expo_id") or [""])[0]).strip()
            with TASK_LOCK:
                tasks = sorted(TASKS.values(), key=lambda item: item.get("created_at", ""), reverse=True)
            if expo_id:
                tasks = [task for task in tasks if str((task.get("payload") or {}).get("expo_id") or "") == expo_id]
            tasks = [decorate_task_for_client(task) for task in tasks]
            self.send_json({"count": len(tasks), "items": tasks[:100], "expo_id": expo_id})
            return
        if path.startswith("/api/tasks/"):
            task_id = unquote(path.rsplit("/", 1)[-1])
            with TASK_LOCK:
                task = TASKS.get(task_id)
            self.send_json(decorate_task_for_client(task) if task else {"error": "task not found"}, status=200 if task else 404)
            return
        static_path = (FRONTEND_DIR / path.lstrip("/")).resolve()
        if FRONTEND_DIR.resolve() in static_path.parents:
            self.send_static(static_path)
            return
        self.send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/oceanengine/token":
                payload = self.read_payload()
                auth_code = require_text(payload, "auth_code", "巨量授权 auth_code")
                self.send_json(ocean_exchange_token(auth_code))
                return
            if path == "/api/social/verify":
                payload = self.read_payload()
                self.send_json({"ok": True, "binding": social.verify_browser_login(require_text(payload, "platform", "平台"))})
                return
            if path == "/api/social/clear":
                payload = self.read_payload()
                self.send_json(social.clear_binding(require_text(payload, "platform", "平台")))
                return
            if path == "/api/oceanengine/refresh-token":
                payload = self.read_payload()
                self.send_json(ocean_refresh_token(str(payload.get("refresh_token") or "")))
                return
            if path == "/api/social/open-login":
                payload = self.read_payload()
                self.send_json(social.open_local_login_page(require_text(payload, "platform", "平台")))
                return
            if path == "/api/browser-workers/register":
                self.send_json(register_browser_worker(self.read_payload()))
                return
            if path == "/api/browser-workers/heartbeat":
                self.send_json(register_browser_worker(self.read_payload()))
                return
            if path == "/api/browser-workers/claim":
                self.send_json(claim_browser_worker_task(self.read_payload()))
                return
            if path == "/api/browser-workers/result":
                self.send_json(finish_browser_worker_task(self.read_payload()))
                return
            if path == "/api/oceanengine/advertisers":
                self.send_json(ocean_advertisers(self.read_payload()))
                return
            if path == "/api/oceanengine/onboarding":
                self.send_json(ocean_permission_guide(self.read_payload()))
                return
            if path == "/api/oceanengine/report":
                self.send_json(ocean_report(self.read_payload()))
                return
            if path == "/api/oceanengine/report-config":
                self.send_json(ocean_report_config(self.read_payload()))
                return
            if path == "/api/oceanengine/video-upload":
                self.send_json(ocean_video_upload(self.read_payload()))
                return
            if path == "/api/oceanengine/project-create":
                self.send_json(ocean_project_create(self.read_payload()))
                return
            if path == "/api/oceanengine/promotion-create":
                self.send_json(ocean_promotion_create(self.read_payload()))
                return
            if path == "/api/tencent/creatives":
                self.send_json(tencent_ads.get_creatives(self.read_payload()))
                return
            if path == "/api/materials/upload":
                query = parse_qs(parsed.query)
                filename = Path((query.get("name") or [""])[0]).name
                expo_id = str((query.get("expo_id") or [""])[0]).strip()
                allowed = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
                if not filename or Path(filename).suffix.lower() not in allowed:
                    raise ValueError("仅支持常见视频和图片素材")
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    raise ValueError("上传文件为空")
                settings.default_material_dir.mkdir(parents=True, exist_ok=True)
                target = settings.default_material_dir / filename
                if target.exists():
                    target = target.with_name(f"{target.stem}-{uuid4().hex[:6]}{target.suffix.lower()}")
                with target.open("wb") as handle:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)
                index = _load_material_index()
                index[target.name] = {
                    "expo_id": expo_id,
                    "tags": infer_material_tags(target.name),
                    "source": "本地上传",
                    "uploaded_at": now(),
                }
                _save_material_index(index)
                self.send_json({
                    "ok": True,
                    "name": target.name,
                    "path": str(target),
                    "size": target.stat().st_size,
                    "expo_id": expo_id,
                    "tags": index[target.name]["tags"],
                }, status=201)
                return
            if path == "/api/bgms/upload":
                query = parse_qs(parsed.query)
                filename = Path((query.get("name") or [""])[0]).name
                expo_id = str((query.get("expo_id") or [""])[0]).strip()
                if not filename or Path(filename).suffix.lower() not in BGM_EXTENSIONS:
                    raise ValueError("BGM 仅支持 MP3、WAV、M4A、AAC、OGG 或 FLAC")
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    raise ValueError("BGM 文件为空")
                if length > 80 * 1024 * 1024:
                    raise ValueError("BGM 文件不能超过 80MB")
                target_dir = BGM_ROOT / _bgm_scope(expo_id)
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / filename
                if target.exists():
                    target = target.with_name(f"{target.stem}-{uuid4().hex[:6]}{target.suffix.lower()}")
                with target.open("wb") as handle:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    target.unlink(missing_ok=True)
                    raise ValueError("BGM 上传未完成")
                try:
                    duration = round(float(render.duration(target)) or 0.0, 2)
                except Exception:
                    duration = 0.0
                self.send_json({
                    "ok": True,
                    "id": target.relative_to(BGM_ROOT).as_posix(),
                    "name": target.name,
                    "path": str(target),
                    "size": target.stat().st_size,
                    "duration": duration,
                    "expo_id": expo_id,
                }, status=201)
                return
            if path == "/api/digital-human/images/upload":
                query = parse_qs(parsed.query)
                filename = Path((query.get("name") or [""])[0]).name
                expo_id = str((query.get("expo_id") or [""])[0]).strip()
                if not filename or Path(filename).suffix.lower() not in DIGITAL_HUMAN_IMAGE_EXTENSIONS:
                    raise ValueError("数字人形象仅支持 JPG、PNG 或 WEBP")
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    raise ValueError("数字人形象文件为空")
                if length > 10 * 1024 * 1024:
                    raise ValueError("数字人形象不能超过 10MB")
                image_root = avatar.digital_human_image_root()
                target_dir = image_root / _digital_human_scope(expo_id)
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / filename
                if target.exists():
                    target = target.with_name(f"{target.stem}-{uuid4().hex[:6]}{target.suffix.lower()}")
                with target.open("wb") as handle:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    target.unlink(missing_ok=True)
                    raise ValueError("数字人形象上传未完成")
                self.send_json({
                    "ok": True,
                    "id": target.relative_to(image_root).as_posix(),
                    "name": target.name,
                    "path": str(target),
                    "url": media_url(target),
                    "size": target.stat().st_size,
                    "expo_id": expo_id,
                }, status=201)
                return
            payload = self.read_payload()
            if path == "/api/artifacts":
                self.send_json(create_artifact(payload), status=201)
                return
            if path == "/api/handoffs":
                self.send_json(create_handoff(payload), status=201)
                return
            handoff_match = re.fullmatch(r"/api/handoffs/([^/]+)/(accept|reject)", path)
            if handoff_match:
                handoff_id = unquote(handoff_match.group(1))
                self.send_json(update_handoff(handoff_id, handoff_match.group(2)))
                return
            cancel_match = re.fullmatch(r"/api/tasks/([^/]+)/cancel", path)
            if cancel_match:
                self.send_json(request_task_cancel(unquote(cancel_match.group(1))))
                return
            if path.startswith("/api/tasks/"):
                kind = path.rsplit("/", 1)[-1]
                self.send_json(make_task(kind, payload), status=202)
                return
            self.send_json({"error": "not found"}, status=404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except (RuntimeError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, status=409)
        except Exception as exc:
            self.send_json({"error": str(exc), "traceback": traceback.format_exc()}, status=500)


def main() -> None:
    pending_resume = load_tasks()
    if pending_resume:
        print(f"Recovering {len(pending_resume)} safe background task(s) after restart")
        resume_pending_tasks(pending_resume)
    host = settings.host
    port = settings.port
    print(f"ExhibitFlow API running at http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExhibitFlow API stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
