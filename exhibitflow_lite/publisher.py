from __future__ import annotations

import subprocess
from pathlib import Path

from .config import settings


def sau_available() -> bool:
    return settings.sau_bin.exists()


def run_sau(args: list[str]) -> str:
    if not sau_available():
        return (
            "发布插件未配置。\n"
            f"当前查找路径：{settings.sau_bin}\n"
            "如果需要真实发布，请把 social-auto-upload 放到 vendor/social_auto_upload，"
            "或在 .env 里设置 SAU_BIN。"
        )
    result = subprocess.run(
        [str(settings.sau_bin), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip())
    return result.stdout


def check_account(platform: str, account: str = "creator") -> str:
    return run_sau([platform, "check", "--account", account])


def upload_video(platform: str, video_file: str, title: str, desc: str = "", account: str = "creator") -> str:
    path = Path(video_file).expanduser()
    if not path.exists():
        raise FileNotFoundError(video_file)
    args = [platform, "upload-video", "--account", account, "--file", str(path), "--title", title]
    if desc:
        args.extend(["--desc", desc])
    return run_sau(args)
