from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import api_server


def test_resolve_ocean_video_uses_completed_creator_task(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    task_id = "creator-task-for-ocean-upload"
    monkeypatch.setattr(api_server, "project_file", lambda value, label: Path(value).resolve())
    with api_server.TASK_LOCK:
        api_server.TASKS[task_id] = {
            "id": task_id,
            "kind": "creator-pipeline",
            "status": "succeeded",
            "result": {"summary": {"final_video": str(video)}},
        }
    try:
        resolved, source_task_id = api_server.resolve_ocean_video_file({
            "source_task_id": task_id,
            "video_file": "/etc/passwd",
        })
    finally:
        with api_server.TASK_LOCK:
            api_server.TASKS.pop(task_id, None)
    assert resolved == video.resolve()
    assert source_task_id == task_id


def test_ocean_video_upload_streams_file_and_returns_video_id(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "finished.mp4"
    video.write_bytes(b"small-video-body")
    upload_storage = tmp_path / "storage"
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{"code":0}'

        @staticmethod
        def json() -> dict[str, object]:
            return {"code": 0, "data": {"video_id": "vid-test-123", "material_id": "mat-test-456"}}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["data"] = kwargs["data"]
        files = kwargs["files"]
        captured["file_name"] = files["video_file"][0]
        captured["file_body"] = files["video_file"][1].read()
        return FakeResponse()

    monkeypatch.setattr(api_server, "resolve_ocean_video_file", lambda payload: (video, "creator-1"))
    monkeypatch.setattr(api_server.requests, "post", fake_post)
    monkeypatch.setattr(api_server, "save_ocean_token", lambda data: data)
    monkeypatch.setattr(api_server, "settings", replace(api_server.settings, storage_dir=upload_storage))

    result = api_server.ocean_video_upload({
        "access_token": "test-token",
        "advertiser_id": "123456",
        "is_aigc": True,
    })

    assert result["video_id"] == "vid-test-123"
    assert result["material_id"] == "mat-test-456"
    assert result["source_task_id"] == "creator-1"
    assert captured["url"] == "https://api.oceanengine.com/open_api/2/file/video/ad/"
    assert captured["headers"] == {"Access-Token": "test-token"}
    assert captured["data"]["video_signature"] == api_server.file_md5(video)
    assert captured["file_body"] == b"small-video-body"
    assert list((upload_storage / "oceanengine" / "uploads").glob("*.json"))
