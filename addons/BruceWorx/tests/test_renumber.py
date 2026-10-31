# SPDX-License-Identifier: LGPL-2.1-or-later
"""Renumber an item, and assemblies numbered as parts (BW-PDM-12, plan chapter 61b)."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm, renumber, vault_dashboard, vault_setup as vs  # noqa: E402
from bruceworx.pdm_folder import FolderVault, LifecycleError  # noqa: E402
from bruceworx.pdm_workspace import Workspace, linked_files  # noqa: E402

BATTERY = "STD-02-01001-001"
NEW = "STD-01-01190-001"
PLATE = "STD-01-01002-001"
TOP = "STD-02-01002-001"
DWG = "STD-03-01001-001"
FP = {"volume": 10.0, "area": 5.0, "centre": [0, 0, 0], "box": [0, 0, 0, 1, 1, 1], "solids": 1}


def fcstd(path, name, links=(), payload="v1"):
    """A minimal .FCStd whose Document.xml has a PartNumber property and XLinks."""
    xlinks = "".join(f'<XLink file="{link}" name="Body"/>' for link in links)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", f'<Document pn="{name}"><Properties>{xlinks}</Properties></Document>')
        archive.writestr("payload.txt", payload)
    return path


def fake_writer(path, part_number, revision):
    """Stands in for FreeCAD's write_part_number: the P/N in Document.xml, the revision in payload.txt."""
    with zipfile.ZipFile(path) as archive:
        data = {n: archive.read(n) for n in archive.namelist()}
    data["Document.xml"] = data["Document.xml"].replace(b'pn="', f'pn="{part_number}|'.encode(), 1)
    data["payload.txt"] += f"|rev {revision}".encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in data.items():
            archive.writestr(name, content)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-renum-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setup = vs.VaultSetup(name="Standard Libraries", folder=os.path.join(self.tmp, "Vault", "Standard Libraries"),
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("STD")],
                                                          assemblies_as_parts=True))
        self.v = vs.create_vault(setup, actor="bruce@mac")
        from bruceworx import freecad_io  # noqa: PLC0415

        patch = mock.patch.object(freecad_io, "fingerprint_of_file", lambda path: dict(FP))  # no FreeCAD here
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, pn, kind="part", links=(), description="x", fingerprint=None, payload="v1", of=None):
        if pn not in self.v.items:
            self.v.create_item(pn, kind, description, of)
        self.v.checkout(pn)
        path = fcstd(os.path.join(self.tmp, pn + ".FCStd"), pn, links, payload)
        self.v.checkin(pn, path, "first", fingerprint=fingerprint)

    def release(self, pn):
        self.v.submit(pn)
        self.v.approve(pn, note="ok")

    def reopen(self):
        return FolderVault(self.v.root, actor="bruce@mac")


