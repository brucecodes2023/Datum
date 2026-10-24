# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for pick to feature and error chips (bruceworx_picktrace): the pure helpers, and a
headless body (pad, pocket, fillet, pattern) whose every face traces to the feature that made it."""

import unittest

import bruceworx_picktrace as pt


class TestMaker(unittest.TestCase):
    def test_oldest_solid_feature(self):
        types = ["PartDesign::Body", "PartDesign::Fillet", "PartDesign::Pocket", "PartDesign::Pad"]
        self.assertEqual(pt.maker_index(types), 3)

    def test_sketches_and_bodies_do_not_make(self):
        self.assertEqual(pt.maker_index(["PartDesign::Body", "PartDesign::Pad", "Sketcher::SketchObject"]), 1)
        self.assertIsNone(pt.maker_index(["PartDesign::Body"]))
        self.assertIsNone(pt.maker_index([]))

    def test_pattern_candidates(self):
        chain = [
            ("LinearPattern", "PartDesign::LinearPattern", ["Pocket"]),
            ("Fillet", "PartDesign::Fillet", []),
            ("Pocket", "PartDesign::Pocket", []),
        ]
        self.assertEqual(pt.pattern_candidates(chain, "Pocket"), ["LinearPattern"])
        self.assertEqual(pt.pattern_candidates(chain, "Fillet"), [])


class TestWords(unittest.TestCase):
    def test_element_kind(self):
        self.assertEqual(pt.element_kind("Body.Pad.Face3"), "Face3")
        self.assertEqual(pt.element_kind("Edge12"), "Edge12")
        self.assertEqual(pt.element_kind("Body.;#5:1;:H1,F.Face3"), "Face3")
        self.assertEqual(pt.element_kind("Body.Pad."), "")
        self.assertEqual(pt.element_kind("Body.Sketch"), "")
        self.assertEqual(pt.element_kind("Face"), "")

    def test_is_error(self):
        self.assertTrue(pt.is_error(["Touched", "Invalid"]))
        self.assertTrue(pt.is_error(["Error"]))
        self.assertTrue(pt.is_error([], valid=False))
        self.assertFalse(pt.is_error(["Touched"]))
        self.assertFalse(pt.is_error(["Up-to-date"]))

    def test_error_text(self):
        self.assertEqual(pt.error_text("Wire is not closed."), "Wire is not closed.")
        self.assertEqual(pt.error_text("Valid"), "recompute failed")
        self.assertEqual(pt.error_text(""), "recompute failed")


class TestLines(unittest.TestCase):
    def test_elbow(self):
        self.assertEqual(pt.elbow((40, 10), (40, 50), 30), [(40, 10), (30, 10), (30, 50), (40, 50)])

    def test_lanes(self):
        self.assertEqual(pt.lanes(40, 3), [34, 30, 26])
        self.assertEqual(pt.lanes(8, 2), [2, 2])


class TestTraceBody(unittest.TestCase):
    """Every face of a pad + pocket + fillet + pattern traces to the feature that made it."""

    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part
        import Sketcher

        V = FreeCAD.Vector
        cls.doc = doc = FreeCAD.newDocument("PickTraceSmoke")
        body = doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        pts = [V(0, 0, 0), V(30, 0, 0), V(30, 10, 0), V(0, 10, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 5
        doc.recompute()
        top = next(f"Face{i + 1}" for i, f in enumerate(pad.Shape.Faces)
                   if abs(f.CenterOfMass.z - 5) < 1e-6 and f.Surface.__class__.__name__ == "Plane")
        sk2 = body.newObject("Sketcher::SketchObject", "Sketch001")
        sk2.AttachmentSupport = [(pad, top)]
        sk2.MapMode = "FlatFace"
        sk2.addGeometry(Part.Circle(V(5, 5, 0), V(0, 0, 1), 2))
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sk2
        pocket.Length = 3
        doc.recompute()
        edge = next(f"Edge{i + 1}" for i, e in enumerate(pocket.Shape.Edges)
                    if abs(e.CenterOfMass.y) < 1e-6 and abs(e.CenterOfMass.z - 5) < 1e-6)
        fillet = body.newObject("PartDesign::Fillet", "Fillet")
        fillet.Base = (pocket, [edge])
        fillet.Radius = 1
        doc.recompute()
        pattern = body.newObject("PartDesign::LinearPattern", "LinearPattern")
        pattern.Originals = [pocket]
        pattern.Direction = (sk, ["H_Axis"])
        pattern.Length = 15
        pattern.Occurrences = 2
        body.Tip = pattern
        doc.recompute()
        cls.body, cls.pocket = body, pocket

    @classmethod
    def tearDownClass(cls):
        import FreeCAD

        FreeCAD.closeDocument(cls.doc.Name)

    def test_every_face_has_a_maker(self):
        shape = self.body.Shape
        self.assertTrue(shape.isValid())
        for i, face in enumerate(shape.Faces):
            maker = pt.trace(self.body, f"Face{i + 1}")
            self.assertIsNotNone(maker, f"Face{i + 1}")
            kind = face.Surface.__class__.__name__
            radius = getattr(face.Surface, "Radius", None)
            if kind == "Cylinder" and abs(radius - 2) < 1e-6:
                # The pocket's wall, or the pattern's copy of it 15 mm along x.
                expected = "Pocket" if abs(face.Surface.Center.x - 5) < 1e-6 else "LinearPattern"
                self.assertEqual(maker.Name, expected, f"Face{i + 1}")
            elif kind == "Cylinder" and abs(radius - 1) < 1e-6:
                self.assertEqual(maker.Name, "Fillet", f"Face{i + 1}")
            elif kind == "Plane" and abs(face.Area - 3.14159265 * 4) < 1e-4:
                self.assertIn(maker.Name, ("Pocket", "LinearPattern"), f"Face{i + 1}")  # hole floors
            elif kind == "Plane" and abs(face.CenterOfMass.z) < 1e-6:
                self.assertEqual(maker.Name, "Pad", f"Face{i + 1}")  # bottom

    def test_edges_trace_too(self):
        for i in range(len(self.body.Shape.Edges)):
            self.assertIsNotNone(pt.trace(self.body, f"Edge{i + 1}"), f"Edge{i + 1}")

    def test_dependencies_skip_the_previous_feature(self):
        uses, used_by = pt.dependencies(self.pocket)
        names = {o.Name for o in uses}
        self.assertIn("Sketch001", names)
        self.assertNotIn("Pad", names)  # BaseFeature: the order shows it
        self.assertIn("LinearPattern", {o.Name for o in used_by})
        self.assertEqual(pt.dependencies(self.body), ([], []))

    def test_error_chip(self):
        meta, marks = pt.error_chip(self.pocket, "", [])
        self.assertEqual(marks, [])


if __name__ == "__main__":
    unittest.main()
