# SPDX-License-Identifier: LGPL-2.1-or-later
"""The BOM of an assembly revision, and where-used, in FreeCAD (BW-PDM-06, plan approved 24 Oct 2026).

Built on the vault client (bruceworx_vaultclient.py) and the hooks add-on's engine
(bruceworx/pdm.py keeps a BOM per revision; bruceworx/bom.py makes rows and CSV).

* The walker reads an open assembly the way Assembly's BomObject does (links, link arrays
  counting their visible elements, AssemblyLink, a sub-assembly inside the same file) but
  keeps only items: each line is another file, named by its part number, with the quantity
  per parent. A part's bodies are not lines. Raw XLinks are not used: they over-report
  (an array deleted headless leaves link elements behind).
* Check-in: when the file is an assembly, its BOM is read before the file is stored and
  recorded with the new version. A BOM that would contain itself stops the check-in with
  its path; lines that are not vault items are recorded as missing, listed after the
  check-in, and refused by Approve / Release ECO.
* Where Used... (right-click an item): the assembly revisions that use it, exact by default,
  with "may pick up" (they float to a newer released child) as a second list; Export CSV.
* Bill of Materials... (right-click an assembly): a revision's BOM indented or flattened,
  with "newer child released" notes; Export CSV.
* BruceWorx > Vault > Export Released BOMs...: every released BOM in the vault's outbox (what
  Ops receives once it has an API) as one CSV.

The walker is tested headless in TestBruceWorxBom.py.
"""

from __future__ import annotations

import os

_state = {"installed": False, "dialogs": []}

_SVG = {
    "whereused": '<rect x="4" y="13" width="7" height="7" rx="1.2" fill="none" stroke="{c}" stroke-width="1.8"/>'
                 '<rect x="13" y="4" width="7" height="7" rx="1.2" fill="none" stroke="{c}" stroke-width="1.8"/>'
                 '<path d="M7.5 13V8.5a1 1 0 0 1 1-1H13" fill="none" stroke="{c}" stroke-width="1.8" '
                 'stroke-linecap="round"/><path d="M11 5.5l2 2-2 2" fill="none" stroke="{c}" stroke-width="1.8" '
                 'stroke-linecap="round" stroke-linejoin="round"/>',
    "bom": '<path d="M4 5h2M4 12h2M4 19h2" stroke="{c}" stroke-width="2.2" stroke-linecap="round"/>'
           '<path d="M9 5h11M12 12h8M12 19h8" stroke="{c}" stroke-width="1.9" stroke-linecap="round"/>',
    "export": '<path d="M12 3v11M7.5 9.5L12 14l4.5-4.5" fill="none" stroke="{c}" stroke-width="1.9" '
              'stroke-linecap="round" stroke-linejoin="round"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" '
              'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
}
_COLOURS = {"accent": "#0A84FF", "secondary": "#8E8E93"}


# ----- the walker (FreeCAD documents, no GUI) -----------------------------------------------


def _type(obj, name):
    try:
        return bool(obj.isDerivedFrom(name))
    except Exception:
        return False


def top_assembly(doc):
    """The document's top Assembly (one not inside another), or None."""
    for obj in doc.Objects:
        if _type(obj, "Assembly::AssemblyObject") and not any(_type(o, "Assembly::AssemblyObject") for o in obj.InList):
            return obj
    return None


def _visible_elements(link):
    count = int(getattr(link, "ElementCount", 0) or 0)
    shown = list(getattr(link, "VisibilityList", []) or [])
    if len(shown) == count:
        return sum(1 for v in shown if v)
    return count


def _target(obj):
    """(the object a child stands for, how many of it), or (None, 0) for what is not a BOM line."""
    if _type(obj, "App::LinkElement"):
        return None, 0  # an array's element is counted by its array; one left alone is stale
    if obj.TypeId == "Assembly::AssemblyLink":
        target = getattr(obj, "LinkedObject", None)
        return (target, 1) if target is not None else (None, 0)
    if _type(obj, "App::Link"):
        if int(getattr(obj, "ElementCount", 0) or 0) > 0:
            target = getattr(obj, "LinkedObject", None)
            if isinstance(target, tuple):
                target = target[0]
            if target is not None:
                target = target.getLinkedObject(True)
            return (target, _visible_elements(obj)) if target is not None else (None, 0)
        try:
            target = obj.getLinkedObject(True)
        except Exception:
            target = None
        return (target, 1) if target is not None and target is not obj else (None, 0)
    return obj, 1


