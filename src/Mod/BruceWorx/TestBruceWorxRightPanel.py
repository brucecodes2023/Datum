# SPDX-License-Identifier: LGPL-2.1-or-later
"""Right panel tabs: the overlay's widget list and when Properties comes forward. No GUI."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_rightpanel as rp

OURS = [n for n, _t in rp.TABS]


class WidgetListTests(unittest.TestCase):
    def test_ours_before_the_task_view(self):
        self.assertEqual(rp.with_tabs(["Tasks"]), OURS + ["Tasks"])
        self.assertEqual(rp.with_tabs(["Report", "Tasks"]), ["Report"] + OURS + ["Tasks"])

    def test_task_view_kept_or_added(self):
        self.assertEqual(rp.with_tabs([]), OURS + ["Tasks"])
        self.assertEqual(rp.with_tabs([], tasks=False), OURS)

    def test_idempotent(self):
        once = rp.with_tabs(["Tasks"])
        self.assertEqual(rp.with_tabs(once), once)

    def test_off_removes_ours_only(self):
        self.assertEqual(rp.with_tabs(OURS + ["Tasks"], on=False), ["Tasks"])

    def test_parse(self):
        self.assertEqual(rp.parse_widgets("Tasks,"), ["Tasks"])
        self.assertEqual(rp.parse_widgets(""), [])


class SelectionTests(unittest.TestCase):
    def test_properties_forward_unless_a_command_is_open(self):
        self.assertTrue(rp.bring_properties(False, "BWPhysical"))
        self.assertFalse(rp.bring_properties(True, "Tasks"))
        self.assertFalse(rp.bring_properties(False, rp.PROPERTIES))


class StripTests(unittest.TestCase):
    def test_command_word(self):
        self.assertEqual(rp.command_word("Pad Parameters"), "Pad")
        self.assertEqual(rp.command_word("Fillet parameters"), "Fillet")
        self.assertEqual(rp.command_word("Revolute joint"), "Revolute joint")
        self.assertEqual(rp.command_word("Parameters"), "Parameters")

    TITLES = ["Properties", "Vault", "Parameters", "Physical", "Tasks"]

    def test_command_tab_only_while_a_command_is_open(self):
        closed = rp.strip_items(self.TITLES, 0, False, "Tasks")
        self.assertEqual([(i, t, k) for i, t, k, _c in closed],
                         [(0, "Properties", "tab"), (1, "Vault", "tab"), (2, "Parameters", "tab"), (3, "Physical", "tab")])
        opened = rp.strip_items(self.TITLES, 4, True, "Pad parameters")
        self.assertEqual(opened[-1], (4, "Pad", "command", True))
        self.assertFalse(any(c for _i, _t, _k, c in opened[:-1]))


if __name__ == "__main__":
    unittest.main()
