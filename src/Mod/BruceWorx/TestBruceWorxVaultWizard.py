# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the Create Vault wizard (bruceworx_vaultwizard)."""

import os
import tempfile
import unittest
from types import SimpleNamespace

import bruceworx_vaultwizard as wiz


def suggest(name, parent):
    return parent + "/" + name


class TestFolderRule(unittest.TestCase):
    def test_follows_the_name(self):
        rule = wiz.FolderRule("/cloud/BruceWorx Vaults", suggest)
        self.assertEqual(rule.folder("Acme"), "/cloud/BruceWorx Vaults/Acme")
        self.assertEqual(rule.folder("Acme Products"), "/cloud/BruceWorx Vaults/Acme Products")

    def test_blank_name_uses_the_default(self):
        rule = wiz.FolderRule("/p", suggest)
        self.assertEqual(rule.folder("  "), "/p/" + wiz.DEFAULT_NAME)

    def test_typed_folder_stays(self):
        rule = wiz.FolderRule("/p", suggest)
        rule.typed()
        self.assertIsNone(rule.folder("Other"))

    def test_choose_sets_parent_and_follows_again(self):
        rule = wiz.FolderRule("/p", suggest)
        rule.typed()
        self.assertEqual(rule.choose("/q", "Acme"), "/q/Acme")
        self.assertEqual(rule.folder("Beta"), "/q/Beta")


class TestRows(unittest.TestCase):
    def test_clean_and_upper(self):
        pairs, issues = wiz.clean_rows([(" bw ", " BruceWorx "), ("", ""), ("ac", "")])
        self.assertEqual(pairs, [("BW", "BruceWorx"), ("AC", "")])
        self.assertEqual(issues, [])

    def test_duplicate_code(self):
        pairs, issues = wiz.clean_rows([("01", "Part"), ("01", "Again")], "document type code")
        self.assertEqual(pairs, [("01", "Part")])
        self.assertEqual(issues[0][0], "error")
        self.assertIn("01", issues[0][1])

    def test_text_without_code(self):
        pairs, issues = wiz.clean_rows([("", "Drawing")])
        self.assertEqual(pairs, [])
        self.assertTrue(wiz.has_errors(issues))


class TestProperties(unittest.TestCase):
    def test_parse_values(self):
        self.assertEqual(wiz.parse_values(" a, b;c,, a "), ["a", "b", "c"])
        self.assertEqual(wiz.parse_values(""), [])

    def test_entry(self):
        self.assertEqual(wiz.property_entry(" Coating ", "string", doc=" Paint "),
                         {"name": "Coating", "type": "string", "bom": True, "doc": "Paint"})
        enum = wiz.property_entry("Grade", "enum", "A, B")
        self.assertEqual(enum["values"], ["A", "B"])
        self.assertEqual(enum["default"], "A")

    def test_custom_issues(self):
        good = [wiz.property_entry("Coating", "string")]
        self.assertEqual(wiz.custom_issues(good), [])
        bad = [wiz.property_entry("", "string"), wiz.property_entry("Two words", "string"),
               wiz.property_entry("Grade", "enum", ""), wiz.property_entry("Finish", "string")]
        texts = [t for _l, t in wiz.custom_issues(bad, taken=["Finish"])]
        self.assertEqual(len(texts), 4)
        self.assertTrue(wiz.has_errors(wiz.custom_issues(bad)))


