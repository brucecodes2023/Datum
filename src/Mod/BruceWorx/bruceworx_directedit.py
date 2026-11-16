# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct edit on imported geometry (roadmap MOD-04, first slice).

An imported STEP part is a plain solid with no features. These edits change it anyway, each as a step
that stays in the tree on top of the solid it changed:

* Offset face   a flat face moved along its normal (press-pull): out adds material, in removes it;
* Edit hole     a recognised cylindrical hole (through or blind) moved, resized, or both;
* Delete face   faces removed and the neighbours grown over the gap (OpenCASCADE defeaturing, the
                engine of Part_Defeaturing): a hole (all its faces at once), a fillet, a pocket floor.

A step is a Part::FeaturePython with ``Base`` (the solid before, hidden under it in the tree), the
faces it works on (names of Base's faces) and its values. Changing a value recomputes it; deleting
the step shows the solid before again. A step that cannot make a valid solid keeps its last shape and
says why (the panels then change nothing).

Works on plain solids only (Part features that are not Part Design); a body has its own features.
The geometry (holes(), offset_face(), edit_hole(), delete_faces()) needs no GUI.

Off switch: Preferences > BruceWorx > "Direct edit on imported solids" (DirectEdit).
"""

from __future__ import annotations

import math
import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "DirectEdit"
OFFSET, HOLE, DELETE = "OffsetFace", "EditHole", "DeleteFaces"
LABELS = {OFFSET: "Offset face", HOLE: "Edit hole", DELETE: "Delete face"}
COMMANDS = {OFFSET: "BruceWorx_OffsetFace", HOLE: "BruceWorx_EditHole", DELETE: "BruceWorx_DeleteFace"}
ICONS = {OFFSET: "BruceWorx_OffsetFace.svg", HOLE: "BruceWorx_EditHole.svg", DELETE: "BruceWorx_DeleteFace.svg"}
KIND, FACES, STATUS = "DirectEditKind", "DirectEditFaces", "DirectEditStatus"
AXIS_TOL = 1e-6  # two unit axes this close are one direction
LINE_TOL = 1e-4  # mm between two axes that are one line
OPEN_MARGIN = 0.01  # mm a resized hole's tool reaches past an open end
_state = {"panel": None}


class DirectEditError(Exception):
    """An edit that cannot be done, in one sentence for the user."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled():
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- geometry -------------------------------------------------------------------------------------


def _solid(shape, what):
    """The one valid solid in ``shape``, or DirectEditError saying what went wrong."""
    if shape is None or shape.isNull() or not shape.Solids:
        raise DirectEditError(f"{what} leaves no solid")
    if len(shape.Solids) > 1:
        raise DirectEditError(f"{what} would split the part into {len(shape.Solids)} pieces")
    out = shape.Solids[0]
    if not out.isValid() or out.Volume <= 1e-9:
        raise DirectEditError(f"{what} does not give a valid solid")
    return out


def _face(shape, name):
    try:
        face = shape.getElement(name)
    except Exception:  # noqa: BLE001
        face = None
    if face is None or face.ShapeType != "Face":
        raise DirectEditError(f"the solid has no {name} any more")
    return face


def _refined(shape):
    try:
        return shape.removeSplitter()
    except Exception:  # noqa: BLE001 -- the unrefined result is still right
        return shape


def offset_face(shape, name, distance):
    """``shape`` with the flat face ``name`` moved ``distance`` along its outward normal.

    The face's outline is carried straight along the normal: neighbours that stand square to the face
    simply get longer or shorter; a slanted neighbour gets a square step, not a longer slope."""
    face = _face(shape, name)
    if type(face.Surface).__name__ != "Plane":
        raise DirectEditError("Offset face needs a flat face")
    if abs(distance) < 1e-9:
        return _solid(shape, "The solid")
    u0, u1, v0, v1 = face.ParameterRange
    normal = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    try:
        prism = face.extrude(normal * distance)
        out = shape.fuse(prism) if distance > 0 else shape.cut(prism)
    except Exception as exc:  # noqa: BLE001
        raise DirectEditError(f"the face could not be offset ({exc})") from None
    return _solid(_refined(out), f"Offsetting by {distance:g} mm")


class Hole:
    """A cylindrical hole: ``faces`` are the names of all its faces (the wall, and the floor of a blind
    one), ``point`` is on the axis where it is nearest the origin, ``low``/``high`` its ends along
    ``axis`` from there, ``through`` whether both ends are open."""

    __slots__ = ("faces", "diameter", "point", "axis", "low", "high", "through", "open_low", "open_high")

    def __init__(self, faces, diameter, point, axis, low, high):
        self.faces, self.diameter, self.point, self.axis = faces, diameter, point, axis
        self.low, self.high = low, high
        self.through = self.open_low = self.open_high = False

    @property
    def depth(self):
        return self.high - self.low

    @property
    def centre(self):
        return self.point + self.axis * ((self.low + self.high) / 2)

    def text(self):
        kind = "through" if self.through else "blind"
        return f"⌀{self.diameter:.3f} mm {kind} hole, {self.depth:.3f} mm deep"


def _wall(face):
    """(axis, point on the axis nearest the origin, radius, low, high, angle) of a cylindrical face
    whose material is outside it (a hole's wall), else None."""
    App = _app()
    surface = face.Surface
    if type(surface).__name__ != "Cylinder":
        return None
    axis = App.Vector(surface.Axis)
    if (axis.x, axis.y, axis.z) < (0, 0, 0):
        axis = axis * -1
    centre = App.Vector(surface.Center)
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    radial = face.valueAt(u, v) - centre
    radial = radial - axis * radial.dot(axis)
    if face.normalAt(u, v).dot(radial) >= 0:
        return None  # a shaft or a rounded outside edge
    base = centre - axis * centre.dot(axis)
    ends = [(face.valueAt(u, w) - base).dot(axis) for w in (v0, v1)]
    return axis, base, surface.Radius, min(ends), max(ends), u1 - u0


def _axis_distance(point, base, axis):
    gap = point - base
    return (gap - axis * gap.dot(axis)).Length


def holes(shape):
    """[Hole] of the solid. Faces of one wall (two halves) are one hole; a blind hole takes its floor
    (flat or drill point) with it. A counterbore is two holes: the bore and the narrower hole."""
    found = []
    faces = shape.Faces
    for index, face in enumerate(faces, 1):
        wall = _wall(face)
        if wall is None:
            continue
        axis, base, radius, low, high, angle = wall
        for entry in found:
            if (abs(entry["radius"] - radius) < LINE_TOL and (entry["axis"] - axis).Length < AXIS_TOL
                    and (entry["base"] - base).Length < LINE_TOL
                    and low <= entry["high"] + LINE_TOL and high >= entry["low"] - LINE_TOL):
                entry["faces"].append(index)
                entry["low"], entry["high"] = min(entry["low"], low), max(entry["high"], high)
                entry["angle"] += angle
                break
        else:
            found.append({"faces": [index], "axis": axis, "base": base, "radius": radius, "low": low,
                          "high": high, "angle": angle})
    out = []
    for entry in found:
        if entry["angle"] < 2 * math.pi - 1e-3:
            continue  # a fillet or a round slot end, not a hole
        axis, base, radius = entry["axis"], entry["base"], entry["radius"]
        names = [f"Face{i}" for i in entry["faces"]]
        low, high = entry["low"], entry["high"]
        wall_edges = {e.hashCode() for i in entry["faces"] for e in faces[i - 1].Edges}
        closed_low = closed_high = False
        for index, face in enumerate(faces, 1):  # the floor: a face inside the wall's circle that touches it
            if index in entry["faces"] or not any(e.hashCode() in wall_edges for e in face.Edges):
                continue
            points = [v.Point for v in face.Vertexes] + [face.CenterOfMass]
            if all(_axis_distance(p, base, axis) <= radius + LINE_TOL for p in points):
                names.append(f"Face{index}")
                along = [(p - base).dot(axis) for p in points]
                if sum(along) / len(along) <= (entry["low"] + entry["high"]) / 2:
                    closed_low = True
                else:
                    closed_high = True
                low, high = min([low] + along), max([high] + along)
        hole = Hole(names, 2 * radius, base, axis, low, high)
        probe = max(1e-3, min(0.05, radius / 10))
        hole.open_low = not closed_low and not shape.isInside(base + axis * (entry["low"] - probe), 1e-7, True)
        hole.open_high = not closed_high and not shape.isInside(base + axis * (entry["high"] + probe), 1e-7, True)
        hole.through = hole.open_low and hole.open_high
        out.append(hole)
    return out


def hole_of(shape, name):
    """The hole that face ``name`` belongs to, or DirectEditError."""
    for hole in holes(shape):
        if name in hole.faces:
            return hole
    raise DirectEditError(f"{name} is not part of a round hole")


def delete_faces(shape, names):
    """``shape`` without the faces ``names``, the neighbours grown over the gap."""
    if not names:
        raise DirectEditError("no face to delete")
    faces = [_face(shape, n) for n in names]
    try:
        out = shape.defeaturing(faces)
    except Exception as exc:  # noqa: BLE001
        raise DirectEditError(f"the neighbouring faces cannot close the gap ({exc})") from None
    result = _solid(out, "Deleting")
    if abs(result.Volume - shape.Volume) < 1e-9 and len(result.Faces) == len(shape.Faces):
        raise DirectEditError("the neighbouring faces cannot close the gap")
    return result


def edit_hole(shape, name, diameter=0.0, move=None):
    """``shape`` with the hole of face ``name`` at ``diameter`` (0: as it is) and moved by ``move``
    (a vector, or None). A hole that is only moved keeps its exact form (a drill point too); a
    resized blind hole gets a flat floor at the same depth."""
    App = _app()
    import Part

    hole = hole_of(shape, name)
    move = App.Vector(move) if move is not None else App.Vector()
    resize = diameter > 1e-9 and abs(diameter - hole.diameter) > 1e-9
    if not resize and move.Length < 1e-9:
        return _solid(shape, "The solid")
    filled = delete_faces(shape, hole.faces)
    if resize:
        low = hole.low - (OPEN_MARGIN if hole.open_low else 0.0)
        high = hole.high + (OPEN_MARGIN if hole.open_high else 0.0)
        tool = Part.makeCylinder(diameter / 2, high - low, hole.point + hole.axis * low + move, hole.axis)
    else:
        tool = filled.cut(shape)  # the plug: exactly what the hole removed
        if tool.isNull() or not tool.Solids:
            raise DirectEditError("the hole could not be taken out of the solid")
        tool = tool.copy()
        tool.translate(move)
    try:
        out = filled.cut(tool)
    except Exception as exc:  # noqa: BLE001
        raise DirectEditError(f"the hole could not be cut again ({exc})") from None
    result = _solid(_refined(out), "This hole")
    if abs(result.Volume - filled.Volume) < 1e-9:
        raise DirectEditError("the hole would be outside the part")
    return result


def apply(shape, kind, faces, distance=0.0, diameter=0.0, move=None):
    """One step on ``shape`` (in its own frame); the new solid, or DirectEditError."""
    if kind == OFFSET:
        return offset_face(shape, faces[0] if faces else "", distance)
    if kind == HOLE:
        return edit_hole(shape, faces[0] if faces else "", diameter, move)
    if kind == DELETE:
        return delete_faces(shape, faces)
    raise DirectEditError(f"unknown direct edit {kind!r}")


# ----- the step in the document ---------------------------------------------------------------------


def local_shape(obj):
    """The object's shape in its own frame (without its Placement)."""
    shape = obj.Shape.copy()
    shape.Placement = _app().Placement()
    return shape


def editable(obj):
    """None when ``obj`` is a plain solid direct edit works on, else why not."""
    if obj is None:
        return "nothing is selected"
    if obj.isDerivedFrom("PartDesign::Feature") or obj.isDerivedFrom("PartDesign::Body"):
        return f"{obj.Label} is a Part Design body: edit its features instead"
    if not obj.isDerivedFrom("Part::Feature"):
        return f"{obj.Label} is not a solid"
    shape = obj.Shape
    if shape.isNull() or len(shape.Solids) != 1:
        return f"{obj.Label} is not a single solid"
    return None


def is_step(obj):
    return obj is not None and hasattr(obj, KIND) and obj.isDerivedFrom("Part::FeaturePython")


class DirectEditProxy:
    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, obj):
        base = obj.Base
        if base is None:
            raise RuntimeError("the solid this step edits is gone")
        try:
            result = apply(local_shape(base), getattr(obj, KIND), list(getattr(obj, FACES)),
                           obj.Distance.Value, obj.Diameter.Value, obj.Move)
        except DirectEditError as exc:
            _set(obj, STATUS, f"Error: {exc}")
            raise RuntimeError(str(exc)) from None
        placement = obj.Placement
        obj.Shape = result
        obj.Placement = placement
        _set(obj, STATUS, "Up to date")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


