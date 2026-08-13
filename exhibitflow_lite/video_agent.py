"""Internal Skill-driven planner for exhibition videos.

The planner is deliberately separate from Remotion.  The model reads the
versioned project Skill and returns a constrained VideoPlan; the renderer only
receives the normalised plan and never executes model-produced code.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import qwen
from .language import default_cta, is_english, normalize_output_language, screen_copy_limit, script_usable_for_language
from .video_orchestrator import normalise_production_mode
from .config import settings


SKILL_NAME = "exhibition-video"
SKILL_VERSION = "1.1.0"
SKILL_RELATIVE_PATH = "skills/exhibition-video/SKILL.md"
SKILL_PATH = settings.project_root / SKILL_RELATIVE_PATH
ALLOWED_ROLES = {"title", "value", "data", "audience", "cta"}
VISUAL_ROLES = ("venue", "industry", "business", "atmosphere")


def _clean(value: Any, limit: int = 800) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _canonical(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _split_values(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[\n,，、;；|]+", str(value or ""))
    return [re.sub(r"^\s*[-*•]\s*", "", str(item)).strip() for item in raw if str(item).strip()]


def _label_value(text: str, labels: tuple[str, ...]) -> str:
    if not text:
        return ""
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"^\s*(?:[-*•]\s*)?(?:{label_pattern})\s*[：:]\s*(.+?)\s*$", re.I)
    for line in str(text).splitlines():
        match = pattern.match(line)
        if match:
            return _clean(match.group(1))
    return ""


def _title(payload: dict[str, Any]) -> str:
    theme_text = str(payload.get("theme_text") or payload.get("theme_document") or "")
    title = _clean(payload.get("event_name") or payload.get("theme_title") or "", 120)
    if title:
        return title
    for line in theme_text.splitlines():
        heading = re.sub(r"^\s*#+\s*", "", line).strip()
        if heading:
            return _clean(heading, 120)
    topic = _clean(payload.get("topic") or "主题展会推广", 120)
    return re.sub(r"^(?:主题|活动名称|展会主题)\s*[：:]\s*", "", topic).strip() or "主题展会推广"


def _context(payload: dict[str, Any]) -> dict[str, Any]:
    theme_text = str(payload.get("theme_text") or payload.get("theme_document") or "").strip()
    title = _title(payload)
    promo_focus = _clean(
        payload.get("promo_focus")
        or _label_value(theme_text, ("想要宣传的点", "宣传重点", "推广重点", "核心宣传点", "展会解决路径", "卖点")),
        1200,
    )
    audience = _clean(
        payload.get("audience")
        or payload.get("target_audience")
        or _label_value(theme_text, ("用户受众", "目标受众", "受众", "目标用户", "面向人群")),
        1200,
    )
    background = _clean(payload.get("background_context") or "", 5000)
    if not background:
        background = "\n".join(
            item for item in [
                f"想要宣传的点：{promo_focus}" if promo_focus else "",
                f"用户受众：{audience}" if audience else "",
            ] if item
        )
    raw_highlights = payload.get("highlights") or payload.get("highlight_words")
    highlights = [_clean(item, 80) for item in _split_values(raw_highlights) if _clean(item, 80)]
    if not highlights:
        match = re.search(r"(?:亮点|核心亮点|数据亮点|展会亮点)\s*[：:]\s*([^\n]+)", theme_text, re.I)
        if match:
            highlights = [_clean(item, 80) for item in _split_values(match.group(1)) if _clean(item, 80)]
    visual_terms = [_clean(item, 80) for item in _split_values(payload.get("visual_terms") or payload.get("search_terms")) if _clean(item, 80)]
    reference_profile = payload.get("reference_profile") or payload.get("referenceProfile") or {}
    if not isinstance(reference_profile, dict):
        reference_profile = {}
    return {
        "title": title,
        "promo_focus": promo_focus,
        "audience": audience,
        "background_context": background,
        "highlights": highlights[:8],
        "visual_terms": visual_terms[:6],
        "theme_text": theme_text[:12000],
        "target_duration_seconds": max(10, min(120, int(float(payload.get("target_duration_seconds") or 30)))),
        "creative_direction": _clean(payload.get("creative_direction") or "trend", 120),
        "cta_text": _clean(payload.get("cta_text") or default_cta(payload.get("output_language")), 120),
        "production_mode": normalise_production_mode(payload.get("production_mode") or "auto"),
        "reference_profile": reference_profile,
        "output_language": normalize_output_language(payload.get("output_language") or payload.get("language")),
    }


def _fallback_voiceover(context: dict[str, Any]) -> str:
    title = context["title"]
    audience = context["audience"]
    promo_focus = context["promo_focus"]
    highlights = context["highlights"]
    if is_english(context.get("output_language")):
        lines = [f"If you are looking at {title}, start with what you can see on site."]
        if audience:
            lines.append(f"It is built for {audience} to compare products and solutions in one place.")
        if promo_focus:
            lines.append(f"Focus on {promo_focus}, and the comparison becomes more concrete.")
        if highlights:
            lines.append(f"Begin with {highlights[0]} and quickly map the directions that matter.")
        lines.append("Talk with the teams, check the fit, then plan the next step.")
        return "\n".join(lines)
    lines = [f"如果你正在关注{title}，可以先从现场开始了解。"]
    if audience:
        lines.append(f"面向{audience}，这里可以集中查看相关产品与方案。")
    if promo_focus:
        lines.append(f"围绕{promo_focus}，现场比较会更具体。")
    if highlights:
        lines.append(f"从{highlights[0]}开始，快速梳理你关心的方向。")
    lines.append("你可以直接沟通并验证适配，再安排下一步计划。")
    return "\n".join(lines)


def _fallback_screen_copy(context: dict[str, Any]) -> list[dict[str, str]]:
    limit = screen_copy_limit(context.get("output_language"))
    items: list[dict[str, str]] = [{"text": context["title"][:limit], "role": "title"}]
    if context["promo_focus"]:
        items.append({"text": context["promo_focus"][:limit], "role": "value"})
    for highlight in context["highlights"]:
        if not any(item["text"] == highlight[:limit] for item in items):
            items.append({"text": highlight[:limit], "role": "data"})
    if context["audience"]:
        prefix = "For " if is_english(context.get("output_language")) else "面向："
        items.append({"text": f"{prefix}{context['audience']}"[:limit], "role": "audience"})
    return items[:8]


def _fallback_visual_plan(context: dict[str, Any]) -> dict[str, Any]:
    industry_terms = context["visual_terms"] or ["industrial production line", "product manufacturing", "factory equipment"]
    return {
        "industry": context["title"],
        "video_type": "trade show promotion",
        "visual_tone": "professional and energetic",
        "groups": [
            {"role": "venue", "ratio": 0.25, "terms": ["trade show exhibition hall", "expo booth interior"]},
            {"role": "industry", "ratio": 0.30, "terms": industry_terms[:3]},
            {"role": "business", "ratio": 0.25, "terms": ["business meeting exhibition", "buyer supplier discussion"]},
            {"role": "atmosphere", "ratio": 0.20, "terms": ["professional trade show crowd", "exhibition networking"]},
        ],
    }


def _normalise_screen_copy(raw: Any, fallback: list[dict[str, str]]) -> list[dict[str, str]]:
    values = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        return fallback
    result: list[dict[str, str]] = []
    for item in values:
        if isinstance(item, dict):
            text = item.get("text") or item.get("value") or item.get("content")
            role = str(item.get("role") or "value").strip().lower()
        else:
            text, role = item, "value"
        text = _clean(text, 42).strip("。！？!?；;")
        if not text or role not in ALLOWED_ROLES:
            continue
        if any(word in text for word in ("镜头", "画面", "字幕", "旁白", "配音", "转场")):
            continue
        if not any(_canonical(existing["text"]) == _canonical(text) for existing in result):
            result.append({"text": text, "role": role})
    return result[:8] or fallback


def _normalise_visual_plan(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    raw_groups = raw.get("groups") if isinstance(raw, dict) else []
    by_role = {
        str(item.get("role") or "").strip().lower(): item
        for item in raw_groups
        if isinstance(item, dict)
    }
    groups: list[dict[str, Any]] = []
    for fallback_group in fallback["groups"]:
        role = fallback_group["role"]
        candidate = by_role.get(role) or {}
        terms = []
        for term in [*(candidate.get("terms") or []), *fallback_group["terms"]]:
            clean = _clean(term, 80)
            if clean and clean not in terms:
                terms.append(clean)
            if len(terms) >= 4:
                break
        groups.append({
            "role": role,
            "ratio": float(fallback_group["ratio"]),
            "terms": terms[:4],
        })
    return {
        "industry": _clean((raw or {}).get("industry") if isinstance(raw, dict) else "", 80) or fallback["industry"],
        "video_type": _clean((raw or {}).get("video_type") if isinstance(raw, dict) else "", 80) or fallback["video_type"],
        "visual_tone": _clean((raw or {}).get("visual_tone") if isinstance(raw, dict) else "", 160) or fallback["visual_tone"],
        "groups": groups,
        "source": "internal-skill",
    }


def _skill_metadata() -> dict[str, str]:
    try:
        content = SKILL_PATH.read_bytes()
    except OSError:
        content = b""
    return {
        "name": SKILL_NAME,
        "version": SKILL_VERSION,
        "path": SKILL_RELATIVE_PATH,
        "sha256": hashlib.sha256(content).hexdigest() if content else "missing",
    }


def _skill_prompt() -> str:
    try:
        return SKILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return "展会视频 Skill 文件缺失。仅输出符合 VideoPlan 协议的 JSON，不要输出代码。"


def _build_user_prompt(context: dict[str, Any], manual_script: str) -> str:
    return f"""
