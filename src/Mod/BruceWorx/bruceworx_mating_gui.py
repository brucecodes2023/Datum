# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bolt Up (ASM-09) and Infer Mates (ASM-11): the commands over bruceworx_mating.

Bolt Up: select a component in the active assembly (its face, or it in the tree); every free hole line through it
gets a screw (and a nut where there is no thread), each from the vault as a COTS item and jointed on its seat.
Infer Mates: joints for the active assembly from the geometry, every part kept where it is. Each is one undo step;
the result is written to the report view and shown in a message.

Off switch: Preferences/BruceWorx/MatingCommands (default on).
"""

from __future__ import annotations

import os

import bruceworx_mating as bm

HERE = os.path.dirname(os.path.abspath(__file__))
COMMANDS = ("BruceWorx_BoltUp", "BruceWorx_InferMates")


def _app():
    import FreeCAD

    return FreeCAD


def _transaction(doc, name, fn):
    doc.openTransaction(name)
    try:
        out = fn()
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return out


def run_bolt(assembly, target, session=None):
    return _transaction(assembly.Document, "Bolt Up", lambda: bm.bolt(assembly, target, session))


def run_infer(assembly, ground=None):
    return _transaction(assembly.Document, "Infer Mates", lambda: bm.infer(assembly, ground))


def _message(title, text, warn=False):
    App = _app()
    App.Console.PrintMessage(f"{title}:\n{text}\n")
    if not App.GuiUp:
        return
    import FreeCADGui as Gui
    from PySide import QtGui

    box = QtGui.QMessageBox.warning if warn else QtGui.QMessageBox.information
    box(Gui.getMainWindow(), title, text)


def _active_assembly():
    import bruceworx_hardware as bh

    return bh._active_assembly()


def _selected_component(assembly):
    """The assembly component the selection is in (a face of it, or it in the tree)."""
    import FreeCADGui as Gui

    comps = bm.components(assembly)
    for sel in Gui.Selection.getSelectionEx("", 0):
        for sub in (list(sel.SubElementNames) or [""]):
            if sel.Object is assembly and sub:
                name = sub.split(".", 1)[0]
                found = next((c for c in comps if c.Name == name), None)
                if found is not None:
                    return found
            if sel.Object in comps:
                return sel.Object
            for comp in comps:
                if sub.startswith(comp.Name + "."):
                    return comp
    return None


class _BoltUpCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_BoltUp.svg"), "MenuText": "Bolt Up",
                "ToolTip": "Select a part in the assembly: every free hole through it gets a screw of the size the "
                           "clearance hole is for (ISO 273), as long as the thread allows (and a nut where there is "
                           "no thread), each jointed on its seat. One undo step."}

    def IsActive(self):
        return bm.enabled() and _app().ActiveDocument is not None

    def Activated(self):
        import bruceworx_hardware as bh

        if not bh.addon_available():
            _message("Bolt Up", "The Fasteners add-on is not available.", True)
            return
        s = bh._session()
        if s is None:
            _message("Bolt Up", "Open a vault first: each screw is a COTS item of the vault (as Insert Fastener).",
                     True)
            return
        assembly = _active_assembly()
        if assembly is None:
            _message("Bolt Up", "Open (or activate) the assembly.", True)
            return
        target = _selected_component(assembly)
        if target is None:
            _message("Bolt Up", "Select the part to bolt (a face of it, or it in the tree).", True)
            return
        try:
            report = run_bolt(assembly, target, s)
        except Exception as exc:  # noqa: BLE001
            _message("Bolt Up", str(exc), True)
            return
        try:
            import bruceworx_vaultclient as vc

            vc.changed(refresh=True)
        except Exception:  # noqa: BLE001
            pass
        _message("Bolt Up", report.text(), bool(report.moved))


class _InferMatesCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_InferMates.svg"), "MenuText": "Infer Mates",
                "ToolTip": "Joints for the active assembly from its geometry: touching faces coincident, shafts and "
                           "pins concentric, screws and nuts on their seats. Nothing moves (each joint is checked); "
                           "parts left unmated are listed with the reason. One undo step."}

    def IsActive(self):
        return bm.enabled() and _app().ActiveDocument is not None

    def Activated(self):
        assembly = _active_assembly()
        if assembly is None:
            _message("Infer Mates", "Open (or activate) the assembly.", True)
            return
        try:
            report = run_infer(assembly, _selected_component(assembly))
        except Exception as exc:  # noqa: BLE001
            _message("Infer Mates", str(exc), True)
            return
        _message("Infer Mates", report.text(), bool(report.moved))


def install():
    import FreeCADGui as Gui

    if not bm.enabled():
        return
    for name, cmd in zip(COMMANDS, (_BoltUpCommand(), _InferMatesCommand())):
        if name not in Gui.listCommands():
            Gui.addCommand(name, cmd)
