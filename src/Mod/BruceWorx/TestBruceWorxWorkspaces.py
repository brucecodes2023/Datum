# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the workspaces (bruceworx_workspaces) and the toolbar rows.

The pure-logic tests run headless; TestRibbonInWindow needs a GUI FreeCAD.
"""

import time
import unittest

import FreeCAD as App

import bruceworx_toolbars as toolbars
import bruceworx_workspaces as ws


class FakeGroup:
    """Just enough of ParameterGrp for the custom-toolbar writer."""

    def __init__(self):
        self.groups = {}
        self.values = {}

    def GetGroup(self, name):
        return self.groups.setdefault(name, FakeGroup())

    def GetGroups(self):
        return list(self.groups)

    def RemGroup(self, name):
        self.groups.pop(name, None)

    def SetString(self, name, value):
        self.values[name] = value

    def SetBool(self, name, value):
        self.values[name] = value


class FakeParams:
    def __init__(self):
        self.root = FakeGroup()

    def __call__(self, path):
        group = self.root
        for part in path.split(":", 1)[1].split("/"):
            group = group.GetGroup(part)
        return group


class TestDefinition(unittest.TestCase):
    def setUp(self):
        self.defn = ws.load()

    def test_shipped_definition_is_valid(self):
        self.assertEqual(ws.validate(self.defn), [])

    def test_four_workspaces_with_shortcuts(self):
        names = [w["name"] for w in ws.workspaces(self.defn)]
        self.assertEqual(names, ["Design", "Simulation", "Manufacture", "Drawing"])
        self.assertEqual([w["shortcut"] for w in ws.workspaces(self.defn)], ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4"])

    def test_design_tabs(self):
        design = ws.workspace(self.defn, "Design")
        self.assertEqual([t["name"] for t in design["tabs"]], ["Solid", "Surface", "Mesh", "Sheet Metal", "Routing", "Utilities"])
        self.assertIn("Solid · Create", ws.tab_toolbars(design, "Solid"))
        self.assertNotIn("Surface · Create", ws.tab_toolbars(design, "Solid"))

    def test_validate_finds_problems(self):
        bad = {"workspaces": [{"name": "X", "workbench": "", "tabs": [{"name": "T", "groups": [{"caption": "C", "commands": ["A", "A"]}]}]}]}
        errors = ws.validate(bad)
        self.assertTrue(any("no workbench" in e for e in errors))
        self.assertTrue(any("twice" in e for e in errors))

    def test_workbench_tab_has_no_group_toolbars(self):
        cam = ws.workspace(self.defn, "Manufacture")
        self.assertEqual(ws.tab_toolbars(cam, "Manufacture"), [])
        self.assertEqual(ws.group_toolbars(cam), [])

    def test_simulation_tab(self):
        sim = ws.workspace(self.defn, "Simulation")
        self.assertEqual([g["caption"] for g in sim["tabs"][0]["groups"]], ["Study", "Setup", "Mesh", "Solve", "Results"])
        self.assertIn("Simulation · Results", ws.tab_toolbars(sim, "Simulation"))
        self.assertEqual(ws.place_of(self.defn, "BruceWorx_ShowSafetyFactor"), ("Simulation", "Simulation", "Results"))
        for name in ("BruceWorx_SimTemperature", "BruceWorx_SimHeatFlux", "BruceWorx_SimHeatSource", "BruceWorx_SimConvection"):
            self.assertEqual(ws.place_of(self.defn, name), ("Simulation", "Simulation", "Setup"))
        for name in ("BruceWorx_ShowTemperature", "BruceWorx_ShowHDTBands"):
            self.assertEqual(ws.place_of(self.defn, name), ("Simulation", "Simulation", "Results"))
        self.assertIn("Mechanical Boundary Conditions and Loads", ws.replaced_toolbars(self.defn))

    def test_custom_toolbars_per_workbench(self):
        custom = ws.custom_toolbars(self.defn)
        self.assertEqual(list(custom), ["PartDesignWorkbench", "FemWorkbench"])
        gid, name, commands = custom["PartDesignWorkbench"][0]
        self.assertEqual((gid, name), ("BW_Design_Solid_Create", "Solid · Create"))
        self.assertEqual(commands[0], "PartDesign_NewSketch")

    def test_replaced_toolbars_include_the_selector(self):
        replaced = ws.replaced_toolbars(self.defn)
        self.assertEqual(replaced[0], "Workbench")
        self.assertIn("Part Design Modeling Features", replaced)

    def test_more_commands_and_place(self):
        self.assertIn("PartDesign_Groove", ws.more_commands(self.defn, "Solid · Create"))
        self.assertEqual(ws.more_commands(self.defn, "Not a group"), [])
        self.assertEqual(ws.place_of(self.defn, "PartDesign_Hole"), ("Design", "Solid", "Create"))
        self.assertEqual(ws.place_of(self.defn, "Assembly_CreateBom"), ("Design", "Solid", "Assemble"))
        self.assertIsNone(ws.place_of(self.defn, "Std_Nothing"))

    def test_more_workbenches(self):
        available = ["PartDesignWorkbench", "SketcherWorkbench", "NoneWorkbench", "OpenSCADWorkbench", "FemWorkbench"]
        self.assertEqual(ws.more_workbenches(available, ["OpenSCADWorkbench"], self.defn), ["SketcherWorkbench"])

    def test_tab_names(self):
        design = ws.workspace(self.defn, "Design")
        self.assertEqual(ws.tab_names(design, "Mesh"), (["Solid", "Surface", "Mesh", "Sheet Metal", "Routing", "Utilities"], "Mesh"))
        self.assertEqual(ws.tab_names(design, "Gone")[1], "Solid")
        names, current = ws.tab_names(design, "Mesh", sketch=True)
        self.assertEqual((names[-1], current), ("Sketch", "Sketch"))


    def test_sheet_metal_tab_needs_the_addon(self):
        # MOD-06: the tab shows only while the SheetMetal add-on is loaded.
        design = ws.workspace(self.defn, "Design")
        sheet = ws.tab(design, "Sheet Metal")
        self.assertEqual(sheet["requires"], "SheetMetal")
        self.assertIn("Sheet Metal · Flat", ws.tab_toolbars(design, "Sheet Metal"))
        self.assertIn("BruceWorx_ExportFlat", sheet["groups"][2]["commands"])
        self.assertEqual(ws.place_of(self.defn, "BruceWorx_SheetMetalRules"), ("Design", "Sheet Metal", "Rules"))
        self.assertIn("SheetMetalUnfoldCmd", self.defn["load"])
        without = ws.without_missing(self.defn, lambda name: False)
        names = [t["name"] for t in ws.workspace(without, "Design")["tabs"]]
        self.assertEqual(names, ["Solid", "Surface", "Mesh", "Routing", "Utilities"])
        self.assertNotIn("Sheet Metal · Create", [n for _g, n, _c in ws.custom_toolbars(without)["PartDesignWorkbench"]])
        self.assertEqual(ws.without_missing(self.defn, lambda name: name == "SheetMetal"), self.defn)
        self.assertEqual(len(ws.workspace(self.defn, "Design")["tabs"]), 6)  # the original is not changed


class TestCustomToolbarParams(unittest.TestCase):
    def test_write_replaces_only_bw_groups(self):
        params = FakeParams()
        root = params("User parameter:BaseApp/Workbench/PartDesignWorkbench/Toolbar")
        root.GetGroup("Custom_1").SetString("Name", "Mine")
        root.GetGroup("BW_Old").SetString("Name", "Stale")
        ws.write_custom_toolbars(ws.load(), params)
        names = root.GetGroups()
        self.assertIn("Custom_1", names)
        self.assertNotIn("BW_Old", names)
        group = root.GetGroup("BW_Design_Solid_Pattern")
        self.assertEqual(group.values["Name"], "Solid · Pattern")
        self.assertEqual(group.values["BruceWorx_RectangularPattern"], ws.LOADER)
        # Commands keep the order they are written in (FreeCAD reads them in document order).
        self.assertEqual(list(group.values)[1:4], ["BruceWorx_RectangularPattern", "BruceWorx_CircularPattern", "PartDesign_Mirrored"])

    def test_remove(self):
        params = FakeParams()
        ws.write_custom_toolbars(ws.load(), params)
        ws.remove_custom_toolbars(params)
        root = params("User parameter:BaseApp/Workbench/PartDesignWorkbench/Toolbar")
        self.assertEqual([g for g in root.GetGroups() if g.startswith("BW_")], [])


class TestToolbarRows(unittest.TestCase):
    def test_two_rows_without_the_bar(self):
        self.assertEqual(toolbars.plan_breaks(["File", "View", "Sketcher", "Part Tools"]), {"Sketcher"})

    def test_workspace_bar_gets_its_own_row(self):
        names = ["File", "View", "BruceWorxWorkspaces", "Solid · Create", "Solid · Pattern"]
        self.assertEqual(toolbars.plan_breaks(names), {"BruceWorxWorkspaces", "Solid · Create"})

    def test_app_toolbar_after_tools_moves_up(self):
        names = ["File", "Sketcher", "Macro", "Part Tools"]
        self.assertEqual(toolbars.plan_rows(names), [["File", "Macro"], ["Sketcher", "Part Tools"]])
        self.assertEqual(toolbars.plan_breaks(names), {"Sketcher"})

    def test_title_row_on_top_then_workspace_bar_with_app_toolbars(self):
        names = ["File", "BruceWorxWorkspaces", "View", "BruceWorxTitleRow", "Solid \u00b7 Create"]
        self.assertEqual(
            toolbars.plan_rows(names),
            [["BruceWorxTitleRow"], ["BruceWorxWorkspaces", "File", "View"], ["Solid \u00b7 Create"]],
        )



class TestFairWidths(unittest.TestCase):
    def test_all_fit_no_limits(self):
        self.assertIsNone(toolbars.fair_widths([100, 200], [50, 50], 300))

    def test_wide_groups_give_up_room_first(self):
        widths = toolbars.fair_widths([354, 121, 232, 195, 195, 121, 121, 217], [80] * 8, 1028)
        self.assertLessEqual(sum(widths), 1028)
        self.assertGreater(sum(widths), 1028 - 8)
        # The narrow groups keep their whole width, the wide ones share a cap.
        self.assertEqual(widths[1], 121)
        self.assertEqual(widths[0], widths[2])
        self.assertTrue(all(w >= 80 for w in widths))

    def test_never_below_the_minimum(self):
        self.assertEqual(toolbars.fair_widths([100, 100], [80, 80], 120), [80, 80])

    def test_minimum_above_hint_is_the_hint(self):
        self.assertEqual(toolbars.fair_widths([60, 300], [90, 90], 200), [60, 140])


def _settle(seconds=0.6):
    import FreeCADGui as Gui

    end = time.time() + seconds
    while time.time() < end:
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(App.GuiUp, "needs a GUI FreeCAD")
class TestRibbonInWindow(unittest.TestCase):
    """UAT 25 Oct: #10 (groups without buttons after a tab switch), #2 (squeezed groups)."""

    def setUp(self):
        import FreeCADGui as Gui

        self.bar = ws.bar()
        if self.bar is None:
            self.skipTest("the workspace bar is off")
        self.mw = Gui.getMainWindow()
        self.geometry = self.mw.geometry()
        self.maximized = self.mw.isMaximized() or self.mw.isFullScreen()
        self.workbench = Gui.activeWorkbench().name()
        self.saved = ws._saved_tab("Design")
        ws.activate_workspace("Design")
        _settle()

    def tearDown(self):
        import FreeCADGui as Gui

        if self.saved:
            self.bar.select_tab(self.saved)
        if self.maximized:
            self.mw.showMaximized()
        else:
            self.mw.setGeometry(self.geometry)
        Gui.activateWorkbench(self.workbench)
        _settle()

    def shown_groups(self):
        from PySide import QtWidgets

        names = set(ws.tab_toolbars(self.bar.workspace, self.bar.current_tab()))
        return [tb for tb in self.mw.findChildren(QtWidgets.QToolBar) if tb.objectName() in names and tb.isVisible()]

    @staticmethod
    def buttons(tb):
        from PySide import QtWidgets

        skip = ("BruceWorxGroupCaption", "qt_toolbar_ext_button")
        return [b for b in tb.findChildren(QtWidgets.QToolButton) if b.parent() is tb and b.objectName() not in skip]

    def test_toolbars_ignore_the_title_strip_safe_area(self):
        import bruceworx_titlebar
        from PySide import QtCore, QtWidgets

        if bruceworx_titlebar.row() is None:
            self.skipTest("no macOS title row")
        for tb in self.mw.findChildren(QtWidgets.QToolBar):
            if tb.parent() is self.mw:
                self.assertFalse(tb.testAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea), tb.objectName())

    def test_every_tab_shows_its_buttons(self):
        names = [t["name"] for t in self.bar.workspace["tabs"]]
        for name in names + names[:1]:
            self.bar.select_tab(name)
            _settle()
            groups = self.shown_groups()
            self.assertTrue(groups, name)
            for tb in groups:
                label = f"{name}: {tb.objectName()}"
                self.assertEqual(tb.layout().geometry(), tb.contentsRect(), label)
                shown = [b for b in self.buttons(tb) if b.isVisible()]
                self.assertTrue(shown, label)
                for b in shown:
                    self.assertGreater(b.height(), 16, label)

    def test_narrow_window_shares_the_tools_row(self):
        from PySide import QtWidgets

        if self.mw.isFullScreen():
            self.skipTest("full screen")
        self.mw.showNormal()
        self.mw.setGeometry(self.geometry.x(), self.geometry.y(), 1000, max(600, self.geometry.height()))
        self.bar.select_tab("Solid")
        _settle(1.0)
        for tb in self.shown_groups():
            caption = tb.findChild(QtWidgets.QToolButton, "BruceWorxGroupCaption")
            # Every group keeps a button and its whole caption ("CON…T" was the bug).
            self.assertTrue([b for b in self.buttons(tb) if b.isVisible()], tb.objectName())
            if caption is not None:
                self.assertGreaterEqual(caption.width(), caption.sizeHint().width(), tb.objectName())
        right = max(tb.geometry().right() for tb in self.shown_groups())
        self.assertLessEqual(right, self.mw.width())


if __name__ == "__main__":
    unittest.main()
