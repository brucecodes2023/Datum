# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inspect analyses on a shape (roadmap MOD-11; plan section "BW-MOD-11 plan").

The numbers behind the INSPECT tools, without any GUI: the GUI side
(bruceworx_analysis_gui) only colours what this module works out.

* ``surface_mesh``: the shape's display triangles in global coordinates, one
  vertex set per face, with the exact surface normal (outward) and the (u, v)
  of every vertex. The triangulation is the one FreeCAD already made for the
  3D view when it is fine enough, so the coloured overlay lies on the faces.
* ``overhang``: FDM overhangs against a build direction (Cura's convention:
  the angle of a down-facing surface from vertical; more than the threshold,
  45 degrees by default, needs support). Faces on the bed are their own band.
* ``draft``: moulding draft against a pull direction (positive, needs draft,
  negative), 1 degree by default.
* ``thickness``: wall thickness by ray casting: from each triangle, straight
  into the material, to where the ray leaves it. Walls under the minimum
  (0.8 mm FDM: two 0.4 mm lines) and under the target (1.2 mm: three lines).
* ``curvature``: Gaussian, mean or largest principal curvature per vertex
  (BRepLProp), for a colour map; zebra stripes need only the normals.

Each analysis returns an ``Analysis``: a scalar per vertex (what the colour
map reads), the bands with their colours, and the area of each band. Every
array is a numpy array; nothing is written to the document.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ----- bands and presets ------------------------------------------------------------------------

# Colours (r, g, b in 0..1) shared by the GUI legend and the overlay.
RED = (0.86, 0.20, 0.18)
ORANGE = (0.96, 0.58, 0.16)
YELLOW = (0.98, 0.84, 0.22)
GREEN = (0.30, 0.72, 0.36)
BLUE = (0.22, 0.48, 0.86)
NEUTRAL = (0.90, 0.91, 0.93)

OVERHANG_DEFAULT = 45.0  # degrees from vertical (Cura's "support overhang angle", PrusaSlicer's 45 from horizontal)
OVERHANG_MARGIN = 10.0  # the yellow band below the threshold: prints, may sag or curl
DRAFT_DEFAULT = 1.0  # degrees, injection moulding / casting
BED_TOLERANCE = 0.05  # mm: a down-facing face this close to the lowest point sits on the bed

# Wall-thickness presets: (name, minimum, target) in mm. Below the minimum is red, between orange.
THICKNESS_PRESETS = (
    ("FDM, 0.4 mm nozzle", 0.8, 1.2),  # 2 lines (0.8) prints but is weak; 3 lines (1.2) is the usual minimum
    ("FDM, 0.6 mm nozzle", 1.2, 1.8),
    ("CNC, aluminium", 0.5, 1.0),
    ("CNC, plastics", 1.0, 1.5),
)

CURVATURE_KINDS = ("Mean", "Gaussian", "Largest")


@dataclass
class Band:
    """One colour of a map: values from ``lo`` to ``hi`` (scalar units) and the area that has them."""

    name: str
    colour: tuple
    lo: float
    hi: float
    area: float = 0.0


@dataclass
class Analysis:
    kind: str
    scalar: object  # numpy (N,) per vertex: what the colour map reads
    lo: float  # the scalar range the colour map spans
    hi: float
    bands: list = field(default_factory=list)  # [Band]; empty: a continuous ramp
    stats: dict = field(default_factory=dict)
    unit: str = ""

    def band_texture(self, width=512):
        """The colour of each of ``width`` texels across [lo, hi] (the overlay's 1D texture)."""
        out = []
        span = (self.hi - self.lo) or 1.0
        for i in range(width):
            value = self.lo + (i + 0.5) / width * span
            out.append(colour_at(self, value))
        return out

    def texcoords(self):
        """The scalar mapped to the texture's 0..1 (numpy)."""
        import numpy as np

        span = (self.hi - self.lo) or 1.0
        return np.clip((self.scalar - self.lo) / span, 0.0, 1.0)


def colour_at(analysis, value):
    if analysis.bands:
        for band in analysis.bands:
            if band.lo <= value <= band.hi:
                return band.colour
        return analysis.bands[-1].colour if value > analysis.bands[-1].hi else analysis.bands[0].colour
    span = (analysis.hi - analysis.lo) or 1.0
    return ramp((value - analysis.lo) / span)


