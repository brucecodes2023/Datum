# SPDX-License-Identifier: LGPL-2.1-or-later
"""Construct > Plane, Axis and Point: a sensible datum from what is selected, in one step.

Plane
* one plane or flat face selected: a new datum plane on it, offset along its normal by the last distance
  used, and a small panel for the distance, the direction (the normal, or the body's X, Y or Z axis) and a
  count (several planes, one distance apart). The planes follow the panel as you type; OK keeps them (one
  undo step), Cancel removes them.
* two parallel planes or flat faces selected: the plane halfway between them (a midplane). It is attached
  to the first and its offset is an expression of the two, so it stays halfway when either moves.
* anything else: FreeCAD's own Plane tool with its attachment panel, as before.

Axis and Point
* FreeCAD's attacher is asked what the selection best gives (a cylinder's or a circle's axis, the line
  through two points, the intersection of two planes, a line along an edge; a vertex, a circle's centre,
  a face's centre): that datum is made at once, with no panel. When nothing fits, FreeCAD's own tool opens.

All of these are ordinary PartDesign datums, so FreeCAD's own panel edits them afterwards.

Off switch: Preferences > BruceWorx > "Plane, Axis and Point make a datum from the selection"
(OffsetPlane); the buttons then always open FreeCAD's tools.
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
MAX_COUNT = 200
PARALLEL_TOL = 1e-6
# kind: (command, FreeCAD's command, object type, attacher, icon, menu text, tooltip)
DATUMS = {
    "axis": ("BruceWorx_Axis", "PartDesign_Line", "PartDesign::Line", "Attacher::AttachEngineLine",
             "BruceWorx_Axis.svg", "Axis",
             "A construction axis. From the selection at once: the axis of a cylinder or a circle, the line "
             "through two points, the intersection of two planes, a line along an edge. Otherwise: pick what "
             "to attach it to."),
    "point": ("BruceWorx_Point", "PartDesign_Point", "PartDesign::Point", "Attacher::AttachEnginePoint",
              "BruceWorx_Point.svg", "Point",
              "A construction point. From the selection at once: on a vertex, the centre of a circle or an "
              "arc, the centre of a face. Otherwise: pick what to attach it to."),
}
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


def set_row(planes, reference, distance, direction=NORMAL, count=1):
    """Make ``planes`` (at least one, all on ``reference``) a row of ``count``: the k-th is k distances
    away. Planes are added or removed at the end. Returns the new list."""
    count = max(1, min(int(count), MAX_COUNT))
    planes = list(planes)
    body = body_of(planes[0])
    while len(planes) > count:
        extra = planes.pop()
        extra.Document.removeObject(extra.Name)
    while len(planes) < count:
        planes.append(make(body, reference, 0.0))
    for k, plane in enumerate(planes, 1):
        set_offset(plane, k * distance, direction)
    return planes


def _picked():
    """[(object, element)] of the selection, each resolved to the object that owns the element."""
    import FreeCADGui as Gui

    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        obj = sel.Object
        # with resolve 0 the object is the top one and each sub-element a path to the picked thing
        for path in [s for s in sel.SubElementNames if s] or [""]:
            try:
                picked = obj.getSubObject(path, retType=1) if path else obj
            except Exception:  # noqa: BLE001
                picked = obj
            out.append((picked or obj, path.rsplit(".", 1)[-1] if path else ""))
    return out


def _body_for(references):
    """The body a datum on ``references`` goes in: where the first of them lives, else the active body."""
    import FreeCADGui as Gui

    for obj, _sub in references:
        own = body_of(obj)
        if own is not None:
            return own
    try:
        return Gui.ActiveDocument.ActiveView.getActiveObject("pdbody")
    except Exception:  # noqa: BLE001 -- no 3D view
        return None


def _selected_references():
    """(body, [flat references]) for the selection; (None, []) when something not flat is in it."""
    picked = _picked()
    flat = [flat_reference(obj, sub) for obj, sub in picked]
    if not flat or any(r is None for r in flat):
        return None, []
    body = _body_for(flat)
    return (body, flat) if body is not None else (None, [])


def _selected_reference():
    """(body, reference) when exactly one flat thing is selected, else (None, None)."""
    body, flat = _selected_references()
    return (body, flat[0]) if len(flat) == 1 else (None, None)


# ----- midplane -------------------------------------------------------------------------------------


def plane_of(reference):
    """(point, normal) of a flat reference, in its body's axes."""
    import FreeCAD

    obj, sub = reference
    if sub:
        surface = obj.Shape.getElement(sub).Surface
        return FreeCAD.Vector(surface.Position), FreeCAD.Vector(surface.Axis)
    return FreeCAD.Vector(obj.Placement.Base), obj.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))


