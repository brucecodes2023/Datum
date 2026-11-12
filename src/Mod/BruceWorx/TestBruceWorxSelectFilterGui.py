# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests for the selection filter (bruceworx_selectfilter): the gate on the live selection, the top bar
box, the commands and the status readout. Needs the running app (FreeCAD.GuiUp)."""

import os
import shutil
import tempfile
import unittest

import FreeCAD

import bruceworx_selectfilter as sf


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestSelectFilterGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        cls.param = FreeCAD.ParamGet(sf.PREFS_PATH)
        cls.saved = cls.param.GetString(sf.PREF_NAME, sf.AUTO)
        cls.doc = FreeCAD.newDocument("SelectFilterGui")
        cls.box = cls.doc.addObject("Part::Box", "Box")
        cls.doc.recompute()
        sf.install()
        sf._make_bar()

    @classmethod
    def tearDownClass(cls):
        sf.set_mode(cls.param, cls.saved)
        sf.apply()
        FreeCAD.closeDocument(cls.doc.Name)

    def setUp(self):
        self.Gui.Selection.clearSelection()

    def select(self, mode, sub):
        sf.set_mode(self.param, mode)
        sf.apply()  # the pref observer would do it on the next tick
        self.Gui.Selection.addSelection(self.doc.Name, "Box", sub)
        return [s for s in self.Gui.Selection.getSelectionEx() for _ in (s.SubElementNames or [""])]

    def test_face_mode_takes_faces_only(self):
        self.assertEqual(len(self.select(sf.FACE, "Face1")), 1)
        self.Gui.Selection.clearSelection()
        self.assertEqual(self.select(sf.FACE, "Edge1"), [])
        self.assertEqual(self.select(sf.FACE, "Vertex1"), [])
        self.Gui.Selection.clearSelection()
        self.assertEqual(len(self.select(sf.FACE, "")), 1)  # a tree click on the whole object still selects

    def test_edge_and_vertex_modes(self):
        self.assertEqual(len(self.select(sf.EDGE, "Edge2")), 1)
        self.Gui.Selection.clearSelection()
        self.assertEqual(self.select(sf.EDGE, "Face1"), [])
        self.assertEqual(len(self.select(sf.VERTEX, "Vertex3")), 1)
        self.Gui.Selection.clearSelection()
        self.assertEqual(self.select(sf.VERTEX, "Edge1"), [])

    def test_leaving_an_element_mode_takes_the_gate_down(self):
        self.select(sf.FACE, "Face1")
        self.assertIsNotNone(sf._gate)
        self.Gui.Selection.clearSelection()
        self.assertEqual(len(self.select(sf.AUTO, "Edge1")), 1)
        self.assertIsNone(sf._gate)

    def test_box_follows_the_saved_mode(self):
        sf.set_mode(self.param, sf.EDGE)
        sf.apply()
        self.assertEqual(sf._combo.currentData(), sf.EDGE)
        self.assertEqual(sf._combo.currentText(), "Select: Edge")
        self.assertEqual(sf._label.text(), "Select Edge")

    def test_box_choice_saves_the_mode(self):
        sf._combo.setCurrentIndex(sf.MODES.index(sf.BODY))
        sf._combo.activated.emit(sf.MODES.index(sf.BODY))
        self.assertEqual(sf.get_mode(self.param), sf.BODY)

    def test_each_mode_has_a_command(self):
        for mode in sf.MODES:
            self.assertIsNotNone(self.Gui.Command.get(sf.COMMAND_PREFIX + mode), mode)
        self.Gui.runCommand(sf.COMMAND_PREFIX + sf.VERTEX)
        self.assertEqual(sf.get_mode(self.param), sf.VERTEX)


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestAssemblyPickOtherDocument(unittest.TestCase):
    """Component and Body under an assembly link to a part in another document: the observer reports the
    part's document, the selection entry is the assembly's (the 2 Nov "body is working as face" bug)."""

    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        cls.param = FreeCAD.ParamGet(sf.PREFS_PATH)
        cls.saved = cls.param.GetString(sf.PREF_NAME, sf.AUTO)
        cls.part_doc = FreeCAD.newDocument("PickPart")
        part = cls.part_doc.addObject("App::Part", "Part")
        body = cls.part_doc.addObject("PartDesign::Body", "Body")
        part.addObject(body)
        body.addObject(cls.part_doc.addObject("PartDesign::AdditiveBox", "Box"))
        cls.part_doc.recompute()
        cls.tmp = tempfile.mkdtemp()
        cls.part_doc.saveAs(os.path.join(cls.tmp, "PickPart.FCStd"))  # a link needs both documents saved
        cls.asm_doc = FreeCAD.newDocument("PickAssembly")
        cls.asm_doc.saveAs(os.path.join(cls.tmp, "PickAssembly.FCStd"))
        asm = cls.asm_doc.addObject("Assembly::AssemblyObject", "Assembly")
        link = cls.asm_doc.addObject("App::Link", "Link")
        link.LinkedObject = part
        asm.addObject(link)
        cls.asm_doc.recompute()

    @classmethod
    def tearDownClass(cls):
        sf.set_mode(cls.param, cls.saved)
        sf.apply()
        FreeCAD.closeDocument(cls.asm_doc.Name)
        FreeCAD.closeDocument(cls.part_doc.Name)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pick(self, mode):
        import bruceworx_picktrace as pt

        sf.set_mode(self.param, mode)
        sf.apply()
        self.Gui.Selection.clearSelection()
        self.Gui.Selection.addSelection(self.asm_doc.Name, "Assembly", "Link.Body.Box.Face2")
        pt._AssemblyPick()._resolve(self.part_doc.Name, "Face2", False)
        return [(s.Object.Name, n) for s in self.Gui.Selection.getSelectionEx("", 0) for n in s.SubElementNames]

    def test_body_takes_the_whole_solid(self):
        self.assertEqual(self.pick(sf.BODY), [("Assembly", "Link.Body.")])

    def test_component_takes_the_link(self):
        self.assertEqual(self.pick(sf.COMPONENT), [("Assembly", "Link.")])

    def test_auto_takes_the_part_file(self):
        self.assertEqual(sf.file_flags(self.asm_doc.getObject("Assembly"), "Link.Body.Box.Face2"), [True, False, False])
        self.assertEqual(self.pick(sf.AUTO), [("Assembly", "Link.")])


if __name__ == "__main__":
    unittest.main()
