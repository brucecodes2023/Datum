# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fixed toolbar rows: app-wide toolbars, the workspace bar, then the workbench's tools.

FreeCAD restores toolbar line breaks from the saved window state, so tools can
end up spread over three or four rows (add-ons that rebuild the toolbar area
leave breaks behind). With Preferences/BruceWorx/CompactToolbars on (default),
BruceWorx re-lays the top toolbars after every workbench switch (roadmap §13.3,
step A): File, View and search share the first row, the workspace bar (step C2)
has the next, and every workbench toolbar follows on the last row with no
further breaks. With the macOS title row (step C4) on top, the workspace bar
shares its row with File and View (plan_rows).

When the tools row is narrower than its toolbars, Qt gives the first ones their
full width and squeezes the last ones to one button and », with their captions
cut ("CON…T"). fit_tools_row shares the row out instead (fair_widths): every
toolbar keeps at least its caption's width and shows the buttons that fit; the
rest are under » and the caption menu (UAT 25 Oct, #2).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "CompactToolbars"
TOOLBARS_PATH = "User parameter:BaseApp/MainWindow/Toolbars"

# Toolbars that belong to the application, not to a workbench.
# The search fields (the SearchBar add-on's, BruceWorx's Cmd-K) belong with them on the first row.
APP_TOOLBARS = (
    "File",
    "Workbench",
    "View",
    "Edit",
    "Clipboard",
    "Macro",
    "Help",
    "Individual Views",
    "SearchBar",
    "BruceWorxSearchBar",
    "BruceWorxSelectFilter",
)
# Shown in every workbench (Structure comes with the standard workbench), so not a
# sign that the workbench's own toolbars show.
ALWAYS_SHOWN = APP_TOOLBARS + ("Structure",)


# The macOS title row (step C4) is the first row; the workspace bar (step C2) gets a
# row of its own, or shares it with the app toolbars when the title row is there.
TOP_ROW = ("BruceWorxTitleRow",)
OWN_ROW = ("BruceWorxWorkspaces",)


def plan_rows(names, app_toolbars=APP_TOOLBARS):
    """Toolbar names grouped in rows, top to bottom.

    Without the title row: app toolbars, the workspace bar, the workbench tools.
    With it: the title row, the workspace bar followed by the app toolbars, the
    tools. Empty rows are left out; order inside a group is kept.
    """
    top = [n for n in names if n in TOP_ROW]
    bar = [n for n in names if n in OWN_ROW]
    app = [n for n in names if n in app_toolbars and n not in TOP_ROW + OWN_ROW]
    rest = [n for n in names if n not in TOP_ROW + OWN_ROW and n not in app_toolbars]
    rows = [top, bar + app, rest] if top else [app, bar, rest]
    return [row for row in rows if row]


def plan_breaks(names, app_toolbars=APP_TOOLBARS):
    """Names that should start a new row (the first of every row but the top one)."""
    return {row[0] for row in plan_rows(names, app_toolbars)[1:]}


def fair_widths(hints, mins, space):
    """Widths for toolbars sharing a row of ``space`` px; None when all fit.

    Each gets at most its hint and at least its minimum. The widths shrink
    together: a toolbar below the common cap keeps its hint, the others get the
    cap, so a wide group gives up buttons before a narrow one does.
    """
    if sum(hints) <= space:
        return None
    mins = [min(m, h) for m, h in zip(mins, hints)]
    if sum(mins) >= space:
        return list(mins)
    lo, hi = 0, max(hints)
    # The largest cap whose widths still fit.
    while lo < hi:
        cap = (lo + hi + 1) // 2
        if sum(max(m, min(h, cap)) for h, m in zip(hints, mins)) <= space:
            lo = cap
        else:
            hi = cap - 1
    return [max(m, min(h, lo)) for h, m in zip(hints, mins)]


def _enabled():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True)


def relayout():
    """Apply ``plan_breaks`` to the visible top toolbars of the main window."""
    if not _enabled():
        return
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    top = QtCore.Qt.ToolBarArea.TopToolBarArea
    bars = [
        tb
        for tb in mw.findChildren(QtWidgets.QToolBar)
        if tb.parent() is mw and tb.isVisible() and mw.toolBarArea(tb) == top
    ]
    # Row order as laid out: breaks are positional, so sort by where they sit.
    bars.sort(key=lambda tb: (tb.geometry().y(), tb.geometry().x()))
    by_name = {tb.objectName(): tb for tb in bars}
    rows = plan_rows([tb.objectName() for tb in bars])
    ordered = [by_name[name] for row in rows for name in row]
    wanted = {row[0] for row in rows[1:]}
    for tb in ordered:
        if mw.toolBarBreak(tb):
            mw.removeToolBarBreak(tb)
    # Re-adding moves each toolbar to the end of the top area, in this order.
    for tb in ordered:
        if tb.objectName() in wanted:
            mw.addToolBarBreak(top)
        mw.addToolBar(top, tb)
        tb.show()
    fit_tools_row()


