#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

if [[ -z "${AXCLI_BIND_HOST:-}" ]]; then
  AXCLI_BIND_HOST="$(/sbin/ifconfig -a | /usr/bin/awk '
    /^utun[0-9]+:/ { is_utun = 1; next }
    /^[^[:space:]]/ { is_utun = 0 }
    is_utun && /inet 100\./ { for (i = 1; i <= NF; i++) if ($i == "inet") { print $(i + 1); exit } }
  ')"
fi

if [[ ! "$AXCLI_BIND_HOST" =~ ^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\. ]]; then
  print -u2 "No Tailscale IPv4 address found on a utun interface; refusing to bind anywhere."
  exit 1
fi

export AXCLI_BIND_HOST
export AXCLI_BIN="${AXCLI_BIN:-$PWD/.tools/axcli/bin/axcli}"
export AXCLI_DESKTOP_BIN="${AXCLI_DESKTOP_BIN:-$PWD/.tools/desktop-control}"
export AXCLI_TOKEN_FILE="${AXCLI_TOKEN_FILE:-$PWD/.secrets/api_token}"

if [[ ! -x "$AXCLI_BIN" ]]; then
  print -u2 "AXCLI is missing. Run ./setup.sh first."
  exit 1
fi
if [[ ! -r "$AXCLI_TOKEN_FILE" ]]; then
  print -u2 "API token is missing. Create $AXCLI_TOKEN_FILE first."
  exit 1
fi

exec .venv/bin/uvicorn main:app --host "$AXCLI_BIND_HOST" --port "${AXCLI_PORT:-8765}"
