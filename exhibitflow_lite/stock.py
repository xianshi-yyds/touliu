from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

import requests

from .config import settings


def _best_video_file(files: list[dict[str, Any]], orientation: str = "portrait") -> dict[str, Any] | None:
    candidates = [item for item in files if item.get("link") and item.get("width") and item.get("height")]
    if not candidates:
        return None
    wanted = str(orientation or "portrait").strip().lower()
    if wanted == "landscape":
        preferred = [item for item in candidates if int(item["width"]) >= int(item["height"])]
    else:
        preferred = [item for item in candidates if int(item["height"]) >= int(item["width"])]
    pool = preferred or candidates
    return max(pool, key=lambda item: min(int(item["width"]), 1080) * min(int(item["height"]), 1920))


def _exact_portrait_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer a 1080x1920 portrait source, then fall back safely."""
    for item in files:
        if item.get("link") and int(item.get("width") or 0) == 1080 and int(item.get("height") or 0) == 1920:
            return item
    return _best_video_file(files)


def _exact_landscape_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer a 1920x1080 landscape source, then fall back safely."""
    for item in files:
        if item.get("link") and int(item.get("width") or 0) >= int(item.get("height") or 0):
            if int(item.get("width") or 0) >= 1280:
                return item
    return _best_video_file(files, orientation="landscape")


def search_pexels(
    term: str,
    minimum_duration: int = 5,
    per_page: int = 20,
    orientation: str = "portrait",
) -> list[dict[str, Any]]:
    if not settings.pexels_api_key:
        raise RuntimeError("网络检索素材未配置 PEXELS_API_KEY")
    wanted = "landscape" if str(orientation or "portrait").strip().lower() == "landscape" else "portrait"
    response = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": term, "per_page": per_page, "orientation": wanted},
        headers={"Authorization": settings.pexels_api_key},
        timeout=(20, 60),
    )
    response.raise_for_status()
    results: list[dict[str, Any]] = []
    for video in response.json().get("videos", []):
        duration = int(video.get("duration") or 0)
        if duration < minimum_duration:
            continue
        files = video.get("video_files") or []
        media = _exact_landscape_file(files) if wanted == "landscape" else _exact_portrait_file(files)
        if media:
            results.append({"provider": "pexels", "term": term, "duration": duration, "url": media["link"]})
    return results


def search_pixabay(
    term: str,
    minimum_duration: int = 5,
    per_page: int = 50,
    orientation: str = "portrait",
) -> list[dict[str, Any]]:
    """Use the Pixabay branch when a Pixabay key is configured."""
    if not settings.pixabay_api_key:
        raise RuntimeError("网络检索 Pixabay 未配置 PIXABAY_API_KEY")
    response = requests.get(
        "https://pixabay.com/api/videos/",
        params={"q": term, "video_type": "all", "per_page": per_page, "key": settings.pixabay_api_key},
        timeout=(30, 60),
    )
    response.raise_for_status()
    results: list[dict[str, Any]] = []
    for video in response.json().get("hits", []):
        duration = int(video.get("duration") or 0)
        if duration < minimum_duration:
            continue
        choices = video.get("videos") or {}
        wanted = "landscape" if str(orientation or "portrait").strip().lower() == "landscape" else "portrait"
        candidates = [item for item in choices.values() if item.get("url") and item.get("width") and item.get("height")]
        preferred = [
            item for item in candidates
            if (int(item.get("width") or 0) >= int(item.get("height") or 0)) == (wanted == "landscape")
        ]
        media = max(preferred or candidates, key=lambda item: int(item.get("width") or 0), default=None)
        if media:
            results.append({"provider": "pixabay", "term": term, "duration": duration, "url": media["url"]})
    return results


