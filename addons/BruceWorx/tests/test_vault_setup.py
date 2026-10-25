# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create Vault engine (bruceworx.vault_setup) without FreeCAD: location checks,
numbering and property choices, the files a new vault gets, and importing
.FCStd files (synthetic zips) without touching the originals."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import gui, partprops, vault_setup as vs  # noqa: E402
from bruceworx.cli import main  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402
from bruceworx.sources import JsonNumberSource  # noqa: E402

ICLOUD = os.path.join("Library", "Mobile Documents", "com~apple~CloudDocs")


def document_xml(objects, links=()):
    """A Document.xml like FreeCAD writes. ``objects`` is (name, type, props);
    ``links`` are XLink file names (as an App::Link or a TechDraw view has)."""
    heads = "".join(f'<Object type="{t}" name="{n}" id="{i}"/>' for i, (n, t, _p) in enumerate(objects))
    data = []
    for name, _type, props in objects:
        body = "".join(
            f'<Property name="{k}" type="App::PropertyString"><String value="{v}"/></Property>'
            for k, v in props.items()
        )
        data.append(f'<Object name="{name}"><Properties Count="{len(props)}">{body}</Properties></Object>')
    if links:
        xlinks = "".join(f'<XLink file="{f}" stamp="2026-10-23T10:00:00Z" name="Body"/>' for f in links)
        data.append(
            '<Object name="View"><Properties Count="1"><Property name="XSource" type="App::PropertyXLinkList">'
            f'<XLinkList count="{len(links)}">{xlinks}</XLinkList></Property></Properties></Object>'
        )
        heads += '<Object type="TechDraw::DrawViewPart" name="View" id="99"/>'
    return (
        '<?xml version="1.0" encoding="utf-8"?><Document SchemaVersion="4" FileVersion="1">'
        '<Properties Count="0"/>'
        f'<Objects Count="{len(objects)}">{heads}</Objects>'
        f'<ObjectData Count="{len(objects)}">{"".join(data)}</ObjectData></Document>'
    ).encode("utf-8")


def make_fcstd(path, objects, links=(), salt=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Document.xml", document_xml(objects, links))
        zf.writestr("GuiDocument.xml", f"<GuiDocument salt='{salt or path}'/>")
    return path


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def tree_hashes(folder):
    out = {}
    for current, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(current, name)
            out[path] = (digest(path), os.stat(path).st_mtime_ns)
    return out


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-setup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def new_vault(self, name="Shop", **numbering):
        setup = vs.VaultSetup(name=name, folder=self.path("vault"), numbering=vs.NumberingSetup(**numbering))
        return vs.create_vault(setup, actor="bruce@mac")


class NewItemTests(_Tmp):
    """New Part in Vault (FND-08): a numbered WIP 01 item without an import."""

    def make(self, path, number):
        self.made.append(number)
        with open(path, "wb") as handle:
            handle.write(b"fcstd " + number.encode())

    def setUp(self):
        super().setUp()
        self.made = []
        self.vault = self.new_vault(programs=[vs.ProgramSetup("LE")])

    def test_new_part_is_numbered_wip_01_and_checked_in(self):
        pn = vs.new_item(self.vault, "part", "Motor bracket", self.make)
        self.assertEqual(pn, "LE-01-01001-001")
        self.assertEqual(self.made, [pn])
        item = self.vault.items[pn]
        self.assertEqual((item.item_type, item.description), ("part", "Motor bracket"))
        self.assertEqual((item.latest.code, item.latest.state), ("01", "WIP"))
        self.assertIsNotNone(self.vault.latest_version(pn))
        self.assertNotIn(pn, self.vault.locks)
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, vs.STAGING_DIR)))
        self.assertEqual(vs.new_item(self.vault, "assembly", "Arm", self.make), "LE-02-01001-001")
        self.assertEqual(vs.new_item(self.vault, "part", "Second", self.make), "LE-01-01002-001")

    def test_a_file_that_cannot_be_made_burns_no_number(self):
        def broken(path, number):
            raise RuntimeError("no FreeCAD")

        with self.assertRaises(RuntimeError):
            vs.new_item(self.vault, "part", "Bracket", broken)
        self.assertEqual(vs.new_item(self.vault, "part", "Bracket", self.make), "LE-01-01001-001")

    def test_refusals(self):
        with self.assertRaises(ValueError):
            vs.new_item(self.vault, "part", "  ", self.make)
        with self.assertRaises(ValueError):
            vs.new_item(self.vault, "drawing", "Sheet", self.make)
        self.assertEqual(self.made, [])
        self.assertEqual(self.vault.items, {})


