#!/bin/sh
set -eu
exec /usr/bin/xcrun clang -fobjc-arc -Wall -Wextra \
    -framework ApplicationServices -framework Foundation -framework ImageIO -framework ScreenCaptureKit \
    "$(dirname "$0")/Desktop.m" -o "$1"
