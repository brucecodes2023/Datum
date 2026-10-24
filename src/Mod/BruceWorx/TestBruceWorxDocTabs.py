# SPDX-License-Identifier: LGPL-2.1-or-later
"""Document tabs: finding the Start page's tab. No GUI."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_doctabs as dt


class StartIndexTests(unittest.TestCase):
    def test_by_class(self):
        self.assertEqual(dt.start_index([("Gui::View3DInventor", "Plate"), ("StartGui::StartView", "Start")]), 1)

    def test_by_title_when_class_unknown(self):
        self.assertEqual(dt.start_index([("", "Plate"), ("", "Start")]), 1)

    def test_a_document_named_start_is_not_the_page(self):
        self.assertEqual(dt.start_index([("Gui::View3DInventor", "Start")]), -1)

    def test_none(self):
        self.assertEqual(dt.start_index([]), -1)


class TitleTests(unittest.TestCase):
    def test_view_number_dropped(self):
        self.assertEqual(dt.clean_title("Plate : 1"), "Plate")
        self.assertEqual(dt.clean_title("Plate : 2", views=2), "Plate : 2")
        self.assertEqual(dt.clean_title("Start"), "Start")
        self.assertEqual(dt.clean_title("A : B"), "A : B")


if __name__ == "__main__":
    unittest.main()
