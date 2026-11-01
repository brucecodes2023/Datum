# SPDX-License-Identifier: LGPL-2.1-or-later
"""The right panel's tabs (roadmap UIX-12 redesign, plan "Redesign (24 Oct)").

FreeCAD's right overlay panel (OverlayRight) is a tab widget over dock
windows; it held only the task view. This module adds four docks to it, in
this order: Properties, Vault, Parameters, Physical. The task view stays in the
same panel as the last tab: FreeCAD brings it forward when a command opens and
folds it when the command closes, so a command is a tab of its own.

* Each tab is a dock registered through FreeCAD's dock window manager
  (FreeCADGui.addDockWindow, [BW] in Gui/ApplicationPy.cpp), so the overlay
  restores and takes it like a stock dock. Its content comes from the module
  that owns it: ``register(key, factory)``; until then a placeholder shows.
* A new selection brings Properties forward, unless a command is open (you
  are editing a feature: its tab stays in front).
* Every tab follows the selection: the visible one is told at once
  (``selection_changed()`` on its content), the others when shown.
* The tabs are a thin strip of our own over the panel's title bar
  (TabStrip): FreeCAD's overlay draws its tab bar down the panel's outer
  edge (and hides it by default). The command's tab shows only while a
  command is open, named after it (bruceworx_taskcard names the dock).
* Cmd-I hides and shows the panel (BruceWorx_RightPanel): hidden is FreeCAD's
  auto hide, so it still slides in at the window's right edge and while a
  command is open ([BW] FreeCADGui.setOverlayAutoHide).

Preferences/BruceWorx/RightPanelTabs (default on).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "RightPanelTabs"
OVERLAY_PATH = "User parameter:BaseApp/Preferences/DockWindows/Overlay"
RIGHT = "OverlayRight"
TASK_DOCK = "Tasks"
COMMAND = "BruceWorx_RightPanel"
SHORTCUT = "Ctrl+I"  # Cmd-I on macOS

# (dock object name, tab title), in tab order.
TABS = (
    ("BWProperties", "Properties"),
    ("BWVault", "Vault"),
    ("BWParameters", "Parameters"),
    ("BWPhysical", "Physical"),
)
PROPERTIES = TABS[0][0]


# ----- pure helpers (unit tested) ---------------------------------------------------------


def parse_widgets(text):
    return [n for n in (text or "").split(",") if n]


def with_tabs(names, on=True, tasks=True):
    """The overlay's widget list with our tabs before the task view (or without them).

    ``tasks``: the task view floats in this panel (Preferences/BruceWorx/FloatingTaskPanel).
    """
    ours = [n for n, _t in TABS]
    rest = [n for n in names if n not in ours]
    if tasks and TASK_DOCK not in rest:
        rest.append(TASK_DOCK)
    if not on:
        return rest
    if TASK_DOCK in rest:
        i = rest.index(TASK_DOCK)
        return rest[:i] + ours + rest[i:]
    return rest + ours


def bring_properties(dialog_open, current):
    """True when a new selection should bring Properties forward."""
    return not dialog_open and current != PROPERTIES


# ----- FreeCAD side ------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def enabled():
    return bool(_prefs().GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


_hosts = {}  # dock name -> TabHost
BADGES = {}  # dock name -> callable() -> short text after the tab's title ("" for none), e.g. "in"
_factories = {}  # dock name -> factory(parent) -> QWidget
_docks = {}  # dock name -> QDockWidget


def _host_class():
    from PySide import QtCore, QtWidgets

    class TabHost(QtWidgets.QWidget):
        """A tab's dock content: the owner's widget, or a placeholder until it registers."""

        def __init__(self, key, title):
            super().__init__()
            self.key = key
            self.setObjectName(key + "Host")
            self.setWindowTitle(title)
            self.content = None
            self.stale = True
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            self.placeholder = QtWidgets.QLabel(f"{title}\n\nComing in this build.")
            self.placeholder.setObjectName("bwRightPlaceholder")
            self.placeholder.setAlignment(QtCore.Qt.AlignCenter)
            lay.addWidget(self.placeholder)

        def set_content(self, widget):
            lay = self.layout()
            if self.content is not None:
                lay.removeWidget(self.content)
                self.content.deleteLater()
            self.placeholder.hide()
            self.content = widget
            lay.addWidget(widget)
            for w in [widget] + widget.findChildren(QtWidgets.QWidget):
                w.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)
            self.stale = True
            if self.isVisible():
                self.notify()

        def notify(self):
            self.stale = False
            hook = getattr(self.content, "selection_changed", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    import traceback

                    import FreeCAD

                    FreeCAD.Console.PrintError(f"BruceWorx {self.key}:\n" + traceback.format_exc())

        def showEvent(self, event):
            super().showEvent(event)
            if self.stale:
                QtCore.QTimer.singleShot(0, self.notify)

    return TabHost


def register(key, factory):
    """Fill the tab ``key`` (a dock name of TABS) with ``factory(parent)``'s widget.

    The widget may define ``selection_changed()``; it is called when the
    selection changes while the tab is visible, and when the tab is shown.
    """
    _factories[key] = factory
    host = _hosts.get(key)
    if host is not None and _alive(host):
        host.set_content(factory(host))
        return host.content
    return None


def content(key):
    host = _hosts.get(key)
    return host.content if host is not None and _alive(host) else None


def dock(key):
    return _docks.get(key)


def _overlay():
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    for w in mw.findChildren(QtWidgets.QTabWidget):
        if w.metaObject().className() == "Gui::OverlayTabWidget" and w.objectName() == RIGHT:
            return w
    return None


def current_tab():
    """The dock name of the right panel's current tab, or ""."""
    tabs = _overlay()
    if tabs is None:
        return ""
    page = tabs.currentWidget()
    for name, d in _docks.items():
        if page is not None and _alive(d) and (page is d or d.isAncestorOf(page) or page.isAncestorOf(d)):
            return name
    try:
        title = tabs.tabText(tabs.currentIndex())
    except Exception:
        return ""
    return next((n for n, t in TABS if t == title), title)


