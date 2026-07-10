from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import requests

from .config import settings


def _best_video_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [item for item in files if item.get("link") and item.get("width") and item.get("height")]
    if not candidates:
        return None
    portrait = [item for item in candidates if int(item["height"]) >= int(item["width"])]
    pool = portrait or candidates
    return max(pool, key=lambda item: min(int(item["width"]), 1080) * min(int(item["height"]), 1920))


def _exact_portrait_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer MoneyPrinter's 1080x1920 portrait source, then fall back safely."""
    for item in files:
        if item.get("link") and int(item.get("width") or 0) == 1080 and int(item.get("height") or 0) == 1920:
            return item
    return _best_video_file(files)


def search_pexels(term: str, minimum_duration: int = 5, per_page: int = 20) -> list[dict[str, Any]]:
    if not settings.pexels_api_key:
        raise RuntimeError("MoneyPrinter 在线素材未配置 PEXELS_API_KEY")
    response = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": term, "per_page": per_page, "orientation": "portrait"},
        headers={"Authorization": settings.pexels_api_key},
        timeout=(20, 60),
    )
    response.raise_for_status()
    results: list[dict[str, Any]] = []
    for video in response.json().get("videos", []):
        duration = int(video.get("duration") or 0)
        if duration < minimum_duration:
            continue
        media = _exact_portrait_file(video.get("video_files") or [])
        if media:
            results.append({"provider": "pexels", "term": term, "duration": duration, "url": media["link"]})
    return results


def search_pixabay(term: str, minimum_duration: int = 5, per_page: int = 50) -> list[dict[str, Any]]:
    """Mirror MoneyPrinter's Pixabay branch when a Pixabay key is configured."""
    if not settings.pixabay_api_key:
        raise RuntimeError("MoneyPrinter Pixabay 未配置 PIXABAY_API_KEY")
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
        media = next(
            (item for item in choices.values() if item.get("url") and int(item.get("width") or 0) >= 1080),
            None,
        )
        if media:
            results.append({"provider": "pixabay", "term": term, "duration": duration, "url": media["url"]})
    return results


def download_moneyprinter_materials(
    search_terms: list[str], output_dir: str | Path, target_duration: float = 35.0,
    source: str = "pexels", max_clip_duration: int = 5,
) -> dict[str, Any]:
    """MoneyPrinter-compatible search/download: terms, duration filter, dedupe and capped clip duration."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_duration = 0.0
    for term in search_terms:
        searcher = search_pixabay if source == "pixabay" else search_pexels
        for item in searcher(term, minimum_duration=max_clip_duration):
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
        raise RuntimeError("MoneyPrinter 在线素材没有搜索到可用视频")

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
