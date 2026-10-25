# SPDX-License-Identifier: LGPL-2.1-or-later
"""Persistent section views (roadmap MOD-16).

A section is a document object kept in a "Sections" folder at the root of the file,
saved with it like a view: the eye turns it on and off, double-click edits it. Up to
three planes cut at once; a section with a second plane is a quarter section, which
removes one corner and keeps the rest. Only the model is cut, never the grid.

The cut is drawn by the viewer (View3DInventorViewer::setSectionPasses, the C++ caps of
SoFCSectionCap from FreeCAD PR #33186, extended): each part's cap in its colour with a
hatch of its own laid on the plane, and red where two parts' material overlaps on the
plane. Nothing here builds geometry.

A plane is (normal, distance): a point p is kept when normal . p >= distance, so the
normal points into the kept side and the removed side faces the camera when the
normal runs along the view direction.

Opened without BruceWorx, the objects load, keep their values and do nothing.
"""

from __future__ import annotations

import itertools
import os
import time

GROUP_NAME = "Sections"
SECTION_NAME = "Section"
LIMIT = 3  # planes cutting at once
LIFT = 0.35  # grey mixed into the caps, so dark parts' caps and hatches read
MIN_CLASH_PIXELS = 12  # smaller overlaps are touching faces (a bearing in its bore), not clashes
MODES = ["XY", "XZ", "YZ", "Face", "View", "Three points"]
CAPS = ["Part colour", "One colour", "None"]
FILL = {"Part colour": "part", "One colour": "single", "None": "none"}
NOT_CUT = ["Nothing", "Purchased (COTS) parts", "Chosen parts"]
HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "BruceWorx_Section.svg")
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"

_state = {"installed": False, "pending": set(), "observer": None, "editing": None}


# ----- plane arithmetic (unit tested) -------------------------------------------------------------


def _app():
    import FreeCAD

    return FreeCAD


def plane_of(placement, offset=0.0, tilt_a=0.0, tilt_b=0.0, flipped=False):
    """(normal, distance) of a section plane: ``placement``'s Z is the normal and its origin the
    point the tilts turn about; tilt_a turns about its X, tilt_b about its Y (degrees); the
    offset moves the plane along the tilted normal; flipped keeps the other side."""
    App = _app()
    rot = placement.Rotation
    n = rot.multVec(App.Vector(0, 0, 1))
    u = rot.multVec(App.Vector(1, 0, 0))
    v = rot.multVec(App.Vector(0, 1, 0))
    turn = App.Rotation(v, float(tilt_b)).multiply(App.Rotation(u, float(tilt_a)))
    n = turn.multVec(n)
    n.normalize()
    point = placement.Base + n * float(offset)
    if flipped:
        n = -n
    return n, n.dot(point)


def handle_frame(placement, tilt_a=0.0, tilt_b=0.0, flipped=False):
    """The handles' rotation: its Z the cut's normal (``plane_of``, into the kept side), its X and
    Y the plane's own axes turned by the tilts, so the TiltA ring turns about X and the TiltB ring
    about Y; a flipped plane is turned half round its X."""
    App = _app()
    rot = placement.Rotation
    u = rot.multVec(App.Vector(1, 0, 0))
    v = rot.multVec(App.Vector(0, 1, 0))
    turn = App.Rotation(v, float(tilt_b)).multiply(App.Rotation(u, float(tilt_a)))
    frame = turn.multiply(rot)
    if flipped:
        frame = frame.multiply(App.Rotation(App.Vector(1, 0, 0), 180))
    return frame


def drag_signs(flipped):
    """(offset, TiltA, TiltB) per unit of handle motion. The arrow points out of the cut (the
    frame's -Z): along the plane's own normal when flipped, against it when not. The flip turns
    the frame half round X, which reverses Y (the TiltB ring) but not X (TiltA)."""
    return (1.0, 1.0, -1.0) if flipped else (-1.0, 1.0, 1.0)


def flip(plane):
    n, d = plane
    return -n, -d


def facing(normal, view_dir):
    """The normal, or its opposite, whichever runs along the view: the removed side then faces you."""
    return normal if normal.dot(view_dir) >= 0 else -normal


def axis_normal(mode):
    App = _app()
    return {"XY": App.Vector(0, 0, 1), "XZ": App.Vector(0, 1, 0), "YZ": App.Vector(1, 0, 0)}[mode]


def placement_for(normal, base=None):
    """A placement whose Z is ``normal``, at ``base`` (the origin by default)."""
    App = _app()
    return App.Placement(base if base is not None else App.Vector(), App.Rotation(App.Vector(0, 0, 1), normal))


def three_point_normal(a, b, c):
    n = (b - a).cross(c - a)
    if n.Length < 1e-9:
        raise ValueError("the three points are on one line")
    n.normalize()
    return n


def pieces(planes):
    """A section's kept region as disjoint passes: one plane is one pass; a quarter section keeps
    H1 or H2, drawn as H1 and (H2 and not H1). Returns (passes, seams): seams are the flipped
    planes that only split the passes, never capped."""
    if len(planes) == 1:
        return [[planes[0]]], []
    first, second = planes[0], planes[1]
    seam = flip(first)
    return [[first], [second, seam]], [seam]


def passes_for(sections):
    """Passes for several sections at once: each removes its part, so the kept region is the
    intersection of theirs, and every combination of their pieces is one pass."""
    if not sections:
        return [], []
    parts = [pieces(planes) for planes in sections]
    seams = [seam for _p, s in parts for seam in s]
    passes = [list(itertools.chain.from_iterable(combo)) for combo in itertools.product(*(p for p, _s in parts))]
    return passes, seams


def plain(plane):
    """A plane as the viewer takes it: ((x, y, z), d)."""
    n, d = plane
    return (n.x, n.y, n.z), float(d)


def planes_in_use(counts):
    return sum(counts)


def to_turn_off(shown, new_count, limit=LIMIT):
    """Which sections to turn off so a new one with ``new_count`` planes fits: ``shown`` is
    [(key, planes, when shown)], the one shown longest ago goes first."""
    used = sum(n for _k, n, _t in shown) + new_count
    out = []
    for key, n, _t in sorted(shown, key=lambda s: s[2]):
        if used <= limit:
            break
        out.append(key)
        used -= n
    return out


