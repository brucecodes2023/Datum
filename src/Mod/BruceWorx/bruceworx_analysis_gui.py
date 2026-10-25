# SPDX-License-Identifier: LGPL-2.1-or-later
"""INSPECT colour maps in the 3D view (roadmap MOD-11; plan "MOD-11 plan").

Overhang (3D print), Draft (moulding), Wall Thickness, Curvature and Zebra:
five commands, one task panel. The numbers come from bruceworx_analysis;
this module draws them and holds the panel.

* What is analysed: the selection's parts, bodies and solids (as the Physical
  tab counts them: a face counts as its body, links and assemblies are
  followed), or every visible solid when nothing is selected.
* How it is drawn: in its own nodes. A copy of each shape's display
  triangles goes into one separator at the end of the view's scene, drawn at
  the faces' own depth (same polygon offset, less half a unit, depth test
  LEQUAL), so it covers the faces and the edges stay on top. It is
  unpickable: a click still selects the part. FreeCAD's face nodes and the
  studio program (bruceworx_studio) are not touched; studio keeps shading
  every part underneath.
* Colours: each vertex's value is a texture coordinate into a 1D colour
  texture sampled nearest, so band edges are sharp per pixel. Zebra is a
  sphere-map stripe texture.
* Nothing is written to the document. Closing the panel removes the nodes.
  The maps follow recomputes while the panel is open.
"""

from __future__ import annotations

import os

import bruceworx_analysis as ba

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_NAME = "BWAnalysis"
HIDE_NAME = "BWAnalysisHide"
HIDE_FACES = True  # hide FreeCAD's own faces of an analysed part while its map shows
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx/Inspect"

MODES = (
    ("overhang", "Overhang (3D print)"),
    ("draft", "Draft (moulding)"),
    ("thickness", "Wall thickness"),
    ("curvature", "Curvature map"),
    ("zebra", "Zebra stripes"),
)
COMMANDS = {
    "overhang": ("BruceWorx_OverhangAnalysis", "Overhang", "BruceWorx_Overhang.svg",
                 "Colour the faces a 3D print needs support under: angle from vertical against the build "
                 "direction (45 degrees by default), and the faces on the bed."),
    "draft": ("BruceWorx_DraftAnalysis", "Draft Analysis", "BruceWorx_Draft.svg",
              "Colour positive, missing and negative draft against a pull direction (1 degree by default)."),
    "thickness": ("BruceWorx_ThicknessAnalysis", "Wall Thickness", "BruceWorx_Thickness.svg",
                  "Colour walls thinner than the minimum (0.8 mm) and the target (1.2 mm) for a 0.4 mm FDM "
                  "nozzle; presets for CNC."),
    "curvature": ("BruceWorx_CurvatureAnalysis", "Curvature Map", "BruceWorx_Curvature.svg",
                  "Colour the mean, Gaussian or largest curvature of the faces."),
    "zebra": ("BruceWorx_ZebraAnalysis", "Zebra", "BruceWorx_Zebra.svg",
              "Reflect stripes on the faces: a kink at an edge is a tangency break, a jump a gap."),
}
DIRECTIONS = ("+Z", "-Z", "+X", "-X", "+Y", "-Y")
DEFAULTS = {
    "overhang": {"direction": "+Z", "angle": ba.OVERHANG_DEFAULT},
    "draft": {"direction": "+Z", "angle": ba.DRAFT_DEFAULT},
    "thickness": {"preset": 0, "minimum": ba.THICKNESS_PRESETS[0][1], "target": ba.THICKNESS_PRESETS[0][2]},
    "curvature": {"kind": "Mean"},
    "zebra": {"stripes": 12, "vertical": False},
}

_state = {"panel": None, "observer": None, "installed": False, "message": ""}


# ----- what is analysed ---------------------------------------------------------------------------


