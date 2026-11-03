# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless tests for the selection filter (bruceworx_selectfilter): modes, the gate's decisions, the
pick rewrite rules and the preference round trip."""

import unittest

import bruceworx_selectfilter as sf


class FakeParam:
    def __init__(self):
        self.values = {}

    def GetString(self, name, default=""):
        return self.values.get(name, default)

    def SetString(self, name, value):
        self.values[name] = value


class TestModes(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(sf.parse_mode("Face"), sf.FACE)
        self.assertEqual(sf.parse_mode(" edge "), sf.EDGE)
        self.assertEqual(sf.parse_mode("VERTEX"), sf.VERTEX)
        for bad in ("", None, "Solid", 7):
            self.assertEqual(sf.parse_mode(bad), sf.AUTO)

    def test_preference_round_trip(self):
        param = FakeParam()
        self.assertEqual(sf.get_mode(param), sf.AUTO)  # nothing saved: today's behaviour
        for mode in sf.MODES:
            self.assertEqual(sf.set_mode(param, mode), mode)
            self.assertEqual(param.values[sf.PREF_NAME], mode)
            self.assertEqual(sf.get_mode(param), mode)
        self.assertEqual(sf.set_mode(param, "nonsense"), sf.AUTO)
        param.values[sf.PREF_NAME] = "garbage"
        self.assertEqual(sf.get_mode(param), sf.AUTO)


class TestGate(unittest.TestCase):
    SUBS = ("Face12", "Edge3", "Vertex1", "", "Body.Pad.", "Body.Pad.Face12", None)

    def decisions(self, mode):
        gate = sf.Gate(mode)
        return [gate.allow(None, None, sub) for sub in self.SUBS]

    def test_each_element_mode_lets_only_its_kind_through(self):
        # whole objects ("", "Body.Pad.", None: tree clicks) always pass, as in SolidWorks
        self.assertEqual(self.decisions(sf.FACE), [True, False, False, True, True, True, True])
        self.assertEqual(self.decisions(sf.EDGE), [False, True, False, True, True, False, True])
        self.assertEqual(self.decisions(sf.VERTEX), [False, False, True, True, True, False, True])

    def test_other_modes_let_everything_through(self):
        for mode in (sf.AUTO, sf.COMPONENT, sf.BODY):
            self.assertTrue(all(self.decisions(mode)), mode)

    def test_element_type(self):
        self.assertEqual(sf.element_type("Face12"), "Face")
        self.assertEqual(sf.element_type("A.B.Edge3"), "Edge")
        self.assertEqual(sf.element_type("Face"), "")
        self.assertEqual(sf.element_type("Faces1"), "")
        self.assertEqual(sf.element_type(None), "")


class TestRewrite(unittest.TestCase):
    def test_who_rewrites_picks(self):
        self.assertTrue(sf.rewrite_active(sf.AUTO, True))
        self.assertFalse(sf.rewrite_active(sf.AUTO, False))  # AssemblyPick pref off
        self.assertTrue(sf.rewrite_active(sf.COMPONENT, False))  # the rule, even with the pref off
        self.assertTrue(sf.rewrite_active(sf.BODY, False))
        for mode in sf.ELEMENT_MODES:  # the gate's modes: the component rule must not replace the face
            self.assertFalse(sf.rewrite_active(mode, True), mode)

    def test_auto_is_the_assembly_pick(self):
        self.assertEqual(sf.retarget(sf.AUTO, "Door.Panel.Body.Face3"), "Door.")
        self.assertEqual(sf.retarget(sf.AUTO, "Door.Panel.Body.Face3", "Door."), "Door.Panel.")

    def test_component_is_always_the_top(self):
        self.assertEqual(sf.retarget(sf.COMPONENT, "Door.Panel.Body.Face3"), "Door.")
        self.assertEqual(sf.retarget(sf.COMPONENT, "Bolt.Edge2"), "Bolt.")
        self.assertIsNone(sf.retarget(sf.COMPONENT, "Door.Panel."))  # a tree click

    def test_body_selects_the_whole_solid(self):
        types = ["Assembly::AssemblyObject", "App::Link", "PartDesign::Body", "PartDesign::Pad"]
        self.assertEqual(sf.retarget(sf.BODY, "Link.Body.Pad.Face3", types=types), "Link.Body.")
        # A pick on a body in a plain document: the root is the body, no subname.
        self.assertEqual(sf.retarget(sf.BODY, "Pad.Face3", types=["PartDesign::Body", "PartDesign::Pad"]), "")
        self.assertEqual(sf.retarget(sf.BODY, "Face3", types=["Part::Box"]), "")
        self.assertIsNone(sf.retarget(sf.BODY, "Body.", types=["PartDesign::Body"]))

    def test_body_without_a_body_takes_the_deepest_object(self):
        types = ["Assembly::AssemblyObject", "App::Link", "Part::Box"]
        self.assertEqual(sf.retarget(sf.BODY, "Link.Box.Face1", types=types), "Link.Box.")

    def test_mapped_element_names_are_not_objects(self):
        # an imported solid's pick carries the mapped name (starts with ";", may hold dots) before the element
        sub = "Link010.Body.;Face45;:M;CMN;:Hfac:7,F;:Hfac:5,F.Face107"
        types = ["Assembly::AssemblyObject", "App::Link", "PartDesign::Body"]
        self.assertEqual(sf.retarget(sf.BODY, sub, types=types), "Link010.Body.")
        self.assertEqual(sf.retarget(sf.COMPONENT, sub), "Link010.")
        self.assertIsNone(sf.retarget(sf.AUTO, sub, "Link010.Body."))  # inside the deepest object: the face
        self.assertEqual(sf.retarget(sf.BODY, "Link.Body.;#5:1;:H1,F.Face3", types=types), "Link.Body.")

    def test_element_modes_never_retarget(self):
        for mode in sf.ELEMENT_MODES:
            self.assertIsNone(sf.retarget(mode, "Door.Panel.Body.Face3"))


if __name__ == "__main__":
    unittest.main()