class LocationTests(_Tmp):
    def levels(self, issues):
        return [i.level for i in issues]

    def test_default_parent_prefers_icloud_drive(self):
        home = self.path("home")
        os.makedirs(os.path.join(home, "Documents"))
        self.assertEqual(vs.default_parent_dir(home), os.path.join(home, "Documents", "BruceWorx Vaults"))
        os.makedirs(os.path.join(home, ICLOUD))
        self.assertEqual(vs.default_parent_dir(home), os.path.join(home, ICLOUD, "BruceWorx Vaults"))

    def test_suggest_folder_sanitises(self):
        self.assertEqual(vs.suggest_folder("Shop: A/B", "/p"), "/p/Shop- A-B")
        self.assertEqual(vs.suggest_folder("  .hidden \x07 name. ", "/p"), "/p/hidden - name")
        self.assertEqual(vs.suggest_folder("Mill_2 v1.0 (old)", "/p"), "/p/Mill_2 v1.0 (old)")
        self.assertEqual(vs.suggest_folder("///", "/p"), "/p/---")
        self.assertEqual(vs.suggest_folder("", "/p"), "/p/Vault")
        self.assertTrue(vs.suggest_folder("X").endswith(os.path.join("BruceWorx Vaults", "X")))

    def test_new_or_empty_folder_is_fine(self):
        home = self.path("home")
        self.assertEqual(vs.check_location(self.path("a", "b", "new"), home=home), [])
        os.makedirs(self.path("empty"))
        self.assertEqual(vs.check_location(self.path("empty"), home=home), [])

    def test_non_empty_and_hidden_only(self):
        os.makedirs(self.path("full"))
        open(self.path("full", "x.FCStd"), "w").close()
        self.assertEqual(self.levels(vs.check_location(self.path("full"), home=self.tmp)), ["error"])
        os.makedirs(self.path("hidden"))
        open(self.path("hidden", ".DS_Store"), "w").close()
        self.assertEqual(self.levels(vs.check_location(self.path("hidden"), home=self.tmp)), ["warning"])
        open(self.path("file"), "w").close()
        self.assertIn("error", self.levels(vs.check_location(self.path("file"), home=self.tmp)))
        self.assertIn("error", self.levels(vs.check_location(self.path("file", "sub"), home=self.tmp)))
        self.assertEqual(self.levels(vs.check_location("  ")), ["error"])

    def test_vault_or_inside_a_vault(self):
        FolderVault.init(self.path("v"), "V", actor="a")
        issues = vs.check_location(self.path("v"), home=self.tmp)
        self.assertTrue(any("already is a vault" in i.text for i in issues))
        issues = vs.check_location(self.path("v", "sub", "new"), home=self.tmp)
        self.assertEqual(self.levels(issues), ["error"])
        self.assertIn("inside the vault", issues[0].text)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes anywhere")
    def test_not_writable(self):
        os.makedirs(self.path("ro"))
        os.chmod(self.path("ro"), stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.path("ro"), stat.S_IRWXU)
        self.assertEqual(self.levels(vs.check_location(self.path("ro", "new"), home=self.tmp)), ["error"])

    def test_git_and_build_trees_warn(self):
        os.makedirs(self.path("repo", ".git"))
        issues = vs.check_location(self.path("repo", "vault"), home=self.tmp)
        self.assertEqual(self.levels(issues), ["warning"])
        self.assertIn("git", issues[0].text)
        os.makedirs(self.path("b"))
        open(self.path("b", "CMakeCache.txt"), "w").close()
        self.assertEqual(self.levels(vs.check_location(self.path("b", "v"), home=self.tmp)), ["warning"])
        self.assertEqual(self.levels(vs.check_location(self.path("build", "v"), home=self.tmp)), ["warning"])

    def test_icloud_and_documents_info(self):
        home = self.path("home")
        os.makedirs(os.path.join(home, ICLOUD))
        issues = vs.check_location(os.path.join(home, ICLOUD, "BruceWorx Vaults", "Shop"), home=home)
        self.assertEqual(self.levels(issues), ["info"])
        self.assertIn("iCloud", issues[0].text)
        issues = vs.check_location(os.path.join(home, "Documents", "Shop"), home=home)
        self.assertEqual(self.levels(issues), ["info"])
        self.assertIn("Desktop & Documents", issues[0].text)
        self.assertEqual(vs.check_location(os.path.join(home, "Work", "Shop"), home=home), [])


class NumberingTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        setup = vs.NumberingSetup()
        self.assertEqual(vs.validate_numbering(setup), [])
        self.assertEqual(vs.example_number(setup), "BW-01-01001-001")
        self.assertEqual(vs.example_number(setup, "le", "03"), "LE-03-01001-001")

    def test_errors(self):
        def errors(**kwargs):
            issues = vs.validate_numbering(vs.NumberingSetup(**kwargs))
            self.assertTrue(issues and all(i.level == "error" for i in issues), issues)
            return len(issues)

        errors(programs=[])
        errors(programs=[vs.ProgramSetup("1X")])
        errors(programs=[vs.ProgramSetup("bw"), vs.ProgramSetup("BW")])
        errors(document_types={})
        errors(document_types={"1": "Part"})
        errors(document_types={"001": "Part"})
        errors(document_types={"AB": "Part"})
        errors(document_types={"01": " "})
        errors(first_parent=0)
        errors(first_parent=100000)
        errors(first_parent=True)
        errors(first_parent="1001")
        self.assertEqual(errors(programs=[], document_types={}), 2)
        with self.assertRaises(ValueError):
            vs.example_number(vs.NumberingSetup(first_parent=100000))


class PropertyTests(unittest.TestCase):
    def test_suggestions_are_a_valid_layer_without_clashes(self):
        entries = vs.suggested_properties()
        self.assertGreaterEqual(len(entries), 4)
        builtin = partprops.load_schema(extra_layers=[], user_layer=False)
        self.assertFalse({e["name"] for e in entries} & set(builtin.properties))
        self.assertEqual(vs.validate_properties(entries), [])

    def test_bad_entries(self):
        def error(entries, word):
            issues = vs.validate_properties(entries)
            self.assertTrue(issues and issues[0].level == "error", issues)
            self.assertIn(word, issues[0].text)

        error([{"name": "Finish", "type": "string"}], "built-in")
        error([{"name": "Label", "type": "string"}], "FreeCAD")
        error([{"name": "A", "type": "string"}, {"name": "A", "type": "int"}], "twice")
        error([{"name": "A", "type": "colour"}], "unknown type")
        error([{"name": "A", "type": "enum"}], "needs values")
        error([{"name": "Bad name", "type": "string"}], "not a valid property name")
        error([{"name": "A", "type": "string", "required": "always"}], "required")
        error(["A"], "object")
        self.assertEqual(vs.validate_properties([]), [])


