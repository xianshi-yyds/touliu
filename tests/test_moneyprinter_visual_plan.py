from __future__ import annotations

from pathlib import Path

from exhibitflow_lite import qwen, stock


def test_fallback_plan_keeps_anxiety_out_of_visual_terms() -> None:
    plan = qwen._fallback_visual_search_plan(
        "新能源展会，企业负责人担心获客成本高、渠道分散并产生焦虑"
    )
    assert plan["industry"] == "新能源产业"
    assert [group["role"] for group in plan["groups"]] == [
        "venue", "industry", "business", "atmosphere"
    ]
    terms = " ".join(term for group in plan["groups"] for term in group["terms"])
    assert "anxiety" not in terms
    assert "stress" not in terms
    assert "electric vehicle expo" in terms


def test_model_plan_filters_emotion_terms_and_restores_exhibition_anchors() -> None:
    fallback = qwen._fallback_visual_search_plan("新能源产业展")
    normalized = qwen._normalize_visual_search_plan(
        {
            "industry": "new energy",
            "groups": [
                {"role": "venue", "terms": ["business anxiety", "large hall"]},
                {"role": "industry", "terms": ["stressed worker", "solar panel display"]},
                {"role": "business", "terms": ["buyer discussion"]},
                {"role": "atmosphere", "terms": ["modern building"]},
            ],
        },
        fallback,
    )
    by_role = {group["role"]: group["terms"] for group in normalized["groups"]}
    all_terms = " ".join(term for terms in by_role.values() for term in terms)
    assert "anxiety" not in all_terms
    assert "stressed" not in all_terms
    assert "large hall trade show" in by_role["venue"]
    assert "buyer discussion" in by_role["business"]
    assert "modern building conference venue" in by_role["atmosphere"]


def test_sentence_roles_show_industry_instead_of_emotional_people() -> None:
    dirs = {role: f"/tmp/{role}" for role in ("venue", "industry", "business", "atmosphere")}
    _, roles = stock.visual_role_dirs_for_sentences(
        [
            "欢迎来到新能源产业展。",
            "获客成本高、渠道分散让企业越来越焦虑。",
            "在现场集中比较方案并连接采购商。",
            "点击下方链接，立即报名吧。",
        ],
        dirs,
        cta_text="点击下方链接，立即报名吧",
    )
    assert roles == ["venue", "industry", "business", "venue"]


def test_download_plan_applies_quota_to_every_role(tmp_path: Path, monkeypatch) -> None:
    def fake_search(term: str, minimum_duration: int = 5, per_page: int = 20):
        key = term.replace(" ", "-")
        return [
            {"provider": "pexels", "term": term, "duration": 6, "url": f"https://media.test/{key}-{index}.mp4"}
            for index in range(3)
        ]

    class FakeResponse:
        content = b"video"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(stock, "search_pexels", fake_search)
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kwargs: FakeResponse())
    plan = qwen._fallback_visual_search_plan("新能源产业展")
    result = stock.download_moneyprinter_visual_plan(
        plan, tmp_path, target_duration=20, source="pexels", max_clip_duration=5
    )
    assert result["strategy"] == "moneyprinter_exhibition_visual_plan"
    assert set(result["role_dirs"]) == {"venue", "industry", "business", "atmosphere"}
    assert all(group["count"] >= 1 for group in result["groups"])
    assert all(Path(path).is_file() for path in result["files"])
