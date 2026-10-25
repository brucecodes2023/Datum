# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inspect maps in the 3D view (MOD-11). Needs the GUI (run_gui_tests.py); skipped in FreeCADCmd.

A cantilever on the bed: the overhang map finds its 200 mm^2 underside, draws in its own nodes over the part's
hidden faces, lets a click through to the part, leaves the document and its undo stack alone, and puts
everything back when the panel closes.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD as App

try:
    import FreeCADGui as Gui

    HAVE_GUI = App.GuiUp
except ImportError:
    HAVE_GUI = False


def pump(n=5):
    import time

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(HAVE_GUI, "needs the GUI")
class TestInspectMaps(unittest.TestCase):
    def setUp(self):
        import Part

        import bruceworx_analysis_gui as g

        self.g = g
        g.install()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        self.doc = App.newDocument("BWInspect")
        shape = Part.makeBox(10, 10, 20).fuse(Part.makeBox(30, 10, 5, App.Vector(0, 0, 15))).removeSplitter()
        self.obj = self.doc.addObject("Part::Feature", "Cantilever")
        self.obj.Shape = shape
        self.doc.recompute()
        Gui.ActiveDocument.ActiveView.viewIsometric()
        Gui.SendMsgToActiveView("ViewFit")
        pump()

    def tearDown(self):
        self.g.close()
        App.closeDocument(self.doc.Name)

    def _scene_has_map(self):
        """A search by name through the whole scene (the map sits in the viewer's ObjectGroup, which a
        section moves under its passes; SoNode.getByName answers for the last node named, not this scene)."""
        from pivy import coin

        search = coin.SoSearchAction()
        search.setName(self.g.NODE_NAME)
        search.setInterest(coin.SoSearchAction.FIRST)
        search.setSearchingAll(True)
        search.apply(Gui.ActiveDocument.ActiveView.getSceneGraph())
        return search.getPath() is not None

    def test_the_map_is_cut_by_a_section(self):
        """The map hangs in the viewer's ObjectGroup, which the section passes clip (known gap of 25 Oct:
        section planes did not cut the map)."""
        import bruceworx_section as sec

        g = self.g
        session = g.show("overhang", objects=[self.obj])
        pump()
        root = Gui.ActiveDocument.ActiveView.getSceneGraph()
        groups = [root.getChild(i) for i in range(root.getNumChildren()) if root.getChild(i).getName().getString() == g.OBJECT_GROUP]
        self.assertEqual(len(groups), 1)
        self.assertGreaterEqual(groups[0].findChild(session.root), 0)
        self.assertLess(root.findChild(session.root), 0)
        before = self.picture()
        plane = sec.make(self.doc, "XZ")
        plane.Offset = 5.0
        self.doc.recompute()
        pump()
        self.assertTrue(self._scene_has_map())
        self.assertNotEqual(before, self.picture())  # the cut shows: part and map alike
        g.close()
        pump()
        self.assertFalse(self._scene_has_map())

    def picture(self):
        import os
        import tempfile

        from PySide import QtGui

        path = os.path.join(tempfile.gettempdir(), "bw-inspect-cut.png")
        Gui.ActiveDocument.ActiveView.saveImage(path, 200, 150, "Current")
        image = QtGui.QImage(path)
        return bytes(image.constBits())[: image.sizeInBytes()] if not image.isNull() else b""

    def test_overhang_map_round_trip(self):
        g = self.g
        undo = self.doc.UndoCount
        session = g.show("overhang", objects=[self.obj])
        pump()
        self.assertIsNotNone(session)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self._scene_has_map())
        self.assertIn(self.obj.Name, {o.Name for o in session.hidden})
        result = session.results[0][1]
        self.assertAlmostEqual(result.stats["support_area"], 200.0, places=6)
        self.assertIn("Needs support", session.summary())
        # a click on the arm's top still finds the part under the map
        view = Gui.ActiveDocument.ActiveView
        x, y = view.getPointOnScreen(App.Vector(25, 5, 20))
        info = view.getObjectInfo((int(x), int(y)))
        self.assertIsNotNone(info)
        self.assertEqual(info["Object"], self.obj.Name)
        # every map draws; the settings change the numbers
        panel = g._state["panel"]
        panel.widgets["overhang"]["angle"].setValue(80.0)
        panel.update()
        self.assertAlmostEqual(session.results[0][1].stats["support_area"], 200.0, places=6)  # a flat ceiling
        for mode, _text in g.MODES:
            panel.mode.setCurrentIndex([k for k, _t in g.MODES].index(mode))
            panel.update()
            pump(2)
            self.assertTrue(self._scene_has_map(), mode)
        self.assertEqual(self.doc.UndoCount, undo)  # nothing written to the document
        g.close()
        pump()
        self.assertFalse(self._scene_has_map())
        self.assertFalse(Gui.Control.activeDialog())
        sep = g._flat_root(self.obj)
        names = [sep.getChild(i).getName().getString() for i in range(sep.getNumChildren())]
        self.assertNotIn(g.HIDE_NAME, names)

    def test_follows_a_recompute(self):
        import Part

        session = self.g.show("thickness", objects=[self.obj])
        pump()
        self.assertIsNone(session.results[0][1].stats["thinnest"])  # 5 mm and more: beyond the cap
        self.obj.Shape = Part.makeBox(30, 30, 0.6)
        self.obj.touch()
        self.doc.recompute()
        pump(10)
        self.assertAlmostEqual(session.results[0][1].stats["thinnest"], 0.6, delta=0.01)

    def test_commands_are_registered(self):
        for name, *_rest in self.g.COMMANDS.values():
            self.assertIsNotNone(Gui.Command.get(name), name)
        self.assertIsNotNone(Gui.Command.get("BruceWorx_MassProperties"))


if __name__ == "__main__":
    unittest.main()
