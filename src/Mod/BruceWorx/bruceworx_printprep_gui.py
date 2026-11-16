# SPDX-License-Identifier: LGPL-2.1-or-later
"""3D-print prep in the window (roadmap MFG-01): bruceworx_printprep has the how.

Commands:
* Place on Bed       select a flat face: the part prints resting on it (stored on the part as PrintUp,
                     one undo step) and the overhang map opens in that direction.
* Print Prep         a small window for the selected parts: the orientation, a "best orientation" button
                     (least area needing support), the size against the printer's bed, the support and
                     bed-contact areas, the slicer to use, Export 3MF and Send to Slicer.
* Export 3MF         the selected parts as one plate, in print orientation, on z = 0.
* Send to Slicer     the same file, opened in the slicer.

Off switch: Preferences > BruceWorx > "3D-print prep" (PrintPrep); the commands are then inactive.
"""

from __future__ import annotations

import os
import tempfile

import bruceworx_printprep as pp

HERE = os.path.dirname(os.path.abspath(__file__))
COMMANDS = {
    "BruceWorx_PrintPrep": ("Print Prep", "BruceWorx_PrintPrep.svg",
                            "Print orientation, size against the bed, support area, 3MF export and slicer for "
                            "the selected parts."),
    "BruceWorx_PlaceOnBed": ("Place on Bed", "BruceWorx_PlaceOnBed.svg",
                             "Select a flat face: the part prints resting on it. The overhang map opens in that "
                             "direction."),
    "BruceWorx_Export3MF": ("Export 3MF", "BruceWorx_Export3MF.svg",
                            "The selected parts as one 3MF plate, each in its print orientation, on the bed."),
    "BruceWorx_SendToSlicer": ("Send to Slicer", "BruceWorx_SendToSlicer.svg",
                               "Export the selected parts as a 3MF plate and open it in the slicer."),
}
_state = {"dialog": None, "last_file": None, "message": None}


def _message(text):
    """A sentence for the user (and for tests): the status bar and the Report view."""
    import FreeCAD

    _state["message"] = text
    FreeCAD.Console.PrintMessage(f"Print prep: {text}\n")
    try:
        import FreeCADGui as Gui

        Gui.getMainWindow().statusBar().showMessage(text, 8000)
    except Exception:  # noqa: BLE001
        pass


# ----- what is selected -----------------------------------------------------------------------------


def _printable(obj):
    shape = getattr(obj, "Shape", None)
    try:
        return shape is not None and not shape.isNull() and bool(shape.Solids)
    except Exception:  # noqa: BLE001
        return False


def parts(doc=None):
    """The parts to work on: the selected ones, else every visible body or loose solid of the document."""
    import FreeCAD
    import FreeCADGui as Gui

    out = []

    def add(obj):
        part = pp.part_of(obj)
        if part is not None and _printable(part) and part not in out:
            out.append(part)

    for sel in Gui.Selection.getSelectionEx("", 0):
        subs = [s for s in sel.SubElementNames if s] or [""]
        for sub in subs:
            try:
                owner = sel.Object.getSubObject(sub, retType=1) if sub else sel.Object
            except Exception:  # noqa: BLE001
                owner = sel.Object
            add(owner or sel.Object)
    if out:
        return out
    doc = doc or FreeCAD.ActiveDocument
    for obj in getattr(doc, "Objects", []) or []:
        if not getattr(obj, "Visibility", True):
            continue
        if obj.isDerivedFrom("PartDesign::Body"):
            add(obj)
        elif obj.isDerivedFrom("Part::Feature") and not obj.isDerivedFrom("PartDesign::Feature") \
                and not obj.isDerivedFrom("Part::Part2DObject") and not obj.InList:
            add(obj)
    return out


def selected_face():
    """(part, face label, outward normal in the document frame) of the selected flat face, or None."""
    import FreeCADGui as Gui

    for sel in Gui.Selection.getSelectionEx("", 0):
        for sub in sel.SubElementNames:
            element = sub.split(".")[-1]
            if not element.startswith("Face"):
                continue
            try:
                face = sel.Object.getSubObject(sub)
                owner = sel.Object.getSubObject(sub, retType=1)
                if type(face.Surface).__name__ != "Plane":
                    continue
                u0, u1, v0, v1 = face.ParameterRange
                normal = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            except Exception:  # noqa: BLE001
                continue
            part = pp.part_of(owner or sel.Object)
            if part is not None and _printable(part):
                return part, f"{element} of {part.Label}", normal
    return None


# ----- the actions ----------------------------------------------------------------------------------


