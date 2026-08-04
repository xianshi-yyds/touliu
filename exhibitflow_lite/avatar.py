"""Digital-human / AI-video supplement ("AI 自动补充") integration layer.

This module is the seam for the reference design's 数字人 / AI视频 controls.
It stays dormant until a provider and its credentials are configured. When a
key is present, ``avatar_available()`` flips to True and the creator pipeline
can request an avatar clip via ``generate_avatar_clip()``.

RunningHub's documented upload → create → poll → download flow is used for the
new Remotion topic-video path. A HeyGen compatibility flow remains available
for older integrations. No credentials ship in code; supply them in ``.env``.
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from .config import settings
from .storage import safe_stem

# Terminal states are matched loosely so a provider adding a new success/fail
# label does not silently hang the poll loop.
_HEYGEN_DONE = {"completed", "success", "succeeded"}
_HEYGEN_FAILED = {"failed", "error"}
_RUNNINGHUB_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi"}
_DIGITAL_HUMAN_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504, 525, 526}


class AvatarProviderTransientError(RuntimeError):
    """A provider/network error that is safe to retry."""


def _runninghub_code(response: dict) -> str:
    """Normalize RunningHub's code, which may be an int or a string.

    RunningHub returns numeric ``0`` for success.  Using ``value or ""``
    would turn that valid success code into an empty string and incorrectly
    report a successful task submission as a failure.
    """
    value = response.get("code")
    return "" if value is None else str(value).strip()


class AvatarNotConfigured(RuntimeError):
    """Raised when an avatar clip is requested but no provider is configured."""


def avatar_available() -> bool:
    """True when a provider is selected and its minimum credentials exist."""
    provider = settings.avatar_provider
    if provider == "heygen":
        return bool(settings.heygen_api_key and settings.heygen_avatar_id)
    if provider == "volc":
        return bool(settings.volc_avatar_base_url and settings.volc_avatar_token and settings.volc_avatar_id)
    if provider == "runninghub":
        return bool(
            settings.runninghub_api_key
            and settings.runninghub_api_url
            and settings.runninghub_digital_human_workflow_id
        )
    return False


def avatar_status() -> dict[str, object]:
    """Compact status for the config endpoint so the UI can un-grey the toggle."""
    return {
        "provider": settings.avatar_provider or "",
        "configured": avatar_available(),
        "image_required": settings.avatar_provider == "runninghub",
    }


def _require_configured() -> None:
    if not avatar_available():
        provider = settings.avatar_provider or "未设置"
        raise AvatarNotConfigured(
            f"数字人/AI视频未配置（AVATAR_PROVIDER={provider}）。"
            "请在 .env 配置 RunningHub 或 HeyGen 的服务端参数后重试。"
        )


def _http_json(url: str, *, method: str, headers: dict[str, str], payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        message = f"数字人服务返回 {exc.code}：{detail}"
        if exc.code in _RETRYABLE_HTTP_CODES:
            raise AvatarProviderTransientError(message) from exc
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"数字人服务连接失败：{exc.reason}") from exc
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"数字人服务返回无法解析：{body[:300]}") from exc


def _download(url: str, output: Path, timeout: int = 180) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                output.write_bytes(response.read())
            return output
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRYABLE_HTTP_CODES or attempt >= 3:
                break
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt >= 3:
                break
        time.sleep(min(8, 2 ** attempt))
    raise RuntimeError(f"数字人视频下载失败：{last_error}") from last_error


def digital_human_image_root() -> Path:
    """Return the only persistent directory accepted for avatar input images."""
    root = settings.storage_dir / "digital_human_images"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_digital_human_image(value: str | Path, *, expo_id: str = "") -> Path:
    """Resolve a browser-uploaded avatar image without accepting arbitrary paths.

    The API returns an id relative to ``storage/digital_human_images``.  The
    absolute-path branch is kept for internal jobs and is still constrained to
    that same directory.
    """
    raw = Path(str(value or "").strip()).expanduser()
    root = digital_human_image_root()
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("数字人形象必须位于数字人图片目录内")
    if candidate.suffix.lower() not in _DIGITAL_HUMAN_IMAGE_EXTENSIONS:
        raise ValueError("数字人形象仅支持 JPG、PNG 或 WEBP")
    if not candidate.is_file():
        raise FileNotFoundError(f"数字人形象不存在：{candidate.name}")
    if candidate.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("数字人形象不能超过 10MB")
    if expo_id:
        expected_scope = safe_stem(str(expo_id).strip()) or "global"
        if expected_scope not in candidate.parts and "global" not in candidate.parts:
            raise ValueError("数字人形象不属于当前展会")
    return candidate


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


def _multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----ExhibitFlow{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(),
            b"\r\n",
        ])
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _runninghub_upload(file_path: Path) -> str:
    if not file_path.is_file():
        raise FileNotFoundError(f"数字人输入文件不存在：{file_path}")
    body, content_type = _multipart_body({"apiKey": settings.runninghub_api_key}, "file", file_path)
    request = urllib.request.Request(
        f"{settings.runninghub_api_url}/task/openapi/upload",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.runninghub_api_key}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"RunningHub 输入文件上传失败：{exc}") from exc
    if _runninghub_code(payload) != "0":
        raise RuntimeError(f"RunningHub 输入文件上传失败：{json.dumps(payload, ensure_ascii=False)[:500]}")
    file_name = str((payload.get("data") or {}).get("fileName") or "").strip()
    if not file_name:
        raise RuntimeError(f"RunningHub 上传成功但没有返回 fileName：{json.dumps(payload, ensure_ascii=False)[:500]}")
    return file_name


def _runninghub_create_task(image_file_name: str, audio_file_name: str) -> str:
    node_info = [
        {
            "nodeId": settings.runninghub_digital_human_image_node_id,
            "fieldName": settings.runninghub_digital_human_image_field,
            "fieldValue": image_file_name,
        },
        {
            "nodeId": settings.runninghub_digital_human_audio_node_id,
            "fieldName": settings.runninghub_digital_human_audio_field,
            "fieldValue": audio_file_name,
        },
    ]
    payload = {
        "apiKey": settings.runninghub_api_key,
        "workflowId": settings.runninghub_digital_human_workflow_id,
        "instanceType": settings.runninghub_instance_type,
        "nodeInfoList": node_info,
    }
    attempts = max(1, min(120, int(settings.runninghub_queue_maxed_retry_count or 120)))
    for attempt in range(attempts):
        try:
            response = _http_json(
                f"{settings.runninghub_api_url}/task/openapi/create",
                method="POST",
                headers={
                    "Authorization": f"Bearer {settings.runninghub_api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout=120,
            )
        except RuntimeError as exc:
            # Some RunningHub deployments expose queue saturation as HTTP 421
            # instead of a JSON ``code: 421`` response.
            if "421" not in str(exc) and "TASK_QUEUE_MAXED" not in str(exc):
                raise
            response = {"code": "421", "message": str(exc)}
        code = _runninghub_code(response)
        message = json.dumps(response, ensure_ascii=False)
        queue_full = code == "421" or "TASK_QUEUE_MAXED" in message
        if queue_full:
            if attempt + 1 >= attempts:
                raise RuntimeError("RunningHub 全局队列已满，等待重试次数已用尽。")
            time.sleep(max(0.5, int(settings.runninghub_queue_maxed_retry_ms or 10000) / 1000))
            continue
        if code != "0":
            raise RuntimeError(f"RunningHub 数字人任务创建失败：{message[:700]}")
        task_id = str((response.get("data") or {}).get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError(f"RunningHub 创建成功但没有返回 taskId：{message[:500]}")
        return task_id
    raise RuntimeError("RunningHub 数字人任务创建失败。")


def _runninghub_outputs(task_id: str) -> list[dict]:
    response = _http_json(
        f"{settings.runninghub_api_url}/task/openapi/outputs",
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.runninghub_api_key}",
            "Content-Type": "application/json",
        },
        payload={"apiKey": settings.runninghub_api_key, "taskId": task_id},
        timeout=120,
    )
    code = _runninghub_code(response)
    if code == "804":
        return []
    if code == "805":
        raise RuntimeError(f"RunningHub 数字人任务失败：{json.dumps(response, ensure_ascii=False)[:700]}")
    if code in {str(value) for value in _RETRYABLE_HTTP_CODES}:
        raise AvatarProviderTransientError(
            f"RunningHub 输出查询暂时失败：{json.dumps(response, ensure_ascii=False)[:700]}"
        )
    if code != "0":
        raise RuntimeError(f"RunningHub 输出查询失败：{json.dumps(response, ensure_ascii=False)[:700]}")
    raw_data = response.get("data") or []
    if isinstance(raw_data, dict):
        raw_data = [raw_data]
    return [item for item in raw_data if isinstance(item, dict)]


def _generate_runninghub(
    image_file: Path,
    audio_file: Path,
    output: Path,
    *,
    poll_seconds: int,
    max_polls: int,
) -> Path:
    if image_file.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("数字人形象不能超过 10MB")
    if audio_file.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("数字人驱动音频不能超过 30MB")
    image_name, audio_name = _runninghub_upload(image_file), _runninghub_upload(audio_file)
    task_id = _runninghub_create_task(image_name, audio_name)
    transient_failures = 0
    for _ in range(max(1, int(max_polls or 1200))):
        time.sleep(max(0.5, int(poll_seconds or 2)))
        try:
            outputs = _runninghub_outputs(task_id)
            transient_failures = 0
        except AvatarProviderTransientError as exc:
            transient_failures += 1
            if transient_failures >= 12:
                raise RuntimeError(
                    f"RunningHub 输出查询连续失败 {transient_failures} 次：{exc}"
                ) from exc
            continue
        for item in outputs:
            file_url = str(item.get("fileUrl") or item.get("url") or "").strip()
            file_type = str(item.get("fileType") or Path(file_url).suffix).lower()
            normalized_type = file_type if file_type.startswith(".") else f".{file_type}"
            if file_url and (normalized_type in _RUNNINGHUB_VIDEO_EXTENSIONS or Path(file_url).suffix.lower() in _RUNNINGHUB_VIDEO_EXTENSIONS):
                return _download(file_url, output, timeout=600)
    raise RuntimeError("RunningHub 数字人任务超时，请稍后在历史记录中查看或重试。")


def generate_avatar_clip(
    script: str,
    output_dir: Path,
    *,
    voice: str | None = None,
    audio_file: Path | None = None,
    image_file: Path | None = None,
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
    if settings.avatar_provider == "runninghub":
        if image_file is None or not Path(image_file).is_file():
            raise ValueError("RunningHub 数字人需要先上传人物形象图片。")
        if audio_file is None or not Path(audio_file).is_file():
            raise ValueError("RunningHub 数字人需要本次生成的口播音频。")
        return _generate_runninghub(
            Path(image_file),
            Path(audio_file),
            output,
            poll_seconds=settings.runninghub_digital_human_poll_seconds,
            max_polls=settings.runninghub_digital_human_max_polls,
        )
    if settings.avatar_provider == "heygen":
        return _generate_heygen(script, output, voice=voice, poll_seconds=poll_seconds, max_polls=max_polls)
    # Volcengine (火山) avatar wiring remains a separate provider seam.
    raise AvatarNotConfigured(
        "当前只接入 RunningHub 和 HeyGen 数字人，请先配置其中一个提供方。"
    )