def _is_line(obj):
    if getattr(getattr(obj, "Proxy", None), "Type", "") == "BruceWorx::RoutePath":
        return False  # [BW] ROU-04: a route centreline is construction, not an item
    return (_type(obj, "Assembly::AssemblyObject") or _type(obj, "App::Part") or _type(obj, "Part::Feature"))


def _keys(target, imported, shown=None):
    """Candidate part numbers for an object in another file: the file it lives in (work files are
    <P/N>.FCStd; an outside file imported by this check-in maps to its new P/N), then its
    PartNumber property. A part with configurations (MOD-03) offers the P/N of the row shown first
    (``shown``: the instance's configuration copy, else the part itself), so each configuration with
    a P/N of its own is a line of its own."""
    import bruceworx_configs as bc

    path = getattr(target.Document, "FileName", "") or ""
    keys = []
    if path and os.path.realpath(path) in imported:
        keys.append(imported[os.path.realpath(path)])
    if path:
        keys.append(os.path.splitext(os.path.basename(path))[0])
    for holder in (target, getattr(target, "getParentGeoFeatureGroup", lambda: None)()):
        pn = str(getattr(holder, "PartNumber", "") or "").strip() if holder is not None else ""
        if pn and pn not in keys:
            keys.append(pn)
    row = shown if shown is not None else (target if bc.configurable(target) else None)
    pn = str(getattr(row, "PartNumber", "") or "").strip() if row is not None else ""
    if pn:
        keys = [pn] + [k for k in keys if k != pn]
    return keys


def _metered(row, target):
    """[BW] ROU-04: a routed item (QuantityUnit "m", QuantityPer its metres) is listed by length:
    the row's qty becomes count x QuantityPer and carries the unit."""
    unit = str(getattr(target, "QuantityUnit", "") or "").strip()
    if unit:
        row["qty"] = row["qty"] * float(getattr(target, "QuantityPer", 1.0) or 0.0)
        row["unit"] = unit
    return row


def walk(doc, imported=None) -> list:
    """What an assembly document holds, one level of files deep, as rows for
    bruceworx.bom.lines_from_walk: [{"keys", "name", "qty"}] in tree order. A sub-assembly in
    the same file is walked into (its quantity multiplies); anything in another file is one
    row; a part or body in this file is a row with no keys (it is not a vault item)."""
    imported = {os.path.realpath(p): pn for p, pn in (imported or {}).items()}
    try:
        import bruceworx_configs

        bruceworx_configs.settle_copies(doc)  # instances in a row of their own show that row (MOD-03)
    except Exception:
        pass
    top = top_assembly(doc)
    objects = list(top.Group) if top is not None else [o for o in doc.RootObjects]
    rows = []

    import bruceworx_configs as bc

    def visit(objs, mult, seen):
        for obj in objs:
            if obj is None or bc.is_suppressed_component(obj):
                continue
            target, count = _target(obj)
            if target is None or count <= 0 or not _is_line(target):
                continue
            copy = bc.copy_of(obj) if _type(obj, "App::Link") else None
            if copy is not None:  # an instance in a configuration of its own (MOD-03): its part's file
                source = bc.source_of(obj)
                if source is not None and source.Document is not doc:
                    base = os.path.basename(getattr(source.Document, "FileName", "") or "") or source.Document.Label
                    name = f"{base} ({getattr(copy, 'Configuration', '')})"
                    rows.append({"keys": _keys(source, imported, shown=copy), "name": name, "qty": mult * count})
                    continue
            if target.Document is doc:
                if _type(target, "Assembly::AssemblyObject") and target is not top:
                    if target.Name not in seen:
                        visit(list(target.Group), mult * count, seen | {target.Name})
                    continue
                if target is top:
                    continue
                rows.append(_metered({"keys": [], "name": f"{target.Label} (in this file)", "qty": mult * count}, target))
                continue
            name = os.path.basename(getattr(target.Document, "FileName", "") or "") or target.Document.Label
            rows.append(_metered({"keys": _keys(target, imported), "name": name, "qty": mult * count}, target))

    visit(objects, 1, {top.Name} if top is not None else set())
    return rows


