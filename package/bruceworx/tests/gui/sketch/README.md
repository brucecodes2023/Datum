# Sketching GUI checks

Self-quitting FreeCAD GUI scripts that drive sketching the way a user does (toolbar commands,
mouse clicks in the 3D view, task-panel buttons) and print PASS / FAIL per check.

    package/bruceworx/tests/gui/sketch/run.sh            # all scripts, light and dark
    package/bruceworx/tests/gui/sketch/run.sh dark sketch_repair.py

Each run uses a fresh copy of `~/Library/Preferences/FreeCAD/v1-1/user.cfg` with the BruceWorx
pack re-applied in that mode (`cfgset.py`), so the real config is never touched. Logs
(`<script>-<mode>.log`), JSON results and screenshots (`<mode>-<script>-<name>.png`, window +
3D view + floating panels composited) go to `$SK_OUT` (default `$TMPDIR/bw-sketch-tests`).
Exit status 1 when any check fails or a run does not finish.

| Script | Covers |
|---|---|
| `sketch_plane.py` | Design workspace; New Sketch on XY; SKETCH tab, FINISH SKETCH, Solid toolbars hidden; sketch toolbars (Edit Mode, Geometries, Constraints, Sketcher Tools, Visual Helpers) with their buttons present, visible, enabled, with icons; BruceWorx grid shown with the pack's colours; view normal to the sketch; nav pill; view size of a new sketch; task panel in the right panel, readable; line / rectangle / circle / arc / polyline drawn with the tools and mouse clicks; Escape; undo / redo inside the sketch; Finish Sketch back to Design; sketch in the Browser. |
| `sketch_constraints.py` | Python geometry; Horizontal, Coincident (to the origin) and Distance via toolbar commands with a selection; the dimension dialog (readable, value typed, OK); solver messages: DoF, Fully constrained, Redundant, Over-constrained (conflicting); construction toggle; Python rectangle / circle / arc / polyline; Trim and Extend tools with mouse clicks and from Python; undo of a trim; Leave Sketch from the Edit Mode toolbar. |
| `sketch_face.py` | New Sketch on a selected face of a padded block (attachment, height, view); external geometry with the Projection tool (click on an edge) and `addExternal`; a constraint to external geometry; Finish; Pocket from the face sketch with OK in its task panel; the right panel's Properties tab for the selected sketch (light check). |
| `sketch_repair.py` | The profile repair card (`bruceworx_sketchrepair`) on a sketch with a 0.05 mm gap: card text, buttons, placement, readability; Close Gaps (one undo step) makes the Pad valid; Undo; Escape dismisses; the card during the Pad dialog (Open Sketch disabled), Close Gaps then OK; Open Sketch. |

`harness.py` holds the shared bits: step runner (one event-loop turn per step, a watchdog that
logs and closes unexpected modal dialogs, overall timeout), `check()`, screenshot compositing,
text-contrast measurement on rendered widgets (`check_readable`, ink vs background >= 3:1),
and 3D-view clicks (`click_vec`, `click_frac`; mouse moves are sent as QMouseEvents because
`QTest.mouseMove` does not reach the Coin viewer).

Notes: never call `Command.isActive()` (can crash) — enabled state is read from the QAction.
Several agents may run FreeCAD at once; `run.sh` launches through `$FCSLOT` when it exists.

## Leonardo parts (work in progress)
`leo_plate.py` models the motor adapter plate through the UI (commands, task panels, dimension
dialog, Hole, Circular Pattern); `leo_ui.py` holds the UI helpers, `leo_vault.py` the vault steps
(check out, Save As `<P/N>.FCStd` in the work folder, Check In with a comment). Without `LEO_PN` it
is a dry run saved to `$SK_OUT`. End cap and NEMA 17 scripts are still to be written.
