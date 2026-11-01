# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for drawing packages (bruceworx_package, PDM-13): which drawings a package holds, the
IN PROGRESS mark, names and package.csv."""

import datetime
import unittest

import bruceworx_package as pk


def structure():
    items = {
        "VL-02-01002-001": {"type": "assembly"}, "VL-02-01001-001": {"type": "assembly"},
        "VL-01-01001-001": {"type": "part"}, "VL-01-01002-001": {"type": "part"}, "VL-01-01034-001": {"type": "part"},
        "VL-03-01001-001": {"type": "drawing"}, "VL-03-01014-001": {"type": "drawing"},
        "VL-03-01026-001": {"type": "drawing"},
    }
    return {"items": items,
            "bom": {"VL-02-01002-001": [["VL-02-01001-001", 1], ["VL-01-01034-001", 1]],
                    "VL-02-01001-001": [["VL-01-01001-001", 1], ["VL-01-01002-001", 1]]},
            "drawings": {"VL-01-01001-001": ["VL-03-01001-001"], "VL-01-01002-001": ["VL-03-01014-001"],
                         "VL-01-01034-001": ["VL-03-01026-001"]}}


class TestPackage(unittest.TestCase):
    def test_an_assembly_brings_every_drawing_below_it(self):
        self.assertEqual(pk.package_drawings(structure(), ["VL-02-01002-001"]),
                         ["VL-03-01001-001", "VL-03-01014-001", "VL-03-01026-001"])
        self.assertEqual(pk.package_drawings(structure(), ["VL-02-01001-001"]), ["VL-03-01001-001", "VL-03-01014-001"])

    def test_selected_rows_each_once(self):
        picked = ["VL-03-01001-001", "VL-01-01001-001", "VL-01-01034-001", "VL-02-01001-001"]
        self.assertEqual(pk.package_drawings(structure(), picked),
                         ["VL-03-01001-001", "VL-03-01014-001", "VL-03-01026-001"])
        self.assertEqual(pk.package_drawings(structure(), ["nope"]), [])

    def test_only_released_drawings_go_unmarked(self):
        self.assertFalse(pk.watermarked("Released"))
        for state in ("WIP", "In Review", "Obsolete", ""):
            self.assertTrue(pk.watermarked(state))

    def test_names(self):
        self.assertEqual(pk.safe_name("a/b: c?"), "a b c")
        self.assertEqual(pk.safe_name("  "), "Package")
        day = datetime.date(2026, 11, 1)
        self.assertEqual(pk.default_name(["VL-02-01001-001"], day), "VL-02-01001-001 package 2026-11-01")
        self.assertEqual(pk.default_name(["A", "B", "C"], day), "3 drawings package 2026-11-01")

    def test_csv(self):
        rows = pk.csv_rows([{"pn": "VL-03-01001-001", "revision": "01", "state": "WIP", "description": "Impeller drawing",
                             "of": "VL-01-01001-001", "file": "VL-03-01001-001-01.pdf"}])
        self.assertEqual(rows[0], ["part_number", "revision", "state", "description", "drawing_of", "file"])
        self.assertEqual(rows[1][-1], "VL-03-01001-001-01.pdf")


if __name__ == "__main__":
    unittest.main()
