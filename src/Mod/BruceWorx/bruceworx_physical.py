# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Physical tab of the right panel (roadmap UIX-12; plan "Redesign (24 Oct)").

Mass properties of the selection, in the style of the Properties tab
(bruceworx_inspector: folding sections, label left, value right):

* What counts: a part's bodies (nested parts and assembly links followed), a
  body, a solid (Part workbench, imports). A PartDesign feature counts as its
  body; a face, edge or vertex as its body. Several objects are summed, each
  body once.
* Material and density, mass, volume, surface area, bounding box and centre
  of mass (global coordinates); moments of inertia (about the centre of mass,
  or the origin: a switch) and principal axes folded.
* Mass follows bruceworx/partprops.py (computed_mass): volume times the
  material's density, a body's MassOverride instead when set, nothing for a
  body without a material ("Default"). So the numbers match the part data.
* Units follow the user's unit schema (FreeCAD's UserString); inertia in the
  mass and length units the schema picks for the selection.
* Copy (text to the clipboard) and Export CSV. Live: follows recomputes,
  material changes, undo and redo while the tab is visible.

Inertia is the tensor (products are -∫xy dm, as OpenCASCADE gives them).
"""

from __future__ import annotations

import re

import csv
import io
import math

TAB = "BWPhysical"
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
FRAME_PREF = "PhysicalInertiaAbout"  # "com" or "origin"
COM, ORIGIN = "com", "origin"
FRAME_WORDS = {COM: "Centre of mass", ORIGIN: "Origin"}
EMPTY_HINT = "Select a part, body or solid to see its mass, volume and centre of mass here."
NO_MATERIAL_HINT = "Choose a material for the inertia (Properties › Material)."
AXES = ("x", "y", "z")
INERTIA_ROWS = (("Ixx", 0, 0), ("Iyy", 1, 1), ("Izz", 2, 2), ("Ixy", 0, 1), ("Ixz", 0, 2), ("Iyz", 1, 2))
ZERO = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


# ----- pure helpers (unit tested) ---------------------------------------------------------


def shift_inertia(tensor, mass, d):
    """The tensor moved by ``d`` from the centre of mass (parallel axes): I + m(|d|²E − d dᵀ)."""
    dd = sum(x * x for x in d)
    return tuple(tuple(tensor[i][j] + mass * ((dd if i == j else 0.0) - d[i] * d[j]) for j in range(3))
                 for i in range(3))


def combine(items):
    """[(mass, centre, tensor about that centre)] -> (mass, centre, tensor about the common centre).

    None when the total mass is not positive. Used with volumes for mass when
    nothing has a material (the centre of volume).
    """
    items = [(m, c, t) for m, c, t in items if m > 0]
    total = sum(m for m, _c, _t in items)
    if total <= 0:
        return None
    centre = tuple(sum(m * c[k] for m, c, _t in items) / total for k in range(3))
    tensor = ZERO
    for m, c, t in items:
        moved = shift_inertia(t, m, tuple(c[k] - centre[k] for k in range(3)))
        tensor = tuple(tuple(tensor[i][j] + moved[i][j] for j in range(3)) for i in range(3))
    return total, centre, tensor


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def eigen_sym3(matrix, sweeps=60):
    """Eigenvalues and unit eigenvectors of a symmetric 3×3 matrix (Jacobi), smallest value first.

    [(value, (x, y, z))]; each vector's largest component is positive, so the axes read the same every time.
    """
    a = [list(map(float, row)) for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    scale = max(abs(a[i][j]) for i in range(3) for j in range(3)) or 1.0
    for _ in range(sweeps):
        off = a[0][1] ** 2 + a[0][2] ** 2 + a[1][2] ** 2
        if off <= (1e-15 * scale) ** 2:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) <= 1e-300:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
            t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            j = [[1.0 if i == k else 0.0 for k in range(3)] for i in range(3)]
            j[p][p] = j[q][q] = c
            j[p][q] = s
            j[q][p] = -s
            a = _matmul(_transpose(j), _matmul(a, j))
            v = _matmul(v, j)
    out = []
    for i in range(3):
        vec = [v[0][i], v[1][i], v[2][i]]
        big = max(range(3), key=lambda k: abs(vec[k]))
        if vec[big] < 0:
            vec = [-x for x in vec]
        out.append((a[i][i], tuple(vec)))
    out.sort(key=lambda e: e[0])
    return out


def union_box(boxes):
    """The box around (xmin, ymin, zmin, xmax, ymax, zmax) boxes, or None."""
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
            max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))


def fixed(value, decimals=2):
    """A number with ``decimals`` places; never "-0.00"."""
    text = f"{float(value):.{max(0, int(decimals))}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def trimmed(value, decimals=2):
    """A number with up to ``decimals`` places, trailing zeros dropped: 80.0 -> "80"."""
    text = fixed(value, decimals)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def vector_text(values, decimals=2, unit=""):
    """"38.60, 25.00, 6.10 mm"."""
    text = ", ".join(fixed(v, decimals) for v in values)
    return f"{text} {unit}".rstrip()


def box_text(sizes, decimals=2, unit=""):
    """"80 × 50 × 22 mm"."""
    text = " × ".join(trimmed(v, decimals) for v in sizes)
    return f"{text} {unit}".rstrip()


_LITRES = {"ml": "cm³", "l": "dm³", "µl": "mm³"}


def pretty(text):
    """FreeCAD's "cm^2" and "kg/m^3" with superscripts: "cm²", "kg/m³"; volumes in cubic lengths, as CAD
    shows them ("14.52 ml" -> "14.52 cm³": the unit schema speaks of litres)."""
    text = (text or "").replace("^2", "²").replace("^3", "³")
    return re.sub(r"(^|(?<=[\d\s]))(ml|l|µl)$", lambda m: m.group(1) + _LITRES[m.group(2)], text)


def inertia_unit(mass_unit, length_unit):
    """"g" and "mm" -> "g·mm²"."""
    return f"{mass_unit}·{length_unit}²"


def count_text(n, word="body", plural="bodies"):
    return f"{n} {word if n == 1 else plural}"


def subtitle(type_word, owners, objects=1):
    """"Part · 1 body"; "4 bodies" under the title "3 objects"."""
    if objects > 1:
        return count_text(owners)  # the title says how many objects
    return f"{type_word} · {count_text(owners)}"


def missing_text(missing, owners):
    """The note when some bodies have no material, or ""."""
    if not missing:
        return ""
    if missing == owners:
        return "No material: mass and inertia need one."
    return f"{count_text(missing)} without a material, left out of mass and inertia."


def csv_text(rows):
    """CSV with a header: rows are (property, value, unit)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(("Property", "Value", "Unit"))
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def clip_text(title, rows):
    """Text for the clipboard: the title, then "label<TAB>value" lines."""
    return "\n".join([title] + [f"{label}\t{value}" for label, value in rows]) + "\n"


