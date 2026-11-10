# SPDX-License-Identifier: LGPL-2.1-or-later
"""Wire routing objects (BW-WIR-02..06). Needs FreeCAD; no GUI.

**Port**: a coordinate-system datum on a part. The wire leaves it along its
local +Z. A datum stays put when the model is edited; a picked face does not.

**Wire**: a ``Part::FeaturePython`` that sits in the assembly next to the
components. ``Start`` and ``End`` point at ports and ``Via`` at clips, each
as ``(component, "Path.To.Port.")`` the way assembly joints point at
geometry, so a port inside a linked part file works. The centerline and its
length come from ``geometry.route``; the tube is display only and
``ShowTube`` turns it off. A wire that cannot bend raises in ``execute``:
FreeCAD marks it red, ``Status`` says which segment, and it has no shape.

**Following a joint**: the Assembly solver runs inside the assembly's own
recompute, *after* the assembly's children, so a wire recomputed in the
normal order would be one solve behind. :class:`Follower` listens for
placement changes and rebuilds the wires that depend on the moved object, in
a recompute and while a part is dragged.

**Sweep**: a routed path across a joint changes length as the joint moves,
and a real wire does not. :func:`sweep` drives each limited joint through
its range and records the shortest and longest path and where the wire
would kink. The cut list uses the longest.
"""

from __future__ import annotations

import FreeCAD as App
import Part

from wire_routing import cutlist, geometry

PREF_GROUP = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_KEY = "WireRouting"
PORT_TYPES = ("Part::LocalCoordinateSystem", "App::LocalCoordinateSystem", "PartDesign::CoordinateSystem")
GROUP = "Wire"


def enabled() -> bool:
    return bool(App.ParamGet(PREF_GROUP).GetBool(PREF_KEY, True))


# ----- references ------------------------------------------------------------


def as_ref(target):
    """``(object, "sub.path.")`` from an object or a pair. The object is the
    one that sits beside the wire (a component of the assembly, or the port
    itself); the path leads from it down to the port."""
    if isinstance(target, (tuple, list)):
        obj, sub = target[0], target[1]
        if isinstance(sub, (tuple, list)):
            sub = sub[0] if sub else ""
        return obj, sub or ""
    return target, ""


def ref_placement(ref) -> App.Placement:
    """Placement of the referenced datum in the coordinates the root object
    sits in (the assembly's, for a component)."""
    obj, sub = as_ref(ref)
    if obj is None:
        raise ValueError("empty reference")
    if not sub:
        return App.Placement(obj.Placement)
    if not sub.endswith("."):
        sub += "."
    found = obj.getSubObject(sub, retType=1)
    if found is None:
        raise ValueError(f"{obj.Label}: nothing at {sub!r}")
    return App.Placement(obj.getSubObject(sub, retType=3))


def ref_label(ref) -> str:
    """``Component.Port`` for the cut list."""
    obj, sub = as_ref(ref)
    if obj is None:
        return ""
    if not sub:
        return obj.Label
    target = obj.getSubObject(sub if sub.endswith(".") else sub + ".", retType=1)
    leaf = target.Label if target is not None else sub.strip(".").split(".")[-1]
    return f"{obj.Label}.{leaf}"


def port_ref(component, port):
    """Reference to ``port`` as reached through ``component``: an assembly
    component (usually a link to a part file) that holds the port, possibly
    inside nested groups. The path uses object Names, as FreeCAD does."""
    if component is port:
        return port, ""
    root = component.getLinkedObject(True)

    def find(group, trail, depth):
        for child in getattr(group, "Group", None) or []:
            if child is port:
                return trail + [child.Name]
            if depth < 6:
                deeper = find(child.getLinkedObject(True), trail + [child.Name], depth + 1)
                if deeper:
                    return deeper
        return None

    path = find(root, [], 0)
    if path is None:
        raise ValueError(f"{port.Label} is not inside {component.Label}")
    return component, ".".join(path) + "."


def _vec(v):
    return (v.x, v.y, v.z)


# ----- port --------------------------------------------------------------------


def make_port(container, label="Port", placement=None):
    """Add a port datum to ``container`` (an ``App::Part``, a Body's parent
    part, or a document). The wire leaves along the port's +Z."""
    doc = container if hasattr(container, "addObject") and not hasattr(container, "Document") else container.Document
    port = None
    for type_id in PORT_TYPES[:2]:
        try:
            port = doc.addObject(type_id, "Port")
            break
        except Exception:  # noqa: BLE001 — type not in this build
            continue
    if port is None:
        raise RuntimeError("this FreeCAD has no coordinate-system datum type")
    port.Label = label
    if container is not doc:
        container.addObject(port)
    if placement is not None:
        port.Placement = placement
    return port


