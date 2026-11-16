# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault's working folder and file status (BW-PDM-04, plan section 7.3)."""

import os
import plistlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import pdm  # noqa: E402
from heuretes_freecad import pdm_workspace as ws  # noqa: E402
from heuretes_freecad.pdm_folder import FolderVault, LifecycleError  # noqa: E402

PART = "BW-01-01001-001"
BOLT = "BW-01-01002-001"
ASSY = "BW-02-01001-001"
ME = "bruce@mac"


def make_fcstd(path, text="v1", links=()):
    """A minimal .FCStd: Document.xml with optional XLinks, plus a payload that sets the hash."""
    xlinks = "".join(f'<XLink file="{name}" name="Body"/>' for name in links)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", f"<Document><Properties>{xlinks}</Properties></Document>")
        archive.writestr("payload.txt", text)
    return path


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-work-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Test", actor=ME)
        self.work = ws.Workspace(self.vault)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def add_item(self, pn, kind="part", text="v1", links=()):
        """Create an item and check in a first file the way an import does."""
        self.vault.create_item(pn, kind, pn.lower())
        self.vault.checkout(pn)
        self.vault.checkin(pn, make_fcstd(os.path.join(self.src, pn + ".FCStd"), text, links), comment="first")

    def edit(self, pn, text):
        make_fcstd(self.work.path(pn), text)

    # --- the status table -----------------------------------------------------
    def test_status_table_rows(self):
        f = ws.status_code
        self.assertEqual(f(False, "", ME, None, None, None, pdm.WIP), ws.NOT_IN_VAULT)
        self.assertEqual(f(True, "sam@mac", ME, "a", "a", "a", pdm.WIP), ws.CHECKED_OUT_OTHER)
        self.assertEqual(f(True, ME, ME, "a", "b", "a", pdm.WIP), ws.MODIFIED)
        self.assertEqual(f(True, ME, ME, "a", "a", "a", pdm.WIP), ws.CHECKED_OUT)
        self.assertEqual(f(True, "", ME, "a", "a", "b", pdm.WIP), ws.NEWER)
        self.assertEqual(f(True, "", ME, "a", "a", "a", pdm.RELEASED), ws.RELEASED)
        self.assertEqual(f(True, "", ME, "a", "a", "a", pdm.WIP), ws.UP_TO_DATE)
        self.assertEqual(f(True, "", ME, None, None, "a", pdm.WIP), ws.UP_TO_DATE)

    def test_pin_actor(self):
        self.assertEqual(ws.pin_actor("bruce@mac", "bruce@mac-2"), "bruce@mac")
        self.assertEqual(ws.pin_actor("", "bruce@mac"), "bruce@mac")

    # --- operations ---------------------------------------------------------------
    def test_checkout_puts_a_writable_copy_named_by_part_number(self):
        self.add_item(PART)
        path = self.work.checkout(PART)
        self.assertEqual(path, os.path.join(self.vault.root, "work", ME, PART + ".FCStd"))
        self.assertTrue(os.access(path, os.W_OK))
        self.assertEqual(self.work.status(PART).code, ws.CHECKED_OUT)

    def test_edit_shows_modified_and_checkin_needs_a_comment(self):
        self.add_item(PART)
        self.work.checkout(PART)
        self.edit(PART, "v2")
        self.assertEqual(self.work.status(PART).code, ws.MODIFIED)
        with self.assertRaises(ValueError):
            self.work.checkin(PART, "  ")
        self.work.checkin(PART, "longer slot")
        st = self.work.status(PART)
        self.assertEqual(st.code, ws.UP_TO_DATE)
        self.assertFalse(os.access(self.work.path(PART), os.W_OK))
        self.assertEqual(len(self.vault.versions[PART]), 2)
        self.assertEqual(self.vault.versions[PART][-1].comment, "longer slot")

    def test_unchanged_checkin_adds_no_version(self):
        self.add_item(PART)
        self.work.checkout(PART)
        self.work.checkin(PART, "nothing changed")
        self.assertEqual(len(self.vault.versions[PART]), 1)
        self.assertNotIn(PART, self.vault.locks)

    def test_keep_checked_out(self):
        self.add_item(PART)
        self.work.checkout(PART)
        self.edit(PART, "v2")
        self.work.checkin(PART, "midway", keep_checked_out=True)
        self.assertEqual(self.vault.locks.get(PART), ME)
        self.assertEqual(self.work.status(PART).code, ws.CHECKED_OUT)

    def test_undo_checkout_restores_the_base(self):
        self.add_item(PART)
        self.work.checkout(PART)
        base = self.work.base(PART)
        self.edit(PART, "throw away")
        self.work.undo_checkout(PART)
        self.assertEqual(self.work.local_hash(self.work.path(PART)), base)
        self.assertNotIn(PART, self.vault.locks)
        self.assertFalse(os.access(self.work.path(PART), os.W_OK))

    def test_newer_version_and_get_latest(self):
        self.add_item(PART)
        self.work.get_latest(PART)
        # A new version arrives (here: checked in from another folder).
        self.vault.checkout(PART)
        self.vault.checkin(PART, make_fcstd(os.path.join(self.src, "other.FCStd"), "v9"), comment="elsewhere")
        self.assertEqual(self.work.status(PART).code, ws.NEWER)
        self.assertEqual([s.part_number for s in self.work.my_work().newer], [PART])
        self.work.get_latest(PART)
        self.assertEqual(self.work.status(PART).code, ws.UP_TO_DATE)

    def test_get_latest_never_overwrites_a_checked_out_file(self):
        self.add_item(PART)
        self.work.checkout(PART)
        with self.assertRaises(LifecycleError):
            self.work.get_latest(PART)

    def test_checkout_brings_linked_parts_read_only(self):
        self.add_item(PART)
        self.add_item(BOLT)
        self.add_item(ASSY, "assembly", links=(PART + ".FCStd", "sub/" + BOLT + ".FCStd", "Missing.FCStd"))
        self.work.checkout(ASSY)
        for pn in (PART, BOLT):
            self.assertTrue(os.path.exists(self.work.path(pn)), pn)
            self.assertFalse(os.access(self.work.path(pn), os.W_OK), pn)
        self.assertEqual(self.vault.locks, {ASSY: ME})

    def test_released_and_in_review(self):
        self.add_item(PART)
        self.work.get_latest(PART)
        self.vault.submit(PART)
        self.assertEqual([s.part_number for s in self.work.my_work().in_review], [PART])
        self.vault.approve(PART, note="released")
        st = self.work.status(PART)
        self.assertEqual((st.code, st.revision, st.display), (ws.RELEASED, "A", PART + "-A"))

    def test_checked_out_by_someone_else_and_break_lock(self):
        self.add_item(PART)
        other = FolderVault(self.vault.ref_path, actor="sam@mac")
        other.checkout(PART)
        self.vault.refresh()
        self.assertEqual(self.work.status(PART).code, ws.CHECKED_OUT_OTHER)
        self.work.break_lock(PART)
        self.assertNotIn(PART, self.vault.locks)

    def test_get_revision(self):
        self.add_item(PART)
        first = self.vault.versions[PART][0].sha256
        self.work.checkout(PART)
        self.edit(PART, "v2")
        self.work.checkin(PART, "second")
        path = self.work.get_revision(PART, first)
        self.assertIn(os.path.join("work", ME, ".rev"), path)
        self.assertEqual(self.work.local_hash(path), first)
        self.assertFalse(os.access(path, os.W_OK))

    def test_state_survives_reopening(self):
        self.add_item(PART)
        self.work.checkout(PART)
        self.edit(PART, "v2")
        again = ws.Workspace(FolderVault(self.vault.ref_path, actor=ME))
        self.assertEqual(again.status(PART).code, ws.MODIFIED)

    def test_linked_files_reads_xlinks(self):
        path = make_fcstd(os.path.join(self.src, "a.FCStd"), links=("B.FCStd", "B.FCStd", "../c/C.FCStd"))
        self.assertEqual(ws.linked_files(path), ["B.FCStd", "../c/C.FCStd"])
        self.assertEqual(ws.linked_files(os.path.join(self.src, "nothing.FCStd")), [])


