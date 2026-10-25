# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configurations in the GUI (roadmap MOD-03, plan 25 Oct 2026). The engine is bruceworx_configs.py.

* BruceWorx > Configurations... (BruceWorx_Configurations): the Configuration Table of the active
  file, a non-modal window. Rows are configurations (double-click a row's name to make it active),
  columns P/N, Description, then parameters, dimensions, suppressions and (assemblies) instance
  configurations. "+ Column" takes a user parameter, a dimension or the suppression of the selected
  feature, or the configuration of the selected instance. "Assign Number" gives the row the next
  dash number of the file's vault P/N; "Check All" builds every row; CSV export and import. Every
  edit is one undo step.
* The Parameters tab gets a bar on top (bar()): the active configuration in a drop-down, and Table....
* The Properties tab shows a Configuration drop-down for a configured part and for an instance of
  one (combo()).
* The vault: the check-in hook that adds configuration items, and Open from Vault on a configuration
  item opens its file in that row (open_configuration_item).
"""

from __future__ import annotations

import os

import bruceworx_configs as bc

COMMAND = "BruceWorx_Configurations"
_state = {"installed": False, "dialogs": {}}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
        '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/>'
        '<path d="M3 9h18M9 9v11" stroke="{c}" stroke-width="1.6"/>'
        '<circle cx="6" cy="14.5" r="1.4" fill="{c}"/></svg>')


def _console(text, level="msg"):
    import FreeCAD

    {"msg": FreeCAD.Console.PrintMessage, "warn": FreeCAD.Console.PrintWarning,
     "err": FreeCAD.Console.PrintError}[level](f"Configurations: {text}\n")


def run(doc, label, change):
    """One undo step "Configurations: <label>": ``change(doc)``, then a recompute. Returns (ok, error)."""
    name = f"Configurations: {label}"
    doc.openTransaction(name)
    try:
        change(doc)
        doc.recompute()
    except Exception as exc:  # noqa: BLE001 -- shown to the user, the step undone
        doc.abortTransaction()
        try:
            doc.recompute()
        except Exception:
            pass
        return False, str(exc)
    doc.commitTransaction()
    _refresh_panels()
    return True, ""


def _refresh_panels():
    try:
        import bruceworx_parameters

        c = bruceworx_parameters.controller()
        if c is not None:
            c.schedule()
    except Exception:
        pass


def switch(doc, name):
    """Make ``name`` the file's active configuration (one undo step)."""
    return run(doc, f"activate {name}", lambda d: bc.activate(d, name))


# ----- vault -----------------------------------------------------------------------------


def _vault_session():
    try:
        import bruceworx_vaultclient as vc

        return vc.session()
    except Exception:
        return None


def file_pn(doc):
    """The file's vault P/N: the table's, else what the vault calls the file."""
    pn = bc.file_part_number(doc)
    if pn:
        return pn
    s = _vault_session()
    try:
        return s.pn_for(doc.FileName) if s is not None and doc.FileName else ""
    except Exception:
        return ""


def assign_number(doc, name):
    """Give row ``name`` the next dash number of the file's P/N (reserved in the vault's numbering)."""
    s = _vault_session()
    pn = file_pn(doc)
    if s is None or not pn:
        raise bc.ConfigError("open the file from a vault first: a configuration's number follows its file's")
    taken = {p for _n, p, _d, own in bc.part_numbers(doc) if own}
    number = bc.next_configuration_number(s.vault, pn, taken)
    ok, error = run(doc, f"{name} P/N", lambda d: bc.set_cell(d, name, bc.PN, number))
    if not ok:
        raise bc.ConfigError(error)
    return number


class CheckinHook(bc.CheckinConfigurations):
    def record(self, s, pn, data, notes):
        super().record(s, pn, data, notes)
        text = bc.checkin_text(notes)
        if text:
            _console(text)


def open_configuration_item(pn):
    """Open from Vault on a configuration item: its file, in that configuration. True when handled."""
    import FreeCAD

    import bruceworx_vaultclient as vc

    s = vc.session()
    item = s.vault.items.get(pn) if s is not None else None
    if item is None or not getattr(item, "configuration_of", None):
        return False
    if not vc.open_from_vault(item.configuration_of):
        return True
    doc = vc.document_for(s.work.path(item.configuration_of)) or FreeCAD.ActiveDocument
    if doc is not None and item.configuration in bc.names(doc) and bc.active(doc) != item.configuration:
        ok, error = switch(doc, item.configuration)
        if not ok:
            _console(f"{pn}: {error}", "warn")
    return True


# ----- widgets ---------------------------------------------------------------------------


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def combo(obj, parent=None):
    """A drop-down of configurations for the Properties tab: a configured part or assembly switches its
    file; an instance (link) picks the row it shows. None for anything else."""
    _QtCore, _QtGui, QtWidgets = _qt()
    instance = obj.TypeId == "App::Link" and bc.configurable(bc.source_of(obj))
    if not instance and not (bc.configurable(obj) and bc.main_object(obj.Document) is obj):
        return None
    box = QtWidgets.QComboBox(parent)
    box.setObjectName("bwConfigCombo")
    if instance:
        current, follows = bc.instance_configuration(obj)
        names = bc.instance_names(obj)
        box.addItems(names)
        box.setCurrentIndex(names.index(current) if current in names else -1)
        driven = bc._expression(obj, bc.PROP)
        box.setEnabled(not driven)
        box.setToolTip(f"Set by the assembly's Configuration Table ({driven})" if driven else
                       f"Follows {bc.source_of(obj).Document.Label}'s active configuration until you pick one"
                       if follows else f"This instance shows {current} of {bc.source_of(obj).Document.Label}")

        def picked(index):
            name = box.itemText(index)
            if name and (name != current or follows):
                ok, error = run(obj.Document, f"{obj.Label} uses {name}",
                                lambda _d: bc.set_instance_configuration(obj, name))
                if not ok:
                    _console(error, "warn")
    else:
        names = bc.names(obj.Document)
        box.addItems(names)
        current = bc.active(obj.Document)
        box.setCurrentIndex(names.index(current) if current in names else -1)
        box.setToolTip("The file's active configuration (Configuration Table: BruceWorx > Configurations...)")

        def picked(index):
            name = box.itemText(index)
            if name and name != bc.active(obj.Document):
                ok, error = switch(obj.Document, name)
                if not ok:
                    _console(error, "warn")
    box.activated.connect(picked)
    return box


def bar(parent):
    """The configuration bar of the Parameters tab: hidden until the file has configurations."""
    _QtCore, _QtGui, QtWidgets = _qt()

    class ConfigBar(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwConfigBar")
            row = QtWidgets.QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 2)
            row.setSpacing(6)
            label = QtWidgets.QLabel("Configuration", self)
            label.setObjectName("bwConfigBarLabel")
            row.addWidget(label)
            self.box = QtWidgets.QComboBox(self)
            self.box.setObjectName("bwConfigCombo")
            self.box.setToolTip("The active configuration: switch it to see that variant")
            row.addWidget(self.box, 1)
            self.table = QtWidgets.QToolButton(self)
            self.table.setObjectName("bwInspectorMore")
            self.table.setText("Table…")
            self.table.setAutoRaise(True)
            self.table.setToolTip("The Configuration Table: rows, P/Ns, parameters and suppression")
            row.addWidget(self.table)
            self.doc_name = None
            self.box.activated.connect(self.picked)
            self.table.clicked.connect(lambda: show_table(self.doc()))
            self.hide()

        def doc(self):
            import FreeCAD

            return FreeCAD.listDocuments().get(self.doc_name) if self.doc_name else None

        def update_from(self, doc):
            on = doc is not None and bc.has_configurations(doc)
            self.setVisible(on)
            if not on:
                self.doc_name = None
                return
            self.doc_name = doc.Name
            names, current = bc.names(doc), bc.active(doc)
            if [self.box.itemText(i) for i in range(self.box.count())] != names:
                self.box.blockSignals(True)
                self.box.clear()
                self.box.addItems(names)
                self.box.blockSignals(False)
            if current in names and self.box.currentIndex() != names.index(current):
                self.box.setCurrentIndex(names.index(current))

        def picked(self, index):
            doc = self.doc()
            name = self.box.itemText(index)
            if doc is not None and name and name != bc.active(doc):
                ok, error = switch(doc, name)
                if not ok:
                    _console(error, "warn")

    return ConfigBar(parent)


def _make_dialog_class():
    QtCore, QtGui, QtWidgets = _qt()

    class ChoiceDelegate(QtWidgets.QStyledItemDelegate):
        """Instance columns: a drop-down of the part's configurations."""

        def __init__(self, table, choices):
            super().__init__(table)
            self.choices = choices  # column -> [names]

        def createEditor(self, parent, option, index):
            names = self.choices.get(index.column())
            if names is None:
                return super().createEditor(parent, option, index)
            box = QtWidgets.QComboBox(parent)
            box.addItems(names)
            return box

        def setEditorData(self, editor, index):
            if isinstance(editor, QtWidgets.QComboBox):
                text = str(index.data() or "")
                editor.setCurrentIndex(max(0, editor.findText(text)))
            else:
                super().setEditorData(editor, index)

        def setModelData(self, editor, model, index):
            if isinstance(editor, QtWidgets.QComboBox):
                model.setData(index, editor.currentText())
            else:
                super().setModelData(editor, model, index)

    class TableDialog(QtWidgets.QDialog):
        """The Configuration Table of one document."""

        def __init__(self, doc, parent=None):
            super().__init__(parent)
            self.setObjectName("bwConfigTable")
            self.doc_name = doc.Name
            self.setWindowTitle(f"Configurations — {doc.Label}")
            self.resize(760, 360)
            self.filling = False
            self.columns = []  # aliases, in table order
            outer = QtWidgets.QVBoxLayout(self)
            tools = QtWidgets.QHBoxLayout()
            self.buttons = {}
            for key, text, tip in (
                    ("add", "+ Configuration", "A new row, a copy of the selected (or active) one"),
                    ("rename", "Rename", "Rename the selected configuration"),
                    ("delete", "Delete", "Delete the selected configuration"),
                    ("column", "+ Column  ▾", "Configure a parameter, a dimension, a suppression or an instance"),
                    ("uncolumn", "Remove Column", "Stop configuring the selected column: the active row's value stays"),
                    ("number", "Assign Number", "Give the selected row the next dash number of the file's P/N (vault)"),
                    ("check", "Check All", "Build every configuration and list what fails"),
                    ("csv", "CSV  ▾", "Export or import the table as CSV (a design table)")):
                b = QtWidgets.QToolButton(self)
                b.setText(text)
                b.setToolTip(tip)
                b.setObjectName(f"bwConfig_{key}")
                tools.addWidget(b)
                self.buttons[key] = b
            tools.addStretch(1)
            outer.addLayout(tools)
            self.setup_box = QtWidgets.QWidget(self)
            form = QtWidgets.QHBoxLayout(self.setup_box)
            form.addWidget(QtWidgets.QLabel("This file has no configurations. Names:", self.setup_box))
            self.setup_names = QtWidgets.QLineEdit("Default", self.setup_box)
            self.setup_names.setPlaceholderText("Short, Standard, Long")
            form.addWidget(self.setup_names, 1)
            self.setup_button = QtWidgets.QPushButton("Set Up Configurations", self.setup_box)
            form.addWidget(self.setup_button)
            outer.addWidget(self.setup_box)
            self.table = QtWidgets.QTableWidget(self)
            self.table.setObjectName("bwConfigGrid")
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.choices = {}
            self.table.setItemDelegate(ChoiceDelegate(self.table, self.choices))
            outer.addWidget(self.table, 1)
            self.status = QtWidgets.QLabel("", self)
            self.status.setObjectName("bwConfigStatus")
            self.status.setWordWrap(True)
            self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            outer.addWidget(self.status)
            close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
            close.rejected.connect(self.close)
            outer.addWidget(close)
            self.buttons["add"].clicked.connect(self.add_row)
            self.buttons["rename"].clicked.connect(self.rename_row)
            self.buttons["delete"].clicked.connect(self.delete_row)
            self.buttons["uncolumn"].clicked.connect(self.remove_column)
            self.buttons["number"].clicked.connect(self.number_row)
            self.buttons["check"].clicked.connect(self.check_all)
            self.buttons["column"].setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.column_menu = QtWidgets.QMenu(self)
            self.column_menu.aboutToShow.connect(self.fill_column_menu)
            self.buttons["column"].setMenu(self.column_menu)
            csv_menu = QtWidgets.QMenu(self)
            csv_menu.addAction("Export CSV…", self.export_csv)
            csv_menu.addAction("Import CSV…", self.import_csv)
            self.buttons["csv"].setMenu(csv_menu)
            self.buttons["csv"].setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.setup_button.clicked.connect(self.set_up)
            self.table.itemChanged.connect(self.cell_changed)
            self.table.verticalHeader().sectionDoubleClicked.connect(self.activate_row)
            self.fill()

        # -- data ---------------------------------------------------------------------------------
        def doc(self):
            import FreeCAD

            return FreeCAD.listDocuments().get(self.doc_name)

        def say(self, text, error=False):
            self.status.setText(text)
            self.status.setStyleSheet("color: #D2463C;" if error else "")

        def act(self, label, change):
            doc = self.doc()
            if doc is None:
                return False
            ok, error = run(doc, label, change)
            self.say("" if ok else error, not ok)
            self.fill()
            return ok

        def selected_name(self):
            rows = {i.row() for i in self.table.selectedIndexes()} or {self.table.currentRow()}
            row = min(rows) if rows else -1
            names = bc.names(self.doc()) if self.doc() is not None else []
            return names[row] if 0 <= row < len(names) else (bc.active(self.doc()) if self.doc() else "")

        def selected_alias(self):
            col = self.table.currentColumn()
            return self.columns[col] if 0 <= col < len(self.columns) else ""

        def fill(self):
            doc = self.doc()
            if doc is None:
                self.close()
                return
            on = bc.has_configurations(doc)
            self.setup_box.setVisible(not on)
            self.table.setVisible(on)
            for key, b in self.buttons.items():
                b.setEnabled(on)
            if not on:
                return
            self.filling = True
            try:
                sheet = bc.table(doc)
                self.columns = bc.aliases(doc)
                names = bc.names(doc)
                active = bc.active(doc)
                file_number = file_pn(doc)
                self.table.clear()
                self.table.setColumnCount(len(self.columns))
                self.table.setRowCount(len(names))
                heads = []
                self.choices.clear()
                for col, alias in enumerate(self.columns):
                    head = bc.plain(sheet.getContents(bc.cell(col + 1, bc.HEAD_ROW))) or alias
                    heads.append(head)
                    if bc.column_kind(alias) == bc.INSTANCE:
                        target, _p = bc.column_target(doc, alias)
                        if target is not None:
                            self.choices[col] = bc.instance_names(target)
                self.table.setHorizontalHeaderLabels(heads)
                self.table.setVerticalHeaderLabels([("●  " if n == active else "") + n for n in names])
                bold = QtGui.QFont(self.table.font())
                bold.setBold(True)
                for row, (name, values) in enumerate(bc.rows(doc)):
                    for col, alias in enumerate(self.columns):
                        raw = values.get(alias, "")
                        kind = bc.column_kind(alias)
                        item = QtWidgets.QTableWidgetItem()
                        if kind == bc.SUPPRESS:
                            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                                          | QtCore.Qt.ItemIsUserCheckable)
                            item.setCheckState(QtCore.Qt.Checked if bc.suppress_value(bc.literal(raw)) == "1"
                                               else QtCore.Qt.Unchecked)
                            item.setToolTip("Ticked: suppressed in this configuration")
                        elif kind == bc.PN_KIND and (not raw or raw.startswith("=")):
                            item.setText(file_number)
                            font = QtGui.QFont(bold if name == active else self.table.font())
                            font.setItalic(True)
                            item.setFont(font)
                            item.setToolTip(f"The file's own P/N{f' ({file_number})' if file_number else ''}; "
                                            "type one, or Assign Number, for an item of its own")
                            item.setForeground(QtGui.QBrush(QtGui.QColor(142, 142, 147)))
                        else:
                            item.setText(bc.plain(bc.literal(raw)))
                        if name == active and not item.font().italic():
                            item.setFont(bold)
                        self.table.setItem(row, col, item)
                self.table.resizeColumnsToContents()
                self.buttons["number"].setEnabled(_vault_session() is not None and bool(file_number))
                if self.columns:
                    self.buttons["uncolumn"].setEnabled(True)
            finally:
                self.filling = False

        # -- edits --------------------------------------------------------------------------------
        def cell_changed(self, item):
            if self.filling:
                return
            doc = self.doc()
            names = bc.names(doc)
            row, col = item.row(), item.column()
            if not (0 <= row < len(names) and 0 <= col < len(self.columns)):
                return
            name, alias = names[row], self.columns[col]
            if bc.column_kind(alias) == bc.SUPPRESS:
                text = "1" if item.checkState() == QtCore.Qt.Checked else "0"
            else:
                text = item.text()
            QtCore.QTimer.singleShot(0, lambda: self.act(f"{name} {alias}",
                                                         lambda d: bc.set_cell(d, name, alias, text)))

        def activate_row(self, row):
            names = bc.names(self.doc())
            if 0 <= row < len(names):
                self.act(f"activate {names[row]}", lambda d: bc.activate(d, names[row]))

        def set_up(self):
            names = [n.strip() for n in self.setup_names.text().split(",") if n.strip()] or ["Default"]
            self.act("set up", lambda d: bc.setup(d, names))

        def add_row(self):
            source = self.selected_name()
            names = bc.names(self.doc())
            base, n = "Configuration", len(names) + 1
            while f"{base} {n}" in names:
                n += 1
            text, ok = QtWidgets.QInputDialog.getText(self, "New configuration",
                                                      f"Name (a copy of {source}):", text=f"{base} {n}")
            if ok and text.strip():
                self.act(f"add {text.strip()}", lambda d: bc.add_configuration(d, text.strip(), source))

        def rename_row(self):
            old = self.selected_name()
            text, ok = QtWidgets.QInputDialog.getText(self, "Rename configuration", "Name:", text=old)
            if ok and text.strip() and text.strip() != old:
                self.act(f"rename {old}", lambda d: bc.rename_configuration(d, old, text.strip()))

        def delete_row(self):
            name = self.selected_name()
            if QtWidgets.QMessageBox.question(self, "Delete configuration", f"Delete configuration {name}?") \
                    == QtWidgets.QMessageBox.Yes:
                self.act(f"delete {name}", lambda d: bc.remove_configuration(d, name))

        def remove_column(self):
            alias = self.selected_alias()
            if alias:
                self.act(f"remove column {alias}", lambda d: bc.remove_column(d, alias))

        def number_row(self):
            name = self.selected_name()
            try:
                number = assign_number(self.doc(), name)
            except Exception as exc:  # noqa: BLE001
                self.say(str(exc), True)
            else:
                self.say(f"{name}: {number}")
            self.fill()

        def check_all(self):
            doc = self.doc()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                report = bc.check_all(doc)
            except Exception as exc:  # noqa: BLE001
                self.say(str(exc), True)
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            bad = {n: f for n, f in report.items() if f}
            if bad:
                self.say("Fails in " + "; ".join(f"{n}: {', '.join(f)}" for n, f in bad.items()), True)
            else:
                self.say(f"All {len(report)} configurations build.")
            self.fill()

        def fill_column_menu(self):
            import FreeCADGui

            import bruceworx_parameters as bp

            menu = self.column_menu
            menu.clear()
            doc = self.doc()
            taken = set(bc.aliases(doc))
            params = bp.local_sheet(doc)
            sub = menu.addMenu("Parameter")
            names = [a for _c, a in bp.sheet_aliases(params)] if params is not None else []
            free = [a for a in names if a not in taken and a not in bc.RESERVED]
            for alias in free:
                sub.addAction(alias, lambda a=alias: self.act(f"configure {a}", lambda d: bc.add_parameter_column(d, a)))
            if not free:
                sub.addAction("(no user parameters to configure)").setEnabled(False)
            picked = [s.Object for s in FreeCADGui.Selection.getSelectionEx(doc.Name)]
            dims = menu.addMenu("Dimension of the selected feature")
            n = 0
            for obj in picked:
                for prop, label, _t in bp.dimension_props(obj):
                    if bc._expression(obj, prop):
                        continue
                    n += 1
                    dims.addAction(f"{obj.Label}: {label}", lambda o=obj, p=prop: self.act(
                        f"configure {o.Label} {p}", lambda d: bc.add_dimension_column(d, o, p)))
            if not n:
                dims.addAction("(select a feature with dimensions)").setEnabled(False)
            self.submenus = [sub, dims]  # PySide drops the wrappers (and the menus) otherwise
            sup = [o for o in picked if "Suppressed" in o.PropertiesList or o.TypeId == "App::Link"]
            action = menu.addAction("Suppression of the selected" + (f" ({', '.join(o.Label for o in sup)})" if sup else ""))
            action.setEnabled(bool(sup))
            action.triggered.connect(lambda _c=False: self.act("configure suppression", lambda d: [
                bc.add_suppression_column(d, o) for o in sup]))
            links = [o for o in picked if o.TypeId == "App::Link" and bc.configurable(bc.source_of(o))]
            action = menu.addAction("Configuration of the selected instance"
                                    + (f" ({', '.join(o.Label for o in links)})" if links else ""))
            action.setEnabled(bool(links))
            action.triggered.connect(lambda _c=False: self.act("configure instances", lambda d: [
                bc.add_instance_column(d, o) for o in links]))
            menu.addSeparator()
            menu.addAction("Remove all configurations…", self.remove_all)

        def remove_all(self):
            if QtWidgets.QMessageBox.question(
                    self, "Remove configurations",
                    "Keep only the active configuration and remove the table?") == QtWidgets.QMessageBox.Yes:
                self.act("remove all", bc.remove_all)

        def export_csv(self):
            doc = self.doc()
            start = os.path.splitext(doc.FileName or doc.Label)[0] + "-configurations.csv"
            path, _f = QtWidgets.QFileDialog.getSaveFileName(self, "Export configurations", start, "CSV (*.csv)")
            if path:
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(bc.export_csv(doc))
                self.say(f"Exported to {path}")

        def import_csv(self):
            path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Import configurations", "", "CSV (*.csv)")
            if not path:
                return
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            result = {}
            if self.act("import CSV", lambda d: result.update(zip(("u", "a", "x"), bc.import_csv(d, text)))):
                extra = f"; columns not in the table: {', '.join(result['x'])}" if result.get("x") else ""
                self.say(f"Updated {len(result['u'])}, added {len(result['a'])}{extra}")

        def closeEvent(self, event):
            _state["dialogs"].pop(self.doc_name, None)
            super().closeEvent(event)

    return TableDialog


