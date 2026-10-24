# SPDX-License-Identifier: LGPL-2.1-or-later
"""Harness for the assembly GUI checks (runs inside FreeCAD's GUI).

A script calls ``run(steps)`` with a generator: it does something, yields the
ms to let the event loop run, then checks. ``check(id, ok, detail)`` writes one
line "PASS id" / "FAIL id :: detail" to ``$BWT_OUT/<script>-<mode>.txt`` as it
goes, so a crash keeps what ran. The run ends with "END"; run.sh treats a
missing END as a crash. Message boxes a command opens with exec() are answered
by ``answer_modal`` (logged, grabbed, then the named button is clicked).

Env: BWT_OUT (output folder), BWT_MODE (light|dark), BWT_SCRIPT (name).
"""

import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
OUT = os.environ.get("BWT_OUT") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "bruceworx-gui-tests", "assembly")
MODE = os.environ.get("BWT_MODE", "light")
SCRIPT = os.environ.get("BWT_SCRIPT", "asm")
os.makedirs(OUT, exist_ok=True)
SHOTS = os.path.join(OUT, "shots")
os.makedirs(SHOTS, exist_ok=True)
WORK = os.path.join(OUT, "work", f"{SCRIPT}-{MODE}")
os.makedirs(WORK, exist_ok=True)

_txt = open(os.path.join(OUT, f"{SCRIPT}-{MODE}.txt"), "w")
_log = open(os.path.join(OUT, f"{SCRIPT}-{MODE}.log"), "w")
STATE = {"pass": 0, "fail": 0}
V = App.Vector


def say(*args):
    _log.write(" ".join(str(a) for a in args) + "\n")
    _log.flush()


def tell(text):
    """Narrate a step for the person watching ($A/testlog.txt); also in our log."""
    say("STEP", text)
    try:
        import bruceworx_testwindow as tw

        tw.say(text)
    except Exception:
        pass


def check(cid, ok, detail=""):
    ok = bool(ok)
    STATE["pass" if ok else "fail"] += 1
    line = f"{'PASS' if ok else 'FAIL'} {cid}" + (f" :: {detail}" if detail != "" and detail is not None else "")
    _txt.write(line + "\n")
    _txt.flush()
    say(line)
    return ok


def mw():
    return Gui.getMainWindow()