class Target:
    """One body or solid: its object, its global shape, and the placement from its own shape to that."""

    def __init__(self, obj, shape):
        self.obj = obj
        self.shape = shape
        self.label = part_label(obj)
        try:
            own = obj.Shape.Placement
            self.transform = shape.Placement.multiply(own.inverse())
        except Exception:
            self.transform = None
        self.meshes = {}

    def refresh(self):
        """The object's shape again after a recompute, at the same place."""
        if self.transform is None:
            return False
        try:
            shape = self.obj.Shape.copy()
        except Exception:
            return False
        if shape.isNull() or not shape.Solids:
            return False
        shape.Placement = self.transform.multiply(shape.Placement)
        self.shape = shape
        self.meshes = {}
        return True

    def mesh(self, curvature=False):
        """The SurfaceMesh, made once (again with curvatures when they are first needed)."""
        mesh = self.meshes.get("mesh")
        if mesh is None or (curvature and mesh.curvature is None):
            mesh = ba.surface_mesh(self.shape, curvature=curvature)
            self.meshes["mesh"] = mesh
        return mesh


def part_label(obj):
    """A body's part name: the App::Part around it (a part file's "Housing with ring gear 48T"), else its label."""
    label = getattr(obj, "Label", "")
    try:
        for parent in obj.InList:
            if parent.TypeId == "App::Part":
                return parent.Label
    except Exception:
        pass
    return label


def targets(doc=None, objects=None):
    """The Targets of ``objects``, of the selection, or of every visible solid of ``doc``."""
    import FreeCAD

    import bruceworx_physical as bp

    owners = []
    if objects is not None:
        for obj in objects:
            bp._collect(None, [], obj, owners)
    else:
        owners, _objs = bp.owners_of_selection()
        if not owners:
            doc = doc or FreeCAD.ActiveDocument
            for root in getattr(doc, "RootObjects", []) or []:
                vobj = getattr(root, "ViewObject", None)
                if vobj is not None and not vobj.Visibility:
                    continue
                bp._collect(root, [], root, owners)  # through the root: links placed
    if owners:
        # A part's faces are hidden under its map, and every link to it shares those nodes: so every visible
        # instance of an analysed part is analysed too.
        def name(obj):
            return obj.Document.Name, obj.Name

        wanted = {name(o.obj) for o in owners}
        everything = []
        for root in getattr(doc or FreeCAD.ActiveDocument, "RootObjects", []) or []:
            vobj = getattr(root, "ViewObject", None)
            if vobj is None or vobj.Visibility:
                bp._collect(root, [], root, everything)
        owners = owners + [o for o in everything if name(o.obj) in wanted]
    out, seen = [], set()
    for owner in owners:
        try:
            key = owner.key()
        except Exception:
            continue
        if key not in seen:
            seen.add(key)
            out.append(Target(owner.obj, owner.shape))
    return out


def face_direction(flip=False):
    """(label, normal) of the selected face in global coordinates, or None."""
    import FreeCADGui as Gui

    for sel in Gui.Selection.getSelectionEx("", 0):
        for sub in sel.SubElementNames:
            if not sub.split(".")[-1].startswith("Face"):
                continue
            try:
                face = sel.Object.getSubObject(sub)
                u0, u1, v0, v1 = face.ParameterRange
                n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            except Exception:
                continue
            owner = sel.Object.getSubObject(sub, retType=1)
            label = f"{sub.split('.')[-1]} of {getattr(owner, 'Label', sel.Object.Label)}"
            vec = (-n.x, -n.y, -n.z) if flip else (n.x, n.y, n.z)
            return label, vec
    return None


# ----- the overlay ---------------------------------------------------------------------------------


