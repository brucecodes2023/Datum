# SPDX-License-Identifier: LGPL-2.1-or-later
"""Browser row extras: badges, component text and feature summaries. No GUI."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_browser as bb


class Q:
    """A quantity in mm or degrees."""

    def __init__(self, value):
        self.Value = value


class Obj:
    def __init__(self, type_id, label="", **props):
        self.TypeId = type_id
        self.Label = label
        self.PropertiesList = list(props)
        for name, value in props.items():
            setattr(self, name, value)


class Circle:
    pass


class Sketch:
    def __init__(self, geometry, construction=()):
        self.Geometry = geometry
        self._construction = set(construction)

    def getConstruction(self, index):
        return index in self._construction


class LifecycleTests(unittest.TestCase):
    def test_state_from_revision(self):
        self.assertEqual(bb.lifecycle_state("02"), bb.WIP)
        self.assertEqual(bb.lifecycle_state("A"), bb.RELEASED)
        self.assertIsNone(bb.lifecycle_state(""))
        self.assertIsNone(bb.lifecycle_state(None))

    def test_explicit_state_wins(self):
        self.assertEqual(bb.lifecycle_state("B", "wip"), bb.WIP)
        self.assertEqual(bb.lifecycle_state("03", "review"), bb.IN_REVIEW)
        self.assertEqual(bb.lifecycle_state("03", "In Review"), bb.IN_REVIEW)
        self.assertEqual(bb.lifecycle_state("C", "Obsolete"), bb.OBSOLETE)
        self.assertEqual(bb.lifecycle_state("01", "Frozen"), "Frozen")

    def test_badges(self):
        self.assertEqual(bb.part_badges("LE-01-01001", "02"), [("LE-01-01001", ""), ("WIP 02", "warn")])
        self.assertEqual(bb.part_badges("LE-01-01001", "A"), [("LE-01-01001", ""), ("REV A", "")])
        self.assertEqual(bb.part_badges("", "03", "In Review"), [("REVIEW 03", "accent")])
        self.assertEqual(bb.part_badges("P1", "B", "Obsolete"), [("P1", ""), ("OBSOLETE B", "dim")])
        self.assertEqual(bb.part_badges("P1", ""), [("P1", "")])
        self.assertEqual(bb.part_badges("", ""), [])

    def test_component_meta(self):
        self.assertEqual(bb.component_meta("LE-01-02001", "01"), "LE-01-02001 · 01")
        self.assertEqual(bb.component_meta("LE-01-02001", ""), "LE-01-02001")
        self.assertEqual(bb.component_meta("", ""), "")


class SummaryTests(unittest.TestCase):
    def test_number(self):
        self.assertEqual(bb.number(8.0), "8")
        self.assertEqual(bb.number(52.5), "52.5")
        self.assertEqual(bb.number(0.1 + 0.2), "0.3")
        self.assertEqual(bb.number(-0.0001), "0")

    def test_pad_names_its_type_only_when_renamed(self):
        pad = Obj("PartDesign::Pad", "Base plate", Type="Length", Length=Q(8))
        self.assertEqual(bb.feature_summary(pad), "Pad 8 mm")
        pad.Label = "Pad001"
        self.assertEqual(bb.feature_summary(pad), "8 mm")

    def test_pad_modes(self):
        pad = Obj("PartDesign::Pocket", "Slot", Type="ThroughAll", Length=Q(8))
        self.assertEqual(bb.feature_summary(pad), "Pocket through all")
        pad = Obj("PartDesign::Pad", "Rib", Type="TwoLengths", Length=Q(8), Length2=Q(2))
        self.assertEqual(bb.feature_summary(pad), "Pad 8 mm + 2 mm")

    def test_symbols(self):
        self.assertEqual(bb.feature_summary(Obj("PartDesign::Fillet", "Corner fillet", Radius=Q(5))), "R5")
        self.assertEqual(bb.feature_summary(Obj("PartDesign::Chamfer", "Edge", Size=Q(1))), "C1")
        sketch = Sketch([Circle(), Circle(), Circle(), Circle(), Circle()], construction={4})
        hole = Obj("PartDesign::Hole", "Mounting holes", Diameter=Q(9), Profile=(sketch, ["Edge1"]))
        self.assertEqual(bb.feature_summary(hole), "Ø9 × 4")
        hole.Profile = (Sketch([Circle()]), [])
        self.assertEqual(bb.feature_summary(hole), "Ø9")

    def test_patterns_and_revolve(self):
        pat = Obj("PartDesign::LinearPattern", "Bolt row", Occurrences=6)
        self.assertEqual(bb.feature_summary(pat), "Pattern × 6")
        rev = Obj("PartDesign::Revolution", "Revolution", Angle=Q(360))
        self.assertEqual(bb.feature_summary(rev), "Revolve 360°")

    def test_sketch_says_sketch_only_when_renamed(self):
        self.assertEqual(bb.feature_summary(Obj("Sketcher::SketchObject", "Base outline")), "Sketch")
        self.assertEqual(bb.feature_summary(Obj("Sketcher::SketchObject", "Sketch001")), "")

    def test_plain_objects_get_nothing(self):
        self.assertEqual(bb.feature_summary(Obj("PartDesign::Body", "Body")), "")
        self.assertEqual(bb.feature_summary(Obj("App::Part", "Bracket")), "")

    def test_joint_limits(self):
        joint = Obj("App::FeaturePython", "Hinge", JointType="Revolute", EnableAngleMin=True,
                    EnableAngleMax=True, AngleMin=Q(0), AngleMax=Q(110))
        self.assertEqual(bb.feature_summary(joint), "Revolute 0°–110°")
        joint.EnableAngleMin = joint.EnableAngleMax = False
        self.assertEqual(bb.feature_summary(joint), "Revolute")


class RowInfoTests(unittest.TestCase):
    def test_part_gets_badges(self):
        part = Obj("App::Part", "Bracket", PartNumber="LE-01-01001", Revision="02")
        self.assertEqual(bb.row_info(part), ("", [("LE-01-01001", ""), ("WIP 02", "warn")]))

    def test_state_property(self):
        part = Obj("App::Part", "Bracket", PartNumber="X", Revision="A", State="WIP")
        self.assertEqual(bb.row_info(part)[1], [("X", ""), ("WIP A", "warn")])

    def test_part_without_values_gets_nothing(self):
        part = Obj("App::Part", "Bracket", PartNumber="", Revision="")
        self.assertEqual(bb.row_info(part), ("", []))

    def test_link_gets_quiet_text(self):
        part = Obj("App::Part", "Frame", PartNumber="LE-01-02001", Revision="01")
        link = Obj("App::Link", "Frame")
        link.getLinkedObject = lambda recursive=True: part
        self.assertEqual(bb.row_info(link), ("LE-01-02001 · 01", []))

    def test_switches(self):
        part = Obj("App::Part", "Bracket", PartNumber="P", Revision="01")
        self.assertEqual(bb.row_info(part, badges=False), ("", []))
        pad = Obj("PartDesign::Pad", "Base", Type="Length", Length=Q(8))
        self.assertEqual(bb.row_info(pad, summary=False), ("", []))
        self.assertEqual(bb.row_info(pad), ("Pad 8 mm", []))

    def test_hook_never_raises(self):
        class Broken:
            TypeId = "PartDesign::Pad"
            Label = "x"
            PropertiesList = []

            @property
            def Type(self):
                raise RuntimeError("deleted")

        self.assertEqual(bb._Hook(True, True)(Broken()), ("", ()))


if __name__ == "__main__":
    unittest.main()
