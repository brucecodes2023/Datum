"""Shared harness for the Drawings & Vault GUI checks (package/bruceworx/tests/gui/docs).

Each check script runs inside the FreeCAD GUI (``FreeCAD -u <cfg copy> <script>``), does its
steps one by one on QTimers, logs ``CHECK PASS|FAIL <name> | <detail>`` lines and quits itself.

Environment (run.sh sets it):
  BW_THEME   light | dark (only used in names; the cfg copy carries the theme)
  BW_OUT     output folder: logs/, shots/ and run/ (the temporary vaults). Never iCloud Drive,
             ~/Documents or ~/Desktop: the harness refuses those.
  BW_SCRIPT  the script's short name (log and shot names)

Vaults are made only under BW_OUT/run/. The cfg copy's VaultPath is cleared at start unless it
already points inside BW_OUT.
"""

import os
import shutil
import tempfile
import traceback

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

THEME = os.environ.get("BW_THEME", "light")
NAME = os.environ.get("BW_SCRIPT", "docs")
OUT = os.path.abspath(os.environ.get("BW_OUT") or os.path.join(tempfile.gettempdir(), "bruceworx-gui-docs"))
PREFS = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")


def _forbidden(path):
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(path)
    for bad in ("Library/Mobile Documents", "Documents", "Desktop"):
        root = os.path.join(home, bad)
        if real == root or real.startswith(root + os.sep):
            return True
    return False


if _forbidden(OUT):
    raise SystemExit(f"BW_OUT {OUT} is in iCloud Drive, Documents or Desktop: refused")

RUN = os.path.join(OUT, "run", f"{NAME}-{THEME}")
SHOTS = os.path.join(OUT, "shots")
LOGS = os.path.join(OUT, "logs")
shutil.rmtree(RUN, ignore_errors=True)
for _d in (RUN, SHOTS, LOGS):
    os.makedirs(_d, exist_ok=True)
LOG = open(os.path.join(LOGS, f"{NAME}-{THEME}.log"), "w", encoding="utf-8")
RESULTS = []  # (ok, name, detail)
# A crash leaves the Python stack of every thread here (faulthandler).
import faulthandler  # noqa: E402

FAULT = open(os.path.join(LOGS, f"{NAME}-{THEME}.fault"), "w")
faulthandler.enable(file=FAULT, all_threads=True)


