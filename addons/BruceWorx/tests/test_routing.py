# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing maths (BW-ROU-01): spline lengths and curvature, polylines with tangent arcs, the LRA
bend table, the gauge and tube tables, the two CSV reports."""

import math
import unittest

from bruceworx import routing as rt


def circle_points(radius, n, sweep=math.pi):
    return [(radius * math.cos(k * sweep / (n - 1)), radius * math.sin(k * sweep / (n - 1)), 0.0) for k in range(n)]


class SplineTests(unittest.TestCase):
    def test_two_points_is_a_straight_line(self):
        self.assertAlmostEqual(rt.spline_length([(0, 0, 0), (30, 40, 0)]), 50.0, places=9)
        self.assertEqual(rt.spline_min_radius([(0, 0, 0), (30, 40, 0)]), math.inf)

    def test_collinear_points_stay_straight(self):
        pts = [(0, 0, 0), (10, 0, 0), (20, 0, 0), (35, 0, 0)]
        self.assertAlmostEqual(rt.spline_length(pts), 35.0, places=6)
        self.assertEqual(rt.spline_min_radius(pts), math.inf)

    def test_semicircle_length_and_radius(self):
        pts = circle_points(100.0, 13)
        self.assertAlmostEqual(rt.spline_length(pts) / (math.pi * 100.0), 1.0, delta=0.01)
        self.assertAlmostEqual(rt.spline_min_radius(pts) / 100.0, 1.0, delta=0.06)

    def test_curvature_of_a_circle_is_one_over_r(self):
        r = 25.0
        for t in (0.0, 0.7, 2.1):
            r1 = (-r * math.sin(t), r * math.cos(t), 0.0)
            r2 = (-r * math.cos(t), -r * math.sin(t), 0.0)
            self.assertAlmostEqual(rt.curvature(r1, r2), 1.0 / r, places=12)
        self.assertEqual(rt.curvature((1, 0, 0), (0, 0, 0)), 0.0)
        self.assertEqual(rt.curvature((0, 0, 0), (1, 0, 0)), 0.0)

    def test_samples_start_and_end_on_the_points(self):
        pts = [(0, 0, 0), (10, 5, 0), (20, 0, 3)]
        samples = rt.spline_samples(pts, 8)
        self.assertEqual(len(samples), 17)
        self.assertAlmostEqual(rt.dist(samples[0], pts[0]), 0.0)
        self.assertEqual(samples[-1], pts[-1])
        self.assertAlmostEqual(rt.dist(samples[8], (10.0, 5.0, 0.0)), 0.0)

    def test_duplicates_and_vectors_are_cleaned(self):
        class V:
            def __init__(self, x, y, z):
                self.x, self.y, self.z = x, y, z

        pts = rt.clean_points([V(0, 0, 0), (0, 0, 0), V(5, 0, 0)])
        self.assertEqual(pts, [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)])


class PolylineTests(unittest.TestCase):
    L = [(0, 0, 0), (100, 0, 0), (100, 100, 0)]

    def test_right_angle_with_one_arc(self):
        route = rt.polyline_with_arcs(self.L, 20.0)
        self.assertTrue(route.ok)
        self.assertEqual(len(route.bends), 1)
        b = route.bends[0]
        self.assertAlmostEqual(b.degrees, 90.0)
        self.assertAlmostEqual(b.tangent, 20.0)
        self.assertAlmostEqual(b.arc_length, 20.0 * math.pi / 2)
        self.assertAlmostEqual(rt.dist(b.start, (80.0, 0.0, 0.0)), 0.0)
        self.assertAlmostEqual(rt.dist(b.end, (100.0, 20.0, 0.0)), 0.0)
        self.assertAlmostEqual(rt.dist(b.centre, (80.0, 20.0, 0.0)), 0.0)
        self.assertAlmostEqual(rt.dist(b.middle, b.centre), 20.0)
        self.assertAlmostEqual(route.length, 160.0 + 10.0 * math.pi)
        self.assertEqual([s[0] for s in route.segments], ["line", "arc", "line"])
        self.assertEqual(route.min_radius, 20.0)

    def test_developed_length(self):
        self.assertAlmostEqual(rt.developed_length(self.L, 20.0), 160.0 + 10.0 * math.pi)
        self.assertAlmostEqual(rt.developed_length(self.L, 0.0), 200.0)

    def test_overlapping_tangents_are_reported(self):
        route = rt.polyline_with_arcs(self.L, 120.0)
        self.assertFalse(route.ok)
        self.assertIn("corner 1", route.problems[0])
        self.assertEqual([s[0] for s in route.segments], ["line", "line"])
        self.assertAlmostEqual(route.length, 200.0)
        # Two corners sharing a short middle segment.
        pts = [(0, 0, 0), (100, 0, 0), (100, 30, 0), (200, 30, 0)]
        self.assertFalse(rt.polyline_with_arcs(pts, 20.0).ok)
        self.assertTrue(rt.polyline_with_arcs(pts, 10.0).ok)

    def test_straight_corners_make_no_bend(self):
        route = rt.polyline_with_arcs([(0, 0, 0), (50, 0, 0), (100, 0, 0)], 20.0)
        self.assertEqual(route.bends, [])
        self.assertAlmostEqual(route.length, 100.0)
        self.assertEqual(route.min_radius, math.inf)

    def test_fold_back_is_a_problem(self):
        route = rt.polyline_with_arcs([(0, 0, 0), (50, 0, 0), (0, 0, 0.0001)], 5.0)
        self.assertFalse(route.ok)

    def test_lra_table_of_a_three_bend_tube(self):
        pts = [(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100), (100, 0, 100)]
        rows = rt.lra_table(pts, 20.0)
        self.assertEqual([r["bend"] for r in rows], [1, 2, 3, None])
        got = [(round(r["L"], 6), round(r["R"], 6), round(r["A"], 6)) for r in rows]
        self.assertEqual(got, [(80.0, 0.0, 90.0), (60.0, 90.0, 90.0), (60.0, 0.0, 90.0), (80.0, 0.0, 0.0)])
        self.assertAlmostEqual(rt.developed_length(pts, 20.0), 280.0 + 3 * 10.0 * math.pi)
        text = rt.bend_table_text(rows)
        self.assertEqual(text.splitlines()[1], "2  L 60.0  R 90.0  A 90.0")
        self.assertEqual(text.splitlines()[-1], "end  L 80.0")

    def test_lra_rotation_sign(self):
        # Second bend turns the other way: rotation -90.
        pts = [(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, -100)]
        rows = rt.lra_table(pts, 20.0)
        self.assertAlmostEqual(rows[1]["R"], -90.0)


class SummaryTests(unittest.TestCase):
    def test_polyline_summary_checks_the_minimum(self):
        pts = [(0, 0, 0), (100, 0, 0), (100, 100, 0)]
        s = rt.route_summary(pts, "Polyline", radius=20.0, min_radius=30.0)
        self.assertFalse(s["ok"])
        self.assertEqual(s["bends"], 1)
        self.assertAlmostEqual(s["length"], 160.0 + 10.0 * math.pi)
        self.assertTrue(rt.route_summary(pts, "Polyline", radius=20.0, min_radius=20.0)["ok"])

    def test_spline_summary(self):
        s = rt.route_summary(circle_points(50.0, 9), "Spline", min_radius=60.0)
        self.assertFalse(s["ok"])
        self.assertEqual(s["bends"], 7)
        self.assertTrue(rt.route_summary(circle_points(50.0, 9), "Spline", min_radius=40.0)["ok"])
        self.assertTrue(rt.route_summary([(0, 0, 0)], "Spline")["ok"])


class TableTests(unittest.TestCase):
    def test_awg_diameters(self):
        self.assertAlmostEqual(rt.awg_diameter(36), 0.127, places=9)
        self.assertAlmostEqual(rt.gauge("22 AWG")[0], 0.644, places=3)
        self.assertAlmostEqual(rt.gauge("10 AWG")[0], 2.588, places=3)
        self.assertAlmostEqual(rt.gauge("0 AWG")[0], 8.251, places=3)
        self.assertAlmostEqual(rt.gauge("22 AWG")[1], 0.326, places=3)
        self.assertAlmostEqual(rt.gauge("1.5 mm²")[1], 1.5)
        self.assertEqual(rt.GAUGE_NAMES[0], "30 AWG")
        self.assertEqual(rt.GAUGE_NAMES[30], "0 AWG")
        self.assertIn("2.5 mm²", rt.GAUGE_NAMES)
        with self.assertRaises(ValueError):
            rt.gauge("7 gauge")

    def test_wire_mass(self):
        d, area = rt.gauge("22 AWG")
        self.assertAlmostEqual(rt.wire_mass("22 AWG", 1000.0, d), area * 1000.0 * 8.96e-3, places=6)
        bare = rt.wire_mass("22 AWG", 1000.0, 0.0)
        insulated = rt.wire_mass("22 AWG", 1000.0, 1.6)
        self.assertGreater(insulated, bare)
        self.assertAlmostEqual(insulated - bare, math.pi * (1.6**2 - d**2) / 4 * 1000.0 * 1.4e-3, places=6)

    def test_insulation_default_and_bundle(self):
        self.assertAlmostEqual(rt.default_insulation_od("22 AWG"), 1.14, places=2)
        self.assertEqual(rt.default_insulation_od("30 AWG"), 1.0)
        self.assertAlmostEqual(rt.bundle_od([1.0, 1.0, 1.0, 1.0]), 1.15 * 2.0)
        self.assertEqual(rt.bundle_od([]), 0.0)

    def test_tube_sizes(self):
        self.assertEqual(rt.tube_size("12 x 1"), (12.0, 1.0))
        self.assertEqual(rt.tube_size('NPS 1/2"'), (21.3, 2.77))
        self.assertEqual(rt.TUBE_SIZE_NAMES[0], "Custom")
        with self.assertRaises(ValueError):
            rt.tube_size("Custom")


class ReportTests(unittest.TestCase):
    def test_from_to_csv(self):
        text = rt.from_to_csv([{"wire": "W1", "signal": "+12V", "gauge": "22 AWG", "colour": "red", "from": "J1",
                                "from_pin": "1", "to": "M1", "to_pin": "+", "length_m": 0.2345}])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Wire,Signal,Gauge,Colour,From,From pin,To,To pin,Length (m)")
        self.assertEqual(lines[1], "W1,+12V,22 AWG,red,J1,1,M1,+,0.234")

    def test_cut_list_csv(self):
        rows = rt.lra_table([(0, 0, 0), (100, 0, 0), (100, 100, 0)], 20.0)
        text = rt.cut_list_csv([{"item": "Pipe", "kind": "Pipe", "size": "12 x 1", "cut_length": 191.4159,
                                 "radius": 20.0, "bends": 1, "table": rows}])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Item,Kind,Size,Cut length (mm),Bend radius,Bends,Bend table")
        self.assertEqual(lines[1], "Pipe,Pipe,12 x 1,191.4,20,1,L80.0 R0.0 A90.0; end L80.0")


if __name__ == "__main__":
    unittest.main()
