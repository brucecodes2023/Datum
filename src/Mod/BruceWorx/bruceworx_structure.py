# SPDX-License-Identifier: LGPL-2.1-or-later
"""The right panel's Vault tab (BW-PDM-09, plan section 3; built 26 Oct 2026): List | Structure.

The tab is registered with bruceworx_rightpanel ("BWVault"). It shows one root at a time, picked at
the top: a project (PDM-09) or any assembly; the picker follows the Browser when an assembly of the
vault is selected there. Vault browsing stays on the Start page (the user's 25 Oct decision): both
views here are scoped to the root.

* Structure: the product tree, one level at a time (lazy), rows from the per-revision BOM (PDM-06)
  or, until one is recorded, from the checked-in file's links, marked "from links". Columns: the
  item (P/N-REV, description, quantity per parent), its state, and its Drawing cell (missing, out of
  date, metadata only, current, in model; see bruceworx/product_tree.py).
* List: every item under the root once, flat, with its total quantity Q_t.
* Filter chips Needs a drawing, Out of date, Checked out; an assembly row that hides a match below it
  carries the count.
* A row's menu: Open, Open drawing, Update drawing, Create drawing, Where used..., Show in list.
* Footer: Open top, Bill of materials, Update drawings... (checks out each out-of-date drawing, opens
  it, recomputes it and offers the check-in).
* Empty state: New Project... (bruceworx_project).
"""

from __future__ import annotations

import os

TAB = "BWVault"
VIEWS = (("structure", "Structure"), ("list", "List"))
COLUMNS = ("Item", "State", "Drawing")
PN_ROLE = 0x0100 + 7
KIND_ROLE = 0x0100 + 8
PLACEHOLDER = "…"
STATE_COLOURS = {"WIP": "#8E8E93", "In Review": "#D97706", "Released": "#2F9E44", "Obsolete": "#8E8E93"}

_state = {"installed": False, "tab": None}
_classes = None


def _vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def _pt():
    from bruceworx import product_tree

    return product_tree


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


# ----- pure helpers (unit tested) -----------------------------------------------------------------------


def chip_text(text, count):
    return f"{text}  {count}" if count else text


def drawing_colour(state, colours):
    """The Drawing cell's colour role for a product_tree state."""
    pt = _pt()
    if state == pt.OUT_OF_DATE:
        return colours["warn"]
    if state in (pt.MISSING, pt.NO_FILE):
        return colours["error"]
    if state == pt.CURRENT:
        return colours["ok"]
    return colours["secondary"]


def item_text(row):
    """The Item column: "P/N-REV  description  ×2"."""
    words = [row["display"]]
    if row.get("description"):
        words.append(row["description"])
    text = "  ".join(words)
    if row.get("qty") not in (None, 1):
        text += f"  ×{row['qty']}"
    return text


def state_text(row):
    text = row["state"]
    if row.get("source") == "links":
        text += "  · from links"
    if row.get("lock"):
        text += "  · " + _vc().actor_name(row["lock"], "")
    return text


