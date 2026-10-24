#!/bin/sh
# Assembly GUI checks: run every t*.py (or the ones named) in the build-tree FreeCAD,
# light and dark, each on a fresh copy of the user's config. Prints PASS/FAIL per check.
#
#   run.sh                      all scripts, light and dark
#   run.sh t03_joints.py        one script, light and dark
#   MODES=dark run.sh           dark only
#   BWT_OUT=/some/dir run.sh    where results, screenshots and work files go
#                               (default $TMPDIR/bruceworx-gui-tests/assembly)
#   BWT_TIMEOUT=300 run.sh      seconds per script run (default 240)
#   FCSLOT=/path/fcslot.sh      launcher that caps how many FreeCADs run at once
#                               (default: the agents' fcslot.sh when it exists; else none)
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
OUT=${BWT_OUT:-${TMPDIR:-/tmp}/bruceworx-gui-tests/assembly}
MODES=${MODES:-"light dark"}
T=${BWT_TIMEOUT:-240}
CFGSET="$REPO/package/bruceworx/docs/fusion-ui-plan/scripts/ui_mock/cfgset.py"
mkdir -p "$OUT/cfg"
SLOT_DEFAULT=/private/tmp/claude-501/-Users-brucetokar-Documents-GitHub-FreeCAD-BruceWorx/7d380133-b832-4a85-a8ba-be14e8b5b257/scratchpad/agents/fcslot.sh
if [ -z "$FCSLOT" ] && [ -x "$SLOT_DEFAULT" ]; then FCSLOT=$SLOT_DEFAULT; fi
if [ $# -gt 0 ]; then SCRIPTS="$*"; else SCRIPTS=$(cd "$HERE" && ls t*.py); fi
TOTAL_FAIL=0
for MODE in $MODES; do
  for S in $SCRIPTS; do
    NAME=$(basename "$S" .py)
    CFG="$OUT/cfg/$NAME-$MODE.cfg"
    cp "$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg" "$CFG"
    python3 "$CFGSET" "$CFG" "$MODE" BaseApp/Preferences/BruceWorx/FollowSystemAppearance:bool=0 >/dev/null
    rm -f "$OUT/$NAME-$MODE.txt"
    AGENT=${AGENT:-test-assembly} BW_TEST="assembly GUI check $NAME ($MODE)" \
    BWT_OUT="$OUT" BWT_MODE="$MODE" BWT_SCRIPT="$NAME" \
      $FCSLOT timeout "$T" "$REPO/package/bruceworx/run-freecad.sh" -u "$CFG" "$HERE/$NAME.py" \
      > "$OUT/$NAME-$MODE.stdout" 2>&1
    CODE=$?
    if [ -f "$OUT/$NAME-$MODE.txt" ]; then
      sed "s/^/[$MODE] $NAME: /" "$OUT/$NAME-$MODE.txt"
      F=$(grep -c '^FAIL' "$OUT/$NAME-$MODE.txt")
      TOTAL_FAIL=$((TOTAL_FAIL + F))
    fi
    if ! grep -q '^END' "$OUT/$NAME-$MODE.txt" 2>/dev/null; then
      echo "[$MODE] $NAME: FAIL run did not finish (exit $CODE: crash or timeout); see $OUT/$NAME-$MODE.stdout"
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
    fi
  done
done
echo "Results, screenshots: $OUT  (failures: $TOTAL_FAIL)"
[ "$TOTAL_FAIL" -eq 0 ]