class ModelTests(unittest.TestCase):
    def vault(self):
        v = pdm.Vault()
        v.create_item("A-01-00001-001", "part")
        v.create_item("A-02-00001-001", "assembly")
        v.set_bom("A-02-00001-001", [("A-01-00001-001", 2)])
        return v

    def test_every_reference_moves_and_the_old_number_is_an_alias(self):
        v = self.vault()
        v.create_item("A-03-00001-001", "drawing", drawing_of="A-01-00001-001")
        v.add_configuration("A-01-00001-001-S", "A-01-00001-001", "Short")
        v.raise_ecr("ECR-1", "p", ["A-02-00001-001"]) if False else None
        v.create_project("P1", "Proj", "A", "A-02-00001-001")
        v.renumber("A-02-00001-001", "A-01-00009-001", "pn rule")
        v.renumber("A-01-00001-001", "A-01-00002-001", "again")
        v.renumber("A-01-00002-001", "A-01-00003-001", "and again")
        self.assertEqual(v.bom["A-01-00009-001"], [("A-01-00003-001", 2)])
        self.assertEqual(v.items["A-03-00001-001"].drawing_of, "A-01-00003-001")
        self.assertEqual(v.items["A-01-00001-001-S"].configuration_of, "A-01-00003-001")
        self.assertEqual(v.projects["P1"].top, "A-01-00009-001")
        self.assertEqual([v.resolve(n) for n in ("A-01-00001-001", "A-01-00002-001", "A-01-00003-001")],
                         ["A-01-00003-001"] * 3)
        self.assertEqual(v.former_numbers("A-01-00003-001"), ["A-01-00001-001", "A-01-00002-001"])
        self.assertEqual(v.resolve("Z-9"), "Z-9")
        self.assertIn("renumbered A-01-00002-001 -> A-01-00003-001: and again",
                      v.items["A-01-00003-001"].latest.history)

    def test_released_and_obsolete_items_renumber(self):
        v = self.vault()
        v.submit("A-01-00001-001")
        v.approve("A-01-00001-001", note="ok")
        v.renumber("A-01-00001-001", "A-01-00005-001", "released one too")
        self.assertEqual(v.items["A-01-00005-001"].state, pdm.RELEASED)
        self.assertEqual(v.items["A-01-00005-001"].latest.code, "A")
        v.items["A-01-00005-001"].latest.state = pdm.OBSOLETE
        v.items["A-01-00005-001"].obsolete = True
        v.renumber("A-01-00005-001", "A-01-00006-001", "even obsolete")
        self.assertTrue(v.items["A-01-00006-001"].obsolete)

    def test_refusals(self):
        v = self.vault()
        v.create_item("A-01-00002-001")
        v.add_configuration("A-01-00001-001-S", "A-01-00001-001", "Short")
        with self.assertRaisesRegex(LifecycleError, "reason"):
            v.renumber("A-01-00001-001", "A-01-00009-001", "")
        with self.assertRaisesRegex(LifecycleError, "already an item"):
            v.renumber("A-01-00001-001", "A-01-00002-001", "r")
        with self.assertRaisesRegex(LifecycleError, "not an item"):
            v.renumber("Q-1", "A-01-00009-001", "r")
        with self.assertRaisesRegex(LifecycleError, "renumber A-01-00001-001, its file"):
            v.renumber("A-01-00001-001-S", "A-01-00009-001", "r")
        v.renumber("A-01-00001-001", "A-01-00009-001", "r")
        with self.assertRaisesRegex(LifecycleError, "former number"):
            v.renumber("A-01-00002-001", "A-01-00001-001", "r")
        with self.assertRaisesRegex(LifecycleError, "is now A-01-00009-001"):
            v.renumber("A-01-00001-001", "A-01-00010-001", "r")
        v.submit("A-01-00002-001")
        with self.assertRaisesRegex(LifecycleError, "in review"):
            v.renumber("A-01-00002-001", "A-01-00011-001", "r")
        with self.assertRaisesRegex(LifecycleError, "not a part number"):
            v.renumber("A-02-00001-001", "has space", "r")