def selected_root(selection, s):
    """The P/N of the assembly the Browser selection stands for, or ""."""
    vc = _vc()
    for obj in selection:
        pn = vc.pn_for_object(obj, s)
        item = s.vault.items.get(pn) if pn else None
        if item is not None and item.item_type == "assembly":
            return pn
    return ""


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    pt = _pt()

    class StructureTab(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxStructure")
            self.view = "structure"
            self.chip = "all"
            self.root_key = ""
            self.tree_model = None
            self.structure = None
            self.rows = {}
            self.pinned = False  # the user picked a root: the Browser does not move it until a new selection
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            top = QtWidgets.QWidget(self)
            top.setObjectName("bwVaultTop")
            tl = QtWidgets.QVBoxLayout(top)
            tl.setContentsMargins(12, 8, 12, 6)
            tl.setSpacing(6)
            head = QtWidgets.QHBoxLayout()
            self.name = QtWidgets.QLabel("Vault")
            self.name.setObjectName("bwVaultName")
            self.badge = QtWidgets.QLabel("")
            self.badge.setObjectName("bwVaultChip")
            self.badge.setToolTip("This project's units differ from the vault's default")
            self.badge.hide()
            head.addWidget(self.name, 1)
            head.addWidget(self.badge)
            self.segments = {}
            group = QtWidgets.QButtonGroup(self)
            group.setExclusive(True)
            for key, text in VIEWS:
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.setCheckable(True)
                b.setChecked(key == self.view)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                b.clicked.connect(lambda _c=False, k=key: self.set_view(k))
                group.addButton(b)
                head.addWidget(b)
                self.segments[key] = b
            self.refresh_button = QtWidgets.QToolButton()
            self.refresh_button.setObjectName("bwVaultTool")
            self.refresh_button.setText("↻")
            self.refresh_button.setToolTip("Read the vault again")
            self.refresh_button.clicked.connect(lambda: _vc().changed(refresh=True))
            head.addWidget(self.refresh_button)
            tl.addLayout(head)
            self.root = QtWidgets.QComboBox()
            self.root.setObjectName("bwStructureRoot")
            self.root.setToolTip("The project or assembly shown; follows an assembly selected in the Browser")
            self.root.currentIndexChanged.connect(self._root_picked)
            tl.addWidget(self.root)
            chips = QtWidgets.QHBoxLayout()
            chips.setSpacing(4)
            self.chips = {}
            cgroup = QtWidgets.QButtonGroup(self)
            cgroup.setExclusive(True)
            for key, text in (("all", "All"),) + pt.CHIPS:
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.setCheckable(True)
                b.setChecked(key == "all")
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                b.clicked.connect(lambda _c=False, k=key: self.set_chip(k))
                cgroup.addButton(b)
                chips.addWidget(b)
                self.chips[key] = b
            chips.addStretch(1)
            tl.addLayout(chips)
            lay.addWidget(top)
            self.list = QtWidgets.QTreeView(self)
            self.list.setObjectName("bwStructureList")
            self.list.setUniformRowHeights(True)
            self.list.setIndentation(14)
            self.list.setAllColumnsShowFocus(True)
            self.list.setExpandsOnDoubleClick(False)
            self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.model = QtGui.QStandardItemModel(0, len(COLUMNS), self.list)
            self.model.setHorizontalHeaderLabels(list(COLUMNS))
            self.list.setModel(self.model)
            header = self.list.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            self.list.expanded.connect(self._expanded)
            self.list.doubleClicked.connect(self._open_index)
            self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.context_menu)
            lay.addWidget(self.list, 1)
            self.empty = QtWidgets.QWidget(self)
            el = QtWidgets.QVBoxLayout(self.empty)
            el.setContentsMargins(16, 24, 16, 24)
            el.addStretch(1)
            self.empty_text = QtWidgets.QLabel("")
            self.empty_text.setObjectName("bwVaultEmpty")
            self.empty_text.setAlignment(QtCore.Qt.AlignCenter)
            self.empty_text.setWordWrap(True)
            el.addWidget(self.empty_text)
            self.empty_button = QtWidgets.QPushButton("New Project…")
            self.empty_button.setObjectName("bwDashPrimary")
            self.empty_button.setCursor(QtCore.Qt.PointingHandCursor)
            self.empty_button.clicked.connect(self.new_project)
            el.addWidget(self.empty_button, 0, QtCore.Qt.AlignHCenter)
            el.addStretch(1)
            self.empty.hide()
            lay.addWidget(self.empty, 1)
            foot = QtWidgets.QWidget(self)
            foot.setObjectName("bwInspectorFoot")
            fl = QtWidgets.QHBoxLayout(foot)
            fl.setContentsMargins(8, 4, 8, 6)
            self.open_top = self._foot(foot, "Open top", "Open the root assembly", self.open_root)
            self.bom_button = self._foot(foot, "Bill of materials", "The root's BOM by revision (PDM-06)", self.show_bom)
            self.update_button = self._foot(foot, "Update drawings…",
                                            "Check out every out-of-date drawing under the root, open and recompute it, "
                                            "and offer the check-in", self.update_drawings)
            fl.addWidget(self.open_top)
            fl.addWidget(self.bom_button)
            fl.addStretch(1)
            fl.addWidget(self.update_button)
            lay.addWidget(foot)
            self.foot = foot
            self.colours = {"ok": QtGui.QColor("#2F9E44"), "warn": QtGui.QColor("#D97706"),
                            "error": QtGui.QColor("#E5484D"), "secondary": QtGui.QColor("#8E8E93")}
            _vc().add_listener(self.refresh)
            self.refresh()

        def _foot(self, parent, text, tip, slot):
            b = QtWidgets.QToolButton(parent)
            b.setObjectName("bwInspectorMore")
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        # --- data
        def session(self):
            try:
                return _vc().session()
            except Exception:
                return None

        def refresh(self):
            if not _alive(self):
                _vc().remove_listener(self.refresh)
                return
            s = self.session()
            self.structure = None
            self.tree = None
            if s is None:
                self.name.setText("Vault")
                self.root.clear()
                self.show_empty("Open a vault on the Start page to see a project's structure here.", False)
                return
            from bruceworx import vault_trees

            self.name.setText(s.name)
            self.structure = vault_trees.structure(s.vault, links=self._links)
            self.tree = pt.Tree(s.vault, self.structure)
            roots = pt.roots(s.vault, self.structure)
            self.root.blockSignals(True)
            self.root.clear()
            for r in roots:
                self.root.addItem(r["text"], r)
            wanted = next((i for i in range(self.root.count()) if self.root.itemData(i)["key"] == self.root_key), -1)
            if wanted < 0 and self.root.count():
                wanted = 0
                self.root_key = self.root.itemData(0)["key"]
            self.root.setCurrentIndex(wanted)
            self.root.blockSignals(False)
            if not roots:
                self.show_empty("No project yet. Type one as a few lines and every part, assembly and drawing is "
                                "numbered and checked in as WIP 01.", True)
                return
            self.fill()

        def _links(self, pn):
            s = self.session()
            path = s.work.path(pn) if s is not None else ""
            from bruceworx import vault_trees

            if path and os.path.exists(path):
                return vault_trees.file_links(path)
            return vault_trees._links_of(s.vault, pn) if s is not None else {}

        def current_root(self):
            data = self.root.currentData()
            return data if isinstance(data, dict) else None

        def show_empty(self, text, button):
            self.empty_text.setText(text)
            self.empty_button.setVisible(button)
            self.list.hide()
            self.root.setVisible(False)
            for b in self.chips.values():
                b.setVisible(False)
            self.foot.hide()
            self.badge.hide()
            self.empty.show()

        def fill(self):
            self.empty.hide()
            self.root.setVisible(True)
            for b in self.chips.values():
                b.setVisible(True)
            self.foot.show()
            self.list.show()
            root = self.current_root()
            self.model.removeRows(0, self.model.rowCount())
            self.rows = {}
            if root is None or self.tree is None:
                return
            pn = root["pn"]
            s = self.session()
            units = root.get("units") or ""
            try:
                import bruceworx_project

                default = bruceworx_project.vault_units(s.root)
            except Exception:
                default = "mm"
            self.badge.setText(units)
            self.badge.setVisible(bool(units) and units != default)
            counts = self.tree.counts(pn)
            for key, text in pt.CHIPS:
                n = counts.get(key, 0) + (1 if self.tree.matches(pn, key) else 0)
                self.chips[key].setText(chip_text(text, n))
            chip = None if self.chip == "all" else self.chip
            if self.view == "structure":
                self.list.setRootIsDecorated(True)
                row = self.tree.row(pn)
                items = self._items(row)
                self.model.appendRow(items)
                self._add_children(items[0], pn, chip)
                self.list.expand(items[0].index())
            else:
                self.list.setRootIsDecorated(False)
                for row in self.tree.flat(pn, chip):
                    self.model.appendRow(self._items(row))
            self.update_button.setEnabled(bool(self.tree.out_of_date(pn)))

        def _items(self, row):
            pn = row["pn"]
            self.rows[pn] = row
            first = QtGui.QStandardItem(item_text(row))
            first.setData(pn, PN_ROLE)
            first.setData(row["type"], KIND_ROLE)
            first.setEditable(False)
            tip = [row["display"], row.get("description", "")]
            if row.get("counts") and any(row["counts"].values()):
                bits = [f"{n} {text.lower()}" for (key, text), n in zip(pt.CHIPS, row["counts"].values()) if n]
                tip.append("below: " + ", ".join(bits))
            if self.structure is not None:
                tip.append(pt.where_used_text(self.structure["bom"], self.structure, pn))
            first.setToolTip("\n".join(t for t in tip if t))
            state = QtGui.QStandardItem(state_text(row))
            state.setEditable(False)
            state.setForeground(QtGui.QBrush(QtGui.QColor(STATE_COLOURS.get(row["state"], "#8E8E93"))))
            cell = row["drawing"]
            drawing = QtGui.QStandardItem(pt.LABELS.get(cell["state"], cell["state"]))
            drawing.setEditable(False)
            drawing.setForeground(QtGui.QBrush(drawing_colour(cell["state"], self.colours)))
            drawing.setToolTip((", ".join(cell["drawings"]) + ": " if cell["drawings"] else "") + cell["text"])
            return [first, state, drawing]

        def _add_children(self, item, pn, chip):
            """The next level under an open row; deeper rows get a placeholder so the arrow shows."""
            for row in self.tree.children(pn, chip):
                items = self._items(row)
                item.appendRow(items)
                if row["has_children"]:
                    holder = QtGui.QStandardItem(PLACEHOLDER)
                    holder.setEditable(False)
                    items[0].appendRow([holder, QtGui.QStandardItem(""), QtGui.QStandardItem("")])

        def _expanded(self, index):
            item = self.model.itemFromIndex(index.siblingAtColumn(0))
            if item is None or item.rowCount() != 1 or item.child(0).text() != PLACEHOLDER:
                return
            item.removeRow(0)
            chip = None if self.chip == "all" else self.chip
            self._add_children(item, item.data(PN_ROLE), chip)

        # --- choices
        def set_view(self, key):
            self.view = key
            self.fill()

        def set_chip(self, key):
            self.chip = key
            self.fill()

        def _root_picked(self, _index):
            root = self.current_root()
            if root is not None:
                self.root_key = root["key"]
                self.pinned = True
                self.fill()

        def set_root(self, key):
            """Show a root by its key ("project:CODE" or "assembly:P/N"); True when it is there."""
            for i in range(self.root.count()):
                if self.root.itemData(i)["key"] == key:
                    if self.root.currentIndex() != i:
                        self.root.setCurrentIndex(i)  # fills
                    else:
                        self.root_key = key
                        self.fill()
                    return True
            return False

        def selection_changed(self):
            """Follow the Browser: an assembly of the vault selected there becomes the root."""
            s = self.session()
            if s is None:
                return
            try:
                import FreeCADGui

                pn = selected_root(FreeCADGui.Selection.getSelection(), s)
            except Exception:
                pn = ""
            if not pn:
                return
            key = "assembly:" + pn
            projects = [r for r in self._roots() if r["pn"] == pn and r["project"]]
            if projects:
                key = projects[0]["key"]
            if key != self.root_key:
                self.pinned = False
                self.set_root(key)

        def _roots(self):
            return [self.root.itemData(i) for i in range(self.root.count())]

        # --- actions
        def pn_at(self, index):
            if not index.isValid():
                return ""
            return index.siblingAtColumn(0).data(PN_ROLE) or ""

        def _open_index(self, index):
            pn = self.pn_at(index)
            if pn:
                _vc().open_from_vault(pn)

        def context_menu(self, pos):
            index = self.list.indexAt(pos)
            pn = self.pn_at(index)
            if not pn:
                return
            row = self.rows.get(pn) or self.tree.row(pn)
            cell = row["drawing"]
            menu = QtWidgets.QMenu(self)
            menu.addAction("Open", lambda: _vc().open_from_vault(pn))
            if cell["drawings"]:
                if len(cell["drawings"]) == 1:
                    dwg = cell["drawings"][0]
                    menu.addAction("Open drawing", lambda d=dwg: _vc().open_from_vault(d))
                    menu.addAction("Update drawing", lambda d=dwg: self.update_drawings([d]))
                else:
                    sub = menu.addMenu("Open drawing")
                    for dwg in cell["drawings"]:
                        sub.addAction(dwg, lambda d=dwg: _vc().open_from_vault(d))
                    menu.addAction("Update drawings", lambda ds=list(cell["drawings"]): self.update_drawings(ds))
            elif row["type"] in ("part", "assembly"):
                menu.addAction("Create drawing", lambda: self.create_drawing(pn))
            menu.addSeparator()
            menu.addAction("Where used…", lambda: self._bom().where_used(pn))
            if row["type"] == "assembly":
                menu.addAction("Bill of materials…", lambda: self._bom().bill_of_materials(pn))
            menu.addAction("Show in list", lambda: self.show_in_list(pn))
            menu.addAction("Show on the Start page", lambda: _vc().show_in_panel(pn))
            menu.exec(self.list.viewport().mapToGlobal(pos))

        def _bom(self):
            import bruceworx_bom

            return bruceworx_bom

        def show_in_list(self, pn):
            self.segments["list"].setChecked(True)
            self.set_view("list")
            for r in range(self.model.rowCount()):
                index = self.model.index(r, 0)
                if index.data(PN_ROLE) == pn:
                    self.list.setCurrentIndex(index)
                    self.list.scrollTo(index)
                    break

        def open_root(self):
            root = self.current_root()
            if root is not None:
                _vc().open_from_vault(root["pn"])

        def show_bom(self):
            root = self.current_root()
            if root is not None:
                self._bom().bill_of_materials(root["pn"])

        def update_drawings(self, drawings=None):
            root = self.current_root()
            if drawings is None:
                drawings = self.tree.out_of_date(root["pn"]) if root is not None and self.tree is not None else []
            if not drawings:
                _vc()._message("Update drawings", "Every drawing under this root is current.")
                return
            update_drawings(drawings)

        def create_drawing(self, pn):
            s = self.session()
            if s is None:
                return
            import bruceworx_project

            root = self.current_root()
            units = root.get("units") if root else None
            ok, dwg = _vc()._run("Create drawing", bruceworx_project.new_drawing_item, s.vault, s.work, pn,
                                 units or None)
            if ok:
                _vc()._console(f"BruceWorx vault: drawing {dwg} of {pn} checked in as WIP 01", "msg")
                s.refresh()
                _vc().changed()

        def new_project(self):
            import FreeCADGui

            FreeCADGui.runCommand("BruceWorx_NewProject")

    return StructureTab


