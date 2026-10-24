# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small harness for self-quitting BruceWorx GUI test scripts (sketching).

A script imports this, registers steps with ``@step`` (each runs in its own
event-loop turn, so the GUI updates between them) and calls ``run()``.
A step may return a number of milliseconds to wait before the next one.

Results go to $SK_OUT/<script>-<tag>.log as "PASS name" / "FAIL name: detail"
lines and to <script>-<tag>.json. Screenshots: $SK_OUT/<tag>-<script>-<name>.png.
Environment: SK_OUT (output folder), SK_TAG (light | dark), SK_TIMEOUT (s).
"""

import json
import os
import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

OUT = os.environ.get("SK_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
TAG = os.environ.get("SK_TAG", "light")
os.makedirs(OUT, exist_ok=True)
SCRIPT = os.path.splitext(os.path.basename(sys.argv[-1] if sys.argv else "script"))[0]

mw = Gui.getMainWindow()
S = {}  # state shared between steps
results = []
_steps = []
_log = None
_watchdog = None


def set_script(name):
    global SCRIPT
    SCRIPT = name


def log(*a):
    global _log
    if _log is None:
        _log = open(os.path.join(OUT, f"{SCRIPT}-{TAG}.log"), "w")
    _log.write(" ".join(str(x) for x in a) + "\n")
    _log.flush()


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append({"check": name, "ok": ok, "detail": str(detail)})
    log(("PASS " if ok else "FAIL ") + name + ("" if ok and not detail else f": {detail}"))
    return ok


def note(text):
    log("NOTE " + str(text))


def say(text):
    """Narrate a step in the watched test log (bruceworx_testwindow); no-op elsewhere."""
    try:
        import bruceworx_testwindow as tw

        tw.say(text)
    except Exception:
        pass


def step(fn):
    _steps.append(fn)
    return fn


def pump(ms=0):
    end = time.time() + ms / 1000.0
    QtWidgets.QApplication.processEvents()
    while time.time() < end:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)
        time.sleep(0.01)


# ----- widgets -----------------------------------------------------------------------------


def by_class(cls_name, root=None):
    root = root or mw
    return [w for w in root.findChildren(QtWidgets.QWidget) if w.metaObject().className() == cls_name]


def overlay(name):
    for w in by_class("Gui::OverlayTabWidget"):
        if w.objectName() == name:
            return w
    return None


def view_widget():
    v = by_class("Gui::View3DInventor")
    vis = [w for w in v if w.isVisible()]
    return (vis or v or [None])[0]


def toolbar(name):
    for tb in mw.findChildren(QtWidgets.QToolBar):
        if tb.objectName() == name:
            return tb
    return None


def visible_toolbars():
    return [tb for tb in mw.findChildren(QtWidgets.QToolBar) if tb.isVisible() and tb.parent() is mw]


def action_for(command):
    """The QAction FreeCAD made for a command (in any toolbar or menu), or None."""
    found = None
    for a in mw.findChildren(QtGui.QAction):
        if a.objectName() == command:
            if found is None or a.isVisible():
                found = a
    return found


def toolbar_buttons(tb):
    """[(command name or text, action, button widget)] for a toolbar's visible actions."""
    out = []
    for a in tb.actions():
        if a.isSeparator() or not a.isVisible():
            continue
        w = tb.widgetForAction(a)
        name = a.objectName() or a.text()
        # Group (drop-down) actions: the default action carries the command name.
        if not name and isinstance(w, QtWidgets.QToolButton) and w.defaultAction():
            name = w.defaultAction().objectName()
        out.append((name, a, w))
    return out


def task_view():
    tv = by_class("Gui::TaskView::TaskView")
    vis = [w for w in tv if w.isVisible()]
    return (vis or tv or [None])[0]


def task_boxes():
    tv = task_view()
    return by_class("Gui::TaskView::TaskBox", tv) if tv else []


