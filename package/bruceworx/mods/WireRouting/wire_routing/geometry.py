# SPDX-License-Identifier: LGPL-2.1-or-later
"""Wire centerline maths (BW-WIR-02). Plain Python, no FreeCAD.

A wire's centerline is a polyline through its points with every corner
rounded to one radius, the minimum bend radius. At a corner where the
direction turns by ``theta`` the arc starts and ends ``t = r tan(theta/2)``
from the corner and is ``r theta`` long, so the length is::

    sum(segment lengths) - sum(2 t) + sum(r theta)

A segment between two corners must be at least as long as their two tangent
lengths together. When it is not, the wire cannot make the bend at that
radius: :func:`route` raises :class:`RouteError` naming the segment and how
much room is missing. That check is the bend-radius check; it runs before
any geometry is built.

Vectors are 3-tuples of floats, lengths in millimetres, angles in radians.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

TOLERANCE = 1e-7  # mm; points closer than this are one point
STRAIGHT = 1e-9  # rad; a smaller turn is no corner
REVERSAL = math.pi - 1e-6  # rad; a larger turn folds the wire back on itself


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    if n < TOLERANCE:
        raise ValueError("zero-length vector")
    return scale(a, 1.0 / n)


def distance(a, b):
    return norm(sub(a, b))


@dataclass
class Problem:
    """One reason a route cannot be built."""

    kind: str  # "tight" or "reversal"
    index: int  # tight: segment index (points[index] -> points[index+1]); reversal: point index
    need: float = 0.0  # tight: tangent length the two corners need
    have: float = 0.0  # tight: segment length
    message: str = ""

    def as_json(self) -> dict:
        return {
            "kind": self.kind,
            "index": self.index,
            "need": self.need,
            "have": self.have,
            "short_by": max(0.0, self.need - self.have),
            "message": self.message,
        }


class RouteError(ValueError):
    """The wire cannot follow these points at this bend radius."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("; ".join(p.message for p in problems))


@dataclass
class Line:
    start: tuple
    end: tuple

    @property
    def length(self) -> float:
        return distance(self.start, self.end)


@dataclass
class Arc:
    start: tuple
    mid: tuple
    end: tuple
    center: tuple
    radius: float
    angle: float  # turn, radians
    corner: int  # index of the point this arc rounds

    @property
    def length(self) -> float:
        return self.radius * self.angle


@dataclass
class Route:
    points: list  # the polyline, duplicates removed
    radius: float
    elements: list = field(default_factory=list)  # Line and Arc, in order
    turns: list = field(default_factory=list)  # turn angle at each interior point

    @property
    def length(self) -> float:
        return sum(e.length for e in self.elements)

    @property
    def polyline_length(self) -> float:
        return sum(distance(a, b) for a, b in zip(self.points, self.points[1:]))


def clean_points(points) -> list:
    """Points as float tuples, with consecutive duplicates dropped."""
    out = []
    for p in points:
        q = (float(p[0]), float(p[1]), float(p[2]))
        if not out or distance(out[-1], q) > TOLERANCE:
            out.append(q)
    return out


def route(points, radius: float) -> Route:
    """Round every corner of the polyline to ``radius``.

    Raises :class:`RouteError` listing every problem, not only the first.
    ``radius`` 0 gives the sharp polyline.
    """
    pts = clean_points(points)
    if len(pts) < 2:
        raise RouteError([Problem("tight", 0, message="a wire needs two different points")])
    if radius < 0:
        raise ValueError("bend radius cannot be negative")
    n = len(pts)
    dirs = [unit(sub(pts[i + 1], pts[i])) for i in range(n - 1)]
    lengths = [distance(pts[i], pts[i + 1]) for i in range(n - 1)]
    turns = [0.0] * n
    tangents = [0.0] * n
    problems = []
    for i in range(1, n - 1):
        cosine = max(-1.0, min(1.0, dot(dirs[i - 1], dirs[i])))
        theta = math.acos(cosine)
        turns[i] = theta
        if theta < STRAIGHT:
            turns[i] = 0.0
        elif theta > REVERSAL:
            problems.append(
                Problem("reversal", i, message=f"point {i}: the wire folds straight back on itself")
            )
        else:
            tangents[i] = radius * math.tan(theta / 2.0)
    if not any(p.kind == "reversal" for p in problems):
        for i in range(n - 1):
            need = tangents[i] + tangents[i + 1]
            if need > lengths[i] + TOLERANCE:
                problems.append(
                    Problem(
                        "tight",
                        i,
                        need,
                        lengths[i],
                        f"segment {i} (point {i} to {i + 1}) is {lengths[i]:.3f} mm long but the "
                        f"bends at its ends need {need:.3f} mm at radius {radius:g} mm "
                        f"({need - lengths[i]:.3f} mm short)",
                    )
                )
    if problems:
        raise RouteError(problems)

    result = Route(pts, radius, turns=turns[1:-1])
    cursor = pts[0]
    for i in range(1, n - 1):
        t = tangents[i]
        if turns[i] == 0.0:
            continue  # collinear: the line runs on through
        if t <= TOLERANCE:  # sharp corner (radius 0)
            result.elements.append(Line(cursor, pts[i]))
            cursor = pts[i]
            continue
        u, v = dirs[i - 1], dirs[i]
        start = sub(pts[i], scale(u, t))
        end = add(pts[i], scale(v, t))
        bisector = unit(sub(v, u))
        center = add(pts[i], scale(bisector, radius / math.cos(turns[i] / 2.0)))
        mid = sub(center, scale(bisector, radius))
        if distance(cursor, start) > TOLERANCE:
            result.elements.append(Line(cursor, start))
        result.elements.append(Arc(start, mid, end, center, radius, turns[i], i))
        cursor = end
    if distance(cursor, pts[-1]) > TOLERANCE:
        result.elements.append(Line(cursor, pts[-1]))
    return result


