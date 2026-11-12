# SPDX-License-Identifier: LGPL-2.1-or-later
"""The assembly's Model Builder (ASM-UI-01, 2 Nov 2026): mates as nodes in the left sidebar's "Assembly" tab.

While an assembly is active (UtilsAssembly.activeAssembly()) and the Simulation workspace is not up, the left panel gets a
second tab, "Assembly", next to "Browser" (the Simulation tab's strip and look: ``bruceworx_sim_workspace.show_tab``). It holds
the Model Builder (tree) over the Settings pane, with a splitter. A mate is a node like a force in a study: selecting it shows
its Settings and highlights its two references in the 3D view; every edit is live and one undo step (no OK, no task form).

The panes hold no assembly logic: every read and write goes through ``bruceworx_asm_model``.

    BruceWorx_Mate  selects a virtual "New mate" node; the next two 3D picks (on two different parts) create the mate with the
                    default type that fits (or the type clicked in the grid meanwhile); Esc drops the node
    pick row        a click arms that slot of the selected mate; the next 3D pick replaces it
    type grid       FreeCAD's joint icons; disabled types do not fit the picks; a click is ``set_mate_type``

Picks reach the pane through a selection observer that listens only while the new-mate node is current or a slot is armed;
``bruceworx_picktrace.slot_armed`` tells the assembly pick rule to leave the raw element pick alone meanwhile. An open
assembly task dialog (FreeCAD's own joint commands) keeps the panes out of the selection altogether.
"""

from __future__ import annotations

import os
import re

import bruceworx_asm_model as model

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "Resources", "icons", "sim")
TAB = "Assembly"
DOCK = "BWAsmSidebar"
KEY = 0x0100  # Qt.UserRole
NEW_KEY = "mate/new"
DEBOUNCE_MS = 150
POLL_MS = 600
LABEL_WIDTH = 96
SPLIT = (430, 520)
MARK_ICONS = {"ok": "s-ok", "stale": "s-stale", "warn": "s-warn", "bad": "s-bad"}
VOLATILE = ("Visibility", "Shape", "Proxy", "Placement", "Label2", "ExpressionEngine")

# FreeCAD's own joint icons (Assembly module resources) for the mate grid; no new icons are drawn.
MATE_ICONS = {
    "coincident": "Assembly_CreateJointDistance", "parallel": "Assembly_CreateJointParallel",
    "perpendicular": "Assembly_CreateJointPerpendicular", "tangent": "Assembly_CreateJointTangent",
    "concentric": "Assembly_CreateJointCylindrical", "lock": "Assembly_CreateJointFixed",
    "distance": "Assembly_CreateJointDistance", "angle": "Assembly_CreateJointAngle",
    "hinge": "Assembly_CreateJointRevolute", "slider": "Assembly_CreateJointSlider",
    "cylindrical": "Assembly_CreateJointCylindrical", "ball": "Assembly_CreateJointBall",
    "gear": "Assembly_CreateJointGears", "rack": "Assembly_CreateJointRackPinion",
    "screw": "Assembly_CreateJointScrew", "belt": "Assembly_CreateJointPulleys",
}
SHORT = {"rack": "Rack", "perpendicular": "Perpend.", "cylindrical": "Cylind."}

EXTRA_QSS = """
QToolButton#BWAsmMate { border: 1px solid #c9ced6; border-radius: 3px; background: #fff; padding: 3px 2px; font-size: 11px; }
QToolButton#BWAsmMate:hover { border-color: #8a93a0; }
QToolButton#BWAsmMate:checked { background: #dbe8fa; border-color: #1f6fd1; }
QToolButton#BWAsmMate:disabled { color: #b4bac3; background: #f3f4f6; border-color: #e3e6ea; }
QToolButton#BWAsmFold { border: none; color: #5b6573; font-size: 11px; text-align: left; padding: 2px 0; }
QPushButton#BWAsmPick { text-align: left; padding: 4px 8px; }
QPushButton#BWAsmPick:checked { background: #dbe8fa; border-color: #1f6fd1; }
QLineEdit#BWAsmTitle { border: 1px solid transparent; background: transparent; font-size: 15px; font-weight: 600;
  color: #1d232c; padding: 0; }
QLineEdit#BWAsmTitle:focus { border-color: #1f6fd1; background: #fff; }
"""

_a = {"attached": False, "installed": False, "split": None, "mb": None, "st": None, "tree": None, "scroll": None,
      "form": None, "asm": None, "key": None, "new": None, "slot": None, "note": "", "sig": None, "fields": {},
      "syncing": False, "observer": None, "doc_observer": None, "timer": None, "poll": None,
      "filter": None, "icons": {}, "buttons": {}, "grid": {}, "picks": [], "title": None, "errors": {}}


# ----- small helpers -------------------------------------------------------------------------------------------------


