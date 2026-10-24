# SPDX-License-Identifier: LGPL-2.1-or-later
"""Physical tab: inertia sums, principal axes, number text, CSV. No GUI."""

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


if __name__ == "__main__":
    unittest.main()
