# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] ROB-05 kinematic motion, headless: keyframe formulas for the Ondsel solver, a motion
# study on a 2-DOF arm (poses at t = 0, 0.5, 1 match the interpolation), the Driver, and motion
# links (gears, belt, rack and pinion) made from the stock joints.

import math
import unittest

import FreeCAD as App

import JointObject
import UtilsAssembly
import bruceworx_motion as bm

V = App.Vector


def vertex_named(obj, local_point):
    """The name of the box vertex at a local point (the box may have been moved)."""
    inverse = obj.Placement.inverse()
    for i, v in enumerate(obj.Shape.Vertexes):
        if (inverse.multVec(v.Point) - local_point).Length < 1e-6:
            return f"Vertex{i + 1}"
    raise ValueError(local_point)


class ArmCase(unittest.TestCase):
    """A base, Arm1 revolute on the base, Arm2 revolute at the end of Arm1 (both axes vertical)."""

    def setUp(self):
        self.doc = App.newDocument(self.__class__.__name__)
        self.assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jointgroup = self.assembly.newObject("Assembly::JointGroup", "Joints")
        self.base = self.box("Base", 10, 10, 10)
        self.arm1 = self.box("Arm1", 40, 5, 5)
        self.arm2 = self.box("Arm2", 30, 5, 5)
        ground = self.jointgroup.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(ground, self.base)
        self.j1 = self.revolute("J1", self.base, ["Face6", vertex_named(self.base, V(10, 10, 10))],
                                self.arm1, ["Face5", vertex_named(self.arm1, V(0, 0, 0))])
        self.j2 = self.revolute("J2", self.arm1, ["Face6", vertex_named(self.arm1, V(40, 0, 5))],
                                self.arm2, ["Face5", vertex_named(self.arm2, V(0, 0, 0))])
        self.assembly.solve()
        self.start = UtilsAssembly.saveAssemblyPartsPlacements(self.assembly)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def box(self, name, length, width, height):
        obj = self.assembly.newObject("Part::Box", name)
        obj.Length, obj.Width, obj.Height = length, width, height
        self.doc.recompute()
        return obj

    def joint(self, name, kind, refs):
        joint = self.jointgroup.newObject("App::FeaturePython", name)
        JointObject.Joint(joint, JointObject.JointTypes.index(kind))
        joint.Proxy.setJointConnectors(joint, refs)
        self.doc.recompute()
        return joint

    def revolute(self, name, a, ref_a, b, ref_b):
        return self.joint(name, "Revolute", [[a, ref_a], [b, ref_b]])

    def angle(self, joint):
        return bm.reading(joint, "Angular")

    def poses(self, sim):
        """(time, J1, J2) per solver frame after the start pose."""
        rc, frames = bm.run_study(self.assembly, sim, self.start)
        self.assertEqual(rc, 0)
        out = []
        for i in range(frames):
            bm.show_frame(self.assembly, i)
            out.append((bm.time_of_frame(sim, i), self.angle(self.j1), self.angle(self.j2)))
        return out


class TestFormulas(unittest.TestCase):
    def test_interpolation_is_linear_and_constant_outside(self):
        keys = [(0, 0), (0.5, 90), (1, 30)]
        self.assertEqual(bm.interpolate(keys, -1), 0)
        self.assertAlmostEqual(bm.interpolate(keys, 0.25), 45)
        self.assertAlmostEqual(bm.interpolate(keys, 0.5), 90)
        self.assertAlmostEqual(bm.interpolate(keys, 0.75), 60)
        self.assertEqual(bm.interpolate(keys, 7), 30)
        self.assertIsNone(bm.interpolate([], 0))

    def test_keyframes_are_sorted_and_unique_by_time(self):
        self.assertEqual(bm.clean_keyframes([(1, 5), (0, 1), (1, 7)]), [(0.0, 1.0), (1.0, 7.0)])

    def test_formula_is_the_solvers_piecewise(self):
        self.assertEqual(bm.formula([]), "")
        self.assertEqual(bm.formula([(0.3, 2)]), "2")
        text = bm.formula([(0, 0), (0.5, 1), (1, 0.25)])
        self.assertEqual(text, "piecewise(time, functions(0, 0 + 2*time, 1 - 1.5*(time - 0.5), 0.25), "
                               "transitions(0, 0.5, 1))")
        self.assertEqual(bm.formula([(0, 10), (2, 20)], scale=0.5, offset=-10),
                         "piecewise(time, functions(0, 0 + 2.5*time, 5), transitions(0, 2))")

    def test_channel_formula_converts_degrees_to_radians(self):
        class Joint:
            LimitAngleZero = 0.0

        self.assertEqual(bm.channel_formula(Joint(), "Angular", [(0, 180)]), bm.fmt(math.pi))
        self.assertEqual(bm.channel_formula(Joint(), "Linear", [(0, 12.5)]), "12.5")

    def test_link_arithmetic(self):
        self.assertEqual(bm.link_radii(3), (30.0, 10.0))
        self.assertAlmostEqual(bm.pitch_radius(2 * math.pi * 7), 7.0)


