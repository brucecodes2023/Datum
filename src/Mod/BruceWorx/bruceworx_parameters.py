# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Parameters tab of the right panel (roadmap UIX-12; plan "Redesign (24 Oct)").

Fusion's Change Parameters as a tab: every dimension of the active document
in one table (Name, Expression, Value), with a filter.

* User parameters live in a spreadsheet named "Parameters": column A the name,
  B the value or expression (its alias is the name), C a comment. Every aliased
  cell is listed. Decided 24 Oct: one sheet for the whole product, at the root of
  the top-assembly file. A part without a sheet of its own uses the one its
  expressions read in another file (Root#Parameters.<name>), or that the files
  its links point at read; only a file with neither gets its own, made by the
  first "+ Parameter". The tab shows and takes the bare name.
* An edit of the sheet rebuilds the open parts in other files that read it:
  FreeCAD does not recompute them on its own.
* Model parameters: every dimension of every feature (its length, angle and
  count properties, inactive ones left out) and every dimensional sketch
  constraint, named or not, grouped by feature in tree order.
* Edit in place: a value sets the property (or the sketch datum); an
  expression binds it; a plain value typed as the expression unbinds it. One
  undo step each ("<object>: <field>"), then a recompute. Names of user
  parameters rename the alias (FreeCAD updates the expressions that use it).
* Fast on big models: built only while visible; feature groups are filled
  when first opened (folded when there are many); a recompute updates the
  texts in place; the filter never touches the document.
"""

from __future__ import annotations

import re

TAB = "BWParameters"
SHEET_NAME = "Parameters"
SHEET_TYPE = "Spreadsheet::Sheet"
SHEET_HEADERS = (("A1", "Name"), ("B1", "Value"), ("C1", "Comment"))
COLUMNS = ("Name", "Expression", "Value")
NAME, EXPRESSION, VALUE = 0, 1, 2
USER, PROP, CONSTRAINT = "user", "prop", "constraint"
FOLD_LIMIT = 25  # more feature groups than this: folded until opened
UNITS = ("mm", "cm", "m", "in", "ft", "deg", "kg", "g", "")
EMPTY_HINT = "Open a document to see its parameters here."

DIMENSION_TYPES = (
    "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle", "App::PropertyQuantity",
    "App::PropertyQuantityConstraint", "App::PropertyArea", "App::PropertyVolume", "App::PropertyMass",
    "App::PropertyIntegerConstraint",
)
INTEGER_TYPES = ("App::PropertyIntegerConstraint",)
SKIP_GROUPS = ("Base", "Part Design", "Attachment")
SKIP_OBJECT_TYPES = (SHEET_TYPE, "App::Origin", "App::Line", "App::Plane", "App::Point")
CONSTRAINT_WORDS = {
    "Distance": "Distance", "DistanceX": "Horizontal", "DistanceY": "Vertical",
    "Radius": "Radius", "Diameter": "Diameter", "Angle": "Angle",
}
_LITERAL = re.compile(r"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*([A-Za-zµ°\"'][A-Za-z0-9µ°\"'^/*]*)?\s*$")
_IDENT = re.compile(r"(?<![\w.<])([A-Za-z_][A-Za-z0-9_]*)(?![\w.(>])")
_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CELL = re.compile(r"^([A-Za-z]{1,2})(\d+)$")


# ----- pure helpers (unit tested) ---------------------------------------------------------


class Param:
    """One row: a user parameter (a sheet cell), a feature property, or a sketch constraint."""

    __slots__ = ("kind", "owner", "key", "label", "expression", "expr_key", "value", "editable", "unit_kind",
                 "tip")

    def __init__(self, kind, owner, key, label, expression="", value="", editable=True, unit_kind="",
                 expr_key="", tip=""):
        self.kind, self.owner, self.key, self.label = kind, owner, key, label
        self.expression, self.value, self.editable = expression, value, editable
        self.unit_kind, self.expr_key, self.tip = unit_kind, expr_key, tip

    @property
    def ident(self):
        return f"{self.kind}\x1f{self.owner}\x1f{self.key}"

    def signature(self):
        return (self.ident, self.label, self.editable)

    def __repr__(self):
        return f"Param({self.kind!r}, {self.owner!r}, {self.key!r}, {self.label!r})"


def is_literal(text):
    """A number with an optional unit ("8", "8 mm", "-2.5deg"): a value, not an expression."""
    return bool(_LITERAL.match(text or ""))


def has_unit(text):
    match = _LITERAL.match(text or "")
    return bool(match and match.group(4))


def parse_edit(text):
    """What an edit asks for: ("clear", "") for an empty field, ("value", text) or ("expression", text)."""
    text = (text or "").strip()
    if text.startswith("="):
        text = text[1:].strip()
        return ("expression", text) if text else ("clear", "")
    if not text:
        return "clear", ""
    return ("value", text) if is_literal(text) else ("expression", text)


def cell_content(text, unit=""):
    """What goes into the sheet for a user parameter: "8 mm", or "=plate_t * 2" for an expression."""
    kind, body = parse_edit(text)
    if kind == "clear":
        return ""
    if kind == "value":
        return body if has_unit(body) or not unit else f"{body} {unit}"
    return "=" + body


def short_expr(expr, aliases, sheet_name=SHEET_NAME, sheet_label=SHEET_NAME):
    """"Parameters.plate_t * 1.75" -> "plate_t * 1.75" (only for the sheet's aliases)."""
    if not expr:
        return ""
    names = "|".join(re.escape(n) for n in {sheet_name, f"<<{sheet_label}>>"})

    def drop(match):
        return match.group(2) if match.group(2) in aliases else match.group(0)

    return re.sub(rf"(?<![\w.])({names})\.([A-Za-z_][A-Za-z0-9_]*)", drop, expr)


def qualify(expr, aliases, sheet_name=SHEET_NAME):
    """"plate_t * 1.75" -> "Parameters.plate_t * 1.75": bare aliases point at the sheet."""
    if not expr:
        return ""
    return _IDENT.sub(lambda m: f"{sheet_name}.{m.group(1)}" if m.group(1) in aliases else m.group(1), expr)


def uses_alias(expr, alias, sheet_name=SHEET_NAME, sheet_label=SHEET_NAME, bare=False):
    """True when ``expr`` refers to the user parameter ``alias``."""
    if not expr:
        return False
    names = "|".join(re.escape(n) for n in {sheet_name, f"<<{sheet_label}>>"})
    if re.search(rf"(?<![\w.])({names})\.{re.escape(alias)}(?!\w)", expr):
        return True
    return bare and re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w(])", expr) is not None


def valid_alias(name):
    """A name a spreadsheet alias may take: an identifier that is not a cell address."""
    return bool(_ALIAS.match(name or "")) and not _CELL.match(name)


def unique_name(base, taken):
    taken = set(taken)
    if base not in taken:
        return base
    i = 1
    while f"{base}{i}" in taken:
        i += 1
    return f"{base}{i}"


def cell_rc(cell):
    """"B12" -> (12, 2); "AA3" -> (3, 27)."""
    match = _CELL.match(cell or "")
    if not match:
        return (0, 0)
    col = 0
    for ch in match.group(1).upper():
        col = col * 26 + ord(ch) - 64
    return int(match.group(2)), col


def next_row(cells):
    """The first free row below the used cells (row 1 is the header)."""
    return max([cell_rc(c)[0] for c in cells] + [1]) + 1


def expression_map(engine):
    """FreeCAD's ExpressionEngine [(path, expression)] as {path without the leading ".": expression}."""
    return {path.lstrip("."): expr for path, expr in engine or ()}


def constraint_path(index, name=""):
    """The expression path of a sketch constraint: "Constraints.width", or "Constraints[6]" unnamed."""
    return f"Constraints.{name}" if name else f"Constraints[{index}]"


def constraint_rows(constraints):
    """[(index, label, unit kind, driving)] for the dimensional constraints of [(type, name, driving)]."""
    out = []
    for index, (type_name, name, driving) in enumerate(constraints):
        word = CONSTRAINT_WORDS.get(type_name)
        if word is None:
            continue
        label = name or f"{word} {index + 1}"
        out.append((index, label, "Angle" if type_name == "Angle" else "Length", bool(driving)))
    return out


def dimension_props(obj, labels=None):
    """[(property, label, type)] of ``obj``'s dimensions: lengths, angles, counts; not hidden, not
    read-only (an inactive option), not FreeCAD's bookkeeping."""
    import bruceworx_inspector as bi

    labels = labels or {}
    out = []
    for name in getattr(obj, "PropertiesList", ()) or ():
        prop_type = obj.getTypeIdOfProperty(name)
        if prop_type not in DIMENSION_TYPES:
            continue
        if obj.getGroupOfProperty(name) in SKIP_GROUPS:
            continue
        mode = tuple(obj.getEditorMode(name) or ())
        try:
            status = tuple(obj.getPropertyStatus(name) or ())
        except Exception:
            status = ()
        if bi.hidden(mode, status, name) or "ReadOnly" in mode or "ReadOnly" in status:
            continue
        out.append((name, labels.get(name) or bi.friendly(name), prop_type))
    return out


def ordered_objects(objects):
    """Objects in tree order: a body's features follow the body, in the body's order."""
    out, seen = [], set()
    for obj in objects:
        if obj.Name in seen:
            continue
        seen.add(obj.Name)
        out.append(obj)
        if getattr(obj, "TypeId", "") == "PartDesign::Body":
            for child in getattr(obj, "Group", None) or []:
                if child.Name not in seen:
                    seen.add(child.Name)
                    out.append(child)
    return out


def matches(needle, *texts):
    needle = (needle or "").strip().lower()
    return not needle or any(needle in (t or "").lower() for t in texts)


def fold_default(groups):
    """Feature groups start open, unless there are many."""
    return groups <= FOLD_LIMIT


# ----- FreeCAD side ------------------------------------------------------------------------


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _obj_alive(obj):
    try:
        return obj is not None and obj.Document.getObject(obj.Name) is obj
    except Exception:
        return False


def local_sheet(doc):
    """The document's own "Parameters" spreadsheet, or None."""
    if doc is None:
        return None
    obj = doc.getObject(SHEET_NAME)
    if obj is not None and obj.TypeId == SHEET_TYPE:
        return obj
    for obj in doc.getObjectsByLabel(SHEET_NAME):
        if obj.TypeId == SHEET_TYPE:
            return obj
    return None


_XDOC = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z_][A-Za-z0-9_]*|<<[^>]+>>)\.")


