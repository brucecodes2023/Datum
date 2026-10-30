# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing in the GUI (BW-ROU-01..03): the Routing tab and its commands, the Route Path panel fed
by picks in the 3D view (vertices, a circle centre, a face), the item panels, a clip, a bundle,
the Browser badge and the two CSV reports.

Run through package/bruceworx/tests/run_gui_tests.py (BW_TEST_ONLY=TestBruceWorxRouting).
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App

try:
    import FreeCADGui as Gui
except ImportError:  # headless: every test is skipped
    Gui = None

import bruceworx_routing as br

V = App.Vector


@unittest.skipUnless(Gui is not None and App.GuiUp, "needs a GUI FreeCAD")
class RoutingGuiTests(unittest.TestCase):
    def setUp(self):
        import bruceworx_routing_gui as rg

        self.rg = rg
        rg.install()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        self.doc = App.newDocument("BWRoutingGui")
        self.doc.UndoMode = 1
        self.panels = []
        test = self
        self._path_panel, self._item_panel = rg.RoutePathPanel, rg.ItemPanel

        class RecordedPath(rg.RoutePathPanel):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                test.panels.append(self)

        class RecordedItem(rg.ItemPanel):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                test.panels.append(self)

        rg.RoutePathPanel, rg.ItemPanel = RecordedPath, RecordedItem

    def tearDown(self):
        self.rg.RoutePathPanel, self.rg.ItemPanel = self._path_panel, self._item_panel
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        self.rg._state["panel"] = None
        Gui.Selection.clearSelection()
        for name in list(App.listDocuments()):
            if name.startswith("BWRoutingGui"):
                App.closeDocument(name)

    def _pump(self, n=3):
        for _ in range(n):
            Gui.updateGui()

    def _run(self, command):
        before = len(self.panels)
        Gui.runCommand(command)
        self._pump()
        self.assertTrue(Gui.Control.activeDialog(), f"{command} opened no task panel")
        self.assertEqual(len(self.panels), before + 1)
        return self.panels[-1]

    def _bracket(self):
        """A box at (60, 0, 0) and a cylinder at the origin: a motor and its board."""
        box = self.doc.addObject("Part::Box", "Board")
        box.Placement = App.Placement(V(60, 0, 0), App.Rotation())
        cyl = self.doc.addObject("Part::Cylinder", "Motor")
        cyl.Radius, cyl.Height = 8, 20
        self.doc.recompute()
        return box, cyl

    # ----- the tab and the commands -----

    def test_routing_tab_and_commands(self):
        import bruceworx_workspaces as ws

        design = ws.workspace(ws.load(), "Design")
        tab = next(t for t in design["tabs"] if t["name"] == "Routing")
        self.assertEqual([g["caption"] for g in tab["groups"]], ["Path", "Items", "Reports"])
        listed = [c for g in tab["groups"] for c in g["commands"] + g.get("more", [])]
        commands = Gui.listCommands()
        for name in self.rg.COMMANDS:
            self.assertIn(name, commands)
            self.assertIn(name, listed)
        for name in self.rg.COMMANDS:
            self.assertTrue(os.path.exists(os.path.join(self.rg.HERE, name + ".svg")), name)

    # ----- route path panel -----

    def test_path_from_picked_points(self):
        box, cyl = self._bracket()
        top = next(f"Face{i + 1}" for i, f in enumerate(cyl.Shape.Faces) if abs(f.CenterOfMass.z - 20) < 1e-6)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(cyl, top)  # the motor's top face centre
        panel = self._run("BruceWorx_RoutePath")
        self.assertEqual(panel.list.count(), 1)
        self.assertIn("Motor.Face", panel.list.item(0).text())
        self.assertEqual(Gui.Selection.getSelectionEx("", 0), [])  # the pick is a point now
        # Picks in the 3D view arrive through the selection observer; each clears the selection.
        Gui.Selection.addSelection(box, "Vertex1")
        self._pump()
        self.assertEqual(Gui.Selection.getSelectionEx("", 0), [])
        Gui.Selection.addSelection(box, "Vertex7")
        self._pump()
        self.assertEqual(Gui.Selection.getSelectionEx("", 0), [])
        self.assertEqual(panel.list.count(), 3)
        self.assertEqual(len(panel.path.Points), 3)
        self.assertIn("Length", panel.status.text())
        self.assertIn("OK", panel.status.text())
        panel.mode.setCurrentText("Polyline")
        panel.radius.setValue(5.0)
        self._pump()
        self.assertEqual(panel.path.Mode, "Polyline")
        self.assertGreater(panel.path.Bends, 0)
        # Reorder, then remove the last point and put it back.
        panel.list.setCurrentRow(2)
        panel.move_up()
        self.assertEqual(panel.list.currentRow(), 1)
        panel.list.setCurrentRow(2)
        panel.remove_current()  # Vertex1, now last
        self.assertEqual(len(panel.path.Points), 2)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(box, "Vertex1")
        self._pump()
        self.assertEqual(len(panel.path.Points), 3)
        panel.accept()
        self._pump()
        self.assertFalse(Gui.Control.activeDialog())
        path = panel.path
        self.assertEqual(br.kind_of(path), br.PATH)
        self.assertEqual(len(br.attachments_of(path)), 3)
        self.assertGreater(path.Length.Value, 60.0)
        # The attached points follow their part.
        box.Placement = App.Placement(V(60, 0, 15), App.Rotation())
        self.doc.recompute()
        self.assertAlmostEqual(max(p.z for p in path.Points), 25.0, places=6)  # Vertex7 of a 10 mm box at z 15
        # Undo removes the path with the panel's transaction.
        self.doc.undo()
        self.assertEqual(br.paths(self.doc), [])

    def test_cancel_removes_a_new_path_and_edit_keeps_one(self):
        box, _cyl = self._bracket()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(box, "Vertex1")
        Gui.Selection.addSelection(box, "Vertex7")
        panel = self._run("BruceWorx_RoutePath")
        self.assertEqual(panel.list.count(), 2)
        panel.reject()
        self._pump()
        self.assertEqual(br.paths(self.doc), [])
        path = br.make_path([(0, 0, 0), (50, 0, 0), (50, 50, 0)], self.doc)
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        panel = self._run("BruceWorx_RoutePath")
        self.assertIs(panel.path, path)
        self.assertEqual(panel.list.count(), 3)
        panel.minimum.setValue(500.0)
        self._pump()
        self.assertIn("below the minimum", panel.status.text())
        panel.accept()
        self.assertFalse(path.BendOK)
        self.assertEqual(br.row_badges(path, "", [])[1], [("bend too tight", "warn")])

    def test_add_point_and_clip_commands(self):
        box, cyl = self._bracket()
        path = br.make_path([(0, 0, 20), (30, 0, 20)], self.doc)
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        Gui.Selection.addSelection(box, "Vertex1")
        Gui.runCommand("BruceWorx_RouteAddPoint")
        self._pump()
        self.assertEqual(len(path.Points), 3)
        self.assertEqual(len(br.attachments_of(path)), 1)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        top = next(f"Face{i + 1}" for i, f in enumerate(cyl.Shape.Faces) if abs(f.CenterOfMass.z - 20) < 1e-6)
        Gui.Selection.addSelection(cyl, top)
        Gui.runCommand("BruceWorx_RouteClip")
        self._pump()
        clipped = [a for a in br.attachments_of(path) if a[1] is cyl]
        self.assertEqual(len(clipped), 1)
        self.assertEqual(clipped[0][0], 0)  # the nearest point: the start at (0, 0, 20)
        self.doc.undo()
        self.assertEqual(len([a for a in br.attachments_of(path) if a[1] is cyl]), 0)

    # ----- items -----

    def test_wire_panel_and_from_to_list(self):
        path = br.make_path([(0, 0, 20), (30, 0, 25), (60, 0, 10)], self.doc)
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        panel = self._run("BruceWorx_RouteWire")
        panel.fields["Gauge"].setCurrentText("18 AWG")
        panel.fields["Signal"].setText("+5V")
        panel.fields["Signal"].editingFinished.emit()
        panel.fields["FromConnector"].setText("J1")
        panel.fields["FromConnector"].editingFinished.emit()
        panel.fields["ToConnector"].setText("M1")
        panel.fields["ToConnector"].editingFinished.emit()
        panel.set_colour((0.0, 0.0, 1.0))
        self._pump()
        wire = panel.obj
        # The bend rule: Default (Hook-up PVC, 4 x OD), a preset, a multiple typed in.
        od = wire.InsulationOD.Value
        self.assertAlmostEqual(panel.minimum.value(), round(4.0 * od, 2), places=2)
        panel.rule.setCurrentText("Coax")
        self._pump()
        self.assertEqual(wire.BendRule, "Coax")
        self.assertAlmostEqual(wire.MinBendRadius.Value, 10.0 * od)
        self.assertAlmostEqual(panel.minimum.value(), round(10.0 * od, 2), places=2)
        self.assertIn("Coax, 10 × OD", panel.status.text())
        panel.factor.setValue(5.0)
        self._pump()
        self.assertEqual(panel.rule.currentText(), "Multiple of OD")
        self.assertAlmostEqual(wire.MinBendRadius.Value, 5.0 * od)
        panel.rule.setCurrentText("Default")
        self._pump()
        self.assertEqual(wire.Gauge, "18 AWG")
        self.assertEqual(wire.Signal, "+5V")
        self.assertIn("Length", panel.status.text())
        self.assertIn(" g", panel.status.text())
        panel.accept()
        self._pump()
        self.assertEqual(br.kind_of(wire), br.WIRE)
        self.assertAlmostEqual(wire.Length.Value, path.Length.Value * 1.05, places=6)
        self.assertEqual(tuple(wire.ViewObject.ShapeAppearance[0].DiffuseColor)[:3], (0.0, 0.0, 1.0))
        folder = tempfile.mkdtemp(prefix="bw-routing-gui-")
        try:
            out = self.rg.export_from_to(self.doc, os.path.join(folder, "fromto.csv"))
            with open(out, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            self.assertEqual(lines[1].split(",")[:6], [wire.Label, "+5V", "18 AWG", "blue", "J1", ""])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_pipe_and_hose_panels_and_cut_list(self):
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100)], self.doc, mode="Polyline", radius=20.0)
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        panel = self._run("BruceWorx_RoutePipe")
        panel.fields["Size"].setCurrentText("16 x 1.5")
        self._pump()
        pipe = panel.obj
        self.assertAlmostEqual(pipe.OD.Value, 16.0)
        self.assertAlmostEqual(panel.fields["WallThickness"].value(), 1.5)
        panel.fields["BendRadius"].setValue(32.0)
        self._pump()
        self.assertIn("cut", panel.status.text())
        self.assertIn("A 90.0", panel.status.text())
        panel.accept()
        self._pump()
        self.assertAlmostEqual(pipe.CutLength.Value, 300.0 - 4 * 32.0 + 2 * 32.0 * math.pi / 2, places=5)
        self.assertEqual(pipe.Bends, 2)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(path)
        panel = self._run("BruceWorx_RouteHose")
        panel.fields["OD"].setValue(10.0)
        panel.fields["ID"].setValue(6.0)
        self._pump()
        hose = panel.obj
        self.assertIn("bend too tight", panel.status.text())  # 6 x OD = 60 > the path's 32
        self.assertEqual(panel.rule.currentText(), "Default")
        self.assertIn("General hose, 6 × OD", panel.status.text())
        panel.minimum.setValue(25.0)  # a radius typed: a fixed override
        self._pump()
        self.assertEqual(panel.rule.currentText(), "Fixed radius")
        self.assertEqual(hose.BendRule, "Fixed radius")
        self.assertNotIn("bend too tight", panel.status.text())
        hose_name = hose.Name
        panel.reject()
        self._pump()
        self.assertIsNone(self.doc.getObject(hose_name))
        hose = br.make_hose(path, inner=6.0, od=10.0)
        self.doc.recompute()
        self.assertFalse(hose.BendOK)
        import bruceworx_browser

        _meta, marks = bruceworx_browser.row_info(hose)
        self.assertIn(("bend too tight", "warn"), marks)
        folder = tempfile.mkdtemp(prefix="bw-routing-gui-")
        try:
            out = self.rg.export_cut_list(self.doc, os.path.join(folder, "cut.csv"))
            with open(out, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertTrue(lines[1].startswith(f"{pipe.Label},Pipe,16 x 1.5,"))
            self.assertIn("L68.0 R0.0 A90.0; L36.0 R90.0 A90.0; end L68.0", lines[1])
            self.assertTrue(lines[2].startswith(f"{hose.Label},Hose,6 / 10,"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_bend_rules_dialog(self):
        """ROU-02 (30 Oct): the defaults in the preferences, open items that follow them recomputed."""
        path = br.make_path([(0, 0, 0), (100, 0, 0), (100, 100, 0)], self.doc, mode="Polyline", radius=20.0)
        wire = br.make_wire(path, insulation_od=2.0)
        fixed = br.make_wire(path, insulation_od=2.0)
        fixed.MinBendRadius = 5.0
        hose = br.make_hose(path, od=10.0)
        self.doc.recompute()
        self.assertAlmostEqual(wire.MinBendRadius.Value, 8.0)
        try:
            dialog = self.rg.BendRulesDialog(Gui.getMainWindow())
            combo, factor, radius = dialog.widgets["wire"]
            self.assertEqual(combo.currentData(), "Hook-up PVC")
            self.assertFalse(factor.isEnabled())
            self.assertIn("Coax", dialog.presets_html())
            combo.setCurrentIndex(combo.findData("Multiple of OD"))
            self.assertTrue(factor.isEnabled())
            factor.setValue(5.0)
            hcombo, _hf, hradius = dialog.widgets["hose"]
            hcombo.setCurrentIndex(hcombo.findData("Fixed radius"))
            hradius.setValue(18.0)
            dialog.clr.setValue(2.5)
            dialog.accept()
            prefs = br.prefs()
            self.assertEqual((prefs["WireRule"], prefs["WireFactor"]), ("Multiple of OD", 5.0))
            self.assertEqual((prefs["HoseRule"], prefs["HoseRadius"]), ("Fixed radius", 18.0))
            self.assertAlmostEqual(wire.MinBendRadius.Value, 10.0)  # follows the new default
            self.assertAlmostEqual(fixed.MinBendRadius.Value, 5.0)  # its own rule
            self.assertAlmostEqual(hose.MinBendRadius.Value, 18.0)
            self.assertTrue(hose.BendOK)
            self.assertAlmostEqual(br.make_pipe(path, od=10.0, wall=1.0).BendRadius.Value, 25.0)
            again = self.rg.BendRulesDialog(Gui.getMainWindow())
            self.assertEqual(again.widgets["hose"][0].currentData(), "Fixed radius")
            again.restore_defaults()
            self.assertEqual(again.widgets["wire"][0].currentData(), "Hook-up PVC")
            again.accept()
            self.assertAlmostEqual(wire.MinBendRadius.Value, 8.0)
        finally:
            br.reset_prefs()

    def test_bundle_command(self):
        path = br.make_path([(0, 0, 0), (50, 10, 0), (100, 0, 0)], self.doc)
        wires = [br.make_wire(path, gauge="22 AWG", insulation_od=1.6) for _ in range(3)]
        self.doc.recompute()
        Gui.Selection.clearSelection()
        for wire in wires:
            Gui.Selection.addSelection(wire)
        Gui.runCommand("BruceWorx_RouteBundle")
        self._pump()
        bundles = br.routed(self.doc, (br.BUNDLE,))
        self.assertEqual(len(bundles), 1)
        self.assertAlmostEqual(bundles[0].OD.Value, 1.15 * math.sqrt(3 * 1.6**2), places=6)
        self.assertEqual(bundles[0].ViewObject.Transparency, 60)


if __name__ == "__main__":
    unittest.main()
