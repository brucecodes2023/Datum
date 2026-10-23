# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the macOS title row (bruceworx_titlebar)."""

import unittest

import bruceworx_titlebar as titlebar


class TestTitleBar(unittest.TestCase):
    def test_double_click_follows_system_settings(self):
        self.assertEqual(titlebar.double_click_action("Maximize\n"), "zoom")
        self.assertEqual(titlebar.double_click_action("Minimize"), "minimize")
        self.assertIsNone(titlebar.double_click_action("None"))
        self.assertEqual(titlebar.double_click_action(""), "zoom")

    def test_inset_leaves_room_for_the_traffic_lights(self):
        # The lights end at x = 72 pt with the compact toolbar (measured on macOS 27).
        self.assertGreaterEqual(titlebar.inset(False), 72 + 8)
        self.assertLess(titlebar.inset(True), titlebar.inset(False))

    def test_tab_title(self):
        self.assertEqual(titlebar.tab_title("Bracket : 1*"), "Bracket")
        self.assertEqual(titlebar.tab_title("Bracket : 2"), "Bracket : 2")
        self.assertEqual(titlebar.tab_title("Start"), "Start")

    def test_tab_state_compares_by_value(self):
        a = titlebar.tab_state(["Start", "A"], [False, True], 1)
        self.assertEqual(a, titlebar.tab_state(["Start", "A"], [False, True], 1))
        self.assertNotEqual(a, titlebar.tab_state(["Start", "A"], [False, False], 1))

    def test_full_size_content_bit(self):
        self.assertEqual(titlebar.NS_FULL_SIZE_CONTENT_VIEW, 32768)


if __name__ == "__main__":
    unittest.main()
