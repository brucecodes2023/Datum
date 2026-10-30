# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing maths: centrelines for wires, pipes and hoses (BW-ROU-01..03, 27 Oct 2026).

No FreeCAD import. Points are ``(x, y, z)`` tuples in millimetres. Four things live here:

* **Splines.** A route through points as a C2 cubic spline with not-a-knot ends and chord-length
  parameters. Each segment is a polynomial, so position, first and second derivatives are
  analytic; the curvature ``kappa = |r' x r''| / |r'|^3`` is sampled along the curve and the
  tightest radius is ``1 / max(kappa)``. ``Spline.bspline`` gives the same curve exactly as a
  clamped cubic B-spline (knots at the chord parameters), which is what the FreeCAD object draws
  (one curve for the shape and the bend check, 30 Oct).
* **Polylines with arcs.** Straight segments joined by tangent arcs of one centreline bend radius
  R: at a corner of deflection theta the tangent length is ``R tan(theta / 2)``, the arc length is
  ``R theta``. A corner whose tangent length would overlap its neighbour's (or run past an end) is
  reported, not drawn. From the same construction come the developed (cut) length and the LRA
  table a tube bender wants: L the straight length before each bend, R the rotation of the bend
  plane about the tube axis since the previous bend, A the bend angle.
* **Bend rules** (30 Oct). A minimum bend radius as a multiple of OD or a fixed radius, presets by
  wire and hose type, the defaults the preferences hold, a bundle's strictest rule.
* **Leads** (ROU-05, 30 Oct). A route that starts or ends on a pin or port leaves along the
  frame's +Z for a straight length (2 x OD by default) before its first bend: a line plus a
  spline clamped to +Z, or a polyline corner placed so its arc starts that far out
  (``build_route``). Gauge ranges of pins (``gauge_fits``).
* **Tables.** AWG wire sizes (diameter ``0.127 mm * 92 ** ((36 - n) / 39)``), metric and NPS
  tube sizes, and a copper-plus-insulation mass estimate.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field

EPS = 1e-9
COPPER_DENSITY = 8.96e-3  # g/mm3
PVC_DENSITY = 1.40e-3  # g/mm3, the insulation estimate

# ----------------------------------------------------------------------------------------------
# Small vector helpers (tuples, no numpy).


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    if n < EPS:
        raise ValueError("zero-length vector")
    return mul(a, 1.0 / n)


def dist(a, b):
    return norm(sub(a, b))


def as_point(p):
    """A point from a tuple, list or anything with x, y, z (a FreeCAD Vector)."""
    if hasattr(p, "x") and hasattr(p, "y") and hasattr(p, "z"):
        return (float(p.x), float(p.y), float(p.z))
    return (float(p[0]), float(p[1]), float(p[2]))


def clean_points(points, tol=1e-6):
    """The points as tuples with consecutive duplicates dropped."""
    out = []
    for p in points:
        p = as_point(p)
        if not out or dist(out[-1], p) > tol:
            out.append(p)
    return out


# ----------------------------------------------------------------------------------------------
# Interpolating spline: cubic, C2, not-a-knot ends, chord-length parameters, one small solve per
# coordinate; the Part shape is the same curve (Spline.bspline).


def _gauss(rows):
    """Solve the augmented system ``rows`` (n rows of n + 1) by elimination with pivoting."""
    n = len(rows)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-300:
            raise ValueError("singular spline system")
        for r in range(n):
            if r != col and rows[r][col]:
                f = rows[r][col] / rows[col][col]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