class DirectEditView:
    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def claimChildren(self):
        base = getattr(self.Object, "Base", None)
        return [base] if base is not None else []

    def getIcon(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), ICONS.get(getattr(self.Object, KIND, ""), ICONS[OFFSET]))

    def doubleClicked(self, vobj):
        edit(vobj.Object)
        return True

    def onDelete(self, vobj, _subs):
        base = getattr(vobj.Object, "Base", None)
        if base is not None:
            base.Visibility = True  # the solid before the step is the part again
        return True

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def _set(obj, name, value):
    if getattr(obj, name) != value:
        setattr(obj, name, value)


def make(base, kind, faces, distance=0.0, diameter=0.0, move=None):
    """A step of ``kind`` on ``base``'s ``faces``. Recomputed; raises DirectEditError and leaves the
    document as it was when the edit cannot be done. No transaction here."""
    App = _app()
    why = editable(base)
    if why:
        raise DirectEditError(why)
    apply(local_shape(base), kind, list(faces), distance, diameter, move)  # fail before anything is made
    doc = base.Document
    obj = doc.addObject("Part::FeaturePython", "DirectEdit")
    DirectEditProxy(obj)
    obj.addProperty("App::PropertyLink", "Base", "Direct edit", "The solid before this step")
    obj.addProperty("App::PropertyString", KIND, "Direct edit", "What this step does")
    obj.addProperty("App::PropertyStringList", FACES, "Direct edit", "The faces of Base it works on")
    obj.addProperty("App::PropertyDistance", "Distance", "Direct edit", "Offset face: how far, along the face's normal")
    obj.addProperty("App::PropertyLength", "Diameter", "Direct edit", "Edit hole: the new diameter (0: unchanged)")
    obj.addProperty("App::PropertyVector", "Move", "Direct edit", "Edit hole: how far the hole moves, in the part's axes")
    obj.addProperty("App::PropertyString", STATUS, "Direct edit", "Up to date, or why the step failed")
    for name in (KIND, FACES, STATUS):
        obj.setEditorMode(name, 1)  # read-only
    obj.Base = base
    setattr(obj, KIND, kind)
    setattr(obj, FACES, list(faces))
    obj.Distance = distance
    obj.Diameter = diameter
    obj.Move = App.Vector(move) if move is not None else App.Vector()
    obj.Placement = base.Placement
    obj.Label = LABELS[kind]
    if App.GuiUp:
        DirectEditView(obj.ViewObject)
        try:
            obj.ViewObject.ShapeAppearance = base.ViewObject.ShapeAppearance
        except Exception:  # noqa: BLE001
            pass
    base.Visibility = False
    obj.recompute()
    return obj


