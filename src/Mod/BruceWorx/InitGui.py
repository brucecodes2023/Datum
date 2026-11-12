# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx GUI init. Loaded for every session.

BruceWorx rule: everything ships on by default, each with an off switch on
Preferences → BruceWorx. This file:

* applies the BruceWorx preference pack on first start (bruceworx_defaults),
* installs the preference page and the overlay model tree (default on),
* adds the Properties, Vault, Parameters and Physical tabs to the right panel
  (bruceworx_rightpanel),
* starts the ground grid, which shows only while the preference pack is applied,
* shades the faces in the Fusion-like studio look, coloured from their materials (bruceworx_studio),
* registers the Rectangular / Circular Pattern commands (bruceworx_pattern_gui),
* registers code parts: New Code Part, the script editor and the approval prompt (bruceworx_codecad_gui),
* registers the Inspect maps (Overhang, Draft, Wall Thickness, Curvature, Zebra) and Mass
  Properties (bruceworx_analysis_gui, MOD-11),
* registers Inspect > Interference for assemblies (bruceworx_interference_gui),
* registers the Routing tab: route paths, wires, pipes, hoses, bundles and their reports
  (bruceworx_routing_gui, ROU-01..03),
* creates the shared top-level BruceWorx menu (bruceworx_menu),
* keeps the toolbars on two rows (bruceworx_toolbars),
* adds the workspace menu and tabs, with their tool groups (bruceworx_workspaces),
* captions each workbench toolbar as a tool group (bruceworx_groups),
* adds the Cmd-K search field for commands, objects and settings (bruceworx_search),
* puts the document tabs in the macOS title bar (bruceworx_titlebar),
* keeps the sketch grid at the chosen strength (bruceworx_grid),
* follows the macOS appearance and accent colour (bruceworx_appearance),
* gives the floating task panel a dialog-style header (bruceworx_taskcard),
* draws the Browser header, part badges and feature summaries (bruceworx_browser).
* traces a picked face to its feature, marks failed features with an error
  chip and draws dependency lines in the Browser (bruceworx_picktrace).
* shows an undo toast after each command (bruceworx_toasts).
* edits Pad/Pocket distance, fillet radius and hole depth in the 3D view on a
  double-click (bruceworx_oncanvas).
* puts a property inspector in the Browser in place of the View/Data tabs
  (bruceworx_inspector).
* offers to close the gaps of a sketch a Pad or Pocket fails on (bruceworx_sketchrepair).
* puts the navigation pill on every 3D view (bruceworx_navpill).
* shows the BruceWorx page in the Start tab (bruceworx_start), kept as the first
  document tab (bruceworx_doctabs).
* registers Create Vault…, Import Files into Vault… and Vault Settings…, task
  dialogs for the folder vault (bruceworx_vaultwizard).
* brings the vault into the window: check out and in, the Vault panel, Browser
  badges, the save prompt (bruceworx_vaultclient).
* approve with the release checklist, reject, ECR and ECO (bruceworx_changes).
* the BOM per revision at check-in, Where Used..., Bill of Materials... (bruceworx_bom).
* the Vault tab's Structure view (bruceworx_structure) and New Project... (bruceworx_project), PDM-09.
* registers New Drawing and fills drawing title blocks from the part and vault
  (bruceworx_drawing).
* lets Claude Code drive FreeCAD after asking once per session (bruceworx_bridge).
* registers Insert Board… for KiCad boards (bruceworx_board_gui).
* registers Hole Preset, Cosmetic Thread, Hole Callouts, Insert Fastener and Mark as COTS
  (bruceworx_hardware, MOD-05).

