# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx GUI init. Loaded for every session.

BruceWorx rule: everything ships on by default, each with an off switch on
Preferences → BruceWorx. This file:

* applies the BruceWorx preference pack on first start (bruceworx_defaults),
* installs the preference page and the overlay model tree (default on),
* starts the ground grid, which shows only while the preference pack is applied,
* registers the Rectangular / Circular Pattern commands (bruceworx_pattern_gui),
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
* puts the navigation pill on every 3D view (bruceworx_navpill).
* shows the BruceWorx page in the Start tab (bruceworx_start).
* registers Create Vault…, Import Files into Vault… and Vault Settings…, task
  dialogs for the folder vault (bruceworx_vaultwizard).

It runs before the startup workbench is activated, so the first-run pack's
startup workbench (PartDesign) is already used on that first start.
"""


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
    import bruceworx_pattern_gui

    bruceworx_pattern_gui.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx pattern commands were not installed: {exc}")

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
    import bruceworx_statusbar

    bruceworx_statusbar.install()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    _bw_warn(f"BruceWorx status-bar readouts were not installed: {exc}")

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
