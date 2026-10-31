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


class PackTests(unittest.TestCase):
    def test_everything_shows_when_it_fits(self):
        self.assertEqual(rp.pack_tabs([40, 50, 60], 200, 0), ([0, 1, 2], False))

    def test_the_rest_go_under_more(self):
        self.assertEqual(rp.pack_tabs([78, 42, 84, 62, 100, 88], 366, 1), ([0, 1, 2, 3], True))

    def test_the_current_tab_stays_visible(self):
        shown, more = rp.pack_tabs([80, 80, 80, 80], 200, 3, spacing=0, more=40)
        self.assertTrue(more)
        self.assertIn(3, shown)


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

    def test_only_the_tasks_dock_takes_the_command_word(self):
        """Another dock in the panel (the user's Report view) keeps its own name: with the attachment
        dialog open both tabs read "Sel...ent" (26 Oct)."""
        titles = self.TITLES + ["Report View"]
        opened = rp.strip_items(titles, 4, True, "Select attachment")
        self.assertEqual([(t, k) for _i, t, k, _c in opened],
                         [("Properties", "tab"), ("Vault", "tab"), ("Parameters", "tab"), ("Physical", "tab"),
                          ("Select attachment", "command"), ("Report View", "tab")])
        # the Tasks dock, already renamed by the task card, is still the command's tab
        renamed = ["Properties", "Vault", "Parameters", "Physical", "Select attachment", "Report View"]
        self.assertEqual([t for _i, t, k, _c in rp.strip_items(renamed, 4, True, "Select attachment") if k == "command"],
                         ["Select attachment"])
        closed = rp.strip_items(titles, 0, False, "Tasks")
        self.assertEqual([(t, k) for _i, t, k, _c in closed][-1], ("Report View", "tab"))
        self.assertNotIn("Tasks", [t for _i, t, _k, _c in closed])


class TaskDialogInThePanel(unittest.TestCase):
    """GUI: a task dialog opened while the Start page is the active view shows in the right panel with
    its OK/Cancel inside the panel, and one command tab (26 Oct)."""

    def setUp(self):
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("GUI only")

    def _pump(self, n=15):
        import time

        import FreeCADGui as Gui

        for _ in range(n):
            Gui.updateGui()
            time.sleep(0.02)

    def test_attachment_dialog_from_the_start_page(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtWidgets

        mw = Gui.getMainWindow()
        area = mw.findChild(QtWidgets.QMdiArea)
        start = [s for s in area.subWindowList() if s.widget() and s.widget().metaObject().className() == "StartGui::StartView"]
        if not start:
            self.skipTest("no Start page open")
        doc = App.newDocument("RightPanelAttach")
        self.addCleanup(lambda: App.closeDocument(doc.Name) if doc.Name in App.listDocuments() else None)
        doc.addObject("PartDesign::Body", "Body")
        doc.recompute()
        self._pump()
        area.setActiveSubWindow(start[0])
        self._pump()
        Gui.Selection.clearSelection()
        Gui.activateWorkbench("PartDesignWorkbench")
        Gui.runCommand("PartDesign_NewSketch")
        self._pump(30)
        self.addCleanup(lambda: Gui.Control.closeDialog() if Gui.Control.activeDialog() else None)
        self.assertTrue(Gui.Control.activeDialog(), "the attachment dialog did not open")
        tabs = rp._overlay()
        self.assertIsNotNone(tabs)
        strip = [(b.text(), b.property("kind")) for b in tabs.findChildren(QtWidgets.QToolButton) if b.objectName() == "bwRightTab"]
        self.assertEqual(len([t for t, k in strip if k == "command"]), 1, strip)
        dock = mw.findChild(QtWidgets.QDockWidget, rp.TASK_DOCK)
        self.assertTrue(dock.isVisible())
        control = [w for w in dock.findChildren(QtWidgets.QWidget) if w.metaObject().className() == "Gui::TaskView::TaskEditControl"]
        self.assertEqual(len(control), 1)
        self.assertTrue(control[0].isVisibleTo(mw))
        shown = dock.rect()
        for button in control[0].findChildren(QtWidgets.QPushButton):
            if button.text() in ("OK", "Cancel"):
                corner = button.mapTo(dock, button.rect().bottomRight())
                self.assertTrue(shown.contains(corner), f"{button.text()} at {corner} outside the panel {shown}")
                corner = button.mapTo(mw, button.rect().bottomRight())
                self.assertTrue(mw.rect().contains(corner), f"{button.text()} at {corner} outside the window")


if __name__ == "__main__":
    unittest.main()
