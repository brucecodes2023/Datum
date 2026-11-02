# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Model Builder tree and the Settings form of the Simulation workspace (SIM-07 phase P1, plan chapter 69).

Both are built on the headless model layer (``bruceworx_sim_model``): the tree is ``model_tree(analysis)`` with the
marks of ``state(analysis)``; the form is ``settings(node)`` and every edit goes through ``set_value`` (one undo
step). They live in the two hosts of the window shell (``bruceworx_sim_workspace``): ``attach()`` fills them when the
workspace opens, ``detach()`` takes the observers away when it closes.

What follows the document: a debounced refresh (150 ms) after the active study changed, a value was set, the document
recomputed or an object changed, and when a mesh or solve finishes (``bruceworx_sim_events``). The pre-check findings
(which the airflow study pays for by tessellating the model) are cached and recomputed on the active study change, on
an edit of the study's own objects, after Compute and with ``refresh(full=True)``.

Face selection: while a node with a Selection field is the current node, a click on a face, edge or vertex in the 3D
view toggles it in the node's list (a plain click, no modifier: click adds, click on a highlighted one removes), and
the node's elements stay selected (highlighted) in the view. The panel never closes. A Direction or axis field has a
Pick button: the next click sets it.

Compute: the Mesh node meshes; the Study node runs the pre-check, then the solve through the Study panel's QProcess
path (the window stays live); the events feed Progress, Log and Messages. Plot and result nodes show their field.
"""

from __future__ import annotations

import os
import time

import bruceworx_sim as sim
import bruceworx_sim_events as events
import bruceworx_sim_model as model

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "Resources", "icons", "sim")
KEY = 0x0100  # Qt.UserRole
DEBOUNCE_MS = 150
LABEL_WIDTH = 96
MARK_ICONS = {"ok": "s-ok", "stale": "s-stale", "warn": "s-warn", "todo": "s-todo", "run": "s-run", "bad": "s-bad"}
MARK_TEXT = {"ok": "up to date", "stale": "out of date", "warn": "has a warning", "todo": "needs attention", "run": "running"}
FIELD_OF_PLOT = {"stress": "stress", "displacement": "displacement", "safety": "safety", "hoop": "hoop",
                 "temperature": "temperature"}
RANGE_KEYS = {"stress": "vonMises", "displacement": "displacement", "hoop": "hoop"}
RANGE_UNITS = {"stress": "MPa", "displacement": "mm", "hoop": "MPa"}
# nodes whose Compute is the whole solution
SOLVE_KEYS = ("study", "step", "step/main", "step/solver", "results")
ADDABLE = (("fixed", "Fixed"), ("displacement", "Displacement"), ("force", "Force"), ("pressure", "Pressure"),
           ("spin", "Spin"))
ADDABLE_THERMAL = (("temperature", "Temperature"), ("heatflux", "Heat flux"), ("convection", "Convection"),
                   ("heatsource", "Heat source"))

EXTRA_QSS = """
QLabel#BWSimFieldLabel { color: #5b6573; }
QLabel#BWSimError { color: #d23a2f; font-size: 11px; padding: 0 0 2px 0; }
QLabel#BWSimHint { color: #8a93a0; font-size: 11px; }
QLabel#BWSimTitle { font-size: 15px; font-weight: 600; color: #1d232c; }
QLabel#BWSimSubtitle { color: #5b6573; }
QLabel#BWSimReadonly { color: #1d232c; }
QLineEdit[bad="true"] { border: 1px solid #d23a2f; background: #fdf1f0; }
QScrollArea#BWSimScroll { border: none; background: #ffffff; }
QWidget#BWSimForm { background: #ffffff; }
QListWidget#BWSimRefs { border: 1px solid #c9ced6; background: #fff; }
QListWidget#BWSimRefs::item { height: 20px; }
"""

_p = {"attached": False, "tree": None, "form": None, "key": None, "analysis": None, "findings": {}, "marks": {},
      "timer": None, "pending": None, "observer": None, "doc_observer": None, "listener": None, "bridge": None,
      "pick": None, "syncing": False, "runner": None, "fields": {}, "icons": {}, "refresh_count": 0,
      "shown_plot": None, "last_error": ""}


# ----- small helpers -------------------------------------------------------------------------------------------


def _alive(widget):
    try:
        import shiboken6

        return widget is not None and shiboken6.isValid(widget)
    except Exception:
        return widget is not None


def icon(name):
    """The QIcon of a sprite symbol (Resources/icons/sim/<name>.svg), cached."""
    from PySide import QtGui

    if name not in _p["icons"]:
        path = os.path.join(ICON_DIR, name + ".svg")
        _p["icons"][name] = QtGui.QIcon(path) if os.path.isfile(path) else QtGui.QIcon()
    return _p["icons"][name]


def analysis():
    """The study the panes show (None when there is none)."""
    ident = _p["analysis"]
    if not ident:
        return None
    import FreeCAD

    try:
        doc = FreeCAD.getDocument(ident[0])
        return doc.getObject(ident[1])
    except Exception:
        return None


def current_node(key=None):
    study = analysis()
    if study is None:
        return None
    return model.find_node(study, key or _p["key"] or "study")


def current_key():
    return _p["key"]


def _say(level, text, key=None):
    events.message(level, text, node_key=key)


def format_value(value, unit):
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return f"{value} {unit}".strip() if unit and value else value
    if isinstance(value, int):
        text = str(value)
    else:
        text = f"{float(value):.6g}"
    return f"{text} {unit}" if unit else text


# ----- attaching to the shell ------------------------------------------------------------------------------------


def attach():
    """Fills the two hosts and starts following the document (idempotent; headless: nothing)."""
    if _p["attached"]:
        return True
    import bruceworx_sim_workspace as sw

    mb, st = sw.model_builder_host(), sw.settings_host()
    if mb is None or st is None:
        return False
    from PySide import QtCore, QtWidgets

    sw.clear_host(mb)
    sw.clear_host(st)
    tree = QtWidgets.QTreeWidget()
    tree.setObjectName("BWSimTree")
    tree.setHeaderHidden(True)
    tree.setColumnCount(2)
    tree.setIndentation(14)
    tree.setIconSize(QtCore.QSize(16, 16))
    tree.setUniformRowHeights(True)
    tree.setExpandsOnDoubleClick(True)
    tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    head = tree.header()
    head.setStretchLastSection(False)
    head.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
    head.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
    tree.setColumnWidth(1, 26)
    tree.currentItemChanged.connect(lambda cur, _prev: select_key(cur.data(0, KEY) if cur is not None else None, from_tree=True))
    tree.customContextMenuRequested.connect(lambda pos: _context_menu(pos))
    mb.layout().addWidget(tree, 1)
    _p["tree"] = tree

    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("BWSimScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    st.layout().addWidget(scroll, 1)
    _p["scroll"] = scroll
    st.setStyleSheet(EXTRA_QSS)

    timer = QtCore.QTimer(mb)
    timer.setSingleShot(True)
    timer.setInterval(DEBOUNCE_MS)
    timer.timeout.connect(_flush)
    _p["timer"] = timer

    class Bridge(QtCore.QObject):
        poke = QtCore.Signal(str, object)

    bridge = Bridge(mb)
    bridge.poke.connect(_on_event)  # queued when a run reports from another thread
    _p["bridge"] = bridge
    _p["listener"] = lambda kind, payload: bridge.poke.emit(kind, payload)
    events.subscribe(_p["listener"])

    pane = sw.messages_pane()
    if pane is not None:
        pane.messages.itemClicked.connect(lambda item: select_key(item.data(KEY)) if item.data(KEY) else None)
    _start_observers()
    _p["attached"] = True
    sync()
    return True


def detach():
    if not _p["attached"]:
        return
    _p["attached"] = False
    events.unsubscribe(_p["listener"])
    _stop_observers()
    _clear_highlight()
    _p["analysis"] = None
    _p["key"] = None
    _p["pick"] = None
    tree = _p["tree"]
    if _alive(tree):
        tree.blockSignals(True)
        tree.clear()
        tree.blockSignals(False)
    _clear_form()


def attached():
    return bool(_p["attached"])


def tree_widget():
    return _p["tree"]


def sync():
    """Follows the active study; called by the shell's poll. While faces of the part are being picked the active study may
    flip with the 3D selection: the current one is kept."""
    if not _p["attached"]:
        return
    import bruceworx_sim_workspace as sw

    study = sw.active_study()
    ident = (study.Document.Name, study.Name) if study is not None else None
    if ident == _p["analysis"]:
        return
    if ident is not None and _p["analysis"] and _p["analysis"][0] == ident[0] and analysis() is not None \
            and _has_refs(current_node()):
        return
    set_study(study)


def set_study(study):
    """Shows ``study`` (or the empty state)."""
    _clear_highlight()
    _p["pick"] = None
    _p["findings"] = {}
    _p["marks"] = {}
    _p["analysis"] = (study.Document.Name, study.Name) if study is not None else None
    _p["key"] = None
    refresh(full=True, select="study")


# ----- the tree --------------------------------------------------------------------------------------------------


def _mark_for(key):
    mark = _p["marks"].get(key)
    if mark == "todo" and any(level == "error" for level, _t in _p["findings"].get(key, [])):
        return "bad"
    return mark


def _flatten(node, depth=0, out=None):
    out = [] if out is None else out
    out.append((node, depth))
    for child in node.children:
        _flatten(child, depth + 1, out)
    return out


def refresh(full=False, select=None, precheck=None):
    """Rebuilds the tree from the document. ``full`` recomputes the pre-check findings; otherwise the cached ones are used
    (an airflow study never pays for the pre-check on a plain refresh)."""
    if not _p["attached"]:
        return
    _p["refresh_count"] += 1
    tree = _p["tree"]
    study = analysis()
    if study is None:
        tree.blockSignals(True)
        tree.clear()
        tree.blockSignals(False)
        _show_empty()
        return
    root = model.model_tree(study)
    do_check = full if precheck is None else precheck
    if do_check:
        try:
            _p["findings"] = model.node_messages(study, root)
        except Exception as exc:
            _p["findings"] = {"study": [("error", f"The pre-check failed: {exc}")]}
    _p["marks"] = model.state(study, findings=_p["findings"])
    rows = _flatten(root)
    keys = [n.key for n, _d in rows]
    items = _items_by_key()
    tree.blockSignals(True)
    try:
        if list(items) != keys:
            before = set(items)
            expanded = {k for k, it in items.items() if it.isExpanded()}
            tree.clear()
            items = {}
            stack = []
            for node, depth in rows:
                parent = tree if depth == 0 else stack[depth - 1]
                item = _make_item(parent, node)
                items[node.key] = item
                del stack[depth:]
                stack.append(item)
            for key, item in items.items():
                if item.childCount():
                    item.setExpanded(key in expanded or key not in before)
        for node, _d in rows:
            _paint_item(items[node.key], node)
    finally:
        tree.blockSignals(False)
    want = select or _p["key"] or "study"
    if want not in items:
        want = "study"
    if select or _p["key"] != want or _p["form"] is None:
        select_key(want, force=True)
    else:
        _refresh_header()
        reload_values()


def _items_by_key():
    tree = _p["tree"]
    out = {}

    def visit(item):
        out[item.data(0, KEY)] = item
        for i in range(item.childCount()):
            visit(item.child(i))

    for i in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(i))
    return out


def _make_item(parent, node):
    from PySide import QtWidgets

    from PySide import QtCore

    item = QtWidgets.QTreeWidgetItem(parent)
    item.setData(0, KEY, node.key)
    for column in (0, 1):
        item.setSizeHint(column, QtCore.QSize(0, 21))  # the QSS row height is not honoured by every style
    return item


def _paint_item(item, node):
    from PySide import QtCore, QtGui

    item.setText(0, node.label)
    item.setIcon(0, icon(node.icon))
    mark = _mark_for(node.key)
    item.setIcon(1, icon(MARK_ICONS[mark]) if mark else QtGui.QIcon())
    findings = _p["findings"].get(node.key, [])
    tip = [MARK_TEXT.get(_p["marks"].get(node.key), "")] if mark and mark != "ok" else []
    tip += [text for _level, text in findings]
    item.setToolTip(0, "\n".join(t for t in tip if t))
    item.setToolTip(1, "\n".join(t for t in tip if t) or MARK_TEXT.get(mark, ""))
    placeholder = node.extra.get("placeholder")
    dim = QtGui.QBrush(QtGui.QColor("#8a93a0")) if placeholder else QtGui.QBrush(QtGui.QColor("#1d232c"))
    item.setForeground(0, dim)
    item.setTextAlignment(1, QtCore.Qt.AlignCenter)


def rows():
    """[(key, label, depth, mark)] of the tree widget as shown (for tests)."""
    out = []
    tree = _p["tree"]

    def visit(item, depth):
        out.append((item.data(0, KEY), item.text(0), depth, _mark_for(item.data(0, KEY))))
        for i in range(item.childCount()):
            visit(item.child(i), depth + 1)

    for i in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(i), 0)
    return out


def marks():
    """The marks as drawn: {key: "ok"|"stale"|"warn"|"todo"|"bad"|"run"}."""
    return {k: _mark_for(k) for k in _p["marks"]}


def row_height():
    tree = _p["tree"]
    item = tree.topLevelItem(0)
    return tree.visualItemRect(item).height() if item is not None else 0


# ----- the debounced refresh --------------------------------------------------------------------------------------


def schedule(full=False, force=False):
    """Refresh in 150 ms (one refresh for any number of calls). ``full`` also recomputes the pre-check findings, except for an
    airflow study (its pre-check tessellates the model) unless ``force``."""
    if not _p["attached"]:
        return
    pend = _p["pending"] or {"full": False}
    pend["full"] = pend["full"] or full
    pend["force"] = pend.get("force", False) or force
    _p["pending"] = pend
    _p["timer"].start()


def _flush():
    pend, _p["pending"] = _p["pending"], None
    if pend is None or not _p["attached"]:
        return
    study = analysis()
    air = study is not None and model.is_airflow(study)
    refresh(full=pend["full"] and (not air or pend["force"]))


def flush_now():
    """Runs a pending refresh immediately (tests)."""
    if _p["timer"] is not None:
        _p["timer"].stop()
    _flush()


def _on_event(kind, payload):
    """Run events: a finished stage or a final message marks the nodes and refreshes."""
    if not _p["attached"]:
        return
    if kind == "stage_finished":
        name, ok = payload.get("name"), payload.get("ok", True)
        if name == "Mesh":
            _set_running("mesh", False)
        if name == "Read results" or not ok:
            _set_running("solution", False)
        schedule(full=True, force=name in ("Mesh", "Read results") or not ok)
    elif kind == "message":
        text = payload.get("text", "")
        if payload.get("level") == "error" or text.startswith("Solved in"):
            _set_running("solution", False)
            schedule(full=True, force=True)
    elif kind == "run_started":
        schedule()


def _set_running(stage, on):
    study = analysis()
    if study is not None:
        try:
            model.set_running(study, stage, on)
        except Exception:
            pass
    try:
        import bruceworx_sim_meshworker as mw

        mw.PUMP = _pump_events if on else None  # a blocking mesh keeps the window (and its Cancel button) alive
    except Exception:
        pass
    _refresh_buttons()


def _start_observers():
    import FreeCAD

    class DocObs:
        def _poke(self, doc_name, full=False):
            ident = _p["analysis"]
            if ident and doc_name == ident[0]:
                schedule(full=full)

        def slotChangedObject(self, obj, prop):
            if _p["syncing"]:
                return
            try:
                if prop in ("Visibility", "Shape", "Proxy", "ExpressionEngine"):
                    return
                self._poke(obj.Document.Name)
            except Exception:
                pass

        def slotRecomputedDocument(self, doc):
            self._poke(doc.Name)

        def slotCreatedObject(self, obj):
            self._poke(obj.Document.Name, full=True)

        def slotDeletedObject(self, obj):
            self._poke(obj.Document.Name, full=True)

    obs = DocObs()
    FreeCAD.addDocumentObserver(obs)
    _p["doc_observer"] = obs
    try:
        import FreeCADGui as Gui

        class SelObs:
            def addSelection(self, doc_name, obj_name, sub, *_rest):
                if not _p["syncing"]:
                    pick(obj_name, sub, doc_name)

            def removeSelection(self, doc_name, obj_name, sub, *_rest):
                if not _p["syncing"]:
                    pick(obj_name, sub, doc_name, removed=True)

            def clearSelection(self, *_a):
                if not _p["syncing"] and _p["attached"]:
                    from PySide import QtCore

                    QtCore.QTimer.singleShot(0, _rehighlight)  # a click on empty space clears; the node's faces come back

            def setSelection(self, *_a):
                pass

        sel = SelObs()
        Gui.Selection.addObserver(sel)
        _p["observer"] = sel
    except Exception:
        _p["observer"] = None


def _stop_observers():
    import FreeCAD

    if _p["doc_observer"] is not None:
        try:
            FreeCAD.removeDocumentObserver(_p["doc_observer"])
        except Exception:
            pass
        _p["doc_observer"] = None
    if _p["observer"] is not None:
        try:
            import FreeCADGui as Gui

            Gui.Selection.removeObserver(_p["observer"])
        except Exception:
            pass
        _p["observer"] = None


# ----- the selected node, the 3D highlight --------------------------------------------------------------------------


def select_key(key, from_tree=False, force=False):
    """Makes ``key`` the current node: the tree row, the Settings form, the highlight in the 3D view (and a plot node shows
    its field)."""
    if not _p["attached"] or not key:
        return False
    study = analysis()
    node = model.find_node(study, key) if study is not None else None
    if node is None:
        return False
    items = _items_by_key()
    item = items.get(key)
    tree = _p["tree"]
    if item is not None and not from_tree and tree.currentItem() is not item:
        tree.blockSignals(True)
        tree.setCurrentItem(item)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        tree.blockSignals(False)
    if key == _p["key"] and not force and _p["form"] is not None:
        return True
    _p["key"] = key
    _p["pick"] = None
    if key == "mesh/main" and not model.is_airflow(study) and ("MeshTimeLimit" not in study.PropertiesList or "Mesher" not in study.PropertiesList):
        sim.ensure_study_properties(study)  # an older study gets its mesher and time-limit settings when its Mesh node is opened
        node = model.find_node(study, key) or node
    build_form(node)
    if _refs_field(node) is not None or _p["fields"].get("Direction") or _p["fields"].get("RotationAxis"):
        _show_part(study)  # faces can only be clicked on the part, not on its mesh
    _rehighlight()
    if node.kind in ("plot", "result", "table", "probe") and _has_results(study):
        try:
            show_node(key, quiet=True)
        except Exception as exc:
            _say("warning", f"{node.label}: could not be shown ({exc})", key)
    return True


def _has_results(study):
    try:
        return bool(model.state(study, findings={}).get("results") not in (None, "todo"))
    except Exception:
        return False


def _refs_field(node):
    if node is None:
        return None
    try:
        for section in model.settings(node):
            for f in section.fields:
                if f.name == "References":
                    return f
    except Exception:
        return None
    return None


def _has_refs(node):
    return _refs_field(node) is not None


def _show_part(study):
    """The part is shown (and the study's mesh and result meshes put away) so its faces can be clicked."""
    try:
        members = sim.members(study)
        hide = [members["mesh"]] + [getattr(r, "Mesh", None) for r in members["results"]]
        for obj in hide:
            if obj is not None and obj.ViewObject is not None:
                obj.ViewObject.Visibility = False
        part = getattr(study, "Model", None) if "Model" in study.PropertiesList else None
        if part is not None and part.ViewObject is not None:
            part.ViewObject.Visibility = True
    except Exception:
        pass


def _clear_highlight():
    try:
        import FreeCADGui as Gui

        _p["syncing"] = True
        Gui.Selection.clearSelection()
    except Exception:
        pass
    finally:
        _p["syncing"] = False


def _rehighlight():
    """The current node's elements, selected (highlighted) in the 3D view."""
    if not _p["attached"]:
        return
    try:
        import FreeCADGui as Gui
    except Exception:
        return
    node = current_node()
    _p["syncing"] = True
    try:
        Gui.Selection.clearSelection()
        if node is not None and _refs_field(node) is not None:
            for obj_name, sub in model.get_value(node, "References"):
                if sub:
                    Gui.Selection.addSelection(node.doc, obj_name, sub)
    except Exception:
        pass
    finally:
        _p["syncing"] = False


def highlighted():
    """[(object, sub)] selected in the 3D view right now."""
    import FreeCADGui as Gui

    return [(sel.ObjectName, sub) for sel in Gui.Selection.getSelectionEx() for sub in sel.SubElementNames]


def _eligible(obj):
    return obj is not None and getattr(obj, "Shape", None) is not None and not obj.TypeId.startswith("Fem::") \
        and not obj.isDerivedFrom("Fem::FemAnalysis")


def pick(obj_name, sub, doc_name=None, removed=False):
    """A click in the 3D view while a node with a Selection field is current: toggles the element in the node's list (or sets
    the armed Direction/axis field). ``removed`` is FreeCAD's own deselect of an already selected element."""
    node = current_node()
    if node is None or not sub:
        return False
    if not sub.startswith(("Face", "Edge", "Vertex")):
        return False
    import FreeCAD

    doc = FreeCAD.getDocument(doc_name or node.doc)
    obj = doc.getObject(obj_name)
    armed = _p["pick"]
    if armed is not None:
        if not _eligible(obj):
            return False
        _p["pick"] = None
        _set(node, armed, [(obj_name, sub)], rebuild=True)
        _rehighlight()
        return True
    if _refs_field(node) is None or not _eligible(obj):
        return False
    current = list(model.get_value(node, "References"))
    item = (obj_name, sub)
    if removed:
        new = [c for c in current if c != item]
    elif item in current:
        new = [c for c in current if c != item]
    else:
        new = current + [item]
    if new == current:
        return False
    ok = _set(node, "References", new)
    _rehighlight()
    return ok


# ----- writing ---------------------------------------------------------------------------------------------------------


def _set(node, name, value, widget=None, rebuild=False):
    """set_value with the error shown under the field; True when written."""
    if node is None:
        return False
    try:
        model.set_value(node, name, value)
    except ValueError as exc:
        show_error(name, str(exc), widget)
        return False
    except Exception as exc:  # e.g. the object vanished
        show_error(name, f"{exc}", widget)
        return False
    show_error(name, "", widget)
    study = analysis()
    air = study is not None and model.is_airflow(study)
    schedule(full=not air)
    if rebuild:
        build_form(model.find_node(study, node.key) or node)
    else:
        reload_values(skip=widget)
    _refresh_header()
    return True


def edit(name, value):
    """Sets a field of the current node as the form would (tests, scripts). Returns True when written."""
    node = current_node()
    if node is None:
        return False
    widget = _p["fields"].get(name, {}).get("widget")
    if widget is not None and hasattr(widget, "setText"):
        widget.setText(str(value))
    ok = _set(node, name, value, widget=widget)
    if ok:
        _show_field(name)
    return ok


def show_error(name, text, widget=None):
    entry = _p["fields"].get(name)
    _p["last_error"] = text
    if entry is None:
        return
    label = entry.get("error")
    if _alive(label):
        label.setText(text)
        label.setVisible(bool(text))
    w = entry.get("widget")
    if _alive(w):
        w.setProperty("bad", "true" if text else "false")
        w.style().unpolish(w)
        w.style().polish(w)


def field_error(name):
    entry = _p["fields"].get(name)
    if entry is None or not _alive(entry.get("error")):
        return ""
    label = entry["error"]
    return label.text() if not label.isHidden() else ""


# ----- the Settings form ---------------------------------------------------------------------------------------------


def _clear_form():
    scroll = _p.get("scroll")
    if _alive(scroll):
        old = scroll.takeWidget()
        if old is not None:
            old.deleteLater()
    _p["form"] = None
    _p["fields"] = {}
    _p["header"] = None


def _show_empty():
    _clear_form()
    from PySide import QtWidgets

    box = QtWidgets.QLabel("No study in the document. Use New Study in the Home tab.")
    box.setObjectName("BWSimPlaceholder")
    box.setWordWrap(True)
    box.setAlignment(__import__("PySide").QtCore.Qt.AlignTop)
    _p["scroll"].setWidget(box)
    _p["form"] = box


def _kind_text(node):
    return {"study": "Study", "section": "Section", "params": "Definitions", "selections": "Definitions", "namedsel": "Named selection",
            "localsize": "Mesh size", "stepitem": "Study step", "geometry": "Geometry",
            "material": "Material", "air": "Material", "mesh": "Mesh", "step": "Study step", "solver": "Solver",
            "result": "Results", "plot": "Results", "table": "Results", "probe": "Results", "report": "Results"
            }.get(node.kind, "Physics")


def build_form(node):
    """The Settings pane for ``node``: top bar, title, then the folding sections of ``model.settings(node)``."""
    from PySide import QtCore, QtWidgets
    import bruceworx_sim_workspace as sw

    _clear_form()
    study = analysis()
    form = QtWidgets.QWidget()
    form.setObjectName("BWSimForm")
    col = QtWidgets.QVBoxLayout(form)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)

    bar = QtWidgets.QWidget()
    bar_row = QtWidgets.QHBoxLayout(bar)
    bar_row.setContentsMargins(8, 8, 8, 6)
    bar_row.setSpacing(6)
    buttons = _node_buttons(node)
    _p["buttons"] = {}
    for text, tip, handler, primary in buttons:
        b = QtWidgets.QPushButton(text)
        b.setToolTip(tip)
        if primary:
            b.setProperty("primary", True)
        b.clicked.connect(lambda _checked=False, h=handler: h())
        bar_row.addWidget(b)
        _p["buttons"][text] = b
    bar_row.addStretch(1)
    if buttons:
        col.addWidget(bar)

    head = QtWidgets.QWidget()
    head_col = QtWidgets.QVBoxLayout(head)
    head_col.setContentsMargins(10, 8, 10, 8)
    head_col.setSpacing(1)
    title = QtWidgets.QLabel(node.label)
    title.setObjectName("BWSimTitle")
    sub = QtWidgets.QLabel("")
    sub.setObjectName("BWSimSubtitle")
    head_col.addWidget(title)
    head_col.addWidget(sub)
    col.addWidget(head)
    _p["header"] = {"title": title, "sub": sub}

    sections = model.settings(node)
    for section in sections:
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        for f in section.fields:
            lay.addLayout(_field_row(node, f))
        col.addWidget(sw.section(section.title, body))
    extra = _extra_sections(node)
    for title_text, body in extra:
        col.addWidget(sw.section(title_text, body))
    if not sections and not extra:
        hint = QtWidgets.QLabel("This node has no settings of its own." if node.kind != "section"
                                else "Select a node below to edit its settings.")
        hint.setObjectName("BWSimHint")
        hint.setContentsMargins(10, 8, 10, 8)
        col.addWidget(hint)
    col.addStretch(1)
    _p["scroll"].setWidget(form)
    _p["form"] = form
    _refresh_header()
    reload_values()
    return form


def _refresh_header():
    h = _p.get("header")
    study = analysis()
    node = current_node()
    if not h or study is None or node is None or not _alive(h.get("sub")):
        return
    mark = _mark_for(node.key)
    text = f"{_kind_text(node)}  ·  {study.Label[len('Study: '):] if study.Label.startswith('Study: ') else study.Label}"
    if mark and mark != "ok":
        text += f"  ·  {MARK_TEXT.get(_p['marks'].get(node.key), mark)}"
    h["sub"].setText(text)
    h["title"].setText(node.label)


def _field_row(node, f):
    """One field: label column, editor, and the red error line under it."""
    from PySide import QtCore, QtWidgets

    outer = QtWidgets.QVBoxLayout()
    outer.setSpacing(1)
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(8)
    label = QtWidgets.QLabel(f.label)
    label.setObjectName("BWSimFieldLabel")
    label.setFixedWidth(LABEL_WIDTH)
    label.setWordWrap(True)
    label.setToolTip(f.help)
    label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    row.addWidget(label)
    entry = {"field": f, "widget": None, "error": None}
    err = QtWidgets.QLabel("")
    err.setObjectName("BWSimError")
    err.setWordWrap(True)
    err.hide()
    entry["error"] = err
    name = f.name
    if f.kind in ("float", "int", "text"):
        w = QtWidgets.QLineEdit()
        w.setToolTip(f.help)
        w.editingFinished.connect(lambda n=name, wd=w, fld=f: _commit_text(n, wd, fld))
        entry["widget"] = w
        row.addWidget(w, 1)
    elif f.kind == "enum":
        w = QtWidgets.QComboBox()
        w.setToolTip(f.help)
        for c in f.choices:
            w.addItem(str(c))
        w.activated.connect(lambda _i, n=name, wd=w: _commit_enum(n, wd))
        entry["widget"] = w
        row.addWidget(w, 1)
    elif f.kind == "bool":
        w = QtWidgets.QCheckBox("")
        w.setToolTip(f.help)
        w.clicked.connect(lambda checked, n=name, wd=w: _set(current_node(), n, bool(checked), widget=wd))
        entry["widget"] = w
        row.addWidget(w, 1)
    elif f.kind == "vector":
        box = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        edits = []
        for _i in range(3):
            e = QtWidgets.QLineEdit()
            e.editingFinished.connect(lambda n=name: _commit_vector(n))
            edits.append(e)
            h.addWidget(e)
        entry["widget"], entry["edits"] = edits[0], edits
        row.addWidget(box, 1)
    elif f.kind == "selection":
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        if name == "References":
            lst = QtWidgets.QListWidget()
            lst.setObjectName("BWSimRefs")
            lst.setFixedHeight(76)
            lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            v.addWidget(lst)
            hint = QtWidgets.QLabel("Click faces in the 3D view to add or remove them")
            hint.setObjectName("BWSimHint")
            hint.setWordWrap(True)
            v.addWidget(hint)
            bt = QtWidgets.QHBoxLayout()
            rm = QtWidgets.QPushButton("Remove selected")
            clear = QtWidgets.QPushButton("Clear")
            rm.clicked.connect(lambda _c=False: _remove_selected_refs())
            clear.clicked.connect(lambda _c=False: _set(current_node(), "References", []))
            bt.addWidget(rm)
            bt.addWidget(clear)
            bt.addStretch(1)
            v.addLayout(bt)
            entry["widget"], entry["hint"] = lst, hint
        else:
            line = QtWidgets.QLabel("")
            line.setObjectName("BWSimReadonly")
            v.addWidget(line)
            bt = QtWidgets.QHBoxLayout()
            pickb = QtWidgets.QPushButton("Pick in 3D view")
            pickb.setCheckable(True)
            pickb.toggled.connect(lambda on, n=name: _arm_pick(n, on))
            clear = QtWidgets.QPushButton("Clear")
            clear.clicked.connect(lambda _c=False, n=name: _set(current_node(), n, []))
            bt.addWidget(pickb)
            bt.addWidget(clear)
            bt.addStretch(1)
            v.addLayout(bt)
            entry["widget"], entry["pick"] = line, pickb
        row.addWidget(box, 1)
    else:  # readonly
        w = QtWidgets.QLabel("")
        w.setObjectName("BWSimReadonly")
        w.setWordWrap(True)
        w.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        entry["widget"] = w
        row.addWidget(w, 1)
    outer.addLayout(row)
    err_row = QtWidgets.QHBoxLayout()
    err_row.setContentsMargins(LABEL_WIDTH + 8, 0, 0, 0)
    err_row.addWidget(err)
    outer.addLayout(err_row)
    _p["fields"][name] = entry
    return outer


def _arm_pick(name, on):
    _p["pick"] = name if on else None


def _remove_selected_refs():
    node = current_node()
    entry = _p["fields"].get("References")
    if node is None or entry is None:
        return
    lst = entry["widget"]
    drop = {lst.row(it) for it in lst.selectedItems()}
    current = list(model.get_value(node, "References"))
    new = [c for i, c in enumerate(current) if i not in drop]
    if new != current:
        _set(node, "References", new)
        _rehighlight()


def _commit_text(name, widget, f):
    node = current_node()
    if node is None or not _alive(widget):
        return
    text = widget.text().strip()
    if text == widget.property("shown"):
        return
    if f.kind in ("float", "int") and not text:
        show_error(name, f"{f.label}: a value is expected", widget)
        return
    if _set(node, name, text, widget=widget):
        _show_field(name)


def _commit_enum(name, widget):
    node = current_node()
    if node is not None:
        _set(node, name, widget.currentText(), widget=widget)


def _commit_vector(name):
    node = current_node()
    entry = _p["fields"].get(name)
    if node is None or entry is None:
        return
    texts = [e.text().strip() for e in entry["edits"]]
    if texts == entry["edits"][0].property("shown_all"):
        return
    try:
        value = tuple(float(t.replace(",", ".")) for t in texts)
    except ValueError:
        show_error(name, f"{entry['field'].label}: three numbers (x, y, z) expected", entry["edits"][0])
        return
    _set(node, name, value, widget=entry["edits"][0])


def _show_field(name):
    """Puts the stored value of one field back into its widget (normalised text, e.g. '2 kN' -> '2000 N')."""
    node = current_node()
    entry = _p["fields"].get(name)
    if node is None or entry is None:
        return
    f, w = entry["field"], entry["widget"]
    try:
        value = model.get_value(node, f)
    except Exception as exc:
        value = None
        if f.kind == "readonly":
            value = ""
    if not _alive(w):
        return
    if f.kind in ("float", "int"):
        text = format_value(value, f.unit)
        expr, problem = "", ""
        try:
            expr, problem = model.get_expression(node, f), model.expression_error(node, f)
        except Exception:
            pass
        shown = expr or text
        w.setText(shown)
        w.setProperty("shown", shown)
        w.setToolTip(f"= {text}" if expr else f.help)
        if problem:
            show_error(name, problem, w)
    elif f.kind == "text":
        w.setText(str(value))
        w.setProperty("shown", str(value))
    elif f.kind == "enum":
        w.blockSignals(True)
        if w.findText(str(value)) < 0:
            w.addItem(str(value))
        w.setCurrentText(str(value))
        w.blockSignals(False)
    elif f.kind == "bool":
        w.blockSignals(True)
        w.setChecked(bool(value))
        w.blockSignals(False)
    elif f.kind == "vector":
        texts = [f"{v:.6g}" for v in value]
        for e, t in zip(entry["edits"], texts):
            e.setText(t)
        entry["edits"][0].setProperty("shown_all", texts)
    elif f.kind == "selection":
        study = analysis()
        doc = study.Document
        if name == "References":
            w.clear()
            for obj_name, sub in value:
                obj = doc.getObject(obj_name)
                w.addItem(f"{obj.Label if obj is not None else obj_name} · {sub}")
            w.setToolTip(f"{len(value)} selected")
            entry["hint"].setText(f"{len(value)} element{'s' if len(value) != 1 else ''} · click faces in the 3D view to add or remove them")
        else:
            if value:
                obj = doc.getObject(value[0][0])
                w.setText(f"{obj.Label if obj is not None else value[0][0]} · {value[0][1]}")
            else:
                w.setText("none")
    else:
        w.setText(format_value(value, f.unit) if value not in ("", None) else "")


def reload_values(skip=None):
    """Writes the document's values into every field of the form (the field with focus is left alone)."""
    for name, entry in list(_p["fields"].items()):
        w = entry.get("widget")
        if not _alive(w) or w is skip:
            continue
        try:
            if w.hasFocus():
                continue
        except Exception:
            pass
        _show_field(name)


def field_text(name):
    """What the form shows for a field (tests): the line edit's text, the combo's text, 'on'/'off', the list rows."""
    entry = _p["fields"].get(name)
    if entry is None:
        return None
    w = entry["widget"]
    f = entry["field"]
    if f.kind == "enum":
        return w.currentText()
    if f.kind == "bool":
        return w.isChecked()
    if f.kind == "vector":
        return [e.text() for e in entry["edits"]]
    if f.kind == "selection" and name == "References":
        return [w.item(i).text() for i in range(w.count())]
    return w.text()


def field_names():
    return list(_p["fields"])


# ----- extra sections of the plot nodes ----------------------------------------------------------------------------------


def _extra_sections(node):
    """The parameter table of the Parameters node; the colour range of a stress, displacement or hoop plot (pinned per
    study and field, as the study panel does)."""
    if node.kind == "params":
        return [("Parameters", _params_body())]
    key = node.key.split("/")[-1]
    if node.kind != "plot" or key not in RANGE_KEYS:
        return []
    from PySide import QtWidgets

    body = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(10, 6, 10, 8)
    lay.setSpacing(4)
    unit = RANGE_UNITS[key]
    fixed = QtWidgets.QCheckBox("Fixed range")
    lo, hi = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
    for label_text, w in (("Lowest", lo), ("Highest", hi)):
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label_text)
        lab.setObjectName("BWSimFieldLabel")
        lab.setFixedWidth(LABEL_WIDTH)
        row.addWidget(lab)
        row.addWidget(w, 1)
        lay.addLayout(row)
    lay.insertWidget(0, fixed)
    study = analysis()
    import bruceworx_sim_post as post

    rk = RANGE_KEYS[key]
    pinned = post.fixed_range(study, rk)
    shown = None
    try:
        import bruceworx_sim_gui as sg

        shown = sg._state["range"].get(study.Name)
    except Exception:
        pass
    lo_v, hi_v = pinned or ((shown[1], shown[2]) if shown and shown[0] == rk else (0.0, 0.0))
    if not pinned and hi_v == lo_v:
        try:
            result = sim.latest_result(study)
            lo_v, hi_v = post.field_range(result, rk, study) if result is not None else (0.0, 0.0)
        except Exception:
            pass
    fixed.setChecked(pinned is not None)
    lo.setText(format_value(lo_v, unit))
    hi.setText(format_value(hi_v, unit))
    err = QtWidgets.QLabel("")
    err.setObjectName("BWSimError")
    err.hide()
    lay.addWidget(err)
    _p["range"] = {"fixed": fixed, "lo": lo, "hi": hi, "error": err, "key": rk, "unit": unit, "plot": key}

    def number(text):
        t = text.strip()
        if t.endswith(unit):
            t = t[: -len(unit)]
        return float(t.replace(",", "."))

    def apply(*_a):
        try:
            if fixed.isChecked():
                post.set_fixed_range(study, rk, number(lo.text()), number(hi.text()))
            else:
                post.set_fixed_range(study, rk, None)
        except (ValueError, post.PostError) as exc:
            err.setText(f"Range: {exc}" if not isinstance(exc, ValueError) else f"Range: a number in {unit} is expected")
            err.show()
            return False
        err.hide()
        schedule()
        show_node(node.key, quiet=True)
        return True

    fixed.clicked.connect(apply)
    lo.editingFinished.connect(apply)
    hi.editingFinished.connect(apply)
    _p["range"]["apply"] = apply
    return [("Colour range", body)]