# ----- wire --------------------------------------------------------------------


class Wire:
    """Proxy of the wire feature. Holds no state: everything is a property."""

    def __init__(self, obj):
        obj.Proxy = self
        self.ensure_properties(obj)

    @staticmethod
    def ensure_properties(obj):
        have = set(obj.PropertiesList)

        def add(kind, name, doc, default=None, readonly=False):
            if name in have:
                return
            obj.addProperty(kind, name, GROUP, doc)
            if default is not None:
                setattr(obj, name, default)
            if readonly:
                obj.setEditorMode(name, ["ReadOnly"])

        add("App::PropertyXLinkSub", "Start", "Port the wire starts at")
        add("App::PropertyXLinkSub", "End", "Port the wire ends at")
        # Not PropertyXLinkSubList: that one groups its entries by object and
        # hands them back in an order that changes from run to run. The order
        # of clips is the route. A reference always starts at an object in
        # the wire's own document (the component), so a plain link list does.
        add("App::PropertyLinkSubList", "Via", "Clips the wire passes, in order")
        add("App::PropertyLength", "Diameter", "Outside diameter of the wire", 1.6)
        add("App::PropertyLength", "MinBendRadius", "Smallest bend radius; every corner is rounded to it", 6.0)
        add("App::PropertyLength", "LeadOut", "Straight run out of each port before the wire may bend", 5.0)
        add("App::PropertyString", "Gauge", "Wire gauge, for the cut list", "22 AWG")
        add("App::PropertyString", "WireColor", "Insulation colour, for the cut list", "red")
        add("App::PropertyLength", "Allowance", "Extra length added to the cut length (strip, crimp, slack)", 10.0)
        add("App::PropertyBool", "ShowTube", "Show the wire as a tube; off shows the centerline only", True)
        add("App::PropertyLength", "Length", "Centerline length in this pose", readonly=True)
        add("App::PropertyLength", "LengthMin", "Shortest path over the joint sweep", readonly=True)
        add("App::PropertyLength", "LengthMax", "Longest path over the joint sweep", readonly=True)
        add("App::PropertyLength", "CutLength", "Longest path plus the allowance, rounded up to 1 mm", readonly=True)
        add("App::PropertyString", "Status", "ok, or why the wire cannot be built", "", readonly=True)
        add("App::PropertyString", "SweepStatus", "What the joint sweep found", "", readonly=True)

    def onDocumentRestored(self, obj):
        self.ensure_properties(obj)

    def execute(self, obj):
        rebuild(obj, raise_on_error=True)

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    # FreeCAD < 1.0 names
    __getstate__ = dumps
    __setstate__ = loads


def is_wire(obj) -> bool:
    return isinstance(getattr(obj, "Proxy", None), Wire)


def wires(doc) -> list:
    return [o for o in doc.Objects if is_wire(o)]


def make_wire(container, start, end, via=(), label="Wire", **properties):
    """Add a wire to ``container`` (the assembly, or a document).

    ``start``, ``end`` and each of ``via`` are a port object or
    ``(component, "Path.To.Port.")``. ``properties`` sets Wire properties,
    for example ``Diameter=2.0, MinBendRadius=8, Gauge="18 AWG"``.
    """
    is_doc = not hasattr(container, "Document")
    doc = container if is_doc else container.Document
    obj = doc.addObject("Part::FeaturePython", "Wire")
    Wire(obj)
    obj.Label = label
    if not is_doc:
        container.addObject(obj)
    set_route(obj, start, end, via)
    for name, value in properties.items():
        setattr(obj, name, value)
    if App.GuiUp:
        try:
            obj.ViewObject.Proxy = 0
        except Exception:  # noqa: BLE001 — view provider not ready
            pass
    return obj


def set_route(obj, start=None, end=None, via=None):
    if start is not None:
        o, s = as_ref(start)
        obj.Start = (o, [s])
    if end is not None:
        o, s = as_ref(end)
        obj.End = (o, [s])
    if via is not None:
        obj.Via = [(o, s) for o, s in (as_ref(v) for v in via)]


def _via_refs(obj) -> list:
    refs = []
    for entry in obj.Via or []:
        target, subs = entry[0], entry[1]
        if isinstance(subs, str):
            subs = [subs]
        for sub in subs or [""]:
            refs.append((target, sub))
    return refs