def parallel(first, second):
    return plane_of(first)[1].cross(plane_of(second)[1]).Length < PARALLEL_TOL


def _point_expression(reference):
    obj, sub = reference
    return f"{obj.Name}.Shape.{sub}.Surface.Position" if sub else f"{obj.Name}.Placement.Base"


def make_midplane(body, first, second):
    """A datum plane halfway between two parallel flat references. It is attached to ``first``; its offset
    is an expression of both (half the distance between them along the plane's normal), so it follows
    them. No transaction here."""
    import FreeCAD

    if not parallel(first, second):
        raise ValueError("a midplane needs two parallel planes or flat faces")
    plane = body.newObject("PartDesign::Plane", "MidPlane")
    plane.AttachmentSupport = [first]
    plane.MapMode = "FlatFace"
    plane.AttachmentOffset = FreeCAD.Placement()
    plane.recompute()
    normal = plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    half = (plane_of(second)[0] - plane_of(first)[0]).dot(normal) / 2.0
    expression = (f"(({_point_expression(second)}) - ({_point_expression(first)})) * "
                  f"({plane.Name}.Placement.Rotation * vector(0; 0; 1)) / 2")
    try:
        plane.setExpression("AttachmentOffset.Base.z", expression)
        plane.recompute()
        if abs(plane.AttachmentOffset.Base.z - half) > 1e-6 or not plane.isValid():
            raise ValueError(f"the expression gives {plane.AttachmentOffset.Base.z}, not {half}")
    except Exception as exc:  # noqa: BLE001 -- keep the plane, as a number: say that it no longer follows
        plane.setExpression("AttachmentOffset.Base.z", None)
        plane.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, half), FreeCAD.Rotation())
        plane.recompute()
        FreeCAD.Console.PrintWarning(f"Midplane {plane.Label}: placed by a number, it will not follow its "
                                     f"faces ({exc})\n")
    return plane


# ----- axis and point -------------------------------------------------------------------------------


AXIS_MODES = ("AxisOfCurvature", "IntersectionLine", "TwoPointLine", "FaceNormal")  # best first


def refined(references):
    """The references the attacher understands best: a lone cylinder or cone face stands for one of its
    circles (FreeCAD's attacher only offers a face's axes of inertia, which need not be its axis)."""
    if len(references) != 1 or not references[0][1].startswith("Face"):
        return list(references)
    obj, sub = references[0]
    try:
        face = obj.Shape.getElement(sub)
        if type(face.Surface).__name__ not in ("Cylinder", "Cone"):
            return list(references)
        for edge in face.Edges:
            if type(edge.Curve).__name__ != "Circle":
                continue
            for index, other in enumerate(obj.Shape.Edges, 1):
                if other.isSame(edge):
                    return [(obj, f"Edge{index}")]
    except Exception:  # noqa: BLE001
        pass
    return list(references)


def best_mode(kind, references):
    """The attachment mode to use for ``references`` (already refined), or "" when the selection does
    not say plainly enough what is wanted: then FreeCAD's panel asks."""
    import Part

    if not references:
        return ""
    try:
        engine = Part.AttachEngine(DATUMS[kind][3])
        engine.References = [(obj, (sub,)) for obj, sub in references]
        modes = list(engine.suggestModes().get("allApplicableModes", []))
    except Exception:  # noqa: BLE001
        return ""
    if kind == "axis":
        return next((m for m in AXIS_MODES if m in modes), "")
    subs = [sub for _obj, sub in references]
    if "CenterOfCurvature" in modes:
        return "CenterOfCurvature"
    if len(subs) == 1 and subs[0].startswith("Vertex") and "Vertex" in modes:
        return "Vertex"
    if len(subs) == 1 and not subs[0] and "ObjectOrigin" in modes:
        return "ObjectOrigin"
    return "CenterOfMass" if "CenterOfMass" in modes else ""


