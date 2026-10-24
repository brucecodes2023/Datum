# SPDX-License-Identifier: LGPL-2.1-or-later
"""Property inspector: which property goes where, labels, multi-selection. No GUI."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_inspector as bi


class Obj:
    """A document object as the inspector reads it: name -> (type, group, editor mode, value[, choices])."""

    def __init__(self, type_id, label="", **props):
        self.TypeId = type_id
        self.Label = label or type_id.split("::")[-1]
        self.Name = self.Label
        self.Document = None
        self._props = props
        self.PropertiesList = list(props)
        for name, spec in props.items():
            setattr(self, name, spec[3])

    def getTypeIdOfProperty(self, name):
        return self._props[name][0]

    def getGroupOfProperty(self, name):
        return self._props[name][1]

    def getEditorMode(self, name):
        return list(self._props[name][2])

    def getPropertyStatus(self, name):
        return ["Hidden"] if name.startswith("Shape") else []

    def getEnumerationsOfProperty(self, name):
        return self._props[name][4]

    def getDocumentationOfProperty(self, name):
        return "Doc of " + name


L, A, E, B = "App::PropertyLength", "App::PropertyAngle", "App::PropertyEnumeration", "App::PropertyBool"
TYPES = ["Length", "UpToLast", "UpToFirst", "UpToFace", "?TwoLengths", "UpToShape"]


def pad(**extra):
    props = dict(
        Length=(L, "Side1", (), 8.0),
        Length2=(L, "Side2", ("ReadOnly",), 0.0),
        Offset=(L, "Side1", ("ReadOnly",), 0.0),
        TaperAngle=(A, "Side1", (), 0.0),
        Type=(E, "Side1", (), "Length", TYPES),
        SideType=(E, "Pad", (), "One side", ["One side", "Two sides", "Symmetric"]),
        Reversed=(B, "SketchBased", (), False),
        Refine=(B, "Part Design", (), False),
        FuzzyTolerance=("App::PropertyFloatConstraint", "Part Design", (), 1e-7),
        AllowMultiFace=(B, "SketchBased", (), False),
        Profile=("App::PropertyLinkSub", "SketchBased", (), None),
        Suppressed=(B, "Base", (), False),
        Shape=("Part::PropertyPartShape", "", (), None),
    )
    props.update(extra)
    return Obj("PartDesign::Pad", "Boss", **props)


def names(rows):
    return [(r.group, r.name, r.part, r.label) if r.part else (r.group, r.name, r.label) for r in rows]


class LabelTests(unittest.TestCase):
    def test_friendly(self):
        self.assertEqual(bi.friendly("TaperAngle"), "Taper angle")
        self.assertEqual(bi.friendly("UseAllEdges"), "Use all edges")
        self.assertEqual(bi.friendly("Length2"), "Length 2")
        self.assertEqual(bi.friendly("PartNumber"), "Part number")
        self.assertEqual(bi.friendly("BOMNote"), "BOM note")
        self.assertEqual(bi.friendly(""), "")

    def test_choice_words(self):
        self.assertEqual(bi.choice_text("UpToFirst"), "To first")
        self.assertEqual(bi.choice_text("ThroughAll"), "Through all")
        self.assertEqual(bi.choice_text("Length"), "Distance")
        self.assertEqual(bi.choice_text("One side"), "One side")
        self.assertEqual(bi.choice_text("ObjectXY"), "Object XY")

    def test_retired_choices_hidden(self):
        self.assertEqual(bi.visible_choices(TYPES), ["Length", "UpToLast", "UpToFirst", "UpToFace", "UpToShape"])

    def test_segmented_up_to_three(self):
        self.assertTrue(bi.segmented(["A", "B"]))
        self.assertTrue(bi.segmented(["Flat", "Angled"]))
        self.assertTrue(bi.segmented(["Extent", "Spacing"]))
        self.assertFalse(bi.segmented(["One side", "Two sides", "Symmetric"]))  # too long for the Browser
        self.assertFalse(bi.segmented(["a", "b", "c", "d"]))
        self.assertFalse(bi.segmented([]))


class RuleTests(unittest.TestCase):
    def test_rules(self):
        c = bi.classify
        self.assertEqual(c("PartNumber", "App::PropertyString", "BruceWorx"), bi.PART_DATA)
        self.assertEqual(c("Mass", "App::PropertyString", "BruceWorx", ("ReadOnly",)), bi.PART_DATA)
        self.assertEqual(c("Placement", "App::PropertyPlacement", ""), bi.POSITION)
        self.assertEqual(c("MapMode", E, "Attachment"), bi.POSITION)
        self.assertEqual(c("Height", L, "Box"), bi.SIZE)
        self.assertEqual(c("Occurrences", "App::PropertyIntegerConstraint", "Direction 1"), bi.SIZE)
        self.assertIsNone(c("FuzzyTolerance", "App::PropertyFloatConstraint", "Part Design"))
        self.assertEqual(c("Mode", E, "Thickness"), bi.OPTIONS)
        self.assertIsNone(c("AttacherEngine", E, "Attachment"))
        self.assertIsNone(c("AllowMultiFace", B, "SketchBased"))
        self.assertEqual(c("Refine", B, "Part Design"), bi.FEATURE)
        self.assertEqual(c("Profile", "App::PropertyLinkSub", "SketchBased"), bi.FEATURE)
        self.assertIsNone(c("Length2", L, "Side2", ("ReadOnly",)))
        self.assertIsNone(c("Label2", "App::PropertyString", "Base"))

    def test_hidden(self):
        self.assertTrue(bi.hidden(("Hidden",), (), "X"))
        self.assertTrue(bi.hidden((), ("Hidden",), "X"))
        self.assertTrue(bi.hidden((), (), "_private"))
        self.assertFalse(bi.hidden((), (), "Length"))


class PlanRowTests(unittest.TestCase):
    def test_pad_uses_the_curated_table(self):
        rows = bi.plan_rows(pad())
        self.assertEqual(names(rows), [
            (bi.SIZE, "Length", "Distance"),
            (bi.SIZE, "TaperAngle", "Taper"),
            (bi.OPTIONS, "Type", "Extent"),
            (bi.OPTIONS, "SideType", "Direction"),
            (bi.OPTIONS, "Reversed", "Flip"),
            (bi.FEATURE, "Profile", "Sketch"),
            (bi.FEATURE, "Refine", "Refine"),
        ])
        extent = rows[2]
        self.assertEqual(extent.kind, bi.CHOICE)
        self.assertNotIn("?TwoLengths", extent.choices)
        self.assertEqual(rows[0].tip, "Length: Doc of Length")

    def test_active_option_appears_with_its_mode(self):
        rows = bi.plan_rows(pad(Length2=(L, "Side2", (), 4.0)))
        self.assertIn((bi.SIZE, "Length2", "Distance 2"), names(rows))

    def test_unknown_type_follows_the_rules(self):
        box = Obj("Part::Box", Length=(L, "Box", (), 10.0), Width=(L, "Box", (), 10.0),
                  Placement=("App::PropertyPlacement", "", (), None),
                  AttacherEngine=(E, "Attachment", (), "Engine 3D", ["Engine 3D", "Engine Plane"]),
                  Label=("App::PropertyString", "Base", (), "Box"))
        rows = bi.plan_rows(box)
        self.assertEqual([(r.group, r.name, r.part) for r in rows],
                         [(bi.SIZE, "Length", ""), (bi.SIZE, "Width", "")] +
                         [(bi.POSITION, "Placement", p) for p, _l, _u in bi.PLACEMENT_PARTS])
        self.assertEqual([r.label for r in rows[2:]], ["X", "Y", "Z", "Rotation Z", "Rotation Y", "Rotation X"])

    def test_part_data_in_schema_order_and_read_only(self):
        part = Obj("App::Part", "Plate",
                   Placement=("App::PropertyPlacement", "", (), None),
                   Revision=("App::PropertyString", "BruceWorx", (), "02"),
                   Mass=("App::PropertyString", "BruceWorx", ("ReadOnly",), "0.1 kg"),
                   PartNumber=("App::PropertyString", "BruceWorx", (), "LE-01-01001"),
                   Group=("App::PropertyLinkList", "Base", (), []))
        rows = bi.plan_rows(part, part_order=["PartNumber", "Revision", "Mass"])
        data = [r for r in rows if r.group == bi.PART_DATA]
        self.assertEqual([r.name for r in data], ["PartNumber", "Revision", "Mass"])
        self.assertEqual([r.label for r in data], ["Part number", "Revision", "Mass"])
        self.assertEqual(data[2].kind, bi.READONLY)
        self.assertEqual(data[0].kind, bi.TEXT)
        self.assertEqual(rows[0].group, bi.POSITION)
        self.assertFalse(any(r.name == "Group" for r in rows))

    def test_body_position_is_editable(self):
        body = Obj("PartDesign::Body", Placement=("App::PropertyPlacement", "", (), None))
        rows = bi.plan_rows(body)
        self.assertEqual(len(rows), 6)
        self.assertFalse(any(r.readonly for r in rows))

    def test_single_choice_is_left_out(self):
        hole = Obj("PartDesign::Hole", ThreadSize=(E, "Hole", (), "---", ["---"]),
                   Diameter=(L, "Hole", (), 9.0))
        self.assertEqual([r.name for r in bi.plan_rows(hole)], ["Diameter"])


class MultiTests(unittest.TestCase):
    def test_common_rows(self):
        a = bi.plan_rows(pad())
        b = bi.plan_rows(pad(Length2=(L, "Side2", (), 4.0)))
        self.assertEqual([r.name for r in bi.common_rows([a, b])], [r.name for r in a])
        self.assertEqual(bi.common_rows([]), [])

    def test_merged(self):
        self.assertEqual(bi.merged([3, 3]), (3, False))
        self.assertEqual(bi.merged([3, 4]), (3, True))
        self.assertEqual(bi.merged([]), (None, False))

    def test_selection_kind(self):
        p, f = Obj("PartDesign::Pad"), Obj("PartDesign::Fillet")
        self.assertEqual(bi.selection_kind([]), "none")
        self.assertEqual(bi.selection_kind([(p, "")]), "object")
        self.assertEqual(bi.selection_kind([(p, ""), (pad(), "")]), "objects")
        self.assertEqual(bi.selection_kind([(p, ""), (f, "")]), "mixed")
        self.assertEqual(bi.selection_kind([(p, "Face3")]), "element")
        self.assertEqual(bi.selection_kind([(p, "Face3"), (p, "Face4")]), "elements")

    def test_transaction_name(self):
        self.assertEqual(bi.transaction_name(["Boss"], "Distance"), "Boss: Distance")
        self.assertEqual(bi.transaction_name(["Fillet", "Fillet001"], "Radius"), "2 objects: Radius")


class KeyRowTests(unittest.TestCase):
    def test_pad_key_values_in_order(self):
        rows = bi.plan_rows(pad())
        key, more = bi.split_key_rows("PartDesign::Pad", rows)
        self.assertEqual([r.label for r in key], ["Distance", "Direction", "Extent"])
        self.assertEqual([r.label for r in more], ["Taper", "Flip", "Sketch", "Refine"])

    def test_unknown_type_shows_three_size_or_option_rows(self):
        box = Obj("Part::Box", Length=(L, "Box", (), 10.0), Width=(L, "Box", (), 10.0),
                  Height=(L, "Box", (), 10.0), Placement=("App::PropertyPlacement", "", (), None),
                  Refine=(B, "Part Design", (), False))
        key, more = bi.split_key_rows("Part::Box", bi.plan_rows(box))
        self.assertEqual([r.name for r in key], ["Length", "Width", "Height"])
        self.assertTrue(all(r.part for r in more if r.name == "Placement"))

    def test_body_position_goes_under_more(self):
        body = Obj("PartDesign::Body", Placement=("App::PropertyPlacement", "", (), None))
        key, more = bi.split_key_rows("PartDesign::Body", bi.plan_rows(body))
        self.assertEqual(key, [])
        self.assertEqual(len(more), 6)


class SmallTests(unittest.TestCase):
    def test_groups_in_order(self):
        rows = [bi.Row(bi.FEATURE, "Refine", "Refine", bi.SWITCH), bi.Row(bi.SIZE, "Length", "Distance", bi.QUANTITY)]
        self.assertEqual([g for g, _m in bi.group_rows(rows)], [bi.SIZE, bi.FEATURE])
        self.assertEqual(bi.caption(bi.PART_DATA), "Part data")
        self.assertEqual(bi.caption(bi.SIZE), "Size")

    def test_elements(self):
        self.assertEqual(bi.split_element("Face14"), ("Face", 14))
        self.assertEqual(bi.split_element("Body"), ("", 0))
        self.assertEqual(bi.element_words("Face", "Plane"), ("Face", "Flat"))
        self.assertEqual(bi.element_words("Edge", curve="Circle"), ("Edge", "Circle"))

    def test_opacity(self):
        self.assertEqual(bi.opacity(0), 100)
        self.assertEqual(bi.opacity(30), 70)
        self.assertEqual(bi.opacity(None), 100)

    def test_links(self):
        sketch, hole = Obj("Sketcher::SketchObject", "Sketch"), Obj("PartDesign::Hole", "Hole")
        self.assertEqual(bi.link_targets((sketch, [""])), [(sketch, [])])
        self.assertEqual(bi.link_targets((hole, ["Edge1", "Edge2"])), [(hole, ["Edge1", "Edge2"])])
        self.assertEqual(bi.link_targets([sketch, hole]), [(sketch, []), (hole, [])])
        self.assertEqual(bi.link_targets([(hole, ("Face1",)), (sketch, "Edge2")]),
                         [(hole, ["Face1"]), (sketch, ["Edge2"])])
        self.assertEqual(bi.link_targets(None), [])
        self.assertEqual(bi.link_text(hole, ["Edge1", "Edge2", "Edge3", "Edge4"]), "Hole · 4 edges")
        self.assertEqual(bi.link_text(hole, ["Face2"]), "Hole · Face2")
        self.assertEqual(bi.link_text(sketch, []), "Sketch")

    def test_location(self):
        self.assertEqual(bi.location_text("Pad", "Body"), "Pad · in Body")
        self.assertEqual(bi.location_text("Part"), "Part")


if __name__ == "__main__":
    unittest.main()
