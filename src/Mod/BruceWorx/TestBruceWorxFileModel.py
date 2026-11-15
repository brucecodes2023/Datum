# SPDX-License-Identifier: LGPL-2.1-or-later
"""ASM-01 file model, headless: one body or part written out beside the parent, replaced by an
App::Link whose saved path is relative, and that link still resolves after the folder is moved
and the parent is opened from another working directory.

FreeCADCmd -c runs before PYTHONPATH is on sys.path, so the runner inserts it first.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD as App

import bruceworx_filemodel as fm


def _xlink_file(fcstd, linked_name):
    """The file attribute FreeCAD saved on the XLink that points at ``linked_name``."""
    with zipfile.ZipFile(fcstd) as archive:
        xml = archive.read("Document.xml").decode("utf-8")
    needle = f'name="{linked_name}"'
    for line in xml.splitlines():
        if "XLink" in line and "file=" in line and needle in line:
            return line.split('file="', 1)[1].split('"', 1)[0]
    raise AssertionError(f"no XLink to {linked_name} in {fcstd}")


def _body(doc, name, length, width, height):
    body = doc.addObject("PartDesign::Body", name)
    box = body.newObject("PartDesign::AdditiveBox", name + "Box")
    box.Length, box.Width, box.Height = length, width, height
    return body


def _part(doc, name, length):
    part = doc.addObject("App::Part", name)
    box = doc.addObject("Part::Box", name + "Box")
    box.Length = length
    part.addObject(box)
    return part


class TestMakeExternal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-asm01-")
        self.dest = None
        self._cwd = os.getcwd()
        self._before = set(App.listDocuments())

    def tearDown(self):
        os.chdir(self._cwd)
        for name in list(App.listDocuments()):
            if name not in self._before:
                App.closeDocument(name)
        for folder in (self.dest, self.tmp, os.path.realpath(self.tmp)):
            shutil.rmtree(folder, ignore_errors=True)

    def test_refuses_unsaved_and_plain_shapes(self):
        doc = App.newDocument("BWAsmRefuse")
        body = doc.addObject("PartDesign::Body", "Body")
        with self.assertRaises(ValueError):
            fm.make_external(body)
        doc.saveAs(os.path.join(self.tmp, "Refuse.FCStd"))
        box = doc.addObject("Part::Box", "Box")
        with self.assertRaises(ValueError):
            fm.make_external(box)
        with self.assertRaises(ValueError):
            fm.make_external(None)

    def test_body_link_survives_a_moved_folder(self):
        doc = App.newDocument("BWAsmBodies")
        assembly = doc.addObject("App::Part", "Assembly")
        keep = _body(doc, "Keep", 10, 10, 10)
        moved = _body(doc, "Moved", 20, 8, 4)
        moved.Placement.Base.x = 25
        assembly.addObject(keep)
        assembly.addObject(moved)
        doc.recompute()
        doc.saveAs(os.path.join(self.tmp, "Asm.FCStd"))

        link = fm.make_external(moved)
        doc.save()

        self.assertEqual(link.TypeId, "App::Link")
        self.assertEqual(link.Name, "Moved")
        self.assertEqual([obj.Name for obj in assembly.Group], ["Keep", "Moved"])
        self.assertIsNone(doc.getObject("MovedBox"))
        self.assertAlmostEqual(doc.getObject("Keep").Shape.Volume, 1000, delta=1e-6)
        self.assertAlmostEqual(link.Shape.Volume, 640, delta=1e-6)
        self.assertAlmostEqual(link.Shape.BoundBox.XMin, 25, delta=1e-6)
        self.assertEqual(link.getLinkedObject(True).TypeId, "PartDesign::Body")
        stored = _xlink_file(doc.FileName, "Moved")
        self.assertEqual(stored, "Moved.FCStd")
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(doc.FileName), stored)))

        reopened = self._reopen(doc.FileName)
        link = reopened.getObject("Moved")
        self.assertIsNotNone(link.LinkedObject)
        self.assertEqual(os.path.basename(link.LinkedObject.Document.FileName), "Moved.FCStd")
        self.assertEqual(
            os.path.dirname(os.path.realpath(link.LinkedObject.Document.FileName)),
            os.path.dirname(os.path.realpath(reopened.FileName)),
        )
        self.assertAlmostEqual(link.Shape.Volume, 640, delta=1e-6)
        self.assertAlmostEqual(link.Shape.BoundBox.XMin, 25, delta=1e-6)
        self.assertAlmostEqual(reopened.getObject("Keep").Shape.Volume, 1000, delta=1e-6)
        self.assertEqual([obj.Name for obj in reopened.getObject("Assembly").Group], ["Keep", "Moved"])

    def test_part_link_survives_a_moved_folder(self):
        doc = App.newDocument("BWAsmParts")
        stay = _part(doc, "Stay", 8)
        leave = _part(doc, "Leave", 12)
        leave.Placement.Base.y = 15
        doc.recompute()
        doc.saveAs(os.path.join(self.tmp, "TwoParts.FCStd"))

        link = fm.make_external(leave)
        doc.save()

        self.assertEqual(link.TypeId, "App::Link")
        self.assertEqual(stay.TypeId, "App::Part")
        self.assertIsNone(doc.getObject("LeaveBox"))
        self.assertEqual(_xlink_file(doc.FileName, "Leave"), "Leave.FCStd")
        self.assertAlmostEqual(link.Shape.Volume, 1200, delta=1e-6)
        self.assertAlmostEqual(link.Shape.BoundBox.YMin, 15, delta=1e-6)

        reopened = self._reopen(doc.FileName)
        link = reopened.getObject("Leave")
        self.assertEqual(link.LinkedObject.TypeId, "App::Part")
        self.assertEqual(os.path.basename(link.LinkedObject.Document.FileName), "Leave.FCStd")
        self.assertAlmostEqual(link.Shape.Volume, 1200, delta=1e-6)
        self.assertAlmostEqual(link.Shape.BoundBox.YMin, 15, delta=1e-6)
        self.assertAlmostEqual(reopened.getObject("Stay").Shape.Volume, 800, delta=1e-6)

    def _reopen(self, filename):
        for name in list(App.listDocuments()):
            if name not in self._before:
                App.closeDocument(name)
        folder = os.path.dirname(os.path.realpath(filename))
        self.dest = folder + "-moved"
        shutil.move(folder, self.dest)
        os.chdir("/")
        doc = App.openDocument(os.path.join(self.dest, os.path.basename(filename)))
        doc.recompute()
        return doc


if __name__ == "__main__":
    unittest.main()
