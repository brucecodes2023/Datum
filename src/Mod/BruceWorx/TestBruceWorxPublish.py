# SPDX-License-Identifier: LGPL-2.1-or-later
"""Publish TechDraw pages (bruceworx_publish, DRW-08): one PDF per page, and a DXF
from TechDraw.writeDXFPage, which runs headless.

Run: FreeCADCmd -P <this directory> -t TestBruceWorxPublish
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD

import bruceworx_publish as publish

_TEMPLATE = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates", "ISO",
                         "A4_Landscape_TD.svg")


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TestBruceWorxPublish")
        self.tmp = tempfile.mkdtemp(prefix="bw-drw08-")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_each_page_gets_a_pdf(self):
        self._page("Page", with_view=True)
        self._page("Notes", with_view=False)
        self.doc.addObject("Part::Box", "NotAPage")
        written = publish.publish_pages(self.doc, self.tmp)
        self.assertEqual([item["name"] for item in written], ["Page", "Notes"])
        pdfs = []
        for item in written:
            pdf = item["pdf"]
            self.assertTrue(os.path.isfile(pdf), pdf)
            self.assertGreater(os.path.getsize(pdf), 0)
            with open(pdf, "rb") as handle:
                data = handle.read()
            self.assertTrue(data.startswith(b"%PDF-"), pdf)
            # A4 landscape is 297 mm by 210 mm, 842 pt by 595 pt.
            self.assertRegex(data, br"/MediaBox \[0 0 842\.\d+ 595\.\d+\]")
            self.assertTrue(pdf.startswith(self.tmp))
            pdfs.append(os.path.basename(pdf))
            dxf = item["dxf"]
            self.assertTrue(dxf and os.path.isfile(dxf) and os.path.getsize(dxf) > 0, dxf)
        self.assertEqual(sorted(pdfs), ["Notes.pdf", "Page.pdf"])
        sizes = {item["name"]: os.path.getsize(item["pdf"]) for item in written}
        self.assertGreater(sizes["Page"], sizes["Notes"])  # the part view is on the sheet

    def _page(self, name, with_view):
        page = self.doc.addObject("TechDraw::DrawPage", name)
        template = self.doc.addObject("TechDraw::DrawSVGTemplate", name + "Template")
        template.Template = _TEMPLATE
        page.Template = template
        if with_view:
            box = self.doc.addObject("Part::Box", name + "Box")
            view = self.doc.addObject("TechDraw::DrawViewPart", name + "View")
            page.addView(view)
            view.Source = [box]
            view.Direction = FreeCAD.Vector(0, 0, 1)
            view.X, view.Y = 148, 105
        return page


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False, argv=["TestBruceWorxPublish"])
    if not result.result.wasSuccessful():
        raise SystemExit(1)