def fit_key(a, b):
    return "\t".join(sorted((a, b)))


def object_key(obj):
    """'Doc#Name', as the viewer names occurrences."""
    return f"{obj.Document.Name}#{obj.Name}"


def label_of_key(key):
    """'LE_02_01002_001#Assembly/1/LE_02_01002_001#Sun/2/0' -> the label of the last object named in it."""
    App = _app()
    names = [part for part in key.replace("\t", "/").split("/") if "#" in part]
    for name in reversed(names):
        doc_name, obj_name = name.split("#", 1)
        doc = App.listDocuments().get(doc_name)
        obj = doc.getObject(obj_name) if doc else None
        if obj is not None:
            return obj.Label
    return names[-1].split("#", 1)[-1] if names else key


# ----- the document object ------------------------------------------------------------------------


def _add(obj, kind, name, doc, group="Section"):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, group, doc)


def add_properties(obj):
    _add(obj, "App::PropertyPlacement", "Plane", "The plane: its Z is the normal (into the kept side), its origin the point the tilts turn about")
    _add(obj, "App::PropertyEnumeration", "Mode", "How the plane was made")
    _add(obj, "App::PropertyLinkSub", "Support", "The face or datum plane a Face section stays on")
    _add(obj, "App::PropertyDistance", "Offset", "Distance of the plane along its normal")
    _add(obj, "App::PropertyAngle", "TiltA", "Tilt about the plane's first axis")
    _add(obj, "App::PropertyAngle", "TiltB", "Tilt about the plane's second axis")
    _add(obj, "App::PropertyBool", "Flipped", "Keep the other side")
    _add(obj, "App::PropertyBool", "Quarter", "A second plane: remove one corner and keep three quarters", "Second plane")
    _add(obj, "App::PropertyPlacement", "Plane2", "The second plane", "Second plane")
    _add(obj, "App::PropertyDistance", "Offset2", "Distance of the second plane along its normal", "Second plane")
    _add(obj, "App::PropertyBool", "Flipped2", "Keep the second plane's other side", "Second plane")
    _add(obj, "App::PropertyEnumeration", "Caps", "How the cut faces are filled", "Caps")
    _add(obj, "App::PropertyColor", "CapColor", "The caps' colour with Caps 'One colour'", "Caps")
    _add(obj, "App::PropertyBool", "Hatch", "A hatch on the caps, a pattern per part", "Caps")
    _add(obj, "App::PropertyBool", "ShowClash", "Red where two parts' material overlaps on the plane", "Caps")
    _add(obj, "App::PropertyStringList", "Fits", "Part pairs whose overlap is intended (not red)", "Caps")
    _add(obj, "App::PropertyEnumeration", "NotCut", "Parts left whole", "Caps")
    _add(obj, "App::PropertyLinkList", "NotCutParts", "The parts left whole with NotCut 'Chosen parts'", "Caps")
    _add(obj, "App::PropertyBool", "SaveCamera", "Turning the section on goes to the saved view", "View")
    _add(obj, "App::PropertyString", "Camera", "The view saved with the section", "View")
    _add(obj, "App::PropertyFloat", "ShownAt", "When the section was last turned on (the oldest goes first)", "View")
    for name, values in (("Mode", MODES), ("Caps", CAPS), ("NotCut", NOT_CUT)):
        current = getattr(obj, name)
        setattr(obj, name, values)
        if current in values:
            setattr(obj, name, current)
    for name in ("Camera", "ShownAt"):
        obj.setEditorMode(name, 2)


class SectionPlane:
    """The section's data. execute() keeps a Face section on its face."""

    def __init__(self, obj):
        obj.Proxy = self
        add_properties(obj)
        obj.Mode = "XZ"
        obj.Caps = "Part colour"
        obj.CapColor = (0.62, 0.64, 0.68)
        obj.Hatch = True
        obj.ShowClash = True
        obj.NotCut = "Nothing"

    def onDocumentRestored(self, obj):
        add_properties(obj)

    def execute(self, obj):
        if obj.Mode == "Face" and obj.Support:
            plc = face_placement(obj.Support)
            if plc is not None and not plc.isSame(obj.Plane, 1e-9):
                obj.Plane = plc

    def onChanged(self, obj, prop):
        if prop not in ("ShownAt", "Camera", "Label", "Label2", "ExpressionEngine"):
            schedule(obj.Document)

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def face_placement(support):
    """Placement on the centre of a planar face (or a datum plane), Z along its normal."""
    App = _app()
    obj, subs = support
    try:
        if subs and subs[0]:
            shape = obj.getSubObject(subs[0])
            if shape is None or not hasattr(shape, "normalAt"):
                return None
            u0, u1, v0, v1 = shape.ParameterRange
            n = shape.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            return placement_for(n, shape.CenterOfMass)
        plc = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
        return App.Placement(plc.Base, plc.Rotation)
    except Exception:  # noqa: BLE001 -- a support that went away keeps the last plane
        return None


def is_section(obj):
    return obj is not None and getattr(obj, "Proxy", None).__class__.__name__ == "SectionPlane"


def sections(doc):
    return [o for o in doc.Objects if is_section(o)]


def group(doc, create=True):
    g = doc.getObject(GROUP_NAME)
    if g is None and create:
        g = doc.addObject("App::DocumentObjectGroup", GROUP_NAME)
        g.Label = GROUP_NAME
    return g


def section_planes(obj):
    """The section's planes, as (normal, distance)."""
    planes = [plane_of(obj.Plane, obj.Offset.Value, obj.TiltA.Value, obj.TiltB.Value, obj.Flipped)]
    if obj.Quarter:
        planes.append(plane_of(obj.Plane2, obj.Offset2.Value, 0.0, 0.0, obj.Flipped2))
    return planes


def shown(obj):
    return bool(getattr(obj, "Visibility", False))


def cots_links(doc):
    """Objects in the file whose part is purchased (ItemType 'cots'): the links to them too."""
    out = []
    for obj in doc.Objects:
        target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
        if target is not None and getattr(target, "ItemType", "") == "cots":
            out.append(obj)
    return out


def uncut_objects(obj):
    if obj.NotCut == "Purchased (COTS) parts":
        return cots_links(obj.Document)
    if obj.NotCut == "Chosen parts":
        return list(obj.NotCutParts)
    return []


