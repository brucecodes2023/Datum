# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drone balance and thrust-to-weight (roadmap ROB-04): the maths, without FreeCAD.

Masses are in grams, thrust in gram-force (what motor test data gives), lengths in mm. A motor is a point
(its axis, in the frame) and a thrust table (throttle %, thrust g, current A, voltage V). The frame plane is the
plane perpendicular to the mean thrust axis; ``e`` is the distance of the centre of mass from the thrust centre
(the mean of the motor points) in that plane.

Hover share, the force and moment balance with arms r_i from the thrust centre and the CG offset dc:

    T_i = W/n + W r_i^T (sum_j r_j r_j^T)^-1 dc        (sum T_i = W, sum T_i r_i = W dc)

Flight time is estimated as 0.8 C / I_hover (80 % of the pack is usable).
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field

USABLE = 0.8
DEFAULTS = {
    "tw_amber": 2.0,
    "tw_red": 1.5,
    "offset_amber": 2.0,  # % of the motor diagonal
    "offset_red": 5.0,
    "hover_amber": 60.0,  # % throttle
    "hover_red": 80.0,
}
OK, AMBER, RED = "ok", "amber", "red"
_ORDER = {OK: 0, AMBER: 1, RED: 2}

# ----- thrust tables ---------------------------------------------------------------------------------------------

_COLUMNS = (("throttle", ("throttle", "pwm", "duty")), ("thrust", ("thrust",)),
            ("current", ("current", "amp")), ("voltage", ("volt",)))


class ThrustError(ValueError):
    pass


