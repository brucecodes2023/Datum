# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Surface tab's own tools (roadmap MOD-08): commands, the features' tree icons, and the Continuity
panel. The geometry is bruceworx_surfacing; this module reads the selection, asks for a number where one
is needed, and wraps each command in one undo step.

Selection per command:

    Trim            the surface (or its faces), then the curves (a sketch, or edges)
    Untrim          trimmed faces
    Extend          one face and the boundary edges of it to extend past (asks the distance)
    Patch           one free edge round a hole (Surface Filling, tangent to the faces round it)
    Stitch          the surfaces (two or more objects, or faces)
    Thicken         a surface (asks the thickness and side)
    Replace Face    a face of a solid, then the surface to replace it with
    Continuity      surfaces or faces; nothing: every visible surface (objects with faces and no solid)

Continuity colours each edge checked in the 3D view (green G2, yellow G1, orange G0, red a gap) and lists
gap, angle and curvature change per edge. Nothing is written to the document; closing the panel removes
the colours.
"""

from __future__ import annotations

import math
import os

import bruceworx_surfacing as bs
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_NAME = "BWContinuity"
COLOURS = {"G2": (0.20, 0.70, 0.30), "G1": (0.95, 0.80, 0.10), "G0": (1.00, 0.50, 0.10), "none": (0.90, 0.10, 0.10)}

COMMANDS = {
    "trim": ("BruceWorx_SurfaceTrim", "Trim", "Heuretes_SurfaceTrim.svg",
             "Cut a surface with curves projected onto it: select the surface, then a sketch or edges. "
             "Keeps the outside of a closed curve (change Keep in the properties)."),
    "untrim": ("BruceWorx_SurfaceUntrim", "Untrim", "Heuretes_SurfaceUntrim.svg",
               "Give a trimmed face its holes back (Mode Natural: the whole underlying surface)."),
    "extend": ("BruceWorx_SurfaceExtend", "Extend", "Heuretes_SurfaceExtend.svg",
               "Carry a face on past its boundary edges by a distance: Natural continues the surface "
               "itself (curvature continuous), Tangent runs straight on (G1). Select the face and its edges."),
    "patch": ("BruceWorx_SurfacePatch", "Patch", "Heuretes_SurfacePatch.svg",
              "Fill the hole a free edge borders, tangent to the faces round it (Surface Filling). "
              "Select one edge of the hole."),
    "stitch": ("BruceWorx_SurfaceStitch", "Stitch", "Heuretes_SurfaceStitch.svg",
               "Sew surfaces into one shell, and into a solid when it closes. Reports the edges left open."),
    "thicken": ("BruceWorx_SurfaceThicken", "Thicken", "Heuretes_SurfaceThicken.svg",
                "Make a surface into a solid of a thickness: outside, inside or both sides."),
    "replace": ("BruceWorx_ReplaceFace", "Replace Face", "Heuretes_ReplaceFace.svg",
                "Swap a face of a solid for a surface; the sides are carried to the surface. Select the "
                "face, then the surface."),
    "continuity": ("BruceWorx_ContinuityCheck", "Continuity", "Heuretes_Continuity.svg",
                   "Check G0/G1/G2 across the edges where surfaces meet: gap, angle and curvature change, "
                   "coloured in the view (OCCT's limits by default)."),
}
ICONS = {bs.TRIM: "Heuretes_SurfaceTrim.svg", bs.UNTRIM: "Heuretes_SurfaceUntrim.svg",
         bs.EXTEND: "Heuretes_SurfaceExtend.svg", bs.STITCH: "Heuretes_SurfaceStitch.svg",
         bs.THICKEN: "Heuretes_SurfaceThicken.svg", bs.REPLACE: "Heuretes_ReplaceFace.svg"}
_state = {"panel": None}


def _gui():
    import FreeCADGui

    return FreeCADGui


class SurfacingView:
    """Tree icon, the inputs under the feature, and the inputs shown again when it is deleted."""

    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return os.path.join(HERE, ICONS.get(getattr(self.Object, bs.KIND, ""), "Heuretes_SurfaceTrim.svg"))

    def _inputs(self):
        out = []
        for prop in ("Base", "Tool"):
            for obj, _subs in bs._links(getattr(self.Object, prop, None)):
                if obj is not None and obj not in out:
                    out.append(obj)
        return out

    def claimChildren(self):
        return self._inputs()

    def onDelete(self, _vobj, _subs):
        for obj in self._inputs():
            try:
                obj.Visibility = True
            except Exception:  # noqa: BLE001
                pass
        return True

    def dumps(self):
        return None

    def loads(self, _state):
        return None


# ----- selection ----------------------------------------------------------------------------------------

def _selection():
    """[(object, [sub-element names])] in pick order."""
    return [(s.Object, list(s.SubElementNames)) for s in _gui().Selection.getSelectionEx()]


def _tell(text, title="Surface"):
    from PySide import QtWidgets

    QtWidgets.QMessageBox.information(_gui().getMainWindow(), title, text)


def _ask_number(title, label, value, decimals=3):
    from PySide import QtWidgets

    number, ok = QtWidgets.QInputDialog.getDouble(_gui().getMainWindow(), title, label, value, 0.0, 1e6, decimals)
    return number if ok else None


def _ask_item(title, label, items):
    from PySide import QtWidgets

    item, ok = QtWidgets.QInputDialog.getItem(_gui().getMainWindow(), title, label, items, 0, False)
    return item if ok else None


def _run(doc, name, fn):
    """``fn()`` as one undo step; a SurfacingError is told in plain words and undone."""
    doc.openTransaction(name)
    try:
        out = fn()
    except bs.SurfacingError as exc:
        doc.abortTransaction()
        doc.recompute()
        _tell(str(exc))
        return None
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return out


def face_edge_names(obj, face_name, edge_names):
    """The object's edges ``edge_names`` as edges of its face ``face_name`` alone ("Edge2" of the face)."""
    shape = obj.Shape
    face = shape.getElement(face_name)
    out = []
    for name in edge_names:
        edge = shape.getElement(name)
        idx = next((i for i, e in enumerate(face.Edges, 1) if e.isSame(edge)), None)
        if idx is None:
            raise bs.SurfacingError(f"{name} is not an edge of {face_name}")
        out.append(f"Edge{idx}")
    return out


