# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sheet metal (MOD-06): the vault's rules on a part, and the shop DXF of its flat.

The SheetMetal add-on is bundled (package/bruceworx/vendor, Mod/SheetMetal); its commands
fill the Design -> Sheet Metal tab. This module adds three commands to that tab:

* BruceWorx_SheetMetalRule picks the part's rule. The rule's values go into a per-document
  App::VarSet named SheetMetalRule, and the add-on's properties are bound to it by
  expressions: Thickness, Radius / radius, reliefw, reliefd on every wall, KFactor on the
  unfold. Changing the rule updates the whole part in one recompute; a feature keeps its
  own value when its expression is cleared (it is not bound again). The rule name and
  Stock ("Sheet 1.5 Al 5052-H32") go on the part's properties.
* BruceWorx_SheetMetalRules edits the vault's rule table and its DXF export profile.
* BruceWorx_ExportFlat writes P/N-REV.dxf (CUT, BEND_UP / BEND_DOWN, ETCH) and the bend
  table (a BendTable spreadsheet), through bruceworx.flat_export, which also runs headless.

Units: rules are stored in mm. The rule table shows the vault's units (vault_units), a
part's rule and its DXF the document's units (document_units).

Preferences/BruceWorx/SheetMetalAddon (default on) loads the add-on at all (read by
FreeCADInit.py). Preferences/BruceWorx/SheetMetalAutoRule (default on): the first
sheet-metal wall of a document without a rule gets the vault's default rule.
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ADDON_PREF = "SheetMetalAddon"
AUTO_PREF = "SheetMetalAutoRule"
NOTED_PREF = "SheetMetalUserCopyNoted"  # the user copy's path once the note was shown
VARSET = "SheetMetalRule"
HERE = os.path.dirname(os.path.abspath(__file__))
BEND_SHEET = "BendTable"

# VarSet property -> (type, rule field, tool tip)
RULE_PROPS = (
    ("RuleName", "App::PropertyString", "name", "The vault's sheet-metal rule"),
    ("Material", "App::PropertyString", "material", "Material of the rule"),
    ("Gauge", "App::PropertyString", "gauge", "Gauge alias of the thickness"),
    ("Thickness", "App::PropertyLength", "thickness", "Sheet thickness T"),
    ("Radius", "App::PropertyLength", "radius", "Inner bend radius R"),
    ("KFactor", "App::PropertyFloat", "k_factor", "K-factor (ANSI)"),
    ("ReliefWidth", "App::PropertyLength", "relief_width", "Bend relief width"),
    ("ReliefDepth", "App::PropertyLength", "relief_depth", "Bend relief depth"),
    ("MinFlange", "App::PropertyLength", "min_flange", "Shortest flange"),
)
BOUND_PROP = "Bound"  # "Object.property" entries this module bound (a missing expression = an override)
# add-on property -> VarSet property
BINDINGS = {"Thickness": "Thickness", "thickness": "Thickness", "Radius": "Radius", "radius": "Radius",
            "reliefw": "ReliefWidth", "reliefd": "ReliefDepth", "KFactor": "KFactor", "kfactor": "KFactor"}
_SM_MODULES = ("SheetMetal", "ExtrudedCutout", "SketchOnSheetMetalCmd")
_SKIP_MODULES = ("SheetMetalFormingCmd",)  # a forming tool's thickness is the tool's, not the sheet's
_BASE_CLASSES = ("SMBaseBend", "SMBaseShape", "SMFromSolid")

_state = {"observer": None, "pending": set(), "restoring": set()}


def _rules():
    from bruceworx import sheetmetal_rules

    return sheetmetal_rules


def _export():
    from bruceworx import flat_export

    return flat_export


# --- the add-on ---------------------------------------------------------------------------


def addon_enabled() -> bool:
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(ADDON_PREF, True))


