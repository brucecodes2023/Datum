#!/bin/sh
# Part-modelling GUI checks: runs each test_*.py in FreeCAD's GUI, light and dark,
# on a fresh copy of the user's config, and prints PASS/FAIL per check.
#
#   run.sh [light|dark|both] [test_name ...]     (default: both, every test_*.py)
#   OUT=/some/dir run.sh                          (results + screenshots; default below)
#
# Each run: <OUT>/<test>-<mode>.txt (PASS/FAIL lines), .json, .log, and *.png screenshots.
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
OUT=${OUT:-${TMPDIR:-/tmp}/bw-gui-tests/part}
MODES=${1:-both}
[ "$MODES" = both ] && MODES="light dark"
[ $# -gt 0 ] && shift
TESTS="$*"
[ -z "$TESTS" ] && TESTS=$(cd "$HERE" && ls test_*.py | sed 's/\.py$//')
TIMEOUT=${TIMEOUT:-400}
# At most 5 FreeCAD instances at once (the user's rule): launch through fcslot.sh when it is there.
FCSLOT=${FCSLOT:-/private/tmp/claude-501/-Users-brucetokar-Documents-GitHub-FreeCAD-BruceWorx/7d380133-b832-4a85-a8ba-be14e8b5b257/scratchpad/agents/fcslot.sh}
[ -x "$FCSLOT" ] || FCSLOT=""
mkdir -p "$OUT"
echo "results in $OUT"
fails=0
for mode in $MODES; do
  for t in $TESTS; do
    cfg="$OUT/$t-$mode.cfg"
    cp "$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg" "$cfg"
    python3 "$ROOT/package/bruceworx/docs/fusion-ui-plan/scripts/ui_mock/cfgset.py" "$cfg" "$mode" \
      BaseApp/Preferences/BruceWorx/FollowSystemAppearance:bool=0
    rm -f "$OUT/$t-$mode.txt"
    desc=$(sed -n 's/^"""\(.*\)$/\1/p' "$HERE/$t.py" | head -1)
    AGENT=${AGENT:-test-part} BW_TEST="part modelling, $mode: $desc" PT_OUT="$OUT" PT_TAG="$mode" PT_SCRIPT="$t" \
      $FCSLOT timeout "$TIMEOUT" "$ROOT/package/bruceworx/run-freecad.sh" -u "$cfg" "$HERE/$t.py" > "$OUT/$t-$mode.stdout" 2>&1
    code=$?
    echo "== $t ($mode) exit $code"
    if [ -f "$OUT/$t-$mode.txt" ]; then
      cat "$OUT/$t-$mode.txt"
      grep -q "^# " "$OUT/$t-$mode.txt" || { echo "FAIL $t.finished: run ended early (crash or timeout)"; fails=$((fails+1)); }
      fails=$((fails + $(grep -c "^FAIL" "$OUT/$t-$mode.txt")))
    else
      echo "FAIL $t.started: no results (see $OUT/$t-$mode.stdout)"; fails=$((fails+1))
    fi
  done
done
echo "== total FAIL lines: $fails"
[ "$fails" -eq 0 ]