class TestStudy(ArmCase):
    def test_channels_and_ranges(self):
        self.assertEqual(bm.channels(self.assembly), [(self.j1, "Angular"), (self.j2, "Angular")])
        self.assertEqual(bm.joint_range(self.j1, "Angular"), (-180.0, 180.0))
        self.j1.EnableAngleMin = self.j1.EnableAngleMax = True
        self.j1.AngleMin, self.j1.AngleMax = -30, 60
        self.assertEqual(bm.joint_range(self.j1, "Angular"), (-30.0, 60.0))

    def test_study_has_a_motion_per_joint_and_is_a_simulation(self):
        sim = bm.make_study(self.assembly)
        self.assertTrue(bm.is_study(sim))
        self.assertEqual([j for _m, j, _k in bm.study_rows(sim)], [self.j1, self.j2])
        self.assertEqual(bm.studies(self.assembly), [sim])
        self.assertEqual(sim.Group[0].Formula, "")
        self.assertTrue(sim.Group[0].BWHold)

    def test_keyframed_arm_reaches_each_pose_at_its_time(self):
        sim = bm.make_study(self.assembly)
        sim.bTimeEnd = 1.0
        sim.cTimeStepOutput = 0.25
        m1, m2 = bm.motion_for(sim, self.j1, "Angular"), bm.motion_for(sim, self.j2, "Angular")
        bm.set_keyframes(m1, [(0, 0), (0.5, 90), (1, 30)])
        bm.set_keyframes(m2, [(0.25, -20), (0.75, 40)])
        self.assertEqual(bm.keyframes_of(m1), [(0.0, 0.0), (0.5, 90.0), (1.0, 30.0)])
        self.assertEqual(bm.frame_count(sim), 5)
        for t, a1, a2 in self.poses(sim):
            self.assertAlmostEqual(a1, bm.interpolate(bm.keyframes_of(m1), t), places=3, msg=f"J1 at {t}")
            self.assertAlmostEqual(a2, bm.interpolate(bm.keyframes_of(m2), t), places=3, msg=f"J2 at {t}")
        self.assertEqual(bm.frame_of_time(sim, 0.5), 2)
        self.assertEqual(bm.frame_of_time(sim, 9), 4)

    def test_a_joint_without_keyframes_holds_or_follows(self):
        sim = bm.make_study(self.assembly)
        sim.bTimeEnd = 1.0
        sim.cTimeStepOutput = 0.5
        bm.set_keyframes(bm.motion_for(sim, self.j1, "Angular"), [(0, 0), (1, 60)])
        poses = self.poses(sim)
        self.assertAlmostEqual(poses[-1][1], 60, places=3)
        self.assertAlmostEqual(poses[-1][2], 0, places=3)  # J2 held
        self.assertNotEqual(bm.motion_for(sim, self.j2, "Angular").Formula, "")
        bm.motion_for(sim, self.j2, "Angular").BWHold = False
        self.poses(sim)
        self.assertEqual(bm.motion_for(sim, self.j2, "Angular").Formula, "")

    def test_sync_adds_and_drops_channels(self):
        sim = bm.make_study(self.assembly)
        self.j2.Suppressed = True
        bm.sync_channels(self.assembly, sim)
        self.assertEqual([j for _m, j, _k in bm.study_rows(sim)], [self.j1])
        self.j2.Suppressed = False
        bm.sync_channels(self.assembly, sim)
        self.assertEqual(len(bm.study_rows(sim)), 2)

    def test_driver_moves_one_joint_and_leaves_nothing_behind(self):
        count = len(self.doc.Objects)
        driver = bm.Driver(self.assembly)
        self.assertEqual(driver.drive(self.j1, "Angular", 45), 0)
        self.assertAlmostEqual(self.angle(self.j1), 45, places=3)
        self.assertAlmostEqual(self.angle(self.j2), 0, places=3)
        self.assertEqual(driver.drive(self.j2, "Angular", -30), 0)
        self.assertAlmostEqual(self.angle(self.j1), 45, places=3)
        self.assertAlmostEqual(self.angle(self.j2), -30, places=3)
        self.assertEqual(driver.drive(self.j1, "Angular", -170), 0)  # a 215 degree jump, in steps
        self.assertAlmostEqual(self.angle(self.j1), -170, places=3)
        self.assertEqual(driver.drive(self.j1, "Angular", 170), 0)  # 20 degrees the short way round
        self.assertAlmostEqual(self.angle(self.j1), 170, places=3)
        driver.restore()
        driver.finish(keep=False)
        self.assertEqual(len(self.doc.Objects), count)
        self.assertAlmostEqual(self.angle(self.j1), 0, places=3)

    def test_driver_keep_is_one_undo_step(self):
        self.doc.UndoMode = 1  # off by default headless
        count = len(self.doc.Objects)
        steps = self.doc.UndoCount
        driver = bm.Driver(self.assembly)
        driver.drive(self.j1, "Angular", 30)
        driver.finish(keep=True)
        self.assertEqual(len(self.doc.Objects), count)
        self.assertAlmostEqual(self.angle(self.j1), 30, places=3)
        self.assertEqual(self.doc.UndoCount, steps + 1)
        self.doc.undo()
        self.doc.recompute()
        self.assertAlmostEqual(self.angle(self.j1), 0, places=3)
        self.assertEqual(len(self.doc.Objects), count)


