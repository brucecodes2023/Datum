# Part modelling GUI checks

Self-quitting FreeCAD GUI scripts that drive part modelling the way a user does
(workspace tab, toolbar command, task panel widgets, OK / Cancel, Browser,
Undo / Redo) and print PASS / FAIL per check.

| Script | Covers |
| --- | --- |
| `test_toolbars.py` | Design workspace bar and tabs; every Solid group (CREATE, PATTERN, MODIFY, ...) is a visible toolbar with a readable caption; each command has a visible, unclipped button with an icon and tooltip; caption menus list the "more" commands; one row, no overlap, no overflow chevron; enabled / disabled state with no document, an empty body, a sketch selected, a feature selected; the Surface / Mesh / Utilities tabs switch the groups. |
| `test_features.py` | Std_Part, PartDesign_Body, then Pad, Pocket, Hole, Revolve, Groove, Draft, Fillet, Chamfer, Mirror, Linear and Polar pattern, BruceWorx Rectangular / Circular Pattern on a feature (hands over to PartDesign), Cancel, a second body with an additive box, Thickness and a datum plane. Per feature: the dialog opens in the right panel with readable OK / Cancel, values typed into its widgets, OK gives one valid solid, the Browser summary ("8 mm", "R2", "Pattern × 3"), one undo step and an undo toast, Undo / Redo, double-click on the Browser row reopens the dialog and an edit applies. |
| `test_extras.py` | History timeline chips (one per feature, in order, double-click edits); pick to feature (a picked face highlights its feature in the Browser and rings its timeline chip); the red ERROR chip on a failing fillet (and clicking it opens the feature); Std_SetMaterial (steel) and the mass that follows; the right panel's Properties tab for a selection; BruceWorx Rectangular / Circular Pattern on a body (own panel, instances, undo / redo, edit again). |

`harness.py` is the small runner (generator tests, `check()`, screenshots,
a guard that closes stray modal message boxes and logs them). `partmodel.py`
makes the sketches by script (sketching is the sketch tests' job).

## Run

    package/bruceworx/tests/gui/part/run.sh                 # light and dark, every script
    package/bruceworx/tests/gui/part/run.sh light test_extras
    OUT=/some/dir TIMEOUT=600 package/bruceworx/tests/gui/part/run.sh

Each run copies `~/Library/Preferences/FreeCAD/v1-1/user.cfg` (never the
original), sets the theme with `cfgset.py`, and launches through the agents'
`fcslot.sh` when it exists (instance limit). Results land in `$OUT`
(default `$TMPDIR/bw-gui-tests/part`): `<script>-<mode>.txt` (PASS / FAIL
lines), `.json`, `.log` (details, Browser summaries, toasts), and PNG
screenshots (`<script>-<mode>-<step>.png`, 3D view composited under the
panels). The exit status is non-zero when any check fails or a run ends early.

Notes: Gui::UIntSpinBox (pattern occurrences) must be typed into, not set
with `QSpinBox.setValue` from Python: its value is offset by INT_MIN, and
`setValue(3)` asks for about 2^31 occurrences (FreeCAD then allocates until
it is killed).
