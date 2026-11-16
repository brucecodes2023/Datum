# SPDX-License-Identifier: LGPL-2.1-or-later
"""The worksheet tab (roadmap MOD-15, plan step 4; MOD-17 by hand).

A ``.bwsheet`` opens as a document tab: its source on the left, the page on the right (Markdown text,
LaTeX math drawn by matplotlib's mathtext, every calc line with its value, checks and errors). Typing
re-evaluates (nothing is written); **Apply** (Cmd-Return) writes the ``=> Parameters`` lines into the
model, rebuilds it and rewrites the worksheet's output files. **Live** keeps the page following the
model when you change it in the Parameters tab or elsewhere. Save writes the file with its results in
the header. Export PDF prints the page (formulas as images; vector PDF is plan step 6).

Commands: New Worksheet (for the active document) and Open Worksheet. File > Open and a Finder
double-click open ``*.bwsheet`` here too (registered import type).

Off switch: Preferences/BruceWorx/Worksheet (default on).
"""

from __future__ import annotations

import html as _html
import io
import os
import re

import bruceworx_worksheet as bw

HERE = os.path.dirname(os.path.abspath(__file__))
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "Worksheet"
PAGE_PREFIX = "bwWorksheet:"
FILTER = "Design worksheet (*.bwsheet)"
EVAL_DELAY_MS = 300

COMMANDS = {
    "new": ("BruceWorx_NewWorksheet", "New Worksheet", "Heuretes_Worksheet.svg",
            "A design worksheet for the active document: calculations with units that read its Parameters "
            "and write results back (=> Parameters)"),
    "open": ("BruceWorx_OpenWorksheet", "Open Worksheet…", "Heuretes_WorksheetOpen.svg",
             "Open a design worksheet (.bwsheet)"),
}

GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu",
         "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega", "Gamma", "Delta",
         "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega"}

_state = {"pages": {}, "registered": False}


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


# --------------------------------------------------------------------------- text to show (no Qt)


def tex_name(name):
    """``T_out`` -> ``T_{out}``, ``sigma_F`` -> ``\\sigma_{F}``, ``alpha`` -> ``\\alpha``."""
    base, _sep, sub = name.partition("_")

    def word(w):
        return "\\" + w if w in GREEK else (w if len(w) == 1 else "\\mathrm{%s}" % w)

    out = word(base) if base else ""
    if sub:
        out += "_{%s}" % ",".join(word(p) if p in GREEK else p for p in sub.split("_") if p)
    return out


def template(describes="", title="Design worksheet"):
    return f"""---
bw-worksheet: 1
title: {title}
describes: {describes}
---
## Requirements
What the design must do. Math as in LaTeX: $F = m a$, or on its own line \\[ \\sigma = \\frac{{M}}{{W}} \\]

## Calculations
Names come from lines above, then from the model's Parameters sheet. Add `=> Parameters` to write a
result into the model; `in mm` shows it in a unit; `check` and `warn` test a condition.

```calc
```
"""


_MATH = re.compile(r"\\\[(.+?)\\\]|\$([^$\n]+)\$", re.S)
_VALUE = re.compile(r"\{=([A-Za-z_][A-Za-z0-9_]*)\}")


def text_markdown(text, results, formula):
    """A text block as Markdown with live values put in and math replaced by images
    (``formula(latex, display)`` gives the image's URL)."""
    values = {r.line.name: r for r in results if r.line.name}

    def value(m):
        r = values.get(m.group(1))
        if r is None:
            return f"**{{{m.group(1)}?}}**"
        return f"**{r.shown}**" if r.ok else f"**{{{m.group(1)}: error}}**"

    def math(m):
        display = m.group(1) is not None
        url = formula((m.group(1) or m.group(2)).strip(), display)
        return f"\n\n{token(url)}\n\n" if display else token(url)

    return _MATH.sub(math, _VALUE.sub(value, text))


def token(url):
    """A word that stands for a formula image through the Markdown conversion (which drops images it
    cannot load); ``images_in`` turns it back into an <img>."""
    return "BWMATHIMG" + url.replace(":", "Q").replace("-", "D") + "END"