def _alive(widget):
    try:
        import shiboken6

        return widget is not None and shiboken6.isValid(widget)
    except Exception:
        return widget is not None


def _dot(name):
    """The QIcon of a state dot (the Simulation tree's own sprites)."""
    from PySide import QtGui

    if name not in _a["icons"]:
        path = os.path.join(ICON_DIR, name + ".svg")
        _a["icons"][name] = QtGui.QIcon(path) if os.path.isfile(path) else QtGui.QIcon()
    return _a["icons"][name]


def _joint_icon(key):
    from PySide import QtGui

    name = MATE_ICONS.get(key)
    if not name:
        return QtGui.QIcon()
    ck = ("mate", name)
    if ck not in _a["icons"]:
        try:
            import FreeCADGui as Gui

            _a["icons"][ck] = Gui.getIcon(name)
        except Exception:
            _a["icons"][ck] = QtGui.QIcon()
    return _a["icons"][ck]


def _object_icon(doc_name, obj_name):
    """The icon the Browser shows for the object (``ViewObject.Icon``)."""
    from PySide import QtGui

    if not obj_name:
        return QtGui.QIcon()
    try:
        import FreeCAD

        ic = FreeCAD.getDocument(doc_name).getObject(obj_name).ViewObject.Icon
    except Exception:
        return QtGui.QIcon()
    if isinstance(ic, QtGui.QIcon):
        return ic
    try:
        if isinstance(ic, (bytes, bytearray)) or (isinstance(ic, str) and ic.lstrip().startswith("/*")):  # XPM data
            pix = QtGui.QPixmap()
            pix.loadFromData(ic if isinstance(ic, (bytes, bytearray)) else ic.encode())
            return QtGui.QIcon(pix)
        if isinstance(ic, str):
            return QtGui.QIcon(ic)
    except Exception:
        pass
    return QtGui.QIcon()


def assembly():
    """The assembly the panes show (None when none)."""
    ident = _a["asm"]
    if not ident:
        return None
    import FreeCAD

    try:
        return FreeCAD.getDocument(ident[0]).getObject(ident[1])
    except Exception:
        return None


def active_assembly():
    """The active assembly in the 3D view, None if none or while the Simulation workspace is up."""
    try:
        import bruceworx_sim_workspace as sw

        if sw.active():
            return None
    except Exception:
        pass
    try:
        import FreeCADGui as Gui

        if Gui.ActiveDocument is None:
            return None
        import UtilsAssembly

        return UtilsAssembly.activeAssembly()
    except Exception:
        return None


def current_key():
    return _a["key"]


def current_joint():
    """The mate object of the current node (None for any other node, and the virtual one)."""
    key = _a["key"]
    asm = assembly()
    if asm is None or not key or not key.startswith("mate/") or key == NEW_KEY:
        return None
    obj = asm.Document.getObject(key[len("mate/"):])
    return obj if obj is not None and hasattr(obj, "Reference1") else None


def note():
    """The last message shown in the Selections section (refused pick, not in the assembly)."""
    return _a["note"]


def tree_widget():
    return _a["tree"]


def attached():
    return bool(_a["attached"])


def _set_note(text):
    _a["note"] = text
    label = _a["fields"].get("_note")
    if label is not None and _alive(label):
        label.setText(text)
        label.setVisible(bool(text))


def _flag_armed():
    """Tells the assembly pick rule (bruceworx_picktrace) to leave the raw element pick alone while picks are wanted."""
    on = bool(_a["attached"] and (_a["key"] == NEW_KEY or (_a["slot"] is not None and current_joint() is not None)))
    try:
        import bruceworx_picktrace as pt

        pt.slot_armed = on
    except Exception:
        pass
    return on


# ----- the tab ---------------------------------------------------------------------------------------------------------


