from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import wave
from array import array
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .config import settings
from .storage import safe_stem, write_json


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


CAPTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "viral": {
        "label": "会展爆款大字",
        "font": "Yuanti SC",
        "font_size": 88,
        "accent_size": 98,
        "fill": (255, 255, 255, 255),
        "accent": (255, 207, 38, 255),
        "stroke": (24, 24, 24, 255),
        "accent_stroke": (190, 47, 38, 255),
        "stroke_width": 8,
        "shadow": (0, 0, 0, 150),
        "y": 1330,
        "box": False,
    },
    "business": {
        "label": "会展蓝白信息",
        "font": "Lantinghei SC",
        "font_size": 74,
        "accent_size": 82,
        "fill": (255, 255, 255, 255),
        "accent": (67, 191, 255, 255),
        "stroke": (20, 32, 52, 255),
        "accent_stroke": (20, 32, 52, 255),
        "stroke_width": 6,
        "shadow": (0, 0, 0, 140),
        "y": 1400,
        "box": False,
    },
    "minimal": {
        "label": "半透明信息卡",
        "font": "PingFang SC",
        "font_size": 70,
        "accent_size": 76,
        "fill": (255, 255, 255, 255),
        "accent": (105, 232, 158, 255),
        "stroke": (8, 12, 20, 255),
        "accent_stroke": (8, 12, 20, 255),
        "stroke_width": 4,
        "shadow": (0, 0, 0, 120),
        "y": 1420,
        "box": True,
    },
    "energetic": {
        "label": "橙色行动强调",
        "font": "Hiragino Sans GB",
        "font_size": 84,
        "accent_size": 100,
        "fill": (255, 255, 255, 255),
        "accent": (255, 132, 42, 255),
        "stroke": (35, 21, 20, 255),
        "accent_stroke": (191, 36, 49, 255),
        "stroke_width": 8,
        "shadow": (0, 0, 0, 155),
        "y": 1330,
        "box": False,
    },
    # This is a real PyCaps renderer, not only a browser mock.  Keeping it as
    # an explicit template lets us compare the new character-level timing
    # against the legacy Pillow/FFmpeg renderer without changing old records.
    "pycaps_hype": {
        "label": "PyCaps 动态高亮",
        "font": "Noto Sans CJK SC",
        "font_size": 78,
        "accent_size": 86,
        "fill": (255, 255, 255, 255),
        "accent": (255, 212, 0, 255),
        "stroke": (16, 17, 20, 255),
        "accent_stroke": (16, 17, 20, 255),
        "stroke_width": 5,
        "shadow": (0, 0, 0, 150),
        "y": 1510,
        "box": False,
        "renderer": "pycaps",
    },
}

# 字体类型与视觉模板分离：模板决定描边、重点色和卡片，字体类型只决定
# 字形。默认沿用每个模板原有字体，其他选项在浏览器预览和最终导出中一致。
CAPTION_FONT_TYPES: dict[str, dict[str, str]] = {
    "template": {"label": "跟随样式", "font": ""},
    "sans": {"label": "现代黑体", "font": "Noto Sans CJK SC"},
    "serif": {"label": "宋体衬线", "font": "Noto Serif CJK SC"},
    "calligraphy": {"label": "毛笔手写", "font": "Ma Shan Zheng"},
    "playful": {"label": "潮酷圆体", "font": "ZCOOL KuaiLe"},
    "display": {"label": "青科标题", "font": "ZCOOL QingKe HuangYou"},
    "xiaowei": {"label": "小薇宋体", "font": "ZCOOL XiaoWei"},
    "longcang": {"label": "龙藏手书", "font": "Long Cang"},
    "maocao": {"label": "刘建毛草", "font": "Liu Jian Mao Cao"},
}

# The preview layer follows the same separation used by pycaps: a visual
# template controls typography/colour while an animation controls how the
# caption block enters.  Keeping these as two independent dimensions means a
# user can try several looks without rebuilding the video or audio track.
CAPTION_ANIMATIONS: dict[str, dict[str, Any]] = {
    "pop": {
        "label": "弹入",
        "description": "整句轻微缩放并淡入，适合爆款口播",
    },
    "slide_up": {
        "label": "上滑",
        "description": "从底部上滑进入，字幕更靠近短视频节奏",
    },
    "fade": {
        "label": "淡入",
        "description": "低干扰淡入，适合专业展会内容",
    },
    "typewriter": {
        "label": "打字机",
        "description": "按字符逐步出现，适合标题和重点句",
    },
}

AUTO_HIGHLIGHT_WORDS = [
    "立即报名", "点击下方链接", "提前锁定", "专业买家", "采购商", "人流量",
    "展位", "招商", "报名", "稀缺", "免费", "限时", "国际", "专业", "成交",
]

MAX_SPEECH_SEGMENT_CHARS = 28
CAPTION_MAX_CHARS = 13
CAPTION_DING_MIN_GAP = 0.28
PYCAPS_TEMPLATE_NAME = "pycaps_hype"


def ffmpeg_bin() -> str:
    return os.getenv("FFMPEG_BIN", "ffmpeg")


def ffprobe_bin() -> str:
    return os.getenv("FFPROBE_BIN", "ffprobe")


def run(cmd: list[str]) -> str:
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or "command failed: " + " ".join(cmd))
    return result.stdout


def media_files(folder: str | Path) -> list[Path]:
    root = Path(folder).expanduser()
    if not root.exists():
        return []
    suffixes = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in suffixes
    ]


def sentence_ranges(script: str, total_duration: float, audio_file: Path) -> list[dict[str, float]]:
    """Return sentence time ranges, preferring ranges recorded by sentence-level TTS."""
    sentences = split_sentences(script)
    timing_file = audio_file.with_suffix(".words.json")
    if timing_file.is_file():
        try:
            recorded = (json.loads(timing_file.read_text(encoding="utf-8")) or {}).get("segments") or []
        except Exception:
            recorded = []
        if len(recorded) == len(sentences):
            return [
                {"start": max(0.0, float(item.get("start") or 0)), "end": min(total_duration, max(0.0, float(item.get("end") or 0)))}
                for item in recorded
            ]
    weights = [max(len(_clean_text(item)), 1) for item in sentences] or [1]
    total_weight = sum(weights)
    cursor = 0.0
    ranges: list[dict[str, float]] = []
    for index, weight in enumerate(weights):
        end = total_duration if index == len(weights) - 1 else cursor + total_duration * weight / total_weight
        ranges.append({"start": cursor, "end": end})
        cursor = end
    return ranges


def _hard_wrap_speech_piece(text: str, max_chars: int) -> list[str]:
    """Last-resort wrapping for a long clause that has no natural comma."""
    value = str(text or "").strip()
    if not value:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(value):
        chunks.append(value[cursor : cursor + max_chars])
        cursor += max_chars
    if len(chunks) > 1:
        chunks = [
            f"{chunk.rstrip('，,、：:')}，" if index < len(chunks) - 1 and chunk[-1:] not in "，,、：:" else chunk
            for index, chunk in enumerate(chunks)
        ]
    return chunks


def _split_long_speech_sentence(sentence: str, max_chars: int = MAX_SPEECH_SEGMENT_CHARS) -> list[str]:
    """Split a long sentence at commas while preserving pause punctuation."""
    value = re.sub(r"\s+", " ", str(sentence or "")).strip()
    if not value:
        return []
    if len(_clean_text(value)) <= max_chars:
        return [value]

    terminal = value[-1] if value[-1] in "。！？!?；;" else ""
    body = value[:-1] if terminal else value
    comma_parts = [part for part in re.split(r"(?<=[，,、：:])", body) if part]
    chunks: list[str] = []
    current = ""
    for part in comma_parts:
        if len(_clean_text(part)) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_wrap_speech_piece(part, max_chars))
            continue
        candidate = f"{current}{part}"
        if current and len(_clean_text(candidate)) > max_chars:
            chunks.append(current.strip())
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    if terminal and chunks:
        chunks[-1] = f"{chunks[-1].rstrip()}{terminal}"
    return [chunk for chunk in chunks if chunk]


