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
        colors = {tuple(round(c, 2) for c in col[:3]) for col in result.Mesh.ViewObject.NodeColorArray}
        self.assertIn(tuple(round(c, 2) for c in self.sg.SF_BANDS[0][1]), colors)  # red: below 1 at the root
        self.assertGreater(len(colors), 1)
        self.assertIn("below 1", panel.legend.text())
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
        # the report with three renders, beside the document (an unsaved one: the temp folder)
        files = self.sg.report_study(analysis, folder=tmp)
        self.assertTrue(os.path.isfile(files["html"]))
        with open(files["html"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count("data:image/png;base64"), 3)
        self.assertIn("Min safety factor", text)
        if files["pdf"]:
            self.assertGreater(os.path.getsize(files["pdf"]), 1000)
        panel.release()

    def test_ribbon_commands_in_the_fem_workbench(self):
        import FreeCADGui as Gui

        import bruceworx_workspaces as ws

        custom = ws.custom_toolbars(ws.definition())
        self.assertIn("FemWorkbench", custom)
        names = [name for _gid, name, _cmds in custom["FemWorkbench"]]
        self.assertEqual(names, ["Simulation · Study", "Simulation · Setup", "Simulation · Mesh", "Simulation · Solve", "Simulation · Results"])
        self.assertIn("BruceWorx_SolveStudy", Gui.listCommands())


if __name__ == "__main__":
    unittest.main()
