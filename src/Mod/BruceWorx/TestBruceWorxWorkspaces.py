# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the workspaces (bruceworx_workspaces) and the toolbar rows."""

import unittest

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
        self.assertEqual([t["name"] for t in design["tabs"]], ["Solid", "Surface", "Mesh", "Utilities"])
        self.assertIn("Solid · Create", ws.tab_toolbars(design, "Solid"))
        self.assertNotIn("Surface · Create", ws.tab_toolbars(design, "Solid"))

    def test_validate_finds_problems(self):
        bad = {"workspaces": [{"name": "X", "workbench": "", "tabs": [{"name": "T", "groups": [{"caption": "C", "commands": ["A", "A"]}]}]}]}
        errors = ws.validate(bad)
        self.assertTrue(any("no workbench" in e for e in errors))
        self.assertTrue(any("twice" in e for e in errors))

    def test_workbench_tab_has_no_group_toolbars(self):
        sim = ws.workspace(self.defn, "Simulation")
        self.assertEqual(ws.tab_toolbars(sim, "Simulation"), [])
        self.assertEqual(ws.group_toolbars(sim), [])

    def test_custom_toolbars_per_workbench(self):
        custom = ws.custom_toolbars(self.defn)
        self.assertEqual(list(custom), ["PartDesignWorkbench"])
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
        self.assertEqual(ws.tab_names(design, "Mesh"), (["Solid", "Surface", "Mesh", "Utilities"], "Mesh"))
        self.assertEqual(ws.tab_names(design, "Gone")[1], "Solid")
        names, current = ws.tab_names(design, "Mesh", sketch=True)
        self.assertEqual((names[-1], current), ("Sketch", "Sketch"))


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


if __name__ == "__main__":
    unittest.main()