def addon_dirs() -> list:
    """Folders FreeCAD found for SheetMetal; the last one is the one loaded."""
    import FreeCAD

    return list(getattr(FreeCAD, "__fallback_mods__", {}).get("sheetmetal", []))


def user_copy(dirs=None, home=None):
    """The user's own SheetMetal folder when it replaces the bundled one, else None."""
    import FreeCAD

    dirs = addon_dirs() if dirs is None else dirs
    home = os.path.realpath(home or os.path.join(FreeCAD.getHomePath(), "Mod"))
    if len(dirs) < 2:
        return None
    winner = os.path.realpath(dirs[-1])
    return None if winner.startswith(home + os.sep) else dirs[-1]


def note_user_copy():
    """Once per user copy: say in the status bar that it replaces the bundled add-on."""
    import FreeCAD
    import FreeCADGui

    found = user_copy()
    params = FreeCAD.ParamGet(PREFS_PATH)
    if not found or params.GetString(NOTED_PREF, "") == found:
        return False
    params.SetString(NOTED_PREF, found)
    text = f"SheetMetal from {found} replaces the bundled sheet-metal add-on (FreeCAD loads the user copy)."
    FreeCAD.Console.PrintMessage(text + "\n")
    try:
        FreeCADGui.getMainWindow().statusBar().showMessage(text, 15000)
    except Exception:
        pass
    return True


# --- units and the vault ----------------------------------------------------------------------


def document_units(doc=None) -> str:
    """"mm" or "in": the document's unit schema (FreeCAD >= 1.0), else the app's."""
    import FreeCAD

    smr = _rules()
    doc = doc or FreeCAD.ActiveDocument
    name = ""
    if doc is not None and "UnitSystem" in doc.PropertiesList:
        name = str(doc.UnitSystem)
    if not name:
        try:
            name = FreeCAD.Units.listSchemas(FreeCAD.Units.getSchema())
        except Exception:
            name = "mm"
    return smr.norm_units(name)


def vault_root() -> str:
    try:
        return __import__("bruceworx_drawing").vault_root()
    except Exception:
        return ""


def vault_units(root=None) -> str:
    """"mm" or "in" for the vault: its own units setting when the vault has one (the hook:
    bruceworx.vault_setup.vault_units(root) -> "mm" | "in"), else its drawing standards' units."""
    root = vault_root() if root is None else root
    if not root:
        return document_units()
    try:
        from bruceworx import vault_setup

        hook = getattr(vault_setup, "vault_units", None)
        if hook is not None:
            return _rules().norm_units(hook(root))
    except Exception:
        pass
    try:
        from bruceworx import drawing_standards

        return _rules().norm_units(drawing_standards.load(root).units)
    except Exception:
        return "mm"


def load_rules(root=None):
    root = vault_root() if root is None else root
    return _rules().load(root, vault_units(root))


# --- rule on a document --------------------------------------------------------------------------


def is_feature(obj) -> bool:
    """A SheetMetal add-on feature (wall, flange, hem, unfold, ...)."""
    proxy = getattr(obj, "Proxy", None)
    module = type(proxy).__module__ if proxy is not None else ""
    return module.startswith(_SM_MODULES) and module not in _SKIP_MODULES


def is_base(obj) -> bool:
    proxy = getattr(obj, "Proxy", None)
    return is_feature(obj) and type(proxy).__name__ in _BASE_CLASSES


def bindings_for(obj) -> list:
    """[(property, VarSet property)] the object has."""
    if not is_feature(obj):
        return []
    have = set(obj.PropertiesList)
    return [(prop, var) for prop, var in BINDINGS.items() if prop in have]


def rule_varset(doc):
    """The document's SheetMetalRule VarSet, or None."""
    found = doc.getObject(VARSET)
    if found is not None and "RuleName" in found.PropertiesList:
        return found
    return next((o for o in doc.Objects if o.TypeId == "App::VarSet" and "RuleName" in o.PropertiesList), None)


