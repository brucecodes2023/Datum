# SPDX-License-Identifier: LGPL-2.1-or-later
"""ROB-04 maths: thrust tables, hover shares, checks, flight time (no FreeCAD)."""

import math
import os
import sys

import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bruceworx import balance as b  # noqa: E402


class approx:
    def __init__(self, want, rel=1e-6, abs=1e-9):
        self.want, self.rel, self.abs = want, rel, abs

    def __eq__(self, got):
        if isinstance(self.want, (tuple, list)):
            return len(got) == len(self.want) and all(approx(w, self.rel, self.abs) == g for w, g in zip(self.want, got))
        return math.isclose(got, self.want, rel_tol=self.rel, abs_tol=self.abs)

    def __repr__(self):
        return f"approx({self.want})"

CSV = """throttle %,thrust (g),current (A),voltage (V)
0,0,0.5,14.8
25,150,2.0,14.8
50,520,8.0,14.8
75,1000,18.0,14.8
100,1400,32.0,14.8
"""


def table():
    return b.parse_thrust_csv(CSV)


def quad(arm=115.0):
    a = arm / math.sqrt(2)
    return [(f"m{i}", (sx * a, sy * a, 0.0), (0, 0, 1), table()) for i, (sx, sy) in
            enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1)))]



class BalanceTests(unittest.TestCase):
    def test_parse_headers_any_order_and_percent_fraction(self):
        t = b.parse_thrust_csv("Thrust g,Throttle,Amps\n0,0,1\n700,0.5,9\n1400,1.0,30\n")
        assert [r[0] for r in t.rows] == [0.0, 50.0, 100.0]
        assert t.max_thrust == 1400
        assert t.current_at(25) == approx(5.0)
        with self.assertRaises(b.ThrustError):
            b.parse_thrust_csv("a,b\n1,2\n")


    def test_headerless_table_and_comments(self):
        t = b.parse_thrust_csv("# 2207 5in\n0,0,0,16\n100,1000,30,16\n")
        assert t.voltage == 16 and t.thrust_at(50) == 500


    def test_hover_interpolation(self):
        t = table()
        assert t.throttle_for(520) == approx(50.0)
        assert t.throttle_for(335) == approx(25 + 25 * (335 - 150) / (520 - 150))
        assert t.throttle_for(1401) is None
        assert t.current_for(1000) == approx(18.0)


    def test_shares_sum_to_weight_and_balance_moments(self):
        pts = [(80, 80), (-80, 80), (-80, -80), (80, -80), (0, 130)]
        dc = (3.0, -7.0)
        shares, ok = b.hover_shares(500.0, pts, dc)
        assert ok and sum(shares) == approx(500.0)
        cx = sum(p[0] for p in pts) / 5
        cy = sum(p[1] for p in pts) / 5
        mx = sum(s * (p[0] - cx) for s, p in zip(shares, pts))
        my = sum(s * (p[1] - cy) for s, p in zip(shares, pts))
        assert (mx, my) == approx((500 * dc[0], 500 * dc[1]))


    def test_symmetric_quad_matches_the_closed_form(self):
        a = 115.0
        pts = [(a * math.cos(math.radians(45 + 90 * k)), a * math.sin(math.radians(45 + 90 * k))) for k in range(4)]
        dc = (2.45, 0.0)
        shares, _ok = b.hover_shares(388.1, pts, dc)
        for s, r in zip(shares, pts):
            assert s == approx(388.1 / 4 + 388.1 / (2 * a * a) * (r[0] * dc[0] + r[1] * dc[1]))
        shares0, _ = b.hover_shares(400.0, pts, (0, 0))
        assert shares0 == approx([100.0] * 4)


    def test_untrimmable_in_a_line(self):
        shares, ok = b.hover_shares(100.0, [(0, 0), (100, 0)], (0.0, 5.0))
        assert not ok and shares == [50.0, 50.0]


    def test_flight_time(self):
        assert b.flight_time_min(1500, 10.0) == approx(0.8 * 1.5 / 10.0 * 60)
        assert b.flight_time_min(0, 10.0) is None and b.flight_time_min(1500, 0) is None


    def test_balanced_quad_is_ok(self):
        r = b.analyse(400.0, (0, 0, 5), quad(), 1500)
        assert r.level == b.OK
        assert r.tw == approx(4 * 1400 / 400.0)
        assert r.hover_throttle == approx(table().throttle_for(100.0))
        assert r.hover_current == approx(4 * table().current_for(100.0))
        assert r.flight_min == approx(b.flight_time_min(1500, r.hover_current))
        assert r.offset == approx(0.0, abs=1e-9)


    def test_offset_and_low_tw_are_flagged(self):
        r = b.analyse(400.0, (6.0, 0, 0), quad(), 0)  # 6 mm of a 230 mm diagonal = 2.6 %
        assert r.level == b.AMBER and r.offset_pct == approx(600 / 230, rel=1e-6)
        far = b.analyse(400.0, (14.0, 0, 0), quad())
        assert far.level == b.RED
        heavy = b.analyse(3000.0, (0, 0, 0), quad())  # T/W 1.87
        assert [c.level for c in heavy.checks if c.name == "Thrust to weight"] == [b.AMBER]
        assert b.analyse(4000.0, (0, 0, 0), quad()).level == b.RED  # T/W 1.4


    def test_hover_throttle_amber_and_thresholds(self):
        r = b.analyse(2600.0, (0, 0, 0), quad())  # 650 g each, about 56 % throttle
        assert r.peak_throttle < 60
        r = b.analyse(3000.0, (0, 0, 0), quad())  # 750 g each
        assert r.peak_throttle > 60 and r.level == b.AMBER
        custom = b.analyse(3000.0, (0, 0, 0), quad(), thresholds={"hover_amber": 80, "tw_amber": 1.4})
        assert custom.level == b.OK


    def test_missing_table_and_axes_off_vertical(self):
        motors = quad()
        motors[0] = (motors[0][0], motors[0][1], motors[0][2], None)
        r = b.analyse(400.0, (0, 0, 0), motors)
        assert r.tw is None and any(c.name == "Thrust data" for c in r.checks)
        # the frame plane follows the thrust axis: a frame in the xz plane with thrust along y
        xz = [(n, (p[0], 0.0, p[1]), (0, 1, 0), t) for n, p, _a, t in quad()]
        r = b.analyse(400.0, (6.0, 50.0, 0.0), xz)
        assert r.offset == approx(6.0)


if __name__ == "__main__":
    unittest.main()
