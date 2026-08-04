"""Resolve one stable production mode for the topic-video pipeline.

The caller may explicitly choose a mode, or leave it on ``auto`` so a future
VL analyser can recommend one through ``reference_profile``.  This module is
deliberately provider-agnostic: it decides the structure, while RunningHub and
Remotion remain adapters behind that decision.
"""

from __future__ import annotations

from typing import Any


PRODUCTION_MODES = {"auto", "montage", "avatar", "hybrid"}
AVATAR_MODES = {"avatar", "hybrid"}

_MODE_ALIASES = {
    "mix": "montage",
    "mixed-cut": "montage",
    "混剪": "montage",
    "talking-head": "avatar",
    "digital-human": "avatar",
    "数字人": "avatar",
    "数字人口播": "avatar",
    "mixed": "hybrid",
    "混合": "hybrid",
    "混合型": "hybrid",
}


def normalise_production_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    return mode if mode in PRODUCTION_MODES else "auto"


def _reference_profile(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("reference_profile", "referenceProfile", "vl_profile", "reference_analysis"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    sample = payload.get("sample")
    if isinstance(sample, dict):
        for key in ("reference_profile", "analysis", "vl_profile"):
            value = sample.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _profile_mode(profile: dict[str, Any]) -> str:
    for key in ("suggested_mode", "production_mode", "content_type", "video_type", "type"):
        value = profile.get(key)
        if value:
            mode = normalise_production_mode(value)
            if mode != "auto":
                return mode
    talking_ratio = profile.get("talking_head_ratio")
    try:
        ratio = float(talking_ratio)
    except (TypeError, ValueError):
        ratio = -1
    if ratio >= 0.72:
        return "avatar"
    if ratio >= 0.22:
        return "hybrid"
    return "montage"


def resolve_production_plan(payload: dict[str, Any], *, avatar_available: bool) -> dict[str, Any]:
    """Return the auditable mode decision used by the render pipeline.

    Explicit user choices win.  ``auto`` can consume the future VL output but
    never requires it; without a usable recommendation the stable default is
    montage.  Avatar-dependent modes fall back to montage when the provider or
    portrait is unavailable.
    """

    raw_requested = payload.get("production_mode") or payload.get("productionMode")
    if normalise_production_mode(raw_requested) == "auto" and (
        payload.get("supplement_avatar") or payload.get("digital_human_enabled")
    ):
        # Backwards compatibility for projects created before the mode selector
        # existed, and for users who still use the AI-supplement toggle: the
        # former top-right avatar option maps to hybrid mode.
        raw_requested = "hybrid"
    requested = normalise_production_mode(raw_requested or "auto")
    profile = _reference_profile(payload)
    skill_plan = payload.get("skill_plan") if isinstance(payload.get("skill_plan"), dict) else {}
    skill_production = skill_plan.get("production") if isinstance(skill_plan.get("production"), dict) else {}
    skill_recommended = normalise_production_mode(skill_production.get("recommended_mode") or "montage")
    recommended = _profile_mode(profile) if profile else ("montage" if skill_recommended == "auto" else skill_recommended)
    resolved = recommended if requested == "auto" else requested
    reason = "reference-profile" if requested == "auto" and profile else "agent-skill" if requested == "auto" and skill_production else "stable-default"
    if requested != "auto":
        reason = "user-selected"

    fallback = ""
    if resolved in AVATAR_MODES and not avatar_available:
        fallback = resolved
        resolved = "montage"
        reason = "avatar-unavailable-fallback"

    confidence = profile.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = None

    return {
        "requested": requested,
        "resolved": resolved,
        "recommended": recommended,
        "reason": reason,
        "fallbackFrom": fallback,
        "avatarRequired": resolved in AVATAR_MODES,
        "layout": "fullscreen" if resolved == "avatar" else "circle-pip" if resolved == "hybrid" else "none",
        "confidence": confidence,
        "referenceProfile": profile,
    }


def avatar_scene_ids(scene_ids: list[str], mode: str) -> list[str]:
    """Choose deterministic avatar appearances for one generated timeline."""

    mode = normalise_production_mode(mode)
    if mode == "avatar":
        return list(scene_ids)
    if mode != "hybrid" or not scene_ids:
        return []
    if len(scene_ids) <= 2:
        return list(scene_ids)
    # Opening establishes the presenter; a middle reminder and final CTA make
    # the hybrid mode visibly different without covering every stock shot.
    selected = [scene_ids[0], scene_ids[len(scene_ids) // 2], scene_ids[-1]]
    return list(dict.fromkeys(selected))
