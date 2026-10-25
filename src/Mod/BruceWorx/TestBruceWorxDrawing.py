# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for drawing title blocks (bruceworx_drawing, DRW-01): the field map, the
keep-what-the-user-typed rule, the template choice, the template variants, a
headless page filled from a part, and (GUI) the DRW-01 acceptance: a released vault part's
drawing fills every field by itself."""

import os
import re
import unittest

import FreeCAD

import bruceworx_drawing as drawing
from bruceworx import drawing_standards as ds


class TestTemplates(unittest.TestCase):
    def test_every_sheet_has_a_template_with_every_field(self):
        for sheet, (_label, width, height) in ds.SHEETS.items():
            path = drawing.template_path(sheet)
            self.assertTrue(os.path.isfile(path), path)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            size = f"{width:.2f}".rstrip("0").rstrip(".")
            self.assertIn(f'width="{size}mm"', text)
            fields = set(re.findall(r'freecad:editable="([^"]+)"', text))
            self.assertEqual(fields, set(ds.FIELDS), sheet)
            self.assertEqual(set(drawing.template_defaults(sheet)), set(ds.FIELDS))
            self.assertNotIn("BruceWorx</text>", text)  # no brand on the sheet

    def test_template_choice_from_standards(self):
        self.assertEqual(drawing.choose_sheet(ds.DrawingStandards()), "ANSI_B_Landscape")
        self.assertEqual(drawing.choose_sheet(ds.DrawingStandards(default_sheet="ISO_A3_Landscape")),
                         "ISO_A3_Landscape")
        self.assertEqual(drawing.choose_sheet(ds.DrawingStandards(), "ISO_A4_Portrait"), "ISO_A4_Portrait")
        self.assertEqual(drawing.choose_sheet(ds.DrawingStandards(), "nonsense"), "ANSI_B_Landscape")
        self.assertTrue(drawing.template_path("ISO_A3_Landscape").endswith("Templates/ISO_A3_Landscape.svg"))

    def test_customize_logo_and_projection(self):
        with open(drawing.template_path("ANSI_B_Landscape"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('<g id="bw-proj-first" display="none">', text)
        plain = drawing.customize_svg(text)
        self.assertEqual(plain, text)
        first = drawing.customize_svg(text, "data:image/png;base64,AAAA", first_angle=True)
        self.assertIn('<g id="bw-proj-third" display="none">', first)
        self.assertIn('<g id="bw-proj-first">', first)
        self.assertIn('xlink:href="data:image/png;base64,AAAA"', first)
        self.assertNotIn("bw-logo-slot", first)
        self.assertEqual(drawing.variant_of("ANSI_B_Landscape", None, False), "")
        self.assertNotEqual(drawing.variant_of("ANSI_B_Landscape", "x", False),
                            drawing.variant_of("ANSI_B_Landscape", "y", False))
        self.assertTrue(drawing.is_ours(type("T", (), {"Template": "/c/ANSI_B_Landscape-0123456789ab.svg"})()))


class TestFieldMap(unittest.TestCase):
    def test_title_block_fields(self):
        fields = ds.title_block_fields(
            ds.DrawingStandards(company="Lumen", projection="first"), None,
            {"title": "Plate", "part_number": "P-1", "revision": "A", "material": "PETG", "finish": "Raw",
             "mass_kg": 1.25},
            {"drawn": ("ann", "2026-10-20"), "approved": ("bob", "2026-10-22")},
            {"scale": "1:2", "sheet": "1 of 2"})
        self.assertEqual(fields["projection"], "First angle")
        self.assertEqual(fields["mass"], "1.25 kg")
        self.assertEqual((fields["approved_by"], fields["approved_date"]), ("bob", "2026-10-22"))
        self.assertNotIn("checked_by", fields)

    def test_keep_hand_typed(self):
        current = {"title": "Plate", "finish": "Bead blast", "mass": ""}
        texts, filled, kept = ds.merge_fields(current, {"title": "Base plate", "finish": "Raw", "mass": "5 g"},
                                              {"title": "Plate", "finish": "Raw"})
        self.assertEqual(texts, {"title": "Base plate", "finish": "Bead blast", "mass": "5 g"})
        self.assertEqual(kept, ["finish"])
        self.assertEqual(filled, {"title": "Base plate", "mass": "5 g"})


class TestPage(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TestBruceWorxDrawing")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_page_fills_from_the_part_and_keeps_typed(self):
        part = self.doc.addObject("App::Part", "Plate")
        box = self.doc.addObject("Part::Box", "Box")
        part.addObject(box)
        part.addProperty("App::PropertyString", "PartNumber", "BruceWorx")
        part.addProperty("App::PropertyString", "Description", "BruceWorx")
        part.addProperty("App::PropertyString", "Finish", "BruceWorx")
        part.PartNumber, part.Description, part.Finish = "BW-01-01001-001", "Base plate", "Raw"
        self.doc.recompute()
        self.assertIs(drawing.part_of(box), part)
        page = drawing.new_drawing(part, "ISO_A3_Landscape", root="")
        texts = page.Template.EditableTexts
        self.assertEqual(texts["part_number"], "BW-01-01001-001")
        self.assertEqual(texts["title"], "Base plate")
        self.assertEqual(texts["projection"], "Third angle")
        self.assertEqual(texts["sheet"], "1 of 1")
        self.assertTrue(texts["scale"])
        typed = dict(texts)
        typed["finish"] = "Bead blast"
        page.Template.EditableTexts = typed
        part.Finish, part.Description = "Black anodise", "Plate"
        report = drawing.fill_page(page, root="")
        texts = page.Template.EditableTexts
        self.assertEqual((texts["finish"], texts["title"]), ("Bead blast", "Plate"))
        self.assertEqual(report["kept"], ["finish"])
        self.assertEqual(list(page.BWHandTyped), ["finish"])

    def test_other_templates_are_left_alone(self):
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        template = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        template.Template = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates", "ASME",
                                         "ANSIB_Landscape.svg")
        page.Template = template
        self.assertIsNone(drawing.fill_page(page, root=""))
        self.assertNotIn(drawing.FILLED_PROP, page.PropertiesList)


@unittest.skipUnless(FreeCAD.GuiUp, "needs a GUI FreeCAD (New Part in Vault)")
class TestAcceptance(unittest.TestCase):
    """DRW-01: a part made in a vault, given a material and finish and released as A, gets a drawing
    whose title block fills every field by itself (company to signatures)."""

    def setUp(self):
        import shutil
        import tempfile

        import bruceworx_vaultclient as vc
        from bruceworx import vault_setup as vs

        self.vc = vc
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-drw01-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setup = vs.VaultSetup(name="Shop", folder=os.path.join(self.tmp, "vault"),
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")]))
        ref = vs.create_vault(setup, actor="bruce@mac").ref_path
        prefs = vc._prefs()
        old = prefs.GetString("VaultPath", "")
        prefs.SetString("VaultPath", ref)
        self.addCleanup(prefs.SetString, "VaultPath", old)

    def tearDown(self):
        for name, doc in list(FreeCAD.listDocuments().items()):
            if doc.FileName.startswith(self.tmp):
                FreeCAD.closeDocument(name)

    def test_released_part_fills_every_field(self):
        import Materials

        pn = self.vc.new_part("part", "Sample mounting plate")
        doc = FreeCAD.ActiveDocument
        part, body = doc.getObject("Part"), doc.getObject("Body")
        box = body.newObject("PartDesign::AdditiveBox", "Plate")
        box.Length, box.Width, box.Height = 60, 40, 5
        pla = next(m for m in Materials.MaterialManager().Materials.values()
                   if m.Name.startswith("PLA") and m.hasPhysicalProperty("Density"))
        body.ShapeMaterial = pla
        part.Finish = "As printed"
        doc.recompute()
        doc.save()
        s = self.vc.session()
        s.work.checkin(pn, "sample plate")
        s.vault.submit(pn)
        s.vault.approve(pn, note="Sample release")
        s.refresh()
        page = drawing.new_drawing(part)
        drawing.fill_page(page)
        texts = dict(page.Template.EditableTexts)
        lines = len([line for line in ds.tolerance_lines(drawing.standards()) if line.strip()])  # the rest blank
        empty = sorted(k for k, v in texts.items() if not str(v).strip() and k not in
                       {f"tol{i}" for i in range(lines + 1, 9)})
        self.assertEqual(empty, [], texts)
        self.assertEqual((texts["part_number"], texts["revision"], texts["title"]), (pn, "A", "Sample mounting plate"))
        self.assertEqual((texts["material"], texts["finish"]), (pla.Name, "As printed"))
        self.assertTrue(texts["mass"].endswith("g"))
        for role in ("drawn", "checked", "approved"):
            self.assertTrue(texts[f"{role}_by"] and texts[f"{role}_date"], role)
        self.assertEqual(texts["company"], "Shop")  # no company set: the vault's name


if __name__ == "__main__":
    unittest.main()
