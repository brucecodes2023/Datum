# SPDX-License-Identifier: LGPL-2.1-or-later
"""Captioned tool groups: a small caption under each workbench toolbar.

Roadmap §13.4, step C1 (UIX-06). Every workbench toolbar in the top area gets a
14 px bottom margin and, centred in it, a flat caption button (CREATE ▾,
PATTERN ▾, ...). Clicking the caption opens a menu with every command of the
group, named, including those that do not fit on the row. The menu holds the
toolbar's own QActions, so enabled state, checked state and shortcuts stay live.

App toolbars (File, View, ...) get no caption, nor does a toolbar that is
floating, vertical or outside the top area. Captions come from CAPTIONS, or from
the toolbar title with its workbench prefix and a trailing "Features"/"Tools"
taken off; a title "Solid · Create" (the step C2 groups) gives "Create".

Preferences/BruceWorx/GroupCaptions (default on). Turning it off removes the
captions and gives the toolbars their margins back.
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "GroupCaptions"
OBJECT_NAME = "BruceWorxGroupCaption"
CAPTION_HEIGHT = 14  # px added under the buttons
ARROW = "▾"

# Toolbars that never get a caption: the app-wide ones and those shown in every workbench.
SKIP = (
    "File",
    "Workbench",
    "View",
    "Edit",
    "Clipboard",
    "Macro",
    "Help",
    "Individual Views",
    "Structure",
    "SearchBar",
)

# Stock toolbar names with a caption that does not follow from the title.
CAPTIONS = {
    "Part Design Helper Features": "Construct",
    "Part Design Modeling Features": "Create",
    "Part Design Dress-Up Features": "Modify",
    "Part Design Transformation Features": "Pattern",
    "Assembly": "Assemble",
    "Assembly Joints": "Joints",
    "Solids": "Primitives",
    "Boolean Tools": "Boolean",
    "Sketcher": "Sketch",
    # Sketcher's edit-mode toolbars (Sketcher/Gui/Workbench.cpp, editModeToolbarNames)
    "Edit Mode": "Sketch",
    "Geometries": "Create",
    "Constraints": "Constrain",
    "Sketcher Tools": "Modify",
    "B-Spline Tools": "B-spline",
    "Visual Helpers": "Display",
    "Sketcher Edit Tools": "Edit",
    "Mechanical Boundary Conditions and Loads": "Mechanical",
    "Thermal Boundary Conditions and Loads": "Thermal",
    "Fluid Boundary Conditions": "Fluid",
    "Electromagnetic Boundary Conditions": "Electromagnetic",
    "Geometrical Analysis Features": "Analysis",
}

# Workbench words a title may start with.
PREFIXES = ("Part Design", "Sketcher", "Assembly", "TechDraw", "Mesh", "Part", "Surface", "FEM", "CAM", "BIM")
SUFFIXES = ("Features", "Tools", "Commands")


def caption_for(title):
    """The short caption for a toolbar title (not upper-cased)."""
    title = (title or "").strip()
    if "·" in title:
        return title.rsplit("·", 1)[1].strip()
    if title in CAPTIONS:
        return CAPTIONS[title]
    words = title
    for prefix in PREFIXES:
        if words.startswith(prefix + " ") and len(words) > len(prefix) + 1:
            words = words[len(prefix) + 1 :]
            break
    for suffix in SUFFIXES:
        if words.endswith(" " + suffix):
            words = words[: -len(suffix) - 1]
            break
    return words or title


def caption_text(title):
    """What the caption button shows: upper case and a menu arrow."""
    return f"{caption_for(title).upper()} {ARROW}"


def place_caption(span_left, span_right, bar_width, caption_width):
    """(x, width) of the caption: centred under the buttons, kept inside the toolbar."""
    width = max(0, min(caption_width, bar_width))
    x = (span_left + span_right - width) // 2
    return max(0, min(x, bar_width - width)), width


def eligible(name, top_area, floating, horizontal):
    return bool(name) and name not in SKIP and not name.startswith("BruceWorx") and top_area and not floating and horizontal


# --- GUI ---------------------------------------------------------------------

_classes = None
_manager = None


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _set_group_property(bar, on):
    """Mark a captioned toolbar for the stylesheet (its divider)."""
    bar.setProperty("bwGroup", on)
    bar.style().unpolish(bar)
    bar.style().polish(bar)


def _more_actions(toolbar, menu):
    """QActions for the workspace group's "more" commands (bruceworx_workspaces)."""
    import FreeCADGui
    from PySide import QtGui

    try:
        import bruceworx_workspaces

        names = bruceworx_workspaces.more_commands(bruceworx_workspaces.definition(), toolbar)
    except Exception:
        return []
    actions = []
    for name in names:
        command = FreeCADGui.Command.get(name)
        if command is None:
            continue
        existing = command.getAction()
        if existing:
            actions.append(existing[0])
            continue
        # Not on any menu or toolbar yet: a stand-in that runs the command.
        info = command.getInfo()
        icon = FreeCADGui.getIcon(info.get("pixmap", "")) if info.get("pixmap") else None
        action = QtGui.QAction(icon or QtGui.QIcon(), info.get("menuText", name).replace("&", ""), menu)
        # No isActive() here: some commands crash when asked outside their context.
        action.setToolTip(info.get("toolTip", ""))
        action.triggered.connect(lambda _c=False, n=name: FreeCADGui.runCommand(n))
        actions.append(action)
    return actions


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtWidgets

    class Caption(QtWidgets.QToolButton):
        """The caption under one toolbar; its menu lists the toolbar's actions."""

        def __init__(self, bar):
            super().__init__(bar)
            self.setObjectName(OBJECT_NAME)
            self.setAutoRaise(True)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.setFixedHeight(CAPTION_HEIGHT)
            menu = QtWidgets.QMenu(self)
            menu.setObjectName(OBJECT_NAME + "Menu")
            menu.aboutToShow.connect(self.fill)
            self.setMenu(menu)
            self.update_text()

        @property
        def bar(self):
            return self.parentWidget()

        def update_text(self):
            self.title = self.bar.windowTitle()
            self.setText(caption_text(self.title))
            self.setToolTip(self.title)
            self.adjustSize()

        def fill(self):
            menu = self.menu()
            menu.clear()
            # A label, not addSection: the macOS style draws a section as a bare line.
            title = QtWidgets.QLabel(caption_for(self.title).upper())
            title.setObjectName(OBJECT_NAME + "MenuTitle")
            header = QtWidgets.QWidgetAction(menu)
            header.setDefaultWidget(title)
            header.setEnabled(False)
            menu.addAction(header)
            for action in self.bar.actions():
                if action.isSeparator() or not action.isVisible():
                    continue
                if isinstance(action, QtWidgets.QWidgetAction):
                    continue
                menu.addAction(action)
            more = _more_actions(self.bar.objectName(), menu)
            if more:
                menu.addSeparator()
                label = QtWidgets.QLabel("MORE IN THIS GROUP")
                label.setObjectName(OBJECT_NAME + "MenuTitle")
                header = QtWidgets.QWidgetAction(menu)
                header.setDefaultWidget(label)
                header.setEnabled(False)
                menu.addAction(header)
                for action in more:
                    menu.addAction(action)

        def place(self):
            bar = self.bar
            if not _alive(bar):
                return
            spans = []
            for action in bar.actions():
                if action.isSeparator():
                    continue
                widget = bar.widgetForAction(action)
                if widget is not None and widget is not self and widget.isVisible():
                    spans.append(widget.geometry())
            if spans:
                left = min(r.left() for r in spans)
                right = max(r.right() for r in spans) + 1
            else:
                left, right = 0, bar.width()
            x, width = place_caption(left, right, bar.width(), self.sizeHint().width())
            rect = QtCore.QRect(x, bar.height() - CAPTION_HEIGHT - 1, width, CAPTION_HEIGHT)
            # Only touch the widget on a change: showing a child asks the toolbar for a new layout.
            if self.geometry() != rect:
                self.setGeometry(rect)
            if self.isHidden():
                self.raise_()
                self.show()

        def place_later(self):
            QtCore.QTimer.singleShot(0, lambda: _alive(self) and self.place())

    class Manager(QtCore.QObject):
        """Adds and removes captions; keeps them placed through an event filter."""

        def __init__(self):
            super().__init__()
            self.margins = {}  # toolbar object name -> original bottom margin
            self._pending = False

        def schedule(self, *_args):
            if self._pending:
                return
            self._pending = True
            QtCore.QTimer.singleShot(0, self.refresh)

        def _bars(self):
            import FreeCADGui

            mw = FreeCADGui.getMainWindow()
            return mw, [tb for tb in mw.findChildren(QtWidgets.QToolBar) if tb.parent() is mw]

        def refresh(self):
            self._pending = False
            mw, bars = self._bars()
            on = enabled()
            top = QtCore.Qt.ToolBarArea.TopToolBarArea
            for bar in bars:
                ok = on and eligible(
                    bar.objectName(),
                    mw.toolBarArea(bar) == top,
                    bar.isFloating(),
                    bar.orientation() == QtCore.Qt.Horizontal,
                )
                if ok:
                    self.attach(bar)
                else:
                    self.detach(bar)

        def attach(self, bar):
            caption = bar.findChild(Caption, OBJECT_NAME)
            if caption is None:
                margins = bar.contentsMargins()
                self.margins[bar.objectName()] = margins.bottom()
                bar.setContentsMargins(margins.left(), margins.top(), margins.right(), margins.bottom() + CAPTION_HEIGHT)
                caption = Caption(bar)
                _set_group_property(bar, True)
                bar.installEventFilter(self)
                bar.topLevelChanged.connect(self.schedule)
                bar.orientationChanged.connect(self.schedule)
            elif caption.title != bar.windowTitle():
                caption.update_text()
            caption.place()

        def detach(self, bar):
            caption = bar.findChild(Caption, OBJECT_NAME)
            if caption is None:
                return
            bar.removeEventFilter(self)
            caption.hide()
            caption.setParent(None)
            caption.deleteLater()
            margins = bar.contentsMargins()
            bottom = self.margins.pop(bar.objectName(), max(0, margins.bottom() - CAPTION_HEIGHT))
            bar.setContentsMargins(margins.left(), margins.top(), margins.right(), bottom)
            _set_group_property(bar, False)

        def eventFilter(self, obj, event):
            kind = event.type()
            caption = None
            if kind in (
                QtCore.QEvent.Resize,
                QtCore.QEvent.Show,
                QtCore.QEvent.LayoutRequest,
                QtCore.QEvent.ActionAdded,
                QtCore.QEvent.ActionRemoved,
                QtCore.QEvent.ActionChanged,
                QtCore.QEvent.WindowTitleChange,
            ):
                caption = obj.findChild(Caption, OBJECT_NAME)
            if caption is None:
                return False
            if kind in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
                caption.place()
            elif kind == QtCore.QEvent.WindowTitleChange:
                caption.update_text()
                caption.place_later()
            else:
                # The buttons move only after the layout ran.
                caption.place_later()
            return False

    class MainFilter(QtCore.QObject):
        """New toolbars (Sketcher's edit toolbars, add-ons) get a caption too."""

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.ChildPolished and isinstance(event.child(), QtWidgets.QToolBar):
                _manager.schedule()
            return False

    _classes = (Caption, Manager, MainFilter)
    return _classes


class _PrefObserver:
    def onChange(self, _group, name):
        if _manager is not None and name == PREF_NAME:
            _manager.schedule()


_observer = None
_main_filter = None
_groups = []


def captions():
    """The live caption buttons (for tests and scripts)."""
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    return [w for w in mw.findChildren(QtWidgets.QToolButton, OBJECT_NAME)]


def install():
    """Caption the workbench toolbars now and after every workbench switch."""
    global _manager, _observer, _main_filter
    if _manager is not None:
        return
    import FreeCAD
    import FreeCADGui

    _caption, manager_class, main_filter_class = _make_classes()
    _manager = manager_class()
    mw = FreeCADGui.getMainWindow()
    mw.workbenchActivated.connect(_manager.schedule)
    _main_filter = main_filter_class()
    mw.installEventFilter(_main_filter)
    _observer = _PrefObserver()
    group = FreeCAD.ParamGet(PREFS_PATH)
    # Keep the handle: a collected wrapper detaches its observer.
    group.Attach(_observer)
    _groups.append(group)
    _manager.schedule()
