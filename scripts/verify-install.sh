#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "未找到 .venv，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi

PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv/bin/python" -m compileall -q \
  api_server.py app.py exhibitflow_lite frontend
PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv/bin/python" -c "import exhibitflow_lite; print('Python package import: OK')"

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg/ffprobe: OK"
else
  echo "ffmpeg/ffprobe: MISSING (仅影响视频合成)"
fi

echo "ExhibitFlow Lite 安装检查完成。"
