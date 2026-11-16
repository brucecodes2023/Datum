# SPDX-License-Identifier: LGPL-2.1-or-later
"""A vault's drawing standards (bruceworx.drawing_standards, DRW-01): defaults, the JSON
file, the tolerance block, the vault CSV's "standard" rows, journal signatures and the
keep-what-the-user-typed rule of the title-block filler."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import drawing_standards as ds, vault_setup as vs  # noqa: E402
from heuretes_freecad.pdm_folder import FolderVault  # noqa: E402


def errors(issues):
    return [i.text for i in issues if i.level == "error"]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-drw-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class DefaultsAndValues(unittest.TestCase):
    def test_defaults_are_the_plan(self):
        s = ds.DrawingStandards()
        self.assertEqual((s.standard, s.projection, s.units, s.default_sheet, s.tolerance_mode),
                         ("ASME Y14.5-2018", "third", "mm", "ANSI_B_Landscape", "decimal"))
        self.assertEqual((s.tol_1_place, s.tol_2_place, s.tol_3_place, s.tol_angle), (0.5, 0.25, 0.1, 1.0))
        self.assertEqual(ds.validate(s), [])

    def test_inch_profile_defaults_and_view(self):
        s = ds.DrawingStandards()
        self.assertEqual((s.in_default_sheet, s.in_tol_1_place, s.in_tol_2_place, s.in_tol_3_place, s.in_tol_angle),
                         ("ANSI_B_Landscape", 0.1, 0.01, 0.005, 1.0))
        mm = ds.profile(s, "mm")
        self.assertEqual((mm.units, mm.default_sheet, mm.tol_1_place), ("mm", "ANSI_B_Landscape", 0.5))
        s.default_sheet, s.standard, s.tolerance_mode = "ISO_A3_Landscape", "ISO GPS", "iso2768"
        s.in_default_sheet, s.in_tol_3_place = "ANSI_C_Landscape", 0.002
        inch = ds.profile(s, "in")
        self.assertEqual((inch.units, inch.default_sheet, inch.standard, inch.tolerance_mode),
                         ("in", "ANSI_C_Landscape", "ASME Y14.5-2018", "decimal"))
        self.assertEqual((inch.tol_1_place, inch.tol_2_place, inch.tol_3_place), (0.1, 0.01, 0.002))
        self.assertEqual(ds.tolerance_lines(inch)[:2],
                         ["X.X ±.1  ·  X.XX ±.01", "X.XXX ±.002  ·  Angles ±1°"])
        self.assertEqual(ds.tolerance_lines(ds.profile(s, "mm"))[0], "ISO 2768-m")
        self.assertEqual(ds.title_block_fields(inch)["units"], "inch")
        self.assertEqual(ds.profile(s).units, s.units)  # the vault's default
        self.assertEqual((ds.dimension_format("in"), ds.dimension_format("mm")), ("%.3f", "%.2w"))
        self.assertEqual((ds.dimension_format("in", True), ds.dimension_format("mm", True)), ("%+.3f", "%+.2w"))
        self.assertEqual((ds.tolerance_text(0.005, "in"), ds.tolerance_text(0.5, "mm"), ds.tolerance_text(1, "in")),
                         (".005", "0.5", "1"))

    def test_version_1_inch_vault_moves_its_tolerances_to_the_inch_profile(self):
        old = {"format": ds.FORMAT, "version": 1, "settings": {
            "units": "in", "default_sheet": "ANSI_C_Landscape", "tol_1_place": 0.03, "tol_2_place": 0.01,
            "tol_3_place": 0.005, "tol_angle": 0.5}}
        s, issues = ds.from_dict(old)
        self.assertEqual(issues, [])
        self.assertEqual((s.in_default_sheet, s.in_tol_1_place, s.in_tol_2_place, s.in_tol_3_place, s.in_tol_angle),
                         ("ANSI_C_Landscape", 0.03, 0.01, 0.005, 0.5))
        self.assertEqual((s.default_sheet, s.tol_1_place, s.tol_angle), ("ANSI_B_Landscape", 0.5, 1.0))
        # an old inch vault that kept the defaults keeps the inch defaults (0.5 in was never meant)
        s2, _i = ds.from_dict({"version": 1, "settings": {"units": "in"}})
        self.assertEqual(s2, ds.DrawingStandards(units="in"))
        # a mm vault, and a version-2 file, are left as they are
        s3, _i = ds.from_dict({"version": 1, "settings": {"units": "mm", "tol_1_place": 0.3}})
        self.assertEqual((s3.tol_1_place, s3.in_tol_1_place), (0.3, 0.1))
        s4, _i = ds.from_dict({"version": 2, "settings": {"units": "in", "tol_1_place": 0.3}})
        self.assertEqual((s4.tol_1_place, s4.in_tol_1_place), (0.3, 0.1))
        self.assertEqual(ds.to_dict(s4)["version"], 2)

    def test_loose_text_for_choices(self):
        s = ds.DrawingStandards()
        for key, text, value in [
            ("projection", "First angle", "first"), ("projection", "3rd", "third"),
            ("tolerance_mode", "ISO 2768", "iso2768"), ("tolerance_mode", "Per drawing", "per_drawing"),
            ("default_sheet", "ANSI C landscape (22 × 17 in)", "ANSI_C_Landscape"),
            ("default_sheet", "a3", "ISO_A3_Landscape"), ("default_sheet", "A4 portrait", "ISO_A4_Portrait"),
            ("default_sheet", "B", "ANSI_B_Landscape"), ("units", "inches", "in"),
            ("in_default_sheet", "ANSI D", "ANSI_D_Landscape"), ("in_tol_3_place", "±.002", 0.002),
            ("iso2768_class", "c (coarse)", "c"), ("standard", "iso gps", "ISO GPS"),
            ("tol_2_place", "±0,3", 0.3), ("tol_angle", "0.5°", 0.5),
        ]:
            self.assertIsNone(ds.set_text(s, key, text), (key, text))
            self.assertEqual(getattr(s, key), value, (key, text))

    def test_bad_values_change_nothing(self):
        s = ds.DrawingStandards()
        self.assertIsNotNone(ds.set_text(s, "projection", "sideways"))
        self.assertIsNotNone(ds.set_text(s, "tol_1_place", "-1"))
        self.assertIsNotNone(ds.set_text(s, "tol_1_place", "lots"))
        self.assertIsNotNone(ds.set_text(s, "colour", "red"))
        self.assertEqual(s, ds.DrawingStandards())
        s.logo = "logo.gif"
        self.assertTrue(errors(ds.validate(s)))

    def test_numbers_print_cleanly(self):
        self.assertEqual([ds.format_number(v) for v in (0.5, 1.0, 0.25, 0.1, 0.30000000000000004)],
                         ["0.5", "1", "0.25", "0.1", "0.3"])
        self.assertEqual(ds.format_mass(0.135), "135 g")
        self.assertEqual(ds.format_mass(2.5), "2.5 kg")
        self.assertEqual(ds.format_mass(0.0042), "4.2 g")
        self.assertEqual(ds.format_mass(0), "")
        self.assertEqual(ds.format_mass(None), "")
        self.assertEqual([ds.scale_text(v) for v in (1, 0.5, 2, 0.2, 0.75)], ["1:1", "1:2", "2:1", "1:5", "3:4"])


class ToleranceBlock(unittest.TestCase):
    def test_decimal_places(self):
        lines = ds.tolerance_lines(ds.DrawingStandards())
        self.assertEqual(lines, ["X.X ±0.5  ·  X.XX ±0.25", "X.XXX ±0.1  ·  Angles ±1°", "", ""])

    def test_iso_2768_and_notes(self):
        s = ds.DrawingStandards(tolerance_mode="iso2768", iso2768_class="f", surface_finish="3.2",
                                break_edges="0.2-0.5")
        self.assertEqual(ds.tolerance_lines(s), ["ISO 2768-f", "Linear and angular: class f",
                                                 "Surface finish Ra 3.2 µm", "Break sharp edges 0.2-0.5"])

    def test_per_drawing(self):
        s = ds.DrawingStandards(tolerance_mode="per_drawing")
        self.assertEqual(ds.tolerance_lines(s)[0], "As noted on the drawing")

    def test_standard_note(self):
        self.assertEqual(ds.standard_note(ds.DrawingStandards()), "Interpret per ASME Y14.5-2018")
        self.assertIn("ISO 8015", ds.standard_note(ds.DrawingStandards(standard="ISO GPS")))


class FileTests(Tmp):
    def test_missing_file_gives_defaults(self):
        self.assertEqual(ds.load(self.tmp), ds.DrawingStandards())
        self.assertFalse(ds.exists(self.tmp))

    def test_round_trip_keeps_unknown_keys(self):
        s = ds.DrawingStandards(company="Lumen Engineering", projection="first", tol_2_place=0.3)
        s.extra["future_key"] = 42
        path = ds.save(self.tmp, s)
        self.assertEqual(os.listdir(self.tmp), [ds.STANDARDS_FILE])
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["format"], ds.FORMAT)
        self.assertEqual(data["settings"]["future_key"], 42)
        again = ds.load(self.tmp)
        self.assertEqual(again, s)
        self.assertEqual(again.extra, {"future_key": 42})

    def test_bad_value_in_file_falls_back(self):
        with open(ds.standards_path(self.tmp), "w", encoding="utf-8") as handle:
            json.dump({"format": ds.FORMAT, "settings": {"projection": "upside", "units": "in"}}, handle)
        s, issues = ds.load_with_issues(self.tmp)
        self.assertEqual((s.projection, s.units), ("third", "in"))
        self.assertEqual(len(issues), 1)
        with open(ds.standards_path(self.tmp), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(ds.load(self.tmp), ds.DrawingStandards())

    def test_save_refuses_errors(self):
        with self.assertRaises(ValueError):
            ds.save(self.tmp, ds.DrawingStandards(projection="sideways"))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_logo_is_copied_into_the_vault(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "Logo.PNG"), "wb") as handle:
            handle.write(b"\x89PNG")
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        name = ds.import_logo(vault, os.path.join(src, "Logo.PNG"))
        self.assertEqual(name, "drawing_logo.png")
        s = ds.DrawingStandards(logo=name)
        self.assertEqual(ds.logo_path(vault, s), os.path.join(vault, "drawing_logo.png"))
        self.assertEqual(ds.validate(s, vault), [])
        with self.assertRaises(ValueError):
            ds.import_logo(vault, os.path.join(src, "x.bmp"))


class CsvTests(Tmp):
    def test_rows_round_trip_through_the_vault_csv(self):
        settings = vs.example_settings()
        settings.standards["projection"] = "first"
        settings.standards["company"] = "Lumen, Inc."
        settings.standards["in_tol_3_place"] = "0.002"
        text = vs.csv_template(settings)
        self.assertIn("standard,projection,first,", text)
        self.assertIn("standard,in_tol_3_place,0.002,", text)
        self.assertIn('standard,company,"Lumen, Inc."', text)
        parsed, issues = vs.parse_csv(text)
        self.assertEqual(issues, [])
        self.assertEqual(parsed, settings)

    def test_old_csv_without_standard_rows_still_parses(self):
        text = "table,code,name,description\nprogram,BW,,\ndocument_type,01,Part,\n"
        settings, issues = vs.parse_csv(text)
        self.assertEqual(errors(issues), [])
        self.assertEqual(settings.standards, {})

    def test_loose_and_bad_rows(self):
        text = "\n".join([
            "table,code,name,retired",
            "program,BW,,",
            "document_type,01,Part,",
            "Drawing standard,Default Sheet,a3,",
            "standard,projection,sideways,",    # 5 bad value
            "standard,colour,red,",             # 6 unknown setting
            "standard,default_sheet,B,",        # 7 repeat
            "standard,units,in,yes",            # 8 retired ignored
        ])
        settings, issues = vs.parse_csv(text)
        found = errors(issues)
        for line in (5, 6, 7):
            self.assertTrue(any(t.startswith(f"Line {line}:") for t in found), (line, found))
        self.assertEqual(settings.standards, {"default_sheet": "ISO_A3_Landscape", "units": "in"})
        self.assertTrue(any(i.level == "warning" and i.text.startswith("Line 8:") for i in issues))

    def test_vault_settings_save_and_load(self):
        vault = vs.create_vault(vs.VaultSetup(name="Lumen", folder=os.path.join(self.tmp, "v")), actor="a")
        self.assertTrue(ds.exists(vault.root))  # a new vault has the defaults on file
        loaded = vs.load_settings(vault.root)
        self.assertEqual(loaded.standards["default_sheet"], "ANSI_B_Landscape")
        loaded.standards["tolerance_mode"] = "iso2768"
        issues = vs.settings_change_issues(vault.root, loaded)
        self.assertTrue(any("tolerance_mode changes" in i.text for i in issues))
        vs.save_settings(vault.root, loaded)
        self.assertEqual(ds.load(vault.root).tolerance_mode, "iso2768")
        # A CSV without standard rows leaves them alone.
        loaded.standards = {}
        vs.save_settings(vault.root, loaded)
        self.assertEqual(ds.load(vault.root).tolerance_mode, "iso2768")
        loaded.standards = {"projection": "sideways"}
        with self.assertRaises(ValueError):
            vs.save_settings(vault.root, loaded)

    def test_create_vault_takes_wizard_standards(self):
        setup = vs.VaultSetup(name="Lumen", folder=os.path.join(self.tmp, "v"), standards={"units": "in"})
        vault = vs.create_vault(setup, actor="a")
        self.assertEqual(ds.load(vault.root).units, "in")
        self.assertEqual(ds.company_name(ds.load(vault.root), vault.root), "Lumen")
        with self.assertRaises(ValueError):
            vs.create_vault(vs.VaultSetup(name="X", folder=os.path.join(self.tmp, "w"),
                                          standards={"units": "furlongs"}))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "w")))


def event(op, actor="bruce@mac", at="2026-10-20T10:00:00+00:00", **args):
    return SimpleNamespace(op=op, actor=actor, at=at, args=args)


class Signatures(Tmp):
    def test_from_a_real_journal(self):
        vault = FolderVault.init(os.path.join(self.tmp, "v"), "V", actor="bruce@mac")
        vault.create_item("LE-01-01001-001", "part", "Base plate")
        vault.submit("LE-01-01001-001")
        vault.approve("LE-01-01001-001", note="released")
        sign = ds.signatures(vault._read_events(), "LE-01-01001-001")
        self.assertEqual([sign[r][0] for r in ("drawn", "checked", "approved")], ["bruce"] * 3)
        self.assertRegex(sign["approved"][1], r"\A\d{4}-\d{2}-\d{2}\Z")

    def test_rules(self):
        pn = "P-1"
        events = [
            event("create_item", part_number=pn, actor="ann@a"),
            event("checkin", part_number=pn, actor="bob@b", at="2026-10-21T10:00:00+00:00"),
            event("submit", part_number=pn, actor="bob@b"),
            event("reject", part_number=pn, actor="cy@c"),
        ]
        sign = ds.signatures(events, pn)
        self.assertEqual(sign["drawn"][0], "bob")
        self.assertIsNone(sign["checked"])
        events += [event("submit", part_number=pn, actor="bob@b"), event("approve", part_number=pn, actor="cy@c"),
                   event("create_item", part_number="Q-1", actor="zed@z")]
        sign = ds.signatures(events, pn)
        self.assertEqual((sign["checked"][0], sign["approved"][0]), ("bob", "cy"))
        # An ECO opens a new revision (clears), its release approves it.
        events += [event("raise_ecr", number="ECR-1", problem="x", items=[pn]),
                   event("open_eco", number="ECO-1", ecr_numbers=["ECR-1"])]
        sign = ds.signatures(events, pn)
        self.assertIsNone(sign["approved"])
        events += [event("release_eco", number="ECO-1", actor="dee@d")]
        self.assertEqual(ds.signatures(events, pn)["approved"][0], "dee")
        self.assertEqual(ds.signatures(events, "nothing")["drawn"], None)

    def test_revision_text(self):
        self.assertEqual(ds.revision_text("02"), "WIP 02")
        self.assertEqual(ds.revision_text("A"), "A")


class TitleBlock(unittest.TestCase):
    def test_fields(self):
        s = ds.DrawingStandards(company="Lumen")
        part = {"title": "Base plate", "part_number": "LE-01-01001-001", "revision": "01", "material": "PETG",
                "finish": "", "mass_kg": 0.0412}
        fields = ds.title_block_fields(s, None, part, {"drawn": ("bruce", "2026-10-20")},
                                       {"scale": "1:2", "sheet": "1 of 1"})
        self.assertEqual(fields["company"], "Lumen")
        self.assertEqual(fields["revision"], "WIP 01")
        self.assertEqual(fields["mass"], "41 g")
        self.assertEqual(fields["drawn_by"], "bruce")
        self.assertNotIn("finish", fields)  # nothing to say: the sheet keeps its text
        self.assertNotIn("checked_by", fields)
        self.assertEqual(fields["tol1"], "X.X ±0.5" + ds.GAP + "X.XX ±0.25")
        self.assertEqual(fields["standard_note"], "Interpret per ASME Y14.5-2018")
        self.assertTrue(set(fields) <= set(ds.FIELDS))

    def test_sheet_of_template(self):
        self.assertEqual(ds.sheet_of_template("/x/ANSI_B_Landscape.svg"), "ANSI_B_Landscape")
        self.assertEqual(ds.sheet_of_template("ISO_A3_Landscape-0123abcd4567.svg"), "ISO_A3_Landscape")
        self.assertIsNone(ds.sheet_of_template("/TechDraw/ANSIB_Landscape.svg"))
        self.assertEqual(ds.template_name("nonsense"), "ANSI_B_Landscape.svg")


class KeepTyped(unittest.TestCase):
    def test_first_fill_takes_blank_and_template_defaults(self):
        current = {"title": "", "part_number": "P/N", "material": "Steel (typed)"}
        texts, filled, kept = ds.merge_fields(current, {"title": "Plate", "part_number": "P-1", "material": "PETG"},
                                              {}, defaults={"part_number": "P/N"})
        self.assertEqual(texts, {"title": "Plate", "part_number": "P-1", "material": "Steel (typed)"})
        self.assertEqual(filled, {"title": "Plate", "part_number": "P-1"})
        self.assertEqual(kept, ["material"])

    def test_later_fills_follow_ours_and_keep_typed(self):
        current = {"title": "Plate", "part_number": "P-1 special"}
        texts, filled, kept = ds.merge_fields(current, {"title": "Base plate", "part_number": "P-2"},
                                              {"title": "Plate", "part_number": "P-1"})
        self.assertEqual(texts["title"], "Base plate")
        self.assertEqual(texts["part_number"], "P-1 special")
        self.assertEqual(kept, ["part_number"])
        self.assertNotIn("part_number", filled)
        # A field the user clears is ours again.
        texts, filled, kept = ds.merge_fields({"part_number": ""}, {"part_number": "P-2"}, filled)
        self.assertEqual((texts["part_number"], kept), ("P-2", []))

    def test_unknown_fields_are_not_added(self):
        texts, _filled, _kept = ds.merge_fields({"title": ""}, {"title": "A", "mass": "1 g"}, {})
        self.assertEqual(texts, {"title": "A"})


if __name__ == "__main__":
    unittest.main()
