"""Tencent Marketing API adapter used by the Video Channels delivery path.

The first integration is deliberately read-only: ``adcreatives/get`` can
inspect the advertiser's creative library before a future create/update flow
is enabled.  Publishing or spending is never triggered by this module.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import requests

from .config import settings


CREATIVES_GET_ENDPOINT = "/v1.3/adcreatives/get"
DEFAULT_TIMEOUT = (8, 45)


class TencentAdsError(RuntimeError):
    """A safe, user-facing Tencent Ads error."""


def access_token(payload: dict[str, Any] | None = None) -> str:
    body = payload or {}
    return str(body.get("access_token") or settings.tencent_ads_access_token or "").strip()


def configured() -> bool:
    return bool(access_token())


def public_status() -> dict[str, Any]:
    token_present = bool(access_token())
    account_id = settings.tencent_ads_account_id
    return {
        "configured": token_present,
        "ready": bool(token_present and account_id),
        "has_access_token": token_present,
        "has_refresh_token": bool(settings.tencent_ads_refresh_token),
        "account_id": account_id,
        "base_url": settings.tencent_ads_base_url,
        "supports_creatives_get": True,
        "supports_video_channels_placement": True,
        "oauth_required": True,
        "message": (
            "已配置腾讯广告访问令牌，可读取创意库。"
            if token_present
            else "未配置腾讯广告访问令牌；视频号投放暂保持人工确认。"
        ),
    }


def _json_param(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _filtering(payload: dict[str, Any]) -> Any:
    if payload.get("filtering") not in (None, "", [], {}):
        return payload["filtering"]
    field = str(payload.get("field") or "").strip()
    operator = str(payload.get("operator") or "").strip()
    values = payload.get("values")
    if field or operator or values not in (None, "", [], {}):
        return {"field": field, "operator": operator, "values": values or []}
    return ""


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("adcreatives", "creatives", "items", "list", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows = _extract_list(value)
            if rows:
                return rows
    return []


def _extract_page_info(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("page_info", "pageInfo", "pagination"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    data = payload.get("data")
    return _extract_page_info(data) if isinstance(data, dict) else {}


def _error_message(response: requests.Response, payload: Any = None) -> str:
    body = payload if isinstance(payload, dict) else {}
    code = body.get("code")
    message = str(body.get("message") or body.get("msg") or body.get("error") or "腾讯广告 API 请求失败").strip()
    if response.status_code in {401, 403} or code in {401, 403}:
        return "腾讯广告访问令牌无效或没有创意读取权限，请重新授权并检查广告主权限。"
    if response.status_code == 429 or code == 429:
        return "腾讯广告 API 请求频率超限，请稍后重试。"
    return message


def _request(endpoint: str, params: dict[str, Any], *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = access_token(payload)
    if not token:
        raise TencentAdsError("未配置 TENCENT_ADS_ACCESS_TOKEN")
    query = {key: value for key, value in params.items() if value not in (None, "", [], {})}
    query.setdefault("access_token", token)
    query.setdefault("timestamp", int(time.time()))
    query.setdefault("nonce", uuid.uuid4().hex[:16])
    url = f"{settings.tencent_ads_base_url}{endpoint}"
    try:
        response = requests.get(
            url,
            params=query,
            headers={
                "access_token": token,
                "timestamp": str(query["timestamp"]),
                "nonce": str(query["nonce"]),
                "fields": str(query.get("fields") or ""),
                "Content-Type": "application/json",
                "User-Agent": "ExhibitFlow/1.0",
            },
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TencentAdsError(f"腾讯广告 API 网络请求失败：{exc.__class__.__name__}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise TencentAdsError("腾讯广告 API 返回了无法解析的 JSON") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise TencentAdsError(_error_message(response, data))
    if isinstance(data, dict):
        code = data.get("code")
        # Tencent commonly uses 0 for success; some compatible gateways use
        # 200 or omit code entirely.
        if code not in (None, 0, 200, "0", "200") or data.get("success") is False:
            raise TencentAdsError(_error_message(response, data))
    if not isinstance(data, dict):
        raise TencentAdsError("腾讯广告 API 返回格式异常")
    return data


def get_creatives(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    account_id = str(body.get("account_id") or settings.tencent_ads_account_id or "").strip()
    if not account_id:
        raise TencentAdsError("请先配置腾讯广告 account_id")
    fields = body.get("fields") or []
    filtering = _filtering(body)
    params: dict[str, Any] = {
        "account_id": account_id,
        "filtering": _json_param(filtering) if filtering not in (None, "", [], {}) else "",
        "page": max(1, int(body.get("page") or 1)),
        "page_size": max(1, min(int(body.get("page_size") or 20), 100)),
        "is_deleted": body.get("is_deleted", 0),
    }
    if fields:
        params["fields"] = ",".join(map(str, fields)) if isinstance(fields, (list, tuple)) else fields
    response = _request(CREATIVES_GET_ENDPOINT, params, payload=body)
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    items = _extract_list(data)
    return {
        "ok": True,
        "provider": "tencent_ads",
        "platform": "weixin_channels",
        "platform_label": "视频号",
        "account_id": account_id,
        "count": len(items),
        "items": items,
        "page_info": _extract_page_info(data),
        "raw": response,
    }
