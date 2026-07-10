#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p storage/logs

./.venv/bin/python api_server.py > storage/logs/api-server.log 2>&1 &
API_PID=$!
./.venv/bin/python frontend/serve_frontend.py --port "${FRONTEND_PORT:-5173}" > storage/logs/frontend-server.log 2>&1 &
FRONTEND_PID=$!

cleanup() {
  kill "$API_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "ExhibitFlow backend: http://127.0.0.1:${EXHIBITFLOW_PORT:-8501}"
echo "ExhibitFlow frontend: http://127.0.0.1:${FRONTEND_PORT:-5173}/?api=http://127.0.0.1:${EXHIBITFLOW_PORT:-8501}"
wait
