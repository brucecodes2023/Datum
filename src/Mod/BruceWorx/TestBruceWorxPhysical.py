# SPDX-License-Identifier: LGPL-2.1-or-later
"""Physical tab: inertia sums, principal axes, number text, CSV; a hand-worked mass fixture (MOD-11). No GUI."""

import csv
import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_physical as bp


def box_tensor(m, a, b, c):
    """A solid box's inertia about its centre (sides a, b, c along x, y, z)."""
    return ((m * (b * b + c * c) / 12, 0.0, 0.0), (0.0, m * (a * a + c * c) / 12, 0.0),
            (0.0, 0.0, m * (a * a + b * b) / 12))


def close(test, a, b, places=9):
    for i in range(3):
        for j in range(3):
            test.assertAlmostEqual(a[i][j], b[i][j], places=places)


class InertiaTests(unittest.TestCase):
    def test_shift_is_parallel_axes(self):
        t = bp.shift_inertia(box_tensor(2.0, 1, 1, 1), 2.0, (3.0, 0.0, 0.0))
        self.assertAlmostEqual(t[0][0], 2.0 * 2 / 12)  # along the shift: unchanged
        self.assertAlmostEqual(t[1][1], 2.0 * 2 / 12 + 2.0 * 9)
        self.assertAlmostEqual(t[2][2], 2.0 * 2 / 12 + 2.0 * 9)

    def test_products_follow_the_tensor_sign(self):
        # Point masses at (1, 1) and (-1, -1): ∫xy dm = 2, so the tensor's Ixy is -2.
        t = bp.combine([(1.0, (1.0, 1.0, 0.0), bp.ZERO), (1.0, (-1.0, -1.0, 0.0), bp.ZERO)])[2]
        self.assertAlmostEqual(t[0][1], -2.0)
        self.assertAlmostEqual(t[1][0], -2.0)

    def test_l_shape_matches_opencascade(self):
        # Two boxes 2x1x1 and 1x1x1 make an L; OpenCASCADE gives Ixy = +1/3 about the centre (checked 24 Oct).
        a = (2.0, (1.0, 0.5, 0.5), box_tensor(2.0, 2, 1, 1))
        b = (1.0, (0.5, 1.5, 0.5), box_tensor(1.0, 1, 1, 1))
        mass, centre, t = bp.combine([a, b])
        self.assertAlmostEqual(mass, 3.0)
        for got, want in zip(centre, (5 / 6, 5 / 6, 0.5)):
            self.assertAlmostEqual(got, want)
        self.assertAlmostEqual(t[0][1], 1 / 3)
        self.assertAlmostEqual(t[0][0], 7 / 6)
        self.assertAlmostEqual(t[2][2], 11 / 6)

    def test_combine_ignores_massless_and_empty(self):
        self.assertIsNone(bp.combine([]))
        self.assertIsNone(bp.combine([(0.0, (1, 2, 3), bp.ZERO)]))
        one = bp.combine([(2.0, (1.0, 2.0, 3.0), box_tensor(2.0, 1, 2, 3)), (0.0, (9, 9, 9), bp.ZERO)])
        self.assertEqual(one[1], (1.0, 2.0, 3.0))
        close(self, one[2], box_tensor(2.0, 1, 2, 3))

    def test_eigen_of_a_diagonal(self):
        values = bp.eigen_sym3(((3.0, 0, 0), (0, 1.0, 0), (0, 0, 2.0)))
        self.assertEqual([round(v, 9) for v, _a in values], [1.0, 2.0, 3.0])
        self.assertEqual([tuple(round(x, 9) for x in a) for _v, a in values],
                         [(0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)])

    def test_eigen_rebuilds_the_matrix(self):
        m = ((7.6, 0.2, -0.77), (0.2, 18.7, 0.1), (-0.77, 0.1, 24.6))
        pairs = bp.eigen_sym3(m)
        for i in range(3):
            for j in range(3):
                rebuilt = sum(v * a[i] * a[j] for v, a in pairs)
                self.assertAlmostEqual(rebuilt, m[i][j], places=9)
        for _v, a in pairs:
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in a)), 1.0)
            self.assertGreater(max(a, key=abs), 0)  # the largest component is positive
        self.assertLessEqual(pairs[0][0], pairs[1][0])
        self.assertLessEqual(pairs[1][0], pairs[2][0])

    def test_eigen_matches_freecad_principal_moments(self):
        # The plate of the sample model (mm^5), with FreeCAD's PrincipalProperties.Moments (24 Oct).
        m = ((7.60883e+06, 0, -766561), (0, 1.8659e+07, 0), (-766561, 0, 2.46285e+07))
        values = [v for v, _a in bp.eigen_sym3(m)]
        for got, want in zip(values, (7574371.8, 18659017.2, 24662961.9)):
            self.assertAlmostEqual(got / want, 1.0, places=4)


class TextTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(bp.fixed(38.6), "38.60")
        self.assertEqual(bp.fixed(-0.001), "0.00")
        self.assertEqual(bp.fixed(2.5, 0), "2")
        self.assertEqual(bp.trimmed(80.0), "80")
        self.assertEqual(bp.trimmed(22.5), "22.5")

    def test_vectors_and_boxes(self):
        self.assertEqual(bp.vector_text((38.6, 25, 6.1), 1, "mm"), "38.6, 25.0, 6.1 mm")
        self.assertEqual(bp.box_text((80, 50, 22), 2, "mm"), "80 × 50 × 22 mm")
        self.assertEqual(bp.vector_text((1, 0, 0), 3), "1.000, 0.000, 0.000")

    def test_pretty_units(self):
        self.assertEqual(bp.pretty("116.67 cm^2"), "116.67 cm²")
        self.assertEqual(bp.pretty("2700.00 kg/m^3"), "2700.00 kg/m³")
        self.assertEqual(bp.pretty(""), "")
        self.assertEqual(bp.pretty("14.52 ml"), "14.52 cm³")
        self.assertEqual(bp.pretty("2.10 l"), "2.10 dm³")
        self.assertEqual(bp.pretty("ml"), "cm³")  # the CSV's unit column
        self.assertEqual(bp.pretty("kg/l"), "kg/l")  # a density keeps its unit

    def test_units_and_counts(self):
        self.assertEqual(bp.inertia_unit("g", "mm"), "g·mm²")
        self.assertEqual(bp.count_text(1), "1 body")
        self.assertEqual(bp.count_text(3), "3 bodies")
        self.assertEqual(bp.subtitle("Part", 1), "Part · 1 body")
        self.assertEqual(bp.subtitle("", 4, 3), "4 bodies")

    def test_missing_material_note(self):
        self.assertEqual(bp.missing_text(0, 2), "")
        self.assertEqual(bp.missing_text(2, 2), "No material: mass and inertia need one.")
        self.assertEqual(bp.missing_text(1, 3), "1 body without a material, left out of mass and inertia.")

    def test_union_box(self):
        self.assertIsNone(bp.union_box([]))
        self.assertEqual(bp.union_box([(0, 0, 0, 1, 1, 1), None, (-1, 2, 0, 0, 3, 5)]), (-1, 0, 0, 1, 3, 5))

    def test_csv_and_clipboard(self):
        text = bp.csv_text([("Mass", 124.0, "g"), ("Centre of mass X", 38.6, "mm"), ("Material, note", "a", "")])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["Property", "Value", "Unit"])
        self.assertEqual(rows[1], ["Mass", "124.0", "g"])
        self.assertEqual(rows[3][0], "Material, note")
        self.assertEqual(bp.clip_text("Plate (Part · 1 body)", [("Mass", "124.00 g")]),
                         "Plate (Part · 1 body)\nMass\t124.00 g\n")

    def test_file_stem(self):
        self.assertEqual(bp.file_stem("Mounting plate"), "Mounting plate-physical")
        self.assertEqual(bp.file_stem("a/b:c"), "a_b_c-physical")
        self.assertEqual(bp.file_stem(""), "selection-physical")


