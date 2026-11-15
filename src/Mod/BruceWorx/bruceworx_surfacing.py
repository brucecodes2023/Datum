# SPDX-License-Identifier: LGPL-2.1-or-later
"""Surfacing parity (roadmap MOD-08): the surface tools Fusion has and FreeCAD lacks, and a continuity check.

What FreeCAD 1.1 already gives, and the Design workspace's Surface tab shows, is used as it is:
Surface Filling (n-sided patch with G0/G1/G2 boundary conditions), Sections, GeomFill, Blend Curve,
Extend Face (by a fraction of the face, every side), Part Loft/Sweep/Ruled, Offset, Thickness (on a
solid), Make Solid; Zebra and Curvature maps are BruceWorx's own (MOD-11, bruceworx_analysis_gui).

Added here, each a parametric Part::FeaturePython that rebuilds when its inputs change:

* Trim: cut a face (or surface) with curves projected onto it; keep the side a point names, or the
  outside/inside of a closed curve, or the larger/smaller piece of an open one.
* Untrim: give a trimmed face its holes back (Holes), or its whole underlying surface (Natural).
* Extend: carry a face on past chosen boundary edges by a distance, Natural (the surface's own
  polynomial continued: curvature continuous) or Tangent (ruled along the cross-boundary tangent: G1).
* Stitch: sew surfaces into one shell, and into a solid when the shell is closed; reports its free edges.
* Thicken: a surface or shell made into a solid of a thickness, outside, inside or both sides.
* Replace face: a face of a solid swapped for a surface; the sides are carried to it along the face's
  normal (exact for sides square to the face, as a pocket or boss has).
* Patch (one click): Surface Filling over the hole a free edge borders, tangent (G1) to the faces round
  it. The feature made is FreeCAD's Surface::Filling.

Continuity (``check``): for every edge two faces share (or, between separate surfaces, edges that lie
within a search distance of each other), sampled along the edge:

    G0  the gap between the two surfaces, mm
    G1  the angle between their normals, degrees
    G2  the difference of their normal curvatures across the edge, relative to the larger

The pass limits are OCCT's own defaults for local surface continuity (LocalAnalysis_SurfaceContinuity:
EpsC0 = 0.001 mm, EpsG1 = 0.001 rad, Percent = 0.01, and Maxlen = 10000 mm below which a curvature
counts as zero). They are preferences (Preferences/BruceWorx/Surfacing: G0Tolerance, G1Tolerance,
G2Tolerance, ContinuitySearch). The search distance (how far apart two separate surfaces' edges may be
and still be compared, default 1 mm) is a setting of this check, not a standard.

Off switch: Preferences/BruceWorx/Surfacing (default on).
"""

from __future__ import annotations

import math

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "Surfacing"
TOL_PATH = "User parameter:BaseApp/Preferences/BruceWorx/Surfacing"
# OCCT LocalAnalysis_SurfaceContinuity defaults (LocalAnalysis_SurfaceContinuity.hxx)
OCCT_EPS_C0 = 0.001  # mm
OCCT_EPS_G1 = 0.001  # rad
OCCT_PERCENT = 0.01  # relative curvature variation
OCCT_MAXLEN = 10000.0  # mm: curvature below 1/Maxlen is zero
SEARCH_DEFAULT = 1.0  # mm: a setting of this check
SAMPLES = 21
KIND = "SurfaceKind"
STATUS = "SurfaceStatus"
GROUP = "Surface"

TRIM, UNTRIM, EXTEND, STITCH, THICKEN, REPLACE = "Trim", "Untrim", "Extend", "Stitch", "Thicken", "ReplaceFace"
LABELS = {TRIM: "Trim", UNTRIM: "Untrim", EXTEND: "Extend", STITCH: "Stitch", THICKEN: "Thicken",
          REPLACE: "Replace face"}


class SurfacingError(Exception):
    """Something the user can fix, in plain words."""


def _app():
    import FreeCAD

    return FreeCAD


def _part():
    import Part

    return Part


def enabled() -> bool:
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


def tolerances() -> dict:
    """The pass limits of the continuity check: g0 mm, g1 rad, g2 relative, zero curvature 1/mm, search mm."""
    p = _app().ParamGet(TOL_PATH)
    return {"g0": p.GetFloat("G0Tolerance", OCCT_EPS_C0), "g1": p.GetFloat("G1Tolerance", OCCT_EPS_G1),
            "g2": p.GetFloat("G2Tolerance", OCCT_PERCENT), "zero": 1.0 / OCCT_MAXLEN,
            "search": p.GetFloat("ContinuitySearch", SEARCH_DEFAULT)}


# ----- continuity -------------------------------------------------------------------------------------

def _normal_curvature(surface, u, v, direction, normal):
    """The normal curvature of ``surface`` at (u, v) in the tangent ``direction``, signed against ``normal``."""
    try:
        k1 = surface.curvature(u, v, "Max")
        k2 = surface.curvature(u, v, "Min")
    except Exception:  # noqa: BLE001 -- a degenerate point
        return None
    try:
        d1, _d2 = surface.curvatureDirections(u, v)
    except Exception:  # noqa: BLE001 -- umbilic: every direction the same
        d1 = None
    if d1 is None or d1.Length < 1e-12:
        k = k1
    else:
        c = d1.normalize().dot(direction)
        k = k1 * c * c + k2 * (1.0 - c * c)
    if surface.normal(u, v).dot(normal) < 0:
        k = -k  # OCCT signs curvature against the surface's own normal
    return k


def _uv(face, point):
    u, v = face.Surface.parameter(point)
    return u, v


