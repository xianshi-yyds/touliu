from exhibitflow_lite.video_orchestrator import avatar_scene_ids, resolve_production_plan


def test_auto_defaults_to_montage_without_reference_profile():
    plan = resolve_production_plan({}, avatar_available=True)
    assert plan["resolved"] == "montage"
    assert plan["reason"] == "stable-default"


def test_auto_uses_vl_recommendation_when_avatar_is_available():
    plan = resolve_production_plan(
        {"reference_profile": {"content_type": "数字人口播", "confidence": 0.91}},
        avatar_available=True,
    )
    assert plan["resolved"] == "avatar"
    assert plan["layout"] == "fullscreen"
    assert plan["confidence"] == 0.91


def test_avatar_mode_falls_back_safely_when_unavailable():
    plan = resolve_production_plan({"production_mode": "hybrid"}, avatar_available=False)
    assert plan["resolved"] == "montage"
    assert plan["fallbackFrom"] == "hybrid"
    assert plan["reason"] == "avatar-unavailable-fallback"


def test_hybrid_uses_opening_middle_and_final_scenes():
    assert avatar_scene_ids(["01", "02", "03", "04", "05"], "hybrid") == ["01", "03", "05"]


def test_legacy_avatar_toggle_maps_auto_to_hybrid():
    plan = resolve_production_plan(
        {"production_mode": "auto", "supplement_avatar": True},
        avatar_available=True,
    )
    assert plan["requested"] == "hybrid"
    assert plan["resolved"] == "hybrid"
