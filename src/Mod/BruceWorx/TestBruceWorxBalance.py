# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mass report and drone balance on the ROB-03 figure's quad X-frame (roadmap ROB-03, ROB-04). No GUI."""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_massreport as mr

THRUST_CSV = """throttle %,thrust (g),current (A),voltage (V)
0,0,0.4,14.8
25,150,2.0,14.8
50,520,8.0,14.8
75,1000,18.0,14.8
100,1400,32.0,14.8
"""
RHO_CF = 1.6e-6  # kg/mm^3, carbon plate (1.6 g/cm^3)


def _app():
    try:
        import FreeCAD
        import Part
    except ImportError:
        raise unittest.SkipTest("needs FreeCAD")
    return FreeCAD, Part


def _body(doc, part, name, shape, mass_kg=None):
    """A solid in ``part``; a MassOverride stands for a material (the figure's carbon plate is 1.6 g/cm^3)."""
    body = doc.addObject("Part::Feature", name)
    body.Shape = shape
    body.addProperty("App::PropertyMass", "MassOverride", "BruceWorx", "")
    body.MassOverride = f"{mass_kg if mass_kg is not None else shape.Volume * RHO_CF} kg"
    part.addObject(body)
    return body


def xframe(doc, battery_dx=5.0, typed=True, wires=False):
    """The figure of ROB-03: carbon plate and four arms from density, four 32 g motors, a 190 g battery
    5 mm forward. Returns the assembly."""
    FreeCAD, Part = _app()
    V = FreeCAD.Vector
    asm = doc.addObject("App::Part", "Drone")

    def part(name, shape, mass_kg=None):
        p = doc.addObject("App::Part", name)
        _body(doc, p, name + "Body", shape, mass_kg)
        asm.addObject(p)
        return p

    part("Plate", Part.makeBox(80, 80, 3, V(-40, -40, 0)))
    arm = part("Arm", Part.makeBox(110, 14, 4, V(0, -7, 0)))
    motor = part("Motor", Part.makeCylinder(14, 18, V(0, 0, 4)), mass_kg=0.032 if not typed else None)
    batt = part("Battery", Part.makeBox(70, 35, 30, V(-35 + battery_dx, -17.5, -30)),
                mass_kg=0.19 if not typed else None)
    if typed:  # the COTS masses on the part: datasheet for the motor, weighed for the battery
        for p, prop, grams in ((motor, "DatasheetMass", 32.0), (batt, "MeasuredMass", 190.0)):
            p.addProperty("App::PropertyMass", prop, "BruceWorx", "")
            setattr(p, prop, f"{grams} g")
    for k in range(4):
        ang = 45 + 90 * k
        for src, base in ((arm, V(0, 0, 0)), (motor, V(115 * math.cos(math.radians(ang)),
                                                       115 * math.sin(math.radians(ang)), 0))):
            link = doc.addObject("App::Link", f"{src.Name}Link{k}")
            link.LinkedObject = src
            link.Placement = FreeCAD.Placement(base, FreeCAD.Rotation(V(0, 0, 1), ang if src is arm else 0))
            asm.addObject(link)
    for name in ("Arm", "Motor"):  # the originals are the library parts; hide their own occurrence
        doc.getObject(name).Visibility = False
    # the library parts sit in the assembly too: move them out so only the links count
    for p in (arm, motor):
        asm.removeObject(p)
    return asm


class Fixture(unittest.TestCase):
    def setUp(self):
        FreeCAD, _Part = _app()
        self.doc = FreeCAD.newDocument("BWBalance")

    def tearDown(self):
        import FreeCAD

        FreeCAD.closeDocument(self.doc.Name)


