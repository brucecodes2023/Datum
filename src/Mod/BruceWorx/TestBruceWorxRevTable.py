# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the revision table and release stamp (bruceworx_revtable, DRW-05).

Headless: FreeCADCmd -t TestBruceWorxRevTable. A throwaway vault; never the user's."""

import os
import shutil
import tempfile
import unittest

import FreeCAD

import bruceworx_drawing as drawing
import bruceworx_revtable as rt


class TestText(unittest.TestCase):
    def test_style_matches_the_templates(self):
        import importlib.util

        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_templates.py")
        src = os.path.join(os.path.dirname(os.path.abspath(rt.__file__)), "make_templates.py")
        path = next((p for p in (here, src) if os.path.isfile(p)), None)
        if path is None:
            self.skipTest("make_templates.py is in the source tree only")
        spec = importlib.util.spec_from_file_location("bw_make_templates", path)
        mt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mt)
        for name in ("FONT", "LINE", "LABEL_COLOR", "BLOCK_W", "RULE_W", "LABEL_SIZE", "PAD", "VALUE_SIZE"):
            self.assertEqual(getattr(rt._Style, name), getattr(mt, name), name)

    def test_pn_rev(self):
        self.assertEqual(rt.pn_rev("LE-01-01001-001", "A"), "LE-01-01001-001-A")
        self.assertEqual(rt.pn_rev("LE-01-01001-001", "02"), "LE-01-01001-001 WIP 02")
        self.assertEqual(rt.pn_rev("LE-01-01001-001", ""), "LE-01-01001-001")

    def test_table_grows_with_a_long_note(self):
        row = {"rev": "A", "description": "Initial release", "eco": "", "date": "2026-11-12", "by": "bruce"}
        _svg, w, h1 = rt.table_svg([row], "P-A")
        long = dict(row, description="Moved the mounting holes 2 mm outboard to clear the motor's "
                                     "connector and widened the slot for the cable tie")
        svg, w2, h2 = rt.table_svg([row, long], "P-B")
        self.assertEqual(w, w2)
        self.assertGreater(h2, h1 + rt.ROW_H)
        self.assertIn("REVISIONS", svg)
        self.assertIn("P-B", svg)