请根据展会信息生成 VideoPlan JSON。

展会主题：{context['title']}
想要宣传的点：{context['promo_focus']}
用户受众：{context['audience']}
已确认数字亮点：{json.dumps(context['highlights'], ensure_ascii=False)}
网络检索词（可选）：{json.dumps(context['visual_terms'], ensure_ascii=False)}
目标时长：约 {context['target_duration_seconds']} 秒
表达方向：{context['creative_direction']}
结尾行动引导：{context['cta_text']}
背景信息：{context['background_context']}
主题原文：{context['theme_text']}
用户选择的成片方式：{context['production_mode']}
参考视频 VL 画像：{json.dumps(context['reference_profile'], ensure_ascii=False)[:4000]}
用户已有口播稿（如果非空且语言匹配必须保留，不要重写）：{manual_script[:10000]}

要求：
1. 输出语言必须是 {"English" if is_english(context.get("output_language")) else "简体中文"}。voiceover 和 screen_copy 都使用该语言。
2. 如果已有口播稿且语言匹配，voiceover 原样返回；否则生成适合 TTS 的纯口播正文，每句一行。
3. screen_copy 必须和 voiceover 有关联但不逐句重复；明确数字必须保留。
4. visual_plan 只使用 venue、industry、business、atmosphere 四组，关键词使用具体可拍摄的英文场景或动作。
5. production.recommended_mode 只能是 montage、avatar、hybrid；没有可靠画像时选 montage。
6. 只返回 JSON 对象。
""".strip()


def generate_video_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the internal exhibition-video Skill and return a safe VideoPlan."""
    payload = dict(payload or {})
    context = _context(payload)
    manual_script = _clean(payload.get("script") or payload.get("manual_script") or "", 12000)
    fallback_screen = _fallback_screen_copy(context)
    fallback_visual = _fallback_visual_plan(context)
    raw: dict[str, Any] = {}
    provider = "fallback"
    status = "fallback"
    warnings: list[str] = []

    if settings.text_llm_api_key:
        try:
            result = qwen.generate_skill_json(
                _skill_prompt(),
                _build_user_prompt(context, manual_script),
                temperature=0.55,
                max_tokens=2200,
            )
            if isinstance(result, dict):
                raw = result
                provider = "qwen"
                status = "generated"
        except Exception as exc:
            warnings.append(f"内部 Skill 模型调用失败，使用安全回退：{type(exc).__name__}")
    else:
        warnings.append("未配置 TEXT_LLM_API_KEY，使用内部 Skill 的规则回退方案")

    voiceover = qwen.clean_voiceover_copy(raw.get("voiceover") or raw.get("script") or "")
    if script_usable_for_language(manual_script, context["output_language"]):
        voiceover = qwen.clean_voiceover_copy(manual_script)
    if not voiceover:
        voiceover = _fallback_voiceover(context)

    screen_copy = _normalise_screen_copy(raw.get("screen_copy") or raw.get("screenCopy"), fallback_screen)
    # User-provided facts are never allowed to disappear because of an
    # abstract model response; they also power the Remotion number ticker.
    for highlight in context["highlights"]:
        clean = highlight[:28]
        if clean and not any(_canonical(item["text"]) == _canonical(clean) for item in screen_copy):
            screen_copy.append({"text": clean, "role": "data"})
    if not any(_canonical(item["text"]) == _canonical(context["title"][:28]) for item in screen_copy):
        screen_copy.insert(0, {"text": context["title"][:28], "role": "title"})
    screen_copy = screen_copy[:8]

    visual_plan = _normalise_visual_plan(raw.get("visual_plan") or raw.get("visualPlan"), fallback_visual)
    style_raw = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    style = {
        "animation_profile": _clean(style_raw.get("animation_profile") or "kinetic-pop", 40),
        "accent": _clean(style_raw.get("accent") or "red-blue", 40),
        "intensity": _clean(style_raw.get("intensity") or "high", 20),
    }
    production_raw = raw.get("production") if isinstance(raw.get("production"), dict) else {}
    recommended_mode = normalise_production_mode(production_raw.get("recommended_mode") or "montage")
    if recommended_mode == "auto":
        recommended_mode = "montage"
    production = {
        "recommended_mode": recommended_mode,
        "reason": _clean(production_raw.get("reason") or "未提供可靠参考画像，使用稳定混剪", 240),
    }
    return {
        "plan_version": "1.1",
        "skill": _skill_metadata(),
        "agent": {
            "mode": "internal-skill",
            "provider": provider,
            "model": settings.text_llm_model if provider == "qwen" else "rules-fallback",
            "status": status,
            "warnings": warnings,
        },
        "input": {
            "title": context["title"],
            "promo_focus": context["promo_focus"],
            "audience": context["audience"],
            "target_duration_seconds": context["target_duration_seconds"],
        },
        "voiceover": voiceover,
        "screen_copy": screen_copy,
        "visual_plan": visual_plan,
        "style": style,
        "production": production,
        "copy_relation": {
            "voiceover": "完整解释主题、受众和现场价值，由 Edge TTS 朗读。",
            "screen": "提炼标题、价值关键词、受众标签和明确数据，用于 Remotion 动态文字。",
            "rule": "两套文案共享主题背景，但屏幕短文案不逐句复述旁白。",
        },
    }
