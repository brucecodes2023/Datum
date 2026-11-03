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

    def test_definitions_mesh_and_step_commands(self):
        for kind in ("static", "modal", "thermal_stress"):
            self.assertEqual(self.commands("Definitions", kind), ["BruceWorx_SimParameters", "BruceWorx_SimSelections"])
            self.assertIn("BruceWorx_SimLocalSize", self.commands("Mesh", kind))
            self.assertEqual(self.commands("Study", kind)[-1], "BruceWorx_SimAddStep")
        self.assertNotIn("BruceWorx_SimLocalSize", self.commands("Mesh", "prop_wash"))
        self.assertNotIn("BruceWorx_SimAddStep", self.commands("Study", "prop_wash"))
        names = {c[0] for c in sw.COMMANDS}
        for cmd in ("BruceWorx_SimParameters", "BruceWorx_SimSelections", "BruceWorx_SimLocalSize", "BruceWorx_SimAddStep"):
            self.assertIn(cmd, names)

    def test_no_study_shows_home_only_with_new_study(self):
        names = ws.study_tab_names(self.sim, None)
        self.assertIn("Home", names)
        self.assertNotIn("Physics", names)
        self.assertNotIn("Study", names)  # SIM-07: the Study and Results tabs wait for a study
        self.assertNotIn("Results", names)
        self.assertIn("Study", ws.study_tab_names(self.sim, "static"))
        self.assertIn("Results", ws.study_tab_names(self.sim, "prop_wash"))
        self.assertEqual(self.commands("Home", None)[:2], ["BruceWorx_NewStudy", "BruceWorx_Study"])

    def test_all_kinds_keep_the_old_commands_somewhere(self):
        placed = {c for t in self.sim["tabs"] for g in t["groups"] for c in g["commands"] + g.get("more", [])}
        for name in ("BruceWorx_ShowHoop", "BruceWorx_StudyReport", "BruceWorx_Validation", "BruceWorx_SimHeatSource",
                     "BruceWorx_StudyRefine", "BruceWorx_SafetyFactorBands", "FEM_MaterialSolid"):
            self.assertIn(name, placed)

    def test_stylesheet_has_the_compact_rows(self):
        self.assertIn("21px", sw.stylesheet())
        self.assertFalse(hasattr(sw, "layout_mode"))  # one stacked sidebar: no columns switch

    def test_tab_names_use_the_kind(self):
        names, current = ws.tab_names(self.sim, "Physics", kind="prop_wash")
        self.assertNotIn("Materials", names)
        self.assertEqual(current, "Physics")
        self.assertEqual(ws.tab_names(self.sim, "Mesh", kind="prop_wash")[1], "Home")
        design = ws.workspace(self.defn, "Design")
        self.assertEqual(ws.tab_names(design, "Solid", kind="prop_wash")[0], ws.tab_names(design, "Solid")[0])


def _settle(seconds=0.5):
    import FreeCADGui as Gui

    end = time.time() + seconds
    while time.time() < end:
        Gui.updateGui()
        time.sleep(0.02)


