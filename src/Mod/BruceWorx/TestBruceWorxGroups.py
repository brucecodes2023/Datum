# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the captioned tool groups (bruceworx_groups)."""

import unittest

import bruceworx_groups as groups


class TestCaptions(unittest.TestCase):
    def test_known_part_design_toolbars(self):
        self.assertEqual(groups.caption_for("Part Design Modeling Features"), "Create")
        self.assertEqual(groups.caption_for("Part Design Transformation Features"), "Pattern")
        self.assertEqual(groups.caption_for("Part Design Dress-Up Features"), "Modify")

    def test_sketcher_edit_toolbars(self):
        names = ("Edit Mode", "Geometries", "Constraints", "Sketcher Tools", "Visual Helpers")
        self.assertEqual([groups.caption_for(n) for n in names], ["Sketch", "Create", "Constrain", "Modify", "Display"])

    def test_step_c2_group_title(self):
        self.assertEqual(groups.caption_for("Solid · Create"), "Create")

    def test_prefix_and_suffix_dropped(self):
        self.assertEqual(groups.caption_for("TechDraw Pages"), "Pages")
        self.assertEqual(groups.caption_for("Mesh Segmentation"), "Segmentation")
        self.assertEqual(groups.caption_for("Part Tools"), "Tools")

    def test_title_that_is_only_a_prefix_is_kept(self):
        self.assertEqual(groups.caption_for("Surface"), "Surface")
        self.assertEqual(groups.caption_for("Spreadsheet"), "Spreadsheet")

    def test_caption_text_is_upper_case_with_arrow(self):
        self.assertEqual(groups.caption_text("Part Design Helper Features"), "CONSTRUCT ▾")

    def test_empty_title(self):
        self.assertEqual(groups.caption_for(""), "")


class TestPlacement(unittest.TestCase):
    def test_centred_under_buttons(self):
        self.assertEqual(groups.place_caption(10, 110, 200, 40), (40, 40))

    def test_kept_inside_the_toolbar(self):
        self.assertEqual(groups.place_caption(0, 30, 100, 60), (0, 60))
        self.assertEqual(groups.place_caption(70, 100, 100, 60), (40, 60))

    def test_wider_than_the_toolbar(self):
        self.assertEqual(groups.place_caption(0, 30, 50, 80), (0, 50))


class TestEligible(unittest.TestCase):
    def test_workbench_toolbar_in_top_area(self):
        self.assertTrue(groups.eligible("Part Design Modeling Features", True, False, True))

    def test_app_toolbars_and_own_bars_skipped(self):
        for name in ("File", "View", "Workbench", "SearchBar", "BruceWorxWorkspaces"):
            self.assertFalse(groups.eligible(name, True, False, True), name)

    def test_floating_vertical_or_elsewhere_skipped(self):
        name = "Assembly Joints"
        self.assertFalse(groups.eligible(name, False, False, True))
        self.assertFalse(groups.eligible(name, True, True, True))
        self.assertFalse(groups.eligible(name, True, False, False))


if __name__ == "__main__":
    unittest.main()