def routed_length(points, radius: float) -> float:
    return route(points, radius).length


def wire_points(start, start_dir, end, end_dir, via=(), lead: float = 0.0) -> list:
    """The polyline for a wire between two ports.

    The wire leaves each port straight along the port's direction (local +Z)
    for ``lead`` mm before it may bend, then passes the ``via`` points in
    order. ``lead`` 0 gives no lead-out.
    """
    points = [tuple(start)]
    if lead > TOLERANCE:
        points.append(add(start, scale(unit(start_dir), lead)))
    points.extend(tuple(p) for p in via)
    if lead > TOLERANCE:
        points.append(add(end, scale(unit(end_dir), lead)))
    points.append(tuple(end))
    return points


# ----- clip graph (BW-WIR-06) --------------------------------------------------


@dataclass
class GraphRoute:
    path: list  # node names, start first
    length: float
    explored: int  # complete start-to-end paths tried
    rejected: list  # [(path, reason)] for paths that could not be bent
    truncated: bool = False  # the search limit was hit; a shorter path may exist


def route_on_graph(
    nodes: dict,
    edges,
    start: str,
    end: str,
    radius: float,
    *,
    points_for=None,
    limit: int = 20000,
) -> GraphRoute:
    """Shortest path from ``start`` to ``end`` that the wire can bend through.

    ``nodes`` maps a name to a point: the two ports and the clips. ``edges``
    is the pairs of names the wire may run between (both ways). A path is
    feasible when :func:`route` accepts it, so the bend check and the length
    are the same code the wire itself uses. ``points_for(path)`` turns a
    list of names into the polyline to check; the default is the node points
    (pass one that adds the port lead-outs, see :func:`wire_points`). Every
    simple path is tried; harness clip graphs are small. ``limit`` bounds
    the search.

    Raises :class:`RouteError` when no path is feasible.
    """
    if points_for is None:
        def points_for(path):
            return [nodes[name] for name in path]

    for name in (start, end):
        if name not in nodes:
            raise KeyError(f"unknown node {name!r}")
    neighbours: dict[str, list[str]] = {name: [] for name in nodes}
    for a, b in edges:
        if a not in nodes or b not in nodes:
            raise KeyError(f"edge ({a!r}, {b!r}) names an unknown node")
        if b not in neighbours[a]:
            neighbours[a].append(b)
        if a not in neighbours[b]:
            neighbours[b].append(a)

    best = None
    rejected = []
    explored = 0
    truncated = False
    stack = [(start, [start])]
    while stack:
        node, path = stack.pop()
        if node == end:
            explored += 1
            if explored > limit:
                truncated = True
                break
            points = points_for(path)
            try:
                length = routed_length(points, radius)
            except RouteError as exc:
                rejected.append((list(path), str(exc)))
                continue
            if best is None or length < best[1] - TOLERANCE:
                best = (list(path), length)
            continue
        for nxt in sorted(neighbours[node], key=str, reverse=True):
            if nxt not in path:
                stack.append((nxt, path + [nxt]))
    if best is None:
        if not explored:
            raise RouteError([Problem("tight", 0, message=f"no path from {start} to {end} in the clip graph")])
        raise RouteError(
            [Problem("tight", 0, message=f"no path from {start} to {end} can be bent at radius {radius:g} mm")]
        )
    return GraphRoute(best[0], best[1], explored, rejected, truncated)


# ----- bundles (BW-WIR-05) -----------------------------------------------------


def shared_run(paths) -> list:
    """Longest run of names that every path contains, in the same order and
    adjacent. Empty when the wires share fewer than two names."""
    paths = [list(p) for p in paths]
    if not paths:
        return []
    first = paths[0]
    best = []
    for i in range(len(first)):
        for j in range(len(first), i + len(best), -1):
            candidate = first[i:j]
            if len(candidate) < 2:
                break
            if all(_contains_run(p, candidate) for p in paths[1:]):
                best = candidate
                break
    return best


def _contains_run(path, run) -> bool:
    n = len(run)
    reverse = run[::-1]
    return any(path[k : k + n] in (run, reverse) for k in range(len(path) - n + 1))


def bundle_diameter(diameters, packing: float = 0.75) -> float:
    """Sleeve inner diameter for wires bundled loosely: the circle whose
    area is the wires' area over a packing density (0.75 is a loose round
    bundle; 0.9 is the tightest a hand-laid bundle gets)."""
    diameters = list(diameters)
    if len(diameters) == 1:
        return float(diameters[0])
    return math.sqrt(sum(d * d for d in diameters) / packing)