def read_bom(vault, path_or_doc, imported=None):
    """(lines, missing) of an assembly: an open document, or a file opened hidden for the read."""
    import FreeCAD

    from bruceworx import bom

    doc, opened = path_or_doc, []
    if isinstance(path_or_doc, str):
        doc = next((d for d in FreeCAD.listDocuments().values()
                    if d.FileName and os.path.realpath(d.FileName) == os.path.realpath(path_or_doc)), None)
        if doc is None:
            before = set(FreeCAD.listDocuments())
            doc = FreeCAD.openDocument(path_or_doc, True)
            opened = [n for n in FreeCAD.listDocuments() if n not in before]
    try:
        return bom.lines_from_walk(walk(doc, imported), vault.items)
    finally:
        for name in reversed(opened):
            try:
                FreeCAD.closeDocument(name)
            except Exception:
                pass


# ----- the check-in hook (bruceworx_vaultclient.CHECKIN_HOOKS) --------------------------------


class CheckinBom:
    """Reads an assembly's BOM before its check-in and records it with the new version."""

    def prepare(self, s, pn, notes):
        """(lines, missing) for an assembly, None for anything else. Raises when the BOM would
        contain itself, so nothing is checked in."""
        from bruceworx.pdm import LifecycleError

        item = s.vault.items.get(pn)
        if item is None or item.item_type != "assembly":
            return None
        imported = dict((path, key) for path, key in notes.get("imported", ()))
        lines, missing = read_bom(s.vault, s.work.path(pn), imported)
        cycle = s.vault.cycle_path(pn, [line.child for line in lines])
        if cycle:
            raise LifecycleError("its BOM would contain itself (" + " → ".join(cycle) + "); "
                                 "nothing was checked in")
        return lines, missing

    def record(self, s, pn, data, notes):
        if data is None:
            return
        lines, missing = data
        item = s.vault.items[pn]
        rev = item.latest
        version = s.vault.latest_version(pn)
        same = (rev.bom is not None and [l.as_dict() for l in rev.bom] ==
                [dict(l.as_dict(), rev=l.rev or s.vault.items[l.child].latest.code) for l in lines]
                and rev.bom_missing == missing)
        if not same and rev.state == "WIP":
            s.vault.set_bom(pn, lines, revision=rev.code, sha256=version.sha256 if version else "",
                            missing=missing)
        notes.setdefault("bom", []).append((pn, len(lines), list(missing)))


def checkin_text(notes):
    """The words after a check-in for its BOMs ("" when nothing needs saying)."""
    out = []
    for pn, count, missing in notes.get("bom", ()):
        if missing:
            names = ", ".join(f"{m['name']} ×{m['qty']}" for m in missing)
            out.append(f"{pn}: BOM recorded with {count} line{'s' if count != 1 else ''}. Not vault items: "
                       f"{names}. Add them to the vault (COTS parts too) and check the assembly in again; "
                       "until then it cannot be released.")
    return "\n\n".join(out)


# ----- dialogs ---------------------------------------------------------------------------------


def _vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def _session():
    try:
        return _vc().session()
    except Exception:
        return None


def _save_csv(parent, name, text):
    from PySide import QtWidgets

    from bruceworx import bom

    start = os.path.join(os.path.expanduser("~/Desktop"), name)
    path, _f = QtWidgets.QFileDialog.getSaveFileName(parent, "Export CSV", start, "CSV (*.csv)")
    if not path:
        return ""
    bom.write_text(path, text)
    _vc()._console(f"BruceWorx vault: wrote {path}", "msg")
    return path


def _classes():
    if "classes" not in _state:
        _state["classes"] = _make_classes()
    return _state["classes"]