PARAM_COLUMNS = ("Name", "Expression", "Unit", "Value", "Description")


def _params_body():
    """The study's parameter table (name, expression, unit, evaluated value, description) with Add and Remove."""
    from PySide import QtCore, QtWidgets
    import bruceworx_sim_defs as defs

    study = analysis()
    body = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(10, 6, 10, 8)
    lay.setSpacing(4)
    table = QtWidgets.QTableWidget(0, len(PARAM_COLUMNS))
    table.setObjectName("BWSimParams")
    table.setHorizontalHeaderLabels(PARAM_COLUMNS)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumHeight(170)
    lay.addWidget(table)
    err = QtWidgets.QLabel("")
    err.setObjectName("BWSimError")
    err.setWordWrap(True)
    err.hide()
    lay.addWidget(err)
    hint = QtWidgets.QLabel("An expression may use other parameters and units: 2*t + 1 mm, F_load/4. "
                            "Any number field in the Settings takes a parameter name or an expression too.")
    hint.setObjectName("BWSimHint")
    hint.setWordWrap(True)
    lay.addWidget(hint)
    row = QtWidgets.QHBoxLayout()
    add = QtWidgets.QPushButton("Add parameter")
    rm = QtWidgets.QPushButton("Remove")
    row.addWidget(add)
    row.addWidget(rm)
    row.addStretch(1)
    lay.addLayout(row)
    _p["params"] = {"table": table, "error": err, "loading": False}

    def load():
        _p["params"]["loading"] = True
        table.setRowCount(0)
        for prm in defs.parameters(study):
            r = table.rowCount()
            table.insertRow(r)
            for c, text in enumerate((prm.name, prm.expression, prm.unit, prm.text, prm.description)):
                item = QtWidgets.QTableWidgetItem(text)
                if c in (0, 3):
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                table.setItem(r, c, item)
        _p["params"]["loading"] = False

    def changed(item):
        if _p["params"]["loading"]:
            return
        param_edit(item.row(), item.column(), item.text())

    def add_clicked(_c=False):
        text, ok = QtWidgets.QInputDialog.getText(table, "New parameter", "Name")
        if ok and text.strip():
            param_add(text.strip(), "0", "")

    def remove_clicked(_c=False):
        r = table.currentRow()
        if r >= 0:
            param_remove(table.item(r, 0).text())

    _p["params"]["load"] = load
    table.itemChanged.connect(changed)
    add.clicked.connect(add_clicked)
    rm.clicked.connect(remove_clicked)
    load()
    return body


