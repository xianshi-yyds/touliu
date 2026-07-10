from __future__ import annotations

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

from exhibitflow_lite import pipeline, publisher, qwen, render, social, stock, tts
from exhibitflow_lite.config import settings
from exhibitflow_lite.storage import ensure_storage, latest_manifest, safe_stem, write_json


FRONTEND_DIR = settings.project_root / "frontend"
TASK_DIR = settings.storage_dir / "api_tasks"
TASKS: dict[str, dict[str, Any]] = {}
TASK_LOCK = threading.Lock()
_DASHSCOPE_HEALTH: dict[str, Any] = {"checked_at": 0.0, "valid": False, "status": "unchecked"}

OCEAN_BASE_URL = "https://api.oceanengine.com"
OCEAN_TOKEN_FILE = settings.storage_dir / "oceanengine" / "oauth.json"



def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def save_task(task: dict[str, Any]) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    write_json(task_path(task["id"]), task)


def load_tasks() -> None:
    ensure_storage()
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(TASK_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = task.get("result") or {}
        fake_success = (
            task.get("status") == "succeeded"
            and (
                result.get("ok") is False
                or result.get("source") == "missing_optional_crawler"
            )
        )
        if task.get("status") in {"queued", "running"}:
            task["status"] = "failed"
            task["finished_at"] = now()
            task["error"] = "服务重启，任务在执行完成前被中断，请重新提交。"
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


def require_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"请填写{label}")
    return value


DEFAULT_VIDEO_CTA = "点击下方链接，立即报名吧"


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


def media_url(path: str | Path) -> str:
    candidate = Path(path).expanduser().resolve()
    root = settings.project_root.resolve()
    if candidate != root and root not in candidate.parents:
        return ""
    return f"/media/{quote(str(candidate.relative_to(root)))}"


def parse_render_result(log: str) -> dict[str, Any]:
    marker = "__SUMMARY_JSON__"
    if marker not in log:
        return {"log": log}
    raw = log.rsplit(marker, 1)[-1].strip()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError:
        return {"log": log}
    final_video = str(summary.get("final_video") or "")
    if final_video:
        summary["preview_url"] = media_url(final_video)
    subtitle_vtt = str(summary.get("subtitle_vtt") or "")
    if subtitle_vtt:
        summary["subtitle_preview_url"] = media_url(subtitle_vtt)
    return {"summary": summary, "log": log[:4000]}


def requests_post_no_proxy(*args, **kwargs):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(*args, **kwargs)
    finally:
        session.close()


def dashscope_health() -> dict[str, Any]:
    """Validate DashScope with the same OpenAI-compatible chat endpoint used by copy generation."""
    global _DASHSCOPE_HEALTH
    if not settings.dashscope_api_key:
        return {"valid": False, "status": "missing"}
    if time.time() - float(_DASHSCOPE_HEALTH.get("checked_at") or 0) < 300:
        return {key: value for key, value in _DASHSCOPE_HEALTH.items() if key != "checked_at"}
    try:
        response = requests_post_no_proxy(
            settings.dashscope_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.qwen_text_model,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            },
            timeout=8,
        )
        valid = response.status_code == 200
        status = "ready" if valid else ("invalid_key" if response.status_code == 401 else f"http_{response.status_code}")
    except requests.RequestException as exc:
        valid = False
        status = f"unreachable:{exc.__class__.__name__}"
    _DASHSCOPE_HEALTH = {"checked_at": time.time(), "valid": valid, "status": status}
    return {"valid": valid, "status": status}


def public_config() -> dict[str, Any]:
    materials = render.media_files(settings.default_material_dir)
    ai_health = dashscope_health()
    return {
        "project_root": str(settings.project_root),
        "storage_dir": str(settings.storage_dir),
        "material_dir": str(settings.default_material_dir),
        "crawler_configured": any((settings.crawler_dir / name).exists() for name in social.PLATFORM_SCRIPTS.values()),
        "publisher_configured": publisher.sau_available(),
        "render_engine": settings.render_engine,
        "qwen_text_model": settings.qwen_text_model,
        "qwen_vl_model": settings.qwen_vl_model,
        "qwen_tts_model": settings.qwen_tts_model,
        "dashscope_key_present": bool(settings.dashscope_api_key),
        "dashscope_configured": bool(ai_health.get("valid")),
        "dashscope_status": ai_health.get("status"),
        "material_count": len(materials),
        "moneyprint_stock_configured": bool(settings.pexels_api_key),
        "pixabay_stock_configured": bool(settings.pixabay_api_key),
        "ffmpeg_configured": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "oceanengine": ocean_config_public(),
    }


