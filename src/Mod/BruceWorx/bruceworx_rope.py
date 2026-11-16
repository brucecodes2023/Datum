# SPDX-License-Identifier: LGPL-2.1-or-later
"""Flexible wires and hoses (BW-ROU-08, 30 Oct 2026): the FreeCAD side of ``bruceworx/rope.py``.

A wire, hose or bundle keeps its *cut length* (``CutLength``, any value the user types; nothing
stretches) and a hidden state ``RopePoints`` (the rope's points in the document frame). A recompute
solves the rope from that state, so a moved pin never makes the wire jump to a new shape; the
route path only gives the first layout. Taut or bent past its minimum radius, the item turns red,
gets a Browser badge and a ``Problems`` text; only an Assembly drag can be stopped (see
``bruceworx_drag``).

* **Ends** are the lead straights of the pin frames (clamped points along the frame's +Z).
* **Clips**: a path point attached to geometry (not an end) is a node the wire passes through, free
  to turn; a free-space path point is only a guide for the first layout. The node is pinned at its own
  station on the route (within half a segment of the clip), so the rope between two clips has the
  route's length there, its share of the slack included.
* **Gravity** is along an axis of the top assembly (property ``BWGravity``, +-X/Y/Z, default -Z),
  else the preference ``Mod/BruceWorx/Routing/Gravity``.
* **Collision proxies**: the oriented bounding box of each part near the wire (not the parts the
  wire's ends are on, nor those whose box holds the wire's route). TODO: convex hull / mesh per part
  when the box is too coarse.
"""

import math

import FreeCAD as App

GRAVITY_AXES = ["-Z", "+Z", "-X", "+X", "-Y", "+Y"]
_VECTORS = {"-Z": (0, 0, -1), "+Z": (0, 0, 1), "-X": (-1, 0, 0), "+X": (1, 0, 0), "-Y": (0, -1, 0), "+Y": (0, 1, 0)}
CLEARANCE = 0.1  # mm between the wire surface and a proxy
MIN_CLIP_GAP = 3  # rope segments from a clip's node to the clip or end before it, and to the end after it
ASSEMBLY = "Assembly::AssemblyObject"
INSULATIONS = ["Auto", "PVC", "Silicone"]


def _br():
    import bruceworx_routing

    return bruceworx_routing


def _rt():
    from heuretes_freecad import routing

    return routing


def _rp():
    from heuretes_freecad import rope

    return rope


def _np():
    import numpy

    return numpy


def available():
    try:
        _np()
        _rp()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------------------------
# Gravity.


def top_assembly(obj):
    best = None
    for a in _br()._ancestors(obj):
        if a.TypeId == ASSEMBLY:
            best = a
    return best


def ensure_gravity_property(obj):
    """The gravity properties on the top assembly of ``obj`` (made when missing); None when there is no assembly."""
    assembly = top_assembly(obj)
    if assembly is not None:
        ensure_gravity_property_on(assembly)
    return assembly


def set_gravity(assembly, axis):
    if axis not in GRAVITY_AXES:
        raise ValueError(f"gravity axis {axis!r} is not one of {GRAVITY_AXES}")
    ensure_gravity_property_on(assembly)
    assembly.BWGravity = axis


def ensure_gravity_property_on(assembly):
    """``BWGravity`` (the axis) and ``BWGravityOn`` (False unless asked for: an assembly saved
    before the switch has no sag work done)."""
    if "BWGravity" not in assembly.PropertiesList:
        assembly.addProperty("App::PropertyEnumeration", "BWGravity", "BruceWorx",
                             "Direction of gravity for flexible wires (along an axis of this assembly)")
        assembly.BWGravity = GRAVITY_AXES
        assembly.BWGravity = "-Z"
    if "BWGravityOn" not in assembly.PropertiesList:
        assembly.addProperty("App::PropertyBool", "BWGravityOn", "BruceWorx",
                             "Flexible wires sag under gravity (off: length, bend limit and contacts only)")
        assembly.BWGravityOn = False


def gravity_on(obj):
    """Is the gravity term used for ``obj``? The top assembly's ``BWGravityOn`` (False when it has
    none); with no assembly the preference ``GravityOn`` (default off)."""
    assembly = top_assembly(obj)
    if assembly is not None:
        return bool("BWGravityOn" in assembly.PropertiesList and assembly.BWGravityOn)
    try:
        return bool(App.ParamGet(_br().PREFS_PATH).GetBool("GravityOn", False))
    except Exception:
        return False


def set_gravity_on(assembly, on):
    """Switch gravity for the wires of ``assembly``; every rope wire inside is recomputed once."""
    ensure_gravity_property_on(assembly)
    if bool(assembly.BWGravityOn) == bool(on):
        return
    assembly.BWGravityOn = bool(on)
    for wire in rope_wires(assembly):
        wire.touch()
    if assembly.Document is not None:
        assembly.Document.recompute()


