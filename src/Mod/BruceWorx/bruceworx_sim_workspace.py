# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Simulation workspace's window shell (SIM-07 phase P1; plan chapter 69).

While the Simulation workspace is active the left sidebar (FreeCAD's overlay panel with the model tree, "Browser") has a
second tab, "Simulation": the Model Builder on top and the Settings pane below it, with a splitter (as Ansys' Outline and
Details). The tabs are FreeCAD's own overlay tabs (vertical, on the panel's left edge); the fork hides that tab bar in
every other workspace, so it is switched on here and back off on leaving, and the Simulation tab is hidden again (the dock
stays registered in the panel: an overlay panel cannot give a dock back). A bottom dock (Messages, Progress, Log, Table)
sits under the 3D view. The right panel and its floating task view are put away. The panes get one stylesheet from the
mockup's tokens, and the 3D view a light-blue gradient and the axis triad. Leaving the workspace restores everything.

``model_builder_host()`` and ``settings_host()`` return the two panes' content widgets (vertical layouts; a placeholder
label sits in them until ``clear_host(host)``); ``messages_pane()`` is the bottom pane: ``add_message(level, text,
node_key=None)``, ``stage_rows()``, ``log_text()``. The pane listens to ``bruceworx_sim_events``.

Also here: the active study's type (``active_kind``, polled while the workspace is up and handed to the workspace
bar, which shows the tabs and groups of that type), and a few small commands (the placeholders of the Definitions,
Geometry and Project tabs, and the airflow ones).
"""

from __future__ import annotations

import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBENCH = "FemWorkbench"
SIDEBAR, BOTTOM = "BWSimSidebar", "BWSimBottom"
LEFT = "OverlayLeft"
SIDEBAR_TITLE = "Simulation"
SIDEBAR_WIDTH = 400  # px: the left panel while the Simulation tab is up
SPLIT = (430, 520)  # px: Model Builder over Settings
VIEW_PATH = "User parameter:BaseApp/Preferences/View"
VIEW_TOP, VIEW_BOTTOM = "#c6d4e6", "#eef2f7"  # the mockup's viewport gradient
ACCENT = "#1f6fd1"
TREE_ROW, FIELD_HEIGHT = 21, 22

TOKENS = {
    "bg": "#e9ebee", "panel": "#f7f8fa", "panel2": "#ffffff", "line": "#c9ced6", "line2": "#dde1e7",
    "text": "#1d232c", "dim": "#5b6573", "faint": "#8a93a0", "accent": ACCENT, "accent_soft": "#dbe8fa",
    "sel": "#cfe1f9", "ok": "#2e9b48", "warn": "#e0a100", "bad": "#d23a2f", "title": "#2b3440",
}


# ----- pure helpers ----------------------------------------------------------------------------------------


def stylesheet(t=None):
    """The one stylesheet of the Simulation panes (applied to the three docks only)."""
    t = dict(TOKENS, **(t or {}))
    return f"""
QDockWidget#{BOTTOM} {{ background: {t['panel']}; }}
QSplitter#BWSimSidebarSplit {{ background: {t['panel']}; }}
QSplitter#BWSimSidebarSplit::handle {{ background: {t['line']}; height: 1px; }}
QWidget#BWSimPane {{ background: {t['panel']}; font-size: 12px; color: {t['text']}; }}
QWidget#BWSimHost {{ background: {t['panel2']}; }}
QWidget#BWSimHeader {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fbfcfd, stop:1 #eef0f3);
  border-bottom: 1px solid {t['line']}; min-height: 26px; max-height: 26px; }}
QLabel#BWSimHeaderTitle {{ font-weight: 600; color: {t['title']}; }}
QToolButton#BWSimHeaderButton {{ border: 1px solid transparent; border-radius: 2px; padding: 1px 6px; color: {t['dim']}; }}
QToolButton#BWSimHeaderButton:hover {{ border-color: {t['line']}; background: #fff; }}
QToolButton#BWSimHeaderButton:checked {{ background: {t['accent_soft']}; border-color: {t['accent']}; color: {t['text']}; }}
QToolButton#BWSimSectionHeader {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f9fafb, stop:1 #eceff3);
  border: none; border-top: 1px solid {t['line2']}; border-bottom: 1px solid {t['line2']}; padding: 4px 10px;
  font-weight: 600; text-align: left; }}
