# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing objects: route paths, wires, pipes, hoses and bundles (BW-ROU-01..04, 27 Oct 2026).

Every object is a ``Part::FeaturePython`` with a proxy from this module; the maths (splines,
tangent arcs, the LRA bend table, gauges, tube sizes, the CSV reports) is FreeCAD-free in
``bruceworx/routing.py``. The GUI (commands, task panels, the Routing tab) is
``bruceworx_routing_gui.py``.

* **RoutePath** is the centreline: ``Points`` in the document frame, optionally attached to
  geometry (``Attachments`` + ``AttachedTo``: a vertex, a circular edge's centre, a face's
  centre or an object's origin, re-read on every recompute so a routed point follows its part).
  ``Mode`` Spline (an interpolating B-spline through the points) or Polyline (straight segments
  joined by tangent arcs of ``BendRadius``). Read-only results: ``Length``, ``MinRadius`` (the
  tightest bend found, 0 for straight), ``Bends``, ``BendOK`` (against ``MinBendRadius``),
  ``BendTable`` (LRA rows in Polyline mode) and ``Problems``.
* **Wire**, **Pipe**, **Hose** sweep a section along their path (``Path``) and carry what the BOM
  and the reports need: ``Length`` in mm, ``QuantityUnit`` ``"m"`` and ``QuantityPer`` (metres per
  occurrence, read by the vault's BOM walker, ROU-04), ``BendOK`` against their own
  ``MinBendRadius``. A wire adds its gauge, insulation, colour, signal, from/to and mass; a pipe
  its size, wall, CLR, cut length and bend table; a hose its ID/OD and slack.
* **Bundle** groups wires on one path with a combined OD (sqrt of the summed squares times a
  packing factor of 1.15), drawn translucent.

A badge "bend too tight" shows in the Browser on any routed object whose ``BendOK`` is False
(``row_badges``, registered by the GUI module).
"""

import math
import os

import FreeCAD as App

HERE = os.path.dirname(os.path.abspath(__file__))
GROUP = "Routing"
PATH = "BruceWorx::RoutePath"
WIRE = "BruceWorx::Wire"
PIPE = "BruceWorx::Pipe"
HOSE = "BruceWorx::Hose"
BUNDLE = "BruceWorx::Bundle"
MODES = ["Spline", "Polyline"]
ITEM_KINDS = (WIRE, PIPE, HOSE, BUNDLE)
_SAMPLES = 120  # curvature samples per edge in Spline mode


def _rt():
    from bruceworx import routing

    return routing


def _part():
    import Part

    return Part


def V(p):
    return App.Vector(float(p[0]), float(p[1]), float(p[2]))


def kind_of(obj):
    proxy = getattr(obj, "Proxy", None)
    return getattr(proxy, "Type", "") if proxy is not None else ""


def is_path(obj):
    return kind_of(obj) == PATH


def is_routed(obj):
    return kind_of(obj) in ITEM_KINDS


def paths(doc):
    return [o for o in doc.Objects if is_path(o)]


def routed(doc, kinds=ITEM_KINDS):
    return [o for o in doc.Objects if kind_of(o) in kinds]


def _add(obj, kind, name, doc, default=None, group=GROUP, readonly=False, hidden=False):
    if name not in obj.PropertiesList:
        obj.addProperty(kind, name, group, doc)
        if default is not None:
            setattr(obj, name, default)
    if readonly:
        obj.setEditorMode(name, 1)
    if hidden:
        obj.setEditorMode(name, 2)


# ---------------------------------------------------------------------------------------------
# Attachments: a point in the document frame from a picked sub-element.


def _parent_frame(obj):
    """The placement that takes ``obj``'s own-frame geometry (what getSubObject returns) to the
    document frame: its parents' placements only."""
    try:
        return obj.getGlobalPlacement().multiply(obj.Placement.inverse())
    except Exception:
        return App.Placement()


def attachment_point(obj, sub=""):
    """The document-frame point a (object, subname) attachment stands for, or None: a vertex, the
    centre of a circular edge or of a face, else the object's origin."""
    if obj is None:
        return None
    try:
        if not sub:
            return App.Vector(obj.getGlobalPlacement().Base)
        shape = obj.getSubObject(sub)
        if shape is None:
            return None
        if shape.ShapeType == "Vertex":
            point = shape.Point
        elif shape.ShapeType == "Edge":
            curve = shape.Curve
            point = App.Vector(curve.Center) if hasattr(curve, "Center") else shape.CenterOfMass
        else:
            point = shape.CenterOfMass
        return _parent_frame(obj).multVec(point)
    except Exception:
        return None


def resolve_points(obj):
    """``obj.Points`` with every attached point re-read from its geometry."""
    points = [App.Vector(p) for p in obj.Points]
    for idx, link, sub in attachments_of(obj):
        point = attachment_point(link, sub)
        if point is not None and 0 <= idx < len(points):
            points[idx] = point
    return points


# ---------------------------------------------------------------------------------------------
# Geometry.


def path_wire(points, mode="Spline", radius=0.0):
    """A Part wire through ``points`` (document frame): a B-spline, or lines and tangent arcs."""
    Part = _part()
    rt = _rt()
    pts = rt.clean_points(points)
    if len(pts) < 2:
        raise ValueError("a route needs at least two distinct points")
    if mode == "Polyline" and len(pts) > 2:
        route = rt.polyline_with_arcs(pts, radius)
        edges = []
        for seg in route.segments:
            if seg[0] == "line":
                edges.append(Part.LineSegment(V(seg[1]), V(seg[2])).toShape())
            else:
                edges.append(Part.Arc(V(seg[1]), V(seg[2]), V(seg[3])).toShape())
        return Part.Wire(edges)
    if len(pts) == 2:
        return Part.Wire([Part.LineSegment(V(pts[0]), V(pts[1])).toShape()])
    curve = Part.BSplineCurve()
    curve.interpolate([V(p) for p in pts])
    return Part.Wire([curve.toShape()])


def wire_min_radius(wire, samples=_SAMPLES):
    """The tightest radius of curvature along the wire's edges (inf when straight)."""
    kmax = 0.0
    for edge in wire.Edges:
        u0, u1 = edge.FirstParameter, edge.LastParameter
        for k in range(samples + 1):
            try:
                kmax = max(kmax, abs(edge.curvatureAt(u0 + (u1 - u0) * k / samples)))
            except Exception:
                continue
    return math.inf if kmax < 1e-9 else 1.0 / kmax


def _start_frame(wire):
    """(point, tangent) at the wire's first vertex."""
    edge = wire.OrderedEdges[0]
    start = wire.OrderedVertexes[0].Point
    u0, u1 = edge.FirstParameter, edge.LastParameter
    if (edge.valueAt(u0) - start).Length <= (edge.valueAt(u1) - start).Length:
        return edge.valueAt(u0), edge.tangentAt(u0)
    return edge.valueAt(u1), edge.tangentAt(u1) * -1


def sweep(wire, od, inner=0.0):
    """A solid of diameter ``od`` swept along ``wire``; with ``inner`` > 0 a tube."""
    Part = _part()
    point, tangent = _start_frame(wire)
    outer = Part.Wire([Part.makeCircle(od / 2.0, point, tangent)])
    solid = wire.makePipeShell([outer], True, False, 2)
    if inner > 0 and inner < od:
        bore = Part.Wire([Part.makeCircle(inner / 2.0, point, tangent)])
        solid = solid.cut(wire.makePipeShell([bore], True, False, 2))
    return solid


def _to_parent(obj, points):
    """Document-frame ``points`` moved into ``obj``'s container frame: the wire is built from these,
    since a shape's own Placement is overwritten by the object's while it recomputes."""
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:
        parent = None
    if parent is None:
        return list(points)
    inverse = parent.getGlobalPlacement().inverse()
    return [tuple(inverse.multVec(V(p))) for p in points]


def _container_of(obj):
    try:
        return obj.getParentGeoFeatureGroup()
    except Exception:
        return None


def _place(obj, like=None, container=None):
    """Put ``obj`` next to ``like`` (its container) or into ``container``."""
    target = container if container is not None else (_container_of(like) if like is not None else None)
    if target is not None and hasattr(target, "addObject"):
        try:
            target.addObject(obj)
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------
# RoutePath.


class RoutePath:
    def __init__(self, obj):
        self.Type = PATH
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        _add(obj, "App::PropertyVectorList", "Points", "Route points in the document frame")
        _add(obj, "App::PropertyLinkSubList", "Attachments",
             "Geometry a point follows: a vertex, a circular edge (its centre), a face (its centre) or an object (its origin)")
        _add(obj, "App::PropertyStringList", "AttachedTo", "Per attachment: point index, object name, sub-element", hidden=True)
        _add(obj, "App::PropertyEnumeration", "Mode", "Spline through the points, or straight segments with tangent arcs")
        obj.Mode = MODES
        _add(obj, "App::PropertyLength", "BendRadius", "Centreline bend radius of the arcs in Polyline mode", 10.0)
        _add(obj, "App::PropertyLength", "MinBendRadius", "Smallest bend radius allowed (0 = no check)", 0.0)
        _add(obj, "App::PropertyLength", "Length", "Length of the centreline", readonly=True)
        _add(obj, "App::PropertyLength", "MinRadius", "The tightest bend radius found (0 when straight)", readonly=True)
        _add(obj, "App::PropertyInteger", "Bends", "Number of bends", readonly=True)
        _add(obj, "App::PropertyBool", "BendOK", "No bend tighter than MinBendRadius and every corner drawn", True, readonly=True)
        _add(obj, "App::PropertyString", "BendTable", "LRA bend table (Polyline mode): length, rotation, angle per bend", readonly=True)
        _add(obj, "App::PropertyString", "Problems", "Why BendOK is false", readonly=True)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def dumps(self):
        return {"Type": PATH}

    def loads(self, state):
        self.Type = PATH

    def execute(self, obj):
        rt = _rt()
        points = resolve_points(obj)
        if [tuple(p) for p in points] != [tuple(p) for p in obj.Points]:
            obj.Points = points
        pts = rt.clean_points(points)
        mode = obj.Mode
        radius = float(obj.BendRadius.Value)
        minimum = float(obj.MinBendRadius.Value)
        summary = rt.route_summary(pts, mode, radius, minimum)
        wire = path_wire(_to_parent(obj, pts), mode, radius)
        obj.Shape = wire
        tightest = wire_min_radius(wire) if mode == "Spline" else summary["min_radius"]
        problems = list(summary["problems"])
        if mode == "Spline" and minimum > 0 and tightest < minimum - 1e-6:
            problems = [p for p in problems if "tightest" not in p]
            problems.append(f"tightest bend radius {tightest:.1f} is below the minimum {minimum:g}")
        obj.Length = wire.Length
        obj.MinRadius = 0.0 if math.isinf(tightest) else tightest
        obj.Bends = int(summary["bends"])
        obj.BendOK = not problems
        obj.Problems = "; ".join(problems)
        obj.BendTable = rt.bend_table_text(rt.lra_table(pts, radius)) if mode == "Polyline" and len(pts) > 2 else ""


class ViewProviderRoutePath:
    def __init__(self, vobj):
        vobj.Proxy = self
        try:
            vobj.LineColor = (0.95, 0.55, 0.10)
            vobj.LineWidth = 2.0
            vobj.PointColor = (0.95, 0.55, 0.10)
            vobj.PointSize = 6.0
        except Exception:
            pass

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return os.path.join(HERE, "BruceWorx_RoutePath.svg")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def make_path(points, doc=None, mode="Spline", radius=10.0, attachments=None, container=None, name="RoutePath"):
    """A RoutePath through ``points`` (document frame). ``attachments``: ``[(index, obj, sub)]``."""
    _part()
    doc = doc or App.ActiveDocument
    obj = doc.addObject("Part::FeaturePython", name)
    RoutePath(obj)
    obj.Points = [V(p) for p in points]
    obj.Mode = mode
    obj.BendRadius = float(radius)
    set_attachments(obj, attachments or [])
    if App.GuiUp and obj.ViewObject is not None:
        ViewProviderRoutePath(obj.ViewObject)
    _place(obj, container=container)
    return obj


def set_attachments(obj, attachments):
    """``attachments``: ``[(index, obj, sub)]``. ``Attachments`` holds the links (so the path
    recomputes when a part moves; FreeCAD groups a PropertyLinkSubList by object, so the order
    is not kept there) and ``AttachedTo`` the ``index|object|sub`` triples in order."""
    links, text = [], []
    for index, target, sub in attachments:
        if target is None:
            continue
        links.append((target, [sub or ""]))
        text.append(f"{int(index)}|{target.Name}|{sub or ''}")
    obj.Attachments = links
    obj.AttachedTo = text


def attachments_of(obj):
    """``[(index, obj, sub)]`` of a path (``set_attachments`` reversed)."""
    out = []
    doc = obj.Document
    for entry in obj.AttachedTo:
        try:
            index, name, sub = entry.split("|", 2)
        except ValueError:
            continue
        target = doc.getObject(name)
        if target is not None:
            out.append((int(index), target, sub))
    return out


# ---------------------------------------------------------------------------------------------
# Routed items.


class _Routed:
    """Shared properties of wires, pipes, hoses and bundles."""

    Type = ""

    def __init__(self, obj):
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        _add(obj, "App::PropertyLink", "Path", "The RoutePath this follows")
        _add(obj, "App::PropertyLength", "Length", "Length to cut or order, slack included", readonly=True)
        _add(obj, "App::PropertyLength", "MinBendRadius", "Smallest bend radius this item allows (0 = no check)", 0.0)
        _add(obj, "App::PropertyBool", "BendOK", "The path bends no tighter than MinBendRadius", True, readonly=True)
        _add(obj, "App::PropertyString", "QuantityUnit", "Unit the BOM counts this in", "m", group="BruceWorx")
        _add(obj, "App::PropertyFloat", "QuantityPer", "Quantity of the unit per occurrence (metres)", 0.0,
             group="BruceWorx", readonly=True)
        obj.setEditorMode("QuantityUnit", 1)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def dumps(self):
        return {"Type": self.Type}

    def loads(self, state):
        pass

    # ----- helpers -----

    def path_of(self, obj):
        path = obj.Path
        if path is None or not is_path(path):
            raise ValueError(f"{obj.Label}: no route path")
        if not path.Shape.Wires:
            path.recompute()
        return path

    def check(self, obj, path, extra_radius=None):
        """BendOK: the path's own check, then this item's MinBendRadius against the tightest bend."""
        tightest = float(path.MinRadius.Value) if path.MinRadius.Value > 0 else math.inf
        if extra_radius is not None:
            tightest = min(tightest, extra_radius)
        minimum = float(obj.MinBendRadius.Value)
        ok = bool(path.BendOK) and not (minimum > 0 and tightest < minimum - 1e-6)
        obj.BendOK = ok
        return ok

    def finish(self, obj, length_mm):
        obj.Length = float(length_mm)
        obj.QuantityPer = float(length_mm) / 1000.0


class Wire(_Routed):
    Type = WIRE

    def setup(self, obj):
        super().setup(obj)
        rt = _rt()
        _add(obj, "App::PropertyEnumeration", "Gauge", "Conductor size (AWG or mm²)")
        obj.Gauge = rt.GAUGE_NAMES
        _add(obj, "App::PropertyLength", "InsulationOD", "Outer diameter over the insulation", 0.0)
        _add(obj, "App::PropertyColor", "Colour", "Insulation colour, shown in the 3D view", (0.85, 0.1, 0.1, 0.0))
        _add(obj, "App::PropertyString", "Signal", "Net or signal name")
        _add(obj, "App::PropertyString", "FromConnector", "Where the wire starts (connector or part)")
        _add(obj, "App::PropertyString", "FromPin", "Pin or terminal at the start")
        _add(obj, "App::PropertyString", "ToConnector", "Where the wire ends")
        _add(obj, "App::PropertyString", "ToPin", "Pin or terminal at the end")
        _add(obj, "App::PropertyLink", "FromPart", "The part at the start, when it is in the model")
        _add(obj, "App::PropertyLink", "ToPart", "The part at the end, when it is in the model")
        _add(obj, "App::PropertyPercent", "Slack", "Length added for service loops and tolerance", 5)
        _add(obj, "App::PropertyMass", "Mass", "Copper plus an insulation estimate", readonly=True)

    def execute(self, obj):
        rt = _rt()
        path = self.path_of(obj)
        od = float(obj.InsulationOD.Value) or rt.default_insulation_od(obj.Gauge)
        if obj.InsulationOD.Value <= 0:
            obj.InsulationOD = od
        obj.Shape = sweep(path.Shape.Wires[0], od)
        length = float(path.Length.Value) * (1.0 + obj.Slack / 100.0)
        self.finish(obj, length)
        obj.Mass = rt.wire_mass(obj.Gauge, length, od) / 1000.0  # g -> kg
        self.check(obj, path)


class Pipe(_Routed):
    Type = PIPE

    def setup(self, obj):
        super().setup(obj)
        rt = _rt()
        _add(obj, "App::PropertyEnumeration", "Size", "Nominal tube size (sets OD and wall), or Custom")
        obj.Size = rt.TUBE_SIZE_NAMES
        _add(obj, "App::PropertyLength", "OD", "Outer diameter", 12.0)
        _add(obj, "App::PropertyLength", "WallThickness", "Wall thickness", 1.0)
        _add(obj, "App::PropertyString", "Material", "Tube material")
        _add(obj, "App::PropertyLength", "BendRadius", "Centreline bend radius (CLR) the bender uses", 0.0)
        _add(obj, "App::PropertyLength", "CutLength", "Developed length to cut before bending", readonly=True)
        _add(obj, "App::PropertyInteger", "Bends", "Number of bends", readonly=True)
        _add(obj, "App::PropertyString", "BendTable", "LRA table: length fed, rotation, angle per bend", readonly=True)

    def onChanged(self, obj, prop):
        if prop == "Size" and getattr(obj, "Size", "Custom") != "Custom":
            try:
                od, wall = _rt().tube_size(obj.Size)
                if abs(obj.OD.Value - od) > 1e-9:
                    obj.OD = od
                if abs(obj.WallThickness.Value - wall) > 1e-9:
                    obj.WallThickness = wall
            except Exception:
                pass

    def execute(self, obj):
        rt = _rt()
        path = self.path_of(obj)
        od = float(obj.OD.Value)
        wall = float(obj.WallThickness.Value)
        if obj.BendRadius.Value <= 0:
            obj.BendRadius = 2.0 * od
        clr = float(obj.BendRadius.Value)
        obj.Shape = sweep(path.Shape.Wires[0], od, od - 2.0 * wall)
        pts = rt.clean_points(path.Points)
        if path.Mode == "Polyline" and len(pts) > 2:
            route = rt.polyline_with_arcs(pts, clr)
            cut = route.length
            obj.Bends = sum(1 for b in route.bends if b.ok)
            obj.BendTable = rt.bend_table_text(rt.lra_table(pts, clr))
        else:
            cut = float(path.Length.Value)
            obj.Bends = int(path.Bends)
            obj.BendTable = ""
        obj.CutLength = cut
        self.finish(obj, cut)
        self.check(obj, path, extra_radius=clr if path.Mode == "Polyline" and obj.Bends else None)


class Hose(_Routed):
    Type = HOSE

    def setup(self, obj):
        super().setup(obj)
        _add(obj, "App::PropertyLength", "ID", "Bore", 6.0)
        _add(obj, "App::PropertyLength", "OD", "Outer diameter", 10.0)
        _add(obj, "App::PropertyPercent", "Slack", "Length added for movement and fitting", 5)

    def execute(self, obj):
        path = self.path_of(obj)
        od = float(obj.OD.Value)
        if obj.MinBendRadius.Value <= 0:
            obj.MinBendRadius = 6.0 * od
        obj.Shape = sweep(path.Shape.Wires[0], od, float(obj.ID.Value))
        self.finish(obj, float(path.Length.Value) * (1.0 + obj.Slack / 100.0))
        self.check(obj, path)


class Bundle(_Routed):
    Type = BUNDLE

    def setup(self, obj):
        super().setup(obj)
        _add(obj, "App::PropertyLinkList", "Wires", "The wires in this bundle")
        _add(obj, "App::PropertyLength", "OD", "Combined outer diameter (packing factor 1.15)", readonly=True)

    def execute(self, obj):
        rt = _rt()
        path = self.path_of(obj)
        diameters = [float(w.InsulationOD.Value) for w in obj.Wires if kind_of(w) == WIRE]
        od = rt.bundle_od(diameters) or 1.0
        obj.OD = od
        obj.Shape = sweep(path.Shape.Wires[0], od)
        self.finish(obj, float(path.Length.Value))
        self.check(obj, path)


class ViewProviderRouted:
    """Colour from the wire's Colour property; a translucent bundle; the kind's icon."""

    ICONS = {WIRE: "BruceWorx_RouteWire.svg", PIPE: "BruceWorx_RoutePipe.svg", HOSE: "BruceWorx_RouteHose.svg",
             BUNDLE: "BruceWorx_RouteBundle.svg"}
    COLOURS = {PIPE: (0.75, 0.76, 0.78), HOSE: (0.15, 0.15, 0.17), BUNDLE: (0.3, 0.3, 0.32)}

    def __init__(self, vobj):
        vobj.Proxy = self
        self.kind = kind_of(vobj.Object)
        self.apply(vobj)

    def attach(self, vobj):
        self.Object = vobj.Object
        self.kind = kind_of(vobj.Object)

    def apply(self, vobj):
        obj = vobj.Object
        kind = kind_of(obj)
        try:
            if kind == WIRE:
                colour = tuple(obj.Colour)[:3]
            else:
                colour = self.COLOURS.get(kind, (0.6, 0.6, 0.6))
            material = App.Material()
            material.DiffuseColor = colour
            if kind == BUNDLE:
                material.Transparency = 0.6
                vobj.Transparency = 60
            vobj.ShapeAppearance = [material]
        except Exception:
            pass

    def updateData(self, obj, prop):
        if prop in ("Colour", "Shape") and getattr(obj, "ViewObject", None) is not None:
            self.apply(obj.ViewObject)

    def getIcon(self):
        return os.path.join(HERE, self.ICONS.get(getattr(self, "kind", ""), "BruceWorx_RouteWire.svg"))

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def _make_item(proxy_class, path, name, **values):
    _part()
    doc = path.Document
    obj = doc.addObject("Part::FeaturePython", name)
    proxy_class(obj)
    obj.Path = path
    for key, value in values.items():
        if value is not None:
            setattr(obj, key, value)
    if App.GuiUp and obj.ViewObject is not None:
        ViewProviderRouted(obj.ViewObject)
    _place(obj, like=path)
    return obj


def make_wire(path, gauge="22 AWG", insulation_od=0.0, colour=None, signal="", from_connector="", from_pin="",
              to_connector="", to_pin="", slack=5, name="Wire"):
    obj = _make_item(Wire, path, name, Gauge=gauge, InsulationOD=float(insulation_od), Signal=signal,
                     FromConnector=from_connector, FromPin=from_pin, ToConnector=to_connector, ToPin=to_pin,
                     Slack=int(slack))
    if colour is not None:
        rgb = tuple(float(c) for c in colour)
        obj.Colour = rgb + (0.0,) * (4 - len(rgb))
    return obj


def make_pipe(path, size="Custom", od=None, wall=None, material="", bend_radius=0.0, min_bend_radius=None, name="Pipe"):
    obj = _make_item(Pipe, path, name, Material=material)
    if size != "Custom":
        obj.Size = size
    if od is not None:
        obj.OD = float(od)
    if wall is not None:
        obj.WallThickness = float(wall)
    obj.BendRadius = float(bend_radius) if bend_radius else 2.0 * float(obj.OD.Value)
    obj.MinBendRadius = float(min_bend_radius) if min_bend_radius is not None else 1.5 * float(obj.OD.Value)
    sync_clr(obj)
    return obj


def sync_clr(pipe):
    """The path's arcs follow the pipe's CLR (Polyline mode): one bend radius for the geometry, the
    cut length and the bend table. Returns True when the path changed."""
    path = pipe.Path
    if not is_path(path) or path.Mode != "Polyline" or pipe.BendRadius.Value <= 0:
        return False
    if abs(float(path.BendRadius.Value) - float(pipe.BendRadius.Value)) <= 1e-9:
        return False
    path.BendRadius = float(pipe.BendRadius.Value)
    return True


def make_hose(path, inner=6.0, od=10.0, min_bend_radius=None, slack=5, name="Hose"):
    obj = _make_item(Hose, path, name, ID=float(inner), OD=float(od), Slack=int(slack))
    obj.MinBendRadius = float(min_bend_radius) if min_bend_radius is not None else 6.0 * float(od)
    return obj


def make_bundle(wires, path=None, name="Bundle"):
    wires = [w for w in wires if kind_of(w) == WIRE]
    if not wires:
        raise ValueError("a bundle needs wires")
    path = path or wires[0].Path
    return _make_item(Bundle, path, name, Wires=wires)


# ---------------------------------------------------------------------------------------------
# Reports and badges.


def _colour_name(colour):
    r, g, b = [round(c * 255) for c in tuple(colour)[:3]]
    names = {(217, 26, 26): "red", (0, 0, 0): "black", (255, 255, 255): "white", (255, 0, 0): "red",
             (0, 0, 255): "blue", (0, 128, 0): "green", (255, 255, 0): "yellow"}
    return names.get((r, g, b), f"#{r:02x}{g:02x}{b:02x}")


def _end_name(obj, connector, part):
    if connector:
        return connector
    return part.Label if part is not None else ""


def from_to_rows(doc):
    """From-to list rows of every wire in ``doc`` (tree order)."""
    rows = []
    for obj in routed(doc, (WIRE,)):
        rows.append({"wire": obj.Label, "signal": obj.Signal, "gauge": obj.Gauge, "colour": _colour_name(obj.Colour),
                     "from": _end_name(obj, obj.FromConnector, obj.FromPart), "from_pin": obj.FromPin,
                     "to": _end_name(obj, obj.ToConnector, obj.ToPart), "to_pin": obj.ToPin,
                     "length_m": float(obj.Length.Value) / 1000.0})
    return rows


def cut_list_rows(doc):
    """Cut list rows of every pipe and hose in ``doc``."""
    rt = _rt()
    rows = []
    for obj in routed(doc, (PIPE, HOSE)):
        kind = "Pipe" if kind_of(obj) == PIPE else "Hose"
        if kind == "Pipe":
            size = obj.Size if obj.Size != "Custom" else f"{obj.OD.Value:g} x {obj.WallThickness.Value:g}"
            radius = float(obj.BendRadius.Value)
            pts = rt.clean_points(obj.Path.Points) if obj.Path is not None else []
            table = rt.lra_table(pts, radius) if obj.Path is not None and obj.Path.Mode == "Polyline" and len(pts) > 2 else []
            rows.append({"item": obj.Label, "kind": kind, "size": size, "cut_length": float(obj.CutLength.Value),
                         "radius": radius, "bends": int(obj.Bends), "table": table})
        else:
            rows.append({"item": obj.Label, "kind": kind, "size": f"{obj.ID.Value:g} / {obj.OD.Value:g}",
                         "cut_length": float(obj.Length.Value), "radius": float(obj.Path.MinRadius.Value) if obj.Path else 0.0,
                         "bends": int(obj.Path.Bends) if obj.Path else 0, "table": []})
    return rows


def from_to_csv(doc):
    return _rt().from_to_csv(from_to_rows(doc))


def cut_list_csv(doc):
    return _rt().cut_list_csv(cut_list_rows(doc))


def write_text(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def row_badges(obj, meta, marks):
    """bruceworx_browser.ROW_EXTRAS: a warning badge on a path or routed item that bends too tight."""
    try:
        if (is_path(obj) or is_routed(obj)) and "BendOK" in obj.PropertiesList and not obj.BendOK:
            return meta, marks + [("bend too tight", "warn")]
    except Exception:
        pass
    return meta, marks
