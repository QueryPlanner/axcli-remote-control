#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
mkdir -p .cache/pip
export PIP_CACHE_DIR="$PWD/.cache/pip"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if ! command -v cargo >/dev/null 2>&1; then
  print -u2 "Rust/Cargo is required to install AXCLI."
  exit 1
fi

mkdir -p .tools/axcli .cache/cargo .cache/cargo-target
python3 build-axcli.py
/bin/sh macos/build-desktop.sh "$PWD/.tools/desktop-control"

print "Installed AXCLI at $PWD/.tools/axcli/bin/axcli"