QLabel#BWSimPlaceholder {{ color: {t['faint']}; padding: 10px; }}
QLabel#BWSimStudyName {{ font-size: 14px; font-weight: 600; padding: 8px 10px 0 10px; }}
QWidget#BWSimPane QLineEdit, QWidget#BWSimPane QComboBox, QWidget#BWSimPane QAbstractSpinBox {{
  min-height: {FIELD_HEIGHT}px; max-height: {FIELD_HEIGHT}px; border: 1px solid {t['line']}; border-radius: 2px;
  background: #fff; padding: 0 6px; }}
QWidget#BWSimPane QPushButton {{ border: 1px solid {t['line']}; border-radius: 3px; padding: 3px 9px; font-size: 11px;
  background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fff, stop:1 #f0f2f5); }}
QWidget#BWSimPane QPushButton[primary="true"] {{ background: {t['accent']}; border-color: #1858a8; color: #fff; font-weight: 600; }}
QWidget#BWSimPane QTreeView, QWidget#BWSimPane QTreeWidget {{ background: {t['panel']}; border: none; outline: 0; }}
QWidget#BWSimPane QTreeView::item {{ height: {TREE_ROW}px; }}
QWidget#BWSimPane QTreeView::item:selected {{ background: {t['sel']}; color: {t['text']}; border-left: 2px solid {t['accent']}; }}
QTabWidget#BWSimBottomTabs::pane {{ border: none; border-top: 1px solid {t['line']}; background: #fff; }}
QTabWidget#BWSimBottomTabs QTabBar::tab {{ padding: 5px 14px; color: {t['dim']}; border: none;
  background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fbfcfd, stop:1 #eef0f3); border-right: 1px solid {t['line2']}; }}
QTabWidget#BWSimBottomTabs QTabBar::tab:selected {{ color: {t['text']}; font-weight: 600; background: #fff;
  border-bottom: 2px solid {t['accent']}; }}