def split_sentences(text: str, max_chars: int = MAX_SPEECH_SEGMENT_CHARS) -> list[str]:
    """Split speech on full stops first, then commas only when a sentence is long.

    Each returned segment retains its ending punctuation.  That punctuation is
    important: TTS providers use it to create a natural pause, and the joined
    word timings then stay aligned with the video sentence boundaries.
    """
    pieces: list[str] = []
    current = ""
    for char in str(text or ""):
        if char in "\n。！？!?；;":
            piece = current.strip()
            if piece:
                pieces.extend(_split_long_speech_sentence(piece + char, max_chars=max_chars))
            current = ""
        else:
            current += char
    trailing = current.strip()
    if trailing:
        pieces.extend(_split_long_speech_sentence(trailing, max_chars=max_chars))
    return pieces or [str(text or "").strip() or "展会现场精彩瞬间"]


def _clean_text(text: str) -> str:
    return re.sub(r"[\s，,。！？!?；;、：:]", "", str(text or ""))


def _caption_display_text(text: str) -> str:
    """Normalize a timed speech segment for the visible caption card.

    Punctuation still belongs in the narration text because it controls TTS
    pauses. It does not need to become a separate visual caption token, so we
    remove whitespace and trim punctuation at the two ends here.
    """
    value = re.sub(r"\s+", "", str(text or ""))
    return value.strip("，,、：:。！？!?；;") or value


def split_caption_chunks(text: str, max_chars: int = CAPTION_MAX_CHARS) -> list[str]:
    chunks: list[str] = []
    for sentence in split_sentences(text):
        clauses = [item.strip() for item in re.split(r"[，,、：:]", sentence) if item.strip()]
        for clause in clauses or [sentence]:
            if len(clause) <= max_chars:
                chunks.append(clause)
                continue
            cursor = 0
            while cursor < len(clause):
                chunks.append(clause[cursor : cursor + max_chars])
                cursor += max_chars
    return chunks or ["展会现场精彩瞬间"]


def _group_caption_words(words: list[dict[str, Any]], protected: list[str], max_chars: int = CAPTION_MAX_CHARS) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    count = 0
    for word in words:
        word_len = max(len(_clean_text(word["text"])), 1)
        combined_tail = _clean_text("".join(str(item["text"]) for item in current[-2:]) + word["text"])
        joins_highlight = any(keyword in combined_tail for keyword in protected)
        if current and count + word_len > max_chars and not (joins_highlight and count + word_len <= max_chars + 3):
            groups.append(current)
            current = []
            count = 0
        current.append(word)
        count += word_len
    if current:
        groups.append(current)
    return groups


def _normalise_recorded_bounds(recorded_segments: list[dict[str, Any]], total_duration: float) -> list[dict[str, float]]:
    """Clamp sentence-level TTS ranges to the final audio duration."""
    ranges: list[dict[str, float]] = []
    previous_end = 0.0
    for index, bounds in enumerate(recorded_segments):
        try:
            raw_start = float(bounds.get("start") or 0)
        except (TypeError, ValueError):
            raw_start = previous_end
        try:
            raw_end = float(bounds.get("end") or 0)
        except (TypeError, ValueError):
            raw_end = 0.0
        start = max(0.0, min(total_duration, raw_start))
        if index > 0:
            # concat_segments is gapless. Avoid a one-frame overlap caused by
            # rounded metadata while retaining a real gap, if a provider adds
            # one intentionally.
            start = max(start, previous_end)
        end = max(start, min(total_duration, raw_end if raw_end > 0 else total_duration))
        ranges.append({"start": start, "end": end})
        previous_end = end
    if ranges:
        ranges[0]["start"] = 0.0
    return ranges


def _word_matches_segment(word: dict[str, Any], segment_index: int, start: float, end: float) -> bool:
    """Prefer an explicit segment marker, then fall back to word timestamps."""
    marker = word.get("segment_index")
    if marker is not None:
        try:
            return int(marker) == segment_index
        except (TypeError, ValueError):
            pass
    return start <= float(word.get("start") or 0) < end