def show_overhang(part):
    """Open (or turn) the overhang map to the part's print direction. Returns the Inspect session or None."""
    try:
        import bruceworx_analysis_gui as bag
    except Exception:  # noqa: BLE001
        return None
    session = bag.session()
    if session is None:
        session = bag.show("overhang", [part])
    if session is None:
        return None
    up = pp.up_global(part)
    session.mode = "overhang"
    session.vector["overhang"] = (up.x, up.y, up.z)
    session.vector_label["overhang"] = "Print orientation"
    panel = bag._state.get("panel")
    try:
        combo = panel.widgets["overhang"]["direction"]
        index = combo.findData("face")
        combo.blockSignals(True)  # its own handler would read the direction from the selection again
        combo.setItemText(index, "Print orientation")
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        panel.widgets["overhang"]["hint"].setVisible(False)
        panel.changed()
    except Exception:  # noqa: BLE001 -- the panel changed shape: the session still has the direction
        session.params["overhang"]["direction"] = "face"
    return session


def place_on_bed(show_map=True):
    """The part of the selected flat face prints resting on it. Returns the part or None."""
    picked = selected_face()
    if picked is None:
        _message("Select a flat face of the part first: the face that goes on the bed.")
        return None
    part, label, normal = picked
    doc = part.Document
    doc.openTransaction("Place on bed")
    try:
        pp.place_on_face(part, normal)
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        _message(str(exc))
        return None
    _message(f"{part.Label} prints on {label}.")
    if show_map:
        show_overhang(part)
    refresh()
    return part


def use_best(part):
    """Set the orientation that needs the least support. Returns the Candidate."""
    best = pp.suggest(part)
    doc = part.Document
    doc.openTransaction("Print orientation")
    try:
        pp.set_up(part, best.up)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    _message(f"{part.Label}: {best.label}, {best.support_area:.0f} mm² needs support.")
    return best


def reset(part):
    doc = part.Document
    doc.openTransaction("Reset print orientation")
    pp.clear_up(part)
    doc.commitTransaction()


def default_path(objects):
    """Where Send to Slicer writes: beside the saved document, else the temporary folder."""
    doc = objects[0].Document
    name = (objects[0].Label if len(objects) == 1 else doc.Label) or "plate"
    name = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip() or "plate"
    folder = os.path.dirname(doc.FileName) if doc.FileName else tempfile.gettempdir()
    return os.path.join(folder, name + ".3mf")


def export_3mf(path=None, objects=None):
    """Write the plate; asks where when ``path`` is None. Returns the path or None."""
    import FreeCADGui as Gui

    objects = objects or parts()
    if not objects:
        _message("Nothing to print: select a part, a body or a solid.")
        return None
    if path is None:
        from PySide import QtWidgets

        path, _filter = QtWidgets.QFileDialog.getSaveFileName(Gui.getMainWindow(), "Export 3MF",
                                                              default_path(objects), "3MF (*.3mf)")
        if not path:
            return None
    try:
        report = pp.write_3mf(path, objects)
    except pp.PrintPrepError as exc:
        _message(str(exc))
        return None
    _state["last_file"] = path
    open_meshes = [r["label"] for r in report if not r["watertight"]]
    note = f" Not watertight: {', '.join(open_meshes)}." if open_meshes else ""
    _message(f"Wrote {len(report)} part{'s' if len(report) != 1 else ''} to {path}.{note}")
    return path


def send_to_slicer(objects=None, run=None):
    """Export beside the document and open the file in the slicer. Returns (path, slicer) or None."""
    objects = objects or parts()
    if not objects:
        _message("Nothing to print: select a part, a body or a solid.")
        return None
    app = pp.slicer()
    if app is None:
        _message("No slicer was found in Applications. Use Export 3MF and open the file by hand.")
        return None
    path = export_3mf(default_path(objects), objects)
    if path is None:
        return None
    pp.send(path, app, run)
    _message(f"Opened {os.path.basename(path)} in {app['name']}.")
    return path, app


# ----- the Print Prep window ------------------------------------------------------------------------


