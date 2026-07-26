from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from exhibitflow_lite import tencent_ads


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@patch("exhibitflow_lite.tencent_ads.requests.get")
def test_get_creatives_builds_read_only_request(request_get) -> None:
    request_get.return_value = FakeResponse(
        {"code": 0, "data": {"list": [{"adcreative_id": "c-1", "name": "展会视频"}], "page_info": {"page": 1}}}
    )
    fake_settings = SimpleNamespace(
        tencent_ads_access_token="token",
        tencent_ads_refresh_token="",
        tencent_ads_account_id="acct-1",
        tencent_ads_base_url="https://api.e.qq.com",
    )
    with patch.object(tencent_ads, "settings", fake_settings):
        result = tencent_ads.get_creatives(
            {"field": "adcreative_id", "operator": "IN", "values": ["c-1"]}
        )

    assert result["platform"] == "weixin_channels"
    assert result["items"][0]["adcreative_id"] == "c-1"
    call = request_get.call_args
    assert call.args[0] == "https://api.e.qq.com/v1.3/adcreatives/get"
    assert call.kwargs["params"]["account_id"] == "acct-1"
    assert json.loads(call.kwargs["params"]["filtering"])["field"] == "adcreative_id"
    assert call.kwargs["headers"]["access_token"] == "token"