RAMP = ((0.0, (0.16, 0.30, 0.86)), (0.25, (0.18, 0.70, 0.92)), (0.5, (0.36, 0.78, 0.36)),
        (0.75, (0.98, 0.84, 0.22)), (1.0, (0.86, 0.20, 0.18)))


def ramp(t):
    """Blue, cyan, green, yellow, red for t in 0..1."""
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(RAMP, RAMP[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(c0[k] + f * (c1[k] - c0[k]) for k in range(3))
    return RAMP[-1][1]


# ----- directions --------------------------------------------------------------------------------

AXIS_DIRECTIONS = {"+Z": (0, 0, 1), "-Z": (0, 0, -1), "+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0),
                   "-Y": (0, -1, 0)}


def unit(vector):
    x, y, z = (float(c) for c in vector)
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-12:
        raise ValueError("a direction needs a length")
    return (x / length, y / length, z / length)


def overhang_angle(normal, build):
    """Degrees from vertical of a surface with outward ``normal``: 0 a wall, 90 a flat ceiling, negative facing up."""
    n, b = unit(normal), unit(build)
    down = -(n[0] * b[0] + n[1] * b[1] + n[2] * b[2])  # cos of the angle between n and -b
    return math.degrees(math.asin(max(-1.0, min(1.0, down))))


def draft_angle(normal, pull):
    """Degrees of draft of a surface with outward ``normal`` against ``pull``: positive releases."""
    n, d = unit(normal), unit(pull)
    return math.degrees(math.asin(max(-1.0, min(1.0, n[0] * d[0] + n[1] * d[1] + n[2] * d[2]))))


# ----- the mesh ----------------------------------------------------------------------------------


def default_deflection(shape):
    """FreeCAD's own display deflection for this shape (ViewProviderPartExt: 0.5 % of the mean box side x 3).

    Asking for this (or coarser) reuses the triangulation the 3D view made; BRepMesh keeps a finer one.
    """
    box = shape.BoundBox
    if not box.isValid():
        return 0.1
    return max(1e-3, (box.XLength + box.YLength + box.ZLength) / 300.0 * 0.5)


class SurfaceMesh:
    """Triangles of a shape in global coordinates, one vertex set per face (numpy arrays).

    points (N,3), normals (N,3) outward unit, uv (N,2), vertex_face (N,), triangles (T,3), triangle_face (T,).
    """

    def __init__(self, points, normals, uv, vertex_face, triangles, triangle_face, faces, exact):
        self.points, self.normals, self.uv, self.vertex_face = points, normals, uv, vertex_face
        self.triangles, self.triangle_face, self.faces = triangles, triangle_face, faces
        self.exact = exact  # per face: True when the normals came from the surface, False when averaged

    def triangle_geometry(self):
        """(centroids, unit normals by winding, areas) of every triangle."""
        import numpy as np

        a, b, c = (self.points[self.triangles[:, k]] for k in range(3))
        cross = np.cross(b - a, c - a)
        length = np.linalg.norm(cross, axis=1)
        safe = np.where(length > 1e-300, length, 1.0)
        return (a + b + c) / 3.0, cross / safe[:, None], 0.5 * length

    @property
    def area(self):
        return float(self.triangle_geometry()[2].sum())


def _match(points, nodes):
    """For each tessellation point, the index of the triangulation node at the same place."""
    import numpy as np

    if not len(nodes):
        return None
    scale = 1e-5 * max(1.0, float(np.abs(points).max()))
    keys = {}
    for i, p in enumerate(np.round(nodes / scale).astype(np.int64)):
        keys.setdefault(tuple(p), i)
    out = np.empty(len(points), dtype=np.int64)
    for i, p in enumerate(np.round(points / scale).astype(np.int64)):
        j = keys.get(tuple(p))
        if j is None:  # on a grid line: the nearest node
            j = int(np.argmin(((nodes - points[i]) ** 2).sum(axis=1)))
        out[i] = j
    return out


def surface_mesh(shape, deflection=None, curvature=False):
    """The ``SurfaceMesh`` of ``shape`` (global coordinates). ``curvature``: also (k1, k2) per vertex."""
    import numpy as np

    if deflection is None:
        deflection = default_deflection(shape)
    shape.tessellate(deflection)  # one BRepMesh call for every face (kept when the view's is finer)
    pts_all, nrm_all, uv_all, vf_all, tri_all, tf_all, curv_all, exact = [], [], [], [], [], [], [], []
    base = 0
    faces = shape.Faces
    for fi, face in enumerate(faces):
        try:
            pts, tris = face.tessellate(deflection)
        except Exception:
            continue
        if not pts or not tris:
            continue
        p = np.array([(v.x, v.y, v.z) for v in pts], dtype=float)
        t = np.array(tris, dtype=np.int64)
        uv = np.zeros((len(p), 2))
        normals = None
        flip = face.Orientation == "Reversed"
        try:
            nodes_uv = face.getUVNodes()
            surface = face.Surface  # the Geom surface, placed: ~1000x faster to evaluate than the face itself
        except Exception:
            nodes_uv, surface = [], None
        if nodes_uv:
            nodes = np.array([tuple(surface.value(u, v)) for u, v in nodes_uv], dtype=float)
            match = _match(p, nodes)
            uv = np.array(nodes_uv, dtype=float)[match]
            normals = np.empty_like(p)
            try:
                for i, (u, v) in enumerate(uv):
                    n = surface.normal(u, v)
                    normals[i] = (n.x, n.y, n.z)
                if flip:
                    normals = -normals
            except Exception:
                normals = None
        # The winding follows the face orientation; a surface normal that disagrees with it (a pole, a
        # degenerate point) is replaced by the averaged triangle normal.
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        cross = np.cross(b - a, c - a)
        averaged = np.zeros_like(p)
        for k in range(3):
            np.add.at(averaged, t[:, k], cross)
        length = np.linalg.norm(averaged, axis=1)
        averaged = averaged / np.where(length > 1e-300, length, 1.0)[:, None]
        if normals is None:
            normals = averaged
            exact.append(False)
        else:
            bad = (normals * averaged).sum(axis=1) < 0.2
            normals[bad] = averaged[bad]
            exact.append(True)
        if curvature:
            k = np.zeros((len(p), 2))
            if nodes_uv:
                for i, (u, v) in enumerate(uv):
                    try:
                        kmin, kmax = surface.curvature(u, v, "Min"), surface.curvature(u, v, "Max")
                    except Exception:
                        continue
                    # OCCT's curvatures follow the surface's normal; a reversed face's outward normal is the other one.
                    k[i] = (-kmax, -kmin) if flip else (kmin, kmax)
            curv_all.append(k)
        pts_all.append(p)
        nrm_all.append(normals)
        uv_all.append(uv)
        vf_all.append(np.full(len(p), fi, dtype=np.int64))
        tri_all.append(t + base)
        tf_all.append(np.full(len(t), fi, dtype=np.int64))
        base += len(p)
    if not pts_all:
        empty = np.zeros((0, 3))
        mesh = SurfaceMesh(empty, empty, np.zeros((0, 2)), np.zeros(0, np.int64), np.zeros((0, 3), np.int64),
                           np.zeros(0, np.int64), faces, [])
        mesh.curvature = np.zeros((0, 2))
        mesh.shape = shape
        return mesh
    mesh = SurfaceMesh(np.vstack(pts_all), np.vstack(nrm_all), np.vstack(uv_all), np.concatenate(vf_all),
                       np.vstack(tri_all), np.concatenate(tf_all), faces, exact)
    mesh.curvature = np.vstack(curv_all) if curvature else None
    mesh.shape = shape
    return mesh


# ----- overhang and draft -------------------------------------------------------------------------


def _band_areas(bands, values, areas):
    for band in bands:
        inside = (values >= band.lo) & (values <= band.hi)
        band.area = float(areas[inside].sum())
        # a value on a shared limit counts once, in the first band
        values = values.copy()
        values[inside] = math.inf


def overhang(mesh, build=(0, 0, 1), threshold=OVERHANG_DEFAULT, margin=OVERHANG_MARGIN, bed=True):
    """FDM overhang map. The scalar is the angle from vertical in degrees (-90 up-facing .. 90 a ceiling); faces
    resting on the bed get 95. Stats: support area (mm^2), bed area, the lowest overhang point."""
    import numpy as np

    b = np.array(unit(build))
    scalar = np.degrees(np.arcsin(np.clip(-(mesh.normals @ b), -1.0, 1.0)))
    centroids, tri_normals, areas = mesh.triangle_geometry()
    tri_values = np.degrees(np.arcsin(np.clip(-(tri_normals @ b), -1.0, 1.0)))
    height = mesh.points @ b
    bed_level = float(height.min()) if len(height) else 0.0
    bed_tris = np.zeros(len(areas), dtype=bool)
    if bed and len(areas):
        # a face is on the bed when it faces straight down (within 1 degree) and all of it is at the lowest level
        for fi in np.unique(mesh.triangle_face):
            sel = mesh.triangle_face == fi
            verts = mesh.vertex_face == fi
            if tri_values[sel].min() >= 89.0 and float(height[verts].max()) - bed_level <= BED_TOLERANCE:
                bed_tris |= sel
                scalar[verts] = 95.0
    bands = [
        Band("Faces up or vertical", NEUTRAL, -90.0, 0.0),
        Band("Prints without support", GREEN, 0.0, max(0.0, threshold - margin)),
        Band("Near the limit", YELLOW, max(0.0, threshold - margin), threshold),
        Band("Needs support", RED, threshold, 90.0),
        Band("On the bed", BLUE, 90.0, 100.0),
    ]
    values = np.where(bed_tris, 95.0, tri_values)
    _band_areas(bands, values, areas)
    needs = (values > threshold) & ~bed_tris
    stats = {
        "support_area": float(areas[needs].sum()),
        "bed_area": float(areas[bed_tris].sum()),
        "total_area": float(areas.sum()),
        "faces_needing_support": sorted({int(f) for f in mesh.triangle_face[needs]}),
        "build": tuple(float(x) for x in b),
        "threshold": float(threshold),
    }
    return Analysis("overhang", scalar, -90.0, 100.0, bands, stats, "°")


def draft(mesh, pull=(0, 0, 1), required=DRAFT_DEFAULT):
    """Moulding draft map. The scalar is the draft angle in degrees (asin n.d): positive releases toward ``pull``."""
    import numpy as np

    d = np.array(unit(pull))
    scalar = np.degrees(np.arcsin(np.clip(mesh.normals @ d, -1.0, 1.0)))
    _c, tri_normals, areas = mesh.triangle_geometry()
    values = np.degrees(np.arcsin(np.clip(tri_normals @ d, -1.0, 1.0)))
    bands = [
        Band("Negative draft", RED, -90.0, -required),
        Band("Needs draft", YELLOW, -required, required),
        Band("Positive draft", GREEN, required, 90.0),
    ]
    _band_areas(bands, values, areas)
    needs = np.abs(values) < required
    stats = {
        "needs_draft_area": float(areas[needs].sum()),
        "negative_area": float(areas[values <= -required].sum()),
        "positive_area": float(areas[values >= required].sum()),
        "faces_needing_draft": sorted({int(f) for f in mesh.triangle_face[needs]}),
        "pull": tuple(float(x) for x in d),
        "required": float(required),
    }
    return Analysis("draft", scalar, -90.0, 90.0, bands, stats, "°")


# ----- wall thickness ------------------------------------------------------------------------------


def ray_lengths(origins, directions, tri_a, tri_b, tri_c, cap, eps=1e-9, neighbours=None, tri_normals=None):
    """For each ray, the distance to the nearest triangle it hits within ``cap``, or inf (Moller-Trumbore).

    Also the index of that triangle (-1 when none). Plain numpy over every (ray, triangle) pair: callers keep
    the candidate lists short (``thickness`` does, with a grid).
    """
    import numpy as np

    e1 = tri_b - tri_a
    e2 = tri_c - tri_a
    p = np.cross(directions[:, None, :], e2[None, :, :])  # (R,T,3)
    det = (e1[None, :, :] * p).sum(axis=2)
    ok = np.abs(det) > eps
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origins[:, None, :] - tri_a[None, :, :]
    u = (s * p).sum(axis=2) * inv
    q = np.cross(s, e1[None, :, :])
    v = (directions[:, None, :] * q).sum(axis=2) * inv
    t = (e2[None, :, :] * q).sum(axis=2) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1.0 + 1e-9) & (t > 0.0) & (t <= cap)
    t = np.where(hit, t, np.inf)
    if neighbours is not None:
        # (R,T) booleans: the triangle is on the ray's own face or a neighbour (sharing an edge). Such a hit is a
        # wall only when the face there turns back against the ray's start (normals over 120 degrees apart, the two
        # sides of a thin fin); otherwise it reads the edge between them: a hole's mouth, a chamfer.
        facing = directions @ tri_normals.T
        t = np.where(neighbours & (facing < 0.5), np.inf, t)
    index = np.argmin(t, axis=1)
    best = t[np.arange(len(t)), index]
    return best, np.where(np.isfinite(best), index, -1)


def face_adjacency(shape):
    """F x F booleans (numpy): True where two faces share an edge, and on the diagonal."""
    import numpy as np

    faces = shape.Faces
    out = np.eye(len(faces), dtype=bool)
    owners = {}
    for i, face in enumerate(faces):
        for edge in face.Edges:
            owners.setdefault(edge.hashCode(), set()).add(i)  # OCCT's hash: the edge, whatever its orientation
    for group in owners.values():
        group = list(group)
        for i in group:
            out[i, group] = True
    return out


def _cast(starts, directions, tri_a, tri_b, tri_c, tri_normals, cap, ray_face=None, tri_face=None,
          adjacent=None):
    """Distance along each ray to where it leaves the solid, inf when beyond ``cap`` or unreadable.

    A grid of cells ``cap`` wide keeps the candidates near each ray: a ray no longer than ``cap`` that starts in a
    cell stays inside it and its 26 neighbours.
    """
    import numpy as np

    size = cap
    out = np.full(len(starts), np.inf)
    lo = np.floor(np.minimum(np.minimum(tri_a, tri_b), tri_c) / size).astype(np.int64)
    hi = np.floor(np.maximum(np.maximum(tri_a, tri_b), tri_c) / size).astype(np.int64)
    cells = {}
    for i in range(len(tri_a)):
        for x in range(lo[i, 0], hi[i, 0] + 1):
            for y in range(lo[i, 1], hi[i, 1] + 1):
                for z in range(lo[i, 2], hi[i, 2] + 1):
                    cells.setdefault((x, y, z), []).append(i)
    groups = {}
    for i, key in enumerate(map(tuple, np.floor(starts / size).astype(np.int64))):
        groups.setdefault(key, []).append(i)
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for key, rays in groups.items():
        candidates = set()
        for dx, dy, dz in offsets:
            candidates.update(cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ()))
        if not candidates:
            continue
        cand = np.fromiter(candidates, dtype=np.int64)
        rays = np.array(rays, dtype=np.int64)
        for chunk in range(0, len(rays), 256):  # keeps the (rays x triangles) arrays small
            r = rays[chunk:chunk + 256]
            mask = None if adjacent is None else adjacent[ray_face[r][:, None], tri_face[cand][None, :]]
            dist, hit = ray_lengths(starts[r], directions[r], tri_a[cand], tri_b[cand], tri_c[cand], cap,
                                    neighbours=mask, tri_normals=tri_normals[cand])
            found = hit >= 0
            cos = np.zeros(len(r))
            cos[found] = (tri_normals[cand[hit[found]]] * directions[r[found]]).sum(axis=1)
            # the ray must leave through a face turned away from it; one that meets a face turned towards it
            # went out through a gap, and a grazing hit (along a face) is no reading either
            good = found & (cos > 0.05)
            out[r[good]] = dist[good]
    return out


