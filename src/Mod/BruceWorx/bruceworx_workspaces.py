# SPDX-License-Identifier: LGPL-2.1-or-later
"""Workspaces: a Fusion-style workspace menu and tabs above the tool groups.

Roadmap §13.4, step C2 (UIX-05). workspaces.json defines four workspaces (Design,
Simulation, Manufacture, Drawing). Each runs one FreeCAD workbench; changing
workspace is an ordinary workbench switch. Inside a workspace, tabs only show
and hide toolbars, so there is no rebuild.

* A tab's groups are FreeCAD custom toolbars, written at start-up under
  BaseApp/Workbench/<wb>/Toolbar/BW_* and named "<Tab> · <Caption>". FreeCAD
  imports the module named with each command when the command is not
  registered yet; for these it is bruceworx_wsload, which loads the commands
  of the other workbenches (Part, Surface, Mesh, Assembly) the groups use.
  The BW_* groups are rewritten on every start, so they are always current,
  and nothing the user customised is touched.
* A workbench manipulator takes out the stock toolbars the groups replace and
  the stock Workbench selector, while the bar is on.
* The bar is one toolbar (BruceWorxWorkspaces): the workspace button, whose menu
  lists the workspaces (Ctrl+1..4) and More workbenches, and a tab bar. Opening
  a sketch adds an accent SKETCH tab (the last tab; BruceWorx.qss colours it
  through the tab bar's "sketch" property) and a Finish Sketch button; leaving
  the sketch removes them. A workbench from More gets one tab with its own toolbars.
* The last tab of each workspace is kept in BruceWorx/Workspaces/<name>/Tab.

Preferences/BruceWorx/WorkspaceBar (default on). Off removes the bar, the
groups and the manipulator, so the stock toolbars and selector come back.
"""

from __future__ import annotations

import json
import os
import re

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "WorkspaceBar"
STATE_PATH = PREFS_PATH + "/Workspaces"
WORKBENCH_PATH = "User parameter:BaseApp/Workbench"
WORKBENCHES_PATH = "User parameter:BaseApp/Preferences/Workbenches"
BAR_NAME = "BruceWorxWorkspaces"
GROUP_PREFIX = "BW_"
LOADER = "bruceworx_wsload"
DOT = "·"
ARROW = "▾"
WORKBENCH_TABS = "workbench"


# --- definition --------------------------------------------------------------


def definition_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces.json")


def load(path=None):
    with open(path or definition_path(), encoding="utf-8") as handle:
        return json.load(handle)


def validate(defn):
    """Problems in a definition, as readable strings (empty when it is fine)."""
    errors = []
    names = set()
    toolbars = set()
    for ws in defn.get("workspaces", []):
        name = ws.get("name")
        if not name or name in names:
            errors.append(f"workspace name missing or repeated: {name!r}")
        names.add(name)
        if not ws.get("workbench"):
            errors.append(f"{name}: no workbench")
        if not ws.get("tabs"):
            errors.append(f"{name}: no tabs")
        for tab in ws.get("tabs", []):
            if tab.get("toolbars") == WORKBENCH_TABS:
                continue
            if not tab.get("groups"):
                errors.append(f"{name}/{tab.get('name')}: no groups")
            for group in tab.get("groups", []):
                caption = group.get("caption")
                toolbar = toolbar_name(tab.get("name"), caption)
                if toolbar in toolbars:
                    errors.append(f"{name}: group repeated: {toolbar}")
                toolbars.add(toolbar)
                commands = group.get("commands", [])
                if not commands:
                    errors.append(f"{toolbar}: no commands")
                both = commands + group.get("more", [])
                if len(set(both)) != len(both):
                    errors.append(f"{toolbar}: a command is listed twice")
    return errors


def without_missing(defn, available):
    """The definition without the tabs whose "requires" add-on is not loaded.
    available(name) says whether an add-on folder is loaded."""
    out = dict(defn)
    out["workspaces"] = []
    for ws in defn.get("workspaces", []):
        ws = dict(ws)
        ws["tabs"] = [t for t in ws.get("tabs", []) if not t.get("requires") or available(t["requires"])]
        out["workspaces"].append(ws)
    return out


