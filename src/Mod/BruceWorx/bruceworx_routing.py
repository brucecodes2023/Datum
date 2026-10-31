# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing objects: route paths, wires, pipes, hoses and bundles (BW-ROU-01..04, 27 Oct 2026).

Every object is a ``Part::FeaturePython`` with a proxy from this module; the maths (splines,
tangent arcs, the LRA bend table, gauges, tube sizes, the CSV reports) is FreeCAD-free in
``bruceworx/routing.py``. The GUI (commands, task panels, the Routing tab) is
``bruceworx_routing_gui.py``.

* **RoutePath** is the centreline: ``Points`` in the document frame, optionally attached to
  geometry (``Attachments`` + ``AttachedTo``: a vertex, a circular edge's centre, a face's
  centre or an object's origin, re-read on every recompute so a routed point follows its part).
  ``Mode`` Spline (a cubic through the points, C2, not-a-knot ends, chord-length parameters:
  ``routing.Spline``, drawn as that same curve in B-spline form, so the shape and the bend check
  are one curve) or Polyline (straight segments joined by tangent arcs of ``BendRadius``). Read-only results: ``Length``, ``MinRadius`` (the
  tightest bend found, 0 for straight), ``Bends``, ``BendOK`` (against ``MinBendRadius``),
  ``BendTable`` (LRA rows in Polyline mode) and ``Problems``.
* **Wire**, **Pipe**, **Hose** sweep a section along their path (``Path``) and carry what the BOM
  and the reports need: ``Length`` in mm, ``QuantityUnit`` ``"m"`` and ``QuantityPer`` (metres per
  occurrence, read by the vault's BOM walker, ROU-04), ``BendOK`` against their own
  ``MinBendRadius``. A wire adds its gauge, insulation, colour, signal, from/to and mass; a pipe
  its size, wall, CLR, cut length and bend table; a hose its ID/OD and slack.
* **Bundle** groups wires on one path with a combined OD (sqrt of the summed squares times a
  packing factor of 1.15), drawn translucent.

**Bend rules** (ROU-02, 30 Oct). Every item's ``MinBendRadius`` comes from its ``BendRule``:
Default (the preference group ``Mod/BruceWorx/Routing``, set in the Bend Rules dialog), a preset
by wire or hose type (Hook-up PVC 4 x OD, Silicone 3 x OD, Coax 10 x OD, ...), Multiple of OD
(``BendFactor``), Fixed radius (``MinBendRadius`` as typed) or No check. Setting ``MinBendRadius``
by hand makes it a fixed override. A bundle takes the strictest of its wires' rules (From wires)
unless set. ``BendRuleUsed`` says which rule gave the radius. Items saved before 30 Oct are
migrated on load (``migrate``).

The Assembly BOM (C++, ROU-04) skips a RoutePath (``BomExclude``) and its Length column prints a
routed item's ``Quantity x QuantityPer`` with ``QuantityUnit`` ("0.42 m").

A badge "bend too tight" shows in the Browser on any routed object whose ``BendOK`` is False
(``row_badges``, registered by the GUI module).

**Connectors and fittings** (ROU-05, 30 Oct). A path whose first or last point is attached to a
pin or port frame (``bruceworx_routing_ports``: an LCS with ``BWFrame``) *leaves along the frame's
+Z* for a straight length before its first bend (``LeaveStraight``; the frame's
``StraightLength``, else the preferences: wires 2 x OD past the terminal, hoses 2 x OD, tubes
2 x OD or the bender's clamp length): a line then a spline clamped to +Z, or a polyline corner
placed so its arc starts that far out (``routing.build_route``); ``StartStraight`` /
``EndStraight`` say what is in force and the bend check covers it. ``RoutePoints`` keeps the
vertices with the lead ends (a pipe's bend table is read from them). A wire on such a path fills
``FromConnector`` / ``FromPin`` / ``ToConnector`` / ``ToPin`` (and ``Signal`` from the pin's
``DefaultSignal``) from the connector's label and the pin's name, as long as they are empty or
still what was filled in last (``AutoFromTo``), so a typed value stays; ``Warnings`` names a gauge
outside the pin's range; ``BomExtras`` lists one terminal (and seal) per attached end from the
pin's ``TerminalPN`` / ``TerminalMfr`` / ``TerminalItem`` (the seal's likewise): ordered by
manufacturer and MPN, keyed by the Standard Library item when there is one; the vault BOM walker
counts them. A port's ``Engagement``
(tube inside the fitting) is added to a pipe's cut length and bend table and to a hose's length.
The from-to list names each end's mating connector when both halves are in the document.
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
PREFS_PATH = "User parameter:BaseApp/Preferences/Mod/BruceWorx/Routing"


def _rt():
    from bruceworx import routing

    return routing


def _part():
    import Part

    return Part


def _bp():
    import bruceworx_routing_ports

    return bruceworx_routing_ports


def _rope():
    import bruceworx_rope

    return bruceworx_rope


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


def prefs():
    """The routing preferences (bend rule defaults) over their shipped defaults."""
    rt = _rt()
    out = dict(rt.PREF_DEFAULTS)
    try:
        group = App.ParamGet(PREFS_PATH)
        for key, default in rt.PREF_DEFAULTS.items():
            out[key] = group.GetString(key, default) if isinstance(default, str) else group.GetFloat(key, default)
    except Exception:
        pass
    return rt.prefs_with_defaults(out)


def set_prefs(values):
    """Write ``values`` (keys of PREF_DEFAULTS) to the preference group."""
    rt = _rt()
    group = App.ParamGet(PREFS_PATH)
    for key, value in values.items():
        if key not in rt.PREF_DEFAULTS:
            continue
        if isinstance(rt.PREF_DEFAULTS[key], str):
            group.SetString(key, str(value))
        else:
            group.SetFloat(key, float(value))


def reset_prefs():
    """Back to the shipped defaults."""
    group = App.ParamGet(PREFS_PATH)
    for key, default in _rt().PREF_DEFAULTS.items():
        if isinstance(default, str):
            group.RemString(key)
        else:
            group.RemFloat(key)


def _restoring(obj):
    try:
        return "Restore" in obj.State or bool(getattr(obj.Document, "Restoring", False))
    except Exception:
        return False


def _set_enum(obj, name, choices):
    """``choices`` on an enumeration, keeping its value when it is still one of them."""
    current = getattr(obj, name, "")
    if list(obj.getEnumerationsOfProperty(name) or []) != list(choices):
        obj.__setattr__(name, list(choices))
        if current in choices:
            obj.__setattr__(name, current)


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
        found = _bp().frame_at(obj, sub)  # ROU-05: a pin or port frame (or one of its axes)
        if found is not None:
            return App.Vector(found[1].Base)
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


def path_wire(points, mode="Spline", radius=0.0, start=None, end=None):
    """A Part wire through ``points`` (document frame): a B-spline, or lines and tangent arcs;
    with leads (``routing.Lead``, ROU-05) the straights at the ends."""
    rt = _rt()
    pts = rt.clean_points(points)
    if len(pts) < 2:
        raise ValueError("a route needs at least two distinct points")
    return route_wire(rt.build_route(pts, mode, radius, start, end))


def route_wire(route):
    """The Part wire of a ``routing.Route``: lines, arcs and the spline (exactly the module's, as
    a clamped cubic B-spline)."""
    Part = _part()
    if route.mode != "Polyline" and len(route.pieces) > 1:
        poles, knots, mults = route.bspline()  # leads and spline as one edge (ROU-05)
        curve = Part.BSplineCurve()
        curve.buildFromPolesMultsKnots([V(p) for p in poles], mults, knots, False, 3)
        return Part.Wire([curve.toShape()])
    edges = []
    for piece in route.pieces:
        if piece[0] == "line":
            edges.append(Part.LineSegment(V(piece[1]), V(piece[2])).toShape())
        elif piece[0] == "arc":
            edges.append(Part.Arc(V(piece[1]), V(piece[2]), V(piece[3])).toShape())
        else:
            spline = piece[1]
            if len(spline.points) == 2 and not spline.clamped:
                edges.append(Part.LineSegment(V(spline.points[0]), V(spline.points[1])).toShape())
                continue
            poles, knots, mults = spline.bspline()
            curve = Part.BSplineCurve()
            curve.buildFromPolesMultsKnots([V(p) for p in poles], mults, knots, False, 3)
            edges.append(curve.toShape())
    if not edges:
        raise ValueError("a route needs at least two distinct points")
    return Part.Wire(edges)


def wire_min_radius(wire, samples=_SAMPLES):
    """The tightest radius of curvature along the wire's edges (inf when straight), measured on
    the shape (the tests compare it with the module's figure for the same curve)."""
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


def _parent_inverse(obj):
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:
        parent = None
    return parent.getGlobalPlacement().inverse() if parent is not None else None


def _lead_to_parent(obj, lead):
    """A lead moved into ``obj``'s container frame, like ``_to_parent`` does the points."""
    inverse = _parent_inverse(obj)
    if lead is None or inverse is None:
        return lead
    rt = _rt()
    return rt.Lead(tuple(inverse.multVec(V(lead.origin))), tuple(inverse.Rotation.multVec(V(lead.direction))),
                   lead.length, lead.name)


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
        _add(obj, "App::PropertyBool", "BomExclude", "Not a part: the Assembly BOM leaves it out (ROU-04)", True,
             group="BruceWorx", hidden=True)
        # ROU-05: leaving pins and ports straight.
        _add(obj, "App::PropertyBool", "LeaveStraight",
             "An end on a pin or port leaves along its +Z for the straight length before the first bend", True)
        _add(obj, "App::PropertyLength", "StartStraight", "Straight at the first point (0: not on a pin or port)", readonly=True)
        _add(obj, "App::PropertyLength", "EndStraight", "Straight at the last point (0: not on a pin or port)", readonly=True)
        _add(obj, "App::PropertyString", "StartFrame", "The pin or port the first point is on", readonly=True)
        _add(obj, "App::PropertyString", "EndFrame", "The pin or port the last point is on", readonly=True)
        _add(obj, "App::PropertyVectorList", "RoutePoints",
             "The route's vertices in the document frame, lead ends included (a pipe's bend table reads them)", hidden=True)

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
        start, end = path_leads(obj)
        summary = rt.route_summary(pts, mode, radius, minimum, start, end)
        local = rt.build_route(_to_parent(obj, pts), mode, radius, _lead_to_parent(obj, start), _lead_to_parent(obj, end))
        wire = route_wire(local)
        obj.Shape = wire
        obj.RoutePoints = [V(p) for p in summary["points"]]
        obj.StartStraight = float(start.length) if start is not None else 0.0
        obj.EndStraight = float(end.length) if end is not None else 0.0
        obj.StartFrame = start.name if start is not None else ""
        obj.EndFrame = end.name if end is not None else ""
        tightest = summary["min_radius"]  # the drawn curve: the shape is the module's spline (or arcs)
        problems = list(summary["problems"])
        obj.Length = wire.Length
        obj.MinRadius = 0.0 if math.isinf(tightest) else tightest
        obj.Bends = int(summary["bends"])
        obj.BendOK = not problems
        obj.Problems = "; ".join(problems)
        corners = summary["points"]
        obj.BendTable = rt.bend_table_text(rt.lra_table(corners, radius)) if mode == "Polyline" and len(corners) > 2 else ""


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
        target, sub = rebase(obj, target, sub)
        links.append((target, [sub or ""]))
        text.append(f"{int(index)}|{target.Name}|{sub or ''}")
    obj.Attachments = links
    obj.AttachedTo = text


def _ancestors(obj):
    out, seen = [], set()
    current = _container_of(obj)
    while current is not None and current.Name not in seen:
        out.append(current)
        seen.add(current.Name)
        current = _container_of(current)
    return out


def rebase(path, target, sub):
    """``(target, sub)`` re-rooted below the path's own containers: a pick in the 3D view comes as
    (root object, "Child.Grandchild.Element"), and a path inside that root (an assembly) linking to
    the root itself would be a dependency cycle. The point stays the same."""
    ancestors = {a.Name for a in _ancestors(path)}
    names = (sub or "").split(".")
    while target is not None and target.Name in ancestors and len(names) > 1 and names[0]:
        try:
            child = target.getSubObjectList(names[0] + ".")[-1]
        except Exception:
            break
        if child is None or child is target:
            break
        target, names = child, names[1:]
    return target, ".".join(names)


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
# Ends on pins and ports (ROU-05).


def path_ends(path):
    """``{"start": (frame, placement, connector), "end": ...}`` for the path's first and last
    points that are attached to a pin or port frame."""
    bp = _bp()
    n = len(path.Points)
    out = {}
    if n < 2:
        return out
    for index, target, sub in attachments_of(path):
        if index not in (0, n - 1):
            continue
        found = bp.frame_at(target, sub)
        if found is not None:
            out["start" if index == 0 else "end"] = found
    return out


def frame_text(frame, connector):
    """"Pin 1 of J1"."""
    bp = _bp()
    what = f"{bp.frame_kind(frame)} {bp.frame_name(frame)}"
    return f"{what} of {connector.Label}" if connector is not None else what


def items_on(path):
    """The wires, pipes, hoses and bundles on ``path``."""
    return [o for o in path.InList if is_routed(o) and getattr(o, "Path", None) == path]


def lead_length(path, frame):
    """The straight ``frame`` asks of ``path``: its own ``StraightLength``, else the preferences
    on the largest OD among the items on the path (a pin: wires and bundles, by the wire rule; a
    port: pipes by the tube rule, hoses by the hose rule). With no item yet, a pin uses the
    thickest wire it takes (22 AWG if not stated) and a port its ``TubeOD``."""
    rt = _rt()
    bp = _bp()
    override = float(frame.StraightLength.Value) if "StraightLength" in frame.PropertiesList else 0.0
    if override > 0:
        return override
    p = prefs()
    lengths = []
    for item in items_on(path):
        kind = kind_of(item)
        if bp.is_pin(frame) and kind in (WIRE, BUNDLE):
            lengths.append(rt.straight_length("wire", item.Proxy.od(item), 0.0, p))
        elif bp.is_port(frame) and kind == PIPE:
            lengths.append(rt.straight_length("tube", item.Proxy.od(item), 0.0, p))
        elif bp.is_port(frame) and kind == HOSE:
            lengths.append(rt.straight_length("hose", item.Proxy.od(item), 0.0, p))
    if lengths:
        return max(lengths)
    if bp.is_pin(frame):
        gauge = f"{int(frame.GaugeMax)} AWG" if frame.GaugeMax else "22 AWG"
        try:
            od = rt.default_insulation_od(gauge)
        except ValueError:
            od = rt.default_insulation_od("22 AWG")
        return rt.straight_length("wire", od, 0.0, p)
    od = float(frame.TubeOD.Value) if "TubeOD" in frame.PropertiesList else 0.0
    return rt.straight_length("tube", od, 0.0, p) if od > 0 else 0.0


def path_leads(path):
    """``(start, end)``: a ``routing.Lead`` for each end on a pin or port (document frame), else
    None; none at all when ``LeaveStraight`` is off."""
    if "LeaveStraight" in path.PropertiesList and not path.LeaveStraight:
        return None, None
    rt = _rt()
    leads = []
    ends = path_ends(path)
    for key in ("start", "end"):
        found = ends.get(key)
        if found is None:
            leads.append(None)
            continue
        frame, placement, connector = found
        z = placement.Rotation.multVec(App.Vector(0, 0, 1))
        leads.append(rt.Lead(tuple(placement.Base), (z.x, z.y, z.z), lead_length(path, frame),
                             frame_text(frame, connector)))
    return leads[0], leads[1]


def route_points(path):
    """The vertices a pipe on ``path`` is bent through: ``RoutePoints`` (lead ends included), or
    the points of a path saved before ROU-05."""
    rt = _rt()
    if "RoutePoints" in path.PropertiesList and len(path.RoutePoints) >= 2:
        return rt.clean_points(path.RoutePoints)
    return rt.clean_points(path.Points)


def engagements(path):
    """``(start, end)``: the tube inside a fitting at each end on a port (its ``Engagement``)."""
    bp = _bp()
    ends = path_ends(path)
    out = []
    for key in ("start", "end"):
        found = ends.get(key)
        frame = found[0] if found is not None else None
        out.append(float(frame.Engagement.Value) if bp.is_port(frame) and "Engagement" in frame.PropertiesList else 0.0)
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# Routed items.


class _Routed:
    """Shared properties of wires, pipes, hoses and bundles."""

    Type = ""
    RULE_KIND = ""  # "wire", "hose", "tube" or "bundle": which bend rules apply
    OLD_FACTOR = 0.0  # the multiple of OD the 27 Oct code set by default (migration)

    def __init__(self, obj):
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        rt = _rt()
        self._quiet = True
        try:
            _add(obj, "App::PropertyLink", "Path", "The RoutePath this follows")
            _add(obj, "App::PropertyLength", "Length", "Length to cut or order, slack included", readonly=True)
            new_rule = "BendRule" not in obj.PropertiesList
            _add(obj, "App::PropertyEnumeration", "BendRule",
                 "Where MinBendRadius comes from: Default (the preferences), a preset, a multiple of OD, a fixed radius or no check")
            choices = rt.rule_choices(self.RULE_KIND)
            if new_rule:
                obj.BendRule = choices
            else:
                _set_enum(obj, "BendRule", choices)
            factor = prefs().get(self.RULE_KIND.capitalize() + "Factor", 4.0)
            _add(obj, "App::PropertyFloat", "BendFactor", "Minimum bend radius as a multiple of OD (rule Multiple of OD)", factor)
            _add(obj, "App::PropertyLength", "MinBendRadius",
                 "Smallest bend radius this item allows (0 = no check); typing a value makes it a fixed override", 0.0)
            _add(obj, "App::PropertyString", "BendRuleUsed", "The rule that gave MinBendRadius", readonly=True)
            _add(obj, "App::PropertyBool", "BendOK", "The path bends no tighter than MinBendRadius", True, readonly=True)
            _add(obj, "App::PropertyString", "QuantityUnit", "Unit the BOM counts this in", "m", group="BruceWorx")
            _add(obj, "App::PropertyFloat", "QuantityPer", "Quantity of the unit per occurrence (metres)", 0.0,
                 group="BruceWorx", readonly=True)
            obj.setEditorMode("QuantityUnit", 1)
        finally:
            self._quiet = False

    def rope_setup(self, obj, insulation=False):
        """ROU-08: the properties of a flexible item (wire, hose, bundle)."""
        _add(obj, "App::PropertyLength", "CutLength",
             "Length to cut, any value (it need not follow the route: longer sags, shorter pulls taut); nothing stretches. "
             "Slack edits it", 0.0)
        _add(obj, "App::PropertyFloat", "StiffnessFactor",
             "Calibration of the bending stiffness from gauge and insulation (1 = the table; raise it for a stiffer wire)", 1.0)
        _add(obj, "App::PropertyBool", "Taut", "The cut length is too short for the ends, or the bend is tighter than allowed",
             False, readonly=True)
        _add(obj, "App::PropertyString", "Problems", "Why the item is taut or bent too tight", readonly=True)
        _add(obj, "App::PropertyString", "RopeStatus", "taut, or how far it sags below the straight line", readonly=True)
        _add(obj, "App::PropertyVectorList", "RopePoints", "The rope's points (document frame): the state a recompute starts from",
             hidden=True)
        _add(obj, "App::PropertyIntegerList", "ClipNodes", "Rope points that are clips", hidden=True)
        _add(obj, "App::PropertyInteger", "SlackApplied", "Slack the cut length includes", hidden=True)
        if insulation:
            new = "Insulation" not in obj.PropertiesList
            _add(obj, "App::PropertyEnumeration", "Insulation",
                 "Insulation material for the stiffness table (Auto: from the bend rule preset)")
            if new:
                obj.Insulation = ["Auto", "PVC", "Silicone"]

    def on_slack(self, obj):
        """Slack edits the cut length (W2): the length it includes is rescaled."""
        cut = float(obj.CutLength.Value) if "CutLength" in obj.PropertiesList else 0.0
        if cut <= 0:
            return
        old, new = 1.0 + int(obj.SlackApplied) / 100.0, 1.0 + int(obj.Slack) / 100.0
        if int(obj.SlackApplied) == int(obj.Slack):
            return
        self._quiet = True
        try:
            obj.CutLength = cut * new / old
            obj.SlackApplied = int(obj.Slack)
        finally:
            self._quiet = False

    def onDocumentRestored(self, obj):
        old = "BendRule" not in obj.PropertiesList
        self.setup(obj)
        if old:
            self._quiet = True
            try:
                self.migrate(obj)
            finally:
                self._quiet = False

    def migrate(self, obj):
        """An item saved before bend rules: its MinBendRadius kept (see rt.migrate_rule)."""
        rt = _rt()
        choice = rt.migrate_rule(float(obj.MinBendRadius.Value), self.od(obj), self.OLD_FACTOR)
        if choice == rt.RULE_OD:
            obj.BendFactor = self.OLD_FACTOR
        obj.BendRule = choice

    def onChanged(self, obj, prop):
        """Typing a MinBendRadius makes it a fixed override (0: no check); typing a BendFactor
        switches to Multiple of OD. Not while loading or while execute sets them."""
        if prop == "Slack" and not getattr(self, "_quiet", False) and not _restoring(obj):
            self.on_slack(obj)
            return
        if prop in ("OD", "InsulationOD", "Gauge", "Path") and not getattr(self, "_quiet", False) and not _restoring(obj):
            path = getattr(obj, "Path", None)  # ROU-05: a lead's straight follows the item's OD
            if path is not None and is_path(path) and path_ends(path):
                path.touch()
            return
        if prop not in ("MinBendRadius", "BendFactor") or getattr(self, "_quiet", False) or _restoring(obj):
            return
        if "BendRule" not in obj.PropertiesList:
            return
        rt = _rt()
        if prop == "MinBendRadius":
            want = rt.RULE_FIXED if obj.MinBendRadius.Value > 0 else rt.RULE_NONE
        else:
            want = rt.RULE_OD
        if obj.BendRule != want:
            self._quiet = True
            try:
                obj.BendRule = want
            finally:
                self._quiet = False

    def od(self, obj):
        """The diameter a multiple-of-OD rule is taken of."""
        return float(obj.OD.Value) if "OD" in obj.PropertiesList else 0.0

    def rule(self, obj):
        """(rule, label) this item's BendRule gives."""
        return _rt().item_rule(self.RULE_KIND, obj.BendRule, obj.BendFactor, float(obj.MinBendRadius.Value), prefs())

    def apply_rule(self, obj, od=None):
        """MinBendRadius and BendRuleUsed from the rule; returns the radius (0 = no check)."""
        rule, label = self.rule(obj)
        radius = rule.radius(self.od(obj) if od is None else od)
        self._set_min(obj, radius)
        obj.BendRuleUsed = label
        return radius

    def _set_min(self, obj, radius):
        if abs(float(obj.MinBendRadius.Value) - radius) > 1e-9:
            self._quiet = True
            try:
                obj.MinBendRadius = radius
            finally:
                self._quiet = False

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
    RULE_KIND = "wire"

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
        # ROU-05
        _add(obj, "App::PropertyStringList", "AutoFromTo",
             "From, from pin, to, to pin and signal as last filled in from the pins (a value typed over them stays)",
             hidden=True)
        _add(obj, "App::PropertyString", "Warnings", "A wire end the pin does not take (gauge range)", readonly=True)
        _add(obj, "App::PropertyLength", "FromStrip", "Wire inside the connector at the start (its pin's StripLength)",
             readonly=True)
        _add(obj, "App::PropertyLength", "ToStrip", "Wire inside the connector at the end (its pin's StripLength)",
             readonly=True)
        _add(obj, "App::PropertyStringList", "BomExtras",
             "Counted BOM lines this item brings: P/N|quantity|kind (a terminal and a seal per end on a pin)",
             group="BruceWorx", readonly=True)
        self.rope_setup(obj, insulation=True)

    def od(self, obj):
        od = float(obj.InsulationOD.Value)
        return od if od > 0 else _rt().default_insulation_od(obj.Gauge)

    def migrate(self, obj):
        """Before 30 Oct a wire had no default: a radius set by hand stays as a fixed override,
        none takes the Default rule (Hook-up PVC, 4 x OD unless the preferences say otherwise)."""
        obj.BendRule = _rt().RULE_FIXED if obj.MinBendRadius.Value > 0 else _rt().RULE_DEFAULT

    def execute(self, obj):
        rt = _rt()
        path = self.path_of(obj)
        od = self.od(obj)
        if obj.InsulationOD.Value <= 0:
            self._quiet = True
            try:
                obj.InsulationOD = od
            finally:
                self._quiet = False
        self.apply_rule(obj, od)
        strips = self.fill_ends(obj, path)
        if _rope().rope_execute(self, obj):  # ROU-08: a rope of its cut length
            length = float(obj.Length.Value)
        else:
            obj.Shape = sweep(path.Shape.Wires[0], od)
            length = float(path.Length.Value) * (1.0 + obj.Slack / 100.0) + strips  # ROU-05: wire inside the connectors
            self.finish(obj, length)
            self.check(obj, path)
        obj.Mass = rt.wire_mass(obj.Gauge, length, od) / 1000.0  # g -> kg

    def fill_ends(self, obj, path):
        """ROU-05: from/to and signal from the pins the path's ends are on, the gauge check and the
        terminals and the strip lengths (returned summed). A field is filled while it is empty or still
        holds what was filled last."""
        rt = _rt()
        ends = path_ends(path)
        values = ["", "", "", "", ""]
        warnings, extras = [], {}
        strip = [0.0, 0.0]
        for k, key in enumerate(("start", "end")):
            found = ends.get(key)
            if found is None or not _bp().is_pin(found[0]):
                continue
            frame, _placement, connector = found
            if "StripLength" in frame.PropertiesList:
                strip[k] = float(frame.StripLength.Value)
            values[2 * k] = connector.Label if connector is not None else ""
            values[2 * k + 1] = str(frame.PinName)
            if not values[4] and frame.DefaultSignal:
                values[4] = str(frame.DefaultSignal)
            problem = rt.gauge_fits(obj.Gauge, frame.GaugeMin, frame.GaugeMax)
            if problem:
                warnings.append(f"{frame_text(frame, connector)}: {problem}")
            for what in ("Terminal", "Seal"):
                mpn = str(getattr(frame, what + "PN", "") or "").strip()
                mfr = str(getattr(frame, what + "Mfr", "") or "").strip()
                item = str(getattr(frame, what + "Item", "") or "").strip()
                if mpn or item:
                    key = (item or mpn, what, mfr, mpn)
                    extras[key] = extras.get(key, 0) + 1
        last = list(obj.AutoFromTo) if len(obj.AutoFromTo) == 5 else ["", "", "", "", ""]
        for k, name in enumerate(("FromConnector", "FromPin", "ToConnector", "ToPin", "Signal")):
            current = getattr(obj, name)
            if current in ("", last[k]) and current != values[k]:
                setattr(obj, name, values[k])
        if list(obj.AutoFromTo) != values:
            obj.AutoFromTo = values
        text = "; ".join(warnings)
        if obj.Warnings != text:
            obj.Warnings = text
        rows = [f"{key}|{n}|{what}|{mfr}|{mpn}" for (key, what, mfr, mpn), n in extras.items()]
        if list(obj.BomExtras) != rows:
            obj.BomExtras = rows
        if "FromStrip" in obj.PropertiesList:
            if abs(obj.FromStrip.Value - strip[0]) > 1e-12:
                obj.FromStrip = strip[0]
            if abs(obj.ToStrip.Value - strip[1]) > 1e-12:
                obj.ToStrip = strip[1]
        return strip[0] + strip[1]


class Pipe(_Routed):
    Type = PIPE
    RULE_KIND = "tube"
    OLD_FACTOR = 1.5

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
        super().onChanged(obj, prop)
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
            obj.BendRadius = prefs()["TubeClrFactor"] * od
        clr = float(obj.BendRadius.Value)
        self.apply_rule(obj, od)
        obj.Shape = sweep(path.Shape.Wires[0], od, od - 2.0 * wall)
        pts = route_points(path)
        inside = sum(engagements(path))  # ROU-05: tube inside the fittings at the ends
        if path.Mode == "Polyline" and len(pts) > 2:
            route = rt.polyline_with_arcs(pts, clr)
            cut = route.length + inside
            obj.Bends = sum(1 for b in route.bends if b.ok)
            obj.BendTable = rt.bend_table_text(pipe_table(obj))
        else:
            cut = float(path.Length.Value) + inside
            obj.Bends = int(path.Bends)
            obj.BendTable = ""
        obj.CutLength = cut
        self.finish(obj, cut)
        self.check(obj, path, extra_radius=clr if path.Mode == "Polyline" and obj.Bends else None)


class Hose(_Routed):
    Type = HOSE
    RULE_KIND = "hose"
    OLD_FACTOR = 6.0

    def setup(self, obj):
        super().setup(obj)
        _add(obj, "App::PropertyLength", "ID", "Bore", 6.0)
        _add(obj, "App::PropertyLength", "OD", "Outer diameter", 10.0)
        _add(obj, "App::PropertyPercent", "Slack", "Length added for movement and fitting", 5)
        self.rope_setup(obj)

    def execute(self, obj):
        path = self.path_of(obj)
        od = float(obj.OD.Value)
        self.apply_rule(obj, od)
        if _rope().rope_execute(self, obj):  # ROU-08
            return
        obj.Shape = sweep(path.Shape.Wires[0], od, float(obj.ID.Value))
        inside = sum(engagements(path))  # ROU-05: hose over the barbs at the ends
        self.finish(obj, (float(path.Length.Value) + inside) * (1.0 + obj.Slack / 100.0))
        self.check(obj, path)


class Bundle(_Routed):
    Type = BUNDLE
    RULE_KIND = "bundle"

    def setup(self, obj):
        super().setup(obj)
        _add(obj, "App::PropertyLinkList", "Wires", "The wires in this bundle")
        _add(obj, "App::PropertyLength", "OD", "Combined outer diameter (packing factor 1.15)", readonly=True)
        self.rope_setup(obj)

    def migrate(self, obj):
        rt = _rt()
        obj.BendRule = rt.RULE_FIXED if obj.MinBendRadius.Value > 0 else rt.RULE_FROM_WIRES

    def rule(self, obj):
        """From wires: the strictest of the wires' rules on the bundle's OD; else its own."""
        rt = _rt()
        if obj.BendRule != rt.RULE_FROM_WIRES:
            rule = rt._plain("bundle", obj.BendRule, obj.BendFactor, float(obj.MinBendRadius.Value))
            return (rule or rt.BendRule("None")), (rule or rt.BendRule("None")).text()
        rules = [w.Proxy.rule(w)[0] for w in obj.Wires if kind_of(w) == WIRE]
        radius = rt.bundle_min_radius(rules, float(obj.OD.Value))
        strictest = max((r for r in rules if r.mode != "None"), key=lambda r: r.radius(float(obj.OD.Value)), default=None)
        if strictest is None:
            return rt.BendRule("None"), "From wires: no check"
        return rt.BendRule("Fixed", radius), f"From wires: {strictest.text()}"

    def execute(self, obj):
        rt = _rt()
        path = self.path_of(obj)
        diameters = [float(w.InsulationOD.Value) for w in obj.Wires if kind_of(w) == WIRE]
        od = rt.bundle_od(diameters) or 1.0
        obj.OD = od
        self.apply_rule(obj, od)
        if _rope().rope_execute(self, obj):  # ROU-08: one rope with the wires' combined stiffness
            return
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
            if getattr(obj, "Taut", False):
                colour = (0.9, 0.1, 0.1)  # ROU-08: red when the cut length is too short for the ends
            elif kind == WIRE:
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
        if prop in ("Colour", "Shape", "Taut") and getattr(obj, "ViewObject", None) is not None:
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
    _rope().ensure_gravity_property(obj)
    return obj


def make_pipe(path, size="Custom", od=None, wall=None, material="", bend_radius=0.0, min_bend_radius=None, name="Pipe"):
    obj = _make_item(Pipe, path, name, Material=material)
    if size != "Custom":
        obj.Size = size
    if od is not None:
        obj.OD = float(od)
    if wall is not None:
        obj.WallThickness = float(wall)
    obj.BendRadius = float(bend_radius) if bend_radius else prefs()["TubeClrFactor"] * float(obj.OD.Value)
    if min_bend_radius is not None:
        obj.MinBendRadius = float(min_bend_radius)  # a fixed override
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


def pipe_table(pipe):
    """The pipe's LRA rows on its path's vertices (lead ends included), the first and last feed
    lengthened by the tube inside the fittings (ROU-05)."""
    rt = _rt()
    path = pipe.Path
    if path is None or path.Mode != "Polyline":
        return []
    pts = route_points(path)
    if len(pts) <= 2:
        return []
    rows = rt.lra_table(pts, float(pipe.BendRadius.Value))
    first, last = engagements(path)
    if rows:
        rows[0] = dict(rows[0], L=rows[0]["L"] + first)
        rows[-1] = dict(rows[-1], L=rows[-1]["L"] + last)
    return rows


def make_hose(path, inner=6.0, od=10.0, min_bend_radius=None, slack=5, name="Hose"):
    obj = _make_item(Hose, path, name, ID=float(inner), OD=float(od), Slack=int(slack))
    if min_bend_radius is not None:
        obj.MinBendRadius = float(min_bend_radius)  # a fixed override
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


def _connector_named(doc, label, found=None):
    """The connector instance an end names: the one its path is attached to, else by label."""
    if found is not None and found[2] is not None and found[2].Label == label:
        return found[2]
    if not label:
        return None
    return next((c for c in _bp().connectors(doc) if c.Label == label), None)


def mate_label(doc, label, found=None):
    """The label of the connector mated with the end's connector, when both halves are in ``doc``."""
    connector = _connector_named(doc, label, found)
    mate = _bp().mate_of(connector, doc) if connector is not None else None
    return mate.Label if mate is not None else ""


def from_to_rows(doc):
    """From-to list rows of every wire in ``doc`` (tree order), each end's mating connector named
    when both halves are present (ROU-05)."""
    rows = []
    for obj in routed(doc, (WIRE,)):
        ends = path_ends(obj.Path) if obj.Path is not None and is_path(obj.Path) else {}
        start = _end_name(obj, obj.FromConnector, obj.FromPart)
        end = _end_name(obj, obj.ToConnector, obj.ToPart)
        rows.append({"wire": obj.Label, "signal": obj.Signal, "gauge": obj.Gauge, "colour": _colour_name(obj.Colour),
                     "from": start, "from_pin": obj.FromPin, "from_mate": mate_label(doc, start, ends.get("start")),
                     "from_strip": float(obj.FromStrip.Value) if "FromStrip" in obj.PropertiesList else 0.0,
                     "to": end, "to_pin": obj.ToPin, "to_mate": mate_label(doc, end, ends.get("end")),
                     "to_strip": float(obj.ToStrip.Value) if "ToStrip" in obj.PropertiesList else 0.0,
                     "length_m": float(obj.Length.Value) / 1000.0})
    return rows


def terminal_rows(doc):
    """``[(key, kind, count, manufacturer, MPN)]``: the terminals and seals the wires in ``doc``
    bring (ROU-05, C3), summed per part; the key is the Standard Library item when the pin names
    one, else the manufacturer P/N."""
    totals = {}
    for obj in routed(doc, (WIRE,)):
        for key, qty, kind, mfr, mpn in bom_extras(obj):
            totals[(key, kind, mfr, mpn)] = totals.get((key, kind, mfr, mpn), 0) + qty
    return [(key, kind, n, mfr, mpn) for (key, kind, mfr, mpn), n in totals.items()]


def terminal_list(doc):
    """Rows for ``routing.terminal_csv``: every wire's terminals and seals and every connector
    instance's accessories in ``doc``."""
    bp = _bp()
    rows = []
    for obj in routed(doc, (WIRE,)):
        for key, qty, kind, mfr, mpn in bom_extras(obj):
            rows.append({"kind": kind, "manufacturer": mfr, "mpn": mpn, "item": key if key != mpn else "", "qty": qty})
    for connector in bp.connectors(doc):
        for mpn, mfr, item, qty, kind in bp.accessories(connector):
            rows.append({"kind": kind, "manufacturer": mfr, "mpn": mpn, "item": item, "qty": qty})
    return rows


def terminal_csv(doc):
    return _rt().terminal_csv(terminal_list(doc))


def bom_extras(obj):
    """``[(key, quantity, kind, manufacturer, MPN)]`` from an item's ``BomExtras``
    ("key|qty|kind|manufacturer|MPN"; the first ROU-05 entries had only the first three)."""
    out = []
    for entry in getattr(obj, "BomExtras", []) or []:
        fields = entry.split("|")
        if len(fields) < 3:
            continue
        try:
            qty = int(fields[1])
        except ValueError:
            continue
        mfr = fields[3] if len(fields) > 3 else ""
        mpn = fields[4] if len(fields) > 4 else fields[0]
        out.append((fields[0], qty, fields[2], mfr, mpn))
    return out


def cut_list_rows(doc):
    """Cut list rows of every pipe and hose in ``doc``."""
    rt = _rt()
    rows = []
    for obj in routed(doc, (PIPE, HOSE)):
        kind = "Pipe" if kind_of(obj) == PIPE else "Hose"
        if kind == "Pipe":
            size = obj.Size if obj.Size != "Custom" else f"{obj.OD.Value:g} x {obj.WallThickness.Value:g}"
            radius = float(obj.BendRadius.Value)
            table = pipe_table(obj) if obj.Path is not None else []
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
            marks = marks + [("bend too tight", "warn")]
        if is_routed(obj) and getattr(obj, "Taut", False):
            marks = marks + [("taut", "warn")]  # ROU-08
        if is_routed(obj) and getattr(obj, "Warnings", ""):
            marks = marks + [("gauge", "warn")]  # ROU-05: a pin that does not take this wire
    except Exception:
        pass
    return meta, marks
