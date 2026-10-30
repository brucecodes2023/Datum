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

    def test_bspline_is_the_same_curve(self):
        """30 Oct: the Part shape is built from these poles, so it must be this spline exactly."""
        for pts in ([(0, 0, 0), (40, 10, 0), (80, -10, 5), (120, 0, 0)],  # four points
                    [(0, 0, 0), (10, 5, 0), (20, 0, 3)],  # three: one parabola
                    [(0, 0, 0), (30, 40, 0)],  # two: a line
                    circle_points(50.0, 9),
                    [(0, 0, 0), (5, 1, 0), (60, 30, 10), (61, 32, 10), (100, 0, -20), (140, 5, 0)]):  # uneven spans
            spline = rt.Spline(pts)
            poles, knots, mults = spline.bspline()
            self.assertEqual(len(poles), len(spline.points) + 2)
            self.assertEqual(knots, spline.params)
            self.assertEqual(sum(mults), len(poles) + 4)
            for i in range(spline.segments):
                for s in (0.0, 0.13, 0.5, 0.77, 1.0):
                    u = spline.params[i] + s * (spline.params[i + 1] - spline.params[i])
                    self.assertAlmostEqual(rt.dist(rt.bspline_value(poles, knots, mults, u), spline.at(i, s)[0]), 0.0, places=9)

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


class BendRuleTests(unittest.TestCase):
    """ROU-02 (30 Oct): minimum bend rules as a multiple of OD or a fixed radius."""

    def test_rule_radius_and_text(self):
        self.assertAlmostEqual(rt.BendRule("OD", 4.0).radius(1.15), 4.6)
        self.assertEqual(rt.BendRule("Fixed", 25.0).radius(99.0), 25.0)
        self.assertEqual(rt.BendRule("None").radius(5.0), 0.0)
        self.assertEqual(rt.BendRule("OD", 1.5).text(), "1.5 × OD")
        self.assertEqual(rt.BendRule("Fixed", 25.0).text(), "25 mm")
        self.assertEqual(rt.BendRule("None").text(), "no check")

    def test_presets(self):
        multiples = {name: rule.value for name, (rule, _src) in rt.WIRE_PRESETS.items()}
        self.assertEqual(multiples["Hook-up PVC"], 4.0)
        self.assertEqual(multiples["Silicone"], 3.0)
        self.assertEqual(multiples["Shielded"], 6.0)
        self.assertEqual(multiples["Coax"], 10.0)
        self.assertEqual(multiples["Polyimide (Kapton)"], 10.0)
        self.assertTrue(all(rule.mode == "OD" and src for rule, src in rt.WIRE_PRESETS.values()))
        self.assertEqual(rt.HOSE_PRESETS["General hose"][0], rt.BendRule("OD", 6.0))
        self.assertEqual(rt.rule_choices("wire")[0], "Default")
        self.assertEqual(rt.rule_choices("wire")[-3:], ["Multiple of OD", "Fixed radius", "No check"])
        self.assertEqual(rt.rule_choices("tube"), ["Default", "Multiple of OD", "Fixed radius", "No check"])
        self.assertEqual(rt.rule_choices("bundle")[0], "From wires")

    def test_defaults_from_the_preferences(self):
        self.assertEqual(rt.default_rule("wire"), (rt.BendRule("OD", 4.0), "Hook-up PVC"))
        self.assertEqual(rt.default_rule("hose"), (rt.BendRule("OD", 6.0), "General hose"))
        self.assertEqual(rt.default_rule("tube"), (rt.BendRule("OD", 1.5), "Multiple of OD"))
        self.assertEqual(rt.PREF_DEFAULTS["TubeClrFactor"], 2.0)
        prefs = {"WireRule": "Silicone", "HoseRule": "Fixed radius", "HoseRadius": "80", "TubeFactor": 2.5}
        self.assertEqual(rt.default_rule("wire", prefs)[0], rt.BendRule("OD", 3.0))
        self.assertEqual(rt.default_rule("hose", prefs)[0], rt.BendRule("Fixed", 80.0))
        self.assertEqual(rt.default_rule("tube", prefs)[0], rt.BendRule("OD", 2.5))
        self.assertEqual(rt.default_rule("wire", {"WireRule": "Gone"})[0], rt.BendRule("OD", 4.0))

    def test_item_rules(self):
        rule, label = rt.item_rule("wire", "Default")
        self.assertEqual((rule, label), (rt.BendRule("OD", 4.0), "Default: Hook-up PVC, 4 × OD"))
        self.assertEqual(rt.item_rule("wire", "Coax"), (rt.BendRule("OD", 10.0), "Coax, 10 × OD"))
        self.assertEqual(rt.item_rule("wire", "Multiple of OD", factor=5.0), (rt.BendRule("OD", 5.0), "5 × OD"))
        self.assertEqual(rt.item_rule("hose", "Fixed radius", radius=42.0), (rt.BendRule("Fixed", 42.0), "42 mm"))
        self.assertEqual(rt.item_rule("tube", "No check")[0].mode, "None")
        self.assertEqual(rt.item_rule("tube", "Default"), (rt.BendRule("OD", 1.5), "Default: 1.5 × OD"))

    def test_bundle_takes_the_strictest_wire(self):
        rules = [rt.BendRule("OD", 4.0), rt.BendRule("OD", 10.0), rt.BendRule("None")]
        self.assertAlmostEqual(rt.bundle_min_radius(rules, 3.2), 32.0)
        self.assertEqual(rt.bundle_min_radius(rules + [rt.BendRule("Fixed", 50.0)], 3.2), 50.0)
        self.assertEqual(rt.bundle_min_radius([rt.BendRule("None")], 3.2), 0.0)
        self.assertEqual(rt.bundle_min_radius([], 3.2), 0.0)

    def test_migration_of_old_items(self):
        self.assertEqual(rt.migrate_rule(60.0, 10.0, 6.0), "Multiple of OD")  # the old hose default
        self.assertEqual(rt.migrate_rule(25.0, 10.0, 6.0), "Fixed radius")
        self.assertEqual(rt.migrate_rule(0.0, 10.0, 6.0), "No check")
        self.assertEqual(rt.migrate_rule(4.6, 1.15, 0.0), "Fixed radius")


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


