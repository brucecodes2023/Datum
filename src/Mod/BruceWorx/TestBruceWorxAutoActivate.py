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

    def test_single_body_without_assembly(self):
        self.assertIsNone(aa.should_activate(**{**OK, "n_assemblies": 0, "n_bodies": 1}))
        for n_asm, n_body in ((0, 0), (0, 2), (2, 1), (2, 0)):
            self.assertIsNotNone(aa.should_activate(**{**OK, "n_assemblies": n_asm, "n_bodies": n_body}), (n_asm, n_body))
        # one assembly wins over any number of bodies
        self.assertIsNone(aa.should_activate(**{**OK, "n_assemblies": 1, "n_bodies": 3}))

    def test_single_part_without_assembly_or_body(self):
        self.assertIsNone(aa.should_activate(**{**OK, "n_assemblies": 0, "n_parts": 1}))
        self.assertIsNotNone(aa.should_activate(**{**OK, "n_assemblies": 0, "n_parts": 2}))
        self.assertIsNotNone(aa.should_activate(**{**OK, "n_assemblies": 2, "n_parts": 1}))

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
        cls.sub = cls.make("sub", 1)
        cls.parent = cls.make_parent("parent", cls.sub)
        cls.body1 = cls.make_bodies("body1", 1)
        cls.body2 = cls.make_bodies("body2", 2)
        cls.step = cls.make_step("step")
        cls.imp = cls.make_import("imp", 1)
        cls.imp2 = cls.make_import("imp2", 2)
        cls.pdpart = cls.make_pd_part("pdpart")

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

    @classmethod
    def _save(cls, doc, name):
        path = os.path.join(cls.dir, name + ".FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        return path

    @classmethod
    def make_parent(cls, name, sub_path):
        """An assembly document that links the assembly of another file (a sub-assembly in a machine)."""
        sub = FreeCAD.openDocument(sub_path)
        doc = FreeCAD.newDocument("AA" + name)
        doc.addObject("Assembly::AssemblyObject", "Assembly0")
        path = os.path.join(cls.dir, name + ".FCStd")
        doc.saveAs(path)  # a cross-document link needs the owner saved
        link = doc.addObject("App::Link", "SubLink")
        link.LinkedObject = sub.getObject("Assembly0")
        doc.recompute()
        doc.save()
        FreeCAD.closeDocument(doc.Name)
        FreeCAD.closeDocument(sub.Name)
        return path

    @classmethod
    def make_bodies(cls, name, n):
        doc = FreeCAD.newDocument("AA" + name)
        for i in range(n):
            doc.addObject("PartDesign::Body", f"Body{i}")
        doc.recompute()
        return cls._save(doc, name)

    @classmethod
    def make_step(cls, name):
        """What a STEP import gives: plain Part::Features, no Body."""
        import Part

        doc = FreeCAD.newDocument("AA" + name)
        for i in range(2):
            doc.addObject("Part::Feature", f"Shape{i}").Shape = Part.makeBox(5, 5, 5)
        doc.recompute()
        return cls._save(doc, name)

    @classmethod
    def make_import(cls, name, n):
        """What the body import gives: n App::Parts, each holding a Part::Feature "Body" (no PartDesign Body)."""
        import Part

        doc = FreeCAD.newDocument("AA" + name)
        for i in range(n):
            part = doc.addObject("App::Part", f"Part{i}")
            feat = doc.addObject("Part::Feature", f"Body{i}")
            feat.Shape = Part.makeBox(5, 5, 5)
            part.addObject(feat)
        doc.recompute()
        return cls._save(doc, name)

    @classmethod
    def make_pd_part(cls, name):
        doc = FreeCAD.newDocument("AA" + name)
        part = doc.addObject("App::Part", "Part")
        part.addObject(doc.addObject("PartDesign::Body", "Body"))
        doc.recompute()
        return cls._save(doc, name)

    def active_part(self, doc):
        return self.Gui.getDocument(doc.Name).ActiveView.getActiveObject("part")

    def active_body(self, doc):
        return self.Gui.getDocument(doc.Name).ActiveView.getActiveObject("pdbody")

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

    def test_sub_assembly_after_parent(self):
        """The printer is active; a sub-assembly it links opened afterwards (not a root: linked from the
        printer's file) becomes active and the printer's edit is let go."""
        pdoc = self.open(self.parent)
        self.assertIsNotNone(self.active_assembly(pdoc), f"{self.why}")
        sub = [d for d in FreeCAD.listDocuments().values() if d.FileName == self.sub]
        self.assertEqual(len(sub), 1, "the linked file is open too")
        sub = sub[0]
        self.Gui.setActiveDocument(sub.Name)
        self.Gui.getDocument(sub.Name).createView("Gui::View3DInventor")  # the user opens the linked file
        self.assertEqual(len(aa._assemblies(sub)), 1)  # the link from the other file does not hide it
        self.assertIsNone(aa.activate(sub.Name), aa._state(sub.Name))
        self.wait()
        self.assertEqual(self.active_assembly(sub).Name, "Assembly0")
        self.assertIsNone(self.Gui.getDocument(pdoc.Name).getInEdit(), "the parent's edit was reset")
        self.assertIsNotNone(self.Gui.getDocument(sub.Name).getInEdit())

    def test_switching_tabs_moves_the_edit(self):
        """Bruce: "whatever window I am in should be the active window". The printer and a sub-assembly are
        both open; choosing either tab gives its assembly the edit, even when that tab's view still holds
        the "assembly" marker from before (seen 2 Nov: drags in the sub-assembly tab went nowhere)."""
        pdoc = self.open(self.parent)
        sub = [d for d in FreeCAD.listDocuments().values() if d.FileName == self.sub][0]
        self.Gui.setActiveDocument(sub.Name)
        self.Gui.getDocument(sub.Name).createView("Gui::View3DInventor")
        self.assertIsNone(aa.activate(sub.Name), aa._state(sub.Name))
        self.wait()
        self.assertEqual(self.Gui.editDocument().Document.Name, sub.Name)
        for doc in (pdoc, sub, pdoc):
            other = sub if doc is pdoc else pdoc
            # the stale state: the marker is still set here while the other file holds the edit
            self.Gui.getDocument(doc.Name).ActiveView.setActiveObject("assembly", doc.getObject("Assembly0"))
            self.Gui.setActiveDocument(doc.Name)
            self.assertIsNone(aa.activate(doc.Name), aa._state(doc.Name))
            self.wait()
            self.assertEqual(self.Gui.editDocument().Document.Name, doc.Name)
            self.assertIsNone(self.Gui.getDocument(other.Name).getInEdit(), "the other file let go")

    def test_sketch_in_other_document_nothing(self):
        other = FreeCAD.newDocument("AAsketchdoc")
        sk = other.addObject("Sketcher::SketchObject", "Sketch")
        other.recompute()
        self.Gui.getDocument(other.Name).setEdit(sk)
        self.assertIsNotNone(self.Gui.getDocument(other.Name).getInEdit())
        doc = FreeCAD.openDocument(self.one)
        self.assertIsNotNone(aa.activate(doc.Name))
        self.wait(500)
        self.assertIsNone(self.active_assembly(doc))
        self.assertIsNotNone(self.Gui.getDocument(other.Name).getInEdit(), "the sketch stays in edit")

    def test_single_body_becomes_active(self):
        doc = self.open(self.body1)
        body = self.active_body(doc)
        self.assertIsNotNone(body, f"{self.why} {aa._state(doc.Name)}")
        self.assertEqual(body.Name, "Body0")

    def test_two_bodies_nothing(self):
        doc = self.open(self.body2)
        self.assertIsNone(self.active_body(doc))

    def test_step_like_part_features_nothing(self):
        doc = self.open(self.step)
        self.assertIsNone(self.active_body(doc))
        self.assertIsNone(self.active_assembly(doc))

    def test_imported_part_becomes_active(self):
        doc = self.open(self.imp)
        part = self.active_part(doc)
        self.assertIsNotNone(part, f"{self.why} {aa._state(doc.Name)}")
        self.assertEqual(part.Name, "Part0")
        self.assertEqual(part.TypeId, "App::Part")
        self.assertFalse(self.Gui.getDocument(doc.Name).Modified)
        self.assertIsNone(self.active_body(doc))
        self.assertEqual(len(self.Gui.Selection.getSelection()), 0)

    def test_two_imported_parts_nothing(self):
        doc = self.open(self.imp2)
        self.assertIsNone(self.active_part(doc))

    def test_pd_part_gets_body_and_part(self):
        doc = self.open(self.pdpart)
        self.assertEqual(self.active_body(doc).Name, "Body")
        self.assertEqual(self.active_part(doc).Name, "Part")

    def test_imported_part_preference_off_nothing(self):
        self.param.SetBool(aa.PREF_NAME, False)
        doc = self.open(self.imp)
        self.assertIsNone(self.active_part(doc))

    def test_single_body_preference_off_nothing(self):
        self.param.SetBool(aa.PREF_NAME, False)
        doc = self.open(self.body1)
        self.assertIsNone(self.active_body(doc))

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
