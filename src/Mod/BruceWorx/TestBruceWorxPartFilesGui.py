# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests of one part per file and sub-assemblies (bruceworx_partfiles): the three commands and the repair
on open. Needs the running app: FreeCAD -t TestBruceWorxPartFilesGui"""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App

import bruceworx_partfiles as pf


def pump(n=10):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestPartFilesGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        pf.install()

    def setUp(self):
        self.before = set(App.listDocuments())
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-partfiles-gui-"))
        self.Gui.Selection.clearSelection()

    def tearDown(self):
        box = pf._state["box"]
        if box is not None:
            box.close()
            pf._state["box"] = None
        for name in set(App.listDocuments()) - self.before:
            App.closeDocument(name)
        pump()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def select(self, obj):
        """Select ``obj``; a just-made object is not selectable until the tree has caught up."""
        for _ in range(20):
            self.Gui.Selection.clearSelection()
            self.Gui.Selection.addSelection(obj.Document.Name, obj.Name)
            pump(4)
            if self.Gui.Selection.getSelectionEx("", 0):
                return
        self.fail(f"{obj.Name} could not be selected")

    def test_commands_are_registered(self):
        names = self.Gui.listCommands()
        for name in (pf.CMD_EXTERNAL, pf.CMD_REPAIR, pf.CMD_FLEXIBLE):
            self.assertIn(name, names)
            self.assertTrue(os.path.isfile(self.Gui.Command.get(name).getInfo()["pixmap"]), name)

    def test_make_external_on_the_selected_body(self):
        doc = App.newDocument("PfGui")
        doc.saveAs(os.path.join(self.tmp, "PfGui.FCStd"))
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        body = asm.newObject("PartDesign::Body", "Body")
        body.Label = "Bracket"
        body.newObject("PartDesign::AdditiveBox", "Box")
        doc.recompute()
        pump()
        self.select(body)  # the app records it as the assembly and the path "Body."
        pump()
        self.assertTrue(self.Gui.Command.get(pf.CMD_EXTERNAL).isActive())
        self.Gui.runCommand(pf.CMD_EXTERNAL)
        pump()
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Bracket.FCStd")))
        links = [o for o in doc.Objects if o.TypeId == "App::Link"]
        self.assertEqual([link.Label for link in links], ["Bracket"])
        self.assertIsNone(doc.getObject("Body"))
        box = pf._state["box"]
        self.assertTrue(box is not None and box.isVisible())
        self.assertIn("Bracket.FCStd", box.informativeText())
        self.assertIn("Undo", box.informativeText())
        self.assertTrue(links[0].ViewObject.Visibility)

    def test_nothing_selected_says_so(self):
        doc = App.newDocument("PfNone")
        doc.saveAs(os.path.join(self.tmp, "PfNone.FCStd"))
        pump()
        self.Gui.runCommand(pf.CMD_EXTERNAL)
        pump()
        self.assertIn("Select a body", pf._state["box"].informativeText())

    def test_a_file_with_a_moved_part_is_repaired_on_open_and_says_so(self):
        part = App.newDocument("PfPart")
        part.addObject("Part::Box", "Solid")
        part.recompute()
        os.makedirs(os.path.join(self.tmp, "parts"))
        part.saveAs(os.path.join(self.tmp, "parts", "PfPart.FCStd"))
        doc = App.newDocument("PfAsm")
        path = os.path.join(self.tmp, "PfAsm.FCStd")
        doc.saveAs(path)
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        link = asm.newObject("App::Link", "L0")
        link.LinkedObject = part.Objects[0]
        doc.recompute()
        doc.save()
        App.closeDocument("PfAsm")
        App.closeDocument("PfPart")
        pump()
        os.makedirs(os.path.join(self.tmp, "moved"))
        shutil.move(os.path.join(self.tmp, "parts", "PfPart.FCStd"), os.path.join(self.tmp, "moved", "PfPart.FCStd"))
        opened = App.openDocument(path)
        pump(25)
        self.assertEqual(pf.broken_links(opened), [])
        self.assertIsNotNone(opened.getObject("L0").LinkedObject)
        box = pf._state["box"]
        self.assertTrue(box is not None and box.isVisible())
        self.assertIn("parts/PfPart.FCStd is now moved/PfPart.FCStd", box.informativeText())
        self.assertEqual(opened.UndoNames[0], "Repair links")

    def test_flexible_command_toggles_the_selected_sub_assembly(self):
        sub = App.newDocument("PfSub")
        sub_asm = sub.addObject("Assembly::AssemblyObject", "SubAsm")
        sub_asm.newObject("Assembly::JointGroup", "Joints")
        sub_asm.newObject("Part::Box", "Rail")
        sub.recompute()
        sub_path = os.path.join(self.tmp, "PfSub.FCStd")
        sub.saveAs(sub_path)
        top = App.newDocument("PfTop")
        top.saveAs(os.path.join(self.tmp, "PfTop.FCStd"))
        asm = top.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        link = pf.insert(asm, sub_path, rigid=True)
        pump()
        self.assertFalse(self.Gui.Command.get(pf.CMD_FLEXIBLE).isActive())  # nothing selected
        self.select(link)
        pump()
        got = [(x.Object.Name, x.SubElementNames) for x in self.Gui.Selection.getSelectionEx("", 0)]
        self.assertTrue(self.Gui.Command.get(pf.CMD_FLEXIBLE).isActive(), (link.Name, got))
        self.Gui.runCommand(pf.CMD_FLEXIBLE)
        pump()
        self.assertFalse(link.Rigid)
        self.Gui.runCommand(pf.CMD_FLEXIBLE)
        pump()
        self.assertTrue(link.Rigid)

    def test_off_switch(self):
        prefs = App.ParamGet(pf.PREFS_PATH)
        doc = App.newDocument("PfOff")
        doc.saveAs(os.path.join(self.tmp, "PfOff.FCStd"))
        pump()
        try:
            prefs.SetBool(pf.PREF, False)
            self.assertFalse(self.Gui.Command.get(pf.CMD_EXTERNAL).isActive())
            self.assertFalse(self.Gui.Command.get(pf.CMD_REPAIR).isActive())
            self.assertFalse(pf.repair_on_open())
        finally:
            prefs.RemBool(pf.PREF)
