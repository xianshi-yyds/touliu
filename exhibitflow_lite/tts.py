from __future__ import annotations

import asyncio
import json
import subprocess
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


def concat_segments(segments: list[Path], output_name: str, text: str = "", voice: str = "") -> Path:
    """Join sentence-level audio and preserve Edge word timings for subtitles."""
    if not segments:
        raise ValueError("没有可拼接的语音片段")
    out = settings.storage_dir / "tts" / output_name
    out.parent.mkdir(parents=True, exist_ok=True)
    concat_file = out.with_suffix(".concat.txt")
    def _concat_quote(path: Path) -> str:
        # ffmpeg concat demuxer uses single-quoted paths; escape embedded quotes.
        return path.resolve().as_posix().replace("'", "'\\\\''")

    concat_file.write_text(
        "\n".join(f"file '{_concat_quote(path)}'" for path in segments) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c:a", "libmp3lame", "-q:a", "2", str(out)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"语音片段拼接失败：{result.stdout[-1200:]}")

    # Edge TTS 会写入逐词边界；合并后同步偏移，供字幕时间轴使用。
    combined_words: list[dict[str, object]] = []
    segment_ranges: list[dict[str, object]] = []
    offset = 0.0
    for segment in segments:
        timing = segment.with_suffix(".words.json")
        if timing.is_file():
            try:
                words = (json.loads(timing.read_text(encoding="utf-8")) or {}).get("words") or []
            except Exception:
                words = []
            for word in words:
                combined_words.append({
                    "text": word.get("text") or "",
                    "start": round(float(word.get("start") or 0) + offset, 4),
                    "duration": round(float(word.get("duration") or 0), 4),
                })
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(segment)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            segment_duration = float(probe.stdout.strip() or 0)
        except ValueError:
            segment_duration = 0.0
        segment_ranges.append({"start": round(offset, 4), "end": round(offset + segment_duration, 4)})
        offset += segment_duration
    out.with_suffix(".words.json").write_text(
        json.dumps({"text": text, "voice": voice, "words": combined_words, "segments": segment_ranges}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
