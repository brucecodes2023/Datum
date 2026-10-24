#!/bin/sh
# Render the UIX-12 (property inspector) mock figures from uix12.html.
#   render.sh           HTML only (reuses the 3D renders in the work folder)
#   render.sh --views   also re-render the 3D content with FreeCAD (../mod14_mock/views_mod14.py, ~20 s)
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-mod14-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
mkdir -p "$WORK/icons" "$WORK/out-uix12"
cp "$HERE/uix12.html" "$WORK/"

if [ "$1" = "--views" ] || [ ! -f "$WORK/points.js" ]; then
    "$HERE/../mod14_mock/render.sh" --views-only
fi

grep -oE '"[A-Za-z_-]+"' "$HERE/uix12.html" | tr -d '"' | sort -u | while read -r n; do
    f=$(find "$ROOT/src" -name "$n.svg" -not -path "*/build/*" | head -1)
    if [ -n "$f" ]; then cp "$f" "$WORK/icons/"; fi
done

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out-uix12/$1.png" "file://$WORK/uix12.html?$2" >/dev/null 2>&1
}
shot browser_light "fig=browser" 1180 820
shot browser_dark "fig=browser&theme=dark" 1180 820
shot browser_notes "fig=browser&notes=1" 1180 820
shot states "fig=states" 1300 470

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out-uix12" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    im.save(os.path.join(dst, "uix12_" + f), optimize=True)
    print("fig/uix12_" + f, im.size)
PY
