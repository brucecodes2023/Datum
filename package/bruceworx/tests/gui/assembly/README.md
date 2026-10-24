# Assembly GUI checks

Self-quitting scripts that drive the real FreeCAD GUI (build/release) the way a user does:
toolbar buttons, task dialogs with face/edge picks, OK/Cancel buttons, the Browser.
Each check prints `PASS` or `FAIL name :: detail`.

| Script | Covers |
|---|---|
| `t01_toolbar.py` | Design > Solid > Assemble group: 5 buttons shown, icons, tooltips, not clipped; ASSEMBLE caption and its menu (all "more" commands, incl. Insert Board); enabled states with no document / empty document / active assembly / assembly with a part / joint dialog open / assembly left; Slider button switches an open joint dialog; More entries with no document |
| `t02_insert.py` | New Assembly; Component dialog from other (opened) files: unsaved-assembly prompt, ground-first prompt, insert counters, OK; links to the files; Browser part-number text on components; Toggle Grounded on/off, undo/redo; save, close all, reopen: links resolve, places and grounding kept; right panel Properties tab (light) |
| `t03_joints.py` | Fixed, Revolute, Cylindrical, Slider, Ball, Distance (5 mm), Parallel, Perpendicular, Angle (30°) through the joint dialog with picks; solve; geometry checked; undo/redo; OK with one pick; Cancel |
| `t04_limits_drag.py` | Revolute angle limits and Slider length limits set in the edit dialog (double-click); drag API (preDrag/doDragStep/postDrag) free and against the limits; Browser shows the range; "Keep parts where they are"; save/reopen keeps them |
| `t05_explode_bom.py` | Exploded view (one move, dragger driven through the panel), reopen, cancel; BOM dialog: default columns, a `.PartNumber` column, quantities, a sub-assembly (detailed and not), File Name, no N/A, detail parts and `BomDetail = False`, BOM after deleting a component |
| `t06_delete_replace.py` | Delete a component (its joints go too), undo; a component removed by a script (joint kept and marked broken, Report view reason), undo heals; replacing a component's part (missing element), replace back heals; delete a joint; broken joint through save/reopen |

Not covered: Insert Board (no `.kicad_pcb` sample in the repo; the button's presence is checked in t01),
mouse drags in the 3D view (the drag API is used instead), the native file dialog of the Component
dialog's "Open file" button (part files are opened with File > Open first).

## Run

    package/bruceworx/tests/gui/assembly/run.sh                 # all, light and dark
    package/bruceworx/tests/gui/assembly/run.sh t03_joints.py   # one script
    MODES=dark BWT_OUT=/tmp/asm package/bruceworx/tests/gui/assembly/run.sh

Each run uses a fresh copy of `~/Library/Preferences/FreeCAD/v1-1/user.cfg` (light/dark set with
`cfgset.py`), writes `<script>-<mode>.txt` (checks), `.log`, `-report.txt` (Report view) and
`shots/*.png` (window with the 3D view rendered in, task panel, Browser) under `BWT_OUT`
(default `$TMPDIR/bruceworx-gui-tests/assembly`). Part files are made fresh in `BWT_OUT/work/`.
A run that does not reach `END` (crash or timeout) is a FAIL; see `<script>-<mode>.stdout` and
`~/Library/Logs/DiagnosticReports/FreeCAD-*.ips`. FreeCAD starts through `fcslot.sh` when it exists
(set `FCSLOT=` to override). Exit code 0 when everything passed.

Files: `asmlib.py` (harness: checks, screenshots, task buttons, modal answers, Browser),
`fixture.py` (Base plate, Pin, Block part files with part numbers; a Pin-on-Block sub-assembly),
`scene.py` (a saved assembly of those parts, pick helpers, `make_joint`).
