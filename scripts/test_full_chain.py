from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import requests


BASE_URL = "http://127.0.0.1:8610"
CLASH_SOCKET = "/tmp/verge/verge-mihomo.sock"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def set_clash_mode(mode: str) -> None:
    subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "5",
            "--unix-socket",
            CLASH_SOCKET,
            "-X",
            "PATCH",
            "http://localhost/configs",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"mode": mode}),
        ],
        check=True,
        timeout=8,
    )


def create_task(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{BASE_URL}/api/tasks/{kind}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def wait_task(task: dict[str, Any], timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = requests.get(f"{BASE_URL}/api/tasks/{task['id']}", timeout=10)
        response.raise_for_status()
        current = response.json()
        if current.get("status") in {"succeeded", "failed"}:
            if current.get("status") == "failed":
                raise RuntimeError(f"{current.get('kind')} failed: {current.get('error')}")
            return current
        time.sleep(1)
    raise TimeoutError(f"task timed out: {task['id']}")


def main() -> None:
    summary: dict[str, Any] = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    output = PROJECT_ROOT / "storage" / "full-chain-test-result.json"
    failure: BaseException | None = None
    try:
        set_clash_mode("direct")
        summary["network"] = {"status": "succeeded", "temporary_clash_mode": "direct"}

        sample_task = wait_task(
            create_task(
                "import-links",
                {
                    "platform": "douyin",
                    "keyword": "展会完整链路实测",
                    "links": "https://www.douyin.com/video/1234567890",
                },
            ),
            30,
        )
        sample = sample_task["result"]["items"][0]
        summary["sample"] = {"status": "succeeded", "count": sample_task["result"]["count"]}

        copy_task = wait_task(
            create_task(
                "copy",
                {
                    "topic": "2026国际展会招商推广，突出专业买家、人流热度和展位稀缺",
                    "sample": sample,
                },
            ),
            120,
        )
        copy_text = copy_task["result"]["copy"]
        summary["copy"] = {"status": "succeeded", "characters": len(copy_text)}

        tts_task = wait_task(
            create_task("tts", {"text": copy_text, "voice": "Cherry"}),
            180,
        )
        audio_path = tts_task["result"]["audio_path"]
        summary["tts"] = {"status": "succeeded", "audio_path": audio_path}

        render_task = wait_task(
            create_task(
                "render",
                {
                    "keyword": "展会完整链路实测",
                    "material_dir": str(PROJECT_ROOT / "materials"),
                    "script": copy_text,
                    "audio_file": audio_path,
                },
            ),
            240,
        )
        render_summary = render_task["result"]["summary"]
        final_video = render_summary["final_video"]
        summary["render"] = {
            "status": "succeeded",
            "final_video": final_video,
            "preview_url": render_summary["preview_url"],
            "duration_seconds": render_summary.get("duration_seconds"),
        }

        delivery_task = wait_task(
            create_task(
                "delivery-draft",
                {
                    "advertiser_id": "test-advertiser-001",
                    "access_token": "test-token-not-real",
                    "landing_url": "https://example.com/exhibition",
                    "convert_id": "test-convert-001",
                    "budget": "300",
                    "subject": "2026国际展会招商推广",
                    "selling_points": "专业买家、人流热度、核心展位稀缺",
                    "video_file": final_video,
                    "goal": "线索收集",
                },
            ),
            30,
        )
        summary["delivery_draft"] = {
            "status": "succeeded",
            "ready": delivery_task["result"]["status"] == "ready",
            "checks": delivery_task["result"]["checks"],
        }
    except BaseException as exc:
        failure = exc
        summary["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        try:
            set_clash_mode("rule")
            summary["network_restore"] = {"status": "succeeded", "clash_mode": "rule"}
        except BaseException as restore_exc:
            summary["network_restore"] = {
                "status": "failed",
                "type": type(restore_exc).__name__,
                "message": str(restore_exc),
            }
            if failure is None:
                failure = restore_exc

        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"result_file={output}")

    if failure is not None:
        raise failure


if __name__ == "__main__":
    main()
