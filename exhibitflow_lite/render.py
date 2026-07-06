from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
        "label": "爆款强调",
        "font": "Yuanti SC",
        "font_size": 88,
        "accent_size": 98,
        "fill": (255, 255, 255, 255),
        "accent": (255, 207, 38, 255),
        "stroke": (24, 24, 24, 255),
        "accent_stroke": (190, 47, 38, 255),
        "stroke_width": 8,
        "shadow": (0, 0, 0, 150),
        "y": 1040,
        "box": False,
    },
    "business": {
        "label": "专业展会",
        "font": "Lantinghei SC",
        "font_size": 74,
        "accent_size": 82,
        "fill": (255, 255, 255, 255),
        "accent": (67, 191, 255, 255),
        "stroke": (20, 32, 52, 255),
        "accent_stroke": (20, 32, 52, 255),
        "stroke_width": 6,
        "shadow": (0, 0, 0, 140),
        "y": 1260,
        "box": False,
    },
    "minimal": {
        "label": "极简商务",
        "font": "PingFang SC",
        "font_size": 70,
        "accent_size": 76,
        "fill": (255, 255, 255, 255),
        "accent": (105, 232, 158, 255),
        "stroke": (8, 12, 20, 255),
        "accent_stroke": (8, 12, 20, 255),
        "stroke_width": 4,
        "shadow": (0, 0, 0, 120),
        "y": 1280,
        "box": True,
    },
    "energetic": {
        "label": "活力招展",
        "font": "Hiragino Sans GB",
        "font_size": 84,
        "accent_size": 100,
        "fill": (255, 255, 255, 255),
        "accent": (255, 132, 42, 255),
        "stroke": (35, 21, 20, 255),
        "accent_stroke": (191, 36, 49, 255),
        "stroke_width": 8,
        "shadow": (0, 0, 0, 155),
        "y": 980,
        "box": False,
    },
}

AUTO_HIGHLIGHT_WORDS = [
    "立即报名", "点击下方链接", "提前锁定", "专业买家", "采购商", "人流量",
    "展位", "招商", "报名", "稀缺", "免费", "限时", "国际", "专业", "成交",
]


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


