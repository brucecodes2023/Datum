# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sheet-metal rules in an inch document (PDM-09 step 3): the rule list reads "16 ga (0.060 in)", T, R
and relief show in inch with three places, mm labels are unchanged."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import sheetmetal_rules as smr  # noqa: E402


class InchLabels(unittest.TestCase):
    def test_gauge_rule_in_an_inch_document(self):
        rule = smr.find(smr.seed_rules("mm"), "CRS-16ga")  # rules stay mm with the gauge alias
        self.assertAlmostEqual(rule.thickness, 0.0598 * 25.4, places=4)
        self.assertEqual(smr.format_length(rule.thickness, "in"), "0.060 in")
        self.assertTrue(smr.label(rule, "in").startswith("CRS-16ga — Steel CR 1008, 16 ga (0.060 in), R 0.060, K "))
        self.assertTrue(smr.label(rule, "mm").startswith("CRS-16ga — Steel CR 1008, 16 ga (1.519 mm), R 1.519, K "))
        self.assertEqual(smr.format_length(smr.to_mm(0.125, "in"), "in"), "0.125 in")
        self.assertEqual(smr.format_length(0.0, "in", unit=False), "0.000")
        self.assertEqual(smr.stock_text(rule, "in"), "Sheet 16 ga Steel CR 1008")

    def test_mm_labels_unchanged(self):
        rule = smr.find(smr.seed_rules("mm"), "AL5052-1.5")
        self.assertEqual(smr.format_length(rule.thickness, "mm"), "1.5 mm")
        self.assertTrue(smr.label(rule, "mm").startswith("AL5052-1.5 — Al 5052-H32, 1.5 mm, R 1.5, K "))
        inch = smr.find(smr.seed_rules("in"), "AL5052-.063")
        self.assertEqual(smr.stock_text(inch, "in"), "Sheet 0.063 in Al 5052-H32")
        self.assertEqual(smr.format_length(inch.thickness, "in"), "0.063 in")


if __name__ == "__main__":
    unittest.main()
