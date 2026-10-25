# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for bruceworx_pattern (BW-MOD-01).

The math tests run in plain Python. The document tests need FreeCAD and are
skipped without it:

    FreeCADCmd -t TestBruceWorxPattern
    python3 -m unittest TestBruceWorxPattern      # math only
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_pattern as bp

try:
    import FreeCAD as App
    import Part
except ImportError:  # plain Python
    App = None


def _rect(**kw):
    params = {
        "kind": bp.RECTANGULAR, "count1": 3, "count2": 1, "distance1": 10.0, "distance2": 10.0,
        "type1": "Spacing", "type2": "Spacing", "symmetric1": False, "symmetric2": False,
    }
    params.update(kw)
    return params


class MathTests(unittest.TestCase):
    def test_index_set(self):
        self.assertEqual(bp.index_set(4), [0, 1, 2, 3])
        self.assertEqual(bp.index_set(5, True), [-2, -1, 0, 1, 2])
        self.assertEqual(bp.index_set(4, True), [-1, 0, 1, 2])
        self.assertEqual(bp.index_set(1, True), [0])
        self.assertEqual(bp.index_set(0), [0])

    def test_step(self):
        self.assertEqual(bp.step(4, 30.0, "Extent"), 10.0)
        self.assertEqual(bp.step(4, 30.0, "Spacing"), 30.0)
        self.assertEqual(bp.step(1, 30.0, "Extent"), 0.0)

    def test_rectangular_offsets(self):
        offsets = bp.rectangular_offsets(_rect(count2=2, distance2=5.0))
        self.assertEqual(len(offsets), 6)
        self.assertEqual(offsets[(2, 1)], (20.0, 5.0))
        sym = bp.rectangular_offsets(_rect(symmetric1=True))
        self.assertEqual(sorted(sym), [(-1, 0), (0, 0), (1, 0)])

    def test_circular_angles(self):
        full = bp.circular_angles({"count1": 8, "circular_type": "Full", "angle": 360.0})
        self.assertEqual(len(full), 8)
        self.assertAlmostEqual(full[(1,)], 45.0)
        self.assertAlmostEqual(full[(7,)], 315.0)  # 360 is the seed, never repeated
        angle = bp.circular_angles({"count1": 3, "circular_type": "Angle", "angle": 90.0})
        self.assertEqual(sorted(angle.values()), [0.0, 45.0, 90.0])
        sym = bp.circular_angles({"count1": 3, "circular_type": "Symmetric", "angle": 90.0})
        self.assertEqual(sorted(sym.values()), [-45.0, 0.0, 45.0])

    def test_keys(self):
        self.assertEqual(bp.text_to_key(bp.key_to_text((-1, 2))), (-1, 2))
        self.assertEqual(bp.text_to_key("3"), (3,))

    def test_held_entries(self):
        self.assertEqual(bp.instance_name((2,)), "#2")
        self.assertEqual(bp.instance_name((1, -1)), "(1, -1)")
        entry = bp.held_entry((1, 0), "Pattern_i0", ["Joint", "Joint001"])
        self.assertEqual(entry, "(1, 0) Pattern_i0: Joint, Joint001")
        self.assertEqual(bp.parse_held_entry(entry), ("Pattern_i0", ["Joint", "Joint001"]))
        self.assertEqual(bp.parse_held_entry("#2 Pattern_i1: Joint"), ("Pattern_i1", ["Joint"]))
        added, gone = bp.held_changes(["#2 Pattern_i1: Joint"], ["#2 Pattern_i1: Joint, Joint001"])
        self.assertEqual(added, ["#2 Pattern_i1: Joint, Joint001"])
        self.assertEqual(gone, ["#2 Pattern_i1: Joint"])
        self.assertEqual(bp.held_changes(["a"], ["a"]), ([], []))