def addon_loaded(name):
    """True when FreeCAD found the add-on folder (any case) and it is not disabled."""
    import FreeCAD

    disabled = {n.lower() for n in FreeCAD.ConfigGet("DisabledAddons").split(";") if n}
    if name.lower() in disabled:
        return False
    return any(os.path.basename(os.path.normpath(d)).lower() == name.lower()
               for d in getattr(FreeCAD, "__ModDirs__", []))


def toolbar_name(tab, caption):
    return f"{tab} {DOT} {caption}"


def group_id(workspace, tab, caption):
    """Parameter group name of one custom toolbar."""
    return GROUP_PREFIX + re.sub(r"[^A-Za-z0-9]+", "_", f"{workspace}_{tab}_{caption}")


def workspaces(defn):
    return defn.get("workspaces", [])


def workspace(defn, name):
    return next((ws for ws in workspaces(defn) if ws["name"] == name), None)


def workspace_for_workbench(defn, workbench):
    return next((ws for ws in workspaces(defn) if ws["workbench"] == workbench), None)


def tab(ws, name):
    return next((t for t in ws.get("tabs", []) if t["name"] == name), None)


def tab_toolbars(ws, tab_name):
    """Toolbar names a group tab shows (empty for a workbench tab)."""
    found = tab(ws, tab_name)
    if not found or found.get("toolbars") == WORKBENCH_TABS:
        return []
    return [toolbar_name(found["name"], g["caption"]) for g in found.get("groups", [])]


def group_toolbars(ws):
    """Every group toolbar of a workspace, over all its tabs."""
    return [name for t in ws.get("tabs", []) for name in tab_toolbars(ws, t["name"])]


def custom_toolbars(defn):
    """{workbench: [(group id, toolbar name, [commands])]} for the param writer."""
    out = {}
    for ws in workspaces(defn):
        for t in ws.get("tabs", []):
            if t.get("toolbars") == WORKBENCH_TABS:
                continue
            for g in t.get("groups", []):
                entry = (group_id(ws["name"], t["name"], g["caption"]), toolbar_name(t["name"], g["caption"]), list(g["commands"]))
                out.setdefault(ws["workbench"], []).append(entry)
    return out


def replaced_toolbars(defn):
    """Stock toolbars the manipulator takes out while the bar is on."""
    names = ["Workbench"]
    for ws in workspaces(defn):
        names += [n for n in ws.get("replaces", []) if n not in names]
    return names


def more_commands(defn, toolbar):
    """Commands listed under 'more' for a group toolbar name."""
    for ws in workspaces(defn):
        for t in ws.get("tabs", []):
            for g in t.get("groups", []):
                if toolbar_name(t["name"], g["caption"]) == toolbar:
                    return list(g.get("more", []))
    return []


def place_of(defn, command):
    """Where a command lives, e.g. ("Design", "Solid", "Create"), or None."""
    for ws in workspaces(defn):
        for t in ws.get("tabs", []):
            for g in t.get("groups", []):
                if command in g.get("commands", []) or command in g.get("more", []):
                    return ws["name"], t["name"], g["caption"]
    return None


def more_workbenches(available, disabled, defn):
    """Workbench names for the More menu: enabled, not one of the workspaces', not None."""
    taken = {ws["workbench"] for ws in workspaces(defn)} | {"NoneWorkbench"}
    return [wb for wb in available if wb not in taken and wb not in disabled]


def tab_names(ws, saved, sketch=False):
    """(tab names to show, the selected one). saved is the last tab used."""
    names = [t["name"] for t in ws.get("tabs", [])]
    if sketch:
        return names + ["Sketch"], "Sketch"
    return names, saved if saved in names else names[0]


# --- custom toolbars in the parameters -----------------------------------------


