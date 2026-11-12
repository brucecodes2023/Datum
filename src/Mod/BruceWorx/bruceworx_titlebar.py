# SPDX-License-Identifier: LGPL-2.1-or-later
"""Document tabs in the macOS title bar.

Roadmap §13.4, step C4 (UIX-06), macOS only. The window's title strip becomes
the first toolbar row (BruceWorxTitleRow): traffic lights, save / undo / redo,
the document tabs and a + for a new document, then the Cmd-K search field on
the right. There is no separate title strip any more.

* Native window: NSWindow gets a full-size content view, a transparent title
  bar and no title text, through the Objective-C runtime from ctypes (no
  compiled code). An empty NSToolbar in the UnifiedCompact style makes the
  strip 40 pt tall, so the traffic lights sit centred in the 40 px row. Qt's
  unified title-and-toolbar mode is switched off meanwhile (it would fight),
  through MainWindow/UnifiedTitleAndToolBar, which is put back afterwards.
* Tabs: the row's tab bar mirrors the document area's own tab bar, which step
  B keeps current (titles without "*", the modified dot, creation order).
  Clicking, closing and dragging a tab are passed back to it. The document
  area's bar is hidden and styled to zero height so its space is given back.
* Moving the window: a press on an empty part of the row moves it; a
  double-click zooms or minimises, as System Settings says.
* Full screen hides the traffic lights, so the left inset shrinks.
* Docks ignore the title strip's safe area and are laid out again when they
  show; otherwise the floating Browser kept its tree outside its card.
* Toolbars ignore it too. A toolbar that is still hidden sits at the top left,
  inside the old strip, so Qt gave it a 40 px top margin; shown and moved to
  its row it lost the margin, but its layout kept the squashed rect, and the
  tool groups of a tab showed no buttons until the window was resized (UAT
  25 Oct, #10).

With MainWindow/DocumentTabPos at the bottom (the pack's default since 24 Oct),
the row keeps the traffic lights, quick commands and search but shows no tabs:
the document area's own tab bar stays at the bottom of the window.

Preferences/BruceWorx/TabsInTitleBar (default on). Off puts everything back
(step B's tab row under the toolbars).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import sys

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "TabsInTitleBar"
STATE_PATH = PREFS_PATH + "/TitleRow"
MAINWINDOW_PATH = "User parameter:BaseApp/Preferences/MainWindow"
UNIFIED = "UnifiedTitleAndToolBar"
ROW_NAME = "BruceWorxTitleRow"
ROW_HEIGHT = 40
LIGHTS_INSET = 84  # px left of the row kept free for the traffic lights
FULLSCREEN_INSET = 8
NS_FULL_SIZE_CONTENT_VIEW = 1 << 15
NS_TITLE_HIDDEN = 1
NS_TOOLBAR_UNIFIED_COMPACT = 4
QUICK_COMMANDS = ("Std_Save", "Std_Undo", "Std_Redo")
MDI_TAB_SHEET = (
    "QTabBar::tab { height: 0px; min-height: 0px; padding: 0px; margin: 0px; border: none; }"
)


BOTTOM = 1  # MainWindow/DocumentTabPos: 0 top, 1 bottom


def tabs_at_bottom():
    """True when the document tabs sit at the bottom of the window (not in the title row)."""
    import FreeCAD

    return FreeCAD.ParamGet(MAINWINDOW_PATH).GetInt("DocumentTabPos", 1) == BOTTOM


def _pinned_start(source, index):
    """True for the Start page's tab while bruceworx_doctabs keeps it pinned (no close button)."""
    try:
        import bruceworx_doctabs

        return index == 0 and bruceworx_doctabs._enabled() and source.tabText(0).replace("&", "") == "Start"
    except Exception:
        return False


def double_click_action(value):
    """What a double-click on the row does, from AppleActionOnDoubleClick."""
    value = (value or "").strip().lower()
    if value == "minimize":
        return "minimize"
    if value == "none":
        return None
    return "zoom"  # "Maximize", the default, and anything unknown