def evict(path, size=None):
    """What iCloud did before macOS 14: the file goes, a .<name>.icloud stand-in stays."""
    size = os.path.getsize(path) if size is None else size
    folder, name = os.path.split(path)
    keep = path + ".kept"
    os.chmod(path, 0o644)
    os.replace(path, keep)
    with open(os.path.join(folder, "." + name + ".icloud"), "wb") as handle:
        plistlib.dump({"NSURLNameKey": name, "NSURLFileSizeKey": size}, handle)
    return keep


def download(path):
    """The download iCloud would do: the file is back and the stand-in gone."""
    folder, name = os.path.split(path)
    os.replace(path + ".kept", path)
    os.remove(os.path.join(folder, "." + name + ".icloud"))


def dataless_stat(*dataless):
    """os.stat that reports SF_DATALESS for the given paths (macOS 14 and later evictions)."""
    real = os.stat

    def fake(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if os.fspath(path) in dataless:
            fields = {k: getattr(info, k) for k in dir(info) if k.startswith("st_")}
            fields["st_flags"] = ws.SF_DATALESS
            return SimpleNamespace(**fields)
        return info

    return fake


class ICloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-icloud-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Test", actor=ME)
        self.work = ws.Workspace(self.vault)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def add_item(self, pn, kind="part", text="v1", links=()):
        self.vault.create_item(pn, kind, pn.lower())
        self.vault.checkout(pn)
        self.vault.checkin(pn, make_fcstd(os.path.join(self.src, pn + ".FCStd"), text, links), comment="first")

    def object_path(self, pn):
        return os.path.join(self.vault.root, self.vault.latest_version(pn).object_name)

    def test_stand_in_detection(self):
        path = make_fcstd(os.path.join(self.src, "a.FCStd"))
        self.assertFalse(ws.needs_download(path))
        self.assertEqual(ws.icloud_placeholder(path), "")
        evict(path, size=123456)
        self.assertTrue(ws.needs_download(path))
        self.assertEqual(ws.icloud_placeholder(path), os.path.join(self.src, ".a.FCStd.icloud"))
        self.assertEqual(ws.cloud_size(path), 123456)
        self.assertEqual(ws.pending_downloads([path, path, os.path.join(self.src, "none.FCStd")]), [path])
        download(path)
        self.assertFalse(ws.needs_download(path))

    def test_unreadable_stand_in_has_no_size(self):
        path = os.path.join(self.src, "b.FCStd")
        with open(os.path.join(self.src, ".b.FCStd.icloud"), "wb") as handle:
            handle.write(b"not a plist")
        self.assertTrue(ws.needs_download(path))
        self.assertEqual(ws.cloud_size(path), 0)

    def test_dataless_detection(self):
        path = make_fcstd(os.path.join(self.src, "c.FCStd"))
        self.assertFalse(ws.is_dataless(path))
        fake = dataless_stat(path)
        self.assertTrue(ws.is_dataless(path, fake))
        self.assertTrue(ws.needs_download(path, fake))
        self.assertEqual(ws.cloud_size(path, fake), os.path.getsize(path))
        self.assertFalse(ws.is_dataless(os.path.join(self.src, "none"), fake))

    def test_get_latest_waits_for_an_evicted_object(self):
        self.add_item(PART)
        evict(self.object_path(PART))
        with self.assertRaises(ws.NotDownloadedError) as caught:
            self.work.get_latest(PART)
        self.assertIn("iCloud", str(caught.exception))
        asked = []
        self.work.ensure_local = lambda paths: (asked.extend(paths), [download(p) for p in paths])
        path = self.work.get_latest(PART)
        self.assertEqual(asked, [self.object_path(PART)])
        self.assertEqual(self.work.status(PART).code, ws.UP_TO_DATE)
        self.assertTrue(os.path.exists(path))

    def test_a_download_that_does_not_finish_stops_the_check_out(self):
        self.add_item(PART)
        evict(self.object_path(PART))
        self.work.ensure_local = lambda paths: None  # timed out
        with self.assertRaises(ws.NotDownloadedError):
            self.work.checkout(PART)
        self.assertFalse(os.path.exists(self.work.path(PART)))

    def test_make_local_fetches_the_assembly_and_its_parts(self):
        self.add_item(PART)
        self.add_item(ASSY, "assembly", links=(PART + ".FCStd", "Missing.FCStd"))
        self.work.get_latest(ASSY)
        self.work.get_latest(PART)
        evict(self.work.path(ASSY))
        evict(self.work.path(PART))
        asked = []
        self.work.ensure_local = lambda paths: (asked.append(list(paths)), [download(p) for p in paths])
        self.assertEqual(self.work.make_local(ASSY), sorted([self.work.path(ASSY), self.work.path(PART)]))
        self.assertEqual(asked, [[self.work.path(ASSY)], [self.work.path(PART)]])
        self.assertEqual(self.work.make_local(ASSY), sorted([self.work.path(ASSY), self.work.path(PART)]))
        self.assertEqual(len(asked), 2)  # nothing left to download

    def test_make_local_fetches_parts_never_fetched(self):
        """Opening an assembly whose parts have no work copy yet gets them read-only (30 Oct)."""
        self.add_item(PART)
        self.add_item(ASSY, "assembly", links=(PART + ".FCStd", "Missing.FCStd"))
        self.work.get_latest(ASSY)
        self.assertFalse(os.path.exists(self.work.path(PART)))
        self.assertEqual(self.work.make_local(ASSY), sorted([self.work.path(ASSY), self.work.path(PART)]))
        self.assertEqual(self.work.status(PART).code, ws.UP_TO_DATE)

    def test_evicted_work_copy_keeps_its_status_and_is_downloaded_to_check_out(self):
        self.add_item(PART)
        self.work.checkout(PART)
        make_fcstd(self.work.path(PART), "v2")
        self.work.statuses()  # keeps the hash in state.json
        evict(self.work.path(PART))
        again = ws.Workspace(FolderVault(self.vault.ref_path, actor=ME))
        self.assertEqual(again.status(PART).code, ws.MODIFIED)
        self.work.undo_checkout(PART)  # puts the base back over the stand-in
        self.assertFalse(ws.needs_download(self.work.path(PART)))
        self.assertEqual(os.listdir(self.work.root).count(".%s.FCStd.icloud" % PART), 0)
        evict(self.work.path(PART))
        asked = []
        self.work.ensure_local = lambda paths: (asked.extend(paths), [download(p) for p in paths])
        self.work.checkout(PART)
        self.assertEqual(asked, [self.work.path(PART)])
        self.assertTrue(os.access(self.work.path(PART), os.W_OK))

    def test_get_latest_replaces_an_evicted_copy(self):
        self.add_item(PART)
        self.work.get_latest(PART)
        evict(self.work.path(PART))
        self.work.get_latest(PART)
        self.assertFalse(ws.needs_download(self.work.path(PART)))
        self.assertEqual(self.work.status(PART).code, ws.UP_TO_DATE)

    def test_status_pass_never_reads_a_dataless_file(self):
        self.add_item(PART)
        self.work.checkout(PART)
        make_fcstd(self.work.path(PART), "v2")
        self.assertEqual(self.work.statuses()[PART].code, ws.MODIFIED)  # hashes, and keeps the hash
        path = self.work.path(PART)
        again = ws.Workspace(FolderVault(self.vault.ref_path, actor=ME))
        with mock.patch("os.stat", dataless_stat(path)), \
                mock.patch.object(ws, "sha256_of", side_effect=AssertionError("read a dataless file")):
            self.assertEqual(again.status(PART).code, ws.MODIFIED)
        # A dataless file with no cached hash is taken to be its base.
        os.utime(path, ns=(1, 1))
        third = ws.Workspace(FolderVault(self.vault.ref_path, actor=ME))
        with mock.patch("os.stat", dataless_stat(path)), \
                mock.patch.object(ws, "sha256_of", side_effect=AssertionError("read a dataless file")):
            self.assertEqual(third.status(PART).code, ws.CHECKED_OUT)


if __name__ == "__main__":
    unittest.main()
