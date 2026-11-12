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


class SubFileTests(unittest.TestCase):
    def test_files_linked_into_are_sub_files(self):
        deps = {"Printer": ["Printer", "Frame", "Bolt"], "Frame": ["Frame", "Bolt"], "Bolt": ["Bolt"], "Sheet": ["Sheet"]}
        self.assertEqual(dt.sub_files(deps), {"Frame", "Bolt"})

    def test_label_drops_lock_and_view_number(self):
        self.assertEqual(dt.tab_label("\U0001F512 TL-01-01058-001 : 1"), "TL-01-01058-001")
        self.assertEqual(dt.tab_label("\U0001F512 TL-01-01058-001"), "TL-01-01058-001")
        self.assertEqual(dt.tab_label("Start"), "Start")

    def test_only_sub_file_tabs_hide(self):
        labels = ["Start", "Printer", "Frame", "Bolt", "Buy list"]
        by_label = {"Printer": {"Printer"}, "Frame": {"Frame"}, "Bolt": {"Bolt"}}
        self.assertEqual(dt.hidden_tabs(labels, by_label, {"Frame", "Bolt"}), {2, 3})

    def test_current_tab_stays(self):
        by_label = {"Printer": {"Printer"}, "Frame": {"Frame"}}
        self.assertEqual(dt.hidden_tabs(["Printer", "Frame"], by_label, {"Frame"}, current=1), set())

    def test_shared_label_with_an_open_file_stays(self):
        by_label = {"Plate": {"Plate", "Plate001"}}
        self.assertEqual(dt.hidden_tabs(["Plate"], by_label, {"Plate001"}), set())


if __name__ == "__main__":
    unittest.main()