def referenced_sheets(expressions):
    """{(document name, object name or <<label>>)} of the other files' objects the expressions read."""
    found = set()
    for expr in expressions:
        for match in _XDOC.finditer(expr or ""):
            found.add((match.group(1), match.group(2)))
    return found


def linked_sheet(doc):
    """The product's Parameters sheet in another file (decided 24 Oct: one sheet at the root of the
    top-assembly file), found through the expressions of this document, or of the files its links
    point at (an assembly reads the sheet only through its parts)."""
    import FreeCAD

    def sheet_in(refs):
        for doc_name, obj_name in sorted(refs):
            other = FreeCAD.listDocuments().get(doc_name)
            if other is None:
                continue
            obj = (other.getObjectsByLabel(obj_name[2:-2]) or [None])[0] if obj_name.startswith("<<") else other.getObject(obj_name)
            if obj is not None and obj.TypeId == SHEET_TYPE:
                return obj
        return None

    def refs_of(d):
        return referenced_sheets(e for o in d.Objects for _p, e in (getattr(o, "ExpressionEngine", None) or ()))

    found = sheet_in(refs_of(doc))
    if found is not None:
        return found
    seen = {doc.Name}
    for obj in doc.Objects:
        target = obj.getLinkedObject(True) if obj.TypeId == "App::Link" else None
        other = getattr(target, "Document", None)
        if other is None or other.Name in seen:
            continue
        seen.add(other.Name)
        found = local_sheet(other) or sheet_in(refs_of(other))
        if found is not None:
            return found
    return None


def find_sheet(doc):
    """The Parameters spreadsheet this document uses: its own, else the product's in another file."""
    if doc is None:
        return None
    return local_sheet(doc) or linked_sheet(doc)