class MassReportTests(Fixture):
    def test_figure_numbers(self):
        # ROB-03 acceptance: 388.1 g, CG (2.45, 0, -2.73) mm (the figure's model, rebuilt with the same dimensions)
        asm = xframe(self.doc)
        self.doc.recompute()
        report = mr.build_report(self.doc, [asm])
        self.assertAlmostEqual(report.mass * 1000, 388.1, delta=0.05)
        self.assertAlmostEqual(report.cg[0], 2.45, delta=0.005)
        self.assertAlmostEqual(report.cg[1], 0.0, delta=0.005)
        self.assertAlmostEqual(report.cg[2], -2.73, delta=0.005)

    def test_rows_quantities_sources_and_shares(self):
        asm = xframe(self.doc)
        report = mr.build_report(self.doc, [asm])
        rows = {r.label: r for r in report.rows}
        self.assertEqual(rows["Arm"].qty, 4)
        self.assertEqual(rows["Motor"].qty, 4)
        self.assertEqual((rows["Motor"].source, rows["Battery"].source), ("datasheet", "measured"))
        self.assertEqual(rows["Plate"].source, "override")
        self.assertAlmostEqual(rows["Motor"].mass * 1000, 128.0, places=6)
        self.assertAlmostEqual(sum(r.share for r in report.rows), 1.0)
        self.assertEqual(report.rows[0].label, "Battery")  # biggest first
        text = report.csv_text()
        self.assertIn("Measured", text)
        self.assertIn("Total", text)

    def test_measured_beats_datasheet_beats_density(self):
        asm = xframe(self.doc)
        batt = self.doc.getObject("Battery")
        batt.addProperty("App::PropertyMass", "DatasheetMass", "BruceWorx", "")
        batt.DatasheetMass = "200 g"
        self.assertEqual(mr.build_report(self.doc, [asm]).rows[0].source, "measured")  # 190 g measured wins
        batt.MeasuredMass = "0 g"
        report = mr.build_report(self.doc, [asm])
        self.assertEqual([r for r in report.rows if r.label == "Battery"][0].source, "datasheet")
        batt.DatasheetMass = "0 g"
        row = [r for r in mr.build_report(self.doc, [asm]).rows if r.label == "Battery"][0]
        self.assertEqual(row.source, "override")  # falls back to the body's own mass

    def test_typed_mass_keeps_centroid_and_scales_inertia(self):
        FreeCAD, Part = _app()
        part = self.doc.addObject("App::Part", "Blk")
        _body(self.doc, part, "BlkBody", Part.makeBox(10, 20, 30, FreeCAD.Vector(1, 2, 3)), mass_kg=0.5)
        part.addProperty("App::PropertyMass", "MeasuredMass", "BruceWorx", "")
        part.MeasuredMass = "100 g"
        report = mr.build_report(self.doc, [part])
        self.assertAlmostEqual(report.mass, 0.1)
        for got, want in zip(report.cg, (6, 12, 18)):
            self.assertAlmostEqual(got, want, places=6)
        m = 0.1  # solid box: I = m (b^2 + c^2) / 12
        self.assertAlmostEqual(report.tensor[0][0], m * (20 ** 2 + 30 ** 2) / 12, places=6)
        self.assertAlmostEqual(report.tensor[2][2], m * (10 ** 2 + 20 ** 2) / 12, places=6)
        about_origin = report.tensor_origin()
        self.assertAlmostEqual(about_origin[0][0], m * (20 ** 2 + 30 ** 2) / 12 + m * (12 ** 2 + 18 ** 2), places=6)

    def test_link_array_and_sub_assembly(self):
        FreeCAD, Part = _app()
        V = FreeCAD.Vector
        part = self.doc.addObject("App::Part", "Peg")
        _body(self.doc, part, "PegBody", Part.makeCylinder(5, 10), mass_kg=0.01)
        sub = self.doc.addObject("App::Part", "Sub")
        arr = self.doc.addObject("App::Link", "Pegs")
        arr.LinkedObject = part
        arr.ElementCount = 3
        arr.ShowElement = False
        arr.PlacementList = [FreeCAD.Placement(V(i * 30, 0, 0), FreeCAD.Rotation()) for i in range(3)]
        sub.addObject(arr)
        top = self.doc.addObject("App::Part", "Top")
        link = self.doc.addObject("App::Link", "SubLink")
        link.LinkedObject = sub
        link.Placement.Base = V(0, 100, 0)
        top.addObject(link)
        report = mr.build_report(self.doc, [top])
        self.assertEqual(len(report.instances), 3)
        self.assertAlmostEqual(report.mass, 0.03)
        self.assertAlmostEqual(report.cg[0], 30.0, places=6)
        self.assertAlmostEqual(report.cg[1], 100.0, places=6)
        self.assertEqual(report.rows[0].qty, 3)

    def test_density_source_from_a_material(self):
        import FreeCAD
        import Materials
        import Part

        card = next((c for c in (Materials.MaterialManager().getMaterial(u) for u in Materials.MaterialManager().Materials)
                     if c.Name == "Steel-1C45"), None)
        if card is None:
            self.skipTest("no Steel-1C45 card")
        part = self.doc.addObject("App::Part", "Steel")
        body = self.doc.addObject("Part::Feature", "SteelBody")
        body.Shape = Part.makeBox(10, 10, 10)
        body.ShapeMaterial = card
        part.addObject(body)
        report = mr.build_report(self.doc, [part])
        rho = FreeCAD.Units.Quantity(str(card.PhysicalProperties["Density"])).getValueAs("kg/mm^3").Value
        self.assertEqual(report.rows[0].source, "density")
        self.assertAlmostEqual(report.mass, 1000 * rho, places=9)

    def test_routed_wire_brings_its_own_mass(self):
        FreeCAD, Part = _app()
        asm = xframe(self.doc)
        wire = self.doc.addObject("Part::FeaturePython", "Wire")

        class Proxy:
            Type = mr.WIRE_TYPE

        wire.Proxy = Proxy()
        wire.addProperty("App::PropertyMass", "Mass", "BruceWorx", "")
        wire.Shape = Part.makeCylinder(1, 100, FreeCAD.Vector(0, 0, 10))
        wire.Mass = "12 g"  # after the shape: a Part::Feature clears its Mass when the shape is set
        asm.addObject(wire)
        report = mr.build_report(self.doc, [asm])
        self.assertAlmostEqual(report.mass * 1000, 400.1, delta=0.05)
        self.assertEqual([r for r in report.rows if r.source == "wire"][0].label, "Wire")