def inset(fullscreen):
    return FULLSCREEN_INSET if fullscreen else LIGHTS_INSET


def tab_title(text):
    """A document tab's title: no "*", and no " : 1" for a document's first view."""
    text = text.rstrip("*").strip()
    return text[:-4] if text.endswith(" : 1") else text


def tab_state(texts, modified, current):
    """A comparable snapshot of the document tabs: ((text, modified), ...), current."""
    return tuple(zip(texts, modified)), current


# --- Objective-C from ctypes ---------------------------------------------------

_objc = None


class NSRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


def _lib():
    global _objc
    if _objc is None:
        _objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        _objc.sel_registerName.restype = ctypes.c_void_p
        _objc.sel_registerName.argtypes = [ctypes.c_char_p]
        _objc.objc_getClass.restype = ctypes.c_void_p
        _objc.objc_getClass.argtypes = [ctypes.c_char_p]
    return _objc


def _send(restype, obj, selector, *args, argtypes=()):
    objc = _lib()
    call = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", objc))
    return call(obj, objc.sel_registerName(selector), *args)


def _ns_window(widget):
    view = ctypes.c_void_p(int(widget.winId()))
    return _send(ctypes.c_void_p, view, b"window")


def set_native_title_bar(widget, on):
    """Full-size content, transparent title bar, compact toolbar; or the stock window."""
    win = _ns_window(widget)
    if not win:
        return False
    mask = _send(ctypes.c_ulong, win, b"styleMask")
    mask = mask | NS_FULL_SIZE_CONTENT_VIEW if on else mask & ~NS_FULL_SIZE_CONTENT_VIEW
    _send(None, win, b"setStyleMask:", ctypes.c_ulong(mask), argtypes=(ctypes.c_ulong,))
    _send(None, win, b"setTitlebarAppearsTransparent:", ctypes.c_bool(on), argtypes=(ctypes.c_bool,))
    _send(None, win, b"setTitleVisibility:", ctypes.c_long(NS_TITLE_HIDDEN if on else 0), argtypes=(ctypes.c_long,))
    toolbar = None
    if on:
        cls = _lib().objc_getClass(b"NSToolbar")
        toolbar = _send(ctypes.c_void_p, _send(ctypes.c_void_p, cls, b"alloc"), b"init")
    _send(None, win, b"setToolbar:", ctypes.c_void_p(toolbar), argtypes=(ctypes.c_void_p,))
    if on:
        _send(None, win, b"setToolbarStyle:", ctypes.c_long(NS_TOOLBAR_UNIFIED_COMPACT), argtypes=(ctypes.c_long,))
    return True


def _window_action(widget, selector):
    win = _ns_window(widget)
    if win:
        _send(None, win, selector, ctypes.c_void_p(None), argtypes=(ctypes.c_void_p,))


def _system_double_click():
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleActionOnDoubleClick"], capture_output=True, text=True, timeout=2
        )
        return double_click_action(out.stdout if out.returncode == 0 else "")
    except Exception:
        return "zoom"


# --- GUI ---------------------------------------------------------------------

_row = None
_observer = None
_groups = []
_classes = None


def available():
    return sys.platform == "darwin"


