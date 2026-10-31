# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless tests of the flexible wires (BW-ROU-08): cut length, taut, gravity axis, the Assembly
drag stop. The rope maths itself is tested in addons/BruceWorx/tests/test_rope.py."""

import math
import os
import unittest

import FreeCAD as App

import bruceworx_routing as br
import bruceworx_rope as brope

V = App.Vector


def vertex_named(obj, local_point):
    inverse = obj.Placement.inverse()
    for i, v in enumerate(obj.Shape.Vertexes):
        if (inverse.multVec(v.Point) - local_point).Length < 1e-6:
            return f"Vertex{i + 1}"
    raise ValueError(local_point)


def rope_length(wire):
    pts = [(p.x, p.y, p.z) for p in wire.RopePoints]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


class RopeCoreTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWRopeCore")
        br.reset_prefs()

    def tearDown(self):
        br.reset_prefs()
        App.closeDocument(self.doc.Name)

    def wire(self, **kw):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, gauge="22 AWG", **kw)
        self.doc.recompute()
        return path, wire

    def test_cut_length_is_the_wires_own(self):
        _path, wire = self.wire()
        cut = (160.0 + 10.0 * math.pi) * 1.05
        self.assertAlmostEqual(wire.CutLength.Value, cut, places=5)
        self.assertAlmostEqual(rope_length(wire), cut, delta=cut * 1e-3)
        self.assertFalse(wire.Taut)
        self.assertTrue(wire.RopeStatus.startswith("sagging"))
        wire.CutLength = 400.0  # any length: longer than the route, it sags
        self.doc.recompute()
        self.assertAlmostEqual(wire.Length.Value, 400.0, places=6)
        self.assertAlmostEqual(rope_length(wire), 400.0, delta=0.4)
        self.assertFalse(wire.Taut)
        # slack edits the cut length (W2)
        wire.Slack = 0
        self.doc.recompute()
        self.assertAlmostEqual(wire.CutLength.Value, 400.0 / 1.05, places=4)

    def test_taut_is_red_with_a_badge_and_a_reason(self):
        _path, wire = self.wire()
        wire.CutLength = 100.0  # the ends are 141 apart
        self.doc.recompute()
        self.assertTrue(wire.Taut)
        self.assertEqual(wire.RopeStatus, "taut")
        self.assertIn("taut", wire.Problems)
        self.assertIn(("taut", "warn"), br.row_badges(wire, {}, [])[1])
        self.assertAlmostEqual(wire.Length.Value, 100.0)  # nothing stretches: the cut length stays
        wire.CutLength = 300.0
        self.doc.recompute()
        self.assertFalse(wire.Taut)
        self.assertEqual(wire.Problems, "")

    def test_existing_wire_keeps_its_length(self):
        """W2: a wire saved before ropes (no cut length) takes its present length, slack included."""
        _path, wire = self.wire()
        length = wire.Length.Value
        wire.CutLength = 0.0
        wire.RopePoints = []
        wire.touch()
        self.doc.recompute()
        self.assertAlmostEqual(wire.CutLength.Value, length, places=6)
        self.assertAlmostEqual(wire.Length.Value, length, places=6)

    def test_gravity_axis_per_assembly(self):
        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        path = br.make_path([(0, 0, 0), (100, 0, 0)], self.doc, mode="Spline", container=asm)
        wire = br.make_wire(path, gauge="22 AWG", slack=40)
        self.doc.recompute()
        self.assertEqual(asm.BWGravity, "-Z")
        low = min(p.z for p in wire.RopePoints)
        self.assertLess(low, -10.0)
        brope.set_gravity(asm, "+Y")
        wire.touch()
        wire.RopePoints = []
        self.doc.recompute()
        ys = [p.y for p in wire.RopePoints]
        self.assertGreater(max(ys), 10.0)
        self.assertAlmostEqual(min(p.z for p in wire.RopePoints), 0.0, delta=2.0)

    def test_stored_state_survives_save_and_pin_move(self):
        box = self.doc.addObject("Part::Box", "Box")
        box.Placement.Base = V(100, 0, 0)
        self.doc.recompute()
        path = br.make_path([(0, 0, 0), (100, 0, 0)], self.doc, mode="Spline",
                            attachments=[(1, box, vertex_named(box, V(0, 0, 0)))])
        wire = br.make_wire(path, gauge="22 AWG", slack=30)
        self.doc.recompute()
        before = [tuple(p) for p in wire.RopePoints]
        self.doc.recompute()
        self.assertEqual(before, [tuple(p) for p in wire.RopePoints])
        box.Placement.Base = V(104, 0, 0)  # a pin moves 4 mm: the wire starts from where it was
        self.doc.recompute()
        after = [tuple(p) for p in wire.RopePoints]
        self.assertAlmostEqual(after[-1][0], 104.0, places=6)
        self.assertLess(max(math.dist(a, b) for a, b in zip(before[:-8], after[:-8])), 6.0)
        self.assertAlmostEqual(rope_length(wire), wire.CutLength.Value, delta=wire.CutLength.Value * 1e-3)


class RopeDragTests(unittest.TestCase):
    """A base, an arm swung on a revolute joint, a wire from the base to the arm tip."""

    def setUp(self):
        try:
            import JointObject
            import UtilsAssembly
        except ImportError:
            self.skipTest("Assembly module not available")
        self.JointObject, self.UtilsAssembly = JointObject, UtilsAssembly
        self.doc = App.newDocument("BWRopeDrag")
        self.assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        jointgroup = self.assembly.newObject("Assembly::JointGroup", "Joints")
        self.base = self.assembly.newObject("Part::Box", "Base")
        self.base.Length = self.base.Width = self.base.Height = 10
        self.arm = self.assembly.newObject("Part::Box", "Arm")
        self.arm.Length, self.arm.Width, self.arm.Height = 40, 5, 5
        self.arm.Placement = App.Placement(V(30, 30, 30), App.Rotation(10, 20, 30))
        ground = jointgroup.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(ground, self.base)
        self.joint = jointgroup.newObject("App::FeaturePython", "Revolute")
        JointObject.Joint(self.joint, JointObject.JointTypes.index("Revolute"))
        self.joint.Proxy.setJointConnectors(self.joint, [[self.base, ["Face6", "Vertex6"]], [self.arm, ["Face5", "Vertex1"]]])
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def reading(self):
        return math.degrees(self.UtilsAssembly.getJointXYAngle(self.joint))

    def turn(self, degrees):
        plc1 = self.UtilsAssembly.getJcsGlobalPlc(self.joint.Placement1, self.joint.Reference1)
        axis = plc1.Rotation.multVec(V(0, 0, 1))
        turn = App.Placement(V(), App.Rotation(axis, degrees - self.reading()), plc1.Base)
        self.arm.Placement = turn * self.arm.Placement

    def tip_gap(self, anchor, tip):
        return (self.arm.Placement.multVec(tip) - self.base.Placement.multVec(anchor)).Length

    def test_drag_stops_where_the_wire_is_taut(self):
        anchor, tip = V(0, 0, 0), V(40, 0, 0)
        gaps = {}
        for angle in range(0, 181, 10):
            self.turn(angle)
            gaps[angle] = self.tip_gap(anchor, tip)
        cut = gaps[90] + 1.0  # the gap grows as the arm swings back from 180 to 0: taut near 85 degrees
        self.turn(180)
        path = br.make_path([anchor, tip], self.doc, mode="Spline",
                            attachments=[(0, self.base, vertex_named(self.base, anchor)),
                                         (1, self.arm, vertex_named(self.arm, tip))], container=self.assembly)
        wire = br.make_wire(path, gauge="22 AWG", slack=0)
        wire.CutLength = cut
        self.doc.recompute()
        self.assertFalse(wire.Taut)
        self.assertAlmostEqual(rope_length(wire), cut, delta=cut * 1e-3)
        self.assembly.preDrag([self.arm])
        for angle in range(180, -1, -10):
            self.turn(angle)
            self.assembly.doDragStep()
        self.assembly.postDrag()
        final = self.reading()
        stop_gap = self.tip_gap(anchor, tip)
        # it stopped where the gap reaches the cut length (about 85 degrees), not at 0
        self.assertGreater(final, 60.0)
        self.assertLess(final, 100.0)
        self.assertLessEqual(self.tip_gap(anchor, tip), cut + 0.05)
        self.assertGreater(self.tip_gap(anchor, tip), cut - 1.5)
        self.assertFalse(wire.Taut)
        self.assertAlmostEqual(wire.Length.Value, cut, places=6)
        self.assertAlmostEqual(rope_length(wire), cut, delta=cut * 1e-3)
        # dragging back frees it
        self.assembly.preDrag([self.arm])
        for angle in (final + 20, final + 60, 180):
            self.turn(angle)
            self.assembly.doDragStep()
        self.assembly.postDrag()
        self.assertAlmostEqual(self.reading(), 180.0, delta=0.5)
        self.assertFalse(wire.Taut)
        print(f"\n  drag stopped at {final:.1f} deg (gap {stop_gap:.2f} of cut {cut:.2f})")


class BatteryTests(unittest.TestCase):
    """The battery assembly STD-02-01001-001 on a copy of the vault (BW_BATTERY=<path to the copy's .FCStd>)."""

    def test_battery_leads_keep_their_length(self):
        path = os.environ.get("BW_BATTERY")
        if not path or not os.path.exists(path):
            self.skipTest("BW_BATTERY not set")
        doc = App.openDocument(path)
        try:
            wires = br.routed(doc, (br.WIRE,))
            before = {w.Name: w.Length.Value for w in wires}
            doc.recompute()
            for w in wires:
                self.assertAlmostEqual(w.Length.Value, before[w.Name], places=6)
                self.assertAlmostEqual(w.CutLength.Value, before[w.Name], places=6)
                self.assertFalse(w.Taut, (w.Label, w.Problems))
                self.assertAlmostEqual(rope_length(w), w.CutLength.Value - w.FromStrip.Value - w.ToStrip.Value,
                                       delta=w.CutLength.Value * 2e-3)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