def _build():
    if _a["split"] is not None:
        return True
    try:
        import FreeCAD

        if not FreeCAD.GuiUp:
            return False
        from PySide import QtCore, QtWidgets
    except Exception:
        return False
    import bruceworx_sim_workspace as sw
    import bruceworx_sim_panes as sp

    c = sw._classes()
    mb = c["Pane"]("Model Builder", "")
    st = c["Pane"]("Settings", "")
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.setObjectName("BWSimSidebarSplit")  # the Simulation tab's splitter look
    split.setChildrenCollapsible(False)
    split.setWindowTitle(TAB)  # the overlay names the tab after it
    split.addWidget(mb)
    split.addWidget(st)
    split.setSizes(list(SPLIT))
    split.setStyleSheet(sw.stylesheet() + sp.EXTRA_QSS + EXTRA_QSS)
    sw.clear_host(mb.host)
    sw.clear_host(st.host)

    tree = QtWidgets.QTreeWidget()
    tree.setObjectName("BWSimTree")
    tree.setHeaderHidden(True)
    tree.setColumnCount(3)
    tree.setIndentation(14)
    tree.setIconSize(QtCore.QSize(16, 16))
    tree.setUniformRowHeights(True)
    tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    head = tree.header()
    head.setStretchLastSection(False)
    head.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
    head.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
    head.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
    tree.setColumnWidth(1, 120)
    tree.setColumnWidth(2, 26)
    tree.currentItemChanged.connect(lambda cur, _p: select_key(cur.data(0, KEY) if cur is not None else None, from_tree=True))
    mb.host.layout().addWidget(tree, 1)

    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("BWSimScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    st.host.layout().addWidget(scroll, 1)
    _a.update(split=split, mb=mb, st=st, tree=tree, scroll=scroll)

    timer = QtCore.QTimer(mb)
    timer.setSingleShot(True)
    timer.setInterval(DEBOUNCE_MS)
    timer.timeout.connect(_flush)
    _a["timer"] = timer
    return True


def tab_shown():
    import bruceworx_sim_workspace as sw

    return sw.tab_shown(TAB)


def show_tab(select=False):
    """Adds the Assembly tab to the left panel's strip (it stays on the current tab unless ``select``)."""
    import bruceworx_sim_workspace as sw

    if not _build():
        return False
    left = sw._left()
    current = left.currentIndex() if left is not None else 0
    ok = sw.show_tab(TAB, DOCK, _a["split"])
    if ok and not select and left is not None and current != left.currentIndex():
        sw._show_index(current)
        if sw.strip() is not None:
            sw.strip().sync()
    return ok


def hide_tab():
    import bruceworx_sim_workspace as sw

    if TAB in sw._s["tabs"]:
        sw.hide_tab(TAB)


def select_tab():
    import bruceworx_sim_workspace as sw

    index = sw._tab_index(TAB)
    return index >= 0 and sw._show_index(index)


# ----- attaching -------------------------------------------------------------------------------------------------------


def attach(asm):
    """Shows the Assembly tab for ``asm`` (idempotent)."""
    if asm is None or not _build():
        return False
    ident = (asm.Document.Name, asm.Name)
    if _a["attached"] and _a["asm"] == ident:
        return True
    if not _a["attached"]:
        _start_observers()
        _a["attached"] = True
        show_tab()
    _a["asm"] = ident
    _a["key"] = None
    _a["new"] = None
    _a["slot"] = None
    _a["sig"] = None
    refresh(select=model.tree(asm).key)
    return True


def detach():
    if not _a["attached"]:
        return
    _a["attached"] = False
    _stop_observers()
    _flag_armed()
    _clear_selection()
    hide_tab()
    _a.update(asm=None, key=None, new=None, slot=None, sig=None, note="")
    tree = _a["tree"]
    if _alive(tree):
        tree.blockSignals(True)
        tree.clear()
        tree.blockSignals(False)
    _clear_form()


def sync():
    """The poll: the tab follows the active assembly."""
    asm = active_assembly()
    if asm is None:
        if _a["attached"]:
            detach()
        return
    attach(asm)


def install():
    """Registers the BruceWorx_Mate command and starts following the active assembly; called once from InitGui."""
    if _a["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_Mate", _MateCommand())
    mw = Gui.getMainWindow()
    if mw is not None:
        from PySide import QtCore

        poll = QtCore.QTimer(mw)
        poll.setInterval(POLL_MS)
        poll.timeout.connect(_safe_sync)
        poll.start()
        _a["poll"] = poll
    _a["installed"] = True


def _safe_sync():
    try:
        sync()
    except Exception as exc:  # a poll must never raise
        try:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"[asm] {exc}\n")
        except Exception:
            pass


class _MateCommand:
    def GetResources(self):
        return {"Pixmap": "Assembly_CreateJointFixed", "MenuText": "Mate",
                "ToolTip": "Add a mate in the Model Builder: pick an element on two parts, adjust the mate in Settings"}

    def IsActive(self):
        try:
            import FreeCADGui as Gui

            return active_assembly() is not None and not Gui.Control.activeDialog()
        except Exception:
            return False

    def Activated(self):
        start_new_mate()


# ----- the tree --------------------------------------------------------------------------------------------------------


def _flatten(node, depth=0, out=None):
    out = [] if out is None else out
    out.append((node, depth))
    for child in node.children:
        _flatten(child, depth + 1, out)
    return out


def _tree_model(asm):
    root = model.tree(asm)
    if _a["new"] is not None:
        mates = root.find("sec/mates")
        if mates is not None:
            mates.children.append(model.Node(NEW_KEY, "New mate", "mate", None,
                                             meta="pick two elements" if len(_a["new"]["picks"]) < 2 else "", state="warn"))
    return root


def _items_by_key():
    out = {}
    tree = _a["tree"]

    def visit(item):
        out[item.data(0, KEY)] = item
        for i in range(item.childCount()):
            visit(item.child(i))

    for i in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(i))
    return out


