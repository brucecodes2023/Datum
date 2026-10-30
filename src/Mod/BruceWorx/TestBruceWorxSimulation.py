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
import bruceworx_sim_eigen as eigen


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
        # modal and buckling are available; thermal and thermal stress are listed as planned and disabled
        texts = [panel.type_combo.itemText(i) for i in range(panel.type_combo.count())]
        self.assertEqual(texts[:3], ["Static stress", "Modal frequencies", "Structural buckling"])
        self.assertTrue(all("(planned)" in t for t in texts[3:]))
        self.assertTrue(panel.type_combo.model().item(1).isEnabled())
        self.assertFalse(panel.type_combo.model().item(3).isEnabled())
        self.assertTrue(panel.modal_box.isHidden() and panel.buckling_box.isHidden())
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
        self.assertIn("Away from the supports", panel.summary.text())
        self.assertIn("Solved in", panel.progress.text())
        self.assertEqual(self.sg.shown_field(analysis), "safety")
        # the banded colours, crisp: one colour per element (its lowest node's band); ElementColor has no
        # getter (ViewProviderFemMeshPy), ElementColorArray is the per-face list
        self.assertEqual(result.Mesh.ViewObject.ColorMode, "ByElement")

        def shown_colors():
            return {tuple(round(c, 2) for c in col[:3]) for col in result.Mesh.ViewObject.ElementColorArray}

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

    def test_new_modal_and_buckling_studies_from_the_panel(self):
        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("modal"))
        self.assertFalse(panel.modal_box.isHidden())
        self.assertTrue(panel.measure_combo.isHidden())  # the safety factor is a static matter
        self.assertEqual(panel.modal_count.value(), 8)
        self.assertEqual(panel.fmin_spin.text(), "none")
        panel.modal_count.setValue(6)
        panel.fmax_spin.setValue(2000.0)
        panel.prestressed_check.setChecked(True)
        panel.name_edit.setText("Vibes")
        analysis = panel.create()
        self.assertEqual(analysis.StudyType, "modal")
        self.assertEqual((analysis.ModeCount, analysis.FrequencyMax, analysis.Prestressed), (6, 2000.0, True))
        panel.release()
        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("buckling"))
        self.assertFalse(panel.buckling_box.isHidden())
        self.assertEqual(panel.buckling_count.value(), 4)
        self.assertAlmostEqual(panel.target_spin.value(), 3.0)
        panel.nonlinear_check.setChecked(True)
        analysis = panel.create()
        self.assertEqual((analysis.StudyType, analysis.ModeCount, analysis.Nonlinear), ("buckling", 4, True))
        panel.release()
        study = self.sg.classes()["StudyPanel"](analysis)  # its results box is the mode table
        self.assertEqual(study.modes_table.columnCount(), 3)
        self.assertIn("Not solved", study.summary.text())
        study.release()

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_modal_solve_table_playback_timeline_and_report(self):
        import FreeCADGui as Gui

        import bruceworx_motion as bm

        tmp = tempfile.mkdtemp(prefix="bw-simgui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        close_all()
        doc, objs, _theory = sim.cantilever(name="Modes", study_type="modal")
        analysis = objs["analysis"]
        Gui.getDocument(doc.Name).activeView().viewIsometric()
        panel = self.sg.classes()["StudyPanel"](analysis)
        self.assertTrue(panel.solve(blocking=True))
        self.assertEqual(panel.modes_table.rowCount(), 8)
        self.assertIn("First mode", panel.summary.text())
        self.assertEqual(panel.modes_table.item(0, 0).text(), "1")
        self.assertAlmostEqual(float(panel.modes_table.item(0, 1).text()), 833.65, delta=1.0)
        results = eigen.mode_results(analysis)
        self.assertIs(self.sg.shown_mode(analysis), results[0])  # the first mode is shown after a solve
        # a row shows its mode at 10 % of the diagonal
        self.assertTrue(panel.select_mode(3))
        self.assertIs(self.sg.shown_mode(analysis), results[2])
        self.assertTrue(results[2].Mesh.ViewObject.Visibility)
        self.assertFalse(results[0].Mesh.ViewObject.Visibility)
        panel.scale_spin.setValue(20.0)
        self.assertAlmostEqual(analysis.ModeScale, 0.2)
        # play and pause
        panel.play_button.setChecked(True)
        self.assertTrue(panel.player.playing())
        pump(6)
        self.assertGreater(panel.player.frame, 0)
        panel.play_button.setChecked(False)
        self.assertFalse(panel.player.playing())
        # the excitation check: 25 000 rpm x 2 blades = 833 Hz, modes 1 and 2 amber
        panel.excite_check.setChecked(True)
        panel.rpm_min.setValue(20000)
        panel.rpm_max.setValue(30000)
        amber = eigen.STATUS_COLORS["amber"].lower()
        colors = [panel.modes_table.item(r, 1).background().color().name() for r in range(3)]
        self.assertEqual(colors[:2], [amber, amber])
        self.assertNotEqual(colors[2], amber)
        self.assertIn("Modes 1, 2 inside the excitation band", panel.summary.text())
        # the selected mode on the Motion Study timeline: frames, scrub, Save frames
        panel.select_mode(1)
        timeline = panel.open_timeline()
        pump()
        self.assertIsNotNone(timeline)
        self.assertEqual(timeline.frames, 50)  # 2 cycles of 1 s at 25 fps
        timeline.fps_spin.setValue(5)
        self.assertEqual(timeline.frames, 10)
        timeline.go(2)
        import math

        self.assertAlmostEqual(timeline.factor(2), timeline.scale * math.sin(2 * math.pi * 2 / 5), places=9)
        self.assertEqual(timeline.scrub.value(), 2)
        self.assertIn("0.40 s", timeline.time_label.text())
        timeline.mode.setCurrentText("Ping-pong")
        timeline.buttons["play"].setChecked(True)
        pump(4)
        timeline.buttons["play"].setChecked(False)
        frames_dir = os.path.join(tmp, "frames")
        paths, _mp4 = timeline.save_frames(frames_dir)
        self.assertEqual(len(paths), 10)
        self.assertTrue(all(os.path.isfile(p) for p in paths))
        timeline.reject()
        pump(12)  # the study panel comes back
        # Motion Study with a mode selected opens that mode on the timeline
        self.sg.close_dialog()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(results[1])
        self.assertIsNotNone(bm._source_target()[0])
        opened = bm.study()
        self.assertIs(opened.result, results[1])
        opened.reject()
        Gui.Selection.clearSelection()
        self.sg.close_dialog()
        pump(12)
        self.sg.close_dialog()
        # the report: the mesh and the first three modes, the frequency table, the excitation result
        panel = self.sg.classes()["StudyPanel"](analysis)
        files = panel.report(folder=tmp)
        for kind in ("mesh", "mode1", "mode2", "mode3"):
            self.assertTrue(os.path.isfile(os.path.join(files["dir"], f"fig_{kind}.png")), kind)
        with open(files["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count(r"\includegraphics"), 4)
        self.assertIn("Modes inside a band: 1, 2", text)
        if sim.find_tex()[0]:
            self.assertTrue(self.sg.wait_for_report(600))
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), files.get("message"))
        panel.release()

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_buckling_solve_target_and_nonlinear(self):
        import FreeCADGui as Gui

        tmp = tempfile.mkdtemp(prefix="bw-simgui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(eigen.reset_default_target)
        close_all()
        doc, objs, _theory = eigen.column(max_size=8.0, options={"Nonlinear": True})
        analysis = objs["analysis"]
        Gui.getDocument(doc.Name).activeView().viewIsometric()
        panel = self.sg.classes()["StudyPanel"](analysis)
        self.assertTrue(panel.nonlinear_check.isChecked())
        self.assertTrue(panel.solve(blocking=True))
        self.assertEqual(panel.modes_table.rowCount(), 4)
        self.assertEqual(panel.modes_table.item(0, 2).text(), "λ1")
        amber = eigen.STATUS_COLORS["amber"].lower()
        self.assertEqual(panel.modes_table.item(0, 1).foreground().color().name(), amber)
        self.assertIn("below the target 3", panel.summary.text())
        self.assertIn("non-conservative", panel.summary.text())
        self.assertIn("Nonlinear: collapse at", panel.summary.text())
        panel.target_spin.setValue(1.5)  # lambda_1 = 1.73 passes a target of 1.5: green
        self.assertEqual(panel.modes_table.item(0, 1).foreground().color().name(), eigen.STATUS_COLORS["green"].lower())
        self.assertTrue(panel.save_target_default())
        self.assertAlmostEqual(eigen.default_target(), 1.5)
        files = panel.report(folder=tmp)
        for kind in ("mode1", "mode2"):
            self.assertTrue(os.path.isfile(os.path.join(files["dir"], f"fig_{kind}.png")), kind)
        with open(files["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count(r"\includegraphics"), 2)
        self.assertIn(r"\begin{axis}", text)
        if sim.find_tex()[0]:
            self.assertTrue(self.sg.wait_for_report(600))
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), files.get("message"))
        panel.release()

    def test_simulation_preferences_page(self):
        prefs = App.ParamGet(sim.SIM_PREFS)

        def clean():
            for name in ("SFThresholds", "SFColors", "StressMeasure", "TexPath"):
                prefs.RemString(name)
            for name in ("ModalModes", "BucklingModes"):
                prefs.RemInt(name)
            for name in ("TargetBLF", "MeshDivisor"):
                prefs.RemFloat(name)

        clean()
        self.addCleanup(clean)
        page = self.sg.classes()["SimPreferencesPage"]()
        self.assertEqual(page.form.windowTitle(), "Simulation")
        page.loadSettings()
        self.assertEqual(page.thresholds.text(), "1;2")
        self.assertEqual((page.modal_modes.value(), page.buckling_modes.value()), (8, 4))
        self.assertAlmostEqual(page.divisor.value(), 30.0)
        page.thresholds.setText("1;3")
        page.colors.setText("#ff0000;#ffaa00;#00aa00")
        page.measure.setCurrentIndex(page.measure.findData("Tresca"))
        page.modal_modes.setValue(10)
        page.buckling_modes.setValue(6)
        page.target.setValue(2.0)
        page.divisor.setValue(20.0)
        page.saveSettings()
        self.assertEqual(sim.default_bands(), ([1.0, 3.0], ["#FF0000", "#FFAA00", "#00AA00"]))
        self.assertEqual(sim.default_measure(), "Tresca")
        self.assertEqual((eigen.default_modes("modal"), eigen.default_modes("buckling")), (10, 6))
        self.assertAlmostEqual(eigen.default_target(), 2.0)
        self.assertAlmostEqual(sim.default_mesh_size(self.box), self.box.Shape.BoundBox.DiagonalLength / 20.0)
        new_study = self.sg.classes()["NewStudyPanel"](self.doc)
        self.assertEqual(new_study.modal_count.value(), 10)
        new_study.release()
        page.thresholds.setText("3;1")  # not rising: refused, the rest saved
        page.saveSettings()
        self.assertIn("not saved", page.note.text())
        self.assertEqual(sim.default_bands()[0], [1.0, 3.0])

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
