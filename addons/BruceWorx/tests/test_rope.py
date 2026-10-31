# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rope solver (BW-ROU-08): the plan's benchmarks. Needs numpy (the FreeCAD pixi python has it)."""

import math
import statistics
import time
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from bruceworx import routing as rt

if np is not None:
    from bruceworx import rope

D12, OD12 = rt.gauge("12 AWG")[0], 3.6


def wire12(material="PVC"):
    d, a = rt.gauge("12 AWG")
    return rope.wire_properties(d, a, OD12, material)


def catenary(d, length):
    """(a, sag) of the catenary of cut length ``length`` between supports ``d`` apart at one height."""
    lo, hi = 1.0, 1e5
    for _ in range(200):
        a = 0.5 * (lo + hi)
        lo, hi = (a, hi) if 2 * a * math.sinh(d / (2 * a)) > length else (lo, a)
    return a, a * (math.cosh(d / (2 * a)) - 1)


def make(n, length, a, b, lead_a=None, lead_b=None, ei=None, q=None, rmin=0.0, radius=1.8):
    e, w = wire12()
    x, ks, ke = rope.initial_layout(n, length, [a, b], lead_a, lead_b)
    return rope.Rope(x, length, ei if ei is not None else e, q if q is not None else w, rmin=rmin, radius=radius,
                     fixed_start=ks, fixed_end=ke)


@unittest.skipIf(np is None, "numpy missing")
class RopeTests(unittest.TestCase):
    def test_tables(self):
        ei, q = wire12()
        self.assertAlmostEqual(ei, 1.69e3, delta=0.02 * 1.69e3)
        self.assertAlmostEqual(q, 3.85e-4, delta=0.1e-4)
        self.assertEqual(rope.point_count(81, 1.6), 35)
        self.assertEqual(rope.point_count(10, 3.6), 20)
        self.assertEqual(rope.point_count(5000, 1.0), 200)

    def test_catenary(self):
        a, sag = catenary(200, 250)
        self.assertAlmostEqual(a, 84.55, places=2)
        self.assertAlmostEqual(sag, 66.36, places=2)
        r = make(60, 250.0, (0, 0, 0), (200, 0, 0), ei=1e-9)
        rope.solve([r], max_steps=400, tol=1e-3)
        got = -r.x[:, 2].min()
        self.assertAlmostEqual(got, sag, delta=0.01 * sag)
        self.assertFalse(r.taut)

    def test_cantilever_stub(self):
        ei, q = wire12()
        expect = q * 100.0**4 / (8 * ei)  # small deflection, tip sag q L^4 / 8 EI
        n = 41
        x = np.array([[i * 100.0 / (n - 1), 0.0, 0.0] for i in range(n)])
        r = rope.Rope(x, 100.0, ei, q, fixed_start=2, fixed_end=0)
        rope.solve([r], max_steps=300, tol=1e-6)
        self.assertAlmostEqual(-r.x[-1, 2], expect, delta=0.05 * expect)
        print(f"\n  EI {ei:.0f} N mm2, q {q:.3e} N/mm, tip sag {-r.x[-1, 2]:.3f} mm (expected {expect:.3f})")

    def test_length_kept_after_random_moves(self):
        rng = np.random.default_rng(3)
        r = make(40, 150.0, (0, 0, 0), (100, 0, 0), ((0, 0, 0), (0, 0, 1), 4), ((100, 0, 0), (0, 0, 1), 4), rmin=14.4)
        rope.solve([r], max_steps=300)
        for _ in range(25):
            r.x[-r.fixed_end :] += rng.uniform(-2, 2, 3) * np.array([1, 1, 0.5])
            r.x[: r.fixed_start] += rng.uniform(-1, 1, 3) * 0.5
            rope.solve([r], max_steps=60, tol=1e-2)
            if not r.taut:
                total = rope.segment_lengths(r.x).sum()
                self.assertAlmostEqual(total / 150.0, 1.0, delta=1e-3)

    def test_bend_limit_never_exceeded(self):
        # a pin turned back on itself: both leads leave +X, 30 mm apart (the U-turn needs 28.8)
        r = make(40, 150.0, (0, 0, 0), (0, 30, 0), ((0, 0, 0), (1, 0, 0), 4), ((0, 30, 0), (1, 0, 0), 4), rmin=14.4)
        rope.solve([r], max_steps=400)
        self.assertGreater(rope.measure(r.x[None], np.array([r.l0]), np.array([0.0]))[0][0], -1)
        _s, ratio = rope.measure(r.x[None], np.array([r.l0]), np.array([14.4]))
        self.assertLessEqual(float(ratio[0]), 1.0 + 1e-3)
        self.assertFalse(r.taut)

    def test_collision_with_a_box(self):
        box = rope.Box(center=(50, 0, -10), half=(20, 20, 10))  # top at z = 0
        r = make(50, 116.0, (0, 0, 15), (100, 0, 15), radius=1.8, ei=20.0)  # a limp wire, just longer than the way over the box
        rope.drape(r.x, [box], (0, 0, -1), 1.8, r.fixed_start, r.fixed_end)
        rope.solve([r], boxes=[box], max_steps=400, tol=1e-3)
        sd, _g = rope.box_distance(box, r.x)
        self.assertGreater(float(sd.min()), -1e-2)
        self.assertLess(float(sd.min()), 1.8 + 0.5)  # it rests on the box
        self.assertGreater(float((sd < 2.5).sum()), 3)
        self.assertFalse(r.taut)

    def test_oriented_box_distance(self):
        c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
        box = rope.Box((0, 0, 0), (10, 5, 5), ((c, s, 0), (-s, c, 0), (0, 0, 1)))
        outside = 13.0 * np.array([c, s, 0.0])  # 3 mm beyond the +X face along the box's own X axis
        sd, grad = rope.box_distance(box, np.array([[0.0, 0.0, 0.0], outside]))
        self.assertAlmostEqual(float(sd[0]), -5.0, places=9)
        self.assertAlmostEqual(float(sd[1]), 3.0, places=9)

    def test_taut_detection(self):
        far = make(40, 250.0, (0, 0, 0), (300, 0, 0))
        rope.solve([far], max_steps=100)
        self.assertTrue(far.taut)
        self.assertFalse(rope.taut_possible((0, 0, 0), (300, 0, 0), 0, 0, 250.0))
        near = make(40, 250.0, (0, 0, 0), (200, 0, 0))
        rope.solve([near], max_steps=200)
        self.assertFalse(near.taut)
        self.assertGreater(rope.straight_line_sag(near.x), 30.0)

    def test_warm_start_time(self):
        ropes = [make(40, 150.0, (0, 5 * k, 0), (100, 5 * k, 0), ((0, 5 * k, 0), (0, 0, 1), 4),
                      ((100, 5 * k, 0), (0, 0, 1), 4), rmin=14.4) for k in range(6)]
        rope.solve(ropes, max_steps=300)
        times = []
        for _ in range(20):
            for r in ropes:
                r.x[-r.fixed_end :] += np.array([0.3, 0.0, 0.1])
            t = time.perf_counter()
            rope.solve(ropes, max_steps=15, tol=1e-2, settle=1)
            times.append(time.perf_counter() - t)
        median = statistics.median(times) * 1e3
        print(f"\n  6 wires x 40 points, warm start: median {median:.1f} ms per drag step")
        self.assertFalse(any(r.taut for r in ropes))
        self.assertLess(median, 10.0)


if __name__ == "__main__":
    unittest.main()
