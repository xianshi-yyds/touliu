#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Keep the temporary public demo tunnel alive. The SSH keepalive detects a
# broken connection; this outer loop reconnects it after VPN/network changes.
REMOTE_HOST="${EXHIBITFLOW_TUNNEL_HOST:-xianshi.icu}"
REMOTE_USER="${EXHIBITFLOW_TUNNEL_USER:-root}"
REMOTE_PORT="${EXHIBITFLOW_TUNNEL_SSH_PORT:-2222}"
API_PORT="${EXHIBITFLOW_PORT:-8501}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LOG_FILE="${EXHIBITFLOW_TUNNEL_LOG:-storage/logs/reverse-tunnel.log}"

mkdir -p "$(dirname "$LOG_FILE")"

while true; do
  printf '[%s] connecting reverse tunnel\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  if ssh -N -T \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ConnectTimeout=10 \
      -o BatchMode=yes \
      -R "127.0.0.1:18610:127.0.0.1:${API_PORT}" \
      -R "127.0.0.1:15173:127.0.0.1:${FRONTEND_PORT}" \
      -p "$REMOTE_PORT" "${REMOTE_USER}@${REMOTE_HOST}" >> "$LOG_FILE" 2>&1; then
    code=0
  else
    code=$?
  fi
  printf '[%s] tunnel exited code=%s; retrying in 3s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$code" >> "$LOG_FILE"
  sleep 3
done