def split_sentences(text: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for char in str(text or ""):
        if char in "\n。！？!?；;":
            clean = current.strip(" ，,、：:")
            if clean:
                pieces.append(clean)
            current = ""
        else:
            current += char
    clean = current.strip(" ，,、：:")
    if clean:
        pieces.append(clean)
    return pieces or [str(text or "").strip() or "展会现场精彩瞬间"]


def _clean_text(text: str) -> str:
    return re.sub(r"[\s，,。！？!?；;、：:]", "", str(text or ""))


def split_caption_chunks(text: str, max_chars: int = 11) -> list[str]:
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


def caption_timeline(
    script: str,
    total_duration: float,
    audio_file: Path,
    cta_text: str = "",
    highlight_words: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build caption groups from real Edge word boundaries when available.

    Qwen and imported audio fall back to character-weighted timings, keeping the
    entire subtitle timeline inside the actual narration duration.
    """
    timing_file = audio_file.with_suffix(".words.json")
    cta_clean = _clean_text(cta_text)
    if timing_file.is_file():
        try:
            words = (json.loads(timing_file.read_text(encoding="utf-8")) or {}).get("words") or []
        except Exception:
            words = []
        valid = [
            {
                "text": str(item.get("text") or "").strip(),
                "start": float(item.get("start") or 0),
                "end": float(item.get("start") or 0) + float(item.get("duration") or 0),
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
            groups: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            count = 0
            protected = [word for word in (highlight_words or []) + AUTO_HIGHLIGHT_WORDS if word]
            for index, word in enumerate(valid[:cta_start]):
                word_len = max(len(_clean_text(word["text"])), 1)
                combined_tail = _clean_text("".join(str(item["text"]) for item in current[-2:]) + word["text"])
                joins_highlight = any(keyword in combined_tail for keyword in protected)
                if current and count + word_len > 10 and not (joins_highlight and count + word_len <= 13):
                    groups.append(current)
                    current = []
                    count = 0
                current.append(word)
                count += word_len
            if current:
                groups.append(current)
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
    }
    preferred = Path(candidates.get(family, candidates["PingFang SC"]))
    if preferred.is_file():
        return str(preferred)
    for fallback in (Path("/System/Library/Fonts/PingFang.ttc"), Path("/System/Library/Fonts/Hiragino Sans GB.ttc")):
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
    split_at = min(max_chars, len(text) - 1)
    for begin, end in spans:
        if begin < split_at < end:
            split_at = begin if begin >= 4 else end
            break
    if split_at <= 0 or split_at >= len(text):
        split_at = len(text) // 2
    return [text[:split_at], text[split_at:]]


def render_caption_card(
    text: str,
    output: Path,
    template_name: str,
    highlight_words: list[str],
    is_cta: bool = False,
) -> Path:
    template = CAPTION_TEMPLATES.get(template_name) or CAPTION_TEMPLATES["viral"]
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_path = _font_path(str(template["font"]))
    body_font = ImageFont.truetype(font_path, int(template["font_size"]))
    accent_font = ImageFont.truetype(font_path, int(template["accent_size"]))
    if is_cta:
        body_font = ImageFont.truetype(font_path, 86)
        accent_font = ImageFont.truetype(font_path, 112)
        clean = text.strip("。！？!?；;，,")
        if "立即" in clean:
            split_at = clean.find("立即")
            lines = [clean[:split_at].strip("，,"), clean[split_at:]]
        else:
            lines = [clean]
        center_y = 1160
        # A simple down-arrow echoes the reference CTA without external sticker assets.
        draw.rounded_rectangle((110, center_y - 58, 190, center_y + 22), radius=18, fill=(236, 65, 62, 235))
        draw.polygon([(125, center_y + 15), (175, center_y + 15), (150, center_y + 62)], fill=(236, 65, 62, 235))
    else:
        lines = _wrap_caption_lines(text, highlight_words, max_chars=9)
        center_y = int(template["y"])

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
        draw.rounded_rectangle(
            (540 - max_width // 2 - 38, top - 28, 540 + max_width // 2 + 38, top + total_height + 28),
            radius=28,
            fill=(8, 12, 20, 170),
        )
    y = top
    for runs, width, height in line_metrics:
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
) -> Path:
    cards_dir = run_dir / "caption_cards"
    clips_dir = run_dir / "caption_clips"
    clips: list[Path] = []
    for index, item in enumerate(timeline, start=1):
        card = render_caption_card(
            str(item["text"]),
            cards_dir / f"caption-{index:03d}.png",
            template_name=template_name,
            highlight_words=highlight_words,
            is_cta=bool(item.get("cta")),
        )
        clip = clips_dir / f"caption-{index:03d}.mov"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip_duration = max(float(item["end"]) - float(item["start"]), 0.55)
        fade_out_start = max(clip_duration - 0.12, 0.12)
        run(
            [
                ffmpeg_bin(), "-y", "-loop", "1", "-i", str(card), "-t", f"{clip_duration:.3f}",
                "-vf", f"format=rgba,fade=t=in:st=0:d=0.12:alpha=1,fade=t=out:st={fade_out_start:.3f}:d=0.12:alpha=1,fps=30",
                "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(clip),
            ]
        )
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


def burn_caption_overlay(video: Path, audio: Path, overlay: Path, output: Path, total_duration: float) -> Path:
    """Hard-burn the rendered RGBA caption layer into the video.

    This deliberately does not fall back to a soft mov_text track: advertising
    platforms often discard or hide soft subtitles, so a successful render must
    visibly contain its captions.
    """
    run(
        [
            ffmpeg_bin(), "-y", "-i", str(video), "-i", str(audio), "-i", str(overlay),
            "-filter_complex", "[0:v][2:v]overlay=0:0:format=auto:eof_action=pass[v]",
            "-map", "[v]", "-map", "1:a:0", "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart",
            str(output),
        ]
    )
    return output


def render_lite_video(
    keyword: str,
    script: str,
    material_dir: str,
    audio_file: str = "",
    name: str = "",
    seconds_per_sentence: float = 4.0,
    caption_template: str = "viral",
    highlight_words: list[str] | None = None,
    cta_text: str = "",
) -> dict[str, Any]:
    materials = media_files(material_dir)
    if not materials:
        raise FileNotFoundError(f"no media files found in material dir: {material_dir}")

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
    selected_highlights = [word.strip() for word in (highlight_words or []) if word.strip()]
    selected_highlights.extend(word for word in AUTO_HIGHLIGHT_WORDS if word in script and word not in selected_highlights)
    timeline = caption_timeline(script, total_duration, audio, cta_text=cta_text, highlight_words=selected_highlights)

    clips: list[Path] = []
    for index, item in enumerate(timeline, start=1):
        source = materials[(index - 1) % len(materials)]
        clip_duration = max(float(item["end"]) - float(item["start"]), 0.55)
        clips.append(normalize_clip(source, clips_dir / f"clip-{index:03d}.mp4", clip_duration))

    combined = concat_clips(clips, run_dir / "combined.mp4")
    subtitle = write_srt(timeline, run_dir / "subtitle.srt")
    subtitle_vtt = write_vtt(subtitle, run_dir / "subtitle.vtt")
    caption_overlay = make_caption_overlay(timeline, run_dir, caption_template, selected_highlights)
    final = burn_caption_overlay(combined, audio, caption_overlay, run_dir / "final.mp4", total_duration)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "exhibitflow_lite_internal",
        "keyword": keyword,
        "script": script,
        "sentence_count": len(sentences),
        "caption_count": len(timeline),
        "caption_template": caption_template,
        "caption_template_label": (CAPTION_TEMPLATES.get(caption_template) or CAPTION_TEMPLATES["viral"])["label"],
        "highlight_words": selected_highlights,
        "caption_timeline": timeline,
        "duration_seconds": total_duration,
        "material_dir": str(Path(material_dir).expanduser()),
        "materials": [str(path) for path in materials],
        "audio_file": str(audio),
        "combined_video": str(combined),
        "subtitle": str(subtitle),
        "subtitle_vtt": str(subtitle_vtt),
        "caption_overlay": str(caption_overlay),
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
