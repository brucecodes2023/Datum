# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the vault's iso pictures (bruceworx_preview): the thumbnail in the file, the mark, and
when a check-in replaces it. The rendering itself needs a 3D view (tested in the GUI)."""

import os
import shutil
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

import bruceworx_preview as pv


def fcstd(path, thumbnail=b"old"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Document.xml", "<Document/>")
        if thumbnail is not None:
            archive.writestr(pv.THUMBNAIL, thumbnail)
        archive.writestr("GuiDocument.xml", "<Document/>")


def marked_png():
    from PySide import QtGui

    image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
    image.fill(0xFFFFFF)
    image.setText(*pv.MARK)
    return pv.png_bytes(image)


class TestThumbnailInTheFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-preview-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "LE-01-01003-001.FCStd")

    def test_replaced_in_place_keeping_every_other_entry(self):
        fcstd(self.path)
        pv.set_thumbnail(self.path, b"new")
        with zipfile.ZipFile(self.path) as archive:
            self.assertEqual(archive.namelist(), ["Document.xml", pv.THUMBNAIL, "GuiDocument.xml"])
            self.assertEqual(archive.read("Document.xml"), b"<Document/>")
        self.assertEqual(pv.thumbnail_of(self.path), b"new")
        self.assertFalse(os.path.exists(self.path + ".bw-thumbnail"))

    def test_added_when_the_file_has_none(self):
        fcstd(self.path, thumbnail=None)
        self.assertEqual(pv.thumbnail_of(self.path), b"")
        pv.set_thumbnail(self.path, b"new")
        self.assertEqual(pv.thumbnail_of(self.path), b"new")

    def test_a_damaged_file_is_left_alone(self):
        with open(self.path, "wb") as out:
            out.write(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            pv.set_thumbnail(self.path, b"new")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"not a zip")
        self.assertFalse(os.path.exists(self.path + ".bw-thumbnail"))

    def test_the_mark(self):
        self.assertTrue(pv.is_iso(marked_png()))
        self.assertFalse(pv.is_iso(b""))
        from PySide import QtGui

        plain = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
        self.assertFalse(pv.is_iso(pv.png_bytes(plain)))


class TestSourceForPictures(unittest.TestCase):
    """Refresh Pictures renders a part from its work copy only when that is the latest version, an
    assembly from its work copy whenever there is one (its links then find the work copies)."""

    def session(self, kind, work_exists=True, same=False):
        tmp = tempfile.mkdtemp(prefix="bw-src-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        work = os.path.join(tmp, "work", "P.FCStd")
        if work_exists:
            os.makedirs(os.path.dirname(work))
            fcstd(work)
        latest = SimpleNamespace(sha256="latest", object_name="objects/latest.FCStd")
        vault = SimpleNamespace(root=os.path.join(tmp, "vault"), items={"P": SimpleNamespace(item_type=kind)},
                                latest_version=lambda pn: latest)
        ws = SimpleNamespace(path=lambda pn: work, local_hash=lambda p: "latest" if same else "older")
        return SimpleNamespace(vault=vault, work=ws), work, os.path.join(vault.root, latest.object_name)

    def test_a_current_work_copy_is_used(self):
        for kind in ("part", "assembly"):
            s, work, _stored = self.session(kind, same=True)
            self.assertEqual(pv.source_for(s, "P"), work, kind)

    def test_an_older_part_copy_is_not_but_an_older_assembly_copy_is(self):
        s, _work, stored = self.session("part")
        self.assertEqual(pv.source_for(s, "P"), stored)
        s, work, _stored = self.session("assembly")
        self.assertEqual(pv.source_for(s, "P"), work)

    def test_no_work_copy_means_the_stored_file(self):
        s, _work, stored = self.session("assembly", work_exists=False)
        self.assertEqual(pv.source_for(s, "P"), stored)


class TestStampAtCheckIn(unittest.TestCase):
    """stamp_work_file renders only a part or assembly that is about to become a new version."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-stamp-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rendered = []
        self._picture = pv.picture_of
        pv.picture_of = lambda path, size=pv.SIZE: self.rendered.append(path) or b"iso"
        self.addCleanup(setattr, pv, "picture_of", self._picture)

    def session(self, kind, changed=True):
        path = os.path.join(self.tmp, "P.FCStd")
        fcstd(path)
        items = {"P": SimpleNamespace(item_type=kind)}
        latest = SimpleNamespace(sha256="base")
        work = SimpleNamespace(path=lambda pn: path, local_hash=lambda p: "changed" if changed else "base")
        vault = SimpleNamespace(items=items, latest_version=lambda pn: latest)
        return SimpleNamespace(vault=vault, work=work), path

    def test_a_changed_part_gets_the_picture(self):
        s, path = self.session("part")
        self.assertTrue(pv.stamp_work_file(s, "P"))
        self.assertEqual(pv.thumbnail_of(path), b"iso")

    def test_an_unchanged_file_is_not_touched(self):
        s, path = self.session("assembly", changed=False)
        self.assertFalse(pv.stamp_work_file(s, "P"))
        self.assertEqual((pv.thumbnail_of(path), self.rendered), (b"old", []))

    def test_a_drawing_keeps_its_sheet(self):
        s, path = self.session("drawing")
        self.assertFalse(pv.stamp_work_file(s, "P"))
        self.assertEqual(self.rendered, [])


if __name__ == "__main__":
    unittest.main()