def refresh(select=None):
    """Rebuilds the tree from the document and the Settings form when its content changed."""
    if not _a["attached"]:
        return
    asm = assembly()
    tree = _a["tree"]
    if asm is None:
        return
    from PySide import QtCore, QtWidgets

    root = _tree_model(asm)
    rows_ = _flatten(root)
    keys = [n.key for n, _d in rows_]
    items = _items_by_key()
    tree.blockSignals(True)
    try:
        if list(items) != keys:
            expanded = {k for k, it in items.items() if it.isExpanded()}
            before = set(items)
            tree.clear()
            items = {}
            stack = []
            for node, depth in rows_:
                item = QtWidgets.QTreeWidgetItem(tree if depth == 0 else stack[depth - 1])
                item.setData(0, KEY, node.key)
                for column in range(3):
                    item.setSizeHint(column, QtCore.QSize(0, 21))
                items[node.key] = item
                del stack[depth:]
                stack.append(item)
            for key, item in items.items():
                if item.childCount():
                    item.setExpanded(key in expanded or key not in before)
        for node, _d in rows_:
            _paint(items[node.key], node, asm.Document.Name)
    finally:
        tree.blockSignals(False)
    want = select or _a["key"] or root.key
    if want not in items:
        want = root.key
    if select or _a["key"] != want or _a["form"] is None:
        select_key(want, force=True)
    else:
        _rebuild_if_changed()


def _paint(item, node, doc_name):
    from PySide import QtCore, QtGui

    item.setText(0, f"{node.mark} {node.label}" if node.mark else node.label)
    item.setIcon(0, _object_icon(doc_name, node.obj) if node.kind != "section" else QtGui.QIcon())
    item.setText(1, node.meta)
    item.setForeground(1, QtGui.QBrush(QtGui.QColor("#8a93a0")))
    virtual = node.key == NEW_KEY
    item.setForeground(0, QtGui.QBrush(QtGui.QColor("#8a93a0" if virtual else "#1d232c")))
    font = item.font(0)
    font.setItalic(virtual)
    font.setBold(node.kind == "section")
    item.setFont(0, font)
    item.setIcon(2, _dot(MARK_ICONS[node.state]) if node.state in MARK_ICONS else QtGui.QIcon())
    item.setTextAlignment(2, QtCore.Qt.AlignCenter)
    item.setToolTip(2, {"ok": "up to date", "warn": "needs attention", "bad": "conflict", "stale": "out of date"}.get(node.state, ""))


def rows():
    """[(key, text, depth, meta)] of the tree widget as shown (for tests)."""
    out = []
    tree = _a["tree"]

    def visit(item, depth):
        out.append((item.data(0, KEY), item.text(0), depth, item.text(1)))
        for i in range(item.childCount()):
            visit(item.child(i), depth + 1)

    for i in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(i), 0)
    return out


def schedule():
    if _a["attached"] and _a["timer"] is not None:
        _a["timer"].start()


def _flush():
    if _a["attached"]:
        refresh()


def flush_now():
    """Runs a pending refresh immediately (tests)."""
    if _a["timer"] is not None:
        _a["timer"].stop()
    _flush()


# ----- the selected node, the 3D highlight --------------------------------------------------------------------------------


def select_key(key, from_tree=False, force=False):
    """Makes ``key`` the current node: the tree row, the Settings form, the highlight in the 3D view."""
    if not _a["attached"] or not key:
        return False
    items = _items_by_key()
    item = items.get(key)
    if item is None:
        return False
    tree = _a["tree"]
    if not from_tree and tree.currentItem() is not item:
        tree.blockSignals(True)
        tree.setCurrentItem(item)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        tree.blockSignals(False)
    if key == _a["key"] and not force and _a["form"] is not None:
        return True
    if key != NEW_KEY and _a["new"] is not None:
        _a["new"] = None  # leaving the virtual node drops it
        refresh(select=key)
        return True
    _a["key"] = key
    _a["slot"] = None
    _a["note"] = ""
    build_form()
    _rehighlight()
    _flag_armed()
    return True


def _clear_selection():
    try:
        import FreeCADGui as Gui

        _a["syncing"] = True
        Gui.Selection.clearSelection()
    except Exception:
        pass
    finally:
        _a["syncing"] = False