def file_stem(label):
    """A file name from a label: "Mounting plate" -> "Mounting plate-physical"."""
    keep = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in (label or "selection")).strip()
    return (keep or "selection") + "-physical"


# ----- FreeCAD side ------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def frame():
    value = _prefs().GetString(FRAME_PREF, COM)
    return value if value in FRAME_WORDS else COM


def set_frame(value):
    _prefs().SetString(FRAME_PREF, value if value in FRAME_WORDS else COM)


def decimals():
    import FreeCAD

    return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Units").GetInt("Decimals", 2)


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def quantity(value, kind):
    """A FreeCAD quantity of ``kind`` ("Mass", "Length", ...) in internal units (kg, mm)."""
    import FreeCAD

    return FreeCAD.Units.Quantity(value, getattr(FreeCAD.Units, kind))


def preferred(value, kind):
    """(text, factor, unit) in the user's unit schema: value / factor is the number in ``unit``."""
    text, factor, unit = quantity(value, kind).getUserPreferred()
    return text, float(factor or 1.0), unit


def is_pd_feature(obj):
    type_id = getattr(obj, "TypeId", "")
    return type_id.startswith("PartDesign::") and type_id != "PartDesign::Body"


def _has_solids(obj):
    shape = getattr(obj, "Shape", None)
    try:
        return shape is not None and not shape.isNull() and bool(shape.Solids)
    except Exception:
        return False


