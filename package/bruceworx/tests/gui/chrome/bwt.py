# SPDX-License-Identifier: LGPL-2.1-or-later
"""Harness for the window-chrome GUI checks (runs inside FreeCAD's GUI).

A check script defines ``main()`` and ends with ``bwt.start(main)``. ``main``
runs straight through and lets the event loop run with ``wait(ms)``
(QTest.qWait), so a check reads top to bottom like the clicks a user makes.

* ``check(id, ok, detail)`` records PASS/FAIL. Lines go to
  ``$BWT_OUT/<script>-<tag>.txt`` as they come (a crash keeps what ran).
* ``step(name)`` writes ``[bwtest] step <name>`` to the console, so collect.py
  can tell which step an error or warning in the FreeCAD log belongs to.
* ``shot(name)`` saves the window with the 3D view composited in
  (``mw.grab()`` leaves the OpenGL view blank).
* A watcher closes stray modal message boxes (logged as FAIL), and a watchdog
  ends the run if it hangs.

Env: BWT_OUT (folder), BWT_TAG (light|dark), BWT_SCRIPT (name),
BWT_FIXTURES (folder made by fixtures.py).
"""

import json
import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("BWT_OUT") or os.path.join(HERE, "out")
TAG = os.environ.get("BWT_TAG", "light")
SCRIPT = os.environ.get("BWT_SCRIPT", "chrome")
FIXTURES = os.environ.get("BWT_FIXTURES", "")
SHOTS = os.path.join(OUT, "shots")
os.makedirs(SHOTS, exist_ok=True)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PREFS = "User parameter:BaseApp/Preferences/BruceWorx"
APP_TOOLBARS = ("File", "Workbench", "View", "Edit", "Clipboard", "Macro", "Help", "Individual Views",
                "SearchBar", "BruceWorxSearchBar", "Structure")

_txt = open(os.path.join(OUT, f"{SCRIPT}-{TAG}.txt"), "w")
_log = open(os.path.join(OUT, f"{SCRIPT}-{TAG}.log"), "w")
RESULTS = []
_state = {"step": "", "allow_modal": False}


def mw():
    return Gui.getMainWindow()


def say(*args):
    _log.write(" ".join(str(a) for a in args) + "\n")
    _log.flush()


def check(cid, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"id": cid, "ok": ok, "detail": str(detail), "step": _state["step"]})
    _txt.write(f"{'PASS' if ok else 'FAIL'} {cid}" + (f": {detail}" if detail else "") + "\n")
    _txt.flush()
    return ok


def info(cid, detail):
    """A measurement worth reading, not a pass/fail."""
    RESULTS.append({"id": cid, "ok": None, "detail": str(detail), "step": _state["step"]})
    _txt.write(f"INFO {cid}: {detail}\n")
    _txt.flush()


def step(name, what=""):
    """Start a step: marks the log and narrates it in the agents' test log (what = one plain sentence)."""
    _state["step"] = name
    App.Console.PrintMessage(f"[bwtest] step {SCRIPT}:{name}\n")
    say("== step", name)
    try:
        import bruceworx_testwindow as tw

        tw.say(what or f"{SCRIPT} [{TAG}]: {name.replace('-', ' ')}")
    except Exception:
        pass


def expected(text):
    """A warning/error the test makes on purpose (collect.py skips lines with [bwtest-expected])."""
    return "[bwtest-expected] " + text


def wait(ms=300):
    QTest.qWait(ms)


def wait_until(fn, timeout=4000, poll=50):
    left = timeout
    while left > 0:
        try:
            if fn():
                return True
        except Exception:
            pass
        QTest.qWait(poll)
        left -= poll
    try:
        return bool(fn())
    except Exception:
        return False


# ----- widgets ----------------------------------------------------------------------------


def alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def by_class(name, root=None):
    root = root or mw()
    return [w for w in root.findChildren(QtWidgets.QWidget) if w.metaObject().className() == name]


def named(name, cls=QtWidgets.QWidget, root=None):
    return (root or mw()).findChild(cls, name)