def show_index(index):
    """Bring the overlay's tab ``index`` forward, as a click on FreeCAD's own tab bar does."""
    tabs = _overlay()
    if tabs is None or not 0 <= index < tabs.count():
        return False
    if tabs.currentIndex() != index:
        _release_dock(tabs, index)  # a folded dock has no height until it is the current page
        tabs.setCurrentIndex(index)
        tabs.tabBar().tabBarClicked.emit(index)  # OverlayTabWidget::onCurrentChanged sizes the splitter
    return True


def _release_dock(tabs, index):
    """Let one dock fill the panel again. Folded docks are held at zero height."""
    from PySide import QtWidgets

    for child in tabs.children():
        if isinstance(child, QtWidgets.QSplitter) and 0 <= index < child.count():
            page = child.widget(index)
            if page is not None:
                page.setMaximumHeight(16777215)
                page.setMinimumSize(0, 0)
            return


def show_tab(key):
    """Bring the tab ``key`` forward."""
    tabs = _overlay()
    title = dict(TABS).get(key, key)
    if tabs is None:
        return False
    for i in range(tabs.count()):
        if tabs.tabText(i) == title:
            return show_index(i)
    return False


def pack_tabs(widths, available, current, spacing=2, more=48):
    """Indexes to show, and whether the rest go under a More button.

    Labels stay whole. The current tab is always one of the ones shown. What does not
    fit is overflow, so six tabs in a narrow panel do not have to be clipped."""
    count = len(widths)
    if count == 0:
        return [], False

    def total(indexes, with_more):
        if not indexes:
            return more if with_more else 0
        size = sum(widths[i] for i in indexes) + spacing * (len(indexes) - 1)
        if with_more:
            size += spacing + more
        return size

    everything = list(range(count))
    if total(everything, False) <= available:
        return everything, False
    shown = []
    for index in everything:
        if total(shown + [index], True) <= available:
            shown.append(index)
    if current not in shown and 0 <= current < count:
        while shown and total(shown + [current], True) > available:
            shown.pop()
        if total(shown + [current], True) <= available:
            shown.append(current)
        elif not shown:
            shown = [current]
    overflow = [index for index in everything if index not in shown]
    return shown, bool(overflow)