def material_info(obj):
    """(material name or "", density in kg/mm³ or None, mass override in kg or None) of a body or solid.

    The same rules as bruceworx/partprops.py computed_mass.
    """
    material = getattr(obj, "ShapeMaterial", None)
    try:
        from bruceworx import partprops as pp

        chosen = pp._chosen(material)
        density = pp._density_kg_per_mm3(material) if chosen else None
        override = pp._mass_override(obj)
    except Exception:
        chosen = getattr(material, "Name", "Default") != "Default"
        density = None
        if chosen:
            try:
                import FreeCAD

                # PhysicalProperties: getPhysicalValue touches the body in FreeCAD 1.1 (see partprops)
                density = FreeCAD.Units.Quantity(str(material.PhysicalProperties["Density"])).getValueAs(
                    "kg/mm^3").Value
            except Exception:
                density = None
        override = None
        if "MassOverride" in getattr(obj, "PropertiesList", ()):
            value = obj.MassOverride
            kg = value.getValueAs("kg").Value if hasattr(value, "getValueAs") else float(value)
            override = kg if kg > 0 else None
    return (material.Name if chosen and material is not None else ""), density, override


class Owner:
    """One body or solid that counts: its shape in global coordinates and its mass."""

    __slots__ = ("obj", "shape", "material", "density", "mass")

    def __init__(self, obj, shape):
        self.obj = obj
        self.shape = shape
        self.material, self.density, override = material_info(obj)
        volume = sum(s.Volume for s in shape.Solids)
        if override is not None:
            self.mass = override
        elif self.density is not None:
            self.mass = volume * self.density
        else:
            self.mass = None

    def key(self):
        com = self.shape.Solids[0].CenterOfMass
        return (self.obj.Document.Name, self.obj.Name, round(com.x, 6), round(com.y, 6), round(com.z, 6))


def _global_shape(top, names, obj):
    """``obj``'s shape in global coordinates: through the selection path when there is one."""
    import Part

    if top is not None:
        sub = ".".join(names) + "." if names else ""
        return Part.getShape(top, sub)
    shape = Part.getShape(obj, "", transform=False).copy()
    try:
        shape.Placement = obj.getGlobalPlacement().multiply(shape.Placement)
    except Exception:
        shape = Part.getShape(obj)
    return shape


def _is_shape_feature(obj):
    try:
        return obj.isDerivedFrom("Part::Feature")
    except Exception:
        return False


def _collect(top, names, obj, out, depth=0):
    """Append the owners under ``obj`` (reached from ``top`` by ``names``) to ``out``."""
    if obj is None or depth > 30:
        return
    target = obj
    try:
        target = obj.getLinkedObject(True) or obj
    except Exception:
        pass
    if is_pd_feature(target):
        body = None
        try:
            body = target.getParentGeoFeatureGroup()
        except Exception:
            pass
        if body is None:
            return
        if top is not None and len(names) >= 2 and names[-1] == obj.Name and names[-2] == body.Name:
            names = names[:-1]
        elif top is not None and names and names[-1] == obj.Name and len(names) == 1 and top is body:
            names = []
        else:
            top, names = None, []
        obj = target = body
    if "ShapeMaterial" in getattr(target, "PropertiesList", ()) and _has_solids(target):
        shape = _global_shape(top, names, obj if top is None else target)
        if shape is not None and not shape.isNull() and shape.Solids:
            out.append(Owner(target, shape))
        return
    children = list(getattr(target, "Group", None) or [])
    # A Part workbench result's operands (a Cut's base and tool) are inside it: count the result only. Only
    # shapes count as results: an assembly's joints and links point at the parts too (MOD-11).
    used = set()
    for child in children:
        if getattr(child, "TypeId", "") != "PartDesign::Body" and _is_shape_feature(child):
            used.update(id(o) for o in getattr(child, "OutList", ()) or ())
    for child in children:
        if id(child) in used:
            continue
        _collect(top, names + [child.Name] if top is not None else names, child, out, depth + 1)


