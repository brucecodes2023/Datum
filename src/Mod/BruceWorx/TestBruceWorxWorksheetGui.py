# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the worksheet tab (bruceworx_worksheet_gui, MOD-15 step 4, MOD-17 by hand).

The text helpers run headless; the tab needs the running app: FreeCAD -t TestBruceWorxWorksheetGui"""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD

import bruceworx_worksheet as bw
import bruceworx_worksheet_gui as wg

SPEC = """---
title: Bracket spec
describes: WsGuiBracket
outputs: [{"object": "Body", "file": "out/plate.3mf"}]
---
## Load
The arm carries $F$ = {=F_load}. Bending stress \\[ \\sigma = \\frac{6 F L}{b t^2} \\]

```calc
F_load = 200 N
t_plate = max(3 mm, ceil(sqrt(6 * F_load * L_arm / (b_plate * sigma_allow)) / 1 mm) * 1 mm)  => Parameters
S_f = sigma_allow * b_plate * t_plate^2 / (6 * F_load * L_arm)   check S_f >= 1 "bends"
```
"""


def pump(n=6):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


class TestText(unittest.TestCase):
    def test_tex_name(self):
        self.assertEqual(wg.tex_name("T_out"), "T_{out}")
        self.assertEqual(wg.tex_name("sigma_F"), "\\sigma_{F}")
        self.assertEqual(wg.tex_name("alpha"), "\\alpha")
        self.assertEqual(wg.tex_name("z_min"), "z_{min}")
        self.assertEqual(wg.tex_name("load"), "\\mathrm{load}")

    def test_template_parses(self):
        doc = bw.parse(wg.template("Bracket.FCStd", "Bracket"))
        self.assertEqual(doc.header["describes"], "Bracket.FCStd")
        self.assertEqual([b.kind for b in doc.blocks], ["text", "calc"])

    def test_markdown_values_and_math(self):
        ws = bw.Worksheet("```calc\nF_load = 200 N\n```")
        ws.evaluate()
        seen = []
        md = wg.text_markdown("F is {=F_load}, unknown {=G}; $F = m a$ and \\[ x^2 \\]", ws.results,
                              lambda t, d: seen.append((t, d)) or f"bwmath:{len(seen)}")
        self.assertIn("**200 N**", md)
        self.assertIn("**{G?}**", md)
        self.assertEqual(seen, [("F = m a", False), ("x^2", True)])  # in reading order
        self.assertIn("\n\n" + wg.token("bwmath:2") + "\n\n", md)  # a display formula on its own line
        self.assertEqual(wg.images_in("a " + wg.token("bwmath:1") + " b", ["bwmath:1"]), 'a <img src="bwmath:1"/> b')

    def test_math_png(self):
        png = wg.math_png("\\frac{a}{b}")
        self.assertTrue(png.startswith(b"\x89PNG"))


@unittest.skipUnless(FreeCAD.GuiUp, "needs the running app")
class TestTab(unittest.TestCase):
    def setUp(self):
        import bruceworx_parameters as bp

        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-wsgui-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.model = FreeCAD.newDocument("WsGuiBracket")
        sheet = bp.make_sheet(self.model)
        for row, (alias, content) in enumerate((("L_arm", "60 mm"), ("b_plate", "30 mm"),
                                                ("sigma_allow", "40 MPa")), start=2):  # test inputs only
            sheet.set(f"A{row}", alias)
            sheet.set(f"B{row}", content)
            sheet.setAlias(f"B{row}", alias)
        body = self.model.addObject("PartDesign::Body", "Body")
        self.box = self.model.addObject("PartDesign::AdditiveBox", "Plate")
        body.addObject(self.box)
        self.box.Length, self.box.Width = 60, 30
        self.model.recompute()
        self.sheet = sheet
        self.path = os.path.join(self.tmp, "spec.bwsheet")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(SPEC)

    def tearDown(self):
        wg._state["closing_quietly"] = True
        try:
            for page in list(wg._state["pages"].values()):
                sub = page.parentWidget()
                if sub is not None:
                    sub.close()
            pump()
        finally:
            wg._state["closing_quietly"] = False
        if self.model.Name in FreeCAD.listDocuments():
            FreeCAD.closeDocument(self.model.Name)

    def test_open_apply_live_save_pdf(self):
        import FreeCADGui as Gui

        self.assertIn("bruceworx_worksheet_gui", FreeCAD.getImportType("bwsheet") or [])
        self.assertIsNotNone(Gui.Command.get("BruceWorx_NewWorksheet"))
        page = wg.open_worksheet(self.path)
        pump()
        self.assertIs(wg.open_worksheet(self.path), page)  # one tab per file
        text = page.view.toPlainText()
        self.assertIn("200 N", text)
        self.assertIn("8 mm", text)  # sqrt(6*200*60/(30*40)) = 7.75 -> 8
        self.assertIn("bends", text)
        self.assertIn("Model WsGuiBracket", page.chip.text())
        self.assertGreaterEqual(len(page._formulas), 3)
        self.assertGreaterEqual(page.view.toHtml().count("<img"), 5)  # 3 names, F and the display formula
        self.assertTrue(all(url for url in page._formulas.values()), page._formulas)
        # Apply: the sheet gets t_plate, the model is rebuilt, the print file written
        self.box.setExpression("Height", "Parameters.t_plate")
        report = page.apply()
        self.assertEqual(report["written"], ["t_plate"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", "plate.3mf")))
        self.assertEqual(self.box.Height.Value, 8.0)
        # by hand in the spec: the load goes up
        page.editor.setPlainText(page.editor.toPlainText().replace("F_load = 200 N", "F_load = 450 N"))
        pump()
        page.reevaluate()
        self.assertIn("12 mm", page.view.toPlainText())
        self.assertEqual(self.box.Height.Value, 8.0)  # typing writes nothing
        self.assertTrue(page.dirty())
        page.apply()
        self.assertEqual(self.box.Height.Value, 12.0)
        # by hand in the Parameters sheet: live
        self.sheet.set(self.sheet.getCellFromAlias("sigma_allow"), "80 MPa")
        self.model.recompute()
        for _ in range(30):
            pump(2)
            if "9 mm" in page.view.toPlainText():
                break
        self.assertIn("9 mm", page.view.toPlainText())
        self.assertEqual(self.box.Height.Value, 9.0)
        # save: results in the header; the tab is clean
        page.save()
        self.assertFalse(page.dirty())
        saved = bw.Worksheet.load(self.path)
        self.assertEqual(saved.doc.header["results"]["t_plate"], "9 mm")
        self.assertIn("F_load = 450 N", open(self.path, encoding="utf-8").read())
        pdf = page.write_pdf(os.path.join(self.tmp, "spec.pdf"))
        self.assertGreater(os.path.getsize(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