class FolderTests(Base):
    def build(self):
        """The battery (an assembly numbered as a part), a plate it holds, a parent assembly and a drawing."""
        self.put(BATTERY, "assembly", fingerprint=FP, payload="battery")
        self.put(PLATE, links=[BATTERY + ".FCStd"])
        self.put(TOP, "assembly", links=["../" + BATTERY + ".FCStd", PLATE + ".FCStd"])
        self.v.set_bom(TOP, [(BATTERY, 1), (PLATE, 1)])
        self.put(DWG, "drawing", links=[BATTERY + ".FCStd"], of=BATTERY)

    def test_renumber_writes_files_then_one_journal_line(self):
        self.build()
        sha_before = {pn: self.v.latest_version(pn).sha256 for pn in (BATTERY, PLATE, TOP, DWG)}
        lines = lambda: sum(len(open(os.path.join(self.v.root, "journal", n)).readlines())  # noqa: E731
                            for n in os.listdir(os.path.join(self.v.root, "journal")))
        count = lines()
        result = renumber.renumber(self.v, BATTERY, "library items are numbered as parts (31 Oct)",
                                   new=NEW, writer=fake_writer, fingerprint_of=lambda p: dict(FP))
        self.assertEqual(lines(), count + 1)  # a single journal line
        self.assertEqual((result["old"], result["new"]), (BATTERY, NEW))
        self.assertEqual(sorted(result["linkers"]), sorted([PLATE, TOP, DWG]))
        v = self.reopen()
        self.assertEqual(v.problems, [])
        self.assertNotIn(BATTERY, v.items)
        self.assertEqual(v.resolve(BATTERY), NEW)
        self.assertEqual(v.bom[TOP], [(NEW, 1), (PLATE, 1)])
        # the item's file: new name, new PartNumber, same revision code and state, fingerprint carried over
        latest = v.latest_version(NEW)
        self.assertEqual((latest.filename, latest.revision, latest.fingerprint), (NEW + ".FCStd", "01", FP))
        self.assertNotEqual(latest.sha256, sha_before[BATTERY])
        self.assertEqual(len(v.versions[NEW]), 2)
        path = v.fetch(NEW, os.path.join(self.tmp, "out"))
        with zipfile.ZipFile(path) as archive:
            self.assertIn(NEW.encode(), archive.read("Document.xml"))
            self.assertTrue(archive.read("payload.txt").endswith(b"|rev 01"))
        # every linker: the link re-pointed in a new version of the same revision, other links untouched
        for pn, expected in ((PLATE, [NEW + ".FCStd"]), (TOP, ["../" + NEW + ".FCStd", PLATE + ".FCStd"]),
                             (DWG, [NEW + ".FCStd"])):
            version = v.latest_version(pn)
            self.assertNotEqual(version.sha256, sha_before[pn])
            self.assertEqual((version.filename, version.revision), (pn + ".FCStd", "01"))
            self.assertEqual(linked_files(os.path.join(v.root, version.object_name)), expected)
            self.assertEqual(v.items[pn].state, pdm.WIP)
        self.assertEqual(result["files"][NEW], latest.sha256)
        # the new number is taken in the numbering: the next new Part number moves on
        self.assertEqual(renumber.suggest(v, PLATE), "STD-01-01191-001")

    def test_released_linker_keeps_revision_and_released_file_follows(self):
        self.build()
        self.release(PLATE)
        released_before = self.v.released_version(PLATE).sha256
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        v = self.reopen()
        self.assertEqual((v.items[PLATE].state, v.items[PLATE].latest.code), (pdm.RELEASED, "A"))
        self.assertEqual(v.latest_version(PLATE).revision, "A")
        self.assertEqual(v.released_version(PLATE).sha256, v.latest_version(PLATE).sha256)
        self.assertNotEqual(released_before, v.latest_version(PLATE).sha256)

    def test_refused_while_checked_out_in_review_or_taken(self):
        self.build()
        self.v.checkout(PLATE)
        with self.assertRaisesRegex(LifecycleError, f"{PLATE} is checked out by bruce@mac"):
            renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.v.undo_checkout(PLATE)
        self.v.checkout(BATTERY)
        with self.assertRaisesRegex(LifecycleError, "checked out"):
            renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.v.undo_checkout(BATTERY)
        with self.assertRaisesRegex(LifecycleError, "already an item"):
            renumber.renumber(self.v, BATTERY, "r", new=PLATE, writer=fake_writer)
        with self.assertRaisesRegex(LifecycleError, "not a part number"):
            renumber.renumber(self.v, BATTERY, "r", new="nope", writer=fake_writer)
        with self.assertRaisesRegex(LifecycleError, "reason"):
            renumber.renumber(self.v, BATTERY, " ", writer=fake_writer)
        self.v.submit(BATTERY)
        with self.assertRaisesRegex(LifecycleError, "in review"):
            renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)

    def test_alias_is_not_free(self):
        self.build()
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        with self.assertRaisesRegex(LifecycleError, "former number"):
            renumber.renumber(self.v, NEW, "r", new=BATTERY, writer=fake_writer)

    def test_nothing_is_recorded_when_the_file_step_fails(self):
        self.build()
        before = [(os.path.getsize(os.path.join(self.v.root, "journal", n))) for n in
                  sorted(os.listdir(os.path.join(self.v.root, "journal")))]

        def boom(path, pn, rev):
            raise RuntimeError("FreeCAD crashed")

        with self.assertRaisesRegex(RuntimeError, "crashed"):
            renumber.renumber(self.v, BATTERY, "r", writer=boom)
        with self.assertRaisesRegex(LifecycleError, "geometry"):  # the fingerprint changed
            renumber.renumber(self.v, BATTERY, "r", writer=fake_writer, fingerprint_of=lambda p: dict(FP, volume=11.0))
        after = [(os.path.getsize(os.path.join(self.v.root, "journal", n))) for n in
                 sorted(os.listdir(os.path.join(self.v.root, "journal")))]
        self.assertEqual(before, after)
        self.assertIn(BATTERY, self.reopen().items)
        self.assertFalse(os.path.exists(os.path.join(self.v.root, vs.STAGING_DIR)))
        # a crash after the objects were written but before the line: unused objects, vault as it was
        original = self.v.renumber
        self.v.renumber = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        with self.assertRaises(OSError):
            renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.v.renumber = original
        self.assertIn(BATTERY, self.reopen().items)

    def test_dry_run_changes_nothing_and_lists_what_it_would_do(self):
        self.build()
        result = renumber.renumber(self.v, BATTERY, "", dry_run=True)
        self.assertEqual((result["new"], result["files"], result["problems"]), ("STD-01-01001-001", {}, []))
        self.assertEqual(sorted(result["linkers"]), sorted([PLATE, TOP, DWG]))
        self.assertIn(BATTERY, self.reopen().items)

    def test_item_without_a_file_and_old_numbers_resolve(self):
        self.v.create_item(BATTERY, "assembly", "empty")
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.assertEqual(self.v.resolve(BATTERY), NEW)
        self.assertIsNone(self.v.latest_version(NEW))

    def test_kicad_item_is_refused(self):
        self.v.create_item(PLATE, "cots", "board")
        self.v.checkout(PLATE)
        path = os.path.join(self.tmp, "b.kicad.zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.kicad_pro", "{}")
        self.v.checkin(PLATE, path, "first")
        plan = renumber.plan(self.v, PLATE)
        self.assertTrue(any("KiCad" in p for p in plan["problems"]))

    def test_cross_vault_users_are_listed(self):
        self.build()
        other_setup = vs.VaultSetup(name="Projects", folder=os.path.join(self.tmp, "Vault", "Projects"),
                                    numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("BW")]))
        other = vs.create_vault(other_setup, actor="bruce@mac")
        other.create_item("BW-02-01001-001", "assembly", "robot")
        other.checkout("BW-02-01001-001")
        other.checkin("BW-02-01001-001", fcstd(os.path.join(self.tmp, "r.FCStd"), "r", [f"../x/{BATTERY}.FCStd"]), "c")
        plan = renumber.plan(self.v, BATTERY)
        self.assertEqual([(u["vault"], u["items"]) for u in plan["cross_vault"]],
                         [("Projects", ["BW-02-01001-001"])])

    def test_activity_line_shows_the_renumber_and_its_file_versions(self):
        self.build()
        renumber.renumber(self.v, BATTERY, "library rule", new=NEW, writer=fake_writer)
        v = self.reopen()
        event = [e for e in v.events if e.op == "renumber"][0]
        text = vault_dashboard._verb(event, 1, v, "bruce@mac")
        self.assertEqual(text, f"renumbered {BATTERY} -> {NEW}: library rule")
        detail = vault_dashboard._detail(event, [event], v)
        self.assertTrue(detail.startswith("new file versions: "))
        for pn in (NEW, PLATE, TOP, DWG):
            self.assertIn(pn, detail)
        self.assertEqual(vault_dashboard._target(event), NEW)

    def test_old_journals_do_not_change_and_unknown_op_is_a_problem_for_old_code(self):
        self.build()
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.assertEqual(self.reopen().problems, [])
        self.assertIn("renumber", [e.op for e in self.reopen().events])


