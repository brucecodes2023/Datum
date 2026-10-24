# SPDX-License-Identifier: LGPL-2.1-or-later
"""The flat pattern of a sheet-metal part: shop DXF (MFG-03) and bend table (DRW-07).

Runs without the GUI (FreeCADCmd, the release package of MFG-04), so it reads the flat
from the SheetMetal add-on's unfolder functions, not from its GUI-made sketches:

* new unfolder (needs networkx): ``SheetMetalNewUnfolder.getUnfold`` gives the flat and
  one ``BendInfo`` per bend (angle with its sign = up or down, radius);
* old unfolder (no networkx): ``SheetMetalUnfolder.getUnfold`` gives the flat and the
  bend lines, without direction; they go to layer BEND.

The flat is laid in the XY plane like the add-on's unfold sketch (stationary face up, its
bounding box at the origin). Outer and inner wires go to CUT, bend lines to BEND_UP /
BEND_DOWN, the part number and revision to ETCH. Lines, circles and arcs are written as
they are; any other curve is approximated by arcs within 0.01 mm (bi-arcs).
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from bruceworx import flat_dxf, sheetmetal_rules as smr

ARC_TOLERANCE = 0.01  # mm, B-spline -> bi-arc fitting
DEFAULT_K = 0.4  # the add-on's own default when neither an unfold nor a rule gives one


@dataclass
class Bend:
    start: tuple  # (x, y) mm, a piece of the bend line in the flat
    end: tuple
    angle: float  # degrees bent (90 for a right angle)
    direction: str  # "up", "down" or "" (not known)
    radius: float  # inner radius, mm
    k_factor: float  # ANSI
    ba: float  # bend allowance, mm
    number: int = 0  # 1.. in the bend table; pieces of one bend share it


@dataclass
class FlatPattern:
    outer: list = field(default_factory=list)  # flat_dxf entity dicts (layer CUT)
    inner: list = field(default_factory=list)
    bends: list = field(default_factory=list)  # [Bend], one per bend-line piece
    thickness: float = 0.0
    width: float = 0.0  # bounding box of the outline, mm
    height: float = 0.0
    k_factor: float = DEFAULT_K
    unfolder: str = "new"  # "new" or "old"
    face: object = None  # the flat Part.Face in the XY plane (for the etch position)

    def bend_rows(self) -> list:
        """One dict per bend (pieces merged), in bend-table order."""
        seen = {}
        for bend in self.bends:
            seen.setdefault(bend.number, bend)
        return [{"direction": b.direction, "angle": b.angle, "radius": b.radius, "k_factor": b.k_factor,
                 "ba": b.ba} for _n, b in sorted(seen.items())]


# --- which object and face --------------------------------------------------------------------


def _proxy_class(obj) -> str:
    proxy = getattr(obj, "Proxy", None)
    return type(proxy).__name__ if proxy is not None else ""


def is_unfold(obj) -> bool:
    return _proxy_class(obj) == "SMUnfold"


def find_unfold(doc):
    """The document's first SheetMetal unfold object, or None."""
    return next((o for o in doc.Objects if is_unfold(o)), None)


def stationary_face(obj) -> str:
    """"FaceN": the largest planar face of the shape (what Unfold would pick)."""
    best = None
    for index, face in enumerate(obj.Shape.Faces):
        if face.Surface.__class__.__name__ == "Plane" and (best is None or face.Area > best[0] + 1e-9):
            best = (face.Area, index)
    if best is None:
        raise ValueError(f"{obj.Label} has no planar face to unfold from.")
    return f"Face{best[1] + 1}"


def new_unfolder_available() -> bool:
    try:
        import networkx  # noqa: F401, PLC0415
        import SheetMetalTools  # noqa: PLC0415
    except ImportError:
        return False
    try:
        return not SheetMetalTools.use_old_unfolder()
    except Exception:
        return True


# --- geometry ---------------------------------------------------------------------------------------