def rope_wires(assembly):
    """The wires, hoses and bundles whose top assembly is ``assembly``."""
    br = _br()
    out = []
    for o in assembly.Document.Objects:
        if getattr(o, "Proxy", None) is not None and "RopePoints" in o.PropertiesList:
            top = top_assembly(o)
            if top is not None and top.Name == assembly.Name:
                out.append(o)
    return out


def gravity_axis(obj):
    assembly = top_assembly(obj)
    if assembly is not None and "BWGravity" in assembly.PropertiesList:
        return str(assembly.BWGravity)
    try:
        axis = App.ParamGet(_br().PREFS_PATH).GetString("Gravity", "-Z")
    except Exception:
        axis = "-Z"
    return axis if axis in GRAVITY_AXES else "-Z"


def gravity_vector(obj):
    """Unit gravity in the document frame."""
    v = App.Vector(*_VECTORS[gravity_axis(obj)])
    assembly = top_assembly(obj)
    if assembly is not None:
        try:
            v = assembly.getGlobalPlacement().Rotation.multVec(v)
        except Exception:
            pass
    return (v.x, v.y, v.z)


# ---------------------------------------------------------------------------------------------
# Item mechanics.


def material_of(obj):
    choice = str(getattr(obj, "Insulation", "Auto"))
    if choice in ("PVC", "Silicone"):
        return choice
    text = f"{getattr(obj, 'BendRule', '')} {getattr(obj, 'BendRuleUsed', '')}"
    return "Silicone" if "Silicone" in text else "PVC"


def mechanics(obj):
    """``(od, EI, q, rmin)`` of a wire, hose or bundle: outer diameter, bending stiffness (N mm^2),
    weight per length (N/mm) and minimum bend radius (0 = no check)."""
    br, rt, rp = _br(), _rt(), _rp()
    kind = br.kind_of(obj)
    factor = float(getattr(obj, "StiffnessFactor", 1.0)) or 1.0
    if kind == br.WIRE:
        d, a = rt.gauge(obj.Gauge)
        od = obj.Proxy.od(obj)
        ei, q = rp.wire_properties(d, a, od, material_of(obj), factor)
    elif kind == br.HOSE:
        od = float(obj.OD.Value)
        ei, q = rp.tube_properties(od, float(obj.ID.Value), "Silicone", factor)
    else:  # bundle: its wires side by side, their stiffnesses add
        od = float(obj.OD.Value)
        ei = q = 0.0
        for w in obj.Wires:
            if br.kind_of(w) == br.WIRE:
                d, a = rt.gauge(w.Gauge)
                e1, q1 = rp.wire_properties(d, a, w.Proxy.od(w), material_of(w), 1.0)
                ei, q = ei + e1, q + q1
        ei, q = ei * factor, max(q, 1e-9)
    return od, ei, q, float(obj.MinBendRadius.Value)


def hidden_length(obj, path):
    """Cut length that is not visible geometry: the wire inside its connectors, the hose over its barbs."""
    br = _br()
    kind = br.kind_of(obj)
    if kind == br.WIRE:
        return float(obj.FromStrip.Value) + float(obj.ToStrip.Value) if "FromStrip" in obj.PropertiesList else 0.0
    if kind == br.HOSE:
        return float(sum(br.engagements(path)))
    return 0.0


# ---------------------------------------------------------------------------------------------
# Geometry helpers.


def _arr(v):
    np = _np()
    if isinstance(v, np.ndarray):
        return v
    return np.array([v.x, v.y, v.z], dtype=float)


def _vec(a):
    return App.Vector(float(a[0]), float(a[1]), float(a[2]))


def container_placement(obj):
    br = _br()
    parent = br._container_of(obj)
    return parent.getGlobalPlacement() if parent is not None else App.Placement()