class BalanceTests(Fixture):
    def _table_file(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.write(fd, THRUST_CSV.encode())
        os.close(fd)
        self.addCleanup(os.remove, path)
        return path

    def _drone(self, battery_dx, capacity=1500):
        asm = xframe(self.doc, battery_dx)
        motor = self.doc.getObject("Motor")
        mr.mark_motor(motor, table_path=self._table_file())
        mr.set_battery_capacity(self.doc.getObject("Battery"), capacity)
        return asm

    def test_motors_found_with_global_axes_and_points(self):
        asm = self._drone(5.0)
        report = mr.build_report(self.doc, [asm])
        motors = mr.motors_of(report)
        self.assertEqual(len(motors), 4)
        for inst, axis, path in motors:
            self.assertAlmostEqual(math.hypot(inst.centre[0], inst.centre[1]), 115.0, places=6)
            self.assertEqual(tuple(round(a, 9) for a in axis), (0.0, 0.0, 1.0))
            self.assertTrue(path.endswith(".csv"))

    def test_acceptance_offset_battery_is_flagged(self):
        # The figure's drone: 5 mm battery offset puts the CG 2.45 mm off the thrust centre. That is 1.07 % of the
        # 230 mm motor diagonal, under the 2 % amber default, so the default offset check reads ok and the
        # share spread shows it; a 1 % limit (or the real model with the battery 12 mm forward) flags it.
        asm = self._drone(5.0)
        report = mr.build_report(self.doc, [asm])
        result, notes = mr.balance_of(report)
        self.assertEqual(notes, [])
        self.assertAlmostEqual(result.offset, 2.45, delta=0.005)
        self.assertAlmostEqual(result.offset_pct, 100 * 2.45 / 230.0, delta=0.005)
        tight, _ = mr.balance_of(report, limits={"offset_amber": 1.0})
        self.assertEqual(tight.level, "amber")
        self.assertIn("CG offset", [c.name for c in tight.checks if c.level == "amber"])
        shares = [m.share for m in result.motors]
        self.assertAlmostEqual(sum(shares), 388.1, delta=0.05)
        self.assertGreater(max(shares) / min(shares), 1.05)  # the front motors carry more
        self.doc.getObject("BatteryBody").Placement.Base = self.doc.getObject("BatteryBody").Placement.Base.add(
            __import__("FreeCAD").Vector(20, 0, 0))
        far, _ = mr.balance_of(mr.build_report(self.doc, [asm]))
        self.assertIn(far.level, ("amber", "red"))
        self.assertIn("CG offset", [c.name for c in far.checks if c.level != "ok"])

    def test_hover_numbers(self):
        asm = self._drone(0.0)
        report = mr.build_report(self.doc, [asm])
        result, _notes = mr.balance_of(report)
        weight = report.mass * 1000
        self.assertAlmostEqual(result.tw, 4 * 1400 / weight, places=6)
        self.assertGreater(result.tw, 2)
        hover = result.motors[0].throttle
        self.assertTrue(0 < hover < 25)  # 97 g a motor, under the table's 150 g at 25 %
        self.assertAlmostEqual(result.hover_current, sum(m.current for m in result.motors), places=9)
        self.assertAlmostEqual(result.flight_min, 0.8 * 1.5 / result.hover_current * 60, places=9)
        typed, _ = mr.balance_of(report, capacity_mah=3000)
        self.assertAlmostEqual(typed.flight_min, 2 * result.flight_min, places=9)

    def test_no_motors_and_bad_file(self):
        from bruceworx import balance

        asm = xframe(self.doc)
        with self.assertRaises(balance.ThrustError):
            mr.balance_of(mr.build_report(self.doc, [asm]))
        mr.mark_motor(self.doc.getObject("Motor"), table_path="/nonexistent/table.csv")
        result, notes = mr.balance_of(mr.build_report(self.doc, [asm]))
        self.assertTrue(notes)
        self.assertIsNone(result.tw)


def _gui():
    try:
        import FreeCAD
        import FreeCADGui

        return FreeCADGui if FreeCAD.GuiUp else None
    except ImportError:
        return None


@unittest.skipUnless(_gui(), "needs the GUI (run_gui_tests.py)")
class PanelTests(BalanceTests):
    """The commands and panels on the same drone; skipped in FreeCADCmd."""

    def test_commands_panels_and_marker(self):
        import bruceworx_massreport_gui as g

        Gui = _gui()
        asm = self._drone(5.0)
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, asm.Name)
        self.assertIsNotNone(Gui.Command.get("BruceWorx_MassReport"))
        self.assertIsNotNone(Gui.Command.get("BruceWorx_DroneBalance"))
        panel = g.open_panel("mass")
        self.assertEqual(panel.table.rowCount(), 4)  # plate, arm, motor, battery
        self.assertIn("388.1", panel.totals.text())
        self.assertTrue(g.show_marker(Gui.ActiveDocument.ActiveView, panel.report))
        root = Gui.ActiveDocument.ActiveView.getSceneGraph()
        names = [root.getChild(i).getName().getString() for i in range(root.getNumChildren())]
        self.assertIn(g.OVERLAY_NAME, names)
        panel.reject()
        names = [root.getChild(i).getName().getString() for i in range(root.getNumChildren())]
        self.assertNotIn(g.OVERLAY_NAME, names)
        panel = g.open_panel("balance")
        self.assertEqual(panel.table.rowCount(), 4)
        self.assertIn("T/W", panel.checks.text())
        panel.capacity.setValue(3000)
        self.assertIn("flight time", panel.checks.text())
        panel.reject()


if __name__ == "__main__":
    unittest.main()
