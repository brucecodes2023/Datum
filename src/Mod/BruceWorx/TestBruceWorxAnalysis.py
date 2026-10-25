# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inspect analyses (MOD-11): mesh normals, overhang, draft, wall thickness, curvature. No GUI.

The fixtures have answers worked out by hand: a cantilever's underside, the upper quarter of a horizontal
hole, a drafted cone, plates of known thickness, a sphere and a cylinder's curvature.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD
import Part

import bruceworx_analysis as ba

V = FreeCAD.Vector


def faces_of(mesh, shape, kind):
    return [i for i, f in enumerate(shape.Faces) if f.Surface.TypeId == kind]


class AngleTests(unittest.TestCase):
    def test_overhang_angle_convention(self):
        up = (0, 0, 1)
        self.assertAlmostEqual(ba.overhang_angle((0, 0, -1), up), 90.0)  # a ceiling
        self.assertAlmostEqual(ba.overhang_angle((1, 0, 0), up), 0.0)  # a wall
        self.assertAlmostEqual(ba.overhang_angle((0, 0, 1), up), -90.0)  # facing up
        self.assertAlmostEqual(ba.overhang_angle((1, 0, -1), up), 45.0)

    def test_draft_angle(self):
        self.assertAlmostEqual(ba.draft_angle((1, 0, 0), (0, 0, 1)), 0.0)
        self.assertAlmostEqual(ba.draft_angle((math.cos(math.radians(3)), 0, math.sin(math.radians(3))),
                                              (0, 0, 1)), 3.0)
        with self.assertRaises(ValueError):
            ba.unit((0, 0, 0))

    def test_ramp_and_texture(self):
        self.assertEqual(ba.ramp(0.0), ba.RAMP[0][1])
        for a, b in zip(ba.ramp(1.0), ba.RAMP[-1][1]):
            self.assertAlmostEqual(a, b)
        a = ba.Analysis("x", None, 0.0, 10.0, [ba.Band("lo", ba.RED, 0.0, 5.0), ba.Band("hi", ba.GREEN, 5.0, 10.0)])
        texels = a.band_texture(10)
        self.assertEqual(texels[:5], [ba.RED] * 5)
        self.assertEqual(texels[5:], [ba.GREEN] * 5)


class MeshTests(unittest.TestCase):
    def test_box_normals_point_out(self):
        box = Part.makeBox(10, 20, 30)
        mesh = ba.surface_mesh(box)
        self.assertTrue(all(mesh.exact))
        centre = (5.0, 10.0, 15.0)
        out = ((mesh.points - centre) * mesh.normals).sum(axis=1)
        self.assertTrue((out > 0).all())
        self.assertAlmostEqual(mesh.area, box.Area, places=6)

    def test_hole_normals_point_to_the_axis(self):
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(5, 20, V(10, 10, 0)))
        mesh = ba.surface_mesh(block)
        hole = faces_of(mesh, block, "Part::GeomCylinder")[0]
        sel = mesh.vertex_face == hole
        radial = mesh.points[sel][:, :2] - (10.0, 10.0)
        self.assertTrue(((radial * mesh.normals[sel][:, :2]).sum(axis=1) < 0).all())

    def test_deflection_follows_the_view(self):
        box = Part.makeBox(100, 100, 100)
        self.assertAlmostEqual(ba.default_deflection(box), 0.5)