_dialog_class = None


def show_table(doc=None):
    """Open (or raise) the Configuration Table of ``doc`` (default: the active document)."""
    import FreeCAD
    import FreeCADGui

    global _dialog_class
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        return None
    if _dialog_class is None:
        _dialog_class = _make_dialog_class()
    dialog = _state["dialogs"].get(doc.Name)
    if dialog is None:
        dialog = _dialog_class(doc, FreeCADGui.getMainWindow())
        _state["dialogs"][doc.Name] = dialog
    else:
        dialog.fill()
    dialog.show()
    return dialog


class _Command:
    def GetResources(self):
        return {"Pixmap": "BruceWorx_Configurations", "MenuText": "Configurations…",
                "ToolTip": "Configurations of this part or assembly: one file, several variants (sizes, options), "
                           "each with its own values, suppressed features and P/N"}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_table()


class _Observer:
    """Keeps an open table (and the Parameters bar) in step when the file changes."""

    def slotRecomputedDocument(self, doc):
        try:  # instances copied again after their part changed take their row one recompute later
            if any("Touched" in (getattr(doc.getObject(n), "State", None) or []) for n in bc.copies(doc)):
                from PySide import QtCore

                QtCore.QTimer.singleShot(0, lambda: bc.settle_copies(doc))
        except Exception:
            pass
        dialog = _state["dialogs"].get(doc.Name)
        if dialog is not None and not dialog.filling:
            try:
                from PySide import QtCore

                QtCore.QTimer.singleShot(0, dialog.fill)
            except Exception:
                pass

    def slotChangedObject(self, obj, prop):
        # The switch changed outside BruceWorx (the property editor): apply what expressions cannot.
        if prop == bc.PROP and obj.TypeId in bc._main_rank() and bc.configurable(obj):
            try:
                from PySide import QtCore

                doc = obj.Document
                QtCore.QTimer.singleShot(0, lambda: bc.after_switch(doc))
            except Exception:
                pass

    def slotDeletedDocument(self, doc):
        dialog = _state["dialogs"].pop(doc.Name, None)
        if dialog is not None:
            dialog.close()


