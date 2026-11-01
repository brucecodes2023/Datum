# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Simulation workspace's window shell (SIM-07 P1): run events, the study-type ribbon, the docks.

The pure parts run headless; TestShellInWindow needs a GUI FreeCAD (run_gui_tests.py).
"""

import time
import unittest

import FreeCAD as App

import bruceworx_sim as sim
import bruceworx_sim_events as events
import bruceworx_sim_workspace as sw
import bruceworx_workspaces as ws


class Recorder:
    def __init__(self):
        self.items = []

    def __call__(self, kind, payload):
        self.items.append((kind, payload))

    def kinds(self):
        return [k for k, _p in self.items]


class TestEvents(unittest.TestCase):
    def setUp(self):
        events.clear_history()
        self.rec = Recorder()
        events.subscribe(self.rec)
        self.addCleanup(events.unsubscribe, self.rec)

    def test_emitting_without_a_listener_is_harmless(self):
        events.unsubscribe(self.rec)
        events.stage_started("Mesh")
        events.stage_finished("Mesh", 1.0, True)
        events.progress("Mesh", 2.0)
        events.log("x")
        events.message("nonsense", "t")
        events.residuals({"p": 1e-3})
        self.assertEqual(self.rec.items, [])

    def test_the_event_kinds_and_payloads(self):
        events.stage_started("Solve")
        events.progress("Solve", 1.7)
        events.stage_finished("Solve", 2.5, False)
        events.log("line\n")
        events.message("warning", "w", node_key="Load")
        events.residuals({"Ux": 0.1})
        self.assertEqual(self.rec.kinds(), ["stage_started", "progress", "stage_finished", "log", "message", "residuals"])
        p = dict(self.rec.items)
        self.assertEqual(p["progress"]["fraction"], 1.0)
        self.assertEqual((p["stage_finished"]["seconds"], p["stage_finished"]["ok"]), (2.5, False))
        self.assertEqual(p["log"]["line"], "line")
        self.assertEqual((p["message"]["level"], p["message"]["node_key"]), ("warning", "Load"))

    def test_a_broken_listener_is_dropped_not_fatal(self):
        def bad(kind, payload):
            raise RuntimeError("boom")

        events.subscribe(bad)
        events.log("a")
        events.log("b")
        self.assertEqual(self.rec.kinds(), ["log", "log"])

    def test_stage_context_and_precheck(self):
        with events.stage("Mesh"):
            pass
        with self.assertRaises(ValueError):
            with events.stage("Solve"):
                raise ValueError("no")
        self.assertEqual([p["ok"] for k, p in self.rec.items if k == "stage_finished"], [True, False])
        self.assertEqual(self.rec.items[-1][1]["level"], "error")
        events.from_precheck([("error", "No load"), ("warning", "Thin")])
        levels = [p["level"] for k, p in self.rec.items if k == "message"][-2:]
        self.assertEqual(levels, ["error", "warning"])

    def test_history_replays_for_a_late_listener(self):
        events.log("early")
        late = Recorder()
        events.subscribe(late, replay=True)
        self.addCleanup(events.unsubscribe, late)
        self.assertEqual(late.items[-1][1]["line"], "early")

    def test_foam_feed_reads_stages_log_and_residuals(self):
        import os
        import tempfile

        folder = tempfile.mkdtemp(prefix="bw-feed-")
        run = os.path.join(folder, "log.run")
        open(run, "w").write("@@ stage: blockMesh\n")
        feed = events.FoamFeed(folder)
        feed.poll()
        open(run, "a").write("@@ stage: simpleFoam\n")
        open(os.path.join(folder, "log.simpleFoam"), "w").write(
            "Time = 1\nSolving for Ux, Initial residual = 0.5, x\nTime = 2\nSolving for Ux, Initial residual = 0.25, x\n")
        feed.poll()
        feed.finish("done")
        kinds = self.rec.kinds()
        self.assertEqual(kinds.count("stage_started"), 2)
        self.assertEqual(kinds.count("stage_finished"), 2)
        self.assertEqual(feed.last_res["Ux"], 0.25)
        self.assertIn("Solving for Ux", [p["line"] for k, p in self.rec.items if k == "log"][1])
        self.assertEqual(self.rec.items[-1][1]["level"], "ok")


class TestRibbonByStudy(unittest.TestCase):
    def setUp(self):
        self.defn = ws.load()
        self.sim = ws.workspace(self.defn, "Simulation")

    def test_definition_is_valid_and_has_nine_tabs(self):
        self.assertEqual(ws.validate(self.defn), [])
        self.assertEqual([t["name"] for t in self.sim["tabs"]],
                         ["Home", "Definitions", "Geometry", "Materials", "Physics", "Mesh", "Study", "Results", "Project"])
        self.assertTrue(self.sim["byStudy"])

    def commands(self, tab, kind):
        found = ws.tab(self.sim, tab)
        return [c for g in ws.tab_groups(self.sim, tab, kind) for c in g["commands"]]

    def test_static_has_loads_but_no_airflow(self):
        physics = self.commands("Physics", "static")
        self.assertIn("FEM_ConstraintPressure", physics)
        self.assertIn("BruceWorx_SimSpin", physics)
        self.assertNotIn("BruceWorx_AirflowSetup", physics)
        self.assertNotIn("BruceWorx_SimTemperature", physics)
        results = self.commands("Results", "static")
        self.assertIn("BruceWorx_ShowSafetyFactor", results)
        self.assertNotIn("BruceWorx_AirflowResults", results)
        self.assertNotIn("BruceWorx_ModeShapes", results)

    def test_prop_wash_has_airflow_but_no_pressure(self):
        for kind in ("prop_wash", "external_flow"):
            physics = self.commands("Physics", kind)
            self.assertEqual(physics, ["BruceWorx_AirflowSetup"])
            self.assertEqual(self.commands("Results", kind)[:2], ["BruceWorx_AirflowResults", "BruceWorx_AirflowParaView"])
            self.assertNotIn("BruceWorx_ShowStress", self.commands("Results", kind))
            self.assertEqual(ws.study_tab_names(self.sim, kind), ["Home", "Definitions", "Geometry", "Physics", "Study", "Results", "Project"])

    def test_thermal_and_modal(self):
        self.assertIn("BruceWorx_SimConvection", self.commands("Physics", "thermal"))
        self.assertNotIn("FEM_ConstraintForce", self.commands("Physics", "thermal"))
        self.assertIn("BruceWorx_SimConvection", self.commands("Physics", "thermal_stress"))
        self.assertIn("FEM_ConstraintForce", self.commands("Physics", "thermal_stress"))
        self.assertIn("BruceWorx_ModeShapes", self.commands("Results", "modal"))
        self.assertNotIn("FEM_ConstraintPressure", self.commands("Physics", "modal"))

    def test_no_study_shows_home_only_with_new_study(self):
        names = ws.study_tab_names(self.sim, None)
        self.assertIn("Home", names)
        self.assertNotIn("Physics", names)
        self.assertEqual(self.commands("Home", None)[:2], ["BruceWorx_NewStudy", "BruceWorx_Study"])

    def test_all_kinds_keep_the_old_commands_somewhere(self):
        placed = {c for t in self.sim["tabs"] for g in t["groups"] for c in g["commands"] + g.get("more", [])}
        for name in ("BruceWorx_ShowHoop", "BruceWorx_StudyReport", "BruceWorx_Validation", "BruceWorx_SimHeatSource",
                     "BruceWorx_StudyRefine", "BruceWorx_SafetyFactorBands", "FEM_MaterialSolid"):
            self.assertIn(name, placed)

    def test_tab_names_use_the_kind(self):
        names, current = ws.tab_names(self.sim, "Physics", kind="prop_wash")
        self.assertNotIn("Materials", names)
        self.assertEqual(current, "Physics")
        self.assertEqual(ws.tab_names(self.sim, "Mesh", kind="prop_wash")[1], "Home")
        design = ws.workspace(self.defn, "Design")
        self.assertEqual(ws.tab_names(design, "Solid", kind="prop_wash")[0], ws.tab_names(design, "Solid")[0])

    def test_layout_mode(self):
        self.assertEqual(sw.layout_mode(1499), "stacked")
        self.assertEqual(sw.layout_mode(1500), "columns")
        self.assertIn("21px", sw.stylesheet())


def _settle(seconds=0.5):
    import FreeCADGui as Gui

    end = time.time() + seconds
    while time.time() < end:
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(App.GuiUp, "needs a GUI FreeCAD")
class TestShellInWindow(unittest.TestCase):
    def setUp(self):
        import FreeCADGui as Gui

        self.bar = ws.bar()
        if self.bar is None:
            self.skipTest("the workspace bar is off")
        self.mw = Gui.getMainWindow()
        self.workbench = Gui.activeWorkbench().name()
        self.geometry = self.mw.geometry()
        self.addCleanup(self.restore)
        ws.activate_workspace("Design")
        _settle()

    def restore(self):
        import FreeCADGui as Gui

        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        Gui.activateWorkbench(self.workbench)
        self.mw.setGeometry(self.geometry)
        _settle()

    def docks_visible(self):
        return [d.isVisible() for d in sw.docks().values()]

    def go_simulation(self):
        ws.activate_workspace("Simulation")
        _settle(0.8)

    def test_docks_appear_and_disappear_with_the_workspace(self):
        self.assertFalse(sw.active())
        self.assertFalse(any(self.docks_visible()))
        self.go_simulation()
        self.assertTrue(sw.active())
        self.assertTrue(all(self.docks_visible()))
        names = {d.objectName() for d in sw.docks().values()}
        self.assertEqual(names, {"BWSimModelBuilder", "BWSimSettings", "BWSimBottom"})
        self.assertIsNotNone(sw.model_builder_host())
        self.assertIsNotNone(sw.settings_host())
        pane = sw.messages_pane()
        self.assertEqual([pane.tabText(i) for i in range(pane.count())], ["Messages", "Progress", "Log", "Table"])
        ws.activate_workspace("Design")
        _settle(0.8)
        self.assertFalse(sw.active())
        self.assertFalse(any(self.docks_visible()))

    def test_model_tree_is_put_away_and_comes_back(self):
        import FreeCADGui as Gui

        before = sw.model_tree_shown()
        Gui.updateGui()
        self.go_simulation()
        self.assertFalse(sw.model_tree_shown())
        sw.set_model_tree(True)  # the header button
        self.assertTrue(sw.model_tree_shown())
        sw.set_model_tree(False)
        ws.activate_workspace("Design")
        _settle(0.8)
        self.assertEqual(sw.model_tree_shown(), before)

    def test_stacked_below_1500_px(self):
        if self.mw.isFullScreen():
            self.skipTest("full screen")
        self.mw.showNormal()
        self.mw.resize(1300, 800)
        self.go_simulation()
        _settle(0.6)
        self.assertEqual(sw.current_mode(), "stacked")
        mb, st = sw.docks()["BWSimModelBuilder"], sw.docks()["BWSimSettings"]
        self.assertLess(mb.geometry().bottom(), st.geometry().top() + 2)
        self.assertAlmostEqual(mb.geometry().x(), st.geometry().x(), delta=4)
        self.mw.resize(1700, 800)
        _settle(0.6)
        self.assertEqual(sw.current_mode(), "columns")
        self.assertLess(mb.geometry().right(), st.geometry().left() + 2)

    def test_view_gradient_and_triad_restored(self):
        import FreeCADGui as Gui

        group = App.ParamGet(sw.VIEW_PATH)
        before = (group.GetUnsigned("BackgroundColor2", 0), group.GetUnsigned("BackgroundColor3", 0))
        doc = App.newDocument("ShellView")
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        Gui.ActiveDocument.ActiveView.setAxisCross(False)
        _settle()
        self.go_simulation()
        self.assertEqual(group.GetUnsigned("BackgroundColor2"), sw._rgba(sw.VIEW_TOP))
        self.assertTrue(Gui.ActiveDocument.ActiveView.hasAxisCross())
        ws.activate_workspace("Design")
        _settle(0.8)
        self.assertEqual((group.GetUnsigned("BackgroundColor2", 0), group.GetUnsigned("BackgroundColor3", 0)), before)
        self.assertFalse(Gui.ActiveDocument.ActiveView.hasAxisCross())

    def test_events_reach_the_panes(self):
        self.go_simulation()
        pane = sw.messages_pane()
        events.run_started("Fake study")
        events.message("warning", "Mesh 1 is out of date", node_key="Mesh")
        events.message("error", "No load")
        events.from_precheck([("warning", "Thin wall")])
        events.stage_started("Mesh")
        events.progress("Mesh", 0.5)
        events.log("first line")
        events.log("second line")
        events.residuals({"p": 1e-2})
        _settle(0.4)
        rows = dict(pane.stage_rows() and [(r[0], r) for r in pane.stage_rows()])
        self.assertEqual(rows["Mesh"][1:3], ("running", 50.0))
        events.stage_finished("Mesh", 1.25, True)
        events.stage_started("Solve")
        events.stage_finished("Solve", 3.0, False)
        _settle(0.4)
        rows = dict((r[0], r) for r in pane.stage_rows())
        self.assertEqual(rows["Mesh"][1:], ("done", 100.0, "1.2 s"))
        self.assertIn("failed", rows["Solve"][3])
        text = " | ".join(t for _l, t in pane.message_rows())
        self.assertIn("Mesh 1 is out of date", text)
        self.assertIn("Pre-check: Thin wall", text)
        self.assertIn(("error", "No load"), pane.message_rows())
        self.assertEqual(pane.log_text(), "first line\nsecond line")
        keys = [pane.messages.item(i).data(0x0100) for i in range(pane.messages.count())]
        self.assertIn("Mesh", keys)

    def test_real_static_solve_streams_to_the_panes(self):
        import bruceworx_sim_gui as sg

        if not sim.find_ccx():
            self.skipTest("CalculiX (ccx) is not installed")
        self.go_simulation()
        pane = sw.messages_pane()
        doc, objs, _theory = sim.cantilever()
        analysis = objs["analysis"]
        _settle()
        events.run_started("mesh only")
        sim.mesh_study(analysis)
        _settle(0.3)
        self.assertEqual([r[0] for r in pane.stage_rows()], ["Mesh"])
        self.assertIn("Meshed:", " ".join(t for _l, t in pane.message_rows()))
        panel = sg.classes()["StudyPanel"](analysis)
        self.assertTrue(panel.solve(blocking=True))
        _settle(0.5)
        stages = [r[0] for r in pane.stage_rows()]
        for stage in ("Write input", "Solve", "Read results"):
            self.assertIn(stage, stages)
        self.assertTrue(all(r[1] == "done" for r in pane.stage_rows()))
        text = " ".join(t for _l, t in pane.message_rows())
        self.assertIn("Solved in", text)
        self.assertIn("Writing the CalculiX input", pane.log_text())
        panel.release()

    def test_ribbon_follows_the_study_type(self):
        from PySide import QtWidgets

        self.go_simulation()
        bar = self.bar
        self.assertIsNone(sw.active_kind())
        names = [bar.tabs.tabData(i) for i in range(bar.tabs.count())]
        self.assertEqual(names, ["Home", "Definitions", "Geometry", "Study", "Results", "Project"])
        bar.set_study_kind("static")
        _settle()
        names = [bar.tabs.tabData(i) for i in range(bar.tabs.count())]
        self.assertEqual(names, ["Home", "Definitions", "Geometry", "Materials", "Physics", "Mesh", "Study", "Results", "Project"])
        bar.select_tab("Physics")
        _settle()

        def visible_groups():
            return sorted(tb.objectName() for tb in self.mw.findChildren(QtWidgets.QToolBar)
                          if tb.parent() is self.mw and tb.objectName().startswith("Physics") and tb.isVisible())

        self.assertEqual(visible_groups(), ["Physics · Loads", "Physics · Supports"])
        bar.set_study_kind("prop_wash")
        _settle()
        names = [bar.tabs.tabData(i) for i in range(bar.tabs.count())]
        self.assertNotIn("Materials", names)
        self.assertEqual(bar.current_tab(), "Physics")
        self.assertEqual(visible_groups(), ["Physics · Airflow"])
        bar.set_study_kind(None)
        ws.activate_workspace("Design")
        _settle(0.5)
        self.assertEqual(bar.workspace["name"], "Design")


if __name__ == "__main__":
    unittest.main()