def _image(colours, vertical_stripes=None):
    """An SoSFImage-ready (size, components, bytes) for a row of colours, or a stripe pattern."""
    from pivy import coin

    if vertical_stripes is not None:
        count, vertical = vertical_stripes
        size = 256
        data = bytearray()
        for y in range(size):
            for x in range(size):
                k = x if vertical else y
                data.append(255 if (k * 2 * count // size) % 2 == 0 else 20)
        return coin.SbVec2s(size, size), 1, bytes(data)
    data = bytearray()
    for _row in range(2):
        for r, g, b in colours:
            data += bytes((int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return coin.SbVec2s(len(colours), 2), 3, bytes(data)


def build_node(mesh, analysis=None, zebra=None):
    """The overlay separator for one mesh: coloured by ``analysis``, or zebra stripes (count, vertical)."""
    import numpy as np
    from pivy import coin

    sep = coin.SoSeparator()
    sep.setName("BWAnalysisPart")
    pick = coin.SoPickStyle()
    pick.style = coin.SoPickStyle.UNPICKABLE
    sep.addChild(pick)
    # The faces' own depth (ViewProviderPartExt draws them with SoPolygonOffset 1, 1), half a unit nearer and
    # LEQUAL: drawn after them, the map covers them exactly; the edges (no offset) stay in front.
    offset = coin.SoPolygonOffset()
    offset.factor = 1.0
    offset.units = 1.0 if HIDE_FACES else 0.5
    sep.addChild(offset)
    try:
        depth = coin.SoDepthBuffer()
        depth.function = coin.SoDepthBuffer.LEQUAL
        sep.addChild(depth)
    except Exception:
        pass
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
    sep.addChild(hints)
    material = coin.SoMaterial()
    material.diffuseColor.setValue(1.0, 1.0, 1.0)
    material.ambientColor.setValue(0.35, 0.35, 0.35)
    material.specularColor.setValue(0.12, 0.12, 0.12)
    material.shininess.setValue(0.25)
    sep.addChild(material)
    complexity = coin.SoComplexity()
    complexity.textureQuality = 0.5 if zebra else 0.05  # under 0.1: nearest texels, sharp band edges (0 is off)
    sep.addChild(complexity)
    texture = coin.SoTexture2()
    if zebra:
        size, nc, data = _image(None, zebra)
        texture.model = coin.SoTexture2.REPLACE
    else:
        size, nc, data = _image(analysis.band_texture(512))
        texture.model = coin.SoTexture2.MODULATE
    texture.image.setValue(size, nc, data)
    texture.wrapS = texture.wrapT = coin.SoTexture2.REPEAT if zebra else coin.SoTexture2.CLAMP
    sep.addChild(texture)
    if zebra:
        # The sphere map spends most of its range near the middle: repeat the stripes twice over it.
        scale = coin.SoTexture2Transform()
        scale.scaleFactor.setValue(2.0, 2.0)
        sep.addChild(scale)
        sep.addChild(coin.SoTextureCoordinateEnvironment())
    else:
        coords = coin.SoTextureCoordinate2()
        s = analysis.texcoords()
        coords.point.setValues(0, len(s), np.column_stack([s, np.full(len(s), 0.5)]).tolist())
        sep.addChild(coords)
        binding = coin.SoTextureCoordinateBinding()
        binding.value = coin.SoTextureCoordinateBinding.PER_VERTEX_INDEXED
        sep.addChild(binding)
    points = coin.SoCoordinate3()
    points.point.setValues(0, len(mesh.points), mesh.points.tolist())
    sep.addChild(points)
    normals = coin.SoNormal()
    normals.vector.setValues(0, len(mesh.normals), mesh.normals.tolist())
    sep.addChild(normals)
    nbind = coin.SoNormalBinding()
    nbind.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
    sep.addChild(nbind)
    faces = coin.SoIndexedFaceSet()
    index = np.hstack([mesh.triangles, np.full((len(mesh.triangles), 1), -1)]).ravel().tolist()
    faces.coordIndex.setValues(0, len(index), index)
    sep.addChild(faces)
    return sep


def _scene(view):
    try:
        return view.getSceneGraph()
    except Exception:
        return None


def _flat_root(obj):
    try:
        import bruceworx_studio as studio

        return studio.flat_root(obj.ViewObject)
    except Exception:
        return None


def drawn_by(obj):
    """The objects whose view providers draw ``obj``'s faces: itself, and a body's visible features (the tip)."""
    out = [obj]
    if getattr(obj, "TypeId", "") == "PartDesign::Body":
        for child in getattr(obj, "Group", []) or []:
            vobj = getattr(child, "ViewObject", None)
            if vobj is not None and vobj.Visibility and child.isDerivedFrom("Part::Feature"):
                out.append(child)
    return out


def hide_faces(obj):
    """Stop FreeCAD drawing ``obj``'s faces (its edges stay): an invisible draw style after its FaceStyle."""
    from pivy import coin

    sep = _flat_root(obj)
    if sep is None:
        return False
    names = [sep.getChild(i).getName().getString() for i in range(sep.getNumChildren())]
    if HIDE_NAME in names:
        return True
    style = coin.SoDrawStyle()
    style.setName(HIDE_NAME)
    style.style = coin.SoDrawStyle.INVISIBLE
    at = names.index("FaceStyle") + 1 if "FaceStyle" in names else sep.getNumChildren() - 1
    sep.insertChild(style, max(at, 1))  # never at 0: that place is the studio program's
    return True


def show_faces(obj):
    sep = _flat_root(obj)
    if sep is None:
        return
    for i in range(sep.getNumChildren() - 1, -1, -1):
        if sep.getChild(i).getName().getString() == HIDE_NAME:
            sep.removeChild(i)


# ----- the session ---------------------------------------------------------------------------------


class Session:
    """The analysed shapes, the current analysis of each and the overlay in one 3D view."""

    def __init__(self, doc, items, view, mode="overhang"):
        self.doc = doc
        self.targets = items
        self.view = view
        self.mode = mode
        self.params = {k: dict(v) for k, v in DEFAULTS.items()}
        self.vector = {"overhang": None, "draft": None}  # a picked face's direction, when chosen
        self.vector_label = {"overhang": "", "draft": ""}
        self.results = []  # [(target, Analysis or None)]
        self.root = None
        self.hidden = set()  # objects whose own faces are hidden under the map

    # The direction of the current mode as a vector.
    def direction(self, mode=None):
        mode = mode or self.mode
        vec = self.vector.get(mode)
        if vec is not None and self.params[mode]["direction"] == "face":
            return vec
        return ba.AXIS_DIRECTIONS.get(self.params[mode]["direction"], (0, 0, 1))

    def compute(self):
        """The current mode's analysis of every target."""
        mode, p = self.mode, self.params[self.mode]
        out = []
        for target in self.targets:
            if mode == "curvature":
                mesh = target.mesh(curvature=True)
                result = ba.curvature(mesh, p["kind"])
            else:
                mesh = target.mesh()
                if mode == "overhang":
                    result = ba.overhang(mesh, self.direction(), p["angle"])
                elif mode == "draft":
                    result = ba.draft(mesh, self.direction(), p["angle"])
                elif mode == "thickness":
                    result = ba.thickness(mesh, p["minimum"], p["target"])
                else:
                    result = None
            out.append((target, result))
        if mode == "curvature" and out:
            # one range for every part, so the colours compare
            limit = max(r.stats["limit"] for _t, r in out)
            out = [(t, ba.curvature(t.mesh(True), p["kind"], limit)) for t, _r in out]
        self.results = out
        return out

    def draw(self):
        from pivy import coin

        self.clear()
        scene = _scene(self.view)
        if scene is None:
            return None
        root = coin.SoSeparator()
        root.setName(NODE_NAME)
        zebra = (self.params["zebra"]["stripes"], self.params["zebra"]["vertical"]) if self.mode == "zebra" else None
        for target, result in self.results:
            mesh = target.mesh(self.mode == "curvature")
            if len(mesh.triangles):
                root.addChild(build_node(mesh, result, zebra))
        scene.addChild(root)
        self.root = root
        if HIDE_FACES:
            for target, _result in self.results:
                for obj in drawn_by(target.obj):
                    if hide_faces(obj):
                        self.hidden.add(obj)
        return root

    def run(self):
        self.compute()
        self.draw()
        return self.results

    def clear(self):
        for obj in list(self.hidden):
            try:
                show_faces(obj)
            except Exception:
                pass
        self.hidden.clear()
        if self.root is None:
            return
        scene = _scene(self.view)
        try:
            if scene is not None and scene.findChild(self.root) >= 0:
                scene.removeChild(self.root)
        except Exception:
            pass
        self.root = None

    def refresh(self):
        """After a recompute: the shapes again, then the map."""
        changed = [t.refresh() for t in self.targets]
        if any(changed):
            self.run()

    # ----- summary -----------------------------------------------------------------------

    def totals(self):
        """Band areas summed over the targets: [(name, colour, area)] and the total area."""
        rows, total = {}, 0.0
        order = []
        for _t, result in self.results:
            if result is None:
                continue
            for band in result.bands:
                if band.name not in rows:
                    rows[band.name] = [band.colour, 0.0]
                    order.append(band.name)
                rows[band.name][1] += band.area
                total += band.area
        return [(n, rows[n][0], rows[n][1]) for n in order], total

    def summary(self):
        """One line on what the map found."""
        results = [r for _t, r in self.results if r is not None]
        if self.mode == "zebra":
            return "Turn the view: stripes that kink or jump at an edge show a tangency or position break."
        if not results:
            return "Nothing to analyse: select a part, body or solid."
        if self.mode == "overhang":
            area = sum(r.stats["support_area"] for r in results)
            faces = sum(len(r.stats["faces_needing_support"]) for r in results)
            if area <= 0:
                return "No overhangs: prints without support in this direction."
            return f"Needs support: {area_text(area)} on {faces} face{'s' if faces != 1 else ''}."
        if self.mode == "draft":
            area = sum(r.stats["needs_draft_area"] for r in results)
            faces = sum(len(r.stats["faces_needing_draft"]) for r in results)
            return "Every face has draft." if area <= 0 else \
                f"Needs draft: {area_text(area)} on {faces} face{'s' if faces != 1 else ''}."
        if self.mode == "thickness":
            thin = [r.stats["thinnest"] for r in results if r.stats["thinnest"] is not None]
            if not thin:
                return f"Every wall is thicker than {length_text(results[0].stats['cap'])}."
            under = sum(r.stats["under_minimum_area"] for r in results)
            text = f"Thinnest wall {length_text(min(thin))}."
            return text + (f" Too thin: {area_text(under)}." if under > 0 else "")
        if self.mode == "curvature":
            radius = [r.stats["smallest_radius"] for r in results if r.stats["smallest_radius"]]
            return f"Smallest radius {length_text(min(radius))}." if radius else "Every face is flat."
        return ""


def area_text(mm2):
    try:
        import FreeCAD

        return FreeCAD.Units.Quantity(f"{mm2} mm^2").UserString
    except Exception:
        return f"{mm2:.1f} mm²"


def length_text(mm):
    try:
        import FreeCAD

        return FreeCAD.Units.Quantity(f"{mm} mm").UserString
    except Exception:
        return f"{mm:.2f} mm"


def ramp_labels(analysis):
    """(left, middle, right) texts of a continuous ramp's legend."""
    def k(v):
        return "0" if abs(v) < 1e-12 else f"{v:.3g}"

    unit = analysis.unit
    mid = 0.5 * (analysis.lo + analysis.hi)
    right = f"{k(analysis.hi)} {unit}"
    if analysis.kind == "curvature" and analysis.hi > 0 and unit == "1/mm":
        right += f" (R {1.0 / analysis.hi:.3g} mm)"
    return f"{k(analysis.lo)} {unit}", f"{k(mid)} {unit}", right


# ----- the task panel ------------------------------------------------------------------------------


class InspectPanel:
    """Task panel: the analysis, its settings, the legend and a one-line result."""

    def __init__(self, session):
        from PySide import QtCore, QtWidgets

        self.session = session
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("BWInspectPanel")
        self.form.setWindowTitle("Inspect")
        layout = QtWidgets.QVBoxLayout(self.form)
        top = QtWidgets.QFormLayout()
        self.mode = QtWidgets.QComboBox()
        for key, text in MODES:
            self.mode.addItem(text, key)
        top.addRow("Analysis", self.mode)
        names = ", ".join(t.label for t in session.targets[:4]) + ("…" if len(session.targets) > 4 else "")
        self.of = QtWidgets.QLabel(names or "Nothing")
        self.of.setWordWrap(True)
        top.addRow("Of", self.of)
        layout.addLayout(top)

        self.pages = QtWidgets.QStackedWidget()
        self.widgets = {}
        for key, _text in MODES:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            form.setContentsMargins(0, 0, 0, 0)
            self._fill(key, form)
            self.pages.addWidget(page)
        layout.addWidget(self.pages)

        self.legend_box = QtWidgets.QGroupBox("Legend")
        self.legend = QtWidgets.QGridLayout(self.legend_box)
        layout.addWidget(self.legend_box)
        self.result = QtWidgets.QLabel("")
        self.result.setWordWrap(True)
        self.result.setObjectName("BWInspectResult")
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.update)
        self.mode.setCurrentIndex([k for k, _t in MODES].index(session.mode))
        self.pages.setCurrentIndex(self.mode.currentIndex())
        self.mode.currentIndexChanged.connect(self._mode_changed)

    # settings per mode
    def _spin(self, value, lo, hi, step, suffix, decimals=1):
        from PySide import QtWidgets

        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(value)
        spin.valueChanged.connect(lambda *_a: self.changed())
        return spin

    def _direction_row(self, key, form, label, pick_text):
        from PySide import QtWidgets

        row = QtWidgets.QHBoxLayout()
        combo = QtWidgets.QComboBox()
        for d in DIRECTIONS:
            combo.addItem(d, d)
        combo.addItem("Selected face", "face")
        combo.currentIndexChanged.connect(lambda *_a, k=key: self._direction_changed(k))
        row.addWidget(combo, 1)
        flip = QtWidgets.QToolButton()
        flip.setText("Flip")
        flip.setToolTip("Reverse the direction")
        flip.clicked.connect(lambda *_a, k=key: self._flip(k))
        row.addWidget(flip)
        form.addRow(label, row)
        hint = QtWidgets.QLabel(pick_text)
        hint.setWordWrap(True)
        hint.setVisible(False)
        form.addRow(hint)
        self.widgets[key] = {"direction": combo, "hint": hint}

    def _fill(self, key, form):
        from PySide import QtWidgets

        p = self.session.params[key]
        if key == "overhang":
            self._direction_row(key, form, "Build direction",
                                "Select the face the part rests on, then choose Selected face.")
            self.widgets[key]["angle"] = self._spin(p["angle"], 0.0, 89.0, 5.0, "°")
            self.widgets[key]["angle"].setToolTip("A down-facing surface more than this from vertical needs support "
                                                  "(Cura's support overhang angle).")
            form.addRow("Overhang angle", self.widgets[key]["angle"])
        elif key == "draft":
            self._direction_row(key, form, "Pull direction",
                                "Select a face whose normal is the pull direction, then choose Selected face.")
            self.widgets[key]["angle"] = self._spin(p["angle"], 0.0, 30.0, 0.5, "°")
            form.addRow("Required draft", self.widgets[key]["angle"])
        elif key == "thickness":
            preset = QtWidgets.QComboBox()
            for name, lo, hi in ba.THICKNESS_PRESETS:
                preset.addItem(name)
            preset.addItem("Custom")
            preset.currentIndexChanged.connect(self._preset_changed)
            form.addRow("Preset", preset)
            self.widgets[key] = {"preset": preset,
                                 "minimum": self._spin(p["minimum"], 0.05, 50.0, 0.1, " mm", 2),
                                 "target": self._spin(p["target"], 0.05, 50.0, 0.1, " mm", 2)}
            self.widgets[key]["minimum"].setToolTip("Thinner is red: it will not print or machine reliably.")
            self.widgets[key]["target"].setToolTip("Thinner is orange; thicker is green.")
            form.addRow("Minimum", self.widgets[key]["minimum"])
            form.addRow("Target", self.widgets[key]["target"])
        elif key == "curvature":
            kind = QtWidgets.QComboBox()
            for name in ba.CURVATURE_KINDS:
                kind.addItem(name)
            kind.currentIndexChanged.connect(lambda *_a: self.changed())
            form.addRow("Curvature", kind)
            self.widgets[key] = {"kind": kind}
        elif key == "zebra":
            stripes = QtWidgets.QSpinBox()
            stripes.setRange(2, 60)
            stripes.setValue(p["stripes"])
            stripes.valueChanged.connect(lambda *_a: self.changed())
            vertical = QtWidgets.QComboBox()
            vertical.addItems(["Horizontal", "Vertical"])
            vertical.currentIndexChanged.connect(lambda *_a: self.changed())
            form.addRow("Stripes", stripes)
            form.addRow("Direction", vertical)
            self.widgets[key] = {"stripes": stripes, "vertical": vertical}

    def _read(self):
        s, w = self.session, self.widgets
        o, d, t, c, z = (s.params[k] for k in ("overhang", "draft", "thickness", "curvature", "zebra"))
        o["direction"] = w["overhang"]["direction"].currentData()
        o["angle"] = w["overhang"]["angle"].value()
        d["direction"] = w["draft"]["direction"].currentData()
        d["angle"] = w["draft"]["angle"].value()
        t["minimum"] = w["thickness"]["minimum"].value()
        t["target"] = max(w["thickness"]["target"].value(), t["minimum"])
        c["kind"] = w["curvature"]["kind"].currentText()
        z["stripes"] = w["zebra"]["stripes"].value()
        z["vertical"] = w["zebra"]["vertical"].currentIndex() == 1

    def _mode_changed(self, index):
        self.session.mode = MODES[index][0]
        self.pages.setCurrentIndex(index)
        self.changed()

    def _direction_changed(self, key):
        w = self.widgets[key]
        if w["direction"].currentData() == "face":
            # overhang: the part rests on the face (build = -normal); draft: the face's normal is the pull
            picked = face_direction(flip=(key == "overhang"))
            if picked is None:
                w["hint"].setVisible(True)
                return
            w["hint"].setVisible(False)
            self.session.vector_label[key], self.session.vector[key] = picked
            w["direction"].setItemText(len(DIRECTIONS), f"Face: {picked[0]}")
        else:
            w["hint"].setVisible(False)
        self.changed()

    def _flip(self, key):
        w = self.widgets[key]
        data = w["direction"].currentData()
        if data == "face" and self.session.vector[key] is not None:
            x, y, z = self.session.vector[key]
            self.session.vector[key] = (-x, -y, -z)
            self.changed()
            return
        opposite = {"+Z": "-Z", "-Z": "+Z", "+X": "-X", "-X": "+X", "+Y": "-Y", "-Y": "+Y"}.get(data)
        if opposite:
            w["direction"].setCurrentIndex(DIRECTIONS.index(opposite))

    def _preset_changed(self, index):
        if index < len(ba.THICKNESS_PRESETS):
            _name, lo, hi = ba.THICKNESS_PRESETS[index]
            w = self.widgets["thickness"]
            for spin, value in ((w["minimum"], lo), (w["target"], hi)):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self.changed()

    def changed(self):
        self.timer.start()

    def update(self):
        from PySide import QtCore, QtWidgets

        self._read()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.session.run()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.show_legend()

    def show_legend(self):
        from PySide import QtWidgets

        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        s = self.session
        self.result.setText(s.summary())
        results = [r for _t, r in s.results if r is not None]
        self.legend_box.setVisible(bool(results))
        if not results:
            return
        if results[0].bands:
            rows, total = s.totals()
            for i, (name, colour, area) in enumerate(rows):
                swatch = QtWidgets.QLabel()
                swatch.setFixedSize(14, 14)
                swatch.setStyleSheet("background: rgb({},{},{}); border-radius: 3px;".format(
                    *(int(c * 255) for c in colour)))
                self.legend.addWidget(swatch, i, 0)
                self.legend.addWidget(QtWidgets.QLabel(self._band_text(name)), i, 1)
                share = f"{100.0 * area / total:.0f} %" if total > 0 else ""
                value = QtWidgets.QLabel(f"{area_text(area)}  {share}")
                self.legend.addWidget(value, i, 2)
            return
        bar = QtWidgets.QLabel()
        bar.setFixedHeight(12)
        stops = ", ".join(f"stop:{t} rgb({int(c[0] * 255)},{int(c[1] * 255)},{int(c[2] * 255)})"
                          for t, c in ba.RAMP)
        bar.setStyleSheet(f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops}); border-radius: 3px;")
        self.legend.addWidget(bar, 0, 0, 1, 3)
        for col, text in enumerate(ramp_labels(results[0])):
            label = QtWidgets.QLabel(text)
            label.setStyleSheet("font-size: 11px;")
            self.legend.addWidget(label, 1, col)

    def _band_text(self, name):
        p = self.session.params
        if self.session.mode == "overhang":
            a = p["overhang"]["angle"]
            return {"Prints without support": f"Prints (under {a - ba.OVERHANG_MARGIN:.0f}°)",
                    "Near the limit": f"Near the limit ({a - ba.OVERHANG_MARGIN:.0f}–{a:.0f}°)",
                    "Needs support": f"Needs support (over {a:.0f}°)"}.get(name, name)
        if self.session.mode == "thickness":
            t = p["thickness"]
            return {"Too thin": f"Too thin (under {t['minimum']:g} mm)",
                    "Thin": f"Thin ({t['minimum']:g}–{t['target']:g} mm)",
                    "Thick enough": f"Thick enough ({t['target']:g} mm and more)"}.get(name, name)
        if self.session.mode == "draft":
            a = p["draft"]["angle"]
            return {"Needs draft": f"Needs draft (under {a:g}°)"}.get(name, name)
        return name

    # task dialog
    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        close()
        return True

    def accept(self):
        close()
        return True


# ----- opening and closing -------------------------------------------------------------------------


class _DocObserver:
    def slotRecomputedDocument(self, doc):
        panel = _state["panel"]
        if panel is not None and panel.session.doc == doc:
            try:
                from PySide import QtCore

                QtCore.QTimer.singleShot(0, _refresh)
            except Exception:
                pass

    def slotDeletedDocument(self, doc):
        panel = _state["panel"]
        if panel is not None and panel.session.doc == doc:
            panel.session.root = None  # the view is going away with its scene
            close()


def _refresh():
    panel = _state["panel"]
    if panel is not None:
        panel.session.refresh()
        panel.show_legend()


def session():
    """The open Session, or None (tests, scripts)."""
    panel = _state["panel"]
    return panel.session if panel is not None else None


def show(mode="overhang", objects=None):
    """Open the Inspect panel on ``mode`` for ``objects`` (default: the selection, else every visible solid)."""
    import FreeCAD
    import FreeCADGui as Gui

    panel = _state["panel"]
    if panel is not None:  # already open: switch the analysis
        panel.mode.setCurrentIndex([k for k, _t in MODES].index(mode))
        return panel.session
    doc = FreeCAD.ActiveDocument
    gdoc = Gui.ActiveDocument
    if doc is None or gdoc is None:
        return None
    if Gui.Control.activeDialog():
        _message("Close the open task panel first.")
        return None
    items = targets(doc, objects)
    if not items:
        _message("Select a part, body or solid to inspect.")
        return None
    s = Session(doc, items, gdoc.ActiveView, mode)
    panel = InspectPanel(s)
    _state["panel"] = panel
    if _state["observer"] is None:
        _state["observer"] = _DocObserver()
        FreeCAD.addDocumentObserver(_state["observer"])
    Gui.Control.showDialog(panel)
    panel.update()
    return s


def close():
    import FreeCAD
    import FreeCADGui as Gui

    panel, _state["panel"] = _state["panel"], None
    if panel is not None:
        panel.timer.stop()
        panel.session.clear()
    if _state["observer"] is not None:
        try:
            FreeCAD.removeDocumentObserver(_state["observer"])
        except Exception:
            pass
        _state["observer"] = None
    try:
        if panel is not None and Gui.Control.activeDialog():
            Gui.Control.closeDialog()
    except Exception:
        pass


def _message(text):
    _state["message"] = text
    try:
        import FreeCADGui as Gui

        Gui.getMainWindow().statusBar().showMessage(text, 6000)
    except Exception:
        pass


def show_mass_properties():
    """Mass Properties: the right panel's Physical tab (UIX-12) for the selection."""
    import bruceworx_physical as bp
    import bruceworx_rightpanel as rp

    if not rp.show_tab(bp.TAB):
        _message("Turn the right panel on (BruceWorx > Right Panel) to see mass properties.")
        return False
    return True


class _AnalysisCommand:
    def __init__(self, mode):
        self.mode = mode

    def GetResources(self):
        _name, text, icon, tip = COMMANDS[self.mode]
        return {"Pixmap": os.path.join(HERE, icon), "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show(self.mode)


class _MassCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_MassProperties.svg"), "MenuText": "Mass Properties",
                "ToolTip": "Mass, volume, area, centre of mass and inertia of the selection, in the right "
                           "panel's Physical tab."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_mass_properties()


def install():
    import FreeCADGui as Gui

    if _state["installed"]:
        return
    for mode, (name, *_rest) in COMMANDS.items():
        Gui.addCommand(name, _AnalysisCommand(mode))
    Gui.addCommand("BruceWorx_MassProperties", _MassCommand())
    _state["installed"] = True
