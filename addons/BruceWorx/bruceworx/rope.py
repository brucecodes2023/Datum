# SPDX-License-Identifier: LGPL-2.1-or-later
"""Flexible wires and hoses as ropes (BW-ROU-08, 30 Oct 2026).

No FreeCAD import; numpy only. A wire of cut length ``L`` is ``N`` points ``x_0 .. x_{N-1}`` with a
rest segment length ``l0 = L / (N - 1)``. The shape is the *resting* configuration, not a
simulation: a quasi-static XPBD solve (Macklin, Mueller, Chentanez 2016) in which each outer step
moves the free points down by a gravity step and then projects the constraints:

* **length** (inextensible, alpha = 0): ``|x_{i+1} - x_i| = l0``;
* **bending** (elastic): ``C = |x_{i-1} - 2 x_i + x_{i+1}| = 2 l0 sin(theta/2)`` for the turning
  angle theta, energy ``EI/(2 l0) theta^2`` for small angles, so the compliance is
  ``alpha = l0^3 / EI``;
* **minimum bend radius** (a limit, only when violated): ``theta <= 2 asin(l0 / 2 Rmin)``, which is
  ``|d| <= l0^2 / Rmin`` for the same second difference ``d``;
* **collision** against oriented boxes (exact signed distance), pushed out to ``wire radius +
  clearance``.

The first and last ``k`` points are clamped (inverse mass 0): they are the lead straights on the
pin frames. With the velocity reset every step the iteration is a proximal-point iteration, whose
fixed point is the static equilibrium whatever the step size, so a drag step warm-started from the
last shape needs only a few passes. Wires of the same point count are solved together as one
``(wires, points, 3)`` array, so six wires cost about what one costs.

A solve reports ``strain`` (largest relative segment error after the iterations), ``bend_ratio``
(largest turning angle over the allowed one) and ``taut``: the length constraints could not be
met (strain above 1e-3) or the bend limit is exceeded, which is what stops a drag.

Stiffness: ``EI = E_cu * n_s * pi d_s^4 / 64`` for the strands (they slide, so their stiffnesses add)
plus ``E_i * pi (D^4 - d^4) / 64`` for the insulation tube; weight per length from the copper and
insulation densities. Units: N, mm, g/mm^3 for densities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

try:
    import numpy as np
except ImportError:  # pragma: no cover - the tests skip without numpy
    np = None

G_MM = 9810.0  # mm/s^2
E_COPPER = 117e3  # N/mm^2
COPPER_DENSITY = 8.96e-3  # g/mm^3
STRAIN_TOL = 1e-3

# Insulation and hose materials: Young's modulus (N/mm^2) and density (g/mm^3).
MATERIALS = {
    "PVC": (18.6, 1.40e-3),  # plasticised PVC, 10-20 MPa; 18.6 reproduces EI = 1.69e3 N mm^2 for 12 AWG
    "Silicone": (5.0, 1.15e-3),  # soft silicone rubber, 2-10 MPa
}
MATERIAL_NAMES = list(MATERIALS)


# ----------------------------------------------------------------------------------------------
# Stiffness and weight.


def strands(area_mm2):
    """``(n, d)``: strand count and strand diameter of a stranded conductor of copper ``area_mm2``
    (the usual hook-up stranding: 30 AWG strands of 0.254 mm for AWG 22 and thicker, finer seven-strand
    wire below)."""
    if area_mm2 >= 0.28:
        d = 0.254
    elif area_mm2 >= 0.15:
        d = 0.203  # 32 AWG, 24 AWG 7/32
    else:
        d = 0.16  # 34 AWG
    n = max(1, int(round(area_mm2 / (math.pi * d * d / 4))))
    return n, d


def wire_properties(conductor_d, conductor_area, od, material="PVC", factor=1.0):
    """``(EI, q)`` of an insulated stranded wire: bending stiffness in N mm^2 (times the per-item
    calibration ``factor``) and weight per length in N/mm."""
    e_ins, rho_ins = MATERIALS[material]
    outer = max(od, conductor_d)
    n, ds = strands(conductor_area)
    ei_c = E_COPPER * n * math.pi * ds**4 / 64
    ei_i = e_ins * math.pi * (outer**4 - conductor_d**4) / 64
    a_i = math.pi * (outer**2 - conductor_d**2) / 4
    q = (conductor_area * COPPER_DENSITY + a_i * rho_ins) * 1e-3 * 9.81 / 1.0  # g/mm -> kg/mm -> N/mm
    return factor * (ei_c + ei_i), q


def tube_properties(od, inner, material="Silicone", factor=1.0):
    """``(EI, q)`` of a hose or tube (annulus of ``material``)."""
    e, rho = MATERIALS[material]
    ei = e * math.pi * (od**4 - inner**4) / 64
    q = rho * math.pi * (od**2 - inner**2) / 4 * 1e-3 * 9.81
    return factor * ei, q


def point_count(length, od):
    """One point per 1.5 OD of wire, 20 to 200 points."""
    return int(min(200, max(20, round(length / (1.5 * max(od, 1e-6))) + 1)))


# ----------------------------------------------------------------------------------------------
# Collision proxies: oriented boxes.


@dataclass
class Box:
    """An oriented box: ``center``, ``axes`` (3x3, columns are the box axes in the world) and
    ``half`` extents."""

    center: tuple
    half: tuple
    axes: tuple = ((1, 0, 0), (0, 1, 0), (0, 0, 1))  # rows here are the columns of the matrix

    def matrix(self):
        return np.array(self.axes, dtype=float).T


def box_distance(box, pts):
    """Signed distance of ``pts`` (..., 3) to the box surface (negative inside) and its gradient."""
    m = box.matrix()
    c = np.asarray(box.center, dtype=float)
    h = np.asarray(box.half, dtype=float)
    q = (pts - c) @ m  # local coordinates (rows are points: q = R^T p)
    d = np.abs(q) - h
    outside = np.maximum(d, 0.0)
    norm = np.linalg.norm(outside, axis=-1)
    inside_d = np.minimum(d.max(axis=-1), 0.0)
    sd = norm + inside_d
    # gradient in the local frame
    safe = np.where(norm > 1e-12, norm, 1.0)[..., None]
    g_out = np.sign(q) * outside / safe
    axis = np.argmax(d, axis=-1)
    g_in = np.zeros_like(q)
    idx = np.indices(axis.shape)
    sign = np.where(q[(*idx, axis)] >= 0, 1.0, -1.0)
    g_in[(*idx, axis)] = sign
    g_local = np.where((norm > 1e-12)[..., None], g_out, g_in)
    return sd, g_local @ m.T


# ----------------------------------------------------------------------------------------------
# The rope.


@dataclass
class Rope:
    """One rope. ``x`` is (N, 3); ``w`` the inverse mass per point (0 = clamped); ``length`` is the
    cut length that sets ``l0``; ``ei``, ``q`` the stiffness and weight; ``rmin`` the minimum bend
    radius (0 = none); ``radius`` the wire radius plus clearance for collisions."""

    x: object
    length: float
    ei: float
    q: float
    rmin: float = 0.0
    radius: float = 0.0
    fixed_start: int = 1
    fixed_end: int = 1
    gravity: tuple = (0.0, 0.0, -1.0)  # direction (also the way a contact pushes a point out)
    weight_on: bool = True  # False: no gravity term (length, bend limit and contacts still hold)
    pinned: tuple = ()  # interior points held in place (clips: the wire passes through, free to turn)
    # results
    strain: float = 0.0
    bend_ratio: float = 0.0
    steps: int = 0
    taut: bool = False
    message: str = ""
    _w: object = field(default=None, repr=False)

    @property
    def n(self):
        return len(self.x)

    @property
    def l0(self):
        return self.length / (self.n - 1)

    def weights(self):
        w = np.ones(self.n)
        w[: self.fixed_start] = 0.0
        w[self.n - self.fixed_end :] = 0.0
        for k in self.pinned:
            w[k] = 0.0
        return w

    def fix(self, start=None, end=None):
        """Move the clamped points: ``start`` / ``end`` are lists of the clamped points in order."""
        if start is not None:
            self.x[: len(start)] = start
        if end is not None:
            self.x[self.n - len(end) :] = end


def segment_lengths(x):
    return np.linalg.norm(np.diff(x, axis=-2), axis=-1)


def _free_segments(w):
    """Segments with at least one free end: the clamped lead segments keep their own length."""
    return (w[..., :-1] > 0) | (w[..., 1:] > 0)


def measure(x, l0, rmin, w=None):
    """``(strain, bend_ratio)`` for ``x`` (..., N, 3): largest |len/l0 - 1| over the segments that are
    not clamped at both ends, and the largest second difference over the allowed one (0 when there is
    no limit)."""
    err = np.abs(segment_lengths(x) / l0[..., None] - 1.0)
    if w is not None:
        err = np.where(_free_segments(w), err, 0.0)
    strain = err.max(axis=-1)
    d = np.linalg.norm(x[..., :-2, :] - 2 * x[..., 1:-1, :] + x[..., 2:, :], axis=-1).max(axis=-1)
    dmax = np.where(rmin > 0, l0 * l0 / np.where(rmin > 0, rmin, 1.0), np.inf)
    return strain, np.where(np.isfinite(dmax), d / dmax, 0.0)


_D_CACHE = {}


def _second_difference(n):
    """The (n-2, n) matrix of second differences [1, -2, 1]."""
    d = _D_CACHE.get(n)
    if d is None:
        d = np.zeros((n - 2, n))
        r = np.arange(n - 2)
        d[r, r] = 1.0
        d[r, r + 1] = -2.0
        d[r, r + 2] = 1.0
        _D_CACHE[n] = d
    return d


def _collide(x, w, boxes, radius, g_dir=None):
    """Push every free point out of every box to ``radius`` (B,); True when any was inside. A point
    that is inside a box leaves it against gravity (``g_dir`` (B, 3)) unless a side is much closer: a
    rope that fell into a box was draped over it, so it comes out of the top, not the bottom."""
    hit = False
    for box in boxes:
        sd, grad = box_distance(box, x)
        inside = (sd < 0) & (w > 0)
        if inside.any() and g_dir is not None:
            m = box.matrix()
            h = np.asarray(box.half, dtype=float)
            c = np.asarray(box.center, dtype=float)
            q = (x - c) @ m
            up = (-g_dir) @ m  # (B, 3) the way out in the box frame
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (h * np.sign(up)[:, None, :] - q) / up[:, None, :]
            t = np.where(np.abs(up)[:, None, :] > 1e-9, t, np.inf)
            t_up = t.min(axis=-1)
            use = inside & np.isfinite(t_up) & (t_up > 0) & (t_up <= 2.5 * -sd)
            if use.any():
                x += (-g_dir)[:, None, :] * (np.where(use, t_up, 0.0))[..., None]
                sd, grad = box_distance(box, x)
        push = np.maximum(radius[:, None] - sd, 0.0) * (w > 0)
        if push.any():
            hit = True
            x += grad * push[..., None]
    return hit


def _newton(x, w, l0, alpha, lam_b, bending=True, dx0=None, dmax=None, limit_alpha=1e-9):
    """One global XPBD iteration: all length constraints (alpha = 0) and all bending constraints
    at once. Bending is the vector constraint ``C = d_j = x_{j-1} - 2 x_j + x_{j+1}`` (3 per node,
    compliance ``alpha``, smooth and linear, so no direction is left unresisted); with ``dmax`` (B,)
    the nodes whose |d| exceeds 98 % of the limit become hard and are aimed at 98 %, keeping the
    direction of d. The bending multipliers are eliminated exactly (they see the lengths only through
    ``H``), which leaves one (N-1) x (N-1) system per wire for the lengths. ``lam_b`` (B, N-2, 3)
    accumulates the bending multipliers for the compliance term. ``dx0`` is a wanted move (gravity),
    made first-order compatible with the constraints."""
    B, N, _ = x.shape
    nl, nb = N - 1, N - 2
    seg = x[:, 1:] - x[:, :-1]
    dist = np.linalg.norm(seg, axis=-1)
    e = seg / np.where(dist > 1e-12, dist, 1.0)[..., None]
    r_l = l0[:, None] - dist  # -C
    # diagonal and off-diagonal of A_ll = J_l W J_l^T
    S = np.zeros((B, nl, nl))
    i = np.arange(nl)
    S[:, i, i] = w[:, :-1] + w[:, 1:]
    cross = -w[:, 1:-1] * (e[:, :-1] * e[:, 1:]).sum(-1)
    S[:, i[:-1], i[:-1] + 1] = cross
    S[:, i[:-1] + 1, i[:-1]] = cross
    if dx0 is not None:
        r_l = r_l - (e * (dx0[:, 1:] - dx0[:, :-1])).sum(-1)
    proj = 0.0
    T = H = r_b = None
    if bending and nb > 0:
        D = _second_difference(N)
        cb = np.matmul(D, x)  # (B, nb, 3)
        mag = np.linalg.norm(cb, axis=-1)
        a_j = np.broadcast_to(alpha[:, None], (B, nb)).copy()
        target = None
        if dmax is not None:
            viol = mag > (0.98 * dmax)[:, None]
            if viol.any():
                a_j = np.where(viol, limit_alpha, a_j)
                unit = cb / np.where(mag > 1e-12, mag, 1.0)[..., None]
                target = unit * (0.98 * dmax)[:, None, None] * viol[..., None]
        if target is not None:
            cb = cb - target
        r_b = -cb - a_j[..., None] * lam_b
        if dx0 is not None:
            r_b = r_b - np.matmul(D, dx0)
        Dw = D[None] * w[:, None, :]  # (B, nb, N)
        G = Dw @ D.T
        G[:, np.arange(nb), np.arange(nb)] += a_j
        T = np.linalg.inv(G)
        H = (Dw[:, :, 1:] - Dw[:, :, :-1]).transpose(0, 2, 1)  # (B, nl, nb)
        HT = H @ T
        S = S - (HT @ H.transpose(0, 2, 1)) * (e @ e.transpose(0, 2, 1))
        proj = (e * (HT @ r_b)).sum(-1)
    S[:, i, i] += 1e-12
    rhs = r_l - proj
    try:
        dlam = np.linalg.solve(S, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        dlam = np.zeros_like(rhs)
    u = e * dlam[..., None]
    dx = np.zeros_like(x)
    dx[:, :-1] -= u * w[:, :-1, None]
    dx[:, 1:] += u * w[:, 1:, None]
    if T is not None:
        dlam_b = T @ (r_b - H.transpose(0, 2, 1) @ u)
        lam_b += dlam_b
        dx += w[..., None] * np.matmul(D.T, dlam_b)
    # trust region: no point is corrected by more than a segment length at once
    if dx0 is None:
        biggest = np.linalg.norm(dx, axis=-1).max(axis=-1)
        dx = dx * np.minimum(1.0, l0 / np.maximum(biggest, 1e-12))[:, None, None]
    else:
        dx = dx + dx0
    x += dx


def _bend_excess(x, dmax):
    d = np.linalg.norm(x[:, :-2] - 2 * x[:, 1:-1] + x[:, 2:], axis=-1)
    return float((d - dmax[:, None]).max())


def _strain(x, l0, w):
    err = np.abs(np.linalg.norm(x[:, 1:] - x[:, :-1], axis=-1) / l0[:, None] - 1.0)
    return np.where(_free_segments(w), err, 0.0).max()


def solve_batch(x, w, l0, ei_q, dmax, g_dir, boxes, radius, max_steps, tol, step_scale=4.0, limit_alpha=1e-9, g_on=None):
    """Relax the batch ``x`` (B, N, 3) in place. Each outer step moves the free points ``step``
    mm along the unit gravity vectors ``g_dir`` (B, 3) in a way that is first-order compatible with
    the constraints, then restores them with Newton iterations (all lengths and bendings at once);
    collisions are pushed out locally with another global pass when they moved anything. The
    step is ``step_scale * l0``; it is halved when the Newton iterations do not restore the lengths
    and grows back. ``ei_q`` = (q l0^4 / EI): the bending compliance is ``ei_q / step``. Stops
    when no point moves more than ``tol`` mm in a step. Returns the number of steps."""
    free = (w > 0)[..., None]
    unlimited = bool((dmax > 1e11).all())
    done = 0
    if boxes:  # a step must not carry a point through a box
        step_scale = max(1.0, min(step_scale, 4.0 * float(radius.max()) / float(l0.min())))
    scale = step_scale
    lam_b = np.zeros((x.shape[0], x.shape[1] - 2, 3))
    lim = None if unlimited else dmax
    if lim is not None:
        # A first layout that is far past the bend limit: tighten the limit step by step (each wire's
        # worst bend less 10 % a time) so the hard rows are never badly over-determined.
        alpha0 = ei_q / (scale * l0)
        for _ in range(60):
            worst = np.linalg.norm(x[:, :-2] - 2 * x[:, 1:-1] + x[:, 2:], axis=-1).max(axis=-1)
            if (worst <= lim * 1.001).all():
                break
            lim_t = np.maximum(lim, worst * 0.9)
            for _ in range(3):
                _newton(x, w, l0, alpha0, lam_b, bool((alpha0 < 1e6).any()), None, lim_t, limit_alpha)
            lam_b[:] = 0.0
    for done in range(1, max_steps + 1):
        before = x.copy()
        step = scale * l0
        alpha = ei_q / step
        bend_on = bool((alpha < 1e6).any())
        lam_b[:] = 0.0
        _newton(x, w, l0, alpha, lam_b, bend_on, free * (g_dir * (step if g_on is None else step * g_on)[:, None])[:, None, :], lim, limit_alpha)
        for _ in range(8):
            if _strain(x, l0, w) < 1e-7 and (lim is None or _bend_excess(x, lim) <= 0):
                break
            _newton(x, w, l0, alpha, lam_b, bend_on, None, lim, limit_alpha)
        for _ in range(12 if boxes else 0):  # contacts and lengths take turns until both hold
            if not _collide(x, w, boxes, radius - 1e-3, g_dir) and _strain(x, l0, w) < 1e-6:
                break
            _newton(x, w, l0, alpha, lam_b, bend_on, None, lim, limit_alpha)
        if _strain(x, l0, w) > 1e-5:
            scale = max(scale * 0.5, 0.05)
        else:
            scale = min(scale * 1.25, step_scale)
        if np.linalg.norm(x - before, axis=-1).max() < tol:
            break
    return done


def solve(ropes, boxes=(), max_steps=400, tol=1e-3, settle=6):
    """Solve ``ropes`` (a list of :class:`Rope`) in place and fill their results. Ropes with the
    same point count go through the solver together. ``max_steps`` caps the gravity steps (a drag
    uses a handful from the last shape); afterwards ``settle`` length-only passes remove what strain
    is left."""
    groups = {}
    for rope in ropes:
        groups.setdefault(rope.n, []).append(rope)
    for n, group in groups.items():
        x = np.stack([r.x for r in group]).astype(float)
        w = np.stack([r.weights() for r in group])
        l0 = np.array([r.l0 for r in group])
        ei = np.array([max(r.ei, 1e-12) for r in group])
        q = np.array([r.q for r in group])
        rmin = np.array([r.rmin for r in group])
        radius = np.array([r.radius for r in group])
        g = np.array([r.gravity for r in group], dtype=float)
        g /= np.maximum(np.linalg.norm(g, axis=-1), 1e-12)[:, None]
        g_on = np.array([1.0 if r.weight_on else 0.0 for r in group])
        # alpha~ = alpha m / dt^2 with m = q l0 / g, dt^2 = step / g and alpha = l0^3 / EI
        ei_q = q * l0**4 / ei
        dmax = np.where(rmin > 0, l0 * l0 / np.where(rmin > 0, rmin, 1.0), 1e12)
        x0 = x.copy()
        steps = solve_batch(x, w, l0, ei_q, dmax, g, boxes, radius, max_steps, tol, g_on=g_on)
        if (rmin > 0).any():
            strain, ratio = measure(x, l0, rmin, w)
            bad = (strain > STRAIN_TOL) | (ratio > 1.0 + 1e-3)
            if bad.any():
                # The hard bend-limit rows can fail (over-determined, or the limit cannot be met): try the
                # same start with the limit as a stiff spring and keep the better of the two per wire.
                x1 = x0.copy()
                solve_batch(x1, w, l0, ei_q, dmax, g, boxes, radius, max_steps, tol, limit_alpha=1e-3, g_on=g_on)
                s1, r1 = measure(x1, l0, rmin, w)
                worse = (strain + np.maximum(ratio - 1.0, 0.0)) > (s1 + np.maximum(r1 - 1.0, 0.0))
                take = bad & worse
                x[take] = x1[take]
        for _ in range(settle):
            _newton(x, w, l0, np.zeros(x.shape[0]), None, False)
        strain, ratio = measure(x, l0, rmin, w)
        for k, rope in enumerate(group):
            rope.x[:] = x[k]
            rope.strain, rope.bend_ratio, rope.steps = float(strain[k]), float(ratio[k]), steps
            rope.taut = bool(strain[k] > STRAIN_TOL or ratio[k] > 1.0 + 1e-3)
            rope.message = ""
            if strain[k] > STRAIN_TOL:
                rope.message = "taut"
            elif ratio[k] > 1.0 + 1e-3:
                rope.message = "bend limit"
    return ropes


# ----------------------------------------------------------------------------------------------
# Layout and lead points.


def lead_plan(n, length, lead_a=0.0, lead_b=0.0):
    """``(ka, kb, l0)``: how many segments of the ``n - 1`` each lead (of length ``lead_a`` / ``lead_b``,
    0 = no lead) takes, and the segment length ``l0`` of the free part. A lead keeps its own exact
    length (its points are spaced ``lead / k``); the rest of the cut length is shared by the free segments."""
    nominal = length / (n - 1)
    ka = max(1, int(round(lead_a / nominal))) if lead_a > 0 else 0
    kb = max(1, int(round(lead_b / nominal))) if lead_b > 0 else 0
    while n - 1 - ka - kb < 1 and (ka > 1 or kb > 1):
        if ka >= kb:
            ka -= 1
        else:
            kb -= 1
    free = n - 1 - ka - kb
    l0 = max(length - lead_a - lead_b, 1e-6) / max(free, 1)
    return ka, kb, l0


def lead_points(origin, direction, length, k):
    """The ``k + 1`` clamped points of a lead: from ``origin`` along ``direction``, evenly spaced over
    ``length`` (at least one segment, so the exit direction is fixed)."""
    d = np.asarray(direction, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    k = max(1, int(k))
    return [np.asarray(origin, dtype=float) + d * (length / k) * i for i in range(k + 1)]


def _polyline_resample(pts, m):
    pts = np.asarray(pts, dtype=float)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=-1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    u = np.linspace(0.0, s[-1], m)
    return np.stack([np.interp(u, s, pts[:, k]) for k in range(3)], axis=-1)


def _perp_gravity(g, chord):
    g = np.asarray(g, dtype=float)
    g = g / max(np.linalg.norm(g), 1e-12)
    c = chord / max(np.linalg.norm(chord), 1e-12)
    perp = g - (g @ c) * c
    if np.linalg.norm(perp) < 0.2:  # chord along gravity: any sideways direction
        side = np.cross(c, [1.0, 0.0, 0.0] if abs(c[0]) < 0.9 else [0.0, 1.0, 0.0])
        perp = side / np.linalg.norm(side)
        return perp
    return perp / np.linalg.norm(perp)


def _hermite(a, b, ta, tb, k, m):
    s = np.linspace(0.0, 1.0, m)[:, None]
    h00, h10, h01, h11 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
    return h00 * a + h10 * k * ta + h01 * b + h11 * k * tb


def _length(pts):
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=-1).sum())


def initial_layout(n, length, guides, start_lead=None, end_lead=None, gravity=(0, 0, -1)):
    """A first shape for a rope of ``n`` points and cut length ``length`` through ``guides`` (the
    route's points, ends included; a dense sample of the old route is the best guide).
    ``start_lead`` / ``end_lead`` are ``(origin, direction, lead_length)`` tuples: those points are
    clamped straight out of the pin. Between the lead tips:

    * with guides in between, the guide polyline, sagged along ``gravity`` (a parabola whose height
      is found so that the length is right) when the cut length is longer than the polyline;
    * with none and leads on the pins, a cubic Hermite curve leaving each lead along its direction
      (tangent size found for the length), sagged for what is left;
    * otherwise a parabola sag between the two ends.

    A cut length shorter than the guides is spread over them and left taut for the solver to report.

    Returns ``(x, fixed_start, fixed_end, l0)``: the rope is then ``Rope(x, l0 * (n - 1), ...)``."""
    guides = [np.asarray(p, dtype=float) for p in guides]
    la = start_lead[2] if start_lead is not None else 0.0
    lb = end_lead[2] if end_lead is not None else 0.0
    ka, kb, l0 = lead_plan(n, length, la, lb)
    head = lead_points(*start_lead[:2], la, ka) if start_lead is not None else [guides[0]]
    tail = lead_points(*end_lead[:2], lb, kb) if end_lead is not None else [guides[-1]]
    ks, ke = len(head), len(tail)
    m = n - ks - ke + 2  # points between and including the two lead tips
    if m < 2:
        raise ValueError("the leads leave no room for the wire")
    free_len = (m - 1) * l0
    a_pt, b_pt = head[-1], tail[-1]
    dense = 2000
    inner = guides[1:-1]
    if inner:
        base = np.asarray([a_pt] + inner + [b_pt])
        curve = _polyline_resample(base, dense)
    elif start_lead is not None and end_lead is not None:
        ta = np.asarray(start_lead[1], dtype=float)
        ta = ta / np.linalg.norm(ta)
        tb = -np.asarray(end_lead[1], dtype=float)
        tb = tb / np.linalg.norm(tb)
        chord = float(np.linalg.norm(b_pt - a_pt))
        if _length(_hermite(a_pt, b_pt, ta, tb, chord, dense)) > free_len:
            curve = _polyline_resample(np.asarray([a_pt, b_pt]), dense)  # taut: nothing to bulge
        else:
            lo, hi = 0.0, 4.0 * free_len
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if _length(_hermite(a_pt, b_pt, ta, tb, mid, dense)) < free_len:
                    lo = mid
                else:
                    hi = mid
            curve = _hermite(a_pt, b_pt, ta, tb, 0.5 * (lo + hi), dense)
    else:
        curve = _polyline_resample(np.asarray([a_pt, b_pt]), dense)
    plen = _length(curve)
    if free_len > plen * (1 + 1e-9):
        perp = _perp_gravity(gravity, b_pt - a_pt if np.linalg.norm(b_pt - a_pt) > 1e-9 else np.asarray([1.0, 0, 0]))
        t = np.linspace(0.0, 1.0, dense)
        shape = lambda h: curve + (4 * h * t * (1 - t))[:, None] * perp  # noqa: E731
        lo, hi = 0.0, free_len
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if _length(shape(mid)) < free_len:
                lo = mid
            else:
                hi = mid
        curve = shape(0.5 * (lo + hi))
    pts = _polyline_resample(curve, m)  # equal arc length: the segments start near l0
    x = np.vstack([np.array(head[:-1]).reshape(-1, 3), pts, np.array(tail[:-1]).reshape(-1, 3)[::-1]])
    return x, ks, ke, l0


def drape(x, boxes, gravity, radius=0.0, fixed_start=1, fixed_end=1):
    """Lift the points of a first layout that lie inside a box up out of it (against ``gravity``), so a
    rope laid out through an obstacle starts on top of it rather than being pushed out below."""
    g = np.asarray(gravity, dtype=float)
    g = g / max(np.linalg.norm(g), 1e-12)
    free = np.ones(len(x), dtype=bool)
    free[:fixed_start] = False
    if fixed_end:
        free[-fixed_end:] = False
    for box in boxes:
        sd, _ = box_distance(box, x)
        m = box.matrix()
        h = np.asarray(box.half, dtype=float)
        q = (x - np.asarray(box.center, dtype=float)) @ m
        up = (-g) @ m
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (h * np.sign(up) - q) / up
        t = np.where(np.abs(up) > 1e-9, t, np.inf).min(axis=-1)
        lift = (sd < radius) & free & np.isfinite(t)
        x[lift] += (-g) * (np.maximum(t[lift], 0.0) + radius)[:, None]
    return x


def straight_line_sag(x, gravity=(0, 0, -1)):
    """How far the rope's lowest point (along ``gravity``) lies below the straight line between its
    ends, in mm (0 when it does not sag)."""
    g = np.asarray(gravity, dtype=float)
    g = g / np.linalg.norm(g)
    a, b = x[0], x[-1]
    t = np.linspace(0.0, 1.0, len(x))
    line = a[None, :] + t[:, None] * (b - a)[None, :]
    below = ((x - line) @ g).max()
    return float(max(below, 0.0))


def taut_possible(a, b, lead_a, lead_b, length):
    """The quick test: ``|a - b|`` (the lead tips) must not exceed what the rope has left, i.e. the
    lower bound ``|a - b| <= length`` for the tips ``a``, ``b`` and the cut length less the leads."""
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float))) <= length - lead_a - lead_b + 1e-9