@unittest.skipIf(App is None, "needs FreeCAD")
class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWPatternTest")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _box(self, placement=None):
        box = self.doc.addObject("Part::Box", "Seed")
        box.Length = box.Width = box.Height = 4.0
        if placement is not None:
            box.Placement = placement
        self.doc.recompute()
        return box

    def _params(self, kind=bp.RECTANGULAR, **kw):
        params = bp.default_params(kind)
        params.update(kw)
        return params

    def test_rectangular_linked(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=3, count2=2, distance1=10, distance2=20))
        self.doc.recompute()
        self.assertEqual(array.TypeId, "App::Link")
        self.assertEqual(array.ElementCount, 5)  # 3 x 2 minus the seed
        self.assertEqual(len(array.Shape.Solids), 5)
        self.assertAlmostEqual(array.Shape.Volume, 5 * 64.0)
        base = sorted((round(e.Placement.Base.x), round(e.Placement.Base.y)) for e in array.ElementList)
        self.assertEqual(base, [(0, 20), (10, 0), (10, 20), (20, 0), (20, 20)])
        self.assertTrue(bp.is_pattern(array))
        self.assertEqual(bp.patterns_of(seed), [array])

    def test_suppression_hides_and_drops_from_shape(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=4, suppressed=[(2, 0)]))
        self.doc.recompute()
        self.assertEqual(array.ElementCount, 3)
        self.assertEqual(len(array.Shape.Solids), 2)
        keys = list(array.InstanceKeys)
        self.assertFalse(array.isElementVisible(str(keys.index("2,0"))))
        # Unsuppress: the element comes back without renumbering.
        array.Suppressed = []
        bp.apply(array)
        self.doc.recompute()
        self.assertEqual(len(array.Shape.Solids), 3)
        self.assertEqual(list(array.InstanceKeys), keys)

    def test_sync_visibility_after_redo(self):
        # A redo of the array's creation brings every instance back visible.
        seed = self._box()
        self.doc.UndoMode = 1
        self.doc.openTransaction("Pattern")
        array = bp.create_linked(seed, self._params(count1=3, count2=2, suppressed=[(1, 0)]))
        self.doc.commitTransaction()
        name = array.Name
        self.doc.undo()
        self.doc.redo()
        array = self.doc.getObject(name)
        self.assertTrue(bp.sync_visibility(array))
        self.doc.recompute()
        self.assertEqual(len(array.Shape.Solids), 4)
        self.assertFalse(bp.sync_visibility(array))

    def test_symmetric_spreads_both_sides(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=3, distance1=10, symmetric1=True))
        xs = sorted(round(e.Placement.Base.x) for e in array.ElementList)
        self.assertEqual(xs, [-10, 10])

    def test_extent_mode(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=5, distance1=40, type1="Extent"))
        xs = sorted(round(e.Placement.Base.x) for e in array.ElementList)
        self.assertEqual(xs, [10, 20, 30, 40])

    def test_rotated_seed_is_patterned_in_world_directions(self):
        rotated = App.Placement(App.Vector(5, 0, 0), App.Rotation(App.Vector(0, 0, 1), 90))
        seed = self._box(rotated)
        array = bp.create_linked(seed, self._params(count1=2, distance1=10))
        element = array.ElementList[0]
        self.assertTrue(element.Placement.Base.isEqual(App.Vector(15, 0, 0), 1e-9))
        self.assertAlmostEqual(element.Placement.Rotation.Angle, rotated.Rotation.Angle)

    def test_circular_full(self):
        seed = self._box(App.Placement(App.Vector(20, 0, 0), App.Rotation()))
        params = self._params(bp.CIRCULAR, count1=8, circular_type="Full")
        array = bp.create_linked(seed, params)
        self.doc.recompute()
        self.assertEqual(array.ElementCount, 7)
        for element in array.ElementList:
            self.assertAlmostEqual(element.Placement.Base.Length, 20.0, places=6)
        self.assertAlmostEqual(array.Shape.Volume, 7 * 64.0, places=6)

    def test_editing_count_and_type(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=3))
        array.PatternType = bp.CIRCULAR
        array.Count1 = 4
        bp.apply(array)
        self.assertEqual(array.ElementCount, 3)
        self.assertEqual(list(array.InstanceKeys), ["1", "2", "3"])

    def test_follows_seed_geometry(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=2, distance1=10))
        seed.Height = 8.0
        self.doc.recompute()
        self.assertAlmostEqual(array.Shape.Volume, 128.0)

    def test_save_and_reload_keeps_parameters(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=3, symmetric1=True, suppressed=[(1, 0)]))
        array_name, seed_name = array.Name, seed.Name
        path = os.path.join(App.getTempPath(), "bw_pattern_roundtrip.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        array = self.doc.getObject(array_name)
        self.assertTrue(bp.is_pattern(array))
        params = bp.read_params(array)
        self.assertTrue(params["symmetric1"])
        self.assertEqual(params["suppressed"], [(1, 0)])
        self.assertEqual(array.PatternSeed.Name, seed_name)
        self.assertEqual(array.ElementCount, 2)

    def test_independent_copies(self):
        seed = self._box()
        copies = bp.create_independent(seed, self._params(count1=4, suppressed=[(3, 0)]))
        self.doc.recompute()
        self.assertEqual(len(copies), 2)
        for copy in copies:
            self.assertEqual(copy.TypeId, "Part::Feature")
            self.assertAlmostEqual(copy.Shape.Volume, 64.0)
        seed.Height = 8.0
        self.doc.recompute()
        self.assertAlmostEqual(copies[0].Shape.Volume, 64.0)  # detached from the seed

    def test_make_independent_replaces_array(self):
        seed = self._box()
        array = bp.create_linked(seed, self._params(count1=3))
        name = array.Name
        copies = bp.make_independent(array)
        self.assertIsNone(self.doc.getObject(name))
        self.assertEqual(len(copies), 2)
        # No element objects of the old array are left behind.
        self.assertEqual([o.Name for o in self.doc.Objects if o.isDerivedFrom("App::LinkElement")], [])

    def test_make_independent_undoes_in_one_step(self):
        seed = self._box()
        self.doc.UndoMode = 1
        self.doc.openTransaction("Pattern")
        name = bp.create_linked(seed, self._params(count1=3)).Name
        self.doc.commitTransaction()
        before = sorted(o.Name for o in self.doc.Objects)
        self.doc.openTransaction("Independent")
        bp.make_independent(self.doc.getObject(name))
        self.doc.commitTransaction()
        self.doc.undo()
        self.assertEqual(sorted(o.Name for o in self.doc.Objects), before)
        array = self.doc.getObject(name)
        self.assertEqual(array.ElementCount, 2)
        self.assertEqual(len(array.ElementList), 2)

    def test_parallel_directions_rejected(self):
        with self.assertRaises(ValueError):
            bp.transforms(self._params(count2=2, direction2=App.Vector(2, 0, 0)))

    def test_assembly_sees_every_component(self):
        try:
            import UtilsAssembly
        except ImportError:
            self.skipTest("Assembly module not available")
        bolt = self.doc.addObject("Part::Cylinder", "Bolt")
        bolt.Radius, bolt.Height = 2.5, 20
        assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        component = self.doc.addObject("App::Link", "BoltLink")
        component.LinkedObject = bolt
        component.Placement = App.Placement(App.Vector(30, 0, 0), App.Rotation())
        assembly.addObject(component)
        self.doc.recompute()
        array = bp.create_linked(component, self._params(bp.CIRCULAR, count1=6))
        self.doc.recompute()
        self.assertIn(array, assembly.Group)
        self.assertIs(array.LinkedObject, bolt)
        self.assertTrue(UtilsAssembly.isLinkGroup(array))
        # The component itself plus the five generated instances.
        self.assertEqual(UtilsAssembly.number_of_components_in(assembly), 6)

    # ----- instances held by joints (step 15) ------------------------------

    def _bolt_assembly(self):
        """A grounded plate and a 6-bolt circular pattern of a component in an
        assembly; returns (assembly, array, JointObject)."""
        try:
            import JointObject
        except ImportError:
            self.skipTest("Assembly module not available")
        assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.joints = assembly.newObject("Assembly::JointGroup", "Joints")
        plate = assembly.newObject("Part::Box", "Plate")
        plate.Length = plate.Width = 80
        plate.Height = 5
        plate.Placement.Base = App.Vector(-40, -40, -5)
        ground = self.joints.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, plate)
        bolt = self.doc.addObject("Part::Cylinder", "BoltShape")
        bolt.Radius, bolt.Height = 3, 20
        component = self.doc.addObject("App::Link", "Bolt")
        component.LinkedObject = bolt
        component.Placement = App.Placement(App.Vector(25, 0, 0), App.Rotation())
        assembly.addObject(component)
        self.doc.recompute()
        array = bp.create_linked(component, self._params(bp.CIRCULAR, count1=6))
        self.doc.recompute()
        self.plate = plate
        return assembly, array, JointObject

    def _fix(self, JointObject, element):
        """A Fixed joint from the plate to ``element`` where it is now."""
        joint = self.joints.newObject("App::FeaturePython", "Fixed")
        JointObject.Joint(joint, JointObject.JointTypes.index("Fixed"))
        joint.KeepPosition = True
        joint.Proxy.setJointConnectors(joint, [[self.plate, ["Face6", "Vertex6"]], [element, ["Face1", "Edge1"]]])
        self.doc.recompute()
        return joint

    def _set_count(self, array, count):
        params = bp.read_params(array)
        params["count1"] = count
        bp.write_params(array, params)
        bp.apply(array)
        self.doc.recompute()

    def test_jointed_instance_stays_and_is_reported(self):
        assembly, array, JointObject = self._bolt_assembly()
        element = array.ElementList[1]  # instance #2, at 120 degrees
        before = App.Placement(element.Placement)
        joint = self._fix(JointObject, element)
        self.assertEqual(bp.holding_joints(element), [joint])
        self.assertEqual(bp.find_held(array), [])  # still in its place

        self._set_count(array, 8)  # #2 belongs at 90 degrees now
        self.assertTrue(element.Placement.isSame(before, 1e-6), element.Placement)
        held = bp.find_held(array)
        self.assertEqual([(k, e.Name, [j.Name for j in js]) for k, e, js in held],
                         [((2,), element.Name, [joint.Name])])
        self.assertEqual(list(array.HeldInstances), [f"#2 {element.Name}: {joint.Name}"])
        self.assertEqual(bp.held_message(array, *held[0]),
                         f"{array.Label}: instance #2 ({element.Name}) is held by joint {joint.Label} "
                         "and cannot follow the pattern")
        self.assertEqual(bp.held_by(joint), [(array, element.Name)])
        self.assertTrue(JointObject.heldOffPattern(joint))
        # The other instances moved to their new places.
        for (key, placement), other in zip(bp.instance_placements(bp.read_params(array), array.PatternSeed.Placement),
                                           array.ElementList):
            if other != element:
                self.assertTrue(bp.in_place(other.Placement, placement), key)
        # The solver agrees: nothing moves on solve, no fight.
        self.assertEqual(assembly.solve(), 0)
        self.assertTrue(element.Placement.isSame(before, 1e-5))
        bp.apply(array)
        self.assertTrue(element.Placement.isSame(before, 1e-5))

        self._set_count(array, 6)  # its place again
        self.assertEqual(bp.find_held(array), [])
        self.assertEqual(list(array.HeldInstances), [])
        self.assertFalse(JointObject.heldOffPattern(joint))

    def test_instance_follows_once_the_joint_is_gone(self):
        assembly, array, JointObject = self._bolt_assembly()
        element = array.ElementList[1]
        joint = self._fix(JointObject, element)
        self._set_count(array, 8)
        self.assertEqual(len(array.HeldInstances), 1)
        self.doc.removeObject(joint.Name)
        bp.apply(array)
        slot = dict(bp.instance_placements(bp.read_params(array), array.PatternSeed.Placement))[(2,)]
        self.assertTrue(bp.in_place(element.Placement, slot))
        self.assertEqual(list(array.HeldInstances), [])

    def test_suppressed_or_unconnected_joint_does_not_hold(self):
        assembly, array, JointObject = self._bolt_assembly()
        element = array.ElementList[1]
        joint = self._fix(JointObject, element)
        joint.Suppressed = True
        self.assertEqual(bp.holding_joints(element), [])
        # A joint between two instances that nothing grounds: the solver leaves them alone.
        free = self.joints.newObject("App::FeaturePython", "Free")
        JointObject.Joint(free, JointObject.JointTypes.index("Fixed"))
        free.KeepPosition = True
        free.Proxy.setJointConnectors(free, [[array.ElementList[2], ["Face1", "Edge1"]],
                                             [array.ElementList[3], ["Face1", "Edge1"]]])
        self.doc.recompute()
        self.assertEqual(bp.holding_joints(array.ElementList[2]), [])
        self._set_count(array, 8)
        self.assertEqual(bp.find_held(array), [])

    def test_grounded_instance_is_held(self):
        assembly, array, JointObject = self._bolt_assembly()
        element = array.ElementList[0]
        before = App.Placement(element.Placement)
        ground = self.joints.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, element)
        self._set_count(array, 4)
        self.assertTrue(element.Placement.isSame(before, 1e-9))
        self.assertEqual(list(array.HeldInstances), [f"#1 {element.Name}: {ground.Name}"])

    def test_feature_body(self):
        body = self.doc.addObject("PartDesign::Body", "Body")
        other = self.doc.addObject("PartDesign::Body", "Other")
        pad = self.doc.addObject("PartDesign::AdditiveBox", "Box")
        cyl = self.doc.addObject("PartDesign::AdditiveCylinder", "Cylinder")
        far = self.doc.addObject("PartDesign::AdditiveBox", "Far")
        body.addObject(pad)
        body.addObject(cyl)
        other.addObject(far)
        self.assertEqual(bp.feature_body([pad, cyl]).Name, body.Name)
        self.assertIsNone(bp.feature_body([pad, far]))
        self.assertIsNone(bp.feature_body([self._box()]))

    def test_classify_selection(self):
        body = self.doc.addObject("PartDesign::Body", "Body")
        pad = self.doc.addObject("PartDesign::AdditiveBox", "Box")
        body.addObject(pad)
        box = self._box()
        self.assertEqual(bp.classify_selection([]), "empty")
        self.assertEqual(bp.classify_selection([pad]), "features")
        self.assertEqual(bp.classify_selection([body, box]), "objects")
        self.assertEqual(bp.classify_selection([pad, box]), "mixed")


