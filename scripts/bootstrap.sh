#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "未找到 Python 3.10+，请先安装 Python。" >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("ExhibitFlow Lite 需要 Python 3.10 或更高版本。")
print(f"Python {sys.version.split()[0]} OK")
PY

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "创建虚拟环境：$ROOT_DIR/.venv"
  "$PYTHON" -m venv "$ROOT_DIR/.venv"
fi

PYTHON_VENV="$ROOT_DIR/.venv/bin/python"
"$PYTHON_VENV" -m pip install --upgrade pip setuptools wheel
"$PYTHON_VENV" -m pip install .

mkdir -p \
  "$ROOT_DIR/storage/logs" \
  "$ROOT_DIR/storage/api_tasks" \
  "$ROOT_DIR/storage/tasks" \
  "$ROOT_DIR/storage/pipeline_runs" \
  "$ROOT_DIR/storage/renders" \
  "$ROOT_DIR/storage/online_materials" \
  "$ROOT_DIR/storage/downloads"

if [ ! -f "$ROOT_DIR/.env" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "已创建 .env，请按需填写 API Key；未填写时仍可使用本地素材和内部渲染链路。"
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "提示：未检测到 ffmpeg/ffprobe，内部视频合成无法运行。"
  echo "macOS: brew install ffmpeg"
  echo "Ubuntu/Debian: sudo apt-get update && sudo apt-get install -y ffmpeg"
else
  echo "ffmpeg: $(ffmpeg -version | head -n 1)"
fi

echo
echo "安装完成。"
echo "启动完整本地栈：./scripts/start-stack.sh"
echo "仅启动 Streamlit 兼容页：./scripts/start.sh"
echo "可选社交平台抓取依赖：./scripts/install-social-crawler.sh"
