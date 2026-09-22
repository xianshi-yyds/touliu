"""Public capability registry for the multi-platform delivery workspace."""

from __future__ import annotations

from typing import Any

from . import rnote, tikhub, tencent_ads
from .config import settings


NEW_RANK_URL = "https://xs.newrank.cn/home"


def public_capabilities(ocean_status: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ocean = ocean_status or {}
    xhs_search_provider = "rnote" if rnote.configured() else ("tikhub" if tikhub.configured() else "local")
    tencent_status = tencent_ads.public_status()
    return [
        {
            "id": "douyin",
            "label": "抖音",
            "search": {
                "provider": "tikhub" if tikhub.configured() else "target_host",
                "status": "ready" if tikhub.configured() or (settings.crawler_dir / "crawl_douyin.py").exists() else "needs_setup",
                "login_required": not tikhub.configured(),
            },
            "delivery": {
                "provider": "oceanengine",
                "status": "bound" if ocean.get("binding_verified") else "needs_oauth",
                "mode": "official_marketing_api",
            },
            "analytics": {"provider": "oceanengine", "status": "available_with_account"},
        },
        {
            "id": "xiaohongshu",
            "label": "小红书",
            "search": {
                "provider": xhs_search_provider,
                "status": "ready" if rnote.configured() or tikhub.configured() or (settings.crawler_dir / "crawl_xiaohongshu.py").exists() else "needs_setup",
                "login_required": not (rnote.configured() or tikhub.configured()),
            },
            "delivery": {
                "provider": "xiaohongshu_marketing_api",
                "status": "needs_official_permissions",
                "mode": "official_api_or_manual_review",
            },
            "analytics": {"provider": "newrank", "status": "external_console", "url": NEW_RANK_URL},
        },
        {
            "id": "weixin_channels",
            "label": "视频号",
            "search": {
                "provider": "not_supported",
                "status": "manual_or_import",
                "login_required": False,
            },
            "delivery": {
                "provider": "tencent_ads",
                "status": "ready" if tencent_status["ready"] else "needs_oauth",
                "mode": "official_marketing_api",
                "creative_read": tencent_status["configured"],
            },
            "analytics": {"provider": "newrank_or_tencent", "status": "external_or_account_api", "url": NEW_RANK_URL},
        },
    ]