def install():
    """The command, its icon and menu entry, the observer and the vault hook."""
    if _state["installed"]:
        return
    import FreeCAD
    import FreeCADGui

    FreeCADGui.addIcon("BruceWorx_Configurations", _SVG.replace("{c}", "#0A84FF").encode("utf-8"), "SVG")
    FreeCADGui.addCommand(COMMAND, _Command())
    _state["observer"] = _Observer()
    FreeCAD.addDocumentObserver(_state["observer"])
    try:
        import bruceworx_vaultclient as vc

        hooks = getattr(vc, "CHECKIN_HOOKS", None)
        if hooks is not None and not any(isinstance(h, bc.CheckinConfigurations) for h in hooks):
            hooks.insert(0, CheckinHook())  # before the BOM: an assembly's lines may be its parts' rows
    except Exception as exc:  # noqa: BLE001
        _console(f"the vault check-in hook was not added: {exc}", "warn")
    try:
        from PySide import QtGui

        import bruceworx_menu

        action = QtGui.QAction(FreeCADGui.getIcon("BruceWorx_Configurations"), "Configurations…",
                               FreeCADGui.getMainWindow())
        action.triggered.connect(lambda _c=False: FreeCADGui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, "BruceWorxMenu_Configurations")
    except Exception as exc:  # noqa: BLE001
        _console(f"no menu entry: {exc}", "warn")
    _state["installed"] = True
