#!/bin/sh
# Drawings & Vault GUI checks: run each script in the FreeCAD GUI on a fresh copy of the user's
# config, in light and dark, and print PASS/FAIL per check.
#
#   run.sh                      all scripts, light and dark
#   run.sh drawing vault_setup  some scripts (file names without test_ and .py)
#   BW_THEMES=dark run.sh       one theme
#   BW_OUT=/some/folder run.sh  where logs, shots and the temporary vaults go
#                               (default $TMPDIR/bruceworx-gui-docs; never iCloud Drive)
#
# The real user.cfg is only copied. Each FreeCAD started here is killed by its own PID after
# BW_TIMEOUT seconds (default 300) if it has not quit by itself (timeout(1)). When the agents'
# fcslot.sh exists (or BW_FCSLOT names a launcher), every run waits for a free FreeCAD slot.
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
R=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
OUT=${BW_OUT:-${TMPDIR:-/tmp}/bruceworx-gui-docs}
OUT=${OUT%/}
THEMES=${BW_THEMES:-"light dark"}
TIMEOUT=${BW_TIMEOUT:-300}
CFG="$HOME/Library/Preferences/FreeCAD/v1-1/user.cfg"
A=/private/tmp/claude-501/-Users-brucetokar-Documents-GitHub-FreeCAD-BruceWorx/7d380133-b832-4a85-a8ba-be14e8b5b257/scratchpad/agents
FCSLOT=${BW_FCSLOT-}
[ -z "$FCSLOT" ] && [ -x "$A/fcslot.sh" ] && FCSLOT="$A/fcslot.sh"
SCRIPTS=${*:-"drawing vault_setup vault_work changes"}
case "$OUT" in
  *"Mobile Documents"*|"$HOME/Documents"*|"$HOME/Desktop"*) echo "refused: BW_OUT=$OUT is synced by iCloud"; exit 2;;
esac
mkdir -p "$OUT/cfg" "$OUT/logs" "$OUT/shots"
total_pass=0; total_fail=0
for t in $THEMES; do
  for s in $SCRIPTS; do
    name=$s
    cfg="$OUT/cfg/$name-$t.cfg"
    cp "$CFG" "$cfg"
    python3 "$R/package/bruceworx/docs/fusion-ui-plan/scripts/ui_mock/cfgset.py" "$cfg" "$t" \
      BaseApp/Preferences/BruceWorx/FollowSystemAppearance:bool=0 \
      BaseApp/Preferences/BruceWorx/VaultPath:text= \
      BaseApp/Preferences/Mod/Start/ShowOnStartup:bool=1
    log="$OUT/logs/$name-$t.log"
    rm -f "$log"
    # At most 5 FreeCADs at once (the user's rule): start through the shared slot script when
    # there is one (BW_FCSLOT), with our own timeout inside it.
    AGENT=${AGENT:-test-docs} BW_TEST="docs $name ($t)" \
    BW_THEME=$t BW_OUT=$OUT BW_SCRIPT=$name BW_TESTDIR=$HERE \
      $FCSLOT timeout -k 10 "$TIMEOUT" "$R/package/bruceworx/run-freecad.sh" -u "$cfg" "$HERE/test_$name.py" \
      > "$OUT/logs/$name-$t.out" 2>&1
    code=$?
    if [ $code -eq 124 ] || [ $code -eq 137 ]; then
      echo "FAIL [$name/$t] timed out after ${TIMEOUT}s"
      total_fail=$((total_fail+1))
    fi
    if [ -f "$log" ]; then
      grep '^CHECK ' "$log" | sed -e "s/^CHECK PASS /PASS [$name\/$t] /" -e "s/^CHECK FAIL /FAIL [$name\/$t] /"
      p=$(grep -c '^CHECK PASS' "$log"); f=$(grep -c '^CHECK FAIL' "$log")
      total_pass=$((total_pass+p)); total_fail=$((total_fail+f))
      if ! grep -q '^RESULT' "$log"; then
        echo "FAIL [$name/$t] did not finish (exit $code): see $OUT/logs/$name-$t.out and ~/Library/Logs/DiagnosticReports/FreeCAD-*.ips"
        total_fail=$((total_fail+1))
      fi
    else
      echo "FAIL [$name/$t] no log (exit $code): see $OUT/logs/$name-$t.out"
      total_fail=$((total_fail+1))
    fi
  done
done
echo "TOTAL $total_pass passed, $total_fail failed. Shots: $OUT/shots  Logs: $OUT/logs"
[ $total_fail -eq 0 ]
