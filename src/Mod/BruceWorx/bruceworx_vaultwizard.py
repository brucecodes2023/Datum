# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault task dialogs: Create Vault, Import Files into Vault, Vault Settings.

Each runs in FreeCAD's task panel, like Pad or a joint: the boxes are task
boxes, OK and Cancel are the panel's buttons.

* Create Vault (BruceWorx → Create Vault…, the Start page, Cmd-K). Boxes:
  the vault's name and folder (the folder follows the name, under iCloud
  Drive's "BruceWorx Vaults" when there is one, until the user types or picks
  one); part numbering, fixed form PROG-DT-PPPPP-SSS-REV (programs, document
  types, first parent number and revision); company part properties. Create
  checks everything first; errors show in red in their box. The tables can
  be downloaded as one CSV file and loaded back.
* Import Files into Vault: finds .FCStd files, proposes numbers, and imports
  copies, one file per timer tick on the GUI thread (the part-number writer
  opens FreeCAD documents). Cancel stops after the current file. Offered
  right after a vault is created.
* Vault Settings: the open vault's programs, document types and company
  properties. Rows can be added and renamed, not deleted: a code in use is
  retired instead.

The engine is the hooks add-on's bruceworx/vault_setup.py; this module only
gathers the choices. The pure helpers at the top are tested in
TestBruceWorxVaultWizard.py.
"""

from __future__ import annotations

import copy
import os
import re
import threading
import time

COMMAND = "BruceWorx_CreateVault"
IMPORT_COMMAND = "BruceWorx_ImportIntoVault"
SETTINGS_COMMAND = "BruceWorx_VaultSettings"
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
DEFAULT_NAME = "My Vault"
# Schema types offered for a custom property (partprops.FREECAD_TYPES names).
CUSTOM_TYPES = (("string", "Text"), ("int", "Whole number"), ("enum", "List"))
TYPE_LABELS = {
    "string": "Text",
    "int": "Whole number",
    "integer": "Whole number",
    "float": "Number",
    "bool": "Yes / No",
    "enum": "List",
    "length": "Length",
    "mass": "Mass",
    "quantity": "Quantity",
}
KIND_LABELS = {"part": "Part", "assembly": "Assembly", "drawing": "Drawing"}
BUSY_TEXT = "Another command is open in the task panel. Close it (OK or Cancel) first."

_state = {"installed": False, "panel": None, "vault_check": ("", 0.0, False)}
_classes = None


# ----- pure helpers --------------------------------------------------------------


class FolderRule:
    """The vault folder follows the name until the user types a folder.

    ``suggest(name, parent)`` is vault_setup.suggest_folder. Choosing a folder
    with the picker sets the parent, so the folder follows the name again.
    """

    def __init__(self, parent, suggest):
        self.parent = parent
        self.suggest = suggest
        self.edited = False

    def folder(self, name):
        """The folder for ``name``, or None when the user's own folder stays."""
        if self.edited:
            return None
        return self.suggest((name or "").strip() or DEFAULT_NAME, self.parent)

    def typed(self):
        self.edited = True

    def choose(self, parent, name):
        self.parent = parent
        self.edited = False
        return self.folder(name)


def clean_rows(rows, what="code"):
    """Table rows (code, text) as cleaned pairs, plus issues.

    Codes are stripped and upper-cased; blank rows are dropped. A row with
    text but no code, and a code used twice, are errors. Issues are
    (level, text) tuples so this needs no engine.
    """
    pairs, issues, seen = [], [], set()
    for code, text in rows:
        code = (code or "").strip().upper()
        text = (text or "").strip()
        if not code and not text:
            continue
        if not code:
            issues.append(("error", f"“{text}” needs a {what}."))
            continue
        if code in seen:
            issues.append(("error", f"The {what} {code} is used twice."))
            continue
        seen.add(code)
        pairs.append((code, text))
    return pairs, issues


def parse_values(text):
    """List choices typed as "a, b, c": stripped, no blanks, no repeats."""
    out = []
    for value in (text or "").replace(";", ",").split(","):
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def property_entry(name, kind, values="", doc=""):
    """A part_properties.json entry for a custom property. Custom properties
    go on the BOM (decided for the user; the schema file can change it)."""
    entry = {"name": (name or "").strip(), "type": kind, "bom": True}
    if (doc or "").strip():
        entry["doc"] = doc.strip()
    if kind == "enum":
        choices = parse_values(values)
        entry["values"] = choices
        if choices:
            entry["default"] = choices[0]
    return entry


def edit_entry(entry, values="", doc=""):
    """A stored property entry with its description and (for a list) choices
    edited; the name and type stay."""
    out = dict(entry)
    if (doc or "").strip():
        out["doc"] = doc.strip()
    else:
        out.pop("doc", None)
    if out.get("type") == "enum":
        choices = parse_values(values)
        out["values"] = choices
        if choices and out.get("default") not in choices:
            out["default"] = choices[0]
    return out


def custom_issues(entries, taken=()):
    """Plain checks on custom entries before the schema sees them."""
    issues, seen = [], set(taken)
    for entry in entries:
        name = entry.get("name", "")
        if not name:
            issues.append(("error", "A custom property has no name."))
            continue
        if not name.isidentifier():
            issues.append(("error", f"“{name}” is not a property name: use letters, digits and _, no spaces."))
        if name in seen:
            issues.append(("error", f"There are two properties called {name}."))
        seen.add(name)
        if entry.get("type") == "enum" and not entry.get("values"):
            issues.append(("error", f"{name} is a list: type its choices, separated by commas."))
    return issues


def has_errors(issues):
    return any(_level(i) == "error" for i in issues)


def _level(issue):
    return issue[0] if isinstance(issue, tuple) else getattr(issue, "level", "info")


def _text(issue):
    return issue[1] if isinstance(issue, tuple) else getattr(issue, "text", str(issue))


def plural(count, word, words=None):
    return f"{count} {word if count == 1 else (words or word + 's')}"


def report_text(report):
    """One line for an ImportReport."""
    done = plural(len(report.imported), "file")
    rest = []
    if report.skipped:
        rest.append(f"{len(report.skipped)} skipped")
    if report.failed:
        rest.append(f"{len(report.failed)} failed")
    return f"Imported {done}." + (f" {', '.join(rest).capitalize()}." if rest else "")


def csv_file_name(vault_name):
    """The CSV download's default name: "<vault name> vault setup.csv"."""
    name = re.sub(r"[/\\:]", "-", (vault_name or "").strip()) or "Vault"
    return f"{name} vault setup.csv"


def downloads_dir(home=None):
    """~/Downloads, or the home folder when there is none."""
    home = home or os.path.expanduser("~")
    folder = os.path.join(home, "Downloads")
    return folder if os.path.isdir(folder) else home


def _code(program):
    return str(getattr(program, "code", program)).strip().upper()


def merge_settings(current, loaded):
    """``current`` with ``loaded``'s rows merged in (a loaded CSV in Vault Settings).

    A row in both takes the loaded text, type, choices and retired mark; a
    loaded row that is new is added; a current row the CSV leaves out stays.
    Neither argument is changed. Settings are vault_setup.VaultSettings (or
    anything with programs, document_types, properties and retired).
    """
    out = copy.deepcopy(current)
    programs = {_code(p): p for p in out.programs}
    for program in loaded.programs:
        if _code(program) in programs:
            programs[_code(program)].description = getattr(program, "description", "")
        else:
            out.programs.append(copy.deepcopy(program))
    types = {str(c).strip(): c for c in out.document_types}
    for code, name in loaded.document_types.items():
        out.document_types[types.get(str(code).strip(), code)] = name
    names = {e.get("name"): i for i, e in enumerate(out.properties)}
    for entry in loaded.properties:
        if entry.get("name") in names:
            out.properties[names[entry.get("name")]] = dict(entry)
        else:
            out.properties.append(dict(entry))
    shown = {
        "program": {_code(p) for p in loaded.programs},
        "document_type": {str(c).strip() for c in loaded.document_types},
        "property": {e.get("name") for e in loaded.properties},
    }
    retired = {}
    for table in set(current.retired) | set(loaded.retired) | set(shown):
        keep = set(current.retired.get(table, set())) - shown.get(table, set())
        retired[table] = keep | set(loaded.retired.get(table, set()))
    out.retired = retired
    return out


