#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${FRONTEND_PORT:-5173}"
exec ./.venv/bin/python frontend/serve_frontend.py --port "$PORT"