def edge_continuity(edge, face_a, face_b, samples=SAMPLES, tol=None):
    """Continuity across ``edge`` between ``face_a`` and ``face_b``, sampled ``samples`` times along it
    (ends left out by a half step). Returns {"g0": max gap mm, "g1": max angle rad, "g2": max relative
    curvature difference, "level": "G2"|"G1"|"G0"|"none", "worst": point of the worst G-level}."""
    tol = tol or tolerances()
    first, last = edge.ParameterRange
    gap = angle = g2 = 0.0
    worst, worst_score = None, -1.0
    for i in range(samples):
        t = first + (last - first) * (i + 0.5) / samples
        p = edge.valueAt(t)
        try:
            tangent = edge.tangentAt(t)
        except Exception:  # noqa: BLE001 -- a degenerate edge
            continue
        ua, va = _uv(face_a, p)
        ub, vb = _uv(face_b, p)
        pa, pb = face_a.Surface.value(ua, va), face_b.Surface.value(ub, vb)
        na, nb = face_a.Surface.normal(ua, va), face_b.Surface.normal(ub, vb)
        if na.Length < 1e-12 or nb.Length < 1e-12:
            continue
        na, nb = na.normalize(), nb.normalize()
        if nb.dot(na) < 0:
            nb = -nb  # separate surfaces may face either way
        g = (pa - pb).Length
        a = math.acos(max(-1.0, min(1.0, na.dot(nb))))
        # across the edge, in each face's own tangent plane
        ka = _normal_curvature(face_a.Surface, ua, va, na.cross(tangent).normalize(), na)
        kb = _normal_curvature(face_b.Surface, ub, vb, nb.cross(tangent).normalize(), na)
        r = 0.0
        if ka is not None and kb is not None:
            big = max(abs(ka), abs(kb))
            if big > tol["zero"]:
                r = abs(ka - kb) / big
        gap, angle, g2 = max(gap, g), max(angle, a), max(g2, r)
        score = g / tol["g0"] + a / tol["g1"] + r / tol["g2"]
        if score > worst_score:
            worst, worst_score = p, score
    if gap > tol["g0"]:
        level = "none"
    elif angle > tol["g1"]:
        level = "G0"
    elif g2 > tol["g2"]:
        level = "G1"
    else:
        level = "G2"
    return {"g0": gap, "g1": angle, "g2": g2, "level": level, "worst": worst}


def _shared(shape):
    """[(edge, face a, face b)] of the edges two faces of ``shape`` share (seams left out)."""
    Part = _part()
    out = []
    for edge in shape.Edges:
        if edge.isDegenerated() if hasattr(edge, "isDegenerated") else False:
            continue
        faces = shape.ancestorsOfType(edge, Part.Face)
        uniq = []
        for f in faces:
            if not any(f.isSame(g) for g in uniq):
                uniq.append(f)
        if len(uniq) == 2:
            out.append((edge, uniq[0], uniq[1]))
    return out


def _free_edges(shape):
    Part = _part()
    return [e for e in shape.Edges if len(shape.ancestorsOfType(e, Part.Face)) == 1
            and not (hasattr(e, "isDegenerated") and e.isDegenerated())]


def _near(edge_a, edge_b, search, n=5):
    """True when every sample of the shorter edge lies within ``search`` of the other."""
    short, long_ = (edge_a, edge_b) if edge_a.Length <= edge_b.Length else (edge_b, edge_a)
    if not short.BoundBox.isValid() or short.Length < 1e-9:
        return False
    bb = long_.BoundBox
    bb.enlarge(search)
    if not bb.intersect(short.BoundBox):
        return False
    first, last = short.ParameterRange
    Part = _part()
    for i in range(n):
        p = short.valueAt(first + (last - first) * i / (n - 1))
        if long_.distToShape(Part.Vertex(p))[0] > search:
            return False
    return True


def pairs(shapes, search=None):
    """[(edge, face a, face b)] to check: the edges two faces of a shape share, and the free edges of
    different faces (unsewn surfaces) that lie within ``search`` of each other (sampled along the shorter)."""
    search = tolerances()["search"] if search is None else search
    out = []
    loose = []  # (edge, face)
    Part = _part()
    for shape in shapes:
        out += _shared(shape)
        for edge in _free_edges(shape):
            loose.append((edge, shape.ancestorsOfType(edge, Part.Face)[0]))
    for a in range(len(loose)):
        for b in range(a + 1, len(loose)):
            ea, fa = loose[a]
            eb, fb = loose[b]
            if fa.isSame(fb):
                continue  # a face's own free edges are its outline, not a join
            if _near(ea, eb, search):
                out.append((ea if ea.Length <= eb.Length else eb, fa, fb))
    return out


def check(shapes, samples=SAMPLES, tol=None):
    """The continuity of every edge to check in ``shapes`` (Part shapes): [dict] as edge_continuity
    gives, with "edge" (the edge sampled) and "index" (1-based order). Counts per level in ``summary``."""
    tol = tol or tolerances()
    rows = []
    for k, (edge, fa, fb) in enumerate(pairs(list(shapes), tol["search"]), 1):
        row = edge_continuity(edge, fa, fb, samples, tol)
        row.update(edge=edge, index=k)
        rows.append(row)
    return rows


def summary(rows) -> dict:
    out = {"G2": 0, "G1": 0, "G0": 0, "none": 0}
    for r in rows:
        out[r["level"]] += 1
    return out


def worst_level(rows) -> str:
    order = ["none", "G0", "G1", "G2"]
    return min((r["level"] for r in rows), key=order.index) if rows else "G2"


# ----- shapes -----------------------------------------------------------------------------------------

def _one_face(shape, what="a face"):
    faces = shape.Faces
    if len(faces) != 1:
        raise SurfacingError(f"Pick {what}: this has {len(faces)} faces")
    return faces[0]