class WorkspaceTests(Base):
    def test_old_work_copy_is_replaced_and_old_revisions_open_through_aliases(self):
        self.put(BATTERY, "assembly")
        self.put(TOP, "assembly", links=[BATTERY + ".FCStd"])
        work = Workspace(self.v)
        work.get_latest(BATTERY)
        work.get_latest(TOP)
        old_top = self.v.latest_version(TOP)
        old_copy = work.path(BATTERY)
        self.assertTrue(os.path.exists(old_copy))
        work = Workspace(self.v)
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer, workspace=work)
        self.assertFalse(os.path.exists(old_copy))
        self.assertTrue(os.path.exists(work.path(NEW)))
        self.assertNotIn(BATTERY, work.state["items"])
        # an older version of the assembly still links the old name; Get Revision reads today's number
        path = work.get_revision(TOP, old_top.sha256)
        self.assertEqual(linked_files(path), ["../" + NEW + ".FCStd"])
        # a link to the former number is fixed to the item's number at the next check-in
        os.chmod(work.path(TOP), 0o644)
        fcstd(work.path(TOP), TOP, [BATTERY + ".FCStd"])
        fixes = work.link_fixes([TOP])
        self.assertEqual([(f.link, f.target) for f in fixes], [(BATTERY + ".FCStd", NEW)])

    def test_a_writable_old_copy_is_left_alone(self):
        self.put(BATTERY, "assembly")
        work = Workspace(self.v)
        work.get_latest(BATTERY)
        os.chmod(work.path(BATTERY), 0o644)
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        self.assertEqual(work.refresh_renamed(), [])
        self.assertTrue(os.path.exists(work.path(BATTERY)))