class TestText(unittest.TestCase):
    def test_has_errors_takes_objects_and_tuples(self):
        self.assertFalse(wiz.has_errors([("warning", "w"), SimpleNamespace(level="info", text="i")]))
        self.assertTrue(wiz.has_errors([SimpleNamespace(level="error", text="e")]))

    def test_report_text(self):
        report = SimpleNamespace(imported=[("a", "1")], skipped=[], failed=[])
        self.assertEqual(wiz.report_text(report), "Imported 1 file.")
        report = SimpleNamespace(imported=[("a", "1"), ("b", "2")], skipped=[("c", "x")], failed=[("d", "y")])
        self.assertEqual(wiz.report_text(report), "Imported 2 files. 1 skipped, 1 failed.")

    def test_csv_file_name(self):
        self.assertEqual(wiz.csv_file_name("Acme Products"), "Acme Products vault setup.csv")
        self.assertEqual(wiz.csv_file_name("A/B: C"), "A-B- C vault setup.csv")
        self.assertEqual(wiz.csv_file_name("  "), "Vault vault setup.csv")

    def test_downloads_dir(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(wiz.downloads_dir(home), home)
            os.mkdir(os.path.join(home, "Downloads"))
            self.assertEqual(wiz.downloads_dir(home), os.path.join(home, "Downloads"))


def settings(programs=(), types=None, props=(), retired=None):
    return SimpleNamespace(
        programs=[SimpleNamespace(code=c, description=d) for c, d in programs],
        document_types=dict(types or {}),
        properties=[dict(p) for p in props],
        retired={t: set(v) for t, v in (retired or {}).items()},
    )


class TestSettings(unittest.TestCase):
    def test_edit_entry_keeps_name_and_type(self):
        entry = {"name": "Grade", "type": "enum", "values": ["A", "B"], "default": "B", "doc": "old"}
        out = wiz.edit_entry(entry, "A, C", "")
        self.assertEqual(out["values"], ["A", "C"])
        self.assertEqual(out["default"], "A")  # B is gone
        self.assertNotIn("doc", out)
        self.assertEqual(entry["values"], ["A", "B"])  # not changed in place
        text = wiz.edit_entry({"name": "Coating", "type": "string"}, "x", " Paint ")
        self.assertEqual(text, {"name": "Coating", "type": "string", "doc": "Paint"})

    def test_merge_adds_and_renames_but_keeps_rows(self):
        current = settings([("BW", "BruceWorx"), ("AC", "")], {"01": "Part", "02": "Assembly"},
                           [{"name": "Project", "type": "string"}], {"program": {"AC"}})
        loaded = settings([("BW", "BruceWorx Inc"), ("NX", "Next")], {"03": "Drawing"},
                          [{"name": "Customer", "type": "string"}], {"program": set()})
        merged = wiz.merge_settings(current, loaded)
        self.assertEqual([(p.code, p.description) for p in merged.programs],
                         [("BW", "BruceWorx Inc"), ("AC", ""), ("NX", "Next")])
        self.assertEqual(merged.document_types, {"01": "Part", "02": "Assembly", "03": "Drawing"})
        self.assertEqual([e["name"] for e in merged.properties], ["Project", "Customer"])
        self.assertEqual(merged.retired["program"], {"AC"})  # AC is not in the CSV: stays retired
        self.assertEqual(current.programs[0].description, "BruceWorx")  # inputs untouched

    def test_merge_retired_follows_the_csv_row(self):
        current = settings([("BW", ""), ("AC", "")], retired={"program": {"AC"}})
        loaded = settings([("AC", ""), ("BW", "")], retired={"program": {"BW"}})
        merged = wiz.merge_settings(current, loaded)
        self.assertEqual(merged.retired["program"], {"BW"})

    def test_settings_changes(self):
        old = settings([("BW", "")], {"01": "Part"}, [{"name": "Project", "type": "string"}])
        new = settings([("BW", "New")], {"01": "Part", "02": "Assembly"},
                       [{"name": "Project", "type": "string"}], {"document_type": {"01"}})
        self.assertEqual(wiz.settings_changes(old, new), (1, 2))
        self.assertEqual(wiz.settings_changes(old, old), (0, 0))


class TestPartNumberBuilder(unittest.TestCase):
    def test_segments_of_a_number(self):
        segs = wiz.number_segments("BW-01-02001-001")
        self.assertEqual([(k, c, t) for k, c, t, _r in segs],
                         [("program", "PROGRAM", "BW"), ("type", "TYPE", "01"),
                          ("parent", "PARENT", "02001"), ("sub", "SUB", "001")])
        self.assertTrue(all(r for *_x, r in segs))
        # A revision on the end is dropped: it is not part of the number.
        self.assertEqual([t for _k, _c, t, _r in wiz.number_segments("bw-01-02001-001-A")],
                         ["BW", "01", "02001", "001"])

    def test_placeholders(self):
        for bad in ("", None, "BW-01", "BW--01001-001"):
            segs = wiz.number_segments(bad)
            self.assertEqual([t for _k, _c, t, _r in segs], ["PROG", "DT", "PPPPP", "SSS"])
            self.assertFalse(any(r for *_x, r in segs))

    def test_number_key_drops_the_revision(self):
        self.assertEqual(wiz.number_key("BW-01-01001-001-A"), "BW-01-01001-001")
        self.assertEqual(wiz.number_key("BW-01-01001-001"), "BW-01-01001-001")
        self.assertEqual(wiz.number_key(""), "")

    def test_rev_chip(self):
        self.assertEqual(wiz.rev_chip_text(), "01 → A")

    def test_pick_code(self):
        self.assertEqual(wiz.pick_code(["BW", "AC"], "ac"), "AC")  # the selected row
        self.assertEqual(wiz.pick_code(["BW", "AC"], "ZZ"), "BW")  # not in the table: the first
        self.assertEqual(wiz.pick_code(["BW", "AC"], "", retired={"BW"}), "AC")
        self.assertEqual(wiz.pick_code(["BW"], "", retired={"BW"}), "BW")
        self.assertIsNone(wiz.pick_code([], "BW"))
        self.assertEqual(wiz.pick_code(["01", "02", " "], "02"), "02")

    def test_selection_flows_into_the_example(self):
        try:
            from bruceworx import vault_setup as vs
        except ImportError:
            self.skipTest("BruceWorx hooks add-on not installed")
        setup = vs.NumberingSetup(programs=[vs.ProgramSetup("BW"), vs.ProgramSetup("AC")],
                                  document_types={"01": "Part", "02": "Assembly"}, first_parent=2001)
        program = wiz.pick_code([p.code for p in setup.programs], "AC")
        dt = wiz.pick_code(list(setup.document_types), "02")
        number = wiz.number_key(vs.example_number(setup, program=program, document_type=dt))
        self.assertEqual(number, "AC-02-02001-001")
        self.assertEqual([t for _k, _c, t, _r in wiz.number_segments(number)], ["AC", "02", "02001", "001"])

    def test_counter_rows(self):
        data = {"programs": {
            "BW": {"counters": {"02": {"next_parent": 1005, "parents": {"01001": {}, "01002": {}}},
                                "01": {"next_parent": 1001, "parents": {}}}},
            "ac": {"counters": {"01": {"parents": {}}}},
            "XX": "broken",
        }}
        rows = wiz.counter_rows(data, first=1001)
        self.assertEqual(rows, [("AC", "01", "01001", 0), ("BW", "01", "01001", 0), ("BW", "02", "01005", 2)])
        self.assertEqual(wiz.next_parent(rows, "BW", "02"), 1005)
        self.assertEqual(wiz.next_parent(rows, "NX", "01", first=3001), 3001)
        self.assertEqual(wiz.counter_rows(None), [])

    def test_fit_size(self):
        # Four segments of 2, 2, 5 and 3 monospace characters, 60 px each at size 100.
        values = [120.0, 120.0, 300.0, 180.0]
        captions = [50.0, 30.0, 44.0, 24.0]
        size = wiz.fit_size(300, values, captions, 100.0, pad=12, dash=60.0, gaps=12, lo=13, hi=26)
        self.assertTrue(13 <= size <= 26)

        def width(n):
            s = n / 100.0
            return sum(max(v * s, c) + 12 for v, c in zip(values, captions)) + 60.0 * s * 3 + 12

        self.assertLessEqual(width(size), 300)
        self.assertGreater(width(size + 1), 300)
        self.assertEqual(wiz.fit_size(5000, values, captions, 100.0, 12, 60.0, 12, 13, 26), 26)  # capped
        self.assertEqual(wiz.fit_size(50, values, captions, 100.0, 12, 60.0, 12, 13, 26), 13)  # floor
        # A narrower panel never gives a larger number.
        sizes = [wiz.fit_size(w, values, captions, 100.0, 12, 60.0, 12, 13, 26) for w in (420, 380, 330, 300)]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class TestImportJob(unittest.TestCase):
    def test_parts_go_before_the_assemblies_that_link_them(self):
        import shutil
        import zipfile

        from bruceworx import pdm_workspace as ws
        from bruceworx import vault_setup as vs

        tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-wiz-job-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def fcstd(name, kind, links=()):
            path = os.path.join(tmp, "src", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            xlinks = "".join(f'<XLink file="{f}" name="Body"/>' for f in links)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Document.xml", (
                    f'<Document><Objects Count="1"><Object type="{kind}" name="Main" id="0"/></Objects>'
                    f'<ObjectData Count="1"><Object name="Main"><Properties Count="0">{xlinks}</Properties>'
                    '</Object></ObjectData></Document>'))
                archive.writestr("GuiDocument.xml", f"<GuiDocument salt='{name}'/>")
            return path

        fcstd("a-frame.FCStd", "Assembly::AssemblyObject", ("z-plate.FCStd",))  # sorts before its part
        fcstd("z-plate.FCStd", "PartDesign::Body")
        vault = vs.create_vault(vs.VaultSetup(name="Job", folder=os.path.join(tmp, "vault")), actor="bruce@mac")
        candidates = vs.plan_numbers(vs.scan_for_import(os.path.join(tmp, "src")), vault.root, "BW")
        job = wiz.ImportJob(vs, vault, candidates, "BW")
        while job.step() is not None:
            pass
        numbers = {os.path.basename(p): key for p, key in job.report.imported}
        vault.refresh()
        frame = ws.Workspace(vault).get_latest(numbers["a-frame.FCStd"])
        self.assertEqual(ws.linked_files(frame), [numbers["z-plate.FCStd"] + ".FCStd"])

        # A later assembly built against the plate's work copy: its link is rewritten to the number too.
        plate = numbers["z-plate.FCStd"]
        work_copy = ws.Workspace(vault).get_latest(plate)
        os.makedirs(os.path.join(tmp, "src2"))
        later = fcstd("b-stand.FCStd", "Assembly::AssemblyObject", (work_copy,))
        os.replace(later, os.path.join(tmp, "src2", "b-stand.FCStd"))
        candidates = vs.plan_numbers(vs.scan_for_import(os.path.join(tmp, "src2")), vault.root, "BW")
        job = wiz.ImportJob(vs, vault, candidates, "BW")
        while job.step() is not None:
            pass
        self.assertEqual(len(job.report.imported), 1, job.report.failed)
        vault.refresh()
        stand = ws.Workspace(vault).get_latest(job.report.imported[0][1])
        self.assertEqual(ws.linked_files(stand), [plate + ".FCStd"])


