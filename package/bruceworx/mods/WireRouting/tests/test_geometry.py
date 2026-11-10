# SPDX-License-Identifier: LGPL-2.1-or-later
"""Wire centerline maths, clip graph, bundles and cut list. Plain Python:

    python3 -m unittest discover -s package/bruceworx/mods/WireRouting/tests
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wire_routing import cutlist, geometry as g  # noqa: E402


class RouteTests(unittest.TestCase):
    def test_straight_wire(self):
        r = g.route([(0, 0, 0), (100, 0, 0)], 5)
        self.assertAlmostEqual(r.length, 100.0, places=9)
        self.assertEqual(len(r.elements), 1)

    def test_right_angle(self):
        r = g.route([(0, 0, 0), (50, 0, 0), (50, 40, 0)], 10)
        # 90 degrees: tangent 10, arc 10*pi/2
        self.assertAlmostEqual(r.length, 40 + 30 + 10 * math.pi / 2, places=9)
        arc = r.elements[1]
        self.assertAlmostEqual(g.distance(arc.center, (40, 10, 0)), 0, places=9)
        for p in (arc.start, arc.mid, arc.end):
            self.assertAlmostEqual(g.distance(p, arc.center), 10, places=9)

    def test_three_corners_match_line_plus_arc(self):
        """BW-WIR-02 acceptance: analytic length within 0.01 mm."""
        pts = [(0, 0, 0), (60, 0, 0), (60, 50, 0), (60, 50, 40), (120, 50, 40)]
        radius = 8.0
        r = g.route(pts, radius)
        expected = (60 + 50 + 40 + 60) - 3 * 2 * radius + 3 * radius * math.pi / 2
        self.assertAlmostEqual(r.length, expected, places=9)
        self.assertEqual(sum(isinstance(e, g.Arc) for e in r.elements), 3)
        # Continuous: every element starts where the last one ended.
        for a, b in zip(r.elements, r.elements[1:]):
            self.assertAlmostEqual(g.distance(a.end, b.start), 0, places=9)

    def test_oblique_corner(self):
        theta = math.radians(40)
        pts = [(0, 0, 0), (100, 0, 0), (100 + 80 * math.cos(theta), 80 * math.sin(theta), 0)]
        r = g.route(pts, 12)
        t = 12 * math.tan(theta / 2)
        self.assertAlmostEqual(r.length, 180 - 2 * t + 12 * theta, places=9)
        self.assertAlmostEqual(r.turns[0], theta, places=9)

    def test_collinear_point_is_no_corner(self):
        r = g.route([(0, 0, 0), (10, 0, 0), (30, 0, 0)], 5)
        self.assertAlmostEqual(r.length, 30, places=9)
        self.assertFalse(any(isinstance(e, g.Arc) for e in r.elements))

    def test_duplicate_points_dropped(self):
        r = g.route([(0, 0, 0), (0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 10, 0)], 2)
        self.assertEqual(len(r.points), 3)

    def test_zero_radius_is_the_polyline(self):
        pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0)]
        self.assertAlmostEqual(g.route(pts, 0).length, 20, places=9)

    def test_tight_corner_fails_and_names_the_segment(self):
        """BW-WIR-02 acceptance: a corner tighter than the radius fails."""
        pts = [(0, 0, 0), (50, 0, 0), (50, 12, 0), (100, 12, 0)]
        with self.assertRaises(g.RouteError) as caught:
            g.route(pts, 10)  # middle segment 12 mm, needs 10 + 10
        (problem,) = caught.exception.problems
        self.assertEqual((problem.kind, problem.index), ("tight", 1))
        self.assertAlmostEqual(problem.need, 20)
        self.assertAlmostEqual(problem.as_json()["short_by"], 8)
        self.assertIn("segment 1", str(caught.exception))
        g.route(pts, 6)  # fits exactly: 6 + 6 = 12

    def test_every_problem_is_listed(self):
        pts = [(0, 0, 0), (5, 0, 0), (5, 5, 0), (10, 5, 0)]
        with self.assertRaises(g.RouteError) as caught:
            g.route(pts, 10)
        self.assertEqual([p.index for p in caught.exception.problems], [0, 1, 2])

    def test_reversal(self):
        with self.assertRaises(g.RouteError) as caught:
            g.route([(0, 0, 0), (10, 0, 0), (0, 0, 0)], 1)
        self.assertEqual(caught.exception.problems[0].kind, "reversal")

    def test_single_point(self):
        with self.assertRaises(g.RouteError):
            g.route([(1, 1, 1), (1, 1, 1)], 1)

    def test_wire_points_lead_out(self):
        pts = g.wire_points((0, 0, 0), (0, 0, 2), (50, 0, 0), (0, 0, 1), via=[(25, 0, 30)], lead=10)
        self.assertEqual(pts, [(0, 0, 0), (0.0, 0.0, 10.0), (25, 0, 30), (50.0, 0.0, 10.0), (50, 0, 0)])
        self.assertEqual(len(g.wire_points((0, 0, 0), (0, 0, 1), (5, 0, 0), (0, 0, 1))), 2)


class GraphTests(unittest.TestCase):
    NODES = {
        "S": (0, 0, 0),
        "E": (100, 0, 0),
        "near": (50, 10, 0),
        "far": (50, 60, 0),
        "kink": (50, 0.5, 0),
    }

    def test_picks_the_shorter_feasible_path(self):
        """BW-WIR-06 acceptance: three clips, the shorter feasible path wins."""
        edges = [("S", "near"), ("near", "E"), ("S", "far"), ("far", "E"), ("S", "kink"), ("kink", "far")]
        found = g.route_on_graph(self.NODES, edges, "S", "E", 5)
        self.assertEqual(found.path, ["S", "near", "E"])
        self.assertAlmostEqual(found.length, g.routed_length([(0, 0, 0), (50, 10, 0), (100, 0, 0)], 5))
        self.assertEqual(found.explored, 3)
        self.assertFalse(found.truncated)

    def test_shortest_path_that_cannot_bend_is_skipped(self):
        nodes = {"S": (0, 0, 0), "a": (20, 0, 0), "b": (20, 6, 0), "E": (60, 6, 0), "c": (30, 40, 0)}
        edges = [("S", "a"), ("a", "b"), ("b", "E"), ("S", "c"), ("c", "E")]
        found = g.route_on_graph(nodes, edges, "S", "E", 10)  # a-b is 6 mm: too tight
        self.assertEqual(found.path, ["S", "c", "E"])
        self.assertEqual(found.rejected[0][0], ["S", "a", "b", "E"])
        self.assertEqual(g.route_on_graph(nodes, edges, "S", "E", 2).path, ["S", "a", "b", "E"])

    def test_no_route(self):
        with self.assertRaises(g.RouteError):
            g.route_on_graph(self.NODES, [("S", "near")], "S", "E", 5)
        with self.assertRaises(KeyError):
            g.route_on_graph(self.NODES, [("S", "nowhere")], "S", "E", 5)

    def test_lead_out_counts(self):
        nodes = {"S": (0, 0, 0), "E": (100, 0, 0), "up": (50, 0, 40), "down": (50, 0, -40)}
        edges = [("S", "up"), ("up", "E"), ("S", "down"), ("down", "E")]

        def with_leads(path):
            middle = [nodes[n] for n in path[1:-1]]
            return g.wire_points(nodes["S"], (0, 0, 1), nodes["E"], (0, 0, 1), middle, lead=10)

        found = g.route_on_graph(nodes, edges, "S", "E", 3, points_for=with_leads)
        self.assertEqual(found.path, ["S", "up", "E"])  # ports face up; going down doubles back


class BundleTests(unittest.TestCase):
    def test_shared_run(self):
        a = ["P1", "c1", "c2", "c3", "A"]
        b = ["P2", "c1", "c2", "c3", "B"]
        self.assertEqual(g.shared_run([a, b]), ["c1", "c2", "c3"])
        self.assertEqual(g.shared_run([a, b[::-1]]), ["c1", "c2", "c3"])
        self.assertEqual(g.shared_run([a, ["P2", "c1", "x", "c3", "B"]]), [])
        self.assertEqual(g.shared_run([a]), a)

    def test_bundle_diameter(self):
        self.assertEqual(g.bundle_diameter([2.0]), 2.0)
        self.assertAlmostEqual(g.bundle_diameter([2.0, 2.0, 2.0], packing=0.75), 4.0)


class CutListTests(unittest.TestCase):
    def test_cut_length_uses_the_longest_pose(self):
        self.assertEqual(cutlist.cut_length(120.2, 131.4, 10), 142.0)
        self.assertEqual(cutlist.cut_length(120.0, None, 0), 120.0)
        self.assertAlmostEqual(cutlist.cut_length(120.2, None, 0, round_up_to=0), 120.2)

    def test_rows_totals_csv(self):
        rows = [
            cutlist.row("W1", "22 AWG", "red", "ESC.M1", "Motor1.A", 118.4, 110.0, 126.3, 10),
            cutlist.row("W2", "22 AWG", "red", "ESC.M2", "Motor2.A", 90.0, allowance=10),
            cutlist.row("W3", "18 AWG", "black", "Batt.-", "ESC.-", 0, status="segment 1 is too short"),
        ]
        self.assertEqual(rows[0]["cut_mm"], 137.0)
        doc = cutlist.as_json(rows)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["totals"], [{"gauge": "22 AWG", "color": "red", "wires": 2, "total_mm": 237.0}])
        text = cutlist.to_csv(rows)
        self.assertEqual(text.splitlines()[0], ",".join(cutlist.COLUMNS))
        self.assertEqual(len(text.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
