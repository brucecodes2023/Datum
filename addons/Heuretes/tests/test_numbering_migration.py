# SPDX-License-Identifier: LGPL-2.1-or-later
"""BW-PDM-03: the revision leaves the part number.

Acceptance: existing LE numbers migrate unchanged except for the revision.
Covers the numbers themselves, labels in a document, numbering files (counters
untouched, so the next number is the one the old file would have issued), and
a vault made by the Create Vault wizard.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from heuretes_freecad import numbering, pdm, vault_setup as vs  # noqa: E402
from heuretes_freecad.config_path import example_config_path  # noqa: E402
from heuretes_freecad.numbering import (  # noqa: E402
    LEGACY_FORMAT,
    PN_FORMAT,
    migrate_labels,
    migrate_part_number,
    parse_part_number,
    split_revision,
)
from heuretes_freecad.sources import (  # noqa: E402
    MIGRATION_BACKUP_SUFFIX,
    JsonNumberSource,
    migrate_numbering_data,
    migrate_numbering_file,
    open_number_source,
)
from fakes import FakeDoc, FakeObj, FakeShape  # noqa: E402

# LE-style numbers as the numbering file, the macro and `rename` issued them.
LEGACY = [
    "LE-01-01001-001-A",
    "LE-01-01001-002-A",
    "LE-01-01009-001-B",
    "LE-02-02001-001-A",
    "LE-03-01001-001-C",
    "OW-01-00001-999-AA",
    "SI-99-99999-000-YAB",
    "L1-07-01234-010-A",
]


class NumberTests(unittest.TestCase):
    def test_existing_numbers_lose_only_the_revision(self):
        for legacy in LEGACY:
            head, _sep, rev = legacy.rpartition("-")
            self.assertEqual(migrate_part_number(legacy), head)
            self.assertEqual(split_revision(legacy), (head, rev))
            # Every field is the same before and after.
            before, after = parse_part_number(legacy), parse_part_number(head)
            for field in ("program", "document_type", "parent", "sub"):
                self.assertEqual(before[field], after[field])
            self.assertEqual((before["revision"], after["revision"]), (rev, None))
            # The vault's own migration helper agrees.
            self.assertEqual(pdm.strip_revision(legacy), head)

    def test_a_pn_is_already_migrated(self):
        for legacy in LEGACY:
            pn = migrate_part_number(legacy)
            self.assertEqual(migrate_part_number(pn), pn)
            self.assertEqual(split_revision(pn), (pn, None))

    def test_printed_pre_release_numbers_split_too(self):
        self.assertEqual(split_revision("LE-01-01001-001-02"), ("LE-01-01001-001", "02"))
        self.assertEqual(split_revision(" le-01-01001-001-a "), ("LE-01-01001-001", "A"))

    def test_anything_else_is_refused_not_cut(self):
        for text in ("", "Body", "LE-01001-A", "LE-01-1001-001-A", "LE-01-01001-001-ABCD", "LE-01-01001-001-A-B"):
            with self.assertRaises(ValueError, msg=text):
                migrate_part_number(text)
            self.assertFalse(numbering.is_part_number(text))

    def test_format_without_revision_is_the_pn(self):
        self.assertEqual(numbering.format_part_number("LE", "01", 1001, 1), "LE-01-01001-001")
        self.assertEqual(numbering.format_part_number("LE", "01", 1001, 1, "A"), "LE-01-01001-001-A")


class LabelTests(unittest.TestCase):
    def test_document_labels_and_part_number_property(self):
        body = FakeObj("Body", "LE-01-01001-001-A", "PartDesign::Body", FakeShape())
        body.PartNumber = "LE-01-01001-001-A"
        other = FakeObj("Pin", "LE-01-01001-002-B", "Part::Feature", FakeShape())
        free = FakeObj("Sketch", "Sketch", "Sketcher::SketchObject")
        done = FakeObj("Plate", "LE-01-01009-001", "Part::Feature", FakeShape())
        doc = FakeDoc("LE_01_01001_001_A", "LE-01-01001-001-A", [body, other, free, done])
        result = migrate_labels(doc)
        self.assertEqual(body.Label, "LE-01-01001-001")
        self.assertEqual(body.PartNumber, "LE-01-01001-001")
        self.assertEqual(other.Label, "LE-01-01001-002")
        self.assertEqual((free.Label, done.Label), ("Sketch", "LE-01-01009-001"))
        self.assertEqual(doc.Label, "LE-01-01001-001")
        self.assertEqual(doc.Name, "LE_01_01001_001_A")  # FreeCAD cannot rename it; untouched
        self.assertTrue(result["changed"])
        self.assertEqual(result["document"], {"old": "LE-01-01001-001-A", "part_number": "LE-01-01001-001",
                                              "revision": "A"})
        self.assertEqual(
            [(c["name"], c["property"], c["revision"]) for c in result["changes"]],
            [("Body", "Label", "A"), ("Body", "PartNumber", "A"), ("Pin", "Label", "B")],
        )
        json.dumps(result)
        again = migrate_labels(doc)
        self.assertFalse(again["changed"])
        self.assertEqual(again["changes"], [])


def _legacy_file(path, next_parent=1001):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "source": "json",
            "format": LEGACY_FORMAT,
            "document_types": {"01": {"name": "Part"}, "02": {"name": "Assembly"}},
            "programs": {
                "LE": {"description": "Lift", "revision": "A",
                       "counters": {"01": {"next_parent": next_parent, "parents": {}},
                                    "02": {"next_parent": 2001, "parents": {}}}},
                "OW": {"revision": "B", "retired": True,
                       "counters": {"01": {"next_parent": 1001, "parents": {}}}},
            },
        }, handle)
    return JsonNumberSource(path)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-pdm03-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "numbering.json")

    def read(self, path=None):
        with open(path or self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_counters_carry_over_and_numbers_continue_without_revision(self):
        source = _legacy_file(self.path)
        issued = [source.allocate("LE", "01"), source.allocate("LE", "01", parent="01001"),
                  source.allocate("LE", "02")]
        self.assertEqual(issued, ["LE-01-01001-001-A", "LE-01-01001-002-A", "LE-02-02001-001-A"])
        self.assertTrue(source.issues_revision)
        expected = {
            (None,): source.preview("LE", "01"),
            ("01001",): source.preview("LE", "01", parent="01001"),
            ("02001",): source.preview("LE", "02", parent="02001"),
        }
        before = self.read()

        report = migrate_numbering_file(self.path)
        after = self.read()
        self.assertTrue(report["changed"])
        self.assertEqual(report["revisions"], {"LE": "A", "OW": "B"})
        self.assertEqual(after["format"], PN_FORMAT)
        self.assertEqual(after["migrated"]["revisions"], {"LE": "A", "OW": "B"})
        for name, entry in before["programs"].items():
            self.assertNotIn("revision", after["programs"][name])
            self.assertEqual(after["programs"][name]["counters"], entry["counters"])
            entry.pop("revision")
            self.assertEqual(after["programs"][name], entry)  # description, retired: unchanged
        self.assertEqual(after["document_types"], before["document_types"])

        source = JsonNumberSource(self.path)
        self.assertFalse(source.issues_revision)
        for (parent,), legacy in expected.items():
            self.assertEqual(source.preview("LE", "01" if parent != "02001" else "02", parent=parent),
                             migrate_part_number(legacy))
        self.assertEqual(source.allocate("LE", "01"), "LE-01-01002-001")
        self.assertEqual(source.allocate("LE", "01", parent="01001"), "LE-01-01001-003")
        self.assertEqual(source.programs(), ["LE"])
        with self.assertRaises(ValueError):
            source.allocate("LE", "01", revision="A")

    def test_backup_once_and_second_run_changes_nothing(self):
        _legacy_file(self.path)
        with open(self.path, encoding="utf-8") as handle:
            original = handle.read()
        first = migrate_numbering_file(self.path)
        backup = self.path + MIGRATION_BACKUP_SUFFIX
        self.assertEqual(first["backup"], backup)
        with open(backup, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)
        migrated = self.read()
        second = migrate_numbering_file(self.path)
        self.assertFalse(second["changed"])
        self.assertEqual(self.read(), migrated)
        with open(backup, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)

    def test_unmigrated_file_still_issues_what_it_did(self):
        source = _legacy_file(self.path)
        self.assertEqual(source.allocate("LE", "01", revision="C"), "LE-01-01001-001-C")
        self.assertEqual(open_number_source(self.path).allocate("LE", "01"), "LE-01-01002-001-A")

    def test_data_migration_refuses_unknown_formats(self):
        with self.assertRaises(ValueError):
            migrate_numbering_data({"format": "{PROG}-{NNNNN}-{REV}", "programs": {}})
        data = {"format": PN_FORMAT, "programs": {"LE": {"counters": {}}}}
        same, report = migrate_numbering_data(data)
        self.assertIs(same, data)
        self.assertFalse(report["changed"])

    def test_ops_file_has_nothing_to_migrate(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"source": "ops", "base_url": "https://ops.example/api"}, handle)
        report = migrate_numbering_file(self.path)
        self.assertFalse(report["changed"])
        self.assertFalse(os.path.exists(self.path + MIGRATION_BACKUP_SUFFIX))

    def test_shipped_example_migrates_to_the_same_number_without_revision(self):
        copy_dir = os.path.join(self.tmp, "config")
        shutil.copytree(os.path.dirname(example_config_path()), copy_dir)
        path = os.path.join(copy_dir, os.path.basename(example_config_path()))
        legacy = JsonNumberSource(path).preview("LE", "01")
        migrate_numbering_file(path)
        self.assertEqual(JsonNumberSource(path).preview("LE", "01"), migrate_part_number(legacy))


class VaultTests(unittest.TestCase):
    """A vault made by the Create Vault wizard keeps working after its file migrates."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-pdm03-vault-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "vault")
        setup = vs.VaultSetup(
            name="Shop", folder=self.root,
            numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")],
                                        document_types={"01": "Part", "02": "Assembly"}),
        )
        vs.create_vault(setup, actor="a")
        self.path = vs.numbering_path(self.root)

    def test_wizard_vault_is_born_migrated_and_vault_settings_still_save(self):
        # FND-08: the wizard writes the P/N form, so there is nothing to migrate.
        source = JsonNumberSource(self.path)
        self.assertEqual(source.allocate("LE", "01"), "LE-01-01001-001")
        self.assertFalse(migrate_numbering_file(self.path)["changed"])
        source = JsonNumberSource(self.path)
        self.assertEqual(source.allocate("LE", "01"), "LE-01-01002-001")
        settings = vs.load_settings(self.root)
        settings.programs.append(vs.ProgramSetup("NX", "New"))
        vs.save_settings(self.root, settings)
        self.assertEqual(JsonNumberSource(self.path).allocate("NX", "02"), "NX-02-01001-001")
        self.assertEqual(JsonNumberSource(self.path).allocate("LE", "01"), "LE-01-01003-001")


if __name__ == "__main__":
    unittest.main()