def sheet_ref(sheet, doc):
    """How expressions in ``doc`` name the sheet: "Parameters", or "File#Parameters" across files."""
    if sheet is None:
        return SHEET_NAME
    if doc is None or sheet.Document is doc:
        return sheet.Name
    return f"{sheet.Document.Name}#{sheet.Name}"


def dependents(sheet):
    """Objects in other open files whose expressions read the sheet."""
    import FreeCAD

    prefix = f"{sheet.Document.Name}#"
    out = []
    for doc in FreeCAD.listDocuments().values():
        if doc is sheet.Document:
            continue
        for obj in doc.Objects:
            if any(prefix in (e or "") for _p, e in (getattr(obj, "ExpressionEngine", None) or ())):
                out.append(obj)
    return out


def update_dependents(sheet):
    """Rebuild the parts in other files that read the sheet: FreeCAD does not recompute them when the
    sheet changes (found 24 Oct, FND-08). Returns the names of the files rebuilt."""
    docs = {}
    for obj in dependents(sheet):
        obj.touch()
        docs[obj.Document.Name] = obj.Document
    for doc in docs.values():
        doc.recompute(None, True, True)
    return sorted(docs)


def make_sheet(doc):
    """A new "Parameters" spreadsheet with its header row (inside the caller's transaction)."""
    sheet = doc.addObject(SHEET_TYPE, SHEET_NAME)
    sheet.Label = SHEET_NAME
    for cell, text in SHEET_HEADERS:
        sheet.set(cell, text)
    try:
        sheet.setStyle("A1:C1", "bold")
        sheet.setColumnWidth("A", 120)
        sheet.setColumnWidth("B", 160)
        sheet.setColumnWidth("C", 220)
    except Exception:
        pass
    return sheet


def sheet_aliases(sheet):
    """[(cell, alias)] of the sheet, in row order."""
    if sheet is None:
        return []
    out = []
    for cell in sheet.getUsedCells():
        try:
            alias = sheet.getAlias(cell)
        except Exception:
            alias = None
        if alias:
            out.append((cell, alias))
    out.sort(key=lambda ca: cell_rc(ca[0]))
    return out


def text_of(value):
    """A value as the table shows it, in the user's units (a zero keeps its unit: "0.00°")."""
    import bruceworx_inspector as bi

    if hasattr(value, "UserString"):
        return value.UserString
    return bi.value_text(value)


def collect_user(sheet):
    params = []
    for cell, alias in sheet_aliases(sheet):
        content = sheet.getContents(cell) or ""
        expression = content[1:] if content.startswith("=") else content
        try:
            value = text_of(sheet.get(alias))
        except Exception as exc:
            value = "error"
            expression = expression or ""
            tip = str(exc)
        else:
            tip = ""
        comment = ""
        row = cell_rc(cell)[0]
        try:
            comment = sheet.getContents(f"C{row}") if cell.startswith("B") else ""
        except Exception:
            pass
        params.append(Param(USER, sheet.Name, alias, alias, expression, value, True, "", cell,
                            tip or (f"{alias} ({cell})" + (f": {comment}" if comment else ""))))
    return params


def collect_feature(obj, aliases, sheet):
    """[Param] for one feature (dimensions) or sketch (dimensional constraints)."""
    import math

    import FreeCAD

    import bruceworx_inspector as bi

    sheet_name = sheet_ref(sheet, obj.Document)
    sheet_label = sheet.Label if sheet is not None else SHEET_NAME
    exprs = expression_map(getattr(obj, "ExpressionEngine", ()))
    params = []
    if obj.isDerivedFrom("Sketcher::SketchObject"):
        constraints = list(obj.Constraints)
        rows = constraint_rows([(c.Type, c.Name, c.Driving) for c in constraints])
        for index, label, kind, driving in rows:
            c = constraints[index]
            path = constraint_path(index, c.Name)
            key = path if path in exprs else constraint_path(index) if constraint_path(index) in exprs else ""
            value = math.degrees(c.Value) if kind == "Angle" else c.Value
            text = FreeCAD.Units.Quantity(value, getattr(FreeCAD.Units, kind)).UserString
            expr = exprs.get(key, "") if key else ""
            params.append(Param(CONSTRAINT, obj.Name, index, label,
                                short_expr(expr, aliases, sheet_name, sheet_label), text, driving, kind,
                                key or path, f"{obj.Label}: constraint {index + 1}"
                                + ("" if driving else " (reference)")))
        return params
    labels = {name: label for _g, name, label in bi.CURATED.get(obj.TypeId, ())}
    for name, label, prop_type in dimension_props(obj, labels):
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        text = str(int(value)) if prop_type in INTEGER_TYPES else text_of(value)
        expr = exprs.get(name, "")
        params.append(Param(PROP, obj.Name, name, label, short_expr(expr, aliases, sheet_name, sheet_label), text,
                            True, prop_type, name, f"{obj.Label}.{name}"))
    return params


def collect_model(doc, sheet):
    """[(feature, [Param])] for the document, in tree order; features without dimensions left out."""
    aliases = {a for _c, a in sheet_aliases(sheet)}
    groups = []
    for obj in ordered_objects(doc.Objects):
        if obj.TypeId in SKIP_OBJECT_TYPES:
            continue
        try:
            params = collect_feature(obj, aliases, sheet)
        except Exception:
            continue
        if params:
            groups.append((obj, params))
    return groups


