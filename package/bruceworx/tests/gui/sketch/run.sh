#!/bin/sh
# Sketching GUI checks: run each script on a fresh copy of the user's config, light and dark.
# Usage: run.sh [light|dark|both] [script.py ...]      (default: both, all scripts)
# Env:   SK_OUT   output folder (logs, screenshots)   default: $TMPDIR/bw-sketch-tests
#        FCSLOT   launcher that limits concurrent FreeCAD runs (used when it exists)
#        SK_TIMEOUT seconds per script (default 150)
# Prints PASS/FAIL per check; exit status 1 when any check failed or a run crashed.
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
MODES=${1:-both}
[ $# -gt 0 ] && shift
[ "$MODES" = "both" ] && MODES="light dark"
SCRIPTS="$*"
[ -z "$SCRIPTS" ] && SCRIPTS="sketch_plane.py sketch_constraints.py sketch_face.py sketch_repair.py"
OUT=${SK_OUT:-${TMPDIR:-/tmp}/bw-sketch-tests}
mkdir -p "$OUT"
FCSLOT=${FCSLOT:-/private/tmp/claude-501/-Users-brucetokar-Documents-GitHub-FreeCAD-BruceWorx/7d380133-b832-4a85-a8ba-be14e8b5b257/scratchpad/agents/fcslot.sh}
[ -x "$FCSLOT" ] || FCSLOT=""
TO=${SK_TIMEOUT:-150}
FAILED=0
for MODE in $MODES; do
  for S in $SCRIPTS; do
    N=$(basename "$S" .py)
    CFG="$OUT/$N-$MODE.cfg"
    cp "$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg" "$CFG"
    python3 "$ROOT/package/bruceworx/docs/fusion-ui-plan/scripts/ui_mock/cfgset.py" "$CFG" "$MODE" \
      BaseApp/Preferences/BruceWorx/FollowSystemAppearance:bool=0
    rm -f "$OUT/$N-$MODE.log"
    AGENT=${AGENT:-test-sketch} BW_TEST="sketching: $N ($MODE)" \
    SK_OUT="$OUT" SK_TAG="$MODE" SK_TIMEOUT="$TO" $FCSLOT timeout $((TO + 60)) \
      "$ROOT/package/bruceworx/run-freecad.sh" -u "$CFG" "$HERE/$N.py" > "$OUT/$N-$MODE.out" 2>&1
    RC=$?
    echo "== $N ($MODE) exit $RC"
    if [ -f "$OUT/$N-$MODE.log" ]; then
      grep -E "^(PASS|FAIL|DONE)" "$OUT/$N-$MODE.log"
      grep -q "^FAIL" "$OUT/$N-$MODE.log" && FAILED=1
      grep -q "^DONE" "$OUT/$N-$MODE.log" || { echo "FAIL $N ($MODE): did not finish (crash?)"; FAILED=1; }
    else
      echo "FAIL $N ($MODE): no log (FreeCAD did not start or crashed)"; FAILED=1
    fi
  done
done
echo "Screenshots and logs: $OUT"
exit $FAILED
