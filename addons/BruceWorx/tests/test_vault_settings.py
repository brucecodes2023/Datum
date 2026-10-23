# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault tables as one CSV file and Vault Settings (bruceworx.vault_setup, section 6):
the template, spreadsheet quirks, and changing an existing vault's programs,
document types and company properties (add, rename, retire; never delete or
change a code)."""

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import macro, partprops, vault_setup as vs  # noqa: E402
from bruceworx.cli import main  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402
from bruceworx.sources import JsonNumberSource  # noqa: E402

HEADER = ",".join(vs.CSV_COLUMNS)


def errors(issues):
    return [i.text for i in issues if i.level == "error"]


def full_settings():
    """Settings using every column: two programs, types, three property kinds, retired rows."""
    return vs.VaultSettings(
        programs=[vs.ProgramSetup("BW", "BruceWorx"), vs.ProgramSetup("LE", "Legacy, old")],
        document_types={"01": "Part", "02": "Assembly", "07": "Spec \"sheet\""},
        properties=[
            {"name": "MaterialSpec", "type": "string", "label": "Material spec", "doc": "e.g. 6061-T6", "bom": True},
            {"name": "Coats", "type": "int", "default": 2},
            {"name": "Tolerance", "type": "enum", "values": ["ISO 2768-f", "ISO 2768-m"], "default": "ISO 2768-m",
             "doc": "Général"},
        ],
        retired={"program": {"LE"}, "document_type": {"07"}, "property": {"Coats"}},
    )


class CsvTests(unittest.TestCase):
    def test_template_round_trip(self):
        text = vs.csv_template()
        self.assertTrue(text.startswith(HEADER + "\r\n# table: program | document_type | property\r\n"))
        settings, issues = vs.parse_csv(text)
        self.assertEqual(issues, [])
        self.assertEqual(settings, vs.example_settings())
        self.assertEqual([p.code for p in settings.programs], ["BW"])
        self.assertEqual(settings.document_types, {"01": "Part", "02": "Assembly", "03": "Drawing"})
        self.assertEqual(settings.properties[0]["name"], "MaterialSpec")
        self.assertEqual(vs.csv_template(settings), text)
        full = full_settings()
        again, issues = vs.parse_csv(vs.csv_template(full))
        self.assertEqual(errors(issues), [])
        self.assertEqual(again, full)
        self.assertIn("property,Coats,,,integer,,2,no,yes", vs.csv_template(full))

    def test_spreadsheet_quirks(self):
        text = ("﻿ Table , CODE,Name ,Notes,Description,  bom\r\n"
                "# a comment\r\n"
                "\r\n"
                "Program,bw,,x,BruceWorx,\r\n"
                "Document Type,1,Part,,,\r\n"
                "document_types,=\"02\",Assembly,,,\r\n"
                "property,Finish2,,,,YES\r\n")
        settings, issues = vs.parse_csv(text)
        self.assertEqual(errors(issues), [])
        self.assertEqual(settings.programs, [vs.ProgramSetup("BW", "BruceWorx")])
        self.assertEqual(settings.document_types, {"01": "Part", "02": "Assembly"})
        self.assertEqual(settings.properties, [{"name": "Finish2", "type": "string", "bom": True}])
        self.assertTrue(any("Notes" in i.text and i.level == "info" for i in issues))
        # Bytes with a BOM; a semicolon-separated sheet (European Excel); Windows-1252 bytes.
        data = vs.csv_template(full_settings()).encode("utf-8-sig")
        self.assertEqual(vs.parse_csv(data)[0], full_settings())
        semi = "table;code;name\nprogram;BW;\ndocument_type;01;Pièce\n"
        settings, issues = vs.parse_csv(semi.encode("cp1252"))
        self.assertEqual(errors(issues), [])
        self.assertEqual(settings.document_types, {"01": "Pièce"})

    def test_file_helpers_write_a_bom(self):
        tmp = tempfile.mkdtemp(prefix="bw-csv-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "vault.csv")
        vs.write_csv_file(path, full_settings())
        with open(path, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\xef\xbb\xbftable,code"))
        self.assertEqual(vs.read_csv_file(path)[0], full_settings())
        self.assertEqual(os.listdir(tmp), ["vault.csv"])

    def test_errors_name_the_line(self):
        text = "\n".join([
            HEADER,
            "program,BW,,,,,,,",
            "program,bw,,,,,,,",                   # 3 repeat
            "program,9X,,,,,,,",                   # 4 bad code
            "document_type,01,Part,,,,,,",
            "document_type,123,Big,,,,,,",         # 6 bad code
            "document_type,02,,,,,,,",             # 7 no name
            "widget,1,,,,,,,",                     # 8 unknown table
            "property,Finish,,,string,,,,",        # 9 built-in name
            "property,Grade,,,enum,,,,",           # 10 enum without values
            "property,Grade2,,,enum,a|b,c,,",      # 11 default not a value
            "property,Count,,,integer,,two,,",     # 12 not a number
            "property,Flag,,,float3,,,,",          # 13 unknown type
            "property,Ok,,,string,,,maybe,",       # 14 bad bom
            "property,Ok2,,,,,,,perhaps",          # 15 bad retired
            "property,Ok3,,,,,,,",
            "property,Ok3,,,,,,,",                 # 17 repeat
            "property,bad name,,,,,,,",            # 18 not an identifier
        ])
        settings, issues = vs.parse_csv(text)
        found = errors(issues)
        for line in (3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18):
            self.assertTrue(any(t.startswith(f"Line {line}:") for t in found), (line, found))
        self.assertIn("Line 3: Program BW is already on line 2.", found)
        self.assertIn("Line 17: Property Ok3 is already on line 16.", found)
        self.assertEqual([p.code for p in settings.programs], ["BW"])
        self.assertEqual([e["name"] for e in settings.properties], ["Ok3"])

    def test_header_missing_or_empty(self):
        self.assertTrue(errors(vs.parse_csv("program,BW\n")[1])[0].startswith("Line 1: the first row"))
        self.assertTrue(errors(vs.parse_csv("")[1]))
        _settings, issues = vs.parse_csv("table,code\n")
        self.assertEqual(errors(issues), [])
        self.assertEqual(len([i for i in issues if i.level == "warning"]), 2)  # no programs, no types

    def test_wizard_setup_round_trip(self):
        numbering = vs.NumberingSetup(programs=[vs.ProgramSetup("bw", "B")], document_types={"1": "Part"},
                                      first_parent=500, first_revision="C")
        settings = vs.settings_from_setup(numbering, vs.suggested_properties())
        self.assertEqual(settings.document_types, {"01": "Part"})
        parsed, issues = vs.parse_csv(vs.csv_template(settings))
        self.assertEqual(errors(issues), [])
        parsed.retired["program"].add("LE")
        parsed.programs.append(vs.ProgramSetup("LE"))
        setup, props = vs.apply_to_setup(parsed, numbering)
        self.assertEqual(setup.first_parent, 500)
        self.assertEqual(setup.first_revision, "C")
        self.assertEqual([p.code for p in setup.programs], ["BW"])  # retired rows are left out
        self.assertEqual(vs.validate_numbering(setup), [])
        self.assertEqual([p["name"] for p in props], [p["name"] for p in vs.suggested_properties()])
        self.assertEqual(vs.validate_properties(props), [])


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "vault")
        setup = vs.VaultSetup(
            name="Shop", folder=self.root,
            numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("BW", "BruceWorx"), vs.ProgramSetup("LE")],
                                        document_types={"01": "Part", "02": "Assembly"}, first_parent=2001),
            properties=vs.suggested_properties()[:3],
        )
        self.vault = vs.create_vault(setup, actor="a")
        self.source = JsonNumberSource(vs.numbering_path(self.root))

    def numbering(self):
        with open(vs.numbering_path(self.root), encoding="utf-8") as handle:
            return json.load(handle)

    def layer(self):
        with open(vs.properties_path(self.root), encoding="utf-8") as handle:
            return json.load(handle)


class SettingsTests(_Vault):
    def test_load_matches_creation(self):
        settings = vs.load_settings(self.vault.ref_path)
        self.assertEqual(settings.programs, [vs.ProgramSetup("BW", "BruceWorx"), vs.ProgramSetup("LE", "")])
        self.assertEqual(settings.document_types, {"01": "Part", "02": "Assembly"})
        self.assertEqual([p["name"] for p in settings.properties], ["MaterialSpec", "HeatTreatment", "ToleranceClass"])
        self.assertEqual(settings.retired, {"program": set(), "document_type": set(), "property": set()})
        self.assertEqual(self.numbering()["first_parent"], 2001)
        # Unchanged settings save as no change at all.
        before = self.numbering()
        self.assertFalse(vs._errors(vs.settings_change_issues(self.root, settings)))
        vs.save_settings(self.root, settings)
        self.assertEqual(self.numbering(), before)

    def test_add_and_rename(self):
        self.source.allocate("BW", "01")
        settings = vs.load_settings(self.root)
        settings.programs[0].description = "BruceWorx Inc"
        settings.programs.append(vs.ProgramSetup("NX", "New"))
        settings.document_types["01"] = "Component"
        settings.document_types["03"] = "Drawing"
        settings.properties[0]["label"] = "Material"
        settings.properties.append({"name": "Coats", "type": "int", "default": 1})
        issues = vs.settings_change_issues(self.root, settings)
        self.assertEqual(errors(issues), [])
        warnings = [i.text for i in issues if i.level == "warning"]
        self.assertEqual(len(warnings), 3, warnings)
        self.assertTrue(any("Adds program NX" in i.text for i in issues))
        vs.save_settings(self.root, settings)
        data = self.numbering()
        # Existing counters untouched; every new pair starts at the first parent.
        self.assertEqual(data["programs"]["BW"]["counters"]["01"]["next_parent"], 2002)
        self.assertEqual(data["programs"]["BW"]["description"], "BruceWorx Inc")
        self.assertEqual(data["programs"]["BW"]["counters"]["03"], {"next_parent": 2001, "parents": {}})
        self.assertEqual(data["programs"]["NX"]["revision"], "A")
        self.assertEqual(sorted(data["programs"]["NX"]["counters"]), ["01", "02", "03"])
        self.assertEqual(self.source.programs(), ["BW", "LE", "NX"])
        self.assertEqual(self.source.document_types(), ["01", "02", "03"])
        self.assertEqual(self.source.allocate("BW", "01"), "BW-01-02002-001-A")
        self.assertEqual(self.source.allocate("NX", "03"), "NX-03-02001-001-A")
        schema = partprops.load_schema(extra_layers=partprops.vault_layers(self.root), user_layer=False)
        self.assertIn("Coats", schema.properties)
        self.assertEqual(vs.load_settings(self.root), settings)
        self.assertFalse([n for n in os.listdir(self.root) if n.endswith(".tmp")])

    def test_delete_change_code_and_type_refused(self):
        original = {}
        for name in ("numbering.json", "document_types.json", "part_properties.json"):
            with open(os.path.join(self.root, name), "rb") as handle:
                original[name] = handle.read()
        base = vs.load_settings(self.root)

        def refused(change, pattern):
            settings = copy.deepcopy(base)
            change(settings)
            with self.assertRaisesRegex(ValueError, pattern):
                vs.save_settings(self.root, settings)

        refused(lambda s: s.programs.pop(), "LE is missing.*retired")
        refused(lambda s: setattr(s.programs[0], "code", "BX"), "BW became BX")
        refused(lambda s: s.document_types.pop("02"), "Document type 02 is missing")
        refused(lambda s: s.document_types.update({"05": s.document_types.pop("02")}), "02 became 05")
        refused(lambda s: s.properties.pop(), "ToleranceClass is missing")
        refused(lambda s: s.properties[0].update(type="int"), "MaterialSpec cannot change from string to int")
        refused(lambda s: s.retired["program"].update({"BW", "LE"}), "at least one program")
        refused(lambda s: s.retired["document_type"].add("09"), "Retired document type 09 has no row")
        refused(lambda s: s.properties.append({"name": "Vendor", "type": "string"}), "built-in")
        for name, data in original.items():
            with open(os.path.join(self.root, name), "rb") as handle:
                self.assertEqual(handle.read(), data, name)
        # Without the used set every code counts as used; an unused property may change type.
        old = vs.load_settings(self.root)
        new = copy.deepcopy(old)
        new.properties[0]["type"] = "int"
        self.assertTrue(errors(vs.check_settings_change(old, new)))
        unused = {"program": set(), "document_type": set(), "property": set()}
        self.assertFalse(errors(vs.check_settings_change(old, new, unused)))

    def test_retire_hides_but_existing_numbers_still_allocate(self):
        first = self.source.allocate("LE", "02")
        self.assertEqual(first, "LE-02-02001-001-A")
        settings = vs.load_settings(self.root)
        settings.retired["program"].add("LE")
        settings.retired["document_type"].add("02")
        settings.retired["property"].add("HeatTreatment")
        issues = vs.settings_change_issues(self.root, settings)
        self.assertEqual(errors(issues), [])
        self.assertEqual(len([i for i in issues if i.text.startswith("Retires")]), 3)
        vs.save_settings(self.root, settings)
        self.assertTrue(self.numbering()["programs"]["LE"]["retired"])
        with open(os.path.join(self.root, "document_types.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["types"]["02"], {"name": "Assembly", "retired": True})
        # Not offered any more (the Assign Part Number pickers and the macro use these lists).
        self.assertEqual(self.source.programs(), ["BW"])
        self.assertEqual(self.source.document_types(), ["01"])
        self.assertEqual(macro.sole_program(self.source), "BW")
        self.assertEqual(macro.sole_document_type(self.source), "01")
        self.assertEqual(vs._vault_document_types(self.source, self.source._load()), {"01": "Part"})
        # Numbers under existing parents (and explicit codes) still allocate.
        self.assertEqual(self.source.allocate("LE", "02", parent="02001"), "LE-02-02001-002-A")
        self.assertEqual(self.source.allocate("LE", "02"), "LE-02-02002-001-A")
        # A retired property leaves the schema (new parts do not get it) but stays in the file.
        layer = self.layer()
        self.assertEqual([e["name"] for e in layer["retired_properties"]], ["HeatTreatment"])
        schema = partprops.load_schema(extra_layers=partprops.vault_layers(self.root), user_layer=False)
        self.assertNotIn("HeatTreatment", schema.properties)
        self.assertIn("MaterialSpec", schema.properties)
        loaded = vs.load_settings(self.root)
        self.assertEqual(loaded.retired, settings.retired)
        self.assertIn("program,LE,,,,,,,yes", vs.csv_template(loaded))
        # Bringing them back.
        loaded.retired = {t: set() for t in vs.CSV_TABLES}
        issues = vs.settings_change_issues(self.root, loaded)
        self.assertEqual(len([i for i in issues if i.text.startswith("Brings back")]), 3)
        vs.save_settings(self.root, loaded)
        self.assertNotIn("retired", self.numbering()["programs"]["LE"])
        self.assertNotIn("retired_properties", self.layer())
        self.assertEqual(self.source.programs(), ["BW", "LE"])
        self.assertEqual(vs.load_settings(self.root).properties[-1]["name"], "HeatTreatment")

    def test_csv_import_keeps_keys_a_csv_does_not_carry(self):
        layer = self.layer()
        layer["properties"][0]["required"] = "release"
        with open(vs.properties_path(self.root), "w", encoding="utf-8") as handle:
            json.dump(layer, handle)
        settings, issues = vs.parse_csv(vs.csv_template(vs.load_settings(self.root)))
        self.assertEqual(errors(issues), [])
        settings.properties[0]["doc"] = "Material specification"
        vs.save_settings(self.root, settings)
        entry = self.layer()["properties"][0]
        self.assertEqual(entry["required"], "release")
        self.assertEqual(entry["doc"], "Material specification")

    def test_used_codes(self):
        used = vs.used_codes(self.root)
        self.assertEqual(used["program"], set())
        self.assertEqual(used["property"], {"MaterialSpec", "HeatTreatment", "ToleranceClass"})
        self.source.allocate("BW", "02")
        self.vault.create_item("LE-01-00007-001", "part", "Bracket")
        self.vault.create_item("NOT-A-NUMBER", "part", "Free form")
        used = vs.used_codes(self.vault.ref_path)
        self.assertEqual(used["program"], {"BW", "LE"})
        self.assertEqual(used["document_type"], {"01", "02"})

    def test_used_codes_after_an_import(self):
        settings = vs.load_settings(self.root)
        settings.programs.append(vs.ProgramSetup("NX"))
        settings.document_types["04"] = "Model"
        vs.save_settings(self.root, settings)
        self.assertNotIn("NX", vs.used_codes(self.root)["program"])
        number = JsonNumberSource(vs.numbering_path(self.root)).allocate("NX", "04")
        self.assertEqual(number, "NX-04-02001-001-A")
        used = vs.used_codes(self.root)
        self.assertIn("NX", used["program"])
        self.assertIn("04", used["document_type"])

    def test_old_vault_without_numbering_gets_it(self):
        bare = FolderVault.init(os.path.join(self.tmp, "bare"), "Bare", actor="a")
        empty = vs.load_settings(bare.root)
        self.assertEqual((empty.programs, empty.document_types, empty.properties), ([], {}, []))
        settings, _issues = vs.parse_csv(vs.csv_template())
        vs.save_settings(bare.root, settings)
        source = JsonNumberSource(vs.numbering_path(bare.root))
        self.assertEqual(source.allocate("BW", "03"), "BW-03-01001-001-A")
        self.assertEqual(vs.load_settings(bare.root), settings)
        self.assertTrue(partprops.vault_layers(bare.root))

    def test_first_parent_derived_for_older_numbering(self):
        data = self.numbering()
        del data["first_parent"]
        self.assertEqual(vs.first_parent_of(data), 2001)
        for entry in data["programs"].values():
            for bucket in entry["counters"].values():
                bucket["next_parent"], bucket["parents"] = 2005, {"02003": {"next_sub": 2}}
        self.assertEqual(vs.first_parent_of(data), 2003)
        self.assertEqual(vs.first_parent_of({}), vs.DEFAULT_FIRST_PARENT)


class CliTests(_Vault):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        lines = out.getvalue().splitlines()
        result = [ln for ln in lines if ln.startswith("BW_RESULT ")]
        return code, (json.loads(result[-1][len("BW_RESULT "):]) if result else None), lines

    def test_export_and_import(self):
        path = os.path.join(self.tmp, "tables.csv")
        code, result, _lines = self.run_cli("vault", "settings", "--vault", self.root, "--export", path)
        self.assertEqual(code, 0)
        self.assertEqual(vs.read_csv_file(path)[0], vs.load_settings(self.root))
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write("program,NX,,Next,,,,,\r\ndocument_type,05,Sheet,,,,,,\r\n")
        before = self.numbering()
        code, result, lines = self.run_cli("vault", "settings", "--vault", self.root, "--import", path, "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(result["saved"])
        self.assertIn("info: Adds program NX.", lines)
        self.assertEqual(self.numbering(), before)
        code, result, _lines = self.run_cli("vault", "settings", "--vault", self.vault.ref_path, "--import", path)
        self.assertEqual(code, 0)
        self.assertTrue(result["saved"])
        self.assertIn("NX", self.source.programs())
        self.assertIn("05", self.source.document_types())
        # Deleting a row is refused and nothing changes.
        with open(path, encoding="utf-8-sig") as handle:
            rows = [ln for ln in handle.read().splitlines() if not ln.startswith("program,LE")]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
        before = self.numbering()
        code, result, lines = self.run_cli("vault", "settings", "--vault", self.root, "--import", path)
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(any(ln.startswith("error: Program LE is missing") for ln in lines))
        self.assertEqual(self.numbering(), before)
        code, _result, _lines = self.run_cli("vault", "settings", "--vault", self.root)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