# ----- widgets -----------------------------------------------------------------------------


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    def no_safe_area(root):
        """macOS: no widget may keep the old title strip's safe area as contents margins (see the inspector)."""
        for widget in [root] + root.findChildren(QtWidgets.QWidget):
            if widget.testAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea):
                widget.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)

    class Delegate(QtWidgets.QStyledItemDelegate):
        """Only the cells that can change open an editor (see Controller.can_edit)."""

        def __init__(self, tree):
            super().__init__(tree)
            self.tree = tree

        def paint(self, painter, option, index):
            if str(index.data(QtCore.Qt.UserRole) or "").startswith("top\x1f") or (
                    index.column() and str(index.siblingAtColumn(0).data(QtCore.Qt.UserRole) or "").startswith("top\x1f")):
                band = QtGui.QColor(option.palette.color(QtGui.QPalette.Text))
                band.setAlphaF(0.06)
                painter.fillRect(option.rect, band)  # User / Model parameters: a banded row
            super().paint(painter, option, index)

        def createEditor(self, parent, option, index):
            if not self.tree.can_edit(self.tree.itemFromIndex(index), index.column()):
                return None
            editor = super().createEditor(parent, option, index)
            editor.setObjectName("bwParamEditor")
            return editor

    class ParamTree(QtWidgets.QTreeWidget):
        """The table: groups and features as rows spanning the columns, then their parameters."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwParamTree")
            self.setColumnCount(3)
            self.setHeaderLabels(list(COLUMNS))
            self.setUniformRowHeights(True)
            self.setIndentation(16)
            self.setRootIsDecorated(True)
            self.setAllColumnsShowFocus(True)
            self.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked
                                 | QtWidgets.QAbstractItemView.SelectedClicked
                                 | QtWidgets.QAbstractItemView.EditKeyPressed)
            self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.can_edit = lambda _item, _column: False
            self.setItemDelegate(Delegate(self))
            head = self.header()
            head.setStretchLastSection(False)
            head.setSectionResizeMode(NAME, QtWidgets.QHeaderView.Stretch)
            head.setSectionResizeMode(EXPRESSION, QtWidgets.QHeaderView.Interactive)
            head.setSectionResizeMode(VALUE, QtWidgets.QHeaderView.Interactive)
            head.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            width = self.viewport().width()
            self.header().resizeSection(EXPRESSION, int(width * 0.34))
            self.header().resizeSection(VALUE, int(width * 0.26))

    class ParametersTab(QtWidgets.QWidget):
        """The Parameters tab: filter, table, footer."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxParameters")
            self.controller = None
            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            top = QtWidgets.QWidget(self)
            top.setObjectName("bwParamTop")
            t = QtWidgets.QVBoxLayout(top)
            t.setContentsMargins(10, 8, 10, 4)
            t.setSpacing(4)
            self.filter = QtWidgets.QLineEdit(top)
            self.filter.setObjectName("bwParamFilter")
            self.filter.setPlaceholderText("Filter")
            self.filter.setClearButtonEnabled(True)
            self.filter.setFixedHeight(24)
            t.addWidget(self.filter)
            self.error = QtWidgets.QLabel(top)
            self.error.setObjectName("bwParamError")
            self.error.setWordWrap(True)
            self.error.hide()
            t.addWidget(self.error)
            outer.addWidget(top)
            self.hint = QtWidgets.QLabel(EMPTY_HINT, self)
            self.hint.setObjectName("bwParamHint")
            self.hint.setAlignment(QtCore.Qt.AlignCenter)
            self.hint.setWordWrap(True)
            self.hint.setContentsMargins(12, 24, 12, 24)
            self.hint.hide()
            outer.addWidget(self.hint)
            self.tree = ParamTree(self)
            outer.addWidget(self.tree, 1)
            foot = QtWidgets.QWidget(self)
            foot.setObjectName("bwParamFoot")
            f = QtWidgets.QHBoxLayout(foot)
            f.setContentsMargins(12, 4, 8, 6)
            self.add = self._foot_button(foot, "+  Parameter", "Add a user parameter (to the Parameters spreadsheet)")
            f.addWidget(self.add)
            f.addStretch(1)
            self.sheet = self._foot_button(foot, "Spreadsheet  ›", "Open the Parameters spreadsheet")
            f.addWidget(self.sheet)
            outer.addWidget(foot)
            self.error_timer = QtCore.QTimer(self)
            self.error_timer.setSingleShot(True)
            self.error_timer.setInterval(8000)
            self.error_timer.timeout.connect(self.error.hide)

        def _foot_button(self, foot, text, tip):
            b = QtWidgets.QToolButton(foot)
            b.setObjectName("bwInspectorMore")
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            return b

        def show_error(self, text):
            self.error.setText(text)
            self.error.setVisible(bool(text))
            if text:
                self.error_timer.start()

        def showEvent(self, event):
            no_safe_area(self)
            super().showEvent(event)

        def selection_changed(self):
            if self.controller is not None:
                self.controller.shown()

    class AddDialog(QtWidgets.QDialog):
        """+ Parameter: name, value or expression, unit, comment."""

        def __init__(self, parent, taken, check):
            super().__init__(parent)
            self.setWindowTitle("New parameter")
            self.setObjectName("bwParamDialog")
            self.taken, self.check = set(taken), check
            form = QtWidgets.QFormLayout(self)
            self.name = QtWidgets.QLineEdit(unique_name("param", taken))
            self.name.selectAll()
            self.value = QtWidgets.QLineEdit("10")
            self.value.setPlaceholderText("10, 8 mm or plate_t * 2")
            self.unit = QtWidgets.QComboBox()
            self.unit.setEditable(True)
            for unit in UNITS:
                self.unit.addItem(unit or "no unit", unit)
            self.comment = QtWidgets.QLineEdit()
            form.addRow("Name", self.name)
            form.addRow("Value", self.value)
            form.addRow("Unit", self.unit)
            form.addRow("Comment", self.comment)
            self.message = QtWidgets.QLabel()
            self.message.setObjectName("bwParamError")
            self.message.setWordWrap(True)
            self.message.hide()
            form.addRow(self.message)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(self.try_accept)
            buttons.rejected.connect(self.reject)
            form.addRow(buttons)

        def unit_text(self):
            text = self.unit.currentText().strip()
            return "" if text in ("", "no unit") else text

        def try_accept(self):
            name = self.name.text().strip()
            problem = ""
            if not valid_alias(name):
                problem = "A name is letters, digits and _, starting with a letter (not a cell name like A1)."
            elif name in self.taken:
                problem = f"{name} is taken."
            elif not self.value.text().strip():
                problem = "Give a value or an expression."
            else:
                problem = self.check(name, self.value.text(), self.unit_text())
            if problem:
                self.message.setText(problem)
                self.message.show()
                return
            self.accept()

    return ParametersTab, AddDialog, no_safe_area


_classes = None


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


# ----- the controller ----------------------------------------------------------------------


