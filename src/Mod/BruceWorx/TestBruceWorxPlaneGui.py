# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of Construct > Plane (bruceworx_plane): an offset plane from a selected plane or flat face.

The geometry tests run headless; the panel needs the running app: FreeCAD -t TestBruceWorxPlaneGui"""

import time
import unittest

import FreeCAD

import bruceworx_plane as bp


def pump(n=6):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("PlaneTest")
        self.doc.UndoMode = 1
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        self.box = self.body.newObject("PartDesign::AdditiveBox", "Box")  # 10 x 10 x 10 at the origin
        self.doc.recompute()
        self.origin = {o.Role: o for o in self.body.Origin.OriginFeatures}

    def tearDown(self):
        if FreeCAD.GuiUp:
            import FreeCADGui as Gui

            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        FreeCAD.closeDocument(self.doc.Name)

    def top_face(self):
        return next(f"Face{i}" for i, f in enumerate(self.box.Shape.Faces, 1)
                    if abs(f.CenterOfMass.z - 10) < 1e-6 and abs(f.normalAt(0, 0).z - 1) < 1e-6)


class TestOffsetGeometry(Base):
    def test_normal_offset_from_an_origin_plane(self):
        plane = bp.make(self.body, (self.origin["YZ_Plane"], ""), 25.0)
        self.assertAlmostEqual(plane.Placement.Base.x, 25, places=6)  # the normal of YZ is X
        self.assertAlmostEqual(plane.AttachmentOffset.Base.z, 25, places=6)
        self.assertEqual(plane.MapMode, "FlatFace")

    def test_axis_offset_moves_along_that_axis_whatever_the_plane(self):
        plane = bp.make(self.body, (self.origin["YZ_Plane"], ""), 12.0, "z")
        base = plane.Placement.Base
        self.assertAlmostEqual(base.z, 12, places=6)
        self.assertAlmostEqual(base.x, 0, places=6)
        bp.set_offset(plane, -7.0, "y")  # set again: from the attachment, not on top of the last one
        base = plane.Placement.Base
        self.assertAlmostEqual(base.y, -7, places=6)
        self.assertAlmostEqual(base.z, 0, places=6)

    def test_a_plane_from_a_plane_and_from_a_face(self):
        first = bp.make(self.body, (self.origin["XY_Plane"], ""), 5.0)
        second = bp.make(self.body, (first, ""), 5.0)
        self.assertAlmostEqual(second.Placement.Base.z, 10, places=6)
        on_top = bp.make(self.body, (self.box, self.top_face()), 3.0)
        self.assertAlmostEqual(on_top.Placement.Base.z, 13, places=6)  # outward from the face

    def test_flat_reference(self):
        self.assertEqual(bp.flat_reference(self.origin["XY_Plane"]), (self.origin["XY_Plane"], ""))
        self.assertEqual(bp.flat_reference(self.box, self.top_face()), (self.box, self.top_face()))
        self.assertIsNone(bp.flat_reference(self.box, "Edge1"))
        self.assertIsNone(bp.flat_reference(self.box, ""))
        cyl = self.body.newObject("PartDesign::AdditiveCylinder", "Cyl")
        self.doc.recompute()
        round_face = next(f"Face{i}" for i, f in enumerate(cyl.Shape.Faces, 1)
                          if type(f.Surface).__name__ == "Cylinder")
        self.assertIsNone(bp.flat_reference(cyl, round_face))

    def test_bad_direction(self):
        with self.assertRaises(ValueError):
            bp.offset_vector(FreeCAD.Rotation(), "up", 1.0)


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestOffsetPanel(Base):
    def setUp(self):
        import FreeCADGui as Gui

        super().setUp()
        self.Gui = Gui
        self.prefs = FreeCAD.ParamGet(bp.PREFS_PATH)
        self.old = self.prefs.GetFloat(bp.DISTANCE_PREF, bp.DEFAULT_DISTANCE)
        self.prefs.SetFloat(bp.DISTANCE_PREF, 10.0)
        Gui.Selection.clearSelection()
        pump()

    def tearDown(self):
        self.prefs.SetFloat(bp.DISTANCE_PREF, self.old)
        self.prefs.RemBool(bp.PREF)
        super().tearDown()
        pump()

    def planes(self):
        return [o for o in self.doc.Objects if o.isDerivedFrom("PartDesign::Plane")]

    def test_selected_plane_gives_a_normal_offset_at_once_and_follows_the_panel(self):
        self.Gui.Selection.addSelection(self.origin["XY_Plane"])
        panel = bp.start()
        pump()
        self.assertIsNotNone(panel)
        plane, = self.planes()
        self.assertAlmostEqual(plane.Placement.Base.z, 10, places=6)  # the last distance, along the normal
        panel.distance.setValue(25)
        self.assertAlmostEqual(plane.Placement.Base.z, 25, places=6)
        panel.flip.setChecked(True)
        self.assertAlmostEqual(plane.Placement.Base.z, -25, places=6)
        panel.flip.setChecked(False)
        panel.direction.setCurrentIndex(panel.direction.findData("x"))
        self.assertAlmostEqual(plane.Placement.Base.x, 25, places=6)
        self.assertAlmostEqual(plane.Placement.Base.z, 0, places=6)
        panel.accept()
        pump()
        self.assertEqual(self.doc.UndoNames[0], "Offset plane")
        self.assertAlmostEqual(self.prefs.GetFloat(bp.DISTANCE_PREF), 25)
        self.assertEqual([s.Object.Name for s in self.Gui.Selection.getSelectionEx()], [plane.Name])
        self.doc.undo()
        self.assertEqual(self.planes(), [])

    def test_selected_face(self):
        self.Gui.Selection.addSelection(self.doc.Name, self.body.Name, f"{self.box.Name}.{self.top_face()}")
        panel = bp.start()
        pump()
        self.assertIsNotNone(panel)
        plane, = self.planes()
        self.assertAlmostEqual(plane.Placement.Base.z, 20, places=6)
        self.assertIn(plane, self.body.Group)
        panel.accept()

    def test_cancel_leaves_nothing(self):
        undo = list(self.doc.UndoNames)
        self.Gui.Selection.addSelection(self.origin["XZ_Plane"])
        panel = bp.start()
        pump()
        panel.reject()
        pump()
        self.assertEqual(self.planes(), [])
        self.assertEqual(list(self.doc.UndoNames), undo)

    def test_off_switch_and_nothing_flat_selected_use_the_stock_tool(self):
        self.Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", self.body)
        self.prefs.SetBool(bp.PREF, False)
        self.Gui.Selection.addSelection(self.origin["XY_Plane"])
        self.assertIsNone(bp.start())
        pump()
        self.assertTrue(self.Gui.Control.activeDialog())  # FreeCAD's attachment panel
        self.Gui.Control.closeDialog()
        self.Gui.ActiveDocument.resetEdit()
