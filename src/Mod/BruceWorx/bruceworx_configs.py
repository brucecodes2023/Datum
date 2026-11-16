# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configurations: table-driven variants of one part or assembly (roadmap MOD-03, plan 25 Oct 2026).

Built on FreeCAD's own configuration table (Spreadsheet's "Configuration Table...") so that a file
with configurations still opens, and still switches, in stock FreeCAD 1.1:

* The switch is a PropertyEnumeration ``Configuration`` (status CopyOnChange) on the file's main
  object (the App::Part with the part properties, the Assembly, or a lone Body). Assemblies link
  to that object, so their links see it and can each pick a row (App::Link CopyOnChange).
* The table is a spreadsheet ``Configurations`` at the root of the file:
  row 1 the file's own P/N (B1, alias FilePartNumber); row 2 the headers; row 3 the active row,
  bound to the selected one (its aliases are what the model reads); rows 4... one configuration
  each, the name in column A. ``<Main>.Configuration`` lists column A from row 4 down.
* Columns are known by the alias on row 3: PartNumber and Description (bound to the main
  object's properties; a row without a P/N of its own holds ``=FilePartNumber``); a user
  parameter's name (its cell in Parameters becomes ``=Configurations.<name>``); ``<Obj>_<Prop>``
  for a model dimension bound directly; ``sup_<Obj>`` (0/1, the feature's Suppressed, or a
  component's); ``cfg_<Link>`` (an assembly instance's configuration).
* An instance (App::Link) picks a row with LinkCopyOnChange = Tracking, and always gets its own
  copy, so it never follows the part file's active row.

No GUI here; bruceworx_configs_gui.py has the table dialog. Tested headless in TestBruceWorxConfigs.py.
"""

from __future__ import annotations

import csv
import io
import re

SHEET_NAME = "Configurations"
SHEET_TYPE = "Spreadsheet::Sheet"
PROP = "Configuration"
GROUP = "Configuration"
FILE_PN = "FilePartNumber"
PN, DESC = "PartNumber", "Description"
SUP, CFG = "sup_", "cfg_"
FILE_ROW, HEAD_ROW, ACTIVE_ROW, FIRST_ROW = 1, 2, 3, 4
DEFAULT_NAMES = ("Default",)
RESERVED = (PN, DESC, FILE_PN)
TRACKING = "Tracking"

PN_KIND, DESC_KIND, PARAM, DIM, SUPPRESS, INSTANCE = "pn", "description", "parameter", "dimension", "suppress", "instance"

_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CELLNAME = re.compile(r"^[A-Za-z]{1,3}\d+$")
_BIND = re.compile(r"^\.cells\.Bind\.([A-Z]+)(\d+)\.([A-Z]+)(\d+)$")


class ConfigError(ValueError):
    """A configuration change that cannot be made, with the reason in words."""


# ----- pure helpers (unit tested) ---------------------------------------------------------


def col_name(index):
    """0 -> "A", 25 -> "Z", 26 -> "AA"."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def col_index(name):
    """"A" -> 0, "AA" -> 26."""
    out = 0
    for ch in name.upper():
        out = out * 26 + ord(ch) - 64
    return out - 1


def cell(col, row):
    """cell(1, 3) -> "B3" (col 0-based, row 1-based as the sheet shows it)."""
    return f"{col_name(col)}{row}"


def column_kind(alias):
    if alias == PN:
        return PN_KIND
    if alias == DESC:
        return DESC_KIND
    if alias.startswith(SUP):
        return SUPPRESS
    if alias.startswith(CFG):
        return INSTANCE
    return PARAM


def valid_name(name):
    """A configuration name: any text but empty, a leading quote or '=' (sheet syntax), or '|'."""
    name = (name or "").strip()
    return bool(name) and not name.startswith(("'", "=")) and "|" not in name


def text_cell(text):
    """Cell content that the sheet keeps as text ("'80" stays "80", not a number). An empty text is
    "=<<>>": a lone "'" leaves the cell without a value, and an alias on it then fails."""
    text = "" if text is None else str(text)
    if not text:
        return "=<<>>"
    return text if text.startswith("=") else "'" + text


def plain(content):
    """What a cell's raw content says, without the text quote."""
    content = content or ""
    if content in ("=<<>>", "'"):
        return ""
    return content[1:] if content.startswith("'") else content


def literal(content):
    """A cell's raw content as a user would type it: the sheet keeps "50 mm" as "=50 mm"."""
    content = content or ""
    body = content[1:].strip() if content.startswith("=") else content
    if re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*([A-Za-zµ°\"'][A-Za-z0-9µ°\"'^/*]*)?$", body):
        return body
    return content


def alias_for(obj_name, prop=""):
    """A column alias for a model dimension: "Pad_Length"."""
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"{obj_name}_{prop}" if prop else obj_name)
    if not re.match(r"^[A-Za-z_]", base):
        base = "_" + base
    return base


def bind_expression(main_name, last_col):
    """The binding of row 3 (B3:<last>3) to the row of the chosen configuration."""
    ref = f"hiddenref({main_name}.{PROP})+{FIRST_ROW}"
    return f"tuple(.cells, <<B>> + str({ref}), <<{last_col}>> + str({ref}))"


def header_for(alias, label=""):
    kind = column_kind(alias)
    if kind == PN_KIND:
        return "P/N"
    if kind == DESC_KIND:
        return "Description"
    if kind == SUPPRESS:
        return f"Suppress {label or alias[len(SUP):]}"
    if kind == INSTANCE:
        return f"{label or alias[len(CFG):]} configuration"
    return label or alias


def suppress_value(text):
    """A suppression cell from what a user typed or ticked: "1" for suppressed, "0" for not."""
    text = str(text).strip().lower()
    return "1" if text in ("1", "1.0", "true", "yes", "y", "x", "suppressed", "s") else "0"


def rows_to_csv(aliases, rows):
    """CSV text: header Configuration + aliases, then one row per configuration (raw contents)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Configuration"] + list(aliases))
    for name, values in rows:
        writer.writerow([name] + [values.get(a, "") for a in aliases])
    return out.getvalue()


def rows_from_csv(text):
    """(aliases, [(name, {alias: content})]) from CSV text written by rows_to_csv (or a spreadsheet)."""
    reader = csv.reader(io.StringIO(text))
    head = next(reader, None)
    if not head or head[0].strip() != "Configuration":
        raise ConfigError("the first column of the CSV must be headed Configuration")
    aliases = [h.strip() for h in head[1:]]
    rows = []
    for record in reader:
        if not record or not record[0].strip():
            continue
        rows.append((record[0].strip(), {a: (record[i + 1] if i + 1 < len(record) else "")
                                         for i, a in enumerate(aliases)}))
    return aliases, rows


# ----- the table in a document --------------------------------------------------------------


def _main_rank():
    return {"Assembly::AssemblyObject": 0, "App::Part": 1, "PartDesign::Body": 2}


def main_object(doc):
    """The object that carries (or would carry) the Configuration switch: one that has it, else
    the file's main object (the assembly, an App::Part, a body; a top-level one first)."""
    if doc is None:
        return None
    copied = copies(doc)
    rank = _main_rank()
    for obj in doc.Objects:  # an instance's link shows its part's switch too: only parts, assemblies, bodies
        if (getattr(obj, "TypeId", "") in rank and PROP in getattr(obj, "PropertiesList", ()) and _is_switch(obj)
                and obj.Name not in copied):
            return obj
    best = None
    for order, obj in enumerate(doc.Objects):
        r = rank.get(getattr(obj, "TypeId", ""))
        if r is None or obj.Name in copied:
            continue
        nested = any(getattr(p, "TypeId", "") in ("App::Part", "Assembly::AssemblyObject")
                     for p in getattr(obj, "InList", []) or [])
        key = (r, nested, order)
        if best is None or key < best[0]:
            best = (key, obj)
    return best[1] if best else None


def _is_switch(obj):
    try:
        return (obj.getTypeIdOfProperty(PROP) == "App::PropertyEnumeration"
                and "CopyOnChange" in obj.getPropertyStatus(PROP))
    except Exception:
        return False


def copies(doc):
    """Names of the objects that are configuration copies of other files' parts (what instances in
    their own row show, held in CopyOnChangeGroup link groups): never the document's own."""
    out = set()
    for obj in doc.Objects if doc is not None else ():
        if obj.TypeId == "App::LinkGroup" and obj.Name.startswith("CopyOnChangeGroup"):
            out.update(o.Name for o in (getattr(obj, "ElementList", None) or []) if o is not None)
    return out


def is_copy(obj):
    """True for an object in a configuration copy (see copies)."""
    try:
        return obj.Name in copies(obj.Document)
    except Exception:
        return False


def table(doc):
    """The document's own Configurations sheet (not a copy of a part's), or None."""
    if doc is None:
        return None
    copied = copies(doc)
    candidates = [doc.getObject(SHEET_NAME)] + list(doc.getObjectsByLabel(SHEET_NAME)) + list(doc.Objects)
    for obj in candidates:
        if obj is None or obj.TypeId != SHEET_TYPE or obj.Name in copied:
            continue
        head = _contents(obj, f"A{ACTIVE_ROW}")
        if obj.Name == SHEET_NAME or obj.Label == SHEET_NAME or (head.startswith("=hiddenref(") and f".{PROP}.String" in head):
            if _alias(obj, f"B{FILE_ROW}") == FILE_PN or obj.Name == SHEET_NAME:
                return obj
    return None


def _ref(doc):
    """How expressions name the document's table ("Configurations", or "Configurations001" when a part's
    copy took the name first)."""
    sheet = table(doc)
    return sheet.Name if sheet is not None else SHEET_NAME


def has_configurations(doc):
    return table(doc) is not None and main_object(doc) is not None and _is_switch(main_object(doc))


def _contents(sheet, address):
    try:
        return sheet.getContents(address) or ""
    except Exception:
        return ""


def _alias(sheet, address):
    try:
        return sheet.getAlias(address) or ""
    except Exception:
        return ""


def aliases(doc_or_sheet):
    """The column aliases of row 3, left to right (B, C, ...)."""
    sheet = doc_or_sheet if getattr(doc_or_sheet, "TypeId", "") == SHEET_TYPE else table(doc_or_sheet)
    if sheet is None:
        return []
    out, col = [], 1
    while True:
        alias = _alias(sheet, cell(col, ACTIVE_ROW))
        if not alias:
            return out
        out.append(alias)
        col += 1


def names(doc):
    """The configuration names, in table order."""
    sheet = table(doc)
    if sheet is None:
        return []
    out, row = [], FIRST_ROW
    while True:
        name = plain(_contents(sheet, f"A{row}")).strip()
        if not name:
            return out
        out.append(name)
        row += 1


def active(doc):
    main = main_object(doc)
    if main is None or not _is_switch(main):
        return ""
    try:
        return str(main.Configuration or "")
    except Exception:
        return ""


def row_of(doc, name):
    """The sheet row (1-based) of a configuration."""
    all_names = names(doc)
    if name not in all_names:
        raise ConfigError(f"there is no configuration {name!r}")
    return FIRST_ROW + all_names.index(name)


def column_of(doc, alias):
    found = aliases(doc)
    if alias not in found:
        raise ConfigError(f"there is no column {alias!r}")
    return 1 + found.index(alias)


def rows(doc):
    """[(name, {alias: raw content})] in table order."""
    sheet = table(doc)
    cols = aliases(sheet)
    out = []
    for i, name in enumerate(names(doc)):
        row = FIRST_ROW + i
        out.append((name, {a: _contents(sheet, cell(1 + j, row)) for j, a in enumerate(cols)}))
    return out


def value(doc, name, alias):
    """The evaluated value of one cell (a Quantity, a number or text)."""
    sheet = table(doc)
    return sheet.get(cell(column_of(doc, alias), row_of(doc, name)))


def contents(doc, name, alias):
    return _contents(table(doc), cell(column_of(doc, alias), row_of(doc, name)))


def _binding(sheet):
    """The row-3 binding path of the sheet (".cells.Bind.B3.E3"), or ""."""
    for path, _expr in getattr(sheet, "ExpressionEngine", None) or ():
        if _BIND.match(path):
            return path
    return ""


def _rebind(sheet, main):
    """Bind B3:<last>3 to the selected row again (after columns were added or removed)."""
    old = _binding(sheet)
    if old:
        sheet.setExpression(old, None)
    count = len(aliases(sheet))
    if count:
        last = col_name(count)
        sheet.setExpression(f".cells.Bind.B{ACTIVE_ROW}.{last}{ACTIVE_ROW}", bind_expression(main.Name, last))


def _style(sheet):
    try:
        count = len(aliases(sheet))
        sheet.setStyle(f"A{HEAD_ROW}:{col_name(max(count, 1))}{HEAD_ROW}", "bold")
        sheet.setStyle(f"A{FILE_ROW}", "italic")
        sheet.setBackground(f"A{ACTIVE_ROW}:{col_name(max(count, 1))}{ACTIVE_ROW}", (0.85, 0.92, 1.0, 1.0))
        sheet.setColumnWidth("A", 130)
    except Exception:
        pass


def setup(doc, config_names=DEFAULT_NAMES, main=None):
    """Give ``doc`` a configuration table with the given rows (each a copy of the model as it is now),
    the switch on its main object, and the P/N and Description columns. Returns the sheet.
    Runs inside the caller's transaction; recomputes."""
    if table(doc) is not None:
        raise ConfigError(f"{doc.Label} already has configurations")
    main = main or main_object(doc)
    if main is None:
        raise ConfigError(f"{doc.Label} has no part, assembly or body to configure")
    config_names = [n.strip() for n in config_names]
    if not config_names or not all(valid_name(n) for n in config_names) or len(set(config_names)) != len(config_names):
        raise ConfigError("configuration names must be different, and not empty")
    sheet = doc.addObject(SHEET_TYPE, SHEET_NAME)
    sheet.Label = SHEET_NAME
    file_pn = str(getattr(main, PN, "") or "").strip()
    description = str(getattr(main, DESC, "") or "")
    sheet.set(f"A{FILE_ROW}", "File P/N")
    sheet.set(f"B{FILE_ROW}", text_cell(file_pn))
    sheet.setAlias(f"B{FILE_ROW}", FILE_PN)
    sheet.set(f"A{HEAD_ROW}", "Configuration")
    for col, alias in ((1, PN), (2, DESC)):
        sheet.set(cell(col, HEAD_ROW), header_for(alias))
        sheet.setAlias(cell(col, ACTIVE_ROW), alias)
    for i, name in enumerate(config_names):
        row = FIRST_ROW + i
        sheet.set(f"A{row}", text_cell(name))
        sheet.set(cell(1, row), "=" + FILE_PN)
        sheet.set(cell(2, row), text_cell(description))
    if PROP not in main.PropertiesList:
        main.addProperty("App::PropertyEnumeration", PROP, GROUP,
                         "The active configuration (rows of the Configurations sheet)")
    main.setPropertyStatus(PROP, "CopyOnChange")
    setattr(main, PROP, list(config_names))  # valid at once; the expression keeps it in step with column A
    doc.recompute()
    main.setExpression(f"{PROP}.Enum", f"{sheet.Name}.cells[<<A{FIRST_ROW}:|>>]")
    doc.recompute()
    sheet.set(f"A{ACTIVE_ROW}", f"=hiddenref({main.Name}.{PROP}.String)")
    _rebind(sheet, main)
    doc.recompute()
    for prop in (PN, DESC):
        if prop not in main.PropertiesList:
            main.addProperty("App::PropertyString", prop, "BruceWorx")
        main.setExpression(prop, f"{sheet.Name}.{prop}")
    _style(sheet)
    doc.recompute()
    _settle(main)
    return sheet


def _settle(main):
    """Rebuild the main object if the document recompute left it touched (an Assembly, seen 25 Oct)."""
    if "Touched" in (getattr(main, "State", []) or []):
        main.recompute()


def remove_all(doc):
    """Back to one model: every bound value keeps the active row's value, the switch and the sheet go."""
    sheet = table(doc)
    main = main_object(doc)
    if sheet is None:
        return
    for alias in list(aliases(sheet)):
        if alias not in (PN, DESC):
            remove_column(doc, alias, recompute=False)
    if main is not None:
        for prop in (PN, DESC):
            if prop in main.PropertiesList:
                current = str(getattr(main, prop, "") or "")
                main.setExpression(prop, None)
                setattr(main, prop, current)
        if PROP in main.PropertiesList:
            main.setExpression(f"{PROP}.Enum", None)
            main.removeProperty(PROP)
    doc.removeObject(sheet.Name)
    doc.recompute()


# ----- rows -------------------------------------------------------------------------------


def add_configuration(doc, name, copy_from=None):
    """A new row at the end, a copy of ``copy_from`` (default the active row). Returns its row."""
    name = (name or "").strip()
    if not valid_name(name):
        raise ConfigError("give the configuration a name (not starting with ' or =)")
    if name in names(doc):
        raise ConfigError(f"there is already a configuration {name!r}")
    sheet = table(doc)
    source = row_of(doc, copy_from or active(doc) or names(doc)[0])
    row = FIRST_ROW + len(names(doc))
    sheet.set(f"A{row}", text_cell(name))
    for col in range(1, len(aliases(sheet)) + 1):
        sheet.set(cell(col, row), _contents(sheet, cell(col, source)))
    doc.recompute()
    _sync_enum(doc)
    return row


def rename_configuration(doc, old, new):
    new = (new or "").strip()
    if not valid_name(new):
        raise ConfigError("give the configuration a name (not starting with ' or =)")
    if new != old and new in names(doc):
        raise ConfigError(f"there is already a configuration {new!r}")
    was = active(doc)
    table(doc).set(f"A{row_of(doc, old)}", text_cell(new))
    doc.recompute()
    _sync_enum(doc, new if was == old else was)
    doc.recompute()


def remove_configuration(doc, name):
    all_names = names(doc)
    if name not in all_names:
        raise ConfigError(f"there is no configuration {name!r}")
    if len(all_names) == 1:
        raise ConfigError("a table keeps at least one configuration (Remove Configurations removes them all)")
    if active(doc) == name:
        activate(doc, next(n for n in all_names if n != name))
    keep = active(doc)
    table(doc).removeRows(str(row_of(doc, name)), 1)
    doc.recompute()
    _sync_enum(doc, keep)
    doc.recompute()


def set_cell(doc, name, alias, text):
    """One cell, from what a user typed: a parameter takes a value or "=expression"; P/N, Description
    and instance cells take text (an empty P/N means the file's own); a suppression cell 0/1."""
    kind = column_kind(alias)
    if kind == PN_KIND:
        text = (text or "").strip()
        content = "=" + FILE_PN if not text or text == file_part_number(doc) else text_cell(text)
    elif kind in (DESC_KIND, INSTANCE):
        content = text_cell(text)
    elif kind == SUPPRESS:
        content = suppress_value(text)
    else:
        content = (text or "").strip()
        if not content:
            raise ConfigError("give a value")
    table(doc).set(cell(column_of(doc, alias), row_of(doc, name)), content)


def _sync_enum(doc, keep=""):
    """The switch lists column A again (an assembly is not always rebuilt by a document recompute while
    its table changes, seen 25 Oct), keeping ``keep`` (or the current row) active when it still exists."""
    main = main_object(doc)
    current = keep or active(doc)
    wanted = names(doc)
    if list(main.getEnumerationsOfProperty(PROP)) != wanted:
        main.recompute()
        if list(main.getEnumerationsOfProperty(PROP)) != wanted:
            setattr(main, PROP, wanted)
    if current in wanted and str(getattr(main, PROP)) != current:
        setattr(main, PROP, current)


def activate(doc, name):
    """Make ``name`` the active row and rebuild (instances that pick their row by the table get their
    own copy; suppressed components are hidden and their joints suppressed)."""
    main = main_object(doc)
    if name not in names(doc):
        raise ConfigError(f"there is no configuration {name!r}")
    _sync_enum(doc)
    if str(main.Configuration) != name:
        main.Configuration = name
    doc.recompute()
    _settle(main)
    after_switch(doc)


def after_switch(doc):
    """What the table cannot do by expressions alone: own copies for instances it drives, and
    component suppression (hide, joints). Recomputes when it changed something."""
    changed = False
    for obj in doc.Objects:
        if obj.TypeId != "App::Link" or not _is_instance(obj):
            continue
        if _expression(obj, PROP) and _follows(obj):
            _force_copy(obj)
            changed = True
    changed = apply_suppressed_components(doc) or changed
    if changed:
        doc.recompute()
        # a copy made in a recompute takes its row in the next one (seen 25 Oct)
        doc.recompute()
    settle_copies(doc)


def check_all(doc):
    """Build every configuration in turn and return {name: [labels of the features that fail]}; the
    active row is restored."""
    keep = active(doc)
    out = {}
    try:
        for name in names(doc):
            activate(doc, name)
            bad = []
            for obj in doc.Objects:
                if obj.TypeId in (SHEET_TYPE, "App::Origin", "App::Line", "App::Plane", "App::Point"):
                    continue
                if _is_suppressed(obj):
                    continue
                state = list(getattr(obj, "State", []) or [])
                if "Invalid" in state or "Error" in state:
                    bad.append(obj.Label)
            out[name] = bad
    finally:
        if keep:
            activate(doc, keep)
    return out


def _is_suppressed(obj):
    return bool(getattr(obj, "Suppressed", False)) if "Suppressed" in getattr(obj, "PropertiesList", ()) else False


# ----- columns ----------------------------------------------------------------------------


def _new_column(doc, alias, header, row_contents):
    """Append a column: its header, the alias on row 3, and the contents per row (a function of the
    row's name); then bind again."""
    if not _ALIAS.match(alias) or _CELLNAME.match(alias):
        raise ConfigError(f"{alias!r} cannot name a column")
    if alias in aliases(doc) or alias == FILE_PN:
        raise ConfigError(f"there is already a column {alias}")
    sheet = table(doc)
    col = 1 + len(aliases(sheet))
    sheet.set(cell(col, HEAD_ROW), header)
    for i, name in enumerate(names(doc)):
        sheet.set(cell(col, FIRST_ROW + i), row_contents(name))
    sheet.setAlias(cell(col, ACTIVE_ROW), alias)
    _rebind(sheet, main_object(doc))
    _style(sheet)
    doc.recompute()
    return alias


def _expression(obj, prop):
    for path, expr in getattr(obj, "ExpressionEngine", None) or ():
        if path == prop:
            return expr
    return ""


def _quantity_text(value):
    """A property value as sheet content: "80.0 mm", "45.0 deg", "3"."""
    if hasattr(value, "Value") and hasattr(value, "Unit"):
        text = str(value)
        return text.replace("°", "deg")
    return str(value)


def _parameters_sheet(doc):
    import bruceworx_parameters as bp

    return bp.local_sheet(doc)


def add_parameter_column(doc, name):
    """Configure a user parameter of this file's Parameters sheet: every row takes its value now, and
    its cell reads the active row (``=Configurations.<name>``)."""
    params = _parameters_sheet(doc)
    address = params.getCellFromAlias(name) if params is not None else None
    if not address:
        import bruceworx_parameters as bp

        other = bp.linked_sheet(doc)
        where = f" (it is in {other.Document.Label}; a part's configurations change only its own file)" \
            if other is not None and other.getCellFromAlias(name) else ""
        raise ConfigError(f"{name} is not a parameter of this file{where}")
    if name in RESERVED or name.startswith((SUP, CFG)):
        raise ConfigError(f"a parameter named {name} cannot be configured; rename it first")
    content = params.getContents(address) or ""
    if content.startswith("=") and f"{_ref(doc)}." in content:
        raise ConfigError(f"{name} is already configured")
    if content.startswith("="):
        content = _quantity_text(params.get(address))  # an expression: every row starts from its value
    _new_column(doc, name, name, lambda _n: content)
    params.set(address, f"={_ref(doc)}.{name}")
    doc.recompute()
    return name


def add_dimension_column(doc, obj, prop):
    """Configure a model dimension that is not a parameter: the property reads the table."""
    if prop not in obj.PropertiesList:
        raise ConfigError(f"{obj.Label} has no {prop}")
    expr = _expression(obj, prop)
    if expr:
        raise ConfigError(f"{obj.Label}.{prop} reads {expr}; configure that instead")
    alias = alias_for(obj.Name, prop)
    content = _quantity_text(getattr(obj, prop))
    _new_column(doc, alias, f"{obj.Label} {prop}", lambda _n: content)
    obj.setExpression(prop, f"{_ref(doc)}.{alias}")
    doc.recompute()
    return alias


def _component_suppressible(obj):
    return obj.TypeId == "App::Link" or obj.isDerivedFrom("App::Link")


def add_suppression_column(doc, obj):
    """Suppression per configuration for a feature (its Suppressed), a joint, or an assembly
    component (a BruceWorx Suppressed property on the link)."""
    if "Suppressed" not in obj.PropertiesList:
        if not _component_suppressible(obj):
            raise ConfigError(f"{obj.Label} cannot be suppressed")
        obj.addProperty("App::PropertyBool", "Suppressed", GROUP,
                        "Suppressed in this configuration: hidden, left out of the BOM, its joints off")
    if _expression(obj, "Suppressed"):
        raise ConfigError(f"{obj.Label} is already suppressed by {_expression(obj, 'Suppressed')}")
    now = "1" if obj.Suppressed else "0"
    alias = SUP + alias_for(obj.Name)
    _new_column(doc, alias, header_for(alias, obj.Label), lambda _n: now)
    obj.setExpression("Suppressed", f"{_ref(doc)}.{alias}")
    doc.recompute()
    return alias


def add_instance_column(doc, link):
    """Assemblies: which configuration an instance of a configured part uses, per row."""
    source = source_of(link)
    if not configurable(source):
        raise ConfigError(f"{link.Label} is not an instance of a part with configurations")
    if str(link.LinkCopyOnChange) == "Disabled":
        link.LinkCopyOnChange = TRACKING
        doc.recompute()
    now = str(link.Configuration)
    alias = CFG + alias_for(link.Name)
    _new_column(doc, alias, header_for(alias, link.Label), lambda _n: text_cell(now))
    link.setExpression(PROP, f"{_ref(doc)}.{alias}")
    doc.recompute()
    after_switch(doc)
    return alias


def remove_column(doc, alias, recompute=True):
    """Take a column out: what it drove keeps the active row's value."""
    kind = column_kind(alias)
    if kind in (PN_KIND, DESC_KIND):
        raise ConfigError("the P/N and Description columns stay (leave a P/N empty for the file's own)")
    sheet = table(doc)
    col = column_of(doc, alias)
    if kind == PARAM:
        params = _parameters_sheet(doc)
        address = params.getCellFromAlias(alias) if params is not None else None
        if address and f"{sheet.Name}." in (params.getContents(address) or ""):
            params.set(address, literal(_contents(sheet, cell(col, row_of(doc, active(doc))))))
    for obj in doc.Objects:
        for path, expr in list(getattr(obj, "ExpressionEngine", None) or ()):
            if obj is not sheet and re.search(rf"(?<![\w.]){sheet.Name}\.{re.escape(alias)}(?!\w)", expr or ""):
                obj.setExpression(path, None)
    old = _binding(sheet)
    if old:
        sheet.setExpression(old, None)
    sheet.setAlias(cell(col, ACTIVE_ROW), "")
    sheet.removeColumns(col_name(col), 1)
    _rebind(sheet, main_object(doc))
    if recompute:
        doc.recompute()


def column_target(doc, alias):
    """(object, property) a column drives, or (None, "") for the P/N, Description and parameters."""
    kind = column_kind(alias)
    ref = re.compile(rf"(?<![\w.]){_ref(doc)}\.{re.escape(alias)}(?!\w)")
    for obj in doc.Objects:
        if obj.TypeId == SHEET_TYPE:
            continue
        for path, expr in getattr(obj, "ExpressionEngine", None) or ():
            if ref.search(expr or "") and kind not in (PN_KIND, DESC_KIND):
                return obj, path
    return None, ""


# ----- the file's P/N and the configurations' P/Ns ----------------------------------------


def file_part_number(doc):
    sheet = table(doc)
    if sheet is None:
        main = main_object(doc)
        return str(getattr(main, PN, "") or "").strip() if main is not None else ""
    return plain(_contents(sheet, f"B{FILE_ROW}")).strip()


def set_file_part_number(doc, pn):
    """The vault numbers a configured file here (its PartNumber reads the table)."""
    table(doc).set(f"B{FILE_ROW}", text_cell(pn))
    doc.recompute()


def part_numbers(doc):
    """[(configuration, P/N, description, own)] where own is False for a row that is the file's item."""
    if not has_configurations(doc):
        return []
    file_pn = file_part_number(doc)
    out = []
    for name, values in rows(doc):
        raw = values.get(PN, "")
        own = bool(raw) and not raw.startswith("=")
        pn = plain(raw).strip() if own else file_pn
        out.append((name, pn, plain(values.get(DESC, "")), own and pn != file_pn))
    return out


# ----- instances in assemblies ------------------------------------------------------------


def configurable(obj):
    """True for an object that has a configuration switch links can use."""
    return obj is not None and PROP in getattr(obj, "PropertiesList", ()) and _is_switch(obj)


def source_of(link):
    """The object an instance stands for in its own file (through a configuration copy)."""
    if link is None:
        return None
    mode = str(getattr(link, "LinkCopyOnChange", "Disabled"))
    src = getattr(link, "LinkCopyOnChangeSource", None) if mode != "Disabled" else None
    if src is not None:
        return src
    target = getattr(link, "LinkedObject", None)
    if isinstance(target, tuple):
        target = target[0]
    return target


def copy_of(link):
    """The configuration copy an instance shows (in the assembly file), or None when it shows its part."""
    mode = str(getattr(link, "LinkCopyOnChange", "Disabled"))
    src = getattr(link, "LinkCopyOnChangeSource", None)
    target = getattr(link, "LinkedObject", None)
    if mode == "Disabled" or src is None or target is None or target is src:
        return None
    return target


def settle_copies(doc, tries=3):
    """Recompute again while configuration copies are left touched: when its part changes, a Tracking
    instance copies it again and takes its own row only in the next recompute (seen 25 Oct, stock
    1.1.4 too: one recompute shows the part's active row). Returns how many recomputes it took."""
    done = 0
    while done < tries:
        names = copies(doc)
        if not any("Touched" in (getattr(doc.getObject(n), "State", None) or []) for n in names):
            break
        doc.recompute()
        done += 1
    return done


def _is_instance(obj):
    return obj.TypeId == "App::Link" and configurable(source_of(obj))


def _follows(link):
    return copy_of(link) is None


def _force_copy(link):
    """Make the instance its own copy now (FreeCAD copies on Owned when it still shows the source)."""
    link.LinkCopyOnChange = "Owned"
    link.LinkCopyOnChange = TRACKING


def instance_configuration(link):
    """(configuration, follows) of an instance: follows is True when it shows its part file's active
    row (no row picked, or a stock link)."""
    source = source_of(link)
    if not configurable(source):
        return "", False
    if str(getattr(link, "LinkCopyOnChange", "Disabled")) == "Disabled" or PROP not in link.PropertiesList:
        return str(source.Configuration), True
    return str(link.Configuration), _follows(link)


def instance_names(link):
    source = source_of(link)
    return list(source.getEnumerationsOfProperty(PROP)) if configurable(source) else []


def set_instance_configuration(link, name):
    """The instance uses configuration ``name`` of its part, in a copy of its own (Tracking: edits of
    the part still arrive). Recomputes the assembly."""
    source = source_of(link)
    if not configurable(source):
        raise ConfigError(f"{link.Label} is not an instance of a part with configurations")
    if name not in instance_names(link):
        raise ConfigError(f"{source.Label} has no configuration {name!r}")
    doc = link.Document
    if _expression(link, PROP):
        raise ConfigError(f"{link.Label}'s configuration is set by the assembly's table "
                          f"({_expression(link, PROP)})")
    if str(link.LinkCopyOnChange) != TRACKING:
        link.LinkCopyOnChange = TRACKING
        doc.recompute()
    if str(link.Configuration) != name:
        link.Configuration = name
    if _follows(link):
        _force_copy(link)
    doc.recompute()
    settle_copies(doc)
    return name


def follow_part(link):
    """The instance shows its part file's active row again (its copy goes)."""
    if _expression(link, PROP):
        raise ConfigError(f"{link.Label}'s configuration is set by the assembly's table")
    source = source_of(link)
    link.LinkCopyOnChange = "Disabled"
    link.LinkedObject = source
    link.Document.recompute()


def instance_part_number(link):
    """The P/N of the row an instance shows ("" when its part has no P/N)."""
    shown = copy_of(link) or source_of(link)
    return str(getattr(shown, PN, "") or "").strip() if shown is not None else ""


# ----- component suppression in assemblies ------------------------------------------------


def _joints_of(doc, link):
    """Joints in ``doc`` that attach ``link``."""
    out = []
    for obj in doc.Objects:
        if "JointType" not in getattr(obj, "PropertiesList", ()):
            continue
        for prop in ("Reference1", "Reference2"):
            ref = getattr(obj, prop, None)
            if not ref:
                continue
            holder, subs = ref[0], ref[1] if len(ref) > 1 else []
            subs = subs if isinstance(subs, (list, tuple)) else [subs]
            if holder is link or any(str(s).split(".")[0] == link.Name for s in subs):
                out.append(obj)
                break
    return out


def apply_suppressed_components(doc):
    """Hide suppressed components and suppress their joints (show and release them when not). A joint
    with a suppression column of its own keeps it. Returns True when something changed."""
    changed = False
    for obj in doc.Objects:
        if obj.TypeId != "App::Link" or "Suppressed" not in obj.PropertiesList:
            continue
        off = bool(obj.Suppressed)
        if bool(obj.Visibility) == off:
            obj.Visibility = not off
            changed = True
        for joint in _joints_of(doc, obj):
            if _expression(joint, "Suppressed"):
                continue
            if bool(joint.Suppressed) != off:
                joint.Suppressed = off
                changed = True
    return changed


def is_suppressed_component(obj):
    return obj.TypeId == "App::Link" and "Suppressed" in obj.PropertiesList and bool(obj.Suppressed)


# ----- CSV (SolidWorks design table parity) -----------------------------------------------


def export_csv(doc):
    return rows_to_csv(aliases(doc), [(n, {a: literal(c) for a, c in v.items()}) for n, v in rows(doc)])


def import_csv(doc, text):
    """Rows of a CSV into the table: existing configurations are updated, new ones added; columns are
    matched by alias. Returns (updated, added, unknown columns)."""
    cols, data = rows_from_csv(text)
    known = aliases(doc)
    unknown = [c for c in cols if c not in known]
    updated, added = [], []
    for name, values in data:
        if name not in names(doc):
            add_configuration(doc, name)
            added.append(name)
        else:
            updated.append(name)
        sheet = table(doc)
        row = row_of(doc, name)
        for alias, content in values.items():
            if alias in known:
                sheet.set(cell(column_of(doc, alias), row), content)
    doc.recompute()
    return updated, added, unknown


# ----- the vault: configuration items (MOD-03) --------------------------------------------


def next_configuration_number(vault, file_pn, taken=()):
    """The next number for a configuration of the file ``file_pn``: the next sub-number under the
    same parent drawing (LE-01-01001-001 -> LE-01-01001-002), as a family of dash numbers. Reserved in
    the vault's numbering; numbers already in the vault or in ``taken`` are skipped."""
    from heuretes_freecad import numbering, pdm, vault_setup
    from heuretes_freecad.sources import JsonNumberSource

    fields = numbering.parse_part_number(file_pn)
    source = JsonNumberSource(vault_setup.numbering_path(vault.root))
    for _attempt in range(1000):
        pn = pdm.strip_revision(source.allocate(fields["program"], fields["document_type"], parent=fields["parent"]))
        if pn not in vault.items and pn not in taken and pn != file_pn:
            return pn
    raise ConfigError(f"no free configuration number under {file_pn}")


def configuration_items(doc, file_pn):
    """[(P/N, configuration, description)] the vault should hold for a configured file: the rows with
    a P/N of their own. Refuses a P/N used by two rows."""
    out, seen = [], {}
    for name, pn, description, own in part_numbers(doc):
        if not own or pn == file_pn:
            continue
        if pn in seen:
            raise ConfigError(f"{pn} is the P/N of two configurations ({seen[pn]} and {name})")
        seen[pn] = name
        out.append((pn, name, description))
    return out


def check_configuration_items(vault, file_pn, wanted):
    """Refuse (before a check-in stores anything) P/Ns that already name another item."""
    for pn, name, _description in wanted:
        item = vault.items.get(pn)
        if item is not None and getattr(item, "configuration_of", None) != file_pn:
            raise ConfigError(f"configuration {name}: {pn} is already another item in the vault")


def register_configuration_items(vault, file_pn, wanted):
    """Add the configuration items that are missing, and rename the ones whose row changed. Returns
    the P/Ns added or changed."""
    done = []
    for pn, name, description in wanted:
        item = vault.items.get(pn)
        if item is not None and item.configuration == name and (not description or item.description == description):
            continue
        vault.add_configuration(pn, file_pn, name, description)
        done.append(pn)
    return done


def _open(path):
    """(document, opened_here) for a file, opened hidden when it is not open."""
    import os

    import FreeCAD

    doc = next((d for d in FreeCAD.listDocuments().values()
                if d.FileName and os.path.realpath(d.FileName) == os.path.realpath(path)), None)
    if doc is not None:
        return doc, False
    before = set(FreeCAD.listDocuments())
    doc = FreeCAD.openDocument(path, True)
    return doc, [n for n in FreeCAD.listDocuments() if n not in before]


def _close(opened):
    import FreeCAD

    for name in reversed(opened or []):
        try:
            FreeCAD.closeDocument(name)
        except Exception:
            pass


class CheckinConfigurations:
    """Check-in hook (bruceworx_vaultclient.CHECKIN_HOOKS): a file's configurations with P/Ns of their
    own become configuration items of its item."""

    def prepare(self, s, pn, notes):
        item = s.vault.items.get(pn)
        if item is None or getattr(item, "configuration_of", None):
            return None
        doc, opened = _open(s.work.path(pn))
        try:
            if not has_configurations(doc):
                return None
            wanted = configuration_items(doc, pn)
            if file_part_number(doc) not in ("", pn):
                notes.setdefault("configurations_note", []).append(
                    f"{pn}: its configuration table names {file_part_number(doc)} as the file's P/N")
        finally:
            _close(opened)
        check_configuration_items(s.vault, pn, wanted)
        return wanted

    def record(self, s, pn, data, notes):
        if not data:
            return
        done = register_configuration_items(s.vault, pn, data)
        if done:
            notes.setdefault("configurations", []).append((pn, done))


def checkin_text(notes):
    """The words after a check-in for configuration items ("" when nothing needs saying)."""
    out = []
    for pn, done in notes.get("configurations", ()):
        out.append(f"{pn}: configuration item{'s' if len(done) != 1 else ''} {', '.join(done)} added to the vault.")
    out.extend(notes.get("configurations_note", ()))
    return "\n".join(out)
