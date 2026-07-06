from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .storage import manifest_path, safe_stem, write_json


PLATFORM_SCRIPTS = {
    "douyin": "crawl_douyin.py",
    "xiaohongshu": "crawl_xiaohongshu.py",
}
PLATFORM_LABELS = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}


def python_executable() -> str:
    candidate = settings.crawler_dir / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def run_command(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return result.stdout


def item_matches_platform(item: dict[str, Any], platform: str) -> bool:
    item_platform = str(item.get("platform") or "")
    if item_platform:
        return item_platform == platform
    url = str(item.get("url") or item.get("video_url") or "")
    if platform == "xiaohongshu":
        return "xiaohongshu.com" in url or bool(item.get("note_id"))
    if "xiaohongshu.com" in url or item.get("note_id"):
        return False
    return "douyin.com" in url or bool(item.get("aweme_id")) or not url


def report_path_matches_platform(path: Path, platform: str) -> bool:
    has_xhs_part = "xiaohongshu" in path.parts
    return has_xhs_part if platform == "xiaohongshu" else not has_xhs_part


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_crawler_report(platform: str, since: float = 0) -> Path:
    reports = sorted(
        [
            path
            for path in settings.crawler_dir.glob("output_date/**/douyin_video_metadata.json")
            if path.stat().st_mtime >= since and report_path_matches_platform(path, platform)
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        try:
            data = load_report(path)
        except Exception:
            continue
        items = data.get("items") or []
        if all(item_matches_platform(item, platform) for item in items):
            return path
    raise RuntimeError(f"{platform} crawler did not write a matching report")


def engagement_score(item: dict[str, Any]) -> float:
    return (
        float(item.get("digg_count") or 0)
        + float(item.get("comment_count") or 0) * 1.5
        + float(item.get("share_count") or 0) * 2
    )


def normalize_items(items: list[dict[str, Any]], platform: str, limit: int) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for item in items:
        if not item_matches_platform(item, platform):
            continue
        key = str(item.get("aweme_id") or item.get("note_id") or item.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        row = dict(item)
        row["platform"] = platform
        row["platform_label"] = PLATFORM_LABELS.get(platform, platform)
        row["title"] = row.get("title") or row.get("desc") or ""
        row["desc"] = row.get("desc") or row.get("title") or ""
        row["interaction_score"] = round(engagement_score(row), 1)
        rows.append(row)
    rows.sort(key=lambda row: (float(row.get("interaction_score") or 0), -float(row.get("rank") or 9999)), reverse=True)
    for index, row in enumerate(rows[:limit], 1):
        row["rank"] = index
    return rows[:limit]


def normalize_report(report_path: Path, action: str, platform: str, log: str, limit: int) -> dict[str, Any]:
    report = load_report(report_path)
    items = normalize_items(report.get("items") or [], platform, limit)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "source": "social_video_crawler",
        "platform": platform,
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "crawler_dir": str(settings.crawler_dir),
        "report_path": str(report_path),
        "query": report.get("query", ""),
        "requested_limit": limit,
        "count": len(items),
        "actual_count": len(items),
        "keyword_stats": report.get("keyword_stats") or [],
        "items": items,
        "log": log,
    }


def search(platform: str, keyword: str, limit: int, deep: bool = False) -> dict[str, Any]:
    if platform not in PLATFORM_SCRIPTS:
        raise ValueError(f"unsupported platform: {platform}")
    if not (settings.crawler_dir / PLATFORM_SCRIPTS[platform]).exists():
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "action": "search",
            "source": "missing_optional_crawler",
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            "query": keyword,
            "requested_limit": limit,
            "count": 0,
            "actual_count": 0,
            "items": [],
            "log": (
                f"未找到内置抓取器：{settings.crawler_dir / PLATFORM_SCRIPTS[platform]}。\n"
                "独立版可先使用手动导入链接或上传本地样本；真实平台检索需要配置 SOCIAL_CRAWLER_DIR。"
            ),
        }
        out = manifest_path("search", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        return manifest
    started_at = time.time() - 1
    cmd = [
        python_executable(),
        PLATFORM_SCRIPTS[platform],
        "--keywords",
        keyword,
        "--limit",
        str(limit),
        "--no-download",
        "--comments-limit",
        "0",
    ]
    if platform == "xiaohongshu" and not deep:
        cmd.append("--list-only")
    cmd.extend(["--date-folder", "--run-name", f"search_{platform}_{safe_stem(keyword)}"])
    log = run_command(cmd, settings.crawler_dir)
    manifest = normalize_report(latest_crawler_report(platform, started_at), "search", platform, log, limit)
    manifest["deep_enriched"] = bool(deep)
    out = manifest_path("search", platform, keyword)
    manifest["_manifest_path"] = str(write_json(out, manifest))
    if not manifest.get("items"):
        hint = (
            "请先在 Chrome 登录抖音后重试。"
            if platform == "douyin"
            else "请确认 Chrome 中的小红书账号仍为登录状态，并检查筛选页是否能正常打开。"
        )
        raise RuntimeError(f"{PLATFORM_LABELS.get(platform, platform)}没有检索到可用视频。{hint}")
    return manifest


def download_selected(platform: str, keyword: str, urls: list[str], limit: int = 1) -> dict[str, Any]:
    if platform not in PLATFORM_SCRIPTS:
        raise ValueError(f"unsupported platform: {platform}")
    clean_urls = [url.strip() for url in urls if url and url.strip()]
    if not clean_urls:
        raise ValueError("no urls selected")
    if not (settings.crawler_dir / PLATFORM_SCRIPTS[platform]).exists():
        items = [
            {
                "rank": index,
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "url": url,
                "title": f"{keyword} 参考链接 {index}",
                "download_ok": False,
            }
            for index, url in enumerate(clean_urls[:limit], start=1)
        ]
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "action": "download",
            "source": "missing_optional_crawler",
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            "query": keyword,
            "count": len(items),
            "items": items,
            "downloaded_count": 0,
            "download_dir": "",
            "log": "未配置抓取器，不能从平台链接下载视频。请上传本地样本或配置 SOCIAL_CRAWLER_DIR。",
        }
        out = manifest_path("download", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        return manifest
    started_at = time.time() - 1
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        urls_file = Path(handle.name)
        handle.write("\n".join(clean_urls) + "\n")
    try:
        cmd = [
            python_executable(),
            PLATFORM_SCRIPTS[platform],
            "--url-file",
            str(urls_file),
            "--limit",
            str(min(limit, len(clean_urls))),
            "--comments-limit",
            "0",
            "--date-folder",
            "--run-name",
            f"selected_{platform}_{safe_stem(keyword)}",
        ]
        log = run_command(cmd, settings.crawler_dir)
    finally:
        urls_file.unlink(missing_ok=True)
    manifest = normalize_report(latest_crawler_report(platform, started_at), "download-selected", platform, log, limit)
    downloaded = [item for item in manifest["items"] if item.get("download_ok") and item.get("download_path")]
    manifest["downloaded_count"] = len(downloaded)
    manifest["download_dir"] = str(Path(downloaded[0]["download_path"]).parent) if downloaded else ""
    out = manifest_path("download", platform, keyword)
    manifest["_manifest_path"] = str(write_json(out, manifest))
    if not manifest.get("items") or not manifest.get("downloaded_count"):
        raise RuntimeError(
            f"{PLATFORM_LABELS.get(platform, platform)}未能解析并下载选中的视频。"
            "链接可能已失效、作品不是视频，或当前浏览器登录会话无权访问详情页。"
        )
    return manifest


def preview_url(item: dict[str, Any]) -> str:
    for key in ("play_url", "video_url", "download_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("play_urls", "video_urls", "download_urls"):
        for value in item.get(key) or []:
            if value:
                return str(value)
    return ""


def cover_url(item: dict[str, Any]) -> str:
    for key in ("cover_url", "origin_cover_url", "dynamic_cover_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("cover_urls", "origin_cover_urls", "dynamic_cover_urls"):
        for value in item.get(key) or []:
            if value:
                return str(value)
    return ""
