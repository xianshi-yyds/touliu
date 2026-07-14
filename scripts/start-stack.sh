#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Keep this launcher and config.py on the same API port. Values in .env are
# loaded for the child processes, while explicit shell variables still win.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

API_PORT="${EXHIBITFLOW_PORT:-8610}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
mkdir -p "${EXHIBITFLOW_STORAGE:-storage}/logs"
LOG_DIR="${EXHIBITFLOW_STORAGE:-storage}/logs"

EXHIBITFLOW_PORT="$API_PORT" ./.venv/bin/python api_server.py > "$LOG_DIR/api-server.log" 2>&1 &
API_PID=$!
./.venv/bin/python frontend/serve_frontend.py --port "$FRONTEND_PORT" > "$LOG_DIR/frontend-server.log" 2>&1 &
FRONTEND_PID=$!

cleanup() {
  kill "$API_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "ExhibitFlow backend: http://127.0.0.1:${API_PORT}"
echo "ExhibitFlow frontend: http://127.0.0.1:${FRONTEND_PORT}/?api=http://127.0.0.1:${API_PORT}"
wait
