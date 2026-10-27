# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing maths: centrelines for wires, pipes and hoses (BW-ROU-01..03, 27 Oct 2026).

No FreeCAD import. Points are ``(x, y, z)`` tuples in millimetres. Three things live here:

* **Splines.** A route through points as a C2 cubic spline with not-a-knot ends and chord-length
  parameters (close to the curve OCC's interpolation gives the Part shape). Each segment is a polynomial, so position,
  first and second derivatives are analytic; the curvature ``kappa = |r' x r''| / |r'|^3`` is
  sampled along the curve and the tightest radius is ``1 / max(kappa)``.
* **Polylines with arcs.** Straight segments joined by tangent arcs of one centreline bend radius
  R: at a corner of deflection theta the tangent length is ``R tan(theta / 2)``, the arc length is
  ``R theta``. A corner whose tangent length would overlap its neighbour's (or run past an end) is
  reported, not drawn. From the same construction come the developed (cut) length and the LRA
  table a tube bender wants: L the straight length before each bend, R the rotation of the bend
  plane about the tube axis since the previous bend, A the bend angle.
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
# Interpolating spline: cubic, C2, not-a-knot ends, chord-length parameters (close to what
# OCC's interpolation gives the Part shape), one small solve per coordinate.


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

    def __init__(self, points):
        self.points = clean_points(points)
        n = len(self.points)
        self.params = [0.0]
        for i in range(1, n):
            self.params.append(self.params[-1] + dist(self.points[i], self.points[i - 1]))
        self.second = [self._solve(axis) for axis in range(3)] if n > 2 else [[0.0] * n] * 3

    def _solve(self, axis):
        """Second derivatives M[i] at the points: the interior continuity rows plus not-a-knot
        ends (the third derivative continuous at the second and second-last point, so a circle
        sampled evenly comes back with its own curvature); three points make one parabola."""
        t, y = self.params, [p[axis] for p in self.points]
        n = len(y)
        h = [t[i + 1] - t[i] for i in range(n - 1)]
        rows = [[0.0] * (n + 1) for _ in range(n)]
        for i in range(1, n - 1):
            rows[i][i - 1], rows[i][i], rows[i][i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
            rows[i][n] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
        rows[0][0], rows[0][1], rows[0][2] = h[1], -(h[0] + h[1]), h[0]
        if n == 3:
            rows[2][0], rows[2][2] = 1.0, -1.0
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


def route_summary(points, mode="Spline", radius=0.0, min_radius=0.0):
    """``{"length", "min_radius", "bends", "ok", "problems"}`` for a route in either mode.
    ``ok`` is False when a bend is tighter than ``min_radius`` (0 = no check) or a corner failed."""
    points = clean_points(points)
    if mode == "Polyline":
        route = polyline_with_arcs(points, radius)
        length, tightest, bends, problems = route.length, route.min_radius, sum(b.ok for b in route.bends), list(route.problems)
    else:
        length, tightest, bends, problems = spline_length(points), spline_min_radius(points), max(0, len(points) - 2), []
    if min_radius > 0 and tightest < min_radius - 1e-6:
        problems.append(f"tightest bend radius {tightest:.1f} is below the minimum {min_radius:g}")
    return {"length": length, "min_radius": tightest, "bends": bends, "ok": not problems, "problems": problems}


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

FROM_TO_COLUMNS = ("Wire", "Signal", "Gauge", "Colour", "From", "From pin", "To", "To pin", "Length (m)")
CUT_LIST_COLUMNS = ("Item", "Kind", "Size", "Cut length (mm)", "Bend radius", "Bends", "Bend table")


def _csv(columns, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def from_to_csv(rows):
    """``rows``: dicts with wire, signal, gauge, colour, from, from_pin, to, to_pin, length_m."""
    return _csv(FROM_TO_COLUMNS, [
        (r.get("wire", ""), r.get("signal", ""), r.get("gauge", ""), r.get("colour", ""), r.get("from", ""),
         r.get("from_pin", ""), r.get("to", ""), r.get("to_pin", ""), f"{float(r.get('length_m', 0)):.3f}")
        for r in rows])


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
