# SPDX-License-Identifier: LGPL-2.1-or-later
"""Parameters tab: edits, expressions with user parameters, sheet cells, which dimensions. No GUI."""

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


if __name__ == "__main__":
    unittest.main()