def _param_error(text):
    box = _p.get("params")
    _p["last_error"] = text
    if box and _alive(box.get("error")):
        box["error"].setText(text)
        box["error"].setVisible(bool(text))


def _param_done():
    box = _p.get("params")
    if box and _alive(box.get("table")):
        box["load"]()
    schedule(full=False)
    reload_values()


def param_add(name, expression, unit="", description=""):
    """Adds a parameter as the table's Add does; the error (unit, name, cycle) is shown under the table. True when added."""
    import bruceworx_sim_defs as defs

    study = analysis()
    if study is None:
        return False
    try:
        defs.add_parameter(study, name, expression, unit, description)
    except defs.DefsError as exc:
        _param_error(str(exc))
        return False
    _param_error("")
    _param_done()
    return True


def param_edit(row, column, text):
    """Edits a cell of the parameter table (column 1 expression, 2 unit, 4 description) as typing in it does."""
    import bruceworx_sim_defs as defs

    study = analysis()
    box = _p.get("params")
    if study is None or not box:
        return False
    names = defs.parameter_names(study)
    if row >= len(names):
        return False
    kwargs = {1: "expression", 2: "unit", 4: "description"}.get(column)
    if kwargs is None:
        return False
    try:
        defs.edit_parameter(study, names[row], **{kwargs: text.strip() if kwargs != "description" else text})
    except defs.DefsError as exc:
        _param_error(str(exc))
        _param_done()  # the table shows the stored values again
        _param_error(str(exc))
        return False
    _param_error("")
    _param_done()
    return True