def _until(check, seconds=2.0):
    """Events until ``check()`` holds or ``seconds`` pass: the left panel hides and shows again on a tab switch, and the
    strip follows it when it is back (sooner or later, by the machine's load)."""
    import FreeCADGui as Gui

    end = time.time() + seconds
    while not check() and time.time() < end:
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

    def left_tabs(self):
        left = sw._left()
        return [(left.tabText(i), left.tabBar().isTabVisible(i)) for i in range(left.count())]

    def test_the_sidebar_is_a_tab_next_to_the_model_tree(self):
        self.assertFalse(sw.active())
        self.assertNotIn(("Simulation", True), self.left_tabs() if sw._left() is not None else [])
        doc = App.newDocument("Sidebar")  # the left panel only shows with a document open
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        _settle(0.5)
        self.go_simulation()
        self.assertTrue(sw.active())
        self.assertEqual(sorted(sw.docks()), ["BWSimBottom"])
        self.assertTrue(sw.docks()["BWSimBottom"].isVisible())
        dock = sw.sidebar_dock()
        self.assertEqual(dock.objectName(), "BWSimSidebar")
        tabs = self.left_tabs()
        self.assertEqual(len(tabs), 2)  # the model tree's tab and the Simulation tab
        self.assertEqual(tabs[-1], ("Simulation", True))
        self.assertFalse(sw._left().tabBar().isVisible())  # the native vertical bar stays off: the horizontal strip is up
        self.assertEqual(sw.strip_tabs(), [("Browser", False), ("Simulation", True)])
        self.assertLess(sw.strip().geometry().bottom(), sw._s["split"].mapTo(sw._left(), sw._s["split"].rect().topLeft()).y() + 2)
        self.assertGreater(sw._left().width(), 380)
        self.assertTrue(sw.sidebar_shown())  # entering selects the Simulation tab
        self.assertIsNotNone(sw.model_builder_host())
        self.assertIsNotNone(sw.settings_host())
        # the Model Builder is over Settings in one splitter, with a splitter handle between them
        split = sw._s["split"]
        self.assertEqual(split.count(), 2)
        mb, st = sw.model_builder_host(), sw.settings_host()
        self.assertLess(mb.mapTo(split, mb.rect().bottomLeft()).y(), st.mapTo(split, st.rect().topLeft()).y() + 2)
        pane = sw.messages_pane()
        self.assertEqual([pane.tabText(i) for i in range(pane.count())], ["Messages", "Progress", "Log", "Table"])
        # the model tree is the other tab: one click brings it back, and the Simulation tab comes back with one
        browser = [b for b in sw.strip().buttons if b.text() == "Browser"][0]
        browser.click()  # a click on the strip's Browser tab brings the model tree back
        _until(lambda: sw.strip_tabs() == [("Browser", True), ("Simulation", False)])
        self.assertFalse(sw.sidebar_shown())
        self.assertEqual(sw.strip_tabs(), [("Browser", True), ("Simulation", False)])
        [b for b in sw.strip().buttons if b.text() == "Simulation"][0].click()
        _until(lambda: sw.strip_tabs() == [("Browser", False), ("Simulation", True)])
        self.assertTrue(sw.sidebar_shown())
        self.assertEqual(sw.strip_tabs(), [("Browser", False), ("Simulation", True)])
        ws.activate_workspace("Design")
        _settle(0.8)
        self.assertFalse(sw.active())
        self.assertFalse(sw.docks()["BWSimBottom"].isVisible())
        self.assertEqual(self.left_tabs()[-1], ("Simulation", False))
        self.assertFalse(sw._left().tabBar().isVisible())
        self.assertEqual(sw.strip_tabs(), [])  # outside the workspace: no strip, only the Browser
        self.assertEqual(sw._left().currentIndex(), 0)
        self.assertEqual(App.ParamGet(sw.VIEW_PATH).GetBool("DockOverlayHideTabBar", True), True)  # the fork's setting is untouched

    def test_the_model_tree_is_not_put_away(self):
        import FreeCADGui as Gui

        before = Gui.setOverlayAutoHide("OverlayLeft", False)
        self.go_simulation()
        Gui.updateGui()
        self.assertFalse(Gui.setOverlayAutoHide("OverlayLeft", False))  # still not auto hidden
        Gui.setOverlayAutoHide("OverlayLeft", before)
        ws.activate_workspace("Design")
        _settle(0.8)
        self.assertEqual(Gui.setOverlayAutoHide("OverlayLeft", before), before)

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
        self.assertEqual(names, ["Home", "Definitions", "Geometry", "Project"])  # no Study or Results tab without a study
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


STEEL = {"uuid": "", "name": "Steel (test)", "library": "", "E": 210000.0, "nu": 0.3, "yield": 355.0, "uts": None,
         "density": 7850.0}


