#!/usr/bin/env python3
"""Run a real, no-spend Ocean Engine chain and write a redacted audit report.

The script uses only official API objects. Project and promotion are always
created with operation=DISABLE, so a successful test cannot start spending.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import api_server  # noqa: E402


DOCS = {
    "project_create": "https://open.oceanengine.com/labels/7/docs/1740868093375503",
    "promotion_create": "https://open.oceanengine.com/labels/7/docs/1740946299496459",
    "orange_site": "https://open.oceanengine.com/labels/7/docs/1755162848410635",
    "optimized_goal": "https://open.oceanengine.com/labels/7/docs/1740944984250381",
    "aweme_auth": "https://open.oceanengine.com/docs/1729983667746823",
    "event_assets": "https://open.oceanengine.com/labels/7/docs/1800985709803914",
}


def ffmpeg_frame(video: Path, output: Path, vf: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1", "-i", str(video), "-frames:v", "1", "-vf", vf, str(output)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output


def query_orange_sites(token: str, advertiser_id: int) -> list[dict[str, Any]]:
    result = api_server.ocean_request(
        "/open_api/v3.0/tools/orange_site/get/",
        method="GET",
        access_token=token,
        params={
            "advertiser_id": advertiser_id,
            "page": 1,
            "page_size": 50,
            "status": "SITE_ONLINE",
            "optimize_goal": {"external_action": "AD_CONVERT_TYPE_FORM"},
        },
    )
    return (result.get("data") or {}).get("list") or []


def query_account_assets(token: str, advertiser_id: int) -> dict[str, int]:
    aweme = api_server.ocean_request(
        "/open_api/2/tools/aweme_auth_list/",
        method="GET",
        access_token=token,
        params={
            "advertiser_id": advertiser_id,
            "filtering": {"auth_status": ["AUTHRIZED"]},
            "page": 1,
            "page_size": 100,
        },
    )
    assets = api_server.ocean_request(
        "/open_api/2/tools/event/all_assets/list/",
        method="GET",
        access_token=token,
        params={"advertiser_id": advertiser_id, "page": 1, "page_size": 20},
    )
    return {
        "authorized_aweme_count": len((aweme.get("data") or {}).get("list") or []),
        "event_asset_count": len((assets.get("data") or {}).get("asset_list") or []),
    }


def create_disabled_project(advertiser_id: int, name: str = "") -> int:
    response = api_server.ocean_project_create({
        "official_json": {
            "advertiser_id": advertiser_id,
            "operation": "DISABLE",
            "delivery_mode": "PROCEDURAL",
            "landing_type": "LINK",
            "marketing_goal": "VIDEO_AND_IMAGE",
            "ad_type": "ALL",
            "delivery_type": "NORMAL",
            "name": (name.strip() or "ExhibitFlow展会视频项目")[:100] + "-" + time.strftime("%m%d-%H%M%S"),
            "asset_type": "ORANGE",
            "optimize_goal": {"external_action": "AD_CONVERT_TYPE_FORM"},
            "delivery_range": {"inventory_catalog": "UNIVERSAL_SMART"},
            "audience": {"district": "NONE"},
            "delivery_setting": {
                "schedule_type": "SCHEDULE_FROM_NOW",
                "bid_type": "CUSTOM",
                "deep_bid_type": "DEEP_BID_DEFAULT",
                "budget_mode": "BUDGET_MODE_DAY",
                "budget": 300,
                "pricing": "PRICING_OCPM",
                "cpa_bid": 10,
            },
        }
    })
    return int(response["project_id"])


def create_disabled_promotion(
    advertiser_id: int,
    project_id: int,
    video_id: str,
    video_cover_id: str,
    product_image_id: str,
    landing_url: str,
    name: str = "",
    title: str = "",
) -> int:
    response = api_server.ocean_promotion_create({
        "official_json": {
            "advertiser_id": advertiser_id,
            "project_id": project_id,
            "name": (name.strip() or "ExhibitFlow展会视频单元")[:100] + "-" + time.strftime("%m%d-%H%M%S"),
            "operation": "DISABLE",
            "promotion_materials": {
                "video_material_list": [{
                    "video_id": video_id,
                    "video_cover_id": video_cover_id,
                    "image_mode": "CREATIVE_IMAGE_MODE_VIDEO_VERTICAL",
                }],
                "title_material_list": [{"title": (title.strip() or "展会招商席位开放")[:30]}],
                "external_url_material_list": [landing_url],
                "product_info": {
                    "product_name_type": "CUSTOM",
                    "product_image_type": "CUSTOM",
                    "product_selling_point_type": "CUSTOM",
                    "titles": ["国际展会招商"],
                    "image_ids": [product_image_id],
                    "selling_points": ["专业买家精准对接"],
                },
                "call_to_action_buttons": ["立即咨询"],
            },
            "source": "展会招商",
            "is_comment_disable": "ON",
            "ad_download_status": "OFF",
        }
    })
    return int(response["promotion_id"])


def verify_disabled_objects(
    token: str, advertiser_id: int, project_id: int, promotion_id: int
) -> dict[str, str]:
    project_result = api_server.ocean_request(
        "/open_api/v3.0/project/list/",
        method="GET",
        access_token=token,
        params={"advertiser_id": advertiser_id, "page": 1, "page_size": 20},
    )
    promotion_result = api_server.ocean_request(
        "/open_api/v3.0/promotion/list/",
        method="GET",
        access_token=token,
        params={"advertiser_id": advertiser_id, "page": 1, "page_size": 20},
    )
    projects = (project_result.get("data") or {}).get("list") or []
    promotions = (promotion_result.get("data") or {}).get("list") or []
    project = next((item for item in projects if int(item.get("project_id") or 0) == project_id), {})
    promotion = next((item for item in promotions if int(item.get("promotion_id") or 0) == promotion_id), {})
    # `status` is the audit/delivery lifecycle and can legitimately be `AUDIT`
    # immediately after creation.  The operator switch is represented by
    # `opt_status` (and mirrored by `status_first`), which is the field that
    # guarantees this smoke test cannot start delivery or spend money.
    project_status = str(project.get("status") or "")
    promotion_status = str(promotion.get("status") or "")
    project_opt_status = str(project.get("opt_status") or project.get("status_first") or "")
    promotion_opt_status = str(promotion.get("opt_status") or promotion.get("status_first") or "")
    if "DISABLE" not in project_opt_status.upper() or "DISABLE" not in promotion_opt_status.upper():
        raise RuntimeError(
            "安全校验失败："
            f"project_opt_status={project_opt_status}, promotion_opt_status={promotion_opt_status}"
        )
    return {
        "project_status": project_status,
        "promotion_status": promotion_status,
        "project_opt_status": project_opt_status,
        "promotion_opt_status": promotion_opt_status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--video",
        default=str(ROOT / "storage/renders/saas-render-20260701-120859-cc0b83/final.mp4"),
    )
    parser.add_argument("--fresh", action="store_true", help="重新上传素材并创建新的关闭状态项目/推广")
    parser.add_argument("--video-id", default="", help="已上传的视频素材 ID；提供后不会重复上传视频")
    parser.add_argument("--project-name", default="", help="投放项目名称")
    parser.add_argument("--unit-name", default="", help="投放单元名称")
    parser.add_argument("--title", default="", help="单元标题文案")
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"成片不存在：{video}")

    oauth = api_server.load_ocean_token()
    token = str(oauth.get("access_token") or "")
    advertiser_id = int(oauth.get("advertiser_id") or 0)
    if not token or not advertiser_id:
        raise RuntimeError("请先完成巨量 OAuth 并解析出真实广告主 ID")

    stages: list[dict[str, Any]] = []
    account_assets = query_account_assets(token, advertiser_id)
    orange_sites = query_orange_sites(token, advertiser_id)
    stages.append({"stage": "account_assets", "status": "succeeded", **account_assets, "orange_site_count": len(orange_sites)})
    if not orange_sites:
        raise RuntimeError("没有审核通过且支持表单优化目标的橙子落地页")
    landing_url = str(orange_sites[0].get("url") or "")

    # An explicit ID always wins, including in --fresh mode.  This binds the
    # project/unit to the video selected by the caller instead of a process-
    # global last_video_id that may belong to another task.
    video_id = str(args.video_id or "").strip() or ("" if args.fresh else str(oauth.get("last_video_id") or ""))
    if not video_id:
        uploaded = api_server.ocean_video_upload({"advertiser_id": advertiser_id, "video_file": str(video), "is_aigc": True})
        video_id = str(uploaded.get("video_id") or "")
    stages.append({"stage": "video_upload", "status": "succeeded", "video_id": video_id})

    product_image_id = "" if args.fresh else str(oauth.get("last_image_id") or "")
    video_cover_id = "" if args.fresh else str(oauth.get("last_video_cover_id") or "")
    cover_dir = ROOT / "storage/oceanengine"
    if not product_image_id:
        square = ffmpeg_frame(video, cover_dir / "product-cover-108.jpg", "scale=108:108:force_original_aspect_ratio=increase,crop=108:108")
        product_image_id = str(api_server.ocean_image_upload({"image_file": str(square), "store_key": "last_image_id"}).get("id") or "")
    if not video_cover_id:
        portrait = ffmpeg_frame(video, cover_dir / "video-cover-1080x1920.jpg", "scale=1080:1920")
        video_cover_id = str(api_server.ocean_image_upload({"image_file": str(portrait), "store_key": "last_video_cover_id"}).get("id") or "")
    stages.append({
        "stage": "image_upload",
        "status": "succeeded",
        "product_image_id": product_image_id,
        "video_cover_id": video_cover_id,
    })

    project_id = 0 if args.fresh else int(oauth.get("last_project_id") or 0)
    if not project_id:
        project_id = create_disabled_project(advertiser_id, args.project_name)
    stages.append({"stage": "project_create", "status": "succeeded", "project_id": project_id, "operation": "DISABLE"})

    promotion_id = 0 if args.fresh else int(oauth.get("last_promotion_id") or 0)
    if not promotion_id:
        promotion_id = create_disabled_promotion(
            advertiser_id,
            project_id,
            video_id,
            video_cover_id,
            product_image_id,
            landing_url,
            args.unit_name,
            args.title,
        )
    stages.append({"stage": "promotion_create", "status": "succeeded", "promotion_id": promotion_id, "operation": "DISABLE"})

    verified = verify_disabled_objects(token, advertiser_id, project_id, promotion_id)
    stages.append({"stage": "safety_verification", "status": "succeeded", **verified})

    report = api_server.ocean_report({
        "advertiser_id": advertiser_id,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "end_date": datetime.now().strftime("%Y-%m-%d"),
        "dimensions": ["cdp_project_id", "cdp_promotion_id"],
    })
    stages.append({"stage": "report", "status": "succeeded", "row_count": report["row_count"]})

    result = {
        "ok": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "real_api_disabled_no_spend",
        "advertiser_id": advertiser_id,
        "video_file": str(video),
        "video_id": video_id,
        "landing_url": landing_url,
        "project_id": project_id,
        "promotion_id": promotion_id,
        "operation": "DISABLE",
        "spend_enabled": False,
        "dashboard": report["dashboard"],
        "stages": stages,
        "docs": DOCS,
        "report_manifest": report.get("_manifest_path"),
        "note": "真实素材、项目、推广和报表接口均已贯通；项目与推广保持关闭，因此报表为 0 且不会产生消耗。",
    }
    output = ROOT / "storage/oceanengine" / f"minimal-chain-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_file={output}")


if __name__ == "__main__":
    main()