def param_remove(name):
    import bruceworx_sim_defs as defs

    study = analysis()
    if study is None:
        return False
    try:
        defs.remove_parameter(study, name)
    except defs.DefsError as exc:
        _param_error(str(exc))
        return False
    _param_error("")
    _param_done()
    return True


def param_rows():
    """The parameter table as shown: [[name, expression, unit, value, description]] (tests)."""
    box = _p.get("params")
    if not box or not _alive(box.get("table")):
        return []
    t = box["table"]
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def param_error():
    box = _p.get("params")
    return box["error"].text() if box and _alive(box.get("error")) else ""


def add_named_selection(refs=None, label=None):
    """A new named selection, made from ``refs`` or from what is selected in the 3D view now; selects its node."""
    import bruceworx_sim_defs as defs

    study = analysis()
    if study is None:
        return None
    if refs is None:
        try:
            refs = [(o, [s for s in subs]) for o, subs in _view_selection(study)]
        except Exception:
            refs = []
    n = len(defs.selections(study)) + 1
    try:
        obj = defs.make_selection(study, label or f"Selection {n}", refs)
    except defs.DefsError as exc:
        _say("error", f"Named selection: {exc}")
        return None
    refresh(full=True, select=f"defs/sel/{obj.Name}")
    return obj


def _view_selection(study):
    """[(object, [subs])] of the faces, edges and bodies selected in the 3D view (parts of the study's model only)."""
    import FreeCADGui as Gui

    out = []
    for sel in Gui.Selection.getSelectionEx(study.Document.Name):
        subs = [s for s in sel.SubElementNames if s.startswith(("Face", "Edge", "Vertex", "Solid"))]
        if subs and _eligible(sel.Object):
            out.append((sel.Object, subs))
    return out