class Spline:
    """A cubic spline through ``points`` (C2, not-a-knot ends, chord-length parameters); segment
    ``i`` runs from P[i] to P[i+1]."""

    def __init__(self, points, start_tangent=None, end_tangent=None):
        """``start_tangent`` / ``end_tangent`` (ROU-05): clamp the first derivative at an end to
        that unit vector (the spline leaves a pin or port along its +Z); else the end is
        not-a-knot (natural for two points)."""
        self.points = clean_points(points)
        n = len(self.points)
        self.start_tangent = unit(as_point(start_tangent)) if start_tangent is not None else None
        self.end_tangent = unit(as_point(end_tangent)) if end_tangent is not None else None
        self.params = [0.0]
        for i in range(1, n):
            self.params.append(self.params[-1] + dist(self.points[i], self.points[i - 1]))
        clamped = self.start_tangent is not None or self.end_tangent is not None
        if n > 2 or (n == 2 and clamped):
            self.second = [self._solve(axis) for axis in range(3)]
        else:
            self.second = [[0.0] * n] * 3

    @property
    def clamped(self):
        return self.start_tangent is not None or self.end_tangent is not None

    def _solve(self, axis):
        """Second derivatives M[i] at the points: the interior continuity rows plus not-a-knot
        ends (the third derivative continuous at the second and second-last point, so a circle
        sampled evenly comes back with its own curvature); three points make one parabola. A
        clamped end has ``2 h M0 + h M1 = 6 ((y1 - y0) / h - y0')`` (and its mirror at the end);
        two points with one clamped end are natural (M = 0) at the other."""
        t, y = self.params, [p[axis] for p in self.points]
        n = len(y)
        h = [t[i + 1] - t[i] for i in range(n - 1)]
        rows = [[0.0] * (n + 1) for _ in range(n)]
        for i in range(1, n - 1):
            rows[i][i - 1], rows[i][i], rows[i][i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
            rows[i][n] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
        d0 = self.start_tangent[axis] if self.start_tangent is not None else None
        d1 = self.end_tangent[axis] if self.end_tangent is not None else None
        if d0 is None and d1 is None:
            rows[0][0], rows[0][1], rows[0][2] = h[1], -(h[0] + h[1]), h[0]
            if n == 3:
                rows[2][0], rows[2][2] = 1.0, -1.0
            else:
                rows[n - 1][n - 3], rows[n - 1][n - 2], rows[n - 1][n - 1] = h[n - 2], -(h[n - 3] + h[n - 2]), h[n - 3]
            return _gauss(rows)
        if d0 is not None:
            rows[0][0], rows[0][1] = 2 * h[0], h[0]
            rows[0][n] = 6 * ((y[1] - y[0]) / h[0] - d0)
        elif n == 2:
            rows[0][0] = 1.0
        else:
            rows[0][0], rows[0][1], rows[0][2] = h[1], -(h[0] + h[1]), h[0]
        if d1 is not None:
            rows[n - 1][n - 2], rows[n - 1][n - 1] = h[n - 2], 2 * h[n - 2]
            rows[n - 1][n] = 6 * (d1 - (y[n - 1] - y[n - 2]) / h[n - 2])
        elif n == 2:
            rows[1][1] = 1.0
        else:
            rows[n - 1][n - 3], rows[n - 1][n - 2], rows[n - 1][n - 1] = h[n - 2], -(h[n - 3] + h[n - 2]), h[n - 3]
        return _gauss(rows)

    @property
    def segments(self):
        return max(0, len(self.points) - 1)

    def at(self, i, s):
        """Position, dr/dtau and d2r/dtau2 of segment ``i`` at ``s`` in [0, 1] (tau the chord
        parameter, so the derivatives are with respect to arc length of the chord polygon)."""
        t0, t1 = self.params[i], self.params[i + 1]
        h = t1 - t0
        tau = t0 + s * h
        r, r1, r2 = [], [], []
        for axis in range(3):
            y0, y1 = self.points[i][axis], self.points[i + 1][axis]
            m0, m1 = self.second[axis][i], self.second[axis][i + 1]
            lo, hi = t1 - tau, tau - t0
            r.append(m0 * lo**3 / (6 * h) + m1 * hi**3 / (6 * h) + (y0 / h - m0 * h / 6) * lo + (y1 / h - m1 * h / 6) * hi)
            r1.append(-m0 * lo**2 / (2 * h) + m1 * hi**2 / (2 * h) - (y0 / h - m0 * h / 6) + (y1 / h - m1 * h / 6))
            r2.append(m0 * lo / h + m1 * hi / h)
        return tuple(r), tuple(r1), tuple(r2)

    def length(self, per_segment=64):
        """Arc length by composite Simpson on |r'| per segment."""
        n = per_segment if per_segment % 2 == 0 else per_segment + 1
        total = 0.0
        for i in range(self.segments):
            h = (self.params[i + 1] - self.params[i]) / n
            acc = 0.0
            for k in range(n + 1):
                speed = norm(self.at(i, k / n)[1])
                acc += (1 if k in (0, n) else (4 if k % 2 else 2)) * speed
            total += acc * h / 3.0
        return total

    def samples(self, per_segment=32):
        out = []
        for i in range(self.segments):
            for k in range(per_segment):
                out.append(self.at(i, k / per_segment)[0])
        if self.points:
            out.append(self.points[-1])
        return out

    def curvatures(self, per_segment=64):
        """(kappa, point) sampled along the spline."""
        out = []
        for i in range(self.segments):
            for k in range(per_segment + 1):
                r, r1, r2 = self.at(i, k / per_segment)
                out.append((curvature(r1, r2), r))
        return out

    def min_radius(self, per_segment=64):
        kmax = max((k for k, _p in self.curvatures(per_segment)), default=0.0)
        return math.inf if kmax < EPS else 1.0 / kmax

    def bspline(self):
        """``(poles, knots, mults)`` of this spline as a clamped cubic B-spline over the chord
        parameters: the same curve exactly. Each segment's Bezier points are ``P_i + h r'/3`` and
        ``P_i+1 - h r'/3``; since the spline is C2 they lie on the legs of one control polygon,
        each leg cut in the ratio of the neighbouring spans ``h_i-1 : h_i : h_i+1``, so the
        polygon's vertices are read off by extending each segment's middle leg. n points give
        n + 2 poles, knots at the n parameters (end multiplicity 4)."""
        pts, t = self.points, self.params
        n = len(pts)
        if n < 2:
            raise ValueError("a spline needs at least two distinct points")
        if n == 2 and not self.clamped:
            a, b = pts
            return [a, add(a, mul(sub(b, a), 1 / 3)), add(a, mul(sub(b, a), 2 / 3)), b], [t[0], t[1]], [4, 4]
        m = n - 1
        h = [t[i + 1] - t[i] for i in range(m)] + [0.0]
        b1 = [add(pts[i], mul(self.at(i, 0.0)[1], h[i] / 3.0)) for i in range(m)]
        b2 = [sub(pts[i + 1], mul(self.at(i, 1.0)[1], h[i] / 3.0)) for i in range(m)]
        inner = [b1[0]] + [add(b2[i], mul(sub(b2[i], b1[i]), h[i + 1] / h[i])) for i in range(m)]
        return [pts[0]] + inner + [pts[-1]], list(t), [4] + [1] * (n - 2) + [4]


def bspline_value(poles, knots, mults, u, degree=3):
    """A point of the B-spline ``(poles, knots, mults)`` at ``u`` (de Boor); for tests and previews."""
    flat = [k for k, m in zip(knots, mults) for _ in range(m)]
    u = min(max(u, flat[degree]), flat[-degree - 1])
    span = degree
    while span < len(flat) - degree - 2 and u >= flat[span + 1]:
        span += 1
    d = [poles[j + span - degree] for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            i = j + span - degree
            den = flat[i + degree - r + 1] - flat[i]
            alpha = 0.0 if den == 0 else (u - flat[i]) / den
            d[j] = add(mul(d[j - 1], 1.0 - alpha), mul(d[j], alpha))
    return d[degree]


def spline_point(points, i, t):
    """Position, r' and r'' of segment ``i`` at ``t`` in [0, 1]."""
    return Spline(points).at(i, t)


def spline_samples(points, per_segment=32):
    """Points along the spline, ``per_segment`` steps per segment (the last point included)."""
    return Spline(points).samples(per_segment)


def spline_length(points, per_segment=64):
    return Spline(points).length(per_segment)


def spline_curvatures(points, per_segment=64):
    return Spline(points).curvatures(per_segment)


def spline_min_radius(points, per_segment=64):
    """The tightest radius along the spline (inf for a straight route)."""
    return Spline(points).min_radius(per_segment)


def curvature(r1, r2):
    """kappa = |r' x r''| / |r'|^3; zero for a stopped curve."""
    speed = norm(r1)
    if speed < EPS:
        return 0.0
    return norm(cross(r1, r2)) / speed**3


# ----------------------------------------------------------------------------------------------
# Polyline with tangent arcs.


@dataclass
class Bend:
    """One corner of a polyline route turned into an arc."""

    index: int  # the corner's point index
    angle: float  # deflection, radians
    tangent: float  # R tan(theta / 2)
    arc_length: float  # R theta
    start: tuple  # tangent point on the incoming segment
    end: tuple  # tangent point on the outgoing segment
    centre: tuple
    middle: tuple  # the arc's midpoint (for a three-point arc)
    normal: tuple  # bend plane normal, incoming x outgoing
    ok: bool = True
    problem: str = ""

    @property
    def degrees(self):
        return math.degrees(self.angle)


@dataclass
class PolylineRoute:
    points: list
    radius: float
    segments: list = field(default_factory=list)  # ("line", a, b) | ("arc", start, middle, end)
    bends: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.problems

    @property
    def length(self):
        total = 0.0
        for seg in self.segments:
            if seg[0] == "line":
                total += dist(seg[1], seg[2])
        total += sum(b.arc_length for b in self.bends if b.ok)
        return total

    @property
    def min_radius(self):
        return self.radius if any(b.ok for b in self.bends) else math.inf


def polyline_with_arcs(points, radius, min_angle=math.radians(0.5)):
    """Straight segments joined by tangent arcs of ``radius`` at every corner that turns more than
    ``min_angle``. A corner whose tangent length overlaps a neighbour's or an end is listed in
    ``problems`` and drawn sharp."""
    points = clean_points(points)
    route = PolylineRoute(points, float(radius))
    n = len(points)
    if n < 2:
        return route
    if n == 2 or radius <= EPS:
        route.segments = [("line", points[i], points[i + 1]) for i in range(n - 1)]
        if radius <= EPS and n > 2:
            route.problems.append("bend radius is zero")
        return route
    bends = {}
    for i in range(1, n - 1):
        u = unit(sub(points[i], points[i - 1]))
        v = unit(sub(points[i + 1], points[i]))
        c = max(-1.0, min(1.0, dot(u, v)))
        theta = math.acos(c)
        if theta < min_angle:
            continue
        if math.pi - theta < min_angle:
            bend = Bend(i, theta, math.inf, math.inf, points[i], points[i], points[i], points[i],
                        (0.0, 0.0, 0.0), ok=False, problem=f"corner {i} folds back on itself")
            bends[i] = bend
            route.problems.append(bend.problem)
            continue
        tangent = radius * math.tan(theta / 2)
        bisector = unit(sub(v, u))
        centre = add(points[i], mul(bisector, radius / math.cos(theta / 2)))
        middle = sub(centre, mul(bisector, radius))
        bends[i] = Bend(i, theta, tangent, radius * theta, sub(points[i], mul(u, tangent)),
                        add(points[i], mul(v, tangent)), centre, middle, unit(cross(u, v)))
    # Overlap check: on each segment the tangent lengths at its two ends must fit.
    for i in range(n - 1):
        seg = dist(points[i], points[i + 1])
        need = sum(b.tangent for b in (bends.get(i), bends.get(i + 1)) if b is not None and b.ok)
        if need > seg + 1e-6:
            for b in (bends.get(i), bends.get(i + 1)):
                if b is not None and b.ok:
                    b.ok = False
                    b.problem = (f"corner {b.index}: bend radius {radius:g} needs {b.tangent:.1f} of tangent "
                                 f"on a {seg:.1f} segment")
                    route.problems.append(b.problem)
    # Walk the corners building the segments.
    cursor = points[0]
    for i in range(1, n):
        b = bends.get(i)
        if i == n - 1 or b is None or not b.ok:
            route.segments.append(("line", cursor, points[i]))
            cursor = points[i]
            continue
        route.segments.append(("line", cursor, b.start))
        route.segments.append(("arc", b.start, b.middle, b.end))
        cursor = b.end
    route.bends = [bends[i] for i in sorted(bends)]
    return route


def developed_length(points, radius):
    """Cut length of a tube bent along the polyline: straights plus centreline arcs."""
    return polyline_with_arcs(points, radius).length


def lra_table(points, radius):
    """Bend table rows ``{"bend", "L", "R", "A"}`` (mm, degrees, degrees): L the straight length fed
    before the bend, R the bend-plane rotation about the tube axis since the previous bend (signed,
    looking down the feed direction), A the bend angle. The last row (``bend`` None) is the straight
    after the final bend."""
    route = polyline_with_arcs(points, radius)
    rows = []
    good = [b for b in route.bends if b.ok]
    if not route.points:
        return rows
    cursor = route.points[0]
    previous = None
    for k, b in enumerate(good, start=1):
        length = dist(cursor, b.start)
        rotation = 0.0
        if previous is not None:
            axis = unit(sub(b.start, previous.end)) if dist(b.start, previous.end) > EPS else previous.normal
            s = dot(cross(previous.normal, b.normal), axis)
            c = dot(previous.normal, b.normal)
            rotation = math.degrees(math.atan2(s, c))
        rows.append({"bend": k, "L": length, "R": rotation, "A": b.degrees})
        cursor = b.end
        previous = b
    rows.append({"bend": None, "L": dist(cursor, route.points[-1]), "R": 0.0, "A": 0.0})
    return rows


def bend_table_text(rows):
    """The LRA table as lines: ``1  L 120.0  R 0.0  A 90.0``; the last line the end straight."""
    lines = []
    for r in rows:
        if r["bend"] is None:
            lines.append(f"end  L {r['L']:.1f}")
        else:
            lines.append(f"{r['bend']}  L {r['L']:.1f}  R {r['R']:.1f}  A {r['A']:.1f}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# Route-level summary used by the FreeCAD objects.


def route_summary(points, mode="Spline", radius=0.0, min_radius=0.0, start=None, end=None):
    """``{"length", "min_radius", "bends", "ok", "problems", "straights", "points"}`` for a route in
    either mode. ``ok`` is False when a bend is tighter than ``min_radius`` (0 = no check), a corner
    failed, or a lead (``start`` / ``end``, ROU-05) is shorter than its pin or port asks."""
    route = build_route(points, mode, radius, start, end)
    problems = list(route.problems)
    tightest = route.min_radius
    if min_radius > 0 and tightest < min_radius - 1e-6:
        problems.append(f"tightest bend radius {tightest:.1f} is below the minimum {min_radius:g}")
    return {"length": route.length, "min_radius": tightest, "bends": route.bends, "ok": not problems,
            "problems": problems, "straights": route.straights, "points": route.points}


# ----------------------------------------------------------------------------------------------
# Leads (ROU-05, 30 Oct): a route that starts or ends on a pin or port leaves along the frame's +Z
# for a straight length before its first bend, so a wire does not bend at the crimp and a tube
# enters its fitting straight.


@dataclass
class Lead:
    """A route end on a frame: ``origin`` (the frame's origin, the route's end point),
    ``direction`` (its +Z, out of the part: the way the route leaves) and the straight ``length``."""

    origin: tuple
    direction: tuple
    length: float = 0.0
    name: str = ""  # for messages: "Pin 1 of J1"


@dataclass
class Route:
    """A route's geometry: ``pieces`` ``("line", a, b)``, ``("arc", start, middle, end)`` or
    ``("spline", Spline)`` in order; ``points`` the vertices (the polyline's corners, or the
    spline's points with the lead ends); ``straights`` the straight length found at each lead
    (None where there is none)."""

    mode: str
    pieces: list = field(default_factory=list)
    points: list = field(default_factory=list)
    length: float = 0.0
    min_radius: float = math.inf
    bends: int = 0
    problems: list = field(default_factory=list)
    straights: tuple = (None, None)

    @property
    def ok(self):
        return not self.problems

    def bspline(self):
        """A Spline-mode route (lead lines and the spline) as one clamped cubic B-spline
        ``(poles, knots, mults)``: each line a cubic Bezier at its thirds, joined to the spline at a
        knot of multiplicity 3. The lines run at unit speed and the spline leaves them with a unit
        first derivative (chord-length parameters, clamped to +Z), so the joins are C1 and the
        whole route is one edge (OCC sweeps one edge where a line-spline-line wire can fail)."""
        poles, knots, mults = [], [], []
        offset = 0.0
        for piece in self.pieces:
            if piece[0] == "line":
                a, b = piece[1], piece[2]
                v = sub(b, a)
                p, k = [a, add(a, mul(v, 1 / 3)), add(a, mul(v, 2 / 3)), b], [0.0, dist(a, b)]
                m = [4, 4]
            elif piece[0] == "spline":
                p, k, m = piece[1].bspline()
            else:
                raise ValueError("only a Spline-mode route is one B-spline")
            k = [x - k[0] + offset for x in k]
            if not poles:
                poles, knots, mults = list(p), list(k), list(m)
            else:
                poles += list(p[1:])
                mults[-1] = 3
                knots += k[1:]
                mults += list(m[1:])
            offset = knots[-1]
        return poles, knots, mults


def _lead_corner(lead, extra, target, radius):
    """The lead's end ``origin + d (L + extra)`` and the tangent length ``R tan(theta / 2)`` of the
    corner there towards ``target`` (0 when straight on or folding back)."""
    d = unit(as_point(lead.direction))
    q = add(as_point(lead.origin), mul(d, lead.length + extra))
    v = sub(target, q)
    if norm(v) < EPS or radius <= EPS:
        return q, 0.0
    theta = math.acos(max(-1.0, min(1.0, dot(d, unit(v)))))
    if theta > math.pi - math.radians(0.5):
        return q, 0.0
    # A target behind the lead end (more than a right angle) cannot be reached with one arc out
    # of the straight: the end goes R past the straight (as for a right angle) and the corner,
    # which then needs more tangent than it has, is reported rather than chased outwards.
    return q, radius * min(1.0, math.tan(theta / 2.0))


def _polyline_leads(pts, radius, start, end):
    """The polyline's corners with the lead ends inserted. A lead end sits ``L + t`` out along +Z,
    ``t`` the tangent length of the corner there, so the arc starts exactly ``L`` from the frame;
    ``t`` depends on the corner's angle and so on the end itself (and, with two points, on the
    other lead): a fixed-point iteration, a few steps on any route."""
    ts, te = 0.0, 0.0
    qs = qe = None
    for _ in range(100):
        if start is not None:
            target = pts[1] if len(pts) > 2 or end is None else (qe or as_point(end.origin))
            qs, ts_new = _lead_corner(start, ts, target, radius)
        if end is not None:
            target = pts[-2] if len(pts) > 2 or start is None else (qs or as_point(start.origin))
            qe, te_new = _lead_corner(end, te, target, radius)
        done = True
        if start is not None:
            done &= abs(ts_new - ts) < 1e-10
            ts = ts_new
        if end is not None:
            done &= abs(te_new - te) < 1e-10
            te = te_new
        if done:
            break
    if start is not None:
        qs, _t = _lead_corner(start, ts, pts[1] if len(pts) > 2 or end is None else qe, radius)
    out = list(pts)
    if end is not None:
        out = out[:-1] + [qe, out[-1]]
    if start is not None:
        out = [out[0], qs] + out[1:]
    return clean_points(out)


def _straight_at(route_points, segments, at_start):
    """Length of the first (last) straight of a polyline route: from the end point to where the
    first arc starts, or to the first corner when there is no arc there."""
    if not segments:
        return 0.0
    seg = segments[0] if at_start else segments[-1]
    if seg[0] != "line":
        return 0.0
    return dist(seg[1], seg[2])


def build_route(points, mode="Spline", radius=0.0, start=None, end=None):
    """The route through ``points`` in ``mode`` with optional leads at its ends (``start`` /
    ``end``: a :class:`Lead` whose origin is the first / last point).

    * Polyline: the lead ends become corners placed so that the arc there starts exactly ``L`` out
      (``_polyline_leads``); the rest is :func:`polyline_with_arcs`. A first or last straight
      shorter than its lead (a corner that does not fit) is a problem.
    * Spline: a straight line of length ``L`` along +Z, then the spline from there through the
      other points with its first derivative clamped to +Z (G1, so the bend check sees the real
      curvature where the spline leaves the straight); the same at the end, arriving along -Z.
    """
    pts = clean_points(points)
    route = Route(mode)
    if start is not None and pts:
        pts[0] = as_point(start.origin)
    if end is not None and pts:
        pts[-1] = as_point(end.origin)
    if len(pts) < 2:
        route.points = pts
        return route
    if mode == "Polyline":
        corners = _polyline_leads(pts, radius, start, end) if (start or end) else pts
        poly = polyline_with_arcs(corners, radius)
        route.pieces, route.points = list(poly.segments), list(poly.points)
        route.length, route.min_radius = poly.length, poly.min_radius
        route.bends, route.problems = sum(b.ok for b in poly.bends), list(poly.problems)
        straights = [None, None]
        for k, lead in ((0, start), (1, end)):
            if lead is None:
                continue
            found = _straight_at(poly.points, poly.segments, k == 0)
            straights[k] = found
            if found < lead.length - 1e-6:
                where = lead.name or ("start" if k == 0 else "end")
                route.problems.append(f"the straight at {where} is {found:.1f}, it needs {lead.length:g}")
        route.straights = tuple(straights)
        return route
    # Spline mode.
    head, tail, inner = None, None, list(pts)
    t0 = t1 = None
    if start is not None:
        d = unit(as_point(start.direction))
        t0 = d
        if start.length > EPS:
            q = add(pts[0], mul(d, start.length))
            head, inner[0] = ("line", pts[0], q), q
    if end is not None:
        d = unit(as_point(end.direction))
        t1 = mul(d, -1.0)
        if end.length > EPS:
            q = add(pts[-1], mul(d, end.length))
            tail, inner[-1] = ("line", q, pts[-1]), q
    inner = clean_points(inner)
    pieces, length = [], 0.0
    if head is not None:
        pieces.append(head)
        length += dist(head[1], head[2])
    tightest = math.inf
    if len(inner) >= 2:
        spline = Spline(inner, t0, t1)
        pieces.append(("spline", spline))
        length += spline.length()
        tightest = spline.min_radius()
    if tail is not None:
        pieces.append(tail)
        length += dist(tail[1], tail[2])
    route.pieces, route.length, route.min_radius = pieces, length, tightest
    route.points = ([pts[0]] if head else []) + inner + ([pts[-1]] if tail else [])
    route.bends = max(0, len(pts) - 2)
    route.straights = (start.length if start is not None else None, end.length if end is not None else None)
    return route


def straight_length(kind, od, override=0.0, prefs=None):
    """The straight a pin or port asks before the first bend: its own ``override`` when set, else
    the preferences: wire and hose ``<Kind>StraightFactor`` x OD (2 x OD: past the terminal, out
    of the fitting), tube the larger of ``TubeStraightFactor`` x OD and ``TubeClampLength`` (the
    bender's clamp needs that much straight)."""
    if override and override > 0:
        return float(override)
    p = prefs_with_defaults(prefs)
    if kind == "tube":
        return max(p["TubeStraightFactor"] * float(od), p["TubeClampLength"])
    return p[kind.capitalize() + "StraightFactor"] * float(od)


def awg_number(name):
    """The AWG number of a gauge name ("22 AWG" -> 22), None for a metric size."""
    text = str(name).strip()
    if text.endswith("AWG"):
        try:
            return int(text[:-3].strip())
        except ValueError:
            return None
    return None


def gauge_fits(name, gauge_min=0, gauge_max=0):
    """None when the wire ``name`` fits a pin taking AWG ``gauge_min`` (thinnest, larger number)
    to ``gauge_max`` (thickest); else why not. A metric size is compared by conductor area with the
    AWG limits; 0 means no limit."""
    if not gauge_min and not gauge_max:
        return None
    try:
        _d, area = gauge(name)
    except ValueError:
        return None
    if gauge_min and area < gauge(f"{int(gauge_min)} AWG")[1] - 1e-9:
        return f"{name} is thinner than the pin takes ({int(gauge_min)} AWG)"
    if gauge_max and area > gauge(f"{int(gauge_max)} AWG")[1] + 1e-9:
        return f"{name} is thicker than the pin takes ({int(gauge_max)} AWG)"
    return None


# ----------------------------------------------------------------------------------------------
# Minimum bend rules (ROU-02, 30 Oct): per item a multiple of its OD or a fixed radius, with
# presets by wire or hose type and defaults the user sets once (the Bend Rules dialog).


@dataclass(frozen=True)
class BendRule:
    """``mode`` "OD" (``value`` x the item's OD), "Fixed" (``value`` mm) or "None" (no check)."""

    mode: str
    value: float = 0.0

    def radius(self, od):
        if self.mode == "OD":
            return self.value * float(od)
        if self.mode == "Fixed":
            return float(self.value)
        return 0.0

    def text(self):
        if self.mode == "OD":
            return f"{self.value:g} × OD"
        if self.mode == "Fixed":
            return f"{self.value:g} mm"
        return "no check"


RULE_DEFAULT = "Default"  # follow the preferences
RULE_OD = "Multiple of OD"
RULE_FIXED = "Fixed radius"
RULE_NONE = "No check"
RULE_FROM_WIRES = "From wires"  # a bundle: the strictest of its wires' rules
PLAIN_RULES = (RULE_OD, RULE_FIXED, RULE_NONE)

# name -> (rule, where the number comes from). Minimum radii for a static (installed) bend; the
# sources are in the ROU built chapter of 30 Oct.
WIRE_PRESETS = {
    "Hook-up PVC": (BendRule("OD", 4.0), "your default (R2)"),
    "Silicone": (BendRule("OD", 3.0), "soft insulation; NASA-STD-8739.4A Table 7-1 harness minimum 3 x OD"),
    "PTFE / ETFE": (BendRule("OD", 3.0), "NASA-STD-8739.4A Table 7-1, AWG 10 and smaller without coax: 3 x OD"),
    "Polyimide (Kapton)": (BendRule("OD", 10.0), "NASA-STD-8739.4A Table 7-1 minimum 10 x OD"),
    "Large (AWG 8 or larger)": (BendRule("OD", 6.0), "NASA-STD-8739.4A Table 7-1 minimum 6 x OD"),
    "Shielded": (BendRule("OD", 6.0), "your choice; NASA-STD-8739.4A gives 6 x OD for coax"),
    "Coax": (BendRule("OD", 10.0), "NASA-STD-8739.4A optimum 10 x OD (minimum 6 x OD)"),
    "High-flex (moving)": (BendRule("OD", 10.0), "continuous-flex cable data, typically 7.5 to 15 x OD"),
    "Ribbon / flat": (BendRule("OD", 3.0), "NASA workmanship: 3 x thickness short-term (OD = thickness)"),
}
HOSE_PRESETS = {
    "General hose": (BendRule("OD", 6.0), "your default"),
}
PRESETS = {"wire": WIRE_PRESETS, "hose": HOSE_PRESETS, "tube": {}}
RULE_KINDS = ("wire", "hose", "tube")

# The preference group Mod/BruceWorx/Routing: key -> default. <Kind>Rule names a preset or a plain
# rule; <Kind>Factor and <Kind>Radius are the numbers of Multiple of OD and Fixed radius.
PREF_DEFAULTS = {
    "WireRule": "Hook-up PVC", "WireFactor": 4.0, "WireRadius": 10.0,
    "HoseRule": "General hose", "HoseFactor": 6.0, "HoseRadius": 60.0,
    "TubeRule": RULE_OD, "TubeFactor": 1.5, "TubeRadius": 20.0, "TubeClrFactor": 2.0,
    # ROU-05: the straight at a pin or port before the first bend (F2).
    "WireStraightFactor": 2.0, "HoseStraightFactor": 2.0, "TubeStraightFactor": 2.0, "TubeClampLength": 0.0,
}


def default_choices(kind):
    """What the preference may name for ``kind``: its presets, then the three plain rules."""
    return list(PRESETS[kind]) + list(PLAIN_RULES)


def rule_choices(kind):
    """An item's ``BendRule`` choices: Default (the preference) first; a bundle's From wires."""
    if kind == "bundle":
        return [RULE_FROM_WIRES] + list(PLAIN_RULES)
    return [RULE_DEFAULT] + default_choices(kind)


def prefs_with_defaults(prefs=None):
    """``prefs`` over PREF_DEFAULTS, numbers as floats; unknown keys and empty values ignored."""
    out = dict(PREF_DEFAULTS)
    for key, value in (prefs or {}).items():
        if key in out and value not in (None, ""):
            out[key] = type(PREF_DEFAULTS[key])(value)
    return out


def _plain(kind, choice, factor, radius):
    if choice in PRESETS.get(kind, {}):
        return PRESETS[kind][choice][0]
    if choice == RULE_OD:
        return BendRule("OD", float(factor))
    if choice == RULE_FIXED:
        return BendRule("Fixed", float(radius))
    if choice == RULE_NONE:
        return BendRule("None")
    return None


def default_rule(kind, prefs=None):
    """(rule, name) the preferences give ``kind`` ("wire", "hose", "tube"); an unknown name in
    the preferences falls back to the shipped default."""
    key = kind.capitalize()
    for p in (prefs_with_defaults(prefs), PREF_DEFAULTS):
        rule = _plain(kind, p[key + "Rule"], p[key + "Factor"], p[key + "Radius"])
        if rule is not None:
            return rule, p[key + "Rule"]
    raise ValueError(f"no default bend rule for {kind!r}")


def item_rule(kind, choice, factor=0.0, radius=0.0, prefs=None):
    """(rule, label) for an item's ``BendRule`` choice; ``factor`` and ``radius`` are the item's
    own BendFactor and MinBendRadius, read by Multiple of OD and Fixed radius."""
    rule = _plain(kind, choice, factor, radius) if choice != RULE_DEFAULT else None
    if rule is None:
        rule, name = default_rule(kind, prefs)
        return rule, f"Default: {rule.text()}" if name in PLAIN_RULES else f"Default: {name}, {rule.text()}"
    if choice in PRESETS.get(kind, {}):
        return rule, f"{choice}, {rule.text()}"
    return rule, rule.text()


def bundle_min_radius(rules, bundle_od):
    """The strictest of the wires' rules applied to the bundle: a multiple of OD is taken of the
    bundle's OD (NASA-STD-8739.4A gives harness radii on the overall OD), a fixed radius as it is;
    0 when no wire is checked."""
    return max((r.radius(bundle_od) for r in rules if r.mode != "None"), default=0.0)


def migrate_rule(old_radius, od, old_default_factor):
    """BendRule choice for an item saved before bend rules (27 Oct) with ``MinBendRadius``
    ``old_radius``: the old default multiple of OD keeps following OD, any other radius is a fixed
    override, 0 is no check."""
    if old_radius <= 0:
        return RULE_NONE
    if old_default_factor and od > 0 and abs(old_radius - old_default_factor * od) <= 1e-6 * max(1.0, old_radius):
        return RULE_OD
    return RULE_FIXED


# ----------------------------------------------------------------------------------------------
# Wire gauges.


def awg_diameter(n):
    """Conductor diameter in mm of AWG ``n`` (0 for 1/0, -1 for 2/0, ...)."""
    return 0.127 * 92 ** ((36 - n) / 39)


def _awg_rows():
    rows = []
    for n in range(30, -1, -1):
        d = awg_diameter(n)
        rows.append((f"{n} AWG", d, math.pi * d * d / 4))
    return rows


METRIC_MM2 = (0.25, 0.34, 0.5, 0.75, 1.0, 1.5, 2.5, 4.0, 6.0, 10.0)


def _metric_rows():
    return [(f"{a:g} mm²", 2 * math.sqrt(a / math.pi), a) for a in METRIC_MM2]


# name -> (conductor diameter mm, conductor area mm2)
GAUGES = {name: (d, a) for name, d, a in _awg_rows() + _metric_rows()}
GAUGE_NAMES = list(GAUGES)


def gauge(name):
    """(conductor diameter mm, area mm2) of a gauge name such as ``"22 AWG"`` or ``"1.5 mm²"``."""
    try:
        return GAUGES[name]
    except KeyError:
        raise ValueError(f"unknown gauge {name!r}") from None


def default_insulation_od(name):
    """A typical hook-up wire outer diameter: conductor plus 0.25 mm of wall each side, at least
    1.0 mm overall."""
    d, _a = gauge(name)
    return max(1.0, round(d + 0.5, 2))


def wire_mass(name, length_mm, insulation_od_mm):
    """Copper conductor plus a PVC insulation estimate, in grams."""
    d, area = gauge(name)
    outer = max(insulation_od_mm, d)
    insulation = math.pi * (outer * outer - d * d) / 4
    return length_mm * (area * COPPER_DENSITY + insulation * PVC_DENSITY)


def bundle_od(diameters, packing=1.15):
    """Outer diameter of a bundle: the circle holding the summed areas, times a packing factor."""
    if not diameters:
        return 0.0
    return packing * math.sqrt(sum(d * d for d in diameters))


# ----------------------------------------------------------------------------------------------
# Tube sizes.

# name -> (OD mm, wall mm). Metric tube from the common hydraulic/pneumatic list; NPS schedule 40.
TUBE_SIZES = {
    "4 x 0.5": (4.0, 0.5), "6 x 1": (6.0, 1.0), "8 x 1": (8.0, 1.0), "10 x 1": (10.0, 1.0),
    "12 x 1": (12.0, 1.0), "15 x 1.5": (15.0, 1.5), "16 x 1.5": (16.0, 1.5), "18 x 1.5": (18.0, 1.5),
    "20 x 2": (20.0, 2.0), "22 x 2": (22.0, 2.0), "25 x 2": (25.0, 2.0), "28 x 2": (28.0, 2.0),
    "32 x 2": (32.0, 2.0), "35 x 2": (35.0, 2.0), "42 x 2": (42.0, 2.0),
    'NPS 1/8"': (10.3, 1.73), 'NPS 1/4"': (13.7, 2.24), 'NPS 3/8"': (17.1, 2.31), 'NPS 1/2"': (21.3, 2.77),
    'NPS 3/4"': (26.7, 2.87), 'NPS 1"': (33.4, 3.38), 'NPS 1 1/4"': (42.2, 3.56), 'NPS 1 1/2"': (48.3, 3.68),
    'NPS 2"': (60.3, 3.91),
}
TUBE_SIZE_NAMES = ["Custom"] + list(TUBE_SIZES)


def tube_size(name):
    try:
        return TUBE_SIZES[name]
    except KeyError:
        raise ValueError(f"unknown tube size {name!r}") from None


# ----------------------------------------------------------------------------------------------
# Reports.

FROM_TO_COLUMNS = ("Wire", "Signal", "Gauge", "Colour", "From", "From pin", "From mate", "To", "To pin", "To mate",
                   "Length (m)")
CUT_LIST_COLUMNS = ("Item", "Kind", "Size", "Cut length (mm)", "Bend radius", "Bends", "Bend table")


def _csv(columns, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def from_to_csv(rows):
    """``rows``: dicts with wire, signal, gauge, colour, from, from_pin, from_mate, to, to_pin,
    to_mate, length_m. A mate is the connector mated with that end's connector (ROU-05)."""
    return _csv(FROM_TO_COLUMNS, [
        (r.get("wire", ""), r.get("signal", ""), r.get("gauge", ""), r.get("colour", ""), r.get("from", ""),
         r.get("from_pin", ""), r.get("from_mate", ""), r.get("to", ""), r.get("to_pin", ""), r.get("to_mate", ""),
         f"{float(r.get('length_m', 0)):.3f}")
        for r in rows])


TERMINAL_COLUMNS = ("Kind", "Manufacturer", "MPN", "Standard Library item", "Qty")


def terminal_csv(rows):
    """The terminals, seals and connector accessories to order (ROU-05; your 30 Oct answer: ordered
    by manufacturer and MPN, the Standard Library item alongside for finding). ``rows``: dicts with
    kind, manufacturer, mpn, item, qty; summed per (kind, manufacturer, MPN, item), sorted."""
    totals = {}
    for r in rows:
        key = (r.get("kind", ""), r.get("manufacturer", ""), r.get("mpn", ""), r.get("item", ""))
        totals[key] = totals.get(key, 0) + int(r.get("qty", 0))
    return _csv(TERMINAL_COLUMNS, [k + (n,) for k, n in sorted(totals.items())])


def cut_list_csv(rows):
    """``rows``: dicts with item, kind, size, cut_length, radius, bends, table (the LRA rows)."""
    out = []
    for r in rows:
        table = r.get("table") or []
        text = "; ".join(f"L{t['L']:.1f} R{t['R']:.1f} A{t['A']:.1f}" for t in table if t.get("bend") is not None)
        tail = [t for t in table if t.get("bend") is None]
        if tail:
            text = (text + "; " if text else "") + f"end L{tail[-1]['L']:.1f}"
        out.append((r.get("item", ""), r.get("kind", ""), r.get("size", ""), f"{float(r.get('cut_length', 0)):.1f}",
                    f"{float(r.get('radius', 0)):g}", r.get("bends", 0), text))
    return _csv(CUT_LIST_COLUMNS, out)