def enabled():
    import FreeCAD

    return available() and bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    class TabButton(QtWidgets.QAbstractButton):
        """Close button of a title-row tab: a dot while modified, a thin x on hover."""

        def __init__(self, parent):
            super().__init__(parent)
            self.setObjectName("documentTabButton")
            self.setAttribute(QtCore.Qt.WA_Hover)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.setCursor(QtCore.Qt.ArrowCursor)
            self.setFixedSize(16, 16)
            self.modified = False
            self.shown = False

        def set_state(self, modified, shown):
            if (modified, shown) != (self.modified, self.shown):
                self.modified, self.shown = modified, shown
                self.update()

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            option = QtWidgets.QStyleOption()
            option.initFrom(self)
            self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, option, painter, self)
            hover = bool(option.state & QtWidgets.QStyle.State_MouseOver)
            color = self.palette().color(self.foregroundRole())
            centre = QtCore.QRectF(self.rect()).center()
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            if self.modified and not hover:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(centre, 3.0, 3.0)
            elif hover or self.shown:
                pen = QtGui.QPen(color, 1.2)
                pen.setCapStyle(QtCore.Qt.RoundCap)
                painter.setPen(pen)
                r = 3.5
                painter.drawLine(centre + QtCore.QPointF(-r, -r), centre + QtCore.QPointF(r, r))
                painter.drawLine(centre + QtCore.QPointF(-r, r), centre + QtCore.QPointF(r, -r))

    class Row(QtWidgets.QToolBar):
        """The title row: inset, quick commands, document tabs, search."""

        def __init__(self, mw):
            super().__init__(mw)
            self.setObjectName(ROW_NAME)
            self.setWindowTitle("Title bar")
            self.setMovable(False)
            self.setFloatable(False)
            self.setFixedHeight(ROW_HEIGHT)
            # Hidden toggle: ToolBarManager then leaves the row alone on every switch.
            self.toggleViewAction().setVisible(False)
            self.mdi = mw.findChild(QtWidgets.QMdiArea)
            self.snapshot = None
            self.hovered = -1
            self.search_action = None

            self.inset = QtWidgets.QWidget(self)
            self.inset.setObjectName("BruceWorxTitleInset")
            self.inset.setFixedWidth(LIGHTS_INSET)
            self.addWidget(self.inset)
            for name in QUICK_COMMANDS:
                command = FreeCADGui.Command.get(name)
                actions = command.getAction() if command else []
                if actions:
                    self.addAction(actions[0])

            self.tabs = QtWidgets.QTabBar(self)
            self.tabs.setObjectName("BruceWorxTitleTabs")
            self.tabs.setDocumentMode(True)
            self.tabs.setExpanding(False)
            self.tabs.setMovable(True)
            self.tabs.setDrawBase(False)
            self.tabs.setElideMode(QtCore.Qt.ElideRight)
            self.tabs.setFocusPolicy(QtCore.Qt.NoFocus)
            self.tabs.setMouseTracking(True)
            self.tabs.installEventFilter(self)
            self.tabs.currentChanged.connect(self.tab_clicked)
            self.tabs.tabMoved.connect(self.tab_moved)
            # A widget in a toolbar is shown and hidden through its action, not the widget.
            self.tabs_action = self.addWidget(self.tabs)

            self.new = QtWidgets.QToolButton(self)
            self.new.setObjectName("BruceWorxNewTab")
            self.new.setText("+")
            self.new.setToolTip("New document")
            self.new.clicked.connect(lambda: self.new_document())
            self.new_action = self.addWidget(self.new)

            self.space = QtWidgets.QWidget(self)
            self.space.setObjectName("BruceWorxTitleSpace")
            self.space.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            self.addWidget(self.space)
            for widget in (self, self.inset, self.space):
                widget.installEventFilter(self)
            self.no_safe_area()

            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(400)
            self.timer.timeout.connect(self.sync)
            if self.mdi is not None:
                self.mdi.subWindowActivated.connect(lambda _w: QtCore.QTimer.singleShot(0, self.sync))

        def new_document(self):
            """The "+": the part / assembly / hybrid menu under it (an empty document when that is off)."""
            try:
                import bruceworx_newdoc

                bruceworx_newdoc.choose(self.new)
            except Exception:  # noqa: BLE001 -- never lose the button
                FreeCADGui.runCommand("Std_New")

        def no_safe_area(self):
            """The row sits in the old title strip: none of its widgets may keep it free."""
            for widget in [self] + self.findChildren(QtWidgets.QWidget):
                widget.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)

        # --- the document area's own tab bar
        def mdi_tabs(self):
            # The document area is gone at shutdown while timers may still call sync(): no tabs then.
            return self.mdi.findChild(QtWidgets.QTabBar) if _alive(self.mdi) else None

        def hide_mdi_tabs(self, hide):
            bar = self.mdi_tabs()
            if bar is None:
                return
            bar.setStyleSheet(MDI_TAB_SHEET if hide else "")
            bar.setVisible(not hide)
            # Make the area lay out again so it gives back (or takes) the bar's space.
            self.mdi.resize(self.mdi.size() + QtCore.QSize(1, 0))
            self.mdi.resize(self.mdi.size() - QtCore.QSize(1, 0))

        # --- mirror
        def sync(self):
            source = self.mdi_tabs()
            if source is None or not _alive(self):
                return
            if tabs_at_bottom():
                # The document area's tab bar does the job at the bottom; the row keeps no copy.
                if self.tabs_action.isVisible():
                    self.tabs_action.setVisible(False)
                    self.new_action.setVisible(False)
                    self.snapshot = None
                if not source.isVisible() or source.styleSheet():
                    self.hide_mdi_tabs(False)
                return
            if not self.tabs_action.isVisible():
                self.tabs_action.setVisible(True)
                self.new_action.setVisible(True)
            if source.isVisible():
                self.hide_mdi_tabs(True)
            subs = self.mdi.subWindowList(QtWidgets.QMdiArea.CreationOrder)
            texts = [source.tabText(i) for i in range(source.count())]
            modified = []
            for i in range(source.count()):
                sub = subs[i] if i < len(subs) else None
                widget = sub.widget() if sub is not None else None
                modified.append(bool(sub and (sub.isWindowModified() or (widget and widget.isWindowModified()))))
            icons = tuple(source.tabIcon(i).cacheKey() for i in range(source.count()))  # a tab shows its kind
            snapshot = (tab_state(texts, modified, source.currentIndex()), self.hovered, icons)
            if snapshot == self.snapshot:
                return
            self.snapshot = snapshot
            self.tabs.blockSignals(True)
            while self.tabs.count() > source.count():
                self.tabs.removeTab(self.tabs.count() - 1)
            while self.tabs.count() < source.count():
                self.tabs.addTab("")
            for i, text in enumerate(texts):
                self.tabs.setTabText(i, tab_title(text))
                self.tabs.setTabIcon(i, source.tabIcon(i))
                self.tabs.setTabToolTip(i, source.tabToolTip(i))
                if _pinned_start(source, i):
                    self.tabs.setTabButton(i, QtWidgets.QTabBar.RightSide, None)  # the Start tab stays
                    continue
                button = self.tabs.tabButton(i, QtWidgets.QTabBar.RightSide)
                if not isinstance(button, TabButton):
                    button = TabButton(self.tabs)
                    button.clicked.connect(lambda _c=False, b=button: self.close_tab(b))
                    self.tabs.setTabButton(i, QtWidgets.QTabBar.RightSide, button)
                button.set_state(modified[i], i == source.currentIndex() or i == self.hovered)
            self.tabs.setCurrentIndex(source.currentIndex())
            self.tabs.blockSignals(False)

        def tab_clicked(self, index):
            source = self.mdi_tabs()
            if source is not None and 0 <= index < source.count():
                source.setCurrentIndex(index)

        def tab_moved(self, old, new):
            source = self.mdi_tabs()
            if source is not None:
                source.moveTab(old, new)
            self.snapshot = None

        def close_tab(self, button):
            source = self.mdi_tabs()
            for i in range(self.tabs.count()):
                if self.tabs.tabButton(i, QtWidgets.QTabBar.RightSide) is button and source is not None:
                    source.tabCloseRequested.emit(i)
                    break
            QtCore.QTimer.singleShot(0, self.sync)

        # --- search field
        def take_search(self):
            try:
                import bruceworx_search

                field = bruceworx_search.field()
            except Exception:
                field = None
            if field is not None and self.search_action is None:
                self.search_action = self.addWidget(field)
                self.no_safe_area()
                field.show()
                bruceworx_search.field_moved()

        def give_back_search(self):
            if self.search_action is None:
                return
            self.search_action = None
            try:
                import bruceworx_search

                bruceworx_search.take_field_back()
            except Exception:
                pass

        # --- window
        def set_fullscreen(self, fullscreen):
            self.inset.setFixedWidth(inset(fullscreen))

        def eventFilter(self, obj, event):
            kind = event.type()
            if obj is self.tabs:
                if kind in (QtCore.QEvent.MouseMove, QtCore.QEvent.HoverMove):
                    self.set_hovered(self.tabs.tabAt(self.tabs.mapFromGlobal(QtGui.QCursor.pos())))
                elif kind in (QtCore.QEvent.Leave, QtCore.QEvent.HoverLeave):
                    self.set_hovered(-1)
                return False
            if obj in (self, self.inset, self.space):
                if kind == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                    action = _system_double_click()
                    if action == "zoom":
                        _window_action(self.window(), b"performZoom:")
                    elif action == "minimize":
                        _window_action(self.window(), b"performMiniaturize:")
                    return True
                if kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                    handle = self.window().windowHandle()
                    if handle is not None:
                        handle.startSystemMove()
                    return True
            return False

        def set_hovered(self, index):
            if index != self.hovered:
                self.hovered = index
                self.sync()

    class WindowFilter(QtCore.QObject):
        def eventFilter(self, obj, event):
            kind = event.type()
            if kind == QtCore.QEvent.WindowStateChange and _alive(_row):
                _row.set_fullscreen(obj.isFullScreen())
            elif kind == QtCore.QEvent.ChildPolished and _toolbars_watched:
                # Polished just before it first shows, so before its layout runs.
                child = event.child()
                if isinstance(child, QtWidgets.QToolBar):
                    _no_safe_area(child, True)
            return False

    class DockFilter(QtCore.QObject):
        """Lays a dock out again when it shows or resizes.

        While the window changes to the full-size content view, Qt hands docks the old
        title strip as a safe area, and a dock laid out then (the floating Browser before
        the first document opens) kept its content pushed aside. _watch_docks turns the
        safe area off for docks; this filter lays them out again. Qt 6.8.3, macOS 27.
        """

        def eventFilter(self, obj, event):
            if event.type() in (QtCore.QEvent.Show, QtCore.QEvent.Resize):
                QtCore.QTimer.singleShot(0, lambda d=obj: _relayout_dock(d))
            return False

    _classes = (Row, WindowFilter, DockFilter)
    return _classes