def write_custom_toolbars(defn, param_get):
    """Replace the BW_* custom toolbars with the definition's. param_get is App.ParamGet."""
    remove_custom_toolbars(param_get)
    for workbench, entries in custom_toolbars(defn).items():
        root = param_get(f"{WORKBENCH_PATH}/{workbench}/Toolbar")
        for gid, name, commands in entries:
            group = root.GetGroup(gid)
            group.SetString("Name", name)
            for command in commands:
                group.SetString(command, LOADER)
            group.SetBool("Active", True)


def remove_custom_toolbars(param_get):
    base = param_get(WORKBENCH_PATH)
    for workbench in base.GetGroups():
        root = base.GetGroup(workbench)
        if "Toolbar" not in root.GetGroups():
            continue
        toolbar = root.GetGroup("Toolbar")
        for gid in toolbar.GetGroups():
            if gid.startswith(GROUP_PREFIX):
                toolbar.RemGroup(gid)


# --- GUI ---------------------------------------------------------------------

_defn = None
_bar = None
_manipulator = None
_observer = None
_doc_observer = None
_groups = []


def definition():
    global _defn
    if _defn is None:
        _defn = without_missing(load(), addon_loaded)
    return _defn


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _saved_tab(name):
    import FreeCAD

    return FreeCAD.ParamGet(f"{STATE_PATH}/{name}").GetString("Tab", "")


def _save_tab(name, tab_name):
    import FreeCAD

    FreeCAD.ParamGet(f"{STATE_PATH}/{name}").SetString("Tab", tab_name)


def _sketch_in_edit():
    import FreeCADGui

    doc = FreeCADGui.ActiveDocument
    vp = doc.getInEdit() if doc else None
    obj = getattr(vp, "Object", None)
    return obj is not None and obj.isDerivedFrom("Sketcher::SketchObject")


def _workbench_icon(name):
    import FreeCADGui
    from PySide import QtGui

    try:
        icon = FreeCADGui.listWorkbenches()[name].Icon
    except Exception:
        return QtGui.QIcon()
    if isinstance(icon, str) and os.path.exists(icon):
        return QtGui.QIcon(icon)
    found = FreeCADGui.getIcon(icon) if isinstance(icon, str) and len(icon) < 200 else None
    return found or QtGui.QIcon()


def _menu_text(name):
    import FreeCADGui

    try:
        return FreeCADGui.listWorkbenches()[name].MenuText
    except Exception:
        return name


class Manipulator:
    """Takes out the stock toolbars the groups replace, and the Workbench selector."""

    def modifyToolBars(self):
        if not enabled():
            return []
        return [{"remove": name} for name in replaced_toolbars(definition())]


