# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for sketch profile repair (bruceworx_sketchrepair): the endpoint graph, gap
finding and fix plan on synthetic geometry, and a headless Pad that fails on a 0.05 mm
gap and pads after the fix."""

import unittest

import bruceworx_sketchrepair as sr


def rectangle(gap=0.0, corner=3, w=20.0, h=10.0):
    """Four edges of a rectangle; the end of edge ``corner`` is moved up by ``gap``."""
    pts = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    edges = []
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        if i == corner:
            b = (b[0], b[1] + gap)
        edges.append((i, a, b))
    return edges


def chain_links(n=4, skip=()):
    """Coincidences end of i -> start of i+1 (closing the loop), except at ``skip``."""
    return [((i, 2), ((i + 1) % n, 1)) for i in range(n) if i not in skip]


class TestAnalyse(unittest.TestCase):
    def test_closed_and_constrained(self):
        report = sr.analyse(rectangle(), chain_links())
        self.assertEqual(report.open_ends, [])
        self.assertEqual(report.fixes, [])
        self.assertFalse(sr.has_problem(report))

    def test_touching_without_constraint_is_a_missing_coincidence(self):
        report = sr.analyse(rectangle(), chain_links(skip=(3,)))
        self.assertFalse(sr.has_problem(report))  # the wire closes; nothing is open
        self.assertEqual(len(report.fixes), 1)
        fix = report.fixes[0]
        self.assertEqual(fix.kind, "coincident")
        self.assertEqual({(fix.a.geo, fix.a.pos), (fix.b.geo, fix.b.pos)}, {(3, 2), (0, 1)})

    def test_small_gap_gets_a_coincidence(self):
        report = sr.analyse(rectangle(gap=0.05), chain_links(skip=(3,)), snap=0.1, max_gap=1.0)
        self.assertTrue(sr.has_problem(report))
        self.assertEqual(len(report.open_ends), 2)
        self.assertEqual([f.kind for f in report.fixes], ["coincident"])
        self.assertAlmostEqual(report.fixes[0].distance, 0.05)
        self.assertEqual(report.unresolved, [])

    def test_wider_gap_gets_a_line(self):
        report = sr.analyse(rectangle(gap=0.5), chain_links(skip=(3,)), snap=0.1, max_gap=1.0)
        self.assertEqual([f.kind for f in report.fixes], ["line"])
        self.assertEqual(report.unresolved, [])

    def test_too_wide_is_reported_only(self):
        report = sr.analyse(rectangle(gap=3.0), chain_links(skip=(3,)), snap=0.1, max_gap=1.0)
        self.assertEqual(report.fixes, [])
        self.assertEqual(len(report.unresolved), 2)

    def test_gap_at_a_constrained_join_does_not_branch(self):
        # Corner 1 is joined; a stray end 0.05 away from it must not be tied to it.
        edges = rectangle(gap=0.05, corner=3) + [(4, (20.0, 0.05), (30.0, 0.05))]
        report = sr.analyse(edges, chain_links(skip=(3,)), snap=0.1, max_gap=1.0)
        joined = {(f.a.geo, f.a.pos) for f in report.fixes} | {(f.b.geo, f.b.pos) for f in report.fixes}
        self.assertNotIn((0, 2), joined)
        self.assertNotIn((1, 1), joined)
        self.assertIn((4, 1), {(e.geo, e.pos) for e in report.open_ends})

    def test_nearest_pairs_first(self):
        # Two gaps: 0.02 and 0.08; three open ends near each other take the nearest pair.
        edges = [(0, (0.0, 0.0), (10.0, 0.0)), (1, (10.02, 0.0), (20.0, 0.0)), (2, (10.0, 0.08), (10.0, 5.0))]
        report = sr.analyse(edges, (), snap=0.1, max_gap=1.0)
        first = report.fixes[0]
        self.assertEqual({first.a.geo, first.b.geo}, {0, 1})
        self.assertAlmostEqual(first.distance, 0.02)

    def test_open_arc_gets_a_line_not_a_self_coincidence(self):
        edges = [(0, (1.0, 0.0), (1.0, -0.05))]  # a nearly full arc, ends 0.05 apart
        report = sr.analyse(edges, (), snap=0.1, max_gap=1.0)
        self.assertEqual([f.kind for f in report.fixes], ["line"])

    def test_branch_is_reported(self):
        edges = [(0, (0.0, 0.0), (1.0, 0.0)), (1, (1.0, 0.0), (2.0, 0.0)), (2, (1.0, 0.0), (1.0, 1.0))]
        report = sr.analyse(edges, [((0, 2), (1, 1)), ((0, 2), (2, 1))])
        self.assertEqual(report.branches, [(1.0, 0.0)])

    def test_grid_search_matches_brute_force(self):
        import random

        rnd = random.Random(4)
        ends = sr.ends_of([(i, (rnd.uniform(0, 5), rnd.uniform(0, 5)), (rnd.uniform(0, 5), rnd.uniform(0, 5)))
                           for i in range(40)])
        fast = {(i, j) for _d, i, j in sr.near_pairs(ends, 0.7)}
        slow = {(i, j) for i in range(len(ends)) for j in range(i + 1, len(ends))
                if sr._dist(ends[i].xy, ends[j].xy) <= 0.7}
        self.assertEqual(fast, slow)


class TestWords(unittest.TestCase):
    def test_mm(self):
        self.assertEqual(sr.mm(0.05), "0.05 mm")
        self.assertEqual(sr.mm(1), "1 mm")
        self.assertEqual(sr.mm(0.0000001), "0 mm")

    def test_describe(self):
        report = sr.analyse(rectangle(gap=0.05), chain_links(skip=(3,)))
        head, lines, note = sr.describe(report, {3: "Line 4", 0: "Line 1"})
        self.assertEqual(head, "2 open ends, gap up to 0.05 mm")
        self.assertEqual(lines, ["Line 1 start ↔ Line 4 end: 0.05 mm, coincident"])
        self.assertEqual(note, "")

    def test_signature_follows_geometry(self):
        a = sr.signature(rectangle(gap=0.05), chain_links(skip=(3,)))
        self.assertEqual(a, sr.signature(rectangle(gap=0.05), chain_links(skip=(3,))))
        self.assertNotEqual(a, sr.signature(rectangle(gap=0.06), chain_links(skip=(3,))))


class TestPadSmoke(unittest.TestCase):
    """Headless: a sketch with a 0.05 mm gap fails to pad; after close_gaps it pads, in one undo step."""

    def setUp(self):
        import FreeCAD

        self.doc = FreeCAD.newDocument("SketchRepairSmoke")
        self.doc.UndoMode = 1

    def tearDown(self):
        import FreeCAD

        FreeCAD.closeDocument(self.doc.Name)

    def _pad(self, gap):
        import FreeCAD
        import Part
        import Sketcher

        body = self.doc.addObject("PartDesign::Body", "Body")
        sketch = body.newObject("Sketcher::SketchObject", "Sketch")
        sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sketch.MapMode = "FlatFace"
        for i, a, b in rectangle(gap=gap):
            sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(*a, 0), FreeCAD.Vector(*b, 0)))
        for (g1, p1), (g2, p2) in chain_links(skip=(3,)):
            sketch.addConstraint(Sketcher.Constraint("Coincident", g1, p1, g2, p2))
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sketch
        pad.Length = 5
        self.doc.recompute()
        return sketch, pad

    def test_gap_closes_and_pads(self):
        sketch, pad = self._pad(0.05)
        self.assertTrue(sr.in_error(pad))
        found = sr.check_feature(pad, 0.1, 1.0)
        self.assertIsNotNone(found)
        self.assertEqual([f.kind for f in found[1].fixes], ["coincident"])
        undo_before = self.doc.UndoCount
        ok, message = sr.close_gaps(pad)
        self.assertTrue(ok, message)
        self.assertFalse(sr.in_error(pad))
        self.assertAlmostEqual(pad.Shape.Volume, 20 * 10 * 5, delta=0.5)
        self.assertIsNone(sr.check_feature(pad, 0.1, 1.0))
        self.assertEqual(self.doc.UndoCount, undo_before + 1)
        self.assertEqual(self.doc.UndoNames[0], sr.TRANSACTION)
        self.doc.undo()
        self.doc.recompute()
        self.assertTrue(sr.in_error(pad))

    def test_valid_pad_is_left_alone(self):
        _sketch, pad = self._pad(0.0)
        self.assertFalse(sr.in_error(pad))
        self.assertIsNone(sr.check_feature(pad, 0.1, 1.0))

    def test_line_for_a_wider_gap(self):
        sketch, pad = self._pad(0.5)
        ok, message = sr.close_gaps(pad)
        self.assertTrue(ok, message)
        self.assertEqual(len(sketch.Geometry), 5)
        self.assertFalse(sr.in_error(pad))


if __name__ == "__main__":
    unittest.main()