class TestOnThePage(unittest.TestCase):
    def setUp(self):
        from heuretes_freecad import vault_setup as vs

        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-drw05-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setup = vs.VaultSetup(name="Shop", folder=os.path.join(self.tmp, "vault"),
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")]))
        self.vault = vs.create_vault(setup, actor="bruce@mac")
        self.root = self.vault.root
        self.pn = "LE-01-01001-001"
        self.vault.create_item(self.pn, "part", "Motor bracket")
        self.doc = FreeCAD.newDocument("RevTable")
        self.addCleanup(FreeCAD.closeDocument, self.doc.Name)
        part = self.doc.addObject("App::Part", "Bracket")
        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 60, 40, 5  # big enough at 1:1 to reach the table's corner
        part.addObject(box)
        part.addProperty("App::PropertyString", "PartNumber", "BruceWorx")
        part.PartNumber = self.pn
        self.doc.recompute()
        self.page = drawing.new_drawing(part, "ANSI_A_Landscape", root=self.root)

    def views(self):
        return {getattr(v, rt.ROLE_PROP): v for v in self.page.Views if rt.ROLE_PROP in v.PropertiesList}

    def refill(self):
        report = drawing.fill_page(self.page, self.root)
        self.doc.recompute()
        return report["revision"]

    def test_prerelease_has_the_stamp_and_no_table(self):
        views = self.views()
        self.assertEqual(sorted(views), ["stamp"])
        self.assertEqual(list(views["stamp"].Text), ["PRELIMINARY", f"{self.pn} WIP 01"])
        self.assertEqual(self.refill(), [])  # nothing more to do

    def test_release_a_clears_the_stamp_and_adds_the_row(self):
        self.vault.submit(self.pn)
        self.vault.approve(self.pn, note="Initial release")
        self.assertEqual(sorted(self.refill()), ["-stamp", "table"])
        views = self.views()
        self.assertEqual(sorted(views), ["table"])
        self.assertNotIn(rt.STAMP_TEXT, " ".join(v.Label for v in self.page.Views))
        rows = rt.history(self.root, self.pn)[0]
        self.assertEqual([(r["rev"], r["description"], r["by"]) for r in rows], [("A", "Initial release", "bruce")])
        self.assertTrue(rows[0]["date"])
        table = views["table"]
        self.assertIn(f"{self.pn}-A", table.Symbol)
        # its top-right corner in the frame's corner
        width, height = float(self.page.Template.Width), float(self.page.Template.Height)
        size = table.BW_RevSize
        self.assertAlmostEqual(table.X.Value + size.x / 2, width - rt.FRAME_MARGIN, 6)
        self.assertAlmostEqual(table.Y.Value + size.y / 2, height - rt.FRAME_MARGIN, 6)
        # the views were moved clear of it (the title block leaves room on this sheet)
        boxes = [b for b in (rt._extent(v) for v in rt.part_views(self.page)) if b is not None]
        self.assertEqual(len(rt.part_views(self.page)), 3)
        # headless, TechDraw has not drawn the views yet (no visible edges): checked in the app
        table_left, table_bottom = table.X.Value - size.x / 2, table.Y.Value - size.y / 2
        for left, bottom, right, top in boxes:
            self.assertTrue(right <= table_left or top <= table_bottom - rt.GAP + 1e-6, (left, bottom, right, top))
            self.assertGreaterEqual(bottom, rt.FRAME_MARGIN + rt.TITLE_BLOCK_H + rt.GAP - 1e-6)

    def test_eco_adds_rev_b_and_a_moved_table_stays(self):
        self.vault.submit(self.pn)
        self.vault.approve(self.pn, note="Initial release")
        self.refill()
        table = self.views()["table"]
        table.X, table.Y = table.X.Value - 30, table.Y.Value - 20  # the user drags it
        right = table.X.Value + table.BW_RevSize.x / 2
        top = table.Y.Value + table.BW_RevSize.y / 2
        ecr = self.vault.raise_ecr(None, "Holes foul the motor connector", [self.pn])
        self.vault.decide_ecr(ecr.number, True, reason="Agreed")
        eco = self.vault.open_eco(None, [ecr.number])
        # B is lettered while it is worked on: no PRELIMINARY stamp (that is for numbered revisions),
        # and the table still shows the released A
        self.refill()
        self.assertEqual(sorted(self.views()), ["table"])
        self.assertIn(f"{self.pn}-A", self.views()["table"].Symbol)
        self.vault.submit(self.pn)
        self.vault.release_eco(eco.number, note="Mounting holes moved outboard")
        self.refill()
        views = self.views()
        self.assertEqual(sorted(views), ["table"])
        rows = rt.history(self.root, self.pn)[0]
        self.assertEqual([(r["rev"], r["eco"]) for r in rows], [("A", ""), ("B", eco.number)])
        self.assertIn("Mounting holes moved outboard", views["table"].Symbol)
        self.assertIn(f"{self.pn}-B", views["table"].Symbol)
        self.assertAlmostEqual(views["table"].X.Value + views["table"].BW_RevSize.x / 2, right, 6)
        self.assertAlmostEqual(views["table"].Y.Value + views["table"].BW_RevSize.y / 2, top, 6)

    def test_off_switch(self):
        prefs = FreeCAD.ParamGet(rt.PREFS_PATH)
        prefs.SetBool(rt.PREF, False)
        self.addCleanup(prefs.RemBool, rt.PREF)
        self.page.Document.removeObject(self.views()["stamp"].Name)
        self.assertEqual(self.refill(), [])
        self.assertEqual(self.views(), {})


if __name__ == "__main__":
    unittest.main()
