# SPDX-License-Identifier: LGPL-2.1-or-later
"""Insert Board… command (ECD-02): a KiCad board into the active assembly.

Design > Solid > Assemble (the group's More list) and the BruceWorx menu.
Picks a ``.kicad_pcb``, then :func:`bruceworx_board.insert_board` does the
work. Target: the active assembly; else the active document's only
assembly; else a new document named by the board.

Off switch: Preferences > BruceWorx > "KiCad boards"
(``BaseApp/Preferences/BruceWorx/KiCadBoards``).
"""

import os

import FreeCAD as App

import bruceworx_board as bb

COMMAND = "BruceWorx_InsertBoard"
LAST_DIR_KEY = "KiCadLastDir"
_state = {"installed": False}


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui

        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def target_assembly(doc=None):
    """The assembly to insert into: the active one, else the document's only one."""
    try:
        import UtilsAssembly

        active = UtilsAssembly.activeAssembly()
        if active is not None:
            return active
    except Exception:
        pass
    doc = doc or App.ActiveDocument
    if doc is None:
        return None
    found = [o for o in doc.Objects if o.isDerivedFrom("Assembly::AssemblyObject")]
    return found[0] if len(found) == 1 else None


def summary(report):
    """One line for the status bar and Report view."""
    parts = ", ".join(f"{name} {grams:.2f} g" for name, grams in sorted(report["by_material"].items()))
    text = (f"Board {report['width']:g} x {report['height']:g} mm, {report['total_g']:.2f} g ({parts}); "
            f"{report['frames']} coordinate systems")
    if report.get("missing_models"):
        text += f"; {len(report['missing_models'])} 3D models not found"
    return text


class _InsertBoardCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(bb.HERE, COMMAND + ".svg"),
            "MenuText": "Insert Board…",
            "ToolTip": (
                "Insert a KiCad board (.kicad_pcb) as one part: exported to STEP by KiCad, with FR-4, "
                "copper and component materials, its mass, and coordinate systems at the board origin "
                "and at each mounting hole for joints."
            ),
        }

    def IsActive(self):
        return bb.enabled()

    def Activated(self):
        import FreeCADGui as Gui

        _QtCore, _QtGui, QtWidgets = _qt()
        window = Gui.getMainWindow()
        prefs = App.ParamGet(bb.PREFS_PATH)
        start = prefs.GetString(LAST_DIR_KEY, os.path.expanduser("~"))
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window, "Insert Board", start, "KiCad boards (*.kicad_pcb)")
        if not path:
            return
        prefs.SetString(LAST_DIR_KEY, os.path.dirname(path))
        run(path)


def run(path, assembly=None):
    """Insert ``path`` with a wait cursor; errors go to a message box."""
    import FreeCADGui as Gui

    _QtCore, QtGui, QtWidgets = _qt()
    assembly = assembly or target_assembly()
    QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(_QtCore.Qt.WaitCursor))
    try:
        part = bb.insert_board(path, assembly=assembly)
    except Exception as exc:
        QtWidgets.QApplication.restoreOverrideCursor()
        App.Console.PrintError(f"Insert Board: {exc}\n")
        QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Insert Board", str(exc))
        return None
    QtWidgets.QApplication.restoreOverrideCursor()
    text = summary(bb.last_report)
    App.Console.PrintMessage(f"Insert Board: {part.Label}: {text}\n")
    try:
        Gui.getMainWindow().statusBar().showMessage(f"{part.Label}: {text}", 15000)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(part)
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return part


def install():
    """Register the command and its BruceWorx menu entry (the Assemble group
    lists it in workspaces.json)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand(COMMAND, _InsertBoardCommand())
    _state["installed"] = True
    try:
        import bruceworx_menu

        _QtCore, QtGui, _QtWidgets = _qt()
        action = QtGui.QAction(QtGui.QIcon(os.path.join(bb.HERE, COMMAND + ".svg")), "Insert Board…",
                               Gui.getMainWindow())
        action.triggered.connect(lambda _checked=False: Gui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, f"BruceWorxMenu_{COMMAND}")
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx Insert Board: menu entry not added: {exc}\n")
