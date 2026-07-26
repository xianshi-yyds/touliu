from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from exhibitflow_lite import rnote


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_normalize_note_to_gallery_item() -> None:
    item = rnote.normalize_item(
        {
            "note_id": "abc123",
            "title": "新能源展现场",
            "desc": "展位和采购商内容",
            "user": {"nickname": "作者"},
            "interact_info": {"liked_count": 12, "comment_count": 3, "share_count": 4},
            "cover": {"url": "https://img.example/cover.jpg"},
            "video": {"url": "https://img.example/video.mp4"},
        }
    )
    assert item["platform"] == "xiaohongshu"
    assert item["note_id"] == "abc123"
    assert item["url"].endswith("/abc123")
    assert item["author_name"] == "作者"
    assert item["cover_url"].endswith("cover.jpg")
    assert item["digg_count"] == 12


@patch("exhibitflow_lite.rnote.requests.get")
def test_search_paginates_and_keeps_public_provider_metadata(request_get) -> None:
    request_get.side_effect = [
        FakeResponse({"success": True, "data": {"notes": [{"note_id": "1", "title": "一"}]}}),
        FakeResponse({"success": True, "data": {"notes": [{"note_id": "2", "title": "二"}]}}),
    ]
    fake_settings = SimpleNamespace(
        rnote_api_key="test-key",
        rnote_base_url="https://rnote.test/api/v2/crawler",
    )
    with patch.object(rnote, "settings", fake_settings):
        result = rnote.search("新能源展", 25)

    assert [item["note_id"] for item in result["items"]] == ["1", "2"]
    assert result["provider"] == "rnote"
    assert result["pages"] == 2
    assert result["estimated_cost_usd"] == 0.02
    assert request_get.call_count == 2
    assert request_get.call_args_list[0].kwargs["headers"]["X-API-Key"] == "test-key"
    assert request_get.call_args_list[0].kwargs["params"]["note_type"] == 1