def button_box():
    """The task dialog's OK/Cancel box (the one in the task view)."""
    tv = task_view()
    if tv is None:
        return None
    boxes = [b for b in tv.findChildren(QtWidgets.QDialogButtonBox) if b.isVisible()]
    return boxes[0] if boxes else None


def check_buttons_on_screen(name, box):
    """OK / Cancel of a task dialog: really on screen (not clipped or covered)?"""
    if box is None:
        return check(name, False, "no button box")
    out = []
    for b in box.buttons():
        if not b.isVisible():
            continue
        centre = b.mapTo(mw, b.rect().center())
        top = mw.childAt(centre)
        shown = not b.visibleRegion().isEmpty() and (top is b or (top is not None and b.isAncestorOf(top)))
        out.append((b.text(), shown))
    return check(name, out and all(ok for _t, ok in out), out)


def press(box, role):
    for b in box.buttons():
        if box.standardButton(b) == role:
            b.click()
            return True
    return False


def labels_text(root):
    return [l.text() for l in root.findChildren(QtWidgets.QLabel) if l.isVisible() and l.text()]


# ----- screenshots and contrast -----------------------------------------------------------


def grab(name):
    """Save the window as a user sees it (see composite())."""
    pump(150)
    path = os.path.join(OUT, f"{TAG}-{SCRIPT}-{name}.png")
    img = composite()
    if img is not None:
        img.save(path)
    return path


def composite():
    """The window as a user sees it: window grab + the 3D view image + the overlay panels."""
    img = None
    try:
        img = mw.grab().toImage()
        dpr = mw.devicePixelRatioF()
        img.setDevicePixelRatio(dpr)
        p = QtGui.QPainter(img)
        vw = view_widget()
        if vw is not None and Gui.ActiveDocument and Gui.ActiveDocument.ActiveView:
            tmp = os.path.join(OUT, f"_vp-{TAG}-{SCRIPT}.png")
            try:
                Gui.ActiveDocument.ActiveView.saveImage(
                    tmp, int(vw.width() * dpr), int(vw.height() * dpr), "Current")
                vimg = QtGui.QImage(tmp)
                vimg.setDevicePixelRatio(dpr)
                p.drawImage(vw.mapTo(mw, QtCore.QPoint(0, 0)), vimg)
            except Exception:
                pass
        for w in by_class("Gui::OverlayTabWidget"):
            if w.isVisible():
                p.drawPixmap(w.mapTo(mw, QtCore.QPoint(0, 0)), w.grab())
        for w in mw.findChildren(QtWidgets.QFrame):
            # floating cards over the 3D view (repair card, toasts)
            if w.isVisible() and w.objectName().startswith("BruceWorx") and w.isWindow() is False \
                    and vw is not None and vw.isAncestorOf(w):
                p.drawPixmap(w.mapTo(mw, QtCore.QPoint(0, 0)), w.grab())
        p.end()
    except Exception:
        note("grab failed: " + traceback.format_exc())
    return img


def _lum(c):
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_contrast(widget, panel_img=None, panel=None):
    """Contrast between a text widget's background (most common colour) and its ink
    (the pixel furthest from it), measured on the rendered panel."""
    if panel is None:
        panel = widget.window()
    if panel_img is None:
        panel_img = panel.grab().toImage()
    dpr = panel_img.devicePixelRatio() or 1.0
    top_left = widget.mapTo(panel, QtCore.QPoint(0, 0))
    r = QtCore.QRect(int(top_left.x() * dpr), int(top_left.y() * dpr),
                     int(widget.width() * dpr), int(widget.height() * dpr)).intersected(panel_img.rect())
    if r.width() < 2 or r.height() < 2:
        return None
    counts = {}
    step_px = max(1, int(dpr))
    pixels = []
    for y in range(r.top(), r.bottom() + 1, step_px):
        for x in range(r.left(), r.right() + 1, step_px):
            rgb = panel_img.pixel(x, y) & 0xFFFFFF
            counts[rgb] = counts.get(rgb, 0) + 1
            pixels.append(rgb)
    bg = QtGui.QColor(max(counts, key=counts.get))
    best = 1.0
    for rgb in set(pixels):
        best = max(best, contrast(bg, QtGui.QColor(rgb)))
    return round(best, 2)