_window_filter = None
_dock_filter = None


def _relayout_dock(dock):
    if _alive(dock) and dock.isVisible() and dock.layout() is not None:
        dock.layout().invalidate()
        dock.layout().activate()


_toolbars_watched = False


def _no_safe_area(widget, on):
    """Keep (on) or give back the title strip's safe area for a widget and its children."""
    from PySide import QtCore, QtWidgets

    for w in [widget] + widget.findChildren(QtWidgets.QWidget):
        w.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, not on)


def _watch_toolbars(mw, on):
    """Toolbars ignore the safe area while the row is on, those made later too (WindowFilter)."""
    global _toolbars_watched
    from PySide import QtWidgets

    _toolbars_watched = on
    for bar in mw.findChildren(QtWidgets.QToolBar):
        if bar.parent() is not mw:
            continue
        _no_safe_area(bar, on)
        if on and bar.isVisible() and bar.layout() is not None:
            bar.layout().invalidate()


def _watch_docks(mw, on):
    from PySide import QtCore, QtWidgets

    for dock in mw.findChildren(QtWidgets.QDockWidget):
        # The safe area of the old title strip reaches docks too while the window changes.
        for widget in [dock] + dock.findChildren(QtWidgets.QWidget):
            widget.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, not on)
        if on:
            dock.installEventFilter(_dock_filter)
            _relayout_dock(dock)
        else:
            dock.removeEventFilter(_dock_filter)


