# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stdlib tests for the BruceWorx preference pack. No FreeCAD build required.

    python3 src/Mod/BruceWorx/test_bruceworx_pack.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_defaults as defaults  # noqa: E402
import bruceworx_pack as pack  # noqa: E402
import ground_grid  # noqa: E402
import bruceworx_toolbars as toolbars  # noqa: E402
import bruceworx_grid as grid  # noqa: E402
import bruceworx_appearance as appearance  # noqa: E402
import bruceworx_taskcard as taskcard  # noqa: E402
import bruceworx_statusbar as statusbar  # noqa: E402


ROOT = pack.repo_root()


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class RestoreTest(unittest.TestCase):
    def test_snapshot_read_accepts_a_missing_number(self):
        # First-run snapshot calls get(..., None) for keys the user already has.
        # int(None) used to abort the pack before those keys were recorded.
        self.assertEqual(pack.param_fallback("int", None), 0)
        self.assertEqual(pack.param_fallback("uint", None), 0)
        self.assertEqual(pack.param_fallback("float", None), 0.0)
        self.assertIs(pack.param_fallback("bool", None), False)
        self.assertEqual(pack.param_fallback("int", 4), 4)

    def test_enable_then_off_restores_stock_and_user_values(self):
        store = pack.MemoryStore()
        store.set("BaseApp/Preferences/Units", "UserSchema", "int", 4)
        store.set("BaseApp/Preferences/View", "NavigationStyle", "text", "Gui::BlenderNavigationStyle")
        store.set("BaseApp/Preferences/View", "DefaultShapeLineWidth", "int", 4)
        # Gradient is absent: Off must remove it, not invent a value.
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "stock-snapshot.json")
            pack.enable(store, snap, "light")
            self.assertTrue(store.get(pack.APPLIED_GROUP, pack.APPLIED_NAME, "bool", False))
            self.assertEqual(store.get("BaseApp/Preferences/Units", "UserSchema", "int", -1), 0)
            self.assertEqual(
                store.get("BaseApp/Preferences/View", "NavigationStyle", "text", ""),
                "Gui::RevitNavigationStyle",
            )
            self.assertEqual(
                store.get("BaseApp/Preferences/MainWindow", "StyleSheet", "text", ""),
                "BruceWorx.qss",
            )
            self.assertEqual(
                store.get("BaseApp/Preferences/MainWindow", "Theme", "text", ""),
                "BruceWorx",
            )
            self.assertTrue(store.get(pack.APPLIED_GROUP, "ShowGroundGrid", "bool", False))
            self.assertEqual(pack.disable(store, snap), "snapshot")
            self.assertFalse(os.path.exists(snap))

        self.assertEqual(store.get("BaseApp/Preferences/Units", "UserSchema", "int", -1), 4)
        self.assertEqual(
            store.get("BaseApp/Preferences/View", "NavigationStyle", "text", ""),
            "Gui::BlenderNavigationStyle",
        )
        self.assertEqual(store.get("BaseApp/Preferences/View", "DefaultShapeLineWidth", "int", -1), 4)
        self.assertFalse(store.has("BaseApp/Preferences/View", "Gradient", "bool"))
        self.assertFalse(store.has("BaseApp/Preferences/MainWindow", "StyleSheet", "text"))
        self.assertFalse(store.has(pack.APPLIED_GROUP, pack.APPLIED_NAME, "bool"))
        self.assertFalse(store.has(pack.APPLIED_GROUP, "ShowGroundGrid", "bool"))
        self.assertEqual(pack.disable(store, os.path.join(tmp, "missing.json")), "noop")

    def test_reapply_does_not_overwrite_the_original_snapshot(self):
        store = pack.MemoryStore()
        store.set("BaseApp/Preferences/Units", "UserSchema", "int", 2)
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "stock-snapshot.json")
            pack.enable(store, snap, "light")
            store.set("BaseApp/Preferences/Units", "UserSchema", "int", 9)
            pack.enable(store, snap, "dark")
            self.assertEqual(
                store.get("BaseApp/Preferences/MainWindow", "Theme", "text", ""),
                "BruceWorx Dark",
            )
            pack.disable(store, snap)
            self.assertEqual(store.get("BaseApp/Preferences/Units", "UserSchema", "int", -1), 2)

    def test_theme_dialog_pack_name_is_not_saved_as_the_user_theme(self):
        store = pack.MemoryStore()
        store.set("BaseApp/Preferences/MainWindow", "Theme", "text", "BruceWorx")
        store.set("BaseApp/Preferences/MainWindow", "StyleSheet", "text", "FreeCAD.qss")
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "stock-snapshot.json")
            pack.enable(store, snap, "light")
            pack.disable(store, snap)
        self.assertFalse(store.has("BaseApp/Preferences/MainWindow", "Theme", "text"))
        self.assertEqual(
            store.get("BaseApp/Preferences/MainWindow", "StyleSheet", "text", ""),
            "FreeCAD.qss",
        )

    def test_missing_snapshot_clears_pack_keys(self):
        store = pack.MemoryStore()
        pack.apply_entries(store, pack.pack_entries("light"))
        self.assertEqual(pack.disable(store, os.path.join(tempfile.gettempdir(), "no-such-bw-snap.json")), "cleared")
        self.assertFalse(store.has(pack.APPLIED_GROUP, pack.APPLIED_NAME, "bool"))
        self.assertFalse(store.has("BaseApp/Preferences/View", "NavigationStyle", "text"))