def download_moneyprinter_materials(
    search_terms: list[str], output_dir: str | Path, target_duration: float = 35.0,
    source: str = "pexels", max_clip_duration: int = 5, orientation: str = "portrait",
) -> dict[str, Any]:
    """Network search/download: terms, duration filter, dedupe and capped clip duration."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_duration = 0.0
    for term in search_terms:
        searcher = search_pixabay if source == "pixabay" else search_pexels
        for item in searcher(term, minimum_duration=max_clip_duration, orientation=orientation):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            selected.append(item)
            total_duration += min(float(item["duration"]), max_clip_duration)
            if total_duration >= target_duration:
                break
        if total_duration >= target_duration:
            break
    if not selected:
        raise RuntimeError("网络检索素材没有搜索到可用视频")

    files: list[str] = []
    for item in selected:
        digest = hashlib.md5(item["url"].split("?")[0].encode()).hexdigest()[:12]
        path = output / f"pexels-{digest}.mp4"
        if not path.exists() or path.stat().st_size == 0:
            media = requests.get(item["url"], timeout=(30, 240))
            media.raise_for_status()
            path.write_bytes(media.content)
        files.append(str(path))
    return {"material_dir": str(output), "files": files, "search_terms": search_terms, "duration": total_duration, "source": source, "max_clip_duration": max_clip_duration}


def download_moneyprinter_visual_plan(
    visual_plan: dict[str, Any],
    output_dir: str | Path,
    target_duration: float = 35.0,
    source: str = "pexels",
    max_clip_duration: int = 5,
    orientation: str = "portrait",
) -> dict[str, Any]:
    """Retrieve a balanced exhibition footage pool by visual role.

    Every role receives its own duration quota, so an early broad query cannot
    consume the full video before venue and industry footage are considered.
    Results are interleaved across terms to reduce near-duplicate shots.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    searcher = search_pixabay if source == "pixabay" else search_pexels
    seen: set[str] = set()
    all_selected: list[dict[str, Any]] = []
    group_results: list[dict[str, Any]] = []
    role_dirs: dict[str, str] = {}
    groups = visual_plan.get("groups") if isinstance(visual_plan.get("groups"), list) else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        role = re.sub(r"[^a-z0-9_-]+", "-", str(group.get("role") or "scene").lower()).strip("-") or "scene"
        terms = [str(term).strip() for term in (group.get("terms") or []) if str(term).strip()]
        if not terms:
            continue
        try:
            ratio = max(0.05, min(0.60, float(group.get("ratio") or 0.25)))
        except (TypeError, ValueError):
            ratio = 0.25
        quota = max(float(max_clip_duration), float(target_duration) * ratio)
        desired_clips = max(1, int(math.ceil(quota / max(1, max_clip_duration))))
        query_terms = terms[: min(len(terms), max(2, desired_clips))]
        result_lists: list[list[dict[str, Any]]] = []
        search_errors: list[str] = []
        for term in query_terms:
            try:
                result_lists.append(searcher(term, minimum_duration=max_clip_duration, orientation=orientation))
            except Exception as exc:
                search_errors.append(f"{term}: {exc}")
        selected: list[dict[str, Any]] = []
        selected_duration = 0.0
        max_rows = max((len(items) for items in result_lists), default=0)
        for row in range(max_rows):
            for items in result_lists:
                if row >= len(items):
                    continue
                item = items[row]
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                selected.append({**item, "role": role})
                selected_duration += min(float(item["duration"]), max_clip_duration)
                if selected_duration >= quota:
                    break
            if selected_duration >= quota:
                break
        if not selected:
            group_results.append({"role": role, "terms": query_terms, "quota": quota, "duration": 0.0, "count": 0, "files": [], "errors": search_errors})
            continue
        role_dir = output / role
        role_dir.mkdir(parents=True, exist_ok=True)
        files: list[str] = []
        for item in selected:
            digest = hashlib.md5(item["url"].split("?")[0].encode()).hexdigest()[:12]
            provider = str(item.get("provider") or source)
            path = role_dir / f"{provider}-{digest}.mp4"
            if not path.exists() or path.stat().st_size == 0:
                media = requests.get(item["url"], timeout=(30, 240))
                media.raise_for_status()
                path.write_bytes(media.content)
            files.append(str(path))
        role_dirs[role] = str(role_dir)
        all_selected.extend(selected)
        group_results.append({
            "role": role,
            "terms": query_terms,
            "quota": round(quota, 2),
            "duration": round(selected_duration, 2),
            "count": len(selected),
            "files": files,
            "errors": search_errors,
        })
    if not all_selected:
        raise RuntimeError("网络检索素材没有搜索到符合展会视觉策略的可用视频")
    files = [path for group in group_results for path in group.get("files") or []]
    return {
        "material_dir": str(output),
        "files": files,
        "search_terms": [term for group in groups if isinstance(group, dict) for term in (group.get("terms") or [])],
        "duration": round(sum(min(float(item["duration"]), max_clip_duration) for item in all_selected), 2),
        "source": source,
        "max_clip_duration": max_clip_duration,
        "orientation": orientation,
        "strategy": "moneyprinter_exhibition_visual_plan",
        "role_dirs": role_dirs,
        "groups": group_results,
    }


def visual_role_dirs_for_sentences(
    sentences: list[str],
    role_dirs: dict[str, str],
    cta_text: str = "",
) -> tuple[list[str], list[str]]:
    """Map narration blocks to physical footage roles, not emotion words."""
    if not sentences or not role_dirs:
        return [], []
    available = [role for role in ("venue", "industry", "business", "atmosphere") if role_dirs.get(role)]
    if not available:
        return [], []
    pain_markers = ("焦虑", "压力", "困难", "难以", "成本", "分散", "错过", "流失", "困境", "卡点", "问题")
    solution_markers = ("现场", "集中", "连接", "比较", "洽谈", "采购", "合作", "交流", "对接", "解决", "找到")
    cta_clean = re.sub(r"\s+", "", str(cta_text or ""))
    roles: list[str] = []
    for index, sentence in enumerate(sentences):
        compact = re.sub(r"\s+", "", str(sentence or ""))
        if index == 0 or index == len(sentences) - 1 or (cta_clean and cta_clean in compact):
            preferred = "venue"
        elif any(marker in compact for marker in solution_markers):
            preferred = "business"
        elif any(marker in compact for marker in pain_markers):
            # Pain stays in narration. Show products/industry complexity rather
            # than a distressed face or generic office worker.
            preferred = "industry"
        else:
            progress = index / max(1, len(sentences) - 1)
            preferred = "atmosphere" if progress < 0.25 else "industry" if progress < 0.58 else "business"
        role = preferred if preferred in available else available[index % len(available)]
        roles.append(role)
    return [role_dirs[role] for role in roles], roles
