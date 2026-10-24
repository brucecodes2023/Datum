# SPDX-License-Identifier: LGPL-2.1-or-later
"""The floating task panel reads like a dialog: one header, then sections.

While a command is open (Gui.Control.activeDialog()), the "Tasks" panel takes
the title and icon of the command's first task box (from the dialog's own
content, not the workbench's command lists) ("Fillet Parameters",
"Revolute joint"), and that box's own header is hidden so it is not shown
twice. Boxes with an empty header lose the empty strip. Later boxes keep their
headers as section captions. When the command closes, the panel is "Tasks"
again with every header back. The overlay title bar draws the icon ([BW] in
Gui/OverlayWidgets.cpp).

Only while Preferences/BruceWorx/FloatingTaskPanel is on (default).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
TASKS_DOCK = "Tasks"


def pick_title(headers, fallback="Tasks"):
    """(title, index of the header it came from or None) from (text, has_icon) pairs."""
    for index, (text, _icon) in enumerate(headers):
        if text.strip():
            return text.strip(), index
    return fallback, None


def _enabled():
    import FreeCAD

    prefs = FreeCAD.ParamGet(PREFS_PATH)
    return prefs.GetBool("FloatingModelTree", True) and prefs.GetBool("FloatingTaskPanel", True)


def _header_frames(root):
    """(frame, button) for each task box header under ``root``, top to bottom."""
    from PySide import QtWidgets

    found = []
    for frame in root.findChildren(QtWidgets.QFrame):
        if frame.property("class") != "header":
            continue
        buttons = [b for b in frame.findChildren(QtWidgets.QToolButton) if b.property("class") == "header"]
        found.append((frame, buttons[0] if buttons else None))
    found.sort(key=lambda item: item[0].mapTo(root, item[0].rect().topLeft()).y())
    return found


class _Card:
    def __init__(self, dock):
        from PySide import QtCore

        self.dock = dock
        self.title = dock.windowTitle()  # translated by MainWindow
        self.hidden = []
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.update)

    def schedule(self):
        self.timer.start()

    def _restore(self):
        for frame in self.hidden:
            try:
                frame.show()
            except RuntimeError:  # deleted with its dialog
                pass
        self.hidden = []

    def update(self):
        import FreeCADGui
        from PySide import QtGui

        self._restore()
        if not _enabled() or not FreeCADGui.Control.activeDialog():
            self.dock.setWindowTitle(self.title)
            self.dock.setWindowIcon(QtGui.QIcon())
            return
        # Only the open dialog's own boxes: the task panel also holds the
        # workbench's command lists (task watchers) above them.
        try:
            roots = list(FreeCADGui.Control.activeTaskDialog().getDialogContent())
        except Exception:
            roots = [self.dock]
        frames = []
        for root in roots:
            # The dialog shows and hides its own boxes later (Sketcher's tool box).
            root.installEventFilter(_filter)
            frames += [(f, b) for f, b in _header_frames(root) if f.isVisibleTo(self.dock)]
        title, index = pick_title([(b.text() if b else "", b is not None) for _f, b in frames])
        icon = frames[index][1].icon() if index is not None else QtGui.QIcon()
        self.dock.setWindowTitle(title)
        self.dock.setWindowIcon(icon)
        for i, (frame, button) in enumerate(frames):
            if i == index or button is None or not button.text().strip():
                frame.hide()
                self.hidden.append(frame)
        from PySide import QtCore

        QtCore.QTimer.singleShot(50, self.settle_title_bar)

    def settle_title_bar(self):
        """Lay the overlay title bar out again: after documents open and close its layout can keep
        a stale 8 px width, and the overlay then elides the title to nothing (found by PDM-05)."""
        try:
            bar = self.dock.titleBarWidget()
            if bar is not None and bar.layout() is not None and bar.layout().geometry().width() < bar.width() - 4:
                bar.layout().setGeometry(bar.rect())
                bar.update()
        except RuntimeError:
            pass


_card = None
_filter = None
_tries = 0


def install():
    """Watch the task panel and keep its header in step with the open command."""
    global _card, _filter, _tries
    if _card is not None:
        return
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, TASKS_DOCK)
    if dock is None:
        # The task panel dock is created after the GUI modules load; try again.
        _tries += 1
        if _tries < 40:
            QtCore.QTimer.singleShot(500, install)
        return
    _card = _Card(dock)

    class _Filter(QtCore.QObject):
        # Task boxes are added and removed as commands open and close.
        def eventFilter(self, _obj, event):
            if event.type() in (
                QtCore.QEvent.ChildAdded,
                QtCore.QEvent.ChildRemoved,
                QtCore.QEvent.Show,
                QtCore.QEvent.Hide,
            ):
                _card.schedule()
            return False

    _filter = _Filter()
    for widget in [dock] + dock.findChildren(QtWidgets.QWidget):
        name = widget.metaObject().className()
        if widget is dock or "TaskView" in name or "TaskPanel" in name or "ActionPanel" in name:
            widget.installEventFilter(_filter)
    _card.schedule()