class Controller:
    """Fills the Parameters tab from the active document and applies edits."""

    def __init__(self, tab):
        from PySide import QtCore

        self.tab = tab
        tab.controller = self
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)  # a recompute sends many changes: one refresh
        self.timer.timeout.connect(self.refresh)
        self.filter_timer = QtCore.QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(120)
        self.filter_timer.timeout.connect(self.apply_filter)
        self.busy = False
        self.updating = False
        self.doc_name = None
        self.signature = None
        self.params = {}  # ident -> Param
        self.items = {}  # ident -> QTreeWidgetItem (built rows only)
        self.groups = []  # [(feature name, label, [Param], item)]
        self.group_items = {}  # feature name -> item
        self.folded = {}  # (doc, feature name) -> open?
        self.user_item = self.model_item = None
        self.build_ms = 0.0
        tree = tab.tree
        tree.can_edit = self.can_edit
        tree.itemChanged.connect(self.item_changed)
        tree.itemExpanded.connect(self.expanded)
        tree.itemCollapsed.connect(self.collapsed)
        tree.customContextMenuRequested.connect(self.context_menu)
        tab.filter.textChanged.connect(lambda _t: self.filter_timer.start())
        tab.add.clicked.connect(self.add_parameter)
        tab.sheet.clicked.connect(self.open_sheet)

    # -- scheduling --------------------------------------------------------------------------
    def schedule(self):
        if self.timer is not None and not self.busy and _alive(self.tab):
            self.timer.start()

    def stop(self):
        for timer in (self.timer, self.filter_timer):
            if timer is not None:
                timer.stop()
        self.timer = None

    def shown(self):
        """The tab is shown or the selection changed: catch up, then show the selected feature."""
        self.refresh()
        self.reveal_selection()

    # -- refresh -----------------------------------------------------------------------------
    def refresh(self):
        from PySide import QtWidgets

        if self.busy or not _alive(self.tab) or not self.tab.isVisible():
            return
        if self.tab.tree.state() == QtWidgets.QAbstractItemView.EditingState:
            self.schedule()  # not under the user's typing
            return
        try:
            self._refresh()
        except Exception:
            import traceback

            import FreeCAD

            FreeCAD.Console.PrintError("BruceWorx Parameters:\n" + traceback.format_exc())

    def _refresh(self):
        import time

        import FreeCAD

        t0 = time.perf_counter()
        doc = FreeCAD.ActiveDocument
        tab = self.tab
        if doc is None:
            self.signature = None
            self.doc_name = None
            tab.tree.clear()
            tab.tree.hide()
            tab.hint.show()
            tab.add.setEnabled(False)
            tab.sheet.setEnabled(False)
            return
        tab.tree.show()
        tab.hint.hide()
        tab.add.setEnabled(True)
        tab.sheet.setEnabled(True)
        sheet = find_sheet(doc)
        user = collect_user(sheet) if sheet is not None else []
        model = collect_model(doc, sheet)
        signature = (doc.Name, tuple(p.signature() for p in user),
                     tuple((o.Name, o.Label, tuple(p.signature() for p in ps)) for o, ps in model))
        params = {p.ident: p for p in user}
        for _o, ps in model:
            params.update((p.ident, p) for p in ps)
        self.params = params
        if signature == self.signature and doc.Name == self.doc_name:
            self.update_texts()
        else:
            self.signature = signature
            self.doc_name = doc.Name
            self.build(user, model)
        self.build_ms = (time.perf_counter() - t0) * 1000

    def build(self, user, model):
        from PySide import QtCore, QtGui, QtWidgets

        tree = self.tab.tree
        scroll = tree.verticalScrollBar().value()
        current = tree.currentItem()
        current_ident = current.data(NAME, QtCore.Qt.UserRole) if current is not None else None
        self.updating = True
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            self.items = {}
            self.group_items = {}
            self.groups = []
            fixed = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            fixed.setPointSizeF(max(9.0, tree.font().pointSizeF() - 1))
            self.fixed = fixed
            band = QtGui.QColor(tree.palette().color(QtGui.QPalette.Text))
            band.setAlphaF(0.06)
            self.band = QtGui.QBrush(band)
            bold = QtGui.QFont(tree.font())
            bold.setBold(True)
            self.user_item = self._group_item(tree, "User parameters", "user", bold)
            self._user_head(self.user_item)
            for p in user:
                self._param_item(self.user_item, p)
            if not user:
                self._note(self.user_item, "None yet: + Parameter adds one.")
            self.user_item.setExpanded(True)
            self.model_item = self._group_item(tree, "Model parameters", "model", bold)
            default = fold_default(len(model))
            for obj, params in model:
                item = QtWidgets.QTreeWidgetItem(self.model_item)
                item.setText(NAME, obj.Label)
                item.setToolTip(NAME, f"{obj.Label} ({obj.Name}) · {len(params)} parameters")
                item.setData(NAME, QtCore.Qt.UserRole, "group\x1f" + obj.Name)
                item.setFirstColumnSpanned(True)
                vp = getattr(obj, "ViewObject", None)
                if vp is not None:
                    try:
                        item.setIcon(NAME, vp.Icon)
                    except Exception:
                        pass
                item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ShowIndicator)
                self.groups.append((obj.Name, obj.Label, params, item))
                self.group_items[obj.Name] = item
                if self.folded.get((self.doc_name, obj.Name), default):
                    self.fill(item)
                    item.setExpanded(True)
            if not model:
                self._note(self.model_item, "No dimensions in this document yet.")
            self.model_item.setExpanded(True)
        finally:
            tree.setUpdatesEnabled(True)
            self.updating = False
        if self.tab.filter.text().strip():
            self.apply_filter()
        if current_ident and current_ident in self.items:
            tree.setCurrentItem(self.items[current_ident])
        tree.verticalScrollBar().setValue(scroll)
        classes()[2](self.tab)

    def _group_item(self, tree, text, key, font):
        from PySide import QtCore, QtWidgets

        item = QtWidgets.QTreeWidgetItem(tree)
        item.setText(NAME, text)
        item.setData(NAME, QtCore.Qt.UserRole, "top\x1f" + key)
        item.setFont(NAME, font)
        item.setFirstColumnSpanned(True)
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        for col in range(3):
            item.setBackground(col, self.band)
        return item

    def _user_head(self, item):
        """The "+" next to the User parameters title."""
        from PySide import QtCore, QtWidgets

        holder = QtWidgets.QWidget()
        holder.setObjectName("bwParamGroupHead")
        h = QtWidgets.QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 4, 0)
        h.setSpacing(4)
        h.addStretch(1)
        plus = QtWidgets.QToolButton(holder)
        plus.setObjectName("bwParamPlus")
        plus.setText("+")
        plus.setAutoRaise(True)
        plus.setCursor(QtCore.Qt.PointingHandCursor)
        plus.setToolTip("Add a user parameter")
        plus.clicked.connect(self.add_parameter)
        h.addWidget(plus)
        self.tab.tree.setItemWidget(item, VALUE, holder)

    def _note(self, parent, text):
        from PySide import QtCore, QtGui, QtWidgets

        item = QtWidgets.QTreeWidgetItem(parent)
        item.setText(NAME, text)
        item.setFirstColumnSpanned(True)
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        item.setData(NAME, QtCore.Qt.UserRole, "note")
        item.setForeground(NAME, self.tab.tree.palette().brush(QtGui.QPalette.PlaceholderText))
        return item

    def _param_item(self, parent, p):
        from PySide import QtCore, QtWidgets

        item = QtWidgets.QTreeWidgetItem(parent)
        item.setData(NAME, QtCore.Qt.UserRole, p.ident)
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if p.editable:
            flags |= QtCore.Qt.ItemIsEditable
        item.setFlags(flags)
        if p.kind == USER:
            item.setFont(NAME, self.fixed)
        item.setFont(EXPRESSION, self.fixed)
        self._texts(item, p)
        self.items[p.ident] = item
        return item

    def _texts(self, item, p):
        item.setText(NAME, p.label)
        item.setText(EXPRESSION, p.expression)
        item.setText(VALUE, p.value)
        item.setToolTip(NAME, p.tip or p.label)
        item.setToolTip(EXPRESSION, (p.expression or "Type an expression (plate_t * 2) to bind, "
                                     "or a value to set") if p.editable else "A reference dimension")
        item.setToolTip(VALUE, p.value if p.editable else "A reference dimension")

    def fill(self, item):
        """A feature group's rows, made when first opened."""
        from PySide import QtCore

        if item.childCount():
            return
        name = str(item.data(NAME, QtCore.Qt.UserRole) or "").split("\x1f")[-1]
        for feature, _label, params, group_item in self.groups:
            if group_item is item and feature == name:
                self.updating = True
                try:
                    for p in params:
                        self._param_item(item, p)
                finally:
                    self.updating = False
                return

    def update_texts(self):
        """Same rows, new values and expressions (a recompute, an undo)."""
        self.updating = True
        try:
            for ident, item in self.items.items():
                p = self.params.get(ident)
                if p is not None and _alive(item):
                    self._texts(item, p)
        finally:
            self.updating = False

    def expanded(self, item):
        from PySide import QtCore

        data = str(item.data(NAME, QtCore.Qt.UserRole) or "")
        if data.startswith("group\x1f"):
            self.fill(item)
            if not self.updating:
                self.folded[(self.doc_name, data.split("\x1f")[1])] = True

    def collapsed(self, item):
        from PySide import QtCore

        data = str(item.data(NAME, QtCore.Qt.UserRole) or "")
        if data.startswith("group\x1f") and not self.updating:
            self.folded[(self.doc_name, data.split("\x1f")[1])] = False

    # -- filter ------------------------------------------------------------------------------
    def apply_filter(self):
        from PySide import QtCore

        if not _alive(self.tab) or self.user_item is None or not _alive(self.user_item):
            return
        needle = self.tab.filter.text().strip()
        tree = self.tab.tree
        tree.setUpdatesEnabled(False)
        self.updating = True
        try:
            for i in range(self.user_item.childCount()):
                child = self.user_item.child(i)
                p = self.params.get(child.data(NAME, QtCore.Qt.UserRole))
                child.setHidden(bool(needle) and (p is None or not matches(needle, p.label, p.expression)))
            for feature, label, params, item in self.groups:
                if not needle:
                    item.setHidden(False)
                    for i in range(item.childCount()):
                        item.child(i).setHidden(False)
                    item.setExpanded(self.folded.get((self.doc_name, feature), fold_default(len(self.groups))))
                    continue
                whole = matches(needle, label)
                hits = {p.ident for p in params if whole or matches(needle, p.label, p.expression)}
                item.setHidden(not hits)
                if hits:
                    self.fill(item)
                    for i in range(item.childCount()):
                        child = item.child(i)
                        child.setHidden(child.data(NAME, QtCore.Qt.UserRole) not in hits)
                    item.setExpanded(True)
        finally:
            self.updating = False
            tree.setUpdatesEnabled(True)

    def reveal_selection(self):
        """Open and mark the group of the selected feature (the document selection stays as it is)."""
        import FreeCADGui
        from PySide import QtWidgets

        if not self.group_items:
            return
        try:
            selected = [s.Object for s in FreeCADGui.Selection.getSelectionEx("", 0)]
            resolved = []
            for sel in FreeCADGui.Selection.getSelectionEx("", 0):
                for sub in sel.SubElementNames or []:
                    found = sel.Object.getSubObject(sub, retType=1)
                    if found is not None:
                        resolved.append(found)
        except Exception:
            return
        for obj in resolved + selected:
            item = self.group_items.get(getattr(obj, "Name", ""))
            if item is not None and _alive(item) and not item.isHidden():
                self.fill(item)
                item.setExpanded(True)
                tree = self.tab.tree
                tree.setCurrentItem(item)
                tree.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtTop)
                return

    # -- edits -------------------------------------------------------------------------------
    def can_edit(self, item, column):
        from PySide import QtCore

        p = self.params.get(item.data(NAME, QtCore.Qt.UserRole)) if item is not None else None
        if p is None or not p.editable:
            return False
        if column == NAME:
            return p.kind == USER
        if column == VALUE:
            return not p.expression or (p.kind == USER and is_literal(p.expression))
        return True

    def item_changed(self, item, column):
        from PySide import QtCore

        if self.updating or self.busy:
            return
        p = self.params.get(item.data(NAME, QtCore.Qt.UserRole))
        if p is None:
            return
        text = item.text(column).strip()
        before = (p.label, p.expression, p.value)[column]
        if text == before:
            return
        if column == NAME:
            ok = self.rename(p, text) if p.kind == USER else False
        elif column == EXPRESSION:
            ok = self.set_expression(p, text)
        else:
            ok = self.set_value(p, text)
        if not ok:
            self.updating = True
            try:
                self._texts(item, p)
            finally:
                self.updating = False
        self.signature = None if column == NAME else self.signature
        self.schedule()

    def _doc(self):
        import FreeCAD

        return FreeCAD.getDocument(self.doc_name) if self.doc_name else FreeCAD.ActiveDocument

    def commit(self, label, field, change, doc=None):
        """One undo step "<label>: <field>": ``change()`` then a recompute. A raised error undoes it.
        ``doc``: the document the step belongs to (the sheet's, when it is in another file)."""
        import FreeCAD

        import bruceworx_inspector as bi

        doc = doc or self._doc()
        if doc is None:
            return False
        name = bi.transaction_name([label], field)
        self.busy = True
        doc.openTransaction(name)
        try:
            change(doc)
            doc.recompute()
        except Exception as exc:
            doc.abortTransaction()
            try:
                doc.recompute()
            except Exception:
                pass
            self.tab.show_error(f"{name}: {exc}")
            FreeCAD.Console.PrintError(f"Parameters: {name}: {exc}\n")
            ok = False
        else:
            doc.commitTransaction()
            self.tab.show_error("")
            ok = True
            sheet = self._sheet()
            if sheet is not None and sheet.Document is doc:
                try:
                    rebuilt = update_dependents(sheet)
                except Exception as exc:  # noqa: BLE001
                    FreeCAD.Console.PrintWarning(f"Parameters: the parts reading {sheet.Label} were not rebuilt: {exc}\n")
                else:
                    if rebuilt:
                        FreeCAD.Console.PrintMessage(f"Parameters: rebuilt {', '.join(rebuilt)}\n")
        finally:
            self.busy = False
        self.schedule()
        return ok

    def _aliases(self):
        return {p.key for p in self.params.values() if p.kind == USER}

    def _sheet(self):
        return find_sheet(self._doc())

    def set_expression(self, p, text):
        kind, body = parse_edit(text)
        if kind == "value":
            if p.kind == USER:
                return self._set_cell(p, body)
            return self.set_value(p, body, unbind=True)
        if p.kind == USER:
            if kind == "clear":
                self.tab.show_error(f"{p.key} needs a value (⋯ Delete removes it).")
                return False
            return self._set_cell(p, "=" + body)
        doc = self._doc()
        obj = doc.getObject(p.owner) if doc is not None else None
        if obj is None:
            return False
        if kind == "clear":
            if not p.expr_key or not p.expression:
                return False
            return self.commit(obj.Label, p.label, lambda _d: obj.setExpression(p.expr_key, None))
        sheet = self._sheet()
        expr = qualify(body, self._aliases(), sheet_ref(sheet, obj.Document))
        try:
            obj.evalExpression(expr)
        except Exception as exc:
            self.tab.show_error(f"{p.label}: {exc}")
            return False
        return self.commit(obj.Label, p.label, lambda _d: obj.setExpression(p.expr_key or p.key, expr))

    def set_value(self, p, text, unbind=False):
        import FreeCAD

        kind, body = parse_edit(text)
        if kind == "expression":
            return self.set_expression(p, text)
        if kind == "clear":
            return False
        if p.kind == USER:
            return self._set_cell(p, body)
        doc = self._doc()
        obj = doc.getObject(p.owner) if doc is not None else None
        if obj is None:
            return False
        try:
            if p.unit_kind in INTEGER_TYPES:
                value = int(round(float(body)))
            else:
                if p.kind == CONSTRAINT and p.unit_kind == "Angle" and not has_unit(body):
                    body += " deg"
                value = FreeCAD.Units.Quantity(body)
        except Exception as exc:
            self.tab.show_error(f"{p.label}: {exc}")
            return False

        def change(_doc):
            if unbind and p.expression and p.expr_key:
                obj.setExpression(p.expr_key, None)
            if p.kind == CONSTRAINT:
                obj.setDatum(int(p.key), value)
            else:
                setattr(obj, p.key, value)

        return self.commit(obj.Label, p.label, change)

    def _set_cell(self, p, content):
        sheet = self._sheet()
        if sheet is None:
            return False
        cell = p.expr_key or sheet.getCellFromAlias(p.key)
        if content and not content.startswith("="):
            content = cell_content(content)

        def change(_doc):
            sheet.set(cell, content)
            _doc.recompute()
            if "Invalid" in sheet.State:
                raise ValueError(f"{content.lstrip('=')} does not evaluate")

        return self.commit(sheet.Label, p.key, change, doc=sheet.Document)

    def rename(self, p, new):
        sheet = self._sheet()
        if sheet is None:
            return False
        if not valid_alias(new):
            self.tab.show_error(f"{new}: a name is letters, digits and _, starting with a letter.")
            return False
        if new in self._aliases():
            self.tab.show_error(f"{new} is taken.")
            return False
        cell = p.expr_key or sheet.getCellFromAlias(p.key)
        row = cell_rc(cell)[0]
        old = p.key

        def change(_doc):
            sheet.setAlias(cell, new)
            label_cell = f"A{row}"
            if cell.startswith("B") and (sheet.getContents(label_cell) or "") == old:
                sheet.set(label_cell, new)

        return self.commit(sheet.Label, f"rename {old}", change, doc=sheet.Document)

    def check_new(self, name, text, unit):
        """The add dialog's check of an expression (bare names are the sheet's aliases)."""
        kind, body = parse_edit(text)
        if kind != "expression":
            return ""
        sheet = self._sheet()
        if sheet is None:
            return ""
        try:
            sheet.evalExpression(body)
        except Exception as exc:
            return str(exc)
        return ""

    def add_parameter(self):
        _Tab, AddDialog, _nsa = classes()
        doc = self._doc()
        if doc is None:
            return
        taken = self._aliases()
        dialog = AddDialog(self.tab, taken, self.check_new)
        if dialog.exec_() != dialog.Accepted:
            return
        self.create(dialog.name.text().strip(), dialog.value.text(), dialog.unit_text(),
                    dialog.comment.text().strip())

    def create(self, name, text, unit="", comment=""):
        """A user parameter in a new row of the sheet (made first if missing): one undo step."""
        content = cell_content(text, unit)

        existing = self._sheet()

        def change(d):
            sheet = existing or make_sheet(d)
            row = next_row(sheet.getUsedCells())
            sheet.set(f"A{row}", name)
            sheet.set(f"B{row}", content)
            sheet.setAlias(f"B{row}", name)
            if comment:
                sheet.set(f"C{row}", comment)
            d.recompute()
            if "Invalid" in sheet.State:
                raise ValueError(f"{content.lstrip('=')} does not evaluate")

        self.signature = None
        return self.commit(SHEET_NAME, f"add {name}", change, doc=existing.Document if existing is not None else None)

    def delete_parameter(self, p):
        doc = self._doc()
        sheet = self._sheet()
        if doc is None or sheet is None:
            return
        users = []
        for obj in list(sheet.Document.Objects) + dependents(sheet):
            ref = sheet_ref(sheet, obj.Document)
            for path, expr in getattr(obj, "ExpressionEngine", ()) or ():
                if uses_alias(expr, p.key, ref, sheet.Label, bare=obj is sheet):
                    users.append(f"{obj.Label}.{path.lstrip('.')}" if obj is not sheet else path.lstrip("."))
        for cell, alias in sheet_aliases(sheet):
            if alias != p.key and uses_alias(sheet.getContents(cell) or "", p.key, bare=True):
                users.append(alias)
        if users:
            self.tab.show_error(f"{p.key} is used by {', '.join(sorted(set(users))[:4])}: change those first.")
            return
        cell = p.expr_key or sheet.getCellFromAlias(p.key)
        row = cell_rc(cell)[0]

        def change(_doc):
            sheet.setAlias(cell, "")
            sheet.clear(cell)
            if cell.startswith("B"):
                for other in (f"A{row}", f"C{row}"):
                    if sheet.getContents(other):
                        sheet.clear(other)

        self.signature = None
        self.commit(sheet.Label, f"delete {p.key}", change, doc=sheet.Document)

    # -- actions -----------------------------------------------------------------------------
    def open_sheet(self):
        import FreeCADGui

        doc = self._doc()
        if doc is None:
            return
        sheet = find_sheet(doc)
        if sheet is None:
            self.signature = None
            if not self.commit(SHEET_NAME, "new spreadsheet", lambda d: make_sheet(d)):
                return
            sheet = find_sheet(doc)
        try:
            FreeCADGui.getDocument(sheet.Document.Name).setEdit(sheet.Name)
        except Exception:
            pass

    def open_feature(self, name):
        doc = self._doc()
        obj = doc.getObject(name) if doc is not None else None
        if obj is None:
            return
        try:
            import bruceworx_picktrace

            bruceworx_picktrace.open_feature(obj)
        except Exception:
            import FreeCADGui

            FreeCADGui.getDocument(obj.Document.Name).setEdit(obj.Name)

    def context_menu(self, pos):
        from PySide import QtCore, QtGui, QtWidgets

        tree = self.tab.tree
        item = tree.itemAt(pos)
        if item is None:
            return
        data = str(item.data(NAME, QtCore.Qt.UserRole) or "")
        menu = QtWidgets.QMenu(tree)
        p = self.params.get(data)
        if data.startswith("group\x1f"):
            name = data.split("\x1f")[1]
            menu.addAction("Edit feature", lambda: self.open_feature(name))
        elif p is not None:
            if p.kind == USER:
                menu.addAction("Rename", lambda: tree.editItem(item, NAME))
                menu.addAction("Delete", lambda: self.delete_parameter(p))
            else:
                menu.addAction("Edit feature", lambda: self.open_feature(p.owner))
                if p.expression and p.editable:
                    menu.addAction("Clear expression", lambda: self.set_expression(p, ""))
                doc = self._doc()
                obj = doc.getObject(p.owner) if doc is not None else None
                if obj is not None:
                    ref = f"{obj.Name}.{p.expr_key or p.key}"
                    menu.addAction(f"Copy reference ({ref})",
                                   lambda: QtWidgets.QApplication.clipboard().setText(ref))
            if p.editable:
                menu.addAction("Edit expression", lambda: tree.editItem(item, EXPRESSION))
        else:
            return
        menu.exec_(tree.viewport().mapToGlobal(pos))