def wire_polyline(obj) -> list:
    """The wire's points in this pose, before rounding."""
    start = ref_placement(obj.Start)
    end = ref_placement(obj.End)
    z = App.Vector(0, 0, 1)
    via = [_vec(ref_placement(r).Base) for r in _via_refs(obj)]
    return geometry.wire_points(
        _vec(start.Base),
        _vec(start.Rotation.multVec(z)),
        _vec(end.Base),
        _vec(end.Rotation.multVec(z)),
        via,
        obj.LeadOut.Value,
    )


def wire_route(obj) -> geometry.Route:
    """Route in this pose. Raises ``geometry.RouteError`` or ``ValueError``."""
    if not obj.Start or obj.Start[0] is None or not obj.End or obj.End[0] is None:
        raise ValueError("the wire needs a start port and an end port")
    radius = obj.MinBendRadius.Value
    if obj.ShowTube and obj.Diameter.Value > 0 and radius < obj.Diameter.Value / 2.0 - 1e-9:
        raise ValueError(
            f"bend radius {radius:g} mm is less than half the wire diameter {obj.Diameter.Value:g} mm"
        )
    return geometry.route(wire_polyline(obj), radius)


def route_shape(route: geometry.Route, diameter: float = 0.0):
    """Centerline as a ``Part.Wire``; with ``diameter``, the swept tube."""
    edges = []
    for element in route.elements:
        if isinstance(element, geometry.Arc):
            curve = Part.Arc(App.Vector(*element.start), App.Vector(*element.mid), App.Vector(*element.end))
        else:
            curve = Part.LineSegment(App.Vector(*element.start), App.Vector(*element.end))
        edges.append(curve.toShape())
    centerline = Part.Wire(edges)
    if diameter <= 0:
        return centerline
    first = route.elements[0]
    direction = App.Vector(*geometry.sub(first.end, first.start))
    if isinstance(first, geometry.Arc):  # no lead-out: start along the arc's tangent
        direction = edges[0].tangentAt(edges[0].FirstParameter)
    profile = Part.Wire([Part.Circle(App.Vector(*first.start), direction, diameter / 2.0).toShape()])
    tube = centerline.makePipeShell([profile], True, True)
    if tube.isNull() or not tube.isValid():
        raise ValueError("the tube could not be swept along the centerline")
    return tube


def rebuild(obj, raise_on_error=False) -> bool:
    """Recompute one wire in place. Returns whether it could be built."""
    try:
        route = wire_route(obj)
        shape = route_shape(route, obj.Diameter.Value if obj.ShowTube else 0.0)
    except (geometry.RouteError, ValueError) as exc:
        obj.Shape = Part.Shape()
        _set(obj, "Length", 0.0)
        _set(obj, "CutLength", 0.0)
        _set(obj, "Status", str(exc))
        if raise_on_error:
            raise RuntimeError(str(exc)) from exc
        return False
    obj.Shape = shape
    _set(obj, "Length", route.length)
    _set(obj, "Status", "ok")
    _set(obj, "CutLength", cutlist.cut_length(route.length, obj.LengthMax.Value, obj.Allowance.Value))
    return True


def _set(obj, name, value):
    current = getattr(obj, name)
    current = current.Value if hasattr(current, "Value") else current
    if current != value:
        setattr(obj, name, value)


# ----- follow the solver ---------------------------------------------------------


class Follower:
    """Document observer: rebuild wires when something they hang on moves."""

    def __init__(self):
        self.busy = False
        self.deps = {}  # (doc name, wire name) -> names the wire depends on

    def _dependencies(self, wire):
        key = (wire.Document.Name, wire.Name)
        if key not in self.deps:
            names = set()
            for dep in wire.OutListRecursive:
                names.add((dep.Document.Name, dep.Name))
            self.deps[key] = names
        return self.deps[key]

    def slotChangedObject(self, obj, prop):
        if is_wire(obj):
            if prop in ("Start", "End", "Via"):
                self.deps.pop((obj.Document.Name, obj.Name), None)
            return
        if prop != "Placement" or self.busy:
            return
        moved = (obj.Document.Name, obj.Name)
        self.busy = True
        try:
            for doc in App.listDocuments().values():
                for wire in wires(doc):
                    if moved in self._dependencies(wire):
                        rebuild(wire)
                        wire.purgeTouched()
        except Exception as exc:  # noqa: BLE001 — never break the edit that moved the part
            App.Console.PrintLog(f"BruceWorx wire routing: follow failed: {exc}\n")
        finally:
            self.busy = False

    def slotCreatedObject(self, _obj):
        self.deps.clear()

    def slotDeletedObject(self, _obj):
        self.deps.clear()

    def slotDeletedDocument(self, _doc):
        self.deps.clear()