QListWidget#BWSimMessages, QTableWidget#BWSimProgress, QTableWidget#BWSimTable {{ background: #fff; border: none; outline: 0; }}
QListWidget#BWSimMessages::item {{ height: {TREE_ROW}px; }}
QTableWidget#BWSimProgress {{ gridline-color: {t['line2']}; }}
QPlainTextEdit#BWSimLog {{ background: #fff; border: none; font-family: Menlo, "SF Mono", monospace; font-size: 11px; }}
QProgressBar {{ border: 1px solid {t['line']}; border-radius: 2px; background: #eef0f3; max-height: 12px; text-align: center; }}
QProgressBar::chunk {{ background: {t['accent']}; }}
QProgressBar[state="ok"]::chunk {{ background: {t['ok']}; }}
QProgressBar[state="bad"]::chunk {{ background: {t['bad']}; }}
"""


def format_seconds(seconds):
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else (f"{seconds:.1f} s" if seconds < 100 else f"{seconds / 60:.1f} min")


# ----- the study's type ------------------------------------------------------------------------------------


def active_study():
    try:
        import bruceworx_sim_gui as g

        return g.current_study()
    except Exception:
        return None


def active_kind():
    """The active study's type ("static", "prop_wash", ...), None without a study."""
    study = active_study()
    if study is None:
        return None
    try:
        import bruceworx_sim as sim

        return sim.study_kind(study)
    except Exception:
        return None


def study_label():
    study = active_study()
    return getattr(study, "Label", "") if study is not None else ""


# ----- the widgets -------------------------------------------------------------------------------------------

_s = {"built": False, "active": False, "docks": {}, "panes": {}, "saved": {}, "timer": None, "kind": "?", "bridge": None, "listener": None, "views": {}}


def _level_icon(level):
    from PySide import QtCore, QtGui

    key = ("icon", level)
    if key in _s:
        return _s[key]
    color = {"ok": TOKENS["ok"], "warning": TOKENS["warn"], "error": TOKENS["bad"], "info": TOKENS["accent"]}[level]
    pix = QtGui.QPixmap(14, 14)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(color))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(1, 1, 12, 12)
    p.setPen(QtGui.QPen(QtGui.QColor("white"), 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    if level == "ok":
        p.drawPolyline([QtCore.QPointF(4, 7.5), QtCore.QPointF(6.3, 9.8), QtCore.QPointF(10, 4.8)])
    elif level == "warning":
        p.drawLine(7, 3.8, 7, 8)
        p.drawPoint(7, 10)
    elif level == "error":
        p.drawLine(4.5, 4.5, 9.5, 9.5)
        p.drawLine(9.5, 4.5, 4.5, 9.5)
    else:
        p.drawLine(7, 6, 7, 10)
        p.drawPoint(7, 4)
    p.end()
    _s[key] = QtGui.QIcon(pix)
    return _s[key]


def _classes():
    if _s.get("classes"):
        return _s["classes"]
    from PySide import QtCore, QtGui, QtWidgets

    class Header(QtWidgets.QWidget):
        def __init__(self, title):
            super().__init__()
            self.setObjectName("BWSimHeader")
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            row = QtWidgets.QHBoxLayout(self)
            row.setContentsMargins(8, 0, 6, 0)
            row.setSpacing(4)
            self.title = QtWidgets.QLabel(title)
            self.title.setObjectName("BWSimHeaderTitle")
            row.addWidget(self.title)
            row.addStretch(1)

        def add_button(self, text, tip, checkable=False):
            b = QtWidgets.QToolButton()
            b.setObjectName("BWSimHeaderButton")
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            self.layout().addWidget(b)
            return b

    class Pane(QtWidgets.QWidget):
        """A dock's content: header, then a host widget with a vertical layout."""

        def __init__(self, title, placeholder):
            super().__init__()
            self.setObjectName("BWSimPane")
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            col = QtWidgets.QVBoxLayout(self)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            self.header = Header(title)
            col.addWidget(self.header)
            self.host = QtWidgets.QWidget()
            self.host.setObjectName("BWSimHost")
            self.host.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            lay = QtWidgets.QVBoxLayout(self.host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            self.name_label = QtWidgets.QLabel("")
            self.name_label.setObjectName("BWSimStudyName")
            self.note_label = QtWidgets.QLabel(placeholder)
            self.note_label.setObjectName("BWSimPlaceholder")
            self.note_label.setWordWrap(True)
            for w in (self.name_label, self.note_label):
                w.setProperty("bwPlaceholder", True)
                lay.addWidget(w)
            lay.addStretch(1)
            col.addWidget(self.host, 1)

        def set_study(self, label):
            try:
                self.name_label.setText(label or "No study yet")
            except RuntimeError:  # the placeholders were removed (clear_host) by the Model Builder
                pass

    class Section(QtWidgets.QWidget):
        """A collapsible form section with a gradient header, for the Settings pane."""

        def __init__(self, title, body=None):
            super().__init__()
            col = QtWidgets.QVBoxLayout(self)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            self.button = QtWidgets.QToolButton()
            self.button.setObjectName("BWSimSectionHeader")
            self.button.setText(title)
            self.button.setCheckable(True)
            self.button.setChecked(True)
            self.button.setArrowType(QtCore.Qt.DownArrow)
            self.button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.button.toggled.connect(self._toggled)
            col.addWidget(self.button)
            self.body = body or QtWidgets.QWidget()
            col.addWidget(self.body)

        def _toggled(self, on):
            self.button.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
            self.body.setVisible(on)

    class Bottom(QtWidgets.QTabWidget):
        """Messages, Progress, Log and Table; fed by bruceworx_sim_events."""

        def __init__(self):
            super().__init__()
            self.setObjectName("BWSimBottomTabs")
            self.setDocumentMode(True)
            self.messages = QtWidgets.QListWidget()
            self.messages.setObjectName("BWSimMessages")
            self.progress = QtWidgets.QTableWidget(0, 3)
            self.progress.setObjectName("BWSimProgress")
            self.progress.setHorizontalHeaderLabels(["Stage", "Progress", "Time"])
            self.progress.verticalHeader().hide()
            self.progress.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.progress.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.progress.setShowGrid(False)
            hh = self.progress.horizontalHeader()
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            self.log = QtWidgets.QPlainTextEdit()
            self.log.setObjectName("BWSimLog")
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(5000)
            self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.table = QtWidgets.QTableWidget(0, 0)
            self.table.setObjectName("BWSimTable")
            for widget, name in ((self.messages, "Messages"), (self.progress, "Progress"), (self.log, "Log"), (self.table, "Table")):
                self.addTab(widget, name)
            self.rows = {}  # stage name -> (row, bar, time item, started)
            self.pending_log = []
            self.residual_text = ""
            self.tick = QtCore.QTimer(self)
            self.tick.setInterval(250)
            self.tick.timeout.connect(self.refresh)
            self.tick.start()

        # --- events
        def on_event(self, kind, p):
            handler = getattr(self, "_on_" + kind, None)
            if handler:
                handler(p)

        def _on_run_started(self, p):
            self.progress.setRowCount(0)
            self.rows = {}
            self.pending_log = []
            self.log.clear()
            self.add_message("info", "Run started" + (f": {p['label']}" if p.get("label") else ""), when=p.get("time"))
            self.setCurrentWidget(self.progress)

        def _on_message(self, p):
            self.add_message(p["level"], p["text"], p.get("node_key"), p.get("time"))
            if p["level"] == "error":
                self.setCurrentWidget(self.messages)

        def _on_log(self, p):
            self.pending_log.append(p["line"])

        def _on_stage_started(self, p):
            name = p["name"]
            row = self.progress.rowCount()
            self.progress.insertRow(row)
            self.progress.setRowHeight(row, TREE_ROW + 2)
            self.progress.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 0)  # busy until a fraction arrives
            bar.setTextVisible(False)
            self.progress.setCellWidget(row, 1, bar)
            item = QtWidgets.QTableWidgetItem("running")
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.progress.setItem(row, 2, item)
            self.rows[name] = {"row": row, "bar": bar, "time": item, "t0": p["time"], "done": False}

        def _row(self, name):
            if name not in self.rows:
                self._on_stage_started({"name": name, "time": time.time()})
            return self.rows[name]

        def _on_progress(self, p):
            r = self._row(p["name"])
            r["bar"].setRange(0, 1000)
            r["bar"].setValue(int(p["fraction"] * 1000))

        def _on_stage_finished(self, p):
            r = self._row(p["name"])
            r["done"] = True
            r["bar"].setRange(0, 1000)
            r["bar"].setValue(1000)
            r["bar"].setProperty("state", "ok" if p["ok"] else "bad")
            r["bar"].style().unpolish(r["bar"])
            r["bar"].style().polish(r["bar"])
            r["time"].setText(format_seconds(p["seconds"]) + ("" if p["ok"] else "  failed"))

        def _on_residuals(self, p):
            self.residual_text = "  ".join(f"{k} {v:.2e}" for k, v in sorted(p["values"].items()))

        # --- views
        def add_message(self, level, text, node_key=None, when=None):
            stamp = time.strftime("%H:%M:%S", time.localtime(when or time.time()))
            item = QtWidgets.QListWidgetItem(_level_icon(level if level in ("ok", "warning", "error") else "info"), f"{stamp}   {text}")
            item.setData(QtCore.Qt.UserRole, node_key)
            item.setData(QtCore.Qt.UserRole + 1, level)
            self.messages.addItem(item)
            self.messages.scrollToBottom()
            return item

        def message_rows(self):
            return [(self.messages.item(i).data(QtCore.Qt.UserRole + 1), self.messages.item(i).text().split("   ", 1)[1])
                    for i in range(self.messages.count())]

        def stage_rows(self):
            """[(stage, running/done, bar percent or None when busy, time text)] in the order they started."""
            out = []
            for name, r in sorted(self.rows.items(), key=lambda kv: kv[1]["row"]):
                bar = r["bar"]
                out.append((name, "done" if r["done"] else "running", None if bar.maximum() == 0 else round(bar.value() / 10, 1),
                            r["time"].text()))
            return out

        def flush(self):
            if self.pending_log:
                lines, self.pending_log = self.pending_log, []
                self.log.appendPlainText("\n".join(lines))
                bar = self.log.verticalScrollBar()
                bar.setValue(bar.maximum())

        def log_text(self):
            self.flush()
            return self.log.toPlainText()

        def refresh(self):
            self.flush()
            now = time.time()
            for r in self.rows.values():
                if not r["done"]:
                    r["time"].setText(format_seconds(now - r["t0"]))

    _s["classes"] = {"Header": Header, "Pane": Pane, "Section": Section, "Bottom": Bottom}
    return _s["classes"]


def _main_window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _build():
    """The docks and panes (once). Headless: nothing."""
    if _s["built"]:
        return True
    try:
        import FreeCAD

        if not FreeCAD.GuiUp:
            return False
        from PySide import QtCore, QtWidgets
    except Exception:
        return False
    c = _classes()
    mw = _main_window()
    mb = c["Pane"]("Model Builder", "Model Builder")
    st = c["Pane"]("Settings", "Settings")
    bottom = c["Bottom"]()
    sheet = stylesheet()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)  # Model Builder over Settings, as Ansys' Outline and Details
    split.setObjectName("BWSimSidebarSplit")
    split.setChildrenCollapsible(False)
    split.setWindowTitle(SIDEBAR_TITLE)  # the overlay names the tab after it
    split.addWidget(mb)
    split.addWidget(st)
    split.setSizes(list(SPLIT))
    split.setStyleSheet(sheet)
    _s["split"] = split
    dock = QtWidgets.QDockWidget("Messages", mw)
    dock.setObjectName(BOTTOM)
    dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
    dock.setTitleBarWidget(QtWidgets.QWidget())  # the pane draws its own header
    dock.setWidget(bottom)
    dock.setStyleSheet(sheet)
    dock.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
    _s["docks"][BOTTOM] = dock
    _s["panes"] = {"mb": mb, "st": st, "bottom": bottom}

    class Bridge(QtCore.QObject):
        event = QtCore.Signal(str, object)

    bridge = Bridge(mw)
    bridge.event.connect(bottom.on_event)  # queued when an event comes from another thread
    _s["bridge"] = bridge
    _s["listener"] = lambda kind, payload: bridge.event.emit(kind, payload)
    import bruceworx_sim_events as events

    events.subscribe(_s["listener"])
    _s["built"] = True
    return True


def model_builder_host():
    """The Model Builder's empty content widget (vertical layout), for the next phase's tree; None headless."""
    return _s["panes"]["mb"].host if _build() else None


def settings_host():
    """The Settings pane's content widget (vertical layout); None headless."""
    return _s["panes"]["st"].host if _build() else None


def messages_pane():
    """The bottom pane (Messages, Progress, Log, Table); None headless."""
    return _s["panes"]["bottom"] if _build() else None


def section(title, body=None):
    """A collapsible Settings section with the gradient header."""
    return _classes()["Section"](title, body)


def clear_host(host):
    """Removes the placeholder labels from a host (the next phase calls this before filling it)."""
    lay = host.layout()
    for w in host.findChildren(__import__("PySide").QtWidgets.QLabel):
        if w.property("bwPlaceholder"):
            lay.removeWidget(w)
            w.deleteLater()
    while lay.count() and lay.itemAt(lay.count() - 1).spacerItem():
        lay.takeAt(lay.count() - 1)


def active():
    return bool(_s["active"])


def docks():
    return dict(_s["docks"])


# ----- the model tree and the overlay panels ------------------------------------------------------------------

OVERLAYS = ("OverlayRight",)


def _overlay_widget(name):
    from PySide import QtWidgets

    for w in _main_window().findChildren(QtWidgets.QTabWidget, name):
        if w.metaObject().className() == "Gui::OverlayTabWidget":
            return w
    return None


def _put_away_overlays():
    """The model tree (left) and the right panel with the floating task view: auto hide, so nothing floats over the
    3D view. The task view slides in when a command opens, as it does for any hidden panel."""
    import FreeCADGui

    saved = {}
    for name in OVERLAYS:
        try:
            saved[name] = bool(FreeCADGui.setOverlayAutoHide(name, True))
        except Exception:
            continue
        w = _overlay_widget(name)
        if w is not None:
            saved[name + ":visible"] = w.isVisible()
    _s["saved"]["overlays"] = saved


def _restore_overlays():
    import FreeCADGui

    for name in OVERLAYS:
        was = _s["saved"].get("overlays", {}).get(name)
        if was is None:
            continue
        try:
            FreeCADGui.setOverlayAutoHide(name, was)
        except Exception:
            pass


def _left():
    return _overlay_widget(LEFT)


def _sidebar_index():
    """The overlay tab of the Simulation sidebar (the dock's tab is the one after the model tree's), or -1."""
    left = _left()
    dock = _s.get("sidebar")
    if left is None or dock is None:
        return -1
    for i in range(left.count()):
        if left.tabText(i) == SIDEBAR_TITLE:
            return i
    return -1


def _refresh_left():
    """Makes the overlay manager apply the tab bar setting now (setting the auto hide mode it already has)."""
    import FreeCADGui

    try:
        was = FreeCADGui.setOverlayAutoHide(LEFT, True)
        FreeCADGui.setOverlayAutoHide(LEFT, bool(was))
    except Exception:
        pass


def _show_sidebar():
    """The left panel gets its Simulation tab (made on first use) with the tab bar on, and selects it."""
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore

    left = _left()
    if left is None:
        return False
    if _s.get("sidebar") is None:
        dock = FreeCADGui.addDockWindow(SIDEBAR, _s["split"], int(QtCore.Qt.LeftDockWidgetArea.value), LEFT)
        _s["sidebar"] = dock
        index = left.count() - 1
        left.setTabText(index, SIDEBAR_TITLE)
    dock = _s["sidebar"]
    group = FreeCAD.ParamGet(VIEW_PATH)
    contents = {name: value for _kind, name, value in (group.GetContents() or [])}
    _s["saved"]["tabbar"] = contents.get("DockOverlayHideTabBar")
    _s["saved"]["tab"] = left.currentIndex()
    dock.show()
    if not _s.get("quit_hook"):
        _s["quit_hook"] = True  # quitting inside the workspace must not leave the tab bar on in every other workspace
        from PySide import QtWidgets

        QtWidgets.QApplication.instance().aboutToQuit.connect(_restore_tab_bar)
    group.SetBool("DockOverlayHideTabBar", False)  # the fork hides the overlay's tab bar; the sidebar needs it
    _refresh_left()
    index = _sidebar_index()
    if index >= 0:
        left.tabBar().setTabVisible(index, True)
        left.tabBar().tabBarClicked.emit(index)
        left.setCurrentIndex(index)
        _s["saved"]["width"] = left.width()
        if left.width() < SIDEBAR_WIDTH:
            _widen_left(SIDEBAR_WIDTH)
    return index >= 0


def _hide_sidebar():
    """Back to the model tree alone: the Simulation tab is hidden, the tab bar goes off again."""
    import FreeCAD

    left = _left()
    dock = _s.get("sidebar")
    if left is None or dock is None:
        return
    index = _sidebar_index()
    if index >= 0 and left.currentIndex() == index:
        target = _s["saved"].get("tab", 0)
        target = 0 if target in (None, index) or target >= left.count() else target
        left.tabBar().tabBarClicked.emit(target)
        left.setCurrentIndex(target)
    if _s["saved"].get("width") and left.width() != _s["saved"]["width"]:
        _widen_left(_s["saved"]["width"])
    group = FreeCAD.ParamGet(VIEW_PATH)
    was = _s["saved"].get("tabbar")
    if was is None:
        group.RemBool("DockOverlayHideTabBar")
    else:
        group.SetBool("DockOverlayHideTabBar", bool(was))
    if index >= 0:
        left.tabBar().setTabVisible(index, False)
    dock.hide()
    _refresh_left()
    if _hides_tab_bar():
        left.tabBar().hide()  # the overlay applies the setting only when it re-lays out
        from PySide import QtCore

        QtCore.QTimer.singleShot(150, _late_hide)  # and it re-lays out once more after the panel was resized


def _restore_tab_bar():
    """The fork's tab bar setting as it was before the Simulation tab turned it on (also at quit)."""
    import FreeCAD

    if not _s["active"] and "tabbar" not in _s["saved"]:
        return
    group = FreeCAD.ParamGet(VIEW_PATH)
    was = _s["saved"].get("tabbar")
    if was is None:
        group.RemBool("DockOverlayHideTabBar")
    else:
        group.SetBool("DockOverlayHideTabBar", bool(was))


def _late_hide():
    left = _left()
    if left is not None and not _s["active"] and _hides_tab_bar():
        left.tabBar().hide()


def _hides_tab_bar():
    """The fork's own setting (the tab bar is off unless the preference says otherwise)."""
    was = _s["saved"].get("tabbar")
    return True if was is None else bool(was)


def _widen_left(width):
    """Drags the left panel's size grip to ``width`` px: the grip's dragMove signal is invokable, the panel's own method is not."""
    from PySide import QtCore, QtWidgets

    left = _left()
    if left is None:
        return False
    for grip in left.findChildren(QtWidgets.QWidget):
        if grip.metaObject().className() == "Gui::OverlaySizeGrip":
            edge = left.mapToGlobal(QtCore.QPoint(width, left.height() // 2))
            return bool(QtCore.QMetaObject.invokeMethod(grip, "dragMove", QtCore.Q_ARG(QtCore.QPoint, edge)))
    return False


def sidebar_dock():
    """The overlay dock holding the Model Builder and Settings (None until the workspace was first shown)."""
    return _s.get("sidebar")


def sidebar_shown():
    left = _left()
    index = _sidebar_index()
    return left is not None and index >= 0 and left.currentIndex() == index and left.tabBar().isTabVisible(index)


def select_model_tab(on=True):
    """Switches the left panel between the model tree (on) and the Simulation tab (off)."""
    left = _left()
    index = _sidebar_index()
    if left is None or index < 0:
        return False
    target = 0 if on else index
    left.tabBar().tabBarClicked.emit(target)
    left.setCurrentIndex(target)
    return True


# ----- the 3D view -----------------------------------------------------------------------------------------


def _rgba(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def _apply_view():
    """The light-blue gradient background, with the axis triad."""
    import FreeCAD

    group = FreeCAD.ParamGet(VIEW_PATH)
    contents = {name: value for _kind, name, value in (group.GetContents() or [])}
    saved = {}
    for name in ("Gradient", "Simple", "RadialGradient", "UseBackgroundColorMid"):
        saved[name] = ("bool", contents.get(name))
    for name in ("BackgroundColor2", "BackgroundColor3"):
        saved[name] = ("uint", contents.get(name))
    _s["saved"]["view"] = saved
    group.SetBool("Simple", False)
    group.SetBool("RadialGradient", False)
    group.SetBool("UseBackgroundColorMid", False)
    group.SetUnsigned("BackgroundColor2", _rgba(VIEW_TOP))
    group.SetUnsigned("BackgroundColor3", _rgba(VIEW_BOTTOM))
    group.SetBool("Gradient", True)
    _s["saved"]["triads"] = {}
    _triad(True)


def _restore_view():
    import FreeCAD

    group = FreeCAD.ParamGet(VIEW_PATH)
    for name, (kind, value) in _s["saved"].get("view", {}).items():
        try:
            if value is None:
                (group.RemBool if kind == "bool" else group.RemUnsigned)(name)
            elif kind == "bool":
                group.SetBool(name, bool(value))
            else:
                group.SetUnsigned(name, int(value))
        except Exception:
            pass
    _triad(False)


def _views():
    import FreeCAD
    import FreeCADGui

    out = []
    for name in FreeCAD.listDocuments():
        try:
            out += FreeCADGui.getDocument(name).mdiViewsOfType("Gui::View3DInventor")
        except Exception:
            pass
    return out


def _triad(on):
    """The axis triad on every 3D view while the workspace is up; each view's own setting comes back after."""
    saved = _s["saved"].setdefault("triads", {})
    if on:
        for view in _views():
            try:
                if id(view) not in saved:
                    saved[id(view)] = (view, bool(view.hasAxisCross()))
                view.setAxisCross(True)
            except Exception:
                pass
        return
    for view, was in list(saved.values()):
        try:
            view.setAxisCross(was)
        except Exception:
            pass
    saved.clear()


# ----- entering and leaving ---------------------------------------------------------------------------------


def enter():
    """Shows the shell (the Simulation workspace became active)."""
    if _s["active"] or not _build():
        return False
    from PySide import QtCore

    mw = _main_window()
    d = _s["docks"]
    _s["saved"]["corners"] = {c: mw.corner(c) for c in (QtCore.Qt.BottomLeftCorner, QtCore.Qt.BottomRightCorner)}
    mw.setCorner(QtCore.Qt.BottomLeftCorner, QtCore.Qt.LeftDockWidgetArea)  # the bottom pane sits under the 3D view only
    mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, d[BOTTOM])
    d[BOTTOM].show()
    _s["active"] = True
    _put_away_overlays()
    _apply_view()
    _show_sidebar()
    relayout()
    _s["panes"]["bottom"].add_message("info", "Simulation workspace ready")
    if _s["timer"] is None:
        timer = QtCore.QTimer(mw)
        timer.setInterval(600)
        timer.timeout.connect(poll)
        _s["timer"] = timer
    _s["timer"].start()
    try:
        import bruceworx_sim_panes as panes

        panes.attach()
    except Exception as exc:
        _warn_console(f"Model Builder: {exc}")
    poll()
    QtCore.QTimer.singleShot(0, relayout)
    return True


def leave():
    """Removes the shell and puts everything back."""
    if not _s["active"]:
        return False
    from PySide import QtCore

    mw = _main_window()
    _s["active"] = False
    try:
        import bruceworx_sim_panes as panes

        panes.detach()
    except Exception:
        pass
    if _s["timer"] is not None:
        _s["timer"].stop()
    _hide_sidebar()
    for dock in _s["docks"].values():
        dock.hide()
        mw.removeDockWidget(dock)
    for corner, area in _s["saved"].get("corners", {}).items():
        mw.setCorner(corner, area)
    _restore_overlays()
    _restore_view()
    _s["kind"] = "?"
    return True


def relayout():
    """The bottom pane's height (the sidebar is one overlay tab: nothing to lay out)."""
    if not _s["active"]:
        return
    mw = _main_window()
    from PySide import QtCore

    mw.resizeDocks([_s["docks"][BOTTOM]], [190], QtCore.Qt.Vertical)
    split = _s.get("split")
    if split is not None and split.height() > 0:
        total = split.height()
        split.setSizes([int(total * SPLIT[0] / sum(SPLIT)), int(total * SPLIT[1] / sum(SPLIT))])


def poll():
    """Follows the active study: the ribbon's tabs and groups, the placeholders, new 3D views' triads."""
    if not _s["active"]:
        return
    kind = active_kind()
    label = study_label()
    if kind != _s["kind"]:
        _s["kind"] = kind
        try:
            import bruceworx_workspaces as wsm

            bar = wsm.bar()
            if bar is not None:
                bar.set_study_kind(kind)
        except Exception:
            pass
    try:
        import bruceworx_sim_panes as panes

        panes.sync()
    except Exception as exc:
        _warn_console(f"Model Builder: {exc}")
    for pane in (_s["panes"]["mb"], _s["panes"]["st"]):
        pane.set_study(label)
    try:
        _triad(True)
    except Exception:
        pass


def _warn_console(text):
    try:
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"[sim] {text}\n")
    except Exception:
        pass


def on_workbench(name=None):
    """The workbench changed (FreeCAD's signal comes before the switch is done, so look a moment later)."""
    from PySide import QtCore

    QtCore.QTimer.singleShot(0, lambda: _sync(name))


def _sync(name=None):
    import FreeCADGui

    if not name:
        wb = FreeCADGui.activeWorkbench()
        name = wb.name() if wb is not None else ""
    if name == WORKBENCH:
        enter()
    else:
        leave()


# ----- commands -----------------------------------------------------------------------------------------------


def _later(what, phase):
    def run():
        text = f"{what} arrive in phase {phase} of the Simulation workspace (plan chapter 69)."
        pane = messages_pane()
        if pane is not None:
            pane.add_message("info", text)
        import bruceworx_sim_events as events

        events.message("info", text)

    return run


def _airflow_study():
    study = active_study()
    return study is not None and active_kind() in ("external_flow", "prop_wash")


def _airflow_setup():
    import bruceworx_sim_gui as g

    return g.open_study_panel()


def _airflow_results():
    import bruceworx_sim_gui as g

    panel = g.open_study_panel()
    return panel.load_results() if panel is not None and hasattr(panel, "load_results") else None


def _airflow_paraview():
    import bruceworx_cfd as cfd

    study = active_study()
    folder = getattr(study, "CaseFolder", "") if study is not None else ""
    if not folder:
        import bruceworx_sim_events as events

        events.message("warning", "ParaView: run the study first (it has no case folder yet)")
        return None
    return cfd.open_in_paraview(folder)


def _always():
    return True


COMMANDS = (
    ("BruceWorx_SimParameters", "Parameters", "BruceWorx_NewStudy.svg", "Named values with units for any field (phase P2)", _always,
     _later("Parameters", "P2")),
    ("BruceWorx_SimSelections", "Selections", "BruceWorx_NewStudy.svg", "Named face and body selections reused by loads (phase P2)",
     _always, _later("Named selections", "P2")),
    ("BruceWorx_SimGeometry", "Geometry", "BruceWorx_NewStudy.svg", "The study's geometry nodes: repair, defeature (phase P2)",
     _always, _later("Geometry nodes", "P2")),
    ("BruceWorx_SimProject", "Project", "BruceWorx_NewStudy.svg", "The project schematic of systems and transfers (phase P4)",
     _always, _later("The project schematic", "P4")),
    ("BruceWorx_AirflowSetup", "Flow setup", "BruceWorx_NewStudy.svg",
     "Speed, angle of attack, mesh, turbulence and the domain of the airflow study", _airflow_study, _airflow_setup),
    ("BruceWorx_AirflowResults", "Show flow", "BruceWorx_Probe.svg",
     "Show the pressure on the parts and the velocity on the mid cut plane", _airflow_study, _airflow_results),
    ("BruceWorx_AirflowParaView", "ParaView", "BruceWorx_Probe.svg", "Open the airflow case in ParaView", _airflow_study,
     _airflow_paraview),
)


class _Command:
    def __init__(self, text, icon, tip, active, handler):
        self.text, self.icon, self.tip, self.active, self.handler = text, icon, tip, active, handler

    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, self.icon), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        try:
            return bool(self.active())
        except Exception:
            return False

    def Activated(self):
        self.handler()


def install():
    """Registers the commands and follows the workbench; called once from InitGui."""
    if _s.get("installed"):
        return
    import FreeCADGui

    for name, text, icon, tip, active, handler in COMMANDS:
        FreeCADGui.addCommand(name, _Command(text, icon, tip, active, handler))
    mw = FreeCADGui.getMainWindow()
    if mw is not None:
        mw.workbenchActivated.connect(on_workbench)
    _s["installed"] = True
    try:
        _sync()
    except Exception:
        pass