def style_of(objs):
    """The viewer's cap style for the sections shown: the latest one's look; fits and uncut
    parts of all of them."""
    latest = max(objs, key=lambda o: o.ShownAt)
    c = latest.CapColor
    fits = [tuple(f.split("\t", 1)) for o in objs for f in o.Fits if "\t" in f]
    try:  # and the fits marked in the Interference check (ASM-06), kept on the assembly
        import bruceworx_interference

        fits += [tuple(k.split("\t", 1)) for k in bruceworx_interference.fits(latest.Document) if "\t" in k]
    except Exception:  # noqa: BLE001 -- no interference module: the sections' own fits
        pass
    fits = sorted(set(fits))
    uncut = []
    for o in objs:
        for part in uncut_objects(o):
            uncut.append(object_key(part))
            target = part.getLinkedObject(True) if hasattr(part, "getLinkedObject") else None
            if target is not None and target is not part:
                uncut.append(object_key(target))
    return {"fill": FILL.get(latest.Caps, "part"), "single": (c[0], c[1], c[2]), "hatch": bool(latest.Hatch),
            "planeHatch": True, "clash": bool(latest.ShowClash), "lift": LIFT, "minClashPixels": MIN_CLASH_PIXELS,
            "fits": fits, "uncut": sorted(set(uncut))}


# ----- putting the sections into the views ---------------------------------------------------------


def views_of(doc):
    import FreeCADGui as Gui

    gdoc = Gui.getDocument(doc.Name) if doc is not None else None
    if gdoc is None:
        return []
    try:
        return [v for v in gdoc.mdiViewsOfType("Gui::View3DInventor") if hasattr(v, "setSectionPasses")]
    except Exception:  # noqa: BLE001
        return []


def schedule(doc):
    """Refresh the views of ``doc`` once the current change is done."""
    if doc is None:
        return
    pending = _state["pending"]
    if doc.Name in pending:
        return
    pending.add(doc.Name)
    try:
        from PySide import QtCore
    except ImportError:
        pending.discard(doc.Name)
        return

    def run(name=doc.Name):
        pending.discard(name)
        d = _app().listDocuments().get(name)
        if d is not None:
            refresh(d)

    QtCore.QTimer.singleShot(0, run)


def refresh(doc):
    """Cut every 3D view of ``doc`` by its sections that are on, or make it whole."""
    on = sorted((o for o in sections(doc) if shown(o)), key=lambda o: o.ShownAt)
    passes, seams = passes_for([section_planes(o) for o in on])
    style = style_of(on) if on else None
    if style is not None:
        style["seams"] = [plain(s) for s in seams]
    for view in views_of(doc):
        try:
            if passes:
                view.setSectionPasses([[plain(p) for p in ps] for ps in passes], style)
            else:
                view.clearSectionPasses()
            _show_whole(view, doc, style["uncut"] if style else [])
        except Exception as exc:  # noqa: BLE001
            _app().Console.PrintWarning(f"BruceWorx section: {exc}\n")
    if _app().GuiUp and doc is _app().ActiveDocument:
        sync_button(doc)
    return passes


WHOLE_NODE = "BruceWorxSectionWhole"


def _show_whole(view, doc, uncut):
    """Parts left whole are drawn again outside the cut, at their place in the file."""
    from pivy import coin

    root = view.getSceneGraph()
    for i in range(root.getNumChildren() - 1, -1, -1):
        if root.getChild(i).getName().getString() == WHOLE_NODE:
            root.removeChild(i)
    if not uncut:
        return
    whole = coin.SoSeparator()
    whole.setName(WHOLE_NODE)
    for key in uncut:
        doc_name, name = key.split("#", 1)
        if doc_name != doc.Name:
            continue
        obj = doc.getObject(name)
        vo = getattr(obj, "ViewObject", None)
        if obj is None or vo is None or not vo.Visibility:
            continue
        parent = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
        plc = parent.getGlobalPlacement() if parent is not None else _app().Placement()
        sep = coin.SoSeparator()
        transform = coin.SoMatrixTransform()
        m = plc.toMatrix()
        transform.matrix.setValue(coin.SbMatrix(m.A11, m.A21, m.A31, m.A41, m.A12, m.A22, m.A32, m.A42,
                                                m.A13, m.A23, m.A33, m.A43, m.A14, m.A24, m.A34, m.A44))
        sep.addChild(transform)
        sep.addChild(vo.RootNode)
        whole.addChild(sep)
    root.addChild(whole)


def report(doc=None):
    """What the active view's sections drew in the last frame, with labels: {status, caps, clashes}."""
    import FreeCADGui as Gui

    view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    if view is None or not hasattr(view, "getSectionReport"):
        return {"status": "off", "caps": [], "clashes": []}
    r = view.getSectionReport()
    pairs = {}
    for c in r["clashes"]:
        key = fit_key(c["first"], c["second"])
        entry = pairs.setdefault(key, {"first": label_of_key(c["firstKey"]), "second": label_of_key(c["secondKey"]),
                                       "components": (c["first"], c["second"]), "pixels": 0})
        entry["pixels"] += c["pixels"]
    r["pairs"] = sorted(pairs.values(), key=lambda e: -e["pixels"])
    return r


# ----- turning sections on and off ---------------------------------------------------------------


def turn_on(obj, view=None, own_step=True):
    """Turn a section on; if more than three planes would cut, the one shown longest ago goes off,
    in its own undo step (its toast offers Undo). Inside an open edit (own_step False) the change
    joins the edit's step, so Cancel brings the other section back."""
    doc = obj.Document
    on = [(o.Name, len(section_planes(o)), o.ShownAt) for o in sections(doc) if shown(o) and o is not obj]
    drop = to_turn_off(on, len(section_planes(obj)))
    if drop:
        labels = ", ".join(doc.getObject(n).Label for n in drop)
        if own_step:
            doc.openTransaction(f"Turned off {labels} (3 planes at most)")
        for name in drop:
            doc.getObject(name).Visibility = False
        if own_step:
            doc.commitTransaction()
        else:
            _app().Console.PrintMessage(f"Section: turned off {labels} (3 planes at most).\n")
    obj.ShownAt = time.time()
    obj.Visibility = True
    if obj.SaveCamera and obj.Camera and view is not None:
        try:
            view.setCamera(obj.Camera)
        except Exception:  # noqa: BLE001
            pass
    schedule(doc)


