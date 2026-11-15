# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of direct edit on imported solids (bruceworx_directedit, roadmap MOD-04).

The solid goes through a STEP file first, so it is as plain as an import. Headless:
FreeCADCmd -t TestBruceWorxDirectEdit. The panel tests need the app: FreeCAD -t TestBruceWorxDirectEdit"""

import math
import os
import tempfile
import time
import unittest

import FreeCAD as App
import Part

import bruceworx_directedit as de

L, W, H = 60.0, 40.0, 10.0
THROUGH = (15.0, 20.0, 5.0)  # x, y, diameter
BLIND = (40.0, 20.0, 4.0, 6.0)  # x, y, diameter, depth (flat floor)
DRILLED = (50.0, 10.0, 3.0, 5.0)  # x, y, diameter, depth to the start of a 118 degree-style point
FILLET = 3.0


def fixture():
    """A plate with a through hole, a flat-floored blind hole, a drilled (pointed) blind hole and one
    rounded vertical edge, written to STEP and read back."""
    plate = Part.makeBox(L, W, H)
    edge = next(e for e in plate.Edges if abs(e.Length - H) < 1e-9 and e.Vertexes[0].Point.x > L - 1e-6
                and e.Vertexes[0].Point.y > W - 1e-6)
    plate = plate.makeFillet(FILLET, [edge])
    plate = plate.cut(Part.makeCylinder(THROUGH[2] / 2, H, App.Vector(THROUGH[0], THROUGH[1], 0)))
    plate = plate.cut(Part.makeCylinder(BLIND[2] / 2, BLIND[3], App.Vector(BLIND[0], BLIND[1], H - BLIND[3])))
    r, depth = DRILLED[2] / 2, DRILLED[3]
    drill = Part.makeCylinder(r, depth, App.Vector(DRILLED[0], DRILLED[1], H - depth))
    drill = drill.fuse(Part.makeCone(0, r, r, App.Vector(DRILLED[0], DRILLED[1], H - depth - r)))
    plate = plate.cut(drill)
    path = os.path.join(tempfile.mkdtemp(), "plate.step")
    plate.exportStep(path)
    return Part.read(path)


def pump(n=6):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.solid = fixture()

    def setUp(self):
        self.doc = App.newDocument("DirectEdit")
        self.doc.UndoMode = 1
        self.part = self.doc.addObject("Part::Feature", "Plate")
        self.part.Shape = self.solid
        self.doc.recompute()
        self.shape = de.local_shape(self.part)
        self.volume = self.shape.Volume

    def tearDown(self):
        if App.GuiUp:
            import FreeCADGui as Gui

            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        App.closeDocument(self.doc.Name)

    def face(self, test, shape=None):
        return next(f"Face{i}" for i, f in enumerate((shape or self.shape).Faces, 1) if test(f))

    def top(self, shape=None):
        return self.face(lambda f: type(f.Surface).__name__ == "Plane" and abs(f.CenterOfMass.z - H) < 1e-6
                         and f.Area > 100, shape)

    def hole_at(self, x, y, shape=None):
        shape = shape or self.shape
        return next(h for h in de.holes(shape) if abs(h.centre.x - x) < 1e-6 and abs(h.centre.y - y) < 1e-6)


class TestRecognition(Base):
    def test_the_three_holes_and_not_the_fillet(self):
        found = de.holes(self.shape)
        self.assertEqual(len(found), 3)
        through = self.hole_at(THROUGH[0], THROUGH[1])
        self.assertTrue(through.through)
        self.assertAlmostEqual(through.diameter, THROUGH[2], places=6)
        self.assertAlmostEqual(through.depth, H, places=6)
        blind = self.hole_at(BLIND[0], BLIND[1])
        self.assertFalse(blind.through)
        self.assertAlmostEqual(blind.depth, BLIND[3], places=6)
        self.assertGreater(len(blind.faces), 1)  # the wall and its floor
        drilled = next(h for h in found if abs(h.diameter - DRILLED[2]) < 1e-6)
        self.assertFalse(drilled.through)
        self.assertAlmostEqual(drilled.depth, DRILLED[3] + DRILLED[2] / 2, places=6)  # the point counts
        self.assertIn("blind hole", drilled.text())

    def test_hole_of(self):
        hole = self.hole_at(THROUGH[0], THROUGH[1])
        self.assertEqual(de.hole_of(self.shape, hole.faces[0]).faces, hole.faces)
        with self.assertRaises(de.DirectEditError):
            de.hole_of(self.shape, self.top())


class TestGeometry(Base):
    def test_offset_face_out_and_in(self):
        area = self.shape.getElement(self.top()).Area
        out = de.offset_face(self.shape, self.top(), 2.0)
        self.assertAlmostEqual(out.Volume, self.volume + 2 * area, places=4)
        self.assertAlmostEqual(out.BoundBox.ZMax, H + 2, places=6)
        self.assertEqual(len(de.holes(out)), 3)  # the holes go up with the face
        back = de.offset_face(self.shape, self.top(), -1.5)
        self.assertAlmostEqual(back.BoundBox.ZMax, H - 1.5, places=6)
        self.assertTrue(back.isValid())

    def test_offset_that_removes_the_part_is_refused(self):
        with self.assertRaises(de.DirectEditError):
            de.offset_face(self.shape, self.top(), -H)
        wall = self.hole_at(THROUGH[0], THROUGH[1]).faces[0]
        with self.assertRaises(de.DirectEditError) as ctx:
            de.offset_face(self.shape, wall, 1.0)
        self.assertIn("flat face", str(ctx.exception))

    def test_move_a_through_hole(self):
        hole = self.hole_at(THROUGH[0], THROUGH[1])
        out = de.edit_hole(self.shape, hole.faces[0], move=(5, -3, 0))
        self.assertAlmostEqual(out.Volume, self.volume, places=4)
        moved = self.hole_at(THROUGH[0] + 5, THROUGH[1] - 3, out)
        self.assertAlmostEqual(moved.diameter, THROUGH[2], places=6)
        self.assertTrue(moved.through)
        self.assertEqual(len(de.holes(out)), 3)

    def test_resize_holes(self):
        hole = self.hole_at(THROUGH[0], THROUGH[1])
        out = de.edit_hole(self.shape, hole.faces[0], diameter=6.0)
        self.assertAlmostEqual(out.Volume, self.volume - math.pi * (9 - 6.25) * H, places=4)
        blind = self.hole_at(BLIND[0], BLIND[1])
        out = de.edit_hole(self.shape, blind.faces[0], diameter=3.0)
        self.assertAlmostEqual(out.Volume, self.volume + math.pi * (4 - 2.25) * BLIND[3], places=4)
        self.assertAlmostEqual(self.hole_at(BLIND[0], BLIND[1], out).depth, BLIND[3], places=6)

    def test_a_moved_drilled_hole_keeps_its_point(self):
        drilled = self.hole_at(DRILLED[0], DRILLED[1])
        out = de.edit_hole(self.shape, drilled.faces[0], move=(-6, 4, 0))
        self.assertAlmostEqual(out.Volume, self.volume, places=4)
        again = self.hole_at(DRILLED[0] - 6, DRILLED[1] + 4, out)
        self.assertAlmostEqual(again.depth, DRILLED[3] + DRILLED[2] / 2, places=6)

    def test_a_hole_moved_off_the_part_is_refused(self):
        hole = self.hole_at(THROUGH[0], THROUGH[1])
        with self.assertRaises(de.DirectEditError) as ctx:
            de.edit_hole(self.shape, hole.faces[0], move=(500, 0, 0))
        self.assertIn("outside the part", str(ctx.exception))

    def test_delete_a_hole_and_a_fillet(self):
        hole = self.hole_at(BLIND[0], BLIND[1])
        out = de.delete_faces(self.shape, hole.faces)
        self.assertAlmostEqual(out.Volume, self.volume + math.pi * 4 * BLIND[3], places=4)
        self.assertEqual(len(de.holes(out)), 2)
        fillet = self.face(lambda f: type(f.Surface).__name__ == "Cylinder" and f.Area > 30
                           and abs(f.Surface.Radius - FILLET) < 1e-6)
        sharp = de.delete_faces(self.shape, [fillet])
        self.assertAlmostEqual(sharp.Volume, self.volume + (FILLET ** 2) * (1 - math.pi / 4) * H, places=4)


class TestSteps(Base):
    def test_steps_stack_and_follow_their_values(self):
        first = de.make(self.part, de.OFFSET, [self.top()], distance=2.0)
        self.assertFalse(self.part.Visibility)
        self.assertAlmostEqual(first.Shape.BoundBox.ZMax, H + 2, places=6)
        hole = self.hole_at(THROUGH[0], THROUGH[1], de.local_shape(first))
        second = de.make(first, de.HOLE, [hole.faces[0]], diameter=6.0, move=(4, 0, 0))
        self.assertAlmostEqual(self.hole_at(THROUGH[0] + 4, THROUGH[1], de.local_shape(second)).diameter, 6, places=6)
        first.Distance = 5.0
        self.doc.recompute()
        self.assertTrue(second.isValid())
        self.assertAlmostEqual(second.Shape.BoundBox.ZMax, H + 5, places=6)
        self.assertAlmostEqual(self.hole_at(THROUGH[0] + 4, THROUGH[1], de.local_shape(second)).depth, H + 5, places=6)

    def test_a_bad_value_keeps_the_last_shape_and_says_why(self):
        step = de.make(self.part, de.OFFSET, [self.top()], distance=2.0)
        volume = step.Shape.Volume
        step.Distance = -50.0
        self.doc.recompute()
        self.assertFalse(step.isValid())
        self.assertTrue(getattr(step, de.STATUS).startswith("Error:"))
        self.assertAlmostEqual(step.Shape.Volume, volume, places=6)

    def test_make_refuses_before_touching_the_document(self):
        count = len(self.doc.Objects)
        with self.assertRaises(de.DirectEditError):
            de.make(self.part, de.OFFSET, [self.top()], distance=-50.0)
        self.assertEqual(len(self.doc.Objects), count)
        self.assertTrue(self.part.Visibility)
        body = self.doc.addObject("PartDesign::Body", "Body")
        self.assertIn("Part Design", de.editable(body))

    def test_the_part_keeps_its_place(self):
        self.part.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation(App.Vector(0, 0, 1), 90))
        self.doc.recompute()
        step = de.make(self.part, de.OFFSET, [self.top()], distance=2.0)
        box = step.Shape.BoundBox
        self.assertAlmostEqual(box.ZMax, H + 2, places=6)
        self.assertAlmostEqual(box.XMax, 100, places=6)
        self.assertAlmostEqual(box.YLength, L, places=6)

    def test_remove_gives_the_solid_back_and_relinks_later_steps(self):
        first = de.make(self.part, de.OFFSET, [self.top()], distance=2.0)
        hole = self.hole_at(BLIND[0], BLIND[1], de.local_shape(first))
        second = de.make(first, de.DELETE, hole.faces)
        de.remove(second)
        self.assertTrue(first.Visibility)
        de.remove(first)
        self.assertTrue(self.part.Visibility)
        self.assertEqual([o.Name for o in self.doc.Objects], [self.part.Name])

    def test_survives_save_and_reopen(self):
        step = de.make(self.part, de.HOLE, [self.hole_at(THROUGH[0], THROUGH[1]).faces[0]], diameter=7.0)
        path = os.path.join(tempfile.mkdtemp(), "edited.FCStd")
        self.doc.saveAs(path)
        name, volume = step.Name, step.Shape.Volume
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        again = self.doc.getObject(name)
        again.Diameter = 8.0
        self.doc.recompute()
        self.assertTrue(again.isValid())
        self.assertAlmostEqual(again.Shape.Volume, volume - math.pi * (16 - 12.25) * H, places=4)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestPanels(Base):
    def setUp(self):
        import FreeCADGui as Gui

        super().setUp()
        self.Gui = Gui
        de.install()
        self.told = []
        self._tell, de._tell = de._tell, self.told.append
        Gui.Selection.clearSelection()
        pump()

    def tearDown(self):
        de._tell = self._tell
        super().tearDown()
        pump()

    def steps(self):
        return [o for o in self.doc.Objects if de.is_step(o)]

    def test_offset_panel_follows_refuses_and_is_one_undo_step(self):
        self.Gui.Selection.addSelection(self.doc.Name, self.part.Name, self.top())
        panel = de.start(de.OFFSET)
        pump()
        step, = self.steps()
        panel.fields["distance"].setValue(3.0)
        self.assertAlmostEqual(step.Shape.BoundBox.ZMax, H + 3, places=6)
        panel.fields["distance"].setValue(-50.0)
        self.assertIn("Not possible", panel.status.text())
        self.assertAlmostEqual(step.Shape.BoundBox.ZMax, H + 3, places=6)
        self.assertFalse(panel.accept())  # stays open on a value that cannot be done
        panel.fields["distance"].setValue(4.0)
        self.assertTrue(panel.accept())
        pump()
        self.assertEqual(self.doc.UndoNames[0], "Offset face")
        self.doc.undo()
        self.assertEqual(self.steps(), [])
        self.assertTrue(self.part.Visibility)

    def test_hole_panel_starts_at_the_holes_diameter_and_cancel_leaves_nothing(self):
        hole = self.hole_at(BLIND[0], BLIND[1])
        self.Gui.Selection.addSelection(self.doc.Name, self.part.Name, hole.faces[-1])  # its floor will do
        undo = list(self.doc.UndoNames)
        panel = de.start(de.HOLE)
        pump()
        self.assertAlmostEqual(panel.fields["diameter"].value(), BLIND[2], places=6)
        panel.fields["x"].setValue(-5.0)
        step, = self.steps()
        self.hole_at(BLIND[0] - 5, BLIND[1], de.local_shape(step))
        panel.reject()
        pump()
        self.assertEqual(self.steps(), [])
        self.assertEqual(list(self.doc.UndoNames), undo)
        self.assertTrue(self.part.Visibility)

    def test_delete_takes_the_whole_hole_and_edit_reopens_a_step(self):
        hole = self.hole_at(THROUGH[0], THROUGH[1])
        self.Gui.Selection.addSelection(self.doc.Name, self.part.Name, hole.faces[0])
        step = de.start(de.DELETE)
        pump()
        self.assertEqual(len(de.holes(de.local_shape(step))), 2)
        self.assertEqual(self.doc.UndoNames[0], "Delete face")
        self.assertIsNone(de.edit(step))
        self.Gui.Selection.clearSelection()
        self.Gui.Selection.addSelection(self.doc.Name, step.Name, self.top(de.local_shape(step)))
        de.start(de.OFFSET).accept()
        offset = [s for s in self.steps() if getattr(s, de.KIND) == de.OFFSET][0]
        panel = de.edit(offset)
        panel.fields["distance"].setValue(1.0)
        panel.reject()
        pump()
        self.assertAlmostEqual(offset.Distance.Value, 0.0, places=6)

    def test_messages_instead_of_edits(self):
        self.assertIsNone(de.start(de.OFFSET))
        self.assertIn("Select a face", self.told[-1])
        self.Gui.Selection.addSelection(self.doc.Name, self.part.Name, self.top())
        self.assertIsNone(de.start(de.HOLE))
        self.assertIn("not part of a round hole", self.told[-1])
        self.assertEqual(self.steps(), [])
