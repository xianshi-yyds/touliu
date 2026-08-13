from exhibitflow_lite.language import (
    default_cta,
    default_edge_voice,
    looks_chinese,
    normalize_output_language,
    resolve_edge_voice,
    script_usable_for_language,
)


def test_normalize_language_aliases():
    assert normalize_output_language("English") == "en"
    assert normalize_output_language("en-US") == "en"
    assert normalize_output_language("中文") == "zh"
    assert normalize_output_language("") == "zh"


def test_english_voice_and_cta():
    assert default_edge_voice("en") == "en-US-JennyNeural"
    assert "exhibition" in default_cta("en").lower()
    assert resolve_edge_voice("zh-CN-XiaoxiaoNeural", "en") == "en-US-JennyNeural"
    assert resolve_edge_voice("en-US-GuyNeural", "en") == "en-US-GuyNeural"


def test_script_language_gate():
    assert looks_chinese("如果你正在关注展会")
    assert not script_usable_for_language("如果你正在关注展会", "en")
    assert script_usable_for_language("If you are looking at the expo, start on site.", "en")
    assert script_usable_for_language("如果你正在关注展会", "zh")