def _make_classes():
    from PySide import QtCore, QtWidgets

    from bruceworx import bom, pdm
    from bruceworx import release_check as rc

    def tree(columns):
        t = QtWidgets.QTreeWidget()
        t.setColumnCount(len(columns))
        t.setHeaderLabels(list(columns))
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        t.setUniformRowHeights(True)
        return t

    class WhereUsedDialog(QtWidgets.QDialog):
        """Where Used...: exact assembly revisions, or the ones that may pick it up."""

        def __init__(self, s, pn, parent=None):
            super().__init__(parent)
            self.s, self.pn = s, pn
            self.setObjectName("BruceWorxWhereUsed")
            self.setWindowTitle(f"Where Used — {pn}")
            self.resize(640, 360)
            lay = QtWidgets.QVBoxLayout(self)
            item = s.vault.items[pn]
            head = QtWidgets.QLabel(f"<b>{pdm.display_part_number(pn, item.latest.code)}</b>  {item.description}")
            lay.addWidget(head)
            row = QtWidgets.QHBoxLayout()
            self.exact = QtWidgets.QRadioButton("Exact")
            self.exact.setToolTip("Assembly revisions built with this part: their BOM line resolves to it")
            self.pick = QtWidgets.QRadioButton("May pick up")
            self.pick.setToolTip("Live assembly revisions that use an older revision and would float to this one")
            self.exact.setChecked(True)
            for b in (self.exact, self.pick):
                b.toggled.connect(self.fill)
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
            self.list = tree(("Assembly", "State", "Current", "Qty", "Path", "Via"))
            lay.addWidget(self.list, 1)
            self.hint = QtWidgets.QLabel()
            self.hint.setWordWrap(True)
            lay.addWidget(self.hint)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            export = buttons.addButton("Export CSV…", QtWidgets.QDialogButtonBox.ActionRole)
            export.clicked.connect(self.export)
            buttons.rejected.connect(self.close)
            lay.addWidget(buttons)
            self.rows = rc.where_used(s.work, pn)
            self.fill()

        def shown(self):
            exact = self.exact.isChecked()
            return [r for r in self.rows if bool(r.get("exact", True)) == exact]

        def fill(self, *_args):
            self.list.clear()
            rows = self.shown()
            for r in rows:
                QtWidgets.QTreeWidgetItem(self.list, [
                    pdm.display_part_number(r["part_number"], r["revision"]), r["state"],
                    "yes" if r["released"] or (r["state"] != "Obsolete" and r.get("exact") is False) else "",
                    str(r.get("quantity") or ""), " › ".join(r.get("path") or ()), r["via"]])
            for i in range(self.list.columnCount()):
                self.list.resizeColumnToContents(i)
            other = len(self.rows) - len(rows)
            self.hint.setText(("No assembly revision uses it." if not rows else
                               f"{len(rows)} assembly revision{'s' if len(rows) != 1 else ''}.")
                              + (f" {other} more under “{'May pick up' if self.exact.isChecked() else 'Exact'}”."
                                 if other else ""))

        def export(self):
            _save_csv(self, f"{self.pn}-where-used.csv", bom.where_used_csv(self.shown()))

    class BomDialog(QtWidgets.QDialog):
        """Bill of Materials...: a revision's BOM, indented or flattened."""

        def __init__(self, s, pn, parent=None):
            super().__init__(parent)
            self.s, self.pn = s, pn
            self.setObjectName("BruceWorxBom")
            self.setWindowTitle(f"Bill of Materials — {pn}")
            self.resize(720, 420)
            lay = QtWidgets.QVBoxLayout(self)
            item = s.vault.items[pn]
            lay.addWidget(QtWidgets.QLabel(f"<b>{pn}</b>  {item.description}"))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Revision"))
            self.revision = QtWidgets.QComboBox()
            for rev in reversed(item.revisions):
                if rev.bom is not None:
                    self.revision.addItem(f"{rev.code}  ·  {rev.state}{'  ·  superseded' if rev.superseded else ''}",
                                          rev.code)
            row.addWidget(self.revision)
            self.indented = QtWidgets.QRadioButton("Indented")
            self.flat = QtWidgets.QRadioButton("Flattened")
            self.indented.setChecked(True)
            row.addSpacing(12)
            row.addWidget(self.indented)
            row.addWidget(self.flat)
            row.addStretch(1)
            lay.addLayout(row)
            self.list = tree(bom.BOM_COLUMNS)
            lay.addWidget(self.list, 1)
            self.hint = QtWidgets.QLabel()
            self.hint.setWordWrap(True)
            lay.addWidget(self.hint)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            export = buttons.addButton("Export CSV…", QtWidgets.QDialogButtonBox.ActionRole)
            export.clicked.connect(self.export)
            buttons.rejected.connect(self.close)
            lay.addWidget(buttons)
            self.revision.currentIndexChanged.connect(self.fill)
            self.indented.toggled.connect(self.fill)
            self.fill()

        def code(self):
            return self.revision.currentData()

        def fill(self, *_args):
            self.list.clear()
            code = self.code()
            if not code:
                self.hint.setText("No BOM recorded yet: check the assembly in to record one.")
                return
            flat = self.flat.isChecked()
            rows = bom.bom_rows(self.s.vault, self.pn, code, flattened=flat)
            if flat:
                self.list.setHeaderLabels(list(bom.FLAT_COLUMNS))
                for r in rows:
                    QtWidgets.QTreeWidgetItem(self.list, [r["part_number"], r["revision"], r["description"],
                                                          str(r["total"]), r["state"]])
            else:
                self.list.setHeaderLabels(list(bom.BOM_COLUMNS))
                for r in rows:
                    QtWidgets.QTreeWidgetItem(self.list, [
                        " " * (r["level"] - 1) + str(r["level"]), str(r["find"] or ""), r["part_number"],
                        r["revision"], r["description"], str(r["qty"]), str(r["total"]), r["state"], r["note"]])
            for i in range(self.list.columnCount()):
                self.list.resizeColumnToContents(i)
            rev = self.s.vault.model._revision(self.s.vault.items[self.pn], code)
            newer = self.s.vault.newer_children(self.pn, code)
            notes = []
            if rev is not None and rev.bom_missing:
                notes.append(f"{len(rev.bom_missing)} line(s) are not vault items: this revision cannot be released.")
            if newer:
                notes.append("Newer child released: " + ", ".join(f"{c} {a}→{b}" for c, a, b in newer)
                             + ". Raise an ECR if the assembly should take it.")
            self.hint.setText(" ".join(notes) or "Quantities are counts per parent; Total is per assembly.")

        def export(self):
            code = self.code()
            if code:
                flat = self.flat.isChecked()
                _save_csv(self, bom.default_csv_name(self.pn, code, flat),
                          bom.bom_csv(self.s.vault, self.pn, code, flattened=flat))

    return {"WhereUsedDialog": WhereUsedDialog, "BomDialog": BomDialog}