@unittest.skipUnless(App is not None and App.GuiUp, "needs a GUI FreeCAD")
class GuiAcceptanceTests(unittest.TestCase):
    """MOD-01 acceptance in the GUI: the figures' patterns made with the two commands and their dialog,
    undo/redo, save/reload, and the Assembly workbench seeing every component instance."""

    def setUp(self):
        import FreeCADGui as Gui
        import bruceworx_pattern_gui as bpg

        self.Gui, self.bpg = Gui, bpg
        self.doc = App.newDocument("BWPatternGui")
        self.doc.UndoMode = 1
        self.panels = []
        test = self
        self._panel_class = bpg.PatternTaskPanel

        class Recorded(self._panel_class):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                test.panels.append(self)

        bpg.PatternTaskPanel = Recorded

    def tearDown(self):
        self.bpg.PatternTaskPanel = self._panel_class
        if self.Gui.Control.activeDialog():
            self.Gui.Control.closeDialog()
        self.Gui.Selection.clearSelection()
        for name in list(App.listDocuments()):
            if name.startswith("BWPatternGui") or name.startswith("bw_pattern_gui"):
                App.closeDocument(name)

    def _pump(self, n=4):
        for _ in range(n):
            self.Gui.updateGui()

    def _run(self, kind, *objects):
        """Select ``objects`` and run the BruceWorx command; the panel it opened (None for features)."""
        self.Gui.Selection.clearSelection()
        for obj in objects:
            self.Gui.Selection.addSelection(obj)
        before = len(self.panels)
        self.Gui.runCommand(self.bpg.COMMANDS[kind])
        self._pump()
        self.assertTrue(self.Gui.Control.activeDialog(), f"{kind} Pattern opened no task panel")
        return self.panels[-1] if len(self.panels) > before else None

    def _ok(self, panel):
        panel.accept()
        self._pump()
        self.assertFalse(self.Gui.Control.activeDialog())
        return panel.arrays[0]

    def _body(self, name="Bracket", w=8.0, h=6.0):
        import Part
        import Sketcher

        V = App.Vector
        body = self.doc.addObject("PartDesign::Body", name)
        sk = body.newObject("Sketcher::SketchObject", name + "Sketch")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        pts = [V(0, 0, 0), V(w, 0, 0), V(w, h, 0), V(0, h, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        pad = body.newObject("PartDesign::Pad", name + "Pad")
        pad.Profile = sk
        pad.Length = 3
        self.doc.recompute()
        return body, pad

    def test_feature_patterns_use_partdesign(self):
        """Figures (a) and (b): the commands on a feature open PartDesign's own patterns."""
        import Part

        body, _pad = self._body("Plate", 60, 40)
        V = App.Vector
        sk = body.newObject("Sketcher::SketchObject", "HoleSketch")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(V(6, 6, 0), V(0, 0, 1), 2))
        hole = body.newObject("PartDesign::Pocket", "Hole")
        hole.Profile = sk
        hole.Type = "ThroughAll"
        hole.SideType = "Symmetric"  # through the plate whichever way the sketch faces
        self.doc.recompute()
        v0 = body.Shape.Volume
        self.assertIsNone(self._run(self.bpg.bp.RECTANGULAR, hole))
        linear = [o for o in self.doc.Objects if o.TypeId == "PartDesign::LinearPattern"]
        self.assertEqual(len(linear), 1)
        pattern = linear[0]
        pattern.Direction = (body.Origin.OriginFeatures[0], [""])
        pattern.Direction2 = (body.Origin.OriginFeatures[1], [""])
        pattern.Mode = pattern.Mode2 = "Spacing"
        pattern.Occurrences, pattern.Occurrences2 = 4, 3
        pattern.Offset, pattern.Offset2 = 15, 12
        self.Gui.Control.closeDialog()
        self.Gui.ActiveDocument.resetEdit()
        self._pump()
        self.doc.recompute()
        full = 60 * 40 * 3
        self.assertAlmostEqual((full - pattern.Shape.Volume) / (full - v0), 12, places=3)  # 4 x 3 holes

    def test_bodies_rectangular_with_suppression_undo_redo_and_reload(self):
        """Figure (c): 3 x 2 brackets as a link array; then suppression, undo/redo and save/reload."""
        body, _pad = self._body()
        panel = self._run(self.bpg.bp.RECTANGULAR, body)
        panel.dir1["count"].setValue(3)
        panel.dir1["distance"].setValue(12)
        panel.dir2["box"].setChecked(True)
        panel.dir2["count"].setValue(2)
        panel.dir2["distance"].setValue(10)
        self._pump()
        # uncheck one instance in the list: suppressed (figure 'new', left)
        item = panel.instances.item(1)
        item.setCheckState(self.bpg._qt()[0].Qt.Unchecked)
        self._pump()
        array = self._ok(panel)
        name = array.Name
        self.assertEqual(array.ElementCount, 5)
        self.assertEqual(len(array.Shape.Solids), 4)
        self.assertEqual(len(bp.read_params(array)["suppressed"]), 1)
        self.assertIn("Rectangular Pattern", self.doc.UndoNames[0])
        self.doc.undo()
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(name))
        self.doc.redo()
        self._pump()  # the observer hides the suppressed instance again
        self.doc.recompute()
        array = self.doc.getObject(name)
        self.assertIsNotNone(array)
        self.assertEqual(len(array.Shape.Solids), 4)
        path = os.path.join(App.getTempPath(), "bw_pattern_gui.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.recompute()
        array = self.doc.getObject(name)
        self.assertTrue(bp.is_pattern(array))
        self.assertEqual(len(array.Shape.Solids), 4)
        self.assertEqual(len(bp.read_params(array)["suppressed"]), 1)

    def test_symmetric_direction(self):
        """Figure 'new', right: n = 5 symmetric gives -2..2 spacings."""
        body, _pad = self._body()
        panel = self._run(self.bpg.bp.RECTANGULAR, body)
        panel.dir1["count"].setValue(5)
        panel.dir1["distance"].setValue(10)
        panel.dir1["symmetric"].setChecked(True)
        self._pump()
        array = self._ok(panel)
        xs = sorted(round(e.Placement.Base.x) for e in array.ElementList)
        self.assertEqual(xs, [-20, -10, 10, 20])

    def test_components_circular_seen_by_assembly(self):
        """Figure (d): 8 bolts around the flange axis; the Assembly workbench counts every instance."""
        import UtilsAssembly

        bolt = self.doc.addObject("Part::Cylinder", "BoltShape")
        bolt.Radius, bolt.Height = 2.5, 20
        assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        component = self.doc.addObject("App::Link", "Bolt")
        component.LinkedObject = bolt
        component.Placement = App.Placement(App.Vector(30, 0, 0), App.Rotation())
        assembly.addObject(component)
        self.doc.recompute()
        panel = self._run(self.bpg.bp.CIRCULAR, component)
        self.assertEqual(panel.kind.currentText(), bp.CIRCULAR)
        panel.circ_count.setValue(8)
        self._pump()
        array = self._ok(panel)
        self.assertIn(array, assembly.Group)
        self.assertEqual(UtilsAssembly.number_of_components_in(assembly), 8)
        angles = sorted(round(e.Placement.Rotation.Angle * 180 / 3.141592653589793) for e in array.ElementList)
        self.assertEqual(len(angles), 7)
        self.assertEqual(angles[0], 45)


if __name__ == "__main__":
    unittest.main()
