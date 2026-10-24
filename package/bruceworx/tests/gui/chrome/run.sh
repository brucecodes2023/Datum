#!/bin/sh
# Window-chrome GUI checks: run.sh [script ...]   (default: every t_*.py)
# Env: BWT_OUT (results folder, default $TMPDIR/bw-chrome-tests), BWT_MODES ("light dark"),
#      BWT_TIMEOUT (seconds per run, default 660), BWT_FCSLOT (launcher that limits parallel FreeCADs).
# Each script runs on a fresh copy of the user's config, once per mode, and prints PASS/FAIL per check.
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
OUT=${BWT_OUT:-${TMPDIR:-/tmp}/bw-chrome-tests}
MODES=${BWT_MODES:-"light dark"}
TMO=${BWT_TIMEOUT:-660}
CFG_SRC="$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg"
CFGSET="$ROOT/package/bruceworx/docs/fusion-ui-plan/scripts/ui_mock/cfgset.py"
mkdir -p "$OUT/shots" "$OUT/fixtures"
FIX="$OUT/fixtures"
# Every FreeCAD start goes through the slot limiter when there is one (at most 5 FreeCADs at once).
A=/private/tmp/claude-501/-Users-brucetokar-Documents-GitHub-FreeCAD-BruceWorx/7d380133-b832-4a85-a8ba-be14e8b5b257/scratchpad/agents
SLOT=${BWT_FCSLOT:-}
[ -z "$SLOT" ] && [ -x "$A/fcslot.sh" ] && SLOT="$A/fcslot.sh"

if [ $# -eq 0 ]; then
    set -- $(cd "$HERE" && ls t_*.py)
fi

# Files the checks open (a plate with a part number, a bracket, an empty vault).
if [ ! -f "$FIX/Plate.FCStd" ]; then
    BWT_FIXTURES="$FIX" AGENT=test-chrome BW_TEST="chrome fixtures" $SLOT timeout 120 "$ROOT/build/release/bin/FreeCADCmd" -c "import sys; sys.argv=['fixtures.py','$FIX']; p='$HERE/fixtures.py'; exec(open(p).read(), {'__file__': p, '__name__': '__main__'})" > "$OUT/fixtures.out" 2>&1
fi

TOTAL_FAIL=0
for S in "$@"; do
    NAME=$(basename "$S" .py)
    for MODE in $MODES; do
        TAG=$MODE
        CFG="$OUT/$NAME-$TAG.cfg"
        cp "$CFG_SRC" "$CFG"  # the user's own config is only read
        python3 "$CFGSET" "$CFG" "$MODE" BaseApp/Preferences/BruceWorx/FollowSystemAppearance:bool=0 > /dev/null
        python3 "$HERE/prepcfg.py" "$CFG" "$FIX"
        if [ "$NAME" = "t_leonardo" ]; then
            # A fresh copy of the Leonardo vault (never the vault itself), opened as test-chrome.
            SRC=${BWT_VAULT:-"$HOME/Library/Mobile Documents/com~apple~CloudDocs/Leonardo"}
            COPY="$OUT/LeonardoCopy-$MODE"
            rm -rf "$COPY"
            if [ -d "$SRC" ]; then
                rsync -a --exclude 'work/' "$SRC/" "$COPY/"
                python3 "$HERE/prepcfg.py" "$CFG" "$FIX" "$COPY" test-chrome
            else
                echo "   (no vault at $SRC)"
            fi
        fi
        LOG="$OUT/$NAME-$TAG.fclog"
        rm -f "$LOG" "$OUT/$NAME-$TAG.txt"
        BWT_OUT="$OUT" BWT_TAG="$TAG" BWT_SCRIPT="$NAME" BWT_FIXTURES="$FIX" \
            AGENT=test-chrome BW_TEST="chrome $NAME ($MODE)" $SLOT timeout "$TMO" "$ROOT/package/bruceworx/run-freecad.sh" -u "$CFG" --log-file "$LOG" "$HERE/$NAME.py" \
            > "$OUT/$NAME-$TAG.out" 2>&1
        RC=$?
        RES="$OUT/$NAME-$TAG.txt"
        [ -f "$RES" ] || : > "$RES"
        if [ $RC -ne 0 ]; then
            echo "FAIL process-exit: FreeCAD exited with $RC (124 = timeout, 134/139 = crash; see ~/Library/Logs/DiagnosticReports)" >> "$RES"
        fi
        python3 "$HERE/collect.py" "$LOG" "$OUT/$NAME-$TAG.out" "$RES" > /dev/null
        P=$(grep -c '^PASS' "$RES"); F=$(grep -c '^FAIL' "$RES")
        TOTAL_FAIL=$((TOTAL_FAIL + F))
        echo "== $NAME [$TAG]: $P passed, $F failed  ($RES)"
        grep '^FAIL' "$RES" | sed 's/^/   /'
    done
done
echo "Screenshots: $OUT/shots"
[ $TOTAL_FAIL -eq 0 ]
