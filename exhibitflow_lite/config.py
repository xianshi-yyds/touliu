from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    host: str = os.getenv("EXHIBITFLOW_HOST", "127.0.0.1").strip()
    port: int = int(os.getenv("EXHIBITFLOW_PORT", "8610") or 8610)
    storage_dir: Path = PROJECT_ROOT / "storage"
    vendor_dir: Path = PROJECT_ROOT / "vendor"
    crawler_dir: Path = project_path(
        os.getenv(
            "SOCIAL_CRAWLER_DIR",
            str(PROJECT_ROOT / "vendor" / "social_crawler"),
        )
    )
    moneyprinter_dir: Path = project_path(
        os.getenv("MONEYPRINTERTURBO_DIR", str(PROJECT_ROOT / "vendor" / "money_pipeline"))
    )
    sau_bin: Path = project_path(
        os.getenv(
            "SAU_BIN",
            str(PROJECT_ROOT / "vendor" / "social_auto_upload" / ".venv" / "bin" / "sau"),
        )
    )
    default_competitor_dir: Path = project_path(
        os.getenv("EXHIBITFLOW_COMPETITOR_DIR", str(PROJECT_ROOT / "storage" / "downloads"))
    )
    default_material_dir: Path = project_path(
        os.getenv("EXHIBITFLOW_MATERIAL_DIR", str(PROJECT_ROOT / "materials"))
    )
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    # Text generation is served by DeepSeek. Keep the legacy Qwen text field
    # for backwards-compatible config inspection, but text calls no longer
    # use it.
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_text_model: str = os.getenv("DEEPSEEK_TEXT_MODEL", "deepseek-chat").strip()
    qwen_text_model: str = os.getenv("QWEN_TEXT_MODEL", "qwen3.6-plus").strip()
    qwen_vl_model: str = os.getenv("QWEN_VL_MODEL", "qwen3-vl-plus").strip()
    qwen_tts_model: str = os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash").strip()
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "").strip()
    render_engine: str = os.getenv("EXHIBITFLOW_RENDER_ENGINE", "internal").strip().lower()
    dashscope_base_url: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip().rstrip("/")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/")
    dashscope_multimodal_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    oceanengine_app_id: str = os.getenv("OCEANENGINE_APP_ID", "").strip()
    oceanengine_secret: str = os.getenv("OCEANENGINE_SECRET", "").strip()
    oceanengine_redirect_uri: str = os.getenv("OCEANENGINE_REDIRECT_URI", "http://localhost:8610/api/oceanengine/callback").strip()
    oceanengine_access_token: str = os.getenv("OCEANENGINE_ACCESS_TOKEN", "").strip()
    oceanengine_refresh_token: str = os.getenv("OCEANENGINE_REFRESH_TOKEN", "").strip()
    oceanengine_advertiser_id: str = os.getenv("OCEANENGINE_ADVERTISER_ID", "").strip()


settings = Settings()
