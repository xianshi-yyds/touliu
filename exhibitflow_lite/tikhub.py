"""TikHub adapters used by the independent social-search employee.

TikHub's Douyin search endpoint returns structured public data and does not
require the visitor's browser login.  Keep this provider isolated from the
legacy macOS/Chrome crawler so deployments can switch providers through
environment variables without changing the task API.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

import requests

from .config import settings


SEARCH_ENDPOINT = "/api/v1/douyin/search/fetch_video_search_v2"
DEFAULT_TIMEOUT = (8, 45)


class TikHubError(RuntimeError):
    """A safe, user-facing TikHub error without echoing request secrets."""

    def __init__(self, message: str, *, status_code: int = 0, code: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def configured() -> bool:
    return bool(settings.tikhub_api_key)


def _first_url(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            result = _first_url(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("url_list", "url", "uri"):
            result = _first_url(value.get(key))
            if result:
                return result
    return ""


def _number(value: Any) -> int | float:
    if value is None or value == "":
        return 0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    return int(numeric) if numeric.is_integer() else numeric


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    """Extract result rows across TikHub's V1/V2 response envelopes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("business_data", "items", "results", "aweme_list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("business_data", "items", "results", "aweme_list", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _nested_video(row: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(row.get("data"))
    return (
        _as_dict(row.get("aweme_info"))
        or _as_dict(data.get("aweme_info"))
        or _as_dict(data)
        or row
    )


def _response_meta(payload: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(payload.get("data"))
    containers = (payload, data, _as_dict(data.get("search_info")))
    result: dict[str, Any] = {}
    for container in containers:
        for key in ("cursor", "search_id", "backtrace", "has_more", "hasMore"):
            if key in container and container.get(key) not in (None, ""):
                result[key] = container.get(key)
    return result


def _aweme_id_from_url(value: str) -> str:
    match = re.search(r"/video/(\d+)", value or "")
    return match.group(1) if match else ""


def normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    aweme = _nested_video(row)
    author = _as_dict(aweme.get("author"))
    statistics = _as_dict(aweme.get("statistics"))
    video = _as_dict(aweme.get("video"))
    aweme_id = str(aweme.get("aweme_id") or row.get("aweme_id") or "").strip()
    desc = str(aweme.get("desc") or row.get("desc") or "").strip()
    share_url = str(
        aweme.get("share_url")
        or row.get("share_url")
        or (f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "")
    ).strip()
    play_url = _first_url(video.get("play_addr")) or _first_url(aweme.get("video_play_url"))
    cover_url = (
        _first_url(video.get("cover"))
        or _first_url(video.get("origin_cover"))
        or _first_url(video.get("dynamic_cover"))
        or _first_url(aweme.get("video_cover_url"))
    )
    duration_ms = _number(video.get("duration") or aweme.get("duration") or aweme.get("duration_ms"))
    duration_seconds = round(float(duration_ms) / 1000, 3) if float(duration_ms or 0) > 1000 else float(duration_ms or 0)
    return {
        "platform": "douyin",
        "platform_label": "抖音",
        "aweme_id": aweme_id,
        "url": share_url,
        "share_url": share_url,
        "title": desc,
        "desc": desc,
        "author_name": str(author.get("nickname") or aweme.get("author_nickname") or row.get("author_nickname") or "").strip(),
        "author_nickname": str(author.get("nickname") or aweme.get("author_nickname") or row.get("author_nickname") or "").strip(),
        "author_unique_id": str(author.get("unique_id") or author.get("short_id") or row.get("author_unique_id") or "").strip(),
        "author_uid": str(author.get("uid") or row.get("author_uid") or "").strip(),
        "cover_url": cover_url,
        "origin_cover_url": _first_url(video.get("origin_cover")) or cover_url,
        "dynamic_cover_url": _first_url(video.get("dynamic_cover")) or cover_url,
        "play_url": play_url,
        "video_url": play_url,
        "download_url": _first_url(video.get("download_addr")) or play_url,
        "duration": duration_seconds,
        "duration_seconds": duration_seconds,
        "digg_count": _number(statistics.get("digg_count") or aweme.get("digg_count") or row.get("digg_count")),
        "comment_count": _number(statistics.get("comment_count") or aweme.get("comment_count") or row.get("comment_count")),
        "share_count": _number(statistics.get("share_count") or aweme.get("share_count") or row.get("share_count")),
        "play_count": _number(statistics.get("play_count") or aweme.get("play_count") or row.get("play_count")),
        "collect_count": _number(statistics.get("collect_count") or aweme.get("collect_count") or row.get("collect_count")),
        "create_time": aweme.get("create_time") or row.get("create_time") or 0,
        "source_item": row,
    }


def _error_message(response: requests.Response) -> tuple[str, int]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else {}
    if not isinstance(detail, dict):
        detail = {}
    message = str(
        detail.get("message_zh")
        or body.get("message_zh")
        or detail.get("message")
        or body.get("message")
        or "TikHub 请求失败"
    ).strip()
    code = int(detail.get("code") or body.get("code") or response.status_code or 0)
    if response.status_code == 402 or code == 402:
        return "TikHub 搜索接口余额不足；该搜索接口不接受免费额度，请充值后重试。", code
    if response.status_code == 401 or code == 401:
        return "TikHub API Key 无效、未激活或已过期，请检查 TIKHUB_API_KEY。", code
    if response.status_code == 403 or code == 403:
        return "TikHub 当前账号没有访问该接口的权限。", code
    if response.status_code == 429 or code == 429:
        return "TikHub 请求频率超限，请稍后重试。", code
    return message, code


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    if not configured():
        raise TikHubError("未配置 TIKHUB_API_KEY")
    url = f"{settings.tikhub_base_url}{SEARCH_ENDPOINT}"
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.tikhub_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ExhibitFlow/1.0",
            },
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TikHubError(f"TikHub 网络请求失败：{exc.__class__.__name__}") from exc
    if response.status_code != 200:
        message, code = _error_message(response)
        raise TikHubError(message, status_code=response.status_code, code=code)
    try:
        data = response.json()
    except ValueError as exc:
        raise TikHubError("TikHub 返回了无法解析的 JSON") from exc
    if not isinstance(data, dict):
        raise TikHubError("TikHub 返回格式异常")
    code = int(data.get("code") or 200)
    if code != 200:
        message, parsed_code = _error_message(response)
        raise TikHubError(message, status_code=response.status_code, code=parsed_code or code)
    return data