def owners_of_selection():
    """(owners, objects): the bodies and solids the selection counts (each once), and the selected objects."""
    import bruceworx_inspector as bi

    owners, seen, objects = [], set(), []
    for top, sub, obj, _element in bi.resolve_selection():
        names = [n for n in sub.split(".")[:-1]] if sub else []
        # The resolved object: the last object of the path (a face's body, not the face).
        if obj is not None and obj not in objects:
            objects.append(obj)
        found = []
        _collect(top, names, obj if names else top, found)
        for owner in found:
            key = owner.key()
            if key not in seen:
                seen.add(key)
                owners.append(owner)
    return owners, objects


class Result:
    """The totals the tab shows."""

    def __init__(self, owners):
        self.owners = owners
        self.volume = 0.0
        self.area = 0.0
        boxes, mass_items, volume_items = [], [], []
        for owner in owners:
            solids = owner.shape.Solids
            volume = sum(s.Volume for s in solids)
            self.volume += volume
            self.area += sum(s.Area for s in solids)
            box = owner.shape.BoundBox
            if box.isValid():
                boxes.append((box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax))
            rho = owner.mass / volume if owner.mass is not None and volume > 0 else None
            for s in solids:
                c = s.CenterOfMass
                centre = (c.x, c.y, c.z)
                volume_items.append((s.Volume, centre, ZERO))
                if rho is not None:
                    m = s.MatrixOfInertia
                    tensor = ((m.A11, m.A12, m.A13), (m.A21, m.A22, m.A23), (m.A31, m.A32, m.A33))
                    mass_items.append((rho * s.Volume, centre,
                                       tuple(tuple(rho * x for x in row) for row in tensor)))
        self.box = union_box(boxes)
        self.missing = sum(1 for o in owners if o.mass is None)
        by_mass = combine(mass_items)
        self.mass = by_mass[0] if by_mass else (0.0 if owners else None)
        self.has_mass = by_mass is not None
        if by_mass:
            self.centre, self.tensor = by_mass[1], by_mass[2]
        else:
            by_volume = combine(volume_items)
            self.centre = by_volume[1] if by_volume else None
            self.tensor = None
        materials = sorted({o.material for o in owners if o.material})
        self.material = (materials[0] if len(materials) == 1 else "Mixed: " + ", ".join(materials)) if materials \
            else ""
        densities = {round(o.density, 15) for o in owners if o.density is not None}
        self.density = densities.pop() if len(densities) == 1 else None

    def tensor_about(self, which):
        if self.tensor is None:
            return None
        if which == ORIGIN:
            return shift_inertia(self.tensor, self.mass, self.centre)
        return self.tensor


def describe(objects, owners):
    """(title, subtitle) for the selected objects."""
    import bruceworx_inspector as bi

    if len(objects) == 1:
        obj = objects[0]
        if is_pd_feature(obj) and owners:
            return owners[0].obj.Label, f"Body · from {obj.Label}"
        word = "Body" if getattr(obj, "TypeId", "") == "PartDesign::Body" else bi.type_word(obj)
        if len(owners) == 1 and owners[0].obj is obj:
            return obj.Label, word  # the object is the one body or solid
        return obj.Label, subtitle(word, len(owners))
    return f"{len(objects)} objects", subtitle("", len(owners), len(objects))


