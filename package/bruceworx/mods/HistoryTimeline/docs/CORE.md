# Path into core (BW-UIX-04)

FreeCAD has an open feature request for a Fusion-style history timeline. This add-on is the
Python prototype of one. This note says which parts belong in `src/Gui` as a C++ dock for an
upstream proposal, which parts must stay Python (or stay in this fork), and the preference keys
the two would share. No C++ is written in this epic.

## What the add-on does today

* `history_timeline/model.py` reads a `PartDesign::Body`: `Group` is the row, `Tip` the marker,
  `Suppressed` the slash, `isValid()` / `State` the red badge. It sets the tip (same rule as
  `PartDesign_MoveTip`), toggles `Suppressed`, deletes, renames, steps the tip (playback) and
  reorders through `Body.removeObject` + `Body.insertObject(after=True)`, the same calls
  `PartDesign_MoveFeatureInTree` issues. Before a move it checks dependencies (`can_move`); after
  it, it rebinds dress-ups to the same geometry on their new base (`snapshot_dressups`,
  `rebind_dressups`) because the body's relink keeps the old mapped element names.
* `history_timeline/assembly.py` lists an assembly's components and joints in creation order
  (`obj.ID`).
* `history_timeline/bar.py` is the widget: chips, marker drag, playback strip, disclosure,
  inline rename. It has no FreeCAD import and renders under plain PySide (the mocks).
* `history_timeline/dock.py` is the glue: which body or assembly to show, document observer,
  transactions, preselection, preferences, the menu item and the off switches.

## What would move into `src/Gui` (C++)

A `Gui::HistoryTimeline` dock, built like `Gui::TreeWidget` / `Gui::DockWnd::ReportOutput`, with
one `Gui::HistoryTimelineProvider` interface that a module implements for its own history:

| Piece | Why C++ | Where |
|---|---|---|
| The dock, the one-row chip strip, marker drag, playback strip, disclosure, inline rename | Plain Qt widget code; belongs with the other standard docks so themes, `MainWindow` state and the View menu know it. | `src/Gui/HistoryTimeline.{h,cpp}`, registered in `DockWindowManager` next to the Report view and the Tree. |
| `HistoryTimelineProvider` (abstract): `entries()`, `tip()`, `setTip()`, `canMove()`, `move()`, `isSuppressed()`, `setSuppressed()`, `errorText()`, `fold()` | Lets PartDesign, Assembly and later modules own their semantics without the dock knowing `Body` or `AssemblyObject`. | `src/Gui/HistoryTimelineProvider.h`; the PartDesign provider in `src/Mod/PartDesign/Gui`, the Assembly one in `src/Mod/Assembly/Gui`. |
| PartDesign provider: `isSolidFeature`, `getPrevSolidFeature`, `isMultiTransformChild`, the dress-up rebind | These are already C++ in `PartDesign::Body` and `PartDesign::Transformed`; Python has to re-implement them (and cannot see `isMultiTransformChild`). The rebind belongs next to `DressUp::onChanged`, which today copies the stale mapped names when `BaseFeature` changes. | `src/Mod/PartDesign/Gui/HistoryTimelineProvider.cpp`; the rebind as a fix in `src/Mod/PartDesign/App/FeatureDressUp.cpp`. |
| Preselection on hover, selection on click, `setEdit` on double-click | Same calls the Tree makes; C++ avoids the Python round trip on every mouse move. | Inside the dock. |
| "Set tip" visibility rule (show only the tip feature) | Today the add-on hides and shows features itself; `CmdPartDesignMoveTip` does it with `FCMD_OBJ_SHOW`. One place should own it. | PartDesign provider. |
| Icons: feature icons from the view providers, playback glyphs from the icon theme | In C++ the dock asks `ViewProvider::getIcon()` directly and the playback glyphs go into `src/Gui/Icons` with light and dark variants, as the add-on ships them in `history_timeline/icons/`. | `src/Gui/Icons/`. |

Behaviour that should be kept exactly as built here, since Bruce approved it (`92-ui-design.tex`):
one row, no dock title, 20 px chips in theme colours, rolled-back items dimmed, hidden when there is
no history, no playback and no marker for assemblies, the active body wins over the assembly.

## What stays Python

* The BruceWorx menu item, the preference page wiring and the off switches (BruceWorx rule:
  on by default, off switch). Upstream would use the View → Panels menu instead.
* The pick-trace ring (`bruceworx_picktrace.set_traced`) and anything else BruceWorx-only
  that draws on the chips.
* The headless tests (`tests/test_timeline_access.py`, `tests/test_assembly_timeline.py`): they
  prove the App-level facts (what `Tip`, `Group`, `removeObject`, `insertObject` do) and stay
  valid against a C++ dock. The widget tests move to the C++ test suite.
* The assembly order (`assembly.py`): `Assembly` is itself mostly Python; its provider can stay
  Python through the provider interface if that interface is exposed to Python (as
  `Gui.addDockWindow` already is in this fork).

## Preference keys (shared)

`User parameter:BaseApp/Preferences/Mod/HistoryTimeline`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `Enabled` | bool | true | Dock shown (menu item, preference page and closing the dock all write it). |
| `HideWhenEmpty` | bool | true | Hide the dock while the active document has no history. |
| `ShowSketches` | bool | false | Sketches folded under their features are shown (disclosure state). |

Upstream would move these under `BaseApp/Preferences/View` or a `HistoryTimeline` group of their
own; the names can stay. `LIVE_PREVIEW_MAX` (40 features: above it the marker drag sets the tip at
the drop instead of live) is a constant today and would become a key only if real bodies need it.

## Open points for the upstream proposal

1. The marker is `Body.Tip`: features after it still recompute (they are not suppressed). Fusion
   does not compute rolled-back features. Keeping `Tip` means the file stays compatible with stock
   FreeCAD, which is the BruceWorx rule; upstream may want a real "rolled back" state on the body.
2. Reorder legality is decided from link properties (`hard_links`) plus the "a subtractive feature
   cannot be first" rule, and verified by recompute. `isFeatureMovable` in `PartDesignGui/Utils.cpp`
   answers a different question (can it leave the body) and is not usable here.
3. Patterns inside a MultiTransform look like solids to Python (`isMultiTransformChild` is C++
   only); the C++ provider would hide them.
