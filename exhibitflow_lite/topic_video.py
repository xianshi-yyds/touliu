"""Topic-driven kinetic exhibition video generation.

This module is the bridge between the SaaS task API and the reusable Remotion
composition.  It deliberately keeps the input contract small: a topic, an
optional theme document, optional copy/highlights, a network-stock provider and
an Edge voice.  The output is self-contained under ``storage/video_projects``
and the Remotion public directory so an old project can be reopened and
rendered again without rebuilding the plan by hand.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from . import avatar, qwen, render, stock, tts, video_orchestrator
from .config import settings
from .language import default_cta, is_english, normalize_output_language, resolve_edge_voice, script_usable_for_language
from .storage import safe_stem


ProgressCallback = Callable[[int, str], None]

FPS = 30
REMOTION_ROOT = settings.project_root / "remotion_exhibition_promo"
REMOTION_PUBLIC = REMOTION_ROOT / "public"
DEFAULT_CTA = default_cta("zh")
DEFAULT_EDGE_VOICE = tts.DEFAULT_EDGE_VOICE
DEFAULT_FONT_STYLE = "impact"
DEFAULT_TRANSITION = "wipe"
DEFAULT_TRANSITION_SECONDS = 0.42
ASPECT_DIMENSIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

FONT_STYLES = {"impact", "tech", "editorial", "mono"}
TRANSITIONS = {"cut", "fade", "wipe", "flash"}

_FIELD_LABELS = {
    "title": ("主题", "展会主题", "活动名称", "展会名称", "标题", "主题标题"),
    "promo_focus": ("想要宣传的点", "宣传重点", "推广重点", "核心宣传点", "宣传角度", "展会解决路径", "解决路径", "价值", "核心价值", "卖点"),
    "audience": ("用户受众", "目标受众", "受众", "面向人群", "目标用户"),
    "selling": ("展会解决路径", "解决路径", "价值", "核心价值", "卖点"),
    "pain": ("目标受众痛点", "痛点", "问题"),
    "highlights": ("亮点", "核心亮点", "数据亮点", "展会亮点"),
    "date": ("日期", "展期", "时间"),
    "location": ("地点", "展馆", "举办地点"),
    "cta": ("CTA", "行动引导", "结尾引导"),
}


def _progress(callback: ProgressCallback | None, value: int, stage: str) -> None:
    if callback is not None:
        callback(value, stage)


def _clean_text(value: Any, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _aspect_spec(value: Any) -> tuple[str, int, int]:
    ratio = str(value or "16:9").strip().lower().replace("／", "/")
    aliases = {"landscape": "16:9", "横屏": "16:9", "portrait": "9:16", "竖屏": "9:16", "square": "1:1", "方形": "1:1"}
    ratio = aliases.get(ratio, ratio)
    if ratio not in ASPECT_DIMENSIONS:
        ratio = "16:9"
    width, height = ASPECT_DIMENSIONS[ratio]
    return ratio, width, height


def _normalise_font_style(value: Any) -> str:
    style = str(value or DEFAULT_FONT_STYLE).strip().lower()
    aliases = {
        "bold": "impact",
        "粗体": "impact",
        "科技": "tech",
        "科技感": "tech",
        "杂志": "editorial",
        "衬线": "editorial",
        "数据": "mono",
        "等宽": "mono",
    }
    style = aliases.get(style, style)
    return style if style in FONT_STYLES else DEFAULT_FONT_STYLE


def _normalise_transition(value: Any) -> str:
    transition = str(value or DEFAULT_TRANSITION).strip().lower()
    aliases = {
        "直接": "cut",
        "直接切换": "cut",
        "淡入淡出": "fade",
        "擦除": "wipe",
        "横向擦除": "wipe",
        "闪白": "flash",
        "闪白切换": "flash",
    }
    transition = aliases.get(transition, transition)
    return transition if transition in TRANSITIONS else DEFAULT_TRANSITION


def _transition_seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_TRANSITION_SECONDS
    return max(0.16, min(1.2, seconds))


def _label_value(text: str, labels: tuple[str, ...]) -> str:
    if not text:
        return ""
    joined = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"^\s*(?:[-*•]\s*)?(?:{joined})\s*[：:]\s*(.+?)\s*$", re.I)
    for line in str(text).splitlines():
        match = pattern.match(line)
        if match:
            return _clean_text(match.group(1), 800)
    return ""


def _structured_theme(text: str) -> dict[str, Any]:
    """Parse an uploaded JSON theme without making Markdown parsing brittle."""
    try:
        value = json.loads(str(text or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_heading(text: str) -> str:
    for line in str(text or "").splitlines():
        value = re.sub(r"^\s*#+\s*", "", line).strip()
        if value:
            return _clean_text(value, 100)
    return ""


def _theme_value(payload: dict[str, Any], theme_text: str, key: str) -> str:
    direct_keys = {
        "title": ("event_name", "theme_title", "title"),
        "promo_focus": ("promo_focus", "promotion_focus", "promotional_focus"),
        "audience": ("audience", "target_audience", "target_users"),
        "selling": ("selling", "value"),
        "pain": ("pain",),
        "date": ("date", "event_date"),
        "location": ("location", "venue"),
        "cta": ("cta_text", "cta"),
    }
    structured = _structured_theme(theme_text)
    for candidate in direct_keys.get(key, ()):
        for source in (payload, structured):
            value = _clean_text(source.get(candidate), 800) if isinstance(source, dict) else ""
            if value:
                return value
    for label in _FIELD_LABELS.get(key, ()):
        value = _clean_text(structured.get(label), 800)
        if value:
            return value
    return _label_value(theme_text, _FIELD_LABELS.get(key, ()))


def _topic_title(payload: dict[str, Any], theme_text: str) -> str:
    title = _theme_value(payload, theme_text, "title")
    if title:
        return title
    topic = _clean_text(payload.get("topic"), 120)
    if topic:
        first = topic.splitlines()[0].strip()
        first = re.sub(r"^(?:展会主题|主题|活动名称|标题)\s*[：:]\s*", "", first)
        return _clean_text(first, 100) or "主题展会推广"
    return _first_heading(theme_text) or "主题展会推广"


def _background_context(payload: dict[str, Any], theme_text: str = "") -> str:
    """Return the explicit user context that should travel with every generation."""
    direct = str(payload.get("background_context") or "").strip()
    if direct:
        return direct[:6000]
    promo_focus = _theme_value(payload, theme_text, "promo_focus") or _theme_value(payload, theme_text, "selling")
    audience = _theme_value(payload, theme_text, "audience")
    return "\n".join(
        item for item in [
            f"想要宣传的点：{promo_focus}" if promo_focus else "",
            f"用户受众：{audience}" if audience else "",
        ] if item
    )[:6000]


def _append_cta(script: str, cta: str) -> str:
    clean_script = str(script or "").strip()
    clean_cta = str(cta or "").strip().rstrip("。！？!?；;，,")
    if not clean_cta or not clean_script:
        return clean_script
    compact_script = re.sub(r"[\s。！？!?；;，,]+", "", clean_script)
    compact_cta = re.sub(r"[\s。！？!?；;，,]+", "", clean_cta)
    ending = "." if re.search(r"[A-Za-z]", clean_cta) and not re.search(r"[\u3400-\u9fff]", clean_cta) else "。"
    if not re.search(r"[.!?。！？]$", clean_cta):
        clean_cta = f"{clean_cta}{ending}"
    return clean_script if compact_script.endswith(compact_cta) else f"{clean_script}\n\n{clean_cta}"


def _fallback_script(title: str, theme_text: str, payload: dict[str, Any]) -> str:
    """Make a readable script when an LLM key is intentionally unavailable."""
    lines: list[str] = []
    audience = _theme_value(payload, theme_text, "audience")
    pain = _theme_value(payload, theme_text, "pain")
    selling = _theme_value(payload, theme_text, "selling")
    if is_english(payload.get("output_language") or payload.get("language")):
        if title:
            lines.append(f"If you are looking at {title}, start with what you can see on site.")
        if audience:
            lines.append(f"It is built for {audience} to compare products and solutions in one place.")
        if pain:
            lines.append(f"If you are dealing with {pain}, on-site comparison becomes more concrete.")
        if selling:
            lines.append(f"You can explore {selling}, talk with the teams, and check the fit.")
        if not lines:
            lines.append(f"Learn about {title}, from the theme and products to the next conversation.")
        return "\n".join(lines)
    if title:
        lines.append(f"如果你正在关注{title}，可以先从现场开始了解。")
    if audience:
        lines.append(f"面向{audience}，这里可以集中查看相关产品与方案。")
    if pain:
        lines.append(f"如果你正在面对{pain}，现场比较会更具体。")
    if selling:
        lines.append(f"你可以围绕{selling}，直接沟通并验证适配。")
    if not lines:
        lines.append(f"欢迎了解{title}，从主题、产品和合作路径开始。")
    return "\n".join(lines)


def _build_script(payload: dict[str, Any], title: str, theme_text: str) -> str:
    language = normalize_output_language(payload.get("output_language") or payload.get("language"))
    manual = str(payload.get("script") or payload.get("manual_script") or "").strip()
    if script_usable_for_language(manual, language):
        return manual
    topic = _clean_text(payload.get("topic") or title, 120)
    context = "\n".join(
        item for item in [topic, str(payload.get("background_context") or "").strip(), theme_text] if item
    ).strip()
    try:
        generated = qwen.generate_copy(
            context,
            payload.get("sample") if isinstance(payload.get("sample"), dict) else {},
            target_duration_seconds=int(float(payload.get("target_duration_seconds") or 30)),
            creative_direction=str(payload.get("creative_direction") or "trend"),
            reference_logic=payload.get("reference_logic") if isinstance(payload.get("reference_logic"), dict) else None,
            output_language=language,
        )
        if generated.strip():
            return generated.strip()
    except Exception:
        # A missing text model should not make a manually uploaded theme
        # unusable. The API result records the final script, while the task
        # error remains reserved for a real TTS/search/render failure.
        pass
    return _fallback_script(title, theme_text, payload)


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\n,，、;；|]+", str(value or ""))
    return [re.sub(r"^\s*[-*•]\s*", "", str(item)).strip() for item in raw if str(item).strip()]


def _label_block_values(text: str, labels: tuple[str, ...]) -> list[str]:
    """Read both ``亮点：a,b`` and Markdown-style bullet blocks."""
    label_pattern = re.compile(rf"^\s*(?:[-*•]\s*)?(?:{'|'.join(re.escape(label) for label in labels)})\s*[：:]?\s*(.*)$", re.I)
    values: list[str] = []
    collecting = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        match = label_pattern.match(line)
        if match:
            collecting = True
            values.extend(_split_values(match.group(1)))
            continue
        if collecting and re.match(r"^\s*[-*•]\s+", raw_line):
            values.extend(_split_values(line))
            continue
        if collecting and line:
            collecting = False
    return [value for value in values if value]


def _explicit_highlights(payload: dict[str, Any], theme_text: str) -> list[str]:
    raw = payload.get("highlights") or payload.get("highlight_words")
    values = _split_values(raw)
    if not values:
        structured = _structured_theme(theme_text)
        raw = next(
            (structured.get(key) for key in ("highlights", "亮点", "核心亮点", "数据亮点", "展会亮点") if structured.get(key)),
            "",
        )
        values = _split_values(raw)
    if not values:
        values = _label_block_values(theme_text, _FIELD_LABELS["highlights"])
    cleaned: list[str] = []
    for value in values:
        value = re.sub(r"^\s*(?:亮点|卖点)\s*[：:]\s*", "", value).strip()
        if value and value not in cleaned:
            cleaned.append(value[:60])
    return cleaned[:5]


def _highlights(payload: dict[str, Any], theme_text: str, script: str) -> list[str]:
    values = _explicit_highlights(payload, theme_text)
    if values:
        return values
    # Keep the scene count tied to the narration when no separate highlight
    # list was supplied. Screen-copy generation deliberately does not use this
    # fallback, otherwise the full voiceover would reappear as on-screen text.
    return [value[:60] for value in render.split_sentences(script) if value.strip()][:5]


def _synthesise_edge(script: str, voice: str, stem: str) -> tuple[Path, str, str, list[dict[str, Any]], list[str]]:
    sentences = render.split_sentences(script)
    if not sentences:
        sentences = [script]
    requested_voice = voice if voice in tts.EDGE_VOICES else DEFAULT_EDGE_VOICE
    used_service = "edge"
    used_voice = requested_voice
    attempts: list[dict[str, Any]] = []
    segments: list[Path] = []
    for index, sentence in enumerate(sentences, start=1):
        audio, active_service, active_voice, errors = tts.synthesize_resilient(
            sentence,
            service="edge",
            voice=used_voice,
            output_name=f"{stem}-part-{index:02d}.mp3",
        )
        segments.append(audio)
        attempts.append({
            "sentence": index,
            "text": sentence,
            "requested_service": "edge",
            "used_service": active_service,
            "used_voice": active_voice,
            "fallback_errors": errors,
        })
        used_service = active_service
        used_voice = active_voice
    combined = tts.concat_segments(segments, output_name=f"{stem}.mp3", text=script, voice=used_voice)
    return combined, used_service, used_voice, attempts, sentences


def _copy_network_assets(downloaded: dict[str, Any], project_id: str) -> tuple[dict[str, list[str]], list[str], Path]:
    target_root = REMOTION_PUBLIC / "assets" / "topic-video" / project_id
    target_root.mkdir(parents=True, exist_ok=True)
    by_role: dict[str, list[str]] = {}
    all_files: list[str] = []
    role_dirs = downloaded.get("role_dirs") if isinstance(downloaded.get("role_dirs"), dict) else {}
    for raw_role, raw_dir in role_dirs.items():
        role = safe_stem(str(raw_role or "scene"))
        source_dir = Path(str(raw_dir)).expanduser()
        if not source_dir.is_dir():
            continue
        role_target = target_root / role
        role_target.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_dir.iterdir()):
            if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                continue
            destination = role_target / source.name
            if not destination.exists() or destination.stat().st_size != source.stat().st_size:
                shutil.copy2(source, destination)
            relative = destination.relative_to(REMOTION_PUBLIC).as_posix()
            by_role.setdefault(role, []).append(relative)
            all_files.append(relative)
    return by_role, all_files, target_root


def _copy_audio(audio: Path, project_id: str) -> str:
    target = REMOTION_PUBLIC / "assets" / "topic-video" / project_id / "voiceover.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(audio, target)
    return target.relative_to(REMOTION_PUBLIC).as_posix()


def _copy_avatar_video(video: Path, project_id: str) -> str:
    suffix = video.suffix.lower() if video.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"} else ".mp4"
    target = REMOTION_PUBLIC / "assets" / "topic-video" / project_id / f"digital-human{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, target)
    return target.relative_to(REMOTION_PUBLIC).as_posix()


def _copy_bgm(payload: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    """Copy a user-selected BGM into the immutable Remotion project assets.

    The browser only submits a relative BGM id returned by ``/api/bgms``.  The
    resolver intentionally accepts files from the project BGM library (and a
    built-in Remotion BGM directory for local development) but never an
    arbitrary server path.
    """
    selected = str(payload.get("bgm") or payload.get("bgm_name") or "").strip()
    if not selected or selected.lower() in {"none", "off", "无", "无bgm"}:
        return None
    allowed = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    library_root = (settings.storage_dir / "bgms").resolve()
    built_in_root = (REMOTION_PUBLIC / "assets" / "bgm").resolve()
    raw_path = Path(selected).expanduser()
    if raw_path.is_absolute():
        candidates = [raw_path.resolve()]
    else:
        candidates = [(library_root / selected).resolve(), (built_in_root / selected).resolve()]
    source = next(
        (
            candidate
            for candidate in candidates
            if candidate.is_file()
            and candidate.suffix.lower() in allowed
            and (
                candidate == library_root
                or library_root in candidate.parents
                or candidate == built_in_root
                or built_in_root in candidate.parents
            )
        ),
        None,
    )
    if source is None:
        raise FileNotFoundError("所选 BGM 不存在，或不在 BGM 素材库内")
    target = REMOTION_PUBLIC / "assets" / "topic-video" / project_id / f"bgm{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    try:
        duration = round(float(render.duration(source)) or 0.0, 3)
    except Exception:
        duration = 0.0
    try:
        volume = float(payload.get("bgm_volume") or 0.16)
    except (TypeError, ValueError):
        volume = 0.16
    return {
        "file": target.relative_to(REMOTION_PUBLIC).as_posix(),
        "name": source.name,
        "duration": duration,
        "volume": max(0.04, min(0.32, volume)),
        "loop": True,
    }


def _numeric_run(text: str, accent: str, base_color: str) -> list[dict[str, Any]]:
    # Chinese copy commonly embeds figures directly after a character, e.g.
    # ``汇聚2000+`` or ``覆盖8大``.  Only reject a digit immediately before
    # the match so those embedded figures still receive the counter animation.
    pattern = re.compile(r"(?<!\d)(?P<number>\d[\d,]*(?:\.\d+)?)(?P<unit>万|亿|％|%|㎡|平方|大|强|家|场|个|项|吨|\+)?")
    match = pattern.search(text)
    if not match:
        return [{"text": text, "color": base_color}]
    raw_number = match.group("number").replace(",", "")
    try:
        target = int(round(float(raw_number)))
    except ValueError:
        return [{"text": text, "color": base_color}]
    before = text[:match.start()]
    after = text[match.end():]
    runs: list[dict[str, Any]] = []
    if before:
        runs.append({"text": before, "color": base_color})
    runs.append({"text": match.group("number"), "color": accent, "counter": target, "suffix": match.group("unit") or ""})
    if after:
        runs.append({"text": after, "color": base_color})
    return runs


def _wrap_visual_text(text: str, limit: int = 16) -> list[str]:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return [compact]
    # Prefer punctuation/space boundaries so number tokens stay intact.
    separators = "，。！？；,.!? /·"
    candidates = [index for index, char in enumerate(compact) if char in separators and 3 <= index < len(compact) - 3]
    if candidates:
        split_at = min(candidates, key=lambda index: abs(index - len(compact) / 2)) + 1
    else:
        split_at = min(limit, max(4, len(compact) // 2))
    return [compact[:split_at].strip(), compact[split_at:].strip()]


def _normalise_screen_copy(value: Any, limit: int = 28) -> list[dict[str, str]]:
    """Normalise user/model screen-copy output into a small manifest contract."""
    if isinstance(value, dict):
        raw_items = value.get("items") or value.get("screenCopy") or value.get("screen_copy") or []
    elif isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = _split_values(value)
    else:
        raw_items = []
    if not isinstance(raw_items, list):
        return []
    allowed_roles = {"title", "value", "data", "audience", "cta"}
    result: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("value") or item.get("content")
            role = str(item.get("role") or "value").strip().lower()
        else:
            text = item
            role = "value"
        clean = re.sub(r"\s+", " ", str(text or "")).strip().strip("。！？!?；;")[:limit]
        if not clean or role not in allowed_roles:
            continue
        if any(word in clean for word in ("镜头", "画面", "字幕", "旁白", "配音")):
            continue
        if not any(existing["text"] == clean for existing in result):
            result.append({"text": clean, "role": role})
    return result


def _screen_copy_plan(
    payload: dict[str, Any],
    title: str,
    script: str,
    highlights: list[str],
    theme_text: str,
) -> list[dict[str, str]]:
    """Create a visual copy plan that is related to, but not a transcript of, the voiceover."""
    explicit = payload.get("screen_copy") or payload.get("visual_copy")
    plan = _normalise_screen_copy(explicit)
    explicit_highlights = _explicit_highlights(payload, theme_text)
    promo_focus = _theme_value(payload, theme_text, "promo_focus") or _theme_value(payload, theme_text, "selling")
    audience = _theme_value(payload, theme_text, "audience")
    background_context = _background_context(payload, theme_text)
    if not background_context:
        background_context = "\n".join(
            item for item in [
                theme_text,
            ] if item
        )
    if not plan:
        try:
            plan = qwen.generate_screen_copy_plan(
                title,
                script,
                background_context=background_context,
                highlights=explicit_highlights,
                promo_focus=promo_focus,
                audience=audience,
                max_items=7,
                output_language=str(payload.get("output_language") or payload.get("language") or "zh"),
            )
        except Exception:
            plan = []
    plan = _normalise_screen_copy(plan)
    if not plan:
        plan = _normalise_screen_copy([
            {"text": title, "role": "title"},
            {"text": promo_focus, "role": "value"},
            *[{"text": item, "role": "data"} for item in explicit_highlights],
            {"text": (f"For {audience}" if is_english(payload.get("output_language")) else f"面向：{audience}"), "role": "audience"} if audience else {},
        ])
    # Keep the title as the first visual beat even if the model returned a
    # value or audience label first. This matches the reference composition.
    title_item = next((item for item in plan if item["text"] == title), None)
    plan = [item for item in plan if item["text"] != title]
    plan.insert(0, title_item or {"text": title[:28], "role": "title"})
    # Explicit highlights are facts supplied by the user and must survive a
    # model response that chose only abstract value words; they also power the
    # 0 -> target numeric animation in Remotion.
    for highlight in explicit_highlights:
        clean = re.sub(r"\s+", " ", str(highlight or "")).strip().strip("。！？!?；;")[:28]
        if clean and not any(item["text"] == clean for item in plan):
            plan.append({"text": clean, "role": "data"})
    return plan[:8]


def _scene_units(
    title: str,
    script: str,
    highlights: list[str],
    cta: str,
    screen_copy: list[dict[str, str]] | None = None,
) -> list[str]:
    screen_copy = screen_copy or []
    units: list[str] = [title]
    for item in screen_copy:
        value = re.sub(r"[\n\r]+", " ", str(item.get("text") or "")).strip()
        if value and value not in units:
            units.append(value)
    # If there is no usable visual plan (for example a brand-new theme with no
    # focus, audience or highlights), keep the old safe fallback so the video
    # still has enough readable scenes.
    if len(units) == 1:
        sentences = [item for item in render.split_sentences(script) if item.strip()]
        for value in sentences:
            value = re.sub(r"[\n\r]+", " ", value).strip()
            if value and value not in units:
                units.append(value)
        for value in highlights:
            if value and value not in units:
                units.append(value)
    final = cta.strip().rstrip("。！？!?；;")
    if final and final not in units:
        units.append(final)
    units = [item[:70] for item in units if item]
    if len(units) > 9:
        units = units[:8] + [units[-1]]
    return units[:9]


def _build_manifest(
    *,
    payload: dict[str, Any],
    project_id: str,
    title: str,
    script: str,
    cta: str,
    audio_duration: float,
    audio_relative: str,
    role_files: dict[str, list[str]],
    all_files: list[str],
    downloaded: dict[str, Any],
    visual_plan: dict[str, Any],
    tts_info: dict[str, Any],
    bgm_info: dict[str, Any] | None,
    digital_human_info: dict[str, Any] | None,
    theme_text: str,
) -> dict[str, Any]:
    highlights = _highlights(payload, theme_text, script)
    screen_copy = _screen_copy_plan(payload, title, script, highlights, theme_text)
    background_context = _background_context(payload, theme_text)
    skill_plan = payload.get("skill_plan") if isinstance(payload.get("skill_plan"), dict) else {}
    agent_info = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    aspect_ratio, width, height = _aspect_spec(
        payload.get("aspect_ratio") or payload.get("aspectRatio") or payload.get("video_ratio")
    )
    font_style = _normalise_font_style(payload.get("font_style") or payload.get("fontStyle"))
    transition_type = _normalise_transition(payload.get("transition") or payload.get("transition_type"))
    transition_duration = _transition_seconds(
        payload.get("transition_duration_seconds") or payload.get("transitionDurationSeconds")
    )
    units = _scene_units(title, script, highlights, cta, screen_copy)
    if not units:
        units = [title]
    total_duration = max(8.0, round(audio_duration + 0.75, 3))
    weights = [0.72 + min(1.6, len(unit) / 10.0) for unit in units]
    weight_total = sum(weights) or 1.0
    durations = [total_duration * weight / weight_total for weight in weights]
    # A readable single-screen rhythm is more important than equal scene
    # lengths; very short scenes make the bottom-pop animation unreadable.
    floor = 1.55 if len(units) >= 8 else 1.8
    if min(durations) < floor:
        durations = [max(floor, value) for value in durations]
        scale = total_duration / sum(durations)
        durations = [value * scale for value in durations]
    role_order = ["venue", "atmosphere", "industry", "industry", "business", "business", "industry", "atmosphere", "venue"]
    available_roles = [role for role, files in role_files.items() if files]
    if not available_roles and all_files:
        available_roles = ["all"]
        role_files["all"] = list(all_files)
    if not available_roles:
        raise RuntimeError("网络素材已经下载，但没有可用于 Remotion 的视频文件")
    scenes: list[dict[str, Any]] = []
    cursor = 0.0
    accents = ["#1f64dc", "#39c8e8", "#f0332b", "#1f64dc", "#f0332b", "#1f64dc", "#f0332b", "#ffffff", "#1f64dc"]
    for index, (unit, duration) in enumerate(zip(units, durations)):
        role = role_order[index % len(role_order)]
        if role not in role_files:
            role = available_roles[index % len(available_roles)]
        files = role_files.get(role) or all_files
        video = files[index % len(files)]
        opening = index == 0
        final = index == len(units) - 1
        align = "left" if opening or final else ("right" if index % 4 == 3 else "center")
        base_color = "#102448" if opening or final else "#ffffff"
        line_size = 108 if opening or final else (82 if len(unit) > 22 else 94)
        lines = []
        for wrapped in _wrap_visual_text(unit, 15 if line_size >= 94 else 18):
            lines.append({"runs": _numeric_run(wrapped, accents[index % len(accents)], base_color), "size": line_size})
        subline = ""
        if opening:
            date = _theme_value(payload, theme_text, "date")
            location = _theme_value(payload, theme_text, "location")
            subline = "  /  ".join(value for value in [date, location] if value)
        elif final:
            subline = str(payload.get("subline") or "SEE YOU THERE  /  MAKE THE NEXT CONNECTION").strip()
        scenes.append({
            "id": f"{index + 1:02d}",
            "start": round(cursor, 3),
            "duration": round(duration, 3),
            "video": video,
            "lines": lines,
            "align": align,
            "top": 350 if opening else (410 if final else 545),
            "accent": accents[index % len(accents)],
            "subline": subline,
            "copyRole": next(
                (str(item.get("role") or "value") for item in screen_copy if str(item.get("text") or "") == unit),
                "value",
            ),
            "opening": opening,
            "final": final,
            "flash": index > 0 and index % 2 == 0,
        })
        cursor += duration
    # Eliminate cumulative rounding gaps on the final scene.
    if scenes:
        scenes[-1]["duration"] = round(max(0.1, total_duration - float(scenes[-1]["start"])), 3)
    resolved_mode = video_orchestrator.normalise_production_mode(
        (digital_human_info or {}).get("mode") or payload.get("production_mode") or "montage"
    )
    visible_avatar_scenes = video_orchestrator.avatar_scene_ids(
        [str(scene["id"]) for scene in scenes], resolved_mode
    )
    if digital_human_info is not None:
        digital_human_info["visibleSceneIds"] = visible_avatar_scenes
    for scene in scenes:
        scene["visualSource"] = "avatar" if str(scene["id"]) in visible_avatar_scenes else "broll"
    return {
        "version": 1,
        "template": "AutoPartsKinetic",
        "projectId": project_id,
        "title": title,
        "brand": str(payload.get("brand") or title).strip()[:80],
        "fps": FPS,
        "width": width,
        "height": height,
        "aspectRatio": aspect_ratio,
        "durationSeconds": total_duration,
        "fontStyle": font_style,
        "transition": {
            "type": transition_type,
            "durationSeconds": transition_duration,
        },
        "bgm": bgm_info,
        "digitalHuman": digital_human_info or {"enabled": False, "status": "disabled"},
        "productionPlan": payload.get("production_plan") or {
            "requested": payload.get("production_mode") or "auto",
            "resolved": resolved_mode,
        },
        "scenes": scenes,
        "voiceSegments": [{"file": audio_relative, "start": 0, "duration": round(audio_duration, 3)}],
        "script": script,
        "voiceoverScript": script,
        "screenCopy": screen_copy,
        "backgroundContext": background_context,
        "copyRelation": skill_plan.get("copy_relation") or {
            "voiceover": "完整解释主题、受众和现场价值，由 Edge TTS 朗读。",
            "screen": "提炼标题、价值关键词、受众标签和明确数据，用于动态大字展示。",
            "rule": "屏幕短文案与旁白相关但不逐句重复；明确数字优先保留并进入 0→目标值滚动动画。",
        },
        "agent": agent_info,
        "skill": skill_plan.get("skill") or {},
        "videoPlan": {
            "version": skill_plan.get("plan_version") or "legacy",
            "style": {
                **(skill_plan.get("style") if isinstance(skill_plan.get("style"), dict) else {}),
                "font_style": font_style,
                "transition": transition_type,
            },
            "screen_copy": screen_copy,
            "visual_plan": visual_plan,
        },
        "cta": cta,
        "highlights": highlights,
        "networkSearch": {
            "provider": downloaded.get("source"),
            "orientation": downloaded.get("orientation") or "landscape",
            "strategy": downloaded.get("strategy") or "moneyprinter_exhibition_visual_plan",
            "terms": downloaded.get("search_terms") or [],
            "groups": downloaded.get("groups") or [],
            "visualPlan": visual_plan,
            "files": all_files,
        },
        "tts": tts_info,
    }


def _render_with_remotion(props_file: Path, output_file: Path) -> str:
    remotion_bin = REMOTION_ROOT / "node_modules" / ".bin" / "remotion"
    if remotion_bin.is_file():
        executable = str(remotion_bin)
        prefix: list[str] = []
    else:
        executable = "npx"
        prefix = ["--yes"]
    command = [
        executable,
        *prefix,
        "render",
        "src/index.ts",
        "AutoPartsKinetic",
        str(output_file),
        f"--props={props_file}",
        "--concurrency=2",
    ]
    result = subprocess.run(
        command,
        cwd=str(REMOTION_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1200,
    )
    if result.returncode != 0 or not output_file.is_file() or output_file.stat().st_size == 0:
        raise RuntimeError(f"Remotion 动态主题成片失败：{result.stdout[-3500:]}")
    return result.stdout[-5000:]


def _write_summary_markdown(path: Path, manifest: dict[str, Any], theme_text: str, render_log: str) -> None:
    search = manifest.get("networkSearch") or {}
    tts_info = manifest.get("tts") or {}
    digital_human = manifest.get("digitalHuman") or {}
    production_plan = manifest.get("productionPlan") or {}
    lines = [
        f"# {manifest.get('title') or '主题视频'} · 动态成片记录",
        "",
        "## 生成结论",
        "",
        f"- 模板：`{manifest.get('template')}`",
        f"- 画幅：`{manifest.get('width')}×{manifest.get('height')} / {manifest.get('fps')}fps`",
        f"- 比例：`{manifest.get('aspectRatio') or '16:9'}`",
        f"- 时长：`{manifest.get('durationSeconds')} 秒`",
        f"- 项目编号：`{manifest.get('projectId')}`",
        f"- 配音：`{tts_info.get('service')} / {tts_info.get('voice')}`",
        f"- 字体样式：`{manifest.get('fontStyle') or 'impact'}`",
        f"- 镜头转场：`{(manifest.get('transition') or {}).get('type') or 'cut'}` / `{(manifest.get('transition') or {}).get('durationSeconds') or 0}s`",
        f"- BGM：`{(manifest.get('bgm') or {}).get('name') if manifest.get('bgm') else '未启用'}`",
        f"- 生产模式：请求 `{production_plan.get('requested') or 'auto'}` → 实际 `{production_plan.get('resolved') or 'montage'}`",
        f"- 模式决策：`{production_plan.get('reason') or 'stable-default'}`",
        f"- 数字人：`{(manifest.get('digitalHuman') or {}).get('status') or '未启用'}`",
        f"- 数字人位置：`{digital_human.get('position') or '未启用'}` / `{digital_human.get('shape') or ''}`",
        "",
        "## 主题输入",
        "",
        "```text",
        theme_text.strip()[:12000],
        "```",
        "",
        "## 宣传背景信息",
        "",
        manifest.get("backgroundContext") or "未单独提供宣传重点和用户受众。",
        "",
        "## 内部 Agent / Skill",
        "",
        f"- Agent 模式：`{(manifest.get('agent') or {}).get('mode') or 'legacy'}`",
        f"- Agent 提供方：`{(manifest.get('agent') or {}).get('provider') or 'legacy'}`",
        f"- Skill：`{(manifest.get('skill') or {}).get('name') or '未使用'}@{(manifest.get('skill') or {}).get('version') or ''}`",
        f"- Skill 状态：`{(manifest.get('agent') or {}).get('status') or 'legacy'}`",
        "- Agent 只生成结构化 VideoPlan；最终视频由 Remotion 根据 manifest 渲染。",
        "",
        "## 口播稿",
        "",
        manifest.get("script") or "",
        "",
        "## 屏幕短文案（与旁白关联但不重复）",
        "",
        "旁白负责完整解释；屏幕文字负责抓住标题、价值关键词、受众和数据。两者共享主题背景，但不会把旁白逐句铺到画面上。",
        "",
    ]
    for item in manifest.get("screenCopy") or []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('role') or 'value'}`：{item.get('text') or ''}")
    lines.extend([
        "",
        "## 网络素材检索",
        "",
        f"- 来源：`{search.get('provider')}`",
        f"- 方向：`{search.get('orientation')}`",
        f"- 检索策略：`{search.get('strategy')}`",
        f"- 关键词：{', '.join(search.get('terms') or [])}",
        f"- 下载文件数：`{len(search.get('files') or [])}`",
        "",
        "## 动效模板",
        "",
        f"- 字体样式由参数 `{manifest.get('fontStyle') or 'impact'}` 控制，Remotion 使用对应的字体栈和字重渲染。",
        f"- 镜头切换使用 `{(manifest.get('transition') or {}).get('type') or 'cut'}` 转场，默认在相邻镜头边界加入 `{(manifest.get('transition') or {}).get('durationSeconds') or 0.42}` 秒的帧驱动效果。",
        f"- BGM：`{(manifest.get('bgm') or {}).get('name') if manifest.get('bgm') else '未启用'}`；启用时循环铺满成片并压低音量，避免盖住旁白。",
        "- 每句文字使用逐字裁切擦除、从下方弹出、弹性缩放和轻微旋转。",
        "- 数字文本在 0.5 秒内从 0 滚动到输入目标值，并保留单位/后缀。",
        "- 关键段落加入扫光、闪白和结尾呼吸放大；不使用卡片式信息面板。",
        "",
        "## 渲染日志（尾部）",
        "",
        "```text",
        render_log[-5000:],
        "```",
        "",
    ])
    if digital_human.get("error"):
        lines.extend([f"- 数字人补充失败原因：`{digital_human.get('error')}`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def generate_topic_video(payload: dict[str, Any], progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Generate one theme-driven landscape kinetic video."""
    payload = dict(payload or {})
    theme_text = str(payload.get("theme_text") or payload.get("theme_document") or "").strip()[:16000]
    title = _topic_title(payload, theme_text)
    background_context = _background_context(payload, theme_text)
    raw_project_id = safe_stem(str(payload.get("video_project_id") or f"topic-{uuid4().hex[:12]}"))
    project_id = raw_project_id[:96] or f"topic-{uuid4().hex[:8]}"
    run_dir = settings.storage_dir / "video_projects" / project_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _progress(progress, 6, "解析主题输入")
    language = normalize_output_language(payload.get("output_language") or payload.get("language"))
    payload["output_language"] = language
    script = _build_script(payload, title, theme_text)
    cta = _theme_value(payload, theme_text, "cta") or default_cta(language)
    if is_english(language) and cta in {"点击下方链接，立即了解展会信息", "点击下方链接，立即报名吧"}:
        cta = default_cta(language)
    script = _append_cta(script, cta)
    theme_snapshot = theme_text or background_context or f"# {title}\n"
    (run_dir / "theme-input.md").write_text(theme_snapshot, encoding="utf-8")
    (run_dir / "script.txt").write_text(script + "\n", encoding="utf-8")
    if isinstance(payload.get("skill_plan"), dict):
        (run_dir / "video-plan.json").write_text(
            json.dumps(payload["skill_plan"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    _progress(progress, 18, "使用 Edge TTS 生成配音")
    audio, used_service, used_voice, attempts, sentences = _synthesise_edge(
        script,
        resolve_edge_voice(payload.get("voice"), language),
        safe_stem(f"{project_id}-edge"),
    )
    audio_duration = render.duration(audio)
    audio_relative = _copy_audio(audio, project_id)

    raw_digital_human = payload.get("digitalHuman")
    image_value = payload.get("digital_human_image_id") or payload.get("digital_human_image")
    legacy_avatar_requested = bool(
        payload.get("supplement_avatar")
        or payload.get("digital_human_enabled")
        or (raw_digital_human.get("enabled") if isinstance(raw_digital_human, dict) else raw_digital_human)
    )
    provider_ready = bool(settings.avatar_provider)
    image_ready = bool(image_value) if settings.avatar_provider == "runninghub" else True
    production_plan = video_orchestrator.resolve_production_plan(
        payload,
        avatar_available=bool(provider_ready and image_ready),
    )
    payload["production_plan"] = production_plan
    digital_human_enabled = bool(production_plan.get("avatarRequired"))
    digital_human_info: dict[str, Any] = {
        "enabled": digital_human_enabled,
        "provider": settings.avatar_provider if digital_human_enabled else "",
        "status": "pending" if digital_human_enabled else "disabled",
        "mode": production_plan.get("resolved") or "montage",
        "layout": production_plan.get("layout") or "none",
        "position": "top-right" if production_plan.get("layout") == "circle-pip" else "fullscreen",
        "shape": "circle" if production_plan.get("layout") == "circle-pip" else "rectangle",
    }
    if production_plan.get("fallbackFrom"):
        digital_human_info.update({
            "status": "fallback",
            "error": "数字人服务或人物形象不可用，已自动降级为混剪成片",
            "requestedMode": production_plan.get("fallbackFrom"),
        })
    elif legacy_avatar_requested and not digital_human_enabled:
        digital_human_info["error"] = "旧版数字人选项已降级为混剪成片"
    digital_human_image: Path | None = None
    if digital_human_enabled and settings.avatar_provider == "runninghub":
        digital_human_image = avatar.resolve_digital_human_image(
            str(image_value), expo_id=str(payload.get("expo_id") or "")
        )
        digital_human_info["imageName"] = digital_human_image.name
        digital_human_info["imageId"] = str(image_value)

    _progress(progress, 36, "根据主题生成网络检索计划")
    promo_focus = _theme_value(payload, theme_text, "promo_focus") or _theme_value(payload, theme_text, "selling")
    audience = _theme_value(payload, theme_text, "audience")
    aspect_ratio, _, _ = _aspect_spec(
        payload.get("aspect_ratio") or payload.get("aspectRatio") or payload.get("video_ratio")
    )
    stock_orientation = "portrait" if aspect_ratio == "9:16" else "landscape"
    search_context = "\n".join(
        item for item in [
            title,
            background_context,
            f"想要宣传的点：{promo_focus}" if promo_focus else "",
            f"用户受众：{audience}" if audience else "",
            theme_text,
            script,
        ] if item
    ).strip()
    provided_visual_plan = payload.get("visual_plan")
    if isinstance(provided_visual_plan, dict) and isinstance(provided_visual_plan.get("groups"), list):
        visual_plan = provided_visual_plan
    else:
        visual_plan = qwen.generate_visual_search_plan(search_context, script)
    structured_theme = _structured_theme(theme_text)
    manual_terms = _split_values(
        payload.get("visual_terms")
        or payload.get("search_terms")
        or structured_theme.get("visual_terms")
        or structured_theme.get("search_terms")
        or structured_theme.get("检索词")
    )
    if manual_terms:
        for group in visual_plan.get("groups") or []:
            if isinstance(group, dict) and str(group.get("role")) == "industry":
                group["terms"] = manual_terms[:4]

    source = str(payload.get("material_source") or payload.get("network_source") or "pexels").strip().lower()
    if source not in {"pexels", "pixabay"}:
        source = "pexels"
    _progress(progress, 48, f"网络检索{stock_orientation}素材（{source}）")
    online_dir = settings.storage_dir / "online_materials" / f"topic-video-{project_id}"
    downloaded = stock.download_moneyprinter_visual_plan(
        visual_plan,
        online_dir,
        target_duration=max(8.0, audio_duration),
        source=source,
        max_clip_duration=5,
        orientation=stock_orientation,
    )
    role_files, all_files, _ = _copy_network_assets(downloaded, project_id)
    bgm_info = _copy_bgm(payload, project_id)

    if digital_human_enabled:
        _progress(progress, 58, "RunningHub 生成数字人口播素材")
        try:
            avatar_clip = avatar.generate_avatar_clip(
                script,
                run_dir / "digital-human",
                voice=str(payload.get("voice") or DEFAULT_EDGE_VOICE),
                audio_file=audio,
                image_file=digital_human_image,
            )
            digital_human_info.update({
                "status": "completed",
                "file": _copy_avatar_video(avatar_clip, project_id),
                "clip": str(avatar_clip),
            })
        except Exception as exc:
            # RunningHub is an optional visual provider. Preserve the TTS and
            # downloaded stock assets, then render a complete montage instead.
            production_plan.update({
                "fallbackFrom": production_plan.get("resolved") or "avatar",
                "resolved": "montage",
                "reason": "avatar-generation-failed-fallback",
                "avatarRequired": False,
                "layout": "none",
            })
            digital_human_enabled = False
            digital_human_info.update({
                "enabled": False,
                "status": "failed-fallback",
                "mode": "montage",
                "layout": "none",
                "position": "",
                "shape": "",
                "error": str(exc)[:1000],
            })
            (run_dir / "digital-human-error.txt").write_text(str(exc) + "\n", encoding="utf-8")
            _progress(progress, 60, "数字人失败，自动切换为混剪成片")

    _progress(progress, 62, "编排主题文字与数字动效")
    manifest = _build_manifest(
        payload=payload,
        project_id=project_id,
        title=title,
        script=script,
        cta=cta,
        audio_duration=audio_duration,
        audio_relative=audio_relative,
        role_files=role_files,
        all_files=all_files,
        downloaded=downloaded,
        visual_plan=visual_plan,
        tts_info={
            "service": used_service,
            "voice": used_voice,
            "requestedService": "edge",
            "requestedVoice": str(payload.get("voice") or DEFAULT_EDGE_VOICE),
            "attempts": attempts,
            "sentences": sentences,
        },
        bgm_info=bgm_info,
        digital_human_info=digital_human_info,
        theme_text=theme_text,
    )
    manifest_file = run_dir / "manifest.json"
    props_file = REMOTION_PUBLIC / "topic-video-manifests" / f"{project_id}.json"
    props_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    props_file.write_text(json.dumps({"manifest": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_props = run_dir / "remotion-props.json"
    run_props.write_text(props_file.read_text(encoding="utf-8"), encoding="utf-8")

    _progress(progress, 70, "Remotion 编排并渲染主题成片")
    output_file = run_dir / "final.mp4"
    render_log = _render_with_remotion(props_file, output_file)
    if digital_human_enabled:
        digital_human_info["output"] = str(output_file)
    manifest["digitalHuman"] = digital_human_info
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    props_file.write_text(json.dumps({"manifest": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_props.write_text(props_file.read_text(encoding="utf-8"), encoding="utf-8")
    summary_markdown = run_dir / "generation-summary.md"
    _write_summary_markdown(summary_markdown, manifest, theme_text, render_log)
    _progress(progress, 95, "整理视频与生成记录")
    return {
        "video_project_id": project_id,
        "final_video": str(output_file),
        "audio_path": str(REMOTION_PUBLIC / audio_relative),
        "manifest": str(manifest_file),
        "props": str(props_file),
        "summary_markdown": str(summary_markdown),
        "template": "AutoPartsKinetic",
        "render_engine": "remotion-topic",
        "agent_mode": (manifest.get("agent") or {}).get("mode") or "legacy",
        "skill": manifest.get("skill") or {},
        "video_plan": str(run_dir / "video-plan.json") if isinstance(payload.get("skill_plan"), dict) else "",
        "duration_seconds": manifest["durationSeconds"],
        "script": script,
        "manifest_data": manifest,
        "material": downloaded,
        "render_log": render_log,
    }
