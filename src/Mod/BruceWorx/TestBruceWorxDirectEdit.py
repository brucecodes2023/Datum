# SPDX-License-Identifier: LGPL-2.1-or-later
"""MOD-04 direct edit, early slice: offset one planar face of a box.

The box stands in for an imported dumb solid. Offsetting a face changes the
volume by that face's area times the distance, and leaves one valid solid.

Run: FreeCADCmd -P <Mod/BruceWorx>, then unittest this module. Once the module
is installed, FreeCADCmd -t TestBruceWorxDirectEdit.
"""

import unittest

import FreeCAD as App
import Part

import bruceworx_directedit as de

DX, DY, DZ = 10.0, 20.0, 30.0


def _box():
    return Part.makeBox(DX, DY, DZ)


def _face(solid, normal):
    """The one face whose outward normal is ``normal``."""
    axis = App.Vector(*normal)
    found = [face for face in solid.Faces if face.normalAt(0, 0).dot(axis) > 0.999]
    if len(found) != 1:
        raise AssertionError(f"expected one face along {normal}, found {len(found)}")
    return found[0]


class TestOffsetFace(unittest.TestCase):
    def test_growing_the_top_adds_area_times_distance(self):
        solid = _box()
        face = _face(solid, (0, 0, 1))
        distance = 2.0
        out = de.offset_face(solid, face, distance)

        self.assertEqual(out.ShapeType, "Solid")
        self.assertTrue(out.isValid())
        self.assertEqual(len(out.Solids), 1)
        self.assertAlmostEqual(out.Volume - DX * DY * DZ, face.Area * distance, delta=1e-6)
        self.assertAlmostEqual(solid.Volume, DX * DY * DZ, delta=1e-6)
        self.assertAlmostEqual(out.BoundBox.XLength, DX, delta=1e-6)
        self.assertAlmostEqual(out.BoundBox.YLength, DY, delta=1e-6)
        self.assertAlmostEqual(out.BoundBox.ZMin, 0.0, delta=1e-6)
        self.assertAlmostEqual(out.BoundBox.ZMax, DZ + distance, delta=1e-6)

        # The result is a solid the same call can edit again.
        again = de.offset_face(out, _face(out, (0, 0, 1)), 1.0)
        self.assertTrue(again.isValid())
        self.assertAlmostEqual(again.Volume - DX * DY * DZ, face.Area * 3.0, delta=1e-6)
        self.assertAlmostEqual(again.BoundBox.ZMax, DZ + 3.0, delta=1e-6)

    def test_shrinking_and_the_far_side_follow_the_outward_normal(self):
        solid = _box()
        top = _face(solid, (0, 0, 1))
        shrunk = de.offset_face(solid, top, -1.0)
        self.assertAlmostEqual(shrunk.Volume - solid.Volume, -top.Area, delta=1e-6)
        self.assertAlmostEqual(shrunk.BoundBox.ZMax, DZ - 1.0, delta=1e-6)
        self.assertAlmostEqual(shrunk.BoundBox.ZMin, 0.0, delta=1e-6)

        bottom = _face(solid, (0, 0, -1))
        grown = de.offset_face(solid, bottom, 2.0)
        self.assertAlmostEqual(grown.Volume - solid.Volume, bottom.Area * 2.0, delta=1e-6)
        self.assertAlmostEqual(grown.BoundBox.ZMin, -2.0, delta=1e-6)
        self.assertAlmostEqual(grown.BoundBox.ZMax, DZ, delta=1e-6)

        side = _face(solid, (1, 0, 0))
        wider = de.offset_face(solid, side, 3.0)
        self.assertAlmostEqual(wider.Volume - solid.Volume, side.Area * 3.0, delta=1e-6)
        self.assertAlmostEqual(wider.BoundBox.XMax, DX + 3.0, delta=1e-6)
        self.assertAlmostEqual(wider.BoundBox.YLength, DY, delta=1e-6)
        self.assertAlmostEqual(wider.BoundBox.ZLength, DZ, delta=1e-6)

    def test_refuses_a_non_planar_face_another_solid_and_a_collapse(self):
        solid = _box()
        cylinder = Part.makeCylinder(5, 10)
        curved = next(face for face in cylinder.Faces if not isinstance(face.Surface, Part.Plane))
        with self.assertRaises(ValueError):
            de.offset_face(cylinder, curved, 1.0)

        foreign = _face(_box(), (0, 0, 1))
        with self.assertRaises(ValueError):
            de.offset_face(solid, foreign, 1.0)

        with self.assertRaises(ValueError):
            de.offset_face(solid.Shells[0], _face(solid, (0, 0, 1)), 1.0)

        with self.assertRaises(ValueError):
            de.offset_face(solid, _face(solid, (0, 0, 1)), -(DZ + 1.0))
