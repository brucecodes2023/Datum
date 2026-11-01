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
        # all five structural types and the Airflow group (a heading, then External flow)
        texts = [panel.type_combo.itemText(i) for i in range(panel.type_combo.count())]
        self.assertEqual(texts, ["Static stress", "Modal frequencies", "Structural buckling", "Thermal", "Thermal stress",
                                 "— Airflow —", "External flow", "Prop wash"])
        self.assertTrue(all(panel.type_combo.model().item(i).isEnabled() for i in (0, 1, 2, 3, 4, 6, 7)))
        self.assertFalse(panel.type_combo.model().item(5).isEnabled())
        self.assertTrue(panel.modal_box.isHidden() and panel.buckling_box.isHidden() and panel.thermal_box.isHidden())
        self.assertTrue(panel.air_box.widget.isHidden())
        # a part without a material warns and the mesh size is the default
        self.assertIn("no material", panel.material_warn.text())
        self.assertAlmostEqual(panel.size_spin.value(), sim.default_mesh_size(self.box), places=3)
        steel = [c for c in sim.material_cards() if c["name"] == "CalculiX-Steel"][0]
        self.assertTrue(panel.select_material(steel["uuid"]))
        self.assertIn("E 2.1e+05 MPa", panel.material_warn.text())
        # S1: the Vector Labs library heads the picker when it is installed, the stock FEM cards after it
        headings = [panel.material_combo.itemText(i) for i in range(panel.material_combo.count())
                    if panel.material_combo.itemText(i).startswith("— ")]
        self.assertIn("— FreeCAD (FEM) —", headings)
        if any(c["library"] == sim.LIBRARY for c in sim.material_cards()):
            self.assertEqual(headings[0], "— Vector Labs —")
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
        self.assertAlmostEqual(panel.limit_spin.value(), 1.5)
        panel.disp_limit_spin.setValue(0.5)  # answer 2: reached (about 1.44) before the knee (about 1.54)
        self.assertAlmostEqual(analysis.CollapseDisplacement, 0.5)
        self.assertTrue(panel.solve(blocking=True))
        self.assertEqual(panel.modes_table.rowCount(), 4)
        self.assertEqual(panel.modes_table.item(0, 2).text(), "λ1")
        amber = eigen.STATUS_COLORS["amber"].lower()
        self.assertEqual(panel.modes_table.item(0, 1).foreground().color().name(), amber)
        self.assertIn("below the target 3", panel.summary.text())
        self.assertIn("non-conservative", panel.summary.text())
        self.assertIn("Nonlinear: collapse at", panel.summary.text())
        self.assertIn("displacement limit", panel.summary.text())
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
        self.assertIn("the displacement limit of 0.5", text)
        if sim.find_tex()[0]:
            self.assertTrue(self.sg.wait_for_report(600))
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), files.get("message"))
        panel.release()

    PETG = "b4e991ce-42af-48ad-acbc-40e7f1de3a52"  # Vector Labs PETG printed, heat deflection temperature 65 C

    def test_new_thermal_studies_from_the_panel(self):
        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("thermal"))
        self.assertFalse(panel.thermal_box.isHidden())
        self.assertFalse(panel.transient_check.isHidden())
        self.assertFalse(panel.end_spin.isEnabled())
        panel.transient_check.setChecked(True)
        self.assertTrue(panel.end_spin.isEnabled())
        panel.end_spin.setValue(300.0)
        panel.step_spin.setValue(5.0)
        panel.initial_spin.setValue(25.0)
        panel.select_material(self.PETG)
        analysis = panel.create()
        self.assertEqual(analysis.StudyType, "thermal")
        self.assertEqual((analysis.Transient, analysis.EndTime, analysis.TimeStep, analysis.InitialTemperature),
                         (True, 300.0, 5.0, 25.0))
        panel.release()
        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("thermal_stress"))
        self.assertTrue(panel.transient_check.isHidden())
        self.assertFalse(panel.reference_spin.isHidden())
        self.assertFalse(panel.measure_combo.isHidden())
        panel.reference_spin.setValue(22.0)
        stress = panel.create()
        self.assertEqual((stress.StudyType, stress.ReferenceTemperature), ("thermal_stress", 22.0))
        panel.release()
        study = self.sg.classes()["StudyPanel"](stress)
        self.assertIn("Not solved", study.summary.text())
        study.release()

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_thermal_solve_bands_probe_and_report(self):
        import bruceworx_sim_thermal as thermal

        tmp = tempfile.mkdtemp(prefix="bw-simgui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("thermal"))
        panel.transient_check.setChecked(True)
        panel.end_spin.setValue(300.0)
        panel.step_spin.setValue(10.0)
        panel.select_material(self.PETG)
        analysis = panel.create()
        panel.release()
        study = self.sg.classes()["StudyPanel"](analysis)
        # the setup commands (values given, so no dialog): 3 W in the body, convection from a preset on the sides
        made = self.sg.add_thermal_object("source", analysis, values={"watts": 3.0})
        self.assertEqual(len(made), 1)
        made = self.sg.add_thermal_object("convection", analysis, values={"h": 25.0, "ambient_c": 20.0},
                                          refs=[(self.box, f"Face{i}") for i in range(1, 7)])
        self.assertEqual(len(made), 1)
        self.assertEqual(self.sg.add_thermal_object("temperature", analysis, values={"celsius": 1.0}, refs=[]), [])
        study.refresh()
        self.assertIn("3 W", " ".join(study.members_list.item(i).text() for i in range(study.members_list.count())))
        self.assertTrue(study.solve(blocking=True), study.progress.text())
        data = thermal.stored(analysis)
        self.assertGreater(data["tmax"], 40.0)  # 3 W into a printed PETG bar in still-ish air
        self.assertEqual(data["limit"], 65.0)
        text = study.summary.text()
        self.assertIn("Time to reach the limit", text)
        self.assertIn("Heat balance" if not data["transient"] else "Heat in", text)
        self.assertTrue(study.time_combo.count() > 5 and not study.time_combo.isHidden())
        for kind in ("temperature", "tempbands"):
            self.assertTrue(study.show(kind), kind)
            self.assertEqual(self.sg.shown_field(analysis), kind)
        study.time_combo.setCurrentIndex(2)  # an earlier time step re-colours
        self.assertEqual(self.sg.shown_field(analysis), "tempbands")
        self.assertIn("65", study.legend.text())
        # the probe moves to a clicked point without a new solve
        reading = sim.probe(sim.latest_result(analysis), App.Vector(100, 5, 5))
        reading["point"] = (100.0, 5.0, 5.0)
        study._probed(reading)
        self.assertTrue(analysis.ProbeSet)
        self.assertLess(thermal.stored(analysis)["probe_distance"], 5.0)
        self.assertIn("°C", self.sg.probe_text(reading))
        files = study.report(folder=tmp)
        for kind in ("temperature", "tempbands"):
            self.assertTrue(os.path.isfile(os.path.join(files["dir"], f"fig_{kind}.png")), kind)
        with open(files["tex"], encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count(r"\includegraphics"), 2)
        self.assertIn(r"\begin{axis}", source)
        if sim.find_tex()[0]:
            self.assertTrue(self.sg.wait_for_report(600))
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), files.get("message"))
        study.release()

    def test_simulation_preferences_page(self):
        prefs = App.ParamGet(sim.SIM_PREFS)

        def clean():
            for name in ("SFThresholds", "SFColors", "StressMeasure", "TexPath"):
                prefs.RemString(name)
            for name in ("ModalModes", "BucklingModes", "MeshWarnNodes", "MeshMaxNodes"):
                prefs.RemInt(name)
            for name in ("TargetBLF", "MeshDivisor", "NonlinearLoadLimit"):
                prefs.RemFloat(name)

        clean()
        self.addCleanup(clean)
        page = self.sg.classes()["SimPreferencesPage"]()
        self.assertEqual(page.form.windowTitle(), "Simulation")
        page.loadSettings()
        self.assertEqual(page.thresholds.text(), "1;2")
        self.assertEqual((page.modal_modes.value(), page.buckling_modes.value()), (8, 4))
        self.assertAlmostEqual(page.divisor.value(), 30.0)
        self.assertAlmostEqual(page.load_limit.value(), 1.5)
        page.load_limit.setValue(1.8)
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
        self.assertAlmostEqual(eigen.default_load_limit(), 1.8)
        self.assertAlmostEqual(sim.default_mesh_size(self.box), self.box.Shape.BoundBox.DiagonalLength / 20.0)
        new_study = self.sg.classes()["NewStudyPanel"](self.doc)
        self.assertEqual(new_study.modal_count.value(), 10)
        new_study.release()
        page.thresholds.setText("3;1")  # not rising: refused, the rest saved
        page.saveSettings()
        self.assertIn("not saved", page.note.text())
        self.assertEqual(sim.default_bands()[0], [1.0, 3.0])

    def test_approve_and_release_offer_the_study_reports(self):
        """Answer 5 (30 Oct): Approve and Release ECO tick the item's study reports as attachments."""
        import types

        try:
            from bruceworx import pdm_workspace
            from bruceworx.pdm_folder import FolderVault
        except ImportError:
            self.skipTest("hooks add-on not installed")
        import bruceworx_changes as ch

        tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-reports-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        pn = "BW-01-01001-001"
        vault = FolderVault.init(os.path.join(tmp, "v"), "Reports", actor="bruce@mac")
        vault.create_item(pn, "part", "Bracket")
        work = pdm_workspace.Workspace(vault)
        vault.checkout(pn)
        os.makedirs(os.path.dirname(work.path(pn)), exist_ok=True)
        with open(work.path(pn), "w") as handle:
            handle.write("model")
        vault.checkin(pn, work.path(pn), comment="first")
        vault.submit(pn)
        report = os.path.join(os.path.dirname(work.path(pn)), pn + "_Bracket_report.pdf")
        with open(report, "wb") as handle:
            handle.write(b"%PDF-1.5")
        s = types.SimpleNamespace(vault=vault, work=work, invalidate=lambda: None)
        panel = ch._classes()["ApprovePanel"](s, pn)
        self.assertEqual(panel.files.count(), 1)
        self.assertIn(pn + "_Bracket_report.pdf (", panel.files.item(0).text())
        self.assertEqual(panel.attachments(), [report])
        from PySide import QtCore

        panel.files.item(0).setCheckState(QtCore.Qt.Unchecked)  # untick: not attached
        self.assertEqual(panel.attachments(), [])
        panel.files.add_file(report)
        self.assertEqual(panel.attachments(), [report])
        self.assertEqual(ch._reports(s, [pn, pn]), [report])
        panel.finish()

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_rotor_spin_contours_section_range_and_export(self):
        """SIM-04: + Spin, the cylindrical stress fields, colour map, fixed range, section, export and picture."""
        import FreeCADGui as Gui
        from PySide import QtWidgets

        import bruceworx_sim_post as post
        import bruceworx_validation_cases as vc

        tmp = tempfile.mkdtemp(prefix="bw-rotor-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        close_all()
        setup = vc.build_rotating_disc({"a_mm": 10.0, "b_mm": 50.0, "thickness_mm": 2.0, "rpm": 30000.0, "E_MPa": 210000.0,
                                        "nu": 0.3, "density": 7850.0}, 6.0)
        analysis = setup.analysis
        doc = setup.doc
        Gui.getDocument(doc.Name).activeView().viewIsometric()
        pump()
        panel = self.sg.classes()["StudyPanel"](analysis)
        texts = [b.text() for b in panel.form[0].findChildren(QtWidgets.QPushButton)]
        self.assertIn("+ Spin", texts)
        self.assertTrue(any("Spin 30000 rpm" in panel.members_list.item(i).text() for i in range(panel.members_list.count())))
        # + Spin from a script: a second load about the model's own axis; the pre-check still passes
        spin = self.sg.add_spin_object(analysis, rpm=1000.0, axis="z", panel=panel)
        self.assertIsNotNone(spin)
        self.assertIn("1000 rpm", spin.Label)
        doc.removeObject(spin.Name)
        self.assertTrue(panel.solve(blocking=True))
        result = sim.latest_result(analysis)
        self.assertIsNotNone(result)
        # the cylindrical fields colour the result mesh, by node, with the study's colour map
        self.assertTrue(panel.show("hoop"))
        self.assertEqual(self.sg.shown_field(analysis), "hoop")
        self.assertEqual(result.Mesh.ViewObject.ColorMode, "ByNode")
        self.assertIn("Hoop stress", panel.legend.text())
        self.assertIn("spin load", panel.axis_label.text())
        for kind in ("radial", "axial", "shear"):
            self.assertTrue(panel.show(kind))
        panel.cmap_combo.setCurrentIndex(panel.cmap_combo.findData("Turbo"))
        self.assertEqual(post.study_colormap(analysis), "Turbo")
        self.assertIn("Turbo", panel.legend.text())
        # the fixed range is remembered for the study and the field
        panel.show("hoop")
        panel.range_min.setValue(0.0)
        panel.range_max.setValue(300.0)
        panel.range_check.setChecked(True)
        self.assertEqual(post.fixed_range(analysis, "hoop"), (0.0, 300.0))
        self.assertIn("fixed range", panel.legend.text())
        self.assertIsNone(post.fixed_range(analysis, "radial"))
        # von Mises keeps FEM's own colouring until the study asks for something else; with Turbo it is painted by node
        self.assertTrue(panel.show("stress"))
        self.assertEqual(result.Mesh.ViewObject.ColorMode, "ByNode")
        # a quarter model has nothing left after a quarter cut: refused with the reason, the whole mesh stays
        panel.show("hoop")
        panel.section_combo.setCurrentIndex(panel.section_combo.findData("quarter"))
        self.assertIsNone(doc.getObject("SectionMesh_" + analysis.Name))
        self.assertTrue(result.Mesh.ViewObject.Visibility)
        panel.section_combo.setCurrentIndex(panel.section_combo.findData("none"))
        # the quarter section of a whole wheel: a mesh object of the elements that stay, the whole result mesh hidden
        import Part

        full = App.newDocument("FullWheel")
        wheel = vc.shape_object(full, "Wheel", Part.makeCylinder(30.0, 6.0).cut(Part.makeCylinder(8.0, 6.0)))
        fobjs = sim.new_study(full, sim.StudyPlan(name="Wheel", model=wheel, material=vc.material(), max_size=8.0))
        fan = fobjs["analysis"]
        post.add_spin(fan, 20000.0, axis="z")
        self.sg.hold_for_spin_object(fan)  # the + Hold for spin helper: axial face + 3-2-1 points (a Fixed bore is an error)
        self.assertEqual(len([o for o in fan.Group if o.Name.startswith("Hold")]), 3, [o.Name for o in fan.Group])
        fresult = sim.solve(fan, os.path.join(tmp, "full"))
        post.set_study_section(fan, "quarter")
        self.assertTrue(self.sg.show_field(fan, "hoop", fresult))
        cut = full.getObject("SectionMesh_" + fan.Name)
        self.assertIsNotNone(cut)
        whole = fresult.Mesh.FemMesh.VolumeCount
        self.assertTrue(0 < cut.FemMesh.VolumeCount < whole)
        self.assertAlmostEqual(cut.FemMesh.VolumeCount / whole, 0.75, delta=0.1)  # a quarter is cut away
        self.assertTrue(cut.ViewObject.Visibility)
        self.assertFalse(fresult.Mesh.ViewObject.Visibility)
        self.assertEqual(cut.ViewObject.ColorMode, "ByNode")
        post.set_study_section(fan, "none")
        self.assertTrue(self.sg.show_field(fan, "hoop", fresult))
        self.assertIsNone(full.getObject("SectionMesh_" + fan.Name))
        self.assertTrue(fresult.Mesh.ViewObject.Visibility)
        post.set_study_section(fan, "half")
        self.assertTrue(self.sg.show_field(fan, "stress", fresult))
        self.assertTrue(self.sg.show_field(fan, "safety", fresult))  # the safety bands drop the section again
        self.assertIsNone(full.getObject("SectionMesh_" + fan.Name))
        # a picked axis replaces the spin axis, and "Spin axis" brings it back
        post.set_study_axis(analysis, ((5.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        panel._show_axis()
        self.assertIn("picked", panel.axis_label.text())
        panel.pick_axis(clear=True)
        self.assertIn("spin load", panel.axis_label.text())
        # export, ParaView script and picture
        vtu = panel.export_vtu(os.path.join(tmp, "rotor.vtu"))
        self.assertTrue(os.path.isfile(vtu))
        self.assertIn("Hoop", [post.read_vtu(vtu).GetPointData().GetArrayName(i) for i in range(12)])
        script = post.write_paraview_script(analysis, vtu, "hoop", os.path.join(tmp, "view.py"))
        self.assertIn("Hoop", open(script, encoding="utf-8").read())
        png = panel.picture(os.path.join(tmp, "hoop.png"))
        self.assertTrue(png and os.path.isfile(png))
        panel.release()

    def test_ribbon_commands_in_the_fem_workbench(self):
        import FreeCADGui as Gui

        import bruceworx_workspaces as ws

        custom = ws.custom_toolbars(ws.definition())
        self.assertIn("FemWorkbench", custom)
        names = [name for _gid, name, _cmds in custom["FemWorkbench"]]
        self.assertEqual(names, ["Simulation · Study", "Simulation · Setup", "Simulation · Mesh", "Simulation · Solve", "Simulation · Results", "Simulation · Validation"])
        results = [cmds for _gid, name, cmds in custom["FemWorkbench"] if name == "Simulation · Results"][0]
        for name in ("BruceWorx_ShowSafetyFactor", "BruceWorx_ShowHoop", "BruceWorx_SafetyFactorBands", "BruceWorx_StudyReport"):
            self.assertIn(name, results)
        setup = [cmds for _gid, name, cmds in custom["FemWorkbench"] if name == "Simulation · Setup"][0]
        self.assertIn("BruceWorx_SimSpin", setup)
        self.assertIn("BruceWorx_SolveStudy", Gui.listCommands())


if __name__ == "__main__":
    unittest.main()