def check_readable(name, root, minimum=3.0, limit=40):
    """Every visible label / check box / button text in ``root`` has ink-to-background contrast >= minimum.

    Measured on what the user sees: the composited main window for panels, the dialog for dialogs."""
    if root.window() is mw:
        pump(100)
        img, panel = composite(), mw
    else:
        img, panel = root.grab().toImage(), root
    bad = []
    n = 0
    kinds = (QtWidgets.QLabel, QtWidgets.QCheckBox, QtWidgets.QRadioButton, QtWidgets.QPushButton)
    for w in root.findChildren(QtWidgets.QWidget):
        if not isinstance(w, kinds) or not w.isVisible() or not w.text().strip():
            continue
        if w.width() < 4 or w.height() < 4:
            continue
        centre = w.mapTo(root, w.rect().center())
        top = root.childAt(centre)
        if w.visibleRegion().isEmpty() or (top is not None and top is not w and not w.isAncestorOf(top)):
            hidden_by = top.metaObject().className() if top is not None else "scroll clip"
            note(f"{name}: {w.text().strip()[:30]!r} ({type(w).__name__}) is 'visible' but covered by {hidden_by}")
            continue
        n += 1
        if n > limit:
            break
        c = text_contrast(w, img, panel)
        if c is not None and c < minimum:
            pos = w.mapTo(root, QtCore.QPoint(0, 0))
            bad.append(f"{w.text().strip()[:30]!r}={c} ({type(w).__name__} {w.objectName()!r} "
                       f"at {pos.x()},{pos.y()} {w.width()}x{w.height()} in {w.parentWidget().metaObject().className()})")
    return check(name, n > 0 and not bad, f"{n} texts; low contrast: {bad}" if bad else f"{n} texts")


# ----- documents ---------------------------------------------------------------------------


def wait_for(cond, ms=3000):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        pump(50)
    try:
        return bool(cond())
    except Exception:
        return False


def in_edit():
    doc = Gui.ActiveDocument
    vp = doc.getInEdit() if doc else None
    return getattr(vp, "Object", None)


def workspace_bar():
    try:
        import bruceworx_workspaces

        return bruceworx_workspaces.bar()
    except Exception:
        return None


def tab_names():
    bar = workspace_bar()
    if bar is None:
        return []
    return [bar.tabs.tabData(i) for i in range(bar.tabs.count())]


def view_size_text():
    """The status bar's view size text ("View 8.75 mm x 4.14 mm")."""
    for w in mw.statusBar().findChildren(QtWidgets.QWidget):
        t = getattr(w, "text", None)
        if callable(t):
            try:
                txt = t()
            except Exception:
                continue
            if isinstance(txt, str) and " x " in txt.replace("×", " x ") and "mm" in txt:
                return txt
    return None


def close_modal_dialogs():
    """Close any message box / dialog a step left open, report it."""
    found = []
    for w in QtWidgets.QApplication.topLevelWidgets():
        if isinstance(w, QtWidgets.QDialog) and w.isVisible() and w is not mw:
            found.append(f"{type(w).__name__}:{w.windowTitle()}")
            w.reject()
    return found


# ----- the 3D view: points and clicks --------------------------------------------------------


def _gv():
    return Gui.ActiveDocument.ActiveView.graphicsView()


def screen_of(vec):
    """Viewport (Qt, logical px) position of a global 3D point."""
    v = Gui.ActiveDocument.ActiveView
    gv = _gv()
    x, y = v.getPointOnScreen(vec)
    d = gv.devicePixelRatioF()
    return QtCore.QPoint(int(round(x / d)), int(round(gv.viewport().height() - y / d)))


