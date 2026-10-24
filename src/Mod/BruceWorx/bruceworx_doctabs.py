# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Start page stays the first document tab, without a close button (24 Oct 2026).

The document tabs (at the bottom of the window, MainWindow/DocumentTabPos)
show every open file; the Start page, where the vault lives, is kept as the
first tab so one click always goes back to it. Its close button is removed;
Std_Start reopens it if it is closed some other way (Window > Close all).
Tab titles drop FreeCAD's view number ("Plate : 1" -> "Plate") unless a
document has two views, as the title-row tabs do.

Preferences/BruceWorx/PinStartTab (default on).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "PinStartTab"
START_CLASSES = ("StartGui::StartView",)
START_TITLES = ("Start",)


def start_index(entries):
    """Index of the Start page among (class name, title) tab entries, or -1."""
    for i, (cls, title) in enumerate(entries):
        if cls in START_CLASSES or (not cls and title in START_TITLES):
            return i
    return -1


def clean_title(text, views=1):
    """"Plate : 1" -> "Plate" when the document has one view; kept as is with several."""
    base, sep, number = (text or "").rpartition(" : ")
    if sep and number.strip().isdigit() and views <= 1:
        return base
    return text


def _enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


_mdi = None
_timer = None


def _bar():
    from PySide import QtWidgets

    return _mdi.findChild(QtWidgets.QTabBar) if _alive(_mdi) else None


def pin():
    """Move the Start tab to the front and take its close button away."""
    from PySide import QtWidgets

    if not _enabled() or not _alive(_mdi):
        return
    bar = _bar()
    if bar is None:
        return
    subs = _mdi.subWindowList(QtWidgets.QMdiArea.CreationOrder)
    entries = []
    for i in range(bar.count()):
        sub = subs[i] if i < len(subs) else None
        widget = sub.widget() if sub is not None else None
        cls = widget.metaObject().className() if widget is not None else ""
        entries.append((cls, bar.tabText(i).replace("&", "")))
    counts = {}
    for _cls, title in entries:
        base = title.rpartition(" : ")[0] or title
        counts[base] = counts.get(base, 0) + 1
    for i, (_cls, title) in enumerate(entries):
        base = title.rpartition(" : ")[0] or title
        clean = clean_title(title, counts.get(base, 1))
        if clean != title:
            bar.setTabText(i, clean)  # QMdiArea sets it again only when the window title changes
    index = start_index(entries)
    if index < 0:
        return
    if index > 0:
        bar.moveTab(index, 0)  # QMdiArea follows the move
        index = 0
    for side in (QtWidgets.QTabBar.RightSide, QtWidgets.QTabBar.LeftSide):
        button = bar.tabButton(index, side)
        if button is not None and button.isVisible():
            button.hide()


def _shutdown():
    global _timer
    if _timer is not None:
        _timer.stop()
        _timer = None


def install():
    global _mdi, _timer
    if _timer is not None:
        return
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    def late(tries=50):
        global _mdi, _timer
        mw = FreeCADGui.getMainWindow()
        mdi = mw.findChild(QtWidgets.QMdiArea) if mw is not None else None
        if mdi is None:
            if tries:
                QtCore.QTimer.singleShot(200, lambda: late(tries - 1))
            return
        _mdi = mdi
        mdi.subWindowActivated.connect(lambda _w: QtCore.QTimer.singleShot(0, pin))
        _timer = QtCore.QTimer()
        _timer.setInterval(500)  # tabs come and go without a signal Python can reach (open, close, reorder)
        _timer.timeout.connect(pin)
        _timer.start()
        QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
        pin()

    QtCore.QTimer.singleShot(0, late)