@dataclass
class ThrustTable:
    """Rows (throttle %, thrust g, current A, voltage V), sorted by throttle."""

    rows: list = field(default_factory=list)

    @property
    def max_thrust(self):
        return max(r[1] for r in self.rows)

    @property
    def max_throttle(self):
        return self.rows[-1][0]

    @property
    def voltage(self):
        volts = [r[3] for r in self.rows if r[3]]
        return volts[-1] if volts else 0.0

    def _at(self, throttle, col):
        rows = self.rows
        if throttle <= rows[0][0]:
            return rows[0][col]
        for a, b in zip(rows, rows[1:]):
            if throttle <= b[0]:
                f = (throttle - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
                return a[col] + f * (b[col] - a[col])
        return rows[-1][col]

    def thrust_at(self, throttle):
        return self._at(throttle, 1)

    def current_at(self, throttle):
        return self._at(throttle, 2)

    def throttle_for(self, thrust):
        """The throttle % that gives ``thrust`` g (interpolated), None when above the table's maximum."""
        rows = self.rows
        if thrust > self.max_thrust + 1e-9:
            return None
        if thrust <= rows[0][1]:
            return rows[0][0]
        for a, b in zip(rows, rows[1:]):
            if a[1] <= thrust <= b[1]:
                f = (thrust - a[1]) / (b[1] - a[1]) if b[1] > a[1] else 0.0
                return a[0] + f * (b[0] - a[0])
        return rows[-1][0]

    def current_for(self, thrust):
        t = self.throttle_for(thrust)
        return None if t is None else self.current_at(t)


def parse_thrust_csv(text):
    """A ThrustTable from CSV text. Columns by header (throttle, thrust, current, voltage; any order, case, extra
    words like "Thrust (g)"), or in that order when there is no header. Throttle given as 0..1 becomes percent."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ThrustError("the thrust table is empty")
    dialect = csv.excel_tab if "\t" in lines[0] else csv.excel
    rows = list(csv.reader(io.StringIO("\n".join(lines)), dialect))
    index = {"throttle": 0, "thrust": 1, "current": 2, "voltage": 3}
    first = [c.strip().lower() for c in rows[0]]
    try:
        [float(c) for c in first if c]
        header = False
    except ValueError:
        header = True
    if header:
        index = {}
        for key, words in _COLUMNS:
            for i, cell in enumerate(first):
                if any(w in cell for w in words) and i not in index.values():
                    index[key] = i
                    break
        if "throttle" not in index or "thrust" not in index:
            raise ThrustError("the thrust table needs throttle and thrust columns")
        rows = rows[1:]
    data = []
    for row in rows:
        def cell(key):
            i = index.get(key)
            if i is None or i >= len(row) or not row[i].strip():
                return 0.0
            return float(row[i])

        try:
            data.append((cell("throttle"), cell("thrust"), cell("current"), cell("voltage")))
        except ValueError as exc:
            raise ThrustError(f"unreadable number in the thrust table: {exc}") from None
    if len(data) < 2:
        raise ThrustError("the thrust table needs at least two rows")
    if max(r[0] for r in data) <= 1.0:
        data = [(r[0] * 100.0,) + r[1:] for r in data]
    data.sort(key=lambda r: r[0])
    return ThrustTable(data)


# ----- geometry --------------------------------------------------------------------------------------------------


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n > 0 else (0.0, 0.0, 1.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def plane_basis(axis):
    """Two orthonormal vectors perpendicular to ``axis``."""
    n = _unit(axis)
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(n, helper))
    return u, _cross(n, u)


def project(points, axis):
    u, v = plane_basis(axis)
    return [(sum(p[k] * u[k] for k in range(3)), sum(p[k] * v[k] for k in range(3))) for p in points]


def diagonal(points2):
    return max((math.dist(a, b) for a in points2 for b in points2), default=0.0)


def hover_shares(weight, points2, dc):
    """(shares, solved): per-motor thrust (same unit as ``weight``) that holds hover with the CG offset ``dc`` from the
    thrust centre of ``points2``. ``solved`` is False when the motors cannot trim the offset (collinear): equal shares."""
    n = len(points2)
    cx = sum(p[0] for p in points2) / n
    cy = sum(p[1] for p in points2) / n
    r = [(p[0] - cx, p[1] - cy) for p in points2]
    sxx = sum(a * a for a, _b in r)
    syy = sum(b * b for _a, b in r)
    sxy = sum(a * b for a, b in r)
    det = sxx * syy - sxy * sxy
    if n < 3 or det <= 1e-9 * max(sxx + syy, 1e-30) ** 2:
        return [weight / n] * n, abs(dc[0]) + abs(dc[1]) < 1e-9
    # S^-1 dc
    wx = (syy * dc[0] - sxy * dc[1]) / det
    wy = (-sxy * dc[0] + sxx * dc[1]) / det
    return [weight / n + weight * (a * wx + b * wy) for a, b in r], True


def flight_time_min(capacity_mah, hover_current_a):
    """Minutes: 0.8 C / I. None without a capacity or a current."""
    if not capacity_mah or not hover_current_a or hover_current_a <= 0:
        return None
    return USABLE * capacity_mah / 1000.0 / hover_current_a * 60.0


# ----- the analysis ----------------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    level: str
    text: str


@dataclass
class MotorResult:
    name: str
    share: float  # g
    throttle: float | None  # % at the hover share, None above the table
    current: float | None
    max_thrust: float


@dataclass
class Balance:
    mass: float
    weight: float
    thrust_max: float
    tw: float | None
    offset: float
    diagonal: float
    offset_pct: float
    hover_throttle: float | None  # % where each motor gives mg/n on its own table (the highest), None above a table
    peak_throttle: float | None  # % of the busiest motor at its actual hover share
    hover_current: float | None  # A, all motors at their shares
    flight_min: float | None
    motors: list
    checks: list
    solved: bool = True

    @property
    def level(self):
        return max((c.level for c in self.checks), key=_ORDER.get, default=OK)


def analyse(mass_g, cg, motors, capacity_mah=0.0, thresholds=None):
    """``motors``: [(name, point (x, y, z) mm, axis, ThrustTable or None)]; ``cg`` (x, y, z) mm; ``mass_g`` grams."""
    th = dict(DEFAULTS)
    th.update(thresholds or {})
    if not motors:
        raise ThrustError("no motors are marked")
    names = [m[0] for m in motors]
    axes = [m[2] for m in motors]
    mean_axis = _unit(tuple(sum(a[k] for a in axes) for k in range(3)))
    pts = project([m[1] for m in motors], mean_axis)
    c_m = project([cg], mean_axis)[0]
    n = len(motors)
    ct = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    dc = (c_m[0] - ct[0], c_m[1] - ct[1])
    e = math.hypot(*dc)
    diag = diagonal(pts)
    pct = 100.0 * e / diag if diag > 0 else 0.0
    shares, solved = hover_shares(mass_g, pts, dc)
    results, total_current, throttles, missing = [], 0.0, [], []
    for (name, _p, _a, table), share in zip(motors, shares):
        if table is None:
            missing.append(name)
            results.append(MotorResult(name, share, None, None, 0.0))
            continue
        t = table.throttle_for(share)
        i = table.current_for(share)
        results.append(MotorResult(name, share, t, i, table.max_thrust))
        throttles.append(t)
        if i is not None:
            total_current += i
    complete = not missing
    thrust_max = sum(r.max_thrust for r in results)
    tw = thrust_max / mass_g if complete and mass_g > 0 else None
    over = any(t is None for t in throttles)
    peak = None if over or not complete else max(throttles)
    hover_throttle = None
    if complete and all(m[3].throttle_for(mass_g / n) is not None for m in motors):
        hover_throttle = max(m[3].throttle_for(mass_g / n) for m in motors)
    hover_current = total_current if complete and not over else None
    checks = []
    if missing:
        checks.append(Check("Thrust data", AMBER, "no thrust table for " + ", ".join(missing)))
    if tw is not None:
        level = RED if tw < th["tw_red"] else AMBER if tw < th["tw_amber"] else OK
        checks.append(Check("Thrust to weight", level, f"T/W {tw:.2f} (amber below {th['tw_amber']:g}, red below "
                                                       f"{th['tw_red']:g})"))
    level = RED if pct > th["offset_red"] else AMBER if pct > th["offset_amber"] else OK
    checks.append(Check("CG offset", level, f"CG is {e:.2f} mm off the thrust centre, {pct:.2f} % of the motor "
                                            f"diagonal {diag:.0f} mm (amber above {th['offset_amber']:g} %, red above "
                                            f"{th['offset_red']:g} %)"))
    if not solved:
        checks.append(Check("Trim", RED, "the motors cannot trim this CG offset (fewer than three, or in a line)"))
    if over:
        checks.append(Check("Hover", RED, "a motor cannot hold its hover share: above the table's maximum thrust"))
    elif peak is not None:
        level = RED if peak > th["hover_red"] else AMBER if peak > th["hover_amber"] else OK
        checks.append(Check("Hover throttle", level, f"busiest motor hovers at {peak:.0f} % throttle "
                                                      f"(amber above {th['hover_amber']:g} %)"))
    return Balance(mass_g, mass_g, thrust_max, tw, e, diag, pct, hover_throttle, peak,
                   hover_current, flight_time_min(capacity_mah, hover_current), results, checks, solved)
