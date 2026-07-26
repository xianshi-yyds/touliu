from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def first_env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable in ``names``.

    The provider settings used to be split between DeepSeek and Qwen names.
    Keeping a small compatibility resolver lets existing local ``.env`` files
    continue to work while the text model moves to the Qwen OpenAI-compatible
    endpoint.
    """
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default.strip()


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
    # Text generation now uses the Qwen OpenAI-compatible endpoint. The
    # legacy DeepSeek fields remain available for old integrations and status
    # pages, but application text calls use the ``text_llm_*`` fields below.
    text_llm_api_key: str = first_env(
        "TEXT_LLM_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
    )
    text_llm_model: str = first_env(
        "TEXT_LLM_MODEL",
        "QWEN_TEXT_MODEL",
        "DEEPSEEK_TEXT_MODEL",
        default="qwen3.6-plus",
    )
    text_llm_enable_thinking: bool = os.getenv("TEXT_LLM_ENABLE_THINKING", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_text_model: str = os.getenv("DEEPSEEK_TEXT_MODEL", "deepseek-chat").strip()
    qwen_text_model: str = os.getenv("QWEN_TEXT_MODEL", "qwen3.6-plus").strip()
    qwen_vl_model: str = os.getenv("QWEN_VL_MODEL", "qwen3-vl-plus").strip()
    qwen_tts_model: str = os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash").strip()
    qwen_tts_voice: str = os.getenv("QWEN_TTS_VOICE", "Serena").strip() or "Serena"
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "").strip()
    # TikHub provides a server-side, login-free public Douyin search API.  It
    # is optional so the existing local browser crawler remains available for
    # installations that do not want a managed API provider.
    tikhub_api_key: str = os.getenv("TIKHUB_API_KEY", "").strip()
    tikhub_base_url: str = os.getenv("TIKHUB_BASE_URL", "https://api.tikhub.dev").strip().rstrip("/")
    # Rnote is the managed, login-free public Xiaohongshu/RedNote search
    # adapter.  It is optional; local browser search remains the fallback.
    rnote_api_key: str = os.getenv("RNOTE_API_KEY", "").strip()
    rnote_base_url: str = os.getenv("RNOTE_BASE_URL", "https://rnote.dev/api/v2/crawler").strip().rstrip("/")
    social_search_provider: str = os.getenv("SOCIAL_SEARCH_PROVIDER", "auto").strip().lower() or "auto"
    render_engine: str = os.getenv("EXHIBITFLOW_RENDER_ENGINE", "internal").strip().lower()
    dashscope_base_url: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip().rstrip("/")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/")
    text_llm_base_url: str = first_env(
        "TEXT_LLM_BASE_URL",
        "DASHSCOPE_BASE_URL",
        "DEEPSEEK_BASE_URL",
        default="https://ws-7s8hh8dksjtq6j2u.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    dashscope_multimodal_url: str = os.getenv(
        "DASHSCOPE_MULTIMODAL_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    ).strip().rstrip("/")
    oceanengine_app_id: str = os.getenv("OCEANENGINE_APP_ID", "").strip()
    oceanengine_secret: str = os.getenv("OCEANENGINE_SECRET", "").strip()
    oceanengine_redirect_uri: str = os.getenv("OCEANENGINE_REDIRECT_URI", "http://localhost:8610/api/oceanengine/callback").strip()
    oceanengine_access_token: str = os.getenv("OCEANENGINE_ACCESS_TOKEN", "").strip()
    oceanengine_refresh_token: str = os.getenv("OCEANENGINE_REFRESH_TOKEN", "").strip()
    oceanengine_advertiser_id: str = os.getenv("OCEANENGINE_ADVERTISER_ID", "").strip()
    # Tencent Marketing API is used for the Video Channels paid-delivery
    # adapter.  The first release only reads the creative library.
    tencent_ads_base_url: str = os.getenv("TENCENT_ADS_BASE_URL", "https://api.e.qq.com").strip().rstrip("/")
    tencent_ads_access_token: str = os.getenv("TENCENT_ADS_ACCESS_TOKEN", "").strip()
    tencent_ads_refresh_token: str = os.getenv("TENCENT_ADS_REFRESH_TOKEN", "").strip()
    tencent_ads_client_id: str = os.getenv("TENCENT_ADS_CLIENT_ID", "").strip()
    tencent_ads_client_secret: str = os.getenv("TENCENT_ADS_CLIENT_SECRET", "").strip()
    tencent_ads_account_id: str = os.getenv("TENCENT_ADS_ACCOUNT_ID", "").strip()
    # Digital-human / AI-video supplement ("AI 自动补充"). Optional third-party
    # avatar providers; the feature stays dormant (UI greyed "即将上线") until a
    # provider and its credentials are supplied here.
    avatar_provider: str = os.getenv("AVATAR_PROVIDER", "").strip().lower()
    heygen_api_key: str = os.getenv("HEYGEN_API_KEY", "").strip()
    heygen_base_url: str = os.getenv("HEYGEN_BASE_URL", "https://api.heygen.com").strip().rstrip("/")
    heygen_avatar_id: str = os.getenv("HEYGEN_AVATAR_ID", "").strip()
    heygen_voice_id: str = os.getenv("HEYGEN_VOICE_ID", "").strip()
    volc_avatar_base_url: str = os.getenv("VOLC_AVATAR_BASE_URL", "").strip().rstrip("/")
    volc_avatar_token: str = os.getenv("VOLC_AVATAR_TOKEN", "").strip()
    volc_avatar_id: str = os.getenv("VOLC_AVATAR_ID", "").strip()


settings = Settings()