def remove(obj):
    """Take a step away again: the solid before it shows, and a later step now works on that solid."""
    base, doc = obj.Base, obj.Document
    for other in list(obj.InList):
        if is_step(other) and other.Base is obj:
            other.Base = base
    if base is not None:
        base.Visibility = not any(is_step(o) and o.Base is base for o in base.InList if o is not obj)
    doc.removeObject(obj.Name)
    doc.recompute()


# ----- GUI ------------------------------------------------------------------------------------------


def _selected_face():
    """(object, face name) of the first face selected, or (None, "")."""
    import FreeCADGui as Gui

    for sel in Gui.Selection.getSelectionEx():
        for sub in sel.SubElementNames:
            if sub.startswith("Face"):
                return sel.Object, sub
    return None, ""


def _selected_faces():
    """(object, [face names]) of the faces selected on one object."""
    import FreeCADGui as Gui

    for sel in Gui.Selection.getSelectionEx():
        faces = [s for s in sel.SubElementNames if s.startswith("Face")]
        if faces:
            return sel.Object, faces
    return None, []


def _tell(text):
    import FreeCADGui as Gui
    from PySide import QtWidgets

    QtWidgets.QMessageBox.information(Gui.getMainWindow(), "Direct edit", text)


def _panel_class():
    from PySide import QtCore, QtWidgets

    class DirectEditPanel:
        """The values of one step; the solid follows the fields. ``new``: made by this panel (Cancel
        removes it), else an existing step being edited (Cancel puts its values back)."""

        def __init__(self, obj, new, note=""):
            self.obj, self.doc, self.new = obj, obj.Document, new
            self.kind = getattr(obj, KIND)
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle(LABELS[self.kind])
            self.form.setObjectName("BruceWorxDirectEdit")
            layout = QtWidgets.QFormLayout(self.form)
            layout.addRow("On", QtWidgets.QLabel(f"{obj.Base.Label}: {', '.join(getattr(obj, FACES)[:4])}", self.form))
            if note:
                layout.addRow("", QtWidgets.QLabel(note, self.form))
            self.fields = {}
            if self.kind == OFFSET:
                self._spin(layout, "distance", "Distance", obj.Distance.Value, -1e6)
            elif self.kind == HOLE:
                self._spin(layout, "diameter", "Diameter", obj.Diameter.Value, 0.0)
                for axis in "xyz":
                    self._spin(layout, axis, f"Move {axis.upper()}", getattr(obj.Move, axis), -1e6)
            self.status = QtWidgets.QLabel("", self.form)
            self.status.setObjectName("BruceWorxDirectEditStatus")
            self.status.setWordWrap(True)
            layout.addRow(self.status)
            self.good = self.values()
            first = next(iter(self.fields.values()), None)
            if first is not None:
                QtCore.QTimer.singleShot(0, lambda: (first.setFocus(), first.selectAll()))

        def _spin(self, layout, key, text, value, low):
            box = QtWidgets.QDoubleSpinBox(self.form)
            box.setObjectName("BruceWorxDirectEdit_" + key)
            box.setRange(low, 1e6)
            box.setDecimals(3)
            box.setSuffix(" mm")
            box.setKeyboardTracking(False)
            box.setValue(value)
            box.valueChanged.connect(lambda _v: self.apply())
            layout.addRow(text, box)
            self.fields[key] = box

        def values(self):
            return {key: box.value() for key, box in self.fields.items()}

        def write(self, values):
            App = _app()
            if self.kind == OFFSET:
                self.obj.Distance = values["distance"]
            elif self.kind == HOLE:
                self.obj.Diameter = values["diameter"]
                self.obj.Move = App.Vector(values["x"], values["y"], values["z"])

        def apply(self):
            """Try the fields' values; on failure the solid keeps the last good ones and the reason shows."""
            values = self.values()
            try:
                apply(local_shape(self.obj.Base), self.kind, list(getattr(self.obj, FACES)),
                      values.get("distance", 0.0), values.get("diameter", 0.0),
                      (values["x"], values["y"], values["z"]) if "x" in values else None)
            except DirectEditError as exc:
                self.status.setText(f"Not possible: {exc}")
                return False
            self.status.setText("")
            self.write(values)
            self.obj.recompute()
            self.good = values
            return True

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def accept(self):
            import FreeCADGui as Gui

            if not self.apply():
                return False  # the reason is in the panel; stay open
            self.doc.commitTransaction()
            _state["panel"] = None
            Gui.Control.closeDialog()
            self.doc.recompute()
            return True

        def reject(self):
            import FreeCADGui as Gui

            self.doc.abortTransaction()
            _state["panel"] = None
            Gui.Control.closeDialog()
            self.doc.recompute()
            return True

    return DirectEditPanel


