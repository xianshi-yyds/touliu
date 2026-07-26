"""Rnote (小红书/RedNote) public-data adapter.

Rnote exposes public Xiaohongshu search data through a small REST API.  This
adapter intentionally owns only public search/normalisation; it does not try
to automate a user's account or publishing flow.  That keeps the hunter
employee independent from the delivery employee's account permissions.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import requests

from .config import settings


SEARCH_ENDPOINT = "/search/notes"
DEFAULT_TIMEOUT = (8, 45)
PAGE_SIZE_HINT = 20
REQUEST_COST_USD = 0.01


class RnoteError(RuntimeError):
    """A safe, user-facing Rnote error without echoing API credentials."""

    def __init__(self, message: str, *, status_code: int = 0, debug_id: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.debug_id = debug_id


def configured() -> bool:
    return bool(settings.rnote_api_key)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return candidate
    return ""


def _first_url(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            result = _first_url(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("url", "url_default", "url_list", "origin_url", "src", "uri"):
            result = _first_url(value.get(key))
            if result:
                return result
    return ""


def _number(value: Any) -> int | float:
    if value in (None, "", False):
        return 0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    return int(numeric) if numeric.is_integer() else numeric


def _recursive_lists(value: Any) -> list[list[dict[str, Any]]]:
    """Find likely item lists without assuming one response envelope.

    Rnote has kept the top-level ``success/data`` envelope stable, while the
    exact list key can differ between search result versions.  Only return
    lists whose members are dictionaries so metadata arrays are ignored.
    """
    found: list[list[dict[str, Any]]] = []
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        found.append(value)
    elif isinstance(value, dict):
        preferred = ("items", "notes", "results", "list", "data")
        for key in preferred:
            child = value.get(key)
            if isinstance(child, list) and all(isinstance(item, dict) for item in child):
                found.append(child)
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_recursive_lists(child))
    return found


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    lists = _recursive_lists(payload)
    if not lists:
        return []
    # Prefer the longest candidate; a metadata list is normally much shorter
    # than the actual notes list.
    return max(lists, key=len)


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    info = _as_dict(
        _first(row, "interact_info", "interaction", "interactions", "stats", "statistics")
    )
    return {
        "digg_count": _number(_first(info, "liked_count", "like_count", "likes", "digg_count") or _first(row, "liked_count", "like_count", "likes", "digg_count")),
        "comment_count": _number(_first(info, "comment_count", "comments", "comment") or _first(row, "comment_count", "comments", "comment")),
        "share_count": _number(_first(info, "share_count", "shares", "share") or _first(row, "share_count", "shares", "share")),
        "collect_count": _number(_first(info, "collected_count", "collect_count", "collects", "favorites") or _first(row, "collected_count", "collect_count", "collects", "favorites")),
    }


def normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    """Map Rnote's public note shape to the gallery's common item shape."""
    note_id = str(
        _first(row, "note_id", "id", "noteId", "noteID")
        or ""
    ).strip()
    title = str(_first(row, "title", "note_title", "name") or "").strip()
    desc = str(_first(row, "desc", "description", "content", "text") or title).strip()
    author = _as_dict(_first(row, "user", "author", "creator", "user_info"))
    author_name = str(
        _first(author, "nickname", "nick_name", "name", "username")
        or _first(row, "nickname", "author_name", "user_name")
        or ""
    ).strip()
    cover_url = _first_url(
        _first(row, "cover", "cover_url", "image", "image_url", "images", "thumbnail")
    )
    video_url = _first_url(
        _first(row, "video", "video_url", "play_url", "media", "media_url", "download_url")
    )
    metrics = _metrics(row)
    item = {
        "platform": "xiaohongshu",
        "platform_label": "小红书",
        "note_id": note_id,
        "url": str(
            _first(row, "url", "share_url", "note_url")
            or (f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "")
        ).strip(),
        "share_url": str(_first(row, "share_url", "url", "note_url") or "").strip(),
        "title": title or desc,
        "desc": desc or title,
        "author_name": author_name,
        "author_nickname": author_name,
        "cover_url": cover_url,
        "video_url": video_url,
        "play_url": video_url,
        "download_url": video_url,
        **metrics,
        "create_time": _first(row, "create_time", "time", "created_at", "publish_time") or 0,
        "source_item": row,
    }
    return item


def _error_message(response: requests.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = _as_dict(body)
    detail = _as_dict(body.get("error"))
    debug_id = str(body.get("debug_id") or body.get("request_id") or "").strip()
    message = str(
        detail.get("message")
        or body.get("message")
        or body.get("detail")
        or "Rnote 请求失败"
    ).strip()
    if response.status_code in {401, 403}:
        message = "Rnote API Key 无效或没有访问权限，请检查 RNOTE_API_KEY。"
    elif response.status_code == 402:
        message = "Rnote 账户余额或套餐额度不足，请检查 Rnote 账户。"
    elif response.status_code == 429:
        message = "Rnote 请求频率超限，请稍后重试。"
    return message, debug_id


def _request(params: dict[str, Any]) -> dict[str, Any]:
    if not configured():
        raise RnoteError("未配置 RNOTE_API_KEY")
    url = f"{settings.rnote_base_url}{SEARCH_ENDPOINT}"
    try:
        response = requests.get(
            url,
            headers={
                "X-API-Key": settings.rnote_api_key,
                "Accept": "application/json",
                "User-Agent": "ExhibitFlow/1.0",
            },
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RnoteError(f"Rnote 网络请求失败：{exc.__class__.__name__}") from exc
    if response.status_code < 200 or response.status_code >= 300:
        message, debug_id = _error_message(response)
        raise RnoteError(message, status_code=response.status_code, debug_id=debug_id)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RnoteError("Rnote 返回了无法解析的 JSON") from exc
    if not isinstance(payload, dict):
        raise RnoteError("Rnote 返回格式异常")
    if payload.get("success") is False:
        message, debug_id = _error_message(response)
        raise RnoteError(message, status_code=response.status_code, debug_id=debug_id)
    return payload


def search(
    keyword: str,
    limit: int,
    *,
    deep: bool = False,
    note_type: int = 1,
) -> dict[str, Any]:
    keyword = str(keyword or "").strip()
    limit = max(1, min(int(limit or 10), 50))
    if not keyword:
        raise RnoteError("小红书检索关键词不能为空")

    # Rnote's public search is page based.  Keep deep mode bounded so a user
    # cannot accidentally create an unbounded paid crawl.
    max_pages = max(1, min(math.ceil(limit / PAGE_SIZE_HINT), 5 if deep else 3))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    logs: list[str] = []
    for page in range(1, max_pages + 1):
        response = _request(
            {
                "keyword": keyword,
                "page": page,
                "sort": "popularity_descending",
                "note_type": max(0, min(int(note_type), 2)),
            }
        )
        raw_rows = _candidate_rows(response.get("data") or response)
        added = 0
        for raw in raw_rows:
            item = normalize_item(raw)
            key = item.get("note_id") or item.get("url")
            if not key or str(key) in seen:
                continue
            seen.add(str(key))
            rows.append(item)
            added += 1
            if len(rows) >= limit:
                break
        logs.append(f"Rnote 第 {page} 页：返回 {len(raw_rows)} 条，新增 {added} 条")
        if len(rows) >= limit or not raw_rows:
            break

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": "search",
        "source": "rnote_xiaohongshu_search",
        "provider": "rnote",
        "platform": "xiaohongshu",
        "platform_label": "小红书",
        "query": keyword,
        "requested_limit": limit,
        "count": len(rows[:limit]),
        "actual_count": len(rows[:limit]),
        "items": rows[:limit],
        "pages": len(logs),
        "estimated_cost_usd": round(len(logs) * REQUEST_COST_USD, 4),
        "billing_note": "Rnote 小红书公开检索按请求计费；实际扣费以 Rnote 控制台为准。",
        "log": "\n".join(logs),
        "empty_result": not rows,
    }