def _rehighlight():
    """The current node's elements, selected (highlighted) in the 3D view."""
    if not _a["attached"]:
        return
    try:
        import FreeCADGui as Gui
    except Exception:
        return
    _a["syncing"] = True
    try:
        import bruceworx_picktrace as pt

        was, pt.slot_armed = pt.slot_armed, True  # our own highlight is not a click: the pick rule must keep the elements
    except Exception:
        pt, was = None, False
    try:
        Gui.Selection.clearSelection()
        refs = []
        joint = current_joint()
        if joint is not None:
            refs = [joint.Reference1, joint.Reference2]
        elif _a["key"] == NEW_KEY and _a["new"] is not None:
            refs = _a["new"]["picks"]
        elif _a["key"] and _a["key"].startswith("comp/"):
            asm = assembly()
            obj = asm.Document.getObject(_a["key"][len("comp/"):])
            if obj is not None:
                Gui.Selection.addSelection(asm.Document.Name, asm.Name, obj.Name + ".")
        for ref in refs:
            if ref is None or ref[0] is None:
                continue
            try:
                import UtilsAssembly

                sub = UtilsAssembly.addTipNameToSub(ref)  # as the joint task panel highlights its picks
            except Exception:
                sub = list(ref[1])[0]
            Gui.Selection.addSelection(ref[0].Document.Name, ref[0].Name, sub)
    except Exception:
        pass
    finally:
        _a["syncing"] = False
        if pt is not None:
            pt.slot_armed = was


def highlighted():
    """[(object name, sub)] selected in the 3D view right now."""
    import FreeCADGui as Gui

    return [(sel.ObjectName, sub) for sel in Gui.Selection.getSelectionEx("", 0) for sub in sel.SubElementNames]


# ----- the Settings form ------------------------------------------------------------------------------------------------


def _clear_form():
    _a["form"] = None
    _a["fields"] = {}
    _a["errors"] = {}
    _a["grid"] = {}
    _a["picks"] = []
    scroll = _a["scroll"]
    if _alive(scroll):
        old = scroll.takeWidget()
        if old is not None:
            old.deleteLater()


def _sections():
    joint = current_joint()
    if joint is not None:
        return model.settings(joint)
    if _a["key"] == NEW_KEY and _a["new"] is not None:
        return model.new_mate_settings(_a["new"]["picks"])
    return []


def _signature():
    joint = current_joint()
    try:
        return (_a["key"], repr(_sections()), joint.Label if joint is not None else "", _a["slot"],
                repr(_a["new"]["key"]) if _a["new"] else "")
    except Exception:
        return None


def _rebuild_if_changed():
    sig = _signature()
    if sig != _a["sig"]:
        build_form()
        _flag_armed()


def build_form():
    """The Settings pane for the current node: buttons, title, then the folding sections of ``model.settings``."""
    from PySide import QtWidgets
    import bruceworx_sim_workspace as sw

    _clear_form()
    asm = assembly()
    if asm is None:
        return None
    key = _a["key"]
    joint = current_joint()
    form = QtWidgets.QWidget()
    form.setObjectName("BWSimForm")
    col = QtWidgets.QVBoxLayout(form)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)

    if joint is not None:
        bar = QtWidgets.QWidget()
        bar_row = QtWidgets.QHBoxLayout(bar)
        bar_row.setContentsMargins(8, 8, 8, 6)
        bar_row.setSpacing(6)
        _a["buttons"] = {}
        for text, tip, handler, primary in (
            ("Solve", "Solve the assembly again", solve, True),
            ("Flip", "Turn one part over", flip, False),
            ("Delete", "Delete this mate", delete_current, False),
        ):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(lambda _c=False, h=handler: h())
            bar_row.addWidget(b)
            _a["buttons"][text] = b
        bar_row.addStretch(1)
        col.addWidget(bar)

    head = QtWidgets.QWidget()
    head_col = QtWidgets.QVBoxLayout(head)
    head_col.setContentsMargins(10, 8, 10, 8)
    head_col.setSpacing(1)
    if joint is not None:
        title = QtWidgets.QLineEdit(joint.Label)
        title.setObjectName("BWAsmTitle")
        title.setToolTip("Rename the mate")
        title.editingFinished.connect(lambda t=title: _rename(t))
        sub_text = f"Mate  ·  {model.mate_label(model.mate_key(joint))}  ·  {asm.Label}"
    else:
        title = QtWidgets.QLabel("New mate" if key == NEW_KEY else _node_label(key))
        title.setObjectName("BWSimTitle")
        sub_text = "Mate  ·  not created yet" if key == NEW_KEY else asm.Label
    sub = QtWidgets.QLabel(sub_text)
    sub.setObjectName("BWSimSubtitle")
    head_col.addWidget(title)
    head_col.addWidget(sub)
    col.addWidget(head)
    _a["title"] = title

    sections = _sections()
    for section in sections:
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        for f in section.fields:
            _add_field(lay, f)
        box = sw.section(section.title, body)
        if not section.open:
            box.button.setChecked(False)
        col.addWidget(box)
    if not sections:
        hint = QtWidgets.QLabel("Select a mate to edit it, or use Mate on the Assembly tab to add one.")
        hint.setObjectName("BWSimHint")
        hint.setWordWrap(True)
        hint.setContentsMargins(10, 8, 10, 8)
        col.addWidget(hint)
    col.addStretch(1)
    _a["scroll"].setWidget(form)
    _a["form"] = form
    _a["sig"] = _signature()
    return form


