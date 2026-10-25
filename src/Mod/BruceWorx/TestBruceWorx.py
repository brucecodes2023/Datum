# SPDX-License-Identifier: LGPL-2.1-or-later
"""Parameter-level tests for the floating model tree. No GUI and no OverlayManager."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import BruceWorx


class FakeGrp:
    """Enough of ParameterGrp for BruceWorx.sync."""

    def __init__(self):
        self.strings = {}
        self.ints = {}
        self.bools = {}
        self.groups = {}

    def GetGroup(self, name):
        return self.groups.setdefault(name, FakeGrp())

    def GetContents(self):
        rows = [("String", key, value) for key, value in self.strings.items()]
        rows += [("Integer", key, value) for key, value in self.ints.items()]
        rows += [("Boolean", key, value) for key, value in self.bools.items()]
        return rows or None

    def SetString(self, name, value):
        self.strings[name] = "" if value is None else str(value)

    def GetString(self, name, default=""):
        return self.strings.get(name, default)

    def RemString(self, name):
        self.strings.pop(name, None)

    def SetInt(self, name, value):
        self.ints[name] = int(value)

    def GetInt(self, name, default=0):
        return self.ints.get(name, default)

    def RemInt(self, name):
        self.ints.pop(name, None)

    def SetBool(self, name, value):
        self.bools[name] = bool(value)

    def GetBool(self, name, default=False):
        return self.bools.get(name, bool(default))

    def RemBool(self, name):
        self.bools.pop(name, None)


QSS = "/tmp/BruceWorxModelTree.qss"


def _world(tree_enabled=False):
    prefs = FakeGrp()
    overlay = FakeGrp()
    style = FakeGrp()
    docks = FakeGrp()
    if tree_enabled:
        docks.GetGroup("ComboView").SetBool("Enabled", False)
        docks.GetGroup("TreeView").SetBool("Enabled", True)
    return prefs, overlay, style, docks


class WidgetListTests(unittest.TestCase):
    def test_round_trip_names_with_spaces(self):
        text = BruceWorx.format_widgets(["Model", "Tree view"])
        self.assertEqual(text, "Model,Tree view,")
        self.assertEqual(BruceWorx.parse_widgets(text), ["Model", "Tree view"])

    def test_empty(self):
        self.assertEqual(BruceWorx.format_widgets([]), "")
        self.assertEqual(BruceWorx.parse_widgets(""), [])
        self.assertEqual(BruceWorx.parse_widgets(None), [])

    def test_tree_dock_names(self):
        _prefs, _overlay, _style, docks = _world()
        self.assertEqual(BruceWorx.tree_dock_names(docks), ["Model"])
        docks.GetGroup("ComboView").SetBool("Enabled", False)
        docks.GetGroup("TreeView").SetBool("Enabled", True)
        self.assertEqual(BruceWorx.tree_dock_names(docks), ["Tree view"])
        docks.GetGroup("ComboView").SetBool("Enabled", True)
        self.assertEqual(BruceWorx.tree_dock_names(docks), ["Model", "Tree view"])


class SyncTests(unittest.TestCase):
    def test_missing_pref_defaults_on_and_off_restores_stock(self):
        prefs, overlay, style, docks = _world()
        left = overlay.GetGroup("OverlayLeft")
        left.SetString("Widgets", "Report view,")
        left.SetString("Sizes", "180")
        style.SetString(BruceWorx.STYLE_KEY, "kept.qss")

        state = BruceWorx.sync(prefs, overlay, style, QSS, BruceWorx.tree_dock_names(docks))
        self.assertEqual(state, "on")
        self.assertNotIn("FloatingModelTree", prefs.bools)
        self.assertTrue(prefs.GetBool(BruceWorx.APPLIED_KEY, False))
        self.assertEqual(left.GetString("Widgets"), "Model,")
        self.assertEqual(style.GetString(BruceWorx.STYLE_KEY), QSS)
        snapshot = prefs.GetString(BruceWorx.SNAPSHOT_KEY)

        # Still on, key still missing: keep the original stock snapshot.
        again = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(again, "on")
        self.assertEqual(prefs.GetString(BruceWorx.SNAPSHOT_KEY), snapshot)

        prefs.SetBool("FloatingModelTree", False)
        off = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(off, "off")
        self.assertEqual(left.GetString("Widgets"), "Report view,")
        self.assertEqual(left.GetString("Sizes"), "180")
        self.assertNotIn("Width", left.ints)
        self.assertNotIn("AutoHide", left.bools)
        self.assertNotIn("Transparent", left.bools)
        self.assertEqual(style.GetString(BruceWorx.STYLE_KEY), "kept.qss")
        self.assertNotIn(BruceWorx.SNAPSHOT_KEY, prefs.strings)
        self.assertFalse(prefs.GetBool(BruceWorx.APPLIED_KEY, True))

        stayed = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(stayed, "noop")
        self.assertEqual(left.GetString("Widgets"), "Report view,")
        self.assertEqual(style.GetString(BruceWorx.STYLE_KEY), "kept.qss")

    def test_tree_stays_visible_by_default(self):
        # A profile that got AutoHide=1 from the old default is pinned again on
        # the next sync, because the preference itself was never written.
        prefs, overlay, style, docks = _world()
        left = overlay.GetGroup("OverlayLeft")
        left.SetBool("AutoHide", True)
        state = BruceWorx.sync(prefs, overlay, style, QSS, BruceWorx.tree_dock_names(docks))
        self.assertEqual(state, "on")
        self.assertFalse(left.GetBool("AutoHide"))

    def test_explicit_off_before_apply_leaves_stock(self):
        prefs, overlay, style, docks = _world()
        prefs.SetBool("FloatingModelTree", False)
        state = BruceWorx.sync(prefs, overlay, style, QSS, BruceWorx.tree_dock_names(docks))
        self.assertEqual(state, "noop")
        self.assertEqual(overlay.groups, {})
        self.assertEqual(style.strings, {})
        self.assertFalse(prefs.GetBool(BruceWorx.APPLIED_KEY, False))

    def test_on_then_off_restores_stock_parameters(self):
        prefs, overlay, style, docks = _world()
        right = overlay.GetGroup("OverlayRight")
        right.SetString("Widgets", "Tasks,Model,")
        left = overlay.GetGroup("OverlayLeft")
        left.SetString("Widgets", "Report view,")
        left.SetString("Sizes", "200")
        left.SetInt("Width", 400)
        left.SetBool("AutoHide", False)
        style.SetString(BruceWorx.STYLE_KEY, "overlay:Light Theme + Dark Background.qss")

        prefs.SetBool("FloatingModelTree", True)
        prefs.SetBool("FloatingModelTreeAutoHide", True)
        prefs.SetBool("FloatingModelTreeTransparent", True)
        prefs.SetInt("FloatingModelTreeWidth", 50)  # clamped up to MIN_WIDTH
        prefs.SetInt("FloatingModelTreeTopOffset", 12)
        prefs.SetInt("FloatingModelTreeBottomInset", 16)
        prefs.SetInt("FloatingModelTreeLeftInset", 4)

        state = BruceWorx.sync(prefs, overlay, style, QSS, BruceWorx.tree_dock_names(docks))
        self.assertEqual(state, "on")
        self.assertTrue(prefs.GetBool(BruceWorx.APPLIED_KEY, False))
        self.assertEqual(left.GetString("Widgets"), "Model,")
        self.assertNotIn("Sizes", left.strings)
        self.assertEqual(left.GetInt("Width"), BruceWorx.MIN_WIDTH)
        self.assertEqual(left.GetInt("Offset1"), 12)
        self.assertEqual(left.GetInt("Offset2"), 16)
        self.assertEqual(left.GetInt("Offset3"), 4)
        self.assertTrue(left.GetBool("AutoHide"))
        self.assertFalse(left.GetBool("EditHide"))
        self.assertTrue(left.GetBool("Transparent"))
        self.assertEqual(right.GetString("Widgets"), "Tasks,")
        self.assertEqual(style.GetString(BruceWorx.STYLE_KEY), QSS)
        snapshot = prefs.GetString(BruceWorx.SNAPSHOT_KEY)

        # A second apply keeps the original snapshot and reasserts the tree.
        left.SetString("Widgets", "Python console,")
        again = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(again, "on")
        self.assertEqual(left.GetString("Widgets"), "Model,")
        self.assertEqual(prefs.GetString(BruceWorx.SNAPSHOT_KEY), snapshot)

        prefs.SetBool("FloatingModelTree", False)
        off = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(off, "off")
        self.assertEqual(left.GetString("Widgets"), "Report view,")
        self.assertEqual(left.GetString("Sizes"), "200")
        self.assertEqual(left.GetInt("Width"), 400)
        self.assertFalse(left.GetBool("AutoHide"))
        self.assertNotIn("Transparent", left.bools)
        self.assertNotIn("Offset3", left.ints)
        self.assertEqual(right.GetString("Widgets"), "Tasks,Model,")
        self.assertEqual(style.GetString(BruceWorx.STYLE_KEY), "overlay:Light Theme + Dark Background.qss")
        self.assertNotIn(BruceWorx.SNAPSHOT_KEY, prefs.strings)
        self.assertFalse(prefs.GetBool(BruceWorx.APPLIED_KEY, False))

        # Staying off does not wipe the restored docks.
        noop = BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(noop, "noop")
        self.assertEqual(left.GetString("Widgets"), "Report view,")

    def test_absent_right_widgets_stay_absent(self):
        prefs, overlay, style, _docks = _world()
        prefs.SetBool("FloatingModelTree", True)
        prefs.SetBool("FloatingTaskPanel", False)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Tree view"])
        right = overlay.GetGroup("OverlayRight")
        self.assertNotIn("Widgets", right.strings)
        self.assertEqual(overlay.GetGroup("OverlayLeft").GetString("Widgets"), "Tree view,")
        prefs.SetBool("FloatingModelTree", False)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Tree view"])
        self.assertNotIn("Widgets", right.strings)
        self.assertNotIn("Widgets", overlay.GetGroup("OverlayLeft").strings)

    def test_task_panel_floats_right_and_comes_back(self):
        prefs, overlay, style, _docks = _world()
        left = overlay.GetGroup("OverlayLeft")
        right = overlay.GetGroup("OverlayRight")
        left.SetString("Widgets", "Tasks,")
        prefs.SetBool("FloatingModelTree", True)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(right.GetString("Widgets"), "Tasks,")
        self.assertEqual(left.GetString("Widgets"), "Model,")
        self.assertEqual(right.GetInt("Width"), BruceWorx.TASK_WIDTH)
        self.assertTrue(right.GetBool("TaskShow"))
        self.assertFalse(right.GetBool("Transparent"))

        # Its own switch docks it again without touching the tree.
        prefs.SetBool("FloatingTaskPanel", False)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(right.GetString("Widgets"), "")
        self.assertEqual(left.GetString("Widgets"), "Model,")

        # Turning the floating panels off restores the snapshot.
        prefs.SetBool("FloatingTaskPanel", True)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        prefs.SetBool("FloatingModelTree", False)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        self.assertEqual(left.GetString("Widgets"), "Tasks,")
        self.assertNotIn("Widgets", right.strings)
        self.assertNotIn("Width", right.ints)

    def test_snapshot_is_json(self):
        prefs, overlay, style, _docks = _world()
        prefs.SetBool("FloatingModelTree", True)
        BruceWorx.sync(prefs, overlay, style, QSS, ["Model"])
        json.loads(prefs.GetString(BruceWorx.SNAPSHOT_KEY))

class SlotTests(unittest.TestCase):
    """bruceworx_slots.slot: a receiver PySide may keep without keeping (or killing) the object."""

    class Owner:
        def __init__(self):
            self.calls = []

        def one(self, value):
            self.calls.append(value)

        def none(self):
            self.calls.append("none")

        def many(self, *args):
            self.calls.append(args)

    def test_arguments_are_trimmed_to_the_method(self):
        import bruceworx_slots

        owner = self.Owner()
        bruceworx_slots.slot(owner.one)(7, "extra")
        bruceworx_slots.slot(owner.none)(True)
        bruceworx_slots.slot(owner.many)(1, 2, 3)
        self.assertEqual(owner.calls, [7, "none", (1, 2, 3)])

    def test_the_object_lives_while_connected(self):
        import gc
        import weakref

        import bruceworx_slots

        owner = self.Owner()
        receiver = bruceworx_slots.slot(owner.one)
        alive = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNotNone(alive())  # the receiver (held by PySide while connected) keeps it
        receiver(3)
        self.assertEqual(alive().calls, [3])
        del receiver
        gc.collect()
        self.assertIsNone(alive())

    def test_plain_functions_pass_through(self):
        import bruceworx_slots

        def f():
            pass

        self.assertIs(bruceworx_slots.slot(f), f)


class ConnectionLintTests(unittest.TestCase):
    """No plain Python class in this folder connects a widget signal straight to one of its bound
    methods: when such an object dies PySide 6.8.3 drops every function and lambda connection in
    the application (bruceworx_slots explains; found 26 Oct 2026). Use ``slot(self.method)``."""

    # Modules another session owns at the time of writing; to be fixed by their owners.
    KNOWN = {"bruceworx_vaultclient.py", "bruceworx_vaultwizard.py", "bruceworx_drawing.py", "bruceworx_project.py"}

    @staticmethod
    def offenders(folder):
        import ast
        import glob

        def qt_derived(cls, classes):
            for base in cls.bases:
                name = ast.unparse(base).split(".")[-1]
                if name.startswith("Q") and len(name) > 1 and name[1].isupper():
                    return True
                if name in classes and classes[name] is not cls and qt_derived(classes[name], classes):
                    return True
            return False

        found = []
        for path in sorted(glob.glob(os.path.join(folder, "bruceworx_*.py"))):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            for cls in classes.values():
                if qt_derived(cls, classes):
                    continue
                for node in ast.walk(cls):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "connect" and node.args):
                        continue
                    arg = node.args[0]
                    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id == "self":
                        found.append((os.path.basename(path), cls.name, node.lineno, arg.attr))
        return found

    def test_no_bound_method_receivers_in_plain_classes(self):
        here = os.path.dirname(os.path.abspath(__file__))
        found = [f for f in self.offenders(here) if f[0] not in self.KNOWN]
        self.assertEqual(found, [], "wrap these in bruceworx_slots.slot(): %s" % found)

    def test_the_lint_sees_the_pattern(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bruceworx_x.py"), "w") as handle:
                handle.write("class Plain:\n    def go(self):\n        self.b.clicked.connect(self.on)\n"
                             "class Fine(QtWidgets.QDialog):\n    def go(self):\n        self.b.clicked.connect(self.on)\n"
                             "class Wrapped:\n    def go(self):\n        self.b.clicked.connect(slot(self.on))\n")
            self.assertEqual(self.offenders(tmp), [("bruceworx_x.py", "Plain", 3, "on")])


if __name__ == "__main__":
    unittest.main()