def guide_points(path, lead_start, lead_end):
    """Dense document-frame samples along the route path between the lead tips (the lead straights
    themselves are the rope's clamped points)."""
    np = _np()
    plc = container_placement(path)
    pts = [_arr(plc.multVec(p)) for p in path.Shape.Wires[0].discretize(Number=240)]
    seg = np.linalg.norm(np.diff(np.array(pts), axis=0), axis=-1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    lo = lead_start[2] if lead_start is not None else 0.0
    hi = s[-1] - (lead_end[2] if lead_end is not None else 0.0)
    keep = [p for p, d in zip(pts, s) if lo + 1e-6 < d < hi - 1e-6]
    return pts[0], pts[-1], keep, pts, s


def _along(pts, s, point):
    """Distance along the sampled route ``pts`` (cumulative lengths ``s``) of the foot of ``point`` on it."""
    np = _np()
    p = np.asarray(point, dtype=float)
    a, b = pts[:-1], pts[1:]
    d = b - a
    L2 = np.maximum((d * d).sum(axis=-1), 1e-18)
    t = np.clip(((p - a) * d).sum(axis=-1) / L2, 0.0, 1.0)
    dist = np.linalg.norm(a + t[:, None] * d - p, axis=-1)
    k = int(dist.argmin())
    return float(s[k] + t[k] * math.sqrt(L2[k]))


class End:
    """One end of a rope: a lead on a pin frame, or a bare point."""

    def __init__(self, lead, point, owner):
        self.lead = lead  # (origin, direction, length) arrays, or None
        self.point = point  # the end point when there is no lead
        self.owner = owner  # the assembly child it rides on (drag), or None


def path_ends(path):
    """``(start, end)`` as :class:`End` objects (document frame)."""
    br = _br()
    np = _np()
    leads = br.path_leads(path)
    found = br.path_ends(path)
    ends = []
    n = len(path.Points)
    attached = {index: target for index, target, _sub in br.attachments_of(path)}
    for k, key in enumerate(("start", "end")):
        lead = leads[k]
        owner = found.get(key, (None, None, None))[2]
        if owner is None:
            owner = attached.get(0 if k == 0 else n - 1)  # a bare point stuck to a part
        point = _arr(path.Points[0 if k == 0 else -1])
        if lead is not None:
            d = np.array(lead.direction, dtype=float)
            d /= max(np.linalg.norm(d), 1e-12)
            ends.append(End((np.array(lead.origin, dtype=float), d, float(lead.length)), None, owner))
        else:
            ends.append(End(None, point, owner))
    return ends


def clip_points(path):
    """``[(point index, document point, target object)]`` of the path points attached to geometry other than the ends."""
    br = _br()
    n = len(path.Points)
    out = []
    for index, target, _sub in br.attachments_of(path):
        if 0 < index < n - 1:
            out.append((index, _arr(path.Points[index]), target))
    return sorted(out, key=lambda c: c[0])


# ---------------------------------------------------------------------------------------------
# Collision proxies.

_BOX_CACHE = {}


def _local_box(child):
    """``(center, half)`` of the oriented bounding box of ``child`` in its own frame, or None."""
    try:
        linked = child.getLinkedObject(True)
        shape = getattr(linked, "Shape", None)
        key = (child.Document.Name, child.Name)
        sig = None
        if shape is not None and not shape.isNull():
            sig = (linked.Name, shape.hashCode())
        cached = _BOX_CACHE.get(key)
        if cached is not None and cached[0] == sig and sig is not None:
            return cached[1]
        if shape is None or shape.isNull():
            import Part

            shape = Part.getShape(child, "", False, False)
            if shape is None or shape.isNull():
                return None
        if not shape.Solids and not shape.Faces:
            return None
        if linked is child and not shape.Placement.isIdentity():
            # a plain feature (not a link): its Shape already carries its Placement, which box_records applies
            # again; the box was off by the placement (a Part::Box at x 20 boxed at x 40)
            shape = shape.copy()
            shape.Placement = App.Placement()
        bb = shape.BoundBox
        box = ((bb.Center.x, bb.Center.y, bb.Center.z), (bb.XLength / 2, bb.YLength / 2, bb.ZLength / 2))
        _BOX_CACHE[key] = (sig, box)
        return box
    except Exception:
        return None


def collision_candidates(obj):
    """The parts a wire can touch: children of its container (the assembly) that are real parts."""
    br = _br()
    container = br._container_of(obj)
    group = list(container.Group) if container is not None and hasattr(container, "Group") else list(obj.Document.Objects)
    out = []
    for child in group:
        if br.is_routed(child) or br.is_path(child):
            continue
        tid = child.TypeId
        if tid.startswith("App::Origin") or tid in ("App::Line", "App::Plane", "App::Point") or "Joint" in tid:
            continue
        if tid.startswith("Assembly::") or tid == "App::FeaturePython" or tid == "App::DocumentObjectGroup":
            continue
        out.append(child)
    return out


def global_placement(obj):
    """Document-frame placement of an assembly child (an App::Link has no getGlobalPlacement)."""
    try:
        return obj.getGlobalPlacement()
    except Exception:
        parent = _br()._container_of(obj)
        base = parent.getGlobalPlacement() if parent is not None else App.Placement()
        return base.multiply(obj.Placement)


def box_records(obj, exclude, inside_points=()):
    """Collision proxies of ``obj``'s neighbours: ``[(child, center_global, axes_global (rows), half, base placement)]``."""
    np = _np()
    out = []
    inside_points = [tuple(p) for p in inside_points]
    for child in collision_candidates(obj):
        if child.Name in exclude:
            continue
        local = _local_box(child)
        if local is None:
            continue
        g = global_placement(child)
        center = g.multVec(App.Vector(*local[0]))
        axes = [g.Rotation.multVec(App.Vector(*e)) for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        record = (child, _arr(center), np.array([[a.x, a.y, a.z] for a in axes]), np.array(local[1]), g)
        if inside_points:
            # a part whose box already holds an end of the wire (a cover over a connector) is no obstacle to it
            box = _rp().Box(tuple(record[1]), tuple(record[3]), tuple(map(tuple, record[2])))
            sd, _g = _rp().box_distance(box, np.array(inside_points))
            if (sd < 0.5).any():
                continue
        out.append(record)
    return out


def make_boxes(records, deltas=None, reach=None):
    """``rope.Box`` list from box records; ``deltas`` {child name: App.Placement} moves them (a drag).
    ``reach`` = (min, max) arrays: only boxes whose bounding sphere touches that range."""
    np = _np()
    rp = _rp()
    boxes = []
    for child, center, axes, half, _g in records:
        c, a = center, axes
        d = (deltas or {}).get(child.Name)
        if d is not None:
            c = _arr(d.multVec(_vec(center)))
            r = d.Rotation
            a = np.array([[v.x, v.y, v.z] for v in (r.multVec(_vec(row)) for row in axes)])
        if reach is not None:
            radius = float(np.linalg.norm(half))
            if np.any(c + radius < reach[0]) or np.any(c - radius > reach[1]):
                continue
        boxes.append(rp.Box(tuple(c), tuple(half), tuple(map(tuple, a))))
    return boxes


# ---------------------------------------------------------------------------------------------
# One rope item.


SOLVER_CALLS = [0]  # how many times a rope was solved (tests, and a way to see idle work)


class Result:
    def __init__(self, x, taut, message, strain, ratio, sag):
        self.x, self.taut, self.message, self.strain, self.ratio, self.sag = x, taut, message, strain, ratio, sag
        self.weight_on = True


class RopeItem:
    """The rope of a wire, hose or bundle: inputs read once from the objects, then solved any number
    of times (a recompute, or each step of a drag)."""

    def __init__(self, obj, path=None):
        np = _np()
        br = _br()
        self.obj = obj
        self.path = path or obj.Path
        od, ei, q, rmin = mechanics(obj)
        self.od, self.ei, self.q, self.rmin = od, ei, q, rmin
        self.hidden = hidden_length(obj, self.path)
        cut = float(obj.CutLength.Value)
        self.cut = cut if cut > 0 else self.default_cut()
        self.length = max(self.cut - self.hidden, 1e-3)
        self.n = _rp().point_count(self.length, od)
        self.ends = path_ends(self.path)
        self.clips = clip_points(self.path)
        self.gravity = gravity_vector(obj)
        self.weight_on = gravity_on(obj)
        self.x = None
        self.on_route = False
        self.exclude = {e.owner.Name for e in self.ends if e.owner is not None}
        self.records = None

    def default_cut(self):
        """W2: an item that has no cut length yet takes its present length (slack included), else
        the path's length with its slack."""
        obj, path = self.obj, self.path
        if float(obj.Length.Value) > 0:
            return float(obj.Length.Value)
        slack = 1.0 + float(getattr(obj, "Slack", 0)) / 100.0
        return float(path.Length.Value) * slack + self.hidden

    def signature(self):
        """Everything the solution depends on, rounded: the ends and clips, the cut length, the rope
        mechanics, gravity (direction and on/off) and the proxy boxes near the wire. None when the
        proxies cannot be read."""
        try:
            def r(v):
                return tuple(round(float(c), 5) for c in v)
            ends = [(r(e.point) if e.point is not None else None,
                     (r(e.lead[0]), r(e.lead[1]), round(float(e.lead[2]), 5)) if e.lead is not None else None)
                    for e in self.ends]
            clips = [r(c[1]) for c in self.clips]
            if self.records is None:
                self.records = self.neighbour_records()
            boxes = [(c.Name, r(center), r(half), tuple(r(row) for row in axes))
                     for c, center, axes, half, _g in self.records]
            return (ends, clips, round(self.cut, 6), round(self.od, 6), round(self.ei, 6), round(self.q, 9),
                    round(self.rmin, 6), r(self.gravity), self.weight_on, boxes)
        except Exception:
            return None

    # ----- state -----

    def stored(self):
        np = _np()
        pts = list(self.obj.RopePoints)
        if len(pts) == self.n:
            return np.array([[p.x, p.y, p.z] for p in pts], dtype=float)
        return None

    def plan(self):
        """``(ka, kb, l0)`` from :func:`rope.lead_plan` for the leads of this item."""
        la = self.ends[0].lead[2] if self.ends[0].lead else 0.0
        lb = self.ends[1].lead[2] if self.ends[1].lead else 0.0
        return _rp().lead_plan(self.n, self.length, la, lb)

    def fixed_counts(self):
        ka, kb, _l0 = self.plan()
        return ka + 1, kb + 1

    def l0(self):
        return self.plan()[2]

    def _stations(self, s):
        """``(ka, la, step)``: rope node ``k`` (past the start lead's ``ka`` segments) lies ``la + (k - ka) * step``
        along the route when the rope follows it: the lead straights at their own length, the free nodes evenly
        over the rest (``s`` is the cumulative length of the dense route samples)."""
        ka, kb, _l0 = self.plan()
        la = self.ends[0].lead[2] if self.ends[0].lead else 0.0
        lb = self.ends[1].lead[2] if self.ends[1].lead else 0.0
        free = max(self.n - 1 - ka - kb, 1)
        return ka, la, max(float(s[-1]) - la - lb, 0.0) / free

    def clip_nodes(self, ks, ke, pts_dense, s):
        """Rope node of each clip, by its distance along the route, or -1 for a clip that holds no node: kept in
        ``ClipNodes`` once assigned. A clip needs two free nodes to the clip or end before it and to the end after
        it; one closer holds no node. A single free node between two held ones has its bend set by the two
        lengths alone, and on a route at the minimum radius that comes out past the limit (T1X2 motor leads and
        pigtails, 14 Nov 2026: 1.02 to 1.12 of it with nothing the solver can move)."""
        np = _np()
        have = list(self.obj.ClipNodes)
        if len(have) == len(self.clips) and all(h == -1 or ks <= h < self.n - ke for h in have):
            return have
        ka, la, step = self._stations(s)
        pts = np.asarray(pts_dense, dtype=float)
        last, end = ks - 1, self.n - ke
        order = sorted(range(len(self.clips)), key=lambda i: _along(pts, s, self.clips[i][1]))
        nodes = [-1] * len(self.clips)
        for i in order:
            node = ka + int(round((_along(pts, s, self.clips[i][1]) - la) / max(step, 1e-9)))
            node = min(max(node, ks), end - 1)
            if node - last >= MIN_CLIP_GAP and end - node >= MIN_CLIP_GAP:
                nodes[i] = last = node
        return nodes

    def route_layout(self, x, dense, s):
        """``x`` with every node past the start lead and before the end lead moved to its station on the route
        (see :meth:`clip_station`)."""
        np = _np()
        ka, la, step = self._stations(s)
        kb = self.plan()[1]
        pts = np.asarray(dense, dtype=float)
        k = np.arange(ka + 1, self.n - 1 - kb)
        stations = la + (k - ka) * step
        x[k] = np.stack([np.interp(stations, s, pts[:, c]) for c in range(3)], axis=-1)
        return x

    def clip_station(self, node, dense, s):
        """The point on the route where rope node ``node`` lies when the rope follows the route. A clip pins its
        node here, not at the picked point: rounded to the nearest node, the picked point left the rope between
        two clips up to one segment longer or shorter than the route there (17 % on an 8 mm span), far past the
        strain tolerance, so every clipped wire came out taut and kinked (T1X2 harness, 14 Nov 2026). The
        station is within half a segment of the picked point, along the wire."""
        np = _np()
        ka, la, step = self._stations(s)
        station = la + (node - ka) * step
        pts = np.asarray(dense, dtype=float)
        return np.array([np.interp(station, s, pts[:, k]) for k in range(3)])

    def build(self, drag_deltas=None, state=None):
        """The :class:`rope.Rope` at the current pin positions, warm-started from ``state`` (an array),
        the object's stored points or a first layout."""
        np = _np()
        rp = _rp()
        ka, kb, l0 = self.plan()
        ks, ke = ka + 1, kb + 1
        start = self.moved_end(self.ends[0], drag_deltas)
        end = self.moved_end(self.ends[1], drag_deltas)
        head = rp.lead_points(*start[0][:2], start[0][2], ka) if start[0] else [start[1]]
        tail = rp.lead_points(*end[0][:2], end[0][2], kb) if end[0] else [end[1]]
        dense = None
        x = state if state is not None else self.stored()
        pinned = ()
        nodes = []
        fresh = x is None or len(x) != self.n
        self.on_route = False
        if fresh:
            tip0, tip1, inner, dense, _s = guide_points(self.path, self.ends[0].lead, self.ends[1].lead)
            guides = [head[0] if not self.ends[0].lead else _arr(self.ends[0].lead[0])] + inner + [tail[0]]
            x, _a, _b, _l0 = rp.initial_layout(self.n, self.length, guides, start[0], end[0], self.gravity)
        if self.clips:
            if dense is None:
                _t0, _t1, _inner, dense, _s = guide_points(self.path, self.ends[0].lead, self.ends[1].lead)
            nodes = self.clip_nodes(ks, ke, dense, _s)
        x = np.array(x, dtype=float)
        held = [(node, clip) for node, clip in zip(nodes, self.clips) if node >= 0]
        if held:
            spots = {node: self.moved_point(self.clip_station(node, dense, _s), target, drag_deltas)
                     for node, (_index, _point, target) in held}
            # A clipped wire starts on its route when it has no shape yet or its clips moved away from the one it
            # has: the clips pulled out of a free first layout or an old shape leave kinks of many times the bend
            # limit, which the solve does not always undo (T1X2 I2C leads, 14 Nov 2026).
            if fresh or any(np.linalg.norm(x[node] - p) > l0 for node, p in spots.items()):
                if drag_deltas is None:
                    x = self.route_layout(x, dense, _s)
                    self.on_route = True
            for node, p in spots.items():
                x[node] = p
            pinned = tuple(sorted(spots))
        x[:ks] = np.array(head)
        x[self.n - ke :] = np.array(tail)[::-1]
        self.clip_node_list = nodes
        rope = rp.Rope(x, l0 * (self.n - 1), self.ei, self.q, rmin=self.rmin, radius=self.od / 2 + CLEARANCE,
                       fixed_start=ks, fixed_end=ke, gravity=self.gravity, pinned=pinned,
                       weight_on=self.weight_on)
        return rope

    @staticmethod
    def moved_point(point, target, deltas):
        if deltas and target is not None and target.Name in deltas:
            return _arr(deltas[target.Name].multVec(_vec(point)))
        return point

    def moved_end(self, end, deltas):
        """``(lead, point)`` of ``end`` after the drag deltas {owner name: App.Placement}."""
        np = _np()
        d = deltas.get(end.owner.Name) if deltas and end.owner is not None else None
        if d is None:
            return end.lead, end.point
        if end.lead is not None:
            o = _arr(d.multVec(_vec(end.lead[0])))
            v = d.Rotation.multVec(_vec(end.lead[1]))
            return (o, np.array([v.x, v.y, v.z]), end.lead[2]), None
        return None, _arr(d.multVec(_vec(end.point)))

    def neighbour_records(self):
        """Proxies of the parts near the wire, less those whose box holds an end of the wire or its route: a
        route laid in a groove or under a cover is inside that part's box, which is too coarse to say anything
        there (the box would push the wire off its route, out of the groove)."""
        pts = []
        for e in self.ends:
            if e.lead is not None:
                pts += [tuple(e.lead[0]), tuple(e.lead[0] + e.lead[1] * e.lead[2])]
            else:
                pts.append(tuple(e.point))
        try:
            _t0, _t1, inner, _dense, _s = guide_points(self.path, self.ends[0].lead, self.ends[1].lead)
            pts += [tuple(p) for p in inner[::4]]
        except Exception:
            pass  # no drawn route: the ends alone
        return box_records(self.obj, self.exclude, pts)

    def boxes(self, deltas=None, rope=None):
        np = _np()
        if self.records is None:
            self.records = self.neighbour_records()
        reach = None
        if rope is not None:
            pad = self.length
            reach = (rope.x.min(axis=0) - pad, rope.x.max(axis=0) + pad)
        return make_boxes(self.records, deltas, reach)

    # ----- solving -----

    def solve(self, drag_deltas=None, state=None, max_steps=400, tol=1e-3):
        """A :class:`Result`. A cut length shorter than the lead tips' distance is taut without solving."""
        np = _np()
        rp = _rp()
        rope = self.build(drag_deltas, state)
        a, b = rope.x[rope.fixed_start - 1], rope.x[self.n - rope.fixed_end]
        free = (self.n - 1 - (rope.fixed_start - 1) - (rope.fixed_end - 1)) * rope.l0
        gap = float(np.linalg.norm(a - b))
        if drag_deltas is not None and gap > free * (1 + 1e-3):
            return Result(rope.x, True, f"taut: the ends are {gap - free:.1f} mm further apart than the cut length allows",
                          gap / free - 1.0, 0.0, 0.0)
        boxes = self.boxes(drag_deltas, rope)
        if boxes and state is None and self.stored() is None and not self.on_route:
            rp.drape(rope.x, boxes, self.gravity, rope.radius, rope.fixed_start, rope.fixed_end, rope.pinned)
        SOLVER_CALLS[0] += 1
        rp.solve([rope], boxes, max_steps=max_steps, tol=tol)
        message = ""
        if rope.taut:
            if rope.message == "bend limit":
                message = f"bend limit: tighter than the minimum radius ({self.rmin:.1f} mm)"
            elif gap > free:
                message = f"taut: the ends are {gap - free:.1f} mm further apart than the cut length allows"
            else:
                message = "taut at this cut length"
        sag = rp.straight_line_sag(rope.x, self.gravity) if self.weight_on else 0.0
        res = Result(rope.x, rope.taut, message, rope.strain, rope.bend_ratio, sag)
        res.weight_on = self.weight_on
        return res


# ---------------------------------------------------------------------------------------------
# Writing the result into the object.


def status_text(res):
    if res.taut:
        return "taut"
    if not res.weight_on:
        return "slack, gravity off"
    if res.sag < 0.05:
        return "straight"
    return f"sagging, {res.sag:.1f} mm below the straight line"


def sweep_points(item, x):
    """The Part wire through the rope's points, as a clamped cubic B-spline in the object's container frame."""
    br, rt = _br(), _rt()
    Part = br._part()
    pts = br._to_parent(item.obj, [tuple(p) for p in x])
    pts = rt.clean_points(pts, tol=1e-6)
    inverse = br._parent_inverse(item.obj)
    t0 = t1 = None
    if item.ends[0].lead is not None:
        v = item.ends[0].lead[1]
        t0 = tuple(inverse.Rotation.multVec(App.Vector(*v))) if inverse is not None else tuple(v)
    if item.ends[1].lead is not None:
        v = item.ends[1].lead[1]
        t1 = tuple(inverse.Rotation.multVec(App.Vector(-v[0], -v[1], -v[2]))) if inverse is not None else tuple(-v)
    spline = rt.Spline(pts, t0, t1)
    poles, knots, mults = spline.bspline()
    curve = Part.BSplineCurve()
    curve.buildFromPolesMultsKnots([br.V(p) for p in poles], mults, knots, False, 3)
    return Part.Wire([curve.toShape()])


def apply_result(proxy, obj, item, res):
    """Store the rope, set the status properties and build the solid."""
    br = _br()
    obj.RopePoints = [_vec(p) for p in res.x]
    if "ClipNodes" in obj.PropertiesList:
        obj.ClipNodes = list(getattr(item, "clip_node_list", []))
    obj.Taut = bool(res.taut)
    obj.Problems = res.message
    obj.RopeStatus = status_text(res)
    obj.BendOK = not (item.rmin > 0 and res.ratio > 1.0 + 1e-3)
    wire = sweep_points(item, res.x)
    inner = float(obj.ID.Value) if br.kind_of(obj) == br.HOSE else 0.0
    try:
        obj.Shape = br.sweep(wire, item.od, inner)
    except Exception:
        try:
            obj.Shape = sweep_frenet(wire, item.od, inner)  # deep loops: the Frenet frame holds where the default fails
        except Exception:
            try:  # a squeezed (taut) rope: through its points as straight segments
                Part = br._part()
                poly = Part.makePolygon([App.Vector(*q) for q in br._to_parent(obj, [tuple(p) for p in res.x])])
                obj.Shape = br.sweep(Part.Wire(poly.Edges), item.od, inner)
            except Exception:
                obj.Shape = br.sweep(item.path.Shape.Wires[0], item.od, inner)  # last resort: the route path
    return wire


def sweep_frenet(wire, od, inner=0.0):
    Part = _br()._part()
    edge = wire.Edges[0]
    point, tangent = edge.valueAt(edge.FirstParameter), edge.tangentAt(edge.FirstParameter)
    solid = wire.makePipeShell([Part.Wire([Part.makeCircle(od / 2.0, point, tangent)])], True, True, 2)
    if 0 < inner < od:
        solid = solid.cut(wire.makePipeShell([Part.Wire([Part.makeCircle(inner / 2.0, point, tangent)])], True, True, 2))
    return solid


def bundle_of(wire):
    """The bundle that lists ``wire`` among its Wires, or None."""
    br = _br()
    if br.kind_of(wire) != br.WIRE:
        return None
    for o in wire.InList:
        if br.kind_of(o) == br.BUNDLE and any(w.Name == wire.Name for w in o.Wires):
            return o
    return None


def follow_members(bundle):
    """31 Oct: the wires of ``bundle`` take its solved centreline (no solve of their own over the shared
    span; the bundle has no breakouts yet, so it is the whole wire). Each keeps its cut length."""
    br = _br()
    pts = [tuple(p) for p in bundle.RopePoints]
    if len(pts) < 2:
        return
    for wire in bundle.Wires:
        if br.kind_of(wire) != br.WIRE:
            continue
        try:
            item = RopeItem(wire)
            res = Result(_np().array(pts, dtype=float), bool(bundle.Taut), str(bundle.Problems), 0.0,
                         0.0 if bundle.BendOK else 2.0, 0.0)
            res.weight_on = item.weight_on
            apply_result(wire.Proxy, wire, item, res)
            wire.RopeStatus = f"in bundle {bundle.Label}"
            wire.BendOK = bool(bundle.BendOK)
            if abs(float(wire.CutLength.Value) - item.cut) > 1e-9:
                wire.CutLength = item.cut
            wire.Proxy.finish(wire, item.cut)
            wire.purgeTouched()
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx rope: {wire.Label} in {bundle.Label}: {exc}\n")


def rope_execute(proxy, obj):
    """Solve and draw ``obj`` as a rope. Returns True; False when numpy or the rope fails (the caller falls back to the path)."""
    if not available():
        return False
    try:
        if _br().kind_of(obj) == _br().BUNDLE:
            ok = _rope_execute(proxy, obj)
            if ok:
                follow_members(obj)
            return ok
        if bundle_of(obj) is not None and len(obj.RopePoints) > 1 and float(obj.CutLength.Value) > 0 \
                and not obj.Shape.isNull():
            proxy.finish(obj, float(obj.CutLength.Value))  # drawn by its bundle: only the length follows the cut
            return True
        return _rope_execute(proxy, obj)
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx rope: {obj.Label}: {exc}\n")
        return False


def _rope_execute(proxy, obj):
    try:
        item = RopeItem(obj)
        sig = item.signature()
        if sig is not None and getattr(proxy, "_rope_sig", None) == sig and item.stored() is not None \
                and not obj.Shape.isNull():
            return True  # touched by a dependency, but nothing this rope depends on moved: keep the solved rope
        res = item.solve()
        if "SlackApplied" in obj.PropertiesList and hasattr(obj, "Slack"):
            if int(obj.SlackApplied) != int(obj.Slack):
                obj.SlackApplied = int(obj.Slack)
        apply_result(proxy, obj, item, res)
        if abs(float(obj.CutLength.Value) - item.cut) > 1e-9:
            obj.CutLength = item.cut
        proxy.finish(obj, item.cut)
        proxy._rope_sig = sig
        return True
    except Exception as exc:  # the old path-following wire is better than none
        App.Console.PrintWarning(f"BruceWorx rope: {obj.Label}: {exc}\n")
        return False


# ---------------------------------------------------------------------------------------------
# The Gravity switch (31 Oct): command, shortcut and the target assembly.

COMMAND = "BruceWorx_RouteGravity"
SHORTCUT = "G, V"  # free: Draft has G, R; Sketcher G, A/L/M/Y; View has G, G


def has_rope_wires(assembly):
    return assembly is not None and bool(rope_wires(assembly))


def target_assembly(doc=None, selection=None):
    """The assembly the Gravity switch acts on: that of the selected wire (or the selected assembly), else the
    active part's top assembly, else the only assembly of the document that has rope wires. None otherwise."""
    doc = doc or App.ActiveDocument
    if doc is None:
        return None
    if selection is None:
        try:
            import FreeCADGui

            selection = FreeCADGui.Selection.getSelection(doc.Name)
        except Exception:
            selection = []
    for o in selection:
        if o.TypeId == ASSEMBLY:
            return o
        top = top_assembly(o)
        if top is not None and "RopePoints" in o.PropertiesList:
            return top
    try:
        import FreeCADGui

        active = FreeCADGui.ActiveDocument.ActiveView.getActiveObject("part")
        if active is not None:
            top = active if active.TypeId == ASSEMBLY and top_assembly(active) is None else top_assembly(active)
            top = top or (active if active.TypeId == ASSEMBLY else None)
            if top is not None:
                return top
    except Exception:
        pass
    found = [o for o in doc.Objects if o.TypeId == ASSEMBLY and top_assembly(o) is None and has_rope_wires(o)]
    return found[0] if len(found) == 1 else None


def gravity_state(doc=None):
    """``(visible, on, assembly)`` for the nav pill and the ribbon: visible when the target assembly has rope wires."""
    assembly = target_assembly(doc)
    if assembly is None or not has_rope_wires(assembly):
        return False, False, assembly
    return True, bool("BWGravityOn" in assembly.PropertiesList and assembly.BWGravityOn), assembly


def toggle_gravity(doc=None):
    """Flip gravity on the target assembly (the wires are recomputed once). Returns the new state, None without a target."""
    assembly = target_assembly(doc)
    if assembly is None:
        return None
    ensure_gravity_property_on(assembly)
    set_gravity_on(assembly, not bool(assembly.BWGravityOn))
    return bool(assembly.BWGravityOn)


class _GravityCommand:
    def GetResources(self):
        import os

        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), "Heuretes_RouteWire.svg"),
                "MenuText": "Gravity", "Accel": SHORTCUT, "Checkable": True,
                "ToolTip": "Let the flexible wires sag under gravity (on) or only keep their length, bend limit and "
                           "contacts (off, saves recompute time). Acts on the active assembly or the selected wire's."}

    def IsActive(self):
        if App.ActiveDocument is None:
            return False
        return True  # cheap: the shown state is synced when a document is activated or recomputed (navpill)

    def Activated(self, index=None):
        state = toggle_gravity()
        if state is None:
            self._sync(False)
            try:
                import FreeCADGui

                FreeCADGui.getMainWindow().statusBar().showMessage("Gravity: no assembly with flexible wires to act on.", 5000)
            except Exception:
                pass
            return
        self._sync(bool(state))

    @staticmethod
    def _sync(on):  # also called by the nav pill
        """Make the command's action show the state (a checkable command otherwise flips on its own)."""
        try:
            import FreeCADGui

            for action in FreeCADGui.Command.get(COMMAND).getAction():
                if action.isChecked() != on:
                    action.blockSignals(True)
                    action.setChecked(on)
                    action.blockSignals(False)
        except Exception:
            pass


def install_gui():
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND, _GravityCommand())


def sync_command(on):
    """Show ``on`` on the ribbon button and the menu entry."""
    _GravityCommand._sync(bool(on))
