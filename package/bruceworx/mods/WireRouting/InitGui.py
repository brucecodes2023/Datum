# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI init: adds the wire routing commands to the BruceWorx menu (default on).

FreeCAD execs this file. The module directory is already on sys.path.
"""


def _bw_wire_gui():
    import FreeCAD

    from wire_routing import objects

    if not objects.enabled():
        FreeCAD.Console.PrintLog("BruceWorx wire routing is off (WireRouting=false)\n")
        return
    from wire_routing import gui

    gui.install()


try:
    _bw_wire_gui()
except Exception as exc:  # pragma: no cover - FreeCAD console only
    try:
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx wire routing GUI was not loaded: {exc}\n")
    except Exception:
        print(f"BruceWorx wire routing GUI was not loaded: {exc}")