def _remember_unified():
    """Turn Qt's unified title bar off, keeping what was there to put it back."""
    import FreeCAD

    group = FreeCAD.ParamGet(MAINWINDOW_PATH)
    state = FreeCAD.ParamGet(STATE_PATH)
    if not state.GetBool("Saved", False):
        had = UNIFIED in group.GetBools()
        state.SetInt("Unified", int(group.GetBool(UNIFIED, True)) if had else -1)
        state.SetBool("Saved", True)
    group.SetBool(UNIFIED, False)


def _restore_unified():
    import FreeCAD

    group = FreeCAD.ParamGet(MAINWINDOW_PATH)
    state = FreeCAD.ParamGet(STATE_PATH)
    if not state.GetBool("Saved", False):
        return
    was = state.GetInt("Unified", -1)
    if was < 0:
        group.RemBool(UNIFIED)
    else:
        group.SetBool(UNIFIED, bool(was))
    state.RemBool("Saved")
    state.RemInt("Unified")


def row():
    """The live title row (for tests and scripts)."""
    return _row if _alive(_row) else None


def _turn_on():
    global _row, _window_filter, _dock_filter
    import FreeCADGui
    from PySide import QtCore

    mw = FreeCADGui.getMainWindow()
    _remember_unified()
    mw.setUnifiedTitleAndToolBarOnMac(False)
    mw.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)
    set_native_title_bar(mw, True)
    if _row is None or not _alive(_row):
        row_class, filter_class, dock_filter_class = _make_classes()
        _row = row_class(mw)
        mw.addToolBar(QtCore.Qt.TopToolBarArea, _row)
        _window_filter = filter_class()
        mw.installEventFilter(_window_filter)
        _dock_filter = dock_filter_class()
    _watch_docks(mw, True)
    _watch_toolbars(mw, True)
    _row.set_fullscreen(mw.isFullScreen())
    _row.show()
    _row.take_search()
    _row.hide_mdi_tabs(not tabs_at_bottom())
    _row.snapshot = None
    _row.sync()
    _row.timer.start()
    _relayout()