def process(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def by_class(name, root=None):
    root = root or mw()
    return [w for w in root.findChildren(QtWidgets.QWidget) if w.metaObject().className() == name]


def overlay(name):
    for w in by_class("Gui::OverlayTabWidget"):
        if w.objectName() == name:
            return w
    return None


# ----- screenshots ------------------------------------------------------------------------


def view3d():
    """The 3D view widget the user sees (the largest visible one)."""
    found = [w for w in by_class("Gui::View3DInventor") if w.isVisible()]
    found.sort(key=lambda w: w.width() * w.height(), reverse=True)
    return found[0] if found else None


def grab_window(name):
    """The main window with the 3D view rendered in (a plain grab leaves it blank)
    and the floating panels painted on top of it."""
    process()
    path = os.path.join(SHOTS, f"{SCRIPT}-{MODE}-{name}.png")
    try:
        win = mw()
        img = win.grab().toImage()
        dpr = img.devicePixelRatio() or 1.0
        vw = view3d()
        p = QtGui.QPainter(img)
        if vw is not None and Gui.ActiveDocument is not None:
            tmp = os.path.join(SHOTS, "_vp.png")
            try:
                Gui.ActiveDocument.ActiveView.saveImage(
                    tmp, int(vw.width() * dpr), int(vw.height() * dpr), "Current")
                vp = QtGui.QImage(tmp)
                say("viewport", vw.size(), vp.size(), dpr)
                vp.setDevicePixelRatio(dpr)
                p.drawImage(vw.mapTo(win, QtCore.QPoint(0, 0)), vp)
            except Exception as exc:
                say("viewport render failed", exc)
        for panel in by_class("Gui::OverlayTabWidget"):
            if panel.isVisible():
                p.drawPixmap(panel.mapTo(win, QtCore.QPoint(0, 0)), panel.grab())
        p.end()
        img.save(path)
        say("SHOT", path)
    except Exception:
        say("SHOT FAILED", name, traceback.format_exc())
    return path


def grab(widget, name):
    path = os.path.join(SHOTS, f"{SCRIPT}-{MODE}-{name}.png")
    try:
        process()
        widget.grab().save(path)
        say("SHOT", path)
    except Exception as exc:
        say("SHOT FAILED", name, exc)
    return path


# ----- task panel ---------------------------------------------------------------------------


def task_view():
    views = by_class("Gui::TaskView::TaskView")
    vis = [v for v in views if v.isVisible()]
    return (vis or views or [None])[0]


def grab_task(name):
    tv = task_view()
    right = overlay("OverlayRight")
    if right is not None and tv is not None and right.isAncestorOf(tv) and right.isVisible():
        return grab(right, name)
    return grab(tv or mw(), name)


def task_buttons():
    tv = task_view()
    if tv is None:
        return []
    out = []
    for box in tv.findChildren(QtWidgets.QDialogButtonBox):
        if box.isVisible():
            out.extend(b for b in box.buttons() if b.isVisible())
    return out


def press(text):
    """Click the visible task-dialog button with this text ('OK', 'Cancel')."""
    for b in task_buttons():
        if b.text().replace("&", "").strip().lower() == text.lower():
            b.click()
            return True
    say("no visible task button", text, [b.text() for b in task_buttons()])
    return False


def task_in_right_panel():
    tv = task_view()
    right = overlay("OverlayRight")
    left = overlay("OverlayLeft")
    if tv is None or right is None:
        return False, f"taskview={tv is not None} right={right is not None}"
    inside = right.isAncestorOf(tv)
    in_left = left is not None and left.isAncestorOf(tv)
    return inside and tv.isVisible() and not in_left, f"inside={inside} visible={tv.isVisible()} in_left={in_left}"


def python_panel(cls_name):
    """The live Python task panel object of a class name (gc search)."""
    import gc

    found = [o for o in gc.get_objects() if type(o).__name__ == cls_name]

    def live(o):
        form = getattr(o, "form", None)
        try:
            return form is not None and form.isVisible()
        except Exception:
            return False

    shown = [o for o in found if live(o)]
    return (shown or [None])[-1]


def answer_modal(button, ms=300, tries=40, name="modal", log_to=None):
    """Answer the next modal message box: log it, grab it, click `button`."""
    state = {"n": 0}

    def poll():
        m = QtWidgets.QApplication.activeModalWidget()
        if m is None:
            state["n"] += 1
            if state["n"] < tries:
                QtCore.QTimer.singleShot(ms, poll)
            return
        texts = [lab.text() for lab in m.findChildren(QtWidgets.QLabel) if lab.text()][:4]
        say("MODAL", m.metaObject().className(), repr(m.windowTitle()), texts)
        if log_to is not None:
            log_to.append((m.windowTitle(), texts))
        grab(m, name)
        for b in m.findChildren(QtWidgets.QAbstractButton):
            if b.text().replace("&", "").strip().lower() == button.lower():
                b.click()
                return
        try:
            m.reject()
        except Exception:
            m.close()

    QtCore.QTimer.singleShot(ms, poll)


# ----- toolbars -----------------------------------------------------------------------------


def toolbar(name):
    for bar in mw().findChildren(QtWidgets.QToolBar):
        if bar.objectName() == name or bar.windowTitle() == name:
            return bar
    return None


def command_action(bar, command):
    for a in bar.actions():
        if a.objectName() == command:
            return a
        if a.menu() is not None:
            for sub in a.menu().actions():
                if sub.objectName() == command:
                    return a
    return None


def action_enabled(command):
    """QAction enabled state of a command (never Command.isActive())."""
    try:
        cmd = Gui.Command.get(command)
        act = cmd.getAction() if cmd else None
        if act:
            return act[0].isEnabled()
    except Exception:
        pass
    return None


def trigger(command):
    """Run a command the way its toolbar button does (the QAction)."""
    try:
        act = Gui.Command.get(command).getAction()
        if act:
            act[0].trigger()
            return True
    except Exception:
        say("trigger failed", command, traceback.format_exc())
    Gui.runCommand(command)
    return True


# ----- Browser ------------------------------------------------------------------------------


def browser_trees():
    return [t for t in mw().findChildren(QtWidgets.QTreeWidget) if t.metaObject().className() == "Gui::TreeWidget"]


def expand_trees(doc=None):
    """Expand the Browser rows of one document (the active one), collapse the others."""
    doc = doc or App.ActiveDocument
    names = (doc.Label, doc.Name) if doc is not None else ()
    for t in browser_trees():
        tops = [t.topLevelItem(i) for i in range(t.topLevelItemCount())]
        if len(tops) == 1 and tops[0].text(0) not in names and tops[0].childCount():
            t.expandItem(tops[0])  # an "Application" root above the documents
            tops = [tops[0].child(j) for j in range(tops[0].childCount())]
        for top in tops:
            if top.text(0) in names:
                _expand(t, top, 4)
            else:
                t.collapseItem(top)


def _expand(tree, item, depth):
    if depth <= 0:
        return
    tree.expandItem(item)
    for k in range(item.childCount()):
        child = item.child(k)
        if child.text(0) in ("Origin",) or child.text(0).startswith("Origin"):
            continue
        _expand(tree, child, depth - 1)


def tree_items(label):
    out = []
    for t in browser_trees():
        out.extend(t.findItems(label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0))
    return out


def row_info(obj):
    hook = getattr(Gui, "BrowserRowInfo", None)
    try:
        if hook is not None:
            return hook(obj)
        import bruceworx_browser

        return bruceworx_browser.row_info(obj)
    except Exception as exc:
        return ("<error %s>" % exc, [])


def grab_browser(name):
    left = overlay("OverlayLeft")
    if left is not None and left.isVisible():
        return grab(left, name)
    trees = browser_trees()
    return grab(trees[0] if trees else mw(), name)


def report_text():
    out = []
    for w in QtWidgets.QApplication.allWidgets():
        if isinstance(w, QtWidgets.QTextEdit) and w.metaObject().className() == "Gui::DockWnd::ReportOutput":
            out.append(w.toPlainText())
    return "\n".join(out)


# ----- selection helpers --------------------------------------------------------------------


def pick(doc, root, sub, point):
    """A 3D-view pick: root object name in the document, subname, global point."""
    Gui.Selection.addSelection(doc.Name, root, sub, point.x, point.y, point.z)


def find_face(shape, pred):
    for i, f in enumerate(shape.Faces, 1):
        try:
            if pred(f):
                return f"Face{i}"
        except Exception:
            pass
    return None


def find_edge(shape, pred):
    for i, e in enumerate(shape.Edges, 1):
        try:
            if pred(e):
                return f"Edge{i}"
        except Exception:
            pass
    return None


# ----- run loop -----------------------------------------------------------------------------


def run(steps, start_ms=3500, close_docs=True):
    gen = steps()

    def finish():
        try:
            with open(os.path.join(OUT, f"{SCRIPT}-{MODE}-report.txt"), "w") as fh:
                fh.write(report_text())
        except Exception:
            pass
        _txt.write(f"END pass={STATE['pass']} fail={STATE['fail']}\n")
        _txt.flush()
        if close_docs:
            for name in list(App.listDocuments()):
                try:
                    App.closeDocument(name)
                except Exception:
                    pass
        QtCore.QTimer.singleShot(300, QtWidgets.QApplication.quit)

    def tick():
        try:
            wait = next(gen)
        except StopIteration:
            finish()
            return
        except Exception:
            check("script-exception", False, traceback.format_exc().replace("\n", " | "))
            finish()
            return
        QtCore.QTimer.singleShot(int(wait or 0), tick)

    QtCore.QTimer.singleShot(start_ms, tick)