def log(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()


def check(name, ok, *detail):
    text = " ".join(str(d) for d in detail)
    RESULTS.append((bool(ok), name, text))
    log("CHECK", "PASS" if ok else "FAIL", name, ("| " + text) if text else "")
    return bool(ok)


def note(*a):
    """A finding that is not a pass/fail check (looks-wrong notes, values seen)."""
    log("NOTE", *a)


# Vault safety: the cfg copy must not point at a real vault.
_vp = PREFS.GetString("VaultPath", "").strip()
if _vp and not os.path.realpath(os.path.expanduser(_vp)).startswith(os.path.realpath(OUT)) and \
        not (os.environ.get("BW_ALLOW_VAULT") and os.path.realpath(os.path.expanduser(_vp)).startswith(
            os.path.realpath(os.path.expanduser(os.environ["BW_ALLOW_VAULT"])))):
    log("NOTE VaultPath in the cfg copy pointed outside the test folder; cleared:", _vp)
    PREFS.SetString("VaultPath", "")


# Hard guard: no vault is made, opened for writing or moved outside OUT, whatever a step does.
# BW_ALLOW_VAULT: the one real vault folder a run may also write (the Leonardo project vault).
ALLOW = os.path.realpath(os.path.expanduser(os.environ["BW_ALLOW_VAULT"])) if os.environ.get("BW_ALLOW_VAULT") else ""


def _inside_out(path):
    real = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    for root in [os.path.realpath(OUT)] + ([ALLOW] if ALLOW else []):
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def _install_guards():
    try:
        from bruceworx import pdm_folder, vault_move, vault_setup
    except ImportError:
        return
    real_create = vault_setup.create_vault

    def create_vault(setup, *a, **k):
        if not _inside_out(setup.folder):
            check("SAFETY: vault only in the test folder", False, setup.folder)
            raise ValueError(f"test guard: refused to create a vault at {setup.folder}")
        return real_create(setup, *a, **k)

    vault_setup.create_vault = create_vault
    real_init = pdm_folder.FolderVault.init.__func__

    def init(cls, folder, *a, **k):
        if not _inside_out(folder):
            check("SAFETY: vault only in the test folder", False, folder)
            raise ValueError(f"test guard: refused to make a vault at {folder}")
        return real_init(cls, folder, *a, **k)

    pdm_folder.FolderVault.init = classmethod(init)
    real_plan = vault_move.plan_move

    def plan_move(root, destination, *a, **k):
        if not _inside_out(destination):
            check("SAFETY: vault moves only inside the test folder", False, destination)
            raise ValueError(f"test guard: refused to move a vault to {destination}")
        return real_plan(root, destination, *a, **k)

    vault_move.plan_move = plan_move


_install_guards()


# ----- steps -------------------------------------------------------------------------------------

_steps = []
SAY = {}  # step function name -> one sentence for the watcher's log (bruceworx_testwindow.say)


def say(text):
    """Narrate a step in the shared test log the user watches (does nothing outside a test run)."""
    log("SAY", text)
    try:
        import bruceworx_testwindow

        bruceworx_testwindow.say(text)
    except Exception:
        pass


def step(delay=700):
    """Decorator: run fn ``delay`` ms after the previous step. An exception is a FAIL."""
    def deco(fn):
        _steps.append((delay, fn))
        return fn
    return deco


def run(first_delay=None):
    def go_step(i):
        if i >= len(_steps):
            finish()
            return
        delay, fn = _steps[i]

        def go():
            try:
                log("--", fn.__name__)
                fn()
            except Exception:
                check(f"{fn.__name__} ran without an exception", False, traceback.format_exc().replace("\n", " / "))
            go_step(i + 1)

        QtCore.QTimer.singleShot(first_delay if (i == 0 and first_delay) else delay, go)

    QtCore.QTimer.singleShot(250, _modal_loop)
    go_step(0)


def report_lines():
    """The Report view's text, line by line."""
    w = mw().findChild(QtWidgets.QWidget, "Report view")
    edits = w.findChildren(QtWidgets.QTextEdit) if w is not None else []
    if w is not None and isinstance(w, QtWidgets.QTextEdit):
        edits = [w]
    return [ln for e in edits for ln in e.toPlainText().splitlines() if ln.strip()]


def finish():
    _state["stop"] = True
    lines = report_lines()
    with open(os.path.join(LOGS, f"{NAME}-{THEME}.report"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    for line in lines:
        low = line.lower()
        if "error" in low or "warn" in low or "traceback" in low or "exception" in low:
            log("REPORT", line)
    try:
        Gui.Control.closeDialog()
    except Exception:
        pass
    for d in list(FreeCAD.listDocuments().values()):
        try:
            Gui.getDocument(d.Name).Modified = False
        except Exception:
            pass
    passed = sum(1 for ok, _n, _d in RESULTS if ok)
    failed = len(RESULTS) - passed
    log("RESULT", f"{passed} passed, {failed} failed")
    LOG.close()
    QtCore.QTimer.singleShot(300, _quit)


def _quit():
    for d in list(FreeCAD.listDocuments().values()):
        try:
            FreeCAD.closeDocument(d.Name)
        except Exception:
            pass
    QtWidgets.QApplication.quit()


# ----- modal dialogs -------------------------------------------------------------------------------

_state = {"stop": False}
_plan = []  # [{"match", "button", "shot", "item"}]
MODALS = []  # [(title, text, [buttons], clicked)]


def answer(button, match="", shot=None, item=None):
    """The next modal whose text contains ``match`` gets ``button`` clicked (a QMessageBox), or for
    a QInputDialog the item containing ``item`` chosen (button "OK" / "Cancel")."""
    _plan.append({"match": match, "button": button, "shot": shot, "item": item})


def _take_plan(text):
    for i, p in enumerate(_plan):
        if p["match"] in text:
            return _plan.pop(i)
    return None


def _modal_loop():
    if _state["stop"]:
        return
    QtCore.QTimer.singleShot(250, _modal_loop)
    try:
        _handle_modal()
    except Exception:
        log("modal handler error", traceback.format_exc().replace("\n", " / "))


def _handle_modal():
    w = QtWidgets.QApplication.activeModalWidget()
    if w is None or not w.isVisible():
        return
    if isinstance(w, QtWidgets.QMessageBox):
        text = w.text() + ("\n" + w.informativeText() if w.informativeText() else "")
        buttons = [b.text().replace("&", "") for b in w.buttons()]
        plan = _take_plan(text)
        if plan and plan["shot"]:
            shot(plan["shot"], w)
        want = plan["button"] if plan else None
        button = next((b for b in w.buttons() if want and b.text().replace("&", "") == want), None)
        if button is None:
            for std in (QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Ok):
                button = w.button(std)
                if button is not None:
                    break
        if button is None:
            button = next((b for b in w.buttons() if w.buttonRole(b) == QtWidgets.QMessageBox.RejectRole), None)
        clicked = button.text().replace("&", "") if button is not None else "(reject)"
        MODALS.append((w.windowTitle(), text, buttons, clicked))
        log("MODAL", w.windowTitle(), "|", text.replace("\n", " / "), "| buttons", buttons, "->", clicked,
            "" if plan else "(unplanned)")
        button.click() if button is not None else w.reject()
    elif isinstance(w, QtWidgets.QInputDialog):
        plan = _take_plan(w.labelText())
        items = w.comboBoxItems()
        MODALS.append((w.windowTitle(), w.labelText(), items, ""))
        log("INPUT", w.windowTitle(), "|", w.labelText(), "| items", items)
        if plan and plan["shot"]:
            shot(plan["shot"], w)
        if plan and plan["button"] != "Cancel":
            if plan["item"] is not None:
                for it in items:
                    if plan["item"] in it:
                        w.setTextValue(it)
                        break
            w.accept()
        else:
            w.reject()
    elif isinstance(w, QtWidgets.QFileDialog):
        check("no unexpected file dialog", False, w.windowTitle())
        w.reject()
    elif isinstance(w, QtWidgets.QProgressDialog):
        pass
    elif isinstance(w, QtWidgets.QDialog):
        log("DIALOG", type(w).__name__, w.windowTitle(), "(left to the step)")


class fake_file_dialog:
    """Stands in for QFileDialog's static functions: the user picks ``path`` (or cancels with "")."""

    NAMES = ("getSaveFileName", "getOpenFileName", "getOpenFileNames", "getExistingDirectory")

    def __init__(self, path):
        self.path = path
        self.saved = {}
        self.asked = []

    def __enter__(self):
        for name in self.NAMES:
            self.saved[name] = getattr(QtWidgets.QFileDialog, name)

            def fake(*args, _name=name, **kwargs):
                self.asked.append((_name, args[1] if len(args) > 1 else ""))
                if _name == "getExistingDirectory":
                    return self.path
                if _name == "getOpenFileNames":
                    return ([self.path] if self.path else [], "")
                return (self.path, "")

            setattr(QtWidgets.QFileDialog, name, staticmethod(fake))
        return self

    def __exit__(self, *exc):
        for name, fn in self.saved.items():
            setattr(QtWidgets.QFileDialog, name, fn)
        return False


# ----- window, panels, screenshots ---------------------------------------------------------------


def mw():
    return Gui.getMainWindow()


def pump(ms=0):
    end = QtCore.QTime.currentTime().addMSecs(ms)
    QtWidgets.QApplication.processEvents()
    while ms and QtCore.QTime.currentTime() < end:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)


def shot(name, widget=None):
    path = os.path.join(SHOTS, f"{THEME}-{NAME}-{name}.png")
    try:
        (widget or mw()).grab().save(path)
        log("SHOT", path)
    except Exception as exc:
        log("SHOT failed", name, exc)
    return path


def tasks_dock():
    return mw().findChild(QtWidgets.QDockWidget, "Tasks")


def task_buttons():
    """The active task dialog's QDialogButtonBox (the one in the Tasks panel that is visible)."""
    dock = tasks_dock()
    if dock is None:
        return None
    boxes = [b for b in dock.findChildren(QtWidgets.QDialogButtonBox) if b.isVisible()]
    return boxes[0] if boxes else None


def task_button(role="ok"):
    box = task_buttons()
    if box is None:
        return None
    std = {"ok": QtWidgets.QDialogButtonBox.Ok, "cancel": QtWidgets.QDialogButtonBox.Cancel,
           "close": QtWidgets.QDialogButtonBox.Close, "apply": QtWidgets.QDialogButtonBox.Apply}[role]
    return box.button(std)


def task_content():
    """The widget inside the Tasks panel's scroll area (the task boxes), else the dock."""
    dock = tasks_dock()
    if dock is None:
        return None
    for area in dock.findChildren(QtWidgets.QScrollArea):
        if area.isVisible() and area.widget() is not None and area.widget().height() > 40:
            return area.widget()
    return dock


def task_shot(name):
    """The Tasks panel as shown, and its whole content (taller than the panel when it scrolls)."""
    dock = tasks_dock()
    shot(name, dock)
    if dock is not None:
        log("tasks panel geometry", dock.geometry().getRect(), "visible", dock.isVisible())
    shot(name + "-window")
    content = task_content()
    if content is not None and content is not dock:
        shot(name + "-content", content)


def find_text(root, text, cls=QtWidgets.QWidget):
    """Visible widgets under root whose text() contains ``text``."""
    out = []
    for w in root.findChildren(cls):
        try:
            t = w.text() if hasattr(w, "text") else ""
        except Exception:
            t = ""
        if t and text in t.replace("&", "") and w.isVisible():
            out.append(w)
    return out


def button(root, text):
    """The button showing ``text`` under root (visible ones first; a scrolled-away one counts)."""
    found = [b for b in find_text(root, text, QtWidgets.QAbstractButton)]
    if not found and root is not None:
        found = [b for b in root.findChildren(QtWidgets.QAbstractButton)
                 if text in b.text().replace("&", "") and b.isVisibleTo(root)]
    exact = [b for b in found if b.text().replace("&", "").strip() == text]
    return (exact or found or [None])[0]


def toolbar_actions(name):
    """(toolbar visible, {command name or text: (visible, enabled)}) of a toolbar named ``name``."""
    bars = [t for t in mw().findChildren(QtWidgets.QToolBar) if t.objectName() == name or t.windowTitle() == name]
    if not bars:
        return None, {}
    bar = bars[0]
    acts = {}
    for a in bar.actions():
        if a.isSeparator():
            continue
        key = a.objectName() or a.text().replace("&", "")
        acts[key] = (a.isVisible(), a.isEnabled())
        widget = bar.widgetForAction(a)
        if widget is not None:
            acts[key] = (a.isVisible() and widget.isVisible(), a.isEnabled())
    return bar.isVisible(), acts


def command_action(name):
    """The QAction of a FreeCAD command (Gui.Command.get(name).getAction())."""
    try:
        cmd = Gui.Command.get(name)
        acts = cmd.getAction() if cmd is not None else []
        return acts[0] if acts else None
    except Exception:
        return None


def command_enabled(name):
    """The command's QAction enabled state (never Command.isActive(): it can segfault)."""
    Gui.updateCommands() if hasattr(Gui, "updateCommands") else None
    pump()
    act = command_action(name)
    if act is None:  # only in the BruceWorx menu (bruceworx_menu.add_action): as the menu shows it
        found = [a for a in mw().findChildren(QtGui.QAction) if a.objectName() == "BruceWorxMenu_" + name]
        if found:
            for menu in mw().findChildren(QtWidgets.QMenu):
                if found[0] in menu.actions():
                    menu.aboutToShow.emit()
            return found[0].isEnabled()
    return act.isEnabled() if act is not None else None


# ----- looks -------------------------------------------------------------------------------------


def _lum(rgb):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_contrast(img, rect):
    """Contrast ratio of the text in rect of img: the most common colour is the background, the
    colour farthest from it (seen at least 3 times) the text."""
    rect = rect.intersected(img.rect())
    if rect.width() < 2 or rect.height() < 2:
        return None
    counts = {}
    step = max(1, int((rect.width() * rect.height()) ** 0.5 // 120))
    for y in range(rect.top(), rect.bottom() + 1, step):
        for x in range(rect.left(), rect.right() + 1, step):
            c = QtGui.QColor(img.pixel(x, y))
            key = (c.red() >> 2 << 2, c.green() >> 2 << 2, c.blue() >> 2 << 2)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    bg = max(counts, key=counts.get)
    best = 1.0
    for colour, n in counts.items():
        if n >= 3:
            best = max(best, _ratio(colour, bg))
    return best


def _visible_text(w):
    if not w.isVisible() or w.width() < 4 or w.height() < 4:
        return ""
    if isinstance(w, QtWidgets.QLabel):
        if w.pixmap() is not None and not w.pixmap().isNull():
            return ""
        return w.text()
    if isinstance(w, (QtWidgets.QAbstractButton,)):
        return w.text()
    return ""


def readability(container, min_ratio=3.0, name="", _again=True):
    """Check that every visible label and button text in container stands out from what is
    behind it (composited: the container is grabbed once). Returns the bad ones. A failing grab
    is taken again after a moment (panels slide in)."""
    if container is None:
        return []
    if _again:
        bad = readability(container, min_ratio, "", False)
        if not bad:
            return readability(container, min_ratio, name, False) if name else bad
        pump(800)
        return readability(container, min_ratio, name, False)
    pix = container.grab()
    img = pix.toImage()
    dpr = pix.devicePixelRatio() or 1.0
    bad = []
    seen = 0
    for w in container.findChildren(QtWidgets.QWidget):
        text = _visible_text(w)
        if not text or not text.strip():
            continue
        if not w.isVisibleTo(container):
            continue
        top_left = w.mapTo(container, QtCore.QPoint(0, 0))
        rect = QtCore.QRect(int(top_left.x() * dpr), int(top_left.y() * dpr), int(w.width() * dpr),
                            int(w.height() * dpr))
        ratio = text_contrast(img, rect)
        if ratio is None:
            continue
        seen += 1
        plain = QtGui.QTextDocumentFragment.fromHtml(text).toPlainText() if "<" in text else text
        if ratio < min_ratio and w.isEnabled():
            bad.append((plain.strip()[:40], round(ratio, 2)))
    if name:
        check(f"{name}: text readable ({seen} texts)", not bad, bad[:8])
    return bad


def clipped(container, name=""):
    """Labels (single line, plain) and buttons whose text does not fit their width."""
    out = []
    if container is None:
        return out
    for w in container.findChildren(QtWidgets.QWidget):
        if not w.isVisible() or not w.isVisibleTo(container):
            continue
        if isinstance(w, QtWidgets.QLabel):
            text = w.text()
            if not text or w.wordWrap() or "<" in text or "\n" in text or w.textFormat() == QtCore.Qt.RichText:
                continue
            need = w.fontMetrics().horizontalAdvance(text)
            have = w.contentsRect().width() - 2 * w.margin() - w.indent() if w.indent() > 0 else \
                w.contentsRect().width() - 2 * w.margin()
            if need > have + 2:
                out.append((text[:40], need, have))
        elif isinstance(w, (QtWidgets.QPushButton, QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
            text = w.text().replace("&", "")
            if not text:
                continue
            hint = w.sizeHint().width()
            if hint > w.width() + 4:
                out.append((text[:40], hint, w.width()))
    if name:
        check(f"{name}: no clipped text", not out, out[:8])
    return out


# ----- sample files --------------------------------------------------------------------------------


def make_part(path, label, description="", length=60, width=40, height=6, hole=True, props=True):
    """A part file: App::Part > Body > box (and a hole), with BruceWorx part properties."""
    stem = os.path.splitext(os.path.basename(path))[0]
    doc = FreeCAD.newDocument(stem.replace("-", "_").replace(" ", "_"))
    part = doc.addObject("App::Part", "Part")
    part.Label = label
    body = doc.addObject("PartDesign::Body", "Body")
    part.addObject(body)
    base = body.newObject("PartDesign::AdditiveBox", "Base")
    base.Length, base.Width, base.Height = length, width, height
    if hole:
        cut = body.newObject("PartDesign::SubtractiveCylinder", "Hole")
        cut.Radius, cut.Height = min(length, width) / 8.0, height + 4
        cut.Placement = FreeCAD.Placement(FreeCAD.Vector(length * 0.7, width / 2.0, -2), FreeCAD.Rotation())
    doc.recompute()
    if props:
        try:
            from bruceworx.partprops import load_schema

            load_schema(extra_layers=[], user_layer=False).apply(part)
            if description:
                part.Description = description
        except Exception as exc:
            log("NOTE part properties not added:", exc)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.saveAs(path)
    return doc, part


def make_assembly(path, label, parts):
    """An assembly file linking ``parts`` [(name, App::Part, x)] in other documents."""
    stem = os.path.splitext(os.path.basename(path))[0]
    doc = FreeCAD.newDocument(stem.replace("-", "_"))
    assy = doc.addObject("Assembly::AssemblyObject", "Assembly")
    assy.Label = label
    doc.saveAs(path)
    for name, target, x in parts:
        link = doc.addObject("App::Link", name)
        link.LinkedObject = target
        link.Placement.Base = FreeCAD.Vector(x, 0, 0)
        assy.addObject(link)
    doc.recompute()
    doc.save()
    return doc, assy


def close_all():
    for d in list(FreeCAD.listDocuments().values()):
        try:
            Gui.getDocument(d.Name).Modified = False
        except Exception:
            pass
        try:
            FreeCAD.closeDocument(d.Name)
        except Exception:
            pass
    pump()


def new_vault(name, sub="vaults", **numbering):
    """A vault made with the engine (no wizard) under RUN; made the open vault."""
    from bruceworx import vault_setup as vs

    folder = os.path.join(RUN, sub, name)
    vault = vs.create_vault(vs.VaultSetup(name, folder))
    PREFS.SetString("VaultPath", vault.root)
    return vault


def import_folder(root, src, program="BW"):
    """Import every .FCStd under src into the vault at root (the engine, with FreeCAD's writer)."""
    from bruceworx import freecad_io, vault_setup as vs

    cands = vs.scan_for_import(src)
    vs.plan_numbers(cands, root, program)
    from bruceworx.pdm_folder import FolderVault

    report = vs.run_import(FolderVault(root), cands, program, writer=freecad_io.write_part_number)
    return {os.path.basename(p): pn for p, pn in report.imported}, report


def setup_window():
    """A known window size for the screenshots, unless the launcher places test windows (BW_WINDOW)."""
    if os.environ.get("BW_WINDOW", "").strip():
        pump(100)
        return
    w = mw()
    w.showNormal()
    w.resize(1600, 1000)
    pump(100)


def wait_for(cond, ms=8000, step_ms=100):
    """Pump events until cond() is true or ms pass; returns cond()'s last value."""
    end = QtCore.QTime.currentTime().addMSecs(ms)
    value = cond()
    while not value and QtCore.QTime.currentTime() < end:
        pump(step_ms)
        value = cond()
    return value


def click(widget):
    """A left click in the middle of widget (press and release events, as a mouse would)."""
    pos = QtCore.QPointF(widget.width() / 2.0, widget.height() / 2.0)
    glob = QtCore.QPointF(widget.mapToGlobal(pos.toPoint()))
    for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
        buttons = QtCore.Qt.LeftButton if kind == QtCore.QEvent.MouseButtonPress else QtCore.Qt.NoButton
        event = QtGui.QMouseEvent(kind, pos, glob, QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(widget, event)
    pump()


def start_page():
    """The BruceWorx Start page (Start_Start opens it when it is not there)."""
    import bruceworx_start

    pages = bruceworx_start._pages()
    if not pages:
        Gui.runCommand("Start_Start")
        wait_for(lambda: bruceworx_start._pages(), 4000)
        pages = bruceworx_start._pages()
    page = pages[0] if pages else None
    if page is not None:
        mdi = mw().findChild(QtWidgets.QMdiArea)
        for sub in mdi.subWindowList() if mdi else []:
            if page in sub.findChildren(QtWidgets.QWidget):
                mdi.setActiveSubWindow(sub)
        page.refresh()
        pump(400)
    return page


def menu_action(menu, text):
    for a in menu.actions():
        if a.text().replace("&", "").replace("…", "...").startswith(text.replace("…", "...")):
            return a
    return None


def sample_files(src):
    """bracket.FCStd, pin.FCStd and hinge.FCStd (an assembly linking both) under src."""
    _d1, bracket = make_part(os.path.join(src, "bracket.FCStd"), "Hinge bracket", "Hinge bracket", 60, 40, 4)
    _d2, pin = make_part(os.path.join(src, "pin.FCStd"), "Hinge pin", "Hinge pin, 6 mm", 6, 6, 44, hole=False)
    make_assembly(os.path.join(src, "hinge.FCStd"), "Door hinge", [("Bracket", bracket, 0), ("Pin", pin, 30)])
    close_all()
