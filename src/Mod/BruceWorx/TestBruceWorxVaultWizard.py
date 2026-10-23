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


if __name__ == "__main__":
    unittest.main()
