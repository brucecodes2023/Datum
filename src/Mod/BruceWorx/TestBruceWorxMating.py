# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of smart fastener mating (ASM-09) and mate inference (ASM-11): bruceworx_mating.

Headless: FreeCADCmd -t TestBruceWorxMating; the GUI tests: FreeCAD -t TestBruceWorxMating"""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App
import Part

import bruceworx_hardware as bh
import bruceworx_mating as bm

HAVE_FASTENERS = bh.addon_available()
V = App.Vector
HOLES = ((10, 10), (30, 10), (30, 30), (10, 30))


def pump(n=8):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


def block(size=40.0, height=20.0, z=0.0, holes=(), dia=2.5, depth=None, cbore=None, csink=None):
    """A square block with holes from the top (through, or ``depth`` deep); ``cbore`` (Ø, depth), ``csink`` Ø."""
    shape = Part.makeBox(size, size, height, V(0, 0, z))
    top = z + height
    for x, y in holes:
        d = height if depth is None else depth
        shape = shape.cut(Part.makeCylinder(dia / 2, d + (0 if depth else 2), V(x, y, top - d - (0 if depth else 1))))
        if cbore:
            shape = shape.cut(Part.makeCylinder(cbore[0] / 2, cbore[1] + 1, V(x, y, top - cbore[1])))
        if csink:  # a 90 degree countersink to Ø csink at the top
            r = csink / 2
            shape = shape.cut(Part.makeCone(dia / 2, r + 1, r + 1 - dia / 2, V(x, y, top - (r - dia / 2))))
    return shape.removeSplitter()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.before = set(App.listDocuments())
        self.doc = App.newDocument("Mating")
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.asm.newObject("Assembly::JointGroup", "Joints")

    def tearDown(self):
        for name in set(App.listDocuments()) - self.before:
            App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def part(self, name, shape, placement=None):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        self.asm.addObject(obj)
        if placement is not None:
            obj.Placement = placement
        self.doc.recompute()
        return obj

    def ground(self, obj):
        bm.ground(self.asm, obj)
        self.asm.solve()

    def motor_mount(self, **plate):
        base = self.part("Base", block(holes=HOLES, dia=2.5, depth=10))
        args = dict(holes=HOLES, dia=3.4, height=5.0, z=20.0)
        args.update(plate)
        mount = self.part("MotorMount", block(**args))
        self.ground(base)
        return base, mount

    def joints(self):
        import UtilsAssembly

        return [j for j in UtilsAssembly.getJointGroup(self.asm).Group if hasattr(j, "JointType")]


class TestPure(unittest.TestCase):
    def test_residual(self):
        p = App.Placement
        z = App.Rotation()
        flip = App.Rotation(V(1, 0, 0), 180)
        self.assertTrue(bm.residual_ok("hinge", p(V(1, 2, 3), z), p(V(1, 2, 3), flip))[0])
        self.assertEqual(bm.residual_ok("hinge", p(V(1, 2, 4.2), z), p(V(1, 2, 3), z)), (False, "1.200 mm off the seat"))
        self.assertTrue(bm.residual_ok("coincident", p(V(9, 9, 0), z), p(V(0, 0, 0), flip))[0])
        self.assertFalse(bm.residual_ok("coincident", p(V(0, 0, 1), z), p(V(0, 0, 0), z))[0])
        self.assertTrue(bm.residual_ok("concentric", p(V(0, 0, 7), z), p(V(0, 0, 0), z))[0])
        self.assertFalse(bm.residual_ok("concentric", p(V(0, 0.5, 0), z), p(V(0, 0, 0), z))[0])
        self.assertFalse(bm.residual_ok("coincident", p(V(), App.Rotation(V(1, 0, 0), 3)), p(V(), z))[0])

    def test_clearance_sizes_are_iso_273(self):
        self.assertEqual(bm._clearance_size(3.4), "M3")
        self.assertEqual(bm._clearance_size(3.2), "M3")
        self.assertEqual(bm._clearance_size(4.5), "M4")
        self.assertEqual(bm._clearance_size(3.0), "M2.5")
        self.assertEqual(bm._clearance_size(7.5), "")
        self.assertTrue(bm._threaded_for(2.5, "M3"))  # the tap drill
        self.assertTrue(bm._threaded_for(3.0, "M3"))  # modelled at the major diameter
        self.assertFalse(bm._threaded_for(3.4, "M3"))


@unittest.skipUnless(HAVE_FASTENERS, "the Fasteners add-on is not on the path")
class TestLength(unittest.TestCase):
    def test_tapped_longest_that_does_not_bottom_out(self):
        self.assertEqual(bm.choose_length("ISO4762", "M3", 5.0, 10.0, min_engagement=1.0), ("14", 9.0))
        with self.assertRaises(bm.MatingError):
            bm.choose_length("ISO4762", "M3", 5.0, 2.0, min_engagement=1.0)  # 2 mm of thread: too little
        self.assertEqual(bm.choose_length("ISO4762", "M3", 5.0, 2.0, min_engagement=0.0)[0], "6")

    def test_nut_shortest_that_reaches_through(self):
        m = bm.nut_height("M3")
        self.assertAlmostEqual(m, 2.4, places=3)  # ISO 4032 M3 m max, from the add-on's table
        self.assertEqual(bm.choose_length("ISO4762", "M3", 10.0, nut=m), ("14", 4.0))

    def test_engagement_is_a_preference(self):
        prefs = App.ParamGet(bm.PREFS_PATH)
        self.addCleanup(prefs.RemFloat, bm.PREF_ENGAGEMENT)
        prefs.SetFloat(bm.PREF_ENGAGEMENT, 4.0)
        with self.assertRaises(bm.MatingError):
            bm.choose_length("ISO4762", "M3", 5.0, 10.0)  # 9 mm < 4 x 3


@unittest.skipUnless(HAVE_FASTENERS, "the Fasteners add-on is not on the path")
class TestBoltUp(Base):
    def test_motor_mount_four_screws_jointed_nothing_moves(self):
        base, mount = self.motor_mount()
        report = bm.bolt(self.asm, mount)
        self.doc.recompute()
        self.assertEqual(len(report.placed), 4, report.text())
        self.assertEqual(report.skipped, [])
        self.assertEqual(report.moved, [])
        for p in report.placed:
            self.assertEqual((p.line.size, p.line.head, p.length), ("M3", "ISO4762", "14"))
            self.assertAlmostEqual(p.line.clamp, 5.0, places=6)
            self.assertEqual(len(p.joints), 1)
            self.assertEqual(p.joints[0].JointType, "Revolute")
            self.assertLess(abs(p.screw.Placement.Base.z - 25.0), 1e-6)
            self.assertLess((p.screw.Placement.Rotation.multVec(V(0, 0, 1)) - V(0, 0, 1)).Length, 1e-9)
        self.assertEqual(sorted((round(p.screw.Placement.Base.x), round(p.screw.Placement.Base.y)) for p in report.placed),
                         sorted(HOLES))
        self.assertEqual(mount.Placement, App.Placement())
        self.assertEqual(self.asm.solve(), 0)
        again = bm.bolt(self.asm, mount)  # the holes are full now
        self.assertEqual(again.placed, [])

    def test_through_holes_get_a_nut(self):
        lower = self.part("Lower", block(holes=HOLES[:1], dia=3.4, height=5.0, z=0.0))
        upper = self.part("Upper", block(holes=HOLES[:1], dia=3.4, height=5.0, z=5.0))
        self.ground(lower)
        report = bm.bolt(self.asm, upper)
        (p,) = report.placed
        self.assertEqual(p.length, "14")  # clamp 10 + nut 2.4 -> the next standard length
        self.assertIsNotNone(p.nut)
        self.assertLess((p.nut.Placement.Base - V(10, 10, 0)).Length, 1e-6)
        self.assertLess((p.nut.Placement.Rotation.multVec(V(0, 0, 1)) - V(0, 0, -1)).Length, 1e-9)
        self.assertEqual(len(p.joints), 2)
        self.assertEqual(report.moved, [])

    def test_counterbore_and_countersink(self):
        base = self.part("Base", block(holes=HOLES[:2], dia=2.5, depth=12))
        plate = self.doc.addObject("Part::Feature", "Plate")
        shape = block(holes=HOLES[:1], dia=3.4, height=10.0, z=20.0, cbore=(6.5, 3.5))
        shape = shape.cut(Part.makeCylinder(1.7, 12, V(30, 10, 19)))
        r = 3.15
        shape = shape.cut(Part.makeCone(1.7, r + 1, r + 1 - 1.7, V(30, 10, 30 - (r - 1.7)))).removeSplitter()
        plate.Shape = shape
        self.asm.addObject(plate)
        self.doc.recompute()
        self.ground(base)
        report = bm.bolt(self.asm, plate)
        got = {(round(p.screw.Placement.Base.x), p.line.head): (p.length, round(p.line.clamp, 6)) for p in report.placed}
        self.assertEqual(report.skipped, [], report.text())
        self.assertEqual(got[(10, "ISO4762")], ("16", 6.5))  # head on the counterbore floor: 6.5 + 12 >= 16
        self.assertIn((30, "ISO10642"), got)
        self.assertEqual(report.moved, [])

    def test_a_hole_in_one_part_only_is_skipped_with_a_reason(self):
        lonely = self.part("Lonely", block(holes=HOLES[:1], dia=3.4, height=5.0))
        self.ground(lonely)
        report = bm.bolt(self.asm, lonely)
        self.assertEqual(report.placed, [])
        self.assertIn("no part to bolt it to", report.skipped[0][1])

    def test_with_a_vault_one_cots_item_for_all_four(self):
        import TestBruceWorxHardware as th

        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        sync = prefs.GetBool("VaultStateSync", True)
        prefs.SetBool("VaultStateSync", False)
        self.addCleanup(prefs.SetBool, "VaultStateSync", sync)
        s = th._VaultSession(os.path.join(self.tmp, "vault"))
        base, mount = self.motor_mount()
        self.doc.saveAs(os.path.join(s.work.root, "mount.FCStd"))
        report = bm.bolt(self.asm, mount, session=s)
        self.assertEqual(len(report.placed), 4, report.text())
        pns = {p.screw.LinkedObject.PartNumber for p in report.placed}
        self.assertEqual(len(pns), 1)
        self.assertEqual(s.vault.items[pns.pop()].item_type, "cots")
        self.assertTrue(all(len(p.joints) == 1 for p in report.placed))
        self.assertEqual(report.moved, [])


@unittest.skipUnless(HAVE_FASTENERS, "the Fasteners add-on is not on the path")
class TestInfer(Base):
    def seated_screw(self, x, y, z, length="10"):
        screw = bh.make_fastener(self.doc, "ISO4762", "M3", length)
        self.asm.addObject(screw)
        screw.Placement = App.Placement(V(x, y, z), App.Rotation())
        self.doc.recompute()
        return screw

    def test_plate_screws_and_a_floating_screw(self):
        base = self.part("Base", block(holes=HOLES, dia=2.5, depth=10))
        mount = self.part("MotorMount", block(holes=HOLES, dia=3.4, height=5.0, z=20.0))
        screws = [self.seated_screw(x, y, 25.0) for x, y in HOLES[:3]]
        floating = self.seated_screw(*HOLES[3], 26.2)
        stray = self.part("Stray", Part.makeBox(5, 5, 5, V(100, 0, 0)))
        before = {o.Name: App.Placement(o.Placement) for o in (base, mount, stray, floating, *screws)}
        report = bm.infer(self.asm)
        self.assertIs(report.grounded, base)
        kinds = sorted(j.JointType for j, _what in report.joints)
        self.assertEqual(kinds, ["Distance", "Revolute", "Revolute", "Revolute"], report.text())
        unmated = dict(report.unmated)
        self.assertEqual(unmated[floating.Label], f"floating: 1.20 mm off its seat on {mount.Label}")
        self.assertIn("touches no other part", unmated["Stray"])
        self.assertEqual(report.moved, [])
        self.assertEqual(self.asm.solve(), 0)
        for obj in (base, mount, stray, floating, *screws):
            self.assertTrue(obj.Placement.isSame(before[obj.Name], 1e-7), obj.Label)

    def test_rail_and_carriage_make_a_slider(self):
        rail = self.part("Rail", Part.makeBox(200, 20, 10))
        carriage = self.part("Carriage", Part.makeBox(30, 30, 10, V(50, -10, 10)).fuse(
            Part.makeBox(30, 10, 10, V(50, -10, 0))).removeSplitter())
        report = bm.infer(self.asm)
        self.assertEqual(sorted(j.JointType for j, _w in report.joints), ["Distance", "Distance"], report.text())
        self.assertEqual(report.moved, [])
        # along the rail it stays where it is put; lifted off, the solver puts it back on
        carriage.Placement = App.Placement(V(40, 0, 0), App.Rotation())
        self.asm.solve()
        self.assertAlmostEqual(carriage.Placement.Base.x, 40, places=4)
        carriage.Placement = App.Placement(V(40, 0, 3), App.Rotation())
        self.asm.solve()
        self.assertAlmostEqual(carriage.Placement.Base.z, 0, places=4)
        self.assertAlmostEqual(carriage.Placement.Base.y, 0, places=4)

    def test_a_pin_makes_a_hinge(self):
        lug = self.part("Lug", Part.makeBox(20, 20, 5).cut(Part.makeCylinder(1.5, 7, V(10, 10, -1))))
        arm = self.part("Arm", Part.makeBox(40, 20, 5, V(0, 0, 5)).cut(Part.makeCylinder(1.5, 7, V(10, 10, 4))))
        pin = self.part("Pin", Part.makeCylinder(1.5, 10, V(10, 10, 0)))
        report = bm.infer(self.asm, ground_comp=lug)
        types = sorted(j.JointType for j, _w in report.joints)
        self.assertIn("Cylindrical", types, report.text())
        self.assertEqual(report.moved, [])
        self.assertIn(pin, {c for c in (lug, arm, pin)})


@unittest.skipUnless(App.GuiUp and HAVE_FASTENERS, "needs the GUI and the Fasteners add-on")
class TestGui(Base):
    def setUp(self):
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        super().setUp()
        import bruceworx_mating_gui as gui

        self.gui = gui
        gui.install()
        pump()

    def test_commands_are_registered(self):
        import FreeCADGui as Gui

        for name in self.gui.COMMANDS:
            self.assertIn(name, Gui.listCommands())

    def test_bolt_up_is_one_undo_step(self):
        self.doc.UndoMode = 1
        base, mount = self.motor_mount()
        before = len(self.doc.UndoNames)
        report = self.gui.run_bolt(self.asm, mount, session=None)
        pump()
        self.assertEqual(len(report.placed), 4)
        self.assertEqual(len(self.doc.UndoNames), before + 1)
        self.assertEqual(self.doc.UndoNames[0], "Bolt Up")
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual([o for o in self.asm.Group if bh.is_fastener(o)], [])

    def test_infer_is_one_undo_step(self):
        self.doc.UndoMode = 1
        self.part("Base", block())
        self.part("Top", Part.makeBox(10, 10, 10, V(0, 0, 20)))
        before = len(self.doc.UndoNames)
        report = self.gui.run_infer(self.asm)
        pump()
        self.assertEqual(len(report.joints), 1)
        self.assertEqual(len(self.doc.UndoNames), before + 1)
        self.assertEqual(self.doc.UndoNames[0], "Infer Mates")
