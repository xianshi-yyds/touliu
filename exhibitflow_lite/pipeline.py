from __future__ import annotations

import subprocess
from pathlib import Path

from .config import settings
from .render import render_lite_video


def pipeline_python() -> str:
    candidate = settings.moneyprinter_dir / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else "python"


def render_video(
    keyword: str,
    competitor_dir: str,
    material_dir: str,
    name: str = "lite-render",
    tts_mode: str = "silent",
    script_mode: str = "formula",
    script: str = "",
    audio_file: str = "",
    caption_template: str = "viral",
    caption_animation: str = "pop",
    highlight_words: list[str] | None = None,
    cta_text: str = "",
    sentence_material_dirs: list[str] | None = None,
    render_captions: bool = True,
    material_files: list[str] | None = None,
) -> str:
    if settings.render_engine != "moneyprinter":
        manifest = render_lite_video(
            keyword=keyword,
            script=script or keyword,
            material_dir=material_dir,
            audio_file=audio_file,
            name=name,
            caption_template=caption_template,
            caption_animation=caption_animation,
            highlight_words=highlight_words,
            cta_text=cta_text,
            sentence_material_dirs=sentence_material_dirs,
            render_captions=render_captions,
            material_files=material_files,
        )
        return "__SUMMARY_JSON__" + __import__("json").dumps(manifest, ensure_ascii=False)

    if not settings.moneyprinter_dir.exists():
        raise RuntimeError(f"MoneyPrinterTurbo dir not found: {settings.moneyprinter_dir}")
    competitor = Path(competitor_dir).expanduser()
    material = Path(material_dir).expanduser()
    if not competitor.exists():
        raise FileNotFoundError(f"competitor dir not found: {competitor}")
    if not material.exists():
        raise FileNotFoundError(f"material dir not found: {material}")

    cmd = [
        pipeline_python(),
        "scripts/run_exhibition_real_pipeline.py",
        "--keyword",
        keyword,
        "--subject",
        keyword,
        "--competitor-dir",
        str(competitor),
        "--material-dir",
        str(material),
        "--material-source",
        "tagged",
        "--competitor-analysis",
        "metadata",
        "--script-mode",
        script_mode,
        "--tts-mode",
        tts_mode,
        "--name",
        name,
    ]
    result = subprocess.run(
        cmd,
        cwd=str(settings.moneyprinter_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip())
    return result.stdout
