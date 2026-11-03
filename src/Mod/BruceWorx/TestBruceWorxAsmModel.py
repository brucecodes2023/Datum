# SPDX-License-Identifier: LGPL-2.1-or-later
"""The assembly Model Builder's model layer (ASM-UI-01), headless."""

import unittest

import FreeCAD as App

import JointObject
import bruceworx_asm_model as am

V = App.Vector


def face_plane(obj, name):
    """(point, unit normal) of a planar face; a Part shape already holds global coordinates."""
    f = obj.Shape.getElement(name)
    return f.CenterOfMass, f.normalAt(0, 0)


class TestBruceWorxAsmModel(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWAsmModel")
        self.doc.UndoMode = 1
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jg = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.a = self._box("A", (0, 0, 0))
        self.b = self._box("B", (30, 5, 0))
        g = self.jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, self.a)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument("BWAsmModel")

    def _box(self, name, pos):
        b = self.asm.newObject("Part::Box", name)
        b.Placement = App.Placement(V(*pos), App.Rotation())
        self.doc.recompute()
        return b

    def _cyl(self, name, pos, grounded=False):
        c = self.asm.newObject("Part::Cylinder", name)
        c.Radius, c.Height = 5, 20
        c.Placement = App.Placement(V(*pos), App.Rotation())
        if grounded:
            g = self.jg.newObject("App::FeaturePython", "Ground" + name)
            JointObject.GroundedJoint(g, c)
        self.doc.recompute()
        return c

    def _ref(self, obj, el):
        return [obj, [el, el]]

    def _coincident(self):
        return am.create_mate(self.asm, self._ref(self.a, "Face2"), self._ref(self.b, "Face1"))

    # ----- tables ------------------------------------------------------------

    def test_tables(self):
        keys = [m[0] for m in am.MATES]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertIn(am.joint_type_of(key), JointObject.JointTypes)
            self.assertTrue(am.mate_label(key))
        self.assertEqual(am.joint_type_of("concentric"), "Cylindrical")
        self.assertEqual(am.joint_type_of("coincident"), "Distance")
        self.assertEqual(am.mate_label("rack"), "Rack and pinion")

    def test_fitting(self):
        self.assertEqual(am.fitting("plane", "plane"),
                         ["coincident", "distance", "parallel", "perpendicular", "angle", "lock"])
        self.assertEqual(am.fitting("cylinder", "circle")[:4], ["concentric", "hinge", "distance", "lock"])
        self.assertEqual(am.fitting("circle", "cylinder"), am.fitting("cylinder", "circle"))
        for extra in ("gear", "belt", "cylindrical", "slider"):
            self.assertIn(extra, am.fitting("cylinder", "cylinder"))
        self.assertEqual(am.fitting("plane", "cylinder"), ["tangent", "distance", "parallel", "perpendicular"])
        self.assertEqual(am.fitting("cylinder", "plane"), am.fitting("plane", "cylinder"))
        self.assertEqual(am.fitting("line", "line"), ["coincident", "parallel", "perpendicular", "angle", "distance"])
        self.assertEqual(am.fitting("plane", "line"), ["coincident", "parallel", "perpendicular", "distance"])
        self.assertEqual(am.fitting("point", "plane"), ["coincident", "distance"])
        self.assertEqual(am.fitting("sphere", "cone"), ["coincident", "distance", "lock"])

    # ----- element_kind ------------------------------------------------------

    def test_element_kind(self):
        cyl = self._cyl("Cyl", (100, 0, 0))
        self.assertEqual(am.element_kind(self.a, "Face1"), "plane")
        self.assertEqual(am.element_kind(cyl, "Face1"), "cylinder")
        self.assertEqual(am.element_kind(cyl, "Face2"), "plane")
        self.assertEqual(am.element_kind(self.a, "Edge1"), "line")
        circles = [e for e in ("Edge1", "Edge2", "Edge3")
                   if cyl.Shape.getElement(e).Curve.__class__.__name__ == "Circle"]
        self.assertTrue(circles)
        self.assertEqual(am.element_kind(cyl, circles[0]), "circle")
        self.assertEqual(am.element_kind(self.a, "Vertex1"), "point")
        sph = self.asm.newObject("Part::Sphere", "Sph")
        self.doc.recompute()
        self.assertEqual(am.element_kind(sph, "Face1"), "sphere")
        self.assertEqual(am.element_kind(self.a, "Face99"), "other")

    def test_pick_ref(self):
        ref = am.pick_ref(self.asm, self.doc.Name, self.asm.Name, "B.Face3")
        self.assertEqual(ref[0], self.b)
        self.assertEqual(ref[1], ["Face3", "Face3"])
        self.assertEqual(am.ref_label(ref), "B · Face3")
        self.assertIsNone(am.pick_ref(self.asm, self.doc.Name, self.a.Name, "Face1") and None)
        outside = self.doc.addObject("Part::Box", "Outside")
        self.assertIsNone(am.pick_ref(self.asm, self.doc.Name, outside.Name, "Face1"))

    # ----- create / solve ----------------------------------------------------

    def test_create_coincident(self):
        j = self._coincident()
        self.assertEqual(j.Label, "Coincident 1")
        self.assertEqual(j.JointType, "Distance")
        self.assertEqual(am.mate_key(j), "coincident")
        self.assertEqual(getattr(j, am.MATE_PROP), "coincident")
        self.assertIn(j, self.jg.Group)
        self.assertAlmostEqual(self.b.Placement.Base.x, 10, places=4)
        pa, na = face_plane(self.a, "Face2")
        pb, nb = face_plane(self.b, "Face1")
        self.assertAlmostEqual(abs((pb - pa).dot(na)), 0, places=4)
        self.assertAlmostEqual(abs(na.dot(nb)), 1, places=4)
        # a second coincident is numbered 2
        c = self._box("C", (60, 0, 0))
        j2 = am.create_mate(self.asm, self._ref(self.b, "Face2"), self._ref(c, "Face1"))
        self.assertEqual(j2.Label, "Coincident 2")
        self.assertAlmostEqual(c.Placement.Base.x, 20, places=4)

    def test_create_concentric(self):
        ca = self._cyl("CA", (0, 0, 0), grounded=True)
        cb = self._cyl("CB", (30, 12, 0))
        key = am.fitting(am.element_kind(ca, "Face1"), am.element_kind(cb, "Face1"))[0]
        self.assertEqual(key, "concentric")
        j = am.create_mate(self.asm, self._ref(ca, "Face1"), self._ref(cb, "Face1"))
        self.assertEqual(j.JointType, "Cylindrical")
        self.assertEqual(j.Label, "Concentric 1")
        self.assertEqual(am.mate_key(j), "concentric")
        self.assertAlmostEqual(cb.Placement.Base.x, 0, places=4)
        self.assertAlmostEqual(cb.Placement.Base.y, 0, places=4)
        axis = cb.Placement.Rotation.multVec(V(0, 0, 1))
        self.assertAlmostEqual(abs(axis.z), 1, places=4)

    def test_tangent_plane_cylinder(self):
        cyl = self._cyl("Cyl", (40, 40, 0))
        j = am.create_mate(self.asm, self._ref(self.a, "Face2"), self._ref(cyl, "Face1"))
        self.assertEqual(am.mate_key(j), "tangent")
        self.assertEqual(j.JointType, "Distance")
        self.assertAlmostEqual(cyl.Placement.Base.x - 10, 5, places=3)  # the axis sits a radius off the face

    # ----- editing -----------------------------------------------------------

    def test_set_mate_type_distance(self):
        j = self._coincident()
        am.set_mate_type(j, "distance")
        am.set_value(j, "distance", 5)
        self.assertEqual(am.mate_key(j), "distance")
        self.assertEqual(j.Label, "Distance 1")
        self.assertAlmostEqual(abs(self.b.Placement.Base.x - 10), 5, places=4)
        am.set_mate_type(j, "coincident")
        self.assertEqual(j.Distance.Value, 0)
        self.assertEqual(j.Label, "Coincident 1")
        self.assertAlmostEqual(self.b.Placement.Base.x, 10, places=4)

    def test_set_mate_type_parallel_and_label(self):
        j = self._coincident()
        am.set_value(j, "label", "My mate")
        am.set_mate_type(j, "parallel")
        self.assertEqual(j.Label, "My mate")
        self.assertEqual(j.JointType, "Parallel")
        self.assertEqual(am.mate_key(j), "parallel")

    def test_flip(self):
        j = self._coincident()
        r0 = self.b.Placement.Rotation.multVec(V(1, 0, 0))
        am.set_value(j, "flip")
        r1 = self.b.Placement.Rotation.multVec(V(1, 0, 0))
        self.assertAlmostEqual(r0.dot(r1), -1, places=4)

    def test_limits_on_slider(self):
        rail = self._box("Rail", (0, 40, 0))
        rail.Length = 100
        self.doc.recompute()
        car = self._box("Car", (20, 40, 0))
        j = am.create_mate(self.asm, self._ref(rail, "Face2"), self._ref(car, "Face2"), key="slider")
        self.assertEqual(j.JointType, "Slider")
        am.set_value(j, "limit_enabled_min", True)
        am.set_value(j, "limit_min", -5)
        am.set_value(j, "limit_enabled_max", True)
        am.set_value(j, "limit_max", 12.5)
        self.assertTrue(j.EnableLengthMin and j.EnableLengthMax)
        self.assertAlmostEqual(j.LengthMin.Value, -5)
        self.assertAlmostEqual(j.LengthMax.Value, 12.5)
        self.assertFalse(j.EnableAngleMin)
        sec = {s.title: s for s in am.settings(j)}["Limits"]
        self.assertTrue(sec.open)
        self.assertEqual([f.unit for f in sec.fields if f.kind == "float"], ["mm", "mm"])

    def test_limits_on_hinge_use_angle(self):
        ca = self._cyl("CA", (0, 0, 0), grounded=True)
        cb = self._cyl("CB", (30, 0, 0))
        j = am.create_mate(self.asm, self._ref(ca, "Face1"), self._ref(cb, "Face1"), key="hinge")
        am.set_value(j, "limit_enabled_max", True)
        am.set_value(j, "limit_max", 90)
        self.assertTrue(j.EnableAngleMax)
        self.assertFalse(j.EnableLengthMax)
        self.assertAlmostEqual(j.AngleMax.Value, 90)
        with self.assertRaises(ValueError):
            am.set_value(j, "nonsense", 1)

    def test_replace_ref_and_delete(self):
        c = self._box("C", (60, 0, 0))
        j = self._coincident()
        am.replace_ref(j, 1, self._ref(c, "Face1"))
        self.assertEqual(j.Reference2[0], c)
        self.assertAlmostEqual(c.Placement.Base.x, 10, places=4)
        am.delete_mate(j)
        self.assertIsNone(self.doc.getObject("Joint"))

    def test_undo_type_change(self):
        j = self._coincident()
        am.set_mate_type(j, "parallel")
        self.doc.undo()
        self.assertEqual(j.JointType, "Distance")
        self.assertEqual(am.mate_key(j), "coincident")

    # ----- mate_key ----------------------------------------------------------

    def _bare(self, jtype, refs, distance=None):
        j = self.jg.newObject("App::FeaturePython", "Bare")
        JointObject.Joint(j, JointObject.JointTypes.index(jtype))
        if distance is not None:
            j.Distance = distance
        j.Proxy.setJointConnectors(j, refs)
        return j

    def test_mate_key_derived(self):
        a, b = self._ref(self.a, "Face2"), self._ref(self.b, "Face1")
        self.assertEqual(am.mate_key(self._bare("Distance", [a, b])), "coincident")
        self.assertEqual(am.mate_key(self._bare("Distance", [a, b], 7)), "distance")
        cyl = self._cyl("Cyl", (40, 40, 0))
        self.assertEqual(am.mate_key(self._bare("Distance", [a, self._ref(cyl, "Face1")])), "tangent")
        for jt, key in (("Fixed", "lock"), ("Revolute", "hinge"), ("Cylindrical", "cylindrical"),
                        ("Slider", "slider"), ("Ball", "ball"), ("Parallel", "parallel"),
                        ("Perpendicular", "perpendicular"), ("Angle", "angle"), ("Gears", "gear"),
                        ("RackPinion", "rack"), ("Screw", "screw"), ("Belt", "belt")):
            j = self.jg.newObject("App::FeaturePython", "Bare")
            JointObject.Joint(j, JointObject.JointTypes.index(jt))
            self.assertEqual(am.mate_key(j), key, jt)

    def test_stale_stored_key_is_ignored(self):
        j = self._coincident()
        j.JointType = "Parallel"  # changed outside the model layer
        self.assertEqual(am.mate_key(j), "parallel")

    # ----- tree / settings ---------------------------------------------------

    def test_tree(self):
        j = self._coincident()
        root = am.tree(self.asm)
        self.assertEqual(root.key, "asm/Assembly")
        self.assertEqual([c.label for c in root.children], ["Components", "Mates", "Motion", "Checks"])
        comps = {n.key: n for n in root.find("sec/components").children}
        self.assertEqual(set(comps), {"comp/A", "comp/B"})
        self.assertEqual(comps["comp/A"].mark, "(f)")
        self.assertEqual(comps["comp/B"].mark, "")
        mates = root.find("sec/mates").children
        self.assertEqual([m.key for m in mates], ["mate/" + j.Name])
        self.assertEqual(mates[0].meta, "A · B")
        self.assertEqual(mates[0].state, "ok")
        self.assertEqual(mates[0].obj, j.Name)
        self.assertEqual(root.find("sec/motion").children, [])
        self.assertEqual(root.find("check/dof").meta, "after B2")

    def test_settings_sections(self):
        j = self._coincident()
        secs = am.settings(j)
        self.assertEqual([s.title for s in secs], ["Selections", "Mate type", "Placement", "Result"])
        picks = secs[0].fields[0]
        self.assertEqual(picks.kind, "picks")
        self.assertEqual(picks.value, ["A · Face2", "B · Face1"])
        self.assertEqual(picks.tags, ("plane", "plane"))
        grid = secs[1].fields[0]
        self.assertEqual(grid.kind, "mategrid")
        self.assertEqual(grid.value, "coincident")
        self.assertEqual(grid.enabled_choices, tuple(am.fitting("plane", "plane")))
        standard = [m[0] for m in am.MATES if m[2] == "standard"]
        self.assertEqual(list(grid.choices[:len(standard)]), standard)
        self.assertIn("hinge", grid.choices)
        self.assertEqual([f.name for f in secs[2].fields], ["flip"])
        self.assertEqual(secs[3].fields[0].kind, "readonly")
        self.assertEqual(secs[3].fields[0].value, "Solved")
        am.set_mate_type(j, "distance")
        self.assertEqual([f.name for f in am.settings(j)[2].fields], ["distance", "flip"])
        am.set_mate_type(j, "angle")
        self.assertEqual([f.name for f in am.settings(j)[2].fields], ["angle", "flip"])

    def test_new_mate_settings(self):
        secs = am.new_mate_settings([])
        self.assertEqual([s.title for s in secs], ["Selections", "Mate type"])
        self.assertEqual(secs[0].fields[0].value, ["Pick a face, edge or point", "then one on another part"])
        grid = secs[1].fields[0]
        self.assertEqual(set(grid.enabled_choices), {m[0] for m in am.MATES if m[2] == "standard"})
        secs = am.new_mate_settings([self._ref(self.a, "Face2")])
        self.assertEqual(secs[0].fields[0].value, ["A · Face2", "then one on another part"])