def thickness(mesh, minimum=0.8, target=1.2, cap=None):
    """Wall thickness map. Rays go straight into the material, from every vertex (nudged into its face, off the
    edges) and from every triangle's centre, to where they leave the solid. The scalar is per vertex in mm,
    capped at ``cap`` (2 x target by default: thicker walls are all "thick enough"); band areas are measured at
    the triangle centres. A ray that meets its own face or a neighbouring one (sharing an edge) reads a wall only
    where that face turns back against it (a thin fin's other side); otherwise it reads the edge between them (a
    hole's mouth in a curved hub) and the hit is skipped.
    Stats: the thinnest wall and where, the area under the minimum and under the target."""
    import numpy as np

    if cap is None:
        cap = max(2.0 * target, target + 1.0)
    centroids, normals, areas = mesh.triangle_geometry()
    count = len(areas)
    a, b, c = (mesh.points[mesh.triangles[:, k]] for k in range(3))
    # vertex rays start a little way towards the middle of their triangles, so that a ray from a vertex on an
    # edge does not run along the neighbouring face
    pull = np.zeros_like(mesh.points)
    hits = np.zeros(len(mesh.points))
    for k in range(3):
        np.add.at(pull, mesh.triangles[:, k], centroids)
        np.add.at(hits, mesh.triangles[:, k], 1.0)
    inner = pull / np.maximum(hits, 1.0)[:, None]
    vstart = mesh.points + 0.05 * (inner - mesh.points)
    eps = 1e-4 * cap
    starts = np.vstack([vstart - mesh.normals * eps, centroids - normals * eps]) if count else np.zeros((0, 3))
    directions = np.vstack([-mesh.normals, -normals]) if count else np.zeros((0, 3))
    adjacent = face_adjacency(mesh.shape) if count and getattr(mesh, "shape", None) is not None else None
    ray_face = np.concatenate([mesh.vertex_face, mesh.triangle_face])
    found = _cast(starts, directions, a, b, c, normals, cap, ray_face, mesh.triangle_face, adjacent) \
        if count else np.zeros(0)
    vertex_t, centre_t = found[:len(mesh.points)], found[len(mesh.points):]
    scalar = np.minimum(vertex_t, cap)
    values = np.minimum(centre_t, cap)
    bands = [
        Band("Too thin", RED, 0.0, minimum),
        Band("Thin", ORANGE, minimum, target),
        Band("Thick enough", GREEN, target, cap),
    ]
    _band_areas(bands, values, areas)
    every = np.concatenate([vertex_t, centre_t])
    stats = {"thinnest": None, "thinnest_at": None, "thinnest_face": None}
    if len(every) and np.isfinite(every).any():
        i = int(np.argmin(every))
        stats["thinnest"] = float(every[i])
        at = vstart[i] if i < len(mesh.points) else centroids[i - len(mesh.points)]
        stats["thinnest_at"] = tuple(float(x) for x in at)
        stats["thinnest_face"] = int(mesh.vertex_face[i] if i < len(mesh.points)
                                     else mesh.triangle_face[i - len(mesh.points)])
    too_thin = (values < minimum)
    stats.update({
        "under_minimum_area": float(areas[too_thin].sum()),
        "under_target_area": float(areas[values < target].sum()),
        "rays": int(len(every)),
        "beyond_cap": int((~np.isfinite(every)).sum()),
        "minimum": float(minimum),
        "target": float(target),
        "cap": float(cap),
        "faces_too_thin": sorted({int(f) for f in mesh.triangle_face[too_thin]}
                                 | {int(f) for f in mesh.vertex_face[vertex_t < minimum]}),
    })
    return Analysis("thickness", scalar, 0.0, cap, bands, stats, "mm")


