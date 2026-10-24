# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for undo toasts (bruceworx_toasts): words, filters, placement, stale undo."""

import unittest

import bruceworx_toasts as toasts


class TestText(unittest.TestCase):
    def test_created(self):
        self.assertEqual(toasts.toast_text("Make Pad"), "Pad created")
        self.assertEqual(toasts.toast_text("Create Pad"), "Pad created")
        self.assertEqual(toasts.toast_text("Make additive Box"), "Box created")
        self.assertEqual(toasts.toast_text("Add a Body"), "Body created")
        self.assertEqual(toasts.toast_text("New Sketch"), "Sketch created")
        self.assertEqual(toasts.toast_text("Make LinearPattern"), "Linear Pattern created")

    def test_past_tense_and_plain(self):
        self.assertEqual(toasts.toast_text("Delete"), "Deleted")
        self.assertEqual(toasts.toast_text("Linear Pattern"), "Linear Pattern")
        self.assertEqual(toasts.toast_text("Roll history tip"), "Roll history tip")
        self.assertEqual(toasts.toast_text(""), "")


class TestFilter(unittest.TestCase):
    def test_commands_toast(self):
        for name in ("Make Pad", "Make Fillet", "Delete", "Linear Pattern", "Rectangular Pattern"):
            self.assertTrue(toasts.should_toast(name), name)

    def test_property_edits_do_not(self):
        self.assertFalse(toasts.should_toast("Pad.Length"))
        self.assertFalse(toasts.should_toast("Pad001.Length..."))
        self.assertFalse(toasts.should_toast("property Length"))

    def test_sketch_editing_and_quiet(self):
        self.assertFalse(toasts.should_toast("Add coincident constraint", sketch_editing=True))
        self.assertFalse(toasts.should_toast("Recompute"))
        self.assertFalse(toasts.should_toast("Sync state", quiet={"Sync state"}))
        self.assertFalse(toasts.should_toast(""))


class TestUndoStamp(unittest.TestCase):
    def test_top_of_stack_only(self):
        stamp = (3, "Make Pad")
        self.assertTrue(toasts.can_undo(stamp, 3, ["Make Pad", "New Sketch"]))
        self.assertFalse(toasts.can_undo(stamp, 4, ["Make Fillet", "Make Pad"]))  # something since
        self.assertFalse(toasts.can_undo(stamp, 2, ["New Sketch"]))  # already undone
        self.assertFalse(toasts.can_undo(stamp, 3, ["Make Pad"], pending=True))  # a command is open
        self.assertFalse(toasts.can_undo(stamp, 3, []))


class TestPlace(unittest.TestCase):
    def test_above_the_pill(self):
        x, y = toasts.toast_place(800, 600, 160, 30, pill=(300, 550, 200, 38))
        self.assertEqual((x, y), (320, 550 - toasts.PILL_GAP - 30))

    def test_no_pill(self):
        self.assertEqual(toasts.toast_place(800, 600, 160, 30), (320, 558))

    def test_kept_inside(self):
        x, _y = toasts.toast_place(100, 600, 160, 30, pill=(0, 550, 60, 38))
        self.assertEqual(x, 0)


if __name__ == "__main__":
    unittest.main()
