# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad projects as vault items (ECD-05): pack, import, check-out/in of the project folder, launch.

A small synthetic project in a temporary Standard Libraries vault; kicad-cli is mocked away except
in TestRealKiCad (skipped without KiCad), which imports KiCad's Arduino_Mega template."""

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import ecad_vault as ev  # noqa: E402
from bruceworx import kicad_bridge as kb  # noqa: E402
from bruceworx import pdm_workspace as ws  # noqa: E402
from bruceworx import vault_setup as vs  # noqa: E402
from bruceworx.cli import main  # noqa: E402
from bruceworx.pdm_folder import FolderVault, LifecycleError  # noqa: E402

ME = "bruce@mac"
BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
 (general (thickness 1.6))
 (title_block (title "Tiny \\"test\\" board"))
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts"))
 (gr_line (start 20 0) (end 20 10) (layer "Edge.Cuts"))
 (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu") (at 5 5)
   (property "Reference" "R1") (property "Value" "10k")
   (model "${KIPRJMOD}/3dmodels/r.step") (model "${KIPRJMOD}/3dmodels/missing.step"))
)
"""


def make_project(folder, stem="Tiny", board=BOARD):
    os.makedirs(os.path.join(folder, "3dmodels"), exist_ok=True)
    files = {
        f"{stem}.kicad_pro": '{"meta": {"filename": "%s.kicad_pro"}}\n' % stem,
        f"{stem}.kicad_sch": '(kicad_sch (version 20250114) (title_block (title "Tiny schematic")))\n',
        f"{stem}.kicad_pcb": board,
        f"{stem}.kicad_prl": "{}\n",  # local view settings: not in the vault
        "fp-info-cache": "cache\n",
        "3dmodels/r.step": "ISO-10303-21;\n",
        f"{stem}-backups/{stem}-2026-10-30.zip": "old\n",
        "scripts/__pycache__/x.pyc": "x",
        "scripts/gen.py": "print('hi')\n",
        f"~{stem}.kicad_sch.lck": "lock",
    }
    for rel, text in files.items():
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return folder


def make_library(tmp):
    setup = vs.VaultSetup(name="Standard Libraries", folder=os.path.join(tmp, "Vault", "Standard Libraries"))
    setup.numbering.programs = [vs.ProgramSetup("STD", "Standard parts")]
    return vs.create_vault(setup, actor=ME)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-ecad-")
        self.vault = make_library(self.tmp)
        self.work = ws.Workspace(self.vault)
        self.src = make_project(os.path.join(self.tmp, "src", "Tiny"))
        patcher = mock.patch.object(kb, "find_cli", return_value=None)  # no kicad-cli: no renders
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def imported(self, **kw):
        kw.setdefault("comment", "Imported the tiny board")
        return ev.import_project(self.vault, self.src, workspace=self.work, **kw)


class TestFolder(Base):
    def test_project_files_leave_out_kicad_noise(self):
        files = ev.project_files(self.src)
        self.assertEqual(files, ["3dmodels/r.step", "Tiny.kicad_pcb", "Tiny.kicad_pro", "Tiny.kicad_sch",
                                 "scripts/gen.py"])

    def test_exclude(self):
        self.assertEqual(ev.project_files(self.src, ["scripts", "*.step"]),
                         ["Tiny.kicad_pcb", "Tiny.kicad_pro", "Tiny.kicad_sch"])
        self.assertTrue(ev.excluded("build/x/y.log", ["build/"]))
        self.assertFalse(ev.excluded("buildings.txt", ["build"]))

    def test_find_project(self):
        project = ev.find_project(self.src)
        self.assertEqual((project.stem, os.path.basename(project.pcb), os.path.basename(project.sch)),
                         ("Tiny", "Tiny.kicad_pcb", "Tiny.kicad_sch"))
        make_project(self.src, stem="Other")
        with self.assertRaisesRegex(ValueError, "2 KiCad projects"):
            ev.find_project(self.src)
        self.assertEqual(ev.find_project(self.src, "Other.kicad_pro").stem, "Other")
        with self.assertRaisesRegex(ValueError, "no KiCad project"):
            ev.find_project(os.path.join(self.src, "3dmodels"))

    def test_find_project_with_a_dot_in_its_name(self):
        make_project(self.src, stem="Leviathan_V1.3")
        for name in ("Leviathan_V1.3", "Leviathan_V1.3.kicad_pro", "Leviathan_V1.3.kicad_pcb"):
            self.assertEqual(ev.find_project(self.src, name).stem, "Leviathan_V1.3", name)

    def test_pack_is_deterministic_and_round_trips(self):
        a, b = os.path.join(self.tmp, "a.kicad.zip"), os.path.join(self.tmp, "b.kicad.zip")
        sha = ev.pack(self.src, a)
        os.utime(os.path.join(self.src, "Tiny.kicad_pcb"), (1, 1))  # times are not in the archive
        self.assertEqual(ev.pack(self.src, b), sha)
        with zipfile.ZipFile(a) as archive:
            self.assertEqual(archive.namelist(), ev.project_files(self.src))
        out = os.path.join(self.tmp, "out")
        ev.unpack(a, out)
        self.assertEqual(ev.tree_digest(out), ev.tree_digest(self.src))
        self.assertFalse(os.access(os.path.join(out, "Tiny.kicad_pcb"), os.W_OK))
        ev.set_writable(out, True)
        self.assertTrue(os.access(os.path.join(out, "Tiny.kicad_pcb"), os.W_OK))

    def test_unpack_keeps_kicad_locks_and_refuses_unsafe_names(self):
        out = os.path.join(self.tmp, "out")
        archive = os.path.join(self.tmp, "a.zip")
        ev.pack(self.src, archive)
        ev.unpack(archive, out, writable=True)
        with open(os.path.join(out, "~Tiny.kicad_sch.lck"), "w") as handle:
            handle.write("lock")
        with open(os.path.join(out, "stray.txt"), "w") as handle:
            handle.write("not in the version")
        ev.unpack(archive, out)
        self.assertTrue(os.path.exists(os.path.join(out, "~Tiny.kicad_sch.lck")))
        self.assertFalse(os.path.exists(os.path.join(out, "stray.txt")))
        bad = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(bad, "w") as handle:
            handle.writestr("../evil.txt", "x")
        with self.assertRaises(ValueError):
            ev.unpack(bad, os.path.join(self.tmp, "bad"))

    def test_title_and_item_for_path(self):
        self.assertEqual(ev.title_of(ev.find_project(self.src)), 'Tiny "test" board')
        res = self.imported()
        inside = os.path.join(res.work_copy, "3dmodels", "r.step")
        self.assertEqual(ev.item_for_path(inside), (self.vault.root, ME, res.part_number))
        self.assertIsNone(ev.item_for_path(self.src))


class TestLightModel(Base):
    """31 Oct: a light model per KiCad part (board and components), cached beside the full one."""

    def test_light_options(self):
        light = kb.light_options()
        self.assertTrue(kb.is_light(light))
        self.assertFalse(kb.is_light(None))
        self.assertFalse(kb.is_light({}))
        self.assertTrue(kb.step_options(light)["components"])
        self.assertFalse(any(light[k] for k in ("tracks", "pads", "zones", "silkscreen", "soldermask",
                                                 "inner_copper")))
        self.assertEqual(kb.step_args(kb.step_options(light), (0, 0))[:1], ["--no-dnp"])  # KiCad's default export
        board = os.path.join(self.src, "Tiny.kicad_pcb")
        self.assertNotEqual(kb.cache_key(board, light), kb.cache_key(board))

    def test_light_model_paths_and_stamps(self):
        pn = self.imported().part_number
        folder = self.work.path(pn)
        full, light = ev.derived_path(folder, pn), ev.derived_path(folder, pn, light=True)
        self.assertEqual(os.path.basename(light), pn + "-light.FCStd")
        self.assertEqual(os.path.dirname(light), os.path.dirname(full))
        extra = {"Revision": "01", "recipe": 1}
        s_full, s_light = ev.derived_stamp(folder, extra), ev.derived_stamp(folder, extra, light=True)
        self.assertNotEqual(s_full, s_light)
        for path in (full, light):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("model")
        ev.record_derived(folder, pn, s_light, light=True)
        self.assertTrue(ev.derived_fresh(folder, pn, s_light, light=True))
        self.assertFalse(ev.derived_fresh(folder, pn, s_full))  # each model has its own record
        ev.record_derived(folder, pn, s_full)
        self.assertTrue(ev.derived_fresh(folder, pn, s_full))
        self.assertTrue(ev.derived_fresh(folder, pn, s_light, light=True))
        # the same rebuild rules: a board edit makes both stale; neither is in the vault item
        self.work.checkout(pn)
        with open(os.path.join(folder, "Tiny.kicad_pcb"), "a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertNotEqual(ev.derived_stamp(folder, extra, light=True), s_light)
        self.assertNotEqual(ev.derived_stamp(folder, extra), s_full)
        self.assertNotIn(".keystone/" + pn + "-light.FCStd", ev.project_files(folder))


class TestImport(Base):
    def test_import_makes_a_library_item(self):
        res = self.imported(manufacturer="Bruce", value="DevKit")
        pn = res.part_number
        self.assertEqual(pn, "STD-01-01001-001")
        item = self.vault.items[pn]
        self.assertEqual((item.item_type, item.description, item.state), ("cots", 'Tiny "test" board', "WIP"))
        version = self.vault.latest_version(pn)
        self.assertEqual(version.filename, pn + ".kicad.zip")
        self.assertEqual(version.comment, "Imported the tiny board")
        self.assertTrue(ev.is_ecad(self.vault, pn))
        self.assertTrue(self.work.is_folder(pn))
        self.assertEqual(res.work_copy, os.path.join(self.work.root, pn))
        self.assertEqual(res.missing_models, ["${KIPRJMOD}/3dmodels/missing.step"])
        props = ev.read_properties(res.work_copy)
        self.assertEqual(props, {"Description": 'Tiny "test" board', "Manufacturer": "Bruce", "Value": "DevKit",
                                 "KiCadProject": "Tiny.kicad_pro"})
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)
        self.assertFalse(os.access(os.path.join(res.work_copy, "Tiny.kicad_pcb"), os.W_OK))
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, vs.STAGING_DIR)))
        # the source is only read
        self.assertTrue(os.path.exists(os.path.join(self.src, "Tiny.kicad_prl")))
        # a second import of the same board is refused; another description is a new item
        with self.assertRaisesRegex(ValueError, "already a KiCad item"):
            self.imported()
        self.assertEqual(self.imported(description="Tiny, second").part_number, "STD-01-01002-001")

    def test_import_needs_a_comment_and_a_board(self):
        with self.assertRaisesRegex(ValueError, "comment"):
            ev.import_project(self.vault, self.src, comment=" ")
        os.remove(os.path.join(self.src, "Tiny.kicad_pcb"))
        with self.assertRaisesRegex(ValueError, "no board"):
            self.imported()
        self.assertEqual(self.vault.items, {})
        from bruceworx.sources import JsonNumberSource  # the number was not taken

        self.assertEqual(JsonNumberSource(vs.numbering_path(self.vault.root)).preview("STD", "01"), "STD-01-01001-001")

    def test_the_thumbnail_goes_in_the_archive(self):
        png = os.path.join(self.tmp, "render.png")
        with open(png, "wb") as handle:
            handle.write(b"\x89PNG fake")
        with mock.patch.object(kb, "find_cli", return_value="/bin/true"), \
                mock.patch.object(kb, "cached_render", return_value=png) as render:
            res = self.imported()
        render.assert_called_once()
        self.assertTrue(res.thumbnail)
        obj = os.path.join(self.vault.root, self.vault.latest_version(res.part_number).object_name)
        with zipfile.ZipFile(obj) as archive:
            self.assertEqual(archive.read("thumbnails/Thumbnail.png"), b"\x89PNG fake")


class TestWorkspace(Base):
    def test_check_out_edit_check_in(self):
        pn = self.imported().part_number
        folder = self.work.path(pn)
        self.assertEqual(self.work.checkout(pn), folder)
        self.assertEqual(self.work.status(pn).code, ws.CHECKED_OUT)
        board = os.path.join(folder, "Tiny.kicad_pcb")
        self.assertTrue(os.access(board, os.W_OK))
        with open(board, "a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self.work.status(pn).code, ws.MODIFIED)
        with self.assertRaises(ValueError):
            self.work.checkin(pn, "")
        version = self.work.checkin(pn, "Board outline fixed")
        self.assertEqual(version.revision, "01")
        self.assertEqual(len(self.vault.versions[pn]), 2)
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)
        self.assertFalse(os.access(board, os.W_OK))
        # an unchanged check-in only releases the lock
        self.work.checkout(pn)
        self.assertEqual(self.work.status(pn).code, ws.CHECKED_OUT)
        self.work.checkin(pn, "nothing")
        self.assertEqual(len(self.vault.versions[pn]), 2)
        self.assertNotIn(pn, self.vault.locks)
        # KiCad's own files do not make the project modified
        with open(os.path.join(folder, "fp-info-cache"), "w") as handle:
            handle.write("x")
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)

    def test_undo_get_latest_and_revision(self):
        pn = self.imported().part_number
        first = self.vault.latest_version(pn).sha256
        folder = self.work.checkout(pn)
        board = os.path.join(folder, "Tiny.kicad_pcb")
        with open(board, "a", encoding="utf-8") as handle:
            handle.write("; edit\n")
        self.work.undo_checkout(pn)
        with open(board, encoding="utf-8") as handle:
            self.assertNotIn("; edit", handle.read())
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)
        # a newer version (from "another Mac": the vault directly) shows as NEWER, Get Latest unpacks it
        other = os.path.join(self.tmp, "other")
        ev.unpack(os.path.join(self.vault.root, self.vault.latest_version(pn).object_name), other, writable=True)
        with open(os.path.join(other, "notes.txt"), "w") as handle:
            handle.write("new file\n")
        archive = os.path.join(self.tmp, pn + ".kicad.zip")
        ev.pack(other, archive)
        self.vault.checkout(pn)
        self.vault.checkin(pn, archive, comment="notes")
        self.assertEqual(self.work.status(pn).code, ws.NEWER)
        self.work.get_latest(pn)
        self.assertTrue(os.path.exists(os.path.join(folder, "notes.txt")))
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)
        rev = self.work.get_revision(pn, first)
        self.assertTrue(os.path.isdir(rev))
        self.assertFalse(os.path.exists(os.path.join(rev, "notes.txt")))

    def test_state_survives_a_new_workspace(self):
        pn = self.imported().part_number
        again = ws.Workspace(FolderVault(self.vault.root, actor=ME))
        self.assertEqual(again.status(pn).code, ws.UP_TO_DATE)
        again.checkout(pn)
        self.assertEqual(ws.Workspace(FolderVault(self.vault.root, actor=ME)).status(pn).code, ws.CHECKED_OUT)

    def test_update_project(self):
        pn = self.imported().part_number
        with open(os.path.join(self.src, "Tiny.kicad_sch"), "a", encoding="utf-8") as handle:
            handle.write("; routed\n")
        os.remove(os.path.join(self.src, "scripts", "gen.py"))
        ev.update_project(self.work, pn, self.src, "Routing done", value="DevKit v2")
        folder = self.work.path(pn)
        self.assertFalse(os.path.exists(os.path.join(folder, "scripts", "gen.py")))
        self.assertEqual(ev.read_properties(folder)["Value"], "DevKit v2")
        self.assertEqual(ev.read_properties(folder)["Description"], 'Tiny "test" board')
        self.assertEqual(self.vault.latest_version(pn).comment, "Routing done")
        self.assertEqual(self.work.status(pn).code, ws.UP_TO_DATE)
        with self.assertRaisesRegex(ValueError, "not a KiCad item"):
            self.vault.create_item("STD-01-09999-001", "part", "x")
            ev.update_project(self.work, "STD-01-09999-001", self.src, "x")

    def test_other_items_still_fcstd(self):
        self.vault.create_item("STD-01-05000-001", "cots", "Motor")
        self.assertFalse(self.work.is_folder("STD-01-05000-001"))
        self.assertTrue(self.work.path("STD-01-05000-001").endswith(".FCStd"))


class TestLaunch(unittest.TestCase):
    def test_commands(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        project = ev.find_project(make_project(os.path.join(tmp, "p")))
        ran = []
        with mock.patch.dict(os.environ, {ev.LAUNCH_ENV: ""}):
            cmd = ev.launch(ev.editor_file(project, "schematic"), runner=ran.append)
        self.assertEqual(ran, [cmd])
        if sys.platform == "darwin":
            self.assertEqual(cmd[:2], ["open", "-a"])
            self.assertTrue(cmd[2].startswith(kb.KICAD_APP))
        self.assertEqual(cmd[-1], project.sch)
        with mock.patch.dict(os.environ, {ev.LAUNCH_ENV: "dry"}):
            self.assertEqual(ev.launch(project.pro, runner=ran.append)[-1], project.pro)
        self.assertEqual(len(ran), 1)
        with self.assertRaises(ValueError):
            ev.launch_command(os.path.join(tmp, "x.step"))


class TestCli(Base):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        text = out.getvalue()
        return code, json.loads(text[text.index("{"):]) if "{" in text else {}

    def test_kicad_import_update_open(self):
        code, out = self.run_cli("vault", "kicad-import", "--vault", self.vault.root, "--from", self.src,
                                 "--comment", "Imported", "--mpn", "TINY-1", "--actor", ME, "--no-render")
        self.assertEqual(code, 0, out)
        pn = out["part_number"]
        self.assertEqual((pn, out["item_type"]), ("STD-01-01001-001", "cots"))
        vault = FolderVault(self.vault.root, actor=ME)
        folder = ws.Workspace(vault).path(pn)
        self.assertEqual(out["work_copy"], folder)
        self.assertEqual(ev.read_properties(folder)["ManufacturerPartNumber"], "TINY-1")
        code, out = self.run_cli("vault", "kicad-update", "--vault", self.vault.root, "--pn", pn, "--from",
                                 self.src, "--comment", "Same again", "--actor", ME)
        self.assertEqual(code, 0, out)
        with mock.patch.object(ev, "launch", side_effect=AssertionError("KiCad started")):
            code, out = self.run_cli("vault", "kicad-open", "--vault", self.vault.root, "--pn", pn, "--file",
                                     "board", "--actor", ME, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["opened"], os.path.join(folder, "Tiny.kicad_pcb"))
        self.assertEqual(out["status"], ws.UP_TO_DATE)
        self.assertFalse(out["writable"])

    def test_kicad_import_dry_run(self):
        code, out = self.run_cli("vault", "kicad-import", "--vault", self.vault.root, "--from", self.src, "--dry-run",
                                 "--actor", ME, "--exclude", "scripts")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["part_number"], out["description"]), ("STD-01-01001-001", 'Tiny "test" board'))
        self.assertEqual(out["files"], ev.project_files(self.src, ["scripts"]))
        self.assertEqual(out["missing_models"], ["${KIPRJMOD}/3dmodels/missing.step"])
        self.assertEqual(FolderVault(self.vault.root).items, {})
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, "work")))

    def test_kicad_import_needs_a_comment(self):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = main(["vault", "kicad-import", "--vault", self.vault.root, "--from", self.src, "--actor", ME])
        self.assertNotEqual(code, 0)
        self.assertEqual(FolderVault(self.vault.root).items, {})


@unittest.skipUnless(kb.find_cli() and os.path.isdir(os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega")),
                     "KiCad not installed")
class TestRealKiCad(unittest.TestCase):
    def test_import_a_template_with_its_render(self):
        tmp = tempfile.mkdtemp(prefix="bw-ecad-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "Arduino_Mega")
        shutil.copytree(os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega"), src)
        vault = make_library(tmp)
        work = ws.Workspace(vault)
        with mock.patch.dict(os.environ, {kb.CACHE_ENV: os.path.join(tmp, "cache")}):
            res = ev.import_project(vault, src, comment="Template", workspace=work)
        self.assertTrue(res.thumbnail)
        thumb = os.path.join(res.work_copy, "thumbnails", "Thumbnail.png")
        with open(thumb, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertEqual(work.status(res.part_number).code, ws.UP_TO_DATE)
        self.assertTrue(os.path.isfile(os.path.join(res.work_copy, "Arduino_Mega.kicad_pcb")))


if __name__ == "__main__":
    unittest.main()