def _align_matrix(compound, root_face):
    """The add-on's SketchExtraction.move_to_origin: root face Z-up, U along X, box at the origin."""
    from FreeCAD import Matrix, Placement, Rotation, Vector  # noqa: PLC0415

    origin = root_face.valueAt(0, 0)
    x_axis = root_face.valueAt(1, 0) - origin
    z_axis = root_face.normalAt(0, 0)
    align = Placement(origin, Rotation(x_axis, Vector(), z_axis, "ZXY")).toMatrix().inverse()
    box = compound.transformed(align).BoundBox
    shift = Placement(Vector(-box.XMin, -box.YMin, -box.ZMin), Rotation()).toMatrix()
    overall = Matrix()
    overall.transform(Vector(), align)
    overall.transform(Vector(), shift)
    return overall


def _xy(v) -> tuple:
    return (float(v.x), float(v.y))


def _angle(center, point) -> float:
    return math.degrees(math.atan2(point[1] - center[1], point[0] - center[0])) % 360.0


def edge_entities(edge, layer: str, tolerance: float = ARC_TOLERANCE) -> list:
    """flat_dxf entities for one edge in the XY plane."""
    curve = edge.Curve
    kind = type(curve).__name__
    if kind in ("Line", "LineSegment"):
        return [flat_dxf.line(layer, _xy(edge.Vertexes[0].Point), _xy(edge.Vertexes[-1].Point))]
    if kind == "Circle":
        center, radius = _xy(curve.Center), float(curve.Radius)
        if edge.isClosed():
            return [flat_dxf.circle(layer, center, radius)]
        first, last = edge.FirstParameter, edge.LastParameter
        a_start = _angle(center, _xy(edge.valueAt(first)))
        a_mid = _angle(center, _xy(edge.valueAt(0.5 * (first + last))))
        a_end = _angle(center, _xy(edge.valueAt(last)))
        if flat_dxf._in_sweep(a_mid, a_start, a_end):
            return [flat_dxf.arc(layer, center, radius, a_start, a_end)]
        return [flat_dxf.arc(layer, center, radius, a_end, a_start)]
    # Anything else: a B-spline of the edge, fitted with arcs and lines.
    try:
        if kind == "BSplineCurve":
            spline = curve.copy()
            if not edge.isClosed():
                spline.segment(edge.FirstParameter, edge.LastParameter)
        else:
            spline = curve.toBSpline(edge.FirstParameter, edge.LastParameter)
        out = []
        for piece in spline.toBiArcs(tolerance):
            out += edge_entities(piece.toShape(), layer, tolerance)
        if out:
            return out
    except Exception:
        pass
    points = edge.discretize(Deflection=tolerance)
    return [flat_dxf.line(layer, _xy(a), _xy(b)) for a, b in zip(points, points[1:])]


def _top_face(shape, normal):
    faces = [f for f in shape.Faces if f.normalAt(0, 0).getAngle(normal) < 1e-6]
    if not faces:
        raise RuntimeError("the unfolded shape has no face along the stationary face's normal.")
    return max(faces, key=lambda f: f.Area)


def _thickness(shape, top) -> float:
    return shape.Volume / top.Area if top.Area > 0 else 0.0