def current_rule_name(doc) -> str:
    varset = rule_varset(doc)
    return varset.RuleName if varset is not None else ""


def _ensure_varset(doc):
    varset = rule_varset(doc)
    if varset is None:
        varset = doc.addObject("App::VarSet", VARSET)
        varset.Label = "Sheet-metal rule"
    for name, kind, _field, tip in RULE_PROPS:
        if name not in varset.PropertiesList:
            varset.addProperty(kind, name, "Rule", tip)
    if BOUND_PROP not in varset.PropertiesList:
        varset.addProperty("App::PropertyStringList", BOUND_PROP, "Rule", "Properties bound to this rule")
        varset.setEditorMode(BOUND_PROP, 2)
    return varset


def _expression(obj, prop):
    return next((expr for path, expr in obj.ExpressionEngine if path == prop), None)


def bind_features(doc, varset=None, objects=None) -> list:
    """Bind the add-on's properties to the rule; returns "Object.property" newly bound. A property
    bound before whose expression is gone was overridden by the user and is left alone."""
    varset = varset or rule_varset(doc)
    if varset is None:
        return []
    bound = list(getattr(varset, BOUND_PROP, []) or [])
    known = set(bound)
    added = []
    for obj in objects if objects is not None else doc.Objects:
        for prop, var in bindings_for(obj):
            key = f"{obj.Name}.{prop}"
            if key in known:
                continue
            obj.setExpression(prop, f"{varset.Name}.{var}")
            if prop == "KFactor":  # the unfold: one manual K, ANSI
                if "MaterialSheet" in obj.PropertiesList:
                    obj.MaterialSheet = "_manual"
                if "KFactorStandard" in obj.PropertiesList:
                    obj.KFactorStandard = "ansi"
            bound.append(key)
            known.add(key)
            added.append(key)
    if added:
        varset.Bound = bound
    return added


def part_of(obj):
    """The App::Part around obj (else its Body, else obj): where the part properties live."""
    if obj is None:
        return None
    for parent in obj.InList:
        if parent.TypeId == "App::Part":
            return parent
    for parent in obj.InList:
        if parent.TypeId == "PartDesign::Body":
            return part_of(parent) or parent
    return obj


def set_part_properties(target, rule, units="mm"):
    """SheetMetalRule and Stock on the part; MaterialSpec too when the vault has it and it is empty."""
    if target is None:
        return
    smr = _rules()
    for name, tip in (("SheetMetalRule", "Sheet-metal rule (MOD-06)"), ("Stock", "Stock, from the sheet-metal rule")):
        if name not in target.PropertiesList:
            target.addProperty("App::PropertyString", name, "BruceWorx", tip)
    target.SheetMetalRule = rule.name
    target.Stock = smr.stock_text(rule, units)
    if "MaterialSpec" in target.PropertiesList and not str(target.MaterialSpec).strip():
        target.MaterialSpec = rule.material


def _feature_parts(doc):
    parts = []
    for obj in doc.Objects:
        if is_feature(obj):
            part = part_of(obj)
            if part is not None and part not in parts:
                parts.append(part)
    return parts


def apply_rule(doc, rule, part=None, units=None):
    """Put rule into the document's SheetMetalRule VarSet, bind every sheet-metal feature to it,
    write the part properties and recompute. Returns the VarSet."""
    units = units or document_units(doc)
    varset = _ensure_varset(doc)
    for name, _kind, field, _tip in RULE_PROPS:
        setattr(varset, name, getattr(rule, field))
    bind_features(doc, varset)
    for target in [part] if part is not None else _feature_parts(doc):
        set_part_properties(target, rule, units)
    doc.recompute()
    return varset


def apply_rule_command(doc, rule, part=None):
    doc.openTransaction(f"Apply sheet-metal rule {rule.name}")
    try:
        apply_rule(doc, rule, part)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


# --- bend table (DRW-07) -----------------------------------------------------------------------------


