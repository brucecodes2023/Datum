# SPDX-License-Identifier: LGPL-2.1-or-later
"""Test-run window placement: the half of the screen. No GUI."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_testwindow as tw


class HalfTests(unittest.TestCase):
    def test_left_and_right(self):
        self.assertEqual(tw.half((0, 25, 1728, 1080), "left"), (0, 25, 864, 1080))
        self.assertEqual(tw.half((0, 25, 1728, 1080), "right"), (864, 25, 864, 1080))

    def test_odd_width_covers_the_screen(self):
        left, right = tw.half((0, 0, 1001, 10), "left"), tw.half((0, 0, 1001, 10), "right")
        self.assertEqual(left[2] + right[2], 1001)


class NameTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(tw.test_name(["FreeCAD", "-u", "x.cfg", "/a/check_tab.py"]), "check tab")
        self.assertEqual(tw.test_name(["FreeCAD"], "Sketcher: rectangle"), "Sketcher: rectangle")
        self.assertEqual(tw.test_name(["FreeCAD"]), "test run")

    def test_log_line(self):
        self.assertEqual(tw.log_line("10:52:03", "check tab", 3, "Select the pad"),
                         "10:52:03  check tab · step 3  Select the pad")


if __name__ == "__main__":
    unittest.main()