def _make_bar():
    from PySide import QtCore, QtWidgets
    import FreeCADGui

    class Bar(QtWidgets.QToolBar):
        def __init__(self, mw):
            super().__init__(mw)
            self.setObjectName(BAR_NAME)
            self.setWindowTitle("Workspaces")
            self.setMovable(False)
            self.setFloatable(False)
            # Hidden toggle: ToolBarManager then leaves the bar alone on every switch.
            self.toggleViewAction().setVisible(False)
            self.workspace = None  # workspace dict, or None for a More workbench
            self.workbench = None
            self.sketch = False
            self._pending = False

            self.button = QtWidgets.QToolButton(self)
            self.button.setObjectName("BruceWorxWorkspaceButton")
            self.button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.button.setIconSize(QtCore.QSize(18, 18))
            self.menu = QtWidgets.QMenu(self.button)
            self.menu.setObjectName("BruceWorxWorkspaceMenu")
            self.button.setMenu(self.menu)
            self.addWidget(self.button)

            self.tabs = QtWidgets.QTabBar(self)
            self.tabs.setObjectName("BruceWorxWorkspaceTabs")
            self.tabs.setDrawBase(False)
            self.tabs.setExpanding(False)
            self.tabs.setFocusPolicy(QtCore.Qt.NoFocus)
            self.tabs.currentChanged.connect(self.tab_clicked)
            self.addWidget(self.tabs)

            self.finish = QtWidgets.QToolButton(self)
            self.finish.setObjectName("BruceWorxFinishSketch")
            self.finish.setText("FINISH SKETCH")
            self.finish.clicked.connect(self.finish_sketch)
            self.finish_action = self.addWidget(self.finish)
            self.finish_action.setVisible(False)

            self.shortcut_actions = []
            self.build_menu()

        # --- menu
        def build_menu(self):
            menu = self.menu
            menu.clear()
            for action in self.shortcut_actions:
                self.parentWidget().removeAction(action)
            self.shortcut_actions = []
            self.workspace_actions = {}
            for ws in workspaces(definition()):
                action = menu.addAction(ws["name"])
                self.workspace_actions[ws["workbench"]] = action
                if ws.get("shortcut"):
                    action.setShortcut(ws["shortcut"])
                    # macOS leaves shortcuts out of pop-up menus by default.
                    action.setShortcutVisibleInContextMenu(True)
                    # On the main window too, so the shortcut works with the menu closed.
                    self.parentWidget().addAction(action)
                    self.shortcut_actions.append(action)
                action.triggered.connect(lambda _c=False, w=ws["name"]: activate_workspace(w))
            menu.addSeparator()
            more = menu.addMenu("More workbenches")
            more.setObjectName("BruceWorxMoreWorkbenches")
            more.aboutToShow.connect(lambda m=more: self.fill_more(m))
            menu.addSeparator()
            menu.addAction("Choose workbenches…", lambda: FreeCADGui.showPreferences("Workbenches", 0))
            # Icons are looked up when the menu opens: some workbenches set theirs only once loaded.
            menu.aboutToShow.connect(self.update_icons)

        def update_icons(self):
            for name, action in self.workspace_actions.items():
                action.setIcon(_workbench_icon(name))

        def fill_more(self, menu):
            import FreeCAD

            menu.clear()
            available = sorted(FreeCADGui.listWorkbenches(), key=_menu_text)
            disabled = FreeCAD.ParamGet(WORKBENCHES_PATH).GetString("Disabled", "").split(",")
            for name in more_workbenches(available, disabled, definition()):
                action = menu.addAction(_workbench_icon(name), _menu_text(name))
                action.triggered.connect(lambda _c=False, n=name: FreeCADGui.activateWorkbench(n))

        # --- state
        def schedule(self, *_args):
            if self._pending:
                return
            self._pending = True
            QtCore.QTimer.singleShot(0, self.sync)

        def sync(self):
            self._pending = False
            if not _alive(self) or not enabled():
                return
            wb = FreeCADGui.activeWorkbench()
            name = wb.name() if wb else ""
            defn = definition()
            sketch_defn = defn.get("sketch", {})
            self.sketch = _sketch_in_edit() and (
                name == sketch_defn.get("workbench") or (self.workspace and name == self.workspace["workbench"])
            )
            if not self.sketch:
                ws = workspace_for_workbench(defn, name)
                self.workspace = ws
                self.workbench = name
            elif self.workspace is None:
                # A sketch opened from a More workbench: keep showing that one.
                self.workbench = self.workbench or name
            if self.workspace is not None:
                names, current = tab_names(self.workspace, _saved_tab(self.workspace["name"]), self.sketch)
                label = self.workspace["name"]
                icon = _workbench_icon(self.workspace["workbench"])
            else:
                label = _menu_text(self.workbench)
                names = [label] + (["Sketch"] if self.sketch else [])
                current = names[-1]
                icon = _workbench_icon(self.workbench)
            self.button.setText(f"{label.upper()}  {ARROW}")
            self.button.setIcon(icon)
            self.tabs.setProperty("sketch", bool(self.sketch))
            self.tabs.style().unpolish(self.tabs)
            self.tabs.style().polish(self.tabs)
            self.set_tabs(names, current)
            self.finish_action.setVisible(self.sketch)
            self.apply()

        def set_tabs(self, names, current):
            self.tabs.blockSignals(True)
            while self.tabs.count():
                self.tabs.removeTab(0)
            for index, name in enumerate(names):
                self.tabs.addTab(name.upper())
                self.tabs.setTabData(index, name)
            self.tabs.setCurrentIndex(names.index(current) if current in names else 0)
            self.tabs.blockSignals(False)

        def select_tab(self, name):
            for index in range(self.tabs.count()):
                if self.tabs.tabData(index) == name:
                    self.tabs.setCurrentIndex(index)
                    return

        def current_tab(self):
            index = self.tabs.currentIndex()
            return self.tabs.tabData(index) if index >= 0 else None

        def tab_clicked(self, _index):
            name = self.current_tab()
            if self.workspace is not None and name != "Sketch":
                _save_tab(self.workspace["name"], name)
            self.apply()

        def apply(self):
            """Show the current tab's toolbars, hide the other groups of the workspace."""
            if self.workspace is None:
                return
            mw = self.parentWidget()
            current = self.current_tab()
            shown = set() if current == "Sketch" else set(tab_toolbars(self.workspace, current))
            groups = set(group_toolbars(self.workspace))
            for bar in mw.findChildren(QtWidgets.QToolBar):
                if bar.parent() is mw and bar.objectName() in groups:
                    bar.setVisible(bar.objectName() in shown)
            try:
                import bruceworx_toolbars

                bruceworx_toolbars.relayout()
            except Exception:
                pass

        def finish_sketch(self):
            command = definition().get("sketch", {}).get("finish")
            if command:
                FreeCADGui.runCommand(command)

    return Bar


