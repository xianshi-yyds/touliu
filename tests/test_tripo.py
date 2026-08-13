from dataclasses import replace
from pathlib import Path

from exhibitflow_lite import tripo


def test_public_status_without_key(monkeypatch):
    monkeypatch.setattr(tripo, "settings", replace(tripo.settings, tripo_api_key=""))
    status = tripo.public_status()
    assert status["configured"] is False
    assert status["model_version"]


def test_create_local_image_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(tripo, "settings", replace(tripo.settings, storage_dir=tmp_path, project_root=tmp_path))
    record = tripo.create_local_image("expo-a", "part.png", b"fakepng")
    assert record["id"]
    assert record["status"] == "uploaded"
    assert Path(tmp_path / "tripo_models" / record["id"] / "front.png").is_file()
    items = tripo.list_records("expo-a")
    assert len(items) == 1
    assert items[0]["id"] == record["id"]
    assert tripo.list_records("other") == []


def test_normalize_and_output_urls():
    assert tripo._normalize_status("running") == "processing"
    assert tripo._normalize_status("success") == "success"
    urls = tripo._output_urls({"output": {"pbr_model": "https://x/model.glb", "rendered_image": "https://x/p.png"}})
    assert urls["model_url"].endswith("model.glb")
    assert urls["rendered_image_url"].endswith("p.png")


def test_multiview_requires_three_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(tripo, "settings", replace(tripo.settings, storage_dir=tmp_path, project_root=tmp_path))
    first = tripo.create_local_image("expo-a", "front.png", b"front", input_mode="multiview")
    tripo.create_local_image("expo-a", "left.png", b"left", record_id=first["id"], slot="left", input_mode="multiview")
    try:
        tripo.start_generation(first["id"])
        raise AssertionError("expected missing right view")
    except ValueError as exc:
        assert "右侧面" in str(exc)
