# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the vault client (bruceworx_vaultclient)."""

import datetime as dt
import os
import tempfile
import unittest
from types import SimpleNamespace

import bruceworx_vaultclient as vc

UTC = dt.timezone.utc


class FakeGroup:
    """The parameter-group calls BackupSwitch uses."""

    def __init__(self, **bools):
        self.bools = dict(bools)

    def GetContents(self):
        return [("Boolean", k, v) for k, v in self.bools.items()]

    def GetBool(self, name, default):
        return self.bools.get(name, default)

    def SetBool(self, name, value):
        self.bools[name] = bool(value)

    def RemBool(self, name):
        self.bools.pop(name, None)


class TestFileMapping(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-vc-"))
        self.work = os.path.join(self.tmp, "work", "bruce@mac")
        os.makedirs(os.path.join(self.work, ".rev"))

    def test_work_file_maps_to_its_part_number(self):
        path = os.path.join(self.work, "BW-01-01001-001.FCStd")
        self.assertEqual(vc.part_number_for_file(path, self.work), "BW-01-01001-001")
        self.assertEqual(vc.part_number_for_file(path.replace(".FCStd", ".fcstd"), self.work), "BW-01-01001-001")

    def test_other_files_are_not_work_files(self):
        self.assertEqual(vc.part_number_for_file(os.path.join(self.work, ".rev", "BW-1-A.FCStd"), self.work), "")
        self.assertEqual(vc.part_number_for_file(os.path.join(self.tmp, "BW-01-01001-001.FCStd"), self.work), "")
        self.assertEqual(vc.part_number_for_file(os.path.join(self.work, "notes.txt"), self.work), "")
        self.assertEqual(vc.part_number_for_file("", self.work), "")
        self.assertEqual(vc.part_number_for_file(os.path.join(self.work, "A.FCStd"), ""), "")

    def test_symlinked_folder_still_maps(self):
        link = os.path.join(self.tmp, "link")
        os.symlink(self.work, link)
        self.assertEqual(vc.part_number_for_file(os.path.join(link, "P.FCStd"), self.work), "P")

    def test_inside_and_moved_path(self):
        self.assertTrue(vc.inside(os.path.join(self.work, "a.FCStd"), self.tmp))
        self.assertTrue(vc.inside(self.tmp, self.tmp))
        self.assertFalse(vc.inside(self.tmp + "-other/x", self.tmp))
        moved = vc.moved_path(os.path.join(self.work, "a.FCStd"), self.tmp, "/Volumes/SSD/Acme")
        self.assertEqual(moved, os.path.join("/Volumes/SSD/Acme", "work", "bruce@mac", "a.FCStd"))
        self.assertEqual(vc.moved_path("/elsewhere/a.FCStd", self.tmp, "/x"), "")


class TestStatusPresentation(unittest.TestCase):
    def test_codes_agree_with_the_engine(self):
        try:
            from bruceworx import pdm_workspace as ws
        except ImportError:
            self.skipTest("hooks add-on not installed")
        for name in ("NOT_IN_VAULT", "CHECKED_OUT_OTHER", "MODIFIED", "CHECKED_OUT", "NEWER", "RELEASED",
                     "UP_TO_DATE"):
            self.assertEqual(getattr(vc, name), getattr(ws, name), name)
        self.assertEqual(set(vc.WRITABLE), set(ws.WRITABLE))

    def test_one_icon_per_status_and_two_when_modified(self):
        self.assertEqual(len(vc.STATUS_ICONS), 7)
        self.assertEqual(vc.status_icons(vc.CHECKED_OUT), [("lock_me", "accent")])
        self.assertEqual(vc.status_icons(vc.MODIFIED), [("lock_me", "accent"), ("pencil", "accent")])
        self.assertEqual(vc.status_icons(vc.CHECKED_OUT_OTHER), [("lock_other", "error")])
        self.assertEqual(vc.status_icons(vc.NEWER), [("newer", "warn")])
        self.assertEqual(vc.status_icons(vc.RELEASED), [("released", "ok")])
        self.assertEqual(vc.status_icons("unknown"), [])
        for icons in vc.STATUS_ICONS.values():
            for kind, _role in icons:
                self.assertIn(kind, vc._SVG)

    def test_browser_marks(self):
        me = "bruce@mac"
        self.assertEqual(vc.browser_marks(vc.CHECKED_OUT, me, me), [(vc.LOCK, "accent")])
        self.assertEqual(vc.browser_marks(vc.MODIFIED, me, me)[0][1], "accent")
        self.assertEqual(vc.browser_marks(vc.CHECKED_OUT_OTHER, "dana@imac", me), [(vc.LOCK + " dana", "warn")])
        self.assertEqual(vc.browser_marks(vc.NEWER), [("↓ newer", "warn")])
        self.assertEqual(vc.browser_marks(vc.UP_TO_DATE), [])
        self.assertEqual(vc.browser_marks(vc.RELEASED), [])

    def test_actor_name(self):
        self.assertEqual(vc.actor_name("bruce@mac", "bruce@mac"), "you")
        self.assertEqual(vc.actor_name("dana@imac", "bruce@mac"), "dana")
        self.assertEqual(vc.actor_name("", "bruce@mac"), "")

    def test_svg_icon_is_coloured(self):
        svg = vc.svg_icon("newer", "#C27C00")
        self.assertIn("#C27C00", svg)
        self.assertNotIn("{c}", svg)


class TestStatusBar(unittest.TestCase):
    def test_counts_text(self):
        self.assertEqual(vc.status_suffix(0, 0), "")
        self.assertEqual(vc.status_suffix(2, 1), " · 2 checked out · 1 newer")
        self.assertEqual(vc.status_suffix(0, 3), " · 3 newer")
        self.assertEqual(vc.status_suffix(1, 0), " · 1 checked out")


class TestCheckIn(unittest.TestCase):
    def test_comment_is_required(self):
        self.assertFalse(vc.comment_ok(""))
        self.assertFalse(vc.comment_ok("   \n\t"))
        self.assertFalse(vc.comment_ok(None))
        self.assertTrue(vc.comment_ok("Pin bore moved up 0.5 mm"))

    def test_button_text(self):
        self.assertEqual(vc.checkin_button_text(0), "Check In")
        self.assertEqual(vc.checkin_button_text(1), "Check In 1 File")
        self.assertEqual(vc.checkin_button_text(3), "Check In 3 Files")

    def test_version_change(self):
        self.assertEqual(vc.version_change("a41c9e0aa", "c82f5d1bb"), "a41c9e0 → c82f5d1")
        self.assertEqual(vc.version_change("", "5be1f02"), "new → 5be1f02")
        self.assertEqual(vc.version_change("3c90e7a", "3c90e7a"), "3c90e7a")


class TestBackupSwitch(unittest.TestCase):
    def test_off_for_one_save_then_back(self):
        group = FakeGroup(CreateBackupFiles=True)
        switch = vc.BackupSwitch(group)
        switch.suspend()
        self.assertFalse(group.bools["CreateBackupFiles"])
        self.assertTrue(switch.active)
        switch.suspend()  # nested start: the first value is kept
        switch.restore()
        self.assertTrue(group.bools["CreateBackupFiles"])
        self.assertFalse(switch.active)
        switch.restore()  # again: nothing to do
        self.assertTrue(group.bools["CreateBackupFiles"])

    def test_unset_key_is_removed_again(self):
        group = FakeGroup()
        switch = vc.BackupSwitch(group)
        switch.suspend()
        self.assertEqual(group.bools, {"CreateBackupFiles": False})
        switch.restore()
        self.assertEqual(group.bools, {})

    def test_user_who_turned_backups_off_keeps_them_off(self):
        group = FakeGroup(CreateBackupFiles=False)
        switch = vc.BackupSwitch(group)
        switch.suspend()
        switch.restore()
        self.assertFalse(group.bools["CreateBackupFiles"])


class TestPanelRows(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 23, 18, 0, tzinfo=UTC)

    def v(self, minutes, actor="bruce@mac"):
        at = (self.NOW - dt.timedelta(minutes=minutes)).isoformat()
        return SimpleNamespace(at=at, actor=actor, revision="02", sha256="ab" * 32, comment="")

    def test_relative_time(self):
        self.assertEqual(vc.relative_time(self.NOW.isoformat(), self.NOW), "just now")
        self.assertEqual(vc.relative_time((self.NOW - dt.timedelta(minutes=40)).isoformat(), self.NOW), "40 min ago")
        two_days = (self.NOW - dt.timedelta(days=9)).astimezone()
        self.assertEqual(vc.relative_time(two_days.isoformat(), self.NOW),
                         f"{two_days.day} {two_days.strftime('%b')}")
        self.assertEqual(vc.relative_time("not a time", self.NOW), "")

    def test_row_status(self):
        me = "bruce@mac"
        f = vc.row_status
        self.assertEqual(f(vc.MODIFIED, "WIP", me, me, [self.v(5)], now=self.NOW),
                         ("Checked out by you · modified", "accent"))
        self.assertEqual(f(vc.NEWER, "WIP", "", me, [self.v(40)], now=self.NOW),
                         ("Newer version · 40 min ago", "warn"))
        self.assertEqual(f(vc.CHECKED_OUT_OTHER, "WIP", "dana@imac", me, [self.v(5)], now=self.NOW)[0],
                         "Checked out by dana · read-only")
        self.assertEqual(f(vc.UP_TO_DATE, "In Review", "", me, [self.v(5)], now=self.NOW)[0],
                         "In review · read-only")
        self.assertEqual(f(vc.UP_TO_DATE, "WIP", "", me, [self.v(120, "dana@imac")], now=self.NOW)[0],
                         "Checked in · dana, 2 h ago")
        self.assertEqual(f(vc.UP_TO_DATE, "WIP", "", me, [], now=self.NOW)[0], "No file yet")
        self.assertTrue(f(vc.RELEASED, "Released", "", me, [self.v(5)], now=self.NOW)[0].endswith("read-only"))

    def test_filters_and_search(self):
        part = {"part_number": "BW-01-01001-001", "display": "BW-01-01001-001-03", "description": "Hinge leaf",
                "item_type": "part", "code": vc.MODIFIED}
        assy = {"part_number": "BW-02-01001-001", "display": "BW-02-01001-001-02", "description": "Door hinge",
                "item_type": "assembly", "code": vc.UP_TO_DATE}
        self.assertTrue(vc.matches(part, "parts"))
        self.assertFalse(vc.matches(assy, "parts"))
        self.assertTrue(vc.matches(assy, "assemblies"))
        self.assertTrue(vc.matches(part, "checked_out"))
        self.assertFalse(vc.matches(assy, "checked_out"))
        self.assertTrue(vc.matches(assy, "all", "door"))
        self.assertTrue(vc.matches(part, "all", "01001 leaf"))
        self.assertFalse(vc.matches(part, "all", "door"))
        self.assertTrue(vc.matches(part, "all", "-03"))

    def test_size_text(self):
        self.assertEqual(vc.size_text(512), "512 bytes")
        self.assertEqual(vc.size_text(2_500_000), "2.5 MB")


class TestBrowserHook(unittest.TestCase):
    def test_row_extras_run_after_the_badges(self):
        import bruceworx_browser as browser

        obj = SimpleNamespace(PropertiesList=["PartNumber", "Revision"], PartNumber="BW-01-01001-001",
                              Revision="02", TypeId="Part::Feature")
        seen = []

        def extra(o, meta, marks):
            seen.append((o, meta, list(marks)))
            return "meta", marks + [("x", "accent")]

        browser.ROW_EXTRAS.append(extra)
        try:
            meta, marks = browser.row_info(obj)
        finally:
            browser.ROW_EXTRAS.remove(extra)
        self.assertEqual(seen[0][2][0], ("BW-01-01001-001", ""))
        self.assertEqual((meta, marks[-1]), ("meta", ("x", "accent")))
        self.assertEqual(browser.row_info(obj, badges=False)[1], [])


def _fcstd(path, kind="PartDesign::Body", links=()):
    """A minimal .FCStd: one object of kind, plus XLinks."""
    import zipfile

    os.makedirs(os.path.dirname(path), exist_ok=True)
    xlinks = "".join(f'<XLink file="{f}" name="Body"/>' for f in links)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", (
            '<?xml version="1.0" encoding="utf-8"?><Document SchemaVersion="4">'
            f'<Objects Count="1"><Object type="{kind}" name="Main" id="0"/></Objects>'
            f'<ObjectData Count="1"><Object name="Main"><Properties Count="0">{xlinks}</Properties></Object>'
            '</ObjectData></Document>'))
        archive.writestr("GuiDocument.xml", f"<GuiDocument salt='{path}'/>")
    return path