def list_materials() -> dict[str, Any]:
    settings.default_material_dir.mkdir(parents=True, exist_ok=True)
    allowed = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
    items = []
    for path in sorted(settings.default_material_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return {"count": len(items), "items": items}


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

    app_ready = bool(settings.oceanengine_app_id and settings.oceanengine_secret and settings.oceanengine_redirect_uri)
    if not app_ready:
        step("app", "系统应用配置", "failed", "后台还没有配置巨量 APP_ID、Secret 或回调地址。", "联系平台管理员配置开放平台应用")
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


def ocean_video_upload(payload: dict[str, Any]) -> dict[str, Any]:
    token = ocean_access_token(payload)
    advertiser_id = str(payload.get("advertiser_id") or load_ocean_token().get("advertiser_id") or settings.oceanengine_advertiser_id or "").strip()
    if not token:
        raise ValueError("缺少 Access Token，请先授权巨量账号")
    if not advertiser_id:
        raise ValueError("缺少 advertiser_id，请先获取已授权账户")
    video_file = require_text(payload, "video_file", "视频文件")
    path = Path(video_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"视频文件不存在：{path}")
    content = path.read_bytes()
    signature = hashlib.md5(content).hexdigest()
    boundary = "----ExhibitFlow" + uuid4().hex
    def field(name: str, value: Any) -> bytes:
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode("utf-8")
    body = b"".join([
        field("advertiser_id", advertiser_id),
        field("upload_type", "UPLOAD_BY_FILE"),
        field("video_signature", signature),
        field("filename", payload.get("filename") or path.name),
        field("is_aigc", "true" if payload.get("is_aigc") else "false"),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video_file\"; filename=\"{path.name}\"\r\nContent-Type: {mimetypes.guess_type(path.name)[0] or 'video/mp4'}\r\n\r\n").encode("utf-8"),
        content,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ])
    url = OCEAN_BASE_URL + "/open_api/2/file/video/ad/"
    raw = ""
    last_error: Exception | None = None
    for attempt in range(3):
        # Request objects are one-shot when a connection is interrupted, so a
        # fresh object must be created for every retry.
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
            raise RuntimeError(f"巨量视频上传 HTTP {exc.code}: {raw}") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2 * (attempt + 1))
    if last_error is not None:
        raise RuntimeError(f"巨量视频上传网络错误（已重试 5 次）：{last_error}") from last_error
    result = result or json.loads(raw)
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"巨量视频上传失败：{result}")
    data = result.get("data") or {}
    save_ocean_token({"last_video_id": data.get("video_id"), "last_material_id": data.get("material_id")})
    return {"ok": True, "video_signature": signature, "raw": result, **data}


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
    base = settings.storage_dir / "oceanengine" / "projects"
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
    required_state = {
        "advertiser_id": advertiser_id,
        "access_token": access_token,
        "landing_url": landing_url,
        "video_file": video_file,
        "budget": budget,
    }
    missing = [name for name, value in required_state.items() if not value]
    checks = [
        {"key": "auth", "label": "巨量广告主授权", "ok": bool(advertiser_id and access_token)},
        {"key": "landing", "label": "落地页 / 留资页", "ok": bool(landing_url)},
        {"key": "convert", "label": "转化目标自动匹配", "ok": bool(convert_id or saved.get("delivery_asset_verified"))},
        {"key": "video", "label": "投放视频", "ok": bool(video_file)},
        {"key": "budget", "label": "日预算", "ok": bool(budget)},
    ]
    project_name = payload.get("project_name") or payload.get("subject") or "展会投放项目"
    unit_name = payload.get("unit_name") or Path(video_file).stem or "视频投放单元"
    project_id = str(payload.get("project_id") or saved.get("last_project_id") or "").strip()
    unit_id = str(payload.get("unit_id") or payload.get("promotion_id") or saved.get("last_promotion_id") or "").strip()
    official_model = {
        "model": "project_with_units",
        "project": {
            "name": project_name,
            "project_id": project_id,
            "source": "official_project",
            "description": "当前展会对应一个官方投放项目。",
        },
        "unit": {
            "name": unit_name,
            "unit_id": unit_id,
            "promotion_id": unit_id,
            "video_file": video_file,
            "video_id": payload.get("video_id") or "",
            "source": "official_unit",
            "description": "每个产出视频对应项目下一个官方投放单元，数据按该单元回流。",
        },
        "report_scope": "unit_first_project_summary",
    }
    draft = {
        "created_at": now(),
        "ok": not missing,
        "status": "ready" if not missing else "needs_config",
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
            "video_id": payload.get("video_id") or "",
            "video_file": video_file,
            "goal": payload.get("goal") or "lead",
        },
        "dashboard": build_delivery_dashboard(payload.get("dashboard")),
        "recommendation": "草稿就绪后，按官方链路先确认项目 project，再在项目下为每个视频创建一个投放单元；真实投放产生数据后，先按单元复盘，再汇总到项目。",
        "next_steps": [
            "检查广告主授权",
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


def run_job(task_id: str, fn: Callable[[], Any]) -> None:
    with TASK_LOCK:
        task = TASKS[task_id]
        task["status"] = "running"
        task["started_at"] = now()
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
            task["result"] = result
            save_task(task)
    except Exception as exc:
        error = str(exc)
        if "Incorrect API key" in error or "invalid_api_key" in error:
            error = "阿里云百炼 API Key 无效或已失效，请更新 .env 后重启服务。"
        with TASK_LOCK:
            task = TASKS[task_id]
            task["status"] = "failed"
            task["finished_at"] = now()
            task["error"] = error
            task["traceback"] = traceback.format_exc()
            save_task(task)


def enqueue(kind: str, payload: dict[str, Any], fn: Callable[[], Any]) -> dict[str, Any]:
    task_id = (
        f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{kind}-"
        f"{safe_stem(payload.get('keyword') or payload.get('topic') or payload.get('subject') or 'task')}-"
        f"{uuid4().hex[:6]}"
    )
    task = {
        "id": task_id,
        "kind": kind,
        "status": "queued",
        "created_at": now(),
        "payload": public_payload(payload),
    }
    with TASK_LOCK:
        TASKS[task_id] = task
        save_task(task)
    thread = threading.Thread(target=run_job, args=(task_id, fn), daemon=True)
    thread.start()
    return task


def make_task(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "search":
        if not public_config()["crawler_configured"]:
            raise RuntimeError("真实平台抓取器未配置。请使用“导入链接”，或先安装抓取插件。")
        platform = payload.get("platform") or "douyin"
        keyword = require_text(payload, "keyword", "检索关键词")
        limit = int(payload.get("limit") or 10)
        if not 1 <= limit <= 50:
            raise ValueError("检索数量必须在 1 到 50 之间")
        deep = bool(payload.get("deep"))
        return enqueue(kind, payload, lambda: social.search(platform, keyword, limit, deep=deep))
    if kind == "import-links":
        platform = payload.get("platform") or "douyin"
        keyword = require_text(payload, "keyword", "样本主题")
        links = require_text(payload, "links", "至少一个视频链接")
        return enqueue(kind, payload, lambda: render.import_links(platform, keyword, links))
    if kind == "download":
        if not public_config()["crawler_configured"]:
            raise RuntimeError("下载能力未配置，请先安装抓取插件。")
        platform = payload.get("platform") or "douyin"
        keyword = payload.get("keyword") or ""
        urls = payload.get("urls") or []
        limit = int(payload.get("limit") or len(urls) or 1)
        return enqueue(kind, payload, lambda: social.download_selected(platform, keyword, urls, limit=limit))
    if kind == "creator-pipeline":
        topic = require_text(payload, "topic", "视频需求")
        sample = payload.get("sample") or {}
        source = str(payload.get("material_source") or "local").strip().lower()
        if source not in {"local", "pexels", "pixabay"}:
            raise ValueError("素材来源必须是 local、pexels 或 pixabay")
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)
        voice = str(payload.get("voice") or "zh-CN-XiaoxiaoNeural")
        tts_service = str(payload.get("tts_service") or "edge")
        caption_template = str(payload.get("caption_template") or "viral").strip().lower()
        if caption_template not in render.CAPTION_TEMPLATES:
            raise ValueError(f"未知字幕模板：{caption_template}")

        def creator_pipeline_job() -> dict[str, Any]:
            # 兼容前端的 manual_script 和直接 API 调用的 script。
            # 之前这里只读取 script，导致用户已经填写手动文案时仍然调用 Qwen，
            # 也会让手动文案链路误报为模型生成失败。
            manual_script = str(payload.get("script") or payload.get("manual_script") or "").strip()
            script = final_script_with_cta(manual_script or qwen.generate_copy(topic, sample), cta_text)
            sentences = render.split_sentences(script)
            sentence_audio: list[Path] = []
            for index, sentence in enumerate(sentences, start=1):
                sentence_audio.append(tts.synthesize(sentence, service=tts_service, voice=voice, output_name=f"pipeline-{tts_service}-{uuid4().hex[:10]}-part-{index:02d}.mp3"))
            audio = tts.concat_segments(sentence_audio, output_name=f"pipeline-{tts_service}-{uuid4().hex[:10]}.mp3", text=script, voice=voice)
            material_info: dict[str, Any] = {"source": source}
            sentence_material_dirs: list[str] = []
            if source in {"pexels", "pixabay"}:
                terms = qwen.generate_search_terms(topic, script, amount=5)
                online_dir = settings.storage_dir / "online_materials" / f"moneyprint-{uuid4().hex[:10]}"
                # 每句单独检索并下载，保留一个总目录用于渲染器兜底。
                for index, sentence in enumerate(sentences, start=1):
                    sentence_terms = qwen.generate_search_terms(sentence, sentence, amount=2) or terms[:2]
                    sentence_dir = online_dir / f"sentence-{index:02d}"
                    sentence_material_dirs.append(str(sentence_dir))
                    stock.download_moneyprinter_materials(sentence_terms, sentence_dir, target_duration=max(5.0, render.duration(sentence_audio[index - 1])), source=source, max_clip_duration=5)
                material_info.update({"material_dir": str(online_dir), "search_terms": terms, "sentence_material_dirs": sentence_material_dirs})
                material_dir = material_info["material_dir"]
            else:
                material_dir = require_text(payload, "material_dir", "本地素材目录")
                if not Path(material_dir).expanduser().exists():
                    raise FileNotFoundError(f"素材目录不存在：{material_dir}")
                material_info["material_dir"] = material_dir

            raw_highlights = payload.get("highlight_words") or []
            if isinstance(raw_highlights, str):
                raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
            highlights = [str(word).strip() for word in raw_highlights if str(word).strip()]
            parsed = parse_render_result(
                pipeline.render_video(
                    keyword=topic,
                    competitor_dir=str(payload.get("competitor_dir") or ""),
                    material_dir=material_dir,
                    name=str(payload.get("name") or f"creator-pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"),
                    tts_mode=tts_service,
                    script_mode="manual",
                    script=script,
                    audio_file=str(audio),
                    caption_template=caption_template,
                    highlight_words=highlights,
                    cta_text=cta_text,
                    sentence_material_dirs=sentence_material_dirs,
                )
            )
            return {
                "copy": script,
                "audio_path": str(audio),
                "audio_preview_url": media_url(audio),
                "summary": parsed.get("summary") or parsed,
                "material": material_info,
                "cta_text": cta_text,
            }

        return enqueue(kind, payload, creator_pipeline_job)
    if kind == "copy":
        topic = str(payload.get("topic") or payload.get("keyword") or "").strip()
        sample = payload.get("sample") or {}
        if not topic and not (sample.get("title") or sample.get("desc")):
            raise ValueError("请填写主题/卖点，或先选择一个参考样本")
        return enqueue(
            kind,
            payload,
            lambda: {
                "copy": final_script_with_cta(
                    qwen.generate_copy(topic, sample),
                    str(payload.get("cta_text") or DEFAULT_VIDEO_CTA),
                ),
                "cta_text": str(payload.get("cta_text") or DEFAULT_VIDEO_CTA),
            },
        )
    if kind == "tts":
        text = final_script_with_cta(
            require_text(payload, "text", "口播稿"),
            str(payload.get("cta_text") or DEFAULT_VIDEO_CTA),
        )
        voice = payload.get("voice") or "Cherry"
        service = str(payload.get("service") or "qwen")
        def tts_job() -> dict[str, Any]:
            audio = tts.synthesize(text, service=service, voice=voice, output_name=f"{service}-tts-{uuid4().hex[:10]}.mp3")
            return {"audio_path": str(audio), "preview_url": media_url(audio), "voice": voice, "service": service, "text": text}
        return enqueue(kind, payload, tts_job)
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
        raw_highlights = payload.get("highlight_words") or []
        if isinstance(raw_highlights, str):
            raw_highlights = re.split(r"[，,、\n]+", raw_highlights)
        highlight_words = [str(word).strip() for word in raw_highlights if str(word).strip()]
        cta_text = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)

        def render_job() -> dict[str, Any]:
            resolved_audio = audio_file
            if not resolved_audio and payload.get("auto_tts", True):
                resolved_audio = str(
                    tts.synthesize(
                        script,
                        service=str(payload.get("tts_service") or "qwen"),
                        voice=str(payload.get("voice") or "Cherry"),
                        output_name=f"render-tts-{uuid4().hex[:10]}.mp3",
                    )
                )
            parsed = parse_render_result(
                pipeline.render_video(
                    keyword=payload.get("keyword") or payload.get("subject") or "展会推广",
                    competitor_dir=payload.get("competitor_dir") or "",
                    material_dir=material_dir,
                    name=render_name,
                    tts_mode=payload.get("tts_mode") or ("qwen" if resolved_audio else "silent"),
                    script_mode=payload.get("script_mode") or "formula",
                    script=script,
                    audio_file=resolved_audio,
                    caption_template=caption_template,
                    highlight_words=highlight_words,
                    cta_text=cta_text,
                )
            )
            parsed["final_script"] = script
            parsed["cta_text"] = str(payload.get("cta_text") or DEFAULT_VIDEO_CTA)
            return parsed

        return enqueue(
            kind,
            payload,
            render_job,
        )
    if kind == "publish":
        if not public_config()["publisher_configured"]:
            raise RuntimeError("发布器未配置，当前不能提交真实发布任务。")
        video_file = require_text(payload, "video_file", "视频文件")
        require_text(payload, "title", "发布标题")
        return enqueue(
            kind,
            payload,
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
        return enqueue(kind, payload, lambda: delivery_draft(payload))
    if kind == "delivery-report":
        return enqueue(kind, payload, lambda: ocean_report(payload))
    if kind == "ocean-video-upload":
        return enqueue(kind, payload, lambda: ocean_video_upload(payload))
    if kind == "ocean-image-upload":
        return enqueue(kind, payload, lambda: ocean_image_upload(payload))
    if kind == "ocean-project-create":
        return enqueue(kind, payload, lambda: ocean_project_create(payload))
    if kind == "ocean-promotion-create":
        return enqueue(kind, payload, lambda: ocean_promotion_create(payload))
    if kind == "ocean-safe-launch":
        return enqueue(kind, payload, lambda: ocean_safe_delivery_test(payload))
    raise ValueError(f"unsupported task kind: {kind}")


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
            self.send_json(list_materials())
            return
        if path == "/api/latest":
            query = parse_qs(parsed.query)
            self.send_json(latest_manifest(action=(query.get("action") or [""])[0]))
            return
        if path == "/api/oceanengine/status":
            self.send_json({"ok": True, **ocean_config_public()})
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
            with TASK_LOCK:
                tasks = sorted(TASKS.values(), key=lambda item: item.get("created_at", ""), reverse=True)
            self.send_json({"count": len(tasks), "items": tasks[:100]})
            return
        if path.startswith("/api/tasks/"):
            task_id = unquote(path.rsplit("/", 1)[-1])
            with TASK_LOCK:
                task = TASKS.get(task_id)
            self.send_json(task or {"error": "task not found"}, status=200 if task else 404)
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
            if path == "/api/oceanengine/refresh-token":
                payload = self.read_payload()
                self.send_json(ocean_refresh_token(str(payload.get("refresh_token") or "")))
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
            if path == "/api/materials/upload":
                query = parse_qs(parsed.query)
                filename = Path((query.get("name") or [""])[0]).name
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
                self.send_json({"ok": True, "name": filename, "path": str(target), "size": target.stat().st_size}, status=201)
                return
            payload = self.read_payload()
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
    load_tasks()
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