def images_in(html_text, urls):
    for url in urls:
        html_text = html_text.replace(token(url), f'<img src="{url}"/>')
    return html_text


def calc_html(results, formula, colors):
    """The rows of one calc block as an HTML table."""
    rows = []
    for r in results:
        line = r.line
        name = f'<img src="{formula(tex_name(line.name), False)}"/>' if line.name else ""
        expr = _html.escape(line.expr)
        if r.status == "ok" and line.name:
            value = f'<b>{_html.escape(r.shown)}</b>'
        elif r.status == "ok":
            value = ""
        else:
            colour = colors["error"] if r.status == "error" else colors["muted"]
            value = f'<span style="color:{colour}">{_html.escape(r.message)}</span>'
        notes = []
        if line.export:
            notes.append(f'<span style="color:{colors["accent"]}">→ Parameters</span>')
        for kind, label, passed, message in r.checks:
            if message:
                notes.append(f'<span style="color:{colors["error"]}">{_html.escape(label)}: {_html.escape(message)}</span>')
            elif passed:
                notes.append(f'<span style="color:{colors["ok"]}">✓ {_html.escape(label)}</span>')
            else:
                colour = colors["warn"] if kind == "warn" else colors["error"]
                mark = "⚠" if kind == "warn" else "✗"
                notes.append(f'<span style="color:{colour}">{mark} {_html.escape(label)}</span>')
        reads = [n for n in r.inputs]
        if reads:
            notes.append(f'<span style="color:{colors["muted"]}">← model: {", ".join(map(_html.escape, reads))}</span>')
        rows.append(
            f'<tr><td style="padding:2px 6px" align="right">{name}</td>'
            f'<td style="padding:2px 6px" nowrap><code>{"= " + expr if line.name else _html.escape(line.source.strip())}</code></td>'
            f'<td style="padding:2px 10px" nowrap>{value}</td>'
            f'<td style="padding:2px 6px">{" &nbsp; ".join(notes)}</td></tr>')
    return '<table cellspacing="0" style="margin:6px 0 10px 0">' + "".join(rows) + "</table>"


def status_text(ws):
    """The link chip's text: the model, what is read and written, and errors."""
    model = ws.model() if ws.doc.header.get("describes") else None
    describes = str(ws.doc.header.get("describes") or "")
    reads = sorted({n for r in ws.results for n in r.inputs})
    writes = [r.line.name for r in ws.results if r.line.export]
    bad = sum(1 for r in ws.results if not r.ok)
    failed = sum(1 for r in ws.results for c in r.checks if not c[2] and c[0] == "check")
    if not describes:
        head = "Describes no model (add 'describes:' to the header)"
    elif model is None:
        head = f"Model {describes}: not open"
    else:
        head = f"Model {model.Label}"
    parts = [head, f"reads {len(reads)}", f"writes {len(writes)}"]
    if bad:
        parts.append(f"{bad} line{'s' if bad != 1 else ''} in error")
    if failed:
        parts.append(f"{failed} check{'s' if failed != 1 else ''} failed")
    return " · ".join(parts)


# --------------------------------------------------------------------------- formulas