class TestCheckInLinks(unittest.TestCase):
    """Check In points links at <P/N>.FCStd and imports outside files (plan section 4)."""

    def setUp(self):
        import shutil

        from bruceworx import vault_setup as vs

        self.vs = vs
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-vc-links-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        vs.create_vault(vs.VaultSetup(name="Links", folder=os.path.join(self.tmp, "vault")), actor="bruce@mac")
        src = os.path.join(self.tmp, "src")
        _fcstd(os.path.join(src, "plate.FCStd"))
        _fcstd(os.path.join(src, "frame.FCStd"), "Assembly::AssemblyObject", ("plate.FCStd",))
        self.s = vc.Session(os.path.join(self.tmp, "vault"))
        report = vs.run_import(self.s.vault, vs.plan_numbers(vs.scan_for_import(src), self.s.root, "BW"), "BW")
        self.numbers = {os.path.basename(p): key for p, key in report.imported}
        self.frame = self.numbers["frame.FCStd"]
        self.s.work.checkout(self.frame)
        self.writer = vc._writer
        vc._writer = lambda: None  # no FreeCAD document round trip for synthetic files
        self.addCleanup(setattr, vc, "_writer", self.writer)

    def test_outside_file_is_imported_and_linked_by_number(self):
        from bruceworx import pdm_workspace as ws

        washer = _fcstd(os.path.join(self.tmp, "elsewhere", "washer.FCStd"))
        _fcstd(self.s.work.path(self.frame), "Assembly::AssemblyObject",
               (self.numbers["plate.FCStd"] + ".FCStd", "../../../elsewhere/washer.FCStd", "lost.FCStd"))
        lines, imports = vc.link_summary(self.s.work.link_fixes([self.frame]))
        self.assertEqual(imports, [washer])
        self.assertEqual(lines, ["Not in the vault yet: washer.FCStd.",
                                 "Not found: lost.FCStd. This link is checked in as written."])
        changed, left, reports = vc.checkin_links(self.s, [self.frame])
        self.assertEqual((changed, [f.link for f in left]), (1, ["lost.FCStd"]))
        new = reports[0].imported[0][1]
        self.assertEqual(ws.linked_files(self.s.work.path(self.frame)),
                         [self.numbers["plate.FCStd"] + ".FCStd", new + ".FCStd", "lost.FCStd"])
        self.assertTrue(os.path.exists(self.s.work.path(new)))

    def test_without_import_the_links_stay(self):
        _fcstd(os.path.join(self.tmp, "elsewhere", "washer.FCStd"))
        _fcstd(self.s.work.path(self.frame), "Assembly::AssemblyObject", ("../../../elsewhere/washer.FCStd",))
        changed, left, reports = vc.checkin_links(self.s, [self.frame], import_outside=False)
        self.assertEqual((changed, len(left), reports), (0, 1, []))

    def test_relink_summary_line(self):
        fix = SimpleNamespace(link="../sam@mac/P.FCStd", path="/x/P.FCStd", target="P", exists=True,
                              needs_import=False)
        self.assertEqual(vc.link_summary([fix, fix])[0],
                         ["1 link to vault files is pointed at the part number before the check-in."])


