#!/bin/sh
# Delete the object files that were compiled before a conda package they include was updated.
#
# Conda keeps the original modification times of a package's headers, so after `pixi install` moves Coin (or any
# other library) to a new version ninja still thinks every object is up to date and never recompiles it. The old
# objects then disagree with the new library about class layouts: the Coin 4.0.10 update changed sizeof(SbString)
# from 144 to 128 bytes, and every Fem::FemPostPipeline crashed in SoFCColorGradient::setMarkerLabel because the
# GUI objects still stepped through an SoMFString in 144-byte strides.
#
#   package/bruceworx/rebuild-stale-objects.sh coin3d        # list what is stale
#   package/bruceworx/rebuild-stale-objects.sh coin3d -f     # delete it, then run the build (hold the build lock)
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
PKG=${1:?usage: rebuild-stale-objects.sh <conda package, e.g. coin3d> [-f]}
META=$(ls -t "$ROOT"/.pixi/envs/default/conda-meta/"$PKG"-*.json | head -1)
INC=${INCLUDE_MATCH:-$([ "$PKG" = coin3d ] && echo /Inventor/ || echo "/$PKG/")}
cd "$ROOT/build/release"
ninja -t deps | awk -v inc="$INC" -v RS= -F'\n' '
  { split($1, h, ":"); hit = 0; for (i = 2; i <= NF; i++) if (index($i, inc)) { hit = 1; break }
    if (hit && h[1] ~ /\.o$/) print h[1] }' | while read -r obj; do
    [ -f "$obj" ] && [ "$obj" -ot "$META" ] && echo "$obj"
done > /tmp/stale-objects.$$ || true
echo "$(wc -l < /tmp/stale-objects.$$ | tr -d ' ') objects older than $(basename "$META")"
if [ "$2" = "-f" ]; then xargs rm -f < /tmp/stale-objects.$$; echo "deleted; run ninja (with the build lock)"; else cat /tmp/stale-objects.$$; fi
rm -f /tmp/stale-objects.$$
