#!/bin/sh
# Render the PDM-10 mock figures (vault dashboard light and dark, empty vault light) from pdm10.html.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-pdm10-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
mkdir -p "$WORK/out"
cp "$HERE/pdm10.html" "$WORK/"

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out/$1.png" "file://$WORK/pdm10.html?$2" >/dev/null 2>&1
}
shot dashboard_light "theme=light&fig=full" 1440 1010
shot dashboard_dark "theme=dark&fig=full" 1440 1010
shot dashboard_empty "theme=light&fig=empty" 1240 470

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    im.save(os.path.join(dst, "pdm10_" + f), optimize=True)
    print("fig/pdm10_" + f, im.size)
PY