class Facts:
    """The rows of the tab as text and as CSV numbers, from a Result."""

    def __init__(self, result, which, title, sub):
        self.title, self.sub = title, sub
        dec = decimals()
        r = result
        self.material = r.material or "none"
        self.density = pretty(quantity(r.density, "Density").UserString) if r.density is not None else (
            "mixed" if r.material.startswith("Mixed") else "—")
        self.mass = quantity(r.mass, "Mass").UserString if r.has_mass else "no material"
        self.volume = pretty(quantity(r.volume, "Volume").UserString)
        self.area = pretty(quantity(r.area, "Area").UserString)
        self.csv = []
        if r.has_mass:
            text, factor, unit = preferred(r.mass, "Mass")
            self.csv.append(("Mass", r.mass / factor, unit))
        for label, value, kind in (("Volume", r.volume, "Volume"), ("Area", r.area, "Area")):
            _t, factor, unit = preferred(value, kind)
            self.csv.append((label, value / factor, pretty(unit)))
        if r.density is not None:
            _t, factor, unit = preferred(r.density, "Density")
            self.csv.append(("Density", r.density / factor, pretty(unit)))
        # One length unit for the box, the centre and the inertia: the one the schema picks for the longest side.
        longest = max(r.box[3] - r.box[0], r.box[4] - r.box[1], r.box[5] - r.box[2]) if r.box else 1.0
        _t, lf, lu = preferred(longest or 1.0, "Length")
        self.length_unit = lu
        sizes = [(r.box[3 + k] - r.box[k]) / lf for k in range(3)] if r.box else None
        self.box = box_text(sizes, dec, lu) if sizes else "—"
        for k, axis in enumerate(("X", "Y", "Z")):
            if sizes:
                self.csv.append((f"Bounding box {axis}", sizes[k], lu))
        centre = [c / lf for c in r.centre] if r.centre else None
        self.centre = vector_text(centre, dec, lu) if centre else "—"
        self.centre_label = "Centre of mass" if r.has_mass else "Centre of volume"
        for k, axis in enumerate(("X", "Y", "Z")):
            if centre:
                self.csv.append((f"{self.centre_label} {axis}", centre[k], lu))
        self.note = missing_text(r.missing, len(r.owners))
        self.inertia, self.principal = [], []
        tensor = r.tensor_about(which)
        if tensor is not None:
            _t, mf, mu = preferred(r.mass, "Mass")
            scale = 1.0 / (mf * lf * lf)
            self.inertia_unit = inertia_unit(mu, lu)
            for label, i, j in INERTIA_ROWS:
                value = tensor[i][j] * scale
                self.inertia.append((label, f"{fixed(value, dec)} {self.inertia_unit}"))
                self.csv.append((f"{label} ({FRAME_WORDS[which].lower()})", value, self.inertia_unit))
            for n, (value, axis) in enumerate(eigen_sym3(r.tensor), 1):
                self.principal.append((f"I{n}", f"{fixed(value * scale, dec)} {self.inertia_unit}",
                                       vector_text(axis, 3)))
                self.csv.append((f"I{n}", value * scale, self.inertia_unit))
                for k, axis_name in enumerate(("X", "Y", "Z")):
                    self.csv.append((f"Axis {n} {axis_name}", axis[k], ""))

    def text_rows(self, which):
        rows = [("Material", self.material), ("Density", self.density), ("Mass", self.mass),
                ("Volume", self.volume), ("Area", self.area), ("Bounding box", self.box),
                (self.centre_label, self.centre)]
        if self.inertia:
            rows += [(f"{label} ({FRAME_WORDS[which].lower()})", value) for label, value in self.inertia]
            for label, value, axis in self.principal:
                rows += [(label, value), (f"{label} axis", axis)]
        return rows


# ----- widgets -----------------------------------------------------------------------------


