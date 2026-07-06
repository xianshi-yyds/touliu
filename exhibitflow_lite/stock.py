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
        media = _best_video_file(video.get("video_files") or [])
        if media:
            results.append({"provider": "pexels", "term": term, "duration": duration, "url": media["link"]})
    return results


def download_moneyprinter_materials(
    search_terms: list[str], output_dir: str | Path, target_duration: float = 35.0
) -> dict[str, Any]:
    """MoneyPrinter-compatible global stock search: search several terms, dedupe, then download enough duration."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_duration = 0.0
    for term in search_terms:
        for item in search_pexels(term):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            selected.append(item)
            total_duration += float(item["duration"])
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
    return {"material_dir": str(output), "files": files, "search_terms": search_terms, "duration": total_duration}