def _sew(faces, tol=1e-6):
    Part = _part()
    if len(faces) == 1:
        return Part.Shell([faces[0]]) if faces[0].ShapeType == "Face" else faces[0]
    comp = Part.makeCompound(faces)
    comp.sewShape(tol)
    shells = comp.Shells
    if len(shells) == 1 and not comp.Faces[len(shells[0].Faces):]:
        return shells[0]
    return comp


def _face_normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    return n


def bop_errors(shape) -> set:
    """The kinds of error OCCT's BOP check (BOPAlgo_ArgumentAnalyzer, what Part Check Geometry runs) finds."""
    try:
        shape.check(True)
    except Exception as exc:  # noqa: BLE001 -- the check raises with one line per error
        lines = str(exc).splitlines()[1:]
        return {x.split(":")[-1].strip() for x in lines if x.strip()} or {"invalid"}
    return set()


def validated(shape, tol=1e-4):
    """``shape`` checked as Part Check Geometry would: a self-intersection is refused; edges whose stored
    tolerance is below their real distance from the surface (common after an offset) get it raised, by
    tenfold steps from ``tol`` to 100 x ``tol``. Returns (shape, edge tolerance used: 0 when none was)."""
    errors = bop_errors(shape)
    if not errors:
        return shape, 0.0
    if any("SelfIntersect" in e for e in errors):
        raise SurfacingError("The result runs through itself (BOP check: self-intersection): a fold or a "
                             "section thinner than the offset")
    if errors <= {"BOPAlgo_InvalidCurveOnSurface"}:
        for k in range(3):
            t = tol * 10 ** k
            fixed = shape.copy()
            fixed.fixTolerance(t)
            if not bop_errors(fixed):
                return fixed, t
    raise SurfacingError("The result fails the geometry check: " + ", ".join(sorted(errors)))


def trim(target, tools, keep="outside", keep_point=None, direction=None, tol=1e-4):
    """``target`` (faces) cut by the curves of ``tools`` projected onto it along ``direction`` (default: the
    first face's normal). ``keep``: "outside"/"inside" of a closed curve, "larger"/"smaller", or "point"
    (the piece nearest ``keep_point``). Returns the kept faces, sewn."""
    Part = _part()
    App = _app()
    faces = list(target.Faces)
    if not faces:
        raise SurfacingError("Nothing to trim: pick a surface or face")
    edges = list(tools.Edges)
    if not edges:
        raise SurfacingError("Pick the curve to trim with (a sketch or edges)")
    kept = []
    for face in faces:
        on = [e for e in edges if face.distToShape(e)[0] < tol]
        if len(on) == len(edges):
            projected = edges
        else:
            d = App.Vector(direction) if direction is not None else _face_normal(face)
            proj = face.makeParallelProjection(Part.Wire(Part.__sortEdges__(edges)) if len(edges) > 1 else edges[0], d)
            projected = list(proj.Edges)
        if not projected:
            kept.append(face)  # the curve misses this face
            continue
        result, _map = face.generalFuse(projected, tol)
        pieces = list(result.Faces)
        if len(pieces) < 2:
            kept.append(face)
            continue
        cut = Part.makeCompound(projected)

        def inner(piece):
            # a piece bounded by the curve alone is inside it
            return all(cut.distToShape(Part.Vertex(e.valueAt((e.FirstParameter + e.LastParameter) / 2)))[0] < tol * 10
                       for e in piece.Edges)

        if keep == "point":
            if keep_point is None:
                raise SurfacingError("Pick the side to keep")
            pt = Part.Vertex(App.Vector(keep_point))
            pieces = [min(pieces, key=lambda f: f.distToShape(pt)[0])]
        elif keep in ("outside", "inside"):
            ins = [f for f in pieces if inner(f)]
            if not ins:
                raise SurfacingError("The curve does not close on the face: keep the larger or smaller piece, "
                                     "or pick the side to keep")
            pieces = ins if keep == "inside" else [f for f in pieces if not inner(f)]
        elif keep == "larger":
            pieces = [max(pieces, key=lambda f: f.Area)]
        elif keep == "smaller":
            pieces = [min(pieces, key=lambda f: f.Area)]
        else:
            raise SurfacingError(f"Keep what? ({keep})")
        kept += pieces
    return _sew(kept)


def untrim(target, mode="holes"):
    """Each face of ``target`` given back its holes ("holes": the outer boundary kept) or its whole
    underlying surface ("natural": a bounded surface's full extent, an unbounded one's parameter box)."""
    Part = _part()
    out = []
    for face in target.Faces:
        surface = face.Surface
        if mode == "holes":
            new = Part.Face(surface, face.OuterWire)
            if not new.isValid():
                new.fix(1e-7, 1e-7, 1e-7)
            if face.Orientation != new.Orientation:
                new.reverse()
            out.append(new)
        elif mode == "natural":
            try:
                u0, u1, v0, v1 = surface.bounds()
            except Exception:  # noqa: BLE001
                u0 = None
            if u0 is None or any(abs(x) > 1e50 for x in (u0, u1, v0, v1)):
                u0, u1, v0, v1 = face.ParameterRange  # a plane, cylinder ... has no edge of its own
            new = surface.toShape(u0, u1, v0, v1)
            if face.Orientation == "Reversed":
                new.reverse()
            out.append(new)
        else:
            raise SurfacingError(f"Untrim how? ({mode})")
    return _sew(out)