class TestICloudDownload(unittest.TestCase):
    def setUp(self):
        import shutil

        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-vc-icloud-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_texts(self):
        self.assertEqual(vc.duration_text(120), "2 minutes")
        self.assertEqual(vc.duration_text(60), "1 minute")
        self.assertEqual(vc.duration_text(45), "45 seconds")
        self.assertEqual(vc.download_text(3, 2_500_000), "Downloading 3 files (2.5 MB) from iCloud\u2026")
        self.assertEqual(vc.download_text(1, 512), "Downloading 1 file (512 bytes) from iCloud\u2026")

    def test_nothing_to_download_returns_at_once(self):
        path = _fcstd(os.path.join(self.tmp, "a.FCStd"))
        self.assertIsNone(vc.ensure_downloaded([path, os.path.join(self.tmp, "none.FCStd")]))

    def test_a_stand_in_that_never_downloads_gives_a_clear_message(self):
        import plistlib

        from bruceworx import pdm_workspace as ws

        path = os.path.join(self.tmp, "b.FCStd")
        with open(os.path.join(self.tmp, ".b.FCStd.icloud"), "wb") as handle:
            plistlib.dump({"NSURLFileSizeKey": 1000}, handle)
        with self.assertRaises(ws.NotDownloadedError) as caught:
            vc.ensure_downloaded([path], stall=0)
        self.assertIn("did not download from iCloud: b.FCStd", str(caught.exception))
        self.assertIn("online and signed in to iCloud", str(caught.exception))

    def test_request_download_of_a_local_file_is_refused(self):
        # Not in iCloud: NSFileManager says no (and nothing is downloaded anywhere).
        self.assertFalse(vc.request_download(_fcstd(os.path.join(self.tmp, "c.FCStd"))))


if __name__ == "__main__":
    unittest.main()