def write_bend_table(doc, rows, title=""):
    """rows (header first) into the BendTable spreadsheet (made when missing); returns it."""
    sheet = doc.getObject(BEND_SHEET)
    if sheet is None or sheet.TypeId != "Spreadsheet::Sheet":
        sheet = doc.addObject("Spreadsheet::Sheet", BEND_SHEET)
        sheet.Label = "Bend table"
    sheet.clearAll()
    columns = "ABCDEF"
    start = 1
    if title:
        sheet.set("A1", "'" + title)
        start = 2
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.set(f"{columns[c]}{start + r}", "'" + str(value))
    sheet.recompute()
    return sheet


# --- export ------------------------------------------------------------------------------------------------


def _part_values(obj):
    part = part_of(obj)
    number = str(getattr(part, "PartNumber", "") or "") if part is not None else ""
    revision = str(getattr(part, "Revision", "") or "") if part is not None else ""
    return number, revision


def flat_source(selection=None, doc=None):
    """(object, face) to unfold: a selected unfold, a selected sheet-metal face or feature, else the
    document's unfold, else its last sheet-metal feature. (None, None) when there is nothing."""
    fe = _export()
    for sel in selection or []:
        obj = sel.Object
        if fe.is_unfold(obj):
            return obj, None
        faces = [n for n in sel.SubElementNames if n.startswith("Face")]
        if is_feature(obj):
            return obj, (faces[0] if faces else None)
    if doc is None:
        return None, None
    found = fe.find_unfold(doc)
    if found is not None:
        return found, None
    features = [o for o in doc.Objects if is_feature(o) and not fe.is_unfold(o) and o.Shape.Solids]
    tips = [o for o in features if not any(is_feature(p) and p in features for p in o.InList)]
    return ((tips or features)[-1], None) if features else (None, None)


def export_flat(obj, path, face=None, units=None, profile=None, doc=None):
    """Write the shop DXF of obj to path and the bend table into the document. Returns the flat."""
    fe = _export()
    doc = doc or obj.Document
    units = units or document_units(doc)
    if profile is None:
        profile = load_rules().profile
    varset = rule_varset(doc)
    k_factor = None
    if not fe.is_unfold(obj) and varset is not None:
        k_factor = varset.KFactor
    number, revision = _part_values(obj)
    pattern = fe.export_dxf(obj, path, units=units, profile=profile, etch=fe.etch_text(number, revision),
                            face=face, k_factor=k_factor)
    smr = _rules()
    title = f"Rule {varset.RuleName}" if varset is not None and varset.RuleName else ""
    size = f"Flat {smr.format_length(pattern.width, units, unit=False)} x " \
           f"{smr.format_length(pattern.height, units)}, T {smr.format_length(pattern.thickness, units)}"
    write_bend_table(doc, fe.bend_table(pattern, units), "  ".join(t for t in (title, size) if t))
    doc.recompute()
    return pattern


# --- GUI ---------------------------------------------------------------------------------------------------


def _main_window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _message(title, text, warn=False):
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox.warning if warn else QtWidgets.QMessageBox.information
    box(_main_window(), title, text)


def _status(text, ms=8000):
    try:
        _main_window().statusBar().showMessage(text, ms)
    except Exception:
        pass