def trim():
    sel = _selection()
    if len(sel) < 2:
        return _tell("Select the surface to trim, then the curve (a sketch or edges).")
    (obj, subs), tools = sel[0], sel[1:]
    faces = [s for s in subs if s.startswith("Face")]
    doc = obj.Document

    def go():
        keep = "outside"
        tool = [(o, tuple(s for s in ss if s.startswith("Edge"))) for o, ss in tools]
        try:
            return bs.make_trim(doc, (obj, faces), tool, keep)
        except bs.SurfacingError:
            return bs.make_trim(doc, (obj, faces), tool, "larger")  # an open curve: keep the larger piece

    return _run(doc, "Trim", go)


def untrim():
    sel = _selection()
    if not sel:
        return _tell("Select the trimmed face (or surface).")
    obj, subs = sel[0]
    faces = [s for s in subs if s.startswith("Face")]
    return _run(obj.Document, "Untrim", lambda: bs.make_untrim(obj.Document, (obj, faces), "Holes"))


def extend(distance=None, mode=None):
    sel = _selection()
    if not sel:
        return _tell("Select a face and the edges of it to extend past.")
    obj, subs = sel[0]
    faces = [s for s in subs if s.startswith("Face")]
    edges = [s for o, ss in sel if o is obj for s in ss if s.startswith("Edge")]
    if not faces and len(obj.Shape.Faces) == 1:
        faces = ["Face1"]
    if len(faces) != 1 or not edges:
        return _tell("Select one face and the edges of it to extend past (where the surface ends).")
    if distance is None:
        distance = _ask_number("Extend", "Distance (mm)", 10.0)
        if distance is None:
            return None
    if mode is None:
        mode = _ask_item("Extend", "How", ["Natural", "Tangent"])
        if mode is None:
            return None
    doc = obj.Document

    def go():
        local = face_edge_names(obj, faces[0], edges)
        return bs.make_extend(doc, (obj, faces[0]), local, distance, mode)

    return _run(doc, "Extend", go)