def _dialog_class():
    from PySide import QtCore, QtWidgets

    class PrintPrepDialog(QtWidgets.QDialog):
        """Not a task panel, so it stays open beside the overhang map's."""

        def __init__(self, objects, parent=None):
            super().__init__(parent)
            self.objects = list(objects)
            self.setObjectName("BruceWorxPrintPrep")
            self.setWindowTitle("Print Prep")
            self.setWindowFlag(QtCore.Qt.Tool, True)
            self.setMinimumWidth(420)
            layout = QtWidgets.QVBoxLayout(self)
            self.text = QtWidgets.QLabel(self)
            self.text.setObjectName("BruceWorxPrintPrepSummary")
            self.text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.text.setWordWrap(True)
            layout.addWidget(self.text)

            row = QtWidgets.QHBoxLayout()
            self.place = QtWidgets.QPushButton("Place on Selected Face", self)
            self.place.setToolTip("Select a flat face in the 3D view first: the part prints resting on it.")
            self.best = QtWidgets.QPushButton("Best Orientation", self)
            self.best.setToolTip("Try the part's flat faces and the six axis directions; keep the one with the "
                                 "least area that needs support.")
            self.reset = QtWidgets.QPushButton("Reset", self)
            self.map = QtWidgets.QPushButton("Overhang Map", self)
            for button in (self.place, self.best, self.reset, self.map):
                row.addWidget(button)
            layout.addLayout(row)

            self.walls = QtWidgets.QCheckBox("Also measure the thinnest wall (slower)", self)
            layout.addWidget(self.walls)

            form = QtWidgets.QFormLayout()
            self.slicer = QtWidgets.QComboBox(self)
            self.slicer.setObjectName("BruceWorxPrintPrepSlicer")
            self.found = pp.slicers()
            chosen = pp.slicer(self.found)
            for app in self.found:
                self.slicer.addItem(f"{app['name']} {app['version']}".strip(), app["path"])
            if not self.found:
                self.slicer.addItem("None found in Applications", "")
                self.slicer.setEnabled(False)
            elif chosen is not None:
                self.slicer.setCurrentIndex(self.slicer.findData(chosen["path"]))
            form.addRow("Slicer", self.slicer)
            layout.addLayout(form)

            row = QtWidgets.QHBoxLayout()
            self.export = QtWidgets.QPushButton("Export 3MF…", self)
            self.send = QtWidgets.QPushButton("Send to Slicer", self)
            self.send.setDefault(True)
            self.send.setEnabled(bool(self.found))
            close = QtWidgets.QPushButton("Close", self)
            row.addWidget(self.export)
            row.addWidget(self.send)
            row.addStretch(1)
            row.addWidget(close)
            layout.addLayout(row)

            self.place.clicked.connect(lambda _c=False: self.on_place())
            self.best.clicked.connect(lambda _c=False: self.on_best())
            self.reset.clicked.connect(lambda _c=False: self.on_reset())
            self.map.clicked.connect(lambda _c=False: self.on_map())
            self.walls.toggled.connect(lambda _on: self.refresh())
            self.slicer.currentIndexChanged.connect(lambda _i: pp.set_slicer(self.slicer.currentData()))
            self.export.clicked.connect(lambda _c=False: export_3mf(None, self.alive()))
            self.send.clicked.connect(lambda _c=False: send_to_slicer(self.alive()))
            close.clicked.connect(lambda _c=False: self.close())
            self.refresh()

        def alive(self):
            out = []
            for obj in self.objects:
                try:
                    if obj.Document is not None and obj.Document.getObject(obj.Name) is obj:
                        out.append(obj)
                except Exception:  # noqa: BLE001 -- deleted
                    pass
            self.objects = out
            return out

        def refresh(self):
            objects = self.alive()
            if not objects:
                self.text.setText("No part: select a part, a body or a solid and open Print Prep again.")
                return
            blocks = []
            for obj in objects:
                try:
                    blocks.append(pp.summary_text(pp.summary(obj, walls=self.walls.isChecked())))
                except Exception as exc:  # noqa: BLE001
                    blocks.append(f"{obj.Label}: {exc}")
            self.text.setText("\n\n".join(blocks))

        def on_place(self):
            part = place_on_bed()
            if part is not None and part not in self.objects:
                self.objects.append(part)
            self.refresh()

        def on_best(self):
            for obj in self.alive():
                try:
                    use_best(obj)
                except Exception as exc:  # noqa: BLE001
                    _message(f"{obj.Label}: {exc}")
            objects = self.alive()
            if objects:
                import bruceworx_analysis_gui as bag

                if bag.session() is not None:
                    show_overhang(objects[0])
            self.refresh()

        def on_reset(self):
            for obj in self.alive():
                reset(obj)
            self.refresh()

        def on_map(self):
            objects = self.alive()
            if objects:
                show_overhang(objects[0])

        def closeEvent(self, event):
            _state["dialog"] = None
            super().closeEvent(event)

    return PrintPrepDialog


def refresh():
    dialog = _state["dialog"]
    if dialog is not None:
        dialog.refresh()


def show(objects=None):
    """Open the Print Prep window for ``objects`` (default: the selection, else every visible part)."""
    import FreeCADGui as Gui

    objects = objects or parts()
    if not objects:
        _message("Nothing to print: select a part, a body or a solid.")
        return None
    old = _state["dialog"]
    if old is not None:
        old.close()
    dialog = _state["dialog"] = _dialog_class()(objects, Gui.getMainWindow())
    dialog.show()
    return dialog


# ----- commands -------------------------------------------------------------------------------------


class _Command:
    def __init__(self, name):
        self.name = name

    def GetResources(self):
        text, icon, tip = COMMANDS[self.name]
        return {"Pixmap": os.path.join(HERE, icon), "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None and pp.enabled()

    def Activated(self):
        if self.name == "BruceWorx_PrintPrep":
            show()
        elif self.name == "BruceWorx_PlaceOnBed":
            place_on_bed()
        elif self.name == "BruceWorx_Export3MF":
            export_3mf()
        else:
            send_to_slicer()


def install():
    import FreeCADGui

    for name in COMMANDS:
        FreeCADGui.addCommand(name, _Command(name))