def _make_dialogs():
    from PySide import QtCore, QtWidgets

    smr = _rules()

    class RulePicker(QtWidgets.QDialog):
        """Pick the document's rule from the vault's rules (not the retired ones)."""

        def __init__(self, doc, rules, units, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxSheetMetalRule")
            self.setWindowTitle("Sheet-Metal Rule")
            self.rules = [r for r in rules.rules if not r.retired]
            self.units = units
            lay = QtWidgets.QVBoxLayout(self)
            current = current_rule_name(doc)
            hint = QtWidgets.QLabel(
                f"The rule sets thickness, bend radius, K-factor and relief of every sheet-metal feature in "
                f"{doc.Label}. Lengths in {units}." + (f" Now: {current}." if current else ""))
            hint.setWordWrap(True)
            lay.addWidget(hint)
            self.combo = QtWidgets.QComboBox()
            for rule in self.rules:
                self.combo.addItem(smr.label(rule, units), rule.name)
            wanted = current or (smr.default_rule(rules).name if smr.default_rule(rules) else "")
            index = self.combo.findData(wanted)
            self.combo.setCurrentIndex(max(0, index))
            lay.addWidget(self.combo)
            self.details = QtWidgets.QLabel()
            self.details.setObjectName("bwSheetMetalRuleDetails")
            self.details.setWordWrap(True)
            lay.addWidget(self.details)
            self.combo.currentIndexChanged.connect(self._show)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Apply")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            edit = buttons.addButton("Edit Rules…", QtWidgets.QDialogButtonBox.ResetRole)
            edit.clicked.connect(lambda: (self.reject(), QtCore.QTimer.singleShot(0, show_rules_dialog)))
            lay.addWidget(buttons)
            self._show()

        def rule(self):
            name = self.combo.currentData()
            return next((r for r in self.rules if r.name == name), None)

        def _show(self, *_args):
            rule = self.rule()
            if rule is None:
                self.details.setText("The vault has no rules: add one with Edit Rules.")
                return
            u = self.units
            self.details.setText(
                f"T {smr.format_length(rule.thickness, u)}    R {smr.format_length(rule.radius, u)}    "
                f"K {smr.format_number(rule.k_factor)}\n"
                f"Relief {smr.format_length(rule.relief_width, u, unit=False)} x "
                f"{smr.format_length(rule.relief_depth, u)}    Min flange {smr.format_length(rule.min_flange, u)}\n"
                f"Stock: {smr.stock_text(rule, u)}")

    class RulesDialog(QtWidgets.QDialog):
        """The vault's rule table (add, duplicate, retire; never delete) and its DXF profile."""

        COLUMNS = ("name", "material", "thickness", "radius", "k_factor", "relief_width", "relief_depth",
                   "min_flange", "gauge", "note")

        def __init__(self, root, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxSheetMetalRules")
            self.setWindowTitle("Sheet-Metal Rules")
            self.root = root
            self.units = vault_units(root)
            self.saved, self.issues = smr.load_with_issues(root, self.units)
            self.saved_names = {r.name.lower() for r in self.saved.rules} if smr.exists(root) else set()
            lay = QtWidgets.QVBoxLayout(self)
            where = root or "no vault open: the starting rules are shown and cannot be saved"
            hint = QtWidgets.QLabel(f"Rules of {where}. Lengths in {self.units} (a thickness may be typed as a "
                                    "gauge, e.g. 16 ga). Retire a rule instead of deleting it: parts keep theirs.")
            hint.setWordWrap(True)
            lay.addWidget(hint)
            self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS) + 1)
            self.table.setObjectName("bwSheetMetalRulesTable")
            headers = [smr.LABELS[c] + (f" ({self.units})" if c in ("thickness", "radius", "min_flange") else "")
                       for c in self.COLUMNS] + ["Retired"]
            self.table.setHorizontalHeaderLabels(headers)
            self.table.verticalHeader().setVisible(False)
            for column, key in enumerate(self.COLUMNS):
                self.table.horizontalHeaderItem(column).setToolTip(smr.DESCRIPTIONS[key])
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.itemChanged.connect(self._edited)
            lay.addWidget(self.table, 1)
            row = QtWidgets.QHBoxLayout()
            for text, slot in (("Add", self.add), ("Duplicate", self.duplicate)):
                button = QtWidgets.QPushButton(text)
                button.clicked.connect(slot)
                row.addWidget(button)
            row.addStretch(1)
            row.addWidget(QtWidgets.QLabel("Default rule"))
            self.default = QtWidgets.QComboBox()
            self.default.setMinimumContentsLength(12)
            row.addWidget(self.default)
            lay.addLayout(row)
            box = QtWidgets.QGroupBox("Shop DXF (Export Flat)")
            form = QtWidgets.QVBoxLayout(box)
            form.addWidget(QtWidgets.QLabel("CUT (outlines) is always written."))
            self.bend_layers = QtWidgets.QCheckBox("Bend lines on BEND_UP / BEND_DOWN")
            self.etch = QtWidgets.QCheckBox("Part number and revision on ETCH")
            self.bend_layers.setChecked(bool(self.saved.profile.get("bend_layers", True)))
            self.etch.setChecked(bool(self.saved.profile.get("etch", True)))
            form.addWidget(self.bend_layers)
            form.addWidget(self.etch)
            lay.addWidget(box)
            self.problems = QtWidgets.QLabel()
            self.problems.setObjectName("bwSheetMetalRulesIssues")
            self.problems.setWordWrap(True)
            lay.addWidget(self.problems)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
            self.save_button = buttons.button(QtWidgets.QDialogButtonBox.Save)
            self.save_button.setEnabled(bool(root))
            buttons.accepted.connect(self.save)
            buttons.rejected.connect(self.reject)
            lay.addWidget(buttons)
            self._loading = False
            self.show_rules(self.saved)
            self.resize(980, 520)

        def show_rules(self, rules):
            self._loading = True
            self.table.setRowCount(0)
            for rule in rules.rules:
                self._add_row(rule, existing=rule.name.lower() in self.saved_names)
            self._loading = False
            self._fill_default(rules.default)
            self.table.resizeColumnsToContents()
            self.check()

        def _add_row(self, rule, existing=False):
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, key in enumerate(self.COLUMNS):
                item = QtWidgets.QTableWidgetItem(smr.field_text(rule, key, self.units))
                if key == "name" and existing:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    item.setToolTip("A saved rule keeps its name (parts refer to it).")
                self.table.setItem(r, c, item)
            retired = QtWidgets.QTableWidgetItem()
            retired.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            retired.setCheckState(QtCore.Qt.Checked if rule.retired else QtCore.Qt.Unchecked)
            self.table.setItem(r, len(self.COLUMNS), retired)
            return r

        def _fill_default(self, wanted):
            names = [self.table.item(r, 0).text() for r in range(self.table.rowCount())]
            self.default.blockSignals(True)
            self.default.clear()
            self.default.addItems(names)
            self.default.setCurrentIndex(max(0, names.index(wanted)) if wanted in names else 0)
            self.default.blockSignals(False)

        def rules(self):
            """(RuleSet, [Issue]) from the table."""
            issues = []
            out = smr.RuleSet(default=self.default.currentText(), profile=dict(self.saved.profile),
                              extra=dict(self.saved.extra))
            out.profile.update({"bend_layers": self.bend_layers.isChecked(), "etch": self.etch.isChecked()})
            for r in range(self.table.rowCount()):
                rule = smr.Rule("", "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                for c, key in enumerate(self.COLUMNS):
                    found = smr.set_field(rule, key, self.table.item(r, c).text(), self.units)
                    if found is not None:
                        issues.append(smr.Issue("error", f"Row {r + 1}: {found.text}"))
                rule.retired = self.table.item(r, len(self.COLUMNS)).checkState() == QtCore.Qt.Checked
                out.rules.append(rule)
            return out, issues

        def check(self):
            rules, issues = self.rules()
            issues = issues + smr.validate(rules) + smr.check_change(self.saved if self.saved_names else
                                                                     smr.RuleSet(), rules)
            shown = [i for i in issues if i.level != "info"]
            self.problems.setText("\n".join(("⚠ " if i.level == "warning" else "✕ ") + i.text for i in shown))
            self.save_button.setEnabled(bool(self.root) and not any(i.level == "error" for i in issues))
            return issues

        def _edited(self, _item):
            if not self._loading:
                if _item.column() == 0:
                    self._fill_default(self.default.currentText())
                self.check()

        def add(self):
            base = self._selected() or smr.default_rule(self.saved) or smr.make_rule("NEW", "Al 5052-H32", 1.5)
            self._insert(smr.make_rule(self._new_name("NEW"), base.material, base.thickness))

        def duplicate(self):
            base = self._selected()
            if base is None:
                return
            copy = smr.Rule(**{k: getattr(base, k) for k in smr.FIELDS})
            copy.name, copy.retired, copy.note = self._new_name(base.name + "-copy"), False, ""
            self._insert(copy)

        def _insert(self, rule):
            self._loading = True
            r = self._add_row(rule)
            self._loading = False
            self.table.setCurrentCell(r, 0)
            self._fill_default(self.default.currentText())
            self.check()

        def _new_name(self, stem):
            names = {self.table.item(r, 0).text().lower() for r in range(self.table.rowCount())}
            name, n = stem, 2
            while name.lower() in names:
                name, n = f"{stem}-{n}", n + 1
            return name

        def _selected(self):
            r = self.table.currentRow()
            if r < 0:
                return None
            rules, _issues = self.rules()
            return rules.rules[r] if r < len(rules.rules) else None

        def save(self):
            issues = self.check()
            if any(i.level == "error" for i in issues) or not self.root:
                return
            rules, _issues = self.rules()
            try:
                smr.save(self.root, rules)
            except (OSError, ValueError) as exc:
                self.problems.setText(f"✕ The rules were not saved: {exc}")
                return
            self.accept()

    return {"RulePicker": RulePicker, "RulesDialog": RulesDialog}


_dialogs = {}


def dialogs():
    if not _dialogs:
        _dialogs.update(_make_dialogs())
    return _dialogs


def show_rules_dialog(root=None):
    dialog = dialogs()["RulesDialog"](vault_root() if root is None else root, _main_window())
    dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
    return dialog


def pick_rule(doc=None):
    """The rule picker for the active document; applies the chosen rule."""
    import FreeCAD

    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        _message("Sheet-Metal Rule", "Open or create a document first.")
        return None
    root = vault_root()
    rules = load_rules(root)
    picker = dialogs()["RulePicker"](doc, rules, document_units(doc), _main_window())
    accepted = picker.exec_() if hasattr(picker, "exec_") else picker.exec()
    rule = picker.rule() if accepted else None
    if rule is None:
        return None
    if root:
        try:
            _rules().ensure_saved(root, vault_units(root))  # the rule a part took stays in the vault
        except Exception:
            pass
    apply_rule_command(doc, rule)
    _status(f"Sheet-metal rule {rule.name} applied to {doc.Label}.")
    return rule


def export_flat_dialog():
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    doc = FreeCAD.ActiveDocument
    if doc is None:
        _message("Export Flat", "Open a sheet-metal part first.")
        return None
    obj, face = flat_source(FreeCADGui.Selection.getSelectionEx(), doc)
    if obj is None:
        _message("Export Flat", "This document has no sheet-metal part. Make a base wall on the Sheet Metal tab "
                                "first.")
        return None
    fe = _export()
    number, revision = _part_values(obj)
    folder = os.path.dirname(doc.FileName) if doc.FileName else os.path.expanduser("~")
    default = os.path.join(folder, fe.file_name(number, revision, doc.Label or "flat"))
    path, _filter = QtWidgets.QFileDialog.getSaveFileName(_main_window(), "Export Flat (shop DXF)", default,
                                                          "DXF R12 (*.dxf)")
    if not path:
        return None
    if not path.lower().endswith(".dxf"):
        path += ".dxf"
    try:
        doc.openTransaction("Export flat")
        pattern = export_flat(obj, path, face=face, doc=doc)
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        _message("Export Flat", f"The flat of {obj.Label} could not be exported:\n{exc}", warn=True)
        return None
    units = document_units(doc)
    smr = _rules()
    bends = len(pattern.bend_rows())
    note = "" if pattern.unfolder == "new" else " Bend directions are not known without networkx (layer BEND)."
    _status(f"Flat {smr.format_length(pattern.width, units, unit=False)} x "
            f"{smr.format_length(pattern.height, units)}, {bends} bend{'s' if bends != 1 else ''}: "
            f"{os.path.basename(path)}.{note}", 12000)
    return pattern


class _RuleCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_SheetMetalRule.svg"), "MenuText": "Sheet-Metal Rule…",
                "ToolTip": "Pick the vault's sheet-metal rule (material, thickness, bend radius, K-factor, relief) "
                           "for this part; every wall and the unfold follow it."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        pick_rule()


class _RulesCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_SheetMetalRule.svg"), "MenuText": "Sheet-Metal Rules…",
                "ToolTip": "Edit the vault's sheet-metal rules and its shop-DXF settings."}

    def IsActive(self):
        return True

    def Activated(self):
        show_rules_dialog()