class LeadTests(unittest.TestCase):
    """ROU-05: a route on a pin or port leaves along +Z for its straight before the first bend."""

    def test_clamped_spline_leaves_along_its_tangent(self):
        pts = [(0, 0, 0), (40, 30, 0), (80, 0, 0)]
        s = rt.Spline(pts, start_tangent=(0, 1, 0), end_tangent=(0, -1, 0))
        for value, want in ((s.at(0, 0.0)[1], (0, 1, 0)), (s.at(s.segments - 1, 1.0)[1], (0, -1, 0))):
            for a, b in zip(value, want):
                self.assertAlmostEqual(a, b, places=9)
        # Two points with one clamped end are a curve, not a line, and still pass both points.
        two = rt.Spline([(0, 0, 0), (10, 10, 0)], start_tangent=(1, 0, 0))
        self.assertLess(two.min_radius(), math.inf)
        self.assertAlmostEqual(rt.dist(two.at(0, 1.0)[0], (10, 10, 0)), 0.0, places=9)
        # The B-spline form is the same curve with a clamped end too.
        poles, knots, mults = s.bspline()
        for i in range(s.segments):
            for t in (0.25, 0.6):
                u = s.params[i] + t * (s.params[i + 1] - s.params[i])
                self.assertAlmostEqual(rt.dist(rt.bspline_value(poles, knots, mults, u), s.at(i, t)[0]), 0.0, places=9)
        # Without tangents nothing changed (not-a-knot, the 30 Oct curve).
        self.assertEqual(rt.Spline(pts).second, rt.Spline(pts, None, None).second)

    def test_spline_route_with_leads(self):
        start = rt.Lead((0, 0, 0), (0, 0, -1), 5.0, "Pin 1 of J1")
        end = rt.Lead((100, 40, 0), (0, 0, -1), 3.0)
        route = rt.build_route([(0, 0, 0), (50, 20, -30), (100, 40, 0)], "Spline", 0.0, start, end)
        self.assertEqual([p[0] for p in route.pieces], ["line", "spline", "line"])
        self.assertEqual(route.pieces[0][1:], ((0.0, 0.0, 0.0), (0.0, 0.0, -5.0)))
        self.assertEqual(route.pieces[2][1:], ((100.0, 40.0, -3.0), (100.0, 40.0, 0.0)))
        self.assertEqual(route.straights, (5.0, 3.0))
        spline = route.pieces[1][1]
        self.assertAlmostEqual(route.length, 8.0 + spline.length(), places=9)
        self.assertAlmostEqual(route.min_radius, spline.min_radius(), places=9)
        # One B-spline for the whole route: the lines at unit speed, knots of multiplicity 3 at the joins.
        poles, knots, mults = route.bspline()
        self.assertEqual(mults, [4, 3, 1, 3, 4])
        self.assertEqual(len(poles), sum(mults) - 4)
        for u in (0.0, 2.5, 5.0):
            self.assertAlmostEqual(rt.dist(rt.bspline_value(poles, knots, mults, u), (0, 0, -u)), 0.0, places=9)
        u = 5.0 + 0.4 * spline.params[1]
        self.assertAlmostEqual(rt.dist(rt.bspline_value(poles, knots, mults, u), spline.at(0, 0.4)[0]), 0.0, places=9)
        self.assertAlmostEqual(rt.dist(rt.bspline_value(poles, knots, mults, knots[-1]), (100, 40, 0)), 0.0, places=9)

    def test_polyline_lead_puts_the_arc_past_the_straight(self):
        start = rt.Lead((0, 0, 0), (1, 0, 0), 10.0)
        route = rt.build_route([(0, 0, 0), (100, 100, 0)], "Polyline", 20.0, start)
        # The corner sits L + R tan(theta / 2) out, so the arc starts exactly 10 from the frame.
        self.assertAlmostEqual(route.straights[0], 10.0, places=6)
        corner = route.points[1]
        theta = math.acos(rt.dot((1, 0, 0), rt.unit(rt.sub((100, 100, 0), corner))))
        self.assertAlmostEqual(corner[0], 10.0 + 20.0 * math.tan(theta / 2), places=6)
        self.assertTrue(route.ok)
        self.assertEqual(route.bends, 1)
        # Two ports side by side, both facing +X, 30 apart: two corners of R 20 do not fit the 30 mm
        # between the lead ends; reported, drawn sharp, the straights still kept.
        a = rt.Lead((0, 0, 0), (1, 0, 0), 10.0, "Port 1 of F1")
        b = rt.Lead((0, 0, 30), (1, 0, 0), 10.0, "Port 1 of F2")
        route = rt.build_route([(0, 0, 0), (0, 0, 30)], "Polyline", 20.0, a, b)
        self.assertFalse(route.ok)
        self.assertTrue(all("needs" in p for p in route.problems), route.problems)
        self.assertGreaterEqual(min(route.straights), 10.0)
        # A next point behind the pin: the lead end goes R past the straight, the corner is reported.
        route = rt.build_route([(0, 0, 0), (5, 0, 20), (100, 100, 0)], "Polyline", 10.0, start)
        self.assertAlmostEqual(route.points[1][0], 20.0, places=6)
        self.assertFalse(route.ok)
        # The summary carries the straights and the vertices.
        summary = rt.route_summary([(0, 0, 0), (100, 100, 0)], "Polyline", 20.0, 0.0, start)
        self.assertEqual(len(summary["points"]), 3)
        self.assertAlmostEqual(summary["straights"][0], 10.0, places=6)

    def test_no_lead_route_is_unchanged(self):
        pts = [(0, 0, 0), (100, 0, 0), (100, 100, 0)]
        self.assertAlmostEqual(rt.route_summary(pts, "Polyline", 20.0)["length"], 160.0 + 10.0 * math.pi, places=9)
        self.assertAlmostEqual(rt.route_summary(pts, "Spline")["length"], rt.spline_length(pts), places=9)
        self.assertEqual(rt.route_summary(pts, "Spline")["straights"], (None, None))

    def test_straight_length_defaults(self):
        self.assertAlmostEqual(rt.straight_length("wire", 1.6), 3.2)
        self.assertAlmostEqual(rt.straight_length("hose", 10.0), 20.0)
        self.assertAlmostEqual(rt.straight_length("tube", 6.0), 12.0)
        self.assertAlmostEqual(rt.straight_length("tube", 6.0, prefs={"TubeClampLength": 30.0}), 30.0)
        self.assertAlmostEqual(rt.straight_length("tube", 20.0, prefs={"TubeClampLength": 30.0}), 40.0)
        self.assertAlmostEqual(rt.straight_length("wire", 1.6, override=7.5), 7.5)
        self.assertAlmostEqual(rt.straight_length("wire", 1.6, prefs={"WireStraightFactor": "3"}), 4.8)

    def test_gauge_ranges(self):
        self.assertIsNone(rt.gauge_fits("24 AWG", 28, 22))
        self.assertIn("thicker", rt.gauge_fits("20 AWG", 28, 22))
        self.assertIn("thinner", rt.gauge_fits("30 AWG", 28, 22))
        self.assertIsNone(rt.gauge_fits("30 AWG", 0, 0))
        self.assertIn("thicker", rt.gauge_fits("1.5 mm²", 0, 22))  # 1.5 mm² is about 16 AWG
        self.assertEqual(rt.awg_number("22 AWG"), 22)
        self.assertIsNone(rt.awg_number("0.5 mm²"))


