#!/bin/sh
# The committed PNGs are the splash. This re-renders them from splash.html when
# Playwright's headless Chrome is installed. Output: heuretes_splash.png (568x368)
# and heuretes_splash_2x.png (1136x736). Version comes from the top CMakeLists.txt.
set -e
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
ver() { sed -n "s/^set(PACKAGE_VERSION_$1 \"\([^\"]*\)\".*/\1/p" "$ROOT/CMakeLists.txt" | head -1; }
VERSION="$(ver MAJOR).$(ver MINOR).$(ver PATCH)$(ver SUFFIX)"
for scale in 1 2; do
    out="$HERE/heuretes_splash.png"
    [ "$scale" = 2 ] && out="$HERE/heuretes_splash_2x.png"
    "$CH" --headless --hide-scrollbars --force-device-scale-factor=$scale --window-size=568,368 \
        --virtual-time-budget=1500 --screenshot="$out" "file://$HERE/splash.html?v=$VERSION" >/dev/null 2>&1
    echo "$out ($VERSION)"
done