def _which_side(face, edge, eps=1e-6):
    """Which parameter boundary of ``face`` ``edge`` lies on: ("u", value, +1 at umax / -1 at umin) or
    ("v", ...); None when it is not on an iso-parametric boundary."""
    u0, u1, v0, v1 = face.ParameterRange
    first, last = edge.ParameterRange
    uvs = [_uv(face, edge.valueAt(first + (last - first) * k / 6)) for k in range(7)]
    span_u, span_v = max(u1 - u0, 1e-12), max(v1 - v0, 1e-12)
    for axis, lo, hi, idx, span in (("u", u0, u1, 0, span_u), ("v", v0, v1, 1, span_v)):
        vals = [uv[idx] for uv in uvs]
        if all(abs(x - hi) < eps * span + 1e-9 for x in vals):
            return axis, hi, +1
        if all(abs(x - lo) < eps * span + 1e-9 for x in vals):
            return axis, lo, -1
    return None


def _extension(face, edge, distance, mode, cols=24):
    """The extension face of ``face`` past ``edge``."""
    Part = _part()
    App = _app()
    side = _which_side(face, edge, 1e-5)
    if side is None:
        raise SurfacingError("Extend works on a face's own boundary edges (where the surface ends), "
                             "not on a trimmed edge")
    axis, at, sign = side
    s = face.Surface
    u0, u1, v0, v1 = face.ParameterRange
    lo, hi = (v0, v1) if axis == "u" else (u0, u1)

    def point(a, b):  # a along the extended axis, b along the edge
        return s.value(a, b) if axis == "u" else s.value(b, a)

    def deriv(a, b):
        d = s.getDN(a, b, 1, 0) if axis == "u" else s.getDN(b, a, 0, 1)
        return App.Vector(d)

    if mode == "tangent":
        ribs = []
        for j in range(cols + 1):
            b = lo + (hi - lo) * j / cols
            p = point(at, b)
            t = deriv(at, b)
            if t.Length < 1e-12:
                raise SurfacingError("The surface is degenerate at that edge")
            ribs.append((p, p + t.normalize() * (distance * sign)))
        e0 = Part.BSplineCurve()
        e0.interpolate([r[0] for r in ribs])
        e1 = Part.BSplineCurve()
        e1.interpolate([r[1] for r in ribs])
        return Part.makeRuledSurface(e0.toShape(), e1.toShape())
    if mode != "natural":
        raise SurfacingError(f"Extend how? ({mode})")
    # parameter step for the distance, from the cross-boundary speed at the middle of the edge
    speed = deriv(at, (lo + hi) / 2).Length
    if speed < 1e-12:
        raise SurfacingError("The surface is degenerate at that edge")
    da = distance / speed * sign
    analytic = not s.TypeId.startswith(("Part::GeomBSpline", "Part::GeomBezier"))
    a0, a1 = (at, at + da) if sign > 0 else (at + da, at)
    if analytic:
        return s.toShape(a0, a1, lo, hi) if axis == "u" else s.toShape(lo, hi, a0, a1)
    ext = _continued(s, axis, sign, abs(da))
    return ext.toShape(a0, a1, lo, hi) if axis == "u" else ext.toShape(lo, hi, a0, a1)


def _blossom(points, ts):
    """The blossom of the polynomial whose Bezier points (homogeneous 4-tuples) are ``points``, at the
    local parameters ``ts`` (0 and 1 are the span's ends; outside them it extrapolates, exactly)."""
    pts = [list(p) for p in points]
    for t in ts:
        pts = [[(1 - t) * a + t * b for a, b in zip(pts[i], pts[i + 1])] for i in range(len(pts) - 1)]
    return pts[0]


def _continued(surface, axis, sign, delta):
    """``surface`` (B-spline or Bezier) continued past its ``axis`` end (``sign`` +1: the high end) by
    ``delta`` in parameter: the end span's own polynomial, re-expressed over the new range by blossoming,
    so the boundary curve is shared exactly and the join is as smooth as the polynomial (C-infinity).
    Returns a B-spline surface over [end, end + delta] (or [start - delta, start]) in that parameter."""
    Part = _part()
    App = _app()
    bs = surface.toBSpline() if not surface.TypeId.startswith("Part::GeomBSpline") else surface.copy()
    if axis == "v":
        bs.exchangeUV()
    if bs.isUPeriodic():
        raise SurfacingError("The surface closes on itself that way: there is nothing to extend")
    knots = bs.getUKnots()
    u0, u1, v0, v1 = bs.bounds()
    a, b = (knots[-2], knots[-1]) if sign > 0 else (knots[0], knots[1])
    seg = bs.copy()
    seg.segment(a, b, v0, v1)
    p = seg.UDegree
    poles = seg.getPoles()  # rows along u (p + 1 of them), columns along v
    weights = seg.getWeights()
    c, d = (b, b + delta) if sign > 0 else (a - delta, a)
    tc, td = (c - a) / (b - a), (d - a) / (b - a)
    rows = [[None] * len(poles[0]) for _ in range(p + 1)]
    wrows = [[None] * len(poles[0]) for _ in range(p + 1)]
    for j in range(len(poles[0])):
        column = [(poles[i][j].x * weights[i][j], poles[i][j].y * weights[i][j], poles[i][j].z * weights[i][j],
                   weights[i][j]) for i in range(p + 1)]
        for i in range(p + 1):
            x, y, z, w = _blossom(column, [tc] * (p - i) + [td] * i)
            rows[i][j] = App.Vector(x / w, y / w, z / w)
            wrows[i][j] = w
    out = Part.BSplineSurface()
    out.buildFromPolesMultsKnots(rows, [p + 1, p + 1], seg.getVMultiplicities(), [c, d], seg.getVKnots(),
                                 False, seg.isVPeriodic(), p, seg.VDegree, wrows)
    if axis == "v":
        out.exchangeUV()
    return out