def add_local_size(size="2 mm", refs=None, label=None):
    """A new local mesh size node (on ``refs`` or on the faces selected in the 3D view); selects it."""
    import bruceworx_sim_defs as defs

    study = analysis()
    if study is None or model.is_airflow(study):
        return None
    if refs is None:
        try:
            refs = _view_selection(study)
        except Exception:
            refs = []
    try:
        obj = defs.make_local_size(study, size, refs, label=label or "Local size")
    except defs.DefsError as exc:
        _say("error", f"Local size: {exc}")
        return None
    refresh(full=True, select=f"mesh/local/{obj.Name}")
    return obj


def set_range(lo, hi, fixed=True):
    """Pins (or frees) the colour range of the current plot node as the form does (tests)."""
    r = _p.get("range")
    if not r:
        return False
    r["fixed"].setChecked(bool(fixed))
    r["lo"].setText(str(lo))
    r["hi"].setText(str(hi))
    return r["apply"]()


# ----- showing results -------------------------------------------------------------------------------------------------


def show_node(key, quiet=False):
    """Shows a result node's field in the 3D view: a plot (stress, displacement, safety, hoop, temperature, modes), a result
    object, or the airflow results. Returns True when something was drawn."""
    import bruceworx_sim_gui as sg
    import bruceworx_sim_thermal as thermal

    study = analysis()
    node = model.find_node(study, key) if study is not None else None
    if node is None:
        return False
    plot = key.split("/")[-1]
    if model.is_airflow(study):
        return _airflow_show(study, plot)
    if node.kind == "result":
        result = study.Document.getObject(node.obj)
        kind = "safety" if getattr(result, "SafetyFactor", None) else "stress"
        ok = sg.show_field(study, kind, result)
    elif plot == "modes":
        import bruceworx_sim_eigen as eigen

        results = eigen.mode_results(study)
        if not results:
            return False
        sg.show_mode(study, results[0])
        ok = True
    elif plot in FIELD_OF_PLOT:
        kind = FIELD_OF_PLOT[plot]
        if plot == "temperature" and thermal.is_thermal(study):
            kind = "tempbands" if thermal.bands(study)[0] else "temperature"
        if sim.latest_result(study) is None:
            if not quiet:
                _say("warning", "No results yet: compute the study first", key)
            return False
        ok = sg.show_field(study, kind)
    elif plot == "probe":
        if sim.latest_result(study) is None:
            return False
        if not sg.probing():
            sg.start_probe(study)
        return True
    elif plot == "report":
        if sim.latest_result(study) is None:
            return False
        sg.report_study(study, done=lambda files: _say("ok", "Report: " + (files.get("pdf") or files.get("tex") or "written"), key))
        return True
    else:
        return False
    if ok:
        _p["shown_plot"] = key
    return bool(ok)