class _ExportCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_ExportFlat.svg"), "MenuText": "Export Flat…",
                "ToolTip": "Write the flat pattern as a shop DXF (CUT, BEND_UP/BEND_DOWN, ETCH) named "
                           "P/N-REV.dxf, and the bend table."}

    def IsActive(self):
        import FreeCAD

        doc = FreeCAD.ActiveDocument
        return doc is not None and any(is_feature(o) for o in doc.Objects)

    def Activated(self):
        export_flat_dialog()


# --- new features follow the rule -------------------------------------------------------------------------


def auto_rule() -> bool:
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(AUTO_PREF, True))


def _settle():
    """Bind new sheet-metal features; give a first base wall the vault's default rule."""
    import FreeCAD

    pending, _state["pending"] = _state["pending"], set()
    for doc_name, obj_name in pending:
        doc = FreeCAD.listDocuments().get(doc_name)
        obj = doc.getObject(obj_name) if doc is not None else None
        if obj is None or not is_feature(obj):
            continue
        varset = rule_varset(doc)
        if varset is not None:
            if bind_features(doc, varset, [obj]):
                part = part_of(obj)
                rules = _rules()
                rule = rules.find(load_rules(), varset.RuleName)
                if rule is not None and part is not None and "Stock" not in part.PropertiesList:
                    set_part_properties(part, rule, document_units(doc))
                doc.recompute()
        elif is_base(obj) and auto_rule():
            rule = _rules().default_rule(load_rules())
            if rule is not None:
                apply_rule_command(doc, rule)
                _status(f"Sheet-metal rule {rule.name} applied (Sheet Metal tab → Rules to change it).", 12000)


