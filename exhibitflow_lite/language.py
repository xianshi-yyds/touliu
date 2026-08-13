from __future__ import annotations

import re
from typing import Any

ZH = "zh"
EN = "en"

DEFAULT_CTA = {
    ZH: "点击下方链接，立即了解展会信息",
    EN: "Tap the link below to learn more about the exhibition.",
}

DEFAULT_EDGE_VOICE = {
    ZH: "zh-CN-XiaoxiaoNeural",
    EN: "en-US-JennyNeural",
}

EDGE_VOICES_BY_LANG = {
    ZH: (
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoyiNeural",
        "zh-CN-YunjianNeural",
        "zh-CN-YunxiNeural",
    ),
    EN: (
        "en-US-JennyNeural",
        "en-US-AriaNeural",
        "en-US-GuyNeural",
        "en-US-ChristopherNeural",
    ),
}

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def normalize_output_language(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"en", "eng", "english", "en-us", "en-gb"}:
        return EN
    if text in {"zh", "cn", "zh-cn", "zh-hans", "chinese", "中文", "简体中文"}:
        return ZH
    return ZH


def is_english(value: Any) -> bool:
    return normalize_output_language(value) == EN


def looks_chinese(text: Any) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def script_usable_for_language(script: str, language: Any) -> bool:
    text = str(script or "").strip()
    if not text:
        return False
    if is_english(language):
        return not looks_chinese(text)
    return True


def default_cta(language: Any) -> str:
    return DEFAULT_CTA[normalize_output_language(language)]


def default_edge_voice(language: Any) -> str:
    return DEFAULT_EDGE_VOICE[normalize_output_language(language)]


def resolve_edge_voice(voice: Any, language: Any) -> str:
    lang = normalize_output_language(language)
    selected = str(voice or "").strip()
    allowed = EDGE_VOICES_BY_LANG[lang]
    if selected in allowed:
        return selected
    return DEFAULT_EDGE_VOICE[lang]


def screen_copy_limit(language: Any) -> int:
    return 42 if is_english(language) else 28