class OverhangTests(unittest.TestCase):
    def test_cantilever(self):
        shape = Part.makeBox(10, 10, 20).fuse(Part.makeBox(30, 10, 5, V(0, 0, 15))).removeSplitter()
        result = ba.overhang(ba.surface_mesh(shape))
        self.assertAlmostEqual(result.stats["support_area"], 200.0, places=6)  # 20 x 10 under the arm
        self.assertAlmostEqual(result.stats["bed_area"], 100.0, places=6)
        self.assertEqual(sum(b.area for b in result.bands), result.stats["total_area"])

    def test_texture_colours_of_exact_values(self):
        # a ceiling (90), a wall (0) and the bed must not land on a band edge in the 512-texel texture
        shape = Part.makeBox(10, 10, 20).fuse(Part.makeBox(30, 10, 5, V(0, 0, 15))).removeSplitter()
        result = ba.overhang(ba.surface_mesh(shape))
        texels = result.band_texture(512)

        def shown(value):
            s = (value - result.lo) / (result.hi - result.lo)
            return texels[min(511, int(s * 512))]

        self.assertEqual(shown(90.0), ba.RED)
        self.assertEqual(shown(0.0), ba.NEUTRAL)
        self.assertEqual(shown(ba.BED_VALUE), ba.BLUE)
        self.assertEqual(shown(45.0), ba.YELLOW)  # exactly the threshold prints

    def test_upside_down_the_arm_rests_on_nothing(self):
        shape = Part.makeBox(10, 10, 20).fuse(Part.makeBox(30, 10, 5, V(0, 0, 15))).removeSplitter()
        result = ba.overhang(ba.surface_mesh(shape), build=(0, 0, -1))
        self.assertAlmostEqual(result.stats["bed_area"], 300.0, places=6)  # the arm's top is now the bed
        self.assertAlmostEqual(result.stats["support_area"], 0.0, places=6)

    def test_horizontal_hole(self):
        r, length = 5.0, 20.0
        block = Part.makeBox(length, 20, 20).cut(Part.makeCylinder(r, length, V(0, 10, 10), V(1, 0, 0)))
        result = ba.overhang(ba.surface_mesh(block, deflection=0.01))
        expected = r * math.pi / 2 * length  # the upper quarter: more than 45 degrees from vertical
        self.assertAlmostEqual(result.stats["support_area"], expected, delta=0.02 * expected)

    def test_threshold_moves_the_band(self):
        box = Part.makeBox(20, 20, 20)
        chamfer = box.makeChamfer(5, [e for e in box.Edges
                                      if abs(e.CenterOfMass.z) < 1e-9 and abs(e.CenterOfMass.x) < 1e-9])
        mesh = ba.surface_mesh(chamfer)
        strict = ba.overhang(mesh, threshold=40.0)
        lenient = ba.overhang(mesh, threshold=50.0)
        self.assertGreater(strict.stats["support_area"], 0.0)  # the 45 degree chamfer needs support at 40
        self.assertAlmostEqual(lenient.stats["support_area"], 0.0, places=6)


class DraftTests(unittest.TestCase):
    def test_drafted_cone_and_straight_box(self):
        h = 20.0
        cone = Part.makeCone(10, 10 - h * math.tan(math.radians(5)), h)
        result = ba.draft(ba.surface_mesh(cone, deflection=0.01), required=1.0)
        side = cone.Faces[0].Area if cone.Faces[0].Surface.TypeId == "Part::GeomCone" else \
            [f for f in cone.Faces if f.Surface.TypeId == "Part::GeomCone"][0].Area
        top = math.pi * (10 - h * math.tan(math.radians(5))) ** 2
        self.assertAlmostEqual(result.stats["needs_draft_area"], 0.0, places=6)
        self.assertAlmostEqual(result.stats["positive_area"], side + top, delta=0.01 * side)
        box = ba.draft(ba.surface_mesh(Part.makeBox(10, 10, 10)))
        self.assertAlmostEqual(box.stats["needs_draft_area"], 400.0, places=6)
        self.assertAlmostEqual(box.stats["negative_area"], 100.0, places=6)


