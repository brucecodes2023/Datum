# SPDX-License-Identifier: LGPL-2.1-or-later
"""Construct > Plane: with a plane or a flat face selected, an offset plane in one step.

Select a datum plane, an origin plane or a flat face, then Plane: a new datum plane appears on it,
offset along its normal by the last distance used, and a small panel asks for the distance and the
direction (along the normal, or along the body's X, Y or Z axis). The plane follows the panel as you
type; OK keeps it (one undo step), Cancel removes it. With nothing flat selected the command is
FreeCAD's own Plane tool with its attachment panel, as before.

The plane is an ordinary PartDesign datum plane (attached "Plane face", the offset in its Attachment
Offset), so FreeCAD's own panel edits it afterwards.

Off switch: Preferences > BruceWorx > "Plane makes an offset plane from a selected plane or face"
(OffsetPlane); the button then always opens FreeCAD's tool.
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "OffsetPlane"
DISTANCE_PREF = "OffsetPlaneDistance"
DEFAULT_DISTANCE = 10.0  # mm, the first time
COMMAND = "BruceWorx_Plane"
STOCK = "PartDesign_Plane"
ICON = "BruceWorx_Plane.svg"
NORMAL = "normal"
DIRECTIONS = ((NORMAL, "Normal to the plane"), ("x", "X axis"), ("y", "Y axis"), ("z", "Z axis"))
_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
_state = {"panel": None}


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


def last_distance():
    import FreeCAD

    return float(FreeCAD.ParamGet(PREFS_PATH).GetFloat(DISTANCE_PREF, DEFAULT_DISTANCE))


def offset_vector(rotation, direction, distance):
    """The Attachment Offset position, in the plane's own axes (Z is its normal), that moves a plane
    turned by ``rotation`` (in the body) by ``distance`` along ``direction``: "normal", "x", "y" or "z"
    (the body's axes)."""
    import FreeCAD

    if direction == NORMAL:
        return FreeCAD.Vector(0, 0, distance)
    if direction not in _AXES:
        raise ValueError(f"direction must be one of normal, x, y, z, not {direction!r}")
    return rotation.inverted().multVec(FreeCAD.Vector(*_AXES[direction]) * distance)


def flat_reference(obj, sub=""):
    """(object, sub-element) to attach a plane to when the selection is flat, else None:
    a datum or origin plane, or a flat face of a solid."""
    if obj is None:
        return None
    if obj.isDerivedFrom("App::Plane") or obj.isDerivedFrom("PartDesign::Plane"):
        return (obj, "")
    if sub.startswith("Face"):
        try:
            face = obj.Shape.getElement(sub)
            if type(face.Surface).__name__ == "Plane":
                return (obj, sub)
        except Exception:  # noqa: BLE001 -- not a face of this shape
            return None
    return None


def body_of(obj):
    """The body ``obj`` belongs to (itself when it is one), or None."""
    if obj is None:
        return None
    if obj.isDerivedFrom("PartDesign::Body"):
        return obj
    for parent in obj.InListRecursive:
        if parent.isDerivedFrom("PartDesign::Body"):
            return parent
    return None


def make(body, reference, distance, direction=NORMAL):
    """A datum plane in ``body`` on ``reference`` (object, sub-element), offset. No transaction here."""
    import FreeCAD

    plane = body.newObject("PartDesign::Plane", "DatumPlane")
    plane.AttachmentSupport = [reference]
    plane.MapMode = "FlatFace"
    plane.AttachmentOffset = FreeCAD.Placement()
    plane.recompute()
    set_offset(plane, distance, direction)
    return plane


def set_offset(plane, distance, direction=NORMAL):
    """Move the plane to ``distance`` along ``direction`` from where it is attached."""
    import FreeCAD

    offset = plane.AttachmentOffset
    if offset.Base.Length > 0:  # the rotation the offset is measured in is the one without it
        plane.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(), offset.Rotation)
        plane.recompute()
    vector = offset_vector(plane.Placement.Rotation, direction, distance)
    plane.AttachmentOffset = FreeCAD.Placement(vector, offset.Rotation)
    plane.recompute()