def command_word(title):
    """The command tab's text: "Pad Parameters" -> "Pad", "Fillet parameters" -> "Fillet"."""
    text = (title or "").strip()
    for suffix in (" Parameters", " parameters", " Parameter", " parameter"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def strip_items(titles, current, command_open, command_title, badges=None):
    """[(index, text, kind, is_current)] for the strip: our tabs, any other dock in the panel under its
    own name, and the Tasks dock as the command's tab while a command is open.

    ``command_title`` is the Tasks dock's title (bruceworx_taskcard names it after the open command,
    "Select attachment"): only that tab takes the command word. Another dock in the panel (the user's
    Report view, say) keeps its title; it used to be labelled with the command word too, so two
    "Sel...ent" tabs showed (26 Oct).

    ``badges``: tab title -> short text shown after it ("Vault · in" when the active document's
    units differ from the vault's, PDM-09)."""
    ours = [t for _n, t in TABS]
    out = []
    for i, title in enumerate(titles):
        if title in ours:
            badge = (badges or {}).get(title, "")
            out.append((i, f"{title} · {badge}" if badge else title, "tab", i == current))
        elif title in (TASK_DOCK, command_title):
            if command_open:
                out.append((i, command_word(command_title) or title, "command", i == current))
        else:
            out.append((i, title, "tab", i == current))
    return out


def _strip_class():
    from PySide import QtCore, QtWidgets

    class TabStrip(QtWidgets.QWidget):
        """Thin text tabs with an accent underline, over the right panel's title bar."""

        def __init__(self, tabs):
            super().__init__(tabs)
            self.setObjectName("bwRightTabs")
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)
            self.tabs = tabs
            self.row = QtWidgets.QHBoxLayout(self)
            self.row.setContentsMargins(8, 0, 6, 0)
            self.row.setSpacing(2)
            font = self.font()
            font.setPixelSize(12)
            self.setFont(font)
            self.buttons = []
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(0)
            self.timer.timeout.connect(self.sync)
            tabs.currentChanged.connect(lambda _i: self.timer.start())
            tabs.installEventFilter(self)
            self.poll = QtCore.QTimer(self)  # the command's title and open/closed state have no signal
            self.poll.setInterval(400)
            self.poll.timeout.connect(self.sync)
            self.poll.start()
            self.sync()

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Show and obj.metaObject().className() == "Gui::OverlayTitleBar":
                QtCore.QTimer.singleShot(0, obj.hide)  # the strip replaces it (see _panel_title)
            elif event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.Show,
                                  QtCore.QEvent.LayoutRequest):
                self.timer.start()
            return False

        def _panel_title(self):
            """FreeCAD's title row of the panel, under the strip. The overlay paints its children
            translucent, so it would show through: it is kept hidden while the strip is up."""
            for child in self.tabs.children():
                if isinstance(child, QtWidgets.QWidget) and child.metaObject().className() == "Gui::OverlayTitleBar":
                    if not child.property("bwWatched"):
                        child.setProperty("bwWatched", True)
                        child.installEventFilter(self)
                    if child.isVisible():
                        child.hide()

        def _splitter(self):
            for child in self.tabs.children():
                if isinstance(child, QtWidgets.QSplitter):
                    return child
            return None

        def _no_dock_titles(self):
            """Our docks show no title row of their own: the strip names them."""
            for name, d in _docks.items():
                if not _alive(d):
                    continue
                bar = d.titleBarWidget()
                if bar is None or bar.objectName() != "bwNoTitle":
                    empty = QtWidgets.QWidget(d)
                    empty.setObjectName("bwNoTitle")
                    empty.setFixedHeight(0)
                    d.setTitleBarWidget(empty)

        def sync(self):
            if not _alive(self.tabs):
                return
            splitter = self._splitter()
            if splitter is None or not self.tabs.isVisible():
                self.hide()
                return
            self._no_dock_titles()
            self._panel_title()
            # The panel's own title row: FreeCAD keeps it above the splitter, shown or not.
            top = max(splitter.geometry().top(), 30)  # the header reserved in OverlayTabWidget
            if self.geometry() != QtCore.QRect(0, 0, self.tabs.width(), top):
                self.setGeometry(0, 0, self.tabs.width(), top)
            if not self.isVisible():
                self.show()
            self.raise_()
            titles = [self.tabs.tabText(i) for i in range(self.tabs.count())]
            # The command's name: bruceworx_taskcard titles the Tasks dock after the open dialog.
            task_dock = self.tabs.window().findChild(QtWidgets.QDockWidget, TASK_DOCK)
            command_title = task_dock.windowTitle() if task_dock is not None else ""
            items = strip_items(titles, self.tabs.currentIndex(), _dialog_open(), command_title, _badges())
            visible, overflow = self._pack(items)
            signature = ([(i, t, k) for i, t, k, _c in visible], [i for i, _t, _k, _c in overflow])
            current_sig = ([(b.index, b.property("fullText") or b.text(), b.property("kind"))
                            for b in self.buttons if b.property("kind") != "more"],
                           list(getattr(self, "_overflow", [])))
            if signature != current_sig:
                self.rebuild(items, visible, overflow)
            else:
                for b, (_i, _t, _k, current) in zip(
                        [b for b in self.buttons if b.property("kind") != "more"], visible):
                    if bool(b.property("current")) != current:
                        b.setProperty("current", current)
                        b.style().unpolish(b)
                        b.style().polish(b)
            self._fold_hidden_docks(splitter)

        def _fold_hidden_docks(self, splitter):
            """Collapsed pages stay inside the panel.

            A dock with a minimum size is given a zero slot and then laid out at its minimum,
            which puts it just to the left of the panel. Its last letters show there and the
            page itself is missing. Hold every page but the current one at zero height.
            """
            current = self.tabs.currentIndex()
            width = splitter.width()
            for i in range(splitter.count()):
                page = splitter.widget(i)
                if page is None:
                    continue
                page.setMinimumSize(0, 0)
                if i == current:
                    if page.maximumHeight() < splitter.height():
                        page.setMaximumHeight(16777215)
                    continue
                page.setMaximumHeight(0)
                geom = page.geometry()
                if geom.x() < 0 or geom.y() < 0 or geom.height() > 0:
                    page.setGeometry(0, 0, width, 0)

        def _pack(self, items):
            margins = self.row.contentsMargins()
            available = self.width() - margins.left() - margins.right()
            metrics = self.fontMetrics()
            more = metrics.horizontalAdvance("More") + 22
            preferred = [metrics.horizontalAdvance(text) + 16 for _i, text, _k, _c in items]
            current = next((n for n, item in enumerate(items) if item[3]), 0)
            indexes, _has_more = pack_tabs(preferred, max(40, available), current, self.row.spacing(), more)
            return [items[i] for i in indexes], [items[i] for i in range(len(items)) if i not in indexes]

        def _button(self, index, text, kind, current):
            b = QtWidgets.QToolButton(self)
            b.setObjectName("bwRightTab")
            b.setProperty("fullText", text)
            b.setText(text)
            b.setToolTip("")
            b.setAutoRaise(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(26)
            width = self.fontMetrics().horizontalAdvance(text) + 16
            b.setFixedWidth(width)
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            b.setProperty("kind", kind)
            b.setProperty("current", current)
            b.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)
            b.index = index
            if kind != "more":
                b.clicked.connect(lambda _c=False, i=index: show_index(i))
            self.row.addWidget(b)
            self.buttons.append(b)
            return b

        def rebuild(self, items, visible=None, overflow=None):
            for b in self.buttons:
                self.row.removeWidget(b)
                b.hide()  # at once: deleteLater would leave it painted until the next event loop turn
                b.deleteLater()
            while self.row.count():
                self.row.takeAt(0)
            self.buttons = []
            if visible is None:
                visible, overflow = self._pack(items)
            overflow = overflow or []
            self._overflow = [i for i, _t, _k, _c in overflow]
            for index, text, kind, current in visible:
                if kind == "command":
                    self.row.addStretch(1)
                self._button(index, text, kind, current)
            if overflow:
                if not any(k == "command" for _i, _t, k, _c in visible):
                    self.row.addStretch(1)
                more = self._button(-1, "More", "more", False)
                menu = QtWidgets.QMenu(more)
                for index, text, _kind, _current in overflow:
                    menu.addAction(text).triggered.connect(lambda _c=False, i=index: show_index(i))
                more.setMenu(menu)
                more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            elif not any(k == "command" for _i, _t, k, _c in visible):
                self.row.addStretch(1)

    return TabStrip