class TestAssembliesAsPartsBox(unittest.TestCase):
    """PDM-12: Vault Settings has Number assemblies as parts, saved in the vault's numbering.json."""

    def test_the_box_shows_toggles_and_saves_the_setting(self):
        import shutil

        from bruceworx import vault_setup as vs

        tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-wiz-aap-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        vault = vs.create_vault(vs.VaultSetup(name="Lib", folder=os.path.join(tmp, "vault")), actor="bruce@mac")
        panel = wiz._make_classes()["SettingsPanel"](vault.root)
        self.addCleanup(panel.release)
        self.assertFalse(panel.assemblies_as_parts.isChecked())
        self.assertFalse(panel.current_settings().assemblies_as_parts)
        panel.assemblies_as_parts.setChecked(True)
        self.assertTrue(panel.save())
        self.assertTrue(vs.load_settings(vault.root).assemblies_as_parts)
        again = wiz._make_classes()["SettingsPanel"](vault.root)
        self.addCleanup(again.release)
        self.assertTrue(again.assemblies_as_parts.isChecked())
        again.assemblies_as_parts.setChecked(False)
        self.assertTrue(again.save())
        self.assertFalse(vs.load_settings(vault.root).assemblies_as_parts)


if __name__ == "__main__":
    unittest.main()