def settings_changes(old, new):
    """(rows added, rows changed) between two settings, over every table."""
    pairs = (
        ({_code(p): getattr(p, "description", "") for p in old.programs},
         {_code(p): getattr(p, "description", "") for p in new.programs}),
        (dict(old.document_types), dict(new.document_types)),
        ({e.get("name"): e for e in old.properties}, {e.get("name"): e for e in new.properties}),
    )
    added = changed = 0
    for before, after in pairs:
        for key, value in after.items():
            if key not in before:
                added += 1
            elif before[key] != value:
                changed += 1
    for table, codes in new.retired.items():
        changed += len(set(codes) ^ set(old.retired.get(table, set())))
    return added, changed


def _engine():
    from bruceworx import vault_setup

    return vault_setup


def _writer():
    """freecad_io.write_part_number, or None if the add-on has none yet."""
    try:
        from bruceworx import freecad_io

        return getattr(freecad_io, "write_part_number", None)
    except Exception:
        return None


def open_vault_root():
    """The open vault's folder (BruceWorx/VaultPath), or "" when it is not a vault."""
    import FreeCAD

    path = FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "").strip()
    if not path:
        return ""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    try:
        names = os.listdir(path)
    except OSError:
        return ""
    return path if any(n.endswith(".bwvault") for n in names) else ""


def _vault_open():
    """open_vault_root(), checked at most every two seconds (IsActive runs often)."""
    import FreeCAD

    path = FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "")
    last_path, when, ok = _state["vault_check"]
    now = time.monotonic()
    if path != last_path or now - when > 2.0:
        ok = bool(open_vault_root())
        _state["vault_check"] = (path, now, ok)
    return ok


class ImportJob:
    """vault_setup.run_import one file at a time, so the GUI thread can run it
    from a timer: step() imports the next file, stop() ends the import.

    The same steps as run_import: valid existing numbers are claimed first,
    parts and assemblies go before drawings, and each drawing is linked to
    the first imported part it shows.
    """

    def __init__(self, vs, vault, candidates, program, writer=None):
        from bruceworx import numbering  # noqa: PLC0415
        from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415
        from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

        self.vs = vs
        if not hasattr(vault, "create_item"):
            vault = FolderVault(vault)
        self.vault = vault
        self.program = numbering.normalize_program(program)
        self.writer = writer
        self.source = JsonNumberSource(vs.numbering_path(vault.root))
        data = self.source._load()
        self.types = vs._vault_document_types(self.source, data)
        self.report = vs.ImportReport()
        included = []
        for candidate in candidates:
            if candidate.include:
                included.append(candidate)
            else:
                reasons = [_text(i) for i in candidate.issues if _level(i) in ("error", "warning")]
                self.report.skipped.append((candidate.path, reasons[0] if reasons else "not selected"))
        kept = [key for key in (vs.existing_key(c.existing_part_number) for c in included) if key]
        if kept:
            for key in kept:
                vs._claim(data, key)
            self.source._save(data)
        self.ordered = [c for c in included if c.kind != "drawing"] + [c for c in included if c.kind == "drawing"]
        self.staging = os.path.join(vault.root, vs.STAGING_DIR)
        self.numbered = {}
        self.done = 0
        self.finished = False

    @property
    def total(self):
        return len(self.ordered)

    def step(self):
        """Import the next file; returns its path, or None when all are done."""
        if self.done >= len(self.ordered):
            self.finish()
            return None
        candidate = self.ordered[self.done]
        self.done += 1
        try:
            key = self.vs._import_one(self.vault, self.source, candidate, self.program, self.types,
                                      self.writer, self.staging, self.numbered)
        except self.vs._Skip as exc:
            self.report.skipped.append((candidate.path, str(exc)))
        except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the import
            self.report.failed.append((candidate.path, str(exc) or type(exc).__name__))
        else:
            self.report.imported.append((candidate.path, key))
        return candidate.path

    def stop(self):
        """Skip the files not imported yet."""
        self.report.skipped.extend((rest.path, "import stopped") for rest in self.ordered[self.done:])
        self.done = len(self.ordered)
        self.finish()

    def finish(self):
        if self.finished:
            return
        self.finished = True
        try:
            os.rmdir(self.staging)
        except OSError:
            pass


