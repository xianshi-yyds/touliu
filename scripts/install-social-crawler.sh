#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi

"$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/vendor/social_crawler/requirements.txt"
echo "社交平台抓取依赖安装完成。"
echo "注意：真实抖音/小红书登录抓取还需要 macOS、Chrome 登录态和 osascript；服务器 Linux 不能直接复用该浏览器链路。"
