from __future__ import annotations

import unittest
from unittest.mock import patch

from exhibitflow_lite import tikhub


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class TikHubAdapterTest(unittest.TestCase):
    def test_normalize_nested_search_item(self) -> None:
        item = tikhub.normalize_item(
            {
                "data": {
                    "aweme_info": {
                        "aweme_id": "123",
                        "desc": "新能源展现场",
                        "author": {"nickname": "作者"},
                        "statistics": {"digg_count": 10, "comment_count": 2, "share_count": 3},
                        "video": {
                            "duration": 12000,
                            "cover": {"url_list": ["https://cover.example/a.jpg"]},
                            "play_addr": {"url_list": ["https://video.example/a.mp4"]},
                        },
                    }
                }
            }
        )
        self.assertEqual(item["aweme_id"], "123")
        self.assertEqual(item["title"], "新能源展现场")
        self.assertEqual(item["author_name"], "作者")
        self.assertEqual(item["duration_seconds"], 12.0)
        self.assertEqual(item["cover_url"], "https://cover.example/a.jpg")
        self.assertEqual(item["digg_count"], 10)

    @patch("exhibitflow_lite.tikhub._request")
    def test_search_paginates_until_limit(self, request) -> None:
        request.side_effect = [
            {
                "code": 200,
                "data": [{"aweme_info": {"aweme_id": "1", "desc": "一"}}],
                "cursor": 1,
                "search_id": "sid",
                "backtrace": "bt",
                "has_more": True,
            },
            {
                "code": 200,
                "data": [{"aweme_info": {"aweme_id": "2", "desc": "二"}}],
                "cursor": 2,
                "search_id": "sid",
                "backtrace": "bt",
                "has_more": False,
            },
        ]
        with patch("exhibitflow_lite.tikhub.configured", return_value=True):
            result = tikhub.search("新能源", 9)
        self.assertEqual([item["aweme_id"] for item in result["items"]], ["1", "2"])
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["estimated_cost_usd"], 0.02)
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