def point_at(fx, fy):
    """Global 3D point under the viewport position (fx, fy) given as fractions of its size."""
    v = Gui.ActiveDocument.ActiveView
    gv = _gv()
    d = gv.devicePixelRatioF()
    vp = gv.viewport()
    return v.getPoint(int(fx * vp.width() * d), int((1 - fy) * vp.height() * d))


def _qtest():
    try:
        from PySide6.QtTest import QTest
    except ImportError:
        from PySide2.QtTest import QTest
    return QTest


def click_at(pt, button=None):
    """Move to and click a viewport position (QPoint, logical px)."""
    QTest = _qtest()
    vp = _gv().viewport()
    # A few moves first: tools update their preview (and preselection) on mouse moves.
    # (QTest.mouseMove does not reach the Coin viewer; a sent QMouseEvent does.)
    for dx in (6, 3, 0):
        mv = pt + QtCore.QPoint(dx, dx)
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(mv), QtCore.QPointF(vp.mapToGlobal(mv)),
                               QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(vp, ev)
        pump(40)
    try:
        pre = Gui.Selection.getPreselection()
        S["last_presel"] = (pt.x(), pt.y(), pre.ObjectName, pre.SubElementNames)
    except Exception:
        S["last_presel"] = (pt.x(), pt.y(), None)
    QTest.mouseClick(vp, button or QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pt)
    pump(120)


def click_frac(fx, fy):
    vp = _gv().viewport()
    click_at(QtCore.QPoint(int(fx * vp.width()), int(fy * vp.height())))


def click_vec(vec):
    click_at(screen_of(vec))


def key(k, widget=None):
    QTest = _qtest()
    QTest.keyClick(widget or _gv().viewport(), k)
    pump(150)


def escape():
    key(QtCore.Qt.Key_Escape)


# ----- runner ------------------------------------------------------------------------------


def _finish():
    try:
        with open(os.path.join(OUT, f"{SCRIPT}-{TAG}.json"), "w") as f:
            json.dump(results, f, indent=1)
        log(f"DONE {sum(r['ok'] for r in results)}/{len(results)} passed")
    finally:
        try:
            for name in list(App.listDocuments()):
                App.closeDocument(name)
        except Exception:
            pass
        QtCore.QTimer.singleShot(100, QtWidgets.QApplication.quit)


def run(delay=250):
    timeout = int(os.environ.get("SK_TIMEOUT", "150"))
    QtCore.QTimer.singleShot(timeout * 1000, lambda: (log(f"FAIL timeout: script ran over {timeout}s"), _finish()))
    queue = list(_steps)

    def next_step():
        if not queue:
            _finish()
            return
        fn = queue.pop(0)
        wait = delay
        log(f"-- {fn.__name__}")
        say((fn.__doc__ or fn.__name__.replace("s_", "", 1).replace("_", " ")).strip().splitlines()[0])
        try:
            r = fn()
            if isinstance(r, (int, float)):
                wait = int(r)
        except Exception:
            check(f"{fn.__name__} ran", False, traceback.format_exc().strip().splitlines()[-1])
            log(traceback.format_exc())
        left = close_modal_dialogs()
        if left:
            note(f"closed dialogs left open after {fn.__name__}: {left}")
        QtCore.QTimer.singleShot(wait, next_step)

    # Watchdog: a modal dialog nobody expects (an error box) would block the steps; log and close it.
    def watchdog():
        if S.get("expect_dialog"):
            return
        for w in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(w, QtWidgets.QDialog) and w.isVisible() and w.isModal():
                text = w.text() if isinstance(w, QtWidgets.QMessageBox) else ""
                check(f"no unexpected modal dialog ({w.windowTitle()!r})", False, text[:200])
                try:
                    w.grab().save(os.path.join(OUT, f"{TAG}-{SCRIPT}-unexpected-dialog.png"))
                except Exception:
                    pass
                w.reject()

    global _watchdog
    _watchdog = QtCore.QTimer()
    _watchdog.timeout.connect(watchdog)
    _watchdog.start(2000)
    # Let the main window settle (start page, workspace bar) first.
    QtCore.QTimer.singleShot(1500, next_step)