# ----- task panels ------------------------------------------------------------------


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    import FreeCAD
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets

    def box(title, icon=None):
        """A task box: the widget's title and icon become its header."""
        w = QtWidgets.QWidget()
        w.setWindowTitle(title)
        if icon:
            w.setWindowIcon(QtGui.QIcon(icon))
        return w

    def hint(text, parent=None):
        lab = QtWidgets.QLabel(text, parent)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        return lab

    def short(path):
        home = os.path.expanduser("~")
        return "~" + path[len(home):] if path.startswith(home) else path

    def table(headers):
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        model = t.model()
        model.rowsInserted.connect(lambda *_a: fit(t))
        model.rowsRemoved.connect(lambda *_a: fit(t))
        fit(t)
        return t

    def fit(t, least=2, most=7):
        """Height for the rows it has (least..most rows; more scroll)."""
        rows = min(max(t.rowCount(), least), most)
        row = t.verticalHeader().defaultSectionSize()
        height = t.horizontalHeader().sizeHint().height() + rows * row + 2 * t.frameWidth()
        if t.horizontalScrollBarPolicy() != QtCore.Qt.ScrollBarAlwaysOff:
            height += t.horizontalScrollBar().sizeHint().height() if t.columnCount() > 3 else 0
        t.setFixedHeight(height)

    def plus_minus(title, add, remove):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QtWidgets.QLabel(title))
        row.addStretch(1)
        for text, icon, tip, slot in (("+", "list-add", "Add a row", add),
                                      ("−", "list-remove", "Remove the selected row", remove)):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setIcon(QtGui.QIcon(f":/icons/{icon}.svg"))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        return row

    def message(title, text):
        QtWidgets.QMessageBox.information(Gui.getMainWindow(), title, text)

    def refresh_start():
        try:
            import bruceworx_start

            bruceworx_start._refresh_pages()
        except Exception:
            pass

    class Bridge(QtCore.QObject):
        scanned = QtCore.Signal(int, object)

    class IssueList(QtWidgets.QWidget):
        """Errors (red), warnings and notes at the foot of a box."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.lay = QtWidgets.QVBoxLayout(self)
            self.lay.setContentsMargins(0, 4, 0, 0)
            self.lay.setSpacing(2)
            self.hide()

        def set(self, issues):
            while self.lay.count():
                w = self.lay.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            for issue in issues:
                lab = QtWidgets.QLabel(_text(issue), self)
                lab.setObjectName("bwVaultIssue")
                lab.setProperty("level", _level(issue))
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                self.lay.addWidget(lab)
            self.setVisible(bool(issues))

    class NumberSpin(QtWidgets.QSpinBox):
        """The parent number, shown with its five digits."""

        def textFromValue(self, value):
            return f"{value:05d}"

    # ----- shared: code tables and property tables -----------------------------

    class CodeTable:
        """Code + text rows, optionally with a Retired column. Existing rows
        (Vault Settings) keep their code and cannot be removed."""

        def __init__(self, headers, retired=False, changed=None):
            self.retired = retired
            self.changed = changed or (lambda: None)
            self.widget = table(headers + (["Retired"] if retired else []))
            self.widget.setColumnWidth(0, 64)
            if retired:
                self.widget.horizontalHeader().setStretchLastSection(False)
                self.widget.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
                self.widget.setColumnWidth(2, 54)
            self.widget.itemChanged.connect(self._edited)

        def add(self, code="", text="", retired=False, existing=False):
            t = self.widget
            t.blockSignals(True)
            r = t.rowCount()
            t.insertRow(r)
            item = QtWidgets.QTableWidgetItem(code)
            item.setData(QtCore.Qt.UserRole, bool(existing))
            if existing:
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                item.setToolTip("A code cannot change once the vault uses it. Retire it instead.")
            t.setItem(r, 0, item)
            t.setItem(r, 1, QtWidgets.QTableWidgetItem(text))
            if self.retired:
                mark = QtWidgets.QTableWidgetItem("")
                mark.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
                mark.setCheckState(QtCore.Qt.Checked if retired else QtCore.Qt.Unchecked)
                t.setItem(r, 2, mark)
            t.blockSignals(False)
            return r

        def add_new(self):
            r = self.add()
            self.widget.setCurrentCell(r, 0)
            self.widget.editItem(self.widget.item(r, 0))
            self.changed()

        def remove(self):
            r = self.widget.currentRow()
            if r < 0:
                return
            if self.widget.item(r, 0).data(QtCore.Qt.UserRole):
                self.retire_hint()
                return
            self.widget.removeRow(r)
            self.changed()

        def retire_hint(self):
            message("Vault Settings", "Rows the vault already has cannot be deleted: tick Retired instead, "
                                      "so the code is no longer offered but old numbers keep their meaning.")

        def clear(self):
            self.widget.setRowCount(0)

        def _edited(self, item):
            if item.column() == 0 and item.text() != item.text().strip().upper():
                self.widget.blockSignals(True)
                item.setText(item.text().strip().upper())
                self.widget.blockSignals(False)
            self.changed()

        def rows(self):
            """(code, text, retired) per row."""
            out = []
            t = self.widget
            for r in range(t.rowCount()):
                code = t.item(r, 0).text() if t.item(r, 0) else ""
                text = t.item(r, 1).text() if t.item(r, 1) else ""
                mark = t.item(r, 2) if self.retired else None
                out.append((code, text, mark is not None and mark.checkState() == QtCore.Qt.Checked))
            return out

        def pairs(self, what):
            pairs, issues = clean_rows([(c, t) for c, t, _r in self.rows()], what)
            retired = {c.strip().upper() for c, _t, r in self.rows() if r and c.strip()}
            return pairs, retired, issues

    class PropertyTable:
        """Company properties. mode "create": suggested rows to tick plus
        custom rows; mode "settings": the vault's rows (name and type fixed,
        a Retired column) plus rows added now."""

        def __init__(self, mode, changed=None):
            self.mode = mode
            self.changed = changed or (lambda: None)
            # Retired before Description, so it shows in a narrow panel.
            headers = ["Property", "Type", "Choices", "Description"]
            self.doc_col, self.retired_col = 3, None
            if mode == "settings":
                headers = ["Property", "Type", "Choices", "Retired", "Description"]
                self.doc_col, self.retired_col = 4, 3
            self.widget = table(headers)
            t = self.widget
            t.setColumnWidth(0, 118 if mode == "create" else 104)
            t.setColumnWidth(1, 76)
            t.setColumnWidth(2, 72)
            t.horizontalHeader().setMinimumSectionSize(40)
            if mode == "settings":
                t.setColumnWidth(3, 54)
                t.setColumnWidth(4, 180)
            t.itemChanged.connect(lambda _i: self.changed())

        # A row's name item holds ("fixed", entry) or ("custom", None).
        def add(self, entry=None, checked=False, fixed=True, retired=False):
            t = self.widget
            t.blockSignals(True)
            r = t.rowCount()
            t.insertRow(r)
            custom = entry is None or not fixed
            entry = dict(entry or {"name": "", "type": "string"})
            name = QtWidgets.QTableWidgetItem(entry.get("name", ""))
            name.setData(QtCore.Qt.UserRole, None if custom else entry)
            flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if self.mode == "create":
                flags |= QtCore.Qt.ItemIsUserCheckable
            if custom:
                flags |= QtCore.Qt.ItemIsEditable
            name.setFlags(flags)
            if self.mode == "create":
                name.setCheckState(QtCore.Qt.Checked if (custom or checked) else QtCore.Qt.Unchecked)
            if entry.get("doc"):
                name.setToolTip(entry["doc"])
            t.setItem(r, 0, name)
            editable_doc = custom or self.mode == "settings"
            editable_values = custom or (self.mode == "settings" and entry.get("type") == "enum")
            choices = QtWidgets.QTableWidgetItem(", ".join(str(v) for v in entry.get("values", [])))
            doc = QtWidgets.QTableWidgetItem(entry.get("doc", ""))
            doc.setToolTip(entry.get("doc", ""))
            for item, editable in ((choices, editable_values), (doc, editable_doc)):
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                              | (QtCore.Qt.ItemIsEditable if editable else QtCore.Qt.NoItemFlags))
            t.setItem(r, 2, choices)
            t.setItem(r, self.doc_col, doc)
            if custom:
                combo = QtWidgets.QComboBox(t)
                for key, text in CUSTOM_TYPES:
                    combo.addItem(text, key)
                combo.setCurrentIndex(max(0, combo.findData(entry.get("type", "string"))))
                combo.currentIndexChanged.connect(lambda _i: self.changed())
                t.setCellWidget(r, 1, combo)
                t.setItem(r, 1, QtWidgets.QTableWidgetItem(""))
            else:
                kind = QtWidgets.QTableWidgetItem(TYPE_LABELS.get(entry.get("type"), entry.get("type", "")))
                kind.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                t.setItem(r, 1, kind)
            if self.mode == "settings":
                mark = QtWidgets.QTableWidgetItem("")
                mark.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
                mark.setCheckState(QtCore.Qt.Checked if retired else QtCore.Qt.Unchecked)
                t.setItem(r, self.retired_col, mark)
            t.blockSignals(False)
            return r

        def add_custom(self):
            r = self.add(None)
            self.widget.setCurrentCell(r, 0)
            self.widget.editItem(self.widget.item(r, 0))
            self.changed()

        def is_custom(self, r):
            item = self.widget.item(r, 0)
            return item is not None and item.data(QtCore.Qt.UserRole) is None

        def remove(self):
            r = self.widget.currentRow()
            if r < 0:
                return
            if not self.is_custom(r):
                if self.mode == "settings":
                    message("Vault Settings", "Properties the vault already has cannot be deleted: "
                                              "tick Retired instead.")
                else:
                    message("Create Vault", "Untick a suggested property to leave it out.")
                return
            self.widget.removeRow(r)
            self.changed()

        def clear(self):
            self.widget.setRowCount(0)

        def _cell(self, r, c):
            item = self.widget.item(r, c)
            return item.text() if item is not None else ""

        def read(self):
            """(fixed entries, custom entries, retired names); in create mode
            only ticked rows count."""
            fixed, custom, retired = [], [], set()
            t = self.widget
            for r in range(t.rowCount()):
                item = t.item(r, 0)
                if item is None:
                    continue
                if self.mode == "create" and item.checkState() != QtCore.Qt.Checked:
                    continue
                entry = item.data(QtCore.Qt.UserRole)
                if entry is not None:
                    if self.mode == "settings":
                        entry = edit_entry(entry, self._cell(r, 2), self._cell(r, self.doc_col))
                    fixed.append(dict(entry))
                    name = entry.get("name", "")
                else:
                    combo = t.cellWidget(r, 1)
                    new = property_entry(item.text(), combo.currentData() if combo else "string",
                                         self._cell(r, 2), self._cell(r, self.doc_col))
                    custom.append(new)
                    name = new["name"]
                mark = t.item(r, self.retired_col) if self.retired_col is not None else None
                if mark is not None and mark.checkState() == QtCore.Qt.Checked and name:
                    retired.add(name)
            return fixed, custom, retired

    # ----- base for the three panels ----------------------------------------------

    class Panel:
        title = ""

        def __init__(self):
            self.buttons = None
            self.released = False
            self._timer = QtCore.QTimer()
            self._timer.setSingleShot(True)
            self._timer.setInterval(150)
            self._timer.timeout.connect(self._revalidate)

        def changed(self):
            if not self.released:
                self._timer.start()

        def _revalidate(self):
            if not self.released:
                self.validate()

        def release(self):
            """The dialog is closing: stop the timers, and take the combo boxes
            the panel holds out of its boxes. With PySide 6.8, a held combo box
            deleted with its task box left every Python slot dead afterwards
            (no timer or button reached Python any more)."""
            _state["panel"] = None
            self.released = True
            self._timer.stop()
            for value in list(vars(self).values()):
                if isinstance(value, QtWidgets.QComboBox) and _alive(value):
                    value.hide()
                    value.setParent(None)
            return True

        def validate(self):
            return []

        def modifyStandardButtons(self, buttons):
            self.buttons = buttons
            self.setup_buttons()

        def setup_buttons(self):
            pass

        def ok_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Ok) if self.buttons is not None else None

        def cancel_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Cancel) if self.buttons is not None else None

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True

        def reject(self):
            return self.release()

        # CSV download and load, shared by Create Vault and Vault Settings.
        def _csv_buttons(self, parent_layout):
            row = QtWidgets.QHBoxLayout()
            down = QtWidgets.QPushButton("Download CSV template…")
            down.setToolTip("Save programs, document types and properties as one CSV file "
                            "to fill in a spreadsheet. It holds the rows shown here.")
            down.clicked.connect(self.download_csv)
            load = QtWidgets.QPushButton("Load CSV…")
            load.setToolTip("Read programs, document types and properties from a CSV file.")
            load.clicked.connect(self.load_csv)
            row.addWidget(down)
            row.addWidget(load)
            row.addStretch(1)
            parent_layout.addLayout(row)

        def download_csv(self, path=None):
            if not path:
                default = os.path.join(downloads_dir(), csv_file_name(self.vault_name()))
                path, _f = QtWidgets.QFileDialog.getSaveFileName(
                    Gui.getMainWindow(), "Download CSV template", default, "CSV files (*.csv)")
            if not path:
                return False
            try:
                self.vs.write_csv_file(path, self.current_settings())  # UTF-8 with BOM, for Excel
            except Exception as exc:
                self.csv_issues = [("error", f"The CSV file was not saved: {exc}")]
                self.validate()
                return False
            self.csv_issues = [("info", f"Saved {short(path)}.")]
            self.validate()
            return True

        def load_csv(self, path=None):
            if not path:
                path, _f = QtWidgets.QFileDialog.getOpenFileName(
                    Gui.getMainWindow(), "Load CSV", downloads_dir(), "CSV files (*.csv);;All files (*)")
            if not path:
                return False
            try:
                settings, issues = self.vs.read_csv_file(path)
            except Exception as exc:
                settings, issues = None, [("error", f"{os.path.basename(path)} could not be read: {exc}")]
            issues = list(issues)
            if not has_errors(issues):
                more = self.apply_settings(settings)
                issues += list(more)
            name = os.path.basename(path)
            if has_errors(issues):
                issues.insert(0, ("error", f"Nothing was loaded from {name}:"))
            else:
                issues.insert(0, ("info", f"Loaded {name}."))
            self.csv_issues = issues
            self.validate()
            return not has_errors(issues)

    # ----- Create Vault --------------------------------------------------------------

    class CreateVaultPanel(Panel):
        """Create Vault: name and folder, part numbering, company properties."""

        def __init__(self, folder="", name=""):
            super().__init__()
            self.vs = _engine()
            self.csv_issues = []
            self.created = ""
            self.prefilled = bool(folder)
            parent_dir = os.path.dirname(folder.rstrip(os.sep)) if folder else self.vs.default_parent_dir()
            self.rule = FolderRule(parent_dir, self.vs.suggest_folder)
            if folder:
                self.rule.typed()  # the picked folder stays
            self.form = [self._build_name(), self._build_numbering(), self._build_properties()]
            self.name_edit.setText(name or (os.path.basename(folder.rstrip(os.sep)) if folder else DEFAULT_NAME))
            if folder:
                self.folder_edit.setText(folder)
            self.name_edit.selectAll()
            self.validate()

        def setup_buttons(self):
            ok = self.ok_button()
            if ok is not None:
                ok.setText("Create")

        def vault_name(self):
            return self.name_edit.text().strip() or DEFAULT_NAME

        # --- name and location (the first box: its header is the panel's title)
        def _build_name(self):
            w = box("Create Vault", ":/icons/folder.svg")
            lay = QtWidgets.QVBoxLayout(w)
            if self.prefilled:
                lay.addWidget(hint("This folder is not a vault yet. Set one up in it here, or Cancel."))
            form = QtWidgets.QFormLayout()
            self.name_edit = QtWidgets.QLineEdit()
            self.name_edit.setPlaceholderText("e.g. Acme Products")
            self.name_edit.textChanged.connect(self._name_changed)
            form.addRow("Name", self.name_edit)
            row = QtWidgets.QHBoxLayout()
            self.folder_edit = QtWidgets.QLineEdit()
            self.folder_edit.textEdited.connect(lambda _t: self.rule.typed())
            self.folder_edit.textChanged.connect(lambda _t: self.changed())
            self.folder_edit.textChanged.connect(self.folder_edit.setToolTip)
            choose = QtWidgets.QPushButton("Choose…")
            choose.setToolTip("Pick the folder the vault folder is made in.")
            choose.clicked.connect(self._choose_folder)
            row.addWidget(self.folder_edit, 1)
            row.addWidget(choose)
            form.addRow("Folder", row)
            lay.addLayout(form)
            self.make_default = QtWidgets.QCheckBox("Make this the open vault")
            self.make_default.setChecked(True)
            self.make_default.setToolTip("The Start page and the part-number tools then use this vault.")
            lay.addWidget(self.make_default)
            self.name_issues = IssueList()
            lay.addWidget(self.name_issues)
            return w

        def _name_changed(self, text):
            folder = self.rule.folder(text)
            if folder is not None:
                self.folder_edit.setText(folder)
            self.changed()

        def _choose_folder(self):
            start = self.folder_edit.text().strip() or self.rule.parent
            while start and not os.path.isdir(start):
                start = os.path.dirname(start)
            picked = QtWidgets.QFileDialog.getExistingDirectory(
                Gui.getMainWindow(), "Where the vault folder goes", start or os.path.expanduser("~"))
            if picked:
                self.folder_edit.setText(self.rule.choose(picked, self.name_edit.text()))

        def _name_issues(self):
            issues = []
            if not self.name_edit.text().strip():
                issues.append(("error", "Give the vault a name."))
            folder = self.folder_edit.text().strip()
            if not folder:
                issues.append(("error", "Pick the folder for the vault."))
                return issues
            try:
                issues += list(self.vs.check_location(os.path.expanduser(folder)))
            except Exception as exc:
                issues.append(("error", f"The folder cannot be checked: {exc}"))
            return issues

        # --- part numbering
        def _build_numbering(self):
            defaults = self.vs.NumberingSetup()
            w = box("Part numbering")
            lay = QtWidgets.QVBoxLayout(w)
            lay.addWidget(hint("Every part gets PROG-DT-PPPPP-SSS-REV: program, document type, parent "
                               "number, sub number and revision."))
            self.programs = CodeTable(["Code", "Description"], changed=self._edited)
            for p in defaults.programs:
                self.programs.add(p.code, p.description)
            lay.addLayout(plus_minus("Programs", self.programs.add_new, self.programs.remove))
            lay.addWidget(self.programs.widget)
            self.types = CodeTable(["Code", "Name"], changed=self._edited)
            for code, name in defaults.document_types.items():
                self.types.add(code, name)
            lay.addLayout(plus_minus("Document types", self.types.add_new, self.types.remove))
            lay.addWidget(self.types.widget)
            form = QtWidgets.QFormLayout()
            self.parent_spin = NumberSpin()
            self.parent_spin.setRange(1, 99999)
            self.parent_spin.setValue(int(defaults.first_parent))
            self.parent_spin.valueChanged.connect(lambda _v: self.changed())
            form.addRow("First parent number", self.parent_spin)
            self.revision_edit = QtWidgets.QLineEdit(str(defaults.first_revision))
            self.revision_edit.setMaxLength(3)
            self.revision_edit.textEdited.connect(self._revision_edited)
            form.addRow("First revision", self.revision_edit)
            self.example_label = QtWidgets.QLabel("")
            self.example_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            form.addRow("First number", self.example_label)
            lay.addLayout(form)
            self._csv_buttons(lay)
            self.numbering_issues = IssueList()
            lay.addWidget(self.numbering_issues)
            return w

        def _edited(self):
            self.csv_issues = []
            self.changed()

        def _revision_edited(self, text):
            if text != text.upper():
                pos = self.revision_edit.cursorPosition()
                self.revision_edit.setText(text.upper())
                self.revision_edit.setCursorPosition(pos)
            self.changed()

        def numbering(self):
            """The NumberingSetup from the tables, plus issues found reading them."""
            programs, _r, issues = self.programs.pairs("program code")
            types, _r, more = self.types.pairs("document type code")
            setup = self.vs.NumberingSetup(
                programs=[self.vs.ProgramSetup(c, d) for c, d in programs],
                document_types=dict(types),
                first_parent=int(self.parent_spin.value()),
                first_revision=self.revision_edit.text().strip(),
            )
            return setup, issues + more

        def _numbering_issues(self):
            setup, issues = self.numbering()
            if not issues:
                try:
                    issues += list(self.vs.validate_numbering(setup))
                except Exception as exc:
                    issues.append(("error", str(exc)))
            example = ""
            if not has_errors(issues):
                try:
                    example = self.vs.example_number(setup)
                except Exception as exc:
                    issues.append(("error", str(exc)))
            self.example_label.setText(example or "—")
            return issues

        # --- company part properties
        def _build_properties(self):
            w = box("Company part properties")
            lay = QtWidgets.QVBoxLayout(w)
            lay.addWidget(hint("Every part already has P/N, revision, state, description, material and "
                               "mass. Tick what your company also tracks, or add your own."))
            self.props = PropertyTable("create", changed=self.changed)
            try:
                for entry in self.vs.suggested_properties():
                    self.props.add(entry)
            except Exception as exc:
                lay.addWidget(hint(f"No suggestions: {exc}"))
            lay.addWidget(self.props.widget)
            row = QtWidgets.QHBoxLayout()
            add = QtWidgets.QPushButton("Add Custom")
            add.clicked.connect(self.props.add_custom)
            remove = QtWidgets.QPushButton("Remove")
            remove.setToolTip("Remove the selected custom property")
            remove.clicked.connect(self.props.remove)
            row.addWidget(add)
            row.addWidget(remove)
            row.addStretch(1)
            lay.addLayout(row)
            self.property_issues = IssueList()
            lay.addWidget(self.property_issues)
            return w

        def properties(self):
            fixed, custom, _retired = self.props.read()
            return fixed, custom

        def _properties_issues(self):
            chosen, custom = self.properties()
            issues = custom_issues(custom, [e.get("name", "") for e in chosen])
            if not has_errors(issues) and chosen + custom:
                try:
                    issues += list(self.vs.validate_properties(chosen + custom))
                except Exception as exc:
                    issues.append(("error", str(exc)))
            return issues

        # --- CSV
        def current_settings(self):
            numbering, _issues = self.numbering()
            chosen, custom = self.properties()
            return self.vs.settings_from_setup(numbering, chosen + custom)

        def apply_settings(self, settings):
            numbering, _issues = self.numbering()
            numbering, properties = self.vs.apply_to_setup(settings, numbering)
            self.programs.clear()
            for p in numbering.programs:
                self.programs.add(_code(p), getattr(p, "description", ""))
            self.types.clear()
            for code, name in numbering.document_types.items():
                self.types.add(code, name)
            self.props.clear()
            wanted = {e.get("name"): e for e in properties}
            try:
                suggested = list(self.vs.suggested_properties())
            except Exception:
                suggested = []
            for entry in suggested:
                name = entry.get("name")
                if name in wanted:
                    self.props.add(wanted.pop(name), checked=True)
                else:
                    self.props.add(entry)
            custom_keys = {k for k, _t in CUSTOM_TYPES}
            for entry in wanted.values():
                self.props.add(entry, checked=True, fixed=entry.get("type") not in custom_keys)
            return []

        # --- validation and Create
        def validate(self):
            name = self._name_issues()
            numbering = self.csv_issues + self._numbering_issues()
            props = self._properties_issues()
            self.name_issues.set(name)
            self.numbering_issues.set(numbering)
            self.property_issues.set(props)
            return name + numbering + props

        def build_setup(self):
            numbering, _issues = self.numbering()
            chosen, custom = self.properties()
            return self.vs.VaultSetup(
                name=self.name_edit.text().strip(),
                folder=os.path.abspath(os.path.expanduser(self.folder_edit.text().strip())),
                numbering=numbering,
                properties=chosen + custom,
                make_default=self.make_default.isChecked(),
            )

        def create(self):
            """Create the vault; False (errors shown in the boxes) when it cannot be."""
            self.csv_issues = [i for i in self.csv_issues if _level(i) != "error"]
            if has_errors(self.validate()):
                return False
            setup = self.build_setup()
            try:
                vault = self.vs.create_vault(setup)
            except Exception as exc:
                self.name_issues.set([("error", f"The vault was not created: {exc}")])
                return False
            self.created = getattr(vault, "root", "") or setup.folder
            if setup.make_default:
                FreeCAD.ParamGet(PREFS_PATH).SetString("VaultPath", self.created)
            refresh_start()
            return True

        def accept(self):
            if not self.create():
                return False
            self.release()
            root, name = self.created, self.vault_name()
            QtCore.QTimer.singleShot(100, lambda: _offer_import(root, name))
            return True

    # ----- Import Files into Vault ----------------------------------------------------

    class ImportPanel(Panel):
        """Find .FCStd files, check their numbers, import copies one per tick."""

        def __init__(self, root):
            super().__init__()
            self.vs = _engine()
            self.root = root
            self.candidates = []
            self.job = None
            self.report = None
            self._stop = False
            self._scan_token = 0
            self.bridge = Bridge()
            self.bridge.scanned.connect(self._scanned)
            self._tick = QtCore.QTimer()
            self._tick.setInterval(0)
            self._tick.timeout.connect(self._step)
            self.form = [self._build_find(), self._build_files(), self._build_program()]
            self._fill_programs()
            self._update()

        def vault_name(self):
            return _vault_name(self.root)

        def setup_buttons(self):
            self._update()

        def _build_find(self):
            w = box("Import Files into Vault", ":/icons/Std_Import.svg")
            lay = QtWidgets.QVBoxLayout(w)
            lay.addWidget(hint(f"Copies of .FCStd files are numbered and checked in to "
                               f"{self.vault_name()}. The originals are not changed."))
            form = QtWidgets.QFormLayout()
            row = QtWidgets.QHBoxLayout()
            self.folder_edit = QtWidgets.QLineEdit(os.path.expanduser("~/Documents"))
            self.folder_edit.returnPressed.connect(self.scan)
            choose = QtWidgets.QPushButton("Choose…")
            choose.clicked.connect(self._choose)
            row.addWidget(self.folder_edit, 1)
            row.addWidget(choose)
            form.addRow("Folder", row)
            lay.addLayout(form)
            self.recursive = QtWidgets.QCheckBox("Include subfolders")
            self.recursive.setChecked(True)
            lay.addWidget(self.recursive)
            row = QtWidgets.QHBoxLayout()
            self.scan_button = QtWidgets.QPushButton("Find Files")
            self.scan_button.clicked.connect(self.scan)
            row.addWidget(self.scan_button)
            row.addStretch(1)
            lay.addLayout(row)
            self.find_issues = IssueList()
            lay.addWidget(self.find_issues)
            self.find_box = w
            return w

        def _build_files(self):
            w = box("Files")
            lay = QtWidgets.QVBoxLayout(w)
            self.stack = QtWidgets.QStackedWidget()
            # 0: the files found
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            self.empty = hint("Pick a folder and Find Files. Files already in a vault are left out.")
            pl.addWidget(self.empty)
            self.files = QtWidgets.QTreeWidget()
            self.files.setRootIsDecorated(False)
            self.files.setUniformRowHeights(True)
            self.files.setHeaderLabels(["File", "New P/N", "Kind", "Label", "Current P/N", "Notes"])
            self.files.setMinimumHeight(160)
            for col, width in ((0, 150), (1, 136), (2, 100), (3, 110), (4, 110), (5, 220)):
                self.files.setColumnWidth(col, width)
            self.files.itemChanged.connect(self._toggled)
            self.files.hide()
            pl.addWidget(self.files, 1)
            self.count_label = hint("")
            pl.addWidget(self.count_label)
            self.stack.addWidget(page)
            # 1: the result
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            self.result_label = QtWidgets.QLabel("")
            self.result_label.setWordWrap(True)
            pl.addWidget(self.result_label)
            self.results = QtWidgets.QTreeWidget()
            self.results.setRootIsDecorated(False)
            self.results.setHeaderLabels(["Result", "File", "Part number / reason"])
            self.results.setColumnWidth(0, 72)
            self.results.setColumnWidth(1, 150)
            self.results.setMinimumHeight(220)
            pl.addWidget(self.results, 1)
            self.stack.addWidget(page)
            lay.addWidget(self.stack, 1)
            self.progress = QtWidgets.QProgressBar()
            self.progress.setTextVisible(False)
            self.progress.hide()
            lay.addWidget(self.progress)
            self.progress_label = hint("")
            self.progress_label.hide()
            lay.addWidget(self.progress_label)
            self.file_issues = IssueList()
            lay.addWidget(self.file_issues)
            return w

        def _build_program(self):
            w = box("Number under program")
            lay = QtWidgets.QVBoxLayout(w)
            form = QtWidgets.QFormLayout()
            self.program_combo = QtWidgets.QComboBox()
            self.program_combo.currentIndexChanged.connect(lambda _i: self.replan())
            form.addRow("Program", self.program_combo)
            lay.addLayout(form)
            lay.addWidget(hint("Files without a valid part number get the next free number under this "
                               "program. Valid numbers already in a file are kept."))
            self.program_box = w
            return w

        def _fill_programs(self):
            self.program_combo.blockSignals(True)
            self.program_combo.clear()
            for code, description in _vault_programs(self.vs, self.root):
                self.program_combo.addItem(code + (f" — {description}" if description else ""), code)
            self.program_combo.blockSignals(False)

        def _choose(self):
            start = self.folder_edit.text().strip() or os.path.expanduser("~")
            picked = QtWidgets.QFileDialog.getExistingDirectory(Gui.getMainWindow(), "Files to import", start)
            if picked:
                self.folder_edit.setText(picked)
                self.scan()

        def scan(self):
            folder = os.path.expanduser(self.folder_edit.text().strip())
            if not os.path.isdir(folder):
                self.find_issues.set([("error", f"{folder} is not a folder.")])
                return
            self.find_issues.set([("info", f"Looking for FreeCAD files in {short(folder)}…")])
            self._scan_token += 1
            token, recursive = self._scan_token, self.recursive.isChecked()
            self.scan_button.setEnabled(False)
            bridge, vs = self.bridge, self.vs

            def work():
                try:
                    result = vs.scan_for_import(folder, recursive)
                except Exception as exc:
                    result = exc
                if _alive(bridge):
                    bridge.scanned.emit(token, result)

            threading.Thread(target=work, name="BruceWorxVaultScan", daemon=True).start()

        def _scanned(self, token, result):
            if token != self._scan_token:
                return
            self.scan_button.setEnabled(True)
            if isinstance(result, Exception):
                self.find_issues.set([("error", f"The folder could not be read: {result}")])
                return
            self.find_issues.set([])
            self.candidates = list(result)
            self._fill_files()
            self.replan()

        def _fill_files(self):
            t = self.files
            t.blockSignals(True)
            t.clear()
            for c in self.candidates:
                item = QtWidgets.QTreeWidgetItem(t)
                item.setText(0, os.path.basename(c.path))
                item.setToolTip(0, c.path)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if c.include else QtCore.Qt.Unchecked)
                item.setText(3, c.label)
                item.setText(4, c.existing_part_number)
                combo = QtWidgets.QComboBox(t)
                for kind in self.vs.KINDS:
                    combo.addItem(KIND_LABELS.get(kind, kind.title()), kind)
                combo.setCurrentIndex(max(0, combo.findData(c.kind)))
                combo.currentIndexChanged.connect(lambda _i, cand=c, b=combo: self._kind_changed(cand, b))
                t.setItemWidget(item, 2, combo)
            t.blockSignals(False)
            if self.candidates:  # every row shown, up to twelve
                row = max(t.sizeHintForRow(0), 24)
                rows = min(len(self.candidates), 12)
                t.setFixedHeight(t.header().sizeHint().height() + rows * row
                                 + t.horizontalScrollBar().sizeHint().height() + 2 * t.frameWidth())
            self.files.setVisible(bool(self.candidates))
            self.empty.setVisible(not self.candidates)
            if not self.candidates:
                self.empty.setText("No FreeCAD files to import were found there.")

        def _kind_changed(self, cand, combo):
            cand.kind = combo.currentData()
            cand.document_type = ""
            self.replan()

        def _toggled(self, item, column):
            if column != 0:
                return
            i = self.files.indexOfTopLevelItem(item)
            if 0 <= i < len(self.candidates):
                self.candidates[i].include = item.checkState(0) == QtCore.Qt.Checked
                self.replan()

        def program(self):
            return self.program_combo.currentData() or self.program_combo.currentText()

        def replan(self):
            """Proposed numbers for the included files (a preview; nothing reserved)."""
            if not self.candidates or self.job is not None:
                self._update()
                return
            issues = []
            try:
                self.vs.plan_numbers(self.candidates, self.root, self.program())
            except Exception as exc:
                issues.append(("error", f"Numbers could not be planned: {exc}"))
            self.file_issues.set(issues)
            t = self.files
            t.blockSignals(True)
            for i, c in enumerate(self.candidates):
                item = t.topLevelItem(i)
                if item is None:
                    continue
                item.setCheckState(0, QtCore.Qt.Checked if c.include else QtCore.Qt.Unchecked)
                item.setText(1, c.proposed_part_number if c.include else "")
                notes = "; ".join(_text(n) for n in c.issues)
                item.setText(5, notes)
                item.setToolTip(5, notes)
            t.blockSignals(False)
            self._update()

        def included(self):
            return [c for c in self.candidates if c.include]

        def _update(self):
            ok, cancel = self.ok_button(), self.cancel_button()
            count = len(self.included())
            if self.candidates and self.report is None:
                self.count_label.setText(f"{count} of {plural(len(self.candidates), 'file')} selected")
            else:
                self.count_label.setText("")
            if ok is None:
                return
            if self.report is not None:
                ok.setText("Close")
                ok.setEnabled(True)
                cancel.hide()
            elif self.job is not None:
                ok.setText("Import")
                ok.setEnabled(False)
                cancel.setText("Stop")
            else:
                ok.setText(f"Import {plural(count, 'File')}" if count else "Import")
                ok.setEnabled(count > 0)
                cancel.setText("Cancel")

        def start(self):
            chosen = self.included()
            if not chosen or self.job is not None:
                return False
            try:
                self.job = ImportJob(self.vs, self.root, self.candidates, self.program(), _writer())
            except Exception as exc:
                self.file_issues.set([("error", f"The import could not start: {exc}")])
                self.job = None
                return False
            self._stop = False
            self.file_issues.set([])
            for widget in (self.find_box, self.program_box, self.files):
                widget.setEnabled(False)
            self.progress.setRange(0, max(self.job.total, 1))
            self.progress.setValue(0)
            self.progress.show()
            self.progress_label.setText(f"Importing {plural(self.job.total, 'file')}…")
            self.progress_label.show()
            self._update()
            self._tick.start()
            return True

        def _step(self):
            job = self.job
            if job is None:
                self._tick.stop()
                return
            if self._stop:
                job.stop()
            elif job.done < job.total:
                path = job.step()
                self.progress.setValue(job.done)
                self.progress_label.setText(f"{job.done} of {job.total} done: {os.path.basename(path or '')}")
                if job.done < job.total:
                    return
                job.finish()
            self._tick.stop()
            self._finished()

        def _finished(self):
            job, self.job = self.job, None
            self.report = job.report
            self.progress.hide()
            self.progress_label.hide()
            self.files.setEnabled(True)  # the find and program boxes stay off: this import is done
            text = report_text(self.report)
            if self._stop:
                text += " Stopped before the end."
            if _writer() is None:
                text += " (Part numbers were not written into the files: this FreeCAD has no writer.)"
            self.result_label.setText(text)
            rt = self.results
            rt.clear()
            for kind, rows in (("Imported", self.report.imported), ("Skipped", self.report.skipped),
                               ("Failed", self.report.failed)):
                for path, detail in rows:
                    item = QtWidgets.QTreeWidgetItem(rt, [kind, os.path.basename(path), str(detail)])
                    item.setToolTip(1, path)
                    item.setToolTip(2, str(detail))
            self.stack.setCurrentIndex(1)
            self._update()
            refresh_start()

        def accept(self):
            if self.report is not None:
                self._tick.stop()
                return self.release()
            self.start()
            return False

        def reject(self):
            if self.job is not None:
                self._stop = True  # ends after the current file
                self.progress_label.setText("Stopping after this file…")
                return False
            self._tick.stop()
            return self.release()

    # ----- Vault Settings ------------------------------------------------------------

    class SettingsPanel(Panel):
        """The open vault's programs, document types and properties: add,
        rename and retire."""

        def __init__(self, root):
            super().__init__()
            self.vs = _engine()
            self.root = root
            self.csv_issues = []
            self.saved = self.vs.load_settings(root)
            try:
                self.used = self.vs.used_codes(root)
            except Exception:
                self.used = None
            self.form = [self._build_vault(), self._build_numbering(), self._build_properties()]
            self.show_settings(self.saved)
            self.validate()

        def vault_name(self):
            return _vault_name(self.root)

        def setup_buttons(self):
            ok = self.ok_button()
            if ok is not None:
                ok.setText("Save")

        def _build_vault(self):
            w = box("Vault Settings", ":/icons/preferences-system.svg")
            lay = QtWidgets.QVBoxLayout(w)
            form = QtWidgets.QFormLayout()
            name = QtWidgets.QLabel(self.vault_name())
            name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            form.addRow("Name", name)
            folder = QtWidgets.QLabel(short(self.root))
            folder.setWordWrap(True)
            folder.setToolTip(self.root)
            folder.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            form.addRow("Folder", folder)
            lay.addLayout(form)
            row = QtWidgets.QHBoxLayout()
            finder = QtWidgets.QPushButton("Open in Finder")
            finder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.root)))
            row.addWidget(finder)
            row.addStretch(1)
            lay.addLayout(row)
            return w

        def _build_numbering(self):
            w = box("Part numbering")
            lay = QtWidgets.QVBoxLayout(w)
            lay.addWidget(hint("Add programs and document types, or rename them. A code the vault has "
                               "cannot change or be deleted: tick Retired to stop offering it."))
            self.programs = CodeTable(["Code", "Description"], retired=True, changed=self._edited)
            lay.addLayout(plus_minus("Programs", self.programs.add_new, self.programs.remove))
            lay.addWidget(self.programs.widget)
            self.types = CodeTable(["Code", "Name"], retired=True, changed=self._edited)
            lay.addLayout(plus_minus("Document types", self.types.add_new, self.types.remove))
            lay.addWidget(self.types.widget)
            self._csv_buttons(lay)
            self.numbering_issues = IssueList()
            lay.addWidget(self.numbering_issues)
            return w

        def _build_properties(self):
            w = box("Company part properties")
            lay = QtWidgets.QVBoxLayout(w)
            self.props = PropertyTable("settings", changed=self.changed)
            lay.addWidget(self.props.widget)
            row = QtWidgets.QHBoxLayout()
            add = QtWidgets.QPushButton("Add Custom")
            add.clicked.connect(self.props.add_custom)
            remove = QtWidgets.QPushButton("Remove")
            remove.setToolTip("Remove a property added now")
            remove.clicked.connect(self.props.remove)
            row.addWidget(add)
            row.addWidget(remove)
            row.addStretch(1)
            lay.addLayout(row)
            self.property_issues = IssueList()
            lay.addWidget(self.property_issues)
            return w

        def _edited(self):
            self.csv_issues = []
            self.changed()

        def show_settings(self, settings):
            """Fill the tables: rows the saved vault has are fixed, others are new."""
            retired = settings.retired or {}
            saved_programs = {_code(p) for p in self.saved.programs}
            saved_types = {str(c).strip() for c in self.saved.document_types}
            saved_props = {e.get("name") for e in self.saved.properties}
            self.programs.clear()
            for p in settings.programs:
                self.programs.add(_code(p), getattr(p, "description", ""), _code(p) in retired.get("program", ()),
                                  existing=_code(p) in saved_programs)
            self.types.clear()
            for code, name in settings.document_types.items():
                code = str(code).strip()
                self.types.add(code, name, code in retired.get("document_type", ()), existing=code in saved_types)
            self.props.clear()
            custom_keys = {k for k, _t in CUSTOM_TYPES}
            for entry in settings.properties:
                name = entry.get("name")
                existing = name in saved_props
                self.props.add(entry, fixed=existing or entry.get("type") not in custom_keys,
                               retired=name in retired.get("property", ()))

        def current_settings(self):
            programs, p_retired, _i = self.programs.pairs("program code")
            types, t_retired, _i = self.types.pairs("document type code")
            fixed, custom, props_retired = self.props.read()
            return self.vs.VaultSettings(
                programs=[self.vs.ProgramSetup(c, d) for c, d in programs],
                document_types=dict(types),
                properties=fixed + custom,
                retired={"program": p_retired, "document_type": t_retired, "property": props_retired},
            )

        def apply_settings(self, loaded):
            """A loaded CSV is merged into what the panel shows; nothing changes on errors."""
            current = self.current_settings()
            merged = merge_settings(current, loaded)
            issues = list(self.vs.check_settings_change(self.saved, merged, self.used))
            if has_errors(issues):
                return [i for i in issues if _level(i) == "error"]
            added, changed = settings_changes(current, merged)
            self.show_settings(merged)
            # The boxes list what the change does once it is shown.
            return [("info", f"{plural(added, 'row')} added, {changed} changed. Save to keep them.")]

        def _issues(self):
            """(numbering issues, property issues) for what the panel shows."""
            _p, _r, numbering = self.programs.pairs("program code")
            _t, _r, more = self.types.pairs("document type code")
            numbering = numbering + more
            fixed, custom, _retired = self.props.read()
            props = custom_issues(custom, [e.get("name", "") for e in fixed])
            if has_errors(numbering) or has_errors(props):
                return numbering, props
            # Also checks the new settings on their own (codes, names, the property schema).
            for issue in self.vs.check_settings_change(self.saved, self.current_settings(), self.used):
                text = _text(issue).lower()
                (props if "propert" in text else numbering).append(issue)
            return numbering, props

        def validate(self):
            try:
                numbering, props = self._issues()
            except Exception as exc:
                numbering, props = [("error", f"The settings cannot be checked: {exc}")], []
            self.numbering_issues.set(self.csv_issues + numbering)
            self.property_issues.set(props)
            return numbering + props

        def save(self):
            self.csv_issues = [i for i in self.csv_issues if _level(i) != "error"]
            if has_errors(self.validate()):
                return False
            try:
                self.vs.save_settings(self.root, self.current_settings())
            except Exception as exc:
                self.numbering_issues.set([("error", f"The settings were not saved: {exc}")])
                return False
            refresh_start()
            return True

        def accept(self):
            if not self.save():
                return False
            return self.release()

    _classes = {
        "CreateVaultPanel": CreateVaultPanel,
        "ImportPanel": ImportPanel,
        "SettingsPanel": SettingsPanel,
        "Bridge": Bridge,
    }
    return _classes


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _vault_name(root):
    try:
        from bruceworx.pdm_folder import FolderVault

        return FolderVault(root).ref.get("name") or os.path.basename(root.rstrip(os.sep))
    except Exception:
        return os.path.basename(root.rstrip(os.sep)) or "Vault"


def _vault_programs(vs, root):
    """(code, description) of the vault's programs that are not retired."""
    try:
        settings = vs.load_settings(root)
        retired = settings.retired.get("program", set())
        return [(_code(p), getattr(p, "description", "")) for p in settings.programs if _code(p) not in retired]
    except Exception:
        pass
    try:
        import json

        with open(vs.numbering_path(root), encoding="utf-8") as handle:
            data = json.load(handle)
        return [(str(code), (entry or {}).get("description", "")) for code, entry in data.get("programs", {}).items()]
    except Exception:
        return []


