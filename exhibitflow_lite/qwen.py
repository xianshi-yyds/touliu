from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI

from .config import settings
from .storage import manifest_path, write_json


def post_no_proxy(*args, **kwargs):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(*args, **kwargs)
    finally:
        session.close()


def require_key() -> str:
    if not settings.dashscope_api_key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY. Put it in .env or environment variables.")
    return settings.dashscope_api_key


def openai_client() -> OpenAI:
    return OpenAI(api_key=require_key(), base_url=settings.dashscope_base_url)


def generate_copy(topic: str, sample: dict[str, Any] | None = None) -> str:
    sample = sample or {}
    prompt = f"""
你是展会短视频文案策划。请基于下面信息生成一段 20-35 秒短视频口播文案。

主题：{topic}
参考标题：{sample.get('title') or sample.get('desc') or ''}
参考数据：点赞 {sample.get('digg_count') or 0}，评论 {sample.get('comment_count') or 0}，转发 {sample.get('share_count') or 0}

要求：
1. 开头 3 秒要有现场感。
2. 面向展商/观众，不要写成泛泛广告。
3. 输出只给成片口播文案，不要解释。
""".strip()
    url = settings.dashscope_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.qwen_text_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        response = post_no_proxy(
            url,
            headers={"Authorization": f"Bearer {require_key()}", "Content-Type": "application/json"},
            json=payload,
            timeout=(8, 45),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Qwen copy generation failed: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"Qwen copy generation failed: HTTP {response.status_code} {response.text[:800]}")
    data = response.json()
    try:
        return data["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        raise RuntimeError(f"Qwen copy generation failed: invalid response {json.dumps(data, ensure_ascii=False)[:800]}") from exc


def generate_search_terms(topic: str, script: str, amount: int = 5) -> list[str]:
    """Generate the same short English stock-video queries used by MoneyPrinter."""
    prompt = f"""
Generate {amount} English stock-video search terms for one coherent short video.
Return only a JSON array of strings. Each term must contain 1-3 English words.

Subject: {topic}
Script: {script}
""".strip()
    try:
        completion = openai_client().chat.completions.create(
            model=settings.qwen_text_model,
            messages=[{"role": "user", "content": prompt}],
            timeout=12,
        )
        raw = completion.choices[0].message.content or ""
        match = re.search(r"\[[\s\S]*?\]", raw)
        if match:
            terms = json.loads(match.group(0))
            clean = [str(term).strip() for term in terms if str(term).strip()][:amount]
            if clean:
                return clean
    except Exception:
        # Keep the MoneyPrinter material chain usable during transient LLM/network failures.
        pass
    fallback = ["business exhibition", "trade show crowd", "exhibition booth", "business networking", "conference venue"]
    return fallback[:amount]


def data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "video/mp4"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def analyze_video(video_path: str, prompt: str = "分析这段展会短视频的镜头结构、内容节奏和可复刻点。") -> dict[str, Any]:
    path = Path(video_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(video_path)
    if path.stat().st_size > 7 * 1024 * 1024:
        raise RuntimeError("OpenAI compatible base64 video should be under 7MB. Compress or upload to OSS first.")
    completion = openai_client().chat.completions.create(
        model=settings.qwen_vl_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": data_url(path)}, "fps": 2},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    result = {
        "video_path": str(path),
        "model": settings.qwen_vl_model,
        "result": completion.choices[0].message.content or "",
    }
    out = manifest_path("qwen-vl", "local", path.stem)
    result["_manifest_path"] = str(write_json(out, result))
    return result


def synthesize_speech(text: str, voice: str = "Cherry", output_name: str = "qwen-tts.mp3") -> Path:
    api_key = require_key()
    out = settings.storage_dir / "tts" / output_name
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": settings.qwen_tts_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": text},
                    ],
                }
            ]
        },
        "parameters": {
            "voice": voice,
            "language_type": "Chinese",
        },
    }
    response = post_no_proxy(
        settings.dashscope_multimodal_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(response.text)
    data = response.json()
    audio = (
        data.get("output", {})
        .get("audio", {})
        or data.get("output", {})
        .get("choices", [{}])[0]
        .get("message", {})
        .get("audio", {})
    )
    audio_url = audio.get("url") if isinstance(audio, dict) else ""
    audio_data = audio.get("data") if isinstance(audio, dict) else ""
    if audio_data:
        out.write_bytes(base64.b64decode(audio_data))
    elif audio_url:
        media = requests.get(audio_url, timeout=120)
        media.raise_for_status()
        out.write_bytes(media.content)
    else:
        raise RuntimeError(f"Qwen TTS response missing audio: {data}")
    return out