class PackContentsTest(unittest.TestCase):
    def test_required_workbenches_are_first_and_enabled(self):
        ordered = ""
        disabled = ""
        for entry in pack.pack_entries("light"):
            if entry["name"] == "Ordered":
                ordered = entry["value"]
            if entry["name"] == "Disabled":
                disabled = entry["value"]
        names = ordered.split(",")
        self.assertEqual(names[:5], list(pack.REQUIRED_WORKBENCHES))
        for name in pack.REQUIRED_WORKBENCHES:
            self.assertNotIn(name, disabled.split(","))

    def test_no_task_watchers(self):
        # The task panel shows only the open command, not the workbench's command lists.
        for mode in ("light", "dark"):
            entries = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries(mode)}
            self.assertFalse(entries[("BaseApp/Preferences/General", "ShowTaskWatcher")])

    def test_fusion_nav_and_millimetres(self):
        entries = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries("light")}
        self.assertEqual(entries[("BaseApp/Preferences/Units", "UserSchema")], 0)
        self.assertEqual(
            entries[("BaseApp/Preferences/View", "NavigationStyle")],
            "Gui::RevitNavigationStyle",
        )
        self.assertEqual(entries[("BaseApp/Preferences/General", "AutoloadModule")], "PartDesignWorkbench")
        self.assertTrue(entries[("BaseApp/Preferences/View", "Perspective")])
        self.assertFalse(entries[("BaseApp/Preferences/View", "Orthographic")])
        self.assertEqual(entries[("BaseApp/Preferences/View", "DefaultShapeLineWidth")], 2)
        self.assertEqual(entries[("BaseApp/Preferences/View", "DefaultShapeShininess")], 40)
        self.assertEqual(entries[("BaseApp/Preferences/NaviCube", "CornerNaviCube")], 1)
        self.assertEqual(entries[("BaseApp/Preferences/NaviCube", "CubeSize")], 96)
        self.assertEqual(entries[("BaseApp/Preferences/NaviCube", "FontString")], "")
        self.assertEqual(entries[("BaseApp/Preferences/View", "RotationMode")], 1)
        self.assertTrue(entries[("BaseApp/Preferences/General", "LockToolBars")])
        self.assertTrue(entries[("BaseApp/MainWindow/Toolbars", "Part Design Modeling Features")])
        self.assertFalse(entries[("BaseApp/MainWindow/Toolbars", "Macro")])
        self.assertFalse(entries[("BaseApp/MainWindow/Toolbars", "B-Spline Tools")])
        self.assertTrue(entries[("BaseApp/MainWindow/Toolbars", "Assembly")])

    def test_dark_differs_only_in_color_and_theme(self):
        light = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries("light")}
        dark = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries("dark")}
        self.assertEqual(set(light), set(dark))
        self.assertNotEqual(
            light[("BaseApp/Preferences/View", "BackgroundColor2")],
            dark[("BaseApp/Preferences/View", "BackgroundColor2")],
        )
        self.assertEqual(dark[("BaseApp/Preferences/MainWindow", "Theme")], "BruceWorx Dark")
        self.assertEqual(light[("BaseApp/Preferences/MainWindow", "StyleSheet")], dark[("BaseApp/Preferences/MainWindow", "StyleSheet")])

    def test_committed_cfg_matches_inventory(self):
        for mode in ("light", "dark"):
            path = pack.cfg_path(mode)
            with open(path, encoding="utf-8") as handle:
                on_disk = handle.read()
            self.assertEqual(on_disk, pack.render_cfg(pack.pack_entries(mode)))
            root = ET.fromstring(on_disk)
            self.assertEqual(root.tag, "FCParameters")

    def test_off_pack_has_no_cfg(self):
        folder = os.path.join(ROOT, "src", "Gui", "PreferencePacks", "BruceWorx Off")
        self.assertTrue(os.path.isfile(os.path.join(folder, "pre.FCMacro")))
        self.assertTrue(os.path.isfile(os.path.join(folder, "post.FCMacro")))
        self.assertFalse(any(name.endswith(".cfg") for name in os.listdir(folder)))

    def test_inventory_covers_view_groups(self):
        paths = {item["path"] for item in pack.INVENTORY}
        for required in (
            "BaseApp/Preferences/View",
            "BaseApp/Preferences/View/LightSources",
            "BaseApp/Preferences/NaviCube",
            "BaseApp/Preferences/Mod/Part",
        ):
            self.assertIn(required, paths)
        names = {item["name"] for item in pack.INVENTORY}
        for required in (
            "BackgroundColor2",
            "BackgroundColor3",
            "SelectionColor",
            "HighlightColor",
            "HeadlightIntensity",
            "DefaultShapeLineWidth",
            "CubeSize",
            "ShowGroundGrid",
        ):
            self.assertIn(required, names)