def _show(kind, pn):
    s = _session()
    pn = pn or _vc().current_target()
    if s is None or not pn or pn not in s.vault.items:
        return False
    dialog = _classes()[kind](s, pn, _vc()._window())
    dialog.setAttribute(__import__("PySide").QtCore.Qt.WA_DeleteOnClose, True)
    _state["dialogs"] = [d for d in _state["dialogs"] if _vc()._alive(d)] + [dialog]
    dialog.show()
    return dialog


def where_used(pn=None):
    return _show("WhereUsedDialog", pn)


def bill_of_materials(pn=None):
    return _show("BomDialog", pn)


def export_released_boms():
    from bruceworx import bom

    s = _session()
    if s is None:
        return ""
    rows = s.vault.outbox()
    if not rows:
        _vc()._message("Export Released BOMs", "No BOM has been released in this vault yet.")
        return ""
    return _save_csv(_vc()._window(), f"{s.name}-released-BOMs.csv", bom.outbox_csv(rows))


# ----- commands --------------------------------------------------------------------------------


def _active_where_used(s, pn, st):
    return pn in s.vault.items


def _active_bom(s, pn, st):
    item = s.vault.items.get(pn)
    return item is not None and (item.item_type == "assembly" or any(r.bom is not None for r in item.revisions))


