# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mass report and drone balance, the FreeCAD side (roadmap ROB-03 and ROB-04). No GUI here.

Mass report: walks an assembly (links, sub-assemblies, link arrays, routed wires) and lists each part once with its
quantity, where its mass comes from, its mass and share, then the totals, the centre of gravity and the inertia
about the CG and about the origin. A part's mass comes, in this order, from MeasuredMass, DatasheetMass, a body's
MassOverride, then volume times density (bruceworx/partprops.py). With a typed mass the centre of mass is the
shape's centroid and the inertia is the shape's scaled to that mass. A routed wire brings its own mass (ROU-02).

Drone balance: a motor part carries ``ThrustAxis`` (direction of thrust in the part's frame) and ``ThrustTable``
(path of a CSV: throttle %, thrust g, current A, voltage V); a battery part may carry ``CapacitymAh``. The motor's
point is the centre of the motor in the model. The maths is in bruceworx/balance.py.

Lengths are mm, masses kg inside; the report speaks grams, mm and g mm².
"""

from __future__ import annotations

import csv
import io
import math
import os

import bruceworx_physical as bp

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
THRUST_AXIS, THRUST_TABLE, CAPACITY = "ThrustAxis", "ThrustTable", "CapacitymAh"
GROUP = "BruceWorx"
WIRE_TYPE = "BruceWorx::Wire"
SKIP_TYPES = ("App::Origin", "App::Line", "App::Plane", "App::Placement")
SOURCES = {"measured": "Measured", "datasheet": "Datasheet", "override": "Mass override", "density": "Density x volume",
           "wire": "Routed wire", "none": "No mass"}
G_PER_KG = 1000.0
MAX_ARRAY = 2000


def _pp():
    from bruceworx import partprops

    return partprops


# ----- instances: one occurrence of a part in the assembly --------------------------------------------------------


class Instance:
    """One occurrence: its mass (kg), centre (mm, global), inertia about the centre (kg mm²), source and frame."""

    __slots__ = ("target", "label", "mass", "centre", "tensor", "source", "missing", "rotation")

    def __init__(self, target, label, mass, centre, tensor, source, missing=0, rotation=None):
        self.target, self.label, self.mass, self.centre, self.tensor = target, label, mass, centre, tensor
        self.source, self.missing, self.rotation = source, missing, rotation

    @property
    def key(self):
        return (self.target.Document.Name, self.target.Name, self.source)


def _source_of(owners, typed):
    if typed:
        return typed[1]
    found = set()
    for o in owners:
        if o.mass is None:
            found.add("none")
        elif o.density is None:
            found.add("override")
        else:
            found.add("density")
    # an owner with a MassOverride AND a density reports "override" (the override wins in bp.Owner)
    for o in owners:
        if o.mass is not None and o.density is not None and bp.material_info(o.obj)[2] is not None:
            found.discard("density")
            found.add("override")
    if len(found) == 1:
        return found.pop()
    return "+".join(sorted(found, key=("override", "density", "none").index)) if found else "none"


def _apply_typed(owners, typed):
    """Give the owners the part's typed mass, split by volume (centroid and inertia scale with it)."""
    volumes = [sum(s.Volume for s in o.shape.Solids) for o in owners]
    total = sum(volumes)
    for o, v in zip(owners, volumes):
        o.mass = typed[0] * v / total if total > 0 else None


def _rotation(top, names, c):
    """The occurrence's global rotation (a FreeCAD Rotation), or None."""
    import FreeCAD

    try:
        if top is not None and names:
            plc = top.getSubObject(".".join(names) + ".", 3)
            if plc is not None:
                return plc.Rotation
        return c.getGlobalPlacement().Rotation
    except Exception:  # noqa: BLE001
        return FreeCAD.Rotation()


def _part_instance(top, names, c, target):
    owners = []
    bp._collect(top, names, c, owners)
    if not owners:
        return None
    typed = _pp().typed_mass(target)
    if typed:
        _apply_typed(owners, typed)
    result = bp.Result(owners)
    missing = result.missing if not typed else 0
    mass = result.mass if result.has_mass else 0.0
    centre = result.centre if result.centre is not None else (0.0, 0.0, 0.0)
    tensor = result.tensor if result.tensor is not None else bp.ZERO
    return Instance(target, target.Label, mass, centre, tensor, _source_of(owners, typed), missing,
                    _rotation(top, names, c))


def _wire_instance(top, names, c):
    shape = bp._global_shape(top, names, c)
    if shape is None or shape.isNull():
        return None
    kg = c.Mass.getValueAs("kg").Value if hasattr(c.Mass, "getValueAs") else float(c.Mass)
    solids = shape.Solids
    com = (shape.CenterOfMass.x, shape.CenterOfMass.y, shape.CenterOfMass.z)
    tensor = bp.ZERO
    volume = sum(s.Volume for s in solids)
    if solids and volume > 0 and kg > 0:
        items = []
        for s in solids:
            m = s.MatrixOfInertia
            t = ((m.A11, m.A12, m.A13), (m.A21, m.A22, m.A23), (m.A31, m.A32, m.A33))
            rho = kg / volume
            cc = s.CenterOfMass
            items.append((rho * s.Volume, (cc.x, cc.y, cc.z), tuple(tuple(rho * x for x in row) for row in t)))
        combined = bp.combine(items)
        if combined:
            com, tensor = combined[1], combined[2]
    return Instance(c, c.Label, kg, com, tensor, "wire")


def _is_wire(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", "") == WIRE_TYPE and "Mass" in obj.PropertiesList


def _has_body(obj):
    return "ShapeMaterial" in getattr(obj, "PropertiesList", ()) and bp._has_solids(obj)


def _is_leaf(target):
    """A part that holds its own bodies (or is one): its content is one item."""
    if _has_body(target):
        return True
    if bp.is_pd_feature(target):
        return True
    return any(_has_body(ch) and not bp.is_pd_feature(ch) and not _is_wire(ch) for ch in (getattr(target, "Group", None) or []))


def _element_names(c):
    """Sub-name segments of the occurrences a link or link array stands for."""
    count = getattr(c, "ElementCount", 0) or 0
    if count > 0 and "LinkedObject" in c.PropertiesList:
        if getattr(c, "ShowElement", False):
            return [e.Name for e in c.ElementList][:MAX_ARRAY]
        return [f"{c.Name}.{i}" for i in range(min(count, MAX_ARRAY))]
    return [c.Name]


def walk(top, names, container, out, seen=None, depth=0):
    """Append the Instances under ``container`` (reached from ``top`` by ``names``) to ``out``."""
    if depth > 30 or container is None:
        return
    seen = set() if seen is None else seen
    try:
        target = container.getLinkedObject(True) or container
    except Exception:  # noqa: BLE001
        target = container
    for c in list(getattr(target, "Group", None) or []):
        tid = getattr(c, "TypeId", "")
        if tid in SKIP_TYPES or "Joint" in tid or "ViewGroup" in tid or "BillOfMaterials" in tid:
            continue
        seen.add(c.Name)
        if _is_wire(c):
            inst = _wire_instance(top, names + [c.Name] if top is not None else names, c)
            if inst:
                out.append(inst)
            continue
        try:
            linked = c.getLinkedObject(True) or c
        except Exception:  # noqa: BLE001
            linked = c
        if bp.is_pd_feature(linked):
            continue
        for segment in _element_names(c):
            path = names + [segment] if top is not None else names
            if _is_leaf(linked):
                inst = _part_instance(top, path, c, linked)
                if inst:
                    out.append(inst)
            elif getattr(linked, "Group", None):
                walk(top, path, c, out, seen, depth + 1)


# ----- the report -------------------------------------------------------------------------------------------------


class Row:
    __slots__ = ("label", "qty", "source", "unit_mass", "mass", "share", "missing")

    def __init__(self, label, qty, source, unit_mass, mass, missing):
        self.label, self.qty, self.source, self.unit_mass, self.mass, self.missing = (
            label, qty, source, unit_mass, mass, missing)
        self.share = 0.0


class Report:
    """Rows and totals of one assembly. Masses in kg, centre in mm, tensors in kg mm²."""

    def __init__(self, root_label, instances):
        self.root_label = root_label
        self.instances = instances
        rows = {}
        for inst in instances:
            row = rows.get(inst.key)
            if row is None:
                row = rows[inst.key] = Row(inst.label, 0, inst.source, inst.mass, 0.0, 0)
            row.qty += 1
            row.mass += inst.mass
            row.missing += inst.missing
        self.rows = sorted(rows.values(), key=lambda r: -r.mass)
        total = combined = bp.combine([(i.mass, i.centre, i.tensor) for i in instances])
        self.mass = total[0] if total else 0.0
        self.cg = combined[1] if combined else None
        self.tensor = combined[2] if combined else None
        for row in self.rows:
            row.share = row.mass / self.mass if self.mass > 0 else 0.0
        self.missing = sum(r.missing for r in self.rows) + sum(1 for i in instances if i.source == "none")

    def tensor_origin(self):
        return bp.shift_inertia(self.tensor, self.mass, self.cg) if self.tensor else None

    def csv_text(self):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(("Item", "Qty", "Mass source", "Mass each (g)", "Mass (g)", "Share (%)"))
        for r in self.rows:
            w.writerow((r.label, r.qty, SOURCES.get(r.source, r.source), f"{r.unit_mass * G_PER_KG:.4f}",
                        f"{r.mass * G_PER_KG:.4f}", f"{r.share * 100:.2f}"))
        w.writerow(())
        w.writerow(("Total", sum(r.qty for r in self.rows), "", "", f"{self.mass * G_PER_KG:.4f}", "100.00"))
        if self.cg:
            w.writerow(("CG x, y, z (mm)",) + tuple(f"{v:.4f}" for v in self.cg))
            for label, t in (("Inertia about CG (g mm^2)", self.tensor), ("Inertia about origin (g mm^2)",
                                                                       self.tensor_origin())):
                w.writerow((label, "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz"))
                w.writerow(("",) + tuple(f"{t[i][j] * G_PER_KG:.4f}" for _n, i, j in bp.INERTIA_ROWS))
        return buf.getvalue()


def roots_of_selection(doc, selection=()):
    """[(top, names, container)] to walk: the selected assembly/part/group, else the active assembly, else the
    document's top level."""
    picks = [o for o in selection if getattr(o, "Group", None) is not None or hasattr(o, "getLinkedObject")]
    if picks:
        return [(o, [], o) for o in picks[:1]]
    for o in doc.Objects:
        if o.TypeId == "Assembly::AssemblyObject":
            return [(o, [], o)]
    for o in doc.Objects:
        if o.TypeId == "App::Part" and not o.InList:
            return [(o, [], o)]
    return [(None, [], o) for o in doc.RootObjects if getattr(o, "Group", None) is not None
            and o.TypeId not in SKIP_TYPES]


def build_report(doc, selection=()):
    instances, label = [], doc.Name
    for top, names, container in roots_of_selection(doc, selection):
        label = container.Label
        if top is None:
            walk(None, [], container, instances)
        else:
            # a selected leaf part is its own single item
            try:
                target = container.getLinkedObject(True) or container
            except Exception:  # noqa: BLE001
                target = container
            if _is_leaf(target):
                inst = _part_instance(top, [], container, target)
                if inst:
                    instances.append(inst)
            else:
                walk(top, names, container, instances)
    return Report(label, instances)


def cg_marker_shapes(report, size=None):
    """(sphere, plumb line) Part shapes at the CG, sized to the model; None without a CG."""
    import FreeCAD
    import Part

    if report.cg is None:
        return None
    cg = FreeCAD.Vector(*report.cg)
    span = max((math.dist(report.cg, i.centre) for i in report.instances), default=10.0)
    size = size or max(span, 10.0)
    sphere = Part.makeSphere(size * 0.05, cg)
    line = Part.makeCylinder(size * 0.012, size * 1.6, FreeCAD.Vector(cg.x, cg.y, cg.z - size * 0.8))
    return sphere, line


# ----- drone balance ----------------------------------------------------------------------------------------------


def _ensure(obj, kind, name, doc, default=None):
    if name not in obj.PropertiesList:
        obj.addProperty(kind, name, GROUP, doc)
        if default is not None:
            setattr(obj, name, default)
    return name


def mark_motor(part, axis=(0.0, 0.0, 1.0), table_path=""):
    """Mark ``part`` (the motor part, not a link) with ThrustAxis and ThrustTable."""
    import FreeCAD

    _ensure(part, "App::PropertyVector", THRUST_AXIS, "Direction of thrust in the part's frame (marks a motor)",
            FreeCAD.Vector(*axis))
    _ensure(part, "App::PropertyFile", THRUST_TABLE,
            "CSV thrust table for this motor and propeller: throttle %, thrust g, current A, voltage V")
    if table_path:
        setattr(part, THRUST_TABLE, table_path)


def set_battery_capacity(part, mah):
    _ensure(part, "App::PropertyFloat", CAPACITY, "Pack capacity in mAh (flight time)")
    setattr(part, CAPACITY, float(mah))


def motors_of(report):
    """[(Instance, axis_global (x, y, z), table_path)] for every occurrence of a part marked as a motor."""
    out = []
    for inst in report.instances:
        t = inst.target
        if THRUST_AXIS not in getattr(t, "PropertiesList", ()):
            continue
        axis = t.ThrustAxis
        if inst.rotation is not None:
            axis = inst.rotation.multVec(axis)
        path = getattr(t, THRUST_TABLE, "") if THRUST_TABLE in t.PropertiesList else ""
        out.append((inst, (axis.x, axis.y, axis.z), path))
    return out


def battery_capacity(report):
    """mAh of the first part that carries CapacitymAh, else 0."""
    for inst in report.instances:
        if CAPACITY in getattr(inst.target, "PropertiesList", ()) and getattr(inst.target, CAPACITY) > 0:
            return float(getattr(inst.target, CAPACITY))
    return 0.0


def read_table(path):
    from bruceworx import balance

    with open(path, encoding="utf-8-sig") as fh:
        return balance.parse_thrust_csv(fh.read())


def thresholds():
    """The balance thresholds, from the preferences (User parameter ... BruceWorx), else the defaults."""
    from bruceworx import balance

    out = dict(balance.DEFAULTS)
    try:
        import FreeCAD

        grp = FreeCAD.ParamGet(PREFS_PATH)
        for key, default in balance.DEFAULTS.items():
            out[key] = grp.GetFloat("Balance_" + key, default)
    except Exception:  # noqa: BLE001
        pass
    return out


def balance_of(report, capacity_mah=None, limits=None, tables=None):
    """(Balance, notes): the drone balance of a Report. ``tables`` {path: ThrustTable} overrides reading files."""
    from bruceworx import balance

    notes, motors = [], []
    for inst, axis, path in motors_of(report):
        table = None
        if path:
            try:
                table = (tables or {}).get(path) or read_table(path)
            except (OSError, balance.ThrustError) as exc:
                notes.append(f"{inst.label}: {exc}")
        motors.append((f"{inst.label} #{len(motors) + 1}", inst.centre, axis, table))
    if not motors:
        raise balance.ThrustError("no motors are marked: give a motor part a ThrustAxis")
    cap = battery_capacity(report) if capacity_mah is None else capacity_mah
    result = balance.analyse(report.mass * G_PER_KG, report.cg, motors, cap, limits or thresholds())
    return result, notes