def caption_timeline(
    script: str,
    total_duration: float,
    audio_file: Path,
    cta_text: str = "",
    highlight_words: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the visual caption timeline.

    The timing unit is deliberately a TTS speech segment (a sentence, or a
    long sentence split at a comma). A segment may wrap to two visual lines,
    but it must not become two sequential caption clips. This separation keeps
    the last character of a sentence with the audio that speaks it while still
    allowing the card renderer to fit long text on screen.
    """
    timing_file = audio_file.with_suffix(".words.json")
    cta_clean = _clean_text(cta_text)
    speech_segments = split_sentences(script)
    if timing_file.is_file():
        try:
            timing_data = json.loads(timing_file.read_text(encoding="utf-8")) or {}
            words = timing_data.get("words") or []
            recorded_segments = timing_data.get("segments") or []
        except Exception:
            words = []
            recorded_segments = []

        # concat_segments records one range for every independently synthesized
        # TTS piece. Use that contract first, even for Qwen, which has no word
        # boundaries. The script is the canonical caption text; word timings
        # are only used below for the legacy/partial-metadata fallback.
        recorded_ranges = _normalise_recorded_bounds(recorded_segments, total_duration)
        if (
            len(recorded_ranges) == len(speech_segments)
            and recorded_ranges
            and any(item["end"] > item["start"] for item in recorded_ranges)
        ):
            timeline = []
            for index, (speech_segment, bounds) in enumerate(zip(speech_segments, recorded_ranges)):
                text = _caption_display_text(speech_segment)
                if not text:
                    continue
                timeline.append(
                    {
                        "text": text,
                        "start": bounds["start"],
                        "end": bounds["end"],
                        "cta": bool(cta_clean and _clean_text(speech_segment) == cta_clean),
                        # This range is one independently synthesized TTS
                        # sentence.  Keep it as an independent caption cue;
                        # punctuation has deliberately been removed only for
                        # display and must not make later code join it again.
                        "semantic_unit": True,
                    }
                )
            if timeline:
                timeline[0]["start"] = 0.0
                return timeline

        valid = [
            {
                "text": str(item.get("text") or "").strip(),
                "start": float(item.get("start") or 0),
                "end": float(item.get("start") or 0) + float(item.get("duration") or 0),
                "segment_index": item.get("segment_index"),
            }
            for item in words
            if str(item.get("text") or "").strip()
        ]
        if valid:
            cta_start = len(valid)
            if cta_clean:
                for index in range(len(valid)):
                    if _clean_text("".join(word["text"] for word in valid[index:])) == cta_clean:
                        cta_start = index
                        break
            protected = [word for word in (highlight_words or []) + AUTO_HIGHLIGHT_WORDS if word]
            content_words = valid[:cta_start]
            groups: list[list[dict[str, Any]]] = []
            if recorded_segments and len(recorded_segments) == len(speech_segments):
                # The normal path above returns before this branch. Keep this
                # guarded fallback for old timing files whose ranges are
                # incomplete, but never split a segment into visual chunks.
                for segment_index, bounds in enumerate(recorded_segments):
                    segment_start = float(bounds.get("start") or 0)
                    segment_end = float(bounds.get("end") or total_duration)
                    segment_words = [
                        word for word in content_words
                        if _word_matches_segment(word, segment_index, segment_start, segment_end)
                    ]
                    if segment_words:
                        groups.append(segment_words)
            else:
                groups = _group_caption_words(content_words, protected)
            if cta_start < len(valid):
                groups.append(valid[cta_start:])
            timeline: list[dict[str, Any]] = []
            for index, group in enumerate(groups):
                start = 0.0 if index == 0 else float(group[0]["start"])
                if index + 1 < len(groups):
                    end = float(groups[index + 1][0]["start"])
                else:
                    end = total_duration
                text = "".join(str(word["text"]) for word in group)
                timeline.append({"text": text, "start": start, "end": min(end, total_duration), "cta": bool(cta_clean and _clean_text(text) == cta_clean)})
            if timeline:
                timeline[0]["start"] = 0.0
                return timeline

    chunks = split_caption_chunks(script)
    weights = [max(len(_clean_text(chunk)), 2) for chunk in chunks]
    total_weight = sum(weights) or 1
    cursor = 0.0
    timeline = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        end = total_duration if index == len(chunks) - 1 else cursor + total_duration * weight / total_weight
        timeline.append({"text": chunk, "start": cursor, "end": end, "cta": bool(cta_clean and _clean_text(chunk) == cta_clean)})
        cursor = end
    return timeline


def _font_path(family: str) -> str:
    candidates = {
        "PingFang SC": "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc",
        "Yuanti SC": "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/b86e58f38fd21e9782e70a104676f1655e72ebab.asset/AssetData/Yuanti.ttc",
        "Lantinghei SC": "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/f7f6b250e97c182e68ac53a2b359ec44548878b9.asset/AssetData/Lantinghei.ttc",
        "Hiragino Sans GB": "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "Noto Sans CJK SC": "/usr/local/share/fonts/NotoSansCJKsc-Regular.otf",
        "Noto Serif CJK SC": "/usr/local/share/fonts/exhibitflow/NotoSerifSC.ttf",
        "Ma Shan Zheng": "/usr/local/share/fonts/exhibitflow/MaShanZheng-Regular.ttf",
        "ZCOOL KuaiLe": "/usr/local/share/fonts/exhibitflow/ZCOOLKuaiLe-Regular.ttf",
        "ZCOOL QingKe HuangYou": "/usr/local/share/fonts/exhibitflow/ZCOOLQingKeHuangYou-Regular.ttf",
        "ZCOOL XiaoWei": "/usr/local/share/fonts/exhibitflow/ZCOOLXiaoWei-Regular.ttf",
        "Long Cang": "/usr/local/share/fonts/exhibitflow/LongCang-Regular.ttf",
        "Liu Jian Mao Cao": "/usr/local/share/fonts/exhibitflow/LiuJianMaoCao-Regular.ttf",
    }
    preferred = Path(candidates.get(family, candidates["PingFang SC"]))
    if preferred.is_file():
        return str(preferred)
    for fallback in (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        # Linux deployments use a bundled Noto Sans Simplified Chinese font.
        # Keep the fallback here instead of hard-coding a macOS-only render
        # path so the same render worker works on the EC2 host.
        Path("/usr/local/share/fonts/NotoSansCJKsc-Regular.otf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
    ):
        if fallback.is_file():
            return str(fallback)
    raise FileNotFoundError("系统缺少可用中文字体")


def _keyword_spans(text: str, keywords: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for keyword in sorted({word.strip() for word in keywords if word.strip()}, key=len, reverse=True):
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            candidate = (index, index + len(keyword))
            if not any(candidate[0] < end and candidate[1] > begin for begin, end in spans):
                spans.append(candidate)
            start = index + len(keyword)
    return sorted(spans)


def _text_runs(text: str, keywords: list[str]) -> list[tuple[str, bool]]:
    spans = _keyword_spans(text, keywords)
    runs: list[tuple[str, bool]] = []
    cursor = 0
    for begin, end in spans:
        if cursor < begin:
            runs.append((text[cursor:begin], False))
        runs.append((text[begin:end], True))
        cursor = end
    if cursor < len(text):
        runs.append((text[cursor:], False))
    return runs or [(text, False)]


def _wrap_caption_lines(text: str, keywords: list[str], max_chars: int = 9) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    spans = _keyword_spans(text, keywords)
    # For a normal two-line card, balance the lines instead of always cutting
    # at the hard limit. This avoids visual fragments such as ``新`` / ``能源``
    # while keeping longer cards bounded by the original line-length limit.
    split_at = (len(text) + 1) // 2 if len(text) <= max_chars * 2 + 2 else max_chars
    split_at = min(split_at, len(text) - 1)
    for begin, end in spans:
        if begin < split_at < end:
            split_at = begin if begin >= 4 else end
            break
    if split_at <= 0 or split_at >= len(text):
        split_at = len(text) // 2
    return [text[:split_at], text[split_at:]]


def caption_sentence_timeline(timeline: list[dict[str, Any]], max_clause_chars: int = 16) -> list[dict[str, Any]]:
    """Turn long narration cues into sentence-first caption cues.

    A caption should never be a mechanical fixed-character slice.  Preserve a
    full sentence whenever possible; only a long sentence is split at its
    natural comma/pause boundary.  Each new cue receives a proportional part
    of the original narration range so browser preview and burned captions
    change at the same points.
    """
    # Historical timelines may already have been cut into fixed-size pieces
    # (for example ``新能源产`` + ``业展。``). Rejoin neighbouring pieces
    # before applying sentence-first segmentation, otherwise the final glyph
    # of one spoken sentence appears in a separate caption cue.
    clean_items = [
        {**original, "text": str(original.get("text") or "").strip()}
        for original in timeline
        if str(original.get("text") or "").strip()
    ]
    average_chars = (
        sum(len(re.sub(r"\s+", "", str(item.get("text") or ""))) for item in clean_items) / len(clean_items)
        if clean_items else 0
    )
    average_duration = (
        sum(max(0.0, float(item.get("end") or 0) - float(item.get("start") or 0)) for item in clean_items) / len(clean_items)
        if clean_items else 0
    )
    # New timelines carry an explicit semantic marker. Existing records from
    # before that marker can still be recognised by their sentence-like text
    # and speech duration. Only old, very short fixed-width fragments should
    # be rejoined.
    already_semantic = any(item.get("semantic_unit") is True for item in clean_items) or (
        len(clean_items) > 1 and average_chars >= 7 and average_duration >= 1.1
    )

    semantic_items: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for item in clean_items:
        if already_semantic:
            semantic_items.append(item)
            continue
        raw = str(item.get("text") or "")
        if pending is None:
            pending = item
        elif bool(pending.get("cta")) != bool(item.get("cta")):
            semantic_items.append(pending)
            pending = item
        else:
            pending["text"] = f'{str(pending.get("text") or "")}{raw}'
            pending["end"] = item.get("end", pending.get("end"))
            pending["cta"] = bool(pending.get("cta")) or bool(item.get("cta"))
        combined = str(pending.get("text") or "")
        compact_length = len(re.sub(r"\s+", "", combined))
        if re.search(r"[。！？!?；;]$", combined) or compact_length >= max_clause_chars * 2:
            semantic_items.append(pending)
            pending = None
    if pending is not None:
        semantic_items.append(pending)

    output: list[dict[str, Any]] = []
    for item in semantic_items:
        raw = str(item.get("text") or "").strip()
        if not raw:
            continue
        sentences = [part.strip() for part in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", raw) if part.strip()]
        chunks: list[str] = []
        for sentence in sentences or [raw]:
            compact_length = len(re.sub(r"\s+", "", sentence))
            if compact_length <= max_clause_chars:
                chunks.append(sentence)
                continue
            clauses = [part.strip() for part in re.findall(r"[^，,、]+[，,、]?", sentence) if part.strip()]
            for clause in clauses or [sentence]:
                # Comma boundaries are preferred, but a long proper noun or
                # slogan may contain no punctuation. Bound it anyway so one
                # cue never turns into a five-line subtitle card.
                compact_clause = re.sub(r"\s+", "", clause)
                if len(compact_clause) <= max_clause_chars:
                    chunks.append(clause)
                else:
                    chunks.extend(
                        compact_clause[index:index + max_clause_chars]
                        for index in range(0, len(compact_clause), max_clause_chars)
                    )
        start = float(item.get("start") or 0)
        end = max(start + 0.01, float(item.get("end") or start + 0.01))
        weights = [max(1, len(re.sub(r"\s+", "", value))) for value in chunks]
        total_weight = sum(weights) or 1
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            next_cursor = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total_weight
            output.append({
                **item,
                "text": chunk,
                "start": round(cursor, 4),
                "end": round(max(cursor + 0.01, next_cursor), 4),
                "cta": bool(item.get("cta")) and index == len(chunks) - 1,
            })
            cursor = next_cursor
    return output


def render_caption_card(
    text: str,
    output: Path,
    template_name: str,
    highlight_words: list[str],
    is_cta: bool = False,
    font_scale: float = 1.0,
    vertical_position: float | None = None,
    max_chars_per_line: int = 9,
    font_type: str = "template",
    alignment: str = "center",
) -> Path:
    template = dict(CAPTION_TEMPLATES.get(template_name) or CAPTION_TEMPLATES["viral"])
    selected_font = CAPTION_FONT_TYPES.get(font_type) or CAPTION_FONT_TYPES["template"]
    if selected_font.get("font"):
        template["font"] = selected_font["font"]
    font_scale = max(0.70, min(1.50, float(font_scale or 1.0)))
    # Horizontal alignment mirrors the caption editor's 对齐 control. The safe
    # margin keeps left/right text clear of the 9:16 edge, matching preview.
    align = alignment if alignment in {"left", "center", "right"} else "center"
    align_margin = 80
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_path = _font_path(str(template["font"]))
    body_font = ImageFont.truetype(font_path, max(18, int(template["font_size"] * font_scale)))
    accent_font = ImageFont.truetype(font_path, max(18, int(template["accent_size"] * font_scale)))
    if is_cta:
        body_font = ImageFont.truetype(font_path, max(18, int(86 * font_scale)))
        accent_font = ImageFont.truetype(font_path, max(18, int(112 * font_scale)))
        clean = text.strip("。！？!?；;，,")
        if "立即" in clean:
            split_at = clean.find("立即")
            lines = [clean[:split_at].strip("，,"), clean[split_at:]]
        else:
            lines = [clean]
        center_y = int(1920 * (1 - vertical_position / 100)) if vertical_position is not None else 1450
        # A simple down-arrow echoes the reference CTA without external sticker assets.
        draw.rounded_rectangle((110, center_y - 58, 190, center_y + 22), radius=18, fill=(236, 65, 62, 235))
        draw.polygon([(125, center_y + 15), (175, center_y + 15), (150, center_y + 62)], fill=(236, 65, 62, 235))
    else:
        lines = _wrap_caption_lines(text, highlight_words, max_chars=max(6, min(14, int(max_chars_per_line or 9))))
        center_y = int(1920 * (1 - vertical_position / 100)) if vertical_position is not None else int(template["y"])

    line_metrics: list[tuple[list[tuple[str, bool]], int, int]] = []
    for line in lines:
        runs = _text_runs(line, highlight_words + ([line] if is_cta and line == lines[-1] else []))
        width = 0
        height = 0
        for run_text, highlighted in runs:
            font = accent_font if highlighted else body_font
            box = draw.textbbox((0, 0), run_text, font=font, stroke_width=int(template["stroke_width"]))
            width += box[2] - box[0]
            height = max(height, box[3] - box[1])
        line_metrics.append((runs, width, height))
    total_height = sum(item[2] for item in line_metrics) + max(len(line_metrics) - 1, 0) * 22
    top = center_y - total_height // 2
    if template.get("box"):
        max_width = max(item[1] for item in line_metrics)
        if align == "left":
            box_left, box_right = align_margin - 38, align_margin + max_width + 38
        elif align == "right":
            box_left, box_right = 1080 - align_margin - max_width - 38, 1080 - align_margin + 38
        else:
            box_left, box_right = 540 - max_width // 2 - 38, 540 + max_width // 2 + 38
        draw.rounded_rectangle(
            (box_left, top - 28, box_right, top + total_height + 28),
            radius=28,
            fill=(8, 12, 20, 170),
        )
    y = top
    for runs, width, height in line_metrics:
        if align == "left":
            x = align_margin
        elif align == "right":
            x = 1080 - align_margin - width
        else:
            x = (1080 - width) // 2
        for run_text, highlighted in runs:
            font = accent_font if highlighted else body_font
            fill = template["accent"] if highlighted else template["fill"]
            stroke = template["accent_stroke"] if highlighted else template["stroke"]
            run_box = draw.textbbox((0, 0), run_text, font=font, stroke_width=int(template["stroke_width"]))
            run_width = run_box[2] - run_box[0]
            run_height = run_box[3] - run_box[1]
            text_y = y + (height - run_height) // 2
            draw.text((x + 5, text_y + 8), run_text, font=font, fill=template["shadow"], stroke_width=int(template["stroke_width"]) + 2, stroke_fill=template["shadow"])
            draw.text((x, text_y), run_text, font=font, fill=fill, stroke_width=int(template["stroke_width"]), stroke_fill=stroke)
            if highlighted and template_name in {"viral", "energetic"}:
                underline_y = text_y + run_height + 5
                draw.line((x + 6, underline_y, x + run_width - 6, underline_y), fill=fill, width=7)
            x += run_width
        y += height + 22
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return output


def _caption_animation_filter(is_cta: bool = False, animation: str = "pop") -> str:
    """Build a transparent caption clip animation.

    The browser preview has the full CSS version of these animations.  This
    filter keeps the server-side export in sync for the final selected style.
    ``typewriter`` is rendered as a short sequence of cards in
    ``_make_caption_clip`` because a static PNG cannot reveal individual
    characters by itself.
    """
    pop_duration = 0.22 if is_cta else 0.16
    initial_scale = 0.78 if is_cta else 0.86
    animation = animation if animation in CAPTION_ANIMATIONS else "pop"
    if animation == "none":
        return "format=rgba,fps=30"
    if animation == "fade":
        return "format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fps=30"
    if animation == "slide_up":
        slide_duration = 0.26 if is_cta else 0.20
        # Pad below the 1920px canvas, then move the crop window upward.  The
        # alpha channel is preserved, so the text still overlays the video.
        return (
            "format=rgba,"
            "pad=1080:2100:0:180:color=black@0,"
            f"crop=1080:1920:0:'90+90*min(t/{slide_duration:.3f},1)',"
            f"fade=t=in:st=0:d={min(0.14, slide_duration):.3f}:alpha=1,"
            "fps=30"
        )
    scale_expression = (
        f"{initial_scale:.2f}+{1.0 - initial_scale:.2f}*min(t/{pop_duration:.3f},1)"
        f"-0.02*min(max((t-{pop_duration:.3f})/0.10,0),1)"
    )
    return (
        "format=rgba,"
        f"scale=w='trunc(iw*({scale_expression})/2)*2':"
        f"h='trunc(ih*({scale_expression})/2)*2':eval=frame,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        "fade=t=in:st=0:d=0.10:alpha=1,"
        "fps=30"
    )


def _make_caption_clip(
    item: dict[str, Any],
    run_dir: Path,
    template_name: str,
    highlight_words: list[str],
    animation: str,
    index: int,
    font_scale: float = 1.0,
    vertical_position: float | None = None,
    max_chars_per_line: int = 9,
    font_type: str = "template",
    alignment: str = "center",
) -> Path:
    """Render one transparent caption clip for the selected animation."""
    cards_dir = run_dir / "caption_cards"
    clips_dir = run_dir / "caption_clips"
    card = render_caption_card(
        str(item["text"]),
        cards_dir / f"caption-{index:03d}.png",
        template_name=template_name,
        highlight_words=highlight_words,
        is_cta=bool(item.get("cta")),
        font_scale=font_scale,
        vertical_position=vertical_position,
        max_chars_per_line=max_chars_per_line,
        font_type=font_type,
        alignment=alignment,
    )
    clip = clips_dir / f"caption-{index:03d}.mov"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = max(float(item["end"]) - float(item["start"]), 0.55)
    selected_animation = animation if animation in CAPTION_ANIMATIONS else "pop"
    if selected_animation != "typewriter":
        run(
            [
                ffmpeg_bin(), "-y", "-loop", "1", "-i", str(card), "-t", f"{clip_duration:.3f}",
                "-vf", _caption_animation_filter(bool(item.get("cta")), selected_animation),
                "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(clip),
            ]
        )
        return clip

    # pycaps exposes a typewriting effect at the element level.  Recreate the
    # same behaviour with a small number of transparent Pillow frames and let
    # FFmpeg hold the final frame for the rest of the caption duration.
    intro_duration = min(0.42 if item.get("cta") else 0.34, clip_duration)
    frame_count = max(2, int(math.ceil(intro_duration * 30)))
    frames_dir = run_dir / "caption_typewriter_frames" / f"caption-{index:03d}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    text = str(item["text"])
    for frame_index in range(frame_count):
        visible_count = max(1, int(math.ceil(len(text) * (frame_index + 1) / frame_count)))
        render_caption_card(
            text[:visible_count],
            frames_dir / f"frame-{frame_index + 1:04d}.png",
            template_name=template_name,
            highlight_words=highlight_words,
            is_cta=bool(item.get("cta")),
            font_scale=font_scale,
            vertical_position=vertical_position,
            max_chars_per_line=max_chars_per_line,
            font_type=font_type,
            alignment=alignment,
        )
    intro = clips_dir / f"caption-{index:03d}-intro.mov"
    run(
        [
            ffmpeg_bin(), "-y", "-framerate", "30", "-i", str(frames_dir / "frame-%04d.png"),
            "-t", f"{intro_duration:.3f}", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(intro),
        ]
    )
    remaining = clip_duration - intro_duration
    if remaining <= 0.01:
        intro.replace(clip)
        return clip
    tail = clips_dir / f"caption-{index:03d}-tail.mov"
    run(
        [
            ffmpeg_bin(), "-y", "-loop", "1", "-i", str(card), "-t", f"{remaining:.3f}",
            "-vf", _caption_animation_filter(False, "none"),
            "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(tail),
        ]
    )
    list_file = clips_dir / f"caption-{index:03d}-concat.txt"
    list_file.write_text(
        f"file '{str(intro).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
        f"file '{str(tail).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n",
        encoding="utf-8",
    )
    run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(clip)])
    return clip


def make_caption_sfx(timeline: list[dict[str, Any]], run_dir: Path, total_duration: float) -> Path:
    """Synthesize a quiet two-tone chime at each caption entrance.

    Keeping the sound generated locally avoids another asset dependency and
    makes every render reproducible. CTA captions get a slightly stronger
    chime so the action line feels like a deliberate beat.
    """
    output = run_dir / "caption-sfx.wav"
    sample_rate = 44_100
    total_samples = max(int(math.ceil((max(total_duration, 1.0) + 0.35) * sample_rate)), sample_rate)
    mix = array("f", [0.0]) * total_samples
    last_start = -999.0
    for item in timeline:
        start = max(0.0, min(float(item.get("start") or 0), max(total_duration, 0.0)))
        if start - last_start < CAPTION_DING_MIN_GAP:
            continue
        last_start = start
        offset = int(round(start * sample_rate))
        gain = 0.18 if item.get("cta") else 0.11
        length = int(0.32 * sample_rate)
        for index in range(length):
            position = index / sample_rate
            envelope = math.exp(-8.5 * position)
            tone = 0.72 * math.sin(2 * math.pi * 1046 * position)
            if position >= 0.045:
                tone += 0.55 * math.sin(2 * math.pi * 1568 * (position - 0.045))
            if position >= 0.10:
                tone += 0.20 * math.sin(2 * math.pi * 2093 * (position - 0.10))
            target = offset + index
            if target < total_samples:
                mix[target] += float(gain * envelope * tone)
    pcm = array("h", (max(-32767, min(32767, int(value * 32767))) for value in mix))
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output


def duration(path: Path) -> float:
    try:
        output = run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ]
        )
        return float(output.strip() or 0)
    except Exception:
        return 0.0


def mux_preview_video(video: Path, audio: Path, output: Path, total_duration: float) -> Path:
    """Create a browser-previewable base video with narration but no captions.

    The base video is intentionally kept separate from the caption overlay. The
    SaaS UI can therefore change a CSS caption template instantly while the
    user is previewing, and only the final selected style needs a server export.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin(), "-y", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-t", f"{max(total_duration, 0.1):.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-movflags", "+faststart", str(output),
        ]
    )
    return output