def _distance_to_segment(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def flat_pattern(obj, face: str | None = None, k_factor: float | None = None, standard: str = "ansi",
                 unfolder: str | None = None) -> FlatPattern:
    """The flat of a SheetMetal unfold object, or of a sheet-metal solid unfolded from face
    (default: its largest planar face) with k_factor (default: the add-on's 0.4).
    unfolder "new" or "old" forces one; None takes the new one when networkx is there."""
    if is_unfold(obj):
        base, sub = obj.baseObject[0], obj.baseObject[1][0]
        if hasattr(base, "getSubObject") and "." in sub:  # "Body.Face3" style links
            sub = sub.split(".")[-1]
        face = face or sub
        k_factor = obj.KFactor if k_factor is None else k_factor
        standard = getattr(obj, "KFactorStandard", standard) or standard
        obj = base
    face = face or stationary_face(obj)
    k_factor = DEFAULT_K if k_factor is None else float(k_factor)
    k_ansi = k_factor / 2.0 if standard == "din" else k_factor
    use_new = new_unfolder_available() if unfolder is None else unfolder == "new"
    if use_new:
        return _flat_new(obj, face, k_factor, standard, k_ansi)
    return _flat_old(obj, face, k_factor, standard, k_ansi)


def _outline(flat, top, root_face, pattern: FlatPattern):
    matrix = _align_matrix(top.OuterWire, root_face)
    aligned = top.transformed(matrix)
    pattern.face = aligned
    outer = aligned.OuterWire
    for edge in outer.Edges:
        pattern.outer += edge_entities(edge, flat_dxf.CUT)
    for wire in aligned.Wires:
        if wire.isSame(outer):
            continue
        for edge in wire.Edges:
            pattern.inner += edge_entities(edge, flat_dxf.CUT)
    box = outer.BoundBox
    pattern.width, pattern.height = box.XLength, box.YLength
    pattern.thickness = _thickness(flat, top)
    return matrix


def _number_bends(pattern: FlatPattern, keys):
    """Bend numbers left to right (then bottom to top) by each bend's key point."""
    order = sorted(set(keys), key=lambda k: (round(k[0], 3), round(k[1], 3)))
    for bend, key in zip(pattern.bends, keys):
        bend.number = order.index(key) + 1


def _flat_new(obj, face, k_factor, standard, k_ansi) -> FlatPattern:
    import SheetMetalNewUnfolder as unfolder  # noqa: PLC0415

    bac = unfolder.BendAllowanceCalculator.from_single_value(k_factor, standard)
    root_face, flat, bend_lines, normal, infos = unfolder.getUnfold(bac, obj, face)
    pattern = FlatPattern(k_factor=k_ansi, unfolder="new")
    top = _top_face(flat, normal)
    matrix = _outline(flat, top, root_face, pattern)
    thickness = pattern.thickness
    # BendInfo lines are in the add-on's sketch frame, which is ours up to cleanup noise:
    # each trimmed piece takes the bend whose line is nearest.
    lines = [(_xy(i.line.Vertexes[0].Point), _xy(i.line.Vertexes[-1].Point)) for i in infos]
    keys = []
    for edge in bend_lines.transformed(matrix).Edges:
        a, b = _xy(edge.Vertexes[0].Point), _xy(edge.Vertexes[-1].Point)
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        if not lines:
            break
        index = min(range(len(lines)), key=lambda i: _distance_to_segment(mid, *lines[i]))
        info = infos[index]
        up = info.angle >= 0
        angle = 180.0 - abs(info.angle)
        radius = info.radius if up else info.radius - thickness  # the reported face is on the stationary side
        pattern.bends.append(Bend(a, b, angle, "up" if up else "down", radius, k_ansi,
                                  smr.bend_allowance(angle, radius, thickness, k_ansi)))
        keys.append(tuple(round(v, 6) for v in lines[index][0] + lines[index][1]))
    _number_bends(pattern, [((k[0] + k[2]) / 2.0, (k[1] + k[3]) / 2.0) for k in keys])
    return pattern


def _flat_old(obj, face, k_factor, standard, k_ansi) -> FlatPattern:
    import SheetMetalUnfolder as unfolder  # noqa: PLC0415

    shape, folds, normal, *_rest = unfolder.getUnfold({1: k_factor}, obj, face, standard)
    if shape is None:
        raise RuntimeError(f"the old unfolder could not unfold {obj.Label} from {face}.")
    pattern = FlatPattern(k_factor=k_ansi, unfolder="old")
    top = _top_face(shape, normal)
    root_face = obj.Shape.getElement(face) if hasattr(obj.Shape, "getElement") else top
    if root_face.normalAt(0, 0).getAngle(normal) > 1e-6:
        root_face = top
    matrix = _outline(shape, top, root_face, pattern)
    keys = []
    for edge in (folds.transformed(matrix).Edges if folds is not None else []):
        a, b = _xy(edge.Vertexes[0].Point), _xy(edge.Vertexes[-1].Point)
        pattern.bends.append(Bend(a, b, 0.0, "", 0.0, k_ansi, 0.0))
        keys.append(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
    _number_bends(pattern, keys)
    return pattern


# --- DXF ----------------------------------------------------------------------------------------------


def _text_fits(face, center, height, text) -> bool:
    from FreeCAD import Vector  # noqa: PLC0415

    half_w, half_h = 0.45 * height * max(1, len(text)), 0.6 * height
    corners = [(center[0] + sx * half_w, center[1] + sy * half_h) for sx in (-1, 1) for sy in (-1, 1)]
    corners.append(center)
    return all(face.isInside(Vector(x, y, 0), 1e-6, True) for x, y in corners)


def etch_position(pattern: FlatPattern, text: str):
    """(x, y, height) for the etched text: inside the material, as near the middle as it fits;
    None when it fits nowhere (then nothing is etched)."""
    face = pattern.face
    if face is None or not text:
        return None
    short = min(pattern.width, pattern.height)
    start = max(1.0, min(6.0, short / 8.0))
    com = face.CenterOfMass
    middle = (com.x, com.y)
    for height in [start, start * 0.75, start * 0.5, 1.0]:
        if height < 1.0 - 1e-9:
            continue
        steps = 12
        candidates = [middle] + [(pattern.width * i / steps, pattern.height * j / steps)
                                 for i in range(1, steps) for j in range(1, steps)]
        candidates.sort(key=lambda p: math.hypot(p[0] - middle[0], p[1] - middle[1]))
        for point in candidates:
            if _text_fits(face, point, height, text):
                return point[0], point[1], height
    return None


def entities(pattern: FlatPattern, profile: dict | None = None, etch_text: str = "") -> list:
    """The DXF entities of a flat, by the vault's export profile (bend_layers, etch)."""
    profile = dict({"bend_layers": True, "etch": True}, **(profile or {}))
    out = list(pattern.outer) + list(pattern.inner)
    if profile.get("bend_layers"):
        for bend in pattern.bends:
            layer = {"up": flat_dxf.BEND_UP, "down": flat_dxf.BEND_DOWN}.get(bend.direction, flat_dxf.BEND)
            out.append(flat_dxf.line(layer, bend.start, bend.end))
    if profile.get("etch") and etch_text:
        place = etch_position(pattern, etch_text)
        if place is not None:
            out.append(flat_dxf.text(flat_dxf.ETCH, place[:2], place[2], etch_text))
    return out


def etch_text(part_number: str, revision: str) -> str:
    """"BW-01-01001 A" (the part number and revision), or the one there is."""
    return " ".join(t for t in (str(part_number or "").strip(), str(revision or "").strip()) if t)


def file_name(part_number: str, revision: str, fallback: str = "flat") -> str:
    """P/N-REV.dxf ("BW-01-01001-A.dxf"); fallback.dxf without a part number."""
    stem = "-".join(t for t in (str(part_number or "").strip(), str(revision or "").strip()) if t) or fallback
    return re.sub(r'[\\/:*?"<>|]+', "_", stem) + ".dxf"


def export_dxf(obj, path: str, units: str = "mm", profile: dict | None = None, etch: str = "",
               face: str | None = None, k_factor: float | None = None, unfolder: str | None = None) -> FlatPattern:
    """Unfold obj (an unfold object or a sheet-metal solid) and write the shop DXF to path,
    in units ("mm" or "in"). Returns the flat (for the bend table)."""
    pattern = flat_pattern(obj, face=face, k_factor=k_factor, unfolder=unfolder)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    flat_dxf.save(path, entities(pattern, profile, etch), smr.norm_units(units))
    return pattern


def bend_table(pattern: FlatPattern, units: str = "mm") -> list:
    """The bend table rows (header first) in units."""
    return [smr.BEND_TABLE_HEADER] + smr.bend_table(pattern.bend_rows(), units)
