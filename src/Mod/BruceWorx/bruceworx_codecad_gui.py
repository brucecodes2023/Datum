# SPDX-License-Identifier: LGPL-2.1-or-later
"""The GUI of code-CAD parts (roadmap AUT-05; the parts themselves are bruceworx_codecad).

* Tree. A code part has its own icon, with a red mark when its script failed and an amber lock
  when the script waits for approval. Right-click: Edit Script, Rebuild, Approve Script,
  Convert to Solid. Double-click opens the script.
* Script editor. A dock at the bottom ("Code Part"): the script of the selected code part, with
  line numbers and Python colours. Run (Cmd/Ctrl+Return) replaces the part's script in one undo
  step; when the script fails nothing changes, the line is marked and the message says why.
  Revert goes back to the part's script. The editor follows the selection while it has no
  unsaved edits.
* New Code Part. BruceWorx > New Code Part… (and Solid > Create): pick a starter script
  (bruceworx_codecad_templates); the part is made and its script opens.
* Dimensions on the model. Selecting a code part shows its dimensions in the 3D view; double-click one to
  change it (bruceworx_codecad_dims).
* Approval. Opening a file whose code parts carry scripts not written on this Mac asks once:
  Review, Approve and Rebuild, or Not Now. Until approved the parts keep their saved shapes.

Off switch: Preferences/BruceWorx/CodeCad (bool, default on) turns the commands, the editor and
the prompt off; code parts in a file still load and keep their shapes.
"""

from __future__ import annotations

import os

import bruceworx_codecad as cc

HERE = os.path.dirname(os.path.abspath(__file__))
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ENABLED_KEY = "CodeCad"
USED_KEY = "CodeCadUsed"  # set once a code part exists: later sessions load the libraries early
DOCK = "BWCodeEditor"
DOCK_TITLE = "Code Part"
ICON = "Heuretes_CodePart.svg"
ICON_ERROR = "Heuretes_CodePartError.svg"
ICON_LOCKED = "Heuretes_CodePartLocked.svg"
ICON_REBUILD = "Heuretes_CodePartRebuild.svg"
NEW, EDIT, REBUILD, SOLID, APPROVE = ("BruceWorx_CodePartNew", "BruceWorx_CodePartEdit", "BruceWorx_CodePartRebuild",
                                      "BruceWorx_CodePartToSolid", "BruceWorx_CodePartApprove")
KEYWORDS = ("and as assert break class continue def del elif else except finally for from global if import in is "
            "lambda nonlocal not or pass raise return try while with yield None True False").split()

_state = {"installed": False, "observers": [], "dock": None, "editor": None, "classes": None, "asked": set(),
          "prompt": None}


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def enabled():
    return bool(_prefs().GetBool(ENABLED_KEY, True))


def _warn(text):
    import FreeCAD

    FreeCAD.Console.PrintWarning(f"BruceWorx code parts: {text}\n")


# ----- what the view provider proxy asks -------------------------------------------------------------


def state_of(obj):
    """"ok", "error" or "locked" for a code part."""
    status = getattr(obj, cc.STATUS, "") or ""
    if status.startswith("Error"):
        return "error"
    if status == cc.NOT_TRUSTED or (getattr(obj, cc.SCRIPT, "").strip() and not cc.trusted(getattr(obj, cc.SCRIPT))):
        return "locked"
    return "ok"


def icon_for(obj):
    name = {"error": ICON_ERROR, "locked": ICON_LOCKED}.get(state_of(obj) if obj is not None else "ok", ICON)
    return os.path.join(HERE, name)


def context_menu(obj, menu):
    if not enabled():
        return
    _QtCore, QtGui, _QtWidgets = _qt()

    def add(text, icon, run):
        action = QtGui.QAction(QtGui.QIcon(os.path.join(HERE, icon)), text, menu)
        action.triggered.connect(lambda _checked=False: run(obj))
        menu.addAction(action)

    add("Edit Script", ICON, edit)
    add("Rebuild", ICON_REBUILD, rebuild)
    if state_of(obj) == "locked":
        add("Approve Script", ICON_LOCKED, approve)
    add("Convert to Solid", ICON, to_solid)