_follower = None


def install_follower():
    """Start following (idempotent). Init.py calls this unless the feature is
    off; wires in a file still recompute normally without it."""
    global _follower
    if _follower is None:
        _follower = Follower()
        App.addDocumentObserver(_follower)
    return _follower


def remove_follower():
    global _follower
    if _follower is not None:
        App.removeDocumentObserver(_follower)
        _follower = None


# ----- joint sweep ----------------------------------------------------------------


def _assembly_of(obj):
    for parent in obj.InListRecursive:
        if parent.isDerivedFrom("Assembly::AssemblyObject"):
            return parent
    return None


def limited_joints(assembly) -> list:
    """(joint, kind, low, high) for joints with both limits set. ``kind`` is
    ``"angle"`` (degrees) or ``"length"`` (mm). A cylindrical joint with both
    kinds limited is listed twice."""
    try:
        import JointObject  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — Assembly module not available
        return []
    out = []
    for joint in assembly.OutListRecursive:
        props = joint.PropertiesList
        if "JointType" not in props:
            continue
        if joint.JointType in JointObject.JointUsingLimitAngle and "AngleMin" in props:
            if joint.EnableAngleMin and joint.EnableAngleMax:
                out.append((joint, "angle", joint.AngleMin.Value, joint.AngleMax.Value))
        if joint.JointType in JointObject.JointUsingLimitLength and "LengthMin" in props:
            if joint.EnableLengthMin and joint.EnableLengthMax:
                out.append((joint, "length", joint.LengthMin.Value, joint.LengthMax.Value))
    return out


def _reading(joint, kind):
    import JointObject  # noqa: PLC0415

    angle, length = JointObject.limitReading(joint)
    return angle if kind == "angle" else length


def _drive(assembly, joint, kind, target, tolerance):
    """Move the joint to ``target`` by dragging one of its parts with the
    solver. Returns whether the joint got there."""
    import UtilsAssembly  # noqa: PLC0415

    connector = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
    axis = connector.Rotation.multVec(App.Vector(0, 0, 1))
    parts = [UtilsAssembly.getMovingPart(joint.Reference2), UtilsAssembly.getMovingPart(joint.Reference1)]
    for index, part in enumerate(parts):
        if part is None:
            continue
        sign = 1.0 if index == 0 else -1.0
        saved = App.Placement(part.Placement)
        delta = (target - _reading(joint, kind)) * sign
        if kind == "angle":
            move = App.Placement(App.Vector(), App.Rotation(axis, delta), connector.Base)
        else:
            move = App.Placement(axis * delta, App.Rotation())
        assembly.preDrag([part])
        part.Placement = move * part.Placement
        assembly.doDragStep()
        assembly.postDrag()
        if abs(_reading(joint, kind) - target) <= tolerance:
            return True
        part.Placement = saved
        assembly.solve()
    return False