class ViewProviderSection:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ICON

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            obj = vobj.Object
            if vobj.Visibility and obj.ShownAt <= 0:
                obj.ShownAt = time.time()
            schedule(obj.Document)

    def updateData(self, obj, prop):
        pass

    def doubleClicked(self, vobj):
        edit(vobj.Object)
        return True

    def setupContextMenu(self, vobj, menu):
        action = menu.addAction("Edit Section")
        action.triggered.connect(lambda: edit(vobj.Object))

    def setEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        edit(vobj.Object)
        return True

    def unsetEdit(self, vobj, mode=0):
        return False

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def make(doc, mode="XZ", view=None, support=None, points=None, label=None):
    """A new section, on and facing the view. ``support`` (obj, [sub]) for Face, ``points`` for Three points."""
    App = _app()
    view_dir = App.Vector(*view.getViewDirection()) if view is not None else App.Vector(-1, 1, -1)
    obj = doc.addObject("App::FeaturePython", SECTION_NAME)
    SectionPlane(obj)
    obj.Mode = mode
    if mode in ("XY", "XZ", "YZ"):
        obj.Plane = placement_for(facing(axis_normal(mode), view_dir))
    elif mode == "Face" and support is not None:
        obj.Support = support
        plc = face_placement(support)
        if plc is not None:
            obj.Plane = plc
            n = plc.Rotation.multVec(App.Vector(0, 0, 1))
            obj.Flipped = n.dot(view_dir) < 0
    elif mode == "Three points" and points:
        a, b, c = points[:3]
        obj.Plane = placement_for(facing(three_point_normal(a, b, c), view_dir), a)
    else:  # View: square to the view, through the middle of the model
        obj.Plane = placement_for(view_dir, _model_centre(doc))
        obj.Mode = "View"
    obj.Plane2 = placement_for(facing(_second_axis(obj.Plane, view_dir), view_dir), obj.Plane.Base)
    obj.Label = label or f"Section {len(sections(doc))}"
    if App.GuiUp:
        ViewProviderSection(obj.ViewObject)
    group(doc).addObject(obj)
    turn_on(obj, view)
    return obj


def _second_axis(plane, view_dir):
    """The second plane of a quarter section: square to the first, the one most along the view."""
    App = _app()
    rot = plane.Rotation
    u, v = rot.multVec(App.Vector(1, 0, 0)), rot.multVec(App.Vector(0, 1, 0))
    return u if abs(u.dot(view_dir)) >= abs(v.dot(view_dir)) else v


def _model_box(doc):
    """The box of the solids shown (datums are endless, sketches flat: not counted)."""
    App = _app()
    box = App.BoundBox()
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if (shape is None or shape.isNull() or not shape.Solids or not getattr(obj, "Visibility", False)
                or shape.BoundBox.DiagonalLength > 1e7):
            continue
        box.add(shape.BoundBox)
    return box


def _model_centre(doc):
    box = _model_box(doc)
    return box.Center if box.isValid() else _app().Vector()


# ----- editing: the Section tab and the handles on the canvas --------------------------------------


def edit(obj):
    """Open the Section tab, after the command or double-click that asked returns: a command's own
    undo step would otherwise take in the edit's."""
    from PySide import QtCore

    QtCore.QTimer.singleShot(0, lambda name=obj.Name, doc=obj.Document.Name: _edit_now(doc, name))


def _edit_now(doc_name, name):
    import FreeCADGui as Gui

    doc = _app().listDocuments().get(doc_name)
    obj = doc.getObject(name) if doc else None
    if obj is None:
        return
    if _state["editing"] is not None:  # another section's tab: kept, its step closed
        _state["editing"].accept()
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    panel = SectionPanel(obj)
    _state["editing"] = panel
    Gui.Control.showDialog(panel)