def _make_classes():
    from PySide import QtCore, QtWidgets

    import bruceworx_inspector as bi

    _Switch, Segmented, Inspector = bi.classes()

    class PhysicalTab(Inspector):
        """The Physical tab: the Properties tab's sections and footer, with Copy and Export CSV."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxPhysical")
            foot = self.physical.parentWidget()
            row = foot.layout()
            self.physical.hide()  # the Properties tab's own buttons
            self.more.hide()
            self.copy = self._foot_button(foot, "Copy", "Copy the values as text")
            self.export = self._foot_button(foot, "Export CSV", "Save the values as a CSV file")
            row.insertWidget(0, self.copy)
            row.addWidget(self.export)

        def _foot_button(self, foot, text, tip):
            b = QtWidgets.QToolButton(foot)
            b.setObjectName("bwInspectorMore")
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            return b

        def finish(self):
            """One label column for all sections, so the values line up as in one form."""
            labels = [lab for lab in self.body.findChildren(QtWidgets.QLabel, "bwInspectorLabel")]
            width = max([lab.fontMetrics().horizontalAdvance(lab.text()) for lab in labels] + [78])
            for grid_owner in self.body.findChildren(QtWidgets.QWidget, "bwInspectorSectionBody"):
                grid_owner.layout().setColumnMinimumWidth(0, width + 2)
            super().finish()

        def plain(self, key):
            """A section without a head: rows only."""
            s = self.section(key, "")
            head = s.findChild(QtWidgets.QWidget, "bwInspectorSectionHead")
            if head is not None:
                head.hide()
            s.set_open(True)
            return s

    return PhysicalTab, Segmented


_classes = None


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


def _value_label(text, tip=""):
    from PySide import QtCore, QtWidgets

    lab = QtWidgets.QLabel(text)
    lab.setObjectName("bwInspectorValue")
    lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    lab.setMinimumWidth(20)
    lab.setToolTip(tip or text)
    return lab


# ----- the controller ----------------------------------------------------------------------


class Controller:
    """Fills the Physical tab from the selection and keeps it current."""

    def __init__(self, tab):
        from PySide import QtCore

        self.tab = tab
        tab.controller = self
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)  # a recompute sends many changes: one refresh
        self.timer.timeout.connect(self.refresh)
        self.facts = None
        self.dirty = True
        tab.copy.clicked.connect(self.copy)
        tab.export.clicked.connect(self.export)

    def schedule(self):
        self.dirty = True
        if self.timer is not None and _alive(self.tab):
            self.timer.start()

    def stop(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer = None

    def refresh(self):
        if not _alive(self.tab) or not self.tab.isVisible():
            return  # the panel calls selection_changed when the tab is shown
        self.dirty = False
        try:
            self._refresh()
        except Exception:
            import traceback

            import FreeCAD

            FreeCAD.Console.PrintError("BruceWorx Physical:\n" + traceback.format_exc())

    def _refresh(self):
        tab = self.tab
        scroll = tab.scroll.verticalScrollBar().value()
        owners, objects = owners_of_selection()
        tab.clear()
        self.facts = None
        tab.copy.setEnabled(False)
        tab.export.setEnabled(False)
        if not owners:
            tab.hint(EMPTY_HINT if not objects else "No solid in the selection.")
            tab.finish()
            return
        which = frame()
        title, sub = describe(objects, owners)
        facts = Facts(Result(owners), which, title, sub)
        self.facts = facts
        head = tab.section("physical", title, sub)
        head.arrow.hide()  # the selection's name, not a fold
        head.set_open(True)
        head.body.hide()
        s = tab.plain("physical.material")
        s.add_row("Material", _value_label(facts.material))
        s.add_row("Density", _value_label(facts.density))
        tab.separator()
        s = tab.plain("physical.mass")
        s.add_row("Mass", _value_label(facts.mass))
        s.add_row("Volume", _value_label(facts.volume))
        s.add_row("Area", _value_label(facts.area, "Surface area"))
        tab.separator()
        s = tab.plain("physical.place")
        s.add_row("Bounding box", _value_label(facts.box, "Size along X, Y and Z (global axes)"))
        s.add_row(facts.centre_label, _value_label(
            facts.centre, "Global coordinates" + ("" if facts.centre_label == "Centre of mass" else
                                                  ": no material, so the geometric centre")))
        if facts.note:
            note = _value_label(facts.note)
            note.setObjectName("bwInspectorHint")
            s.add_full(note)
        self._inertia(facts, which)
        tab.finish()
        tab.copy.setEnabled(True)
        tab.export.setEnabled(True)
        tab.scroll.verticalScrollBar().setValue(scroll)

    def _inertia(self, facts, which):
        _PhysicalTab, Segmented = classes()
        tab = self.tab
        summary = f"about the {FRAME_WORDS[which].lower()}" if facts.inertia else ""
        s = tab.section("physical.inertia", "Moments of inertia", "", (), opened=False, summary=summary)
        if not facts.inertia:
            hint = _value_label(NO_MATERIAL_HINT)
            hint.setObjectName("bwInspectorHint")
            s.add_full(hint)
        else:
            about = Segmented([FRAME_WORDS[COM], FRAME_WORDS[ORIGIN]])
            about.set_value(FRAME_WORDS[which])
            about.chosen.connect(lambda word: self.set_frame(word))
            s.add_row("About", about, "Moments about the centre of mass, or about the global origin")
            for label, value in facts.inertia:
                s.add_row(label, _value_label(value, f"{label}, about the {FRAME_WORDS[which].lower()}"
                                              + (" (products as the tensor: −∫xy dm)" if label[1] != label[2]
                                                 else "")))
        p = tab.section("physical.principal", "Principal axes", "", (), opened=False,
                        summary="I1, I2, I3" if facts.principal else "")
        if not facts.principal:
            hint = _value_label(NO_MATERIAL_HINT)
            hint.setObjectName("bwInspectorHint")
            p.add_full(hint)
        for label, value, axis in facts.principal:
            p.add_row(label, _value_label(value, f"Principal moment {label}, about the centre of mass"))
            p.add_row("   axis", _value_label(axis, f"Direction of the axis of {label} (global X, Y, Z)"))

    def set_frame(self, word):
        which = next((k for k, w in FRAME_WORDS.items() if w == word), COM)
        if which != frame():
            set_frame(which)
            self.schedule()

    # -- copy and export ---------------------------------------------------------------------
    def copy(self):
        from PySide import QtWidgets

        if self.facts is None:
            return
        f = self.facts
        QtWidgets.QApplication.clipboard().setText(clip_text(f"{f.title} ({f.sub})", f.text_rows(frame())))

    def export(self):
        import os

        from PySide import QtWidgets

        import FreeCAD

        if self.facts is None:
            return
        doc = FreeCAD.ActiveDocument
        folder = os.path.dirname(getattr(doc, "FileName", "") or "") or os.path.expanduser("~")
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self.tab, "Export physical properties", os.path.join(folder, file_stem(self.facts.title) + ".csv"),
            "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(csv_text([(label, repr(float(v)) if isinstance(v, float) else v, unit)
                                   for label, v, unit in self.facts.csv]))
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self.tab, "Export CSV", f"Could not write {path}:\n{exc}")


# ----- install -----------------------------------------------------------------------------

_controller = None
_installed = False
_observer = None
WATCHED = ("ShapeMaterial", "MassOverride", "Placement", "Label", "Shape")


def controller():
    return _controller


def _schedule(*_args):
    if _controller is not None:
        _controller.schedule()


class _DocObserver:
    """Recomputes, material changes, undo and redo: the tab follows them."""

    def slotRecomputedDocument(self, doc):
        _schedule()

    def slotChangedObject(self, obj, prop):
        if prop in WATCHED:
            _schedule()

    def slotUndoDocument(self, doc):
        _schedule()

    def slotRedoDocument(self, doc):
        _schedule()

    def slotDeletedObject(self, obj):
        _schedule()


def _factory(parent):
    global _controller
    PhysicalTab, _Segmented = classes()
    if _controller is not None:
        _controller.stop()
    tab = PhysicalTab(parent)
    _controller = Controller(tab)
    return tab


def _shutdown():
    global _controller
    import FreeCAD

    if _observer is not None:
        try:
            FreeCAD.removeDocumentObserver(_observer)
        except Exception:
            pass
    if _controller is not None:
        _controller.stop()
        _controller = None


def install():
    """The Physical tab of the right panel (bruceworx_rightpanel), and the observer that keeps it current."""
    global _installed, _observer
    if _installed:
        return
    import FreeCAD
    from PySide import QtWidgets

    import bruceworx_rightpanel

    if not bruceworx_rightpanel.enabled():
        return
    bruceworx_rightpanel.register(TAB, _factory)
    _observer = _DocObserver()
    FreeCAD.addDocumentObserver(_observer)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _installed = True
