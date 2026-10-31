# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing objects headless (BW-ROU-01..04): a path through four points, a polyline path with
tangent arcs, a wire with slack and its mass, a pipe whose cut length and bend table match the hand
calculation, a hose that bends too tight, attached points that follow their part, the two CSV
reports, the Browser badge, and save/reload.

Run: FreeCADCmd -t TestBruceWorxRoutingCore
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part  # Part::FeaturePython; Part.Vertex in the fitting test

import bruceworx_routing as br
from bruceworx import routing as rt

V = App.Vector
THREE_BEND = [(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100), (100, 0, 100)]


class RoutingCoreTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWRoutingCore")

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("BWRoutingCore"):
                App.closeDocument(name)

    # ----- paths -----

    def test_spline_path_through_four_points(self):
        pts = [(0, 0, 0), (40, 10, 0), (80, -10, 5), (120, 0, 0)]
        path = br.make_path(pts, self.doc)
        self.doc.recompute()
        self.assertEqual(br.kind_of(path), br.PATH)
        self.assertEqual(len(path.Shape.Wires), 1)
        self.assertEqual(path.Bends, 2)
        chords = sum(rt.dist(pts[i], pts[i + 1]) for i in range(3))
        self.assertGreater(path.Length.Value, chords)
        self.assertAlmostEqual(path.Length.Value / rt.spline_length(pts), 1.0, delta=1e-6)  # one curve
        self.assertGreater(path.MinRadius.Value, 0.0)
        # The shape is the module's spline: it passes the points at their chord parameters with the
        # module's first derivative, and its curvature is the module's.
        spline = rt.Spline(pts)
        edge = path.Shape.Edges[0]
        self.assertEqual([round(k, 9) for k in edge.Curve.getKnots()], [round(t, 9) for t in spline.params])
        for i in range(spline.segments):
            for s in (0.0, 0.3, 0.8):
                u = spline.params[i] + s * (spline.params[i + 1] - spline.params[i])
                r, r1, r2 = spline.at(i, s)
                self.assertAlmostEqual((edge.valueAt(u) - V(*r)).Length, 0.0, places=7)
                self.assertAlmostEqual((edge.derivative1At(u) - V(*r1)).Length, 0.0, places=7)
                self.assertAlmostEqual(edge.curvatureAt(u), rt.curvature(r1, r2), places=9)
        self.assertTrue(path.BendOK)
        self.assertEqual(path.BendTable, "")

    def test_polyline_path_with_tangent_arcs(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        self.doc.recompute()
        self.assertAlmostEqual(path.Shape.Length, 160.0 + 10.0 * math.pi, places=5)
        self.assertAlmostEqual(path.Length.Value, 160.0 + 10.0 * math.pi, places=5)
        self.assertEqual(len(path.Shape.Edges), 3)
        self.assertEqual(path.Bends, 1)
        self.assertAlmostEqual(path.MinRadius.Value, 20.0)
        self.assertEqual(path.BendTable.splitlines(), ["1  L 80.0  R 0.0  A 90.0", "end  L 80.0"])
        path.MinBendRadius = 30.0
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        self.assertIn("below the minimum", path.Problems)
        path.BendRadius = 120.0  # does not fit the 100 mm legs: drawn sharp, reported
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        self.assertEqual(path.Bends, 0)
        self.assertEqual(len(path.Shape.Edges), 2)

    def test_spline_path_min_radius_check(self):
        pts = [(50 * math.cos(k * math.pi / 8), 50 * math.sin(k * math.pi / 8), 0) for k in range(9)]
        path = br.make_path(pts, self.doc)
        self.doc.recompute()
        # 30 Oct: one spline. The drawn semicircle and the check agree; the not-a-knot ends are a little
        # rounder than the OCC interpolation drawn before.
        self.assertAlmostEqual(path.MinRadius.Value, rt.spline_min_radius(pts), places=6)
        self.assertAlmostEqual(br.wire_min_radius(path.Shape.Wires[0], samples=512) / path.MinRadius.Value, 1.0, delta=1e-3)
        self.assertAlmostEqual(path.MinRadius.Value, 45.78, places=2)  # OCC interpolate gave 44.9
        self.assertAlmostEqual(path.Length.Value / (math.pi * 50.0), 1.0, delta=0.01)
        path.MinBendRadius = 60.0
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        path.MinBendRadius = 40.0
        self.doc.recompute()
        self.assertTrue(path.BendOK)

    def test_attached_points_follow_their_part(self):
        box = self.doc.addObject("Part::Box", "Box")
        box.Placement = App.Placement(V(50, 0, 0), App.Rotation())
        self.doc.recompute()
        corner = box.Shape.Vertexes[0].Point  # global: the box is top level
        path = br.make_path([corner - V(50, 0, 0), (999, 999, 999)], self.doc, attachments=[(1, box, "Vertex1")])
        self.doc.recompute()
        self.assertEqual(br.attachments_of(path), [(1, box, "Vertex1")])
        self.assertAlmostEqual((path.Points[1] - corner).Length, 0.0)
        self.assertAlmostEqual(path.Length.Value, 50.0)
        box.Placement = App.Placement(V(80, 60, 0), App.Rotation())
        self.doc.recompute()
        self.assertAlmostEqual((path.Points[1] - (corner + V(30, 60, 0))).Length, 0.0)
        self.assertAlmostEqual(path.Length.Value, 100.0)
        # A circular edge attaches at its centre; an object without a sub-element at its origin.
        cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        cyl.Placement = App.Placement(V(0, 0, 30), App.Rotation())
        self.doc.recompute()
        circle = next(f"Edge{i + 1}" for i, e in enumerate(cyl.Shape.Edges) if hasattr(e.Curve, "Center"))
        centre = br.attachment_point(cyl, circle)
        self.assertAlmostEqual(centre.x, 0.0)
        self.assertAlmostEqual(centre.y, 0.0)
        self.assertIn(round(centre.z, 6), (30.0, 40.0))
        self.assertAlmostEqual((br.attachment_point(cyl) - V(0, 0, 30)).Length, 0.0)
        # Several attachments on one object, out of order, keep their point indices.
        path2 = br.make_path([(0, 0, 0), (1, 1, 1), (2, 2, 2)], self.doc,
                             attachments=[(2, box, "Vertex1"), (0, cyl, circle), (1, box, "Vertex7")])
        self.doc.recompute()
        self.assertEqual([(i, o.Name, s) for i, o, s in br.attachments_of(path2)],
                         [(2, box.Name, "Vertex1"), (0, cyl.Name, circle), (1, box.Name, "Vertex7")])
        self.assertAlmostEqual((path2.Points[2] - box.Shape.Vertexes[0].Point).Length, 0.0)
        self.assertAlmostEqual((path2.Points[1] - box.Shape.Vertexes[6].Point).Length, 0.0)
        self.assertAlmostEqual((path2.Points[0] - centre).Length, 0.0)

    # ----- items -----

    def test_wire_with_slack_and_mass(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, gauge="22 AWG", colour=(1.0, 0.0, 0.0), signal="+12V", from_connector="J1",
                            from_pin="1", to_connector="M1", to_pin="+", slack=5)
        self.doc.recompute()
        self.assertEqual(br.kind_of(wire), br.WIRE)
        length = (160.0 + 10.0 * math.pi) * 1.05
        self.assertAlmostEqual(wire.Length.Value, length, places=5)
        self.assertEqual(wire.QuantityUnit, "m")
        self.assertAlmostEqual(wire.QuantityPer, length / 1000.0, places=9)
        od = rt.default_insulation_od("22 AWG")
        self.assertAlmostEqual(wire.InsulationOD.Value, od)
        self.assertAlmostEqual(wire.Mass.Value * 1000.0, rt.wire_mass("22 AWG", length, od), places=6)
        self.assertTrue(wire.Shape.isValid())
        self.assertEqual(len(wire.Shape.Solids), 1)
        # ROU-08: the wire is a rope of its cut length (slack included), not the path
        self.assertAlmostEqual(wire.Shape.Volume / (math.pi * od * od / 4 * wire.Length.Value), 1.0, delta=0.03)
        self.assertTrue(wire.BendOK)
        self.assertAlmostEqual(wire.MinBendRadius.Value, 4.0 * od)  # 4 x OD by default
        wire.Slack = 10
        self.doc.recompute()
        self.assertAlmostEqual(wire.Length.Value, (160.0 + 10.0 * math.pi) * 1.10, places=5)

    def test_pipe_cut_length_and_bend_table(self):
        path = br.make_path(THREE_BEND, self.doc, mode="Polyline", radius=20.0)
        pipe = br.make_pipe(path, size="12 x 1", bend_radius=20.0, material="304")
        self.doc.recompute()
        self.assertEqual(br.kind_of(pipe), br.PIPE)
        self.assertAlmostEqual(pipe.OD.Value, 12.0)
        self.assertAlmostEqual(pipe.WallThickness.Value, 1.0)
        cut = 280.0 + 3 * 10.0 * math.pi  # by hand: 80 + 60 + 60 + 80 straights, three quarter-circles of R 20
        self.assertAlmostEqual(pipe.MinBendRadius.Value, 18.0)  # 1.5 x OD by default
        self.assertAlmostEqual(pipe.CutLength.Value, cut, places=5)
        self.assertAlmostEqual(pipe.Length.Value, cut, places=5)
        self.assertAlmostEqual(pipe.QuantityPer, cut / 1000.0, places=9)
        self.assertEqual(pipe.Bends, 3)
        self.assertEqual(pipe.BendTable.splitlines(),
                         ["1  L 80.0  R 0.0  A 90.0", "2  L 60.0  R 90.0  A 90.0", "3  L 60.0  R 0.0  A 90.0", "end  L 80.0"])
        annulus = math.pi * (6.0**2 - 5.0**2)
        self.assertAlmostEqual(pipe.Shape.Volume / (annulus * cut), 1.0, delta=0.03)
        self.assertTrue(pipe.BendOK)
        # A tighter CLR than the pipe allows: flagged; the path geometry follows the pipe's CLR.
        pipe.MinBendRadius = 30.0
        self.doc.recompute()
        self.assertFalse(pipe.BendOK)
        pipe2 = br.make_pipe(path, od=10.0, wall=1.0, bend_radius=30.0)
        self.doc.recompute()
        self.assertAlmostEqual(path.BendRadius.Value, 30.0)
        self.assertAlmostEqual(pipe2.CutLength.Value, 280.0 - 3 * 20.0 + 3 * 15.0 * math.pi, places=5)
        self.assertEqual(pipe2.Size, "Custom")
        pipe2.Size = "16 x 1.5"
        self.assertAlmostEqual(pipe2.OD.Value, 16.0)

    def test_hose_too_tight(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        hose = br.make_hose(path, inner=6.0, od=10.0, slack=5)
        self.doc.recompute()
        self.assertEqual(br.kind_of(hose), br.HOSE)
        self.assertAlmostEqual(hose.MinBendRadius.Value, 60.0)
        self.assertTrue(hose.BendOK)  # ROU-08: the hose is a rope; at its 285 mm it relaxes to R >= 60
        hose.CutLength = 150.0  # the ends are 141 apart: 150 mm can only arc at R 127, so R 300 cannot be held
        hose.MinBendRadius = 300.0
        self.doc.recompute()
        self.assertFalse(hose.BendOK)
        self.assertIn(("bend too tight", "warn"), br.row_badges(hose, "", [])[1])
        self.assertEqual(br.row_badges(path, "", [])[1], [])
        self.assertAlmostEqual(hose.Length.Value, 150.0, places=5)
        self.assertEqual(len(hose.Shape.Solids), 1)
        hose.CutLength = (160.0 + 10.0 * math.pi) * 1.05
        hose.MinBendRadius = 15.0
        self.doc.recompute()
        self.assertTrue(hose.BendOK)
        self.assertNotIn(("bend too tight", "warn"), br.row_badges(hose, "", [])[1])

    def test_bundle_od(self):
        path = br.make_path([(0, 0, 0), (50, 0, 0), (100, 20, 0)], self.doc)
        wires = [br.make_wire(path, gauge="22 AWG", insulation_od=1.6) for _ in range(3)]
        bundle = br.make_bundle(wires)
        self.doc.recompute()
        self.assertIs(bundle.Path, path)
        self.assertAlmostEqual(bundle.OD.Value, 1.15 * math.sqrt(3 * 1.6**2), places=6)
        self.assertAlmostEqual(bundle.Length.Value, path.Length.Value)

    # ----- bend rules (ROU-02, 30 Oct) -----

    def test_bend_rules_per_item(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, gauge="22 AWG", insulation_od=2.0)
        self.doc.recompute()
        self.assertEqual(wire.BendRule, "Default")
        self.assertAlmostEqual(wire.MinBendRadius.Value, 8.0)  # Hook-up PVC, 4 x OD
        self.assertEqual(wire.BendRuleUsed, "Default: Hook-up PVC, 4 × OD")
        wire.BendRule = "Coax"
        self.doc.recompute()
        self.assertAlmostEqual(wire.MinBendRadius.Value, 20.0)
        self.assertTrue(wire.BendOK)  # the path's R 20 just meets 10 x 2.0
        wire.InsulationOD = 2.5
        self.doc.recompute()
        self.assertAlmostEqual(wire.MinBendRadius.Value, 25.0)  # follows the OD
        self.assertTrue(wire.BendOK)  # ROU-08: a rope of 210 mm relaxes the path's R 20 corner to R >= 25
        wire.MinBendRadius = 7.0  # typed by hand: a fixed override
        self.assertEqual(wire.BendRule, "Fixed radius")
        self.doc.recompute()
        self.assertAlmostEqual(wire.MinBendRadius.Value, 7.0)
        self.assertEqual(wire.BendRuleUsed, "7 mm")
        self.assertTrue(wire.BendOK)
        wire.BendFactor = 9.0  # typed by hand: a multiple of OD
        self.assertEqual(wire.BendRule, "Multiple of OD")
        self.doc.recompute()
        self.assertAlmostEqual(wire.MinBendRadius.Value, 22.5)
        wire.BendRule = "No check"
        self.doc.recompute()
        self.assertEqual(wire.MinBendRadius.Value, 0.0)
        self.assertTrue(wire.BendOK)
        hose = br.make_hose(path, inner=6.0, od=10.0)
        pipe = br.make_pipe(path, size="12 x 1")
        self.doc.recompute()
        self.assertAlmostEqual(hose.MinBendRadius.Value, 60.0)
        self.assertEqual(hose.BendRuleUsed, "Default: General hose, 6 × OD")
        self.assertAlmostEqual(pipe.BendRadius.Value, 24.0)  # CLR 2 x OD
        self.assertAlmostEqual(pipe.MinBendRadius.Value, 18.0)  # 1.5 x OD
        self.assertEqual(br.make_hose(path, od=10.0, min_bend_radius=30.0).BendRule, "Fixed radius")

    def test_preferences_set_the_default(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, insulation_od=2.0)
        hose = br.make_hose(path, od=10.0)
        try:
            br.set_prefs({"WireRule": "Silicone", "HoseRule": "Fixed radius", "HoseRadius": 15.0, "TubeClrFactor": 3.0})
            self.assertEqual(br.prefs()["WireRule"], "Silicone")
            wire.touch()
            hose.touch()
            self.doc.recompute()
            self.assertAlmostEqual(wire.MinBendRadius.Value, 6.0)
            self.assertAlmostEqual(hose.MinBendRadius.Value, 15.0)
            self.assertEqual(hose.BendRule, "Default")  # still following the preference
            self.assertTrue(hose.BendOK)
            pipe = br.make_pipe(br.make_path([(0, 0, 0), (100, 0, 0)], self.doc), od=10.0, wall=1.0)
            self.assertAlmostEqual(pipe.BendRadius.Value, 30.0)
        finally:
            br.reset_prefs()
        self.assertEqual(br.prefs()["WireRule"], "Hook-up PVC")

    def test_bundle_takes_the_strictest_wire_rule(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=40.0)
        wires = [br.make_wire(path, insulation_od=1.6) for _ in range(3)]
        bundle = br.make_bundle(wires)
        self.doc.recompute()
        od = bundle.OD.Value
        self.assertEqual(bundle.BendRule, "From wires")
        self.assertAlmostEqual(bundle.MinBendRadius.Value, 4.0 * od)
        wires[1].BendRule = "Coax"
        self.doc.recompute()
        self.assertAlmostEqual(bundle.MinBendRadius.Value, 10.0 * od)
        self.assertEqual(bundle.BendRuleUsed, "From wires: 10 × OD")
        self.assertTrue(bundle.BendOK)  # 10 x 3.19 = 31.9 < R 40
        wires[2].MinBendRadius = 500.0  # ROU-08: a rope relaxes a path's corner; 500 cannot be held between these ends
        self.doc.recompute()
        self.assertAlmostEqual(bundle.MinBendRadius.Value, 500.0)
        self.assertFalse(bundle.BendOK)
        bundle.MinBendRadius = 12.0  # set on the bundle: its own
        self.doc.recompute()
        self.assertEqual(bundle.BendRule, "Fixed radius")
        self.assertTrue(bundle.BendOK)

    def test_items_saved_before_bend_rules_migrate(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        set_wire = br.make_wire(path, insulation_od=2.0, name="SetWire")
        bare_wire = br.make_wire(path, insulation_od=2.0, name="BareWire")
        hose = br.make_hose(path, od=10.0, name="OldHose")
        hose2 = br.make_hose(path, od=10.0, name="TunedHose")
        pipe = br.make_pipe(path, size="12 x 1", name="OldPipe")
        self.doc.recompute()
        old = {"SetWire": 7.0, "BareWire": 0.0, "OldHose": 60.0, "TunedHose": 25.0, "OldPipe": 18.0}
        for name, radius in old.items():  # as the 27 Oct code saved them
            obj = self.doc.getObject(name)
            for prop in ("BendRule", "BendFactor", "BendRuleUsed"):
                obj.removeProperty(prop)
            obj.MinBendRadius = radius
        folder = tempfile.mkdtemp(prefix="bw-routing-")
        try:
            file = os.path.join(folder, "old.FCStd")
            self.doc.saveAs(file)
            App.closeDocument(self.doc.Name)
            self.doc = doc = App.openDocument(file)
            got = {n: (doc.getObject(n).BendRule, round(doc.getObject(n).MinBendRadius.Value, 6)) for n in old}
            self.assertEqual(got, {"SetWire": ("Fixed radius", 7.0), "BareWire": ("Default", 0.0),
                                   "OldHose": ("Multiple of OD", 60.0), "TunedHose": ("Fixed radius", 25.0),
                                   "OldPipe": ("Multiple of OD", 18.0)})
            self.assertEqual(doc.getObject("OldHose").BendFactor, 6.0)
            doc.recompute()
            got = {n: round(doc.getObject(n).MinBendRadius.Value, 6) for n in old}
            self.assertEqual(got, {"SetWire": 7.0, "BareWire": 8.0, "OldHose": 60.0, "TunedHose": 25.0, "OldPipe": 18.0})
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # ----- reports, reload -----

    def test_reports(self):
        path = br.make_path(THREE_BEND, self.doc, mode="Polyline", radius=20.0)
        br.make_wire(path, gauge="18 AWG", colour=(0, 0, 0), signal="GND", from_connector="J1", from_pin="2",
                     to_connector="M1", to_pin="-", name="W2")
        br.make_pipe(path, size="12 x 1", bend_radius=20.0, name="Line")
        br.make_hose(path, inner=6.0, od=10.0, name="Return")
        self.doc.recompute()
        lines = br.from_to_csv(self.doc).splitlines()
        self.assertEqual(lines[0], "Wire,Signal,Gauge,Colour,From,From pin,From mate,From strip (mm),To,To pin,To mate,"
                                   "To strip (mm),Length (m)")
        self.assertEqual(lines[1], f"W2,GND,18 AWG,black,J1,2,,,M1,-,,,{(280 + 30 * math.pi) * 1.05 / 1000:.3f}")
        lines = br.cut_list_csv(self.doc).splitlines()
        self.assertEqual(lines[0], "Item,Kind,Size,Cut length (mm),Bend radius,Bends,Bend table")
        self.assertEqual(lines[1], f"Line,Pipe,12 x 1,{280 + 30 * math.pi:.1f},20,3,"
                                   "L80.0 R0.0 A90.0; L60.0 R90.0 A90.0; L60.0 R0.0 A90.0; end L80.0")
        self.assertTrue(lines[2].startswith("Return,Hose,6 / 10,"))
        folder = tempfile.mkdtemp(prefix="bw-routing-")
        try:
            out = br.write_text(os.path.join(folder, "fromto.csv"), br.from_to_csv(self.doc))
            with open(out, encoding="utf-8") as handle:
                self.assertEqual(handle.read().splitlines()[1].split(",")[0], "W2")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_save_and_reload(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, gauge="22 AWG", signal="CLK")
        hose = br.make_hose(path, inner=6.0, od=10.0)
        hose.CutLength = 150.0  # too short for its minimum radius (ROU-08)
        hose.MinBendRadius = 300.0
        self.doc.recompute()
        folder = tempfile.mkdtemp(prefix="bw-routing-")
        try:
            file = os.path.join(folder, "route.FCStd")
            self.doc.saveAs(file)
            names = (path.Name, wire.Name, hose.Name)
            App.closeDocument(self.doc.Name)
            doc = App.openDocument(file)
            self.doc = doc
            path, wire, hose = (doc.getObject(n) for n in names)
            self.assertEqual(br.kind_of(path), br.PATH)
            self.assertEqual(br.kind_of(wire), br.WIRE)
            self.assertEqual(br.kind_of(hose), br.HOSE)
            self.assertEqual(wire.Signal, "CLK")
            self.assertFalse(hose.BendOK)
            doc.recompute()
            self.assertAlmostEqual(wire.Length.Value, (160.0 + 10.0 * math.pi) * 1.05, places=5)
            self.assertEqual(len(br.paths(doc)), 1)
            self.assertEqual(len(br.routed(doc)), 2)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_bom_walker_lists_routed_items_by_length(self):
        """ROU-04: the vault's BOM walker meters a hose in metres and skips the path itself."""
        import bruceworx_bom as bb

        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0, container=asm)
        hose = br.make_hose(path, inner=6.0, od=10.0, slack=0)
        bolt = self.doc.addObject("Part::Box", "Bolt")
        asm.addObject(bolt)
        self.doc.recompute()
        rows = {r["name"]: r for r in bb.walk(self.doc)}
        self.assertEqual(sorted(rows), sorted([f"{hose.Label} (in this file)", f"{bolt.Label} (in this file)"]))
        metered = rows[f"{hose.Label} (in this file)"]
        self.assertEqual(metered["unit"], "m")
        self.assertAlmostEqual(metered["qty"], (160.0 + 10.0 * math.pi) / 1000.0, places=9)
        self.assertNotIn("unit", rows[f"{bolt.Label} (in this file)"])
        self.assertEqual(rows[f"{bolt.Label} (in this file)"]["qty"], 1)
        from bruceworx import bom

        lines, missing = bom.lines_from_walk(bb.walk(self.doc), set())
        self.assertEqual(lines, [])
        self.assertEqual([(m["name"].split(" ")[0], m.get("unit", "")) for m in missing],
                         sorted([(hose.Label, "m"), (bolt.Label, "")], key=lambda t: [m["name"] for m in missing].index(t[0] + " (in this file)")))

    def test_assembly_bom_length_column(self):
        """ROU-04 (30 Oct): the Assembly BOM's Length column meters a hose; the path is not a line."""
        import UtilsAssembly

        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0, container=asm)
        hose = br.make_hose(path, inner=6.0, od=10.0, slack=0)
        bolt = self.doc.addObject("Part::Box", "Bolt")
        asm.addObject(bolt)
        self.doc.recompute()
        self.assertTrue(path.BomExclude)
        bom = UtilsAssembly.getBomGroup(asm).newObject("Assembly::BomObject", "BOM")
        bom.columnsNames = ["Index", "Name", "Quantity", "Length"]
        self.doc.recompute()
        rows = {}
        for row in range(2, 10):
            name = bom.getContents(f"B{row}").lstrip("'")
            if name:
                rows[name] = (bom.getContents(f"C{row}"), bom.getContents(f"D{row}").lstrip("'"))
        metres = (160.0 + 10.0 * math.pi) / 1000.0
        self.assertEqual(rows, {hose.Label: ("1", f"{metres:.3f} m"), bolt.Label: ("1", "")})

    def test_existing_bom_gets_the_length_column_on_load(self):
        """Your 30 Oct answer: a BOM saved before the Length column gets it when its file is opened,
        once; one whose columns were set since (Length removed on purpose) keeps them."""
        import UtilsAssembly

        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        path = br.make_path([(0, 0, 0), (100, 0, 0)], self.doc, container=asm)
        br.make_hose(path, inner=6.0, od=10.0, slack=0)
        old = UtilsAssembly.getBomGroup(asm).newObject("Assembly::BomObject", "OldBOM")
        old.columnsNames = ["Index", "Name", "Quantity"]
        old.lengthColumnAdded = False  # as in a file from before the column (the property is not in it)
        kept = UtilsAssembly.getBomGroup(asm).newObject("Assembly::BomObject", "KeptBOM")
        kept.columnsNames = ["Index", "Name", "Quantity"]
        self.assertTrue(kept.lengthColumnAdded)  # setting the columns marks it
        self.doc.recompute()
        folder = tempfile.mkdtemp(prefix="bw-routing-bom-")
        try:
            name = os.path.join(folder, "asm.FCStd")
            self.doc.saveAs(name)
            App.closeDocument(self.doc.Name)
            doc = App.openDocument(name)
            old, kept = doc.getObject("OldBOM"), doc.getObject("KeptBOM")
            self.assertEqual(list(old.columnsNames), ["Index", "Name", "Quantity", "Length"])
            self.assertTrue(old.lengthColumnAdded)
            self.assertEqual(list(kept.columnsNames), ["Index", "Name", "Quantity"])
            old.touch()
            doc.recompute()
            self.assertEqual(old.getContents("D1").lstrip("'"), "Length")
            self.assertEqual(old.getContents("D2").lstrip("'"), "0.1 m")
            App.closeDocument(doc.Name)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_items_follow_their_path_into_a_part(self):
        part = self.doc.addObject("App::Part", "Harness")
        part.Placement = App.Placement(V(0, 0, 10), App.Rotation())
        path = br.make_path([(0, 0, 10), (100, 0, 10)], self.doc, container=part)
        wire = br.make_wire(path, gauge="22 AWG")
        self.doc.recompute()
        self.assertIn(path, part.Group)
        self.assertIn(wire, part.Group)
        # The path is stored in the document frame and drawn there even inside a placed container.
        self.assertAlmostEqual(path.Shape.BoundBox.ZMin, 0.0, places=6)  # local: 10 - 10
        self.assertAlmostEqual(path.getGlobalPlacement().multVec(path.Shape.Vertexes[0].Point).z, 10.0, places=6)


class ConnectorFittingTests(unittest.TestCase):
    """ROU-05: pin and port frames, routes that attach to them and leave straight, wires that fill
    their from-to from the pins, terminals in the BOM, fittings whose ports drive a tube."""

    def setUp(self):
        import bruceworx_fittings
        import bruceworx_routing_ports

        self.bp, self.bf = bruceworx_routing_ports, bruceworx_fittings
        self.doc = App.newDocument("BWRoutingPorts")
        br.reset_prefs()

    def tearDown(self):
        br.reset_prefs()
        for name in list(App.listDocuments()):
            if name.startswith("BWRoutingPorts"):
                App.closeDocument(name)

    def housing(self, name="XHP_2", pn="XHP-2", mate="B2B-XH-A; S2B-XH-A", z=(0, 0, -1), terminal="SXH-001T-P0.6",
                seal=""):
        """A 2-pin wire housing: a box with two pin frames on its rear face, +Z out of it."""
        part = self.doc.addObject("App::Part", name)
        part.Label = pn
        box = self.doc.addObject("Part::Box", name + "_Body")
        box.Length, box.Width, box.Height = 7.3, 5.7, 7.5
        box.Placement.Base = V(-2.4, -2.85, 0)
        part.addObject(box)
        self.bp.set_connector(part, family="JST-XH", mating_pn=mate, role="Wire housing")
        part.addProperty("App::PropertyString", "PartNumber", "BruceWorx")
        part.PartNumber = pn
        for k, x in enumerate((0.0, 2.5), start=1):
            self.bp.make_pin(part, str(k), ((x, 0, 0), z, (1, 0, 0)), GaugeMin=28, GaugeMax=22, TerminalPN=terminal,
                             TerminalMfr="JST" if terminal else "", SealPN=seal,
                             DefaultSignal="+5V" if k == 1 else "")
        return part

    def link(self, target, label, base, container, rotation=None):
        obj = self.doc.addObject("App::Link", label)
        obj.LinkedObject = target
        obj.Label = label
        obj.Placement = App.Placement(V(*base), rotation or App.Rotation())
        container.addObject(obj)
        return obj

    def harness(self, mode="Spline"):
        """J1 and J2 (two housings) in an assembly, a path from J1 pin 1 to J2 pin 1."""
        part = self.housing()
        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        j1 = self.link(part, "J1", (0, 0, 0), asm)
        j2 = self.link(part, "J2", (100, 40, 0), asm)
        a = self.bp.frame_ref(j1, self.bp.pin(part, "1"))
        b = self.bp.frame_ref(j2, self.bp.pin(part, "1"))
        path = br.make_path([(0, 0, 0), (50, 20, -30), (100, 40, 0)], self.doc, mode=mode, radius=5.0,
                            attachments=[(0, a[0], a[1]), (2, b[0], b[1])], container=asm)
        self.doc.recompute()
        return part, asm, j1, j2, path

    # ----- the schema -----

    def test_pin_and_port_schema(self):
        bp = self.bp
        part = self.housing()
        bp.make_pin(part, "+12V", ((5, 0, 0), (0, 0, -1)))
        self.assertEqual([(f.Name, f.Label, f.PinName) for f in bp.pins(part)],
                         [("Pin1", "Pin 1", "1"), ("Pin2", "Pin 2", "2"), ("Pin_12V", "Pin +12V", "+12V")])
        pin = bp.pin(part, "2")
        self.assertEqual((pin.BWFrame, pin.GaugeMin, pin.GaugeMax, pin.TerminalPN), ("Pin", 28, 22, "SXH-001T-P0.6"))
        self.assertAlmostEqual((pin.Placement.Rotation.multVec(V(0, 0, 1)) - V(0, 0, -1)).Length, 0.0)
        self.assertAlmostEqual((pin.Placement.Rotation.multVec(V(1, 0, 0)) - V(1, 0, 0)).Length, 0.0)
        port = bp.make_port(part, "1", ((0, 0, 9), (0, 0, 1)), PortType="Barb", PortSize="6 mm", TubeOD=6.0)
        self.assertTrue(bp.is_port(port) and not bp.is_pin(port))
        self.assertEqual(bp.frames(part)[-1], port)
        self.assertIn("NPT", port.getEnumerationsOfProperty("PortType"))
        with self.assertRaises(ValueError):
            bp.make_pin(part, "3", None, PortType="Barb")
        lcs = self.doc.addObject("App::LocalCoordinateSystem", "Loose")
        part.addObject(lcs)
        self.assertFalse(bp.is_frame(lcs))
        bp.add_frame_props(lcs, "Pin", PinName="9")
        self.assertEqual(bp.pin(part, "9"), lcs)
        # Through a link in an assembly the frame resolves with the instance as its connector.
        asm = self.doc.addObject("App::Part", "Asm")
        j1 = self.link(part, "J1", (10, 20, 30), asm, App.Rotation(V(0, 0, 1), 90))
        self.doc.recompute()
        obj, sub = bp.frame_ref(j1, bp.pin(part, "2"))
        self.assertEqual((obj, sub), (j1, "Pin2."))
        for o, s in ((obj, sub), (asm, "J1.Pin2."), (asm, f"J1.Pin2.{bp.pin(part, '2').OriginFeatures[0].Name}.")):
            frame, placement, connector = bp.frame_at(o, s)
            self.assertEqual((frame.PinName, connector.Label), ("2", "J1"))
            self.assertAlmostEqual((placement.Base - V(10, 22.5, 30)).Length, 0.0, places=6)
            self.assertAlmostEqual((placement.Rotation.multVec(V(0, 0, 1)) - V(0, 0, -1)).Length, 0.0, places=6)
        self.assertIsNone(bp.frame_at(asm, "J1.XHP_2_Body.Face1"))
        self.assertAlmostEqual((br.attachment_point(asm, "J1.Pin2.") - V(10, 22.5, 30)).Length, 0.0, places=6)

    def test_mating_pairs(self):
        bp = self.bp
        housing = self.housing()
        header = self.housing("B2B", pn="B2B-XH-A", mate="", z=(0, 0, 1))
        asm = self.doc.addObject("App::Part", "Asm")
        j1 = self.link(housing, "J1", (0, 0, 0), asm)
        j2 = self.link(housing, "J2", (100, 0, 0), asm)
        p1 = self.link(header, "P1", (0, 0, -10), asm)
        p2 = self.link(header, "P2", (100, 0, -10), asm)
        self.doc.recompute()
        self.assertTrue(bp.mates(j1, p1))
        self.assertFalse(bp.mates(j1, j2))
        # Each housing pairs with the header it sits on, not with the first one found.
        self.assertEqual(bp.mate_of(j1), p1)
        self.assertEqual(bp.mate_of(j2), p2)
        self.assertEqual(bp.mate_of(p2), j2)
        header.PartNumber = "OTHER"
        self.assertIsNone(bp.mate_of(j1))
        header.addProperty("App::PropertyString", "ManufacturerPartNumber", "BruceWorx")
        header.ManufacturerPartNumber = "S2B-XH-A"  # the second entry of "B2B-XH-A; S2B-XH-A"
        self.assertEqual(bp.mate_of(j1), p1)

    # ----- routes on pins -----

    def test_spline_path_on_pins_leaves_straight(self):
        part, asm, j1, j2, path = self.harness()
        self.assertEqual((path.StartFrame, path.EndFrame), ("Pin 1 of J1", "Pin 1 of J2"))
        straight = 2.0 * rt.default_insulation_od("22 AWG")  # no wire yet: the pin's thickest wire
        self.assertAlmostEqual(path.StartStraight.Value, straight)
        self.assertAlmostEqual(path.EndStraight.Value, straight)
        self.assertEqual(len(path.Shape.Edges), 1)  # leads and spline: one B-spline edge
        edge = path.Shape.Edges[0]
        # The first L of the curve is the straight down -Z (J1's pin points -Z), at unit speed.
        for u in (0.0, 0.3 * straight, straight):
            self.assertAlmostEqual((edge.valueAt(u) - V(0, 0, -u)).Length, 0.0, places=6)
        self.assertAlmostEqual((edge.tangentAt(0.5 * straight) - V(0, 0, -1)).Length, 0.0, places=6)
        self.assertAlmostEqual((edge.valueAt(edge.LastParameter) - V(100, 40, 0)).Length, 0.0, places=6)
        self.assertTrue(path.BendOK)
        # A wire on it: the straight follows its OD (2 x 1.6), and its own StraightLength wins.
        wire = br.make_wire(path, gauge="22 AWG", insulation_od=1.6)
        self.doc.recompute()
        cut0 = wire.Length.Value
        self.assertAlmostEqual(path.StartStraight.Value, 3.2)
        self.bp.pin(part, "1").StraightLength = 10.0
        path.touch()
        self.doc.recompute()
        self.assertAlmostEqual(path.StartStraight.Value, 10.0)
        self.assertAlmostEqual((path.Shape.Edges[0].valueAt(10.0) - V(0, 0, -10)).Length, 0.0, places=6)
        self.assertAlmostEqual(wire.Length.Value, cut0, places=6)  # ROU-08: the cut length is the wire's own
        # The preference drives the default: 3 x OD.
        self.bp.pin(part, "1").StraightLength = 0.0
        br.set_prefs({"WireStraightFactor": 3.0})
        path.touch()
        self.doc.recompute()
        self.assertAlmostEqual(path.StartStraight.Value, 4.8)
        # Off: the path is drawn through its points as before.
        path.LeaveStraight = False
        self.doc.recompute()
        self.assertEqual(path.StartFrame, "")
        self.assertAlmostEqual(path.Length.Value, rt.spline_length([(0, 0, 0), (50, 20, -30), (100, 40, 0)]), places=6)

    def test_polyline_path_on_pins_and_the_bend_check(self):
        part, asm, j1, j2, path = self.harness(mode="Polyline")
        straight = path.StartStraight.Value
        self.assertTrue(path.BendOK, path.Problems)
        first = path.Shape.Edges[0]
        self.assertEqual(first.Curve.TypeId, "Part::GeomLine")
        self.assertAlmostEqual(first.Length, straight, places=6)  # the arc starts exactly L out
        pts = list(path.RoutePoints)
        self.assertEqual(len(pts), 5)
        self.assertAlmostEqual(pts[1].x, 0.0)
        self.assertGreater(-pts[1].z, straight)  # the corner is L + R tan(theta / 2) out
        # The bend check sees the corners at the lead ends: a minimum above the radius fails.
        path.MinBendRadius = 6.0
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        path.MinBendRadius = 0.0
        # A radius whose tangent does not fit between the lead end and the next point is reported.
        path.BendRadius = 400.0
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        self.assertIn("needs", path.Problems)

    def test_spline_bend_check_includes_the_lead(self):
        """A pin facing away from the route: the spline must turn back right after the straight,
        and the check measures that turn (it is not there without the lead)."""
        part = self.housing(z=(0, 0, 1))
        a = self.bp.frame_ref(part, self.bp.pin(part, "1"))
        path = br.make_path([(0, 0, 0), (0, 0, -60), (40, 0, -80)], self.doc, attachments=[(0, a[0], a[1])])
        path.MinBendRadius = 5.0
        self.doc.recompute()
        self.assertFalse(path.BendOK)
        self.assertLess(path.MinRadius.Value, 5.0)
        path.LeaveStraight = False
        self.doc.recompute()
        self.assertTrue(path.BendOK)

    def test_wire_fills_from_to_from_the_pins(self):
        part, asm, j1, j2, path = self.harness()
        header = self.housing("B2B", pn="B2B-XH-A", mate="", z=(0, 0, 1), terminal="")
        p1 = self.link(header, "P1", (0, 0, 8), asm)
        wire = br.make_wire(path, gauge="22 AWG", name="W1")
        self.doc.recompute()
        self.assertEqual((wire.FromConnector, wire.FromPin, wire.ToConnector, wire.ToPin), ("J1", "1", "J2", "1"))
        self.assertEqual(wire.Signal, "+5V")
        self.assertEqual(wire.Warnings, "")
        # A typed value stays; the others keep following the pins.
        wire.ToPin = "B"
        self.doc.recompute()
        self.assertEqual(wire.ToPin, "B")
        j2.Label = "J9"
        path.touch()
        self.doc.recompute()
        self.assertEqual(wire.ToConnector, "J9")
        # A gauge the pin does not take is a warning (and a Browser badge).
        wire.Gauge = "18 AWG"
        self.doc.recompute()
        self.assertIn("Pin 1 of J1: 18 AWG is thicker", wire.Warnings)
        self.assertIn(("gauge", "warn"), br.row_badges(wire, {}, [])[1])
        # The from-to list names J1's mate (P1 sits on it); J9 has none.
        lines = br.from_to_csv(self.doc).splitlines()
        self.assertEqual(lines[1].split(",")[:12], ["W1", "+5V", "18 AWG", "red", "J1", "1", "P1", "", "J9", "B", "", ""])
        # The wire inside the connector at each end (a crimp contact's strip length) is cut too.
        before = wire.Length.Value
        self.bp.pin(part, "1").StripLength = 2.3
        self.doc.recompute()
        self.assertAlmostEqual(wire.Length.Value, before, places=6)  # ROU-08: the cut length is the wire's own
        self.assertAlmostEqual((wire.FromStrip.Value, wire.ToStrip.Value), (2.3, 2.3))
        self.assertEqual(br.from_to_csv(self.doc).splitlines()[1].split(",")[7], "2.3")
        p1.Label = "P7"
        self.assertEqual(br.from_to_rows(self.doc)[0]["from_mate"], "P7")

    def test_terminals_are_counted_in_the_bom(self):
        import bruceworx_bom as bb
        from bruceworx import bom

        part, asm, j1, j2, path = self.harness()
        pin1 = self.bp.pin(part, "1")
        pin1.SealPN, pin1.SealMfr = "WS-1", "Acme"
        for pin in self.bp.pins(part):
            pin.TerminalItem = "STD-01-02001-001"  # the Standard Library item of the JST terminal
        self.bp.set_connector(part, accessories=[("W2P", "TE Connectivity", "", 1, "Wedgelock")])
        w1 = br.make_wire(path, gauge="22 AWG", name="W1")
        a = self.bp.frame_ref(j1, self.bp.pin(part, "2"))
        path2 = br.make_path([(2.5, 0, 0), (60, 0, -20)], self.doc, attachments=[(0, a[0], a[1])], container=asm)
        br.make_wire(path2, gauge="24 AWG", name="W2")  # one end on a pin, the other loose
        self.doc.recompute()
        self.assertEqual(sorted(w1.BomExtras), ["STD-01-02001-001|2|Terminal|JST|SXH-001T-P0.6", "WS-1|2|Seal|Acme|WS-1"])
        self.assertEqual(sorted(br.terminal_rows(self.doc)),
                         [("STD-01-02001-001", "Terminal", 3, "JST", "SXH-001T-P0.6"), ("WS-1", "Seal", 2, "Acme", "WS-1")])
        rows = bb.walk(self.doc)
        counted = {}
        for r in rows:
            if r["keys"] and r["name"].split(":")[0] in ("Terminal", "Seal", "Wedgelock"):
                counted[r["name"]] = counted.get(r["name"], 0) + r["qty"]
        # Ordered by manufacturer and MPN; the Standard Library item alongside; a wedgelock per housing.
        self.assertEqual(counted, {"Terminal: JST SXH-001T-P0.6 (STD-01-02001-001)": 3, "Seal: Acme WS-1": 2,
                                   "Wedgelock: TE Connectivity W2P": 2})
        lines, missing = bom.lines_from_walk(rows, {"STD-01-02001-001"})
        self.assertEqual([(l.child, l.qty, l.manufacturer, l.mpn) for l in lines],
                         [("STD-01-02001-001", 3, "JST", "SXH-001T-P0.6")])
        self.assertIn({"name": "Seal: Acme WS-1", "qty": 2, "manufacturer": "Acme", "mpn": "WS-1"}, missing)
        self.assertIn({"name": "Wedgelock: TE Connectivity W2P", "qty": 2, "manufacturer": "TE Connectivity",
                       "mpn": "W2P"}, missing)
        # A bought part in the assembly prints what it is ordered by (30 Oct): its manufacturer and MPN,
        # else its vendor and vendor P/N; a made part nothing.
        fit = self.bf.make_fitting(self.doc, "Push-to-connect", "Straight", "6 mm", container=asm)
        fit.Vendor, fit.VendorPartNumber = "McMaster-Carr", "5779K108"
        self.doc.recompute()
        by_name = {r["name"]: r for r in bb.walk(self.doc)}
        self.assertEqual((by_name[f"{fit.Label} (in this file)"]["manufacturer"],
                          by_name[f"{fit.Label} (in this file)"]["mpn"]), ("McMaster-Carr", "5779K108"))
        part.addProperty("App::PropertyString", "Manufacturer", "BruceWorx")
        part.addProperty("App::PropertyString", "ManufacturerPartNumber", "BruceWorx")
        part.Manufacturer, part.ManufacturerPartNumber = "JST", "XHP-2"
        by_name = {r["name"]: r for r in bb.walk(self.doc)}
        self.assertEqual(by_name[f"{part.Label} (in this file)"]["mpn"], "XHP-2")
        self.assertNotIn("mpn", by_name[f"{w1.Label} (in this file)"])
        # The terminal list to order from: manufacturer and MPN in columns of their own.
        self.assertEqual(br.terminal_csv(self.doc).splitlines(), [
            "Kind,Manufacturer,MPN,Standard Library item,Qty", "Seal,Acme,WS-1,,2",
            "Terminal,JST,SXH-001T-P0.6,STD-01-02001-001,3", "Wedgelock,TE Connectivity,W2P,,2"])
        # A terminal not in the Standard Library yet is still found by its MPN.
        lines, _missing = bom.lines_from_walk(rows, {"SXH-001T-P0.6"})
        self.assertEqual([(l.child, l.qty) for l in lines], [("SXH-001T-P0.6", 3)])

    # ----- fittings -----

    def test_fitting_ports_and_choices(self):
        bf, bp = self.bf, self.bp
        fit = bf.make_fitting(self.doc, "Push-to-connect", "Elbow", "6 mm", "1/8 BSPP")
        self.doc.recompute()
        body = bf.body_of(fit)
        self.assertTrue(body.Shape.isValid())
        self.assertEqual(len(body.Shape.Solids), 1)
        self.assertEqual(fit.Description, "Push-to-connect male elbow, 6 mm tube x 1/8 BSPP")
        self.assertEqual(fit.FittingCode, "PTC-ELBOW-6MM-1/8BSPP")
        self.assertEqual(fit.ConnectorFamily, "Push-to-connect")
        self.assertIn("PartNumber", fit.PropertiesList)
        self.assertFalse(fit.BomDetail)
        ports = bp.ports(fit)
        self.assertEqual([(f.PortName, f.PortType, f.PortSize) for f in ports],
                         [("1", "Push-to-connect", "6 mm"), ("2", "BSPP", "1/8 BSPP")])
        self.assertAlmostEqual(ports[0].Engagement.Value, 17.0)
        self.assertEqual(bf.mount_port(fit), ports[1])
        # The tube port's origin is on the axis of the socket mouth: the bore wall is one tube radius
        # away, and the collar face is the end of the body along the port's +Z.
        self.assertAlmostEqual(body.Shape.distToShape(Part.Vertex(ports[0].Placement.Base))[0], 3.0, places=6)
        self.assertAlmostEqual(body.Shape.BoundBox.XMax, ports[0].Placement.Base.x, places=6)
        # A tee has three ports, then an elbow two again; a typed description stays.
        bf.set_choice(fit, style="Tee")
        self.doc.recompute()
        self.assertEqual([f.PortName for f in bp.ports(fit)], ["1", "2", "3"])
        self.assertEqual(body.Thread, "None")
        fit.Description = "Tee for the air manifold"
        bf.set_choice(fit, family="Compression")
        self.doc.recompute()
        self.assertEqual((body.Style, body.Size), ("Straight union", "6 mm"))  # the size kept where the family has it
        self.assertEqual(len(bp.ports(fit)), 2)
        self.assertEqual(fit.Description, "Tee for the air manifold")
        self.assertEqual(fit.FittingCode, "CMP-STRAIGHTUNION-6MM")
        # The property editor path: setting Size on the body moves the ports too.
        body.Size = "12 mm"
        self.doc.recompute()
        self.assertEqual(bp.port(fit, "1").PortSize, "12 mm")
        self.assertTrue(body.Shape.isValid())

    def test_every_fitting_builds(self):
        from bruceworx import fittings as ft

        for family, style, size, thread in ft.all_fittings():
            shape = self.bf.layout_shape(ft.layout(family, style, size, thread or None))
            self.assertTrue(shape.isValid(), (family, style, size))
            self.assertEqual(len(shape.Solids), 1, (family, style, size))
            self.assertGreater(shape.Volume, 0.0)

    def test_place_fitting_on_a_face(self):
        bf = self.bf
        block = self.doc.addObject("Part::Box", "Manifold")
        block.Length = block.Width = block.Height = 40
        self.doc.recompute()
        top = next(f for f in block.Shape.Faces if abs(f.CenterOfMass.z - 40) < 1e-6)
        fit = bf.make_fitting(self.doc, "Push-to-connect", "Straight", "8 mm", "1/4 NPT")
        bf.place_fitting(fit, top.CenterOfMass, V(0, 0, 1))
        self.doc.recompute()
        mount = bf.mount_port(fit).getGlobalPlacement()
        self.assertAlmostEqual((mount.Base - V(20, 20, 40)).Length, 0.0, places=6)
        self.assertAlmostEqual((mount.Rotation.multVec(V(0, 0, 1)) - V(0, 0, -1)).Length, 0.0, places=6)
        tube = self.bp.port(fit, "1").getGlobalPlacement()
        self.assertGreater(tube.Base.z, 40.0)  # the socket stands up out of the face
        elbow = bf.make_fitting(self.doc, "Push-to-connect", "Elbow", "6 mm", "1/8 NPT")
        bf.place_fitting(elbow, V(10, 10, 40), V(0, 0, 1))
        self.doc.recompute()
        out = self.bp.port(elbow, "1").getGlobalPlacement().Rotation.multVec(V(0, 0, 1))
        self.assertAlmostEqual((out - V(1, 0, 0)).Length, 0.0, places=6)  # stands as drawn: outlet along +X
        bf.place_fitting(elbow, V(10, 10, 40), V(0, 0, 1), angle=90.0)
        self.doc.recompute()
        out = self.bp.port(elbow, "1").getGlobalPlacement().Rotation.multVec(V(0, 0, 1))
        self.assertAlmostEqual(abs(out.y), 1.0, places=6)  # turned a quarter about the thread
        mount = bf.mount_port(elbow).getGlobalPlacement().Base
        self.assertAlmostEqual((mount - V(10, 10, 40)).Length, 0.0, places=6)

    def test_fitting_port_drives_a_tube_path(self):
        bf, bp = self.bf, self.bp
        f1 = bf.make_fitting(self.doc, "Push-to-connect", "Elbow", "6 mm", "1/8 BSPP")
        f2 = bf.make_fitting(self.doc, "Push-to-connect", "Straight", "6 mm")
        f2.Placement = App.Placement(V(200, 100, 0), App.Rotation())
        self.doc.recompute()
        a = bp.frame_ref(f1, bp.port(f1, "1"))
        b = bp.frame_ref(f2, bp.port(f2, "1"))
        path = br.make_path([(0, 0, 0), (150, 50, 60), (0, 0, 0)], self.doc, mode="Polyline", radius=12.0,
                            attachments=[(0, a[0], a[1]), (2, b[0], b[1])])
        pipe = br.make_pipe(path, size="6 x 1", bend_radius=12.0)
        self.doc.recompute()
        self.assertEqual(path.StartFrame, f"Port 1 of {f1.Label}")
        self.assertAlmostEqual(path.StartStraight.Value, 12.0)  # tube: 2 x OD
        self.assertAlmostEqual(path.EndStraight.Value, 12.0)
        self.assertTrue(path.BendOK, path.Problems)
        start = bp.port(f1, "1").getGlobalPlacement()
        self.assertAlmostEqual((path.Points[0] - start.Base).Length, 0.0, places=6)
        self.assertAlmostEqual(path.Shape.Edges[0].Length, 12.0, places=6)
        # Tube inside both fittings: the cut length and the first and last feeds include it.
        self.assertAlmostEqual(pipe.CutLength.Value, path.Length.Value + 2 * 17.0, places=6)
        rows = br.pipe_table(pipe)
        self.assertAlmostEqual(rows[0]["L"], 12.0 + 17.0, places=6)
        self.assertAlmostEqual(rows[-1]["L"], 12.0 + 17.0, places=6)
        self.assertTrue(pipe.BendTable.startswith("1  L 29.0"))
        # The clamp length preference makes the straight longer.
        br.set_prefs({"TubeClampLength": 25.0})
        path.touch()
        self.doc.recompute()
        self.assertAlmostEqual(path.StartStraight.Value, 25.0)
        # Moving the fitting moves the route's end with it.
        f2.Placement = App.Placement(V(220, 100, 10), App.Rotation())
        self.doc.recompute()
        end = bp.port(f2, "1").getGlobalPlacement()
        self.assertAlmostEqual((path.Points[-1] - end.Base).Length, 0.0, places=6)
        # A hose on a barb's port: the hose over the barb is part of its length.
        barb = bf.make_fitting(self.doc, "Hose barb", "Straight", "6 mm", "1/8 NPT")
        c = bp.frame_ref(barb, bp.port(barb, "1"))
        hp = br.make_path([(0, 0, 0), (0, 80, 80)], self.doc, attachments=[(0, c[0], c[1])])
        hose = br.make_hose(hp, inner=6.0, od=10.0, slack=0)
        self.doc.recompute()
        self.assertAlmostEqual(hp.StartStraight.Value, 20.0)  # hose: 2 x its OD
        self.assertAlmostEqual(hose.Length.Value, hp.Length.Value + bp.port(barb, "1").Engagement.Value, places=6)

    def test_save_and_reload(self):
        part, asm, j1, j2, path = self.harness()
        wire = br.make_wire(path, gauge="22 AWG", name="W1")
        fit = self.bf.make_fitting(self.doc, "Threaded", "Tee", "1/4 NPT")
        self.doc.recompute()
        length = path.Length.Value
        names = (path.Name, wire.Name, fit.Name)
        folder = tempfile.mkdtemp(prefix="bw-routing-ports-")
        try:
            name = os.path.join(folder, "harness.FCStd")
            self.doc.saveAs(name)
            App.closeDocument(self.doc.Name)
            doc = App.openDocument(name)
            doc.recompute()
            path, wire, fit = (doc.getObject(n) for n in names)
            self.assertEqual(path.StartFrame, "Pin 1 of J1")
            self.assertAlmostEqual(path.Length.Value, length, places=6)
            self.assertEqual((wire.FromConnector, wire.ToPin), ("J1", "1"))
            self.assertEqual(list(wire.BomExtras), ["SXH-001T-P0.6|2|Terminal|JST|SXH-001T-P0.6"])
            self.assertEqual(len(self.bp.ports(fit)), 3)
            self.bf.set_choice(fit, style="Elbow")
            doc.recompute()
            self.assertEqual(len(self.bp.ports(fit)), 2)
            self.assertTrue(self.bf.body_of(fit).Shape.isValid())
            App.closeDocument(doc.Name)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