def extend(target, edge_names, distance, mode="natural"):
    """``target`` (one face) carried on past its edges ``edge_names`` ("Edge3" ...) by ``distance``.
    Returns the face and its extensions sewn into one shell."""
    if distance <= 0:
        raise SurfacingError("The distance must be more than zero")
    face = _one_face(target)
    if not edge_names:
        raise SurfacingError("Pick the edges to extend past")
    faces = [face]
    for name in edge_names:
        idx = int(str(name).replace("Edge", "")) - 1
        if not 0 <= idx < len(target.Edges):
            raise SurfacingError(f"{name} is not an edge of the face")
        faces.append(_extension(face, target.Edges[idx], distance, mode))
    return _sew(faces, 1e-5)


def stitch(shapes, tol=1e-4, solid=True):
    """Sew ``shapes`` (Part shapes holding faces) into one shell, and into a solid when it closes.
    Returns (shape, free edge count)."""
    Part = _part()
    faces = [f for s in shapes for f in s.Faces]
    if not faces:
        raise SurfacingError("Pick the surfaces to stitch")
    comp = Part.makeCompound(faces)
    comp.sewShape(tol)
    shells = comp.Shells
    shape = shells[0] if len(shells) == 1 else comp
    free = len(_free_edges(shape))
    if solid and len(shells) == 1 and free == 0:
        out = Part.Solid(shells[0])
        if out.Volume < 0:
            out.reverse()
        if not out.isValid():
            out.fix(tol, tol, tol)
        return validated(out, tol)[0], 0
    return shape, free


def thicken(target, thickness, side="outside", tol=1e-4):
    """A solid made from the surface ``target``: ``thickness`` along its normal ("outside"), against it
    ("inside") or half each way ("both")."""
    if thickness <= 0:
        raise SurfacingError("The thickness must be more than zero")
    shell = _sew(list(target.Faces), tol) if len(target.Faces) > 1 else _part().Shell([_one_face(target)])
    if side == "outside":
        start, t = shell, thickness
    elif side == "inside":
        start, t = shell, -thickness
    elif side == "both":
        start = shell.makeOffsetShape(-thickness / 2, tol, join=0, fill=False)
        t = thickness
    else:
        raise SurfacingError(f"Thicken which side? ({side})")
    out = start.makeOffsetShape(t, tol, join=0, fill=True)
    solids = out.Solids
    if len(solids) != 1:
        raise SurfacingError("The surface could not be thickened: check it for folds tighter than the thickness")
    out = solids[0]
    if out.Volume < 0:
        out.reverse()
    return validated(out, tol)[0]


def replace_face(solid_shape, face_name, tool, tol=1e-4):
    """``solid_shape`` with its face ``face_name`` replaced by the surface ``tool``. The face's sides are
    carried to the surface along the face's normal. The surface must cover the face's outline."""
    Part = _part()
    solids = solid_shape.Solids
    if len(solids) != 1:
        raise SurfacingError("Replace face works on a single solid")
    solid = solids[0]
    idx = int(str(face_name).replace("Face", "")) - 1
    if not 0 <= idx < len(solid.Faces):
        raise SurfacingError(f"{face_name} is not a face of the solid")
    face = solid.Faces[idx]
    n = _face_normal(face)
    if n.Length < 1e-12:
        raise SurfacingError("The face has no normal to work along")
    n = n.normalize()
    surf_faces = list(tool.Faces)
    if not surf_faces:
        raise SurfacingError("Pick the surface to replace the face with")
    surface = Part.makeCompound(surf_faces)
    reach = solid.BoundBox.DiagonalLength + surface.BoundBox.DiagonalLength
    grown = solid.fuse(face.extrude(n * reach)).removeSplitter()
    pieces, _m = grown.generalFuse([surface], tol)
    keep = []
    # a point that must stay: just inside the solid's far side, on the line through the face's centre
    # along its normal (the surface would have to cut the whole solid away to reach it)
    centre = face.CenterOfMass
    line = Part.LineSegment(centre + n * reach, centre - n * reach).toShape()
    inside = solid.common(line)
    ends = [v.Point for v in inside.Vertexes]
    if not ends:
        raise SurfacingError("The solid has no material behind the face")
    far = min(ends, key=lambda q: q.dot(n))
    ref = far + n * min(0.01, solid.BoundBox.DiagonalLength * 1e-4)
    for piece in pieces.Solids:
        if piece.isInside(ref, tol, True):
            keep.append(piece)
    if not keep:
        raise SurfacingError("The surface cuts the solid in a way that leaves nothing behind the face")
    # a surface that does not cover the face's outline leaves the solid joined to the far end of the
    # extrusion: nothing was cut off there
    centre_n = centre.dot(n)
    if max(v.Point.dot(n) - centre_n for k in keep for v in k.Vertexes) > reach / 2:
        raise SurfacingError("The surface does not cover the face's outline: extend it first")
    out = keep[0]
    for extra in keep[1:]:
        out = out.fuse(extra)
    out = out.removeSplitter()
    if len(out.Solids) != 1 or not out.isValid():
        raise SurfacingError("The result is not one valid solid: the surface may cross the solid's sides")
    return validated(out.Solids[0], tol)[0]


# ----- patch: Surface Filling over a hole ------------------------------------------------------------

def boundary_loop(shape, edge_name):
    """The names of the free edges forming the closed loop ``edge_name`` is on, in order."""
    Part = _part()
    free = {}
    for i, e in enumerate(shape.Edges, 1):
        if len(shape.ancestorsOfType(e, Part.Face)) == 1:
            free[f"Edge{i}"] = e
    if edge_name not in free:
        raise SurfacingError(f"{edge_name} is not on the edge of a hole or of the surface")
    loop, todo = [edge_name], True
    while todo:
        todo = False
        ends = [v.Point for name in loop for v in free[name].Vertexes]
        for name, e in free.items():
            if name in loop:
                continue
            if any((v.Point - p).Length < 1e-6 for v in e.Vertexes for p in ends):
                loop.append(name)
                todo = True
    wire = Part.Wire(Part.__sortEdges__([free[n] for n in loop]))
    if not wire.isClosed():
        raise SurfacingError("Those free edges do not close a hole")
    return loop