def patch():
    sel = _selection()
    edges = [(o, s) for o, ss in sel for s in ss if s.startswith("Edge")]
    if len(edges) != 1:
        return _tell("Select one edge of the hole to fill.")
    obj, edge = edges[0]
    return _run(obj.Document, "Patch", lambda: bs.patch(obj, edge, 1))


def stitch():
    sel = _selection()
    if not sel:
        return _tell("Select the surfaces to stitch.")
    doc = sel[0][0].Document
    shapes = [(o, tuple(s for s in ss if s.startswith("Face"))) for o, ss in sel]
    return _run(doc, "Stitch", lambda: bs.make_stitch(doc, shapes))


def thicken(thickness=None, side=None):
    sel = _selection()
    if not sel:
        return _tell("Select the surface to thicken.")
    obj, subs = sel[0]
    faces = [s for s in subs if s.startswith("Face")]
    if thickness is None:
        thickness = _ask_number("Thicken", "Thickness (mm)", 1.0)
        if thickness is None:
            return None
    if side is None:
        side = _ask_item("Thicken", "Side", ["Outside", "Inside", "Both"])
        if side is None:
            return None
    return _run(obj.Document, "Thicken", lambda: bs.make_thicken(obj.Document, (obj, faces), thickness, side))


def replace_face():
    sel = _selection()
    if len(sel) < 2:
        return _tell("Select the face of the solid to replace, then the surface.")
    (solid, subs), (tool, tsubs) = sel[0], sel[1]
    faces = [s for s in subs if s.startswith("Face")]
    if len(faces) != 1:
        return _tell("Select one face of the solid first.")
    tfaces = tuple(s for s in tsubs if s.startswith("Face"))
    doc = solid.Document
    return _run(doc, "Replace face", lambda: bs.make_replace_face(doc, solid, faces[0], (tool, tfaces)))


# ----- continuity -----------------------------------------------------------------------------------------

def check_shapes(selection=None, doc=None):
    """(shapes, what) to check: the selected objects or faces; else every visible surface in ``doc``."""
    import Part

    selection = _selection() if selection is None else selection
    shapes = []
    for obj, subs in selection:
        faces = [s for s in subs if s.startswith("Face")]
        if faces:
            shapes.append(Part.makeCompound([obj.Shape.getElement(f) for f in faces])
                          if len(faces) > 1 else obj.Shape.getElement(faces[0]))
        else:
            shape = Part.getShape(obj)
            if not shape.isNull() and shape.Faces:
                shapes.append(shape)
    if shapes:
        return shapes, "the selection"
    doc = doc or _app().ActiveDocument
    for obj in doc.Objects if doc else []:
        if not obj.isDerivedFrom("Part::Feature") or not getattr(obj, "Visibility", False):
            continue
        shape = obj.Shape
        if not shape.isNull() and shape.Faces and not shape.Solids:
            shapes.append(Part.getShape(obj))
    return shapes, "every visible surface"


def _app():
    import FreeCAD

    return FreeCAD


def build_node(rows, width=4.0):
    """A Coin separator drawing each checked edge in its level's colour (unpickable, on top of the faces)."""
    from pivy import coin

    root = coin.SoSeparator()
    root.setName(NODE_NAME)
    pick = coin.SoPickStyle()
    pick.style = coin.SoPickStyle.UNPICKABLE
    root.addChild(pick)
    style = coin.SoDrawStyle()
    style.lineWidth = width
    root.addChild(style)
    light = coin.SoLightModel()
    light.model = coin.SoLightModel.BASE_COLOR
    root.addChild(light)
    for level, colour in COLOURS.items():
        mine = [r for r in rows if r["level"] == level]
        if not mine:
            continue
        sep = coin.SoSeparator()
        col = coin.SoBaseColor()
        col.rgb = colour
        sep.addChild(col)
        points, counts = [], []
        for r in mine:
            pts = r["edge"].discretize(Deflection=max(r["edge"].Length * 1e-3, 1e-3))
            points += [(p.x, p.y, p.z) for p in pts]
            counts.append(len(pts))
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(points), points)
        sep.addChild(coords)
        lines = coin.SoLineSet()
        lines.numVertices.setValues(0, len(counts), counts)
        sep.addChild(lines)
        root.addChild(sep)
    return root