def _message(title, text):
    import FreeCADGui
    from PySide import QtWidgets

    QtWidgets.QMessageBox.information(FreeCADGui.getMainWindow(), title, text)


def _show(title, make):
    """Open a task dialog made by make(); False when another one is open or it fails."""
    import FreeCADGui

    if FreeCADGui.Control.activeDialog():
        # Only one task dialog at a time; showDialog would fail without a word.
        _message(title, BUSY_TEXT)
        return False
    try:
        panel = make(_make_classes())
    except ImportError as exc:
        _message(title, f"Vaults need the BruceWorx hooks add-on: {exc}")
        return False
    except Exception as exc:
        _message(title, f"{title} could not open: {exc}")
        return False
    _state["panel"] = panel
    FreeCADGui.Control.showDialog(panel)
    _repaint_title()
    return True


def _repaint_title():
    """Lay out and repaint the task panel's title bar once the task card has set
    its title. The overlay title bar does not repaint when only the dock's title
    changes, and after the panel was hidden on the Start page its layout can be
    left squeezed (title drawn in no space)."""
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    def update():
        dock = FreeCADGui.getMainWindow().findChild(QtWidgets.QDockWidget, "Tasks")
        bar = dock.titleBarWidget() if dock is not None else None
        if bar is None:
            return
        if bar.layout() is not None:
            bar.layout().invalidate()
            bar.layout().activate()
        bar.update()

    for delay in (50, 300):
        QtCore.QTimer.singleShot(delay, update)