def sweep(assembly, steps: int = 12, apply: bool = True) -> dict:
    """Drive every limited joint of ``assembly`` through its range, one joint
    at a time with the others where they are, and measure each wire.

    Returns ``{wire name: {"min", "max", "joints": [...], "kinks": [...]}}``.
    ``apply`` stores ``LengthMin``, ``LengthMax``, ``CutLength`` and
    ``SweepStatus`` on the wires. The assembly is put back as it was.
    Joints without both limits are not swept: their range is not known.
    """
    targets = [w for w in wires(assembly.Document) if _assembly_of(w) is assembly]
    report = {}
    for wire in targets:
        entry = {"min": None, "max": None, "joints": [], "kinks": [], "unreached": []}
        try:
            entry["min"] = entry["max"] = wire_route(wire).length
        except (geometry.RouteError, ValueError) as exc:
            entry["kinks"].append({"joint": "", "at": None, "why": str(exc)})
        report[wire.Name] = entry
    joints = limited_joints(assembly)
    if targets and joints:
        components = [o for o in assembly.OutListRecursive if "Placement" in o.PropertiesList and not is_wire(o)]
        saved = [(o, App.Placement(o.Placement)) for o in components]
        follower, was_busy = _follower, False
        if follower is not None:
            was_busy, follower.busy = follower.busy, True  # measure directly; no rebuild per step
        try:
            for joint, kind, low, high in joints:
                tolerance = 0.5 if kind == "angle" else 0.05
                unit = "°" if kind == "angle" else " mm"
                seen = {name: [] for name in report}
                for step in range(steps + 1):
                    target = low + (high - low) * step / steps
                    if not _drive(assembly, joint, kind, target, tolerance):
                        for entry in report.values():
                            entry["unreached"].append(f"{joint.Label} {target:g}{unit}")
                        continue
                    for wire in targets:
                        entry = report[wire.Name]
                        try:
                            length = wire_route(wire).length
                        except (geometry.RouteError, ValueError) as exc:
                            entry["kinks"].append({"joint": joint.Label, "at": target, "unit": unit, "why": str(exc)})
                            continue
                        seen[wire.Name].append(length)
                        entry["min"] = length if entry["min"] is None else min(entry["min"], length)
                        entry["max"] = length if entry["max"] is None else max(entry["max"], length)
                for o, placement in saved:
                    o.Placement = placement
                assembly.solve()
                for name, lengths in seen.items():
                    if lengths and max(lengths) - min(lengths) > 1e-6:
                        report[name]["joints"].append(
                            {"joint": joint.Label, "kind": kind, "min": min(lengths), "max": max(lengths)}
                        )
        finally:
            for o, placement in saved:
                o.Placement = placement
            if follower is not None:
                follower.busy = was_busy
    if apply:
        for wire in targets:
            entry = report[wire.Name]
            _set(wire, "LengthMin", entry["min"] or 0.0)
            _set(wire, "LengthMax", entry["max"] or 0.0)
            _set(wire, "SweepStatus", sweep_text(entry))
            rebuild(wire)
            wire.purgeTouched()
    return report


def sweep_text(entry) -> str:
    parts = []
    for j in entry["joints"]:
        parts.append(f"{j['joint']}: {j['min']:.1f} to {j['max']:.1f} mm")
    kinks: dict[str, list] = {}
    for k in entry["kinks"]:
        if k["at"] is not None:
            kinks.setdefault(k["joint"], []).append((k["at"], k.get("unit", "")))
    for joint, hits in kinks.items():
        values = sorted(h[0] for h in hits)
        parts.append(f"kinks at {joint} {values[0]:g}{hits[0][1]} to {values[-1]:g}{hits[0][1]}")
    if entry["unreached"]:
        parts.append(f"{len(entry['unreached'])} poses not reached")
    return "; ".join(parts) or "no limited joint changes this wire"


# ----- cut list ---------------------------------------------------------------------


def cut_list(doc, run_sweep: bool = True) -> dict:
    """Cut list for every wire in ``doc`` (``cutlist.as_json`` form).

    ``run_sweep`` sweeps each assembly first, so the cut lengths are for the
    longest pose and not the saved one."""
    swept = {}
    if run_sweep:
        for assembly in [o for o in doc.Objects if o.isDerivedFrom("Assembly::AssemblyObject")]:
            swept.update(sweep(assembly))
    rows = []
    for wire in wires(doc):
        if wire.Name not in swept:
            rebuild(wire)
        entry = swept.get(wire.Name, {})
        status = wire.Status if wire.Status != "ok" else "ok"
        if status == "ok" and any(k["at"] is not None for k in entry.get("kinks", [])):
            status = wire.SweepStatus
        rows.append(
            cutlist.row(
                wire.Label,
                wire.Gauge,
                wire.WireColor,
                ref_label(wire.Start),
                ref_label(wire.End),
                wire.Length.Value,
                entry.get("min") if entry else (wire.LengthMin.Value or None),
                entry.get("max") if entry else (wire.LengthMax.Value or None),
                wire.Allowance.Value,
                status,
            )
        )
    return cutlist.as_json(rows)


# ----- clips, clip graph, bundles -----------------------------------------------------


def make_clip(container, label="Clip", placement=None):
    """A clip is a waypoint: a datum the wire passes through."""
    return make_port(container, label, placement)