# ----- curvature -----------------------------------------------------------------------------------


def vertex_areas(mesh):
    """A third of the area of each vertex's triangles (numpy)."""
    import numpy as np

    _c, _n, areas = mesh.triangle_geometry()
    out = np.zeros(len(mesh.points))
    for k in range(3):
        np.add.at(out, mesh.triangles[:, k], areas / 3.0)
    return out


def weighted_percentile(values, weights, q, floor=0.0):
    """The ``q`` percentile of ``values`` above ``floor``, each counted with its weight; None when there are none."""
    import numpy as np

    keep = values > floor
    v, w = values[keep], weights[keep]
    if not len(v) or w.sum() <= 0:
        return None
    order = np.argsort(v)
    cumulative = np.cumsum(w[order])
    i = min(int(np.searchsorted(cumulative, q / 100.0 * cumulative[-1])), len(v) - 1)
    return float(v[order][i])


def curvature(mesh, kind="Mean", limit=None):
    """Curvature map (1/mm) from ``surface_mesh(..., curvature=True)``. Mean and Gaussian are signed (convex
    positive), Largest is max(|k1|, |k2|). The ramp spans +-limit (signed) or 0..limit; ``limit`` None: the 95th
    percentile by area (curved area, not vertex count: small fillets have many vertices), so a few tight fillets
    do not wash out the rest."""
    import numpy as np

    k = mesh.curvature
    if k is None:
        raise ValueError("the mesh was made without curvature")
    if kind == "Gaussian":
        value = k[:, 0] * k[:, 1]
    elif kind == "Largest":
        value = np.maximum(np.abs(k[:, 0]), np.abs(k[:, 1]))
    else:
        value = 0.5 * (k[:, 0] + k[:, 1])
    # OCCT's sign: positive curvature bends towards the normal (a concave surface seen from outside)
    if kind != "Largest":
        value = -value if kind == "Mean" else value
    if limit is None:
        limit = weighted_percentile(np.abs(value), vertex_areas(mesh), 95.0, floor=1e-12) or 1.0
    limit = limit or 1.0
    lo = 0.0 if kind == "Largest" else -limit
    stats = {
        "kind": kind,
        "limit": float(limit),
        "max": float(np.abs(value).max()) if len(value) else 0.0,
        "smallest_radius": (1.0 / float(np.max(np.abs(k)))) if len(k) and np.max(np.abs(k)) > 1e-12 else None,
    }
    return Analysis("curvature", value, lo, limit, [], stats, "1/mm" if kind != "Gaussian" else "1/mm²")