def show_create(folder="", name=""):
    """The Create Vault task dialog; ``folder`` pre-fills it (Open vault… on a plain folder)."""
    return _show("Create Vault", lambda c: c["CreateVaultPanel"](folder, name))


def show_import(root=""):
    """The Import Files into Vault task dialog for ``root`` (default: the open vault)."""
    root = root or open_vault_root()
    if not root:
        _message("Import Files into Vault", "Open a vault first (Start page → Open vault…).")
        return False
    return _show("Import Files into Vault", lambda c: c["ImportPanel"](root))


def show_settings(root=""):
    """The Vault Settings task dialog for ``root`` (default: the open vault)."""
    root = root or open_vault_root()
    if not root:
        _message("Vault Settings", "Open a vault first (Start page → Open vault…).")
        return False
    return _show("Vault Settings", lambda c: c["SettingsPanel"](root))


def _offer_import(root, name, tries=20):
    """After Create: ask whether to import existing files now."""
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    if FreeCADGui.Control.activeDialog():
        if tries > 0:  # the Create dialog is still closing
            QtCore.QTimer.singleShot(100, lambda: _offer_import(root, name, tries - 1))
        return
    answer = QtWidgets.QMessageBox.question(
        FreeCADGui.getMainWindow(), "Create Vault",
        f"The vault {name} is ready.\n\nImport existing files now? You can also do it later with "
        "BruceWorx → Import Files into Vault….",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
    if answer == QtWidgets.QMessageBox.Yes:
        show_import(root)


# ----- commands and menu ------------------------------------------------------------


class _CreateCommand:
    def GetResources(self):
        return {
            "Pixmap": "folder",
            "MenuText": "Create Vault…",
            "ToolTip": "Set up a new vault: its folder, part numbering and company part properties.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        show_create()


class _ImportCommand:
    def GetResources(self):
        return {
            "Pixmap": "Std_Import",
            "MenuText": "Import Files into Vault…",
            "ToolTip": "Number copies of existing FreeCAD files and check them in to the open vault. "
                       "The originals are not changed.",
        }

    def IsActive(self):
        return _vault_open()

    def Activated(self):
        show_import()


class _SettingsCommand:
    def GetResources(self):
        return {
            "Pixmap": "preferences-system",
            "MenuText": "Vault Settings…",
            "ToolTip": "The open vault's programs, document types and company part properties: "
                       "add, rename or retire them.",
        }

    def IsActive(self):
        return _vault_open()

    def Activated(self):
        show_settings()


MENU_ENTRIES = (
    (COMMAND, _CreateCommand, ":/icons/folder.svg", False),
    (IMPORT_COMMAND, _ImportCommand, ":/icons/Std_Import.svg", True),
    (SETTINGS_COMMAND, _SettingsCommand, ":/icons/preferences-system.svg", True),
)


def install():
    """Register the three vault commands and put them in the BruceWorx menu."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    for name, command, _icon, _needs in MENU_ENTRIES:
        Gui.addCommand(name, command())
    _state["installed"] = True
    try:
        import bruceworx_menu
        from PySide import QtGui

        window = Gui.getMainWindow()
        needs_vault = []
        for name, command, icon, needs in MENU_ENTRIES:
            res = command().GetResources()
            action = QtGui.QAction(QtGui.QIcon(icon), res["MenuText"], window)
            action.setToolTip(res["ToolTip"])
            action.triggered.connect(lambda _c=False, n=name: Gui.runCommand(n))
            action = bruceworx_menu.add_action(action, "BruceWorxMenu_" + name)
            if needs and action is not None:
                needs_vault.append(action)
        menu = bruceworx_menu.get_menu()
        if menu is not None and needs_vault:

            def update():
                ok = bool(open_vault_root())
                for action in needs_vault:
                    action.setEnabled(ok)

            menu.aboutToShow.connect(update)
    except Exception as exc:  # pragma: no cover - FreeCAD console only
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx: vault menu entries not added: {exc}\n")