class TestLinks(ArmCase):
    """Two wheels on the base, each revolute about a vertical axis: a carrier they share."""

    def setUp(self):
        super().setUp()
        self.wheel = self.box("Wheel", 8, 8, 4)
        self.j3 = self.revolute("J3", self.base, ["Face6", vertex_named(self.base, V(0, 0, 10))],
                                self.wheel, ["Face5", vertex_named(self.wheel, V(0, 0, 0))])
        self.assembly.solve()
        self.start = UtilsAssembly.saveAssemblyPartsPlacements(self.assembly)

    def test_link_kinds(self):
        self.assertEqual(bm.link_kind(self.j1, self.j3), "Gears")
        slider = self.joint("S", "Slider", [[self.base, ["Face2", vertex_named(self.base, V(10, 10, 10))]],
                                            [self.box("Rack", 60, 5, 5), ["Face1", "Vertex1"]]])
        self.assertEqual(bm.link_kind(self.j1, slider), "RackPinion")
        self.assertEqual(bm.link_kind(slider, self.j1), "RackPinion")
        slider2 = self.joint("S2", "Slider", [[self.base, ["Face2", vertex_named(self.base, V(10, 10, 10))]],
                                              [self.box("Rack2", 60, 5, 5), ["Face1", "Vertex1"]]])
        self.assertIsNone(bm.link_kind(slider, slider2))
        with self.assertRaises(ValueError):
            bm.make_link(self.assembly, slider, slider2)

    def drive_j1(self, degrees, steps=2):
        sim = bm.make_study(self.assembly)
        sim.bTimeEnd = 1.0
        sim.cTimeStepOutput = 1.0 / steps
        bm.set_keyframes(bm.motion_for(sim, self.j1, "Angular"), [(0, 0), (1, degrees)])
        rc, frames = bm.run_study(self.assembly, sim, self.start)
        self.assertEqual(rc, 0, "the solver refused the link")
        bm.show_frame(self.assembly, frames - 1)
        return sim

    def test_gears_drive_the_second_joint_at_the_ratio_the_other_way(self):
        link = bm.make_link(self.assembly, self.j1, self.j3, ratio=3)
        self.assertEqual(link.JointType, "Gears")
        self.assertEqual(link.Label, "Link: J1 -> J3 (1:3)")
        self.assertEqual((link.Distance.Value, link.Distance2.Value), (30.0, 10.0))
        self.assertFalse(bm.motion_for(bm.make_study(self.assembly), self.j3, "Angular").BWHold)
        self.drive_j1(40)
        self.assertAlmostEqual(self.angle(self.j1), 40, places=2)
        self.assertAlmostEqual(self.angle(self.j3), -120, places=2)

    def test_reverse_is_a_belt_the_same_way(self):
        link = bm.make_link(self.assembly, self.j1, self.j3, ratio=0.5, reverse=True)
        self.assertEqual(link.JointType, "Belt")
        self.assertEqual(link.Label, "Link: J1 -> J3 (1:0.5)")
        self.drive_j1(40)
        self.assertAlmostEqual(self.angle(self.j3), 20, places=2)

    def test_rack_and_pinion_from_mm_per_turn(self):
        rack = self.box("Rack", 60, 5, 5)
        rack.Placement = App.Placement(V(0, 20, 10), App.Rotation())
        slider = self.joint("S", "Slider", [[self.base, ["Face2", vertex_named(self.base, V(10, 10, 10))]],
                                            [rack, ["Face1", vertex_named(rack, V(0, 0, 0))]]])
        self.assembly.solve()
        self.start = UtilsAssembly.saveAssemblyPartsPlacements(self.assembly)
        link = bm.make_link(self.assembly, self.j1, slider, mm_per_turn=36.0)
        self.assertEqual(link.JointType, "RackPinion")
        self.assertEqual(link.Label, "Link: J1 -> S (36 mm/turn)")
        self.assertAlmostEqual(link.Distance.Value, 36 / (2 * math.pi), places=6)
        self.drive_j1(90, steps=4)
        self.assertAlmostEqual(abs(bm.reading(slider, "Linear")), 9.0, places=2)
        sign = bm.reading(slider, "Linear")
        self.doc.removeObject(link.Name)
        reverse = bm.make_link(self.assembly, self.j1, slider, mm_per_turn=36.0, reverse=True)
        self.assertAlmostEqual(reverse.Distance.Value, -36 / (2 * math.pi), places=6)
        self.drive_j1(90, steps=4)
        self.assertAlmostEqual(bm.reading(slider, "Linear"), -sign, places=2)


if __name__ == "__main__":
    unittest.main()