def math_png(latex, color="#202020", dpi=200):
    """PNG bytes of a formula drawn by matplotlib's mathtext (a subset of LaTeX)."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import mathtext

    buf = io.BytesIO()
    mathtext.math_to_image(f"${latex}$", buf, dpi=dpi, format="png", color=color)
    return buf.getvalue()


# --------------------------------------------------------------------------- the tab


def _palette_colors():
    from PySide import QtGui, QtWidgets

    pal = QtWidgets.QApplication.instance().palette()
    text = pal.color(QtGui.QPalette.Text)
    dark = text.lightness() > 128
    return {
        "text": text.name(),
        "muted": "#8a8f98" if dark else "#6b7079",
        "error": "#ff6b6b" if dark else "#c62828",
        "warn": "#f2b632" if dark else "#a86b00",
        "ok": "#5cc98a" if dark else "#2e7d32",
        "accent": "#6aa9ff" if dark else "#1f5fbf",
    }


def _make_page_class():
    from PySide import QtCore, QtGui, QtWidgets

    class WorksheetPage(QtWidgets.QWidget):
        def __init__(self, ws, parent=None):
            super().__init__(parent)
            self.ws = ws
            self.setObjectName(PAGE_PREFIX + (ws.path or f"new-{id(ws)}"))
            self._formulas = {}  # (latex, color) -> url
            self._clean_text = ws.text if ws.path else None
            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            bar = QtWidgets.QHBoxLayout()
            bar.setContentsMargins(8, 6, 8, 6)
            self.apply_btn = QtWidgets.QPushButton("Apply to model")
            self.apply_btn.setToolTip("Write the => Parameters lines into the model, rebuild it and rewrite "
                                      "the output files (Cmd-Return)")
            self.save_btn = QtWidgets.QPushButton("Save")
            self.pdf_btn = QtWidgets.QPushButton("Export PDF…")
            self.live = QtWidgets.QCheckBox("Live")
            self.live.setToolTip("Follow the model: re-evaluate after it recomputes")
            self.live.setChecked(True)
            self.chip = QtWidgets.QLabel()
            self.chip.setObjectName("bwWorksheetChip")
            self.note = QtWidgets.QLabel()
            self.note.setObjectName("bwVaultHint")
            for w in (self.apply_btn, self.save_btn, self.pdf_btn, self.live):
                bar.addWidget(w)
            bar.addSpacing(12)
            bar.addWidget(self.chip)
            bar.addStretch(1)
            bar.addWidget(self.note)
            outer.addLayout(bar)
            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
            self.editor = QtWidgets.QPlainTextEdit(split)
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self.editor.setFont(font)
            self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.editor.setPlainText(ws.text)
            self.view = QtWidgets.QTextBrowser(split)
            self.view.setOpenExternalLinks(True)
            split.addWidget(self.editor)
            split.addWidget(self.view)
            split.setSizes([480, 640])
            outer.addWidget(split, 1)
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(EVAL_DELAY_MS)
            self.timer.timeout.connect(self.reevaluate)
            self.editor.textChanged.connect(self.timer.start)
            self.apply_btn.clicked.connect(self.apply)
            self.save_btn.clicked.connect(self.save)
            self.pdf_btn.clicked.connect(self.export_pdf)
            self.live.toggled.connect(self.set_live)
            for keys, slot in (("Ctrl+Return", self.apply), ("Ctrl+S", self.save), ("F9", self.reevaluate)):
                sc = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
                sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(slot)
            bw.listeners.append(self._live_changed)
            self.set_live(True)
            self.reevaluate()

        # ---- state

        def dirty(self):
            return self.editor.toPlainText() != (self._clean_text or "")

        def title(self):
            name = self.ws.title
            return ("Σ " + name) + (" •" if self.dirty() else "")

        def _retitle(self):
            sub = self.parentWidget()
            if isinstance(sub, QtWidgets.QMdiSubWindow):
                sub.setWindowTitle(self.title())

        def set_live(self, on):
            if on:
                bw.register(self.ws)
            elif self.ws in bw.open_worksheets():
                bw._state["open"].remove(self.ws)

        def _live_changed(self, ws):
            if ws is self.ws:
                self.render()

        def closing(self):
            try:
                bw.listeners.remove(self._live_changed)
            except ValueError:
                pass
            bw.unregister(self.ws)
            _state["pages"].pop(self.objectName(), None)

        # ---- actions

        def reevaluate(self):
            self.ws.set_text(self.editor.toPlainText())
            try:
                self.ws.evaluate()
            except Exception as exc:  # noqa: BLE001
                self.note.setText(f"Not evaluated: {exc}")
            self.render()
            self._retitle()

        def apply(self):
            import FreeCAD

            self.ws.set_text(self.editor.toPlainText())
            try:
                report = self.ws.apply()
            except bw.WorksheetError as exc:
                self.note.setText(str(exc))
                self.render()
                return None
            except Exception as exc:  # noqa: BLE001
                FreeCAD.Console.PrintError(f"BruceWorx worksheet: {exc}\n")
                self.note.setText(f"Not applied: {exc}")
                return None
            parts = []
            if report["written"]:
                parts.append("wrote " + ", ".join(report["written"]))
            if report["outputs"]:
                parts.append(f"{len(report['outputs'])} file{'s' if len(report['outputs']) != 1 else ''} rewritten")
            self.note.setText("; ".join(parts) or "The model already had these values.")
            self.render()
            return report

        def save(self):
            path = self.ws.path
            if not path:
                start = ""
                model = self.ws.model()
                if model is not None and model.FileName:
                    start = os.path.join(os.path.dirname(model.FileName),
                                         re.sub(r"[^\w.-]+", "_", self.ws.title) + bw.SUFFIX)
                path, _f = QtWidgets.QFileDialog.getSaveFileName(self, "Save Worksheet", start, FILTER)
                if not path:
                    return None
                if not path.endswith(bw.SUFFIX):
                    path += bw.SUFFIX
            self.ws.set_text(self.editor.toPlainText())
            self.ws.save(path)
            text = self.ws.text
            if text != self.editor.toPlainText():
                cursor_pos = self.editor.textCursor().position()
                self.editor.blockSignals(True)
                self.editor.setPlainText(text)
                cursor = self.editor.textCursor()
                cursor.setPosition(min(cursor_pos, len(text)))
                self.editor.setTextCursor(cursor)
                self.editor.blockSignals(False)
            self._clean_text = text
            _state["pages"].pop(self.objectName(), None)
            self.setObjectName(PAGE_PREFIX + self.ws.path)
            _state["pages"][self.objectName()] = self
            self.note.setText(f"Saved {os.path.basename(self.ws.path)}")
            self._retitle()
            return self.ws.path

        def export_pdf(self):
            start = os.path.splitext(self.ws.path)[0] + ".pdf" if self.ws.path else ""
            path, _f = QtWidgets.QFileDialog.getSaveFileName(self, "Export PDF", start, "PDF (*.pdf)")
            if not path:
                return None
            return self.write_pdf(path)

        def write_pdf(self, path):
            writer = QtGui.QPdfWriter(path)
            writer.setTitle(self.ws.title)
            writer.setResolution(300)
            self.view.document().print_(writer)
            self.note.setText(f"Exported {os.path.basename(path)}")
            return path

        # ---- drawing

        def _formula(self, latex, display, color):
            key = (latex, display, color)
            url = self._formulas.get(key)
            if url is not None:
                return url
            url = f"bwmath:{len(self._formulas)}"
            self._formulas[key] = url
            doc = self.view.document()
            ratio = max(1.0, self.devicePixelRatioF())
            try:
                png = math_png(latex, color, dpi=int((150 if display else 110) * ratio))
                image = QtGui.QImage.fromData(png, "PNG")
                image.setDevicePixelRatio(ratio)
            except Exception:  # noqa: BLE001 -- beyond mathtext: show the source
                image = QtGui.QImage()
            if image.isNull():
                self._formulas[key] = None
                return f"bwmath-bad:{_html.escape(latex)}"
            doc.addResource(QtGui.QTextDocument.ImageResource, QtCore.QUrl(url), image)
            return url

        def render(self):
            colors = _palette_colors()
            results = {id(r.line): r for r in self.ws.results}
            pieces = []
            for block in self.ws.doc.blocks:
                if block.kind == "calc":
                    rows = [results[id(line)] for line in block.lines if id(line) in results]
                    pieces.append(calc_html(rows, lambda t, d: self._formula(t, d, colors["text"]), colors))
                else:
                    md = text_markdown(block.text, self.ws.results, lambda t, d: self._formula(t, d, colors["text"]))
                    tmp = QtGui.QTextDocument()
                    tmp.setMarkdown(md)
                    body = tmp.toHtml()
                    m = re.search(r"<body[^>]*>(.*)</body>", body, re.S)
                    pieces.append(images_in(m.group(1) if m else body, [u for u in self._formulas.values() if u]))
            title = _html.escape(self.ws.title)
            scroll = self.view.verticalScrollBar().value()
            self.view.setHtml(f"<h1>{title}</h1>" + "".join(pieces))
            self.view.verticalScrollBar().setValue(scroll)
            self.chip.setText(status_text(self.ws))

    return WorksheetPage


_classes = {}


def page_class():
    if "page" not in _classes:
        _classes["page"] = _make_page_class()
    return _classes["page"]


def _mdi():
    import FreeCADGui
    from PySide import QtWidgets

    return FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)


def show(ws):
    """Open ``ws`` in a tab (or bring its tab forward). Returns the page widget."""
    from PySide import QtCore, QtWidgets

    key = PAGE_PREFIX + (ws.path or f"new-{id(ws)}")
    page = _state["pages"].get(key)
    mdi = _mdi()
    if page is not None:
        sub = page.parentWidget()
        if isinstance(sub, QtWidgets.QMdiSubWindow):
            mdi.setActiveSubWindow(sub)
        return page
    page = page_class()(ws)
    _state["pages"][page.objectName()] = page
    sub = mdi.addSubWindow(page)
    sub.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    sub.setWindowTitle(page.title())
    sub.installEventFilter(_close_filter())
    sub.showMaximized()
    mdi.setActiveSubWindow(sub)
    return page


def _close_filter():
    from PySide import QtCore, QtWidgets

    if "filter" in _state:
        return _state["filter"]

    class CloseFilter(QtCore.QObject):
        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Close and isinstance(obj, QtWidgets.QMdiSubWindow):
                page = obj.widget()
                if page is not None and hasattr(page, "dirty"):
                    if page.dirty() and not _state.get("closing_quietly"):
                        answer = QtWidgets.QMessageBox.question(
                            obj, "Close Worksheet", f"Save the changes to {page.ws.title}?",
                            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
                        if answer == QtWidgets.QMessageBox.Cancel or (
                                answer == QtWidgets.QMessageBox.Save and page.save() is None):
                            event.ignore()
                            return True
                    page.closing()
            return False

    _state["filter"] = CloseFilter()
    return _state["filter"]


def open_worksheet(path):
    """Open a .bwsheet file in a tab (the model it describes is opened hidden when it is not open)."""
    real = os.path.realpath(path)
    for page in list(_state["pages"].values()):
        if page.ws.path and os.path.realpath(page.ws.path) == real:
            return show(page.ws)
    return show(bw.Worksheet.load(path))


def new_worksheet(doc=None):
    """A new, unsaved worksheet describing ``doc`` (default: the active document)."""
    import FreeCAD

    doc = doc or FreeCAD.ActiveDocument
    describes = ""
    if doc is not None:
        describes = os.path.basename(doc.FileName) if doc.FileName else doc.Name
    ws = bw.Worksheet(template(describes, f"{doc.Label} design worksheet" if doc else "Design worksheet"),
                      model=doc)
    if doc is not None and doc.FileName:
        ws.path = ""  # unsaved; relative 'describes' resolves against the model's folder via _model
    return show(ws)


# --------------------------------------------------------------------------- File > Open


def register_type():
    import FreeCAD

    if "bruceworx_worksheet_gui" in (FreeCAD.getImportType("bwsheet") or []):
        return
    FreeCAD.addImportType(FILTER, "bruceworx_worksheet_gui")


def open(filename, *args):  # noqa: A001 -- the name FreeCAD's open handlers have
    return open_worksheet(filename)


def insert(filename, docname):
    return open_worksheet(filename)


# --------------------------------------------------------------------------- commands


def _choose_and_open():
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    start = ""
    doc = FreeCAD.ActiveDocument
    if doc is not None and doc.FileName:
        start = os.path.dirname(doc.FileName)
    path, _f = QtWidgets.QFileDialog.getOpenFileName(FreeCADGui.getMainWindow(), "Open Worksheet", start, FILTER)
    return open_worksheet(path) if path else None


class _Command:
    def __init__(self, key):
        self.key = key

    def GetResources(self):
        name, menu, icon, tip = COMMANDS[self.key]
        return {"Pixmap": os.path.join(HERE, icon), "MenuText": menu, "ToolTip": tip}

    def IsActive(self):
        import FreeCAD

        if not enabled():
            return False
        return self.key == "open" or FreeCAD.ActiveDocument is not None

    def Activated(self):
        if self.key == "new":
            new_worksheet()
        else:
            _choose_and_open()


def install():
    import FreeCADGui

    if not enabled():
        return
    for key, (name, *_rest) in COMMANDS.items():
        FreeCADGui.addCommand(name, _Command(key))
    register_type()
    bw.install()
