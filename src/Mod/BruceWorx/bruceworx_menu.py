# SPDX-License-Identifier: LGPL-2.1-or-later
"""The top-level BruceWorx menu, shared by every BruceWorx module.

FreeCAD rebuilds the menu bar on every workbench activation:
``MenuManager::setup`` starts with ``menuBar->clear()``. A menu added with
``menuBar().addMenu()`` from an ``InitGui.py`` is therefore gone as soon as
the startup workbench is activated, which happens right after the GUI init
scripts run (``StartupPostProcess::showMainWindow`` then
``activateWorkbench``).

This module keeps one persistent ``QMenu`` (object name ``BruceWorxMenu``,
parented to the main window) and puts it back on the menu bar after every
rebuild. Two triggers, both idempotent:

* ``MainWindow.workbenchActivated``, emitted after the new workbench's menus
  are built;
* a workbench manipulator whose ``modifyMenuBar`` runs inside every
  ``Workbench::activate``, including rebuilds that do not switch workbench.

Both defer the re-attach with a zero-length timer so it runs after
``MenuManager::setup`` has finished.

Other modules (HistoryTimeline, the hooks add-on) find the same menu by its
object name, so it does not matter which module's ``InitGui.py`` runs first.
"""

MENU_OBJECT_NAME = "BruceWorxMenu"
MENU_TITLE = "Heuretes FreeCAD"
HELP_MENU_NAME = "&Help"

_state = {"hooked": False, "manipulator": None}


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:  # FreeCAD's PySide shim re-exports widgets on QtGui
        from PySide import QtCore, QtGui

        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def _window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def attach(window=None, menu=None):
    """Put the BruceWorx menu on the menu bar if it is not there. Before Help."""
    _QtCore, _QtGui, QtWidgets = _qt()
    window = window or _window()
    if window is None:
        return None
    if menu is None:
        menu = window.findChild(QtWidgets.QMenu, MENU_OBJECT_NAME)
        if menu is None:
            return None
    bar = window.menuBar()
    action = menu.menuAction()
    actions = list(bar.actions())
    if action in actions:
        if not action.isVisible():
            action.setVisible(True)
        return menu
    before = None
    for candidate in actions:
        sub = candidate.menu()
        if sub is not None and sub.objectName() == HELP_MENU_NAME:
            before = candidate
            break
    if before is not None:
        bar.insertMenu(before, menu)
    else:
        bar.addMenu(menu)
    action.setVisible(True)
    return menu


def schedule_attach():
    QtCore, _QtGui, _QtWidgets = _qt()
    QtCore.QTimer.singleShot(0, _safe_attach)


def _safe_attach():
    try:
        attach()
    except Exception as exc:  # pragma: no cover - FreeCAD console only
        try:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"BruceWorx menu: {exc}\n")
        except Exception:
            pass


class _Manipulator:
    """Workbench manipulator. Changes nothing; only re-attaches the menu."""

    def modifyMenuBar(self):
        schedule_attach()
        return []


def _hook(window):
    if _state["hooked"]:
        return
    _state["hooked"] = True
    try:
        window.workbenchActivated.connect(lambda _name: schedule_attach())
    except Exception:
        pass
    try:
        import FreeCADGui

        manipulator = _Manipulator()
        FreeCADGui.addWorkbenchManipulator(manipulator)
        _state["manipulator"] = manipulator
    except Exception:
        pass


def get_menu(create=True):
    """Return the shared BruceWorx QMenu, creating and hooking it when needed."""
    _QtCore, _QtGui, QtWidgets = _qt()
    window = _window()
    if window is None:
        return None
    menu = window.findChild(QtWidgets.QMenu, MENU_OBJECT_NAME)
    if menu is None:
        if not create:
            return None
        menu = QtWidgets.QMenu(MENU_TITLE, window)
        menu.setObjectName(MENU_OBJECT_NAME)
    _hook(window)
    attach(window, menu)
    return menu