class StylesheetTest(unittest.TestCase):
    def test_qss_tokens_exist_in_both_themes(self):
        qss_path = os.path.join(ROOT, "src", "Gui", "Stylesheets", "BruceWorx.qss")
        with open(qss_path, encoding="utf-8") as handle:
            qss = handle.read()
        self.assertIn("font-family", qss)
        self.assertIn("border-radius", qss)
        tokens = set(re.findall(r"@([A-Za-z][A-Za-z0-9]*)", qss))
        self.assertIn("AccentColor", tokens)
        self.assertIn("TextForegroundColor", tokens)
        self.assertIn("ToolbarButtonsPadding", tokens)
        for filename in ("BruceWorx.yaml", "BruceWorx Dark.yaml"):
            path = os.path.join(ROOT, "src", "Gui", "Stylesheets", "parameters", filename)
            with open(path, encoding="utf-8") as handle:
                defined = set()
                for line in handle:
                    line = line.split("#", 1)[0].strip()
                    if not line or ":" not in line:
                        continue
                    defined.add(line.split(":", 1)[0].strip())
            missing = tokens - defined - set(appearance.RUNTIME_TOKENS)
            self.assertFalse(missing, f"{filename} missing {sorted(missing)}")

    def test_bruceworx_panel_tokens_exist_in_freecads_themes(self):
        # Under a stock theme the BruceWorx panels asked for these and FreeCAD warned (UAT, 25 Oct).
        panel = ("CardBackgroundColor", "TextSecondaryColor", "HoverColor", "AccentSoftColor", "ErrorColor",
                 "WarnColor", "OkColor", "ChromeColor", "IconPlateColor")
        for filename in ("FreeCAD Light.yaml", "FreeCAD Dark.yaml"):
            path = os.path.join(ROOT, "src", "Gui", "Stylesheets", "parameters", filename)
            with open(path, encoding="utf-8") as handle:
                defined = {line.split(":", 1)[0].strip() for line in handle if ":" in line and not line.startswith("#")}
            self.assertFalse(set(panel) - defined, f"{filename} missing {sorted(set(panel) - defined)}")


