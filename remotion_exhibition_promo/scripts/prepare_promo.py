from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOTION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from exhibitflow_lite import stock, tts  # noqa: E402
from exhibitflow_lite.config import settings  # noqa: E402


FPS = 30
VOICEOVER_NAME = "exhibition-promo-voiceover"

SCENES = [
    {
        "role": "venue",
        "kicker": "2026 国际新能源产业展",
        "title": "把一次到场，变成一次高质量连接",
        "text": "如果你正在为新能源业务寻找下一次增长，真正有价值的，不只是一次曝光。",
    },
    {
        "role": "industry",
        "kicker": "看见产业趋势",
        "title": "集中看见产品、技术与应用",
        "text": "在展会现场，你可以集中了解产品、技术和应用方案，快速建立对行业趋势的判断。",
    },
    {
        "role": "business",
        "kicker": "面对面交流",
        "title": "让比较和沟通发生在现场",
        "text": "从产品展示到方案交流，从需求沟通到合作探讨，让每一次比较都更具体，让每一次沟通都更高效。",
    },
    {
        "role": "atmosphere",
        "kicker": "连接产业伙伴",
        "title": "找到值得继续聊的人",
        "text": "品牌方、专业买家与产业伙伴，在同一个现场交换信息，寻找下一步可以共同推进的机会。",
    },
    {
        "role": "venue",
        "kicker": "立即了解展会信息",
        "title": "为下一次增长，提前到场",
        "text": "新能源产业展，聚焦产业交流与合作机会。现在了解展会信息，预约你的参观计划。",
    },
]

VISUAL_PLAN = {
    "groups": [
        {
            "role": "venue",
            "ratio": 0.25,
            "terms": ["trade show exhibition hall", "expo crowd exhibition booth"],
        },
        {
            "role": "industry",
            "ratio": 0.25,
            "terms": ["solar panels technology", "electric vehicle charging", "renewable energy equipment"],
        },
        {
            "role": "business",
            "ratio": 0.25,
            "terms": ["business meeting trade show", "business networking expo", "professional handshake meeting"],
        },
        {
            "role": "atmosphere",
            "ratio": 0.25,
            "terms": ["technology conference audience", "industrial innovation", "city lights technology"],
        },
    ]
}


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return float(result.stdout.strip() or 0)


def split_caption_text(text: str, max_chars: int = 14) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in text.strip():
        current += char
        if char in "，。！？；,!?.;" or len(current) >= max_chars:
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())
    return [part for part in parts if part]


def build_captions(scene_ranges: list[dict[str, float]]) -> list[dict[str, object]]:
    captions: list[dict[str, object]] = []
    for scene, timing in zip(SCENES, scene_ranges):
        chunks = split_caption_text(scene["text"])
        weights = [max(1, len(chunk)) for chunk in chunks]
        total_weight = sum(weights) or 1
        cursor = timing["start"]
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            end = timing["end"] if index == len(chunks) - 1 else cursor + (timing["end"] - timing["start"]) * weight / total_weight
            captions.append(
                {
                    "text": chunk,
                    "startMs": round(cursor * 1000),
                    "endMs": round(max(cursor + 0.25, end) * 1000),
                    "timestampMs": None,
                    "confidence": None,
                }
            )
            cursor = end
    return captions


def main() -> None:
    public_dir = REMOTION_ROOT / "public"
    asset_dir = public_dir / "assets" / "network"
    voice_dir = public_dir / "voiceover"
    asset_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)

    # This deliberately calls the project's existing provider adapter. It
    # searches Pexels, filters for portrait-capable clips, deduplicates URLs,
    # applies per-role quotas, and downloads only the clips needed by the edit.
    network_dir = settings.storage_dir / "online_materials" / "remotion-exhibition-promo"
    downloaded = stock.download_moneyprinter_visual_plan(
        VISUAL_PLAN,
        network_dir,
        target_duration=30.0,
        source="pexels",
        max_clip_duration=6,
    )

    role_files: dict[str, list[str]] = {}
    copied_files: list[dict[str, object]] = []
    for group in downloaded.get("groups") or []:
        role = str(group.get("role") or "scene")
        role_files.setdefault(role, [])
        for raw_path in group.get("files") or []:
            source = Path(str(raw_path))
            if not source.is_file():
                continue
            target = asset_dir / role / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copy2(source, target)
            relative = target.relative_to(public_dir).as_posix()
            role_files[role].append(relative)
            copied_files.append({"role": role, "file": relative, "source": str(source), "provider": "pexels"})

    all_assets = [item["file"] for item in copied_files]
    if not all_assets:
        raise RuntimeError("网络素材下载成功但没有可供 Remotion 使用的文件")

    full_text = "".join(scene["text"] for scene in SCENES)
    audio_path, used_service, used_voice, errors = tts.synthesize_resilient(
        full_text,
        service="qwen",
        voice=settings.qwen_tts_voice,
        output_name=f"{VOICEOVER_NAME}.mp3",
    )
    audio_target = voice_dir / f"{VOICEOVER_NAME}{audio_path.suffix}"
    shutil.copy2(audio_path, audio_target)
    audio_duration = probe_duration(audio_target)

    scene_weights = [max(1, len(scene["text"])) for scene in SCENES]
    total_weight = sum(scene_weights)
    scene_ranges: list[dict[str, float]] = []
    cursor = 0.0
    for index, weight in enumerate(scene_weights):
        end = audio_duration if index == len(SCENES) - 1 else cursor + audio_duration * weight / total_weight
        scene_ranges.append({"start": round(cursor, 4), "end": round(max(cursor + 1.0, end), 4)})
        cursor = end

    scenes: list[dict[str, object]] = []
    for index, (scene, timing) in enumerate(zip(SCENES, scene_ranges)):
        assets = role_files.get(scene["role"]) or all_assets
        scenes.append({
            "id": index + 1,
            **scene,
            **timing,
            "assets": assets,
        })

    data = {
        "title": "2026 国际新能源产业展",
        "fps": FPS,
        "width": 1080,
        "height": 1920,
        "durationSeconds": round(audio_duration + 0.5, 3),
        "audio": {
            "file": audio_target.relative_to(public_dir).as_posix(),
            "provider": used_service,
            "voice": used_voice,
            "fallbackErrors": errors,
        },
        "scenes": scenes,
        "captions": build_captions(scene_ranges),
        "networkSearch": {
            "provider": downloaded.get("source"),
            "terms": downloaded.get("search_terms") or [],
            "strategy": downloaded.get("strategy") or "moneyprinter_exhibition_visual_plan",
            "downloadedDuration": downloaded.get("duration"),
            "files": copied_files,
            "groups": downloaded.get("groups") or [],
        },
    }

    (public_dir / "promo-manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REMOTION_ROOT / "src" / "promo-data.ts").write_text(
        "// Generated by scripts/prepare_promo.py.\nexport const promoData = "
        + json.dumps(data, ensure_ascii=False, indent=2)
        + " as const;\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "manifest": str(public_dir / "promo-manifest.json"),
        "audio": str(audio_target),
        "audio_duration": audio_duration,
        "asset_count": len(copied_files),
        "tts_provider": used_service,
        "search_provider": downloaded.get("source"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