def search(keyword: str, limit: int, *, deep: bool = False) -> dict[str, Any]:
    keyword = str(keyword or "").strip()
    limit = max(1, min(int(limit or 10), 50))
    if not keyword:
        raise TikHubError("检索关键词不能为空")

    # The dedicated search endpoint returns a bounded page.  Continue using
    # the returned cursor until the requested amount is collected, while
    # capping deep searches to avoid accidental credit explosions.
    page_size_hint = 8
    max_pages = max(1, min(10 if deep else math.ceil(limit / page_size_hint), 10))
    cursor: Any = 0
    search_id = ""
    backtrace = ""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    logs: list[str] = []

    for page in range(max_pages):
        payload = {
            "keyword": keyword,
            "cursor": cursor,
            "sort_type": "0",
            "publish_time": "0",
            "filter_duration": "0",
            "content_type": "1",
            "search_id": search_id,
            "backtrace": backtrace,
        }
        response = _request(payload)
        raw_rows = _candidate_rows(response)
        added = 0
        for raw in raw_rows:
            item = normalize_item(raw)
            key = item.get("aweme_id") or item.get("url")
            if not key or key in seen:
                continue
            seen.add(str(key))
            rows.append(item)
            added += 1
            if len(rows) >= limit:
                break
        logs.append(f"TikHub 第 {page + 1} 页：返回 {len(raw_rows)} 条，新增 {added} 条")
        if len(rows) >= limit or not raw_rows:
            break
        meta = _response_meta(response)
        next_cursor = meta.get("cursor")
        next_search_id = str(meta.get("search_id") or search_id)
        next_backtrace = str(meta.get("backtrace") or backtrace)
        has_more = meta.get("has_more", meta.get("hasMore", True))
        if next_cursor in (None, "", cursor) and next_search_id == search_id and next_backtrace == backtrace:
            break
        if has_more in (False, 0, "0", "false", "False"):
            break
        cursor = next_cursor if next_cursor not in (None, "") else cursor
        search_id = next_search_id
        backtrace = next_backtrace

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": "search",
        "source": "tikhub_douyin_search",
        "provider": "tikhub",
        "platform": "douyin",
        "platform_label": "抖音",
        "query": keyword,
        "requested_limit": limit,
        "count": len(rows[:limit]),
        "actual_count": len(rows[:limit]),
        "items": rows[:limit],
        "pages": len(logs),
        "estimated_cost_usd": round(len(logs) * 0.01, 4),
        "billing_note": "TikHub 抖音搜索接口按请求计费，当前文档价格为 $0.01/请求。",
        "log": "\n".join(logs),
        "empty_result": not rows,
    }