def _selected_reference():
    """(body, reference) for the first flat thing selected, or (None, None)."""
    import FreeCADGui as Gui

    for sel in Gui.Selection.getSelectionEx("", 0):
        obj = sel.Object
        subs = [s for s in sel.SubElementNames if s] or [""]
        # with resolve 0 the object is the top one and the sub-element a path to the picked thing
        path = subs[0]
        try:
            picked = obj.getSubObject(path, retType=1) if path else obj
        except Exception:  # noqa: BLE001
            picked = obj
        element = path.rsplit(".", 1)[-1] if path else ""
        reference = flat_reference(picked or obj, element)
        if reference is None:
            continue
        body = None
        try:
            body = Gui.ActiveDocument.ActiveView.getActiveObject("pdbody")
        except Exception:  # noqa: BLE001 -- no 3D view
            pass
        own = body_of(reference[0])
        if own is not None:
            body = own  # the plane goes where its reference lives
        if body is not None:
            return body, reference
    return None, None


def _panel_class():
    from PySide import QtCore, QtWidgets

    class OffsetPlanePanel:
        """Distance and direction of a plane just made; it follows the fields."""

        def __init__(self, plane, reference):
            self.plane, self.doc = plane, plane.Document
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle("Offset plane")
            self.form.setObjectName("BruceWorxOffsetPlane")
            layout = QtWidgets.QFormLayout(self.form)
            obj, sub = reference
            layout.addRow("From", QtWidgets.QLabel(f"{obj.Label} {sub}".strip(), self.form))
            self.distance = QtWidgets.QDoubleSpinBox(self.form)
            self.distance.setObjectName("BruceWorxOffsetPlaneDistance")
            self.distance.setRange(-1e6, 1e6)
            self.distance.setDecimals(3)
            self.distance.setSuffix(" mm")
            self.distance.setKeyboardTracking(False)
            self.distance.setValue(last_distance())
            layout.addRow("Distance", self.distance)
            self.direction = QtWidgets.QComboBox(self.form)
            self.direction.setObjectName("BruceWorxOffsetPlaneDirection")
            for key, text in DIRECTIONS:
                self.direction.addItem(text, key)
            layout.addRow("Direction", self.direction)
            self.flip = QtWidgets.QCheckBox("Other side", self.form)
            self.flip.setObjectName("BruceWorxOffsetPlaneFlip")
            layout.addRow("", self.flip)
            self.distance.valueChanged.connect(lambda _v: self.apply())
            self.direction.currentIndexChanged.connect(lambda _i: self.apply())
            self.flip.toggled.connect(lambda _on: self.apply())
            QtCore.QTimer.singleShot(0, lambda: (self.distance.setFocus(), self.distance.selectAll()))

        def values(self):
            distance = self.distance.value() * (-1.0 if self.flip.isChecked() else 1.0)
            return distance, self.direction.currentData()

        def apply(self):
            try:
                set_offset(self.plane, *self.values())
            except Exception as exc:  # noqa: BLE001 -- the plane was removed under the panel
                import FreeCAD

                FreeCAD.Console.PrintWarning(f"Offset plane: {exc}\n")

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def accept(self):
            import FreeCAD
            import FreeCADGui as Gui

            self.apply()
            FreeCAD.ParamGet(PREFS_PATH).SetFloat(DISTANCE_PREF, abs(self.distance.value()))
            self.doc.commitTransaction()
            _state["panel"] = None
            Gui.Control.closeDialog()
            self.doc.recompute()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.plane)  # ready for a sketch, or the next plane from this one
            return True

        def reject(self):
            import FreeCADGui as Gui

            self.doc.abortTransaction()
            _state["panel"] = None
            Gui.Control.closeDialog()
            self.doc.recompute()
            return True

    return OffsetPlanePanel


def start():
    """The command: an offset plane from the selection, else FreeCAD's Plane tool. Returns the panel or None."""
    import FreeCADGui as Gui

    body, reference = _selected_reference() if enabled() else (None, None)
    if body is None or Gui.Control.activeDialog():
        Gui.runCommand(STOCK)
        return None
    doc = body.Document
    doc.openTransaction("Offset plane")
    try:
        plane = make(body, reference, last_distance())
    except Exception:
        doc.abortTransaction()
        raise
    panel = _state["panel"] = _panel_class()(plane, reference)
    Gui.Control.showDialog(panel)
    return panel


class _PlaneCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Plane",
                "ToolTip": "A construction plane. With a plane or a flat face selected: a plane offset from it "
                           "(type the distance; along its normal, or along X, Y or Z). Otherwise: pick what "
                           "to attach it to."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        start()


def install():
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND, _PlaneCommand())
