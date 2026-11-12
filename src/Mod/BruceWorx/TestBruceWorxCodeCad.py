# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for code-CAD parts (bruceworx_codecad): dimensions, the Parameters sheet, trust, the cache.

Headless. Run: FreeCADCmd -t TestBruceWorxCodeCad
"""

import importlib.util
import os
import tempfile
import unittest

import FreeCAD

import bruceworx_codecad as cc
import bruceworx_codecad_dims as dims
import bruceworx_codecad_templates as templates

HAVE_OCP = importlib.util.find_spec("OCP") is not None
NEEDS = "no OCP in this Python (pixi.toml: ocp, build123d, cadquery)"
PLATE = """from build123d import *
w = param("tcc_w", 40, "Width")
t = param("tcc_t", 4)
tilt = param.angle("tcc_tilt", 0)
n = param.count("tcc_n", 2, "Holes")
plate = Box(w, 20, t)
for i in range(n):
    plate -= Pos(-w / 4 + i * w / 2, 0, 0) * Cylinder(2, t)
result = Rot(0, 0, tilt) * plate
"""
PLATE_VOLUME = 40 * 20 * 4 - 2 * 3.141592653589793 * 4 * 4


class TestParam(unittest.TestCase):
    def test_kinds_defaults_and_values(self):
        param = cc.Param({"a": 7, "n": 3.0})
        self.assertEqual(param("a", 4), 7.0)
        self.assertEqual(param("b", 2.5, "B"), 2.5)
        self.assertEqual(param.count("n", 6), 3)
        self.assertIsInstance(param.count("n", 6), int)
        self.assertEqual(param.angle("tilt", 30), 30.0)
        self.assertEqual([(d.name, d.kind, d.cell()) for d in param.declared.values()],
                         [("a", "length", "4 mm"), ("b", "length", "2.5 mm"), ("n", "count", "6"),
                          ("tilt", "angle", "30 deg")])

    def test_bad_declarations(self):
        param = cc.Param()
        for name in ("A1", "2x", "plate t", ""):
            with self.assertRaisesRegex(cc.CodeCadError, "not a dimension name"):
                param(name, 1)
        with self.assertRaisesRegex(cc.CodeCadError, "must be a number"):
            param("a", "4 mm")
        param("a", 1)
        with self.assertRaisesRegex(cc.CodeCadError, "declared twice"):
            param.angle("a", 1)

    def test_hash_is_of_the_text(self):
        self.assertEqual(cc.script_hash("x"), cc.script_hash("x"))
        self.assertNotEqual(cc.script_hash("x"), cc.script_hash("x "))


@unittest.skipUnless(HAVE_OCP, NEEDS)
class TestCodeParts(unittest.TestCase):
    def setUp(self):
        self.old_trust = os.environ.get("BW_CODECAD_TRUST_FILE")
        os.environ["BW_CODECAD_TRUST_FILE"] = os.path.join(tempfile.mkdtemp(), "trusted.json")
        cc.clear_cache()
        self.doc = FreeCAD.newDocument("CodeCadTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        if self.old_trust is None:
            os.environ.pop("BW_CODECAD_TRUST_FILE", None)
        else:
            os.environ["BW_CODECAD_TRUST_FILE"] = self.old_trust

    def sheet(self):
        return self.doc.getObject("Parameters")

    def test_make_adds_parameters_and_binds_them(self):
        (part,), result, added = cc.make(self.doc, PLATE, "Plate")
        self.assertEqual(added, ["tcc_w", "tcc_t", "tcc_tilt", "tcc_n"])
        self.assertEqual(part.TypeId, "Part::FeaturePython")
        self.assertEqual((part.Label, part.CodeCadStatus), ("Plate", cc.UP_TO_DATE))
        self.assertAlmostEqual(part.Shape.Volume, PLATE_VOLUME, places=6)
        sheet = self.sheet()
        self.assertEqual(sheet.get("tcc_w").Value, 40.0)
        self.assertEqual(str(sheet.get("tcc_w").Unit), str(FreeCAD.Units.Length))
        self.assertEqual(sheet.get("tcc_n"), 2)
        dims = cc.dimensions(part)
        self.assertEqual([(d["name"], d["kind"], d["value"], d["expression"]) for d in dims],
                         [("tcc_w", "length", 40.0, "Parameters.tcc_w"), ("tcc_t", "length", 4.0, "Parameters.tcc_t"),
                          ("tcc_tilt", "angle", 0.0, "Parameters.tcc_tilt"), ("tcc_n", "count", 2, "Parameters.tcc_n")])

    def test_sheet_edit_reruns_and_cache_serves_old_values(self):
        (part,), _result, _added = cc.make(self.doc, PLATE)
        runs = cc.stats()["runs"]
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "60 mm")
        sheet.set(sheet.getCellFromAlias("tcc_n"), "0")
        self.doc.recompute()
        self.assertAlmostEqual(part.Shape.Volume, 60 * 20 * 4, places=6)
        self.assertEqual(cc.stats()["runs"], runs + 1)
        self.assertGreater(part.CodeCadSeconds, 0)
        sheet.set(sheet.getCellFromAlias("tcc_w"), "40 mm")
        sheet.set(sheet.getCellFromAlias("tcc_n"), "2")
        self.doc.recompute()
        self.assertAlmostEqual(part.Shape.Volume, PLATE_VOLUME, places=6)
        self.assertEqual(cc.stats()["runs"], runs + 1)  # back to values already run: no run
        self.assertEqual(part.CodeCadSeconds, 0.0)

    def test_existing_sheet_value_wins_over_the_default(self):
        cc.make(self.doc, "from build123d import *\nresult = Box(param('tcc_w', 10), 1, 1)")
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "25 mm")
        self.doc.recompute()
        (part,), _result, added = cc.make(self.doc, PLATE)
        self.assertEqual(added, ["tcc_t", "tcc_tilt", "tcc_n"])
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 25, places=6)

    def test_value_on_the_part_unbinds_one_dimension(self):
        (part,), _result, _added = cc.make(self.doc, PLATE)
        part.setExpression("tcc_t", None)
        part.tcc_t = 10
        self.doc.recompute()
        self.assertAlmostEqual(part.Shape.BoundBox.ZLength, 10, places=6)
        self.assertEqual(self.sheet().get("tcc_t").Value, 4.0)

    def test_several_shapes_share_one_run(self):
        code = ("from build123d import *\nw = param('tcc_w', 10)\n"
                "show_object(Box(w, w, 2), name='Plate')\nshow_object(Pos(0, 0, 5) * Cylinder(1, w), name='Pin')")
        runs = cc.stats()["runs"]
        parts, _result, _added = cc.make(self.doc, code)
        self.assertEqual([p.Label for p in parts], ["Plate", "Pin"])
        self.assertEqual(cc.group(parts[1]), parts)
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "20 mm")
        self.doc.recompute()
        self.assertAlmostEqual(parts[0].Shape.Volume, 800, places=6)
        self.assertAlmostEqual(parts[1].Shape.Volume, 3.141592653589793 * 20, places=6)
        self.assertEqual(cc.stats()["runs"], runs + 2)  # once to make, once for the new width: not per part

    def test_set_script_keeps_values_and_drops_old_dimensions(self):
        (part,), _result, _added = cc.make(self.doc, PLATE)
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "50 mm")
        self.doc.recompute()
        new = "from build123d import *\nresult = Box(param('tcc_w', 40), param('tcc_d', 8, 'Depth'), 1)"
        parts, _result, added = cc.set_script(part, new)
        self.assertEqual((parts, added), ([part], ["tcc_d"]))
        self.assertAlmostEqual(part.Shape.Volume, 50 * 8, places=6)
        self.assertEqual(part.CodeCadParams, ["tcc_w", "tcc_d"])
        self.assertFalse(hasattr(part, "tcc_tilt"))
        self.assertIsNotNone(sheet.getCellFromAlias("tcc_tilt"))  # the sheet keeps it: others may use it

    def test_failed_script_changes_nothing_and_names_the_line(self):
        count = len(self.doc.Objects)
        with self.assertRaises(cc.ScriptError) as caught:
            cc.make(self.doc, "from build123d import *\nw = param('tcc_w', 4)\nresult = Box(w, 1, 1)\n1 / 0\n")
        self.assertEqual(caught.exception.line, 4)
        self.assertIn("line 4: ZeroDivisionError", caught.exception.short)
        with self.assertRaises(cc.ScriptError) as caught:
            cc.make(self.doc, "result = (\n")
        self.assertEqual(caught.exception.line, 1)
        with self.assertRaisesRegex(cc.CodeCadError, "every part has a property"):
            cc.make(self.doc, "from build123d import *\nresult = Box(param('Label', 4), 1, 1)")
        self.assertEqual(len(self.doc.Objects), count)
        (part,), _result, _added = cc.make(self.doc, PLATE)
        with self.assertRaises(cc.ScriptError):
            cc.set_script(part, "1 / 0")
        self.assertEqual(part.CodeCadScript, PLATE)

    def test_bad_value_keeps_the_last_shape_and_says_why(self):
        code = "from build123d import *\nw = param('tcc_w', 4)\nresult = Box(w, 1, 1)"
        (part,), _result, _added = cc.make(self.doc, code)
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "0 mm")
        self.doc.recompute()
        self.assertTrue(part.CodeCadStatus.startswith("Error: line 3"), part.CodeCadStatus)
        self.assertFalse(part.isValid())
        self.assertAlmostEqual(part.Shape.Volume, 4, places=6)
        sheet.set(sheet.getCellFromAlias("tcc_w"), "6 mm")
        self.doc.recompute()
        self.assertEqual(part.CodeCadStatus, cc.UP_TO_DATE)
        self.assertAlmostEqual(part.Shape.Volume, 6, places=6)

    def test_script_from_elsewhere_waits_for_approval(self):
        (part,), _result, _added = cc.make(self.doc, PLATE)
        path = os.path.join(tempfile.mkdtemp(), "foreign.FCStd")
        self.doc.saveAs(path)
        name = part.Name
        FreeCAD.closeDocument(self.doc.Name)
        os.environ["BW_CODECAD_TRUST_FILE"] = os.path.join(tempfile.mkdtemp(), "another-mac.json")
        cc.clear_cache()
        runs = cc.stats()["runs"]
        self.doc = FreeCAD.openDocument(path)
        part = self.doc.getObject(name)
        self.assertEqual(cc.untrusted(self.doc), [part])
        sheet = self.sheet()
        sheet.set(sheet.getCellFromAlias("tcc_w"), "80 mm")
        self.doc.recompute()
        self.assertEqual(part.CodeCadStatus, cc.NOT_TRUSTED)
        self.assertEqual(cc.stats()["runs"], runs)                           # nothing ran
        self.assertAlmostEqual(part.Shape.Volume, PLATE_VOLUME, places=6)    # the saved shape stays
        self.assertEqual(cc.trust_document(self.doc), [part])
        self.assertEqual(part.CodeCadStatus, cc.UP_TO_DATE)
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 80, places=6)
        self.assertEqual(cc.untrusted(self.doc), [])

    def test_placement_survives_a_rebuild_and_to_solid(self):
        (part,), _result, _added = cc.make(self.doc, PLATE)
        part.Placement.Base = FreeCAD.Vector(100, 0, 0)
        runs = cc.stats()["runs"]
        cc.rebuild(part)
        self.assertEqual(cc.stats()["runs"], runs + 1)
        self.assertAlmostEqual(part.Shape.BoundBox.Center.x, 100, places=6)
        plain = cc.to_solid(part)
        self.assertEqual(plain.TypeId, "Part::Feature")
        self.assertAlmostEqual(plain.Shape.BoundBox.Center.x, 100, places=6)
        self.assertEqual(cc.code_parts(self.doc), [])


@unittest.skipUnless(HAVE_OCP, NEEDS)
class TestTemplates(unittest.TestCase):
    def test_a_shape_keeps_where_the_script_put_it(self):
        doc = FreeCAD.newDocument("CcPlaced")
        try:
            parts, _result, _added = cc.make(doc, "from build123d import *\n"
                                             "result = Pos(5, 0, 30) * Rot(0, 0, 90) * Box(10, 4, 2)\n", "Moved")
            doc.recompute()
            box = parts[0].Shape.BoundBox
            self.assertTrue(parts[0].Placement.isIdentity())
            self.assertAlmostEqual(box.ZMin, 29, places=6)
            self.assertAlmostEqual(box.XLength, 4, places=6)
            self.assertAlmostEqual(box.YLength, 10, places=6)
            parts, _result, _added = cc.make(doc, templates.script("enclosure"), None)
            doc.recompute()
            self.assertAlmostEqual(parts[1].Shape.BoundBox.ZMin, 30, places=6)  # the lid sits on the box
            self.assertLess(parts[0].Shape.common(parts[1].Shape).Volume, 1e-6)
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_every_template_makes_valid_solids_and_declares_dimensions(self):
        keys = [key for key, _title, _text, _script in templates.templates()]
        self.assertEqual(len(keys), len(set(keys)))
        for key, title, text, script in templates.templates():
            with self.subTest(key):
                self.assertTrue(title and text)
                result = cc.run(script)
                self.assertTrue(result.declared, key)
                for decl in result.declared.values():
                    self.assertTrue(decl.comment, f"{key}: {decl.name} has no comment")
                for _label, shape in result.shapes:
                    self.assertTrue(shape.isValid(), key)
                    self.assertEqual(len(shape.Solids), 1, key)
                    self.assertGreater(shape.Volume, 100, key)

    def test_template_dimensions_do_what_they_say(self):
        plate = cc.run(templates.script("plate_holes"), {"plate_l": 100, "holes_x": 4, "holes_y": 1})
        box = plate.shapes[0][1].BoundBox
        self.assertAlmostEqual(box.XLength, 100, places=6)
        cylinders = [f for f in plate.shapes[0][1].Faces if f.Surface.__class__.__name__ == "Cylinder"
                     and abs(f.Surface.Radius - 1.7) < 1e-6]
        self.assertEqual(len(cylinders), 4)
        parts = cc.run(templates.script("enclosure"), {"box_h": 40})
        self.assertEqual([label for label, _shape in parts.shapes], ["Enclosure", "Lid"])
        self.assertAlmostEqual(parts.shapes[1][1].BoundBox.ZMin, 40, places=6)
        frame = cc.run(templates.script("quad_frame"), {"motor_y": 60}).shapes[0][1].BoundBox
        self.assertAlmostEqual(frame.YLength, 2 * 60 + 21, places=4)
        bracket = cc.run(templates.script("bracket")).shapes[0][1].BoundBox
        self.assertEqual([round(v, 6) for v in (bracket.XLength, bracket.YLength, bracket.ZLength)], [40, 30, 30])


@unittest.skipUnless(HAVE_OCP, NEEDS)
class TestDimensionsOnTheModel(unittest.TestCase):
    PLATE = templates.script("plate_holes")

    def setUp(self):
        self.old_trust = os.environ.get("BW_CODECAD_TRUST_FILE")
        os.environ["BW_CODECAD_TRUST_FILE"] = os.path.join(tempfile.mkdtemp(), "trusted.json")
        cc.clear_cache()
        dims._verified.clear()
        self.doc = FreeCAD.newDocument("CodeCadDims")
        self.doc.UndoMode = 1  # FreeCADCmd documents start without undo

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        if self.old_trust is None:
            os.environ.pop("BW_CODECAD_TRUST_FILE", None)
        else:
            os.environ["BW_CODECAD_TRUST_FILE"] = self.old_trust

    def test_measures_of_a_drilled_block(self):
        import Part

        block = Part.makeBox(40, 20, 10).cut(Part.makeCylinder(3, 10, FreeCAD.Vector(10, 10, 0)))
        found = dims.measures(block)
        kinds = sorted({m.kind for m in found})
        self.assertEqual(kinds, ["diameter", "edge", "extent"])
        self.assertEqual(sorted(round(m.value, 6) for m in found if m.kind == "diameter"), [6.0, 6.0])
        self.assertEqual(sorted(round(m.value, 6) for m in found if m.kind == "extent"), [10.0, 20.0, 40.0])
        self.assertEqual(dims.measures(Part.Shape()), [])

    def test_check_tells_a_real_dimension_from_an_equal_number(self):
        code = ("from build123d import *\nw = param('dw', 30, 'Width')\nlucky = param('lucky', 30, 'Not a size')\n"
                "hole = param('dhole', 6, 'Hole')\nresult = Box(w, 20, 5) - Cylinder(hole / 2, 5)")
        values = {"dw": 30.0, "lucky": 30.0, "dhole": 6.0}
        self.assertEqual(dims.check(code, values, "dw"), ("edge", "extent"))
        self.assertEqual(dims.check(code, values, "lucky"), ())
        self.assertEqual(dims.check(code, values, "dhole"), ("diameter",))
        self.assertEqual(dims.known(code, 0, "dhole"), ("diameter",))
        self.assertIsNone(dims.known(code, 0, "other"))
        runs = cc.stats()["runs"]
        dims.check(code, values, "dw")
        self.assertEqual(cc.stats()["runs"], runs)  # remembered

    def test_match_attaches_checked_dimensions_only(self):
        (part,) = cc.make(self.doc, self.PLATE, "Plate")[0]
        listed = cc.dimensions(part)
        values = cc.values_of(part)
        allowed = {d["name"]: dims.check(self.PLATE, values, d["name"]) for d in listed if d["kind"] == "length"}
        self.assertEqual(allowed["plate_l"], ("extent",))
        self.assertEqual(allowed["plate_t"], ("edge", "extent"))
        self.assertEqual(allowed["hole_d"], ("diameter",))
        self.assertEqual(allowed["corner_r"], ("radius",))
        self.assertEqual(allowed["hole_inset"], ())
        attached = dims.match(listed, part.Shape, allowed)
        self.assertEqual({name: m.kind for name, m in attached.items()},
                         {"plate_l": "extent", "plate_w": "extent", "plate_t": "edge", "corner_r": "radius",
                          "hole_d": "diameter"})
        self.assertEqual(dims.match(listed, part.Shape, {}), {})
        segments, label = dims.witness(attached["plate_l"], part.Shape.BoundBox.Center, 5.0)
        self.assertEqual(len(segments), 3)
        middle = (attached["plate_l"].a + attached["plate_l"].b) * 0.5
        self.assertAlmostEqual((label - middle).Length, 5.0, places=6)
        self.assertLess(label.y, -25)                        # outside the plate, on the near side
        segments, label = dims.witness(attached["hole_d"], part.Shape.BoundBox.Center, 5.0)
        self.assertEqual(len(segments), 1)

    def test_set_dimension_writes_the_sheet_or_the_part(self):
        (part,) = cc.make(self.doc, self.PLATE, "Plate")[0]
        sheet = self.doc.getObject("Parameters")
        self.assertEqual(dims.set_dimension(part, "plate_l", "100"), "sheet")
        self.assertEqual(sheet.get("plate_l").Value, 100.0)
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 100, places=6)
        self.assertEqual(self.doc.UndoNames[0], "Plate: plate_l")
        self.assertEqual(dims.set_dimension(part, "plate_w", "2 in"), "sheet")
        self.assertAlmostEqual(part.Shape.BoundBox.YLength, 50.8, places=6)
        self.assertEqual(dims.set_dimension(part, "plate_t", "=plate_w / 10"), "sheet")
        self.assertAlmostEqual(part.Shape.BoundBox.ZLength, 5.08, places=6)
        self.assertEqual(dims.set_dimension(part, "holes_x", "4"), "sheet")
        self.assertEqual(part.holes_x, 4)
        part.setExpression("corner_r", None)
        self.assertEqual(dims.set_dimension(part, "corner_r", "8"), "part")
        self.assertEqual((part.corner_r.Value, sheet.get("corner_r").Value), (8.0, 5.0))
        for text, why in (("", "type a value"), ("2.5", "whole number")):
            with self.assertRaisesRegex(cc.CodeCadError, why):
                dims.set_dimension(part, "holes_y", text)
        with self.assertRaisesRegex(cc.CodeCadError, "plate_l"):
            dims.set_dimension(part, "plate_l", "abc def")
        self.assertEqual(sheet.get("plate_l").Value, 100.0)
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 100, places=6)
        with self.assertRaisesRegex(cc.CodeCadError, "no dimension"):
            dims.set_dimension(part, "nope", "1")
        self.assertEqual(dims.chip_text(cc.dimensions(part)[0]), "plate_l = 100 mm")
        self.assertEqual(dims.edit_text(cc.dimensions(part)[0]), "100 mm")