def _panel_class():
    from PySide import QtWidgets

    class ContinuityPanel:
        """The rows of a continuity check, its colours in the view, and Check again."""

        def __init__(self, shapes, what, doc):
            self.shapes, self.what, self.doc = shapes, what, doc
            self.node, self.view, self.rows = None, None, []
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle("Continuity")
            lay = QtWidgets.QVBoxLayout(self.form)
            self.head = QtWidgets.QLabel()
            self.head.setWordWrap(True)
            lay.addWidget(self.head)
            self.table = QtWidgets.QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["#", "Level", "Gap (mm)", "Angle (°)", "Curvature change"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            lay.addWidget(self.table)
            tol = bs.tolerances()
            note = QtWidgets.QLabel(
                f"Limits: gap {tol['g0']:g} mm, angle {math.degrees(tol['g1']):.3g}°, curvature change "
                f"{tol['g2'] * 100:g} % (OCCT's defaults unless changed in Preferences). Green G2, yellow G1, "
                "orange G0, red a gap.")
            note.setWordWrap(True)
            lay.addWidget(note)
            again = QtWidgets.QPushButton("Check again")
            again.clicked.connect(slot(self.recheck))
            lay.addWidget(again)
            self.recheck()

        def recheck(self, *_args):
            self.rows = bs.check(self.shapes)
            counts = bs.summary(self.rows)
            if self.rows:
                self.head.setText(f"{len(self.rows)} edges of {self.what}: " + ", ".join(
                    f"{counts[k]} {k if k != 'none' else 'gap'}" for k in ("G2", "G1", "G0", "none") if counts[k]))
            else:
                self.head.setText(f"No edges where surfaces meet in {self.what}.")
            self.table.setRowCount(len(self.rows))
            for i, r in enumerate(self.rows):
                values = [str(r["index"]), r["level"] if r["level"] != "none" else "gap", f"{r['g0']:.2g}",
                          f"{math.degrees(r['g1']):.3g}", f"{r['g2'] * 100:.3g} %"]
                for j, text in enumerate(values):
                    self.table.setItem(i, j, QtWidgets.QTableWidgetItem(text))
            self.table.resizeColumnsToContents()
            self.draw()

        def draw(self):
            self.clear()
            gdoc = _gui().getDocument(self.doc.Name) if self.doc else None
            view = getattr(gdoc, "ActiveView", None) if gdoc else None
            if view is None or not hasattr(view, "getSceneGraph"):
                return
            self.node, self.view = build_node(self.rows), view
            view.getSceneGraph().addChild(self.node)

        def clear(self):
            if self.node is not None and self.view is not None:
                try:
                    self.view.getSceneGraph().removeChild(self.node)
                except Exception:  # noqa: BLE001 -- the view is gone
                    pass
            self.node = self.view = None

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def reject(self):
            self.clear()
            _gui().Control.closeDialog()
            _state["panel"] = None
            return True

        def accept(self):
            return self.reject()

    return ContinuityPanel


def continuity(selection=None):
    """Open the Continuity panel on the selection (or every visible surface). Returns the panel."""
    Gui = _gui()
    doc = _app().ActiveDocument
    if doc is None:
        return None
    shapes, what = check_shapes(selection, doc)
    if not shapes:
        _tell("Nothing to check: select surfaces or faces, or show some.")
        return None
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    panel = _panel_class()(shapes, what, doc)
    _state["panel"] = panel
    Gui.Control.showDialog(panel)
    return panel


ACTIONS = {"trim": trim, "untrim": untrim, "extend": extend, "patch": patch, "stitch": stitch,
           "thicken": thicken, "replace": replace_face, "continuity": continuity}


class _Command:
    def __init__(self, key):
        self.key = key

    def GetResources(self):
        name, menu, icon, tip = COMMANDS[self.key]
        return {"Pixmap": os.path.join(HERE, icon), "MenuText": menu, "ToolTip": tip}

    def IsActive(self):
        return _app().ActiveDocument is not None and bs.enabled()

    def Activated(self):
        ACTIONS[self.key]()


def install():
    Gui = _gui()
    for key, (name, *_rest) in COMMANDS.items():
        Gui.addCommand(name, _Command(key))
