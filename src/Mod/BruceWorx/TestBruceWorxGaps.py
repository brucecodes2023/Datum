# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI checks for the gaps found in real use (FND-08). Skipped without a GUI FreeCAD.

    run_gui_tests.py with BW_TEST_ONLY=TestBruceWorxGaps
"""

import unittest

try:
    import FreeCAD as App
except ImportError:  # plain Python
    App = None


def _gui():
    return App is not None and App.GuiUp


@unittest.skipUnless(_gui(), "needs a GUI FreeCAD")
class CreateAssemblyTests(unittest.TestCase):
    """Create Assembly works with a spreadsheet tab in front, also when the document has no 3D view."""

    def setUp(self):
        import FreeCADGui as Gui

        self.Gui = Gui
        self.doc = App.newDocument("BWGapsAssembly")
        self.sheet = self.doc.addObject("Spreadsheet::Sheet", "Parameters")
        self.doc.recompute()

    def tearDown(self):
        if self.Gui.Control.activeDialog():
            self.Gui.Control.closeDialog()
        gdoc = self.Gui.getDocument(self.doc.Name)
        if gdoc.getInEdit() is not None:
            gdoc.resetEdit()
        App.closeDocument(self.doc.Name)

    def _pump(self, n=10):
        for _ in range(n):
            self.Gui.updateGui()

    def _sheet_in_front(self, close_3d):
        from PySide import QtWidgets

        self.Gui.getDocument(self.doc.Name).setEdit(self.sheet)
        self._pump()
        area = self.Gui.getMainWindow().findChild(QtWidgets.QMdiArea)

        def kind(sub):
            widget = sub.widget()
            return widget.metaObject().className() if widget is not None else ""

        if close_3d:
            for sub in area.subWindowList():
                if kind(sub) == "Gui::View3DInventor":
                    sub.close()
            self._pump()
        if area.activeSubWindow() is None:  # the previous test module left no active sub-window
            for sub in area.subWindowList():
                if kind(sub) == "SpreadsheetGui::SheetView":
                    area.setActiveSubWindow(sub)
            self._pump()
        self.assertEqual(kind(area.activeSubWindow()), "SpreadsheetGui::SheetView")
        return area

    def _create(self, close_3d):
        area = self._sheet_in_front(close_3d)
        self.Gui.activateWorkbench("AssemblyWorkbench")
        self.Gui.runCommand("Assembly_CreateAssembly")
        self._pump(20)
        assemblies = [o for o in self.doc.Objects if o.TypeId == "Assembly::AssemblyObject"]
        self.assertEqual(len(assemblies), 1)
        import UtilsAssembly

        self.assertEqual(UtilsAssembly.activeAssembly(), assemblies[0])  # open for editing
        self.assertEqual(area.activeSubWindow().widget().metaObject().className(), "Gui::View3DInventor")

    def test_with_a_spreadsheet_in_front(self):
        self._create(close_3d=False)

    def test_with_only_a_spreadsheet_view(self):
        self._create(close_3d=True)


@unittest.skipUnless(_gui(), "needs a GUI FreeCAD")
class CounterboreTests(unittest.TestCase):
    """A counterbored Hole cuts into the plate from the face its sketch is on (top and bottom)."""

    def setUp(self):
        import Part
        import Sketcher  # noqa: F401 -- the sketch type

        V = App.Vector
        self.doc = App.newDocument("BWGapsHole")
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        sk = self.body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(self.body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        pts = [V(10, 10, 0), V(70, 10, 0), V(70, 50, 0), V(10, 50, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        self.pad = self.body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = sk
        self.pad.Length = 8
        self.doc.recompute()
        self.Part = Part

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _hole_on(self, z_normal):
        face = next(i for i, f in enumerate(self.pad.Shape.Faces, 1) if abs(f.normalAt(0, 0).z - z_normal) < 1e-9)
        sk = self.body.newObject("Sketcher::SketchObject", "HoleSketch")
        sk.AttachmentSupport = [(self.pad, f"Face{face}")]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        centre = sk.Placement.inverse().multVec(App.Vector(40, 30, 0))
        sk.addGeometry(self.Part.Circle(App.Vector(centre.x, centre.y, 0), App.Vector(0, 0, 1), 3))
        hole = self.body.newObject("PartDesign::Hole", "Hole")
        hole.Profile = sk
        hole.Diameter = 6
        hole.HoleCutType = "Counterbore"
        hole.HoleCutDiameter = 10
        hole.HoleCutDepth = 3
        hole.DepthType = "Dimension"
        hole.Depth = 6
        self.doc.recompute()
        return hole

    def _check(self, hole, head_z):
        self.assertNotIn("Invalid", hole.State)
        removed = self.pad.Shape.Volume - hole.Shape.Volume
        self.assertGreater(removed, 300.0)  # the head (3 mm of 10 mm) and the drill: into the plate
        tool = hole.AddSubShape.BoundBox
        self.assertGreaterEqual(tool.ZMin, -1e-6)  # nothing of the tool outside the plate
        self.assertLessEqual(tool.ZMax, 8 + 1e-6)
        # the wide head is at the face the sketch is on
        head = [f for f in hole.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder" and abs(f.Surface.Radius - 5) < 1e-6]
        self.assertTrue(head)
        gap = min(min(abs(f.BoundBox.ZMin - head_z), abs(f.BoundBox.ZMax - head_z)) for f in head)
        self.assertAlmostEqual(gap, 0.0, places=6)

    def test_from_the_top_face(self):
        self._check(self._hole_on(1.0), head_z=8.0)

    def test_from_the_bottom_face(self):
        self._check(self._hole_on(-1.0), head_z=0.0)


if __name__ == "__main__":
    unittest.main()
