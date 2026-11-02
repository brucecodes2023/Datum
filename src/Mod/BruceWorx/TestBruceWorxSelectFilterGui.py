# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests for the selection filter (bruceworx_selectfilter): the gate on the live selection, the top bar
box, the commands and the status readout. Needs the running app (FreeCAD.GuiUp)."""

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
        self.assertEqual(self.select(sf.FACE, ""), [])

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


if __name__ == "__main__":
    unittest.main()