def _turn_off():
    import FreeCADGui
    from PySide import QtCore

    mw = FreeCADGui.getMainWindow()
    if _dock_filter is not None:
        _watch_docks(mw, False)
    _watch_toolbars(mw, False)
    if _alive(_row):
        _row.timer.stop()
        _row.give_back_search()
        _row.hide_mdi_tabs(False)
        _row.hide()
    set_native_title_bar(mw, False)
    mw.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, True)
    _restore_unified()
    import FreeCAD

    mw.setUnifiedTitleAndToolBarOnMac(FreeCAD.ParamGet(MAINWINDOW_PATH).GetBool(UNIFIED, True))
    _relayout()


def _relayout():
    try:
        import bruceworx_toolbars

        bruceworx_toolbars.relayout()
    except Exception:
        pass


class _PrefObserver:
    def onChange(self, _group, name):
        if name != PREF_NAME:
            return
        if enabled():
            _turn_on()
        else:
            _turn_off()


def install():
    """Put the title row in once the main window is shown (macOS only)."""
    global _observer
    if _observer is not None or not available():
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore

    _observer = _PrefObserver()
    group = FreeCAD.ParamGet(PREFS_PATH)
    # Keep the handle: a collected wrapper detaches its observer.
    group.Attach(_observer)
    _groups.append(group)

    def start(tries=50):
        mw = FreeCADGui.getMainWindow()
        if not mw.isVisible() and tries:
            QtCore.QTimer.singleShot(200, lambda: start(tries - 1))
            return
        if enabled():
            _turn_on()

    QtCore.QTimer.singleShot(0, start)