# ----- actions -----------------------------------------------------------------------------------------


def selected_part():
    """The one selected code part, or None."""
    import FreeCADGui

    try:
        objects = FreeCADGui.Selection.getSelection()
    except Exception:
        return None
    parts = [o for o in objects if cc.is_code_part(o)]
    return parts[0] if len(parts) == 1 and len(objects) == 1 else None


def _transaction(doc, name, work):
    doc.openTransaction(name)
    try:
        out = work()
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return out


def edit(obj):
    """Open the script of a code part in the editor. True when it opened."""
    if not enabled() or not cc.is_code_part(obj):
        return False
    widget = editor()
    if widget is None:
        return False
    widget.load(obj, force=True)
    show_editor()
    return True


def rebuild(obj):
    _transaction(obj.Document, f"Rebuild: {obj.Label}", lambda: cc.rebuild(obj))
    _refresh(obj.Document)


def approve(obj):
    doc = obj.Document
    _transaction(doc, "Approve scripts", lambda: cc.trust_document(doc))
    _refresh(doc)


def to_solid(obj):
    return _transaction(obj.Document, f"Convert to solid: {obj.Label}", lambda: cc.to_solid(obj))


def _show_view(doc):
    """Bring the document's 3D view to the front (New Code Part from the Start page lands on the part)."""
    import FreeCADGui

    _QtCore, _QtGui, QtWidgets = _qt()
    try:
        import bruceworx_oncanvas

        gdoc = FreeCADGui.getDocument(doc.Name)
        views = gdoc.mdiViewsOfType("Gui::View3DInventor") if gdoc else []
        widget = bruceworx_oncanvas._view_widget(views[0]) if views else None
        while widget is not None and not isinstance(widget, QtWidgets.QMdiSubWindow):
            widget = widget.parentWidget()
        if widget is not None and widget.mdiArea() is not None:
            widget.mdiArea().setActiveSubWindow(widget)
        return views[0] if views else None
    except Exception:
        return None


def new_part(key, doc=None):
    """A new code part from the template ``key``; returns the parts. Opens the script when the GUI is on."""
    import FreeCAD
    import FreeCADGui

    import bruceworx_codecad_templates as templates

    doc = doc or FreeCAD.ActiveDocument or FreeCAD.newDocument("Unnamed")
    title = next(t for k, t, _d, _s in templates.templates() if k == key)

    def work():
        parts = cc.make(doc, templates.script(key))[0]
        if len(parts) == 1 and parts[0].Label.startswith("CodeCad"):
            parts[0].Label = title
        return parts

    parts = _transaction(doc, f"New code part: {title}", work)
    _prefs().SetBool(USED_KEY, True)
    try:
        view = _show_view(doc)
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(parts[0])
        if view is not None and hasattr(view, "fitAll") and len(doc.Objects) <= len(parts) + 1:
            view.fitAll()
    except Exception:
        pass
    edit(parts[0])
    return parts


def _refresh(doc):
    """Icons and the editor's status line after something changed."""
    for obj in cc.code_parts(doc):
        try:
            obj.ViewObject.signalChangeIcon()
        except Exception:
            pass
    widget = _state["editor"]
    if widget is not None:
        widget.refresh_status()


# ----- the editor ----------------------------------------------------------------------------------------