class SectionPanel:
    """The Section command tab: plane, offset and tilts (fields that take expressions), Flip and
    the second plane, the caps, the clash list, the view options. OK keeps, Cancel undoes."""

    WATCHED = ("Mode", "Offset", "TiltA", "TiltB", "Flipped", "Quarter", "Offset2", "Flipped2", "Caps",
               "Hatch", "ShowClash", "NotCut", "SaveCamera")

    def __init__(self, obj):
        from PySide import QtCore, QtWidgets
        import FreeCADGui as Gui

        self.obj = obj
        self.doc = obj.Document
        if not shown(obj):
            turn_on(obj, _active_view())
        self.doc.openTransaction(f"Edit {obj.Label}")
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Section")
        self.form.setObjectName("bwSectionPanel")
        lay = QtWidgets.QVBoxLayout(self.form)
        title = QtWidgets.QLabel(f"<b>{obj.Label}</b>")
        lay.addWidget(title)
        grid = QtWidgets.QFormLayout()
        lay.addLayout(grid)
        # plane: segmented buttons
        seg = QtWidgets.QHBoxLayout()
        self.mode_buttons = {}
        for mode in ("XY", "XZ", "YZ", "Face", "View"):
            b = QtWidgets.QToolButton()
            b.setText(mode)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setChecked(obj.Mode == mode)
            b.clicked.connect(lambda _c=False, m=mode: self.set_mode(m))
            seg.addWidget(b)
            self.mode_buttons[mode] = b
        grid.addRow("Plane", seg)
        self.binds = []
        self.offset = self._quantity("Offset", "mm")
        grid.addRow("Offset", self.offset)
        tilts = QtWidgets.QHBoxLayout()
        self.tilt_a = self._quantity("TiltA", "deg")
        self.tilt_b = self._quantity("TiltB", "deg")
        tilts.addWidget(self.tilt_a)
        tilts.addWidget(self.tilt_b)
        grid.addRow("Tilt", tilts)
        row = QtWidgets.QHBoxLayout()
        flip_button = QtWidgets.QPushButton("Flip")
        flip_button.clicked.connect(self.flip)
        self.quarter = QtWidgets.QCheckBox("Second plane (quarter)")
        self.quarter.setChecked(obj.Quarter)
        self.quarter.toggled.connect(self.set_quarter)
        row.addWidget(flip_button)
        row.addWidget(self.quarter)
        grid.addRow("", row)
        self.offset2 = self._quantity("Offset2", "mm")
        grid.addRow("Second offset", self.offset2)
        # caps
        lay.addWidget(self._heading("Caps"))
        caps = QtWidgets.QFormLayout()
        lay.addLayout(caps)
        self.caps = QtWidgets.QComboBox()
        self.caps.addItems(CAPS)
        self.caps.setCurrentText(obj.Caps)
        self.caps.currentTextChanged.connect(lambda t: self._set("Caps", t))
        caps.addRow("Colour", self.caps)
        self.hatch = QtWidgets.QCheckBox("Hatch, a pattern per part")
        self.hatch.setChecked(obj.Hatch)
        self.hatch.toggled.connect(lambda v: self._set("Hatch", v))
        caps.addRow("", self.hatch)
        self.clash = QtWidgets.QCheckBox("Clashes in red")
        self.clash.setChecked(obj.ShowClash)
        self.clash.toggled.connect(lambda v: self._set("ShowClash", v))
        caps.addRow("", self.clash)
        self.not_cut = QtWidgets.QComboBox()
        self.not_cut.addItems(NOT_CUT)
        self.not_cut.setCurrentText(obj.NotCut)
        self.not_cut.currentTextChanged.connect(self.set_not_cut)
        caps.addRow("Don't cut", self.not_cut)
        # clash list
        self.clash_list = QtWidgets.QListWidget()
        self.clash_list.setObjectName("bwSectionClashes")
        self.clash_list.setMaximumHeight(110)
        lay.addWidget(self._heading("Clashes on the plane"))
        lay.addWidget(self.clash_list)
        buttons = QtWidgets.QHBoxLayout()
        fit = QtWidgets.QPushButton("Mark as fit")
        fit.setToolTip("The overlap is intended (a screw in a tapped hole): no longer red")
        fit.clicked.connect(self.mark_fit)
        measure = QtWidgets.QPushButton("Measure")
        measure.setToolTip("The exact overlap volume of the pair")
        measure.clicked.connect(self.measure)
        buttons.addWidget(fit)
        buttons.addWidget(measure)
        lay.addLayout(buttons)
        # view
        lay.addWidget(self._heading("View"))
        self.camera = QtWidgets.QCheckBox("Save the camera with the section")
        self.camera.setChecked(obj.SaveCamera)
        self.camera.toggled.connect(self.set_camera)
        lay.addWidget(self.camera)
        hint = QtWidgets.QLabel("Cuts every 3D view of this file. Drag the blue arrow to move the plane, a ring "
                                "to tilt it. Offset and tilt take expressions, e.g. Parameters.face_width / 2.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)
        lay.addStretch(1)
        self._sync_enabled()
        self.handles = Handles(obj)
        self.timer = QtCore.QTimer()
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.update_clashes)
        self.timer.start()

    # widgets
    def _heading(self, text):
        from PySide import QtWidgets

        label = QtWidgets.QLabel(f"<b>{text}</b>")
        label.setContentsMargins(0, 8, 0, 0)
        return label

    def _quantity(self, prop, unit):
        import FreeCADGui as Gui

        w = Gui.UiLoader().createWidget("Gui::QuantitySpinBox")
        w.setProperty("unit", unit)
        w.setProperty("minimum", -1e9)
        w.setProperty("maximum", 1e9)
        w.setProperty("value", getattr(self.obj, prop))
        try:
            binding = Gui.ExpressionBinding(w)
            binding.bind(self.obj, prop)
            self.binds.append(binding)
        except Exception:  # noqa: BLE001
            pass
        w.valueChanged.connect(lambda v, p=prop: self._set_quantity(p, v))
        return w

    def _set_quantity(self, prop, value):
        if self.obj.getExpression(prop) if hasattr(self.obj, "getExpression") else None:
            return
        setattr(self.obj, prop, value)
        self.doc.recompute()

    def _set(self, prop, value):
        setattr(self.obj, prop, value)
        self.doc.recompute()

    def _sync_enabled(self):
        self.offset2.setEnabled(self.obj.Quarter)

    # actions
    def set_mode(self, mode):
        import FreeCADGui as Gui

        view = _active_view()
        App = _app()
        view_dir = App.Vector(*view.getViewDirection()) if view is not None else App.Vector(-1, 1, -1)
        if mode in ("XY", "XZ", "YZ"):
            self.obj.Mode = mode
            self.obj.Support = None
            self.obj.Plane = placement_for(facing(axis_normal(mode), view_dir))
        elif mode == "Face":
            sel = Gui.Selection.getSelectionEx("", 0)
            picked = picked_faces(sel) or [(s.Object, [""]) for s in sel if not s.SubElementNames]
            if not picked:
                _app().Console.PrintMessage("Section: select a planar face or a datum plane, then press Face.\n")
                self.mode_buttons[self.obj.Mode if self.obj.Mode in self.mode_buttons else "XZ"].setChecked(True)
                return
            self.obj.Mode = "Face"
            self.obj.Support = picked[0]
            plc = face_placement(picked[0])
            if plc is not None:
                self.obj.Plane = plc
                self.obj.Flipped = plc.Rotation.multVec(App.Vector(0, 0, 1)).dot(view_dir) < 0
        else:
            self.obj.Mode = "View"
            self.obj.Support = None
            self.obj.Plane = placement_for(view_dir, _model_centre(self.doc))
        self.obj.Plane2 = placement_for(facing(_second_axis(self.obj.Plane, view_dir), view_dir), self.obj.Plane.Base)
        self.doc.recompute()
        self.handles.rebuild()

    def flip(self):
        self.obj.Flipped = not self.obj.Flipped
        self.doc.recompute()
        self.handles.rebuild()

    def set_quarter(self, on):
        self.obj.Quarter = on
        if on:
            turn_on(self.obj, _active_view(), own_step=False)  # two planes may push the oldest off
        self._sync_enabled()
        self.doc.recompute()

    def set_not_cut(self, text):
        import FreeCADGui as Gui

        if text == "Chosen parts":
            picked = [s.Object for s in Gui.Selection.getSelectionEx("", 0)]
            if picked:
                self.obj.NotCutParts = picked
        self._set("NotCut", text)

    def set_camera(self, on):
        self.obj.SaveCamera = on
        view = _active_view()
        if on and view is not None:
            self.obj.Camera = view.getCamera()

    def update_clashes(self):
        from PySide import QtCore

        try:
            r = report(self.doc)
        except Exception:  # noqa: BLE001
            return
        rows = [(f"{p['first']} / {p['second']}", p["components"]) for p in r.get("pairs", [])]
        if [self.clash_list.item(i).text() for i in range(self.clash_list.count())] == [t for t, _c in rows]:
            return
        current = self.clash_list.currentRow()
        self.clash_list.clear()
        for text, comps in rows:
            self.clash_list.addItem(text)
            self.clash_list.item(self.clash_list.count() - 1).setData(QtCore.Qt.UserRole, "\t".join(comps))
        if rows:
            self.clash_list.setCurrentRow(max(0, min(current, len(rows) - 1)))

    def _picked_pair(self):
        from PySide import QtCore

        item = self.clash_list.currentItem()
        return item.data(QtCore.Qt.UserRole).split("\t") if item is not None else None

    def mark_fit(self):
        pair = self._picked_pair()
        if pair:
            fits = list(self.obj.Fits)
            key = fit_key(*pair)
            if key not in fits:
                fits.append(key)
                self.obj.Fits = fits
            self.doc.recompute()

    def measure(self):
        pair = self._picked_pair()
        if not pair:
            return
        try:
            volume = overlap_volume(self.doc, *pair)
        except Exception as exc:  # noqa: BLE001
            _app().Console.PrintWarning(f"Section: could not measure: {exc}\n")
            return
        item = self.clash_list.currentItem()
        item.setText(item.text().split("  ·  ")[0] + f"  ·  {volume:.1f} mm³")

    # task dialog
    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        return self._close(commit=True)

    def reject(self):
        return self._close(commit=False)

    def _close(self, commit):
        import FreeCADGui as Gui

        self.timer.stop()
        self.handles.close()
        if commit:
            self.doc.commitTransaction()
        else:
            self.doc.abortTransaction()
        self.doc.recompute()
        schedule(self.doc)
        _state["editing"] = None
        Gui.Control.closeDialog()
        return True