class CreateTests(_Tmp):
    def test_files_and_numbering(self):
        setup = vs.VaultSetup(
            name="Shop",
            folder=self.path("vault"),
            numbering=vs.NumberingSetup(
                programs=[vs.ProgramSetup("bw", "BruceWorx"), vs.ProgramSetup("LE")],
                document_types={"01": "Part", "02": "Assembly"},
                first_parent=2001,
            ),
            properties=vs.suggested_properties(),
        )
        vault = vs.create_vault(setup, actor="bruce@mac")
        self.assertTrue(vault.ref_path.endswith("Shop.bwvault"))
        self.assertEqual(vs.numbering_path(vault.root), self.path("vault", "numbering.json"))
        self.assertEqual(vs.numbering_path(vault.ref_path), self.path("vault", "numbering.json"))
        with open(vs.numbering_path(vault.root), encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["document_types_file"], "document_types.json")
        self.assertEqual(data["programs"]["BW"]["description"], "BruceWorx")
        self.assertEqual(data["programs"]["LE"]["counters"]["02"], {"next_parent": 2001, "parents": {}})
        # FND-08: a new vault numbers without revision (01, 02, ... then A belong to the item).
        self.assertEqual(data["format"], "{PROG}-{DT}-{PPPPP}-{SSS}")
        self.assertNotIn("revision", data["programs"]["LE"])
        self.assertNotIn("first_revision", data)
        with open(self.path("vault", "document_types.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"types": {"01": {"name": "Part"}, "02": {"name": "Assembly"}}})
        source = JsonNumberSource(vs.numbering_path(vault.root))
        self.assertEqual(source.programs(), ["BW", "LE"])
        self.assertEqual(source.document_types(), ["01", "02"])
        self.assertEqual(source.allocate("BW", "02"), "BW-02-02001-001")
        self.assertEqual(source.allocate("BW", "02"), "BW-02-02002-001")
        # The vault's properties are the schema's vault layer.
        self.assertEqual(partprops.vault_layers(vault.root), [self.path("vault", "part_properties.json")])
        schema = partprops.load_schema(extra_layers=partprops.vault_layers(vault.ref_path), user_layer=False)
        self.assertIn("ToleranceClass", schema.properties)
        self.assertEqual(schema.properties["ToleranceClass"].values[1], "ISO 2768-m")
        self.assertEqual(os.listdir(self.path("vault", "journal")), [])
        self.assertFalse([n for n in os.listdir(vault.root) if n.endswith(".tmp")])

    def test_no_properties_file_without_properties(self):
        vault = self.new_vault()
        self.assertFalse(os.path.exists(os.path.join(vault.root, "part_properties.json")))
        self.assertEqual(partprops.vault_layers(vault.root), [])

    def test_errors_write_nothing(self):
        bad = vs.VaultSetup(name="X", folder=self.path("v"), numbering=vs.NumberingSetup(first_parent=0))
        with self.assertRaises(ValueError):
            vs.create_vault(bad)
        bad = vs.VaultSetup(name="X", folder=self.path("v"), properties=[{"name": "Finish", "type": "int"}])
        with self.assertRaises(ValueError):
            vs.create_vault(bad)
        with self.assertRaises(ValueError):
            vs.create_vault(vs.VaultSetup(name=" ", folder=self.path("v")))
        self.assertFalse(os.path.exists(self.path("v")))
        self.new_vault()
        with self.assertRaisesRegex(ValueError, "already is a vault"):
            self.new_vault()

    def test_failure_after_init_leaves_a_valid_vault(self):
        original = vs._write_json

        def failing(path, data):
            if path.endswith("numbering.json"):
                raise OSError("disk full")
            original(path, data)

        vs._write_json = failing
        self.addCleanup(setattr, vs, "_write_json", original)
        with self.assertRaisesRegex(OSError, "disk full"):
            self.new_vault()
        self.assertEqual(FolderVault(self.path("vault")).status()["vault"], "Shop")


class OpenVaultTests(_Tmp):
    def test_gui_uses_the_open_vaults_numbering(self):
        vault = self.new_vault()
        self.assertEqual(gui.vault_numbering_path(vault.root), vs.numbering_path(vault.root))
        self.assertEqual(gui.vault_numbering_path(vault.ref_path), vs.numbering_path(vault.root))
        self.assertEqual(gui.vault_numbering_path(""), "")
        bare = FolderVault.init(self.path("bare"), "Bare", actor="a")
        self.assertEqual(gui.vault_numbering_path(bare.root), "")

    def test_load_schema_takes_the_vault_layer_from_preferences(self):
        vault = vs.create_vault(vs.VaultSetup(name="P", folder=self.path("vault"),
                                              properties=vs.suggested_properties()), actor="a")
        group = types.SimpleNamespace(GetString=lambda name, default="": vault.ref_path)
        fake = types.SimpleNamespace(ParamGet=lambda path: group)
        saved = sys.modules.get("FreeCAD")
        sys.modules["FreeCAD"] = fake
        self.addCleanup(lambda: sys.modules.pop("FreeCAD") if saved is None
                        else sys.modules.__setitem__("FreeCAD", saved))
        self.assertEqual(partprops.open_vault_path(), vault.ref_path)
        self.assertIn("Customer", partprops.load_schema(user_layer=False).properties)
        self.assertNotIn("Customer", partprops.load_schema(extra_layers=[], user_layer=False).properties)
        sys.modules["FreeCAD"] = types.SimpleNamespace()  # no parameters: no vault layer
        self.assertNotIn("Customer", partprops.load_schema(user_layer=False).properties)


PART_OBJECTS = [("Body", "PartDesign::Body", {"Label": "Body"}), ("Pad", "PartDesign::Pad", {"Label": "Pad"})]


class ImportTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.vault = self.new_vault()
        src = self.path("old")
        self.src = src
        self.bracket = make_fcstd(os.path.join(src, "bracket.FCStd"), PART_OBJECTS)
        self.plate = make_fcstd(os.path.join(src, "sub", "plate.FCStd"), [
            ("Part", "App::Part", {"Label": "Base Plate", "PartNumber": "BW-01-01003-002"}),
            ("Body", "PartDesign::Body", {"Label": "Body"}),
        ])
        self.assy = make_fcstd(os.path.join(src, "frame.FCStd"), [
            ("Assembly", "Assembly::AssemblyObject", {"Label": "Frame"}),
            ("Link", "App::Link", {"Label": "bracket"}),
        ], links=["bracket.FCStd"])
        self.drawing = make_fcstd(os.path.join(src, "bracket-dwg.FCStd"), [
            ("Page", "TechDraw::DrawPage", {"Label": "Page"}),
        ], links=["bracket.FCStd"])
        self.orphan = make_fcstd(os.path.join(src, "orphan-dwg.FCStd"), [
            ("Page", "TechDraw::DrawPage", {"Label": "Page"}),
        ], links=["gone.FCStd"])
        self.odd = make_fcstd(os.path.join(src, "odd.FCStd"), [
            ("Part", "App::Part", {"Label": "Odd", "PartNumber": "12345"}),
        ])
        shutil.copyfile(self.bracket, os.path.join(src, "sub", "bracket copy.FCStd"))
        open(os.path.join(src, "bracket.FCBak"), "wb").close()
        open(os.path.join(src, "bracket.FCStd1"), "wb").close()
        with open(os.path.join(src, "broken.FCStd"), "wb") as handle:
            handle.write(b"not a zip")
        make_fcstd(os.path.join(src, ".hidden", "secret.FCStd"), PART_OBJECTS)
        other = FolderVault.init(os.path.join(src, "othervault"), "Other", actor="a")
        make_fcstd(os.path.join(other.root, "inside.FCStd"), PART_OBJECTS)

    def by_name(self, candidates):
        return {os.path.basename(c.path): c for c in candidates}

    def test_scan(self):
        found = self.by_name(vs.scan_for_import(self.src, document_types=vs.DEFAULT_DOCUMENT_TYPES))
        self.assertEqual(sorted(found), sorted([
            "bracket.FCStd", "plate.FCStd", "frame.FCStd", "bracket-dwg.FCStd", "orphan-dwg.FCStd",
            "odd.FCStd", "bracket copy.FCStd", "bracket.FCBak", "bracket.FCStd1", "broken.FCStd",
        ]))
        self.assertEqual([found[n].kind for n in ("bracket.FCStd", "plate.FCStd", "frame.FCStd",
                                                  "bracket-dwg.FCStd")],
                         ["part", "part", "assembly", "drawing"])
        self.assertEqual(found["bracket.FCStd"].label, "bracket")  # "Body" says nothing
        self.assertEqual(found["plate.FCStd"].label, "Base Plate")
        self.assertEqual(found["frame.FCStd"].label, "Frame")
        self.assertEqual(found["plate.FCStd"].existing_part_number, "BW-01-01003-002")
        self.assertEqual(found["plate.FCStd"].issues, [])
        self.assertEqual(found["odd.FCStd"].existing_part_number, "12345")
        self.assertEqual(found["odd.FCStd"].issues[0].level, "warning")
        self.assertEqual([found[n].document_type for n in ("bracket.FCStd", "frame.FCStd", "bracket-dwg.FCStd")],
                         ["01", "02", "03"])
        for name in ("bracket.FCBak", "bracket.FCStd1", "broken.FCStd", "bracket copy.FCStd"):
            self.assertFalse(found[name].include, name)
            self.assertTrue(found[name].issues, name)
        self.assertIn("Same content", found["bracket copy.FCStd"].issues[0].text)
        self.assertEqual(len([c for c in found.values() if c.include]), 6)
        flat = self.by_name(vs.scan_for_import(self.src, recursive=False))
        self.assertNotIn("plate.FCStd", flat)
        self.assertEqual(flat["bracket.FCStd"].document_type, "")
        self.assertEqual(vs.scan_for_import(self.vault.root), [])

    def test_document_type_by_name(self):
        self.assertEqual(vs.document_type_for("drawing", {"10": "Model", "20": "DRAWING"}), "20")
        self.assertEqual(vs.document_type_for("assembly", {"10": "Model", "20": "Drawing"}), "10")

    def test_plan_previews_without_writing(self):
        numbering_file = vs.numbering_path(self.vault.root)
        before = digest(numbering_file)
        candidates = vs.scan_for_import(self.src)
        vs.plan_numbers(candidates, self.vault.root, "bw")
        found = self.by_name(candidates)
        # New parents come after the kept number's parent (01003).
        self.assertEqual(found["bracket.FCStd"].proposed_part_number, "BW-01-01004-001")
        self.assertEqual(found["odd.FCStd"].proposed_part_number, "BW-01-01005-001")
        self.assertEqual(found["plate.FCStd"].proposed_part_number, "BW-01-01003-002")
        self.assertEqual(found["frame.FCStd"].proposed_part_number, "BW-02-01001-001")
        self.assertEqual(found["bracket-dwg.FCStd"].proposed_part_number, "BW-03-01001-001")
        self.assertEqual(found["orphan-dwg.FCStd"].proposed_part_number, "")
        self.assertIn("skipped", found["orphan-dwg.FCStd"].issues[0].text)
        self.assertEqual(digest(numbering_file), before)
        numbers = [c.proposed_part_number for c in candidates if c.proposed_part_number]
        self.assertEqual(len(numbers), len(set(numbers)))
        # Planning again replaces the plan's own issues.
        vs.plan_numbers(candidates, self.vault.root, "BW")
        self.assertEqual(len(found["orphan-dwg.FCStd"].issues), 1)
        self.assertEqual(found["odd.FCStd"].issues[0].level, "warning")  # the scan's stays
        self.assertEqual(digest(numbering_file), before)

    def test_plan_flags_numbers_the_vault_has(self):
        self.vault.create_item("BW-01-01003-002", "part")
        candidates = vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW")
        plate = self.by_name(candidates)["plate.FCStd"]
        self.assertFalse(plate.include)
        self.assertEqual(plate.issues[-1].level, "error")

    def test_run_import(self):
        originals = tree_hashes(self.src)
        numbering_file = vs.numbering_path(self.vault.root)
        candidates = vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW")
        written, steps = [], []

        def writer(path, part_number):
            self.assertEqual(os.path.dirname(path), os.path.join(self.vault.root, ".import"))
            self.assertEqual(os.path.basename(path), part_number + ".FCStd")
            with zipfile.ZipFile(path, "a") as zf:
                zf.writestr("BruceWorx.txt", part_number)
            # FreeCAD leaves backups next to a file it saves; the import must clear them.
            for backup in (".FCBak", ".20261023-101500.FCBak"):
                with open(os.path.join(os.path.dirname(path), part_number + backup), "w") as handle:
                    handle.write("backup")
            written.append(part_number)

        report = vs.run_import(self.vault, candidates, "BW", writer=writer,
                               progress=lambda done, total, path: steps.append((done, total)))
        self.assertEqual(tree_hashes(self.src), originals)
        imported = {os.path.basename(p): pn for p, pn in report.imported}
        self.assertEqual(imported, {
            "bracket.FCStd": "BW-01-01004-001",
            "odd.FCStd": "BW-01-01005-001",
            "plate.FCStd": "BW-01-01003-002",
            "frame.FCStd": "BW-02-01001-001",
            "bracket-dwg.FCStd": "BW-03-01001-001",
        })
        self.assertEqual(report.failed, [])
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, ".import")))
        skipped = {os.path.basename(p): why for p, why in report.skipped}
        self.assertIn("orphan-dwg.FCStd", skipped)
        self.assertIn("bracket copy.FCStd", skipped)
        self.assertEqual(sorted(written), sorted(imported.values()))
        self.assertEqual(steps, [(i, 6) for i in range(1, 7)])
        # Counters moved once per new number; the kept parent 01003 is recorded.
        with open(numbering_file, encoding="utf-8") as handle:
            counters = json.load(handle)["programs"]["BW"]["counters"]
        self.assertEqual(counters["01"]["next_parent"], 1006)
        self.assertEqual(sorted(counters["01"]["parents"]), ["01003", "01004", "01005"])
        self.assertEqual(counters["01"]["parents"]["01003"], {"next_sub": 3})
        self.assertEqual(counters["02"]["next_parent"], 1002)
        self.assertEqual(counters["03"]["next_parent"], 1002)
        # Vault items, files and the drawing link.
        again = FolderVault(self.vault.root, actor="bruce@mac")
        self.assertEqual(again.problems, [])
        self.assertEqual(again.items["BW-02-01001-001"].item_type, "assembly")
        self.assertEqual(again.items["BW-03-01001-001"].drawing_of, "BW-01-01004-001")
        self.assertEqual(again.items["BW-01-01003-002"].description, "Base Plate")
        version = again.latest_version("BW-01-01004-001")
        self.assertEqual(version.filename, "BW-01-01004-001.FCStd")
        self.assertEqual(version.comment, "Imported from " + self.bracket)
        self.assertEqual(again.locks, {})
        fetched = again.fetch("BW-01-01004-001", self.path("ws"))
        with zipfile.ZipFile(fetched) as zf:
            self.assertEqual(zf.read("BruceWorx.txt").decode(), "BW-01-01004-001")
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, ".import")))
        # A second import of the same folder finds everything already there.
        replan = self.by_name(vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW"))
        for name in imported:
            self.assertFalse(replan[name].include, name)
            self.assertIn("Already in the vault", replan[name].issues[-1].text)

    def test_failures_continue_and_stop(self):
        candidates = vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW")

        def writer(path, part_number):
            if part_number == "BW-01-01004-001":
                raise RuntimeError("cannot open")

        report = vs.run_import(self.vault, candidates, "BW", writer=writer,
                               progress=lambda done, total, path: done < 3)
        self.assertEqual([(os.path.basename(p), e) for p, e in report.failed], [("bracket.FCStd", "cannot open")])
        self.assertEqual(len(report.imported), 2)
        self.assertEqual(sum(why == "import stopped" for _p, why in report.skipped), 3)
        self.assertNotIn("BW-01-01004-001", self.vault.items)
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, ".import")))