# ----- install -----------------------------------------------------------------------------

_controller = None
_installed = False
_observer = None


def controller():
    return _controller


def _schedule(*_args):
    if _controller is not None:
        _controller.schedule()


class _DocObserver:
    """Documents and objects come and go, recompute, undo: the tab follows them while visible."""

    def slotCreatedObject(self, obj):
        _schedule()

    def slotDeletedObject(self, obj):
        _schedule()

    def slotChangedObject(self, obj, prop):
        if prop in ("Label", "ExpressionEngine", "Constraints", "cells", "Group"):
            _schedule()

    def slotRecomputedDocument(self, doc):
        _schedule()

    def slotUndoDocument(self, doc):
        _schedule()

    def slotRedoDocument(self, doc):
        _schedule()

    def slotActivateDocument(self, doc):
        _schedule()

    def slotDeletedDocument(self, doc):
        _schedule()


def _factory(parent):
    global _controller
    ParametersTab, _AddDialog, _nsa = classes()
    if _controller is not None:
        _controller.stop()
    tab = ParametersTab(parent)
    _controller = Controller(tab)
    return tab


def _shutdown():
    global _controller
    import FreeCAD

    if _observer is not None:
        try:
            FreeCAD.removeDocumentObserver(_observer)
        except Exception:
            pass
    if _controller is not None:
        _controller.stop()
        _controller = None


def install():
    """The Parameters tab of the right panel (bruceworx_rightpanel), and the observer that keeps it current."""
    global _installed, _observer
    if _installed:
        return
    import FreeCAD
    from PySide import QtWidgets

    import bruceworx_rightpanel

    if not bruceworx_rightpanel.enabled():
        return
    bruceworx_rightpanel.register(TAB, _factory)
    _observer = _DocObserver()
    FreeCAD.addDocumentObserver(_observer)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _installed = True