# Room kept on each side of a caption so the narrowest group does not cut it.
CAPTION_PAD = 12
NO_LIMIT = 16777215  # QWIDGETSIZE_MAX


def _min_width(tb):
    from PySide import QtWidgets

    width = tb.minimumSizeHint().width()
    caption = tb.findChild(QtWidgets.QToolButton, "BruceWorxGroupCaption")
    if caption is not None:
        width = max(width, caption.sizeHint().width() + CAPTION_PAD)
    return width


def fit_tools_row():
    """Share the tools row (the last row) out between its toolbars (fair_widths)."""
    if not _enabled():
        return
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    top = QtCore.Qt.ToolBarArea.TopToolBarArea
    bars = [
        tb
        for tb in mw.findChildren(QtWidgets.QToolBar)
        if tb.parent() is mw and tb.isVisible() and mw.toolBarArea(tb) == top and not tb.isFloating()
    ]
    rows = plan_rows([tb.objectName() for tb in bars])
    by_name = {tb.objectName(): tb for tb in bars}
    tools = set(rows[-1]) if len(rows) > 1 else set()
    for tb in bars:
        if tb.objectName() not in tools and tb.maximumWidth() != NO_LIMIT:
            tb.setMaximumWidth(NO_LIMIT)
    row = [by_name[name] for name in rows[-1]] if tools else []
    if not row:
        return
    widths = fair_widths([tb.sizeHint().width() for tb in row], [_min_width(tb) for tb in row], mw.width())
    for tb, width in zip(row, widths or [NO_LIMIT] * len(row)):
        if tb.maximumWidth() != width:
            tb.setMaximumWidth(width)


def all_hidden(states):
    """True when there are toolbars and none of them is shown."""
    states = list(states)
    return bool(states) and not any(states)


def show_if_all_hidden():
    """Bring back the toolbars when none of them is shown.

    FreeCAD-Ribbon hides FreeCAD's own toolbars, and FreeCAD stores that per
    toolbar (BaseApp/MainWindow/Toolbars). While BruceWorx replaces the Ribbon
    (Preferences/BruceWorx/UseBruceWorxBar) those toolbars would stay hidden and
    the window would have no top bar at all. The app-wide toolbars and the active
    workbench's toolbars are checked apart, once each (the move away from the Ribbon);
    after that a whole group can be hidden on purpose.
    """
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    if not FreeCAD.ParamGet(PREFS_PATH).GetBool("UseBruceWorxBar", True):
        return
    mw = FreeCADGui.getMainWindow()
    # Toolbars FreeCAD offers right now (app-wide and the active workbench's): their
    # show/hide entry is visible.
    bars = [
        tb
        for tb in mw.findChildren(QtWidgets.QToolBar)
        if tb.parent() is mw and tb.toggleViewAction().isVisible()
    ]
    group = FreeCAD.ParamGet(TOOLBARS_PATH)
    done = FreeCAD.ParamGet(PREFS_PATH + "/ToolbarsRestored")
    app = [tb for tb in bars if tb.objectName() in ALWAYS_SHOWN]
    workbench = [tb for tb in bars if tb.objectName() not in ALWAYS_SHOWN]
    for key, part in (("App", app), (FreeCADGui.activeWorkbench().name(), workbench)):
        if not part or done.GetBool(key, False):
            continue
        done.SetBool(key, True)
        if all_hidden(not tb.isHidden() for tb in part):
            for tb in part:
                tb.show()
                group.SetBool(tb.objectName(), True)


def _after_switch():
    show_if_all_hidden()
    relayout()


_installed = False
_resize_filter = None


def _make_resize_filter():
    from PySide import QtCore

    class ResizeFilter(QtCore.QObject):
        """Shares the tools row out again after the window changes width."""

        def __init__(self):
            super().__init__()
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(0)
            self.timer.timeout.connect(lambda: fit_tools_row())

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Resize and event.size().width() != event.oldSize().width():
                self.timer.start()
            return False

    return ResizeFilter()


def install():
    """Re-lay the toolbars (and bring them back if all are hidden) after every workbench switch."""
    global _installed, _resize_filter
    if _installed:
        return
    import FreeCADGui
    from PySide import QtCore

    mw = FreeCADGui.getMainWindow()
    mw.workbenchActivated.connect(lambda _name: QtCore.QTimer.singleShot(0, _after_switch))
    _resize_filter = _make_resize_filter()
    mw.installEventFilter(_resize_filter)
    _installed = True
    # The start workbench may already be active.
    QtCore.QTimer.singleShot(0, _after_switch)
