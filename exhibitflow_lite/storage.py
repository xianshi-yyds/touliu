from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings


def safe_stem(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(value)).strip("-")
    return safe or "untitled"


def ensure_storage() -> None:
    for name in ("manifests", "downloads", "tts", "qwen", "renders"):
        (settings.storage_dir / name).mkdir(parents=True, exist_ok=True)
    settings.default_material_dir.mkdir(parents=True, exist_ok=True)


def manifest_path(action: str, platform: str, keyword: str) -> Path:
    ensure_storage()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return settings.storage_dir / "manifests" / f"{stamp}-{action}-{platform}-{safe_stem(keyword)}.json"


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_manifest(action: str = "", platform: str = "", keyword: str = "") -> dict[str, Any]:
    ensure_storage()
    actions = {item.strip() for item in action.split(",") if item.strip()}
    candidates = sorted(
        (settings.storage_dir / "manifests").glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = read_json(path)
        except Exception:
            continue
        if actions and data.get("action") not in actions:
            continue
        if platform and data.get("platform") != platform:
            continue
        if keyword and keyword not in str(data.get("query", "")) and keyword not in path.name:
            continue
        data["_manifest_path"] = str(path)
        return data
    return {}