def all_named(name, cls=QtWidgets.QWidget, root=None):
    return [w for w in (root or mw()).findChildren(cls) if w.objectName() == name]


def overlay(name):
    """Gui::OverlayTabWidget OverlayLeft / OverlayRight."""
    for w in by_class("Gui::OverlayTabWidget"):
        if w.objectName() == name:
            return w
    return None


def rect_in(w, root=None):
    root = root or mw()
    tl = w.mapTo(root, QtCore.QPoint(0, 0))
    return QtCore.QRect(tl, w.size())


def top_toolbars():
    """Visible toolbars in the main window's top area, sorted by row then x."""
    m = mw()
    out = []
    for tb in m.findChildren(QtWidgets.QToolBar):
        if tb.parent() is not m or not tb.isVisible():
            continue
        if m.toolBarArea(tb) != QtCore.Qt.TopToolBarArea:
            continue
        out.append(tb)
    out.sort(key=lambda t: (t.geometry().y(), t.geometry().x()))
    return out


def visible_actions(tb):
    acts = []
    for a in tb.actions():
        if a.isSeparator() or not a.isVisible():
            continue
        acts.append(a)
    return acts


def tool_buttons(tb):
    return [b for b in tb.findChildren(QtWidgets.QToolButton)
            if b.isVisible() and b.parent() is tb and b.objectName() != "qt_toolbar_ext_button"]


def ext_button(tb):
    for b in tb.findChildren(QtWidgets.QToolButton):
        if b.objectName() == "qt_toolbar_ext_button":
            return b
    return None


def text_clipped(w, text=None):
    """True when a label/button's text is wider than the room it has."""
    text = text if text is not None else (w.text() if hasattr(w, "text") else "")
    if not text:
        return False
    fm = QtGui.QFontMetrics(w.font())
    lines = text.split("\n")
    need = max(fm.horizontalAdvance(t) for t in lines)
    room = w.contentsRect().width()
    if isinstance(w, QtWidgets.QAbstractButton) and not w.icon().isNull():
        room -= w.iconSize().width() + 4
    return need > room + 1


def overlaps(rects):
    """Pairs (i, j) of intersecting rects (more than 1 px each way)."""
    out = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            r = rects[i].intersected(rects[j])
            if r.width() > 1 and r.height() > 1:
                out.append((i, j))
    return out