def _steel():
    """A steel card from FreeCAD's library and its density in kg/mm^3."""
    import FreeCAD
    import Materials

    manager = Materials.MaterialManager()
    for uuid in manager.Materials:
        card = manager.getMaterial(uuid)
        if card.Name == "Steel-1C45":
            text = card.PhysicalProperties["Density"]
            return card, FreeCAD.Units.Quantity(str(text)).getValueAs("kg/mm^3").Value
    raise unittest.SkipTest("no Steel-1C45 card")


class FixtureTests(unittest.TestCase):
    """MOD-11 acceptance: mass, centre and inertia of a steel block with a pin, worked out by hand."""

    def setUp(self):
        try:
            import FreeCAD
            import Part
        except ImportError:
            raise unittest.SkipTest("needs FreeCAD")
        self.doc = FreeCAD.newDocument("BWPhysicalFixture")
        block = Part.makeBox(40, 20, 10)
        pin = Part.makeCylinder(5, 20, FreeCAD.Vector(30, 10, 10))
        self.obj = self.doc.addObject("Part::Feature", "Fixture")
        self.obj.Shape = block.fuse(pin).removeSplitter()
        self.card, self.rho = _steel()
        self.obj.ShapeMaterial = self.card
        self.doc.recompute()

    def tearDown(self):
        import FreeCAD

        FreeCAD.closeDocument(self.doc.Name)

    def test_block_and_pin_by_hand(self):
        rho, r, h = self.rho, 5.0, 20.0
        v1, c1 = 40.0 * 20.0 * 10.0, (20.0, 10.0, 5.0)
        v2, c2 = math.pi * r * r * h, (30.0, 10.0, 20.0)
        m1, m2 = rho * v1, rho * v2
        mass = m1 + m2
        centre = tuple((m1 * c1[k] + m2 * c2[k]) / mass for k in range(3))
        own = (
            (m1 * (20 ** 2 + 10 ** 2) / 12, m1 * (40 ** 2 + 10 ** 2) / 12, m1 * (40 ** 2 + 20 ** 2) / 12),
            (m2 * (3 * r * r + h * h) / 12, m2 * (3 * r * r + h * h) / 12, m2 * r * r / 2),
        )
        ixx = iyy = izz = ixz = 0.0
        for m, c, (jxx, jyy, jzz) in ((m1, c1, own[0]), (m2, c2, own[1])):
            dx, dy, dz = (c[k] - centre[k] for k in range(3))
            ixx += jxx + m * (dy * dy + dz * dz)
            iyy += jyy + m * (dx * dx + dz * dz)
            izz += jzz + m * (dx * dx + dy * dy)
            ixz += -m * dx * dz
        result = bp.Result([bp.Owner(self.obj, self.obj.Shape)])
        self.assertAlmostEqual(result.volume, v1 + v2, delta=1e-6 * (v1 + v2))
        self.assertAlmostEqual(result.mass, mass, delta=1e-9 * mass)
        for k in range(3):
            self.assertAlmostEqual(result.centre[k], centre[k], places=6)
        t = result.tensor
        scale = 1e-6 * izz
        self.assertAlmostEqual(t[0][0], ixx, delta=scale)
        self.assertAlmostEqual(t[1][1], iyy, delta=scale)
        self.assertAlmostEqual(t[2][2], izz, delta=scale)
        self.assertAlmostEqual(t[0][2], ixz, delta=scale)
        self.assertAlmostEqual(t[0][1], 0.0, delta=scale)
        self.assertAlmostEqual(t[1][2], 0.0, delta=scale)

    def test_reading_the_mass_leaves_the_part_untouched(self):
        # FreeCAD 1.1's getPhysicalValue writes the material back and touches the object (regression, MOD-11)
        from bruceworx import partprops as pp

        self.assertNotIn("Touched", self.obj.State)
        mass = pp.computed_mass(self.obj)
        bp.Result([bp.Owner(self.obj, self.obj.Shape)])
        self.assertNotIn("Touched", self.obj.State)
        self.assertAlmostEqual(float(mass.split()[0]), self.obj.Shape.Volume * self.rho, places=9)


if __name__ == "__main__":
    unittest.main()
