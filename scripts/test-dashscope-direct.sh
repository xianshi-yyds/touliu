#!/bin/zsh
set -euo pipefail

SOCKET="/tmp/verge/verge-mihomo.sock"
restore_mode() {
  curl -sS --unix-socket "$SOCKET" \
    -X PATCH http://localhost/configs \
    -H 'Content-Type: application/json' \
    -d '{"mode":"rule"}' >/dev/null || true
}
trap restore_mode EXIT INT TERM

curl -sS --unix-socket "$SOCKET" \
  -X PATCH http://localhost/configs \
  -H 'Content-Type: application/json' \
  -d '{"mode":"direct"}' >/dev/null

sleep 1
curl -sS -I --connect-timeout 10 --max-time 20 \
  https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