def shown_plot():
    return _p["shown_plot"]


def _airflow_show(study, plot):
    import bruceworx_sim_gui as sg

    panel = sg.open_study_panel(study)
    if panel is not None and hasattr(panel, "load_results"):
        panel.load_results()
        return True
    return False


# ----- the buttons, Compute -------------------------------------------------------------------------------------------


def _node_buttons(node):
    """[(text, tip, handler, primary)] of the Settings top bar."""
    key = node.key
    text = "Cancel" if busy() else "Compute"
    if key == "mesh/main":
        return [(text, "Mesh the study (Build Mesh); Cancel stops the mesher", lambda: compute_or_cancel("mesh/main"), True)]
    if key in SOLVE_KEYS or node.kind in ("step", "solver", "stepitem"):
        return [(text, "Pre-check, then mesh if needed, solve and read the results; Cancel stops the mesher or CalculiX",
                 lambda: compute_or_cancel("step/main"), True),
                ("Pre-check", "List what the study still needs before it can solve", precheck, False)]
    if node.kind in ("plot", "result", "table", "probe", "report"):
        return [("Plot", "Show this result in the 3D view", lambda k=key: show_node(k), True)]
    return []


def busy():
    """True while this study meshes or solves."""
    study = analysis()
    if study is None:
        return False
    try:
        return bool(model._running(study))
    except Exception:
        return False


