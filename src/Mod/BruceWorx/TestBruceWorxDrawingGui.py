# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI regressions for TechDraw pages scripted by the drawing module (run by run_gui_tests.py,
skipped headless)."""

import os
import shutil
import tempfile
import unittest

import FreeCAD

import bruceworx_drawing as drawing

try:
    import FreeCADGui as Gui
except ImportError:  # headless
    Gui = None


@unittest.skipUnless(Gui is not None and FreeCAD.GuiUp, "needs the GUI")
class TestPageGui(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TestBruceWorxDrawingGui")
        part = self.doc.addObject("App::Part", "Plate")
        box = self.doc.addObject("Part::Box", "Box")
        part.addObject(box)
        self.doc.recompute()
        self.page = drawing.new_drawing(part, "ISO_A3_Landscape", root="")
        self.tmp = tempfile.mkdtemp(prefix="bw-drw-gui-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_removed_view_then_double_click_survives(self):
        group = self.page.Views[0]
        group.removeProjection("Right")
        self.doc.recompute()
        self.page.ViewObject.doubleClicked()
        self.page.ViewObject.show()
        self.doc.recompute()
        self.assertEqual(sorted(v.Type for v in group.Views), ["Front", "Top"])

    def test_unopened_page_exports_template_and_views(self):
        import TechDrawGui

        full = os.path.join(self.tmp, "full.pdf")
        TechDrawGui.exportPageAsPdf(self.page, full)  # never opened
        self.assertGreater(os.path.getsize(full), 0)
        # the reference: the same page after it was opened
        self.page.ViewObject.doubleClicked()
        self.page.ViewObject.show()
        opened = os.path.join(self.tmp, "opened.pdf")
        TechDrawGui.exportPageAsPdf(self.page, opened)
        a, b = os.path.getsize(full), os.path.getsize(opened)
        self.assertGreater(a, 20000, "the template and views are missing from the PDF")
        self.assertLess(abs(a - b), 0.2 * b)
