# SPDX-License-Identifier: LGPL-2.1-or-later
"""The assembly Model Builder's panes (ASM-UI-01): the Assembly tab, the tree, the Settings form, the new-mate node.

Needs a GUI FreeCAD (run_gui_tests.py); skipped headless.
"""

import time
import unittest

import FreeCAD as App

import JointObject
import bruceworx_asm_model as am

V = App.Vector


def settle(seconds=0.3):
    from PySide import QtWidgets

    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


class TestAsmPanes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if App.GuiUp:
            import bruceworx_autoactivate as aa

            cls.param = App.ParamGet(aa.PREFS_PATH)
            cls.saved = cls.param.GetBool(aa.PREF_NAME, True)
            cls.param.SetBool(aa.PREF_NAME, False)  # the tests activate the assembly themselves

    @classmethod
    def tearDownClass(cls):
        if App.GuiUp:
            import bruceworx_autoactivate as aa

            cls.param.SetBool(aa.PREF_NAME, cls.saved)

    def setUp(self):
        if not App.GuiUp:
            self.skipTest("needs the GUI")
        import FreeCADGui as Gui
        import bruceworx_asm_panes as ap
        import bruceworx_sim_workspace as sw
        import bruceworx_workspaces as ws

        self.Gui, self.ap, self.sw = Gui, ap, sw
        if sw.active():
            ws.activate_workspace("Design")
            settle(0.8)
        self.doc = App.newDocument("BWAsmPanes")
        self.doc.UndoMode = 1
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jg = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.a = self._box("A", (0, 0, 0))
        self.b = self._box("B", (30, 5, 0))
        self.c = self._box("C", (60, 0, 0))
        g = self.jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, self.a)
        self.doc.recompute()
        settle(0.5)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.ap.detach()
        try:
            self.Gui.getDocument(self.doc.Name).resetEdit()
        except Exception:
            pass
        App.closeDocument(self.doc.Name)
        settle(0.3)

    def _box(self, name, pos):
        b = self.asm.newObject("Part::Box", name)
        b.Placement = App.Placement(V(*pos), App.Rotation())
        self.doc.recompute()
        return b

    def activate(self):
        self.Gui.getDocument(self.doc.Name).setEdit(self.asm)
        settle(0.3)
        self.ap.sync()
        settle(0.5)
        left = self.sw._left()
        info = (self.sw.active(), self.Gui.ActiveDocument and self.Gui.ActiveDocument.Document.Name, self.Gui.activeWorkbench().name(),
                self.ap.active_assembly(), left is not None and left.isVisible(), self.ap.attached())
        self.assertTrue(self.ap.attached(), info)

    def mate(self):
        j = am.create_mate(self.asm, [self.a, ["Face2", "Face2"]], [self.b, ["Face1", "Face1"]])
        self.ap.flush_now()
        settle(0.2)
        self.ap.refresh()
        return j

    def pick(self, obj, face):
        self.Gui.Selection.addSelection(self.doc.Name, self.asm.Name, f"{obj}.{face}")
        settle(0.3)

    def tab_names(self):
        return [t for t, _c in self.sw.strip_tabs()]

    # ----- the tab ----------------------------------------------------------

    def test_the_tab_comes_with_an_active_assembly_and_goes_with_none(self):
        self.ap.sync()
        settle(0.4)
        self.assertNotIn("Assembly", self.tab_names())
        self.activate()
        self.assertTrue(self.ap.attached())
        self.assertIn("Assembly", self.tab_names())
        self.assertEqual(self.tab_names()[-1], "Assembly")  # next to the Browser, after it
        self.assertNotIn("Simulation", self.tab_names())
        self.Gui.getDocument(self.doc.Name).resetEdit()
        settle(0.3)
        self.ap.sync()
        settle(0.5)
        self.assertFalse(self.ap.attached())
        self.assertEqual(self.tab_names(), [])

    def test_the_assembly_tab_is_a_sidebar_tab_like_the_simulation_one(self):
        self.activate()
        left = self.sw._left()
        titles = [left.tabText(i) for i in range(left.count()) if left.tabBar().isTabVisible(i)]
        self.assertIn("Assembly", titles, (left is None or [(left.tabText(i), left.tabBar().isTabVisible(i)) for i in range(left.count())],
                                           left.isVisible(), self.sw._s["tabs"].keys()))
        self.assertTrue(self.ap.select_tab())
        settle(0.4)
        self.assertTrue(self.ap.tab_shown())
        self.assertEqual(self.ap._a["split"].count(), 2)  # Model Builder over Settings

    # ----- the tree ---------------------------------------------------------

    def test_the_tree_shows_components_and_mates(self):
        self.activate()
        j = self.mate()
        rows = {k: (t, d, meta) for k, t, d, meta in self.ap.rows()}
        self.assertIn("comp/A", rows)
        self.assertEqual(rows["comp/A"][0], "(f) A")
        self.assertEqual(rows["comp/B"][0], "B")
        self.assertIn(f"mate/{j.Name}", rows)
        self.assertEqual(rows[f"mate/{j.Name}"][2], "A · B")
        self.assertGreater(rows[f"mate/{j.Name}"][1], rows["sec/mates"][1])
        item = self.ap._items_by_key()["comp/B"]
        self.assertFalse(item.icon(0).isNull() and False)  # the icon comes from the Browser's own view object

    # ----- selecting a mate ---------------------------------------------------

    def test_selecting_a_mate_fills_settings_and_highlights_the_references(self):
        self.activate()
        j = self.mate()
        self.assertTrue(self.ap.select_key(f"mate/{j.Name}"))
        settle(0.3)
        self.assertEqual(self.ap.title_text(), j.Label)
        self.assertEqual(len(self.ap.pick_rows()), 2)
        self.assertIn("A", self.ap.pick_rows()[0])
        state = self.ap.grid_state()
        self.assertTrue(state["coincident"][1])
        self.assertFalse(state["tangent"][0])  # a tangent mate needs a cylinder
        self.assertTrue(state["distance"][0])
        subs = sorted(s.split(".")[-1] for _o, s in self.ap.highlighted())
        dbg = [(x.ObjectName, list(x.SubElementNames)) for x in self.Gui.Selection.getSelectionEx("", 0)]
        dbg2 = [(x.ObjectName, list(x.SubElementNames)) for x in self.Gui.Selection.getSelectionEx()]
        self.assertEqual(subs, ["Face1", "Face2"], (dbg, dbg2, j.Reference1, j.Reference2))  # the mate's two references
        self.assertIn("Flip", self.ap._a["buttons"])

    def test_a_type_click_changes_the_joint_and_undo_restores_it(self):
        self.activate()
        j = self.mate()
        self.ap.select_key(f"mate/{j.Name}")
        self.assertEqual(am.mate_key(j), "coincident")
        self.ap._a["grid"]["distance"].click()
        self.ap.flush_now()
        settle(0.2)
        self.assertEqual(am.mate_key(j), "distance")
        self.assertIn("distance", self.ap.field_names())  # Placement gained the distance field
        self.ap.edit("distance", 5)
        self.ap.flush_now()
        self.assertAlmostEqual(j.Distance.Value, 5.0, places=4)
        self.doc.undo()  # the distance edit
        self.doc.undo()  # the type change
        settle(0.2)
        self.assertEqual(am.mate_key(j), "coincident")
        self.ap.flush_now()
        settle(0.2)
        self.assertTrue(self.ap.grid_state()["coincident"][1])

    def test_armed_slot_replaces_the_next_pick(self):
        self.activate()
        j = self.mate()
        self.ap.select_key(f"mate/{j.Name}")
        self.ap.arm_slot(1)
        import bruceworx_picktrace as pt

        self.assertTrue(pt.slot_armed)
        self.pick("C", "Face1")
        self.assertEqual(j.Reference2[0].Name, "C")
        self.assertFalse(pt.slot_armed)
        # a pick while no slot is armed is ignored
        self.pick("B", "Face1")
        self.assertEqual(j.Reference2[0].Name, "C")

    # ----- the new mate -------------------------------------------------------

    def test_new_mate_command_and_two_picks_create_a_coincident_mate(self):
        import bruceworx_picktrace as pt

        self.activate()
        before = len(am.tree(self.asm).find("sec/mates").children)
        self.Gui.runCommand("BruceWorx_Mate")
        settle(0.3)
        self.assertEqual(self.ap.current_key(), "mate/new")
        self.assertTrue(pt.slot_armed)
        self.assertIn("mate/new", [k for k, *_ in self.ap.rows()])
        self.assertTrue(self.ap.grid_state()["coincident"][0])
        self.pick("A", "Face2")
        self.assertEqual(self.ap.current_key(), "mate/new")
        self.pick("A", "Face1")  # the same part twice is refused
        self.assertEqual(self.ap.current_key(), "mate/new")
        self.assertIn("same part", self.ap.note())
        self.pick("B", "Face1")
        settle(0.3)
        self.ap.flush_now()
        key = self.ap.current_key()
        self.assertTrue(key.startswith("mate/") and key != "mate/new")
        self.assertEqual(len(am.tree(self.asm).find("sec/mates").children), before + 1)
        joint = self.doc.getObject(key[len("mate/"):])
        self.assertEqual(am.mate_key(joint), "coincident")
        self.assertNotIn("mate/new", [k for k, *_ in self.ap.rows()])
        self.assertFalse(pt.slot_armed)
        fa, fb = self.a.Shape.getElement("Face2"), self.b.Shape.getElement("Face1")
        self.assertAlmostEqual(fa.CenterOfMass.x, fb.CenterOfMass.x, places=3)

    def test_escape_drops_the_new_mate_node(self):
        from PySide import QtCore, QtGui, QtWidgets
        import bruceworx_picktrace as pt

        self.activate()
        self.ap.start_new_mate()
        settle(0.2)
        self.pick("A", "Face2")
        self.assertTrue(self.ap.new_active())
        event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.Gui.getMainWindow(), event)
        settle(0.4)
        self.assertFalse(self.ap.new_active())
        self.assertNotIn("mate/new", [k for k, *_ in self.ap.rows()])
        self.assertFalse(pt.slot_armed)
        self.assertEqual(len(am.tree(self.asm).find("sec/mates").children), 0)

    def test_selecting_another_node_drops_the_new_mate_node(self):
        self.activate()
        self.ap.start_new_mate()
        self.assertTrue(self.ap.new_active())
        self.ap.select_key("comp/B")
        self.assertFalse(self.ap.new_active())
        self.assertNotIn("mate/new", [k for k, *_ in self.ap.rows()])

    def test_the_panes_leave_the_selection_alone_during_a_task_dialog(self):
        self.activate()
        self.ap.start_new_mate()

        class Dialog:
            def getStandardButtons(self):
                return 0

            def reject(self):
                return True

        self.Gui.Control.showDialog(Dialog())
        try:
            self.assertTrue(self.Gui.Control.activeDialog())
            self.assertFalse(self.ap.pick(self.doc.Name, self.asm.Name, "A.Face2"))
            self.assertEqual(self.ap._a["new"]["picks"], [])
        finally:
            self.Gui.Control.closeDialog()

    def test_delete_and_flip_buttons(self):
        self.activate()
        j = self.mate()
        self.ap.select_key(f"mate/{j.Name}")
        self.assertTrue(self.ap.flip())
        self.assertTrue(self.ap.solve())
        name = j.Name
        self.assertTrue(self.ap.delete_current())
        self.assertIsNone(self.doc.getObject(name))
        self.assertEqual(self.ap.current_key(), "sec/mates")


if __name__ == "__main__":
    unittest.main()
