"""Digital-human / AI-video supplement ("AI 自动补充") integration layer.

This module is the seam for the reference design's 数字人 / AI视频 controls.
It stays **dormant** until a provider and its credentials are configured, so
the composer keeps those toggles greyed as "即将上线".  When a key is present,
``avatar_available()`` flips to True and the creator pipeline can request an
avatar clip via ``generate_avatar_clip()``.

Currently a real HeyGen v2 flow is implemented (create → poll → download).
Volcengine (火山) is stubbed with a clear "not yet wired" error so the two
providers share one call site.  No credentials ship in code; supply them in
``.env`` (see ``.env.example``).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from .config import settings

# Terminal states are matched loosely so a provider adding a new success/fail
# label does not silently hang the poll loop.
_HEYGEN_DONE = {"completed", "success", "succeeded"}
_HEYGEN_FAILED = {"failed", "error"}


class AvatarNotConfigured(RuntimeError):
    """Raised when an avatar clip is requested but no provider is configured."""


def avatar_available() -> bool:
    """True when a provider is selected and its minimum credentials exist."""
    provider = settings.avatar_provider
    if provider == "heygen":
        return bool(settings.heygen_api_key and settings.heygen_avatar_id)
    if provider == "volc":
        return bool(settings.volc_avatar_base_url and settings.volc_avatar_token and settings.volc_avatar_id)
    return False


def avatar_status() -> dict[str, object]:
    """Compact status for the config endpoint so the UI can un-grey the toggle."""
    return {
        "provider": settings.avatar_provider or "",
        "configured": avatar_available(),
    }


def _require_configured() -> None:
    if not avatar_available():
        provider = settings.avatar_provider or "未设置"
        raise AvatarNotConfigured(
            f"数字人/AI视频未配置（AVATAR_PROVIDER={provider}）。"
            "请在 .env 配置 HEYGEN_API_KEY + HEYGEN_AVATAR_ID（或火山 VOLC_AVATAR_* ）后重试。"
        )


def _http_json(url: str, *, method: str, headers: dict[str, str], payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"数字人服务返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"数字人服务连接失败：{exc.reason}") from exc
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"数字人服务返回无法解析：{body[:300]}") from exc


def _download(url: str, output: Path, timeout: int = 180) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            output.write_bytes(response.read())
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"数字人视频下载失败：{exc}") from exc
    return output


def _generate_heygen(script: str, output: Path, *, voice: str | None, poll_seconds: int, max_polls: int) -> Path:
    base = settings.heygen_base_url
    headers = {"X-Api-Key": settings.heygen_api_key, "Content-Type": "application/json"}
    voice_id = (voice or settings.heygen_voice_id or "").strip()
    if not voice_id:
        raise AvatarNotConfigured("HeyGen 需要 HEYGEN_VOICE_ID（配音音色）才能生成数字人口播。")
    payload = {
        "video_inputs": [
            {
                "character": {"type": "avatar", "avatar_id": settings.heygen_avatar_id, "avatar_style": "normal"},
                "voice": {"type": "text", "input_text": script, "voice_id": voice_id},
            }
        ],
        "dimension": {"width": 720, "height": 1280},
    }
    created = _http_json(f"{base}/v2/video/generate", method="POST", headers=headers, payload=payload)
    video_id = str((created.get("data") or {}).get("video_id") or created.get("video_id") or "").strip()
    if not video_id:
        raise RuntimeError(f"HeyGen 未返回 video_id：{json.dumps(created, ensure_ascii=False)[:300]}")
    # Poll until the render reaches a terminal state, then download the mp4.
    for _ in range(max_polls):
        time.sleep(poll_seconds)
        status = _http_json(
            f"{base}/v1/video_status.get?video_id={video_id}",
            method="GET",
            headers=headers,
        )
        data = status.get("data") or {}
        state = str(data.get("status") or "").lower()
        if state in _HEYGEN_DONE:
            video_url = str(data.get("video_url") or "").strip()
            if not video_url:
                raise RuntimeError("HeyGen 生成完成但未返回视频地址。")
            return _download(video_url, output)
        if state in _HEYGEN_FAILED:
            raise RuntimeError(f"HeyGen 生成失败：{data.get('error') or state}")
    raise RuntimeError("HeyGen 生成超时，请稍后在历史记录中重试。")


def generate_avatar_clip(
    script: str,
    output_dir: Path,
    *,
    voice: str | None = None,
    poll_seconds: int = 6,
    max_polls: int = 60,
) -> Path:
    """Render a vertical digital-human clip for ``script`` and return its path.

    Raises ``AvatarNotConfigured`` when no provider/credentials are set so the
    caller can fall back to library material without a hard failure.
    """
    _require_configured()
    script = str(script or "").strip()
    if not script:
        raise ValueError("数字人口播脚本不能为空。")
    output = Path(output_dir) / f"avatar-{uuid4().hex[:10]}.mp4"
    if settings.avatar_provider == "heygen":
        return _generate_heygen(script, output, voice=voice, poll_seconds=poll_seconds, max_polls=max_polls)
    # Volcengine (火山) avatar wiring is intentionally deferred; the seam exists
    # so enabling it is a localized change, not a new integration point.
    raise AvatarNotConfigured(
        "火山数字人接入尚未完成。请先使用 HeyGen（AVATAR_PROVIDER=heygen），或等待火山适配上线。"
    )