def route_through_clips(wire, clips, edges=None, max_span: float | None = None, apply: bool = True):
    """Pick the shortest path the wire can bend through (BW-WIR-06).

    ``clips`` are the references the wire may use. ``edges`` are index pairs
    into ``clips`` that may be joined, with ``"start"`` and ``"end"`` for the
    ports. Without ``edges`` every pair closer than ``max_span`` is joined
    (all pairs when ``max_span`` is None). Sets ``wire.Via`` unless
    ``apply`` is False. Returns ``geometry.GraphRoute`` with clip indices in
    ``path``.
    """
    clips = list(clips)
    start = ref_placement(wire.Start)
    end = ref_placement(wire.End)
    z = App.Vector(0, 0, 1)
    lead = wire.LeadOut.Value
    s_dir, e_dir = start.Rotation.multVec(z), end.Rotation.multVec(z)
    nodes = {
        "start": _vec(start.Base),
        "end": _vec(end.Base),
    }
    for index, clip in enumerate(clips):
        nodes[index] = _vec(ref_placement(clip).Base)
    if edges is None:
        names = list(nodes)
        edges = [
            (a, b)
            for i, a in enumerate(names)
            for b in names[i + 1 :]
            if {a, b} != {"start", "end"}
            and (max_span is None or geometry.distance(nodes[a], nodes[b]) <= max_span)
        ]
    radius = wire.MinBendRadius.Value

    def with_leads(path):
        middle = [nodes[n] for n in path[1:-1]]
        return geometry.wire_points(_vec(start.Base), _vec(s_dir), _vec(end.Base), _vec(e_dir), middle, lead)

    found = geometry.route_on_graph(nodes, edges, "start", "end", radius, points_for=with_leads)
    if apply:
        set_route(wire, via=[clips[i] for i in found.path[1:-1]])
    return found


class Bundle:
    """A sleeve over the run of clips that its wires share (BW-WIR-05)."""

    def __init__(self, obj):
        obj.Proxy = self
        self.ensure_properties(obj)

    @staticmethod
    def ensure_properties(obj):
        have = set(obj.PropertiesList)

        def add(kind, name, doc, default=None, readonly=False):
            if name in have:
                return
            obj.addProperty(kind, name, "Bundle", doc)
            if default is not None:
                setattr(obj, name, default)
            if readonly:
                obj.setEditorMode(name, ["ReadOnly"])

        add("App::PropertyLinkList", "Wires", "Wires in this bundle")
        add("App::PropertyLength", "WallThickness", "Sleeve wall thickness", 0.3)
        add("App::PropertyFloat", "Packing", "Share of the sleeve bore the wires fill (0.75 loose, 0.9 tight)", 0.75)
        add("App::PropertyLength", "Length", "Sleeve length: the shared run, clip to clip", readonly=True)
        add("App::PropertyLength", "InnerDiameter", "Sleeve bore estimated from the wires", readonly=True)
        add("App::PropertyString", "Status", "ok, or why there is no sleeve", "", readonly=True)

    def onDocumentRestored(self, obj):
        self.ensure_properties(obj)

    def execute(self, obj):
        members = [w for w in obj.Wires if is_wire(w)]
        try:
            if len(members) < 2:
                raise ValueError("a bundle needs two or more wires")
            paths = [[_ref_key(r) for r in _via_refs(w)] for w in members]
            run = geometry.shared_run(paths)
            if len(run) < 2:
                raise ValueError("the wires do not share two clips in a row")
            by_key = {_ref_key(r): r for r in _via_refs(members[0])}
            points = [_vec(ref_placement(by_key[key]).Base) for key in run]
            radius = max(w.MinBendRadius.Value for w in members)
            bore = geometry.bundle_diameter([w.Diameter.Value for w in members], obj.Packing)
            route = geometry.route(points, radius)
            outer = route_shape(route, bore + 2 * obj.WallThickness.Value)
            inner = route_shape(route, bore)
            shape = outer.cut(inner)
        except (geometry.RouteError, ValueError) as exc:
            obj.Shape = Part.Shape()
            _set(obj, "Length", 0.0)
            _set(obj, "Status", str(exc))
            raise RuntimeError(str(exc)) from exc
        obj.Shape = shape
        _set(obj, "Length", route.length)
        _set(obj, "InnerDiameter", bore)
        _set(obj, "Status", "ok")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def _ref_key(ref):
    obj, sub = as_ref(ref)
    return (obj.Document.Name, obj.Name, sub)


def make_bundle(container, members, label="Bundle"):
    is_doc = not hasattr(container, "Document")
    doc = container if is_doc else container.Document
    obj = doc.addObject("Part::FeaturePython", "Bundle")
    Bundle(obj)
    obj.Label = label
    obj.Wires = list(members)
    if not is_doc:
        container.addObject(obj)
    if App.GuiUp:
        try:
            obj.ViewObject.Proxy = 0
        except Exception:  # noqa: BLE001
            pass
    return obj