def max_contrast(widget):
    """Highest WCAG contrast between the widget's most common colour and any pixel in it.

    Readable text gives >= 4.5; text that disappears in its background gives ~1."""
    img = widget if isinstance(widget, QtGui.QImage) else widget.grab().toImage()
    if img.isNull() or img.width() < 2:
        return 0.0
    counts = {}
    step_x = max(1, img.width() // 120)
    step_y = max(1, img.height() // 40)
    pixels = []
    for y in range(0, img.height(), step_y):
        for x in range(0, img.width(), step_x):
            c = img.pixel(x, y) & 0xFFFFFF
            counts[c] = counts.get(c, 0) + 1
            pixels.append(c)
    for y in range(img.height()):  # every pixel of the middle row and column too (thin text)
        pixels.append(img.pixel(img.width() // 2, y) & 0xFFFFFF)
    for x in range(img.width()):
        pixels.append(img.pixel(x, img.height() // 2) & 0xFFFFFF)
    bg = max(counts, key=counts.get)
    lb = _lum(bg)
    best = 1.0
    for c in set(pixels):
        lc = _lum(c)
        hi, lo = max(lb, lc), min(lb, lc)
        best = max(best, (hi + 0.05) / (lo + 0.05))
    return round(best, 2)


def _lum(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def activate():
    """Bring the window forward; keyboard shortcuts only reach the active window.

    Returns whether it is active (other FreeCADs started meanwhile can take the focus)."""
    m = mw()
    m.raise_()
    m.activateWindow()
    return wait_until(lambda: QtWidgets.QApplication.activeWindow() is m, 1500)


def press(key, modifiers=QtCore.Qt.ControlModifier, widget=None):
    """A key press as the user makes it (Ctrl = Cmd on the Mac). Returns whether the window was active."""
    active = activate()
    QTest.keyClick(widget or QtWidgets.QApplication.focusWidget() or mw(), key, modifiers)
    return active


def check_key(cid, ok, active, detail=""):
    """A shortcut check: a miss while another app had the focus is not the product's fault."""
    if ok or active:
        return check(cid, ok, detail)
    info(cid, f"not checked: the window was not active ({detail})")
    return None


def narrow():
    """The window is narrower than a 13-inch laptop screen (test windows open on half the screen)."""
    return mw().width() < 1280


def check_wide(cid, ok, detail=""):
    """A fit check that only counts at laptop width or more; INFO in a narrow window."""
    if narrow() and not ok:
        info(cid, f"(window {mw().width()} px wide) {detail}")
        return None
    return check(cid, ok, detail)


def is_dark():
    c = mw().palette().color(QtGui.QPalette.Window)
    return c.lightness() < 128


# ----- screenshots ------------------------------------------------------------------------


def _views():
    """The 3D view of the active document tab (other tabs' views may report visible)."""
    area = mw().findChild(QtWidgets.QMdiArea)
    sub = area.activeSubWindow() if area is not None else None
    w = sub.widget() if sub is not None else None
    if w is not None and w.metaObject().className() == "Gui::View3DInventor" and w.isVisible():
        return [w]
    return []


def _parents(w):
    out = []
    p = w.parentWidget()
    while p is not None:
        out.append(p)
        p = p.parentWidget()
    return out


def shot(name, widget=None):
    """Save a screenshot as shots/<script>-<tag>-<name>.png and return its path.

    The whole window by default, with the active 3D view rendered in and the floating
    panels, navigation pill and toasts painted over it."""
    QtWidgets.QApplication.processEvents()
    path = os.path.join(SHOTS, f"{SCRIPT}-{TAG}-{name}.png")
    try:
        if widget is not None:
            widget.grab().save(path)
            return path
        m = mw()
        pix = m.grab()
        if os.environ.get("BWT_RAW"):
            pix.save(path.replace(".png", "-raw.png"))
        img = pix.toImage()
        dpr = pix.devicePixelRatio()
        painter = QtGui.QPainter(img)
        views = _views()
        active = None
        try:
            active = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
        except Exception:
            active = None
        for vw in views:
            if active is None or not hasattr(active, "saveImage"):
                break
            # grabFramebuffer() of the GL widget comes back empty (Coin draws elsewhere); saveImage
            # renders the scene but not the navigation cube.
            vimg = None
            if vimg is None:
                tmp = os.path.join(SHOTS, "_vp.png")
                try:
                    active.saveImage(tmp, int(vw.width() * dpr), int(vw.height() * dpr), "Current")
                except Exception:
                    break
                vimg, at = QtGui.QImage(tmp), vw
            vimg.setDevicePixelRatio(dpr)
            painter.drawImage(at.mapTo(m, QtCore.QPoint(0, 0)), vimg)
            for child in vw.findChildren(QtWidgets.QWidget):
                cn = child.metaObject().className()
                # Small floating children only (pill, toasts, cards): the view's own full-size
                # widgets grab blank and would cover the render.
                if (child.isVisible() and child.parentWidget() is vw and child.isWindow() is False
                        and child.width() < vw.width() * 0.8 and child.height() < vw.height() * 0.5
                        and "Quarter" not in cn and "GL" not in cn and "Viewer" not in cn):
                    painter.drawPixmap(child.mapTo(m, QtCore.QPoint(0, 0)), child.grab())
            break
        # Then everything that floats over the view, in stacking order (children() is bottom to top):
        # the overlay panels, the search results, ...
        painted = set()
        if views:
            vr = rect_in(views[0])
            for child in m.children():
                if not isinstance(child, QtWidgets.QWidget) or not child.isVisible() or child.isWindow():
                    continue
                if isinstance(child, (QtWidgets.QToolBar, QtWidgets.QStatusBar, QtWidgets.QMenuBar,
                                      QtWidgets.QDockWidget)) or child.isAncestorOf(views[0]):
                    continue
                if child.geometry().intersects(vr):
                    painter.drawPixmap(child.mapTo(m, QtCore.QPoint(0, 0)), child.grab())
                    painted.add(id(child))
        for ov in by_class("Gui::OverlayTabWidget"):  # in case they are not the window's own children
            if ov.isVisible() and id(ov) not in painted and not any(id(p) in painted for p in _parents(ov)):
                painter.drawPixmap(ov.mapTo(m, QtCore.QPoint(0, 0)), ov.grab())
        painter.end()
        img.save(path)
    except Exception:
        say("shot failed", name, traceback.format_exc())
    return path


# ----- documents --------------------------------------------------------------------------


def close_all():
    for d in list(App.listDocuments().values()):
        try:
            App.closeDocument(d.Name)
        except Exception:
            pass


def model_doc(name="Plate"):
    """The sample plate (pad, boss, hole, fillet) in a Part with part properties."""
    import bwmodel

    doc = App.newDocument(name)
    part, body, pad, boss, hole, fil = bwmodel.build(doc)
    try:
        from heuretes_freecad.partprops import load_schema

        load_schema().apply(part)
        part.PartNumber = "LE-01-01001"
        part.Revision = "02"
        part.Description = "Mounting plate, 80 x 50"
    except Exception:
        say("partprops", traceback.format_exc())
    doc.recompute()
    wait(300)
    try:
        Gui.ActiveDocument.ActiveView.viewIsometric()
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    for t in by_class("Gui::TreeWidget"):
        t.expandAll()
    return dict(doc=doc, part=part, body=body, pad=pad, boss=boss, hole=hole, fil=fil)


def param(path=PREFS):
    return App.ParamGet(path)


def set_bool(name, value, path=PREFS):
    App.ParamGet(path).SetBool(name, bool(value))


# ----- modal watcher / runner -------------------------------------------------------------


def allow_modal(on=True):
    _state["allow_modal"] = on


def _close_modals():
    if _state["allow_modal"]:
        return
    w = QtWidgets.QApplication.activeModalWidget()
    if w is None:
        return
    text = w.windowTitle()
    if isinstance(w, QtWidgets.QMessageBox):
        text += " | " + w.text()
    check("no-unexpected-dialog", False, f"modal '{w.metaObject().className()}' {text!r} closed by the harness")
    try:
        if isinstance(w, QtWidgets.QMessageBox):
            b = w.button(QtWidgets.QMessageBox.Cancel) or w.button(QtWidgets.QMessageBox.No) \
                or w.button(QtWidgets.QMessageBox.Ok)
            if b is not None:
                b.click()
                return
        w.reject() if hasattr(w, "reject") else w.close()
    except Exception:
        pass


def _finish():
    try:
        with open(os.path.join(OUT, f"{SCRIPT}-{TAG}.json"), "w") as f:
            json.dump(RESULTS, f, indent=1, default=str)
    except Exception:
        pass
    allow_modal(True)
    close_all()
    QtCore.QTimer.singleShot(200, QtWidgets.QApplication.quit)


def start(main, delay=4000, timeout_s=420):
    """Run main() once FreeCAD has settled; quit afterwards."""
    watcher = QtCore.QTimer(mw())
    watcher.setInterval(400)
    watcher.timeout.connect(_close_modals)
    watcher.start()

    def watchdog():
        check("watchdog", False, f"{SCRIPT} did not finish in {timeout_s}s (step {_state['step']})")
        _finish()
        QtCore.QTimer.singleShot(3000, lambda: os._exit(3))

    QtCore.QTimer.singleShot(timeout_s * 1000, watchdog)

    def run():
        try:
            main()
        except Exception:
            check("script-error", False, f"step {_state['step']}: " + traceback.format_exc().strip().splitlines()[-1])
            say(traceback.format_exc())
        step("end")
        _finish()

    QtCore.QTimer.singleShot(delay, run)