def patch(obj, edge_name, continuity=1):
    """A Surface::Filling over the hole of ``obj`` that ``edge_name`` borders, G0 (0) or tangent G1 (1)
    to the faces round it. Recomputed. No transaction here."""
    Part = _part()
    shape = obj.Shape
    loop = boundary_loop(shape, edge_name)
    doc = obj.Document
    fill = doc.addObject("Surface::Filling", "Patch")
    fill.BoundaryEdges = [(obj, (n,)) for n in loop]
    names = []
    for n in loop:
        edge = shape.Edges[int(n[4:]) - 1]
        face = shape.ancestorsOfType(edge, Part.Face)[0]
        idx = next(i for i, f in enumerate(shape.Faces, 1) if f.isSame(face))
        names.append(f"Face{idx}" if continuity else "")
    fill.BoundaryFaces = names
    fill.BoundaryOrder = [continuity] * len(loop)
    fill.Label = "Patch"
    doc.recompute()
    if not fill.isValid():
        raise SurfacingError("The hole could not be filled: " + " ".join(fill.State))
    return fill


# ----- features ---------------------------------------------------------------------------------------

def _shape_of(link):
    """The shape a LinkSub/LinkSubList value names, in the document's frame (sub-faces/edges when given)."""
    Part = _part()
    if link is None:
        return None
    if isinstance(link, tuple) and len(link) == 2 and not isinstance(link[0], tuple):
        links = [link]
    else:
        links = list(link)
    parts = []
    for obj, subs in links:
        if obj is None:
            continue
        subs = [s for s in (subs or []) if s]
        if subs:
            for s in subs:
                parts.append(obj.getSubObject(s))
        else:
            parts.append(Part.getShape(obj))
    parts = [p for p in parts if p is not None and not p.isNull()]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else Part.makeCompound(parts)


def _links(value):
    if value is None:
        return []
    if isinstance(value, tuple) and len(value) == 2 and not isinstance(value[0], tuple):
        return [value]
    return list(value)


def build(obj):
    """The shape of a surfacing feature from its properties (raises SurfacingError)."""
    kind = getattr(obj, KIND)
    base = _shape_of(obj.Base)
    if base is None:
        raise SurfacingError("Its input is gone")
    if kind == TRIM:
        tool = _shape_of(obj.Tool)
        if tool is None:
            raise SurfacingError("The trimming curve is gone")
        pt = obj.KeepPoint if obj.Keep == "point" else None
        return trim(base, tool, obj.Keep, pt)
    if kind == UNTRIM:
        return untrim(base, obj.Mode.lower())
    if kind == EXTEND:
        return extend(base, list(obj.Edges), obj.Distance.Value, obj.Mode.lower())
    if kind == STITCH:
        shapes = [_shape_of(link) for link in _links(obj.Base)]
        shape, free = stitch([s for s in shapes if s is not None], obj.Tolerance.Value, obj.MakeSolid)
        _set(obj, "FreeEdges", free)
        return shape
    if kind == THICKEN:
        return thicken(base, obj.Thickness.Value, obj.Side.lower())
    if kind == REPLACE:
        tool = _shape_of(obj.Tool)
        if tool is None:
            raise SurfacingError("The replacing surface is gone")
        src = _links(obj.Base)[0][0]
        return replace_face(_part().getShape(src), obj.Face, tool)
    raise SurfacingError(f"Unknown surfacing feature {kind}")


def _set(obj, name, value):
    if getattr(obj, name, None) != value:
        setattr(obj, name, value)


class SurfacingProxy:
    """The Part::FeaturePython proxy. Saved files name this class: keep its module and name."""

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, obj):
        try:
            shape = build(obj)
        except SurfacingError as exc:
            _set(obj, STATUS, f"Error: {exc}")
            raise RuntimeError(str(exc)) from None
        obj.Shape = shape
        _set(obj, STATUS, "Up to date")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def is_feature(obj) -> bool:
    return obj is not None and hasattr(obj, KIND) and obj.isDerivedFrom("Part::FeaturePython")


def _new(doc, kind):
    obj = doc.addObject("Part::FeaturePython", kind)
    SurfacingProxy(obj)
    obj.addProperty("App::PropertyString", KIND, GROUP, "What this surfacing feature does")
    obj.addProperty("App::PropertyString", STATUS, GROUP, "Up to date, or why it failed")
    obj.setEditorMode(KIND, 1)
    obj.setEditorMode(STATUS, 1)
    setattr(obj, KIND, kind)
    obj.Label = LABELS[kind]
    return obj


def _finish(obj, hide=()):
    App = _app()
    if App.GuiUp:
        import bruceworx_surfacing_gui as gui

        gui.SurfacingView(obj.ViewObject)
    obj.recompute()
    if not obj.isValid() or str(getattr(obj, STATUS, "")).startswith("Error"):
        why = getattr(obj, STATUS, "") or " ".join(obj.State)
        obj.Document.removeObject(obj.Name)
        raise SurfacingError(why.replace("Error: ", ""))
    for h in hide:
        if h is not None and hasattr(h, "Visibility"):
            h.Visibility = False
    return obj


def make_trim(doc, base, tool, keep="outside", keep_point=None):
    """``base``/``tool``: (object, [subnames]) or object."""
    obj = _new(doc, TRIM)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The surface or faces to trim")
    obj.addProperty("App::PropertyLinkSubList", "Tool", GROUP, "The curves to trim with")
    obj.addProperty("App::PropertyEnumeration", "Keep", GROUP, "Which piece stays")
    obj.Keep = ["outside", "inside", "larger", "smaller", "point"]
    obj.addProperty("App::PropertyVector", "KeepPoint", GROUP, "Keep the piece nearest this point")
    obj.Base, obj.Tool = _as_list(base), _as_list(tool)
    obj.Keep = keep
    if keep_point is not None:
        obj.KeepPoint = _app().Vector(keep_point)
    return _finish(obj, [_first(base)])


