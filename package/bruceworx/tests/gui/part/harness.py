# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small harness for the part-modelling GUI checks (run inside FreeCAD's GUI).

A test is a generator: it does something, yields how many ms to let the event
loop run, then checks. ``check(id, ok, detail)`` records PASS/FAIL. Results go
to ``$PT_OUT/<script>-<tag>.txt`` (one line per check, written as they come, so
a crash keeps what ran) and ``.json``. Modal message boxes that pop up are
closed and logged, so a stray "Select a body first" cannot hang the run.

Env: PT_OUT (output folder), PT_TAG (light|dark), PT_SCRIPT (name, optional).
"""

import json
import os
import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

OUT = os.environ.get("PT_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
TAG = os.environ.get("PT_TAG", "light")
SCRIPT = os.environ.get("PT_SCRIPT", "part")
os.makedirs(OUT, exist_ok=True)

_txt = open(os.path.join(OUT, f"{SCRIPT}-{TAG}.txt"), "w")
_log = open(os.path.join(OUT, f"{SCRIPT}-{TAG}.log"), "w")
RESULTS = []
MODALS = []
_tests = []
_current = {"name": ""}


def mw():
    return Gui.getMainWindow()


def say(*args):
    _log.write(" ".join(str(a) for a in args) + "\n")
    _log.flush()


def narrate(text):
    """One plain sentence for the user watching the run (bruceworx_testwindow; no-op elsewhere)."""
    say("--", text)
    try:
        import bruceworx_testwindow as tw
        tw.say(text)
    except Exception:
        pass


def check(cid, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"id": cid, "ok": ok, "detail": str(detail), "test": _current["name"]})
    _txt.write(f"{'PASS' if ok else 'FAIL'} {cid}" + (f": {detail}" if detail and not ok else "") + "\n")
    _txt.flush()
    return ok


def test(fn):
    _tests.append(fn)
    return fn


# ----- widgets ----------------------------------------------------------------------------


def process(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def by_class(name, root=None):
    root = root or mw()
    return [w for w in root.findChildren(QtWidgets.QWidget) if w.metaObject().className() == name]


def overlay(name):
    """Gui::OverlayTabWidget OverlayLeft / OverlayRight."""
    for w in by_class("Gui::OverlayTabWidget"):
        if w.objectName() == name:
            return w
    return None


def task_view():
    views = by_class("Gui::TaskView::TaskView")
    vis = [v for v in views if v.isVisible()]
    return (vis or views or [None])[0]


def task_button_box():
    """The QDialogButtonBox the task view shows for the active dialog."""
    tv = task_view()
    if tv is None:
        return None
    boxes = [b for b in tv.findChildren(QtWidgets.QDialogButtonBox) if b.isVisible()]
    if not boxes:
        boxes = tv.findChildren(QtWidgets.QDialogButtonBox)
    return boxes[0] if boxes else None


def task_button(which):
    box = task_button_box()
    if box is None:
        return None
    std = {"ok": QtWidgets.QDialogButtonBox.Ok, "cancel": QtWidgets.QDialogButtonBox.Cancel,
           "close": QtWidgets.QDialogButtonBox.Close, "apply": QtWidgets.QDialogButtonBox.Apply}[which]
    return box.button(std)


def click_task(which="ok"):
    btn = task_button(which)
    if btn is None:
        return False
    btn.click()
    return True


def dialog_open():
    return Gui.Control.activeDialog()


def task_in_right_panel():
    """(the task view is inside OverlayRight, it is visible, the panel's current tab holds it)."""
    tv = task_view()
    right = overlay("OverlayRight")
    if tv is None or right is None:
        return False, f"taskview={tv is not None} right={right is not None}"
    inside = right.isAncestorOf(tv)
    current = right.currentWidget()
    title = right.tabText(right.currentIndex()) if right.currentIndex() >= 0 else ""
    front = (current is not None and (current.isAncestorOf(tv) or current is tv)) or title == "Tasks"
    left = overlay("OverlayLeft")
    in_left = left is not None and left.isAncestorOf(tv)
    ok = inside and tv.isVisible() and front and not in_left
    return ok, f"inside={inside} visible={tv.isVisible()} front={front} tab={title!r} in_left={in_left}"


def find_task_widget(name):
    """A widget of the open task dialog by object name (a visible one first)."""
    tv = task_view()
    if tv is None:
        return None
    found = tv.findChildren(QtWidgets.QWidget, name)
    vis = [w for w in found if w.isVisible()]
    return (vis or found or [None])[0]


def task_titles():
    """Titles of the task boxes in the task view (e.g. "Pad parameters")."""
    tv = task_view()
    if tv is None:
        return []
    out = []
    for box in by_class("Gui::TaskView::TaskBox", tv):
        if box.isVisible():
            for lab in box.findChildren(QtWidgets.QAbstractButton) + box.findChildren(QtWidgets.QLabel):
                if lab.isVisible() and lab.text():
                    out.append(lab.text())
                    break
    return out


def set_quantity(widget, value):
    """Set a Gui::QuantitySpinBox (its rawValue, which emits valueChanged as typing does),
    or a plain QSpinBox / QDoubleSpinBox."""
    if widget is None:
        return False
    try:
        if widget.metaObject().indexOfProperty("rawValue") >= 0:
            widget.setProperty("rawValue", float(value))
        elif widget.metaObject().className() == "Gui::UIntSpinBox":
            # Its QSpinBox value is offset by INT_MIN (QSpinBox.setValue(3) from Python means
            # about 2^31 occurrences), so type the number as a user does.
            le = widget.findChild(QtWidgets.QLineEdit)
            le.selectAll()
            le.setText(str(int(value)))
            widget.interpretText()
        else:
            widget.setValue(value)
        return True
    except Exception:
        say(traceback.format_exc())
        return False


def browser_trees():
    return [t for t in mw().findChildren(QtWidgets.QTreeWidget) if t.metaObject().className() == "Gui::TreeWidget"]


def expand_trees():
    for t in browser_trees():
        t.expandAll()


def tree_item(obj):
    """(tree, item) of the Browser row of obj, by its label."""
    for t in browser_trees():
        found = t.findItems(obj.Label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
        if found:
            return t, found[0]
    return None, None


def row_info(obj):
    hook = getattr(Gui, "BrowserRowInfo", None)
    if hook is None:
        return None
    try:
        return hook(obj)
    except Exception as exc:
        return ("<error %s>" % exc, ())


def double_click_row(obj):
    """Double-click the object's Browser row (what a user does to edit a feature)."""
    t, item = tree_item(obj)
    if item is None:
        return False
    t.scrollToItem(item)
    rect = t.visualItemRect(item)
    pos = rect.center()
    pos.setX(rect.left() + 30)
    vp = t.viewport()
    for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                 QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.MouseButtonRelease):
        ev = QtGui.QMouseEvent(kind, QtCore.QPointF(pos), QtCore.QPointF(vp.mapToGlobal(pos)),
                               QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(vp, ev)
    return True


def toast_texts():
    try:
        import bruceworx_toasts
    except ImportError:
        return []
    out = []
    for t in bruceworx_toasts.toasts():
        if t.isVisible():
            out.append(t.label.text())
    return out


def hide_toasts():
    try:
        import bruceworx_toasts
        bruceworx_toasts.hide_all()
    except Exception:
        pass


def toolbar(name):
    for bar in mw().findChildren(QtWidgets.QToolBar):
        if bar.objectName() == name:
            return bar
    return None


def action_for(command):
    """The QAction of a command on any visible toolbar (or None)."""
    cmd = Gui.Command.get(command)
    if cmd is None:
        return None
    for bar in mw().findChildren(QtWidgets.QToolBar):
        for a in bar.actions():
            if a.data() == command or a.objectName() == command:
                return a
    return None


def actions_of(bar):
    """[(command name, action)] of a toolbar's command buttons."""
    out = []
    for a in bar.actions():
        if a.isSeparator() or isinstance(a, QtWidgets.QWidgetAction):
            continue
        out.append((a.data() if a.data() else a.objectName(), a))
    return out


# ----- screenshots ------------------------------------------------------------------------


def shot_path(name):
    return os.path.join(OUT, f"{SCRIPT}-{TAG}-{name}.png")


def grab_window(name):
    """The main window with the 3D view drawn in (it grabs blank otherwise)."""
    process()
    try:
        win = mw()
        img = win.grab().toImage()
        views = [w for w in by_class("Gui::View3DInventor") if w.isVisible()]
        if views and Gui.ActiveDocument and Gui.ActiveDocument.ActiveView:
            vw = views[0]
            dpr = vw.devicePixelRatioF()
            tmp = os.path.join(OUT, "_vp.png")
            Gui.ActiveDocument.ActiveView.saveImage(tmp, int(vw.width() * dpr), int(vw.height() * dpr), "Current")
            vp = QtGui.QImage(tmp)
            vp.setDevicePixelRatio(dpr)
            base = QtGui.QImage(img.size(), QtGui.QImage.Format_ARGB32)
            base.setDevicePixelRatio(img.devicePixelRatio())
            base.fill(QtGui.QColor(0, 0, 0, 0))
            # Viewport first, then the window grab outside it, then the floating panels on top.
            p = QtGui.QPainter(base)
            p.drawImage(vw.mapTo(win, QtCore.QPoint(0, 0)), vp)
            win_img = img.convertToFormat(QtGui.QImage.Format_ARGB32)
            r = QtCore.QRect(vw.mapTo(win, QtCore.QPoint(0, 0)), vw.size())
            p.setClipRegion(QtGui.QRegion(win.rect()).subtracted(QtGui.QRegion(r)))
            p.drawImage(0, 0, win_img)
            p.setClipping(False)
            for ov in by_class("Gui::OverlayTabWidget"):
                if ov.isVisible() and ov.width() > 10:
                    p.drawPixmap(ov.mapTo(win, QtCore.QPoint(0, 0)), ov.grab())
            for t in toast_widgets():
                p.drawPixmap(t.mapTo(win, QtCore.QPoint(0, 0)), t.grab())
            p.end()
            img = base
        img.save(shot_path(name))
    except Exception:
        say("grab", name, traceback.format_exc())
    return shot_path(name)


def toast_widgets():
    try:
        import bruceworx_toasts
        return [t for t in bruceworx_toasts.toasts() if t.isVisible()]
    except Exception:
        return []


def grab_widget(widget, name):
    process()
    try:
        widget.grab().save(shot_path(name))
    except Exception:
        say("grab", name, traceback.format_exc())
    return shot_path(name)


# ----- modal guard ------------------------------------------------------------------------


def _close_modals():
    app = QtWidgets.QApplication.instance()
    w = app.activeModalWidget()
    if w is None:
        return
    text = w.windowTitle()
    if isinstance(w, QtWidgets.QMessageBox):
        text += ": " + w.text()
    MODALS.append({"test": _current["name"], "text": text, "class": w.metaObject().className()})
    say("closed modal during", _current["name"], ":", text)
    try:
        grab_widget(w, "modal-%d" % len(MODALS))
    except Exception:
        pass
    w.reject() if hasattr(w, "reject") else w.close()


_guard = QtCore.QTimer()
_guard.setInterval(400)
_guard.timeout.connect(_close_modals)


# ----- runner -----------------------------------------------------------------------------


def _finish():
    _guard.stop()
    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {"script": SCRIPT, "tag": TAG, "passed": passed, "failed": len(RESULTS) - passed,
               "results": RESULTS, "modals": MODALS}
    with open(os.path.join(OUT, f"{SCRIPT}-{TAG}.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    _txt.write(f"# {SCRIPT} {TAG}: {passed} passed, {len(RESULTS) - passed} failed\n")
    _txt.flush()
    try:
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
    except Exception:
        pass
    for d in list(App.listDocuments().values()):
        try:
            App.closeDocument(d.Name)
        except Exception:
            pass
    QtCore.QTimer.singleShot(300, QtWidgets.QApplication.quit)


def _run_test(index):
    if index >= len(_tests):
        _finish()
        return
    fn = _tests[index]
    _current["name"] = fn.__name__
    say("== test", fn.__name__)
    narrate((fn.__doc__ or fn.__name__).strip().splitlines()[0])
    start = time.time()
    try:
        gen = fn()
    except Exception:
        check(fn.__name__ + ".exception", False, traceback.format_exc().splitlines()[-1])
        say(traceback.format_exc())
        QtCore.QTimer.singleShot(200, lambda: _run_test(index + 1))
        return
    if gen is None or not hasattr(gen, "__next__"):
        QtCore.QTimer.singleShot(200, lambda: _run_test(index + 1))
        return

    def advance():
        try:
            wait = next(gen)
        except StopIteration:
            say("   done in %.1fs" % (time.time() - start))
            QtCore.QTimer.singleShot(200, lambda: _run_test(index + 1))
            return
        except Exception:
            check(fn.__name__ + ".exception", False, traceback.format_exc().splitlines()[-1])
            say(traceback.format_exc())
            _cleanup_dialog()
            QtCore.QTimer.singleShot(400, lambda: _run_test(index + 1))
            return
        QtCore.QTimer.singleShot(int(wait or 0), advance)

    advance()


def _cleanup_dialog():
    try:
        if Gui.Control.activeDialog():
            if not click_task("cancel"):
                Gui.Control.closeDialog()
    except Exception:
        pass


def start(delay=3500):
    """Run the registered tests after FreeCAD has settled."""
    _guard.start()
    QtCore.QTimer.singleShot(delay, lambda: _run_test(0))


def fully_visible(widget):
    """(ok, detail): the widget shows whole on screen (not clipped by its panel, not too narrow)."""
    if widget is None:
        return False, "missing"
    if not widget.isVisible():
        return False, "hidden"
    seen = widget.visibleRegion().boundingRect()
    want = widget.rect()
    hint = widget.sizeHint()
    ok = seen == want and widget.width() >= hint.width() - 2 and widget.height() >= hint.height() - 6
    return ok, (f"text={getattr(widget, 'text', lambda: '')()!r} size={widget.width()}x{widget.height()} "
                f"hint={hint.width()}x{hint.height()} visible part={seen.width()}x{seen.height()}")


def widget_chain(widget, stop=None):
    """Where a widget sits: class/name, geometry, visible region, up to the main window."""
    out = []
    w = widget
    while w is not None and w is not stop:
        vr = w.visibleRegion().boundingRect()
        out.append(f"{w.metaObject().className()}#{w.objectName()} geo={w.geometry().getRect()} "
                   f"vis={vr.width()}x{vr.height()} shown={w.isVisible()}")
        if w is mw():
            break
        w = w.parentWidget()
    return out