def make_datum(body, kind, references, mode=None):
    """A datum line or point in ``body`` attached to ``references`` in ``mode`` (the best one when not
    given). None when no mode fits. No transaction here."""
    references = refined(references)
    mode = mode or best_mode(kind, references)
    if not mode:
        return None
    datum = body.newObject(DATUMS[kind][2], "Datum" + ("Line" if kind == "axis" else "Point"))
    datum.AttachmentSupport = [(obj, (sub,)) for obj, sub in references]
    datum.MapMode = mode
    datum.recompute()
    return datum


def _panel_class():
    from PySide import QtCore, QtWidgets

    class OffsetPlanePanel:
        """Distance and direction of a plane just made; it follows the fields."""

        def __init__(self, plane, reference):
            self.plane, self.doc = plane, plane.Document
            self.planes, self.reference = [plane], reference
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
            self.count = QtWidgets.QSpinBox(self.form)
            self.count.setObjectName("BruceWorxOffsetPlaneCount")
            self.count.setRange(1, MAX_COUNT)
            self.count.setKeyboardTracking(False)
            self.count.setToolTip("Several planes, each one distance further than the one before.")
            layout.addRow("Count", self.count)
            self.count.valueChanged.connect(lambda _n: self.apply())
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
                distance, direction = self.values()
                self.planes = set_row(self.planes, self.reference, distance, direction, self.count.value())
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
            _select(self.planes[-1])  # ready for a sketch, or the next plane from this one
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

    body, flat = _selected_references() if enabled() else (None, [])
    if body is not None and len(flat) == 2 and not Gui.Control.activeDialog() and parallel(*flat):
        _one_step(body, "Midplane", lambda: make_midplane(body, *flat),
                  f"Midplane between {flat[0][0].Label} {flat[0][1]} and {flat[1][0].Label} {flat[1][1]}")
        return None
    if body is None or len(flat) != 1 or Gui.Control.activeDialog():
        Gui.runCommand(STOCK)
        return None
    reference = flat[0]
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


def _select(obj):
    import FreeCADGui as Gui

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(obj)


def _one_step(body, name, build, message):
    """Run ``build`` in one transaction, select what it made and say so in the status bar."""
    import FreeCADGui as Gui

    doc = body.Document
    doc.openTransaction(name)
    try:
        made = build()
    except Exception:
        doc.abortTransaction()
        raise
    if made is None:
        doc.abortTransaction()
        return None
    doc.commitTransaction()
    doc.recompute()
    _select(made)
    try:
        Gui.getMainWindow().statusBar().showMessage(f"{message}: {made.Label}", 6000)
    except Exception:  # noqa: BLE001
        pass
    return made


def start_datum(kind):
    """The Axis and Point commands: the datum the selection best gives, else FreeCAD's tool.
    Returns the datum or None."""
    import FreeCADGui as Gui

    stock = DATUMS[kind][1]
    references = refined(_picked()) if enabled() and not Gui.Control.activeDialog() else []
    body = _body_for(references) if references else None
    mode = best_mode(kind, references) if body is not None else ""
    if not mode:
        Gui.runCommand(stock)
        return None
    return _one_step(body, DATUMS[kind][5], lambda: make_datum(body, kind, references, mode),
                     f"{DATUMS[kind][5]} ({mode})")


class _DatumCommand:
    def __init__(self, kind):
        self.kind = kind

    def GetResources(self):
        _command, _stock, _type, _engine, icon, text, tip = DATUMS[self.kind]
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), icon),
                "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        start_datum(self.kind)


class _PlaneCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Plane",
                "ToolTip": "A construction plane. With a plane or a flat face selected: a plane offset from it "
                           "(type the distance; along its normal, or along X, Y or Z; several at once). With "
                           "two parallel ones selected: the plane halfway between. Otherwise: pick what to "
                           "attach it to."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        start()


def install():
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND, _PlaneCommand())
    for kind, spec in DATUMS.items():
        FreeCADGui.addCommand(spec[0], _DatumCommand(kind))