def make_untrim(doc, base, mode="Holes"):
    obj = _new(doc, UNTRIM)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The trimmed face")
    obj.addProperty("App::PropertyEnumeration", "Mode", GROUP, "Holes: fill the holes; Natural: the whole surface")
    obj.Mode = ["Holes", "Natural"]
    obj.Base = _as_list(base)
    obj.Mode = mode
    return _finish(obj, [_first(base)])


def make_extend(doc, face, edges, distance, mode="Natural"):
    """``face``: (object, "FaceN") or an object of one face; ``edges``: the edge names of that face's own
    shape (Edge1 ... of the face alone)."""
    obj = _new(doc, EXTEND)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The face to extend")
    obj.addProperty("App::PropertyStringList", "Edges", GROUP, "The face's edges to extend past (of the face alone)")
    obj.addProperty("App::PropertyDistance", "Distance", GROUP, "How far past the edges")
    obj.addProperty("App::PropertyEnumeration", "Mode", GROUP, "Natural: the surface continued; Tangent: ruled, G1")
    obj.Mode = ["Natural", "Tangent"]
    obj.Base = _as_list(face)
    obj.Edges = list(edges)
    obj.Distance = distance
    obj.Mode = mode
    return _finish(obj, [_first(face)])


def make_stitch(doc, shapes, tolerance=1e-4, solid=True):
    obj = _new(doc, STITCH)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The surfaces to stitch")
    obj.addProperty("App::PropertyLength", "Tolerance", GROUP, "Edges closer than this are joined")
    obj.addProperty("App::PropertyBool", "MakeSolid", GROUP, "Make a solid when the shell closes")
    obj.addProperty("App::PropertyInteger", "FreeEdges", GROUP, "Edges left open (0: closed)")
    obj.setEditorMode("FreeEdges", 1)
    obj.Base = [l for s in shapes for l in _as_list(s)]
    obj.Tolerance = tolerance
    obj.MakeSolid = solid
    return _finish(obj, [_first(s) for s in shapes])


def make_thicken(doc, base, thickness, side="Outside"):
    obj = _new(doc, THICKEN)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The surface to thicken")
    obj.addProperty("App::PropertyLength", "Thickness", GROUP, "How thick")
    obj.addProperty("App::PropertyEnumeration", "Side", GROUP, "Along the normal, against it, or both")
    obj.Side = ["Outside", "Inside", "Both"]
    obj.Base = _as_list(base)
    obj.Thickness = thickness
    obj.Side = side
    return _finish(obj, [_first(base)])


def make_replace_face(doc, solid, face_name, tool):
    obj = _new(doc, REPLACE)
    obj.addProperty("App::PropertyLinkSubList", "Base", GROUP, "The solid")
    obj.addProperty("App::PropertyString", "Face", GROUP, "The face of the solid to replace")
    obj.addProperty("App::PropertyLinkSubList", "Tool", GROUP, "The surface that replaces it")
    obj.Base = _as_list(solid)
    obj.Face = face_name
    obj.Tool = _as_list(tool)
    return _finish(obj, [solid, _first(tool)])


def _as_list(value):
    """A LinkSubList value. A whole object is (obj, ("",)): FreeCAD drops a link given no sub-names."""
    if isinstance(value, tuple) and len(value) == 2 and not isinstance(value[0], tuple):
        obj, subs = value
        subs = tuple([subs] if isinstance(subs, str) else subs) or ("",)
        return [(obj, subs)]
    if isinstance(value, list):
        return [x for v in value for x in _as_list(v)]
    return [(value, ("",))]


def _first(value):
    links = _as_list(value)
    return links[0][0] if links else None


# ----- benchmark: the MOD-08 acceptance set -------------------------------------------------------------

def naca_00xx(t, n=24):
    """A symmetric NACA four-digit section of unit chord and thickness ratio ``t`` (0.12 for NACA 0012):
    y = 5t(0.2969 sqrt x - 0.1260 x - 0.3516 x^2 + 0.2843 x^3 - 0.1015 x^4) (NACA Report 460, 1933).
    Returns the upper surface [(x, y)] from the trailing edge (x = 1) to the leading edge, cosine spaced."""
    out = []
    for i in range(n + 1):
        x = 0.5 * (1 + math.cos(math.pi * i / n))
        y = 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1015 * x ** 4)
        out.append((x, y))
    return out


def _section(chord, z, t=0.12):
    """One airfoil section at height ``z``: (upper curve, lower curve), both from trailing to leading
    edge, meeting at the leading edge; one closed interpolated curve split there, so the two halves
    join smoothly."""
    Part = _part()
    App = _app()
    upper = naca_00xx(t)
    lower = [(x, -y) for x, y in reversed(upper[:-1])]
    pts = [App.Vector(x * chord, y * chord, z) for x, y in upper + lower]
    curve = Part.BSplineCurve()
    curve.interpolate(pts)  # trailing edge (top) -> leading edge -> trailing edge (bottom)
    le = curve.parameter(App.Vector(0, 0, z))
    a, b = curve.FirstParameter, curve.LastParameter
    return curve.toShape(a, le), curve.toShape(le, b)


def wing_skins(root=100.0, tip=60.0, span=150.0):
    """Upper and lower wing skins lofted through three sections; they share the leading edge."""
    Part = _part()
    sections = [_section(root + (tip - root) * k / 2, span * k / 2) for k in range(3)]
    upper = Part.makeLoft([Part.Wire([s[0]]) for s in sections], False, False)
    lower = Part.makeLoft([Part.Wire([s[1]]) for s in sections], False, False)
    return upper, lower


