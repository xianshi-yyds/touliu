from exhibitflow_lite import qwen


def test_client_report_fallback_keeps_exhibition_scope() -> None:
    report = qwen._fallback_client_report(
        "华南国际新能源产业展",
        "新能源产业 / B2B 专业展",
        {"location": "广州"},
    )

    assert report["exhibition_name"] == "华南国际新能源产业展"
    assert report["exhibition_category"] == "新能源产业 / B2B 专业展"
    assert report["video_recommendation"]["recommended"] is True
    assert report["content_brief"]["audience"]
    assert "未提供的数据" in report["evidence_note"]


def test_client_report_normalizer_limits_and_fills_fields() -> None:
    report = qwen._normalise_client_report(
        {
            "executive_summary": "面向新能源企业的专业展。",
            "target_customers": ["展商负责人", "采购负责人", "渠道方", "专业观众", "第五项", "应被截断"],
            "customer_pain_points": [],
            "video_recommendation": {
                "recommended": False,
                "reason": "档案信息不足，建议先补素材。",
                "preferred_angle": "现场方案比较",
            },
            "content_brief": {"audience": "新能源企业决策者"},
        },
        "华南国际新能源产业展",
        "新能源产业",
    )

    assert report["executive_summary"] == "面向新能源企业的专业展。"
    assert len(report["target_customers"]) == 5
    assert report["customer_pain_points"]
    assert report["video_recommendation"]["recommended"] is False
    assert report["content_brief"]["audience"] == "新能源企业决策者"
    assert report["content_brief"]["pain"]
