# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests for the motion commands (bruceworx_motion, ROB-05): the Joint menu, the Drive Joint
panel moving the part and leaving nothing behind on Cancel, the Motion Study panel (keyframes,
solve, scrub, playback modes, frames saved, Cancel and OK), the Motion Link panel."""

import glob
import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import bruceworx_motion as bm
from AssemblyTests.TestBWMotion import ArmCase, vertex_named

V = App.Vector


class GuiArm(ArmCase):
    def setUp(self):
        super().setUp()
        self.count = len(self.doc.Objects)
        self.doc.UndoMode = 1

    def tearDown(self):
        for key in ("driving", "study", "link"):
            panel = bm._state.get(key)
            if panel is not None:
                panel.reject()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        super().tearDown()

    def pump(self):
        QtWidgets.QApplication.processEvents()


class TestCommands(GuiArm):
    def test_commands_and_icons_are_registered(self):
        for name in bm.COMMANDS:
            self.assertIsNotNone(Gui.Command.get(name), name)
            self.assertTrue(os.path.exists(os.path.join(bm.HERE, name + ".svg")), name)

    def test_joint_menu_lists_the_thirteen_joint_types(self):
        menu = bm.joint_menu()
        names = [a.objectName() for a in menu.actions()]
        self.assertEqual(names, bm.JOINT_COMMANDS)
        self.assertEqual(len(names), 13)
        self.assertTrue(all(not a.icon().isNull() for a in menu.actions()))

    def test_the_motion_group_is_on_the_ribbon(self):
        import bruceworx_workspaces as ws

        defn = ws.definition()
        self.assertEqual(ws.place_of(defn, "BruceWorx_MotionStudy")[2], "Motion")
        self.assertEqual(ws.place_of(defn, "BruceWorx_Joint")[2], "Assemble")
        place = ws.place_of(defn, "BruceWorx_MotionStudy")
        self.assertIn("Assembly_CreateSimulation", ws.more_commands(defn, ws.toolbar_name(place[1], place[2])))


class TestDriveJoint(GuiArm):
    def test_slider_moves_the_part_and_cancel_puts_it_back(self):
        panel = bm.drive(self.assembly)
        self.assertIsNotNone(Gui.Control.activeDialog())
        self.assertEqual(panel.combo.count(), 2)
        self.assertEqual(panel.range_label.text(), "-180 to 180 deg (no limits set)")
        before = self.arm1.Placement.Base
        panel.slider.setValue(panel.STEPS * 3 // 4)  # 90 deg
        self.pump()
        self.assertAlmostEqual(panel.spin.value(), 90, places=6)
        self.assertAlmostEqual(self.angle(self.j1), 90, places=2)
        self.assertGreater((self.arm1.Placement.Base - before).Length + abs(self.arm1.Placement.Rotation.Angle), 0.1)
        panel.spin.setValue(-45)
        self.pump()
        self.assertAlmostEqual(self.angle(self.j1), -45, places=2)
        self.assertAlmostEqual(self.angle(self.j2), 0, places=2)
        self.assertGreater(len(self.doc.Objects), self.count)  # the temporary simulation, while open
        panel.reject()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(len(self.doc.Objects), self.count)
        self.assertAlmostEqual(self.angle(self.j1), 0, places=3)
        self.assertFalse(any(o.TypeId == "Assembly::SimulationGroup" for o in self.doc.Objects))

    def test_ok_keeps_the_pose_as_one_undo_step(self):
        steps = self.doc.UndoCount
        panel = bm.drive(self.assembly, self.j2)
        self.assertEqual(panel.combo.currentIndex(), 1)
        panel.spin.setValue(30)
        self.pump()
        panel.accept()
        self.assertEqual(len(self.doc.Objects), self.count)
        self.assertAlmostEqual(self.angle(self.j2), 30, places=2)
        self.assertEqual(self.doc.UndoCount, steps + 1)
        self.doc.undo()
        self.doc.recompute()
        self.assertAlmostEqual(self.angle(self.j2), 0, places=3)

    def test_limits_bound_the_slider(self):
        self.j1.EnableAngleMin = self.j1.EnableAngleMax = True
        self.j1.AngleMin, self.j1.AngleMax = -20, 50
        panel = bm.drive(self.assembly, self.j1)
        self.assertEqual(panel.range_label.text(), "-20 to 50 deg")
        panel.spin.setValue(200)
        self.pump()
        self.assertAlmostEqual(panel.spin.value(), 50)
        self.assertAlmostEqual(self.angle(self.j1), 50, places=2)

    def test_animate_sweeps_the_range_and_bounces(self):
        panel = bm.drive(self.assembly)
        panel.speed.setValue(1000.0)  # 40 deg per 40 ms tick
        panel.animate.setChecked(True)
        self.assertTrue(panel.timer.isActive())
        values = []
        for _ in range(12):
            panel.tick()
            values.append(panel.spin.value())
        panel.animate.setChecked(False)
        self.assertFalse(panel.timer.isActive())
        self.assertIn(180.0, values)  # reached the top and turned
        self.assertLess(values[-1], 180.0)
        self.assertAlmostEqual(self.angle(self.j1), values[-1], places=1)


class TestMotionStudy(GuiArm):
    def open(self):
        panel = bm.study(self.assembly)
        panel.duration.setValue(1.0)
        panel.fps.setValue(4)
        panel.table.selectRow(0)
        panel.add_keyframe(0, 0)
        panel.add_keyframe(1, 90)
        panel.table.selectRow(1)
        panel.add_keyframe(0.5, -40)
        return panel

    def test_new_study_keyframes_solve_scrub_and_cancel(self):
        panel = self.open()
        self.assertTrue(panel.created)
        self.assertEqual(len(bm.studies(self.assembly)), 1)
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(0, 2).text(), "2")
        self.assertEqual(panel.keys.rowCount(), 1)
        self.assertEqual(panel.solve(), 0)
        self.assertEqual(panel.frames, 5)
        self.assertEqual(panel.scrub.maximum(), 4)
        panel.scrub.setValue(2)
        self.pump()
        self.assertEqual(panel.time_label.text(), "0.50 s")
        self.assertAlmostEqual(self.angle(self.j1), 45, places=2)
        self.assertAlmostEqual(self.angle(self.j2), -40, places=2)
        self.assertEqual(panel.table.item(0, 3).text(), "45.00 deg")
        self.assertEqual(panel.keys.currentRow(), 0)  # the keyframe at 0.5 s highlighted
        panel.table.selectRow(0)
        panel.go(4)
        self.assertAlmostEqual(self.angle(self.j1), 90, places=2)
        self.assertEqual(panel.keys.currentRow(), 1)
        panel.reject()
        self.assertEqual(bm.studies(self.assembly), [])
        self.assertEqual(len(self.doc.Objects), self.count)
        self.assertAlmostEqual(self.angle(self.j1), 0, places=3)

    def test_ok_keeps_the_study_and_restores_the_pose(self):
        panel = self.open()
        panel.name.setText("Wave")
        panel.solve()
        panel.go(4)
        panel.accept()
        self.assertAlmostEqual(self.angle(self.j1), 0, places=3)
        studies = bm.studies(self.assembly)
        self.assertEqual([s.Label for s in studies], ["Wave"])
        sim = studies[0]
        self.assertEqual(bm.keyframes_of(bm.motion_for(sim, self.j1, "Angular")), [(0.0, 0.0), (1.0, 90.0)])
        self.assertIn("piecewise(time", bm.motion_for(sim, self.j1, "Angular").Formula)
        again = bm.study(self.assembly)
        self.assertIs(again.sim, sim)
        self.assertFalse(again.created)
        self.assertEqual(again.table.item(0, 2).text(), "2")
        again.reject()
        self.assertEqual(bm.studies(self.assembly), [sim])  # cancel of an edit keeps the study

    def test_keyframes_edited_in_the_table_and_removed(self):
        panel = self.open()
        panel.table.selectRow(0)
        panel.keys.item(1, 1).setText("60")
        self.pump()
        self.assertEqual(bm.keyframes_of(panel.rows[0][0]), [(0.0, 0.0), (1.0, 60.0)])
        panel.keys.setCurrentCell(0, 0)
        panel.remove_keyframe()
        self.assertEqual(bm.keyframes_of(panel.rows[0][0]), [(1.0, 60.0)])
        self.assertEqual(panel.table.item(0, 2).text(), "1")
        self.assertTrue(panel.dirty)

    def test_playback_modes(self):
        panel = self.open()
        panel.solve()
        panel.mode.setCurrentText("Once")
        panel.buttons["play"].setChecked(True)
        self.assertTrue(panel.timer.isActive())
        for _ in range(10):
            panel.tick()
        self.assertEqual(panel.frame, 4)
        self.assertFalse(panel.buttons["play"].isChecked())  # stopped at the end
        panel.mode.setCurrentText("Loop")
        panel.buttons["play"].setChecked(True)
        panel.tick()
        self.assertEqual(panel.frame, 0)
        panel.mode.setCurrentText("Ping-pong")
        panel.go(3)
        panel.tick()
        panel.tick()
        self.assertEqual(panel.frame, 3)  # 4 then back to 3
        self.assertEqual(panel.direction, -1)
        panel.speed.setCurrentIndex(4)
        self.assertEqual(panel.sim.BWSpeed, 4.0)
        self.assertIn(panel.timer.interval(), (62, 63))  # 1000 / (4 fps x 4)
        panel.stop()
        self.assertEqual(panel.frame, 0)
        self.assertFalse(panel.timer.isActive())

    def test_hold_off_lets_a_joint_follow(self):
        panel = self.open()
        panel.table.selectRow(1)
        panel.keys.setCurrentCell(0, 0)
        panel.remove_keyframe()
        panel.table.item(1, 1).setCheckState(QtCore.Qt.Unchecked)
        self.assertFalse(panel.rows[1][0].BWHold)
        panel.solve()
        self.assertEqual(panel.rows[1][0].Formula, "")

    def test_save_frames_writes_pngs_and_a_video(self):
        panel = self.open()
        folder = tempfile.mkdtemp(prefix="bw-motion-")
        try:
            paths, mp4 = panel.save_frames(folder)
            self.assertEqual(len(paths), 5)
            self.assertEqual(sorted(glob.glob(os.path.join(folder, "frame_*.png"))), paths)
            self.assertGreater(os.path.getsize(paths[-1]), 1000)
            if shutil.which("ffmpeg"):
                self.assertTrue(mp4 and os.path.getsize(mp4) > 500)
            self.assertIn("5 frames saved", panel.status.text())
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestMotionLink(GuiArm):
    def setUp(self):
        super().setUp()
        self.wheel = self.box("Wheel", 8, 8, 4)
        self.j3 = self.revolute("J3", self.base, ["Face6", vertex_named(self.base, V(0, 0, 10))],
                                self.wheel, ["Face5", vertex_named(self.wheel, V(0, 0, 0))])
        self.count = len(self.doc.Objects)

    def test_gears_from_two_revolutes(self):
        panel = bm.link(self.assembly)
        self.assertEqual(panel.first.count(), 3)
        panel.first.setCurrentIndex(0)
        panel.second.setCurrentIndex(2)
        panel.ratio.setValue(2.0)
        self.assertTrue(panel.ratio.isVisibleTo(panel.form))
        self.assertFalse(panel.mm.isVisibleTo(panel.form))
        self.assertIn("Gears joint, radii 20 / 10 mm", panel.preview.text())
        self.assertTrue(panel.accept())
        self.assertEqual(len(self.doc.Objects), self.count + 1)
        self.assertEqual(panel.link.JointType, "Gears")
        self.assertEqual(panel.link.Label, "Link: J1 -> J3 (1:2)")
        self.assertFalse(Gui.Control.activeDialog())

    def test_reverse_makes_a_belt_and_same_joint_is_refused(self):
        panel = bm.link(self.assembly)
        panel.second.setCurrentIndex(0)
        self.assertIn("Pick two different joints", panel.preview.text())
        self.assertFalse(panel.accept())
        panel.second.setCurrentIndex(2)
        panel.reverse.setChecked(True)
        self.assertIn("Belt joint", panel.preview.text())
        self.assertTrue(panel.accept())
        self.assertEqual(panel.link.JointType, "Belt")

    def test_cancel_makes_nothing(self):
        panel = bm.link(self.assembly)
        panel.reject()
        self.assertEqual(len(self.doc.Objects), self.count)
        self.assertFalse(Gui.Control.activeDialog())


if __name__ == "__main__":
    unittest.main()
