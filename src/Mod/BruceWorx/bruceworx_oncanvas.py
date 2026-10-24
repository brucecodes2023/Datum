# SPDX-License-Identifier: LGPL-2.1-or-later
"""On-canvas editing (roadmap MOD-14; plan chapter 28-mod14-plan.tex, approved 24 Oct).

Double-click a face in the 3D view and a drag arrow, a value field and a
command bar appear, as in Fusion's press-pull. Alt-double-click a feature's
row in the Browser does the same; a plain double-click there still opens the
task dialog. First version:

* Pad and Pocket: the distance (Length; Length2 on the second side of a
  two-sided pad; symmetric pads move both caps). Up to face, Up to first and
  Through all show the type and no arrow (Edit... changes it).
* Fillet: the radius. The arrow runs along a face next to the filleted edge,
  from the old sharp edge (dashed) to where the fillet meets the face.
* Hole: the depth (DepthType Dimension) and, with Tab, the diameter.

The arrow is FreeCAD 1.1's own SoLinearDragger (created by type name; pivy's
dragger callbacks crash FreeCAD, View3DInventorPy.addDraggerCallback does
not). It starts on the double-clicked face and points the way the value
grows. The value follows the point of the arrow's line nearest the pointer's
view ray (ray_param), snapped to a step that follows the zoom (Shift: ten
times finer), to other faces' heights (6 px catch, 10 px release) and never
below one step. One drag is one undo step named "<feature>: <field>" like the
Properties tab's edits, so the tab and the undo toast follow. Recomputes run
one at a time and the latest value wins; when they average more than
Preferences/BruceWorx/OnCanvasPreviewMs (80 ms) only the edited feature is
recomputed during the drag, shown see-through, and the rest catch up on
release.

Leave with Done, Enter on an unchanged field, a click on empty space or
another double-click; Esc undoes what this edit did. Edit... opens the task
dialog. Preferences/BruceWorx/OnCanvasEditing (default on).
"""

from __future__ import annotations

import math

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "OnCanvasEditing"
PREVIEW_PREF = "OnCanvasPreviewMs"
DEFAULT_PREVIEW_MS = 80
THEMES_PATH = "User parameter:BaseApp/Preferences/Themes"
VIEW_CLASS = "Gui::View3DInventor"
FIELD_NAME = "BruceWorxCanvasField"
BAR_NAME = "BruceWorxCanvasBar"

ACTIVE_COLOR = "#FF9F0A"  # the arrow while dragged (plan: accent idle, orange dragged)
ARROW_SCREEN = 0.008  # arrow geometry unit as a share of the view height
ARROW_TIP = 13.75  # SoArrowGeometry tip: cylinder 10 + 1.5 * cone 2.5
STEP_FRACTION = 0.015  # the snap step is the power of ten under 1.5 % of the view
CATCH_PX, RELEASE_PX = 6, 10  # face-height snap and its hysteresis
NEAR_PARALLEL_DEG = 8  # the arrow points almost at the eye below this
MIN_VALUE = 0.01  # mm, the floor when snapping is free
PLANE_TOL = 1e-4

SNAP_AUTO, SNAP_FREE, SNAP_GRID = "auto", "free", "grid"
SNAP_MODES = (SNAP_AUTO, SNAP_FREE, SNAP_GRID)

EXTRUDE_TYPES = ("PartDesign::Pad", "PartDesign::Pocket")
HOLE_TYPE = "PartDesign::Hole"
FILLET_TYPE = "PartDesign::Fillet"
SUPPORTED = EXTRUDE_TYPES + (HOLE_TYPE, FILLET_TYPE)

# Same words as the Properties tab (bruceworx_inspector.CURATED), used when it is missing.
LABELS = {
    ("PartDesign::Pad", "Length"): "Distance",
    ("PartDesign::Pad", "Length2"): "Distance 2",
    ("PartDesign::Pocket", "Length"): "Depth",
    ("PartDesign::Pocket", "Length2"): "Depth 2",
    ("PartDesign::Hole", "Depth"): "Depth",
    ("PartDesign::Hole", "Diameter"): "Diameter",
    ("PartDesign::Fillet", "Radius"): "Radius",
}
EXTENT_WORDS = {
    "UpToFace": "Up to face",
    "UpToFirst": "Up to first",
    "UpToLast": "Up to last",
    "UpToShape": "Up to shape",
    "ThroughAll": "Through all",
    "TwoLengths": "Two lengths",
}
HINT_SIDE = "{label}: double-click its end face to change the {field}."
HINT_NONE = "No on-canvas value for this face. Its properties are on the Properties tab."
KEYS_HINT = "Shift finer · Tab next · Esc undo"


# ----- pure helpers (unit tested) ---------------------------------------------------------


def field_label(type_id, prop):
    """The Properties tab's word for a property ("Distance" for a pad's Length)."""
    try:
        import bruceworx_inspector

        for _group, name, label in bruceworx_inspector.CURATED.get(type_id, ()):
            if name == prop:
                return label
    except Exception:
        pass
    return LABELS.get((type_id, prop), prop)


def transaction_name(label, field):
    """"Boss: Distance", as the Properties tab names its steps."""
    return f"{label}: {field}"


def extent_word(type_name):
    return EXTENT_WORDS.get(type_name, type_name)


def extrude_sides(side_type, type1, type2="Length", midplane=False):
    """{"front": (prop, sign, mult) or reason, "back": ...} for a Pad or Pocket.

    The front cap is where the extrusion direction points; the back one is the
    sketch side (one side), the second side (two sides) or the mirror (symmetric).
    ``mult`` is how much the value changes per mm the cap moves.
    """
    if midplane and side_type == "One side":
        side_type = "Symmetric"  # files from before SideType
    if type1 == "TwoLengths":
        side_type, type1, type2 = "Two sides", "Length", "Length"
    front = ("Length", 1, 1.0) if type1 == "Length" else extent_word(type1)
    if side_type == "Symmetric":
        back = ("Length", -1, 2.0) if type1 == "Length" else front
        front = ("Length", 1, 2.0) if type1 == "Length" else front
    elif side_type == "Two sides":
        back = ("Length2", -1, 1.0) if type2 == "Length" else extent_word(type2)
    else:
        back = None
    return {"front": front, "back": back}


def snap_step(view_px, px_per_mm, fraction=STEP_FRACTION):
    """The zoom's step: the power of ten under ``fraction`` of the view, in mm."""
    if px_per_mm <= 0 or view_px <= 0:
        return 1.0
    length = fraction * view_px / px_per_mm
    return 10.0 ** math.floor(math.log10(length))


def step_for(mode, auto_step, grid=None, fine=False):
    """The snap step in use; 0 means free."""
    if mode == SNAP_FREE:
        step = 0.0
    elif mode == SNAP_GRID and grid:
        step = float(grid)
    else:
        step = auto_step
    return step / 10.0 if fine and step > 0 else step


def round_to(value, step):
    if step <= 0:
        return value
    return round(round(value / step) * step, 9)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def near_parallel(u, d, limit_deg=NEAR_PARALLEL_DEG):
    """True when the arrow ``u`` points almost along the view ray ``d`` (both unit)."""
    b = _dot(u, d)
    return 1.0 - b * b < math.sin(math.radians(limit_deg)) ** 2


def ray_param(p0, u, r0, d, limit_deg=NEAR_PARALLEL_DEG):
    """t of the point p0 + t u nearest the ray r0 + s d (u, d unit); None when near parallel."""
    if near_parallel(u, d, limit_deg):
        return None
    b = _dot(u, d)
    w = _sub(r0, p0)
    return _dot(w, (u[0] - b * d[0], u[1] - b * d[1], u[2] - b * d[2])) / (1.0 - b * b)


def face_snap(dt, targets, px_per_mm, held=None, catch_px=CATCH_PX, release_px=RELEASE_PX):
    """(snapped dt or None, held target): snap to a face height, kept until 10 px away."""
    if held is not None:
        if abs(dt - held) * px_per_mm <= release_px:
            return held, held
        held = None
    if targets:
        best = min(targets, key=lambda t: abs(dt - t))
        if abs(dt - best) * px_per_mm <= catch_px:
            return best, best
    return None, None


def drag_value(v0, k, dt, step, snapped_dt=None, minimum=None):
    """The value for a drag of ``dt`` mm along the arrow (value grows k per mm), never below one step."""
    if snapped_dt is not None:
        value = v0 + k * snapped_dt
    else:
        value = round_to(v0 + k * dt, step)
    floor = minimum if minimum is not None else max(step, MIN_VALUE)
    return max(value, floor)