def benchmark():
    """The MOD-08 benchmark set, built in code: {name: dict(shapes=[...], expect=level, solid=shape or None)}.

    * kink: two flat faces at 30 degrees: G0 only.
    * fillet: a flat face running into a round of 10 mm radius: tangent (G1), the curvature jumps.
    * split: one bicubic surface cut in two along an inner parameter: G2.
    * wing: upper and lower skins lofted through NACA 0012 sections (Report 460) of 100, 80 and 60 mm
      chord, sharing the leading edge (G1 or better); closed with a trailing-edge strip and end caps,
      stitched to a solid; the skins thickened 1 mm into a valid solid.
    * patch: an open box-like shell with a curved roof and a hole, filled with Surface Filling tangent to
      its neighbours (G1), stitched and thickened.
    """
    Part = _part()
    App = _app()
    V = App.Vector
    out = {}
    # kink
    a = Part.Face(Part.makePolygon([V(0, 0, 0), V(50, 0, 0), V(50, 40, 0), V(0, 40, 0), V(0, 0, 0)]))
    r = math.radians(30)
    b = Part.Face(Part.makePolygon([V(50, 0, 0), V(50 + 40 * math.cos(r), 0, 40 * math.sin(r)),
                                    V(50 + 40 * math.cos(r), 40, 40 * math.sin(r)), V(50, 40, 0), V(50, 0, 0)]))
    out["kink"] = {"shapes": [_sew([a, b])], "expect": "G0"}
    # fillet: plane z=0 for x <= 0, then a round of radius 10 turning up (the arc extruded along Y)
    flat = Part.Face(Part.makePolygon([V(-40, 0, 0), V(0, 0, 0), V(0, 30, 0), V(-40, 30, 0), V(-40, 0, 0)]))
    h = 10 * math.sqrt(0.5)
    arc = Part.Arc(V(0, 0, 0), V(h, 0, 10 - h), V(10, 0, 10)).toShape()
    round_ = arc.extrude(V(0, 30, 0))
    out["fillet"] = {"shapes": [_sew([flat, round_.Faces[0]])], "expect": "G1"}
    # split bicubic
    pts = [[V(i * 20, j * 20, 6 * math.sin(i * 0.7) + 4 * math.cos(j * 0.6)) for j in range(5)] for i in range(5)]
    s = Part.BSplineSurface()
    s.interpolate(pts)
    u0, u1, v0, v1 = s.bounds()
    mid = 0.5 * (u0 + u1) + 0.013  # not on a knot: the surface is C2 there
    out["split"] = {"shapes": [_sew([s.toShape(u0, mid, v0, v1), s.toShape(mid, u1, v0, v1)])], "expect": "G2"}
    # wing
    upper, lower = wing_skins()
    out["wing"] = {"shapes": [upper, lower], "expect": "G1"}
    return out


def roof():
    """The patch case's surface: the split case's bicubic surface with a 30 mm round hole cut in it."""
    Part = _part()
    App = _app()
    V = App.Vector
    pts = [[V(i * 20, j * 20, 6 * math.sin(i * 0.7) + 4 * math.cos(j * 0.6)) for j in range(5)] for i in range(5)]
    s = Part.BSplineSurface()
    s.interpolate(pts)
    face = s.toShape()
    circle = Part.makeCircle(15, V(40, 40, 50), V(0, 0, 1))
    return trim(face, circle, "outside", direction=V(0, 0, -1))


def patch_case(doc):
    """The patch case in ``doc``: the roof (a Part::Feature), its hole filled tangent (G1) by Surface
    Filling. Returns (roof object, filling object)."""
    Part = _part()
    obj = doc.addObject("Part::Feature", "Roof")
    obj.Shape = roof()
    doc.recompute()
    shape = obj.Shape
    outer = shape.Faces[0].OuterWire
    hole = next(i for i, e in enumerate(shape.Edges, 1)
                if not any(e.isSame(o) for o in outer.Edges) and len(shape.ancestorsOfType(e, Part.Face)) == 1)
    fill = patch(obj, f"Edge{hole}", 1)
    return obj, fill


def wing_solid(thickness=1.0):
    """The wing benchmark closed: skins + trailing-edge strip + end caps stitched to a solid (and the
    free edges of the stitch), and the skins sewn and thickened by ``thickness``."""
    Part = _part()
    upper, lower = wing_skins()
    skins = _sew([upper.Faces[0], lower.Faces[0]], 1e-5)
    free = _free_edges(skins)
    # trailing edge: the two free edges running spanwise (the airfoil's thickness there is 0.0126 c)
    te = [e for e in free if abs(e.BoundBox.ZLength) > 1 and e.BoundBox.XMin > 1]
    strip = Part.makeRuledSurface(te[0], te[1]) if len(te) == 2 else None
    faces = [upper.Faces[0], lower.Faces[0]] + ([strip] if strip else [])
    shell = _sew(faces, 1e-5)
    caps = []
    for z in (shell.BoundBox.ZMin, shell.BoundBox.ZMax):
        loop = [e for e in _free_edges(shell) if abs(e.BoundBox.ZMin - z) < 1e-6 and abs(e.BoundBox.ZMax - z) < 1e-6]
        if loop:
            caps.append(Part.Face(Part.Wire(Part.__sortEdges__(loop))))
    solid, open_edges = stitch([Part.makeCompound(faces + caps)], 1e-4, True)
    thick = thicken(skins, thickness, "outside")  # inside folds through the trailing edge (thinner than 2 t)
    return solid, open_edges, thick
