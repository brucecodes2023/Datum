# SPDX-License-Identifier: LGPL-2.1-or-later
"""Parameters tab: edits, expressions with user parameters, sheet cells, which dimensions. No GUI.

The drive test opens a document in FreeCAD, binds a sketch distance and a pad length to the
Parameters sheet, and checks that changing the alias moves both.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_parameters as bpar


class Obj:
    """A document object as the tab reads it: name -> (type, group, editor mode)."""

    def __init__(self, name, type_id="Part::Feature", group=(), **props):
        self.Name = name
        self.TypeId = type_id
        self.Group = list(group)
        self._props = props
        self.PropertiesList = list(props)

    def getTypeIdOfProperty(self, name):
        return self._props[name][0]

    def getGroupOfProperty(self, name):
        return self._props[name][1]

    def getEditorMode(self, name):
        return list(self._props[name][2])

    def getPropertyStatus(self, name):
        return []


L, A = "App::PropertyLength", "App::PropertyAngle"


class EditTests(unittest.TestCase):
    def test_literals(self):
        for text in ("8", "8 mm", "-2.5deg", ".5 in", "1e3 mm", "3 kg*m^2", "45°"):
            self.assertTrue(bpar.is_literal(text), text)
        for text in ("plate_t", "plate_t * 2", "2*3", "Boss.Length", "", "8 mm + 2 mm"):
            self.assertFalse(bpar.is_literal(text), text)
        self.assertTrue(bpar.has_unit("8 mm"))
        self.assertFalse(bpar.has_unit("8"))

    def test_parse_edit(self):
        self.assertEqual(bpar.parse_edit(" 14 mm "), ("value", "14 mm"))
        self.assertEqual(bpar.parse_edit("plate_t * 1.75"), ("expression", "plate_t * 1.75"))
        self.assertEqual(bpar.parse_edit("=8 mm"), ("expression", "8 mm"))
        self.assertEqual(bpar.parse_edit(""), ("clear", ""))
        self.assertEqual(bpar.parse_edit("="), ("clear", ""))

    def test_cell_content(self):
        self.assertEqual(bpar.cell_content("8", "mm"), "8 mm")
        self.assertEqual(bpar.cell_content("8 in", "mm"), "8 in")
        self.assertEqual(bpar.cell_content("8"), "8")
        self.assertEqual(bpar.cell_content("plate_t * 1.75", "mm"), "=plate_t * 1.75")
        self.assertEqual(bpar.cell_content("=plate_t"), "=plate_t")
        self.assertEqual(bpar.cell_content(""), "")


class ExpressionTests(unittest.TestCase):
    aliases = {"plate_t", "boss_h"}

    def test_short(self):
        s = bpar.short_expr
        self.assertEqual(s("Parameters.plate_t * 1.75", self.aliases), "plate_t * 1.75")
        self.assertEqual(s("<<Parameters>>.boss_h + 1 mm", self.aliases), "boss_h + 1 mm")
        self.assertEqual(s("Parameters.B2 * 2", self.aliases), "Parameters.B2 * 2")  # a cell, not an alias
        self.assertEqual(s("Boss.Length", self.aliases), "Boss.Length")
        self.assertEqual(s("Params001.plate_t", self.aliases, "Params001"), "plate_t")
        self.assertEqual(s("", self.aliases), "")

    def test_qualify(self):
        q = bpar.qualify
        self.assertEqual(q("plate_t * 1.75", self.aliases), "Parameters.plate_t * 1.75")
        self.assertEqual(q("plate_t + boss_h", self.aliases), "Parameters.plate_t + Parameters.boss_h")
        self.assertEqual(q("Parameters.plate_t", self.aliases), "Parameters.plate_t")
        self.assertEqual(q("Boss.plate_t", self.aliases), "Boss.plate_t")
        self.assertEqual(q("plate_tx + 2 mm", self.aliases), "plate_tx + 2 mm")
        self.assertEqual(q("plate_t(2)", self.aliases), "plate_t(2)")  # a function call
        self.assertEqual(q("<<Parameters>>.plate_t", self.aliases), "<<Parameters>>.plate_t")
        self.assertEqual(q("plate_t", self.aliases, "Params001"), "Params001.plate_t")

    def test_round_trip(self):
        shown = "plate_t * 1.75 + boss_h"
        self.assertEqual(bpar.short_expr(bpar.qualify(shown, self.aliases), self.aliases), shown)

    def test_uses_alias(self):
        u = bpar.uses_alias
        self.assertTrue(u("Parameters.plate_t * 2", "plate_t"))
        self.assertTrue(u("<<Parameters>>.plate_t", "plate_t"))
        self.assertFalse(u("Parameters.plate_tx", "plate_t"))
        self.assertFalse(u("plate_t * 2", "plate_t"))
        self.assertTrue(u("=plate_t * 2", "plate_t", bare=True))
        self.assertFalse(u("Boss.plate_t", "plate_t", bare=True))

    def test_expression_map(self):
        self.assertEqual(bpar.expression_map([(".Constraints.width", "80 mm"), ("Length", "plate_t")]),
                         {"Constraints.width": "80 mm", "Length": "plate_t"})
        self.assertEqual(bpar.expression_map(None), {})


class SheetTests(unittest.TestCase):
    def test_alias_names(self):
        self.assertTrue(bpar.valid_alias("plate_t"))
        self.assertTrue(bpar.valid_alias("_x1"))
        self.assertFalse(bpar.valid_alias("A1"))
        self.assertFalse(bpar.valid_alias("ab12"))
        self.assertFalse(bpar.valid_alias("1st"))
        self.assertFalse(bpar.valid_alias("plate t"))
        self.assertFalse(bpar.valid_alias(""))

    def test_cells(self):
        self.assertEqual(bpar.cell_rc("B12"), (12, 2))
        self.assertEqual(bpar.cell_rc("AA3"), (3, 27))
        self.assertEqual(bpar.cell_rc("x"), (0, 0))
        self.assertEqual(bpar.next_row(["A1", "B1", "C1", "B4", "A2"]), 5)
        self.assertEqual(bpar.next_row([]), 2)

    def test_unique_name(self):
        self.assertEqual(bpar.unique_name("param", []), "param")
        self.assertEqual(bpar.unique_name("param", ["param", "param1"]), "param2")


class ModelTests(unittest.TestCase):
    def test_constraint_rows(self):
        rows = bpar.constraint_rows([("Coincident", "", True), ("DistanceX", "width", True),
                                     ("DistanceY", "", True), ("Angle", "", False), ("Radius", "r", True),
                                     ("Horizontal", "", True), ("Diameter", "", True), ("Distance", "", True)])
        self.assertEqual(rows, [(1, "width", "Length", True), (2, "Vertical 3", "Length", True),
                                (3, "Angle 4", "Angle", False), (4, "r", "Length", True),
                                (6, "Diameter 7", "Length", True), (7, "Distance 8", "Length", True)])

    def test_constraint_path(self):
        self.assertEqual(bpar.constraint_path(6), "Constraints[6]")
        self.assertEqual(bpar.constraint_path(6, "width"), "Constraints.width")

    def test_dimension_props(self):
        pad = Obj("Pad", "PartDesign::Pad",
                  Length=(L, "Side1", ()), Length2=(L, "Side2", ("ReadOnly",)), TaperAngle=(A, "Side1", ()),
                  Occurrences=("App::PropertyIntegerConstraint", "Pattern", ()),
                  FuzzyTolerance=("App::PropertyFloatConstraint", "Part Design", ()),
                  _Private=(L, "Side1", ()), Hidden=(L, "Side1", ("Hidden",)),
                  Placement=("App::PropertyPlacement", "Base", ()), Refine=("App::PropertyBool", "Part Design", ()),
                  Gap=(L, "Base", ()))
        self.assertEqual(bpar.dimension_props(pad, {"Length": "Distance"}),
                         [("Length", "Distance", L), ("TaperAngle", "Taper angle", A),
                          ("Occurrences", "Occurrences", "App::PropertyIntegerConstraint")])

    def test_tree_order(self):
        pad, sketch = Obj("Pad"), Obj("Sketch")
        body = Obj("Body", "PartDesign::Body", group=[sketch, pad])
        sheet, box = Obj("Parameters"), Obj("Box")
        # The document lists the body's features in creation order, here after an unrelated box.
        order = bpar.ordered_objects([body, box, pad, sketch, sheet])
        self.assertEqual([o.Name for o in order], ["Body", "Sketch", "Pad", "Box", "Parameters"])

    def test_filter(self):
        self.assertTrue(bpar.matches("", "x"))
        self.assertTrue(bpar.matches("plate", "Base plate", ""))
        self.assertTrue(bpar.matches("PLATE_T", "Boss", "plate_t * 1.75"))
        self.assertFalse(bpar.matches("hole", "Boss", None))

    def test_fold_default(self):
        self.assertTrue(bpar.fold_default(10))
        self.assertFalse(bpar.fold_default(bpar.FOLD_LIMIT + 1))


class LengthPathTests(unittest.TestCase):
    def test_pad_and_sketch(self):
        class Pad:
            TypeId = "PartDesign::Pad"

            def isDerivedFrom(self, name):
                return False

        class Sketch:
            TypeId = "Sketcher::SketchObject"

            def isDerivedFrom(self, name):
                return name == "Sketcher::SketchObject"

        self.assertEqual(bpar.length_path(Pad()), "Length")
        self.assertEqual(bpar.length_path(Pad(), "Length2"), "Length2")
        self.assertEqual(bpar.length_path(Sketch(), "width"), "Constraints.width")
        self.assertEqual(bpar.length_path(Sketch(), 6), "Constraints[6]")
        self.assertEqual(bpar.length_path(Sketch(), "Constraints[2]"), "Constraints[2]")
        with self.assertRaises(ValueError):
            bpar.length_path(Sketch())
        with self.assertRaises(ValueError):
            bpar.length_path(Sketch(), "A1")


def _plate(doc, width=20.0, height=10.0, thick=8.0):
    """A plate on the XY plane: a rectangle sketch (constraint "width") padded to ``thick``."""
    import FreeCAD as App
    import Part
    import Sketcher

    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    plane = doc.getObject("XY_Plane")
    if plane is None:
        plane = next(feature for feature in body.Origin.OriginFeatures if "XY" in feature.Name)
    sketch.AttachmentSupport = (plane, [""])
    sketch.MapMode = "FlatFace"
    sketch.Label = "Plate"
    pts = [App.Vector(0, 0, 0), App.Vector(width, 0, 0), App.Vector(width, height, 0), App.Vector(0, height, 0)]
    for i in range(4):
        sketch.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    cons = []
    for i in range(4):
        cons.append(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    cons += [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
             Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3),
             Sketcher.Constraint("Coincident", 0, 1, -1, 1)]
    sketch.addConstraint(cons)
    width_index = sketch.addConstraint(Sketcher.Constraint("Distance", 0, float(width)))
    sketch.renameConstraint(width_index, "width")
    sketch.addConstraint(Sketcher.Constraint("Distance", 1, float(height)))
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Label = "Plate pad"
    pad.Profile = sketch
    pad.Length = thick
    doc.recompute()
    return sketch, pad, width_index


def _bound_expr(obj, path):
    for stored, expr in getattr(obj, "ExpressionEngine", None) or ():
        if stored.lstrip(".") == path:
            return expr
    return ""


class DriveTests(unittest.TestCase):
    """One alias on the root Parameters sheet drives a sketch distance and a pad length."""

    def test_changing_the_alias_moves_the_sketch_and_the_pad(self):
        import FreeCAD

        doc = FreeCAD.newDocument("BWBind")
        self.addCleanup(lambda: FreeCAD.closeDocument(doc.Name) if doc.Name in FreeCAD.listDocuments() else None)
        sketch, pad, width_index = _plate(doc)
        sheet = bpar.ensure_parameters(doc)
        self.assertEqual(sheet.Name, "Parameters")
        self.assertIsNone(sheet.getParentGeoFeatureGroup())
        self.assertNotIn(sheet, doc.getObject("Body").Group)
        self.assertIs(bpar.ensure_parameters(doc), sheet)
        names = [obj.Name for obj in doc.Objects if obj.TypeId == bpar.SHEET_TYPE]
        self.assertEqual(names, ["Parameters"])
        with self.assertRaises(ValueError):
            bpar.set_alias(sheet, "A1", "1 mm")

        bpar.set_alias(sheet, "width", "20 mm", used_in="plate sketch")
        bpar.set_alias(sheet, "thick", "8 mm")
        doc.recompute()
        self.assertEqual(bpar.bind_length(sketch, "width", "width"), "Constraints.width")
        self.assertEqual(bpar.bind_length(pad, "thick"), "Length")
        self.assertEqual(_bound_expr(sketch, "Constraints.width"), "Parameters.width")
        self.assertEqual(_bound_expr(pad, "Length"), "Parameters.thick")
        self.assertEqual(bpar.used_in(sheet, "width"), "plate sketch, Plate.width")
        self.assertEqual(bpar.used_in(sheet, "thick"), "Plate pad")
        self.assertEqual(_plain_header(sheet), "Used in")
        doc.recompute()

        bpar.set_alias(sheet, "width", "35 mm")
        bpar.set_alias(sheet, "thick", "14 mm")
        self.assertEqual(sheet.getCellFromAlias("width"), "B2")  # updated in place, no second row
        doc.recompute()
        self.assertAlmostEqual(sheet.get("width").Value, 35.0, places=4)
        self.assertAlmostEqual(sheet.get("thick").Value, 14.0, places=4)
        self.assertAlmostEqual(pad.Length.Value, 14.0, places=4)
        self.assertAlmostEqual(sketch.Geometry[0].length(), 35.0, delta=0.05)
        self.assertAlmostEqual(pad.Shape.BoundBox.XLength, 35.0, delta=0.05)
        self.assertAlmostEqual(pad.Shape.BoundBox.YLength, 10.0, delta=0.05)
        self.assertAlmostEqual(pad.Shape.BoundBox.ZLength, 14.0, delta=0.05)
        datum = sketch.getDatum(width_index)
        datum = datum.Value if hasattr(datum, "Value") else float(datum)
        self.assertAlmostEqual(datum, 35.0, delta=0.05)


def _plain_header(sheet):
    return bpar._plain(sheet.getContents("D1") or "")


class ProductSheetTests(unittest.TestCase):
    """One Parameters sheet for the product, in the root file, read by parts in other files."""

    def test_referenced_sheets(self):
        refs = bpar.referenced_sheets(["LE_02_01001_001#Parameters.face_width / 2", "Root#<<Parameters>>.t + 1",
                                       "Parameters.local", "x * 2"])
        self.assertEqual(refs, {("LE_02_01001_001", "Parameters"), ("Root", "<<Parameters>>")})

    def test_short_names_across_files(self):
        ref = "LE_02_01001_001#Parameters"
        self.assertEqual(bpar.short_expr(f"{ref}.face_width / 2", {"face_width"}, ref), "face_width / 2")
        self.assertEqual(bpar.qualify("face_width / 2", {"face_width"}, ref), f"{ref}.face_width / 2")

    def test_a_part_reads_the_root_sheet_and_is_rebuilt(self):
        import os
        import shutil
        import tempfile

        import FreeCAD

        tmp = tempfile.mkdtemp(prefix="bw-params-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = FreeCAD.newDocument("BWRoot")
        part = FreeCAD.newDocument("BWPart")
        self.addCleanup(lambda: [FreeCAD.closeDocument(n) for n in ("BWPart", "BWRoot") if n in FreeCAD.listDocuments()])
        sheet = bpar.make_sheet(root)
        sheet.set("A2", "plate_t")
        sheet.set("B2", "5 mm")
        sheet.setAlias("B2", "plate_t")
        root.recompute()
        root.saveAs(os.path.join(tmp, "BWRoot.FCStd"))
        box = part.addObject("Part::Box", "Box")
        part.saveAs(os.path.join(tmp, "BWPart.FCStd"))  # a link to another file needs its owner saved
        box.setExpression("Height", "BWRoot#Parameters.plate_t")
        part.recompute()
        self.assertIsNone(bpar.local_sheet(part))
        self.assertIs(bpar.find_sheet(part), sheet)
        self.assertEqual(bpar.sheet_ref(sheet, part), "BWRoot#Parameters")
        self.assertEqual(bpar.sheet_ref(sheet, root), "Parameters")
        self.assertEqual(bpar.dependents(sheet), [box])
        sheet.set("B2", "8 mm")
        root.recompute()
        self.assertEqual(bpar.update_dependents(sheet), ["BWPart"])
        self.assertAlmostEqual(box.Height.Value, 8.0)


if __name__ == "__main__":
    unittest.main()
