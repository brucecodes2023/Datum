# Window chrome GUI checks

Self-quitting FreeCAD GUI scripts for everything around the model: start-up,
the Start page, the macOS title row, workspaces and tool groups, Cmd-K search,
the Browser, the right panel, the navigation pill and cube, undo toasts, the
status bar, notifications, Preferences -> BruceWorx, light/dark and small
window sizes.

## Run

    package/bruceworx/tests/gui/chrome/run.sh                 # every t_*.py, light and dark
    package/bruceworx/tests/gui/chrome/run.sh t_panels.py     # one script
    BWT_MODES=dark BWT_OUT=/tmp/chrome run.sh t_resize.py

Each run copies `~/Library/Preferences/FreeCAD/v1-1/user.cfg` (never touched),
sets the theme with `cfgset.py` (FollowSystemAppearance off), points the recent
files at the fixtures (`prepcfg.py`) and starts `run-freecad.sh -u <copy>
--log-file <log> <script>` with a timeout, through the agents' `fcslot.sh`
when it exists (at most a few FreeCADs at once; `BWT_FCSLOT` overrides).
It prints PASS/FAIL per check and exits non-zero on any FAIL.

Output (default `$TMPDIR/bw-chrome-tests`, or `BWT_OUT`):

* `<script>-<mode>.txt` - PASS / FAIL / INFO lines; `.json` the same as data.
* `<script>-<mode>.fclog` - FreeCAD's log. `collect.py` splits it at the
  `[bwtest] step` markers and adds `no-errors:<step>`: every error or warning
  in the Report view is pinned on the step that caused it (`startup` = before
  the first check).
* `shots/` - screenshots (window grabs with the 3D view rendered in; the
  navigation cube is not in them: FreeCAD's saveImage leaves it out).
* `fixtures/` - Plate.FCStd (part number LE-01-01001), Bracket.FCStd and an
  empty folder vault, made once by `fixtures.py` with FreeCADCmd.

## Scripts

| script | covers |
|---|---|
| `t_startup.py` | start-up (workbench, Start tab, Report view clean), Start page (New items and icons, Open/Import, recent cards and table, missing file, segments, filter, vault switcher with a real vault, status-bar vault readout), opening a recent card, each New item (Part, Assembly, Drawing, Sketch, Simulation study), title row (traffic-light inset, Save/Undo/Redo, tabs mirror the documents, tab click activates, + new, modified dot, close button), several open documents |
| `t_workspaces.py` | workspace menu and Ctrl+1..4, every workspace and tab: rows, empty or duplicated toolbars and buttons, buttons laid out 0 px tall, icons, overflow (»), group captions (present, match workspaces.json, not clipped, no overlap), More workbenches, the SKETCH tab and Finish Sketch |
| `t_panels.py` | Browser (floating, header, badges, summaries, error chip, dependency lines, pick trace), right panel (tabs, selection brings Properties, a command brings its tab forward and folds on close, Cmd-I), navigation pill and cube placement, undo toast, status bar readouts, notification popup, Cmd-K search (commands, objects, settings, keys) |
| `t_prefs.py` | Preferences -> BruceWorx: each switch off and on through the page and Apply; saved value, live effect where there is one, errors per switch |
| `t_theme.py` | readability (contrast) of every piece of chrome in the run's theme; FollowSystemAppearance on/off (macOS is only read) |
| `t_leonardo.py` | the Leonardo project on a fresh COPY of the vault (run.sh rsyncs it to `$BWT_OUT/LeonardoCopy-<mode>`, actor test-chrome; `BWT_VAULT` overrides the source): Start page vault + My work, Vault panel, each part opened from the vault read-only (dialog, title tab, Browser badges and lock, workspace, right-panel tabs, Cmd-K by label and P/N, editing a read-only or locked file), a check-out in the copy with an undo toast, 1280/1024 px |
| `t_resize.py` | 1512x945, 1280x800, 1024x700, 800x600: clipping, overflow and overlaps of title row, toolbars, captions, panels, pill, status bar, Start page |

`standin_vault.py` builds a two-part stand-in "Leonardo" vault for trying t_leonardo before the real
one exists (`BWT_VAULT=<stand-in> run.sh t_leonardo.py`); it refuses iCloud paths.
`windowchecks.py` holds the clipping/overlap checks t_resize and t_leonardo share.
`bwt.py` is the harness (checks, steps, screenshots, modal-dialog guard,
watchdog); `bwmodel.py` builds the sample plate.

Keyboard shortcuts only reach the active window: when another app (or another
FreeCAD) has the focus, a shortcut check is reported as INFO "not checked"
rather than FAIL.

Known noise left out: `3DconnexionNavlib` (no SpaceMouse driver installed).