_strip = None


def _badges():
    """tab title -> badge text, from BADGES (a failing badge is blank)."""
    out = {}
    titles = dict(TABS)
    for key, fn in list(BADGES.items()):
        try:
            text = str(fn() or "")
        except Exception:
            text = ""
        if text and key in titles:
            out[titles[key]] = text
    return out


def _install_strip():
    global _strip
    from PySide import QtCore, QtWidgets

    tabs = _overlay()
    if tabs is None:
        QtCore.QTimer.singleShot(500, _install_strip)
        return
    if _strip is None or not _alive(_strip):
        tabs.setProperty("bwTabStrip", True)  # [BW] OverlayWidgets.cpp: folded docks keep no header row
        for splitter in tabs.findChildren(QtWidgets.QSplitter):
            splitter.refresh()
        _strip = _strip_class()(tabs)


def _dialog_open():
    try:
        import FreeCADGui

        return bool(FreeCADGui.Control.activeDialog())
    except Exception:
        return False


def _on_selection():
    if not enabled():
        return
    if bring_properties(_dialog_open(), current_tab()):
        show_tab(PROPERTIES)
    for host in _hosts.values():
        if not _alive(host):
            continue
        if host.isVisible():
            host.notify()
        else:
            host.stale = True


_timer = None


def _schedule(*_args):
    if _timer is not None:
        _timer.start()