def activate_workspace(name):
    """Switch to a workspace by name (its workbench)."""
    import FreeCADGui

    ws = workspace(definition(), name)
    if ws is None:
        return
    if FreeCADGui.activeWorkbench() and FreeCADGui.activeWorkbench().name() == ws["workbench"]:
        if _bar is not None:
            _bar.schedule()
        return
    FreeCADGui.activateWorkbench(ws["workbench"])


def bar():
    """The live bar (for tests and scripts)."""
    return _bar if _alive(_bar) else None


def _reload_workbench():
    import FreeCADGui

    wb = FreeCADGui.activeWorkbench()
    if wb is not None:
        try:
            wb.reloadActive()
        except Exception:
            pass


def _turn_on():
    global _bar, _manipulator
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore

    write_custom_toolbars(definition(), FreeCAD.ParamGet)
    if _manipulator is None:
        _manipulator = Manipulator()
        FreeCADGui.addWorkbenchManipulator(_manipulator)
    mw = FreeCADGui.getMainWindow()
    if _bar is None or not _alive(_bar):
        _bar = _make_bar()(mw)
        mw.addToolBar(QtCore.Qt.TopToolBarArea, _bar)
        mw.workbenchActivated.connect(_bar.schedule)
    _bar.show()
    _bar.schedule()


def _turn_off():
    global _manipulator
    import FreeCAD
    import FreeCADGui

    remove_custom_toolbars(FreeCAD.ParamGet)
    if _manipulator is not None:
        FreeCADGui.removeWorkbenchManipulator(_manipulator)
        _manipulator = None
    if _alive(_bar):
        _bar.hide()


class _PrefObserver:
    def onChange(self, _group, name):
        if name != PREF_NAME:
            return
        if enabled():
            _turn_on()
        else:
            _turn_off()
        _reload_workbench()


class _DocObserver:
    def slotInEdit(self, _vp):
        if _alive(_bar):
            _bar.schedule()

    def slotResetEdit(self, _vp):
        if _alive(_bar):
            _bar.schedule()

    def slotActivateDocument(self, _doc):
        if _alive(_bar):
            _bar.schedule()


def install():
    """Write the groups and add the bar before the start-up workbench activates."""
    global _observer, _doc_observer
    if _observer is not None:
        return
    import FreeCAD
    import FreeCADGui

    if enabled():
        _turn_on()
    else:
        remove_custom_toolbars(FreeCAD.ParamGet)
    _observer = _PrefObserver()
    group = FreeCAD.ParamGet(PREFS_PATH)
    # Keep the handle: a collected wrapper detaches its observer.
    group.Attach(_observer)
    _groups.append(group)
    _doc_observer = _DocObserver()
    FreeCADGui.addDocumentObserver(_doc_observer)
