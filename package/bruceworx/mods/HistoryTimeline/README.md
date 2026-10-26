# History Timeline (BruceWorx spike)

Python add-on that reads a PartDesign body's history and shows it as a Fusion-style bottom bar (BW-UIX-04). It began as the FCB-P1F-15 / FCB-P1F-16 spike; the full bar was built 26 Oct 2026 (marker drag with live rollback, playback buttons, the chip menu with rename, delete and reorder, error badges, hover highlight, sketches folded under their features). The C++ path for upstream is in [docs/CORE.md](docs/CORE.md).

BruceWorx ships it **on by default** (the BruceWorx rule: every feature is on, with an off switch). The normal build copies this folder into `<build>/Mod/HistoryTimeline` (what `pixi run freecad-release` loads) and installs it to `Mod/HistoryTimeline` (`CMakeLists.txt`, wired from `src/Mod/CMakeLists.txt`; build switch `-DBUILD_BW_HISTORY_TIMELINE=OFF`). The dock shows along the bottom of the window on start.

The preference is `User parameter:BaseApp/Preferences/Mod/HistoryTimeline/Enabled` (default `true`).

On stock FreeCAD (no BruceWorx build), copy or symlink this directory into `<user-app-data>/Mod/HistoryTimeline` (`FreeCAD.getUserAppDataDir()` prints that parent), or start with `freecad -M /path/to/HistoryTimeline`.

## Turn it off

Any one of these is enough:

* Uncheck **BruceWorx → History Timeline**, or close the dock (both write `Enabled = false`, so it stays off on later starts).
* Uncheck **History timeline** on **Preferences → BruceWorx**.
* Drop an empty file named `ADDON_DISABLED` in the installed `Mod/HistoryTimeline` directory.
* Start FreeCAD with `--disable-addon HistoryTimeline`.
* Configure the build with `-DBUILD_BW_HISTORY_TIMELINE=OFF`.

The **BruceWorx** menu is kept on the menu bar by `src/Mod/BruceWorx/bruceworx_menu.py`. FreeCAD clears the menu bar on every workbench activation (`MenuManager::setup`), so a menu added once from `InitGui.py` would vanish when the startup workbench loads.

## What the dock does

Order from `Body.Group`, rollback marker after `Body.Tip`.

* Drag the marker: `Body.Tip` follows it live (one undo step per drag, Escape cancels; bodies with more than
  40 entries set the tip at the drop). Click a solid feature: set the tip (same rule as **Set tip**).
* Playback buttons at the left: to start (tip cleared), back one, forward one, to end.
* Double-click: `setEdit` on that object. Hover: the feature is preselected in the 3D view.
* Right-click: Edit feature, Rename (inline), Suppress / Unsuppress, Roll history to here, Delete
  (`Std_Delete`, with FreeCAD's dependency question), Move earlier / Move later. A move is offered only when it
  is legal (`model.can_move`: no link crossing the wrong way, no subtractive feature first); the feature's own
  sketch travels with it and dress-ups are rebound to the same geometry on their new base. The reason for a
  disabled move is its tooltip.
* A feature that failed to recompute gets a red badge (the error is in the tooltip). A sketch used by one
  feature is folded under it; the disclosure at the left shows them (`Mod/HistoryTimeline/ShowSketches`).

For an assembly (the active one, else the first top-level assembly) the bar lists its components and joints in
the order they were created, sorted by `obj.ID`. No marker and no rollback: a click selects the object (in its
assembly, as the tree does), a double-click on a joint opens it for editing. The chip of the object being edited
is lit; a suppressed joint is struck through. The active body wins over an assembly.

The dock hides itself while the active document has nothing to show (no document, no body with features, no
assembly with components or joints) and comes back when it has. `Mod/HistoryTimeline/HideWhenEmpty` (default
`true`, **Preferences → BruceWorx**) keeps it visible with a short message instead.

## Headless test

From a source build:

```text
FreeCADCmd package/bruceworx/mods/HistoryTimeline/tests/test_timeline_access.py
```

From the conda package the binary is `freecadcmd`. Exit 0 means every check passed. The test does not load the GUI.

Assembly order and visibility (set `BW_CHECKLIST` to the checklist folder to also read copies of its files):

```text
FreeCADCmd package/bruceworx/mods/HistoryTimeline/tests/test_assembly_timeline.py
```

## Docs

* [Spike findings](docs/SPIKE.md) — what Python can and cannot do.
* [UX note](docs/UX.md) — the bar, the mocks, what the prototype actually does.
* [Questions for Bruce](docs/REVIEW.md) — FCB-P1F-17 (answered in the roadmap, chapters 92 and 97).
* [Path into core](docs/CORE.md) — what would become a C++ dock upstream, what stays Python.

Regenerate the mock PNGs with PySide6 and no FreeCAD:

```text
QT_QPA_PLATFORM=offscreen python docs/mock/render_mock.py
```

Optional in-app grab (needs a display or `xvfb-run`):

```text
xvfb-run -a freecad -M /path/to/HistoryTimeline tests/grab_prototype.py
```