class _SelectionObserver:
    addSelection = removeSelection = setSelection = clearSelection = staticmethod(_schedule)


def set_hidden(hidden):
    """Hide the panel (FreeCAD's auto hide: it slides away, shows at the window's right edge and while a
    command is open) or show it again. Returns whether it was hidden. FreeCAD keeps the mode."""
    import FreeCADGui

    return bool(FreeCADGui.setOverlayAutoHide(RIGHT, bool(hidden)))


def toggle():
    import FreeCADGui

    if FreeCADGui.setOverlayAutoHide(RIGHT, True):  # it was hidden: show it
        FreeCADGui.setOverlayAutoHide(RIGHT, False)


class _ToggleCommand:
    def GetResources(self):
        return {
            "MenuText": "Right panel",
            "ToolTip": "Hide or show the right panel's Properties, Vault, Parameters and Physical tabs",
            "Accel": SHORTCUT,
        }

    def Activated(self):
        toggle()

    def IsActive(self):
        return enabled()


def order_tabs():
    """The task view's tab last, after ours (a command is the tab on the right)."""
    tabs = _overlay()
    if tabs is None:
        return
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    ours = [t for _n, t in TABS]
    task = [i for i, t in enumerate(titles) if t not in ours]
    last_ours = max((i for i, t in enumerate(titles) if t in ours), default=-1)
    for i in reversed(task):
        if i < last_ours:
            tabs.tabBar().moveTab(i, tabs.count() - 1)


def _write_overlay_list(on):
    import FreeCAD

    right = FreeCAD.ParamGet(OVERLAY_PATH).GetGroup(RIGHT)
    names = parse_widgets(right.GetString("Widgets", ""))
    wanted = with_tabs(names, on, bool(_prefs().GetBool("FloatingTaskPanel", True)))
    if wanted != names:
        right.SetString("Widgets", ",".join(wanted))


def _create_docks():
    import FreeCADGui
    from PySide import QtCore

    TabHost = _host_class()
    for name, title in TABS:
        if name in _docks and _alive(_docks[name]):
            continue
        host = TabHost(name, title)
        _hosts[name] = host
        area = QtCore.Qt.RightDockWidgetArea
        # The overlay restores before modules load, so the dock goes into the panel here.
        _docks[name] = FreeCADGui.addDockWindow(name, host, int(getattr(area, "value", area)), RIGHT)
        factory = _factories.get(name)
        if factory is not None:
            host.set_content(factory(host))


_installed = False
_observer = None


def _shutdown():
    global _timer
    import FreeCADGui

    if _timer is not None:
        _timer.stop()
        _timer = None
    if _observer is not None:
        try:
            FreeCADGui.Selection.removeObserver(_observer)
        except Exception:
            pass


def install():
    """The docks now (before FreeCAD restores the overlay panels), so the panel takes them at start."""
    global _installed, _timer, _observer
    if _installed or not enabled():
        return
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    if not hasattr(FreeCADGui, "addDockWindow") or not hasattr(FreeCADGui, "setOverlayAutoHide"):
        return  # an older build without the [BW] binding
    _write_overlay_list(True)
    _create_docks()
    QtCore.QTimer.singleShot(0, order_tabs)
    QtCore.QTimer.singleShot(0, _install_strip)
    _timer = QtCore.QTimer()
    _timer.setSingleShot(True)
    _timer.setInterval(0)
    _timer.timeout.connect(_on_selection)
    _observer = _SelectionObserver()
    FreeCADGui.Selection.addObserver(_observer)
    FreeCADGui.addCommand(COMMAND, _ToggleCommand())
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _installed = True