def _node_label(key):
    asm = assembly()
    node = model.tree(asm).find(key) if asm is not None else None
    return node.label if node is not None else (key or "")


def _row(lay, text, widget, tip=""):
    from PySide import QtCore, QtWidgets

    row = QtWidgets.QHBoxLayout()
    row.setSpacing(8)
    label = QtWidgets.QLabel(text)
    label.setObjectName("BWSimFieldLabel")
    label.setFixedWidth(LABEL_WIDTH)
    label.setToolTip(tip)
    label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    row.addWidget(label)
    row.addWidget(widget, 1)
    lay.addLayout(row)
    return row


def _add_field(lay, f):
    from PySide import QtCore, QtWidgets

    name = f.name
    if f.kind == "picks":
        _add_picks(lay, f)
    elif f.kind == "mategrid":
        _add_grid(lay, f)
    elif f.kind == "float":
        w = QtWidgets.QLineEdit(f"{f.value:g}")
        w.setToolTip(f.help)
        w.setProperty("shown", w.text())
        w.editingFinished.connect(lambda n=name, wd=w: _commit_float(n, wd))
        box = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(w, 1)
        if f.unit:
            h.addWidget(QtWidgets.QLabel(f.unit))
        err = QtWidgets.QLabel("")
        err.setObjectName("BWSimError")
        err.setWordWrap(True)
        err.hide()
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(box)
        v.addWidget(err)
        _row(lay, f.label, wrap, f.help)
        _a["fields"][name] = w
        _a["errors"][name] = err
    elif f.kind == "bool":
        w = QtWidgets.QCheckBox("")
        w.setChecked(bool(f.value))
        w.setToolTip(f.help)
        w.clicked.connect(lambda checked, n=name: _write(n, bool(checked)))
        _row(lay, f.label, w, f.help)
        _a["fields"][name] = w
    elif f.kind == "action":
        w = QtWidgets.QPushButton(f.label)
        w.setToolTip(f.help)
        w.clicked.connect(lambda _c=False, n=name: _write(n, None))
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(LABEL_WIDTH + 8, 0, 0, 0)
        row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        _a["fields"][name] = w
    else:  # readonly
        w = QtWidgets.QLabel(str(f.value))
        w.setObjectName("BWSimReadonly")
        w.setWordWrap(True)
        w.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        _row(lay, f.label, w, f.help)
        _a["fields"][name] = w


def _add_picks(lay, f):
    from PySide import QtWidgets

    new = _a["key"] == NEW_KEY
    group = []
    for i, text in enumerate(f.value):
        tag = f.tags[i] if i < len(f.tags) else ""
        if new:
            w = QtWidgets.QLabel(f"{i + 1}   {text}")
            w.setObjectName("BWSimReadonly" if i < len(f.tags) else "BWSimHint")
        else:
            w = QtWidgets.QPushButton(f"{i + 1}   {text}" + (f"    [{tag}]" if tag else ""))
            w.setObjectName("BWAsmPick")
            w.setCheckable(True)
            w.setChecked(_a["slot"] == i)
            w.setToolTip("Click, then pick another face, edge or point in the 3D view to replace this one")
            w.clicked.connect(lambda checked, n=i: arm_slot(n if checked else None))
        lay.addWidget(w)
        group.append(w)
    _a["picks"] = group
    hint = QtWidgets.QLabel(_a["note"])
    hint.setObjectName("BWSimError")
    hint.setWordWrap(True)
    hint.setVisible(bool(_a["note"]))
    lay.addWidget(hint)
    _a["fields"]["_note"] = hint
    if not new and _a["slot"] is not None:
        arm_hint = QtWidgets.QLabel(f"Pick the new element {_a['slot'] + 1} in the 3D view")
        arm_hint.setObjectName("BWSimHint")
        lay.addWidget(arm_hint)