def overlap_volume(doc, first, second):
    """Exact volume (mm³) shared by the two components' occurrences in the file."""
    import Part

    def shapes(component):
        doc_name, name = component.split("#", 1)
        out = []
        for obj in doc.Objects:
            target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
            if target is not None and target.Document.Name == doc_name and target.Name == name and obj.TypeId == "App::Link":
                out.append(Part.getShape(obj, "", needSubElement=False, transform=True))
        return out

    total = 0.0
    for a in shapes(first):
        for b in shapes(second):
            if a.BoundBox.intersect(b.BoundBox):
                total += a.common(b).Volume
    return total


class Handles:
    """On the canvas: the plane's frame, an arrow along its normal to drag the offset and two
    rings to tilt it (5° steps, Shift for 1°)."""

    def __init__(self, obj):
        self.obj = obj
        self.root = None
        self.callbacks = []
        self.active = True
        self.view = _active_view()
        self.rebuild()

    def close(self):
        """The editing is over: the handles go, and a rebuild still queued does nothing."""
        self.active = False
        self.remove()

    def remove(self):
        for dragger, kind, callback in getattr(self, "callbacks", []):
            try:
                self.view.removeDraggerCallback(dragger, kind, callback)
            except Exception:  # noqa: BLE001
                pass
        self.callbacks = []
        if self.root is not None and self.view is not None:
            try:
                self.view.getSceneGraph().removeChild(self.root)
            except Exception:  # noqa: BLE001
                pass
        self.root = None

    def _hook(self, dragger, kind, function):
        """A dragger callback through the view, which takes the GIL and keeps the function alive.
        (pivy's own addMotionCallback calls Python without the GIL: FreeCAD crashed on the first
        real drag, 25 Oct.)"""
        self.callbacks.append((dragger, kind, self.view.addDraggerCallback(dragger, kind, function)))

    def _rebuild_later(self):
        """After a drag: not from inside the dragger's own callback, which the rebuild removes."""
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, self.rebuild)

    def rebuild(self):
        from pivy import coin

        self.remove()
        if self.view is None or not self.active:
            return
        App = _app()
        n, d = section_planes(self.obj)[0]
        doc = self.obj.Document
        box = _model_box(doc)
        centre = box.Center if box.isValid() else App.Vector()
        centre = centre - n * (n.dot(centre) - d)
        rot = handle_frame(self.obj.Plane, self.obj.TiltA.Value, self.obj.TiltB.Value, self.obj.Flipped)
        self.signs = drag_signs(self.obj.Flipped)
        u, v = rot.multVec(App.Vector(1, 0, 0)), rot.multVec(App.Vector(0, 1, 0))
        # the frame: the model's extent on the plane, a little more
        half_u = half_v = 10.0
        if box.isValid():
            corners = [App.Vector(x, y, z) for x in (box.XMin, box.XMax) for y in (box.YMin, box.YMax)
                       for z in (box.ZMin, box.ZMax)]
            half_u = max(abs((c - centre).dot(u)) for c in corners) * 1.12
            half_v = max(abs((c - centre).dot(v)) for c in corners) * 1.12
        self.root = coin.SoSeparator()
        self.root.setName("BruceWorxSectionHandles")
        tr = coin.SoTransform()
        tr.translation.setValue(float(centre.x), float(centre.y), float(centre.z))
        q = rot.Q
        tr.rotation.setValue(q[0], q[1], q[2], q[3])
        self.root.addChild(tr)
        # drawn over the model, as handles are
        on_top = coin.SoDepthBuffer()
        on_top.test = False
        self.root.addChild(on_top)
        frame = coin.SoSeparator()
        colour = coin.SoBaseColor()
        colour.rgb.setValue(0.0, 0.48, 1.0)
        style = coin.SoDrawStyle()
        style.lineWidth = 1.5
        style.linePattern = 0xF0F0
        coords = coin.SoCoordinate3()
        hu, hv = float(half_u), float(half_v)
        coords.point.setValues(0, 5, [(-hu, -hv, 0), (hu, -hv, 0), (hu, hv, 0), (-hu, hv, 0), (-hu, -hv, 0)])
        line = coin.SoLineSet()
        line.numVertices.setValue(5)
        for node in (colour, style, coords, line):
            frame.addChild(node)
        self.root.addChild(frame)
        unit = float(max(min(hu, hv) * 0.18, 1.0))
        # offset arrow: a 1D dragger out of the cut, towards you (its own X turned onto -Z), blue
        arrow = coin.SoSeparator()
        turn = coin.SoRotation()
        turn.rotation.setValue(coin.SbVec3f(0, 1, 0), 1.5707963)
        scale = coin.SoScale()
        scale.scaleFactor.setValue(unit * 2.0, unit * 2.0, unit * 2.0)
        self.offset_dragger = coin.SoTranslate1Dragger()
        _dress_arrow(self.offset_dragger)
        self.start_offset = self.obj.Offset.Value
        self._hook(self.offset_dragger, "addMotionCallback", self._offset_moved)
        self._hook(self.offset_dragger, "addFinishCallback", self._offset_done)
        for node in (turn, scale, self.offset_dragger):
            arrow.addChild(node)
        self.root.addChild(arrow)
        self.arrow_scale = unit * 2.0
        # tilt rings: discs turning about the plane's X (TiltA, orange) and Y (TiltB, green)
        self.ring_draggers = []
        for axis_turn, angle, prop, rgb in (((0, 1, 0), 1.5707963, "TiltA", (0.91, 0.64, 0.09)),
                                           ((1, 0, 0), -1.5707963, "TiltB", (0.18, 0.70, 0.42))):
            ring = coin.SoSeparator()
            # out of the cut by its radius: the whole ring is in the cut-away half, where nothing of the
            # model is left to catch the click
            forward = coin.SoTranslation()
            forward.translation.setValue(0, 0, -unit * 1.6)
            r = coin.SoRotation()
            r.rotation.setValue(coin.SbVec3f(*axis_turn), angle)
            sc = coin.SoScale()
            sc.scaleFactor.setValue(unit * 1.6, unit * 1.6, unit * 1.6)
            dragger = coin.SoRotateDiscDragger()
            _dress_ring(dragger, rgb)
            ring.addChild(forward)
            self._hook(dragger, "addMotionCallback", lambda _d, p=prop, dr=dragger: self._tilt_moved(p, dr))
            self._hook(dragger, "addFinishCallback", lambda _d, p=prop, dr=dragger: self._tilt_done(p, dr))
            for node in (r, sc, dragger):
                ring.addChild(node)
            self.root.addChild(ring)
            self.ring_draggers.append((prop, dragger, getattr(self.obj, prop).Value))
        self.view.getSceneGraph().addChild(self.root)

    def _bound(self, prop):
        """True when the property follows an expression: the handle leaves it alone."""
        return any(path == prop for path, _e in (self.obj.ExpressionEngine or ()))

    def _offset_moved(self, _dragger=None):
        if self._bound("Offset"):
            return
        # the callback hands a plain SoDragger: read our own translate dragger
        moved = self.offset_dragger.translation.getValue()[0] * self.arrow_scale * self.signs[0]
        self.obj.Offset = self.start_offset + moved
        refresh(self.obj.Document)

    def _offset_done(self, _dragger=None):
        self.obj.Document.recompute()
        self._rebuild_later()

    def _tilt_moved(self, prop, dragger):
        from PySide import QtWidgets

        import math

        x, y, z, w = dragger.rotation.getValue().getValue()  # a quaternion; the disc turns about its Z
        degrees = math.degrees(2.0 * math.atan2(math.sqrt(x * x + y * y + z * z), w)) * (1 if z >= 0 else -1)
        degrees = (degrees + 180.0) % 360.0 - 180.0
        from PySide import QtCore

        shift = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
        step = 1.0 if shift else 5.0
        start = next(s for p, _d, s in self.ring_draggers if p == prop)
        sign = self.signs[1] if prop == "TiltA" else self.signs[2]
        setattr(self.obj, prop, start + round(sign * degrees / step) * step)
        refresh(self.obj.Document)

    def _tilt_done(self, _prop, _dragger):
        self.obj.Document.recompute()
        self._rebuild_later()


