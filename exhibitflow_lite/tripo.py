from __future__ import annotations

import json
import threading
from datetime import datetime
from html import escape as escape_html
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests

from .config import settings
from .storage import safe_stem, write_json

TRIPO_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIEW_ORDER = ("front", "left", "back", "right")
TRIPLE_SLOTS = ("front", "left", "right")
SLOT_LABELS = {"front": "正面", "left": "左侧面", "back": "背面", "right": "右侧面"}
TRIPO_LOCK = threading.Lock()
SUCCESS_STATUSES = {"success"}
FAILED_STATUSES = {"failed", "banned", "expired", "cancelled", "unknown"}
PENDING_STATUSES = {"pending", "queued", "running", "processing", "in_progress"}


def models_root() -> Path:
    root = settings.storage_dir / "tripo_models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def configured() -> bool:
    return bool(settings.tripo_api_key)


def public_status() -> dict[str, Any]:
    return {
        "configured": configured(),
        "model_version": settings.tripo_model_version,
        "texture_quality": settings.tripo_texture_quality,
        "face_limit": settings.tripo_face_limit,
    }


def record_path(record_id: str) -> Path:
    return models_root() / f"{safe_stem(record_id)}.json"


def load_record(record_id: str) -> dict[str, Any] | None:
    path = record_path(record_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_record(record: dict[str, Any]) -> dict[str, Any]:
    write_json(record_path(str(record["id"])), record)
    return record


def list_records(expo_id: str = "", limit: int = 80) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = sorted(models_root().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    expo = str(expo_id or "").strip()
    for path in paths:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        if expo and str(item.get("expo_id") or "") != expo:
            continue
        items.append(public_record(item))
        if len(items) >= limit:
            break
    return items


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _headers() -> dict[str, str]:
    if not settings.tripo_api_key:
        raise ValueError("尚未配置 TRIPO_API_KEY，无法生成 3D 模型。")
    return {"Authorization": f"Bearer {settings.tripo_api_key}"}


def _raise_for_tripo(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Tripo 返回无法解析：{response.text[:400]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Tripo 返回格式不正确")
    code = payload.get("code")
    if response.status_code >= 400 or (code not in (None, 0)):
        message = str(payload.get("message") or payload.get("msg") or payload)
        if code == 2010 or response.status_code == 403:
            raise RuntimeError("Tripo 积分不足，请先充值后再生成。")
        raise RuntimeError(f"Tripo 请求失败：{message[:700]}")
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def upload_image_file(source: Path, filename: str) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    file_type = "jpg" if suffix in {".jpg", ".jpeg"} else suffix.lstrip(".")
    with source.open("rb") as handle:
        response = requests.post(
            f"{settings.tripo_base_url}/upload/sts",
            headers=_headers(),
            files={"file": (filename, handle, "application/octet-stream")},
            timeout=120,
        )
    data = _raise_for_tripo(response)
    token = str(data.get("image_token") or data.get("file_token") or data.get("token") or "")
    if not token:
        raise RuntimeError(f"Tripo 上传未返回 file_token：{json.dumps(data, ensure_ascii=False)[:400]}")
    return {"file_token": token, "file_type": file_type, "raw": data}


def _generation_defaults() -> dict[str, Any]:
    return {
        "model_version": settings.tripo_model_version,
        "texture": True,
        "pbr": True,
        "texture_quality": settings.tripo_texture_quality,
        "enable_image_autofix": True,
        "face_limit": settings.tripo_face_limit,
    }


def _submit_task(body: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{settings.tripo_base_url}/task",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    data = _raise_for_tripo(response)
    task_id = str(data.get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"Tripo 未返回 task_id：{json.dumps(data, ensure_ascii=False)[:400]}")
    return data


def create_image_to_model_task(file_token: str, file_type: str) -> dict[str, Any]:
    body = {
        "type": "image_to_model",
        "file": {"type": file_type, "file_token": file_token},
        "orientation": "align_image",
        **_generation_defaults(),
    }
    return _submit_task(body)


def create_multiview_to_model_task(views: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = []
    for slot in VIEW_ORDER:
        item = views.get(slot) or {}
        token = str(item.get("file_token") or "")
        file_type = str(item.get("file_type") or "jpg")
        entry: dict[str, Any] = {"type": file_type}
        if token:
            entry["file_token"] = token
        files.append(entry)
    if not (views.get("front") or {}).get("file_token"):
        raise ValueError("三视图必须包含正面照片")
    body = {
        "type": "multiview_to_model",
        "files": files,
        **_generation_defaults(),
    }
    return _submit_task(body)


def fetch_task(task_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{settings.tripo_base_url}/task/{quote(task_id)}",
        headers=_headers(),
        timeout=30,
    )
    return _raise_for_tripo(response)


def _output_urls(data: dict[str, Any]) -> dict[str, str]:
    output = data.get("output") if isinstance(data.get("output"), dict) else data
    return {
        "model_url": str(
            output.get("model")
            or output.get("pbr_model")
            or output.get("model_url")
            or data.get("model")
            or ""
        ),
        "rendered_image_url": str(
            output.get("rendered_image")
            or output.get("rendered_image_url")
            or data.get("rendered_image")
            or ""
        ),
    }


def _normalize_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status in SUCCESS_STATUSES:
        return "success"
    if status in FAILED_STATUSES:
        return "failed"
    if status in PENDING_STATUSES or not status:
        return "processing" if status else "pending"
    return status


def download_url(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def media_rel(path: Path) -> str:
    root = settings.project_root.resolve()
    candidate = path.expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        return ""
    return f"/media/{quote(str(candidate.relative_to(root)))}"


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    preview = str(record.get("preview_url") or "")
    if not preview and record.get("id"):
        preview = f"/showcase/{record['id']}"
    return {
        "ok": True,
        "id": record.get("id"),
        "expo_id": record.get("expo_id") or "",
        "product_name": record.get("product_name") or "",
        "product_description": record.get("product_description") or "",
        "task_id": record.get("task_id") or "",
        "status": record.get("status") or "pending",
        "error": record.get("error") or "",
        "input_image_url": record.get("input_image_url") or "",
        "input_mode": record.get("input_mode") or "single",
        "views": {
            slot: {"url": str((record.get("views") or {}).get(slot, {}).get("url") or "")}
            for slot in VIEW_ORDER
            if (record.get("views") or {}).get(slot)
        },
        "model_url": record.get("local_model_url") or record.get("model_url") or "",
        "rendered_image_url": record.get("local_preview_url") or record.get("rendered_image_url") or "",
        "preview_url": preview,
        "qrcode_url": record.get("qrcode_url") or preview,
        "credits_consumed": record.get("credits_consumed"),
        "created_at": record.get("created_at") or "",
        "completed_at": record.get("completed_at") or "",
    }


def _normalize_slot(value: str) -> str:
    slot = str(value or "front").strip().lower()
    aliases = {"front": "front", "正面": "front", "single": "front", "left": "left", "左": "left", "左侧面": "left", "right": "right", "右": "right", "右侧面": "right", "back": "back", "背面": "back", "后": "back"}
    if slot not in VIEW_ORDER and slot not in aliases:
        raise ValueError("视角只能是正面、左侧面、右侧面或背面")
    return aliases.get(slot, slot)


def _normalize_mode(value: str) -> str:
    mode = str(value or "single").strip().lower()
    if mode in {"multi", "multiview", "triple", "三视图"}:
        return "multiview"
    return "single"


def create_local_image(
    expo_id: str,
    filename: str,
    payload: bytes,
    record_id: str = "",
    slot: str = "front",
    input_mode: str = "single",
) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in TRIPO_IMAGE_EXTENSIONS:
        raise ValueError("产品图片仅支持 JPG、PNG 或 WEBP")
    if not payload:
        raise ValueError("产品图片为空")
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError("产品图片不能超过 20MB")
    slot = _normalize_slot(slot)
    input_mode = _normalize_mode(input_mode)
    with TRIPO_LOCK:
        record = load_record(record_id) if record_id else None
        if record_id and not record:
            raise FileNotFoundError("未找到已上传的产品记录")
        if not record:
            record = {
                "id": uuid4().hex[:12],
                "expo_id": str(expo_id or "").strip(),
                "product_name": "",
                "product_description": "",
                "task_id": "",
                "status": "uploaded",
                "views": {},
                "created_at": now(),
            }
        record["input_mode"] = input_mode
        folder = models_root() / record["id"]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{slot}{suffix}"
        target.write_bytes(payload)
        views = dict(record.get("views") or {})
        views[slot] = {
            "path": str(target),
            "url": media_rel(target),
            "file_type": "jpg" if suffix in {".jpg", ".jpeg"} else suffix.lstrip("."),
        }
        record["views"] = views
        if slot == "front":
            record["input_image_path"] = str(target)
            record["input_image_url"] = media_rel(target)
            record["file_type"] = views[slot]["file_type"]
        record["updated_at"] = now()
        save_record(record)
    return public_record(record)


def start_generation(record_id: str, product_name: str = "", product_description: str = "") -> dict[str, Any]:
    with TRIPO_LOCK:
        record = load_record(record_id)
        if not record:
            raise FileNotFoundError("未找到已上传的产品图片")
        views = dict(record.get("views") or {})
        if not views and record.get("input_image_path"):
            views = {
                "front": {
                    "path": record["input_image_path"],
                    "url": record.get("input_image_url") or "",
                    "file_type": record.get("file_type") or "jpg",
                }
            }
        mode = _normalize_mode(str(record.get("input_mode") or "single"))
        uploaded = [slot for slot in VIEW_ORDER if Path(str((views.get(slot) or {}).get("path") or "")).is_file()]
        if not uploaded:
            raise FileNotFoundError("产品图片文件已丢失，请重新上传")
        if mode == "multiview":
            missing = [SLOT_LABELS[slot] for slot in TRIPLE_SLOTS if slot not in uploaded]
            if missing:
                raise ValueError(f"三视图还缺：{'、'.join(missing)}")
            remote_views: dict[str, dict[str, Any]] = {}
            for slot in uploaded:
                source = Path(str(views[slot]["path"]))
                upload = upload_image_file(source, source.name)
                views[slot]["file_token"] = upload["file_token"]
                views[slot]["file_type"] = upload["file_type"]
                remote_views[slot] = upload
            task = create_multiview_to_model_task(remote_views)
        else:
            source = Path(str((views.get("front") or views[uploaded[0]])["path"]))
            if not source.is_file():
                raise FileNotFoundError("产品图片文件已丢失，请重新上传")
            upload = upload_image_file(source, source.name)
            views.setdefault("front", {})["file_token"] = upload["file_token"]
            views["front"]["file_type"] = upload["file_type"]
            task = create_image_to_model_task(upload["file_token"], upload["file_type"])
        record["views"] = views
        record["product_name"] = str(product_name or record.get("product_name") or "").strip()
        record["product_description"] = str(product_description or record.get("product_description") or "").strip()
        record["file_token"] = str((views.get("front") or {}).get("file_token") or "")
        record["file_type"] = str((views.get("front") or {}).get("file_type") or "")
        record["task_id"] = str(task.get("task_id") or "")
        record["status"] = "pending"
        record["error"] = ""
        record["updated_at"] = now()
        save_record(record)
    return public_record(record)


def refresh_record(record_id: str) -> dict[str, Any]:
    record = load_record(record_id)
    if not record:
        raise FileNotFoundError("未找到 3D 任务")
    task_id = str(record.get("task_id") or "")
    if not task_id:
        return public_record(record)
    if record.get("status") in {"success", "failed"} and record.get("local_model_url"):
        return public_record(record)
    data = fetch_task(task_id)
    status = _normalize_status(str(data.get("status") or ""))
    urls = _output_urls(data)
    record["status"] = status
    record["credits_consumed"] = data.get("credits") or data.get("credits_consumed")
    record["remote"] = {
        "status": data.get("status"),
        "progress": data.get("progress"),
    }
    if status == "success":
        folder = models_root() / record["id"]
        if urls["model_url"]:
            model_path = download_url(urls["model_url"], folder / "model.glb")
            record["model_url"] = urls["model_url"]
            record["local_model_path"] = str(model_path)
            record["local_model_url"] = media_rel(model_path)
        if urls["rendered_image_url"]:
            preview_path = download_url(urls["rendered_image_url"], folder / "preview.png")
            record["rendered_image_url"] = urls["rendered_image_url"]
            record["local_preview_path"] = str(preview_path)
            record["local_preview_url"] = media_rel(preview_path)
        record["completed_at"] = now()
        record["preview_url"] = f"/showcase/{record['id']}"
        record["qrcode_url"] = record["preview_url"]
    elif status == "failed":
        record["error"] = str(data.get("message") or data.get("error") or data.get("error_msg") or "生成失败")
        record["completed_at"] = now()
    save_record(record)
    return public_record(record)


def attach_qrcode(record_id: str, public_base: str = "") -> dict[str, Any]:
    record = load_record(record_id)
    if not record:
        raise FileNotFoundError("未找到 3D 任务")
    base = (public_base or settings.tripo_public_base_url).rstrip("/")
    preview = f"/showcase/{record['id']}"
    record["preview_url"] = f"{base}{preview}" if base else preview
    record["qrcode_url"] = record["preview_url"]
    save_record(record)
    return public_record(record)


def showcase_html(record: dict[str, Any]) -> str:
    public = public_record(record)
    title = escape_html(public.get("product_name") or "3D 产品展示")
    desc = escape_html(public.get("product_description") or "")
    model = escape_html(public.get("model_url") or "")
    image = escape_html(public.get("rendered_image_url") or public.get("input_image_url") or "")
    if not model:
        body = f"<p>模型尚未生成完成（{escape_html(public.get('status') or '')}）。</p>"
        if image:
            body += f'<p><img src="{image}" alt="" style="max-width:100%"></p>'
    else:
        body = (
            f'<model-viewer src="{model}" poster="{image}" camera-controls auto-rotate '
            'shadow-intensity="1" style="width:100%;height:70vh;background:#111"></model-viewer>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"></script>
  <style>
    body {{ margin:0; font-family:-apple-system,sans-serif; background:#0f1115; color:#f4f5f3; }}
    header {{ padding:16px 20px 8px; }}
    h1 {{ margin:0 0 6px; font-size:22px; }}
    p {{ margin:0; color:#b7bcc6; }}
  </style>
</head>
<body>
  <header><h1>{title}</h1><p>{desc}</p></header>
  {body}
</body>
</html>
"""