class _DocObserver:
    # Objects made while a file loads are not new features.
    def slotStartRestoreDocument(self, doc):
        _state["restoring"].add(doc.Name)

    def slotFinishRestoreDocument(self, doc):
        _state["restoring"].discard(doc.Name)

    def slotDeletedDocument(self, doc):
        _state["restoring"].discard(doc.Name)

    def slotCreatedObject(self, obj):
        try:
            if obj.Document.Name in _state["restoring"]:
                return
            _state["pending"].add((obj.Document.Name, obj.Name))
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, _settle)
        except Exception:
            pass


def install():
    """Register the commands, watch for new sheet-metal features, note a user copy of the add-on."""
    import FreeCADGui

    if _state["observer"] is not None:
        return
    FreeCADGui.addCommand("BruceWorx_SheetMetalRule", _RuleCommand())
    FreeCADGui.addCommand("BruceWorx_SheetMetalRules", _RulesCommand())
    FreeCADGui.addCommand("BruceWorx_ExportFlat", _ExportCommand())
    _state["observer"] = _DocObserver()
    import FreeCAD

    FreeCAD.addDocumentObserver(_state["observer"])
    try:
        from PySide import QtCore

        QtCore.QTimer.singleShot(3000, note_user_copy)
    except Exception:
        pass