def _part(rgb, *nodes):
    """A dragger part: a colour, then the shapes."""
    from pivy import coin

    sep = coin.SoSeparator()
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*rgb)
    mat.emissiveColor.setValue(rgb[0] * 0.4, rgb[1] * 0.4, rgb[2] * 0.4)
    sep.addChild(mat)
    for node in nodes:
        sep.addChild(node)
    return sep


def _arrow_shapes():
    """An arrow along +X, one unit long: a shaft and a cone."""
    from pivy import coin

    shaft = coin.SoSeparator()
    t = coin.SoTransform()
    t.translation.setValue(0.5, 0, 0)
    t.rotation.setValue(coin.SbVec3f(0, 0, 1), -1.5707963)
    cyl = coin.SoCylinder()
    cyl.radius = 0.06
    cyl.height = 1.0
    shaft.addChild(t)
    shaft.addChild(cyl)
    tip = coin.SoSeparator()
    t2 = coin.SoTransform()
    t2.translation.setValue(1.12, 0, 0)
    t2.rotation.setValue(coin.SbVec3f(0, 0, 1), -1.5707963)
    cone = coin.SoCone()
    cone.bottomRadius = 0.16
    cone.height = 0.3
    tip.addChild(t2)
    tip.addChild(cone)
    return shaft, tip


def _dress_arrow(dragger):
    from pivy import coin

    dragger.setPart("translator", _part((0.0, 0.48, 1.0), *_arrow_shapes()))
    dragger.setPart("translatorActive", _part((0.35, 0.68, 1.0), *_arrow_shapes()))
    dragger.setPart("feedback", coin.SoSeparator())
    dragger.setPart("feedbackActive", coin.SoSeparator())


