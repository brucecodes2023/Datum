#!/bin/sh
# Render the PDM-04 (vault client) mock figures from pdm04.html.
#   render.sh           HTML only (reuses the 3D renders in the work folder)
#   render.sh --views   also re-render the 3D content with FreeCAD (views_pdm.py, ~25 s)
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../../../../.." && pwd)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK="${BW_MOCK_WORK:-${TMPDIR:-/tmp}/bw-pdm04-mock}"
FIG="$ROOT/package/bruceworx/docs/fusion-ui-plan/fig"
mkdir -p "$WORK/icons" "$WORK/thumbs" "$WORK/out"
cp "$HERE/pdm04.html" "$WORK/"

if [ "$1" = "--views" ] || [ ! -f "$WORK/view-asm.png" ]; then
    cp "$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg" "$WORK/user.cfg"
    BW_MOCK_OUT="$WORK" "$ROOT/package/bruceworx/run-freecad.sh" -u "$WORK/user.cfg" "$HERE/views_pdm.py" >"$WORK/freecad.log" 2>&1 &
    pid=$!
    i=0; while kill -0 $pid 2>/dev/null && [ $i -lt 90 ]; do sleep 1; i=$((i+1)); done
    kill $pid 2>/dev/null || true
fi

# Thumbnails for the Data panel: the renders cropped square, plus the engine block example.
python3 - "$WORK" <<'PY'
import sys, glob, os
from PIL import Image
w = sys.argv[1]
for f in glob.glob(os.path.join(w, "thumb-*.png")):
    im = Image.open(f).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    iw, ih = im.size
    side = int(max(iw, ih) * 1.2)
    c = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    c.alpha_composite(im, ((side - iw) // 2, (side - ih) // 2))
    c.resize((160, 160), Image.LANCZOS).save(os.path.join(w, "thumbs", os.path.basename(f)[6:]))
PY
unzip -p "$ROOT/data/examples/EngineBlock.FCStd" thumbnails/Thumbnail.png > "$WORK/thumbs/EngineBlock.png"

grep -oE '"[A-Za-z_-]+"' "$HERE/pdm04.html" | tr -d '"' | sort -u | while read -r n; do
    f=$(find "$ROOT/src" -name "$n.svg" -not -path "*/build/*" | head -1)
    if [ -n "$f" ]; then cp "$f" "$WORK/icons/"; fi
done

CH=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | tail -1)
shot() {  # name query width height
    "$CH" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size="$3,$4" \
        --virtual-time-budget=2000 --screenshot="$WORK/out/$1.png" "file://$WORK/pdm04.html?$2" >/dev/null 2>&1
}
shot overview "fig=overview" 1600 1000
shot datapanel "fig=data" 420 820
shot datapanel_dark "fig=data&theme=dark" 420 820
shot contextmenu "fig=menu" 980 560
shot checkin "fig=checkin" 460 820
shot states "fig=states" 1120 690

[ -n "$NOFIG" ] && exit 0
python3 - "$WORK/out" "$FIG" <<'PY'
import sys, os
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
for f in sorted(os.listdir(src)):
    im = Image.open(os.path.join(src, f)).convert("RGB")
    if f == "overview.png":
        im = im.resize((2400, 1500), Image.LANCZOS)
    im.save(os.path.join(dst, "pdm04_" + f), optimize=True)
    print("fig/pdm04_" + f, im.size)
PY