It runs before the startup workbench is activated, so the first-run pack's
startup workbench (PartDesign) is already used on that first start.
"""


# Features whose build was stopped half-way (24 Oct 2026, the user paused the agents): not installed until
# their work resumes and passes its checks. Remove a name here to switch it back on.
_BW_PAUSED = ()


def _bw_warn(message):
    try:
        import FreeCAD

        FreeCAD.Console.PrintWarning(message + "\n")
    except Exception:
        print(message)


try:
    import bruceworx_defaults

    bruceworx_defaults.startup()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx defaults were not applied: {exc}")

import BruceWorx

BruceWorx.install()

try:
    import bruceworx_testwindow

    bruceworx_testwindow.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx test window placement was not installed: {exc}")

try:
    import bruceworx_rightpanel

    bruceworx_rightpanel.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx right panel tabs were not installed: {exc}")

try:
    import bruceworx_units

    bruceworx_units.install()  # PDM-09: new documents take the vault's units; readouts
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx units were not installed: {exc}")

try:
    import bruceworx_menu

    bruceworx_menu.get_menu()
    bruceworx_menu.install_preferences_entry()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx menu was not created: {exc}")

try:
    import ground_grid

    ground_grid.startup()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx ground grid was not started: {exc}")

try:
    import bruceworx_studio

    bruceworx_studio.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx studio shading was not installed: {exc}")

try:
    import bruceworx_pattern_gui

    bruceworx_pattern_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx pattern commands were not installed: {exc}")

try:
    import bruceworx_section

    bruceworx_section.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx section views were not installed: {exc}")

try:
    import bruceworx_analysis_gui

    bruceworx_analysis_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx inspect maps were not installed: {exc}")

try:
    import bruceworx_interference_gui

    bruceworx_interference_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx interference check was not installed: {exc}")

try:
    import bruceworx_massreport_gui

    bruceworx_massreport_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx mass report and drone balance were not installed: {exc}")

try:
    import bruceworx_routing_gui

    bruceworx_routing_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx routing commands were not installed: {exc}")

try:
    import bruceworx_rope

    bruceworx_rope.install_gui()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx gravity switch was not installed: {exc}")

try:
    import bruceworx_toolbars

    bruceworx_toolbars.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx toolbar layout was not installed: {exc}")

try:
    import bruceworx_workspaces

    bruceworx_workspaces.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx workspaces were not installed: {exc}")

try:
    import bruceworx_groups

    bruceworx_groups.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx tool group captions were not installed: {exc}")

try:
    import bruceworx_search

    bruceworx_search.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx search was not installed: {exc}")

try:
    import bruceworx_titlebar

    bruceworx_titlebar.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx title bar tabs were not installed: {exc}")

try:
    import bruceworx_grid

    bruceworx_grid.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx sketch grid strength was not installed: {exc}")

try:
    import bruceworx_appearance

    bruceworx_appearance.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx could not follow the macOS appearance: {exc}")

try:
    import bruceworx_taskcard

    bruceworx_taskcard.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx task card header was not installed: {exc}")

try:
    import bruceworx_navpill

    bruceworx_navpill.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx navigation pill was not installed: {exc}")

try:
    import bruceworx_browser

    bruceworx_browser.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Browser extras were not installed: {exc}")

try:
    import bruceworx_picktrace

    bruceworx_picktrace.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx pick-to-feature and error chips were not installed: {exc}")

try:
    import bruceworx_selectfilter

    bruceworx_selectfilter.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx selection filter was not installed: {exc}")

try:
    import bruceworx_fastopen

    bruceworx_fastopen.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Fast Open was not installed: {exc}")

try:
    import bruceworx_autoactivate

    bruceworx_autoactivate.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx assembly auto-activate was not installed: {exc}")

try:
    import bruceworx_inspector

    bruceworx_inspector.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx property inspector was not installed: {exc}")

try:
    if "bruceworx_physical" not in _BW_PAUSED:
        import bruceworx_physical
        import bruceworx_parameters

        bruceworx_physical.install()
        bruceworx_parameters.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Physical and Parameters tabs were not installed: {exc}")

try:
    import bruceworx_configs_gui

    bruceworx_configs_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx configurations were not installed: {exc}")

try:
    import bruceworx_toasts

    bruceworx_toasts.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx undo toasts were not installed: {exc}")

try:
    if "bruceworx_oncanvas" not in _BW_PAUSED:
        import bruceworx_oncanvas

        bruceworx_oncanvas.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx on-canvas editing was not installed: {exc}")

try:
    import bruceworx_sketchrepair

    bruceworx_sketchrepair.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx sketch profile repair was not installed: {exc}")

try:
    import bruceworx_statusbar

    bruceworx_statusbar.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx status-bar readouts were not installed: {exc}")

try:
    import bruceworx_doctabs

    bruceworx_doctabs.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Start tab pinning was not installed: {exc}")

try:
    import bruceworx_start

    bruceworx_start.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Start page was not installed: {exc}")

try:
    import bruceworx_vaultwizard

    bruceworx_vaultwizard.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Create Vault command was not installed: {exc}")

try:
    import bruceworx_vaultclient

    bruceworx_vaultclient.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx vault client was not installed: {exc}")

try:
    import bruceworx_changes

    bruceworx_changes.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx review and change control were not installed: {exc}")

try:
    if "bruceworx_bom" not in _BW_PAUSED:
        import bruceworx_bom

        bruceworx_bom.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx BOM and where-used were not installed: {exc}")

try:
    import bruceworx_structure

    bruceworx_structure.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Structure view was not installed: {exc}")

try:
    import bruceworx_project

    bruceworx_project.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx New Project was not installed: {exc}")

try:
    import bruceworx_drawing

    bruceworx_drawing.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx New Drawing and title-block filling were not installed: {exc}")

try:
    import bruceworx_bridge

    bruceworx_bridge.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Claude Code bridge was not installed: {exc}")

try:
    import bruceworx_board_gui

    bruceworx_board_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx Insert Board was not installed: {exc}")

try:
    if "bruceworx_sheetmetal" not in _BW_PAUSED:
        import bruceworx_sheetmetal

        bruceworx_sheetmetal.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx sheet-metal rules and Export Flat were not installed: {exc}")

try:
    import bruceworx_hardware

    bruceworx_hardware.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx hole presets, cosmetic threads and fasteners were not installed: {exc}")

try:
    import bruceworx_motion

    bruceworx_motion.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx motion commands (ROB-05) were not installed: {exc}")

try:
    import bruceworx_sim_gui

    bruceworx_sim_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx simulation studies were not installed: {exc}")

try:
    import bruceworx_sim_workspace

    bruceworx_sim_workspace.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx simulation window shell (SIM-07) was not installed: {exc}")

try:
    import bruceworx_asm_panes

    bruceworx_asm_panes.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx assembly Model Builder (ASM-UI-01) was not installed: {exc}")

try:
    import bruceworx_validation_gui

    bruceworx_validation_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx validation command (SIM-05) was not installed: {exc}")

try:
    import bruceworx_codecad_gui

    bruceworx_codecad_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx code parts (AUT-05) were not installed: {exc}")