def _show(obj, new, note=""):
    import FreeCADGui as Gui

    panel = _state["panel"] = _panel_class()(obj, new, note)
    Gui.Control.showDialog(panel)
    return panel


def start(kind):
    """The command of ``kind`` on the selected face(s). Returns the panel (Offset face, Edit hole),
    the new step (Delete face) or None when it could not start (a message says why)."""
    import FreeCADGui as Gui

    if Gui.Control.activeDialog():
        _tell("Finish the open panel first.")
        return None
    obj, faces = _selected_faces()
    why = "Select a face of an imported solid first." if obj is None else editable(obj)
    if why:
        _tell(why)
        return None
    doc = obj.Document
    shape = local_shape(obj)
    note = ""
    try:
        if kind == OFFSET:
            values = {"distance": 0.0}
            if type(_face(shape, faces[0]).Surface).__name__ != "Plane":
                raise DirectEditError("Offset face needs a flat face")
            faces = faces[:1]
        elif kind == HOLE:
            hole = hole_of(shape, faces[0])
            faces, note = [hole.faces[0]], hole.text()
            values = {"diameter": hole.diameter}
        else:
            expanded = []
            for name in faces:  # a hole goes as a whole
                try:
                    group = hole_of(shape, name).faces
                except DirectEditError:
                    group = [name]
                expanded.extend(n for n in group if n not in expanded)
            faces, values = expanded, {}
    except DirectEditError as exc:
        _tell(str(exc))
        return None
    doc.openTransaction(LABELS[kind])
    try:
        step = make(obj, kind, faces, **values)
    except DirectEditError as exc:
        doc.abortTransaction()
        _tell(f"Not possible: {exc}")
        return None
    except Exception:
        doc.abortTransaction()
        raise
    Gui.Selection.clearSelection()
    if kind == DELETE:
        doc.commitTransaction()
        doc.recompute()
        return step
    return _show(step, True, note)


def edit(obj):
    """Reopen a step's panel (double-click in the tree). Delete face has nothing to edit."""
    import FreeCADGui as Gui

    if not is_step(obj) or getattr(obj, KIND) == DELETE or Gui.Control.activeDialog():
        return None
    obj.Document.openTransaction(f"Edit {obj.Label}")
    return _show(obj, False)


class _Command:
    TIPS = {
        OFFSET: "Move a flat face of an imported solid along its normal: out adds material, in removes it.",
        HOLE: "Move or resize a round hole of an imported solid. Select a face of the hole.",
        DELETE: "Remove the selected faces of an imported solid and close the gap: a hole, a fillet, a pocket floor.",
    }

    def __init__(self, kind):
        self.kind = kind

    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICONS[self.kind]),
                "MenuText": LABELS[self.kind], "ToolTip": self.TIPS[self.kind]}

    def IsActive(self):
        return _app().ActiveDocument is not None and enabled()

    def Activated(self):
        start(self.kind)


def install():
    import FreeCADGui

    for kind, name in COMMANDS.items():
        FreeCADGui.addCommand(name, _Command(kind))
