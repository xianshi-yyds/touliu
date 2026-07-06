from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .config import settings
from . import qwen


EDGE_VOICES = {
    "zh-CN-YunjianNeural": "zh-CN-YunjianNeural",
    "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural": "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural": "zh-CN-XiaoyiNeural",
}


def synthesize(text: str, service: str, voice: str, output_name: str) -> Path:
    service = (service or "qwen").strip().lower()
    if service == "qwen":
        return qwen.synthesize_speech(text, voice=voice or "Cherry", output_name=output_name)
    if service != "edge":
        raise ValueError(f"不支持的 TTS 服务：{service}")
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Edge TTS 依赖未安装，请运行 pip install edge-tts") from exc
    out = settings.storage_dir / "tts" / output_name
    out.parent.mkdir(parents=True, exist_ok=True)
    selected_voice = EDGE_VOICES.get(voice, voice or "zh-CN-XiaoxiaoNeural")

    async def _save() -> None:
        boundaries: list[dict[str, object]] = []
        communicate = edge_tts.Communicate(
            text=text,
            voice=selected_voice,
            boundary="WordBoundary",
        )
        with out.open("wb") as audio_stream:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_stream.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    boundaries.append(
                        {
                            "text": chunk.get("text") or "",
                            "start": round(float(chunk.get("offset") or 0) / 10_000_000, 4),
                            "duration": round(float(chunk.get("duration") or 0) / 10_000_000, 4),
                        }
                    )
        timing_file = out.with_suffix(".words.json")
        timing_file.write_text(
            json.dumps({"text": text, "voice": selected_voice, "words": boundaries}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    asyncio.run(_save())
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("Edge TTS 未生成有效音频")
    return out
