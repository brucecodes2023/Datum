# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Simulation workspace in a GUI FreeCAD (SIM-01), under run_gui_tests.py.

New Study makes the study's objects from the panel, the pre-check reports the
missing load, Solve runs the cantilever (skipped without ccx), the fields colour
the result mesh, the probe reads a node, the report files exist.
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_sim as sim


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


def pump(n=4):
    import time

    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(App.GuiUp, "needs the FreeCAD window")
class TestSimulationGui(unittest.TestCase):
    def setUp(self):
        import FreeCADGui as Gui

        import bruceworx_sim_gui as sg

        sg.install()
        close_all()
        self.addCleanup(close_all)
        self.addCleanup(sg.close_dialog)
        self.addCleanup(sg.stop_probe)
        self.sg = sg
        self.doc = App.newDocument("SimGui")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 100, 10, 10
        self.doc.recompute()
        Gui.Selection.clearSelection()
        pump()

    def test_commands_registered(self):
        import FreeCADGui as Gui

        names = Gui.listCommands()
        for name, *_rest in self.sg.COMMANDS:
            self.assertIn(name, names)

    def test_new_study_panel_creates_the_objects(self):
        import FreeCADGui as Gui
        from PySide import QtWidgets

        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        self.assertEqual(len(panel.form), 4)
        self.assertEqual([panel.model_combo.itemData(i) for i in range(panel.model_combo.count())], ["Box"])
        # planned study types are listed and disabled
        texts = [panel.type_combo.itemText(i) for i in range(panel.type_combo.count())]
        self.assertEqual(texts[0], "Static stress")
        self.assertTrue(all("(planned)" in t for t in texts[1:]))
        self.assertFalse(panel.type_combo.model().item(1).isEnabled())
        # a part without a material warns and the mesh size is the default
        self.assertIn("no material", panel.material_warn.text())
        self.assertAlmostEqual(panel.size_spin.value(), sim.default_mesh_size(self.box), places=3)
        steel = [c for c in sim.material_cards() if c["name"] == "CalculiX-Steel"][0]
        self.assertTrue(panel.select_material(steel["uuid"]))
        self.assertIn("E 2.1e+05 MPa", panel.material_warn.text())
        # S1: the Keystone library heads the picker when it is installed, the stock FEM cards after it
        headings = [panel.material_combo.itemText(i) for i in range(panel.material_combo.count())
                    if panel.material_combo.itemText(i).startswith("— ")]
        self.assertIn("— FreeCAD (FEM) —", headings)
        if any(c["library"] == sim.KEYSTONE_LIBRARY for c in sim.material_cards()):
            self.assertEqual(headings[0], "— Keystone —")
        # S3: the stress measure, von Mises by default
        self.assertEqual(panel.measure_combo.currentData(), "vonMises")
        panel.measure_combo.setCurrentIndex(panel.measure_combo.findData("MaxPrincipal"))
        panel.name_edit.setText("Beam")
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        panel.modifyStandardButtons(buttons)
        self.assertEqual(panel.ok_button().text(), "Create study")
        analysis = panel.create()
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.Label, "Study: Beam")
        self.assertEqual(analysis.MaterialUUID, steel["uuid"])
        m = sim.members(analysis)
        self.assertIsNotNone(m["solver"])
        self.assertIsNotNone(m["mesh"])
        self.assertEqual(m["materials"][0].Material["YoungsModulus"], "210000 MPa")
        self.assertEqual(analysis.StressMeasure, "MaxPrincipal")
        self.assertEqual(list(analysis.SFThresholds), [1.0, 2.0])
        import FemGui

        self.assertIs(FemGui.getActiveAnalysis(), analysis)
        panel.release()
        # the study panel opens through the task view and closes
        study = self.sg.open_study_panel(analysis)
        pump()
        self.assertIsNotNone(Gui.Control.activeDialog())
        self.sg.close_dialog()
        pump()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertIsNotNone(study)

    def test_part_material_is_prefilled(self):
        manager = sim.material_manager()
        body = self.doc.addObject("PartDesign::Body", "Body")
        body.BaseFeature = None
        pad = self.doc.addObject("PartDesign::AdditiveBox", "Cube")
        body.addObject(pad)
        pad.Length = pad.Width = pad.Height = 20
        self.doc.recompute()
        steel = [c for c in sim.material_cards() if c["name"] == "CalculiX-Steel"][0]
        body.ShapeMaterial = manager.getMaterial(steel["uuid"])
        self.doc.recompute()
        panel = self.sg.classes()["NewStudyPanel"](self.doc, [body])
        self.assertEqual(panel.model(), body)
        self.assertEqual(panel.material_uuid(), steel["uuid"])
        self.assertNotIn("no material", panel.material_warn.text())
        panel.release()

    def test_precheck_reports_the_missing_load(self):
        import ObjectsFem

        objs = sim.new_study(self.doc, sim.StudyPlan(name="Check", model=self.box))
        analysis = objs["analysis"]
        fixed = ObjectsFem.makeConstraintFixed(self.doc, "Fixed")
        fixed.References = [(self.box, "Face1")]
        analysis.addObject(fixed)
        panel = self.sg.classes()["StudyPanel"](analysis)
        self.assertEqual(panel.members_list.count(), 2)  # material + support
        issues = panel.precheck()
        texts = [t for _k, t in issues]
        self.assertTrue(any(t.startswith("No load") for t in texts), texts)
        self.assertFalse(any(t.startswith("No support") for t in texts), texts)
        self.assertIn("No load", panel.issues.text())
        self.assertFalse(panel.solve())  # not ready: refused with the reason in the panel
        self.assertIn("Not ready", panel.progress.text())
        panel.release()

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_solve_fields_probe_and_report(self):
        import FreeCADGui as Gui

        tmp = tempfile.mkdtemp(prefix="bw-simgui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        close_all()
        doc, objs, theory = sim.cantilever()
        analysis = objs["analysis"]
        Gui.getDocument(doc.Name).activeView().viewIsometric()
        pump()
        panel = self.sg.classes()["StudyPanel"](analysis)
        self.assertTrue(panel.solve(blocking=True))
        result = sim.latest_result(analysis)
        self.assertIsNotNone(result)
        self.assertIn("Min safety factor", panel.summary.text())
        self.assertIn("below 1", panel.summary.text())
        self.assertIn("Solved in", panel.progress.text())
        self.assertEqual(self.sg.shown_field(analysis), "safety")
        # the banded colours: NodeColor has no getter (ViewProviderFemMeshPy), NodeColorArray is the per-vertex list
        def shown_colors():
            return {tuple(round(c, 2) for c in col[:3]) for col in result.Mesh.ViewObject.NodeColorArray}

        def rounded(hex_color):
            return tuple(round(c, 2) for c in sim.rgb(hex_color))

        colors = shown_colors()
        self.assertIn(rounded(sim.DEFAULT_SF_COLORS[0]), colors)  # red: below 1 at the root
        self.assertGreater(len(colors), 1)
        self.assertIn("below 1", panel.legend.text())
        self.assertIn("von Mises stress per node", panel.legend.text())
        # S3: the stress measure, from the panel; the safety factor is recomputed without a solve
        vm_sf = result.MinSafetyFactor
        panel.measure_combo.setCurrentIndex(panel.measure_combo.findData("Tresca"))
        self.assertEqual(analysis.StressMeasure, "Tresca")
        self.assertEqual(result.StressMeasure, "Tresca")
        self.assertLessEqual(result.MinSafetyFactor, vm_sf)
        self.assertIn("Tresca stress per node", panel.legend.text())
        # S2: the band editor: four bands, a blue top band, applied to the study and the colours on the mesh
        panel.show_bands(True)
        self.assertTrue(panel.bands_button.isChecked())
        self.assertEqual(panel.bands_table.rowCount(), 3)
        panel.add_band()
        self.assertEqual(panel.bands_from_editor()[0], [1.0, 2.0, 4.0])
        panel.set_band_color(3, "#1f5fbf")
        self.assertTrue(panel.apply_bands())
        self.assertEqual(sim.study_bands(analysis), ([1.0, 2.0, 4.0], ["#D91C1C", "#F2A007", "#3FA34D", "#1F5FBF"]))
        self.assertIn(rounded("#1F5FBF"), shown_colors())  # the unstressed free end
        self.assertIn("above 4", panel.legend.text())
        panel.bands_table.cellWidget(0, 0).setValue(5.0)  # not ascending: refused with the reason
        self.assertFalse(panel.apply_bands())
        self.assertIn("rise", panel.bands_note.text())
        panel.remove_band()
        self.assertEqual(len(panel.bands_from_editor()[1]), 3)
        prefs = App.ParamGet(sim.SIM_PREFS)
        self.addCleanup(sim.reset_default_bands)
        panel.load_bands([1.5, 3.0], ["#ff0000", "#ffcc00", "#00aa00"])
        self.assertTrue(panel.save_bands_default())
        self.assertEqual(prefs.GetString("SFThresholds"), "1.5;3")
        self.assertEqual(sim.default_bands()[0], [1.5, 3.0])
        self.assertTrue(panel.show("stress"))
        self.assertEqual(self.sg.shown_field(analysis), "stress")
        self.assertTrue(panel.show("displacement"))
        # the probe: a selection with a point on the result mesh reads the nearest node
        panel.probe_button.setChecked(True)
        self.assertTrue(self.sg.probing())
        observer = self.sg._state["probe"]
        observer.addSelection(doc.Name, result.Mesh.Name, "", (100.0, 5.0, 5.0))
        self.assertIsNotNone(observer.last)
        self.assertIn("Node", panel.probe_label.text())
        self.assertGreater(observer.last["displacement"], 0.9 * sim.result_extrema(result)["max_disp"])
        panel.probe_button.setChecked(False)
        self.assertFalse(self.sg.probing())
        # S4: the LaTeX report with three renders, compiled in the background when a compiler is there
        files = panel.report(folder=tmp)
        self.assertTrue(os.path.isfile(files["tex"]))
        for kind in ("stress", "displacement", "safety"):
            self.assertTrue(os.path.isfile(os.path.join(files["dir"], f"fig_{kind}.png")), kind)
        with open(files["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count(r"\includegraphics"), 3)
        self.assertIn("Min safety factor", text)
        self.assertIn(r"\textbf{Tresca} equivalent stress", text)
        self.assertIn("above 4", text)
        if sim.find_tex()[0]:
            self.assertTrue(self.sg.wait_for_report(600))
            pump()
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), files.get("message"))
            self.assertEqual(files["pdf"], os.path.join(tmp, os.path.basename(files["dir"]) + ".pdf"))
            self.assertEqual(files["pdf"], files["target"])
            self.assertGreater(os.path.getsize(files["pdf"]), 20000)  # with the three renders
            self.assertIn("Report:", panel.probe_label.text())
        else:
            self.assertIn("No LaTeX compiler", files["message"])
        panel.release()

    def test_ribbon_commands_in_the_fem_workbench(self):
        import FreeCADGui as Gui

        import bruceworx_workspaces as ws

        custom = ws.custom_toolbars(ws.definition())
        self.assertIn("FemWorkbench", custom)
        names = [name for _gid, name, _cmds in custom["FemWorkbench"]]
        self.assertEqual(names, ["Simulation · Study", "Simulation · Setup", "Simulation · Mesh", "Simulation · Solve", "Simulation · Results"])
        results = [cmds for _gid, name, cmds in custom["FemWorkbench"] if name == "Simulation · Results"][0]
        for name in ("BruceWorx_ShowSafetyFactor", "BruceWorx_SafetyFactorBands", "BruceWorx_StudyReport"):
            self.assertIn(name, results)
        self.assertIn("BruceWorx_SolveStudy", Gui.listCommands())


if __name__ == "__main__":
    unittest.main()