class PackagingTest(unittest.TestCase):
    def test_package_xml_lists_packs(self):
        path = os.path.join(ROOT, "src", "Gui", "PreferencePacks", "package.xml")
        tree = ET.parse(path)
        names = [node.findtext("name") for node in tree.findall(".//preferencepack")]
        self.assertIn("BruceWorx", names)
        self.assertIn("BruceWorx Dark", names)
        self.assertIn("BruceWorx Off", names)
        off = [node for node in tree.findall(".//preferencepack") if node.findtext("name") == "BruceWorx Off"][0]
        self.assertNotEqual(off.findtext("type"), "Theme")

    def test_cmake_installs_pack_stylesheet_and_mod(self):
        packs = _read(os.path.join(ROOT, "src", "Gui", "PreferencePacks", "CMakeLists.txt"))
        for folder in ("BruceWorx", "BruceWorx Dark", "BruceWorx Off"):
            self.assertIn(f'"{folder}"', packs)
        sheets = _read(os.path.join(ROOT, "src", "Gui", "Stylesheets", "CMakeLists.txt"))
        self.assertIn("BruceWorx.qss", sheets)
        self.assertIn("parameters/BruceWorx.yaml", sheets)
        self.assertIn("parameters/BruceWorx Dark.yaml", sheets)
        mods = _read(os.path.join(ROOT, "src", "Mod", "CMakeLists.txt"))
        self.assertIn("add_subdirectory(BruceWorx)", mods)

    def test_mac_runner_stays_gated(self):
        workflow = _read(os.path.join(ROOT, ".github", "workflows", "bruceworx-ci.yml"))
        self.assertIn("vars.BW_MAC_RUNNER == 'true'", workflow)
        self.assertNotIn("BW_MAC_RUNNER: true", workflow)
        self.assertNotIn('BW_MAC_RUNNER == "false"', workflow)


class GroundGridTest(unittest.TestCase):
    def test_segments_lie_on_the_xy_plane(self):
        segments = ground_grid.grid_segments()
        self.assertGreater(len(segments), 10)
        majors = 0
        for start, end, major in segments:
            self.assertEqual(start[2], 0.0)
            self.assertEqual(end[2], 0.0)
            self.assertLessEqual(abs(start[0]), ground_grid.HALF_EXTENT_MM)
            self.assertLessEqual(abs(start[1]), ground_grid.HALF_EXTENT_MM)
            self.assertLessEqual(abs(end[0]), ground_grid.HALF_EXTENT_MM)
            self.assertLessEqual(abs(end[1]), ground_grid.HALF_EXTENT_MM)
            if major:
                majors += 1
        self.assertGreater(majors, 0)

    def test_toggle_requires_both_flags(self):
        self.assertFalse(ground_grid.should_show(False, True))
        self.assertFalse(ground_grid.should_show(True, False))
        self.assertTrue(ground_grid.should_show(True, True))


