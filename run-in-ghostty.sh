#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh >>server.log 2>&1