def _classes():
    if _state["classes"] is not None:
        return _state["classes"]
    QtCore, QtGui, QtWidgets = _qt()

    class Highlighter(QtGui.QSyntaxHighlighter):
        def __init__(self, document, dark):
            super().__init__(document)

            def fmt(light, night, bold=False, italic=False):
                out = QtGui.QTextCharFormat()
                out.setForeground(QtGui.QColor(night if dark else light))
                if bold:
                    out.setFontWeight(QtGui.QFont.Bold)
                out.setFontItalic(italic)
                return out

            rx = QtCore.QRegularExpression
            self.rules = [
                (rx(r"\b(?:" + "|".join(KEYWORDS) + r")\b"), fmt("#8a2be2", "#c792ea", bold=True)),
                (rx(r"\bparam(?:\.(?:length|angle|count|number))?\b|\bshow_object\b|\bresult\b"),
                 fmt("#0b6bcb", "#82aaff", bold=True)),
                (rx(r"\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?\b"), fmt("#b3541e", "#f78c6c")),
                (rx(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*'"), fmt("#1d7a3a", "#c3e88d")),
                (rx(r"#[^\n]*"), fmt("#7a8088", "#7f8c98", italic=True)),
            ]

        def highlightBlock(self, text):
            for pattern, style in self.rules:
                matches = pattern.globalMatch(text)
                while matches.hasNext():
                    match = matches.next()
                    self.setFormat(match.capturedStart(), match.capturedLength(), style)

    class Gutter(QtWidgets.QWidget):
        def __init__(self, edit_widget):
            super().__init__(edit_widget)
            self.edit = edit_widget

        def sizeHint(self):
            return QtCore.QSize(self.edit.gutter_width(), 0)

        def paintEvent(self, event):
            self.edit.paint_gutter(event)

    class CodeEdit(QtWidgets.QPlainTextEdit):
        run_requested = QtCore.Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            font.setPointSize(max(font.pointSize(), 12))
            self.setFont(font)
            self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
            self.error_line = None
            self.gutter = Gutter(self)
            self.blockCountChanged.connect(self._margins)
            self.updateRequest.connect(self._scroll_gutter)
            self._margins()

        def gutter_width(self):
            digits = max(2, len(str(max(1, self.blockCount()))))
            return 12 + self.fontMetrics().horizontalAdvance("9") * digits

        def _margins(self, *_args):
            self.setViewportMargins(self.gutter_width(), 0, 0, 0)

        def _scroll_gutter(self, rect, dy):
            if dy:
                self.gutter.scroll(0, dy)
            else:
                self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

        def resizeEvent(self, event):
            super().resizeEvent(event)
            area = self.contentsRect()
            self.gutter.setGeometry(QtCore.QRect(area.left(), area.top(), self.gutter_width(), area.height()))

        def paint_gutter(self, event):
            painter = QtGui.QPainter(self.gutter)
            palette = self.palette()
            painter.fillRect(event.rect(), palette.color(QtGui.QPalette.AlternateBase))
            block = self.firstVisibleBlock()
            number = block.blockNumber()
            top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + round(self.blockBoundingRect(block).height())
            height = self.fontMetrics().height()
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    bad = self.error_line == number + 1
                    painter.setPen(QtGui.QColor("#d43a2f") if bad else palette.color(QtGui.QPalette.PlaceholderText))
                    painter.drawText(0, top, self.gutter.width() - 6, height, QtCore.Qt.AlignRight, str(number + 1))
                block = block.next()
                top, bottom = bottom, bottom + round(self.blockBoundingRect(block).height())
                number += 1

        def mark_error(self, line):
            """Tint the failing line (None clears it) and bring it into view."""
            self.error_line = line
            selections = []
            if line:
                block = self.document().findBlockByNumber(line - 1)
                if block.isValid():
                    selection = QtWidgets.QTextEdit.ExtraSelection()
                    selection.format.setBackground(QtGui.QColor(212, 58, 47, 60))
                    selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
                    selection.cursor = QtGui.QTextCursor(block)
                    selections.append(selection)
                    cursor = QtGui.QTextCursor(block)
                    self.setTextCursor(cursor)
                    self.centerCursor()
            self.setExtraSelections(selections)
            self.gutter.update()

        def keyPressEvent(self, event):
            key, mods = event.key(), event.modifiers()
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and mods & (QtCore.Qt.ControlModifier
                                                                             | QtCore.Qt.MetaModifier):
                self.run_requested.emit()
                return
            if key == QtCore.Qt.Key_Tab and not self.textCursor().hasSelection():
                self.insertPlainText(" " * (4 - self.textCursor().positionInBlock() % 4))
                return
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                line = self.textCursor().block().text()[:self.textCursor().positionInBlock()]
                indent = line[:len(line) - len(line.lstrip(" "))]
                if line.rstrip().endswith(":"):
                    indent += "    "
                super().keyPressEvent(event)
                self.insertPlainText(indent)
                return
            super().keyPressEvent(event)

    class Editor(QtWidgets.QWidget):
        """The script of one code part. ``obj`` is the part it edits (None: nothing loaded)."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BWCodeEditorWidget")
            self.setWindowTitle(DOCK_TITLE)
            self.doc_name = self.obj_name = None
            self.loaded = ""
            self.title = QtWidgets.QLabel("No code part selected")
            self.title.setObjectName("BWCodeEditorTitle")
            self.status = QtWidgets.QLabel("")
            self.status.setObjectName("BWCodeEditorStatus")
            self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.run_button = QtWidgets.QPushButton("Run")
            self.run_button.setObjectName("BWCodeEditorRun")
            self.run_button.setToolTip("Run the script and update the part (Cmd/Ctrl+Return). One undo step.")
            self.revert_button = QtWidgets.QPushButton("Revert")
            self.revert_button.setObjectName("BWCodeEditorRevert")
            self.revert_button.setToolTip("Drop the edits and show the part's script again")
            self.text = CodeEdit(self)
            self.text.setObjectName("BWCodeEditorText")
            self.text.setPlaceholderText("Select a code part, or use BruceWorx > New Code Part…")
            dark = self.palette().color(QtGui.QPalette.Base).lightness() < 128
            self.highlighter = Highlighter(self.text.document(), dark)
            top = QtWidgets.QHBoxLayout()
            top.setContentsMargins(8, 6, 8, 2)
            top.addWidget(self.title)
            top.addStretch(1)
            top.addWidget(self.revert_button)
            top.addWidget(self.run_button)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            layout.addLayout(top)
            layout.addWidget(self.text, 1)
            bottom = QtWidgets.QHBoxLayout()
            bottom.setContentsMargins(8, 2, 8, 6)
            bottom.addWidget(self.status, 1)
            layout.addLayout(bottom)
            self.run_button.clicked.connect(lambda _checked=False: self.run())
            self.revert_button.clicked.connect(lambda _checked=False: self.revert())
            self.text.run_requested.connect(lambda: self.run())
            self.text.textChanged.connect(lambda: self._buttons())
            self._buttons()

        # the part being edited: looked up by name each time, never a kept reference to a deleted object
        def part(self):
            import FreeCAD

            if not self.doc_name:
                return None
            try:
                obj = FreeCAD.getDocument(self.doc_name).getObject(self.obj_name)
            except Exception:
                return None
            return obj if cc.is_code_part(obj) else None

        def dirty(self):
            return self.part() is not None and self.text.toPlainText() != self.loaded

        def _buttons(self):
            has = self.part() is not None
            self.run_button.setEnabled(has and bool(self.text.toPlainText().strip()))
            self.revert_button.setEnabled(self.dirty())
            self.text.setReadOnly(not has)

        def load(self, obj, force=False):
            """Show obj's script. Unsaved edits of another part are kept unless ``force``."""
            if obj is None or not cc.is_code_part(obj):
                return False
            same = (obj.Document.Name, obj.Name) == (self.doc_name, self.obj_name)
            if self.dirty() and not (force or same):
                return False
            if same and self.dirty():
                return True
            self.doc_name, self.obj_name = obj.Document.Name, obj.Name
            self.loaded = getattr(obj, cc.SCRIPT, "")
            self.text.setPlainText(self.loaded)
            self.text.mark_error(None)
            self.refresh_status()
            self._buttons()
            return True

        def clear(self):
            self.doc_name = self.obj_name = None
            self.loaded = ""
            self.text.setPlainText("")
            self.text.mark_error(None)
            self.refresh_status()
            self._buttons()

        def revert(self):
            obj = self.part()
            if obj is None:
                return
            self.loaded = getattr(obj, cc.SCRIPT, "")
            self.text.setPlainText(self.loaded)
            self.text.mark_error(None)
            self.refresh_status()
            self._buttons()

        def say(self, text, bad=False, tip=""):
            self.status.setText(text)
            self.status.setToolTip(tip)
            self.status.setStyleSheet("color: #d43a2f;" if bad else "")

        def refresh_status(self):
            obj = self.part()
            if obj is None:
                if self.doc_name:
                    self.clear()
                    return
                self.title.setText("No code part selected")
                self.say("")
                return
            shapes = len(cc.group(obj))
            self.title.setText(obj.Label if shapes == 1 else f"{obj.Label} (script of {shapes} parts)")
            status = getattr(obj, cc.STATUS, "")
            seconds = getattr(obj, cc.SECONDS, 0.0)
            if status == cc.UP_TO_DATE:
                dims = len(getattr(obj, cc.PARAMS, []) or [])
                took = f"ran in {seconds:.2f} s" if seconds else "from cache"
                self.say(f"Up to date · {dims} dimension{'s' if dims != 1 else ''} · {took}")
            else:
                self.say(status, bad=True)

        def run(self):
            """Replace the part's script with the text. True when the part was updated."""
            obj = self.part()
            if obj is None:
                return False
            code = self.text.toPlainText()
            doc = obj.Document
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                _transaction(doc, f"Edit script: {obj.Label}", lambda: cc.set_script(obj, code))
            except cc.ScriptError as exc:
                self.text.mark_error(exc.line)
                self.say(exc.short, bad=True, tip=exc.text)
                return False
            except cc.CodeCadError as exc:
                self.text.mark_error(None)
                self.say(str(exc), bad=True)
                return False
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.loaded = code
            self.text.mark_error(None)
            _refresh(doc)
            self._buttons()
            return True

        def selection_changed(self):
            obj = selected_part()
            if obj is not None and self.isVisible():
                self.load(obj)

    class TemplateDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            import bruceworx_codecad_templates as templates

            super().__init__(parent)
            self.setObjectName("BWCodePartNewDialog")
            self.setWindowTitle("New Code Part")
            self.items = templates.templates()
            self.list = QtWidgets.QListWidget()
            self.list.setObjectName("BWCodePartTemplates")
            for _key, title, _text, _script in self.items:
                self.list.addItem(QtWidgets.QListWidgetItem(QtGui.QIcon(os.path.join(HERE, ICON)), title))
            self.about = QtWidgets.QLabel("")
            self.about.setWordWrap(True)
            self.preview = QtWidgets.QPlainTextEdit()
            self.preview.setReadOnly(True)
            self.preview.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Create")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            right = QtWidgets.QVBoxLayout()
            right.addWidget(self.about)
            right.addWidget(self.preview, 1)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(self.list)
            row.addLayout(right, 1)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addLayout(row, 1)
            layout.addWidget(buttons)
            self.list.setFixedWidth(210)
            self.resize(820, 480)
            self.list.currentRowChanged.connect(self._show)
            self.list.itemDoubleClicked.connect(lambda _item: self.accept())
            self.list.setCurrentRow(0)

        def _show(self, row):
            if 0 <= row < len(self.items):
                _key, _title, text, script = self.items[row]
                self.about.setText(text + " Its dimensions go into the Parameters tab.")
                self.preview.setPlainText(script)

        def key(self):
            row = self.list.currentRow()
            return self.items[row][0] if 0 <= row < len(self.items) else None

    _state["classes"] = {"Editor": Editor, "CodeEdit": CodeEdit, "TemplateDialog": TemplateDialog}
    return _state["classes"]


def editor():
    """The editor widget, in its dock (made on first use, hidden until shown)."""
    import FreeCADGui

    if _state["editor"] is not None:
        return _state["editor"]
    if not enabled():
        return None
    QtCore, _QtGui, _QtWidgets = _qt()
    widget = _classes()["Editor"]()
    area = QtCore.Qt.BottomDockWidgetArea
    dock = FreeCADGui.addDockWindow(DOCK, widget, int(getattr(area, "value", area)))
    dock.setWindowTitle(DOCK_TITLE)
    dock.hide()
    _state["editor"], _state["dock"] = widget, dock
    return widget


def show_editor():
    if editor() is None:
        return
    dock = _state["dock"]
    first = not _state.get("sized")
    dock.show()
    dock.raise_()
    if first:  # a bottom dock starts a few lines tall: give the script room once, then it is the user's
        import FreeCADGui

        QtCore, _QtGui, _QtWidgets = _qt()
        window = FreeCADGui.getMainWindow()
        window.resizeDocks([dock], [max(260, window.height() // 3)], QtCore.Qt.Vertical)
        _state["sized"] = True
    obj = selected_part()
    if obj is not None:
        _state["editor"].load(obj)
    _state["editor"].text.setFocus()


# ----- approval of scripts from elsewhere ----------------------------------------------------------------


def prompt_text(doc, parts):
    count = len({getattr(p, cc.SCRIPT) for p in parts})
    what = "a code part whose script was" if len(parts) == 1 else f"{len(parts)} code parts whose scripts were"
    return (f"“{doc.Label}” has {what} not written on this Mac.",
            f"A script is a Python program and can do anything you can do on this Mac. Until you approve "
            f"{'it' if count == 1 else 'them'}, the parts keep the shapes saved in the file and do not follow "
            "their dimensions.")


def ask_approval(doc):
    """Ask once per document and session. Returns the dialog (or None when there is nothing to ask)."""
    import FreeCADGui

    if not enabled() or doc.Name in _state["asked"]:
        return None
    parts = cc.untrusted(doc)
    if not parts:
        return None
    _state["asked"].add(doc.Name)
    _refresh(doc)
    _QtCore, _QtGui, QtWidgets = _qt()
    box = QtWidgets.QMessageBox(FreeCADGui.getMainWindow())
    box.setObjectName("BWCodePartApproval")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    text, more = prompt_text(doc, parts)
    box.setText(text)
    box.setInformativeText(more)
    review = box.addButton("Review Scripts", QtWidgets.QMessageBox.ActionRole)
    approve_button = box.addButton("Approve and Rebuild", QtWidgets.QMessageBox.AcceptRole)
    later = box.addButton("Not Now", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(later)
    name = doc.Name

    def done(_result=0):
        import FreeCAD

        _state["prompt"] = None
        try:
            target = FreeCAD.getDocument(name)
        except Exception:
            return
        clicked = box.clickedButton()
        left = cc.untrusted(target)
        if clicked is approve_button and left:
            approve(left[0])
        elif clicked is review and left:
            _state["asked"].discard(name)  # asked again from the menu (Approve Script) once reviewed
            edit(left[0])

    box.finished.connect(done)
    box.open()  # window-modal, does not block the event loop
    _state["prompt"] = box
    return box


# ----- commands --------------------------------------------------------------------------------------------


class _Command:
    def __init__(self, icon, text, tip, run, needs_part=True):
        self.icon, self.text, self.tip, self.run, self.needs_part = icon, text, tip, run, needs_part

    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, self.icon), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        if not enabled():
            return False
        if not self.needs_part:
            return cc.available() is None
        part = selected_part()
        return part is not None and (self.run is not approve or state_of(part) == "locked")

    def Activated(self):
        try:
            self.run(selected_part()) if self.needs_part else self.run()
        except cc.CodeCadError as exc:
            _warn(str(exc))


def choose_template():
    """BruceWorx > New Code Part…: the dialog, then the part."""
    import FreeCADGui

    dialog = _classes()["TemplateDialog"](FreeCADGui.getMainWindow())
    if dialog.exec() and dialog.key():
        return new_part(dialog.key())
    return None


class _DocObserver:
    # FreeCAD has no "finished loading" slot for Python observers: an opened file is created, then activated.
    def slotCreatedDocument(self, doc):
        name = doc.Name
        _later(lambda: _restored(name))

    slotActivateDocument = slotCreatedDocument

    def slotChangedObject(self, obj, prop):
        if prop == cc.STATUS:
            name = obj.Document.Name
            _later(lambda: _changed(name))

    def slotDeletedDocument(self, doc):
        _state["asked"].discard(doc.Name)
        _later(lambda: _state["editor"] is not None and _state["editor"].refresh_status())

    def slotDeletedObject(self, _obj):
        _later(lambda: _state["editor"] is not None and _state["editor"].refresh_status())

    def slotUndoDocument(self, doc):
        name = doc.Name
        _later(lambda: _undone(name))

    slotRedoDocument = slotUndoDocument


class _SelectionObserver:
    def _changed(self, *_args):
        _later(lambda: _state["editor"] is not None and _state["editor"].selection_changed())

    addSelection = removeSelection = setSelection = clearSelection = _changed


def _later(work):
    QtCore, _QtGui, _QtWidgets = _qt()

    def run():
        try:
            work()
        except Exception as exc:  # an observer must never take the app down
            _warn(str(exc))

    QtCore.QTimer.singleShot(0, run)


def _doc(name):
    import FreeCAD

    try:
        return FreeCAD.getDocument(name)
    except Exception:
        return None


def _restored(name):
    doc = _doc(name)
    if doc is None or name in _state["asked"] or not cc.code_parts(doc):
        return
    _prefs().SetBool(USED_KEY, True)
    cc.preload()
    ask_approval(doc)


def _changed(name):
    doc = _doc(name)
    if doc is not None:
        _refresh(doc)


def _undone(name):
    """Undo or redo may have put back another script: show it unless there are edits."""
    doc = _doc(name)
    if doc is None:
        return
    _refresh(doc)
    widget = _state["editor"]
    if widget is not None and widget.part() is not None and not widget.dirty():
        widget.revert()
    elif widget is not None and widget.part() is not None and widget.loaded != getattr(widget.part(), cc.SCRIPT, ""):
        widget.loaded = getattr(widget.part(), cc.SCRIPT, "")
        widget._buttons()


def _shutdown():
    for remove, observer in _state["observers"]:
        try:
            remove(observer)
        except Exception:
            pass
    _state["observers"] = []


def install():
    """Register the commands, the menu entries and the observers. Safe to call twice."""
    import FreeCAD
    import FreeCADGui as Gui

    if _state["installed"]:
        return
    _state["installed"] = True
    if not enabled():
        return
    _QtCore, QtGui, QtWidgets = _qt()
    commands = (
        (NEW, _Command(ICON, "New Code Part…", "A part made by a build123d or CadQuery script, with its dimensions "
                       "in the Parameters tab", choose_template, needs_part=False)),
        (EDIT, _Command(ICON, "Edit Script", "Open the script of the selected code part", edit)),
        (REBUILD, _Command(ICON_REBUILD, "Rebuild Code Part", "Run the script of the selected code part again",
                           rebuild)),
        (APPROVE, _Command(ICON_LOCKED, "Approve Script", "Let the scripts of this document's code parts run on "
                           "this Mac", approve)),
        (SOLID, _Command(ICON, "Convert Code Part to Solid", "Keep the shape and drop the script", to_solid)),
    )
    for name, command in commands:
        Gui.addCommand(name, command)
    try:
        import bruceworx_menu

        window = Gui.getMainWindow()
        for name, command in commands[:2]:
            action = QtGui.QAction(QtGui.QIcon(os.path.join(HERE, command.icon)), command.text, window)
            action.triggered.connect(lambda _checked=False, n=name: Gui.runCommand(n))
            bruceworx_menu.add_action(action, f"BruceWorxMenu_{name}")
    except Exception as exc:
        _warn(f"menu entries were not added: {exc}")
    observer = _DocObserver()
    FreeCAD.addDocumentObserver(observer)
    _state["observers"].append((FreeCAD.removeDocumentObserver, observer))
    selection = _SelectionObserver()
    Gui.Selection.addObserver(selection)
    _state["observers"].append((Gui.Selection.removeObserver, selection))
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    if _prefs().GetBool(USED_KEY, False):
        cc.preload()
    try:
        import bruceworx_codecad_dims

        bruceworx_codecad_dims.install()
    except Exception as exc:
        _warn(f"dimensions on the model were not installed: {exc}")