def _ring_shape():
    """A flat ring in the XY plane (radius 0.85 to 1), as triangles so it is easy to pick."""
    import math

    from pivy import coin

    n = 48
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((0.85 * math.cos(a), 0.85 * math.sin(a), 0.0))
        pts.append((math.cos(a), math.sin(a), 0.0))
    idx = []
    for i in range(n):
        a, b, c, d = 2 * i, 2 * i + 1, (2 * i + 2) % (2 * n), (2 * i + 3) % (2 * n)
        idx += [a, b, d, c, -1]
    sep = coin.SoSeparator()
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(pts), pts)
    faces = coin.SoIndexedFaceSet()
    faces.coordIndex.setValues(0, len(idx), idx)
    for node in (hints, coords, faces):
        sep.addChild(node)
    return sep


def _dress_ring(dragger, rgb):
    from pivy import coin

    light = tuple(min(1.0, c * 1.3 + 0.1) for c in rgb)
    dragger.setPart("rotator", _part(rgb, _ring_shape()))
    dragger.setPart("rotatorActive", _part(light, _ring_shape()))
    dragger.setPart("feedback", coin.SoSeparator())
    dragger.setPart("feedbackActive", coin.SoSeparator())


def _model_size(doc):
    box = _model_box(doc)
    return box.DiagonalLength if box.isValid() else 50.0


def _active_view():
    import FreeCADGui as Gui

    try:
        view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    except Exception:  # noqa: BLE001
        return None
    return view if view is not None and hasattr(view, "setSectionPasses") else None


# ----- commands, observer, install -----------------------------------------------------------------


def picked_faces(sel):
    """(root object, [path to the face]) of each face in the selection, from the top of the file:
    a face of a body's feature, or of a part inside an assembly, is "Body.Pad.Face3" or
    "Part001.Body.Pad.Face3" under the root (getSelectionEx(\"\", 0))."""
    return [(s.Object, [n]) for s in sel for n in s.SubElementNames if n.split(".")[-1].startswith("Face")]


def any_shown(doc):
    return doc is not None and any(shown(o) for o in sections(doc))


def sections_off(doc):
    """Every section of ``doc`` off, in one undo step; an open Section tab is closed first, kept."""
    panel = _state["editing"]
    if panel is not None:
        panel.accept()
    doc.openTransaction("Sections off")
    for obj in sections(doc):
        obj.Visibility = False
    doc.commitTransaction()
    schedule(doc)


def sync_button(doc=None):
    """The Section command's button (the nav pill's, the View menu's) is down while a section of
    the active file is on. Its signals are held: setting the state must not run the command."""
    import FreeCADGui as Gui

    doc = doc if doc is not None else _app().ActiveDocument
    try:
        actions = Gui.Command.get("BruceWorx_Section").getAction()
    except Exception:  # noqa: BLE001 -- not installed yet
        return
    on = any_shown(doc)
    for action in actions:
        if action.isChecked() != on:
            held = action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(held)


class _NewSection:
    """Section View, a toggle: press it to cut (on the selected face, else the last section again,
    else a new XZ section), press it again to turn the sections off."""

    def GetResources(self):
        return {"Pixmap": ICON, "MenuText": "Section View", "Checkable": False,  # a toggle (the key makes it one), up at start
                "CmdType": "ForEdit",  # pressable while its own Section tab is open (IsActive refuses other tools')
                "ToolTip": "Section view on or off. Select a face first to cut on it; with nothing "
                           "selected the last section comes back. Saved in the file: caps every part, "
                           "hatched, clashes in red; up to three planes cut at once."}

    def IsActive(self):
        import FreeCADGui as Gui

        if Gui.Control.activeDialog() and _state["editing"] is None:
            return False  # another tool is editing (a sketch, a pad): not in the middle of it
        return _active_view() is not None

    def Activated(self, checked=None):
        import FreeCADGui as Gui

        doc = _app().ActiveDocument
        view = _active_view()
        if _state["editing"] is not None:  # the Section tab open: keep its changes first
            _state["editing"].accept()
        sel = Gui.Selection.getSelectionEx("", 0)
        faces = [f for f in picked_faces(sel) if face_placement(f) is not None]
        points = [p for s in sel for p in s.PickedPoints]
        if checked is None:
            checked = not any_shown(doc) or bool(faces)
        if not checked and not faces:
            sections_off(doc)
            sync_button(doc)
            return
        previous = sorted(sections(doc), key=lambda o: o.ShownAt)
        if faces or len(points) >= 3 or not previous:
            doc.openTransaction("Make Section")
            if faces:
                obj = make(doc, "Face", view, support=faces[0])
            elif len(points) >= 3:
                obj = make(doc, "Three points", view, points=points)
            else:
                obj = make(doc, "XZ", view)
            doc.commitTransaction()
            edit(obj)
        else:  # the section shown last, back on; double-click it in the tree to change it
            obj = previous[-1]
            doc.openTransaction(f"Turned on {obj.Label}")
            turn_on(obj, view, own_step=False)
            doc.commitTransaction()
        sync_button(doc)


class _SectionsOff:
    def GetResources(self):
        return {"MenuText": "Sections Off", "ToolTip": "Turn every section of this file off"}

    def IsActive(self):
        doc = _app().ActiveDocument
        return doc is not None and any(shown(o) for o in sections(doc))

    def Activated(self):
        sections_off(_app().ActiveDocument)
        sync_button()


class _Manipulator:
    """Section View in the View menu, next to FreeCAD's Clipping dock, which stays as it is."""

    def modifyMenuBar(self):
        return [{"insert": "BruceWorx_Section", "menuItem": "Std_ToggleClipPlane", "after": ""},
                {"insert": "BruceWorx_SectionsOff", "menuItem": "Std_ToggleClipPlane", "after": ""}]


class _Observer:
    def slotFinishRestoreDocument(self, doc):
        for obj in sections(doc):
            if _app().GuiUp and obj.ViewObject is not None and not isinstance(getattr(obj.ViewObject, "Proxy", None), ViewProviderSection):
                ViewProviderSection(obj.ViewObject)
        schedule(doc)

    def slotActivateDocument(self, doc):
        if sections(doc):
            schedule(doc)
        sync_button(doc)


def install():
    """Register the commands and the observer (InitGui)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_Section", _NewSection())
    Gui.addCommand("BruceWorx_SectionsOff", _SectionsOff())
    observer = _Observer()
    _app().addDocumentObserver(observer)
    _state["observer"] = observer
    manipulator = _Manipulator()
    Gui.addWorkbenchManipulator(manipulator)
    _state["manipulator"] = manipulator
    _state["installed"] = True
