# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for sketch profile repair (bruceworx_sketchrepair): the endpoint graph, gap
finding and fix plan on synthetic geometry, edges drawn twice and overlapping lines, a
headless Pad that fails on a 0.05 mm gap and pads after the fix, a loft whose section is
open, and (with the GUI: FreeCAD -t TestBruceWorxSketchRepair) the card and Check Profile."""

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


class TestCopiesAndOverlaps(unittest.TestCase):
    def test_line_drawn_twice_either_way_round(self):
        a = sr.Curve(0, "LineSegment", ((0, 0), (20, 0), (10, 0)))
        b = sr.Curve(4, "LineSegment", ((20, 0), (0, 0), (10, 0)))
        c = sr.Curve(1, "LineSegment", ((20, 0), (20, 10), (20, 5)))
        self.assertEqual(sr.duplicates([a, c, b]), [(0, 4)])
        self.assertEqual(sr.duplicates([a, c, b], weight={4: 3, 0: 1}), [(4, 0)])  # the constrained one stays

    def test_arcs_with_the_same_ends_but_another_middle_are_not_copies(self):
        up = sr.Curve(0, "ArcOfCircle", ((0, 0), (10, 0), (5, 5)))
        down = sr.Curve(1, "ArcOfCircle", ((0, 0), (10, 0), (5, -5)))
        self.assertEqual(sr.duplicates([up, down]), [])

    def test_circles(self):
        a = sr.Curve(2, "Circle", ((0, 0), (5, 0)))
        b = sr.Curve(3, "Circle", ((0, 0), (5, 0)))
        c = sr.Curve(4, "Circle", ((0, 0), (6, 0)))
        self.assertEqual(sr.duplicates([a, b, c]), [(2, 3)])

    def test_overlap(self):
        found = sr.overlaps([(0, (0, 0), (20, 0)), (4, (5, 0), (30, 0))])
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].a, found[0].b), (0, 4))
        self.assertAlmostEqual(found[0].length, 15)
        self.assertAlmostEqual(found[0].xy[0], 12.5)

    def test_not_overlaps(self):
        self.assertEqual(sr.overlaps([(0, (0, 0), (20, 0)), (1, (20, 0), (30, 0))]), [])  # end to end
        self.assertEqual(sr.overlaps([(0, (0, 0), (20, 0)), (1, (5, 0.01), (15, 0.01))]), [])  # parallel
        self.assertEqual(sr.overlaps([(0, (0, 0), (20, 0)), (1, (20, 0), (0, 0))]), [])  # a copy
        self.assertEqual(sr.overlaps([(0, (0, 0), (20, 0)), (1, (10, -5), (10, 5))]), [])  # crossing

    def test_copies_alone_are_a_problem(self):
        report = sr.Report([], [], [], [], duplicates=((0, 4),))
        self.assertTrue(sr.has_problem(report))
        self.assertTrue(sr.fixable(report))
        head, lines, _note = sr.describe(report, {0: "Line 1", 4: "Line 5"})
        self.assertEqual(head, "Edges on top of each other")
        self.assertEqual(lines, ["Line 5 is a copy of Line 1: delete it"])
        only_overlap = sr.Report([], [], [], [], overlaps=(sr.Overlap(0, 4, (12.5, 0), 15.0),))
        self.assertTrue(sr.has_problem(only_overlap))
        self.assertFalse(sr.fixable(only_overlap))  # which part to keep is the user's call
        self.assertIn("Line 1 and Line 5 overlap by 15 mm", sr.describe(only_overlap, {0: "Line 1", 4: "Line 5"})[2])


def _add_rectangle(sketch, gap=0.0, w=20.0, h=10.0):
    import FreeCAD
    import Part
    import Sketcher

    for _i, a, b in rectangle(gap=gap, w=w, h=h):
        sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(*a, 0), FreeCAD.Vector(*b, 0)))
    for (g1, p1), (g2, p2) in chain_links(skip=(3,) if gap else ()):
        sketch.addConstraint(Sketcher.Constraint("Coincident", g1, p1, g2, p2))


class _Doc(unittest.TestCase):
    def setUp(self):
        import FreeCAD

        self.doc = FreeCAD.newDocument("SketchRepairMore")
        self.doc.UndoMode = 1
        self.body = self.doc.addObject("PartDesign::Body", "Body")

    def tearDown(self):
        import FreeCAD

        FreeCAD.closeDocument(self.doc.Name)

    def sketch(self, z=0.0):
        import FreeCAD

        sketch = self.body.newObject("Sketcher::SketchObject", "Sketch")
        sketch.AttachmentSupport = [(self.body.Origin.OriginFeatures[3], "")]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, z), FreeCAD.Rotation())
        return sketch

    def pad(self, sketch):
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sketch
        pad.Length = 5
        self.doc.recompute()
        return pad


class TestMoreFeatures(_Doc):
    def test_line_drawn_twice_is_deleted_and_pads(self):
        import FreeCAD
        import Part

        sketch = self.sketch()
        _add_rectangle(sketch)
        sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20, 0, 0), FreeCAD.Vector(0, 0, 0)))
        pad = self.pad(sketch)
        self.assertTrue(sr.in_error(pad))  # "Wire is not closed"
        found = sr.check_feature(pad, 0.1, 1.0)
        self.assertIsNotNone(found)
        self.assertEqual(found[1].duplicates, ((0, 4),))  # line 1 has the coincidences, so it stays
        before = self.doc.UndoCount
        ok, message = sr.close_gaps(pad)
        self.assertTrue(ok, message)
        self.assertEqual(message, "Deleted 1 extra edge")
        self.assertEqual(len(sketch.Geometry), 4)
        self.assertFalse(sr.in_error(pad))
        self.assertAlmostEqual(pad.Shape.Volume, 20 * 10 * 5, delta=0.5)
        self.assertEqual(self.doc.UndoCount, before + 1)

    def test_overlap_is_reported_and_nothing_changes(self):
        import FreeCAD
        import Part

        sketch = self.sketch()
        _add_rectangle(sketch)
        sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5, 0, 0), FreeCAD.Vector(30, 0, 0)))
        pad = self.pad(sketch)
        found = sr.check_feature(pad, 0.1, 1.0)
        self.assertEqual([(o.a, o.b) for o in found[1].overlaps], [(0, 4)])
        self.assertAlmostEqual(found[1].overlaps[0].length, 15)
        ok, message = sr.close_gaps(pad)
        self.assertFalse(ok)
        self.assertEqual(len(sketch.Geometry), 5)
        self.assertEqual(self.doc.UndoCount, 0)

    def test_loft_with_an_open_section(self):
        bottom, top = self.sketch(), self.sketch(20)
        _add_rectangle(bottom)
        _add_rectangle(top, gap=0.05)
        loft = self.body.newObject("PartDesign::AdditiveLoft", "Loft")
        loft.Profile = bottom
        loft.Sections = [top]
        self.doc.recompute()
        self.assertTrue(sr.in_error(loft))
        found = sr.check_feature(loft, 0.1, 1.0)
        self.assertIs(found[0], top)
        ok, message = sr.close_gaps(loft)
        self.assertTrue(ok, message)
        self.assertFalse(sr.in_error(loft))
        self.assertGreater(loft.Shape.Volume, 0)

    def test_zero_length_edge_goes_with_the_gap(self):
        import FreeCAD
        import Part
        import Sketcher

        sketch = self.sketch()
        _add_rectangle(sketch, gap=0.05)
        stub = sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20, 10, 0), FreeCAD.Vector(21, 10, 0)))
        sketch.addConstraint(Sketcher.Constraint("Coincident", stub, 1, 1, 2))
        sketch.addConstraint(Sketcher.Constraint("Distance", stub, 1, stub, 2, 0.0))
        sketch.solve()
        self.assertEqual(sketch.detectDegeneratedGeometries(sr._confusion()), 1)
        report, _names, _sig = sr.inspect(sketch, 0.1, 1.0)
        self.assertEqual(report.degenerate, (stub,))
        self.assertEqual([f.kind for f in report.fixes], ["coincident"])
        ok, message = sr.close_sketch(sketch)
        self.assertTrue(ok, message)
        self.assertEqual(sketch.detectDegeneratedGeometries(sr._confusion()), 0)
        self.assertEqual(len(sketch.Geometry), 4)
        self.assertIsNone(sr.check_sketch(sketch, 0.1, 1.0))
        self.assertFalse(sr.in_error(self.pad(sketch)))

    def test_closed_sketch_has_nothing_to_say(self):
        sketch = self.sketch()
        _add_rectangle(sketch)
        self.doc.recompute()
        self.assertIsNone(sr.check_sketch(sketch, 0.1, 1.0))
        self.assertEqual(sr.close_sketch(sketch), (True, "Nothing to close"))


def _gui():
    import FreeCAD

    return FreeCAD.GuiUp


def pump(n=10):
    import time

    import FreeCADGui

    for _ in range(n):
        FreeCADGui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(_gui(), "needs the GUI")
class TestCardGui(_Doc):
    def setUp(self):
        import FreeCAD

        prefs = FreeCAD.ParamGet(sr.PREFS_PATH)
        # auto-activate drops the selection 400 ms after a document is shown: not under test here
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        on = prefs.GetBool(sr.PREF_NAME, True)
        prefs.SetBool(sr.PREF_NAME, True)
        self.addCleanup(prefs.SetBool, sr.PREF_NAME, on)
        sr.install()
        sr._add_command()
        super().setUp()
        pump()

    def tearDown(self):
        card = sr.card()
        if card is not None:
            card.hide_card()
        super().tearDown()
        pump()

    def test_command_is_registered(self):
        import FreeCADGui

        self.assertIn(sr.COMMAND, FreeCADGui.listCommands())

    def test_failing_pad_shows_the_card_and_one_click_pads(self):
        sketch = self.sketch()
        _add_rectangle(sketch, gap=0.05)
        pad = self.pad(sketch)
        pump()
        card = sr.card()
        self.assertIsNotNone(card)
        self.assertTrue(card.isVisible())
        self.assertIn("is open", card.title.text())
        self.assertIn("gap up to 0.05 mm", card.body.text())
        self.assertIsNotNone(card.marker_root)  # the red dots
        self.assertTrue(sr.in_error(pad))  # nothing changed before the click
        card.fix_button.click()
        pump()
        self.assertFalse(sr.in_error(pad))
        self.assertAlmostEqual(pad.Shape.Volume, 20 * 10 * 5, delta=0.5)
        self.assertFalse(card.isVisible())
        self.assertIsNone(card.marker_root)

    def test_dismissed_stays_quiet_until_the_sketch_changes(self):
        sketch = self.sketch()
        _add_rectangle(sketch, gap=0.05)
        self.pad(sketch)
        pump()
        sr.card().dismiss()
        self.doc.getObject("Pad").touch()
        self.doc.recompute()
        pump()
        self.assertFalse(sr.card().isVisible())

    def test_check_profile_on_a_selected_sketch(self):
        import FreeCADGui

        sketch = self.sketch()
        _add_rectangle(sketch, gap=0.05)
        self.doc.recompute()
        pump()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, sketch.Name)
        self.assertEqual(sr.chosen_sketches(), [sketch])
        FreeCADGui.runCommand(sr.COMMAND)
        pump()
        card = sr.card()
        self.assertTrue(card is not None and card.isVisible())
        self.assertEqual(card.key, (self.doc.Name, sketch.Name))
        self.assertIn("will not close", card.title.text())
        card.fix_button.click()
        pump()
        self.assertIsNone(sr.check_sketch(sketch, 0.1, 1.0))
        self.assertEqual(self.doc.UndoNames[0], sr.TRANSACTION)
        self.assertIsNone(sr.check_profile([sketch]))  # closed now: no card


if __name__ == "__main__":
    unittest.main()