def _add_grid(lay, f):
    from PySide import QtCore, QtWidgets

    standard = [k for k in f.choices if model.mate_group(k) == "standard"]
    mechanical = [k for k in f.choices if model.mate_group(k) != "standard"]
    chosen = _a["new"].get("key") or f.value if (_a["key"] == NEW_KEY and _a["new"]) else f.value
    buttons = {}

    def grid(keys):
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(4)
        for n, k in enumerate(keys):
            b = QtWidgets.QToolButton()
            b.setObjectName("BWAsmMate")
            b.setText(SHORT.get(k, model.mate_label(k)))
            b.setIcon(_joint_icon(k))
            b.setIconSize(QtCore.QSize(22, 22))
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setChecked(k == chosen)
            b.setEnabled(k in f.enabled_choices)
            b.setToolTip(model.mate_label(k) if b.isEnabled() else f"{model.mate_label(k)}: does not fit the picks")
            b.setFixedHeight(46)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.clicked.connect(lambda _c=False, key=k: set_type(key))
            g.addWidget(b, n // 4, n % 4)
            buttons[k] = b
        return w

    lay.addWidget(grid(standard))
    if mechanical:
        fold = QtWidgets.QToolButton()
        fold.setObjectName("BWAsmFold")
        fold.setText("Mechanical")
        fold.setCheckable(True)
        fold.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        body = grid(mechanical)
        opened = chosen in mechanical
        fold.setChecked(opened)
        fold.setArrowType(QtCore.Qt.DownArrow if opened else QtCore.Qt.RightArrow)
        body.setVisible(opened)

        def toggled(on):
            fold.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
            body.setVisible(on)

        fold.toggled.connect(toggled)
        lay.addWidget(fold)
        lay.addWidget(body)
    _a["grid"] = buttons


def grid_state():
    """{key: (enabled, checked)} of the mate type grid."""
    return {k: (b.isEnabled(), b.isChecked()) for k, b in _a["grid"].items()}


def field_text(name):
    w = _a["fields"].get(name)
    if w is None or not _alive(w) or not hasattr(w, "text"):
        return None
    return w.text()


def field_names():
    return [n for n in _a["fields"] if not n.startswith("_")]


def title_text():
    w = _a["title"]
    return w.text() if w is not None and _alive(w) else ""


def pick_rows():
    return [w.text() for w in _a["picks"] if _alive(w)]


# ----- writing ----------------------------------------------------------------------------------------------------------


def _parse(text):
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", text or "")
    if not m:
        raise ValueError("a number is expected")
    return float(m.group(0).replace(",", "."))


def _show_error(name, text):
    err = _a["errors"].get(name)
    if err is not None and _alive(err):
        err.setText(text)
        err.setVisible(bool(text))


def _write(name, value):
    joint = current_joint()
    if joint is None:
        return False
    try:
        model.set_value(joint, name, value)
    except Exception as exc:
        _show_error(name, str(exc))
        return False
    schedule()
    return True


def _commit_float(name, widget):
    if not _alive(widget) or widget.text() == widget.property("shown"):
        return
    try:
        value = _parse(widget.text())
    except ValueError as exc:
        _show_error(name, str(exc))
        return
    _show_error(name, "")
    if _write(name, value):
        widget.setProperty("shown", widget.text())


def edit(name, value):
    """Sets a Settings field as typing in it does (tests and scripts)."""
    w = _a["fields"].get(name)
    if w is None:
        return False
    if hasattr(w, "setText"):
        w.setText(str(value))
        w.editingFinished.emit()
        return True
    if hasattr(w, "setChecked"):
        w.setChecked(bool(value))
        return _write(name, bool(value))
    return False


def _rename(edit_widget):
    joint = current_joint()
    if joint is None or not _alive(edit_widget) or edit_widget.text() == joint.Label or not edit_widget.text().strip():
        return
    _write("label", edit_widget.text().strip())


def set_type(key):
    """A click in the mate type grid."""
    if _a["key"] == NEW_KEY and _a["new"] is not None:
        _a["new"]["key"] = key
        build_form()
        return True
    joint = current_joint()
    if joint is None:
        return False
    try:
        model.set_mate_type(joint, key)
    except Exception as exc:
        _set_note(f"{model.mate_label(key)}: {exc}")
        return False
    schedule()
    return True


def solve():
    asm = assembly()
    if asm is None:
        return False
    try:
        solver = getattr(model, "_solve", None)
        if solver is not None:
            solver(asm)
        else:
            asm.solve()
    except Exception as exc:
        _set_note(f"Solve: {exc}")
        return False
    schedule()
    return True


def flip():
    return _write("flip", None)


def delete_current():
    joint = current_joint()
    if joint is None or assembly() is None:
        return False
    model.delete_mate(joint)
    _a["key"] = None
    refresh(select="sec/mates")
    return True


# ----- arming a pick slot, the new mate ---------------------------------------------------------------------------------


def arm_slot(index):
    """Arms pick slot ``index`` (0 or 1) of the selected mate; None disarms."""
    if current_joint() is None:
        return False
    _a["slot"] = index
    _a["note"] = ""
    build_form()
    _flag_armed()
    return True


def start_new_mate():
    """The BruceWorx_Mate command: selects the virtual "New mate" node."""
    asm = active_assembly() or assembly()
    if asm is None:
        return False
    attach(asm)
    select_tab()
    _a["new"] = {"picks": [], "key": None}
    _a["note"] = ""
    refresh(select=NEW_KEY)
    return True


def cancel_new():
    """Drops the virtual New mate node (Esc)."""
    if _a["new"] is None:
        return False
    _a["new"] = None
    _a["key"] = None
    _a["note"] = ""
    refresh(select="sec/mates")
    _flag_armed()
    return True


def new_active():
    return _a["new"] is not None and _a["key"] == NEW_KEY


def pick(doc_name, obj_name, sub):
    """A 3D pick while the New mate node is current (collects the two picks and creates the mate) or a slot is armed (replaces
    it). Returns True when the pick was used."""
    if not _a["attached"] or not sub or sub.endswith("."):
        return False
    try:
        import FreeCADGui as Gui

        if Gui.Control.activeDialog():
            return False
    except Exception:
        pass
    asm = assembly()
    if asm is None:
        return False
    if new_active():
        want = "new"
    elif _a["slot"] is not None and current_joint() is not None:
        want = "slot"
    else:
        return False
    ref = model.pick_ref(asm, doc_name, obj_name, sub)
    if ref is None:
        _drop_pick(doc_name, obj_name, sub)
        _set_note("That element is not part of the active assembly.")
        return False
    if want == "slot":
        joint = current_joint()
        index = _a["slot"]
        other = (joint.Reference2, joint.Reference1)[index]
        if other is not None and other[0] is not None and other[0].Name == ref[0].Name:
            _drop_pick(doc_name, obj_name, sub)
            _set_note("Both picks are on the same part; pick an element of another part.")
            return False
        try:
            model.replace_ref(joint, index, ref)
        except Exception as exc:
            _set_note(str(exc))
            return False
        _a["slot"] = None
        _a["note"] = ""
        build_form()
        _rehighlight()
        _flag_armed()
        schedule()
        return True
    picks = _a["new"]["picks"]
    if picks and picks[0][0].Name == ref[0].Name:
        _drop_pick(doc_name, obj_name, sub)
        _set_note("Both picks are on the same part; pick an element of another part.")
        return False
    _a["note"] = ""
    picks.append(ref)
    if len(picks) < 2:
        refresh()
        build_form()
        return True
    key = _a["new"].get("key")
    r1, r2 = picks
    _a["new"] = None
    try:
        joint = model.create_mate(asm, r1, r2, key=key)
    except Exception as exc:
        _a["new"] = {"picks": [], "key": key}
        _set_note(f"The mate could not be created: {exc}")
        build_form()
        return False
    _a["key"] = None
    refresh(select=f"mate/{joint.Name}")
    return True


def _drop_pick(doc_name, obj_name, sub):
    try:
        import FreeCADGui as Gui

        _a["syncing"] = True
        Gui.Selection.removeSelection(doc_name, obj_name, sub)
    except Exception:
        pass
    finally:
        _a["syncing"] = False


# ----- observers ----------------------------------------------------------------------------------------------------------


def _start_observers():
    import FreeCAD
    from PySide import QtCore, QtWidgets

    class DocObs:
        def _poke(self, doc):
            ident = _a["asm"]
            if ident and doc.Name == ident[0]:
                schedule()

        def slotChangedObject(self, obj, prop):
            if prop in VOLATILE or _a["syncing"]:
                return
            try:
                self._poke(obj.Document)
            except Exception:
                pass

        def slotRecomputedDocument(self, doc):
            self._poke(doc)

        def slotCreatedObject(self, obj):
            self._poke(obj.Document)

        def slotDeletedObject(self, obj):
            self._poke(obj.Document)

        def slotUndoDocument(self, doc):
            self._poke(doc)

        def slotRedoDocument(self, doc):
            self._poke(doc)

    obs = DocObs()
    FreeCAD.addDocumentObserver(obs)
    _a["doc_observer"] = obs
    try:
        import FreeCADGui as Gui

        class SelObs:
            def addSelection(self, doc_name, obj_name, sub, *_rest):
                if _a["syncing"] or not _flag_armed():
                    return
                QtCore.QTimer.singleShot(0, lambda: pick(doc_name, obj_name, sub))

            def removeSelection(self, *_args):
                pass

            def clearSelection(self, *_args):
                pass

            def setSelection(self, *_args):
                pass

        sel = SelObs()
        Gui.Selection.addObserver(sel, Gui.Selection.ResolveMode.NoResolve)  # the root object and the full path, as the joint task
        _a["observer"] = sel
    except Exception:
        _a["observer"] = None

    class Esc(QtCore.QObject):
        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape and new_active():
                QtCore.QTimer.singleShot(0, cancel_new)
            return False

    app = QtWidgets.QApplication.instance()
    esc = Esc(app)
    app.installEventFilter(esc)
    _a["filter"] = esc


def _stop_observers():
    import FreeCAD

    if _a["doc_observer"] is not None:
        try:
            FreeCAD.removeDocumentObserver(_a["doc_observer"])
        except Exception:
            pass
        _a["doc_observer"] = None
    if _a["observer"] is not None:
        try:
            import FreeCADGui as Gui

            Gui.Selection.removeObserver(_a["observer"])
        except Exception:
            pass
        _a["observer"] = None
    if _a["filter"] is not None:
        try:
            from PySide import QtWidgets

            QtWidgets.QApplication.instance().removeEventFilter(_a["filter"])
        except Exception:
            pass
        _a["filter"] = None