ITEM_COMMANDS = (
    ("BruceWorx_VaultWhereUsed", "Where Used…", "The assembly revisions that use this item, with the "
     "quantity and the path; exact, or the ones that may pick up a newer revision.", "whereused", "secondary",
     lambda pn=None: where_used(pn), _active_where_used, ""),
    ("BruceWorx_VaultBom", "Bill of Materials…", "This assembly's BOM per revision, indented or flattened, "
     "as recorded at check-in; Export CSV.", "bom", "secondary", lambda pn=None: bill_of_materials(pn),
     _active_bom, ""),
)
EXPORT_COMMAND = ("BruceWorx_VaultExportReleasedBoms", "Export Released BOMs…", "Every released BOM of the "
                  "vault (what Ops receives) as one CSV file.", "export", "secondary")


def _icon_name(kind, colour):
    return f"BruceWorx_Vault_{kind}_{colour}"  # the vault client's naming, so its menus find them


class _ItemCommand:
    def __init__(self, entry):
        self.name, self.text, self.tip, self.icon, self.colour, self.fn, self.active, _accel = entry

    def GetResources(self):
        return {"Pixmap": _icon_name(self.icon, self.colour), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        try:
            s = _session()
            pn = _vc().current_target() if s is not None else ""
            return bool(pn) and bool(self.active(s, pn, None))
        except Exception:
            return False

    def Activated(self):
        try:
            self.fn()
        except Exception as exc:
            _vc()._message(self.text.rstrip("…"), str(exc), "warning")


class _ExportCommand:
    def GetResources(self):
        return {"Pixmap": _icon_name(EXPORT_COMMAND[3], EXPORT_COMMAND[4]), "MenuText": EXPORT_COMMAND[1],
                "ToolTip": EXPORT_COMMAND[2]}

    def IsActive(self):
        return _session() is not None

    def Activated(self):
        try:
            export_released_boms()
        except Exception as exc:
            _vc()._message("Export Released BOMs", str(exc), "warning")


def _extend_vault_menu():
    import FreeCADGui
    from PySide import QtGui, QtWidgets

    sub = _vc()._window().findChild(QtWidgets.QMenu, "BruceWorxVaultMenu")
    if sub is None or sub.property("bwBom"):
        return
    sub.setProperty("bwBom", True)
    act = sub.addAction(FreeCADGui.getIcon(_icon_name(EXPORT_COMMAND[3], EXPORT_COMMAND[4])) or QtGui.QIcon(),
                        EXPORT_COMMAND[1])
    act.setToolTip(EXPORT_COMMAND[2])
    act.triggered.connect(lambda _c=False: FreeCADGui.runCommand(EXPORT_COMMAND[0]))


def install():
    """Where Used... and Bill of Materials... in the vault menus, the check-in hook, the export."""
    if _state["installed"]:
        return
    _state["installed"] = True
    import FreeCADGui
    from PySide import QtCore

    for kind, role in {(e[3], e[4]) for e in ITEM_COMMANDS} | {(EXPORT_COMMAND[3], EXPORT_COMMAND[4])}:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
               + _SVG[kind].replace("{c}", _COLOURS[role]) + "</svg>")
        FreeCADGui.addIcon(_icon_name(kind, role), svg.encode("utf-8"), "SVG")
    for entry in ITEM_COMMANDS:
        FreeCADGui.addCommand(entry[0], _ItemCommand(entry))
    FreeCADGui.addCommand(EXPORT_COMMAND[0], _ExportCommand())
    vc = _vc()
    vc.add_item_commands(ITEM_COMMANDS, after="BruceWorx_VaultRetire")  # after bruceworx_changes' items
    hooks = getattr(vc, "CHECKIN_HOOKS", None)
    if hooks is not None and not any(isinstance(h, CheckinBom) for h in hooks):
        hooks.append(CheckinBom())

    def late():
        try:
            _extend_vault_menu()
        except Exception as exc:
            _vc()._console(f"Vault BOM: menu not extended: {exc}", "warn")

    QtCore.QTimer.singleShot(0, late)
