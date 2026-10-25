# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the persistent section views (bruceworx_section): plane arithmetic, passes, the
three-plane limit, and a section saved with its file."""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_section as sec

V = App.Vector


def near(test, a, b, places=6):
    test.assertAlmostEqual((a - b).Length, 0.0, places=places)


class TestPlanes(unittest.TestCase):
    def test_offset_moves_along_the_normal(self):
        n, d = sec.plane_of(sec.placement_for(V(0, 1, 0)), offset=7.5)
        near(self, n, V(0, 1, 0))
        self.assertAlmostEqual(d, 7.5)

    def test_tilt_turns_the_normal_about_the_plane_axes(self):
        n, d = sec.plane_of(App.Placement(), tilt_a=90)  # Z turned about X
        near(self, n, V(0, -1, 0))
        self.assertAlmostEqual(d, 0.0)
        n, _d = sec.plane_of(App.Placement(), tilt_b=90)  # Z turned about Y
        near(self, n, V(1, 0, 0))

    def test_tilt_turns_about_the_placement_origin(self):
        plc = sec.placement_for(V(0, 0, 1), V(0, 0, 10))
        n, d = sec.plane_of(plc, tilt_a=30)
        self.assertAlmostEqual(d, n.dot(V(0, 0, 10)))

    def test_flip_keeps_the_other_side(self):
        n, d = sec.plane_of(sec.placement_for(V(1, 0, 0)), offset=3, flipped=True)
        near(self, n, V(-1, 0, 0))
        self.assertAlmostEqual(d, -3.0)
        n2, d2 = sec.flip((n, d))
        near(self, n2, V(1, 0, 0))
        self.assertAlmostEqual(d2, 3.0)

    def test_the_removed_side_faces_the_camera(self):
        view_dir = V(-0.577, 0.577, -0.577)
        near(self, sec.facing(V(0, 1, 0), view_dir), V(0, 1, 0))
        near(self, sec.facing(V(1, 0, 0), view_dir), V(-1, 0, 0))

    def test_three_points(self):
        near(self, sec.three_point_normal(V(0, 0, 0), V(1, 0, 0), V(0, 1, 0)), V(0, 0, 1))
        with self.assertRaises(ValueError):
            sec.three_point_normal(V(0, 0, 0), V(1, 0, 0), V(2, 0, 0))


class TestPasses(unittest.TestCase):
    def setUp(self):
        self.p1 = (V(0, 1, 0), 0.0)
        self.p2 = (V(-1, 0, 0), 0.0)
        self.p3 = (V(0, 0, 1), 5.0)

    @staticmethod
    def kept(passes, point):
        return any(all(n.dot(point) >= d for n, d in ps) for ps in passes)

    def test_one_plane_is_one_pass(self):
        passes, seams = sec.passes_for([[self.p1]])
        self.assertEqual(len(passes), 1)
        self.assertEqual(seams, [])

    def test_a_quarter_section_removes_one_corner(self):
        passes, seams = sec.passes_for([[self.p1, self.p2]])
        self.assertEqual(len(passes), 2)
        self.assertEqual(len(seams), 1)
        # removed: y < 0 and x > 0; the other three quarters are kept, each by one pass only
        self.assertFalse(self.kept(passes, V(5, -5, 0)))
        for point in (V(5, 5, 0), V(-5, 5, 0), V(-5, -5, 0)):
            self.assertTrue(self.kept(passes, point))
            self.assertEqual(sum(all(n.dot(point) >= d for n, d in ps) for ps in passes), 1)

    def test_sections_together_keep_what_both_keep(self):
        passes, _seams = sec.passes_for([[self.p1, self.p2], [self.p3]])
        self.assertEqual(len(passes), 2)
        self.assertTrue(self.kept(passes, V(-5, 5, 6)))
        self.assertFalse(self.kept(passes, V(-5, 5, 4)))  # below the third plane
        self.assertFalse(self.kept(passes, V(5, -5, 6)))  # in the quarter's corner

    def test_nothing_on(self):
        self.assertEqual(sec.passes_for([]), ([], []))


class TestLimit(unittest.TestCase):
    def test_room_for_it(self):
        self.assertEqual(sec.to_turn_off([("A", 1, 1.0), ("B", 1, 2.0)], 1), [])

    def test_the_oldest_goes(self):
        self.assertEqual(sec.to_turn_off([("A", 1, 5.0), ("B", 1, 2.0), ("C", 1, 9.0)], 1), ["B"])

    def test_a_quarter_may_push_two_off(self):
        self.assertEqual(sec.to_turn_off([("A", 1, 5.0), ("B", 1, 2.0), ("C", 1, 9.0)], 2), ["B", "A"])

    def test_fit_keys_ignore_order(self):
        self.assertEqual(sec.fit_key("D#B", "D#A"), sec.fit_key("D#A", "D#B"))


class TestSaved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-section-")
        self.doc = App.newDocument("SectionTest")

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("SectionTest"):
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_section_survives_save_and_reopen(self):
        box = self.doc.addObject("Part::Box", "Box")
        obj = self.doc.addObject("App::FeaturePython", sec.SECTION_NAME)
        sec.SectionPlane(obj)
        obj.Plane = sec.placement_for(V(0, 1, 0))
        obj.Offset = 4.0
        obj.Quarter = True
        obj.Plane2 = sec.placement_for(V(-1, 0, 0))
        obj.Fits = [sec.fit_key("A#Part", "B#Part")]
        obj.NotCut = "Chosen parts"
        obj.NotCutParts = [box]
        sec.group(self.doc).addObject(obj)
        self.doc.recompute()
        path = os.path.join(self.tmp, "s.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        obj = doc.getObject(sec.SECTION_NAME)
        self.assertTrue(sec.is_section(obj))
        self.assertEqual(sec.sections(doc), [obj])
        self.assertIn(obj, sec.group(doc, create=False).Group)
        planes = sec.section_planes(obj)
        self.assertEqual(len(planes), 2)
        near(self, planes[0][0], V(0, 1, 0))
        self.assertAlmostEqual(planes[0][1], 4.0)
        style = sec.style_of([obj])
        self.assertEqual(style["fill"], "part")
        self.assertTrue(style["clash"] and style["hatch"] and style["planeHatch"])
        self.assertEqual(style["fits"], [("A#Part", "B#Part")])
        self.assertIn(f"{doc.Name}#Box", style["uncut"])

    def test_cots_parts_are_found_through_links(self):
        part = self.doc.addObject("App::Part", "Screw")
        part.addProperty("App::PropertyEnumeration", "ItemType", "Part")
        part.ItemType = ["part", "assembly", "cots", "drawing"]
        part.ItemType = "cots"
        link = self.doc.addObject("App::Link", "Screw1")
        link.LinkedObject = part
        plain = self.doc.addObject("App::Part", "Plate")
        found = sec.cots_links(self.doc)
        self.assertIn(part, found)
        self.assertIn(link, found)
        self.assertNotIn(plain, found)


if __name__ == "__main__":
    unittest.main()