class CliTests(_Tmp):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        line = [ln for ln in out.getvalue().splitlines() if ln.startswith("BW_RESULT ")]
        return code, json.loads(line[-1][len("BW_RESULT "):]) if line else None

    def test_init_with_numbering_and_import(self):
        props = self.path("props.json")
        with open(props, "w", encoding="utf-8") as handle:
            json.dump(vs.suggested_properties()[:2], handle)
        code, result = self.run_cli(
            "vault", "init", "--vault", self.path("v"), "--name", "Shop", "--actor", "a",
            "--program", "LE,BW", "--document-types", "01=Part,02=Assembly", "--first-parent", "500",
            "--properties-file", props)
        self.assertEqual(code, 0)
        self.assertEqual(result["example"], "LE-01-00500-001")
        self.assertTrue(os.path.isfile(self.path("v", "part_properties.json")))
        make_fcstd(self.path("old", "a.FCStd"), PART_OBJECTS)
        before = digest(self.path("v", "numbering.json"))
        code, result = self.run_cli("vault", "import", "--vault", self.path("v"), "--from", self.path("old"),
                                    "--program", "LE", "--dry-run", "--actor", "a")
        self.assertEqual(code, 0)
        self.assertEqual(result["candidates"][0]["proposed_part_number"], "LE-01-00500-001")
        self.assertEqual(digest(self.path("v", "numbering.json")), before)
        code, result = self.run_cli("vault", "import", "--vault", self.path("v"), "--from", self.path("old"),
                                    "--program", "LE", "--actor", "a")
        self.assertEqual(code, 0)
        self.assertEqual(result["imported"], [[self.path("old", "a.FCStd"), "LE-01-00500-001"]])
        self.assertFalse(result["part_numbers_written"])

    def test_plain_init_unchanged(self):
        code, result = self.run_cli("vault", "init", "--vault", self.path("bare"), "--actor", "a")
        self.assertEqual(code, 0)
        self.assertNotIn("numbering", result)
        self.assertEqual(sorted(os.listdir(self.path("bare"))), ["bare.bwvault", "journal", "objects"])
        code, _result = self.run_cli("vault", "init", "--vault", self.path("bad"), "--document-types", "01")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