def _refresh_buttons():
    """The Compute button of the Settings top bar reads Cancel while a mesh or solve runs."""
    text = "Cancel" if busy() else "Compute"
    old = "Compute" if text == "Cancel" else "Cancel"
    b = (_p.get("buttons") or {}).pop(old, None)
    if b is not None and _alive(b):
        b.setText(text)
        _p["buttons"][text] = b


def compute_or_cancel(key):
    if busy():
        return cancel_compute()
    return compute(key)


def cancel_compute():
    """Stops the running mesh (the worker process is killed) or solve (the CalculiX process is killed). True when something
    was stopped."""
    stopped = False
    study = analysis()
    running = set(model._running(study)) if study is not None else set()
    if "mesh" in running or "solution" in running:
        import bruceworx_sim_meshworker as mw

        mw.cancel()
        stopped = True
    r = _p.get("runner")
    if r is not None and not r.released and r.process is not None:
        try:
            from PySide import QtCore

            if _alive(r.process) and r.process.state() != QtCore.QProcess.NotRunning:
                r.process.kill()
                stopped = True
        except Exception:
            pass
    if stopped:
        events.message("warning", "Cancelled")
    return stopped


def _pump_events():
    try:
        from PySide import QtWidgets

        QtWidgets.QApplication.processEvents()
    except Exception:
        pass


def precheck():
    """Runs the pre-check: findings to the Messages pane and the marks of the tree."""
    study = analysis()
    if study is None:
        return []
    issues = sim.precheck(study)
    events.run_started(getattr(study, "Label", ""))
    events.from_precheck(issues)
    if not issues:
        events.message("ok", "Pre-check: ready to solve")
    refresh(full=True)
    return issues


def runner(study):
    """The Study panel used as the run engine (never shown as a task panel): one per study."""
    r = _p.get("runner")
    if r is not None and not r.released and r.analysis is study:
        return r
    import bruceworx_sim_gui as sg

    r = sg.classes()["StudyPanel"](study)
    _p["runner"] = r
    return r


def compute(key="step/main", blocking=False):
    """Compute of the node: the Mesh node meshes; the Study (and its step, solver and results) pre-checks, meshes if needed,
    solves and reads the results (a QProcess unless ``blocking``). Returns True/False, or None when it cannot start."""
    study = analysis()
    if study is None:
        return None
    air = model.is_airflow(study)
    if key == "mesh/main":
        if air:
            _say("info", "OpenFOAM meshes inside the run: Compute the study", key)
            return None
        return _compute_mesh(study)
    if air:
        import bruceworx_sim_gui as sg

        panel = sg.open_study_panel(study)
        if panel is None or not hasattr(panel, "run"):
            return None
        panel.run()
        schedule(full=True)
        return True
    r = runner(study)
    _set_running("solution", True)
    schedule()
    try:
        ok = r.solve(blocking=blocking)
    except Exception as exc:
        _set_running("solution", False)
        events.message("error", f"Compute failed: {exc}", node_key="step/main")
        schedule(full=True)
        return False
    if blocking or not ok:
        _set_running("solution", False)
        refresh(full=True)
    return ok


def _compute_mesh(study):
    import bruceworx_sim_gui as sg

    events.run_started(getattr(study, "Label", ""))
    _set_running("mesh", True)
    refresh()
    try:
        import FreeCADGui as Gui

        Gui.updateGui()
    except Exception:
        pass
    try:
        stats = sim.mesh_study(study)
    except sim.StudyError as exc:
        events.message("error", f"Mesh: {exc}", node_key="mesh/main")
        stats = None
    finally:
        _set_running("mesh", False)
    try:
        if stats is not None:
            sg.show_study_mesh(study)
    except Exception:
        pass
    refresh(full=True)
    return stats is not None


