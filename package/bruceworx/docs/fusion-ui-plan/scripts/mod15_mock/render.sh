#!/bin/sh
# Render the MOD-15 mock figures (design worksheet tab; worksheet out of date) from mod15.html, light mode.
# Formulas come from matplotlib mathtext (make_math.py, the pixi env's python), as the worksheet would draw them.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-mod15-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
PY="${BW_PY:-$ROOT/.pixi/envs/default/bin/python3.11}"
mkdir -p "$WORK/out"
cp "$HERE/mod15.html" "$WORK/"
"$PY" "$HERE/make_math.py" "$WORK"

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out/$1.png" "file://$WORK/mod15.html?$2" >/dev/null 2>&1
}
shot sheet "fig=sheet" 1440 1150
shot stale "fig=stale" 1180 270

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    im.save(os.path.join(dst, "mod15_" + f), optimize=True)
    print("fig/mod15_" + f, im.size)
PY