def update_drawings(drawings):
    """Update drawings...: check out each, open it, recompute it; then the Check In panel for the first,
    the rest listed (one check-in panel at a time)."""
    import FreeCAD

    vc = _vc()
    s = vc.session()
    if s is None:
        return False
    opened = []
    for dwg in drawings:
        if s.vault.locks.get(dwg) != s.vault.actor and not vc.check_out(dwg):
            continue
        if not vc.open_from_vault(dwg, ask=False):
            continue
        doc = vc.document_for(s.work.path(dwg))
        if doc is not None:
            try:
                doc.recompute()
                try:
                    import bruceworx_drawing

                    bruceworx_drawing.refresh_document(doc)
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                FreeCAD.Console.PrintWarning(f"Update drawings: {dwg} did not recompute: {exc}\n")
        opened.append(dwg)
    if not opened:
        return False
    if len(opened) > 1:
        vc._message("Update drawings", f"{len(opened)} drawings are checked out to you and open: " + ", ".join(opened)
                    + ".\nLook each one over, then check them in (the Check In panel opens for the first).")
    vc.check_in(opened[0])
    return True


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


def tab():
    t = _state.get("tab")
    return t if _alive(t) else None


def show_root(key):
    """Bring the Vault tab forward on a root ("project:CODE" or "assembly:P/N")."""
    import bruceworx_rightpanel

    t = tab()
    if t is None:
        return False
    t.refresh()
    bruceworx_rightpanel.show_tab(TAB)
    return t.set_root(key)


def _factory(parent):
    StructureTab = classes()
    t = StructureTab(parent)
    _state["tab"] = t
    return t


def install():
    """The Vault tab's content (bruceworx_rightpanel.register)."""
    if _state["installed"]:
        return
    import bruceworx_rightpanel

    if not bruceworx_rightpanel.enabled():
        return
    bruceworx_rightpanel.register(TAB, _factory)
    _state["installed"] = True