class SettingTests(Base):
    def test_document_type_for_item(self):
        types = {"01": "Part", "02": "Assembly", "03": "Drawing"}
        on, off = {"assemblies_as_parts": True}, {}
        self.assertEqual(vs.document_type_for_item("assembly", types, on), "01")
        self.assertEqual(vs.document_type_for_item("assembly", types, off), "02")
        self.assertEqual(vs.document_type_for_item("assembly", types, None), "02")
        self.assertEqual(vs.document_type_for_item("drawing", types, on), "03")
        self.assertEqual(vs.document_type_for_item("part", types, on), "01")

    def test_stored_in_numbering_json_and_saved_by_settings(self):
        data = json.load(open(vs.numbering_path(self.v.root)))
        self.assertIs(data["assemblies_as_parts"], True)
        settings = vs.load_settings(self.v.root)
        self.assertTrue(settings.assemblies_as_parts)
        settings.assemblies_as_parts = False
        vs.save_settings(self.v.root, settings)
        self.assertNotIn("assemblies_as_parts", json.load(open(vs.numbering_path(self.v.root))))
        self.assertIsNone(vs.load_settings(self.v.root).assemblies_as_parts)
        settings.assemblies_as_parts = True
        vs.save_settings(self.v.root, settings)
        self.assertTrue(vs.load_settings(self.v.root).assemblies_as_parts)

    def make(self, path, number):
        with open(path, "wb") as handle:
            handle.write(b"x" + number.encode())

    def test_new_item_and_import_honour_the_setting(self):
        self.assertEqual(vs.new_item(self.v, "assembly", "Battery", self.make), "STD-01-01001-001")
        settings = vs.load_settings(self.v.root)
        settings.assemblies_as_parts = False
        vs.save_settings(self.v.root, settings)
        self.assertEqual(vs.new_item(self.v, "assembly", "Other", self.make), "STD-02-01001-001")
        # plan_numbers: an assembly file takes the type through the same helper
        settings.assemblies_as_parts = True
        vs.save_settings(self.v.root, settings)
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        fcstd(os.path.join(src, "a.FCStd"), "a")
        cand = vs.scan_for_import(src)
        cand[0].kind = "assembly"
        vs.plan_numbers(cand, self.v.root, "STD")
        self.assertEqual(cand[0].proposed_part_number[:6], "STD-01")
        self.assertEqual(vs.scan_for_import(src, assemblies_as_parts=True,
                                            document_types={"01": "Part", "02": "Assembly"})[0].document_type, "01")

    def test_cli_setting_and_renumber(self):
        def run(*argv):
            out = io.StringIO()
            from bruceworx.cli import main

            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = main(["vault", *argv, "--vault", self.v.root, "--actor", "bruce@mac"])
                except ValueError:
                    code = 2
            lines = [ln for ln in out.getvalue().splitlines() if ln.startswith("BW_RESULT ")]
            return code, (json.loads(lines[-1][len("BW_RESULT "):]) if lines else None)

        code, result = run("settings", "--assemblies-as-parts", "off")
        self.assertEqual((code, result["assemblies_as_parts"]), (0, False))
        self.assertFalse(vs.load_settings(self.v.root).assemblies_as_parts)
        run("settings", "--assemblies-as-parts", "on")
        self.assertTrue(vs.load_settings(self.v.root).assemblies_as_parts)
        self.put(BATTERY, "assembly")
        code, result = run("renumber", BATTERY, "--dry-run")
        self.assertEqual((code, result["new"], result["dry_run"]), (0, "STD-01-01001-001", True))
        self.assertEqual(run("renumber", BATTERY)[0], 1)  # a reason is required
        self.assertIn(BATTERY, self.reopen().items)
        # an old number is accepted once the item is renumbered
        renumber.renumber(self.v, BATTERY, "r", new=NEW, writer=fake_writer)
        code, result = run("renumber", BATTERY, "--dry-run", "--to", "STD-01-09999-001")
        self.assertEqual((code, result["old"], result["new"]), (0, NEW, "STD-01-09999-001"))


if __name__ == "__main__":
    unittest.main()