def running():
    r = _p.get("runner")
    if r is None or r.released or r.process is None:
        return False
    try:
        from PySide import QtCore

        return _alive(r.process) and r.process.state() != QtCore.QProcess.NotRunning
    except Exception:
        return False


# ----- adding, deleting, renaming nodes ------------------------------------------------------------------------------------


def addable(study=None):
    study = study or analysis()
    if study is None or model.is_airflow(study):
        return []
    kind = sim.study_kind(study)
    out = list(ADDABLE_THERMAL) if kind == "thermal" else []
    if kind in ("static", "thermal_stress", "modal", "buckling"):
        out = [a for a in ADDABLE if a[0] != "spin" or kind in ("static", "thermal_stress")]
    if kind == "thermal_stress":
        out += list(ADDABLE_THERMAL)
    return out


def add_node(kind):
    """Adds a support or load of ``kind`` (no faces yet) to the study and selects its node. Returns the object."""
    study = analysis()
    if study is None:
        return None
    import ObjectsFem
    import bruceworx_sim_gui as sg
    import bruceworx_sim_thermal as thermal

    doc = study.Document
    doc.openTransaction(f"Add {kind}")
    try:
        if kind == "spin":
            doc.abortTransaction()
            obj = sg.add_spin_object(study)
            if obj is not None:
                select_key(f"phys/{obj.Name}") if _wait_for(f"phys/{obj.Name}") else None
            return obj
        if kind == "heatsource":
            doc.abortTransaction()
            made = sg.add_thermal_object("source", study, values={"watts": 1.0})
            obj = made[0] if made else None
        elif kind in ("temperature", "heatflux", "convection"):
            if kind == "temperature":
                obj = thermal.add_temperature(study, [], 20.0)
            elif kind == "heatflux":
                obj = thermal.add_heat_flux(study, [], 1000.0)
            else:
                obj = thermal.add_convection(study, [], 10.0)
        else:
            maker = {"fixed": ("makeConstraintFixed", "Fixed", "Fixed"), "displacement": ("makeConstraintDisplacement", "Displacement", "Displacement"),
                     "force": ("makeConstraintForce", "Force", "Force"), "pressure": ("makeConstraintPressure", "Pressure", "Pressure")}[kind]
            obj = getattr(ObjectsFem, maker[0])(doc, maker[1])
            if kind == "force":
                obj.Force = "100 N"
            elif kind == "pressure":
                obj.Pressure = "1 MPa"
            obj.Label = maker[2]
            study.addObject(obj)
            doc.recompute()
        if kind not in ("heatsource",):
            doc.commitTransaction()
    except Exception as exc:
        try:
            doc.abortTransaction()
        except Exception:
            pass
        _say("error", f"Add {kind}: {exc}")
        return None
    if obj is not None:
        refresh(full=True, select=f"phys/{obj.Name}")
    return obj


def _wait_for(key):
    refresh(full=True)
    return key in _items_by_key()


def delete_node(key):
    """Deletes the node's object (a support, a load or a result) in one undo step. Returns True when deleted."""
    study = analysis()
    node = model.find_node(study, key) if study is not None else None
    if node is None or not node.obj or node.kind in ("study", "geometry", "mesh", "solver", "section"):
        return False
    doc = study.Document
    if node.kind == "namedsel":
        import bruceworx_sim_defs as defs

        try:
            defs.remove_selection(study, doc.getObject(node.obj))
        except defs.DefsError as exc:
            _say("error", f"Delete {node.label}: {exc}")
            return False
        _p["key"] = None
        refresh(full=True, select="defs/sels")
        return True
    doc.openTransaction(f"Delete {node.label}")
    try:
        doc.removeObject(node.obj)
    except Exception as exc:
        doc.abortTransaction()
        _say("error", f"Delete {node.label}: {exc}")
        return False
    doc.commitTransaction()
    _p["key"] = None
    refresh(full=True, select="phys" if key.startswith("phys/") else "mesh" if key.startswith("mesh/") else "study")
    return True


def rename_node(key, text):
    study = analysis()
    node = model.find_node(study, key) if study is not None else None
    if node is None or not node.obj or not text.strip():
        return False
    doc = study.Document
    obj = doc.getObject(node.obj)
    doc.openTransaction("Rename")
    obj.Label = ("Study: " + text.strip()) if node.kind == "study" and obj.Label.startswith("Study: ") else text.strip()
    doc.commitTransaction()
    refresh(full=False)
    return True


def _context_menu(pos):
    from PySide import QtWidgets

    tree = _p["tree"]
    item = tree.itemAt(pos)
    if item is None:
        return
    key = item.data(0, KEY)
    study = analysis()
    node = model.find_node(study, key) if study is not None else None
    if node is None:
        return
    menu = QtWidgets.QMenu(tree)
    if key == "phys" or key.startswith("phys/"):
        sub = menu.addMenu("Add")
        for kind, text in addable(study):
            sub.addAction(text, lambda k=kind: add_node(k))
    if key == "defs/sels" or node.kind == "namedsel":
        menu.addAction("New selection from the 3D view", add_named_selection)
    if key == "defs/params":
        menu.addAction("Add parameter…", lambda: _param_dialog())
    if (key == "mesh" or key.startswith("mesh/")) and not model.is_airflow(study):
        menu.addAction("Add local size", add_local_size)
    if key == "step" or key.startswith("step/"):
        import bruceworx_sim_steps as steps

        sub = menu.addMenu("Add step")
        for stype, text in steps.additions(study):
            sub.addAction(text, lambda t=stype: add_step(t))
        if node.kind == "stepitem":
            menu.addAction("Remove step", lambda k=key: remove_step(k))
    if key == "mesh/main" or key == "mesh":
        menu.addAction("Compute", lambda: compute("mesh/main"))
    if key in SOLVE_KEYS or node.kind in ("step", "solver"):
        menu.addAction("Compute", lambda: compute("step/main"))
        menu.addAction("Pre-check", precheck)
    if node.kind in ("plot", "result"):
        menu.addAction("Plot", lambda: show_node(key))
    if node.obj and node.kind not in ("study", "geometry", "mesh", "solver", "section"):
        menu.addSeparator()
        menu.addAction("Rename…", lambda: _rename_dialog(key, node.label))
        menu.addAction("Delete", lambda: delete_node(key))
    if not menu.isEmpty():
        menu.exec(tree.viewport().mapToGlobal(pos))


def _param_dialog():
    from PySide import QtWidgets

    text, ok = QtWidgets.QInputDialog.getText(_p["tree"], "New parameter", "Name")
    if ok and text.strip():
        select_key("defs/params")
        param_add(text.strip(), "0", "")


def add_step(stype):
    """Adds a study step of ``stype`` (stationary, modal, buckling, heat) after the existing ones; True when accepted."""
    import bruceworx_sim_steps as steps

    study = analysis()
    if study is None:
        return False
    try:
        steps.add_step(study, stype)
    except steps.StepError as exc:
        _say("error", f"Add step: {exc}", "step")
        return False
    refresh(full=True, select="step/main")
    return True


def remove_step(key):
    import bruceworx_sim_steps as steps

    study = analysis()
    if study is None:
        return False
    try:
        steps.remove_step(study, key.split("/")[-1])
    except steps.StepError as exc:
        _say("error", f"Remove step: {exc}", "step")
        return False
    refresh(full=True, select="step/main")
    return True


def _rename_dialog(key, label):
    from PySide import QtWidgets

    text, ok = QtWidgets.QInputDialog.getText(_p["tree"], "Rename", "Name", text=label)
    if ok:
        rename_node(key, text)