def shown_offset(value, v0, k):
    """Where the arrow sits for ``value``: mm along it from the start."""
    return (value - v0) / k if k else 0.0


def fillet_factor(alpha):
    """Radius change per mm along the face, for faces meeting at the interior angle ``alpha``."""
    return math.tan(alpha / 2.0)


def fillet_offset(radius, alpha):
    """Distance from the sharp edge to where the fillet meets each face."""
    return radius / math.tan(alpha / 2.0)


def preview_due(times_ms, threshold_ms, window=3):
    """True when the recent recomputes average more than ``threshold_ms`` (0 or less: never)."""
    if threshold_ms <= 0 or not times_ms:
        return False
    recent = list(times_ms)[-window:]
    return sum(recent) / len(recent) > threshold_ms


def decimals_for(step):
    if step <= 0:
        return 2
    return max(0, min(4, int(math.ceil(-math.log10(step) - 1e-9))))


def format_number(value, step):
    text = "%.*f" % (decimals_for(step), value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_value(value, step, unit="mm", factor=1.0):
    """"20 mm", "20.5 mm": as many decimals as the step needs."""
    return "%s %s" % (format_number(value / (factor or 1.0), step / (factor or 1.0)), unit)


def parse_entry(text):
    """("expr", text), ("value", text) or None for what was typed in the field."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("="):
        expr = text[1:].strip()
        return ("expr", expr) if expr else None
    return ("value", text)


def clean_path(sub):
    """(object path tokens, element) of a subname; mapped element names are dropped."""
    sub = sub or ""
    path, _sep, last = sub.rpartition(".")
    if ";" in path:
        path = path[: path.index(";")].rstrip(".")
    tokens = [t for t in path.split(".") if t] if path else []
    if not (last.rstrip("0123456789") in ("Face", "Edge", "Vertex") and last[-1:].isdigit()):
        if last:
            tokens.append(last)
        last = ""
    return tokens, last


def feature_subname(sub, name):
    """"Body.Fillet.Face5" -> "Body.Pad001.": the selection path of another feature of the body."""
    tokens, _element = clean_path(sub)
    tokens = tokens[:-1] + [name] if tokens else [name]
    return ".".join(tokens) + "."


def body_subname(sub, body_name):
    """"Body." from "Body.Fillet.Face5"; None when the path does not pass the body."""
    tokens, _element = clean_path(sub)
    if body_name in tokens:
        return ".".join(tokens[: tokens.index(body_name) + 1]) + "."
    return None


def viewport_to_qt(x, y, height, dpr=1.0):
    """Coin viewport pixels (device, from the bottom) to a Qt position (logical, from the top)."""
    dpr = dpr or 1.0
    return x / dpr, height - 1 - y / dpr


def next_index(index, count, back=False):
    if count <= 0:
        return 0
    return (index + (-1 if back else 1)) % count


def bar_place(view_w, view_h, w, h, above=None, gap=8, margin=12):
    """Top-left of the command bar: centred above ``above`` (x, y, w, h), else at the bottom."""
    if above is not None:
        ax, ay, aw, _ah = above
        x = ax + (aw - w) // 2
        y = ay - gap - h
    else:
        x = (view_w - w) // 2
        y = view_h - margin - h
    return int(max(0, min(x, view_w - w))), int(max(0, y))


class LatestWins:
    """At most one recompute at a time; a value pushed meanwhile replaces the pending one.

    ``apply(value)`` does the work and returns its time in ms; ``post(fn)`` runs
    ``fn`` later (QTimer.singleShot(0, fn) in FreeCAD).
    """

    def __init__(self, apply, post):
        self.apply = apply
        self.post = post
        self.pending = None
        self.busy = False
        self.scheduled = False
        self.times = []
        self.applied = []

    def push(self, value):
        self.pending = value
        if not self.busy and not self.scheduled:
            self.scheduled = True
            self.post(self.run)

    def run(self):
        self.scheduled = False
        if self.busy or self.pending is None:
            return
        value, self.pending = self.pending, None
        self.busy = True
        try:
            ms = self.apply(value)
        finally:
            self.busy = False
        self.applied.append(value)
        if ms is not None:
            self.times.append(ms)
        if self.pending is not None and not self.scheduled:
            self.scheduled = True
            self.post(self.run)

    def flush(self):
        """Apply what is pending now (on release)."""
        if self.pending is not None and not self.busy:
            self.scheduled = False
            self.run()


# ----- handles: where the arrow goes, from the feature (FreeCAD, no GUI) -------------------


def _vec(v):
    import FreeCAD

    return FreeCAD.Vector(v[0], v[1], v[2]) if not hasattr(v, "x") else FreeCAD.Vector(v.x, v.y, v.z)


def _unit(v):
    v = _vec(v)
    if v.Length < 1e-12:
        return v
    return v.normalize()


def _tup(v):
    return (v.x, v.y, v.z)


class Handle:
    """One value of a feature and its arrow, in global coordinates.

    ``base``: where the arrow starts (on the face that moves); ``u``: its unit
    direction; ``k``: how much the value grows per mm along it; ``moving``:
    polylines that move with the value (the face, the fillet's line);
    ``reference``: polylines drawn dashed where things started.
    """

    def __init__(self, obj, prop, base=None, u=None, k=1.0, moving=(), reference=(), snaps=(), seed=None,
                 reason="", circle=None):
        self.obj = obj
        self.prop = prop
        self.label = field_label(obj.TypeId, prop)
        self.base = base
        self.u = u
        self.k = k
        self.moving = [list(p) for p in moving]
        self.reference = [list(p) for p in reference]
        self.snaps = list(snaps)
        self.seed = dict(seed or {})
        self.reason = reason  # why there is no arrow ("Up to face")
        self.circle = circle  # (centre, e1, e2) for a diameter
        self.v0 = self.value()
        if not reason and expression_of(obj, prop):
            self.reason = "= " + expression_of(obj, prop)

    @property
    def draggable(self):
        return not self.reason and self.base is not None and self.u is not None

    def value(self):
        value = getattr(self.obj, self.prop, None)
        return float(getattr(value, "Value", value or 0.0))

    def outline(self, value):
        """The moving polylines for ``value``."""
        if self.circle is not None:
            centre, e1, e2 = self.circle
            r = value / 2.0
            pts = [centre + e1 * (r * math.cos(a)) + e2 * (r * math.sin(a))
                   for a in (2 * math.pi * i / 48 for i in range(49))]
            return [pts]
        if self.u is None:
            return []
        shift = self.u * shown_offset(value, self.v0, self.k)
        return [[p + shift for p in line] for line in self.moving]


def expression_of(obj, prop):
    for path, expr in getattr(obj, "ExpressionEngine", None) or []:
        if path == prop:
            return expr
    return ""


def _read_only(obj, prop):
    try:
        return "ReadOnly" in obj.getPropertyStatus(prop)
    except Exception:
        return False


def _matrix_map(matrix):
    """(point map, direction map) for a placement matrix."""
    def point(v):
        return matrix.multVec(_vec(v))

    def direction(v):
        return _unit(matrix.multVec(_vec(v)) - matrix.multVec(_vec((0, 0, 0))))

    return point, direction


def _polylines(shape, deflection):
    out = []
    for wire in getattr(shape, "Wires", None) or []:
        try:
            out.append(list(wire.discretize(Deflection=deflection)))
        except Exception:
            continue
    if not out:
        for edge in getattr(shape, "Edges", None) or []:
            try:
                out.append(list(edge.discretize(Deflection=deflection)))
            except Exception:
                continue
    return out


def _deflection(shape):
    try:
        return max(1e-3, shape.BoundBox.DiagonalLength * 0.002)
    except Exception:
        return 0.05


def _profile_plane(obj):
    """(centre, unit normal) of a sketch-based feature's profile, body-local."""
    prof, subs = obj.Profile[0], list(obj.Profile[1] or [])
    shape = prof.Shape
    if subs:
        try:
            shape = prof.Shape.getElement(subs[0])
        except Exception:
            pass
    centre = shape.BoundBox.Center
    try:
        normal = _unit(shape.findPlane().Axis)
    except Exception:
        normal = prof.Placement.Rotation.multVec(_vec((0, 0, 1)))
    return centre, normal


def _planar_heights(shape, base, u, skip=1e-6):
    """Distances along u from base to the planar faces of ``shape`` facing along u."""
    out = []
    for face in getattr(shape, "Faces", None) or []:
        try:
            if face.Surface.TypeId != "Part::GeomPlane":
                continue
            n = _unit(face.Surface.Axis)
            if abs(n.dot(u)) < 0.999:
                continue
            t = (face.CenterOfMass - base).dot(u)
        except Exception:
            continue
        if abs(t) > skip and all(abs(t - o) > 1e-6 for o in out):
            out.append(t)
    return out


def _tip_shape(obj):
    body = obj.getParentGeoFeatureGroup()
    tip = getattr(body, "Tip", None)
    return getattr(tip, "Shape", None) or obj.Shape


def extrude_handles(obj, matrix, seed):
    """Pad/Pocket handles: the cap on ``seed["side"]`` ("front"/"back").

    ``seed["ref"]`` is a body-local point on that cap (the double-clicked face's
    centre) and ``seed["rel"]`` its outline relative to that point; both move
    with the cap. Without them the cap's own face of the feature is used.
    """
    point, direction = _matrix_map(matrix)
    centre, normal = _profile_plane(obj)
    e = _unit(obj.Direction)
    if obj.Reversed:
        e = e * -1
    along = abs(e.dot(normal)) or 1.0
    travel = 1.0 / along if getattr(obj, "AlongSketchNormal", False) else 1.0
    sides = extrude_sides(str(obj.SideType), str(obj.Type), str(getattr(obj, "Type2", "Length")),
                          bool(getattr(obj, "Midplane", False)))
    side = seed.get("side", "front")
    spec = sides.get(side)
    if spec is None:
        return []
    if isinstance(spec, str):
        prop = "Length2" if side == "back" and str(obj.SideType) == "Two sides" else "Length"
        return [Handle(obj, prop, seed=seed, reason=spec)]
    prop, sign, mult = spec
    value = float(getattr(obj, prop).Value)
    cap_distance = value / mult * travel  # mm along e from the profile to the cap
    cap = centre + e * (sign * cap_distance)
    u = e * sign
    ref, rel = seed.get("ref"), seed.get("rel")
    if ref is None:
        face = _cap_face(obj.Shape, cap, normal)
        ref = face.CenterOfMass if face is not None else cap
        rel = [[p - ref for p in line] for line in _polylines(face, _deflection(face))] if face else []
        seed = dict(seed, ref=ref, rel=rel)
    # keep the in-plane position, move along e onto the cap plane
    base_l = ref + e * ((cap - ref).dot(normal) / (e.dot(normal) or 1.0))
    moving = [[point(base_l + r) for r in line] for line in rel or []]
    k = mult * along if getattr(obj, "AlongSketchNormal", False) else mult
    snaps = _planar_heights(_tip_shape(obj), base_l, u)
    handle = Handle(obj, prop, point(base_l), direction(u), k=k, moving=moving, snaps=snaps, seed=seed)
    handle.reference = [list(line) for line in handle.outline(handle.v0)]
    if _read_only(obj, prop):
        handle.reason = handle.reason or "read-only"
    return [handle]


def _cap_face(shape, cap, normal):
    best, area = None, -1.0
    for face in getattr(shape, "Faces", None) or []:
        try:
            if face.Surface.TypeId != "Part::GeomPlane" or abs(_unit(face.Surface.Axis).dot(normal)) < 0.999:
                continue
            if abs((face.CenterOfMass - cap).dot(normal)) > PLANE_TOL * max(1.0, shape.BoundBox.DiagonalLength):
                continue
        except Exception:
            continue
        if face.Area > area:
            best, area = face, face.Area
    return best


def extrude_seed(obj, face_local):
    """The side of a picked face of a Pad/Pocket, or None for a side face or the sketch face."""
    centre, normal = _profile_plane(obj)
    try:
        if face_local.Surface.TypeId != "Part::GeomPlane":
            return None
        n = _unit(face_local.Surface.Axis)
        com = face_local.CenterOfMass
    except Exception:
        return None
    if abs(n.dot(normal)) < 0.999:
        return None
    e = _unit(obj.Direction)
    if obj.Reversed:
        e = e * -1
    along = abs(e.dot(normal)) or 1.0
    travel = 1.0 / along if getattr(obj, "AlongSketchNormal", False) else 1.0
    sides = extrude_sides(str(obj.SideType), str(obj.Type), str(getattr(obj, "Type2", "Length")),
                          bool(getattr(obj, "Midplane", False)))
    tol = PLANE_TOL * max(1.0, obj.Shape.BoundBox.DiagonalLength)
    for side in ("front", "back"):
        spec = sides.get(side)
        if spec is None:
            continue
        if isinstance(spec, str):
            # no length on this side: a face on it still names the feature's extent
            continue
        prop, sign, mult = spec
        cap = centre + e * (sign * float(getattr(obj, prop).Value) / mult * travel)
        if abs((com - cap).dot(normal)) <= tol:
            rel = [[p - com for p in line] for line in _polylines(face_local, _deflection(face_local))]
            return {"side": side, "ref": com, "rel": rel}
    if any(isinstance(s, str) for s in sides.values()):
        for side, spec in sides.items():
            if isinstance(spec, str):
                return {"side": side}
    return None


def hole_handles(obj, matrix, seed):
    """Hole: depth (DepthType Dimension) down the axis, then the diameter out to the rim."""
    point, direction = _matrix_map(matrix)
    prof = obj.Profile[0]
    _c, normal = _profile_plane(obj)
    centres = []
    for edge in prof.Shape.Edges:
        curve = getattr(edge, "Curve", None)
        if getattr(curve, "TypeId", "") == "Part::GeomCircle":
            if all((curve.Center - c).Length > 1e-6 for c in centres):
                centres.append(curve.Center)
    if not centres:
        centres = [v.Point for v in prof.Shape.Vertexes]
    if not centres:
        return []
    index = min(max(0, seed.get("hole", 0)), len(centres) - 1)
    seed = dict(seed, hole=index)
    top = centres[index]
    drill = normal * (1 if obj.Reversed else -1)
    out = []
    depth = float(obj.Depth.Value)
    if str(obj.DepthType) == "Dimension":
        bottom = top + drill * depth
        e1 = _perpendicular(normal)
        e2 = normal.cross(e1)
        r = float(obj.Diameter.Value) / 2.0
        ring = [bottom + e1 * (r * math.cos(a)) + e2 * (r * math.sin(a)) for a in
                (2 * math.pi * i / 48 for i in range(49))]
        h = Handle(obj, "Depth", point(bottom), direction(drill), moving=[[point(p) for p in ring]],
                   snaps=_planar_heights(_tip_shape(obj), bottom, drill), seed=seed)
        h.reference = [list(line) for line in h.outline(h.v0)]
        out.append(h)
    else:
        out.append(Handle(obj, "Depth", seed=seed, reason=extent_word(str(obj.DepthType))))
    e1 = _perpendicular(normal)
    e2 = normal.cross(e1)
    r = float(obj.Diameter.Value) / 2.0
    rim = top + e1 * r
    reason = "set by the thread size" if _read_only(obj, "Diameter") else ""
    h = Handle(obj, "Diameter", point(rim), direction(e1), k=2.0, seed=seed, reason=reason,
               circle=(point(top), direction(e1), direction(e2)))
    h.reference = [list(line) for line in h.outline(h.v0)]
    out.append(h)
    return out


def _perpendicular(n):
    import FreeCAD

    other = FreeCAD.Vector(1, 0, 0) if abs(n.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
    return _unit(n.cross(other).cross(n) * -1) if n.cross(other).Length > 1e-9 else FreeCAD.Vector(1, 0, 0)


def hole_seed(obj, face_local):
    """The hole nearest a picked face (its axis or centre)."""
    prof = obj.Profile[0]
    centres = []
    for edge in prof.Shape.Edges:
        curve = getattr(edge, "Curve", None)
        if getattr(curve, "TypeId", "") == "Part::GeomCircle":
            if all((curve.Center - c).Length > 1e-6 for c in centres):
                centres.append(curve.Center)
    if not centres or face_local is None:
        return {"hole": 0}
    _c, normal = _profile_plane(obj)
    try:
        surf = face_local.Surface
        ref = surf.Center if hasattr(surf, "Center") else face_local.CenterOfMass
    except Exception:
        ref = face_local.CenterOfMass

    def off_axis(c):
        d = ref - c
        return (d - normal * d.dot(normal)).Length

    return {"hole": min(range(len(centres)), key=lambda i: off_axis(centres[i]))}


def fillet_handles(obj, matrix, seed, view_dir=None):
    """Fillet radius: an arrow along a face next to the filleted edge, from the edge to the tangent line."""
    import Part

    point, direction = _matrix_map(matrix)
    base_obj, names = obj.Base[0], list(obj.Base[1] or [])
    shape = base_obj.Shape
    if getattr(obj, "UseAllEdges", False) or not names:
        names = ["Edge%d" % (i + 1) for i in range(len(shape.Edges))]
    name = seed.get("edge") if seed.get("edge") in names else names[0]
    seed = dict(seed, edge=name)
    try:
        edge = shape.getElement(name)
        faces = shape.ancestorsOfType(edge, Part.Face)
    except Exception:
        return []
    if len(faces) < 2:
        return []
    a, b = edge.ParameterRange
    mid_param = (a + b) / 2.0
    mid = edge.valueAt(mid_param)
    tangent = _unit(edge.tangentAt(mid_param))
    eps = max(1e-3, min(0.05, edge.Length * 0.01))
    dirs = []
    for face in faces[:2]:
        try:
            uv = face.Surface.parameter(mid)
            nrm = _unit(face.normalAt(*uv))
        except Exception:
            nrm = _unit(face.normalAt(0, 0))
        d = _unit(nrm.cross(tangent))
        if face.distToShape(Part.Vertex(mid + d * eps))[0] > eps * 0.5:
            d = d * -1
        dirs.append(d)
    alpha = dirs[0].getAngle(dirs[1])
    if alpha < 1e-3 or alpha > math.pi - 1e-3:
        return [Handle(obj, "Radius", seed=seed, reason="faces meet flat")]
    pick = seed.get("face")
    if pick is None:
        pick = 0
        if view_dir is not None:
            local_view = _vec(view_dir)

            def screen_len(d):
                g = direction(d)
                return (g - local_view * g.dot(local_view)).Length

            pick = max((0, 1), key=lambda i: screen_len(dirs[i]))
        seed = dict(seed, face=pick)
    u = dirs[pick]
    r = float(obj.Radius.Value)
    s = fillet_offset(r, alpha)
    base_l = mid + u * s
    pts = list(edge.discretize(Deflection=_deflection(edge)))
    handle = Handle(obj, "Radius", point(base_l), direction(u), k=fillet_factor(alpha),
                    moving=[[point(p + u * s) for p in pts]], reference=[[point(p) for p in pts]], seed=seed)
    return [handle]


def fillet_seed(obj, face_local):
    """The filleted edge nearest a picked face."""
    import Part

    base_obj, names = obj.Base[0], list(obj.Base[1] or [])
    shape = base_obj.Shape
    if getattr(obj, "UseAllEdges", False) or not names:
        names = ["Edge%d" % (i + 1) for i in range(len(shape.Edges))]
    if face_local is None or not names:
        return {"edge": names[0] if names else None}
    probe = Part.Vertex(face_local.CenterOfMass)
    best, dist = names[0], None
    for name in names:
        try:
            d = shape.getElement(name).distToShape(probe)[0]
        except Exception:
            continue
        if dist is None or d < dist:
            best, dist = name, d
    return {"edge": best}


def handles_for(obj, matrix, seed=None, view_dir=None):
    """All handles of a supported feature (Tab cycles them); [] when none."""
    seed = dict(seed or {})
    type_id = getattr(obj, "TypeId", "")
    try:
        if type_id in EXTRUDE_TYPES:
            return extrude_handles(obj, matrix, seed)
        if type_id == HOLE_TYPE:
            return hole_handles(obj, matrix, seed)
        if type_id == FILLET_TYPE:
            return fillet_handles(obj, matrix, seed, view_dir)
    except Exception as exc:  # pragma: no cover - geometry the builders do not know
        import FreeCAD

        FreeCAD.Console.PrintLog(f"On-canvas editing: {obj.Label}: {exc}\n")
    return []


def seed_for(obj, face_local):
    """The seed for a double-clicked face (body-local), or None when the face has no handle."""
    type_id = getattr(obj, "TypeId", "")
    if type_id in EXTRUDE_TYPES:
        return extrude_seed(obj, face_local) if face_local is not None else {"side": "front"}
    if type_id == HOLE_TYPE:
        return hole_seed(obj, face_local)
    if type_id == FILLET_TYPE:
        return fillet_seed(obj, face_local)
    return None


def body_matrix(obj, top=None, sub=""):
    """Body-local to global: through the picked path when there is one."""
    import FreeCAD

    body = obj.getParentGeoFeatureGroup()
    if body is None:
        return FreeCAD.Matrix()
    if top is not None and top is not body:
        path = body_subname(sub, body.Name)
        if path:
            try:
                found = top.getSubObject(path, retType=1, matrix=FreeCAD.Matrix())
                if isinstance(found, tuple) and len(found) == 2:
                    return found[1]
            except Exception:
                pass
    try:
        return body.getGlobalPlacement().toMatrix()
    except Exception:
        return body.Placement.toMatrix()


# ----- GUI ---------------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def enabled():
    return bool(_prefs().GetBool(PREF_NAME, True))


def preview_threshold():
    return int(_prefs().GetInt(PREVIEW_PREF, DEFAULT_PREVIEW_MS))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _cpp(obj):
    try:
        import shiboken6

        return shiboken6.getCppPointer(obj)[0]
    except Exception:
        return id(obj)


def _obj_alive(obj):
    try:
        return obj is not None and obj.Document is not None and obj.Document.getObject(obj.Name) is obj
    except Exception:
        return False


def accent_rgb():
    """The accent (Themes/ThemeAccentColor1) as 0..1 floats."""
    import FreeCAD

    packed = FreeCAD.ParamGet(THEMES_PATH).GetUnsigned("ThemeAccentColor1", 0)
    if packed:
        return ((packed >> 24) & 255) / 255.0, ((packed >> 16) & 255) / 255.0, ((packed >> 8) & 255) / 255.0
    return 0.0, 0.478, 1.0


def _hex_rgb(text):
    text = text.lstrip("#")
    return tuple(int(text[i: i + 2], 16) / 255.0 for i in (0, 2, 4))


def _status(text, ms=5000):
    try:
        import FreeCADGui

        FreeCADGui.getMainWindow().statusBar().showMessage(text, ms)
    except Exception:
        pass


def _graphics_viewport(view_widget):
    from PySide import QtWidgets

    gv = view_widget.findChild(QtWidgets.QGraphicsView)
    return gv.viewport() if gv is not None else None


def _py_view(view_widget):
    try:
        import bruceworx_navpill

        return bruceworx_navpill._find_py_view(view_widget)
    except Exception:
        return None


def _theme_is_bruceworx():
    try:
        import bruceworx_navpill

        return bruceworx_navpill.is_bruceworx_theme(bruceworx_navpill._theme())
    except Exception:
        return False


def _dialog_open():
    import FreeCADGui

    try:
        if FreeCADGui.Control.activeDialog():
            return True
    except Exception:
        pass
    try:
        gdoc = FreeCADGui.ActiveDocument
        return gdoc is not None and gdoc.getInEdit() is not None
    except Exception:
        return False


FALLBACK_QSS = """
#BruceWorxCanvasField, #BruceWorxCanvasBar { background-color: palette(base); border: 1px solid palette(mid);
  border-radius: 8px; }
#BruceWorxCanvasField QLineEdit { border: 1px solid palette(highlight); border-radius: 5px; padding: 1px 6px;
  background-color: palette(base); color: palette(text); }
#BruceWorxCanvasField QLabel, #BruceWorxCanvasBar QLabel { color: palette(text); background: transparent; }
#BruceWorxCanvasBar QToolButton { border: none; border-radius: 5px; padding: 3px 8px; color: palette(text); }
#BruceWorxCanvasBar QToolButton:hover { background-color: palette(alternate-base); }
#BruceWorxCanvasBar QToolButton#BruceWorxCanvasDone { background-color: palette(highlight);
  color: palette(highlighted-text); }
"""

_classes = None


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtGui, QtWidgets

    class Field(QtWidgets.QFrame):
        """The value card at the arrow tip: the value, then the start value and the step."""

        def __init__(self, view, session):
            super().__init__(view)
            self.session = session
            self.setObjectName(FIELD_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 5, 6, 4)
            layout.setSpacing(2)
            self.edit = QtWidgets.QLineEdit(self)
            self.edit.setObjectName("BruceWorxCanvasValue")
            self.edit.setAlignment(QtCore.Qt.AlignCenter)
            self.edit.setMinimumWidth(92)
            self.edit.installEventFilter(self)
            self.edit.returnPressed.connect(session.typed)
            layout.addWidget(self.edit)
            self.note = QtWidgets.QLabel(self)
            self.note.setObjectName("BruceWorxCanvasNote")
            self.note.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(self.note)

        def eventFilter(self, obj, event):
            kind = event.type()
            if kind == QtCore.QEvent.ShortcutOverride and event.key() == QtCore.Qt.Key_Escape:
                event.accept()
                return True
            if kind == QtCore.QEvent.KeyPress:
                key = event.key()
                if key == QtCore.Qt.Key_Escape:
                    QtCore.QTimer.singleShot(0, self.session.escape)
                    return True
                if key in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
                    back = key == QtCore.Qt.Key_Backtab or bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                    QtCore.QTimer.singleShot(0, lambda: self.session.next_handle(back))
                    return True
            return False

    class Bar(QtWidgets.QFrame):
        """The command bar: what is edited, the snap step, keys, Edit... and Done."""

        def __init__(self, view, session):
            super().__init__(view)
            self.setObjectName(BAR_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(10, 4, 4, 4)
            layout.setSpacing(8)
            self.title = QtWidgets.QLabel(self)
            self.title.setObjectName("BruceWorxCanvasTitle")
            layout.addWidget(self.title)
            self.snap = QtWidgets.QToolButton(self)
            self.snap.setObjectName("BruceWorxCanvasSnap")
            self.snap.setFocusPolicy(QtCore.Qt.NoFocus)
            self.snap.setToolTip("Snap step: follows the zoom, free, or the sketch grid. Click to change.")
            self.snap.clicked.connect(session.cycle_snap)
            layout.addWidget(self.snap)
            self.keys = QtWidgets.QLabel(KEYS_HINT, self)
            self.keys.setObjectName("BruceWorxCanvasKeys")
            layout.addWidget(self.keys)
            self.edit = QtWidgets.QToolButton(self)
            self.edit.setObjectName("BruceWorxCanvasEdit")
            self.edit.setText("Edit…")
            self.edit.setToolTip("Open the feature's task dialog")
            self.edit.setFocusPolicy(QtCore.Qt.NoFocus)
            self.edit.clicked.connect(session.open_dialog)
            layout.addWidget(self.edit)
            self.done = QtWidgets.QToolButton(self)
            self.done.setObjectName("BruceWorxCanvasDone")
            self.done.setText("Done")
            self.done.setFocusPolicy(QtCore.Qt.NoFocus)
            self.done.clicked.connect(lambda: QtCore.QTimer.singleShot(0, session.finish_edit))
            layout.addWidget(self.done)

    class Session(QtCore.QObject):
        """One on-canvas edit: a feature, its handles, the arrow, the field and the bar in one view."""

        def __init__(self, view_widget, py_view, obj, handles, index=0, select_path=None):
            super().__init__()
            from pivy import coin

            self.coin = coin
            self.view_widget = view_widget
            self.view = py_view
            self.obj = obj
            self.doc = obj.Document
            self.handles = handles
            self.index = index
            self.select_path = select_path
            self.steps = []  # names of the undo steps this edit made
            self.dragging = False
            self.aborted = False
            self.drag_moved = False
            self.preview = False
            self.preview_state = None
            self.snap_mode = _manager.snap_mode if _manager else SNAP_AUTO
            self.shift = False
            self.held = None
            self.value = None
            self.closed = False
            self.pointer = None  # Coin pixels of the last mouse event in the view
            self.root = None
            self.dragger = None
            self.callbacks = []
            self.throttle = LatestWins(self._apply, lambda fn: QtCore.QTimer.singleShot(0, fn))
            self.field = Field(view_widget, self)
            self.bar = Bar(view_widget, self)
            self.apply_theme()
            self._build_scene()
            self.place_handle()
            self.field.show()
            self.bar.show()
            self.follow = QtCore.QTimer(self)
            self.follow.setInterval(60)
            self.follow.timeout.connect(self.tick)
            self.follow.start()
            self._select()
            self.field.edit.setFocus()
            self.field.edit.selectAll()

        # --- the handle in use
        @property
        def handle(self):
            return self.handles[self.index] if self.handles else None

        def apply_theme(self):
            sheet = "" if _theme_is_bruceworx() else FALLBACK_QSS
            for w in (self.field, self.bar):
                w.setStyleSheet(sheet)

        # --- scene graph
        def _field(self, node, name, kind):
            coin = self.coin
            return coin.cast(node.getField(coin.SbName(name)), kind)

        def _build_scene(self):
            coin = self.coin
            self.root = coin.SoSeparator()
            top = coin.SoAnnotation()
            self.root.addChild(top)
            lines = coin.SoSeparator()
            pick = coin.SoPickStyle()
            pick.style = coin.SoPickStyle.UNPICKABLE
            lines.addChild(pick)
            light = coin.SoLightModel()
            light.model = coin.SoLightModel.BASE_COLOR
            lines.addChild(light)
            self.ref_node = self._line_group(lines, (0.55, 0.55, 0.58), 1.5, 0xF0F0)
            self.moving_node = self._line_group(lines, accent_rgb(), 2.5, 0xFFFF)
            top.addChild(lines)
            kit = coin.SoType.fromName(coin.SbName("SoLinearDraggerContainer")).createInstance()
            kit = coin.cast(kit, "SoInteractionKit")
            self.kit = kit
            self.dragger = coin.cast(kit.getPart(coin.SbName("dragger"), True), "SoDragger")
            accent = accent_rgb()
            self._field(self.dragger, "labelVisible", "SoSFBool").setValue(False)
            self._field(self.dragger, "color", "SoSFColor").setValue(*accent)
            self._field(self.dragger, "activeColor", "SoSFColor").setValue(*_hex_rgb(ACTIVE_COLOR))
            self._field(kit, "color", "SoSFColor").setValue(*accent)
            # tiny increment: the dragger reports the raw move; the snapping is ours
            self._field(self.dragger, "translationIncrement", "SoSFDouble").setValue(1e-4)
            top.addChild(kit)
            self.callbacks = [self.on_start, self.on_motion, self.on_finish]
            for kind, fn in zip(("addStartCallback", "addMotionCallback", "addFinishCallback"), self.callbacks):
                self.view.addDraggerCallback(self.dragger, kind, fn)
            self.view.getSceneGraph().addChild(self.root)

        def _line_group(self, parent, rgb, width, pattern):
            coin = self.coin
            sep = coin.SoSeparator()
            color = coin.SoBaseColor()
            color.rgb = rgb
            sep.addChild(color)
            style = coin.SoDrawStyle()
            style.lineWidth = width
            style.linePattern = pattern
            sep.addChild(style)
            coords = coin.SoCoordinate3()
            sep.addChild(coords)
            lineset = coin.SoLineSet()
            sep.addChild(lineset)
            parent.addChild(sep)
            return coords, lineset

        def _set_lines(self, node, polylines):
            coords, lineset = node
            pts = [(p.x, p.y, p.z) for line in polylines for p in line]
            counts = [len(line) for line in polylines if line]
            coords.point.setNum(len(pts))
            lineset.numVertices.setNum(len(counts))
            if pts:
                coords.point.setValues(0, len(pts), pts)
                lineset.numVertices.setValues(0, len(counts), counts)

        def _remove_scene(self):
            if self.root is None:
                return
            try:
                for kind, fn in zip(("removeStartCallback", "removeMotionCallback", "removeFinishCallback"),
                                    self.callbacks):
                    self.view.removeDraggerCallback(self.dragger, kind, fn)
            except Exception:
                pass
            try:
                self.view.getSceneGraph().removeChild(self.root)
            except Exception:
                pass
            self.root = None

        # --- placing the arrow and the widgets
        def place_handle(self):
            h = self.handle
            coin = self.coin
            self.value = h.value() if h else None
            visible = bool(h and h.draggable)
            self._field(self.kit, "visible", "SoSFBool").setValue(visible)
            if visible:
                b, u = h.base, h.u
                self._field(self.kit, "translation", "SoSFVec3f").setValue(b.x, b.y, b.z)
                self._field(self.kit, "rotation", "SoSFRotation").setValue(
                    coin.SbRotation(coin.SbVec3f(0, 1, 0), coin.SbVec3f(u.x, u.y, u.z)))
                self._field(self.dragger, "translation", "SoSFVec3f").setValue(0, 0, 0)
            self.scale = 0.0
            self._rescale()
            self._set_lines(self.ref_node, h.reference if h else [])
            self._set_lines(self.moving_node, h.outline(self.value) if visible else [])
            self.update_texts()
            self.place_widgets()

        def _rescale(self):
            h = self.handle
            if h is None or not h.draggable:
                return
            coin = self.coin
            try:
                vv = self.view.getCameraNode().getViewVolume()
                g = vv.getWorldToScreenScale(coin.SbVec3f(h.base.x, h.base.y, h.base.z), ARROW_SCREEN)
            except Exception:
                return
            if self.scale and abs(g - self.scale) < 0.01 * self.scale:
                return
            self.scale = g
            self._field(self.dragger, "geometryScale", "SoSFVec3f").setValue(g, g, g)

        def _to_view(self, p):
            """A global point to a position in the view widget."""
            vp = _graphics_viewport(self.view_widget)
            if vp is None:
                return None
            x, y = self.view.getPointOnViewport(p)
            qx, qy = viewport_to_qt(x, y, vp.height(), vp.devicePixelRatioF())
            return vp.mapTo(self.view_widget, QtCore.QPoint(int(qx), int(qy)))

        def px_per_mm(self, p):
            try:
                up = _vec(self.view.getUpDirection())
                a = self.view.getPointOnViewport(p)
                b = self.view.getPointOnViewport(p + up)
                vp = _graphics_viewport(self.view_widget)
                dpr = vp.devicePixelRatioF() if vp is not None else 1.0
                return max(1e-6, math.hypot(b[0] - a[0], b[1] - a[1]) / dpr)
            except Exception:
                return 1.0

        def auto_step(self):
            h = self.handle
            vp = _graphics_viewport(self.view_widget)
            if h is None or h.base is None or vp is None:
                return 1.0
            return snap_step(min(vp.width(), vp.height()), self.px_per_mm(h.base))

        def grid_step(self):
            try:
                prof = self.obj.Profile[0]
                grid = prof.ViewObject.GridSize
                return float(getattr(grid, "Value", grid))
            except Exception:
                return None

        def step(self):
            return step_for(self.snap_mode, self.auto_step(), self.grid_step(), self.shift)

        def place_widgets(self):
            if not _alive(self.view_widget):
                return
            h = self.handle
            self.field.adjustSize()
            pos = None
            if h is not None and h.base is not None:
                offset = shown_offset(self.value, h.v0, h.k) if h.draggable else 0.0
                tip = h.base + h.u * (offset + ARROW_TIP * (self.scale or 0.0)) if h.u is not None else h.base
                pos = self._to_view(tip)
            if pos is None:
                pos = QtCore.QPoint(self.view_widget.width() // 2, self.view_widget.height() // 3)
            x = max(0, min(pos.x() + 14, self.view_widget.width() - self.field.width()))
            y = max(0, min(pos.y() - self.field.height() // 2, self.view_widget.height() - self.field.height()))
            self.field.move(x, y)
            self.field.raise_()
            self.bar.adjustSize()
            above = None
            for name in ("BruceWorxToast", "BruceWorxNavPill"):
                for child in self.view_widget.findChildren(QtWidgets.QFrame, name):
                    if child.isVisible() and child is not self.bar:
                        g = child.geometry()
                        above = (g.x(), g.y(), g.width(), g.height())
                        break
                if above is not None:
                    break
            bx, by = bar_place(self.view_widget.width(), self.view_widget.height(), self.bar.width(),
                               self.bar.height(), above)
            self.bar.move(bx, by)
            self.bar.raise_()

        def update_texts(self):
            h = self.handle
            if h is None:
                return
            step = self.step()
            unit, factor = "mm", 1.0
            try:
                import FreeCAD

                _text, factor, unit = FreeCAD.Units.Quantity(1.0, FreeCAD.Units.Length).getUserPreferred()
            except Exception:
                pass
            if not self.field.edit.hasFocus() or self.dragging:
                if h.reason:
                    self.field.edit.setText(h.reason)
                else:
                    self.field.edit.setText(format_value(self.value, step, unit, factor))
            self.field.edit.setReadOnly(bool(h.reason) and not h.reason.startswith("="))
            snap_word = {SNAP_FREE: "free", SNAP_GRID: "grid"}.get(self.snap_mode, "")
            step_text = format_value(step, step, unit, factor) if step > 0 else "free"
            if snap_word == "grid" and step > 0:
                step_text = "grid " + step_text
            if h.reason:
                self.field.note.setText(h.label)
            else:
                self.field.note.setText("from %s · step %s" % (format_value(h.v0, step, unit, factor), step_text))
            self.bar.title.setText("<b>%s</b> · %s" % (self.obj.Label, h.label))
            self.bar.snap.setText("Snap %s" % step_text)
            self.bar.snap.setVisible(h.draggable)
            keys = KEYS_HINT if len(self.handles) > 1 else KEYS_HINT.replace(" · Tab next", "")
            self.bar.keys.setText(keys if h.draggable else "Edit… to change the extent")

        def tick(self):
            """Follow the camera; leave when the view, document or feature went away."""
            if self.closed:
                return
            if not _alive(self.view_widget) or not _obj_alive(self.obj) or (_dialog_open() and not self.dragging):
                QtCore.QTimer.singleShot(0, lambda: end_session(quiet=True))
                return
            shift = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
            if shift != self.shift:
                self.shift = shift
                self.update_texts()
            self._rescale()
            self.place_widgets()

        def _select(self):
            """Select the feature so the Properties tab shows its values."""
            try:
                import FreeCADGui

                FreeCADGui.Selection.clearSelection()
                if self.select_path:
                    top, sub = self.select_path
                    FreeCADGui.Selection.addSelection(self.doc.Name, top, sub)
                else:
                    FreeCADGui.Selection.addSelection(self.obj)
            except Exception:
                pass

        # --- dragging
        def ray(self):
            # the pointer from the viewport's own mouse events (ViewFilter), else the cursor
            pos = self.pointer or self.view.getCursorPos()
            near, far = self.view.projectPointToLine(pos)
            return near, _unit(far - near), pos

        def on_start(self, *_args):
            h = self.handle
            if h is None or not h.draggable or self.closed:
                return
            self.dragging = True
            self.aborted = False
            self.drag_moved = True
            self.held = None
            self.start_value = h.value()
            self.ppm = self.px_per_mm(h.base)
            near, d, pos = self.ray()
            self.t0 = ray_param(_tup(h.base), _tup(h.u), _tup(near), _tup(d))
            self.y0 = pos[1]
            self.name = transaction_name(self.obj.Label, h.label)
            self.doc.openTransaction(self.name)
            self.throttle.times = []
            self.preview = False

        def on_motion(self, *_args):
            h = self.handle
            if not self.dragging or self.aborted or h is None:
                return
            near, d, pos = self.ray()
            t = ray_param(_tup(h.base), _tup(h.u), _tup(near), _tup(d))
            if t is None or self.t0 is None:
                # the arrow points at the eye: vertical pointer motion times the pixel size
                vp = _graphics_viewport(self.view_widget)
                dpr = vp.devicePixelRatioF() if vp is not None else 1.0
                dt = (pos[1] - self.y0) / dpr / self.ppm
            else:
                dt = t - self.t0
            base_dt = shown_offset(self.start_value, h.v0, h.k)
            step = self.step()
            snapped, self.held = face_snap(base_dt + dt, h.snaps, self.ppm, self.held)
            value = drag_value(h.v0, h.k, base_dt + dt, step, snapped)
            self.value = value
            self._field(self.dragger, "translation", "SoSFVec3f").setValue(
                0, shown_offset(value, h.v0, h.k) - base_dt, 0)
            self._set_lines(self.moving_node, h.outline(value))
            self.update_texts()
            self.place_widgets()
            if abs(value - h.value()) > 1e-9:
                self.throttle.push(value)

        def _apply(self, value):
            import time

            h = self.handle
            if h is None or self.aborted or not _obj_alive(self.obj):
                return None
            t0 = time.perf_counter()
            setattr(self.obj, h.prop, value)
            if self.preview:
                self.obj.recompute()
            else:
                self.doc.recompute()
            ms = (time.perf_counter() - t0) * 1000.0
            if not self.preview and preview_due(self.throttle.times + [ms], preview_threshold()):
                self._enter_preview()
            return ms

        def _enter_preview(self):
            """Slow model: show the edited feature alone, see-through, until release."""
            self.preview = True
            state = []
            try:
                body = self.obj.getParentGeoFeatureGroup()
                for o in [body] + list(getattr(body, "Group", [])):
                    vo = getattr(o, "ViewObject", None)
                    if vo is not None and hasattr(vo, "Visibility"):
                        state.append((o, vo.Visibility, getattr(vo, "Transparency", None)))
                vo = self.obj.ViewObject
                vo.Visibility = True
                if hasattr(vo, "Transparency"):
                    vo.Transparency = 60
            except Exception:
                pass
            self.preview_state = state
            _status("Preview: the features after %s update when you let go." % self.obj.Label)

        def _leave_preview(self):
            if not self.preview:
                return
            self.preview = False
            for o, visible, transparency in self.preview_state or []:
                try:
                    if transparency is not None:
                        o.ViewObject.Transparency = transparency
                    o.ViewObject.Visibility = visible
                except Exception:
                    pass
            self.preview_state = None

        def on_finish(self, *_args):
            if not self.dragging:
                return
            self.dragging = False
            if self.aborted:
                return
            h = self.handle
            self.throttle.flush()
            changed = False
            try:
                if h is not None and self.value is not None and abs(self.value - h.value()) > 1e-9:
                    setattr(self.obj, h.prop, self.value)
                was_preview = self.preview
                self._leave_preview()
                if was_preview or self.value is not None:
                    self.doc.recompute()
                changed = h is not None and abs(h.value() - self.start_value) > 1e-9
            finally:
                if changed:
                    self.doc.commitTransaction()
                    self.steps.append(self.name)
                else:
                    self.doc.abortTransaction()
            QtCore.QTimer.singleShot(0, self.rebuild)

        def abort_drag(self):
            """Esc during a drag: the value goes back, nothing is recorded."""
            self.aborted = True
            self._leave_preview()
            try:
                self.doc.abortTransaction()
                self.doc.recompute()
            except Exception:
                pass
            QtCore.QTimer.singleShot(0, self.rebuild)

        def rebuild(self):
            """New geometry after an edit: the handles start from where things are now."""
            if self.closed or not _obj_alive(self.obj):
                return
            h = self.handle
            seed = h.seed if h else {}
            fresh = handles_for(self.obj, self._matrix(), seed, self._view_dir())
            if fresh:
                index = min(self.index, len(fresh) - 1)
                self.handles, self.index = fresh, index
            self.place_handle()

        def _matrix(self):
            top, sub = self.select_path or (None, "")
            top_obj = self.doc.getObject(top) if top else None
            return body_matrix(self.obj, top_obj, sub)

        def _view_dir(self):
            try:
                return _vec(self.view.getViewDirection())
            except Exception:
                return None

        # --- typing, Tab, snap, leaving
        def typed(self):
            import FreeCAD

            h = self.handle
            if h is None or self.closed:
                return
            entry = parse_entry(self.field.edit.text())
            if entry is None:
                self.update_texts()
                return
            kind, text = entry
            if kind == "value" and h.reason.startswith("= "):
                from PySide import QtWidgets as W

                answer = W.QMessageBox.question(
                    self.view_widget, "Replace expression",
                    "%s is set by the expression %s. Replace it with %s?" % (h.label, h.reason[2:], text))
                if answer != W.QMessageBox.Yes:
                    self.update_texts()
                    return
            elif kind == "value" and h.reason:
                self.update_texts()
                return
            if kind == "value":
                try:
                    q = FreeCAD.Units.Quantity(text)
                    value = float(q.Value)
                except Exception:
                    _status("Not a length: %s" % text)
                    self.update_texts()
                    return
                if abs(value - h.value()) < 1e-9 and not h.reason:
                    QtCore.QTimer.singleShot(0, self.finish_edit)  # Enter on an unchanged field
                    return
                if value <= 0:
                    _status("%s must be more than zero." % h.label)
                    self.update_texts()
                    return
            name = transaction_name(self.obj.Label, h.label)
            self.doc.openTransaction(name)
            try:
                if kind == "expr":
                    self.obj.setExpression(h.prop, text)
                else:
                    if h.reason.startswith("= "):
                        self.obj.setExpression(h.prop, None)
                    setattr(self.obj, h.prop, value)
                self.doc.recompute()
            except Exception as exc:
                self.doc.abortTransaction()
                _status("%s: %s" % (name, exc))
                self.update_texts()
                return
            self.doc.commitTransaction()
            self.steps.append(name)
            self.field.edit.clearFocus()
            self.rebuild()
            self.field.edit.setFocus()
            self.field.edit.selectAll()

        def next_handle(self, back=False):
            if self.closed or self.dragging or len(self.handles) < 2:
                return
            self.index = next_index(self.index, len(self.handles), back)
            self.place_handle()
            self.field.edit.setFocus()
            self.field.edit.selectAll()

        def cycle_snap(self):
            modes = [m for m in SNAP_MODES if m != SNAP_GRID or self.grid_step()]
            i = modes.index(self.snap_mode) if self.snap_mode in modes else 0
            self.snap_mode = modes[(i + 1) % len(modes)]
            if _manager is not None:
                _manager.snap_mode = self.snap_mode
            self.update_texts()

        def escape(self):
            """Esc: during a drag the drag goes back; otherwise undo this edit's steps and leave."""
            if self.dragging:
                self.abort_drag()
                return
            for name in reversed(self.steps):
                try:
                    if self.doc.UndoNames and self.doc.UndoNames[0] == name:
                        self.doc.undo()
                except Exception:
                    break
            self.steps = []
            end_session()

        def finish_edit(self):
            end_session()

        def open_dialog(self):
            obj = self.obj
            end_session()
            try:
                import FreeCADGui

                FreeCADGui.getDocument(obj.Document.Name).setEdit(obj.Name)
            except Exception:
                pass

        def close(self, quiet=False):
            if self.closed:
                return
            self.closed = True
            try:
                self.follow.stop()
            except Exception:
                pass
            if self.dragging and not quiet:
                try:
                    self.doc.abortTransaction()
                except Exception:
                    pass
            self._leave_preview()
            if _alive(self.view_widget):
                self._remove_scene()
            for w in (self.field, self.bar):
                if _alive(w):
                    w.hide()
                    w.deleteLater()

    class ViewFilter(QtCore.QObject):
        """Double-click on a 3D view's viewport; clicks and Esc while an edit is open."""

        def __init__(self):
            super().__init__()
            self.views = {}  # viewport pointer -> View3DInventor widget
            self.trees = set()
            self.press = None

        def eventFilter(self, obj, event):
            try:
                return self._filter(obj, event)
            except Exception as exc:  # pragma: no cover - never break the view
                import FreeCAD

                FreeCAD.Console.PrintLog(f"On-canvas editing: {exc}\n")
                return False

        def _filter(self, obj, event):
            kind = event.type()
            key = _cpp(obj)
            if key in self.trees:
                if (kind == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton
                        and event.modifiers() & QtCore.Qt.AltModifier and enabled()):
                    return start_from_tree(obj.parentWidget(), event.position().toPoint())
                return False
            view_widget = self.views.get(key)
            if view_widget is None:
                return False
            if kind == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                if not enabled() or _dialog_open():
                    return False
                if event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier):
                    return False
                return start_from_view(view_widget, obj, event.position().toPoint())
            s = _session
            if s is None or s.closed or s.view_widget is not view_widget:
                return False
            if kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride) and event.key() == QtCore.Qt.Key_Escape:
                if kind == QtCore.QEvent.KeyPress:
                    QtCore.QTimer.singleShot(0, s.escape)
                event.accept()
                return True
            if kind in (QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
                import bruceworx_navpill

                p = event.position()
                s.pointer = bruceworx_navpill.inventor_pixel(p.x(), p.y(), obj.height(), obj.devicePixelRatioF())
            if kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                self.press = event.position().toPoint()
                s.drag_moved = False
            elif kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                start, self.press = self.press, None
                pos = event.position().toPoint()
                if start is not None and not s.drag_moved and (pos - start).manhattanLength() < 4:
                    QtCore.QTimer.singleShot(0, lambda: click_empty(view_widget, obj, pos))
            return False

    class Manager(QtCore.QObject):
        """Puts the filter on every 3D view and Browser tree."""

        def __init__(self):
            super().__init__()
            self.filter = ViewFilter()
            self.snap_mode = SNAP_AUTO
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(0)
            self.timer.timeout.connect(self.sync)

        def schedule(self, *_args):
            self.timer.start()

        def sync(self):
            import FreeCADGui

            mw = FreeCADGui.getMainWindow()
            mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
            if mdi is None:
                return
            for sub in mdi.subWindowList():
                view = sub.widget()
                if view is None or view.metaObject().className() != VIEW_CLASS:
                    continue
                vp = _graphics_viewport(view)
                if vp is None or _cpp(vp) in self.filter.views:
                    continue
                key = _cpp(vp)
                vp.installEventFilter(self.filter)
                self.filter.views[key] = view
                view.destroyed.connect(lambda _o=None, k=key: self.filter.views.pop(k, None))
            try:
                import bruceworx_picktrace

                trees = bruceworx_picktrace._trees()
            except Exception:
                trees = []
            for tree in trees:
                key = _cpp(tree.viewport())
                if key in self.filter.trees:
                    continue
                tree.viewport().installEventFilter(self.filter)
                self.filter.trees.add(key)
            if _session is not None and (not _alive(_session.view_widget) or
                                         mdi.activeSubWindow() is None or
                                         mdi.activeSubWindow().widget() is not _session.view_widget):
                end_session(quiet=True)

    _classes = (Field, Bar, Session, ViewFilter, Manager)
    return _classes


_manager = None
_session = None


def session():
    """The open edit, or None (for tests and scripts)."""
    return _session if _session is not None and not _session.closed else None


def end_session(quiet=False):
    global _session
    s, _session = _session, None
    if s is not None:
        s.close(quiet)


def _pick(view_widget, viewport, pos):
    """(document, top object, subname, owner, element, global face) under a viewport position."""
    import FreeCAD

    view = _py_view(view_widget)
    if view is None:
        return None
    import bruceworx_navpill

    x, y = bruceworx_navpill.inventor_pixel(pos.x(), pos.y(), viewport.height(), viewport.devicePixelRatioF())
    info = view.getObjectInfo((x, y))
    if not info:
        return None
    doc = FreeCAD.getDocument(info["Document"])
    parent = info.get("ParentObject")
    if parent:
        top = parent if hasattr(parent, "TypeId") else doc.getObject(str(parent))
        sub = info.get("SubName", "")
    else:
        top = doc.getObject(info["Object"])
        sub = info.get("Component", "")
    return view, doc, top, sub


def start_from_view(view_widget, viewport, pos):
    """A double-click in the 3D view: edit the feature that made the face, if it has a value here."""
    import bruceworx_picktrace

    picked = _pick(view_widget, viewport, pos)
    end_session()
    if picked is None:
        return False
    view, doc, top, sub = picked
    element = bruceworx_picktrace.element_kind(sub)
    if not element.startswith("Face"):
        return False
    try:
        owner = top.getSubObject(sub, retType=1)
    except Exception:
        owner = None
    feature = bruceworx_picktrace.trace(owner or top, element)
    if feature is None or feature.TypeId not in SUPPORTED:
        _status(HINT_NONE)
        return False
    matrix = body_matrix(feature, top, sub)
    try:
        face_global = top.getSubObject(sub)
        face_local = face_global.copy()
        face_local.transformShape(matrix.inverse())
    except Exception:
        face_local = None
    seed = seed_for(feature, face_local)
    if seed is None:
        field = field_label(feature.TypeId, "Length")
        _status(HINT_SIDE.format(label=feature.Label, field=field.lower()))
        return False
    try:
        view_dir = _vec(view.getViewDirection())
    except Exception:
        view_dir = None
    handles = handles_for(feature, matrix, seed, view_dir)
    if not handles:
        _status(HINT_NONE)
        return False
    return _open(view_widget, view, feature, handles, (top.Name, feature_subname(sub, feature.Name)))


def start_from_tree(tree, pos):
    """Alt-double-click on a feature's Browser row."""
    import FreeCADGui

    import bruceworx_picktrace

    obj = bruceworx_picktrace.index_object(tree.indexAt(pos))
    if obj is None or obj.TypeId not in SUPPORTED or _dialog_open():
        return False
    end_session()
    gdoc = FreeCADGui.getDocument(obj.Document.Name)
    view = gdoc.activeView() if gdoc else None
    if view is None or not hasattr(view, "getSceneGraph"):
        views = gdoc.mdiViewsOfType(VIEW_CLASS) if gdoc else []
        view = views[0] if views else None
    if view is None:
        return False
    view_widget = _view_widget(view)
    if view_widget is None:
        return False
    try:
        view_dir = _vec(view.getViewDirection())
    except Exception:
        view_dir = None
    seed = seed_for(obj, None) or {}
    handles = handles_for(obj, body_matrix(obj), seed, view_dir)
    if not handles:
        return False
    return _open(view_widget, view, obj, handles, None)


def _view_widget(view):
    """The Gui::View3DInventor widget of a Python view."""
    import FreeCADGui
    from PySide import QtWidgets

    try:
        target = _cpp(view.graphicsView())
    except Exception:
        return None
    mw = FreeCADGui.getMainWindow()
    for w in mw.findChildren(QtWidgets.QWidget):
        if w.metaObject().className() == VIEW_CLASS:
            gv = w.findChild(QtWidgets.QGraphicsView)
            if gv is not None and _cpp(gv) == target:
                return w
    return None


def start(obj, view=None):
    """Open on-canvas editing for a feature from Python (the Browser's way), in ``view`` or the active view."""
    import FreeCADGui

    if obj is None or obj.TypeId not in SUPPORTED:
        return None
    view = view or FreeCADGui.getDocument(obj.Document.Name).activeView()
    view_widget = _view_widget(view)
    if view_widget is None:
        return None
    end_session()
    try:
        view_dir = _vec(view.getViewDirection())
    except Exception:
        view_dir = None
    handles = handles_for(obj, body_matrix(obj), seed_for(obj, None) or {}, view_dir)
    if handles and _open(view_widget, view, obj, handles, None):
        return _session
    return None


def _open(view_widget, view, feature, handles, select_path):
    global _session
    _f, _b, session_class, _vf, _m = _make_classes()
    index = next((i for i, h in enumerate(handles) if h.draggable), 0)
    _session = session_class(view_widget, view, feature, handles, index, select_path)
    return True


def click_empty(view_widget, viewport, pos):
    """A plain click: leave when it hit nothing."""
    s = session()
    if s is None or s.dragging:
        return
    picked = _pick(view_widget, viewport, pos)
    if picked is None:
        end_session()


class _PrefObserver:
    def onChange(self, _group, name):
        if name == PREF_NAME and not enabled():
            end_session(quiet=True)
        elif name == "Theme" and session() is not None:
            session().apply_theme()


class _DocObserver:
    def slotDeletedDocument(self, doc):
        s = session()
        if s is not None and s.doc is doc:
            end_session(quiet=True)

    def slotDeletedObject(self, obj):
        s = session()
        if s is not None and s.obj is obj:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, lambda: end_session(quiet=True))

    def slotUndoDocument(self, doc):
        self._refresh(doc)

    def slotRedoDocument(self, doc):
        self._refresh(doc)

    def _refresh(self, doc):
        s = session()
        if s is not None and s.doc is doc and not s.dragging:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, s.rebuild)


class _GuiDocObserver:
    def slotCreatedDocument(self, _doc):
        if _manager is not None:
            _manager.schedule()

    def slotActivateDocument(self, _doc):
        if _manager is not None:
            _manager.schedule()


_observers = []
_groups = []


def _shutdown():
    end_session(quiet=True)
    for remove, observer in _observers:
        try:
            remove(observer)
        except Exception:
            pass
    _observers.clear()


def install():
    """Watch every 3D view and Browser tree for the double-clicks."""
    global _manager
    if _manager is not None:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
    if mdi is None:
        QtCore.QTimer.singleShot(500, install)
        return
    _f, _b, _s, _vf, manager_class = _make_classes()
    _manager = manager_class()
    mdi.subWindowActivated.connect(_manager.schedule)
    app_observer = _DocObserver()
    FreeCAD.addDocumentObserver(app_observer)
    _observers.append((FreeCAD.removeDocumentObserver, app_observer))
    gui_observer = _GuiDocObserver()
    FreeCADGui.addDocumentObserver(gui_observer)
    _observers.append((FreeCADGui.removeDocumentObserver, gui_observer))
    observer = _PrefObserver()
    for path in (PREFS_PATH, "User parameter:BaseApp/Preferences/MainWindow"):
        group = FreeCAD.ParamGet(path)  # keep the handle: a collected wrapper detaches
        group.Attach(observer)
        _groups.append((group, observer))
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _manager.schedule()
    QtCore.QTimer.singleShot(1500, _manager.schedule)  # the Browser comes up later
