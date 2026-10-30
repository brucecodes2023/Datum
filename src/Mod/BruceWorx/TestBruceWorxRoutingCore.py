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
import Part  # noqa: F401  -- Part::FeaturePython

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
        self.assertAlmostEqual(path.Length.Value / rt.spline_length(pts), 1.0, delta=0.01)
        self.assertGreater(path.MinRadius.Value, 0.0)
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
        self.assertAlmostEqual(path.MinRadius.Value / 50.0, 1.0, delta=0.15)
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
        self.assertAlmostEqual(wire.Shape.Volume / (math.pi * od * od / 4 * path.Length.Value), 1.0, delta=0.02)
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
        self.assertFalse(hose.BendOK)
        self.assertEqual(br.row_badges(hose, "", [])[1], [("bend too tight", "warn")])
        self.assertEqual(br.row_badges(path, "", [])[1], [])
        self.assertAlmostEqual(hose.Length.Value, (160.0 + 10.0 * math.pi) * 1.05, places=5)
        self.assertEqual(len(hose.Shape.Solids), 1)
        hose.MinBendRadius = 15.0
        self.doc.recompute()
        self.assertTrue(hose.BendOK)
        self.assertEqual(br.row_badges(hose, "", [])[1], [])

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
        self.assertFalse(wire.BendOK)
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
        wires[2].MinBendRadius = 50.0
        self.doc.recompute()
        self.assertAlmostEqual(bundle.MinBendRadius.Value, 50.0)
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
        self.assertEqual(lines[0], "Wire,Signal,Gauge,Colour,From,From pin,To,To pin,Length (m)")
        self.assertEqual(lines[1], f"W2,GND,18 AWG,black,J1,2,M1,-,{(280 + 30 * math.pi) * 1.05 / 1000:.3f}")
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


if __name__ == "__main__":
    unittest.main()
