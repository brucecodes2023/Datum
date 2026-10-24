#!/bin/sh
# Render the MOD-14 (on-canvas editing) mock figures from mod14.html.
#   render.sh              HTML only (reuses the 3D renders in the work folder)
#   render.sh --views      also re-render the 3D content with FreeCAD (views_mod14.py, ~20 s)
#   render.sh --views-only only the 3D content (used by ../uix12_mock/render.sh)
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-mod14-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
mkdir -p "$WORK/icons" "$WORK/out-mod14"

if [ "$1" = "--views" ] || [ "$1" = "--views-only" ] || [ ! -f "$WORK/points.js" ]; then
    # On a copy of the user config, so nothing the session saves touches the real one.
    cp "$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg" "$WORK/user.cfg"
    BW_MOCK_OUT="$WORK" "$ROOT/package/bruceworx/run-freecad.sh" -u "$WORK/user.cfg" "$HERE/views_mod14.py" >"$WORK/freecad.log" 2>&1 &
    pid=$!
    i=0; while kill -0 $pid 2>/dev/null && [ $i -lt 90 ]; do sleep 1; i=$((i+1)); done
    kill $pid 2>/dev/null || true
fi
[ "$1" = "--views-only" ] && exit 0
cp "$HERE/mod14.html" "$WORK/"

grep -oE '"[A-Za-z_-]+"' "$HERE/mod14.html" | tr -d '"' | sort -u | while read -r n; do
    f=$(find "$ROOT/src" -name "$n.svg" -not -path "*/build/*" | head -1)
    if [ -n "$f" ]; then cp "$f" "$WORK/icons/"; fi
done

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out-mod14/$1.png" "file://$WORK/mod14.html?$2" >/dev/null 2>&1
}
shot pad_light "fig=pad" 1180 760
shot pad_dark "fig=pad&theme=dark" 1180 760
shot pad_notes "fig=pad&notes=1" 1180 760
shot states "fig=states" 1320 430

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out-mod14" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    im.save(os.path.join(dst, "mod14_" + f), optimize=True)
    print("fig/mod14_" + f, im.size)
PY
