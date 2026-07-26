#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "未找到 .venv，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi

PYTHON="$ROOT_DIR/.venv/bin/python"
"$PYTHON" -m pip install --upgrade \
  "pycaps @ git+https://github.com/francozanardi/pycaps.git" \
  "playwright>=1.40"
"$PYTHON" -m playwright install chromium

echo
echo "PyCaps Python 依赖和 Chromium 已安装。"
echo "Linux 服务器如果启动时报 libxcb.so.1 等动态库缺失，请按发行版补齐 Playwright 的系统依赖。"
echo "Amazon Linux 2023 示例：sudo dnf install -y libxcb libX11 libXcomposite libXdamage libXext libXfixes libXrandr libgbm libdrm pango cairo atk at-spi2-atk cups-libs gtk3 nss alsa-lib"
