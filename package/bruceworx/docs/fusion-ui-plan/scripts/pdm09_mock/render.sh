#!/bin/sh
# Render the PDM-09 mock figures (Vault tab Structure view, New Project panel) from pdm09.html, light mode.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-pdm09-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
mkdir -p "$WORK/out"
cp "$HERE/pdm09.html" "$WORK/"

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out/$1.png" "file://$WORK/pdm09.html?$2" >/dev/null 2>&1
}
shot tree "fig=tree" 880 720
shot wizard "fig=wizard" 990 690

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    im.save(os.path.join(dst, "pdm09_" + f), optimize=True)
    print("fig/pdm09_" + f, im.size)
PY