def add_action(action, object_name=None):
    """Add ``action`` to the BruceWorx menu once (matched by object name), in its section (SECTIONS)."""
    menu = get_menu()
    if menu is None:
        return None
    name = object_name or action.objectName()
    if name:
        action.setObjectName(name)
        for existing in menu.actions():
            if existing.objectName() == name:
                return existing
    rank = _rank(name)
    section = rank // _SECTION_STEP
    if section and _first_named(menu, SECTION_SEPARATOR_NAME + str(section)) is None:
        _QtCore, QtGui, _QtWidgets = _qt()
        separator = QtGui.QAction(menu)
        separator.setSeparator(True)
        separator.setObjectName(SECTION_SEPARATOR_NAME + str(section))
        _insert_ranked(menu, separator, section * _SECTION_STEP - 1)
    _insert_ranked(menu, action, rank)
    return action


PREFS_SEPARATOR_NAME = "BruceWorxPreferencesSeparator"
PREFS_ACTION_NAME = "BruceWorxPreferencesAction"
SECTION_SEPARATOR_NAME = "BruceWorxMenuSection"
RANK_PROPERTY = "bwMenuRank"
_SECTION_STEP = 1000

# The menu's sections, top to bottom, each with its entries (action object names) in order. A separator
# opens every section but the first. An entry no section lists goes to the last one, in the order it came.
# Preferences stays under its own separator at the bottom (install_preferences_entry).
SECTIONS = (
    # File-like
    ("BruceWorxMenu_BruceWorx_NewProject", "BruceWorxMenu_BruceWorx_NewDrawing", "BruceWorxFastOpenAction"),
    # Vault
    ("BruceWorxMenu_Vault", "BruceWorxMenu_BruceWorx_CreateVault", "BruceWorxMenu_BruceWorx_ImportIntoVault",
     "BruceWorxMenu_BruceWorx_VaultSettings"),
    # Design
    ("BruceWorxMenu_Configurations", "BruceWorxPartNumberAction", "BruceWorxMenu_BruceWorx_RectangularPattern",
     "BruceWorxMenu_BruceWorx_CircularPattern", "BruceWorxMenu_BruceWorx_InsertBoard",
     "BruceWorxMenu_BruceWorx_ImportKiCadProject", "BruceWorxMenu_BruceWorx_CodePartNew",
     "BruceWorxMenu_BruceWorx_CodePartEdit"),
    # View
    ("BruceWorxMenu_BruceWorx_RightPanel", "HistoryTimelineAction", "BruceWorxStudioShadingAction",
     "BruceWorxGroundGridAction"),
    # Agents
    ("BruceWorxAllowClaude",),
    # anything else
    (),
)


def _rank(name):
    """Where an entry sorts: its section times _SECTION_STEP, plus its place in the section."""
    for section, names in enumerate(SECTIONS):
        if name in names:
            return section * _SECTION_STEP + names.index(name)
    return (len(SECTIONS) - 1) * _SECTION_STEP + _SECTION_STEP // 2


def _insert_ranked(menu, action, rank):
    """Put ``action`` before the first ranked entry that sorts after it, else above Preferences."""
    action.setProperty(RANK_PROPERTY, rank)
    before = _first_named(menu, PREFS_SEPARATOR_NAME)
    for existing in menu.actions():
        if existing.objectName() == PREFS_SEPARATOR_NAME:
            break
        other = existing.property(RANK_PROPERTY)
        if other is not None and int(other) > rank:
            before = existing
            break
    if before is not None:
        menu.insertAction(before, action)
    else:
        menu.addAction(action)


def _first_named(menu, name):
    for existing in menu.actions():
        if existing.objectName() == name:
            return existing
    return None


def install_preferences_entry():
    """``Heuretes FreeCAD → Heuretes FreeCAD Preferences…`` opens the BruceWorx preference page."""
    _QtCore, QtGui, _QtWidgets = _qt()
    window = _window()
    if window is None:
        return None
    action = QtGui.QAction("Heuretes FreeCAD Preferences…", window)
    action.setToolTip("Every Heuretes FreeCAD default, with its off switch.")

    def _open(_checked=False):
        import FreeCADGui

        FreeCADGui.showPreferences("Heuretes FreeCAD")

    action.triggered.connect(_open)
    menu = get_menu()
    if menu is None:
        return None
    existing = _first_named(menu, PREFS_ACTION_NAME)
    if existing is not None:
        return existing
    action.setObjectName(PREFS_ACTION_NAME)
    separator = menu.addSeparator()
    separator.setObjectName(PREFS_SEPARATOR_NAME)
    menu.addAction(action)
    return action
