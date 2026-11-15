# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for semantic GD&T (bruceworx_gdt, DRW-03): datums, feature control frames,
a missing-datum flag, and the set surviving save and reopen on its JSON property."""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_gdt as gdt


def _position_missing_c():
    """Datums A and B, and a position frame that also names C."""
    return gdt.GdtModel(
        [gdt.Datum("A"), gdt.Datum("B")],
        [gdt.FeatureControlFrame("position", 0.1, ["A", "B", "C"])],
    )


class TestModel(unittest.TestCase):
    def test_datum_letter(self):
        self.assertEqual(gdt.Datum("a").label, "A")
        self.assertEqual(gdt.Datum("C").label, "C")
        for label in ("I", "O", "Q", "i", "", "AB", "1"):
            with self.assertRaises(ValueError):
                gdt.Datum(label)

    def test_frame_stores_characteristic_tolerance_and_datums(self):
        frame = gdt.FeatureControlFrame("position", 0.2, ["A", "b", "C"])
        self.assertEqual(frame.characteristic, "position")
        self.assertEqual(frame.tolerance, 0.2)
        self.assertEqual(frame.datums, ("A", "B", "C"))

    def test_rejects_a_bad_frame_or_a_repeated_datum(self):
        with self.assertRaises(ValueError):
            gdt.FeatureControlFrame("not_a_characteristic", 0.1, ["A"])
        with self.assertRaises(ValueError):
            gdt.FeatureControlFrame("flatness", 0, [])
        with self.assertRaises(TypeError):
            gdt.FeatureControlFrame("flatness", True, [])
        with self.assertRaises(ValueError):
            gdt.FeatureControlFrame("position", 0.1, ["A", "B", "C", "D"])
        with self.assertRaises(ValueError):
            gdt.GdtModel([gdt.Datum("A"), gdt.Datum("a")], [])

    def test_a_missing_datum_is_flagged(self):
        model = _position_missing_c()
        self.assertEqual(model.validate(), [gdt.Finding(0, "C", "position")])
        self.assertIn("not in the datum set", str(model.validate()[0]))

    def test_each_missing_reference_is_its_own_flag(self):
        model = gdt.GdtModel(
            [gdt.Datum("A")],
            [gdt.FeatureControlFrame("perpendicularity", 0.05, ["A"]),
             gdt.FeatureControlFrame("position", 0.1, ["B", "C"])],
        )
        self.assertEqual(model.validate(), [
            gdt.Finding(1, "B", "position"),
            gdt.Finding(1, "C", "position"),
        ])

    def test_a_complete_set_is_clean(self):
        model = gdt.GdtModel(
            [gdt.Datum("A"), gdt.Datum("C")],
            [gdt.FeatureControlFrame("flatness", 0.05, []),
             gdt.FeatureControlFrame("position", 0.1, ["c", "A"])],
        )
        self.assertEqual(model.frames[1].datums, ("C", "A"))
        self.assertEqual(model.validate(), [])

    def test_json_keeps_a_missing_datum_reference(self):
        model = _position_missing_c()
        again = gdt.GdtModel.from_json(model.to_json())
        self.assertEqual(again, model)
        self.assertEqual(again.validate(), [gdt.Finding(0, "C", "position")])
        self.assertEqual(gdt.GdtModel.from_json(""), gdt.GdtModel())


class TestSaved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-gdt-")
        self.doc = App.newDocument("GdtTest")

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("GdtTest") or name == "g":
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_set_survives_save_and_reopen(self):
        model = _position_missing_c()
        obj = gdt.make_gdt(self.doc, model)
        box = self.doc.addObject("Part::Box", "Box")
        box.Height = 12
        self.doc.recompute()
        # A geometry edit recomputes the document and leaves the stored frames alone.
        self.assertEqual(obj.Gdt, model.to_json())
        self.assertEqual(gdt.read(obj).validate(), [gdt.Finding(0, "C", "position")])
        path = os.path.join(self.tmp, "g.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        obj = doc.getObject("Gdt")
        self.assertTrue(gdt.is_gdt(obj))
        loaded = gdt.read(obj)
        self.assertEqual(loaded, model)
        self.assertEqual(obj.Gdt, model.to_json())
        self.assertEqual(loaded.validate(), [gdt.Finding(0, "C", "position")])
        doc.getObject("Box").Height = 20
        doc.recompute()
        self.assertEqual(doc.getObject("Gdt").Gdt, model.to_json())
        self.assertEqual(gdt.read(doc.getObject("Gdt")).validate(), [gdt.Finding(0, "C", "position")])


if __name__ == "__main__":
    unittest.main()