class ReportTests(unittest.TestCase):
    def test_from_to_csv(self):
        text = rt.from_to_csv([{"wire": "W1", "signal": "+12V", "gauge": "22 AWG", "colour": "red", "from": "J1",
                                "from_pin": "1", "to": "M1", "to_pin": "+", "length_m": 0.2345}])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Wire,Signal,Gauge,Colour,From,From pin,From mate,To,To pin,To mate,Length (m)")
        self.assertEqual(lines[1], "W1,+12V,22 AWG,red,J1,1,,M1,+,,0.234")
        text = rt.from_to_csv([{"wire": "W2", "from": "J1", "from_pin": "2", "from_mate": "P1", "to": "M1",
                                "to_pin": "-", "length_m": 0.1}])
        self.assertEqual(text.splitlines()[1], "W2,,,,J1,2,P1,M1,-,,0.100")

    def test_terminal_csv(self):
        text = rt.terminal_csv([
            {"kind": "Terminal", "manufacturer": "JST", "mpn": "SXH-001T-P0.6", "item": "STD-01-02001-001", "qty": 2},
            {"kind": "Seal", "manufacturer": "TE Connectivity", "mpn": "0413-204-2005", "item": "", "qty": 4},
            {"kind": "Terminal", "manufacturer": "JST", "mpn": "SXH-001T-P0.6", "item": "STD-01-02001-001", "qty": 1}])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Kind,Manufacturer,MPN,Standard Library item,Qty")
        self.assertEqual(lines[1:], ["Seal,TE Connectivity,0413-204-2005,,4", "Terminal,JST,SXH-001T-P0.6,STD-01-02001-001,3"])

    def test_cut_list_csv(self):
        rows = rt.lra_table([(0, 0, 0), (100, 0, 0), (100, 100, 0)], 20.0)
        text = rt.cut_list_csv([{"item": "Pipe", "kind": "Pipe", "size": "12 x 1", "cut_length": 191.4159,
                                 "radius": 20.0, "bends": 1, "table": rows}])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Item,Kind,Size,Cut length (mm),Bend radius,Bends,Bend table")
        self.assertEqual(lines[1], "Pipe,Pipe,12 x 1,191.4,20,1,L80.0 R0.0 A90.0; end L80.0")


if __name__ == "__main__":
    unittest.main()
