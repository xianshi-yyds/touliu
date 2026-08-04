"""Copy the existing ExhibitFlow OpenAI-compatible model settings to Hermes.

This helper is intended to run on the deployment host as ``ec2-user``.  It
never prints or writes credentials to stdout; the Hermes env file is kept at
mode 0600 and only receives the model endpoint and the existing DashScope
compatible key.
"""

from __future__ import annotations

import os
from pathlib import Path


SOURCE_ENV = Path("/opt/exhibitflow-lite/.env")
HERMES_ENV = Path.home() / ".hermes" / ".env"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_env(path) if path.exists() else {}
    existing.update(updates)
    lines = [f"{key}={value}" for key, value in existing.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> None:
    source = read_env(SOURCE_ENV)
    api_key = source.get("TEXT_LLM_API_KEY") or source.get("DASHSCOPE_API_KEY") or source.get("DEEPSEEK_API_KEY")
    base_url = source.get("TEXT_LLM_BASE_URL") or source.get("DASHSCOPE_BASE_URL") or source.get("DEEPSEEK_BASE_URL")
    model = source.get("TEXT_LLM_MODEL") or source.get("QWEN_TEXT_MODEL") or "qwen3.7-plus"
    if not api_key or not base_url:
        raise SystemExit("ExhibitFlow text model credentials are incomplete")
    write_env(
        HERMES_ENV,
        {
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": base_url,
            "HERMES_EXHIBITFLOW_MODEL": model,
        },
    )
    print(f"configured model={model} key_present=true env_mode={oct(HERMES_ENV.stat().st_mode & 0o777)}")


if __name__ == "__main__":
    main()
