# SPDX-License-Identifier: LGPL-2.1-or-later
"""Auto-activate the assembly (bruceworx_autoactivate): the rules, and opening saved files in the GUI."""

import os
import tempfile
import unittest

import FreeCAD

import bruceworx_autoactivate as aa

OK = dict(enabled=True, n_assemblies=1, dialog_open=False, editing=False, active_part=False, busy=False,
          workbench="PartDesignWorkbench")


class TestRules(unittest.TestCase):
    def test_all_clear(self):
        self.assertIsNone(aa.should_activate(**OK))

    def test_each_blocker(self):
        for key, value in (("enabled", False), ("n_assemblies", 0), ("n_assemblies", 2), ("dialog_open", True),
                           ("editing", True), ("active_part", True), ("busy", True), ("workbench", "FemWorkbench"),
                           ("workbench", "TechDrawWorkbench")):
            self.assertIsNotNone(aa.should_activate(**{**OK, key: value}), f"{key}={value}")

    def test_assembly_workbench_is_design(self):
        self.assertIsNone(aa.should_activate(**{**OK, "workbench": "AssemblyWorkbench"}))

    def test_other_addon_detected(self):
        self.assertTrue(aa.other_addon_loaded({"VLAutoActivate": object()}))
        self.assertFalse(aa.other_addon_loaded({}))


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestOpen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        cls.dir = tempfile.mkdtemp(prefix="bw-autoact-")
        cls.param = FreeCAD.ParamGet(aa.PREFS_PATH)
        cls.saved = cls.param.GetBool(aa.PREF_NAME, True)
        cls.wb = Gui.activeWorkbench().name() if Gui.activeWorkbench() else ""
        Gui.activateWorkbench("PartDesignWorkbench")
        cls.one = cls.make("one", 1)
        cls.two = cls.make("two", 2)

    @classmethod
    def tearDownClass(cls):
        cls.param.SetBool(aa.PREF_NAME, cls.saved)
        for d in list(FreeCAD.listDocuments().values()):
            if d.FileName.startswith(cls.dir) or d.Name.startswith("AA"):
                FreeCAD.closeDocument(d.Name)

    @classmethod
    def make(cls, name, n):
        doc = FreeCAD.newDocument("AA" + name)
        for i in range(n):
            doc.addObject("Assembly::AssemblyObject", f"Assembly{i}")
        doc.recompute()
        path = os.path.join(cls.dir, name + ".FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        return path

    def wait(self, ms=1500):
        from PySide import QtCore, QtWidgets

        end = QtCore.QTime.currentTime().addMSecs(ms)
        while QtCore.QTime.currentTime() < end:
            QtWidgets.QApplication.processEvents()

    def setUp(self):
        self.Gui.Control.closeDialog()
        self.param.SetBool(aa.PREF_NAME, True)
        self.assertFalse(aa.other_addon_loaded())

    def tearDown(self):
        for d in list(FreeCAD.listDocuments().values()):
            self.Gui.getDocument(d.Name).resetEdit()
            FreeCAD.closeDocument(d.Name)

    def active_assembly(self, doc):
        return self.Gui.getDocument(doc.Name).ActiveView.getActiveObject("assembly")

    def open(self, path):
        doc = FreeCAD.openDocument(path)
        # the installed observer does this on its own; the test calls the same step in case it was not installed
        self.why = aa.activate(doc.Name)
        self.wait()
        return doc

    def test_one_assembly_becomes_active(self):
        doc = self.open(self.one)
        asm = self.active_assembly(doc)
        self.assertIsNotNone(asm, f"{self.why} {aa._state(doc.Name)}")
        self.assertEqual(asm.Name, "Assembly0")
        self.assertEqual(self.Gui.activeWorkbench().name(), "PartDesignWorkbench")  # the workspace stays
        self.assertEqual(len(self.Gui.Selection.getSelection()), 0)  # nothing left selected

    def test_two_assemblies_nothing(self):
        doc = self.open(self.two)
        self.assertIsNone(self.active_assembly(doc))

    def test_sketch_in_edit_nothing(self):
        doc = FreeCAD.openDocument(self.one)
        sk = doc.addObject("Sketcher::SketchObject", "Sketch")
        doc.recompute()
        self.Gui.getDocument(doc.Name).setEdit(sk)
        self.assertIsNotNone(self.Gui.getDocument(doc.Name).getInEdit())
        self.assertIsNotNone(aa.activate(doc.Name))  # edit or its task dialog
        self.wait(500)
        self.assertIsNone(self.active_assembly(doc))

    def test_preference_off_nothing(self):
        self.param.SetBool(aa.PREF_NAME, False)
        doc = self.open(self.one)
        self.assertIsNone(self.active_assembly(doc))

    def test_other_workspace_nothing(self):
        self.Gui.activateWorkbench("TechDrawWorkbench")
        try:
            doc = self.open(self.one)
            self.assertIsNone(self.active_assembly(doc))
        finally:
            self.Gui.activateWorkbench("PartDesignWorkbench")


if __name__ == "__main__":
    unittest.main()