@unittest.skipUnless(App.GuiUp, "needs a GUI FreeCAD")
class TestPanesInWindow(unittest.TestCase):
    """The Model Builder tree and the Settings form (SIM-07 P1) in the real window."""

    def setUp(self):
        import FreeCADGui as Gui
        import bruceworx_sim_panes as panes

        self.panes = panes
        self.bar = ws.bar()
        if self.bar is None:
            self.skipTest("the workspace bar is off")
        self.mw = Gui.getMainWindow()
        self.workbench = Gui.activeWorkbench().name()
        self.geometry = self.mw.geometry()
        self.addCleanup(self.restore)
        ws.activate_workspace("Design")
        _settle()
        ws.activate_workspace("Simulation")
        _settle(0.8)
        self.assertTrue(panes.attached())

    def restore(self):
        import FreeCADGui as Gui

        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        Gui.activateWorkbench(self.workbench)
        self.mw.setGeometry(self.geometry)
        _settle()

    def show(self, study):
        import FemGui

        FemGui.setActiveAnalysis(study)
        self.panes.set_study(study)
        _settle(0.2)

    def keys(self):
        return [r[0] for r in self.panes.rows()]

    def test_trees_for_the_study_types(self):
        import bruceworx_cfd as cfd

        doc = App.newDocument("Kinds")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        static = sim.new_study(doc, sim.StudyPlan(name="S", study_type="static", model=box, material=dict(STEEL)))["analysis"]
        modal = sim.new_study(doc, sim.StudyPlan(name="M", study_type="modal", model=box, material=dict(STEEL)))["analysis"]
        thermal = sim.new_study(doc, sim.StudyPlan(name="T", study_type="thermal", model=box, material=dict(STEEL)))["analysis"]
        wash = cfd.new_airflow_study(doc, box, "wash", kind="prop_wash")
        expect = {static: ["results/stress", "results/displacement", "results/safety", "step/main", "mesh/main"],
                  modal: ["results/modes", "step/main"], thermal: ["results/temperature", "phys/InitialTemperature"],
                  wash: ["phys/farfield", "phys/disks", "mesh/main", "results/pressure"]}
        for study, wanted in expect.items():
            self.show(study)
            keys = self.keys()
            for key in wanted:
                self.assertIn(key, keys, f"{study.Label}: {key}")
            self.assertEqual(keys[0], "study")
            self.assertEqual(self.panes.row_height(), 21)
        self.assertNotIn("results/stress", self.keys())  # the prop-wash tree
        self.assertEqual(self.panes.current_key(), "study")

    def test_marks_follow_an_edit_and_a_wrong_unit_writes_nothing(self):
        import bruceworx_sim_model as model

        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        self.assertTrue(self.panes.compute("mesh/main"))
        self.assertTrue(self.panes.compute("step/main", blocking=True))
        _settle(0.4)
        self.panes.flush_now()
        marks = self.panes.marks()
        for key in ("mesh/main", "step/main", "results/stress", "phys/TipLoad", "phys/Fixed"):
            self.assertEqual(marks[key], "ok", key)
        self.assertTrue(self.panes.select_key("phys/TipLoad"))
        self.assertIn("Force", self.panes.field_names())
        self.assertEqual(self.panes.field_text("Force"), "1000 N")
        before = len(doc.UndoNames)
        self.assertTrue(self.panes.edit("Force", "1500 N"))
        self.assertEqual(len(doc.UndoNames), before + 1)  # one undo step
        self.panes.flush_now()
        marks = self.panes.marks()
        self.assertEqual(marks["step/main"], "stale")
        self.assertEqual(marks["results/stress"], "stale")
        self.assertEqual(marks["mesh/main"], "ok")
        self.assertEqual(marks["phys/TipLoad"], "ok")
        self.assertEqual(marks["results"], "stale")
        # a wrong unit: the error shows under the field and nothing is written
        self.assertFalse(self.panes.edit("Force", "5 mm"))
        self.assertIn("expected a value in N", self.panes.field_error("Force"))
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "Force"), 1500.0)
        self.assertEqual(len(doc.UndoNames), before + 1)
        self.assertTrue(self.panes.edit("Force", "2 kN"))
        self.assertEqual(self.panes.field_error("Force"), "")
        self.assertEqual(self.panes.field_text("Force"), "2000 N")
        self.assertFalse(self.panes.edit("Force", "-3 N"))  # out of range
        self.assertIn("below", self.panes.field_error("Force"))

    def test_face_picking_adds_and_removes(self):
        import bruceworx_sim_model as model

        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        self.assertTrue(self.panes.select_key("phys/Fixed"))
        node = model.find_node(study, "phys/Fixed")
        self.assertEqual(model.get_value(node, "References"), [("Beam", "Face1")])
        self.assertEqual(self.panes.field_text("References"), ["Beam \u00b7 Face1"])
        self.assertIn(("Beam", "Face1"), self.panes.highlighted())
        self.assertTrue(self.panes.pick("Beam", "Face4"))
        self.assertEqual(model.get_value(node, "References"), [("Beam", "Face1"), ("Beam", "Face4")])
        self.assertEqual(sorted(self.panes.highlighted()), [("Beam", "Face1"), ("Beam", "Face4")])
        self.assertEqual(len(self.panes.field_text("References")), 2)
        self.assertTrue(self.panes.pick("Beam", "Face1"))  # a click on a listed face takes it out
        self.assertEqual(model.get_value(node, "References"), [("Beam", "Face4")])
        self.assertEqual(self.panes.highlighted(), [("Beam", "Face4")])
        self.assertFalse(self.panes.pick("Beam", ""))  # not an element
        self.assertTrue(self.panes.select_key("study"))  # a node without a selection ignores clicks
        self.assertFalse(self.panes.pick("Beam", "Face2"))
        self.assertEqual(model.get_value(node, "References"), [("Beam", "Face4")])

    def test_compute_from_the_panes_and_result_nodes(self):
        import bruceworx_sim_gui as sg
        import bruceworx_sim_post as post

        if not sim.find_ccx():
            self.skipTest("CalculiX (ccx) is not installed")
        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        pane = sw.messages_pane()
        self.assertEqual(self.panes.marks()["step/main"], "todo")  # nothing solved yet
        self.assertTrue(self.panes.select_key("mesh/main"))
        self.assertIn("Compute", self.panes._p["buttons"])
        self.panes._p["buttons"]["Compute"].click()
        _settle(0.5)
        self.assertEqual(self.panes.marks()["mesh/main"], "ok")
        self.assertIn("Meshed:", " ".join(t for _l, t in pane.message_rows()))
        self.assertTrue(self.panes.select_key("study"))
        self.panes._p["buttons"]["Compute"].click()
        end = time.time() + 120
        while time.time() < end and (self.panes.running() or "Solved in" not in " ".join(t for _l, t in pane.message_rows())):
            _settle(0.3)
        _settle(0.6)
        self.panes.flush_now()
        stages = [r[0] for r in pane.stage_rows()]
        self.assertIn("Solve", stages)
        self.assertIn("Read results", stages)
        self.assertEqual(self.panes.marks()["step/main"], "ok")
        self.assertEqual(self.panes.marks()["study"], "ok")
        self.assertIn("Solved in", " ".join(t for _l, t in pane.message_rows()))
        # a result node shows its field; its form has the quantity and the fixed range
        self.assertTrue(self.panes.select_key("results/stress"))
        self.assertEqual(self.panes.shown_plot(), "results/stress")
        self.assertEqual(sg.shown_field(study), "stress")
        self.assertIn("StressMeasure", self.panes.field_names())
        self.assertTrue(self.panes.set_range(0, 120))
        self.assertEqual(post.fixed_range(study, "vonMises"), (0.0, 120.0))
        self.assertTrue(self.panes.select_key("results/displacement"))
        self.assertEqual(sg.shown_field(study), "displacement")
        self.assertTrue(self.panes.select_key("results/safety"))
        self.assertEqual(sg.shown_field(study), "safety")
        self.assertIn("Plot", self.panes._p["buttons"])

    def test_blocking_solve_sends_the_solver_output_to_the_log(self):
        if not sim.find_ccx():
            self.skipTest("CalculiX (ccx) is not installed")
        doc, objs, _theory = sim.cantilever()
        self.show(objs["analysis"])
        self.assertTrue(self.panes.compute("step/main", blocking=True))
        _settle(0.4)
        text = sw.messages_pane().log_text()
        self.assertIn("Writing the CalculiX input", text)
        self.assertTrue(any(w in text for w in ("CalculiX", "Job finished", "calculix", "ccx")), text[:400])

    def test_add_delete_and_message_click(self):
        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        pane = sw.messages_pane()
        self.assertIn(("force", "Force"), self.panes.addable())
        made = self.panes.add_node("force")
        self.assertIsNotNone(made)
        key = f"phys/{made.Name}"
        self.assertIn(key, self.keys())
        self.assertEqual(self.panes.current_key(), key)  # the new node is selected, ready for faces
        self.assertEqual(self.panes.marks()[key], "bad")  # no faces yet: the pre-check's error
        self.assertTrue(self.panes.pick("Beam", "Face2"))
        self.assertEqual(self.panes.field_text("References"), ["Beam \u00b7 Face2"])
        self.panes.select_key("study")
        events.message("warning", "Force: look here", node_key=key)
        _settle(0.3)
        item = pane.messages.item(pane.messages.count() - 1)
        pane.messages.itemClicked.emit(item)
        self.assertEqual(self.panes.current_key(), key)
        self.assertTrue(self.panes.rename_node(key, "Tip load 2"))
        self.assertIn("Tip load 2", [r[1] for r in self.panes.rows()])
        self.assertTrue(self.panes.delete_node(key))
        self.assertNotIn(key, self.keys())
        doc.undo()
        _settle(0.3)
        self.panes.flush_now()
        self.assertIn(key, self.keys())

    def test_parameters_table_and_field_expressions(self):
        import bruceworx_sim_model as model

        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        self.assertTrue(self.panes.select_key("defs/params"))
        self.assertEqual(self.panes.param_rows(), [])
        self.assertTrue(self.panes.param_add("F_load", "400 N", "N", "total tip load"))
        self.assertFalse(self.panes.param_add("F_load", "1 N", "N"))
        self.assertIn("exists", self.panes.param_error())
        self.assertFalse(self.panes.param_add("w", "bogus*2", ""))
        self.assertIn("unknown name 'bogus'", self.panes.param_error())
        self.assertEqual(self.panes.param_rows()[0][:4], ["F_load", "400 N", "N", "400 N"])
        self.assertTrue(self.panes.select_key("phys/TipLoad"))
        self.assertTrue(self.panes.edit("Force", "F_load/4"))
        self.assertEqual(self.panes.field_text("Force"), "F_load/4")
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "Force"), 100.0)
        self.assertFalse(self.panes.edit("Force", "bogus/4"))
        self.assertIn("unknown name 'bogus'", self.panes.field_error("Force"))
        self.assertFalse(self.panes.edit("Force", "F_load*1 mm"))
        self.assertIn("N", self.panes.field_error("Force"))
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "Force"), 100.0)  # nothing was written
        self.assertEqual(model.get_expression(model.find_node(study, "phys/TipLoad"), "Force"), "F_load/4")
        self.assertTrue(self.panes.select_key("defs/params"))
        active = App.ActiveDocument
        if active is None:  # the FEM result import reads FreeCAD.ActiveDocument
            App.setActiveDocument(doc.Name)
        self.assertTrue(self.panes.compute("mesh/main"))
        self.assertTrue(self.panes.compute("step/main", blocking=True), f"active was {active}")
        self.panes.flush_now()
        self.assertEqual(self.panes.marks()["step/main"], "ok")
        self.assertTrue(self.panes.param_edit(0, 1, "800 N"))  # the table: expression column
        self.panes.flush_now()
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "Force"), 200.0)
        self.assertEqual(self.panes.marks()["step/main"], "stale")
        self.assertEqual(self.panes.marks()["mesh/main"], "ok")
        self.assertFalse(self.panes.param_remove("F_load"))  # the load uses it
        self.assertIn("TipLoad", self.panes.param_error())

    def test_named_selection_local_size_and_step_nodes(self):
        import bruceworx_sim_model as model

        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        sel = self.panes.add_named_selection(refs=[("Beam", ["Face2"])], label="Tip")
        key = f"defs/sel/{sel.Name}"
        self.assertEqual(self.panes.current_key(), key)
        self.assertIn(key, self.keys())
        self.assertEqual(self.panes.field_text("References"), ["Beam \u00b7 Face2"])
        self.assertTrue(self.panes.select_key("phys/TipLoad"))
        self.assertTrue(self.panes.edit("NamedSelection", "Tip"))
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "References"), [("Beam", "Face2")])
        self.assertTrue(self.panes.select_key(key))
        self.assertTrue(self.panes.pick("Beam", "Face4"))  # a click adds to the named selection; the load follows
        self.assertEqual(sorted(model.get_value(model.find_node(study, "phys/TipLoad"), "References")),
                         [("Beam", "Face2"), ("Beam", "Face4")])
        ls = self.panes.add_local_size("2 mm", refs=[("Beam", ["Face2"])], label="Tip mesh")
        self.assertIn(f"mesh/local/{ls.Name}", self.keys())
        self.assertEqual(self.panes.field_text("Size"), "2 mm")
        self.assertTrue(self.panes.delete_node(f"mesh/local/{ls.Name}"))
        self.assertNotIn(f"mesh/local/{ls.Name}", self.keys())
        self.assertTrue(self.panes.delete_node(key))
        self.assertNotIn(key, self.keys())
        self.assertEqual(model.get_value(model.find_node(study, "phys/TipLoad"), "NamedSelection"), "(own list)")
        # steps: Add step turns the stationary study into preload + eigenfrequency
        self.assertTrue(self.panes.add_step("modal"))
        self.assertIn("step/s1", self.keys())
        self.assertIn("step/s2", self.keys())
        self.assertFalse(self.panes.add_step("heat"))
        self.assertTrue(self.panes.select_key("step/s2"))
        self.assertIn("ModeCount", self.panes.field_names())

    def test_cancel_button_stop_buttons_and_time_limit(self):
        import bruceworx_sim_model as model

        doc, objs, _theory = sim.cantilever()
        study = objs["analysis"]
        self.show(study)
        self.assertTrue(self.panes.select_key("mesh/main"))
        self.assertIn("Compute", self.panes._p["buttons"])
        self.assertTrue(self.panes.field_text("timelimit").startswith("automatic ("), self.panes.field_text("timelimit"))
        self.assertTrue(self.panes.edit("MeshTimeLimit", "300"))
        self.assertEqual(self.panes.field_text("timelimit"), "5.0 min (set)")
        self.assertTrue(self.panes.edit("MeshTimeLimit", "0"))
        self.assertTrue(self.panes.field_text("timelimit").startswith("automatic ("))
        # the Mesher choice sits beside the time limit, Netgen by default
        self.assertIn("Mesher", self.panes.field_names())
        self.assertEqual(str(study.Mesher), "Netgen")
        self.assertTrue(self.panes.edit("Mesher", "Gmsh"))
        self.assertEqual(str(study.Mesher), "Gmsh")
        self.assertTrue(self.panes.edit("Mesher", "Netgen"))
        self.panes._set_running("mesh", True)  # the Mesh node's button reads Cancel while it meshes
        self.assertIn("Cancel", self.panes._p["buttons"])
        self.assertEqual(self.panes._p["buttons"]["Cancel"].text(), "Cancel")
        self.assertTrue(self.panes.busy())
        pane = sw.messages_pane()
        events.run_started("t")
        events.stage_started("Mesh")
        _settle(0.2)
        self.assertIsNotNone(pane.progress.cellWidget(0, 3))  # a running row has a stop button
        self.assertTrue(self.panes.cancel_compute())  # asks the mesh worker to stop
        events.stage_finished("Mesh", 1.0, False)
        _settle(0.2)
        self.assertIsNone(pane.progress.cellWidget(0, 3))
        import bruceworx_sim_meshworker as mw

        mw._cancel.clear()
        self.panes._set_running("mesh", False)
        self.assertIn("Compute", self.panes._p["buttons"])
        self.assertFalse(self.panes.busy())

    def test_refreshes_are_debounced_and_cheap(self):
        doc, objs, _theory = sim.cantilever()
        self.show(objs["analysis"])
        before = self.panes._p["refresh_count"]
        for _i in range(20):
            self.panes.schedule()
        _settle(0.5)
        self.assertEqual(self.panes._p["refresh_count"], before + 1)
        # an object change in the document refreshes by itself
        objs["analysis"].Document.getObject("TipLoad").Force = "1200 N"
        _settle(0.5)
        self.assertEqual(self.panes.marks().get("step/main"), "todo")  # no results yet

    def test_the_axis_triad_is_on_in_the_workspace(self):
        import FreeCADGui as Gui

        doc = App.newDocument("Triad")
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        _settle(0.8)
        self.assertTrue(Gui.ActiveDocument.ActiveView.hasAxisCross())


if __name__ == "__main__":
    unittest.main()
