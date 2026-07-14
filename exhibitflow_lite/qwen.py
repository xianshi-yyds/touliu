from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
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


def post_json_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: tuple[int, int] = (8, 45),
    attempts: int = 3,
) -> requests.Response:
    """Call an OpenAI-compatible endpoint with bounded retries.

    Authentication/validation errors are returned immediately. Transient
    network, timeout, rate-limit and 5xx failures are retried so a background
    task does not fail on the first short connection fluctuation.
    """
    errors: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = post_no_proxy(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            errors.append(f"第 {attempt} 次请求：{type(exc).__name__}: {exc}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
                continue
            raise RuntimeError(
                "模型服务网络连接失败（已重试 "
                f"{attempts} 次）：{' | '.join(errors[-3:])}"
            ) from exc

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < attempts:
            errors.append(f"第 {attempt} 次请求：HTTP {response.status_code}")
            time.sleep(min(2 ** (attempt - 1), 4))
            continue
        return response
    raise RuntimeError("模型服务请求失败")


def require_key() -> str:
    if not settings.dashscope_api_key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY. Put it in .env or environment variables.")
    return settings.dashscope_api_key


def require_deepseek_key() -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY. Put it in .env or environment variables.")
    return settings.deepseek_api_key


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
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.deepseek_text_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        response = post_json_with_retry(
            url,
            headers={"Authorization": f"Bearer {require_deepseek_key()}", "Content-Type": "application/json"},
            payload=payload,
            timeout=(8, 45),
            attempts=3,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"DeepSeek copy generation failed: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"DeepSeek copy generation failed: HTTP {response.status_code} {response.text[:800]}")
    data = response.json()
    try:
        return data["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        raise RuntimeError(f"DeepSeek copy generation failed: invalid response {json.dumps(data, ensure_ascii=False)[:800]}") from exc


def generate_search_terms_with_meta(topic: str, script: str, amount: int = 5) -> dict[str, Any]:
    """Generate MoneyPrinter-compatible global search terms with provenance.

    The previous implementation used the OpenAI SDK with a 12-second timeout
    and silently fell back to generic exhibition words.  Slow workspace models
    therefore produced technically valid videos whose footage had little to do
    with the user's subject.  Use the same bounded retry path as copy generation
    and report whether the terms came from the model or the fallback.
    """
    prompt = f"""
Generate {amount} English stock-video search terms for one coherent short video.
Return only a JSON array of strings. Each term must contain 1-3 English words.

Subject: {topic}
Script: {script}
""".strip()
    error = ""
    try:
        response = post_json_with_retry(
            settings.deepseek_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {require_deepseek_key()}", "Content-Type": "application/json"},
            payload={
                "model": settings.deepseek_text_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=(8, 45),
            attempts=3,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        raw = str(data["choices"][0]["message"].get("content") or "")
        match = re.search(r"\[[\s\S]*?\]", raw)
        if match:
            terms = json.loads(match.group(0))
            clean = [str(term).strip() for term in terms if str(term).strip()][:amount]
            if clean:
                return {"terms": clean, "source": "model", "error": ""}
        error = "模型返回内容不是 JSON 字符串数组"
    except Exception as exc:
        error = str(exc)
    # Keep the material chain usable, but expose the fallback instead of
    # pretending that generic terms were generated from the current topic.
    context = f"{topic} {script}".lower()
    category_terms: list[str] = []
    category_rules = [
        (("新能源", "电动车", "汽车", "充电", "automotive", "vehicle"), ["electric vehicle", "auto show", "charging station", "automotive technology"]),
        (("食品", "餐饮", "饮料", "茶", "food", "beverage"), ["food expo", "food sampling", "beverage exhibition", "food industry"]),
        (("机器人", "人工智能", "ai", "智能制造", "科技"), ["robot exhibition", "artificial intelligence", "technology expo", "smart manufacturing"]),
        (("医疗", "医药", "健康", "medical", "health"), ["medical exhibition", "healthcare technology", "medical equipment", "health conference"]),
        (("家居", "家具", "建材", "装饰"), ["furniture expo", "interior design", "building materials", "home exhibition"]),
        (("外贸", "跨境", "进出口", "采购商"), ["international trade", "business buyers", "trade negotiation", "global commerce"]),
    ]
    for needles, candidates in category_rules:
        if any(needle in context for needle in needles):
            category_terms.extend(candidates)
    fallback = category_terms + ["business exhibition", "trade show crowd", "exhibition booth", "business networking", "conference venue"]
    # Preserve order while removing overlaps from multiple matched categories.
    fallback = list(dict.fromkeys(fallback))
    return {"terms": fallback[:amount], "source": "fallback", "error": error}


def generate_search_terms(topic: str, script: str, amount: int = 5) -> list[str]:
    """Backward-compatible list-only wrapper."""
    return list(generate_search_terms_with_meta(topic, script, amount).get("terms") or [])


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
    response = post_json_with_retry(
        settings.dashscope_multimodal_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload=payload,
        timeout=(8, 120),
        attempts=3,
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