class DefaultsOnTest(unittest.TestCase):
    """BruceWorx rule: features ship on, each with an off switch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snap = os.path.join(self.tmp.name, "BruceWorx", "stock-snapshot.json")
        self.store = pack.MemoryStore()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_applies_pack_once_and_snapshots(self):
        self.store.set("BaseApp/Preferences/Units", "UserSchema", "int", 3)
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "applied")
        self.assertTrue(defaults.pack_applied(self.store))
        self.assertTrue(os.path.exists(self.snap))
        self.assertEqual(self.store.get("BaseApp/Preferences/Units", "UserSchema", "int", -1), 0)
        self.assertEqual(
            self.store.get("BaseApp/Preferences/MainWindow", "Theme", "text", ""), "BruceWorx"
        )
        # Second start: nothing changes.
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "already")

    def test_first_run_turns_off_freecads_first_start_page(self):
        defaults.apply_first_run(self.store, self.snap)
        self.assertIs(self.store.get(defaults.START_GROUP, defaults.FIRST_START, "bool", True), False)

    def test_a_profile_freecads_first_start_overwrote_is_healed_once(self):
        # Vector Labs' new profile: pack applied, then the first-start page put FreeCAD Light over it.
        self.store.set(defaults.GROUP, pack.APPLIED_NAME, "bool", True)
        self.store.set(defaults.GROUP, defaults.FIRST_RUN_DONE, "bool", True)
        self.store.set(defaults.START_GROUP, defaults.FIRST_START, "bool", False)
        self.store.set(defaults.MAIN_GROUP, "Theme", "text", "FreeCAD Light")
        self.store.set(defaults.MAIN_GROUP, "StyleSheet", "text", "FreeCAD.qss")
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "healed")
        self.assertEqual(self.store.get(defaults.MAIN_GROUP, "Theme", "text", ""), "BruceWorx")
        # A stock theme chosen afterwards is the user's choice.
        self.store.set(defaults.MAIN_GROUP, "Theme", "text", "FreeCAD Light")
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "already")
        self.assertEqual(self.store.get(defaults.MAIN_GROUP, "Theme", "text", ""), "FreeCAD Light")

    def test_a_good_profile_is_checked_once_and_left_alone(self):
        defaults.apply_first_run(self.store, self.snap)  # applied, theme BruceWorx
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "already")
        self.store.set(defaults.MAIN_GROUP, "Theme", "text", "FreeCAD Dark")  # the user picks a stock theme
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "already")
        self.assertEqual(self.store.get(defaults.MAIN_GROUP, "Theme", "text", ""), "FreeCAD Dark")

    def test_first_run_does_not_reapply_after_user_turned_pack_off(self):
        defaults.apply_first_run(self.store, self.snap)
        pack.disable(self.store, self.snap)  # "BruceWorx Off" from the Theme combo
        self.assertFalse(defaults.pack_applied(self.store))
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "done")
        self.assertFalse(defaults.pack_applied(self.store))

    def test_switch_off_skips_first_run(self):
        self.store.set(defaults.GROUP, defaults.SWITCH, "bool", False)
        self.assertEqual(defaults.apply_first_run(self.store, self.snap), "off")
        self.assertFalse(defaults.pack_applied(self.store))

    def test_switch_restores_and_reapplies(self):
        self.store.set("BaseApp/Preferences/Units", "UserSchema", "int", 3)
        defaults.apply_first_run(self.store, self.snap)
        self.assertEqual(defaults.on_switch_changed(self.store, self.snap, False), "snapshot")
        self.assertFalse(defaults.pack_applied(self.store))
        self.assertEqual(self.store.get("BaseApp/Preferences/Units", "UserSchema", "int", -1), 3)
        self.assertEqual(defaults.on_switch_changed(self.store, self.snap, False), "noop")
        self.assertEqual(defaults.on_switch_changed(self.store, self.snap, True), "applied")
        self.assertTrue(defaults.pack_applied(self.store))
        self.assertEqual(defaults.on_switch_changed(self.store, self.snap, True), "noop")

    def test_preference_page_has_every_switch_default_on(self):
        ui = ET.parse(os.path.join(ROOT, "src", "Mod", "BruceWorx", "preferences-bruceworx.ui"))
        switches = {}
        for widget in ui.iter("widget"):
            if widget.get("class") != "Gui::PrefCheckBox":
                continue
            props = {p.get("name"): p for p in widget.findall("property")}
            entry = props["prefEntry"].findtext("cstring")
            path = props["prefPath"].findtext("cstring")
            checked = props["checked"].findtext("bool")
            switches[(path, entry)] = checked
        for key in (
            ("BruceWorx", "PreferencePack"),
            ("Mod/HistoryTimeline", "Enabled"),
            ("BruceWorx", "LoadHooks"),
            ("BruceWorx", "FloatingModelTree"),
            ("MainWindow", "UnifiedTitleAndToolBar"),
        ):
            self.assertEqual(switches.get(key), "true", key)

    def test_history_timeline_defaults_on(self):
        dock = _read(
            os.path.join(
                ROOT, "package", "bruceworx", "mods", "HistoryTimeline", "history_timeline", "dock.py"
            )
        )
        self.assertIn("ENABLED_DEFAULT = True", dock)
        self.assertIn('GetBool("Enabled", ENABLED_DEFAULT)', dock)
        # The menu bar is rebuilt on workbench switches; the item must go
        # through the shared re-attaching menu, not menuBar().addMenu().
        self.assertIn("bruceworx_menu.add_action", dock)
        self.assertNotIn('menuBar().addMenu("BruceWorx")', dock)

    def test_build_installs_history_timeline_and_hooks(self):
        mods = _read(os.path.join(ROOT, "src", "Mod", "CMakeLists.txt"))
        self.assertIn('option(BUILD_BW_HISTORY_TIMELINE', mods)
        self.assertIn('option(BUILD_BW_HOOKS', mods)
        self.assertRegex(mods, r'option\(BUILD_BW_HISTORY_TIMELINE "[^"]*" ON\)')
        self.assertRegex(mods, r'option\(BUILD_BW_HOOKS "[^"]*" ON\)')
        timeline_dir = os.path.join(ROOT, "package", "bruceworx", "mods", "HistoryTimeline")
        timeline = _read(os.path.join(timeline_dir, "CMakeLists.txt"))
        self.assertIn('"${CMAKE_BINARY_DIR}/Mod/HistoryTimeline"', timeline)
        self.assertIn("DESTINATION Mod/HistoryTimeline", timeline)
        for rel in re.findall(r"^\s+((?:history_timeline/)?[\w]+\.(?:py|md))$", timeline, re.M):
            self.assertTrue(os.path.isfile(os.path.join(timeline_dir, rel)), rel)
        hooks_dir = os.path.join(ROOT, "addons", "BruceWorx")
        hooks = _read(os.path.join(hooks_dir, "CMakeLists.txt"))
        self.assertIn('"${CMAKE_BINARY_DIR}/Mod/BruceWorxHooks"', hooks)
        self.assertIn("DESTINATION Mod/BruceWorxHooks", hooks)
        self.assertNotIn("package.xml", hooks.split("SET(BruceWorxHooks_SRCS")[1].split(")")[0])
        for rel in re.findall(r"^\s+([\w/]+\.(?:py|json|FCMacro|md))$", hooks, re.M):
            self.assertTrue(os.path.isfile(os.path.join(hooks_dir, rel)), rel)
        # Every Python file of the bruceworx package is shipped.
        for name in os.listdir(os.path.join(hooks_dir, "bruceworx")):
            if name.endswith(".py"):
                self.assertIn(f"bruceworx/{name}", hooks, name)
        bw = _read(os.path.join(ROOT, "src", "Mod", "BruceWorx", "CMakeLists.txt"))
        for name in ("bruceworx_defaults.py", "bruceworx_menu.py"):
            self.assertIn(name, bw)


class ToolbarRowsTest(unittest.TestCase):
    def test_all_hidden(self):
        self.assertTrue(toolbars.all_hidden([False, False]))
        self.assertFalse(toolbars.all_hidden([False, True]))
        self.assertFalse(toolbars.all_hidden([]))

    def test_first_workbench_toolbar_starts_row_two(self):
        names = ["File", "Workbench", "View", "Part Design Helper Features", "Part Design Modeling Features"]
        self.assertEqual(toolbars.plan_breaks(names), {"Part Design Helper Features"})

    def test_only_app_toolbars_means_one_row(self):
        self.assertEqual(toolbars.plan_breaks(["File", "Workbench", "View"]), set())


class SketchGridTest(unittest.TestCase):
    def test_default_strength_matches_the_pack(self):
        entries = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries("dark")}
        path = "BaseApp/Preferences/Mod/Sketcher/General"
        self.assertEqual(grid.colors(grid.DEFAULT_STRENGTH, True),
                         (entries[(path, "GridLineColor")], entries[(path, "GridDivLineColor")]))
        entries = {(e["path"], e["name"]): e["value"] for e in pack.pack_entries("light")}
        self.assertEqual(grid.colors(grid.DEFAULT_STRENGTH, False),
                         (entries[(path, "GridLineColor")], entries[(path, "GridDivLineColor")]))

    def test_white_on_dark_black_on_light_and_clamped(self):
        minor, major = grid.colors(10, True)
        self.assertEqual(minor >> 8, 0xFFFFFF)
        self.assertGreater(major & 0xFF, minor & 0xFF)
        self.assertEqual(grid.colors(10, False)[0] >> 8, 0)
        self.assertEqual(grid.colors(999, True), grid.colors(grid.MAX_STRENGTH, True))
        self.assertEqual(grid.colors(0, True), grid.colors(grid.MIN_STRENGTH, True))


class AppearanceTest(unittest.TestCase):
    def test_text_on_accent_follows_the_contrast_rule(self):
        # Roadmap §13.3 table: white on blue, purple, pink, red, graphite; black on orange, yellow, green.
        for index in (4, 5, 6, 0, -1):
            self.assertEqual(appearance.text_on(appearance.accent_hex(index, False))[1], "white", index)
        for index in (1, 2, 3):
            self.assertEqual(appearance.text_on(appearance.accent_hex(index, False))[1], "black", index)

    def test_unknown_accent_is_blue(self):
        self.assertEqual(appearance.accent_hex(None, True), "#0A84FF")
        self.assertEqual(appearance.packed("#0A84FF"), 0x0A84FFFF)

    def test_mode_switch_touches_only_theme_dependent_keys(self):
        names = {(e["path"], e["name"]) for e in appearance.mode_switch_entries(pack, "dark")}
        self.assertIn(("BaseApp/Preferences/MainWindow", "Theme"), names)
        self.assertIn(("BaseApp/Preferences/View", "BackgroundColor2"), names)
        self.assertNotIn(("BaseApp/Preferences/Units", "UserSchema"), names)
        self.assertNotIn(("BaseApp/Preferences/View", "NavigationStyle"), names)


class TaskCardTest(unittest.TestCase):
    def test_first_titled_box_names_the_card(self):
        self.assertEqual(taskcard.pick_title([("", False), ("Fillet Parameters", True), ("Preview", True)]),
                         ("Fillet Parameters", 1))

    def test_no_titles_keeps_tasks(self):
        self.assertEqual(taskcard.pick_title([("", False), ("  ", True)]), ("Tasks", None))


class DocumentTabsAndStatusTest(unittest.TestCase):
    def test_pack_puts_tabs_on_top_with_dots_and_readouts(self):
        entries = {(e["path"], e["name"]): e for e in pack.INVENTORY}
        tab = entries[("BaseApp/Preferences/MainWindow", "DocumentTabPos")]
        self.assertEqual((tab["stock"], tab["light"], tab["dark"]), (1, 1, 1))  # tabs at the bottom (24 Oct)
        for name in ("DocumentTabDot", "StatusReadouts"):
            entry = entries[(pack.APPLIED_GROUP, name)]
            self.assertEqual((entry["stock"], entry["light"], entry["dark"]), (False, True, True), name)

    def test_preference_page_has_the_switches(self):
        ui = ET.parse(os.path.join(ROOT, "src", "Mod", "BruceWorx", "preferences-bruceworx.ui"))
        found = {}
        for widget in ui.iter("widget"):
            props = {p.get("name"): p for p in widget.findall("property")}
            if "prefEntry" in props:
                found[props["prefEntry"].findtext("cstring")] = (widget.get("class"), props)
        for name in ("DocumentTabDot", "StatusReadouts"):
            cls, props = found[name]
            self.assertEqual(cls, "Gui::PrefCheckBox")
            self.assertEqual(props["checked"].findtext("bool"), "true", name)
            self.assertEqual(props["prefPath"].findtext("cstring"), "BruceWorx")
        cls, props = found["DocumentTabPos"]
        self.assertEqual(cls, "Gui::PrefComboBox")
        self.assertEqual(props["prefPath"].findtext("cstring"), "MainWindow")
        self.assertEqual(props["currentIndex"].findtext("number"), "1")

    def test_stylesheet_styles_the_new_widgets(self):
        qss = _read(os.path.join(ROOT, "src", "Gui", "Stylesheets", "BruceWorx.qss"))
        for selector in ("#documentTabButton", "#reportReadout", "#vaultReadout"):
            self.assertTrue(selector in qss, selector)

    def test_navigation_label(self):
        self.assertEqual(statusbar.navigation_label("Revit  "), "Navigation Revit")
        self.assertEqual(statusbar.navigation_label("", "Gui::TouchpadNavigationStyle"), "Navigation Touchpad")
        self.assertEqual(statusbar.navigation_label("", ""), "Navigation")

    def test_vault_label(self):
        self.assertIsNone(statusbar.vault_label(""))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(statusbar.vault_label(os.path.join(tmp, "gone"))[0], "Vault offline")
            self.assertEqual(statusbar.vault_label(tmp)[0], "Vault not set up")
            ref = os.path.join(tmp, "Shop.bwvault")
            with open(ref, "w", encoding="utf-8") as handle:
                handle.write('{"format": "bruceworx.vault", "version": 1, "name": "Shop"}')
            self.assertEqual(statusbar.vault_label(tmp), ("Vault Shop", tmp))
            self.assertEqual(statusbar.vault_label(ref)[0], "Vault Shop")
            with open(ref, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertEqual(statusbar.vault_label(tmp)[0], "Vault unreadable")


if __name__ == "__main__":
    unittest.main(verbosity=2)

