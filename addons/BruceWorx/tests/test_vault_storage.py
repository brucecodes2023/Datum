# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault storage migration (PRF-04): binary shapes without check-outs or new versions.

A fake converter stands in for OpenCascade (it marks the bytes and reports equal shape facts), so the zip
rewrite, the journal event, replay, the workspace status and the drawing links are tested without FreeCAD.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm_workspace as ws  # noqa: E402
from bruceworx import vault_storage as vs  # noqa: E402
from bruceworx.pdm_folder import FolderVault, object_name, sha256_of  # noqa: E402

ME = "bruce@mac"


def fake_convert(text):
    return b"BIN:" + text[::-1], ("solid", len(text)), ("solid", len(text))


def lying_convert(text):
    return b"BIN:" + text, ("solid", 1), ("solid", 2)


def fcstd(path, shapes=("Shape.brp", "Shape1.brp"), extra=b"view data"):
    refs = "".join(f'<Part ElementMap="4" file="{n}"/>' for n in shapes)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", f"<Document><Properties>{refs}</Properties></Document>")
        for n in shapes:
            z.writestr(n, f"CASCADE Topology V3 {n}\n" + "x" * 200)
        z.writestr("Unreferenced.brp", "left alone")
        z.writestr("GuiDocument.xml", extra)
        z.writestr("thumbnails/Thumbnail.png", b"\x89PNG fake")
        z.writestr("bruceworx/preview.bin", b"BWPM1 mesh")
    return path


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-store-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_shapes_become_bin_and_the_rest_is_copied(self):
        src = fcstd(os.path.join(self.tmp, "a.FCStd"))
        dst = os.path.join(self.tmp, "b.FCStd")
        self.assertEqual(vs.convert_fcstd(src, dst, fake_convert), 2)
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
            names = b.namelist()
            self.assertIn("Shape.bin", names)
            self.assertNotIn("Shape.brp", names)
            self.assertIn("Unreferenced.brp", names)  # not a shape Document.xml names
            doc = b.read("Document.xml").decode()
            self.assertIn('file="Shape.bin"', doc)
            self.assertIn('file="Shape1.bin"', doc)
            self.assertTrue(b.read("Shape.bin").startswith(b"BIN:"))
            for same in ("GuiDocument.xml", "thumbnails/Thumbnail.png", "bruceworx/preview.bin", "Unreferenced.brp"):
                self.assertEqual(a.read(same), b.read(same), same)

    def test_a_shape_that_changes_stops_everything(self):
        src = fcstd(os.path.join(self.tmp, "a.FCStd"))
        with self.assertRaises(vs.ShapeMismatch):
            vs.convert_fcstd(src, os.path.join(self.tmp, "b.FCStd"), lying_convert)

    def test_a_binary_file_is_left_alone(self):
        src = fcstd(os.path.join(self.tmp, "a.FCStd"), shapes=())
        self.assertEqual(vs.convert_fcstd(src, os.path.join(self.tmp, "b.FCStd"), fake_convert), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b.FCStd")))


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-store-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Test", actor=ME)
        self.work = ws.Workspace(self.vault)
        self.part = self._item("TL-01-01001-001", "part")
        self.drawing = self._item("TL-03-01001-001", "drawing", drawing_of="TL-01-01001-001",
                                  made_from={"TL-01-01001-001": self.vault.latest_version("TL-01-01001-001").sha256})

    def _item(self, pn, kind, drawing_of=None, made_from=None):
        self.vault.create_item(pn, kind, pn, drawing_of)
        self.vault.checkout(pn)
        path = fcstd(os.path.join(self.tmp, f"{pn}.FCStd"), extra=pn.encode())
        self.vault.checkin(pn, path, "first", made_from=made_from)
        self.work.get_latest(pn)
        return pn

    def test_one_event_new_objects_same_versions(self):
        old = self.vault.latest_version(self.part).sha256
        events = len(self.vault.events)
        report = vs.migrate(self.vault, fake_convert)
        self.assertEqual((report.converted, report.shapes, report.recorded), (2, 4, True))
        self.assertEqual(len(self.vault.events), events + 1)
        self.assertEqual(self.vault.events[-1].op, "migrate_storage")
        version = self.vault.latest_version(self.part)
        self.assertNotEqual(version.sha256, old)
        self.assertEqual(version.migrated_from, old)
        self.assertEqual(len(self.vault.versions[self.part]), 1)  # no new version
        self.assertEqual(self.vault.model.items[self.part].latest.code, "01")
        self.assertIs(self.vault.version_with(self.part, old), version)  # an old hash still finds it
        self.assertTrue(os.path.exists(os.path.join(self.vault.root, object_name(old, version.filename))))  # kept
        fetched = self.vault.fetch(self.part, os.path.join(self.tmp, "out"))  # verifies the new SHA-256
        with zipfile.ZipFile(fetched) as z:
            self.assertIn("Shape.bin", z.namelist())

    def test_replay_after_reopening_gives_the_same_state(self):
        vs.migrate(self.vault, fake_convert)
        again = FolderVault(self.vault.ref_path, actor=ME)
        self.assertEqual(again.latest_version(self.part).sha256, self.vault.latest_version(self.part).sha256)
        self.assertEqual(again.problems, [])

    def test_drawing_stays_made_from_the_current_part(self):
        vs.migrate(self.vault, fake_convert)
        made = self.vault.latest_version(self.drawing).made_from
        self.assertEqual(made[self.part], self.vault.latest_version(self.part).sha256)

    def test_work_copies_stay_up_to_date_and_refresh_to_binary(self):
        vs.migrate(self.vault, fake_convert)
        self.assertEqual(self.work.status(self.part).code, ws.UP_TO_DATE)  # not "newer in the vault"
        refreshed = vs.refresh_work_copies(self.work)
        self.assertEqual(refreshed, [self.part, self.drawing] if self.part < self.drawing else sorted(refreshed))
        self.assertEqual(self.work.base(self.part), self.vault.latest_version(self.part).sha256)
        self.assertEqual(self.work.status(self.part).code, ws.UP_TO_DATE)
        with zipfile.ZipFile(self.work.path(self.part)) as z:
            self.assertIn("Shape.bin", z.namelist())

    def test_a_checked_out_work_copy_is_not_touched(self):
        self.vault.checkout(self.part)
        before = sha256_of(self.work.path(self.part))
        vs.migrate(self.vault, fake_convert)
        self.assertNotIn(self.part, vs.refresh_work_copies(self.work))
        self.assertEqual(sha256_of(self.work.path(self.part)), before)

    def test_dry_run_records_nothing(self):
        events = len(self.vault.events)
        report = vs.migrate(self.vault, fake_convert, dry_run=True)
        self.assertEqual(report.converted, 2)
        self.assertFalse(report.recorded)
        self.assertEqual(len(self.vault.events), events)
        self.assertEqual(self.vault.migrated, {})

    def test_a_second_run_finds_nothing_to_do(self):
        vs.migrate(self.vault, fake_convert)
        events = len(self.vault.events)
        report = vs.migrate(self.vault, fake_convert)
        self.assertEqual((report.converted, report.already, report.recorded), (0, 2, False))
        self.assertEqual(len(self.vault.events), events)

    def test_a_mismatch_records_nothing(self):
        events = len(self.vault.events)
        with self.assertRaises(vs.ShapeMismatch):
            vs.migrate(self.vault, lying_convert)
        self.assertEqual(len(self.vault.events), events)

    def test_preview_cache_follows(self):
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(cache)
        old = self.vault.latest_version(self.part).sha256
        with open(os.path.join(cache, old + ".png"), "wb") as fh:
            fh.write(b"png")
        report = vs.migrate(self.vault, fake_convert)
        self.assertEqual(vs.copy_preview_cache(report.objects, [cache]), 1)
        self.assertTrue(os.path.exists(os.path.join(cache, report.objects[old]["sha256"] + ".png")))


if __name__ == "__main__":
    unittest.main()