class ThicknessTests(unittest.TestCase):
    def test_plates(self):
        thick = ba.thickness(ba.surface_mesh(Part.makeBox(30, 30, 2)), 0.8, 1.2)
        self.assertAlmostEqual(thick.stats["thinnest"], 2.0, delta=0.01)
        self.assertEqual(thick.stats["under_minimum_area"], 0.0)
        thin = ba.thickness(ba.surface_mesh(Part.makeBox(30, 30, 0.6)), 0.8, 1.2)
        self.assertAlmostEqual(thin.stats["thinnest"], 0.6, delta=0.01)
        self.assertAlmostEqual(thin.stats["under_minimum_area"], 2 * 900.0, places=6)

    def test_shelled_box(self):
        outer = Part.makeBox(30, 20, 15)
        shell = outer.cut(Part.makeBox(28, 18, 14, V(1, 1, 1)))  # 1 mm walls, open top
        result = ba.thickness(ba.surface_mesh(shell), 0.8, 1.2)
        self.assertAlmostEqual(result.stats["thinnest"], 1.0, delta=0.01)
        self.assertEqual(result.stats["under_minimum_area"], 0.0)
        rim = 30 * 20 - 28 * 18  # its rays run down the 14 mm walls: thick
        self.assertGreater(result.stats["under_target_area"], 0.95 * (shell.Area - rim))
        self.assertEqual(result.bands[1].name, "Thin")

    def test_a_hole_mouth_is_not_a_thin_wall(self):
        # a radial hole into a hub: rays near the mouth run beside the hole wall (the sun gear's set screw)
        hub = Part.makeCylinder(5, 8).cut(Part.makeCylinder(2.5, 8))
        hole = Part.makeCylinder(1.25, 6, V(0, 1.5, 4), V(0, 1, 0))
        result = ba.thickness(ba.surface_mesh(hub.cut(hole)), 0.8, 1.2)
        self.assertEqual(result.stats["under_minimum_area"], 0.0)

    def test_a_thin_fin_between_neighbouring_faces(self):
        # a wedge fin, 10 degrees: its two sides share the tip edge and turn back against each other
        import math as m

        h = 10.0
        w = h * m.tan(m.radians(5))
        fin = Part.Face(Part.makePolygon([V(0, -w, 0), V(0, w, 0), V(h, 0, 0), V(0, -w, 0)])).extrude(V(0, 0, 10))
        result = ba.thickness(ba.surface_mesh(fin), 0.8, 1.2)
        self.assertGreater(result.stats["under_minimum_area"], 0.0)

    def test_adjacency(self):
        box = Part.makeBox(1, 1, 1)
        adjacent = ba.face_adjacency(box)
        self.assertEqual(int(adjacent.sum()), 6 + 6 * 4)  # itself and its four neighbours, not the opposite face

    def test_box_edges_are_not_thin(self):
        # rays from vertices on a 90 degree edge must not run along the neighbouring face
        result = ba.thickness(ba.surface_mesh(Part.makeBox(5, 5, 5)), 0.8, 1.2)
        self.assertIsNone(result.stats["thinnest"])  # every wall is 5 mm: beyond the 2.4 mm cap


class CurvatureTests(unittest.TestCase):
    def test_sphere(self):
        mesh = ba.surface_mesh(Part.makeSphere(10), curvature=True)
        mean = ba.curvature(mesh, "Mean")
        gauss = ba.curvature(mesh, "Gaussian")
        self.assertAlmostEqual(float(mean.scalar.mean()), 0.1, delta=1e-3)  # convex: positive
        self.assertAlmostEqual(float(gauss.scalar.mean()), 0.01, delta=1e-4)
        self.assertAlmostEqual(mean.stats["smallest_radius"], 10.0, delta=0.01)

    def test_cylinder_and_hole(self):
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(5, 20, V(10, 10, 0)))
        mesh = ba.surface_mesh(block, curvature=True)
        hole = faces_of(mesh, block, "Part::GeomCylinder")[0]
        sel = mesh.vertex_face == hole
        mean = ba.curvature(mesh, "Mean")
        self.assertAlmostEqual(float(mean.scalar[sel].mean()), -0.1, delta=1e-6)  # concave: negative
        self.assertAlmostEqual(float(ba.curvature(mesh, "Gaussian").scalar[sel].max()), 0.0, delta=1e-9)
        self.assertAlmostEqual(float(ba.curvature(mesh, "Largest").scalar[sel].mean()), 0.2, delta=1e-6)
        self.assertEqual(mean.lo, -mean.hi)


if __name__ == "__main__":
    unittest.main()