def _pycaps_transcription(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert our sentence timeline into PyCaps character-level JSON.

    The Qwen TTS endpoint returns reliable sentence boundaries but not Chinese
    word timestamps.  Treating the complete sentence as one PyCaps word would
    make the animation jump as a block.  Splitting the already-correct
    sentence range into characters gives PyCaps deterministic karaoke timing
    without asking Whisper to transcribe the generated voice a second time.
    """
    segments: list[dict[str, Any]] = []
    for item in timeline:
        text = re.sub(r"\s+", "", str(item.get("text") or "").strip())
        if not text:
            continue
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start + 0.01, float(item.get("end") or start + 0.01))
        characters = list(text)
        span = end - start
        words = []
        for index, character in enumerate(characters):
            character_start = start + span * index / len(characters)
            character_end = end if index == len(characters) - 1 else start + span * (index + 1) / len(characters)
            words.append(
                {
                    "text": character,
                    "time": {
                        "start": round(character_start, 4),
                        "end": round(max(character_start + 0.005, character_end), 4),
                    },
                }
            )
        segments.append({"lines": [{"words": words}]})
    return {"segments": segments}


def _pycaps_css(font_scale: float = 1.0, font_family: str = "Noto Sans CJK SC") -> str:
    """CSS for the first PyCaps migration style.

    PyCaps renders this CSS in Chromium and adds narration state classes to
    every character.  The active character is yellow and slightly enlarged;
    completed characters settle to white.  The browser preview in the UI uses
    the same class names and visual values.
    """
    size = max(18, min(42, round(26 * max(0.70, min(1.50, font_scale)))))
    return """
.line {
    display: flex;
    justify-content: center;
    align-items: center;
    flex-wrap: nowrap;
    width: 100%;
}
.word {
    display: inline-block;
    font-family: "__PYCAPS_FONT_FAMILY__", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    /* PyCaps renders at a 2x/3x device scale before movielite composites the
       subtitle image.  Keep the CSS size close to the upstream presets; a
       68px CSS glyph would become an oversized ~200px glyph in the 1080x1920
       export and force ordinary Chinese sentences into four or five lines. */
    font-size: __PYCAPS_FONT_SIZE__px;
    color: #ffffff;
    font-weight: 900;
    line-height: 1.15;
    letter-spacing: 0.01em;
    padding: 1px 1px;
    text-shadow:
        -2px -2px 0 #101114,
         2px -2px 0 #101114,
        -2px  2px 0 #101114,
         2px  2px 0 #101114,
         0 4px 12px rgba(0, 0, 0, 0.5);
    transform-origin: 50% 80%;
}
.word-being-narrated {
    color: #ffd400;
    transform: scale(1.08);
}
.word-already-narrated { color: #ffffff; }
.word-not-narrated-yet { color: #ffffff; }
""".replace("__PYCAPS_FONT_SIZE__", str(size)).replace("__PYCAPS_FONT_FAMILY__", font_family)


def _render_pycaps_caption(
    source_video: Path,
    audio: Path,
    timeline: list[dict[str, Any]],
    run_dir: Path,
    animation: str,
    caption_sfx_enabled: bool,
    total_duration: float,
    font_scale: float = 1.0,
    vertical_position: float = 28.0,
    font_type: str = "template",
) -> tuple[Path, Path, Path | None]:
    """Render a caption layer with PyCaps and return final/media assets.

    ``source_video`` may be the old no-audio combined video or an existing
    preview base.  We always create a fresh input with the canonical narration
    track so a historical record can be reopened safely and never inherits a
    previously burned subtitle track or stale audio.
    """
    try:
        from pycaps import CapsPipelineBuilder, TranscriptFormat
        from pycaps.animation import FadeIn, PopIn, SlideIn
        from pycaps.animation.definitions import Direction
        from pycaps.common import CacheStrategy, ElementType, EventType
        from pycaps.layout import SubtitleLayoutOptions, VerticalAlignment
    except Exception as exc:  # pragma: no cover - exercised on hosts without the optional extra
        raise RuntimeError(
            "PyCaps 渲染依赖未安装。请安装 pycaps、playwright，并执行 playwright install chromium。"
        ) from exc

    run_dir.mkdir(parents=True, exist_ok=True)
    input_video = mux_preview_video(source_video, audio, run_dir / "pycaps-input.mp4", total_duration)
    transcript = _pycaps_transcription(timeline)
    transcript_path = run_dir / "pycaps-transcription.json"
    write_json(transcript_path, transcript)
    pycaps_video = run_dir / "pycaps-captioned.mp4"
    if pycaps_video.exists():
        pycaps_video.unlink()

    if animation == "slide_up":
        word_animation = SlideIn(direction=Direction.UP, duration=0.22)
    elif animation == "fade":
        word_animation = FadeIn(duration=0.2)
    else:
        # ``typewriter`` is intentionally mapped to character pop-in here:
        # our transcript already consists of one character per word, which is
        # the visual typewriter effect without PyCaps' slower per-letter clip
        # expansion.
        word_animation = PopIn(duration=0.18)

    builder = (
        CapsPipelineBuilder()
        .with_input_video(str(input_video))
        .with_output_video(str(pycaps_video))
        .with_transcription(transcript, TranscriptFormat.PYCAPS_JSON)
        .with_cache_strategy(CacheStrategy.NONE)
        .with_layout_options(
            SubtitleLayoutOptions(
                max_width_ratio=0.88,
                max_number_of_lines=2,
                min_number_of_lines=1,
                vertical_align=VerticalAlignment(align="bottom", offset=-max(0.04, min(0.32, vertical_position / 100))),
            )
        )
        .add_css_content(_pycaps_css(font_scale, (CAPTION_FONT_TYPES.get(font_type) or CAPTION_FONT_TYPES["template"]).get("font") or "Noto Sans CJK SC"))
        .add_animation(word_animation, EventType.ON_NARRATION_STARTS, ElementType.WORD)
        .add_animation(FadeIn(duration=0.12), EventType.ON_NARRATION_STARTS, ElementType.SEGMENT)
    )
    builder.build().run()

    # PyCaps normally keeps the input audio, but explicitly remuxing the
    # canonical narration makes the contract stable across movielite versions.
    with_audio = mux_preview_video(pycaps_video, audio, run_dir / "pycaps-with-audio.mp4", total_duration)
    sfx = make_caption_sfx(timeline, run_dir, total_duration) if caption_sfx_enabled else None
    if not sfx:
        return with_audio, transcript_path, None

    final = run_dir / "final.mp4"
    mix_caption_sfx(with_audio, sfx, final, total_duration)
    return final, transcript_path, sfx


def mix_caption_sfx(video: Path, sfx: Path, output: Path, total_duration: float) -> Path:
    """Mix the generated caption chime into an already-captioned video."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin(), "-y", "-i", str(video), "-i", str(sfx),
            "-filter_complex",
            "[0:a]aresample=44100[narration];[1:a]aresample=44100[chime];"
            "[narration][chime]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
            "-map", "0:v:0", "-map", "[a]", "-t", f"{max(total_duration, 0.1):.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-movflags", "+faststart", str(output),
        ]
    )
    return output


def write_srt(timeline: list[dict[str, Any]], output: Path) -> Path:
    def stamp(total_seconds: float) -> str:
        millis = int(round(total_seconds * 1000))
        hours, rem = divmod(millis, 3600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    lines: list[str] = []
    for index, item in enumerate(timeline, start=1):
        lines.extend([str(index), f"{stamp(float(item['start']))} --> {stamp(float(item['end']))}", str(item["text"]), ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def make_caption_overlay(
    timeline: list[dict[str, Any]],
    run_dir: Path,
    template_name: str,
    highlight_words: list[str],
    animation: str = "pop",
    font_scale: float = 1.0,
    vertical_position: float | None = None,
    max_chars_per_line: int = 9,
    font_type: str = "template",
    alignment: str = "center",
) -> Path:
    clips: list[Path] = []
    for index, item in enumerate(timeline, start=1):
        clip = _make_caption_clip(item, run_dir, template_name, highlight_words, animation, index, font_scale, vertical_position, max_chars_per_line, font_type, alignment)
        clips.append(clip)
    list_file = run_dir / "caption-concat.txt"
    list_file.write_text("\n".join(f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in clips) + "\n", encoding="utf-8")
    overlay = run_dir / "caption-overlay.mov"
    run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(overlay)])
    return overlay


def write_vtt(srt: Path, output: Path) -> Path:
    """Write a browser-native caption file from the generated SRT timeline."""
    text = srt.read_text(encoding="utf-8")
    text = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
    output.write_text("WEBVTT\n\n" + text, encoding="utf-8")
    return output


def make_silent_audio(output: Path, total_duration: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{max(total_duration, 1):.3f}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(output),
        ]
    )
    return output


def normalize_clip(source: Path, output: Path, clip_duration: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in IMAGE_EXTENSIONS:
        cmd = [
            ffmpeg_bin(),
            "-y",
            "-loop",
            "1",
            "-t",
            f"{clip_duration:.3f}",
            "-i",
            str(source),
        ]
    else:
        cmd = [
            ffmpeg_bin(),
            "-y",
            "-stream_loop",
            "-1",
            "-t",
            f"{clip_duration:.3f}",
            "-i",
            str(source),
        ]
    cmd.extend(
        [
            "-an",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )
    run(cmd)
    return output


def concat_clips(clips: list[Path], output: Path) -> Path:
    list_file = output.parent / "concat.txt"
    def escape_concat_path(path: Path) -> str:
        return str(path).replace("'", "'\\''")

    list_file.write_text(
        "\n".join(f"file '{escape_concat_path(path)}'" for path in clips) + "\n",
        encoding="utf-8",
    )
    run(
        [
            ffmpeg_bin(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output),
        ]
    )
    return output


def burn_caption_overlay(
    video: Path,
    audio: Path,
    overlay: Path,
    output: Path,
    total_duration: float,
    sfx: Path | None = None,
) -> Path:
    """Hard-burn the rendered RGBA caption layer into the video.

    This deliberately does not fall back to a soft mov_text track: advertising
    platforms often discard or hide soft subtitles, so a successful render must
    visibly contain its captions.
    """
    command = [ffmpeg_bin(), "-y", "-i", str(video), "-i", str(audio), "-i", str(overlay)]
    if sfx and sfx.exists():
        command.extend(["-i", str(sfx)])
        filter_complex = (
            "[0:v][2:v]overlay=0:0:format=auto:eof_action=pass[v];"
            "[1:a]aresample=44100[narration];"
            "[3:a]aresample=44100[sfx];"
            "[narration][sfx]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        audio_map = "[a]"
    else:
        filter_complex = "[0:v][2:v]overlay=0:0:format=auto:eof_action=pass[v]"
        audio_map = "1:a:0"
    command.extend(
        [
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", audio_map, "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart",
            str(output),
        ]
    )
    run(command)
    return output


def render_lite_video(
    keyword: str,
    script: str,
    material_dir: str,
    audio_file: str = "",
    name: str = "",
    seconds_per_sentence: float = 4.0,
    caption_template: str = "viral",
    caption_animation: str = "pop",
    highlight_words: list[str] | None = None,
    cta_text: str = "",
    sentence_material_dirs: list[str] | None = None,
    render_captions: bool = True,
    material_files: list[str] | None = None,
) -> dict[str, Any]:
    if material_files:
        # Explicit, user-ordered clip selection (delivery workbench "素材内容").
        # The ordering is honoured because clips are handed to caption chunks in
        # sequence below; a dir scan would re-sort them alphabetically.
        materials = [Path(item).expanduser() for item in material_files if Path(item).expanduser().is_file()]
        if not materials:
            raise FileNotFoundError("selected material files not found")
        if not material_dir:
            material_dir = str(materials[0].parent)
    else:
        materials = media_files(material_dir)
        if not materials:
            raise FileNotFoundError(f"no media files found in material dir: {material_dir}")
    sentence_materials = [media_files(folder) for folder in (sentence_material_dirs or [])]

    sentences = split_sentences(script)
    run_name = safe_stem(name or f"render-{keyword}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    run_dir = settings.storage_dir / "renders" / run_name
    clips_dir = run_dir / "clips"
    run_dir.mkdir(parents=True, exist_ok=True)

    estimated_duration = max(len(sentences) * seconds_per_sentence, seconds_per_sentence)
    audio = Path(audio_file).expanduser() if audio_file else make_silent_audio(run_dir / "audio.mp3", estimated_duration)
    if audio_file and not audio.exists():
        raise FileNotFoundError(f"audio file not found: {audio}")
    audio_duration = duration(audio)
    total_duration = audio_duration if audio_duration > 0 else estimated_duration
    sentence_time_ranges = sentence_ranges(script, total_duration, audio)
    caption_template = caption_template if caption_template in CAPTION_TEMPLATES else "viral"
    caption_animation = caption_animation if caption_animation in CAPTION_ANIMATIONS else "pop"
    selected_highlights = [word.strip() for word in (highlight_words or []) if word.strip()]
    selected_highlights.extend(word for word in AUTO_HIGHLIGHT_WORDS if word in script and word not in selected_highlights)
    timeline = caption_timeline(script, total_duration, audio, cta_text=cta_text, highlight_words=selected_highlights)

    clips: list[Path] = []
    for index, item in enumerate(timeline, start=1):
        if sentence_materials:
            midpoint = (float(item["start"]) + float(item["end"])) / 2
            pool_index = next(
                (position for position, bounds in enumerate(sentence_time_ranges) if midpoint <= bounds["end"]),
                len(sentence_materials) - 1,
            )
            pool_index = min(pool_index, len(sentence_materials) - 1)
            pool = sentence_materials[pool_index] or materials
        else:
            pool = materials
        source = pool[(index - 1) % len(pool)]
        clip_duration = max(float(item["end"]) - float(item["start"]), 0.55)
        clips.append(normalize_clip(source, clips_dir / f"clip-{index:03d}.mp4", clip_duration))

    combined = concat_clips(clips, run_dir / "combined.mp4")
    subtitle = write_srt(timeline, run_dir / "subtitle.srt")
    subtitle_vtt = write_vtt(subtitle, run_dir / "subtitle.vtt")
    preview_base_video = mux_preview_video(combined, audio, run_dir / "preview-base.mp4", total_duration)
    caption_overlay: Path | None = None
    pycaps_transcript: Path | None = None
    caption_sfx: Path | None = None
    if not render_captions:
        # The first render is an editable draft: keep one clean, audio-backed
        # base and the semantic timeline. Captions are burned only after the
        # user confirms typography in the final preview step.
        final = preview_base_video
    elif caption_template == PYCAPS_TEMPLATE_NAME:
        final, pycaps_transcript, caption_sfx = _render_pycaps_caption(
            preview_base_video,
            audio,
            timeline,
            run_dir,
            caption_animation,
            True,
            total_duration,
        )
    else:
        caption_overlay = make_caption_overlay(
            timeline,
            run_dir,
            caption_template,
            selected_highlights,
            animation=caption_animation,
        )
        caption_sfx = make_caption_sfx(timeline, run_dir, total_duration)
        final = burn_caption_overlay(combined, audio, caption_overlay, run_dir / "final.mp4", total_duration, sfx=caption_sfx)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "exhibitflow_lite_internal",
        "keyword": keyword,
        "script": script,
        "sentence_count": len(sentences),
        "caption_count": len(timeline),
        "caption_template": caption_template,
        "caption_template_label": (CAPTION_TEMPLATES.get(caption_template) or CAPTION_TEMPLATES["viral"])["label"],
        "caption_animation": caption_animation,
        "caption_preview_mode": "browser_overlay",
        "caption_style_engine": (
            "browser-overlay-draft" if not render_captions
            else "pycaps" if caption_template == PYCAPS_TEMPLATE_NAME
            else "pycaps-compatible-css"
        ),
        "highlight_words": selected_highlights,
        "caption_timeline": timeline,
        "duration_seconds": total_duration,
        "sentence_time_ranges": sentence_time_ranges,
        "material_dir": str(Path(material_dir).expanduser()),
        "sentence_material_dirs": sentence_material_dirs or [],
        "materials": [str(path) for path in materials],
        "audio_file": str(audio),
        "combined_video": str(combined),
        "base_video": str(combined),
        "preview_base_video": str(preview_base_video),
        "subtitle": str(subtitle),
        "subtitle_vtt": str(subtitle_vtt),
        "caption_overlay": str(caption_overlay) if caption_overlay else "",
        "pycaps_transcript": str(pycaps_transcript) if pycaps_transcript else "",
        "caption_sfx": str(caption_sfx) if caption_sfx else "",
        "caption_sfx_enabled": bool(caption_sfx),
        "captions_hard_burned": bool(render_captions),
        "draft_video": not render_captions,
        "final_video": str(final),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "manifest.json", manifest)
    return manifest


def render_caption_variant(
    source_video: str,
    audio_file: str,
    timeline: list[dict[str, Any]],
    *,
    name: str = "",
    caption_template: str = "viral",
    caption_animation: str = "pop",
    highlight_words: list[str] | None = None,
    cta_text: str = "",
    caption_sfx_enabled: bool = True,
    caption_font_scale: float = 100.0,
    caption_vertical_position: float = 28.0,
    caption_max_chars: int | None = None,
    caption_font_type: str = "template",
    caption_alignment: str = "center",
) -> dict[str, Any]:
    """Apply one selected caption style to an existing generated video.

    This is deliberately a post-processing operation: material selection,
    narration and subtitle timing are not regenerated. It is used by the
    preview page's "apply style" action.
    """
    source = Path(source_video).expanduser()
    audio = Path(audio_file).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"字幕预览基底视频不存在：{source}")
    if not audio.is_file():
        raise FileNotFoundError(f"字幕预览配音不存在：{audio}")
    template = caption_template if caption_template in CAPTION_TEMPLATES else "viral"
    animation = caption_animation if caption_animation in CAPTION_ANIMATIONS else "pop"
    font_scale = max(0.70, min(1.50, float(caption_font_scale or 100.0) / 100.0))
    vertical_position = max(15.0, min(50.0, float(caption_vertical_position or 28.0)))
    # Larger glyphs need fewer characters per row so the safe-area padding is
    # preserved; smaller glyphs can use more of the 9:16 frame naturally.
    max_chars_per_line = max(6, min(14, int(caption_max_chars or round(9 / font_scale))))
    font_type = caption_font_type if caption_font_type in CAPTION_FONT_TYPES else "template"
    alignment = caption_alignment if caption_alignment in {"left", "center", "right"} else "center"
    selected_highlights = [str(word).strip() for word in (highlight_words or []) if str(word).strip()]
    total_duration = duration(audio) or duration(source) or max(
        (float(item.get("end") or 0) for item in timeline),
        default=1.0,
    )
    run_name = safe_stem(name or f"caption-variant-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    run_dir = settings.storage_dir / "renders" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    normalized_timeline = [
        {
            **item,
            "start": max(0.0, min(total_duration, float(item.get("start") or 0))),
            "end": max(0.0, min(total_duration, float(item.get("end") or total_duration))),
        }
        for item in timeline
        if str(item.get("text") or "").strip()
    ]
    normalized_timeline = caption_sentence_timeline(normalized_timeline)
    subtitle = write_srt(normalized_timeline, run_dir / "subtitle.srt")
    subtitle_vtt = write_vtt(subtitle, run_dir / "subtitle.vtt")
    # Keep a clean, audio-backed base alongside every variant.  Historical
    # records can therefore reopen the style editor even after the original
    # generation request has finished.
    preview_base_video = mux_preview_video(source, audio, run_dir / "preview-base.mp4", total_duration)
    caption_overlay: Path | None = None
    pycaps_transcript: Path | None = None
    if template == PYCAPS_TEMPLATE_NAME:
        final, pycaps_transcript, caption_sfx = _render_pycaps_caption(
            preview_base_video,
            audio,
            normalized_timeline,
            run_dir,
            animation,
            caption_sfx_enabled,
            total_duration,
            font_scale=font_scale,
            vertical_position=vertical_position,
            font_type=font_type,
        )
    else:
        caption_overlay = make_caption_overlay(
            normalized_timeline,
            run_dir,
            template,
            selected_highlights,
            animation=animation,
            font_scale=font_scale,
            vertical_position=vertical_position,
            max_chars_per_line=max_chars_per_line,
            font_type=font_type,
            alignment=alignment,
        )
        caption_sfx = make_caption_sfx(normalized_timeline, run_dir, total_duration) if caption_sfx_enabled else None
        final = burn_caption_overlay(source, audio, caption_overlay, run_dir / "final.mp4", total_duration, sfx=caption_sfx)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "exhibitflow_lite_caption_variant",
        "caption_preview_mode": "browser_overlay",
        "caption_style_engine": "pycaps" if template == PYCAPS_TEMPLATE_NAME else "pycaps-compatible-css",
        "caption_template": template,
        "caption_template_label": (CAPTION_TEMPLATES.get(template) or CAPTION_TEMPLATES["viral"])["label"],
        "caption_animation": animation,
        "caption_animation_label": (CAPTION_ANIMATIONS.get(animation) or CAPTION_ANIMATIONS["pop"])["label"],
        "caption_font_scale": round(font_scale * 100),
        "caption_vertical_position": round(vertical_position),
        "caption_max_chars": max_chars_per_line,
        "caption_font_type": font_type,
        "caption_font_label": CAPTION_FONT_TYPES[font_type]["label"],
        "caption_alignment": alignment,
        "highlight_words": selected_highlights,
        "caption_timeline": normalized_timeline,
        "duration_seconds": total_duration,
        "source_video": str(source),
        "base_video": str(source),
        "preview_base_video": str(preview_base_video),
        "audio_file": str(audio),
        "subtitle": str(subtitle),
        "subtitle_vtt": str(subtitle_vtt),
        "caption_overlay": str(caption_overlay) if caption_overlay else "",
        "pycaps_transcript": str(pycaps_transcript) if pycaps_transcript else "",
        "caption_sfx": str(caption_sfx) if caption_sfx else "",
        "caption_sfx_enabled": bool(caption_sfx_enabled),
        "captions_hard_burned": True,
        "final_video": str(final),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "manifest.json", manifest)
    return manifest


def import_links(platform: str, keyword: str, links_text: str) -> dict[str, Any]:
    urls = [line.strip() for line in re.split(r"[\n,，]+", links_text or "") if line.strip()]
    items = [
        {
            "rank": index,
            "platform": platform,
            "platform_label": "小红书" if platform == "xiaohongshu" else "抖音",
            "url": url,
            "title": f"{keyword} 参考样本 {index}",
            "desc": "",
            "digg_count": 0,
            "comment_count": 0,
            "share_count": 0,
            "interaction_score": 0,
        }
        for index, url in enumerate(urls, start=1)
    ]
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": "manual-import",
        "source": "manual",
        "platform": platform,
        "platform_label": "小红书" if platform == "xiaohongshu" else "抖音",
        "query": keyword,
        "requested_limit": len(items),
        "count": len(items),
        "items": items,
    }
    out = settings.storage_dir / "manifests" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-manual-{platform}-{safe_stem(keyword)}.json"
    manifest["_manifest_path"] = str(write_json(out, manifest))
    return manifest


def copy_local_samples(platform: str, keyword: str, files: list[Any]) -> dict[str, Any]:
    target = settings.storage_dir / "downloads" / safe_stem(keyword)
    target.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, uploaded in enumerate(files, start=1):
        name = getattr(uploaded, "name", f"sample-{index}.mp4")
        suffix = Path(name).suffix.lower() or ".mp4"
        output = target / f"sample-{index:03d}{suffix}"
        output.write_bytes(uploaded.getbuffer())
        items.append(
            {
                "rank": index,
                "platform": platform,
                "platform_label": "小红书" if platform == "xiaohongshu" else "抖音",
                "url": str(output),
                "title": Path(name).stem,
                "download_ok": True,
                "download_path": str(output),
                "interaction_score": 0,
            }
        )
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": "local-upload",
        "source": "local",
        "platform": platform,
        "platform_label": "小红书" if platform == "xiaohongshu" else "抖音",
        "query": keyword,
        "count": len(items),
        "items": items,
        "download_dir": str(target),
    }
    out = settings.storage_dir / "manifests" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-local-{platform}-{safe_stem(keyword)}.json"
    manifest["_manifest_path"] = str(write_json(out, manifest))
    return manifest
