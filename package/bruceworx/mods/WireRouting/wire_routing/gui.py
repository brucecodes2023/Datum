# SPDX-License-Identifier: LGPL-2.1-or-later
"""Menu commands for wire routing. Thin: the work is in ``objects``.

* Add Wire Port: a port datum in the selected part, at the picked point,
  facing out of the picked face.
* Add Wire: first selected datum is the start port, last is the end port,
  any between are clips, in the order picked.
* Wire Cut List…: sweeps the joints, then writes the cut list as CSV.
"""

from __future__ import annotations

import os

from wire_routing import cutlist, objects

COMMANDS = []


def _qt():
    try:
        from PySide6 import QtGui, QtWidgets
    except ImportError:
        from PySide import QtGui

        QtWidgets = QtGui
    return QtGui, QtWidgets


def _warn(title, text):
    import FreeCAD
    import FreeCADGui

    _QtGui, QtWidgets = _qt()
    FreeCAD.Console.PrintError(f"BruceWorx {title}: {text}\n")
    QtWidgets.QMessageBox.warning(FreeCADGui.getMainWindow(), title, text)


def _active_assembly():
    try:
        import UtilsAssembly

        return UtilsAssembly.activeAssembly()
    except Exception:
        return None


def _selected_refs():
    """Selection, in pick order, as (object beside the wire, "path.")."""
    import FreeCADGui

    assembly = _active_assembly()
    refs = []
    for sel in FreeCADGui.Selection.getSelectionEx("", 0):
        for sub in sel.SubElementNames or [""]:
            if assembly is not None:
                import UtilsAssembly

                component, rest = UtilsAssembly.getComponentReference(assembly, sel.Object, sub)
                if component is not None:
                    refs.append((component, rest))
                    continue
            refs.append((sel.Object, sub))
    return refs


class AddPortCommand:
    TITLE = "Add Wire Port"

    def GetResources(self):
        return {
            "MenuText": "Add Wire Port",
            "ToolTip": (
                "Add a port datum to the selected part. Pick a face first to put the port at "
                "that point, facing out of the face. A wire leaves a port along its Z axis."
            ),
        }

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self, *_args):
        import FreeCAD
        import FreeCADGui

        doc = FreeCAD.ActiveDocument
        selection = FreeCADGui.Selection.getSelectionEx()
        container, placement = doc, None
        if selection:
            picked = selection[0]
            obj = picked.Object
            container = obj if obj.isDerivedFrom("App::Part") else (obj.getParentGeoFeatureGroup() or doc)
            if picked.PickedPoints:
                point = picked.PickedPoints[0]
                rotation = FreeCAD.Rotation()
                faces = [s for s in picked.SubObjects if s.ShapeType == "Face"]
                if faces:
                    u, v = faces[0].Surface.parameter(point)
                    rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), faces[0].normalAt(u, v))
                placement = FreeCAD.Placement(point, rotation)
                if container is not doc:
                    placement = container.getGlobalPlacement().inverse() * placement
        doc.openTransaction(self.TITLE)
        try:
            objects.make_port(container, "Port", placement)
        except Exception as exc:
            doc.abortTransaction()
            _warn(self.TITLE, str(exc))
            return
        doc.commitTransaction()
        doc.recompute()


class AddWireCommand:
    TITLE = "Add Wire"

    def GetResources(self):
        return {
            "MenuText": "Add Wire",
            "ToolTip": (
                "Route a wire between ports. Select the start port, any clips in order, then the "
                "end port (Ctrl+click). The wire follows its ports when a joint moves. A bend "
                "tighter than the wire's minimum bend radius is reported and no wire is drawn."
            ),
        }

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self, *_args):
        import FreeCAD

        doc = FreeCAD.ActiveDocument
        refs = _selected_refs()
        if len(refs) < 2:
            _warn(self.TITLE, "Select a start port and an end port (and any clips between them).")
            return
        container = _active_assembly() or doc
        doc.openTransaction(self.TITLE)
        try:
            wire = objects.make_wire(container, refs[0], refs[-1], refs[1:-1])
        except Exception as exc:
            doc.abortTransaction()
            _warn(self.TITLE, str(exc))
            return
        doc.commitTransaction()
        doc.recompute()
        if wire.Status != "ok":
            _warn(self.TITLE, wire.Status)


class CutListCommand:
    TITLE = "Wire Cut List"

    def GetResources(self):
        return {
            "MenuText": "Wire Cut List…",
            "ToolTip": (
                "Move every limited joint through its range, record each wire's shortest and "
                "longest path, and save the from-to cut list as CSV. Cut length is the longest "
                "path plus the wire's allowance."
            ),
        }

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self, *_args):
        import FreeCAD
        import FreeCADGui

        _QtGui, QtWidgets = _qt()
        doc = FreeCAD.ActiveDocument
        if not objects.wires(doc):
            _warn(self.TITLE, "This document has no wires.")
            return
        doc.openTransaction(self.TITLE)
        try:
            listing = objects.cut_list(doc)
        except Exception as exc:
            doc.abortTransaction()
            _warn(self.TITLE, str(exc))
            return
        doc.commitTransaction()
        start = os.path.splitext(doc.FileName)[0] + "-wires.csv" if doc.FileName else "wires.csv"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            FreeCADGui.getMainWindow(), self.TITLE, start, "CSV (*.csv)"
        )
        if path:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(cutlist.to_csv(listing["wires"]))
        lines = [f"{r['wire']}: {r['cut_mm']} mm  ({r['from']} → {r['to']})" if r["status"] == "ok"
                 else f"{r['wire']}: {r['status']}" for r in listing["wires"]]
        box = QtWidgets.QMessageBox.information if listing["ok"] else QtWidgets.QMessageBox.warning
        box(FreeCADGui.getMainWindow(), self.TITLE, "\n".join(lines[:30]))


COMMANDS = (
    ("BruceWorx_AddWirePort", AddPortCommand, "BruceWorxAddWirePortAction"),
    ("BruceWorx_AddWire", AddWireCommand, "BruceWorxAddWireAction"),
    ("BruceWorx_WireCutList", CutListCommand, "BruceWorxWireCutListAction"),
)


def install():
    import FreeCADGui

    for name, cls, _action in COMMANDS:
        FreeCADGui.addCommand(name, cls())
    try:
        import bruceworx_menu
    except ImportError:
        return  # Not a BruceWorx build: the commands exist, there is no menu.
    QtGui, _QtWidgets = _qt()
    window = FreeCADGui.getMainWindow()
    for name, cls, action_name in COMMANDS:
        resources = cls().GetResources()
        action = QtGui.QAction(resources["MenuText"], window)
        action.setToolTip(resources["ToolTip"])
        action.triggered.connect(lambda _checked=False, n=name: FreeCADGui.runCommand(n))
        bruceworx_menu.add_action(action, action_name)
