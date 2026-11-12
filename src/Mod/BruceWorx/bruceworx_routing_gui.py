# SPDX-License-Identifier: LGPL-2.1-or-later
"""Routing commands and task panels: the Routing tab of the Design workspace (BW-ROU-01..03).

Commands (registered by :func:`install`, listed in workspaces.json):

* ``BruceWorx_RoutePath``: the Route Path panel. Pick vertices, circular edges (their centre),
  faces (their centre) or objects (LCS, datums: their origin) in the 3D view; each pick is a point
  of the path, attached to what was picked so it follows the part, and the selection is cleared
  after it (as Fusion's point tools do), so the next click starts fresh. The list can be reordered and
  trimmed; mode, bend radius and minimum bend radius are live, the length and the bend check are
  shown in the panel. With a RoutePath selected the same panel edits it.
* ``BruceWorx_RouteAddPoint``: the selected geometry appended to the open panel or to the
  selected path. ``BruceWorx_RouteClip``: the selected path's nearest point attached to the
  selected geometry (a clip holds the route there).
* ``BruceWorx_RouteWire``, ``_RoutePipe``, ``_RouteHose``: a short form for the item on the
  selected path (or the only path). ``BruceWorx_RouteBundle``: the selected wires into a bundle.
* ``BruceWorx_RouteFromTo``, ``BruceWorx_RouteCutList``: the CSV reports, saved where asked.
  ``BruceWorx_RouteTerminals`` (ROU-05): the terminals, seals and connector accessories to order,
  manufacturer and MPN in columns of their own.
* ``BruceWorx_RouteBendRules``: the Bend Rules dialog (ROU-02, 30 Oct), the default minimum bend
  rule per wire, hose and tube and the tube CLR, kept in the preference group
  ``Mod/BruceWorx/Routing``. Each item panel can override the rule for its item. ROU-05 adds the
  straight length at pins and ports (wire, hose and tube multiples of OD, the bender clamp).
* ``BruceWorx_RouteFrame`` (ROU-05): the Pin / Port panel. Pick a face, a hole edge or a vertex of
  a part to put a pin or port frame there (+Z out of the face), or select an LCS to edit it: name,
  gauge range, terminal, seal, signal and straight length of a pin; type, size, tube OD, straight
  and engagement of a port; the connector's family and mating P/N.
* ``BruceWorx_RouteFitting`` (ROU-05): the Fitting panel. Family, style, size and thread from the
  size tables, placed on the selection (a face, a hole edge or another port: the fitting's thread
  or first port faces it), live; the P/N can be typed.

* ``BruceWorx_RouteHarness`` / ``BruceWorx_RouteMakeVaultItem`` (ROU-06): group the selected wires, tubes,
  hoses and connector links in a Harness container; save a Harness out as its own vault item (bruceworx_harness).
  Each wire, tube and hose panel has a *Stock material* row: pick the vault item it is cut from, or make one
  from the item's properties (manufacturer, MPN, scrap, purchase unit; bruceworx_stock).

Every Qt signal to a bound method goes through ``slot`` (bruceworx_slots). The selection observer
is not a Qt signal: FreeCAD calls its methods directly.
"""

import math
import os

import FreeCAD as App

import bruceworx_routing as br
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
RED = "<span style='color:#c0392b'>{}</span>"
COMMANDS = ("BruceWorx_RoutePath", "BruceWorx_RouteAddPoint", "BruceWorx_RouteClip", "BruceWorx_RouteWire",
            "BruceWorx_RoutePipe", "BruceWorx_RouteHose", "BruceWorx_RouteBundle", "BruceWorx_RouteFromTo",
            "BruceWorx_RouteCutList", "BruceWorx_RouteBendRules", "BruceWorx_RouteFrame", "BruceWorx_RouteFitting",
            "BruceWorx_RouteTerminals", "BruceWorx_RouteHarness", "BruceWorx_RouteMakeVaultItem")
_state = {"installed": False, "panel": None}


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui

        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def _gui():
    import FreeCADGui as Gui

    return Gui


def _html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _message(text, title="Routing"):
    _QtCore, _QtGui, QtWidgets = _qt()
    QtWidgets.QMessageBox.information(_gui().getMainWindow(), title, text)


def _console(text):
    App.Console.PrintMessage(f"BruceWorx routing: {text}\n")


# ---------------------------------------------------------------------------------------------
# Selection.


def picks_from_selection():
    """``[(point, obj, sub)]`` for the selected sub-elements and objects, RoutePaths left out."""
    out = []
    for sel in _gui().Selection.getSelectionEx("", 0):
        obj = sel.Object
        if br.is_path(obj) or br.is_routed(obj):
            continue
        for sub in sel.SubElementNames or [""]:
            point = br.attachment_point(obj, sub)
            if point is not None:
                out.append((point, obj, sub))
    return out


def refuse_copy(obj):
    """A flexible-sub-assembly copy is edited in its sub-assembly: say so in the status bar, True when refused."""
    import bruceworx_flexsub as fs

    if not fs.is_copy(obj):
        return False
    _gui().getMainWindow().statusBar().showMessage(fs.EDIT_HINT, 5000)
    return True


def selected_paths():
    return [o for o in _gui().Selection.getSelection() if br.is_path(o)]


def target_path(doc=None):
    """The path an item goes on: the selected one, else the document's only one."""
    found = selected_paths()
    if found:
        return found[0]
    doc = doc or App.ActiveDocument
    found = br.paths(doc) if doc is not None else []
    return found[0] if len(found) == 1 else None


def _clear_selection():
    try:
        _gui().Selection.clearSelection()
    except Exception:
        pass


def clear_selection_later():
    """Clear the selection once the current selection notification is done (clearing it from
    inside an observer call would re-enter the selection)."""
    QtCore, _QtGui, _QtWidgets = _qt()
    QtCore.QTimer.singleShot(0, _clear_selection)


def _pick_label(obj, sub):
    if obj is None:
        return ""
    return f"{obj.Label}.{sub}" if sub else obj.Label


def _fmt(point):
    return f"({point.x:.1f}, {point.y:.1f}, {point.z:.1f})"


# ---------------------------------------------------------------------------------------------
# Route Path panel.


class _PickObserver:
    """FreeCAD selection observer feeding the open Route Path panel (not a Qt signal)."""

    def __init__(self, panel):
        self.panel = panel

    def addSelection(self, doc, obj, sub, pnt):
        try:
            self.panel.picked(doc, obj, sub, pnt)
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx routing: pick ignored: {exc}\n")


class RoutePathPanel:
    """Points picked in the 3D view, reordered in a list; mode and radii live; OK keeps the path."""

    def __init__(self, path=None, picks=None):
        QtCore, _QtGui, QtWidgets = _qt()
        self.Gui = _gui()
        self.doc = App.ActiveDocument
        self.editing = path is not None
        self.doc.openTransaction("Route Path")
        if path is None:
            path = br.make_path([], self.doc)
        self.path = path
        self.points = [(App.Vector(p), None, "") for p in path.Points]
        for index, obj, sub in br.attachments_of(path):
            if 0 <= index < len(self.points):
                self.points[index] = (self.points[index][0], obj, sub)
        for point, obj, sub in picks or []:
            self.points.append((point, obj, sub))
        self._building = True
        self.form = self._build(QtWidgets)
        self._load()
        self._building = False
        self.observer = _PickObserver(self)
        self.Gui.Selection.addObserver(self.observer)
        _state["panel"] = self
        self._apply()
        if picks:
            clear_selection_later()  # they are points now

    def _build(self, QtWidgets):
        form = QtWidgets.QWidget()
        form.setWindowTitle("Route Path")
        layout = QtWidgets.QVBoxLayout(form)
        hint = QtWidgets.QLabel("Click vertices, circle edges, faces or datums in the 3D view to add points.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.pick = QtWidgets.QCheckBox("Pick points from the 3D view")
        self.pick.setChecked(True)
        layout.addWidget(self.pick)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("routePoints")
        self.list.setMinimumHeight(120)
        layout.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        self.add = QtWidgets.QPushButton("Add selection")
        self.remove = QtWidgets.QPushButton("Remove")
        self.up = QtWidgets.QPushButton("Up")
        self.down = QtWidgets.QPushButton("Down")
        for button in (self.add, self.remove, self.up, self.down):
            row.addWidget(button)
        layout.addLayout(row)
        grid = QtWidgets.QGridLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(br.MODES)
        self.radius = QtWidgets.QDoubleSpinBox()
        self.radius.setRange(0.0, 1e6)
        self.radius.setDecimals(2)
        self.radius.setSuffix(" mm")
        self.minimum = QtWidgets.QDoubleSpinBox()
        self.minimum.setRange(0.0, 1e6)
        self.minimum.setDecimals(2)
        self.minimum.setSuffix(" mm")
        self.minimum.setSpecialValueText("no check")
        grid.addWidget(QtWidgets.QLabel("Mode"), 0, 0)
        grid.addWidget(self.mode, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Bend radius"), 1, 0)
        grid.addWidget(self.radius, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Min bend radius"), 2, 0)
        grid.addWidget(self.minimum, 2, 1)
        layout.addLayout(grid)
        self.straight = QtWidgets.QCheckBox("Leave pins and ports straight")
        self.straight.setObjectName("routeLeaveStraight")
        self.straight.setToolTip("An end picked on a connector pin or a fitting port leaves along its +Z for the "
                                 "straight length (the pin's or port's own, else Bend Rules) before the first bend")
        layout.addWidget(self.straight)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("routeStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.add.clicked.connect(slot(self.add_selection))
        self.remove.clicked.connect(slot(self.remove_current))
        self.up.clicked.connect(slot(self.move_up))
        self.down.clicked.connect(slot(self.move_down))
        self.mode.currentIndexChanged.connect(slot(self._changed))
        self.radius.valueChanged.connect(slot(self._changed))
        self.minimum.valueChanged.connect(slot(self._changed))
        self.straight.toggled.connect(slot(self._changed))
        return form

    def _load(self):
        self.straight.setChecked(bool(getattr(self.path, "LeaveStraight", True)))
        self.mode.setCurrentText(self.path.Mode)
        self.radius.setValue(float(self.path.BendRadius.Value))
        self.minimum.setValue(float(self.path.MinBendRadius.Value))
        self._fill()

    def _fill(self):
        current = self.list.currentRow()
        self.list.clear()
        for k, (point, obj, sub) in enumerate(self.points, start=1):
            label = _pick_label(obj, sub)
            text = f"{k}  {_fmt(point)}" + (f"   on {label}" if label else "")
            self.list.addItem(text)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self.radius.setEnabled(self.mode.currentText() == "Polyline")

    # ----- picks -----

    def picked(self, doc_name, obj_name, sub, pnt):
        if not self.pick.isChecked() or self._building:
            return
        doc = App.getDocument(doc_name) if doc_name in App.listDocuments() else None
        obj = doc.getObject(obj_name) if doc is not None else None
        if obj is None or br.is_path(obj) or br.is_routed(obj):
            return
        point = br.attachment_point(obj, sub)
        if point is None:
            point = App.Vector(*pnt) if pnt is not None else None
        if point is None:
            return
        clear_selection_later()  # a pick is a point, not a growing selection
        if self.points and (self.points[-1][0] - point).Length < 1e-6:
            return
        self.points.append((point, obj, sub))
        self._fill()
        self.list.setCurrentRow(self.list.count() - 1)
        self._apply()

    def add_selection(self):
        picks = picks_from_selection()
        if not picks:
            self.status.setText("Select a vertex, a circular edge, a face or a datum first.")
            return
        self.points.extend(picks)
        self._fill()
        self._apply()
        clear_selection_later()

    def remove_current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.points):
            del self.points[row]
            self._fill()
            self._apply()

    def _swap(self, row, other):
        if 0 <= row < len(self.points) and 0 <= other < len(self.points):
            self.points[row], self.points[other] = self.points[other], self.points[row]
            self._fill()
            self.list.setCurrentRow(other)
            self._apply()

    def move_up(self):
        self._swap(self.list.currentRow(), self.list.currentRow() - 1)

    def move_down(self):
        self._swap(self.list.currentRow(), self.list.currentRow() + 1)

    def attach_current(self, obj, sub):
        """A clip: the current (or nearest) point attached to ``obj``/``sub``."""
        point = br.attachment_point(obj, sub)
        if point is None or not self.points:
            return False
        row = self.list.currentRow()
        if row < 0:
            row = min(range(len(self.points)), key=lambda i: (self.points[i][0] - point).Length)
        self.points[row] = (point, obj, sub)
        self._fill()
        self._apply()
        return True

    # ----- live update -----

    def _changed(self, *_args):
        if not self._building:
            self._apply()

    def _apply(self):
        self._fill()
        path = self.path
        self._adopt_container()
        path.Points = [p for p, _o, _s in self.points]
        br.set_attachments(path, [(i, o, s) for i, (_p, o, s) in enumerate(self.points) if o is not None])
        if bool(path.LeaveStraight) != self.straight.isChecked():
            path.LeaveStraight = self.straight.isChecked()
        path.Mode = self.mode.currentText()
        path.BendRadius = self.radius.value()
        path.MinBendRadius = self.minimum.value()
        if len(path.Points) < 2:
            self.status.setText("Pick at least two points.")
            return
        try:
            self.doc.recompute()
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        self.status.setText(self.summary(path))

    def _adopt_container(self):
        """ROU-05: a new path whose first attached pick is inside an assembly (or a part) goes into
        it, so its wires are BOM lines of that assembly and the pick is re-rooted below it."""
        path = self.path
        if self.editing or br._container_of(path) is not None:
            return
        first = next(((obj, sub) for _p, obj, sub in self.points if obj is not None), None)
        if first is None:
            return
        obj, sub = first
        if sub and "." in sub and (obj.isDerivedFrom("App::Part") or obj.TypeId == "Assembly::AssemblyObject"):
            br._place(path, container=obj)

    @staticmethod
    def summary(path):
        """One line: length, bends, tightest radius, the check; the pins or ports at the ends."""
        text = f"Length {path.Length.Value:.1f} mm · {path.Bends} bend{'s' if path.Bends != 1 else ''}"
        if path.MinRadius.Value > 0:
            text += f" · tightest R {path.MinRadius.Value:.1f} mm"
        for label, frame, straight in (("Starts on", getattr(path, "StartFrame", ""), getattr(path, "StartStraight", None)),
                                       ("Ends on", getattr(path, "EndFrame", ""), getattr(path, "EndStraight", None))):
            if frame:
                text += f"<br>{label} {_html(frame)}, straight {straight.Value:.1f} mm"
        if not path.BendOK:
            return text + "<br>" + RED.format(_html(path.Problems or "bend too tight"))
        return text + " · OK"

    # ----- task dialog -----

    def getStandardButtons(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def _close(self):
        try:
            self.Gui.Selection.removeObserver(self.observer)
        except Exception:
            pass
        if _state["panel"] is self:
            _state["panel"] = None
        self.Gui.Control.closeDialog()

    def accept(self):
        if len(self.path.Points) < 2:
            self.status.setText("Pick at least two points.")
            return False
        self.doc.commitTransaction()
        self._close()
        self.doc.recompute()
        return True

    def reject(self):
        self.doc.abortTransaction()
        if not self.editing:
            try:
                name = self.path.Name
                if self.doc.getObject(name) is not None:
                    self.doc.removeObject(name)
            except Exception:
                pass
        self._close()
        self.doc.recompute()
        return True


# ---------------------------------------------------------------------------------------------
# Item panels (wire, pipe, hose): a short form, live.


class ItemPanel:
    """``kind``: br.WIRE, br.PIPE or br.HOSE; edits ``obj`` or makes one on ``path``."""

    def __init__(self, kind, path=None, obj=None):
        QtCore, _QtGui, QtWidgets = _qt()
        self.Gui = _gui()
        self.kind = kind
        self.doc = App.ActiveDocument
        self.editing = obj is not None
        title = {br.WIRE: "Wire", br.PIPE: "Pipe", br.HOSE: "Hose"}[kind]
        self.doc.openTransaction(title)
        if obj is None:
            maker = {br.WIRE: br.make_wire, br.PIPE: br.make_pipe, br.HOSE: br.make_hose}[kind]
            obj = maker(path)
        self.obj = obj
        self._building = True
        self.form = self._build(QtWidgets, title)
        self._load()
        self._building = False
        self._apply()

    def _spin(self, QtWidgets, suffix=" mm", decimals=2, maximum=1e6):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.valueChanged.connect(slot(self._changed))
        return spin

    def _edit(self, QtWidgets):
        edit = QtWidgets.QLineEdit()
        edit.editingFinished.connect(slot(self._changed))
        return edit

    def _build(self, QtWidgets, title):
        form = QtWidgets.QWidget()
        form.setWindowTitle(title)
        layout = QtWidgets.QVBoxLayout(form)
        path = self.obj.Path
        layout.addWidget(QtWidgets.QLabel(f"On {path.Label}" if path is not None else "No path"))
        grid = QtWidgets.QFormLayout()
        self.fields = {}
        if self.kind == br.WIRE:
            self.fields["Gauge"] = combo = QtWidgets.QComboBox()
            combo.addItems(list(br._rt().GAUGE_NAMES))
            combo.currentIndexChanged.connect(slot(self._changed))
            grid.addRow("Gauge", combo)
            self.fields["InsulationOD"] = spin = self._spin(QtWidgets)
            spin.setSpecialValueText("from gauge")
            grid.addRow("Insulation OD", spin)
            self.colour_button = QtWidgets.QPushButton("Colour…")
            self.colour_button.clicked.connect(slot(self.pick_colour))
            grid.addRow("Colour", self.colour_button)
            for name, label in (("Signal", "Signal"), ("FromConnector", "From"), ("FromPin", "From pin"),
                                ("ToConnector", "To"), ("ToPin", "To pin")):
                self.fields[name] = self._edit(QtWidgets)
                grid.addRow(label, self.fields[name])
            self.fields["Slack"] = spin = self._spin(QtWidgets, " %", 0, 100)
            grid.addRow("Slack", spin)
            self.fields["CutLength"] = spin = self._spin(QtWidgets, " mm", 2, 100000)
            spin.setToolTip("Any length: longer than the route and it sags, shorter and it pulls taut. It never stretches.")
            grid.addRow("Cut length", spin)
        elif self.kind == br.PIPE:
            self.fields["Size"] = combo = QtWidgets.QComboBox()
            combo.addItems(list(br._rt().TUBE_SIZE_NAMES))
            combo.currentIndexChanged.connect(slot(self._size_changed))
            grid.addRow("Size", combo)
            self.fields["OD"] = self._spin(QtWidgets)
            grid.addRow("OD", self.fields["OD"])
            self.fields["WallThickness"] = self._spin(QtWidgets)
            grid.addRow("Wall", self.fields["WallThickness"])
            self.fields["Material"] = self._edit(QtWidgets)
            grid.addRow("Material", self.fields["Material"])
            self.fields["BendRadius"] = self._spin(QtWidgets)
            grid.addRow("Bend radius (CLR)", self.fields["BendRadius"])
        else:
            self.fields["ID"] = self._spin(QtWidgets)
            grid.addRow("ID", self.fields["ID"])
            self.fields["OD"] = self._spin(QtWidgets)
            grid.addRow("OD", self.fields["OD"])
            self.fields["Slack"] = self._spin(QtWidgets, " %", 0, 100)
            grid.addRow("Slack", self.fields["Slack"])
            self.fields["CutLength"] = spin = self._spin(QtWidgets, " mm", 2, 100000)
            spin.setToolTip("Any length: longer than the route and it sags, shorter and it pulls taut. It never stretches.")
            grid.addRow("Cut length", spin)
        self._rule_rows(QtWidgets, grid)
        self._stock_rows(QtWidgets, grid)
        layout.addLayout(grid)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("routeItemStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        return form

    def _rule_rows(self, QtWidgets, grid):
        """Bend rule (Default, a preset, Multiple of OD, Fixed radius, No check), the multiple and
        the radius. Typing a multiple picks Multiple of OD, typing a radius picks Fixed radius."""
        rt = br._rt()
        self.rule = QtWidgets.QComboBox()
        self.rule.setObjectName("routeBendRule")
        self.rule.addItems(rt.rule_choices(self.obj.Proxy.RULE_KIND))
        self.rule.setToolTip("Default follows Bend Rules (the Routing tab); a preset or your own rule overrides it here")
        self.rule.currentIndexChanged.connect(slot(self._changed))
        grid.addRow("Bend rule", self.rule)
        self.factor = QtWidgets.QDoubleSpinBox()
        self.factor.setRange(0.0, 1000.0)
        self.factor.setDecimals(2)
        self.factor.setSuffix(" × OD")
        self.factor.valueChanged.connect(slot(self._factor_changed))
        grid.addRow("Multiple of OD", self.factor)
        self.minimum = QtWidgets.QDoubleSpinBox()
        self.minimum.setRange(0.0, 1e6)
        self.minimum.setDecimals(2)
        self.minimum.setSuffix(" mm")
        self.minimum.setSpecialValueText("no check")
        self.minimum.valueChanged.connect(slot(self._minimum_changed))
        grid.addRow("Min bend radius", self.minimum)

    # ----- stock material (ROU-06) -----

    def _stock_session(self):
        try:
            import bruceworx_vaultclient

            return bruceworx_vaultclient.session()
        except Exception:
            return None

    def _stock_rows(self, QtWidgets, grid):
        """Stock material: the vault item this is cut from (its length is summed on the purchasing BOM)."""
        self.stock_rows = []
        self.stock = QtWidgets.QComboBox()
        self.stock.setObjectName("routeStockItem")
        self.stock.setToolTip("The Stock material vault item this is cut from: ordered by its manufacturer and MPN")
        self.stock_new = QtWidgets.QPushButton("New…")
        self.stock_new.setToolTip("Make a Stock material item from this item's properties (gauge and colour, "
                                  "tube size, hose size)")
        line = QtWidgets.QHBoxLayout()
        line.addWidget(self.stock, 1)
        line.addWidget(self.stock_new)
        grid.addRow("Stock material", line)
        self._fill_stock()
        self.stock.currentIndexChanged.connect(slot(self._stock_chosen))
        self.stock_new.clicked.connect(slot(self._stock_make))

    def _fill_stock(self):
        import bruceworx_stock as bs
        from bruceworx import stock

        building, self._building = getattr(self, "_building", False), True
        try:
            self.stock.clear()
            self.stock.addItem("(none)", None)
            s = self._stock_session()
            if s is None:
                self.stock.setEnabled(False)
                self.stock_new.setEnabled(False)
                self.stock.setToolTip("Open a vault to pick or make stock material")
                return
            kind, key, _description = bs.stock_of(self.obj)
            self.stock_rows = stock.list_stock(s.vault, kind)
            current = self.obj.StockItem
            for row in self.stock_rows:
                text = f"{row['description']}  ·  {row['manufacturer']} {row['mpn']}".rstrip(" ·")
                self.stock.addItem(f"{text}  ({row['pn']})", row)
                if row["pn"] == current:
                    self.stock.setCurrentIndex(self.stock.count() - 1)
        finally:
            self._building = building

    def _stock_chosen(self, *_args):
        if self._building:
            return
        import bruceworx_stock as bs

        row = self.stock.currentData()
        if row:
            bs.assign_item(self.obj, row)
        else:
            bs.assign(self.obj, "", "", "")
        self.doc.recompute()

    def _stock_make(self):
        """New stock material from the item's properties: ask for what the properties do not say."""
        import bruceworx_stock as bs
        from bruceworx import stock

        s = self._stock_session()
        if s is None:
            return
        _QtCore, _QtGui, QtWidgets = _qt()
        kind, key, description = bs.stock_of(self.obj)
        dialog = QtWidgets.QDialog(self.form)
        dialog.setWindowTitle(f"New stock material — {description}")
        form = QtWidgets.QFormLayout(dialog)
        maker = QtWidgets.QLineEdit(self.obj.Manufacturer)
        mpn = QtWidgets.QLineEdit(self.obj.ManufacturerPartNumber)
        scrap = QtWidgets.QDoubleSpinBox()
        scrap.setRange(0.0, 100.0)
        scrap.setSuffix(" %")
        scrap.setValue(stock.DEFAULT_SCRAP)
        unit = QtWidgets.QDoubleSpinBox()
        unit.setRange(0.0, 100000.0)
        unit.setSuffix(" m")
        unit.setSpecialValueText("exact length")
        unit.setToolTip("Spool or stick length bought, e.g. 30 m; the purchasing BOM rounds up to whole units")
        form.addRow("Manufacturer", maker)
        form.addRow("MPN", mpn)
        form.addRow("Scrap allowance", scrap)
        form.addRow("Purchase unit", unit)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec_():
            return
        if not mpn.text().strip():
            self.status.setText(RED.format("A stock item needs the manufacturer's part number: that is what is ordered."))
            return
        try:
            pn, made = bs.make_from_item(s, self.obj, maker.text().strip(), mpn.text().strip(), scrap.value(),
                                         unit.value())
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        self._fill_stock()
        self.status.setText(f"{'Made' if made else 'Found'} {_html(pn)}: {_html(description)}")

    def _show_rule(self):
        """The rule widgets from the object (after a recompute the radius is the rule's)."""
        building, self._building = self._building, True
        try:
            self.rule.setCurrentText(self.obj.BendRule)
            rule, _label = self.obj.Proxy.rule(self.obj)  # a preset shows its own multiple
            self.factor.setValue(float(rule.value if rule.mode == "OD" else self.obj.BendFactor))
            self.minimum.setValue(float(self.obj.MinBendRadius.Value))
        finally:
            self._building = building

    def _factor_changed(self, *_args):
        if self._building:
            return
        self._building = True
        self.rule.setCurrentText(br._rt().RULE_OD)
        self._building = False
        self._apply()

    def _minimum_changed(self, *_args):
        if self._building:
            return
        self._building = True
        self.rule.setCurrentText(br._rt().RULE_FIXED)
        self._building = False
        self._apply()

    def _apply_rule(self):
        obj = self.obj
        choice = self.rule.currentText()
        if abs(float(obj.BendFactor) - self.factor.value()) > 1e-9 and choice == br._rt().RULE_OD:
            obj.BendFactor = self.factor.value()
        if choice == br._rt().RULE_FIXED and abs(float(obj.MinBendRadius.Value) - self.minimum.value()) > 1e-9:
            obj.MinBendRadius = self.minimum.value()
        if obj.BendRule != choice:
            obj.BendRule = choice

    def _load(self):
        _QtCore, QtGui, _QtWidgets = _qt()
        self._show_rule()
        for name, widget in self.fields.items():
            value = getattr(self.obj, name)
            if hasattr(widget, "setCurrentText"):
                widget.setCurrentText(str(value))
            elif hasattr(widget, "setValue"):
                widget.setValue(float(getattr(value, "Value", value)))
            else:
                widget.setText(str(value))
        if "CutLength" in self.fields:
            self._cut_shown = float(self.obj.CutLength.Value)
        if self.kind == br.WIRE:
            self._show_colour()

    def _show_colour(self):
        r, g, b = [int(round(c * 255)) for c in tuple(self.obj.Colour)[:3]]
        self.colour_button.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {'white' if r + g + b < 380 else 'black'}")

    def _size_changed(self, *_args):
        if self._building:
            return
        self.obj.Size = self.fields["Size"].currentText()  # the proxy sets OD and wall
        self._building = True
        self.fields["OD"].setValue(float(self.obj.OD.Value))
        self.fields["WallThickness"].setValue(float(self.obj.WallThickness.Value))
        self._building = False
        self._apply()

    def pick_colour(self):
        _QtCore, QtGui, QtWidgets = _qt()
        r, g, b = [int(round(c * 255)) for c in tuple(self.obj.Colour)[:3]]
        colour = QtWidgets.QColorDialog.getColor(QtGui.QColor(r, g, b), self.form, "Wire colour")
        if colour.isValid():
            self.set_colour((colour.redF(), colour.greenF(), colour.blueF()))

    def set_colour(self, rgb):
        self.obj.Colour = tuple(float(c) for c in rgb) + (0.0,)
        self._show_colour()
        self._apply()

    def _changed(self, *_args):
        if not self._building:
            self._apply()

    def _apply(self):
        obj = self.obj
        for name, widget in self.fields.items():
            if hasattr(widget, "currentText"):
                value = widget.currentText()
                if name == "Size":
                    continue  # applied by _size_changed
            elif hasattr(widget, "value"):
                value = widget.value()
                if name == "Slack":
                    value = int(value)
                if name == "CutLength" and abs(value - getattr(self, "_cut_shown", value)) < 1e-9:
                    continue  # untouched: Slack may have changed it
            else:
                value = widget.text()
            try:
                current = getattr(obj, name)
                current = getattr(current, "Value", current)
                if current != value:
                    setattr(obj, name, value)
            except Exception as exc:
                self.status.setText(RED.format(_html(exc)))
                return
        try:
            self._apply_rule()
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        if self.kind == br.PIPE:
            br.sync_clr(obj)
        try:
            self.doc.recompute()
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        self._show_rule()
        self._building = True  # Slack edits the cut length: show what it came to
        try:
            if "CutLength" in self.fields:
                self._cut_shown = float(obj.CutLength.Value)
                self.fields["CutLength"].setValue(self._cut_shown)
        finally:
            self._building = False
        self.status.setText(self.summary(obj))

    @staticmethod
    def summary(obj):
        kind = br.kind_of(obj)
        text = f"Length {obj.Length.Value:.1f} mm ({obj.Length.Value / 1000:.3f} m)"
        if kind == br.WIRE:
            text += f" · {obj.Mass.Value * 1000:.1f} g"
        elif kind == br.PIPE:
            text += f" · cut {obj.CutLength.Value:.1f} mm · {obj.Bends} bends"
            if obj.BendTable:
                text += "<br><tt>" + "<br>".join(_html(line) for line in obj.BendTable.splitlines()) + "</tt>"
        status = getattr(obj, "RopeStatus", "")
        if status:  # ROU-08: taut, or how far it sags
            text += "<br>" + (RED.format(_html(status)) if getattr(obj, "Taut", False) else _html(status))
            if getattr(obj, "Problems", ""):
                text += "<br>" + RED.format(_html(obj.Problems))
        if obj.MinBendRadius.Value > 0:
            text += f"<br>min bend R {obj.MinBendRadius.Value:.1f} mm ({_html(obj.BendRuleUsed)})"
        else:
            text += "<br>no bend check"
        if not obj.BendOK:
            text += "<br>" + RED.format("bend too tight for this item (see MinBendRadius)")
        return text

    def getStandardButtons(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        self.doc.commitTransaction()
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True

    def reject(self):
        self.doc.abortTransaction()
        if not self.editing:
            try:
                name = self.obj.Name
                if self.doc.getObject(name) is not None:
                    self.doc.removeObject(name)
            except Exception:
                pass
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True


# ---------------------------------------------------------------------------------------------
# Bend Rules: the defaults in the preference group Mod/BruceWorx/Routing.

KIND_TITLES = {"wire": "Wires", "hose": "Hoses", "tube": "Tubes (pipes)"}


class BendRulesDialog:
    """Per kind: the default rule (a preset, a multiple of OD, a fixed radius or no check) and its
    numbers; for tubes also the default CLR. OK writes the preferences and recomputes every open
    item that follows them."""

    def __init__(self, parent=None):
        _QtCore, _QtGui, QtWidgets = _qt()
        rt = br._rt()
        self.dialog = QtWidgets.QDialog(parent)
        self.dialog.setObjectName("routeBendRules")
        self.dialog.setWindowTitle("Bend Rules")
        layout = QtWidgets.QVBoxLayout(self.dialog)
        intro = QtWidgets.QLabel("The minimum bend radius a new wire, hose or tube gets (its rule <i>Default</i>). "
                                 "Each item's panel can pick another rule for that item. A bundle takes the strictest "
                                 "rule of its wires, on the bundle's OD.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.widgets = {}
        for kind in rt.RULE_KINDS:
            box = QtWidgets.QGroupBox(KIND_TITLES[kind])
            form = QtWidgets.QFormLayout(box)
            combo = QtWidgets.QComboBox()
            combo.setObjectName(f"bend{kind.capitalize()}Rule")
            for name in rt.default_choices(kind):
                rule = rt.PRESETS[kind].get(name, (None, ""))[0]
                combo.addItem(f"{name} ({rule.text()})" if rule is not None else name, name)
            factor = QtWidgets.QDoubleSpinBox()
            factor.setRange(0.1, 1000.0)
            factor.setDecimals(2)
            factor.setSuffix(" × OD")
            radius = QtWidgets.QDoubleSpinBox()
            radius.setRange(0.0, 1e5)
            radius.setDecimals(2)
            radius.setSuffix(" mm")
            form.addRow("Rule", combo)
            form.addRow("Multiple of OD", factor)
            form.addRow("Fixed radius", radius)
            self.widgets[kind] = (combo, factor, radius)
            combo.currentIndexChanged.connect(slot(self._enable))
            if kind == "tube":
                self.clr = QtWidgets.QDoubleSpinBox()
                self.clr.setRange(0.1, 1000.0)
                self.clr.setDecimals(2)
                self.clr.setSuffix(" × OD")
                form.addRow("Bend radius (CLR)", self.clr)
            layout.addWidget(box)
        box = QtWidgets.QGroupBox("Straight at pins and ports")
        box.setToolTip("A route on a connector pin or a fitting port leaves along its +Z this far before the "
                       "first bend; a pin or port with its own straight length keeps it")
        form = QtWidgets.QFormLayout(box)
        self.straights = {}
        for key, label, suffix in (("WireStraightFactor", "Wire, past the terminal", " × OD"),
                                   ("HoseStraightFactor", "Hose, out of the fitting", " × OD"),
                                   ("TubeStraightFactor", "Tube, out of the fitting", " × OD"),
                                   ("TubeClampLength", "Tube, bender clamp (at least)", " mm")):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setObjectName(f"bend{key}")
            spin.setRange(0.0, 1000.0)
            spin.setDecimals(2)
            spin.setSuffix(suffix)
            if key == "TubeClampLength":
                spin.setSpecialValueText("none")
            form.addRow(label, spin)
            self.straights[key] = spin
        layout.addWidget(box)
        sources = QtWidgets.QLabel(self.presets_html())
        sources.setWordWrap(True)
        sources.setObjectName("bendPresetSources")
        layout.addWidget(sources)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel | QtWidgets.QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(slot(self.accept))
        buttons.rejected.connect(slot(self.dialog.reject))
        buttons.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(slot(self.restore_defaults))
        layout.addWidget(buttons)
        self.load(br.prefs())

    @staticmethod
    def presets_html():
        rt = br._rt()
        rows = [f"<tr><td>{_html(name)}</td><td>{_html(rule.text())}</td><td>{_html(source)}</td></tr>"
                for name, (rule, source) in list(rt.WIRE_PRESETS.items()) + list(rt.HOSE_PRESETS.items())]
        return "<small><b>Presets</b><table>" + "".join(rows) + "</table></small>"

    def load(self, values):
        for kind, (combo, factor, radius) in self.widgets.items():
            key = kind.capitalize()
            index = combo.findData(values[key + "Rule"])
            combo.setCurrentIndex(max(0, index))
            factor.setValue(float(values[key + "Factor"]))
            radius.setValue(float(values[key + "Radius"]))
        self.clr.setValue(float(values["TubeClrFactor"]))
        for key, spin in self.straights.items():
            spin.setValue(float(values[key]))
        self._enable()

    def _enable(self, *_args):
        rt = br._rt()
        for combo, factor, radius in self.widgets.values():
            factor.setEnabled(combo.currentData() == rt.RULE_OD)
            radius.setEnabled(combo.currentData() == rt.RULE_FIXED)

    def values(self):
        out = {}
        for kind, (combo, factor, radius) in self.widgets.items():
            key = kind.capitalize()
            out[key + "Rule"] = combo.currentData()
            out[key + "Factor"] = factor.value()
            out[key + "Radius"] = radius.value()
        out["TubeClrFactor"] = self.clr.value()
        for key, spin in self.straights.items():
            out[key] = spin.value()
        return out

    def restore_defaults(self):
        self.load(br._rt().prefs_with_defaults())

    def accept(self):
        br.set_prefs(self.values())
        refresh_documents()
        self.dialog.accept()

    def exec_(self):
        return self.dialog.exec() if hasattr(self.dialog, "exec") else self.dialog.exec_()


def refresh_documents():
    """Recompute every open item whose rule follows the preferences (Default; a bundle From
    wires). Returns how many were touched."""
    rt = br._rt()
    touched = 0
    for doc in App.listDocuments().values():
        mine = [o for o in br.routed(doc) if o.BendRule in (rt.RULE_DEFAULT, rt.RULE_FROM_WIRES)]
        for obj in mine:
            obj.touch()
        for path in br.paths(doc):  # ROU-05: the straights at pins and ports follow the defaults
            if br.path_ends(path):
                path.touch()
                mine.append(path)
        if mine:
            doc.recompute()
            touched += len(mine)
    return touched


# ---------------------------------------------------------------------------------------------
# Reports.


def export_from_to(doc, path):
    """Write the from-to list of ``doc`` to ``path``; the path written."""
    out = br.write_text(path, br.from_to_csv(doc))
    _console(f"wrote {out}")
    return out


def export_cut_list(doc, path):
    out = br.write_text(path, br.cut_list_csv(doc))
    _console(f"wrote {out}")
    return out


def export_terminals(doc, path):
    out = br.write_text(path, br.terminal_csv(doc))
    _console(f"wrote {out}")
    return out


def _ask_and_write(doc, name, writer):
    _QtCore, _QtGui, QtWidgets = _qt()
    start = os.path.join(os.path.expanduser("~/Desktop"), name)
    path, _f = QtWidgets.QFileDialog.getSaveFileName(_gui().getMainWindow(), "Export CSV", start, "CSV (*.csv)")
    if path:
        writer(doc, path)
    return path


# ---------------------------------------------------------------------------------------------
# Pins, ports and fittings (ROU-05).


def _bp():
    import bruceworx_routing_ports

    return bruceworx_routing_ports


def _bf():
    import bruceworx_fittings

    return bruceworx_fittings


def pick_frame(obj, sub):
    """``(point, outward direction)`` in the document frame for a pick: a pin or port frame (its
    origin and +Z), a face (its centre and normal), a circular edge (its centre and axis, pointing
    out of the solid), anything else its point and +Z."""
    found = _bp().frame_at(obj, sub)
    if found is not None:
        placement = found[1]
        return App.Vector(placement.Base), placement.Rotation.multVec(App.Vector(0, 0, 1))
    point = br.attachment_point(obj, sub)
    if point is None:
        return None
    direction = App.Vector(0, 0, 1)
    try:
        shape = obj.getSubObject(sub) if sub else None
        frame = br._parent_frame(obj)
        if shape is not None and shape.ShapeType == "Face":
            u, v = shape.Surface.parameter(shape.CenterOfMass)
            direction = frame.Rotation.multVec(shape.normalAt(u, v))
        elif shape is not None and shape.ShapeType == "Edge" and hasattr(shape.Curve, "Axis"):
            axis = frame.Rotation.multVec(App.Vector(shape.Curve.Axis))
            owner = obj.getSubObject(sub.rsplit(".", 1)[0] + ".") if "." in sub else obj.Shape
            owner = owner if owner is not None else obj.Shape
            probe = point + axis * 0.5
            inside = False
            try:
                inside = owner.isInside(_parent_frame_inverse(obj).multVec(probe), 1e-6, True)
            except Exception:
                pass
            direction = axis * -1.0 if inside else axis
    except Exception:
        pass
    if direction.Length > 1e-12:
        direction.normalize()
    return point, direction


def _parent_frame_inverse(obj):
    return br._parent_frame(obj).inverse()


def _owner_part(obj, sub):
    """The App::Part a new frame goes in: the innermost App::Part on the pick's path (not through a
    link: a frame belongs to the part's own file), else None."""
    names = [n for n in (sub or "").split(".")[:-1] if n]
    chain = list(obj.getSubObjectList(".".join(names) + ".")) if names else [obj]
    owner = None
    for item in chain:
        if item.isDerivedFrom("App::Link"):
            break
        if item.isDerivedFrom("App::Part") and item.TypeId != "Assembly::AssemblyObject":
            owner = item
    return owner


class FramePanel:
    """A pin or port frame: placed on a pick, or an existing LCS edited; live, OK keeps it."""

    def __init__(self, frame=None, pick=None):
        _QtCore, _QtGui, QtWidgets = _qt()
        self.Gui = _gui()
        self.doc = App.ActiveDocument
        self.doc.openTransaction("Pin / Port")
        bp = _bp()
        self.editing = frame is not None
        self.created_part = None
        self.note = ""
        if frame is None:
            obj, sub = pick
            point, direction = pick_frame(obj, sub)
            part = _owner_part(obj, sub)
            if part is None:
                target = obj if not sub or "." not in sub else obj.getSubObjectList(sub.rsplit(".", 1)[0] + ".")[-1]
                part = self.doc.addObject("App::Part", "Connector")
                try:
                    part.addObject(target)
                except Exception:
                    pass
                self.created_part = part
                self.note = f"{_html(target.Label)} was put in a new part, {_html(part.Label)}."
            local = part.getGlobalPlacement().inverse().multiply(
                App.Placement(point, App.Rotation(App.Vector(0, 0, 1), direction)))
            names = [bp.frame_name(f) for f in bp.pins(part)]
            name = str(len(names) + 1)
            while name in names:
                name = str(int(name) + 1)
            frame = bp.make_pin(part, name, local)
        elif not bp.is_frame(frame):
            bp.add_frame_props(frame, bp.PIN, PinName=frame.Label)
        self.frame = frame
        self.part = frame.getParentGeoFeatureGroup()
        if self.part is not None:
            bp.set_connector(self.part)
        self._building = True
        self.form = self._build(QtWidgets)
        self._load()
        self._building = False
        self._apply()

    def _build(self, QtWidgets):
        bp = _bp()
        form = QtWidgets.QWidget()
        form.setWindowTitle("Pin / Port")
        layout = QtWidgets.QVBoxLayout(form)
        top = QtWidgets.QFormLayout()
        self.kind = QtWidgets.QComboBox()
        self.kind.setObjectName("frameKind")
        self.kind.addItems([bp.PIN, bp.PORT])
        self.name = QtWidgets.QLineEdit()
        self.name.setObjectName("frameName")
        top.addRow("Kind", self.kind)
        top.addRow("Name", self.name)
        layout.addLayout(top)

        def spin(suffix=" mm", maximum=1000.0, special=""):
            box = QtWidgets.QDoubleSpinBox()
            box.setRange(0.0, maximum)
            box.setDecimals(2)
            box.setSuffix(suffix)
            if special:
                box.setSpecialValueText(special)
            box.valueChanged.connect(slot(self._changed))
            return box

        def edit():
            line = QtWidgets.QLineEdit()
            line.editingFinished.connect(slot(self._changed))
            return line

        self.pin_box = QtWidgets.QGroupBox("Pin")
        pin = QtWidgets.QFormLayout(self.pin_box)
        self.gauge_min = QtWidgets.QSpinBox()
        self.gauge_max = QtWidgets.QSpinBox()
        for box in (self.gauge_min, self.gauge_max):
            box.setRange(0, 40)
            box.setSuffix(" AWG")
            box.setSpecialValueText("not stated")
            box.valueChanged.connect(slot(self._changed))
        self.terminal, self.seal, self.signal = edit(), edit(), edit()
        self.terminal_mfr, self.terminal_item, self.seal_mfr, self.seal_item = edit(), edit(), edit(), edit()
        self.terminal.setToolTip("The terminal's manufacturer P/N: what is ordered")
        self.terminal_item.setPlaceholderText("Standard Library P/N (optional)")
        self.seal_item.setPlaceholderText("Standard Library P/N (optional)")
        self.pin_straight = spin(special="default rule")
        self.strip = spin(special="none")
        self.strip.setToolTip("Wire inside the connector beyond the frame (the terminal's strip length); added to the cut length")
        pin.addRow("Thinnest wire", self.gauge_min)
        pin.addRow("Thickest wire", self.gauge_max)
        pin.addRow("Terminal MPN", self.terminal)
        pin.addRow("Terminal maker", self.terminal_mfr)
        pin.addRow("Terminal item", self.terminal_item)
        pin.addRow("Seal MPN", self.seal)
        pin.addRow("Seal maker", self.seal_mfr)
        pin.addRow("Seal item", self.seal_item)
        pin.addRow("Default signal", self.signal)
        pin.addRow("Straight before bend", self.pin_straight)
        pin.addRow("Strip length", self.strip)
        layout.addWidget(self.pin_box)

        self.port_box = QtWidgets.QGroupBox("Port")
        port = QtWidgets.QFormLayout(self.port_box)
        self.port_type = QtWidgets.QComboBox()
        self.port_type.addItems(bp.PORT_TYPES)
        self.port_type.currentIndexChanged.connect(slot(self._changed))
        self.port_size = edit()
        self.tube_od = spin()
        self.port_straight = spin(special="default rule")
        self.engagement = spin()
        port.addRow("Type", self.port_type)
        port.addRow("Size", self.port_size)
        port.addRow("Tube OD", self.tube_od)
        port.addRow("Straight before bend", self.port_straight)
        port.addRow("Engagement", self.engagement)
        layout.addWidget(self.port_box)

        conn = QtWidgets.QGroupBox("Connector")
        cform = QtWidgets.QFormLayout(conn)
        self.family, self.mating = edit(), edit()
        self.mating.setToolTip("P/N of the mating half; several separated by ';'")
        cform.addRow("Family", self.family)
        cform.addRow("Mating P/N", self.mating)
        layout.addWidget(conn)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("frameStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.kind.currentIndexChanged.connect(slot(self._kind_changed))
        self.name.editingFinished.connect(slot(self._changed))
        return form

    def _load(self):
        bp = _bp()
        f = self.frame
        self.kind.setCurrentText(bp.frame_kind(f))
        self.name.setText(bp.frame_name(f))
        if bp.is_pin(f):
            self.gauge_min.setValue(int(f.GaugeMin))
            self.gauge_max.setValue(int(f.GaugeMax))
            self.terminal.setText(f.TerminalPN)
            self.seal.setText(f.SealPN)
            for widget, name in ((self.terminal_mfr, "TerminalMfr"), (self.terminal_item, "TerminalItem"),
                                 (self.seal_mfr, "SealMfr"), (self.seal_item, "SealItem")):
                widget.setText(str(getattr(f, name, "")))
            self.signal.setText(f.DefaultSignal)
            self.pin_straight.setValue(float(f.StraightLength.Value))
            self.strip.setValue(float(getattr(getattr(f, "StripLength", 0.0), "Value", 0.0)))
        else:
            self.port_type.setCurrentText(f.PortType)
            self.port_size.setText(f.PortSize)
            self.tube_od.setValue(float(f.TubeOD.Value))
            self.port_straight.setValue(float(f.StraightLength.Value))
            self.engagement.setValue(float(f.Engagement.Value))
        if self.part is not None:
            self.family.setText(getattr(self.part, "ConnectorFamily", ""))
            self.mating.setText(getattr(self.part, "MatingPN", ""))
        self._show_kind()

    def _show_kind(self):
        pin = self.kind.currentText() == _bp().PIN
        self.pin_box.setVisible(pin)
        self.port_box.setVisible(not pin)

    def _kind_changed(self, *_args):
        if self._building:
            return
        bp = _bp()
        kind = self.kind.currentText()
        if bp.frame_kind(self.frame) != kind:
            bp.add_frame_props(self.frame, kind)
            key = "PinName" if kind == bp.PIN else "PortName"
            setattr(self.frame, key, self.name.text().strip() or "1")
        self._building = True
        self._load()
        self._building = False
        self._apply()

    def _changed(self, *_args):
        if not self._building:
            self._apply()

    def _set(self, name, value):
        current = getattr(self.frame, name)
        if getattr(current, "Value", current) != value:
            setattr(self.frame, name, value)

    def _apply(self):
        bp = _bp()
        f = self.frame
        name = self.name.text().strip() or "1"
        try:
            if bp.is_pin(f):
                self._set("PinName", name)
                self._set("GaugeMin", int(self.gauge_min.value()))
                self._set("GaugeMax", int(self.gauge_max.value()))
                self._set("TerminalPN", self.terminal.text().strip())
                self._set("SealPN", self.seal.text().strip())
                for widget, prop in ((self.terminal_mfr, "TerminalMfr"), (self.terminal_item, "TerminalItem"),
                                     (self.seal_mfr, "SealMfr"), (self.seal_item, "SealItem")):
                    if prop not in f.PropertiesList:
                        bp.add_frame_props(f, bp.PIN)  # a pin made before 30 Oct's v1.2 fields
                    self._set(prop, widget.text().strip())
                self._set("DefaultSignal", self.signal.text().strip())
                self._set("StraightLength", self.pin_straight.value())
                if "StripLength" not in f.PropertiesList:
                    bp.add_frame_props(f, bp.PIN)
                self._set("StripLength", self.strip.value())
            else:
                self._set("PortName", name)
                self._set("PortType", self.port_type.currentText())
                self._set("PortSize", self.port_size.text().strip())
                self._set("TubeOD", self.tube_od.value())
                self._set("StraightLength", self.port_straight.value())
                self._set("Engagement", self.engagement.value())
            label = f"{bp.frame_kind(f)} {name}"
            if f.Label != label:
                f.Label = label
            if self.part is not None:
                if self.part.ConnectorFamily != self.family.text().strip():
                    self.part.ConnectorFamily = self.family.text().strip()
                if self.part.MatingPN != self.mating.text().strip():
                    self.part.MatingPN = self.mating.text().strip()
            self.doc.recompute()
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        where = f.getGlobalPlacement()
        z = where.Rotation.multVec(App.Vector(0, 0, 1))
        text = (f"{_html(f.Label)} on {_html(self.part.Label if self.part else '?')} at {_fmt(where.Base)}, "
                f"+Z ({z.x:.2f}, {z.y:.2f}, {z.z:.2f})")
        if self.note:
            text += "<br>" + self.note
        self.status.setText(text)

    def getStandardButtons(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        self._apply()
        self.doc.commitTransaction()
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True

    def reject(self):
        self.doc.abortTransaction()
        if not self.editing:
            for obj in (self.frame, self.created_part):
                try:
                    if obj is not None and self.doc.getObject(obj.Name) is not None:
                        self.doc.removeObject(obj.Name)
                except Exception:
                    pass
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True


_fitting_choice = {"family": "Push-to-connect", "style": "Straight", "size": "6 mm", "thread": None}


class FittingPanel:
    """A fitting from the size tables, placed on the selection; live; OK keeps it."""

    def __init__(self, fitting=None, pick=None):
        _QtCore, _QtGui, QtWidgets = _qt()
        bf = _bf()
        self.Gui = _gui()
        self.doc = App.ActiveDocument
        self.doc.openTransaction("Fitting")
        self.editing = fitting is not None
        self.pick = None
        if fitting is None:
            container = None
            if pick is not None:
                obj, sub = pick
                self.pick = pick_frame(obj, sub)
                if sub and "." in sub and (obj.isDerivedFrom("App::Part") or obj.TypeId == "Assembly::AssemblyObject") \
                        and not bf.is_fitting(obj):
                    container = obj
            c = _fitting_choice
            fitting = bf.make_fitting(self.doc, c["family"], c["style"], c["size"], c["thread"], container=container)
        self.fitting = fitting
        self.body = bf.body_of(fitting)
        self._building = True
        self.form = self._build(QtWidgets)
        self._load()
        self._building = False
        self._apply()

    def _build(self, QtWidgets):
        form = QtWidgets.QWidget()
        form.setWindowTitle("Fitting")
        layout = QtWidgets.QVBoxLayout(form)
        grid = QtWidgets.QFormLayout()
        self.combos = {}
        for key, label in (("Family", "Family"), ("Style", "Style"), ("Size", "Size"), ("Thread", "Thread")):
            combo = QtWidgets.QComboBox()
            combo.setObjectName(f"fitting{key}")
            combo.currentIndexChanged.connect(slot(self._changed))
            grid.addRow(label, combo)
            self.combos[key] = combo
        self.pn = QtWidgets.QLineEdit()
        self.pn.setObjectName("fittingPartNumber")
        self.pn.setPlaceholderText("vault P/N (optional)")
        self.pn.editingFinished.connect(slot(self._changed))
        grid.addRow("Part number", self.pn)
        self.mcm = QtWidgets.QLineEdit()
        self.mcm.setObjectName("fittingMcMaster")
        self.mcm.setPlaceholderText("McMaster-Carr P/N, e.g. 5779K16")
        self.mcm.editingFinished.connect(slot(self._changed))
        grid.addRow("McMaster P/N", self.mcm)
        turn = QtWidgets.QHBoxLayout()
        self.left = QtWidgets.QPushButton("⟲ 90°")
        self.right = QtWidgets.QPushButton("⟳ 90°")
        self.angle = QtWidgets.QDoubleSpinBox()
        self.angle.setObjectName("fittingAngle")
        self.angle.setRange(-360.0, 360.0)
        self.angle.setDecimals(1)
        self.angle.setSuffix("°")
        self.angle.setWrapping(True)
        self.angle.setToolTip("Turn the fitting about the port it is placed by (its thread)")
        for widget in (self.left, self.right, self.angle):
            turn.addWidget(widget)
        self.left.clicked.connect(slot(lambda *_a: self.turn(90.0)))
        self.right.clicked.connect(slot(lambda *_a: self.turn(-90.0)))
        self.angle.valueChanged.connect(slot(self._changed))
        grid.addRow("Turn", turn)
        layout.addLayout(grid)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("fittingStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        return form

    def _fill(self, key, items, current):
        combo = self.combos[key]
        if [combo.itemText(i) for i in range(combo.count())] != list(items):
            combo.clear()
            combo.addItems(list(items))
        combo.setCurrentText(current)
        combo.setEnabled(len(items) > 1)

    def _load(self):
        ft = _bf()._ft()
        b = self.body
        self._fill("Family", ft.FAMILIES, b.Family)
        self._fill("Style", ft.styles(b.Family), b.Style)
        self._fill("Size", ft.sizes(b.Family), b.Size)
        self._fill("Thread", ft.threads(b.Family, b.Style, b.Size) or [ft.NO_THREAD], b.Thread)
        self.pn.setText(str(getattr(self.fitting, "PartNumber", "") or ""))
        self.mcm.setText(str(getattr(self.fitting, "VendorPartNumber", "") or ""))

    def _changed(self, *_args):
        if self._building:
            return
        self._apply()

    def _apply(self):
        bf = _bf()
        c = {k: w.currentText() for k, w in self.combos.items()}
        try:
            b = self.body
            if (c["Family"], c["Style"], c["Size"], c["Thread"]) != (b.Family, b.Style, b.Size, b.Thread):
                family_changed = c["Family"] != b.Family
                bf.set_choice(self.fitting, c["Family"], None if family_changed else c["Style"],
                              None if family_changed else c["Size"], None if family_changed else c["Thread"])
            if "PartNumber" in self.fitting.PropertiesList and self.fitting.PartNumber != self.pn.text().strip():
                self.fitting.PartNumber = self.pn.text().strip()
            if "VendorPartNumber" in self.fitting.PropertiesList:
                if self.fitting.VendorPartNumber != self.mcm.text().strip():
                    self.fitting.VendorPartNumber = self.mcm.text().strip()
                self.fitting.Vendor = "McMaster-Carr"
            if self.pick is None and abs(self.angle.value()) > 1e-9:
                mount = bf.mount_port(self.fitting).getGlobalPlacement()  # an existing fitting turns in place
                self.pick = (mount.Base, mount.Rotation.multVec(App.Vector(0, 0, -1)), self.fitting.Placement)
            if self.pick is not None:
                bf.place_fitting(self.fitting, self.pick[0], self.pick[1], angle=self.angle.value()
                                 + self._base_angle())
            self.doc.recompute()
        except Exception as exc:
            self.status.setText(RED.format(_html(exc)))
            return
        self._building = True
        try:
            self._load()
        finally:
            self._building = False
        _fitting_choice.update(family=b.Family, style=b.Style, size=b.Size,
                               thread=b.Thread if b.Thread != "None" else None)
        ports = ", ".join(f"{_html(f.Label)} {_html(f.PortType)} {_html(f.PortSize)}" for f in _bp().ports(self.fitting))
        self.status.setText(f"<b>{_html(self.fitting.Description)}</b><br>{_html(b.FittingCode)}<br>{ports}")

    def turn(self, degrees):
        """Turn the fitting by ``degrees`` about the port it is placed by."""
        value = self.angle.value() + degrees
        while value > 180.0:
            value -= 360.0
        while value <= -180.0:
            value += 360.0
        self.angle.setValue(value)  # valueChanged applies it

    def _base_angle(self):
        """For an existing fitting turned in place: the angle that keeps its current heading."""
        if len(self.pick) < 3:
            return 0.0
        bf = _bf()
        before = self.pick[2]
        probe = App.Placement(before)
        self.fitting.Placement = probe
        bf.place_fitting(self.fitting, self.pick[0], self.pick[1])
        minimal = self.fitting.Placement.Rotation
        self.fitting.Placement = before
        delta = before.Rotation.multiply(minimal.inverted())
        axis = self.pick[1] * -1.0
        sign = 1.0 if delta.Axis.dot(axis) >= 0 else -1.0
        return sign * math.degrees(delta.Angle)

    def getStandardButtons(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        self.doc.commitTransaction()
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True

    def reject(self):
        self.doc.abortTransaction()
        if not self.editing:
            try:
                for obj in list(self.fitting.Group) + [self.fitting]:
                    if self.doc.getObject(obj.Name) is not None:
                        self.doc.removeObject(obj.Name)
            except Exception:
                pass
        self.Gui.Control.closeDialog()
        self.doc.recompute()
        return True


# ---------------------------------------------------------------------------------------------
# Commands.


class _Command:
    def __init__(self, name, text, tip, icon=None):
        self.name, self.text, self.tip = name, text, tip
        self.icon = icon or name

    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, self.icon + ".svg"), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        return App.ActiveDocument is not None

    def _busy(self):
        if _gui().Control.activeDialog():
            _message("Another command is open in the task panel. Close it (OK or Cancel) first.")
            return True
        return False


class RoutePathCommand(_Command):
    def Activated(self):
        if self._busy():
            return
        found = selected_paths()
        if found:
            if refuse_copy(found[0]):
                return
            _gui().Control.showDialog(RoutePathPanel(path=found[0]))
            return
        _gui().Control.showDialog(RoutePathPanel(picks=picks_from_selection()))


class AddPointCommand(_Command):
    def Activated(self):
        panel = _state["panel"]
        if panel is not None:
            panel.add_selection()
            return
        found = selected_paths()
        picks = picks_from_selection()
        if not found or not picks:
            _message("Select a route path and the geometry to add (or open the Route Path panel).")
            return
        path = found[0]
        path.Document.openTransaction("Add route point")
        points = list(path.Points) + [p for p, _o, _s in picks]
        attachments = br.attachments_of(path) + [(len(path.Points) + k, o, s) for k, (_p, o, s) in enumerate(picks) if o is not None]
        path.Points = points
        br.set_attachments(path, attachments)
        path.Document.commitTransaction()
        path.Document.recompute()


class ClipCommand(_Command):
    def Activated(self):
        picks = picks_from_selection()
        if not picks:
            _message("Select the geometry the clip sits on (a vertex, a circular edge, a face or a datum).")
            return
        point, obj, sub = picks[0]
        panel = _state["panel"]
        if panel is not None:
            panel.attach_current(obj, sub)
            return
        found = selected_paths()
        if not found:
            _message("Select a route path as well.")
            return
        clip(found[0], obj, sub)


def clip(path, obj, sub):
    """Attach the path point nearest to ``obj``/``sub`` to it. Returns the point index."""
    point = br.attachment_point(obj, sub)
    if point is None or not path.Points:
        return -1
    index = min(range(len(path.Points)), key=lambda i: (path.Points[i] - point).Length)
    path.Document.openTransaction("Clip route")
    attachments = [a for a in br.attachments_of(path) if a[0] != index] + [(index, obj, sub)]
    points = list(path.Points)
    points[index] = point
    path.Points = points
    br.set_attachments(path, attachments)
    path.Document.commitTransaction()
    path.Document.recompute()
    return index


class ItemCommand(_Command):
    def __init__(self, name, text, tip, kind):
        super().__init__(name, text, tip)
        self.kind = kind

    def Activated(self):
        if self._busy():
            return
        selected = [o for o in _gui().Selection.getSelection() if br.kind_of(o) == self.kind]
        if selected:
            if refuse_copy(selected[0]):
                return
            _gui().Control.showDialog(ItemPanel(self.kind, obj=selected[0]))
            return
        path = target_path()
        if path is None:
            _message("Select a route path first (Route Path makes one).")
            return
        _gui().Control.showDialog(ItemPanel(self.kind, path=path))


class BundleCommand(_Command):
    def Activated(self):
        wires = [o for o in _gui().Selection.getSelection() if br.kind_of(o) == br.WIRE]
        if not wires:
            path = target_path()
            wires = [w for w in br.routed(App.ActiveDocument, (br.WIRE,)) if path is None or w.Path == path]
        if not wires:
            _message("Select the wires to bundle.")
            return
        doc = wires[0].Document
        doc.openTransaction("Bundle")
        bundle = br.make_bundle(wires)
        doc.commitTransaction()
        doc.recompute()
        _console(f"{bundle.Label}: {len(wires)} wires, OD {bundle.OD.Value:.2f} mm")


class HarnessCommand(_Command):
    """Group the selected wires, tubes, hoses and connector links in a Harness container (ROU-06)."""

    def Activated(self):
        import bruceworx_harness as bh

        doc = App.ActiveDocument
        chosen = [o for o in _gui().Selection.getSelection() if not bh.is_harness(o)]
        if not chosen:
            _message("Select the wires, tubes or hoses (and the connector links) that make up the harness.")
            return
        doc.openTransaction("Harness")
        try:
            container = bh.make_harness(doc, chosen)
        except Exception as exc:
            doc.abortTransaction()
            _message(str(exc))
            return
        doc.commitTransaction()
        _console(f"{container.Label}: {container.HarnessType}, {len(bh.contents(container))} objects")


class MakeVaultItemCommand(_Command):
    """Save the selected Harness out as its own vault item and link it into the assembly (ROU-06)."""

    def Activated(self):
        import bruceworx_harness as bh
        import bruceworx_vaultclient as vc

        doc = App.ActiveDocument
        selected = [bh.harness_of(o) for o in _gui().Selection.getSelection()]
        found = [h for h in selected if h is not None] or bh.harnesses(doc)
        if len(found) != 1:
            _message("Select the Harness to save as a vault item.")
            return
        container = found[0]
        s = vc.session()
        if s is None:
            _message("Open a vault first (Preferences > Vector Labs, or the Start page).")
            return
        _QtCore, _QtGui, QtWidgets = _qt()
        text, ok = QtWidgets.QInputDialog.getText(_gui().getMainWindow(), "Make vault item", "Harness description:",
                                                  text=container.Label)
        if not ok or not text.strip():
            return
        try:
            pn, link = bh.make_vault_item(container, s, text.strip())
        except Exception as exc:
            _message(str(exc), "Make vault item")
            return
        _console(f"{text.strip()}: vault item {pn}, linked in {link.Document.Label}")


class FromToCommand(_Command):
    def Activated(self):
        doc = App.ActiveDocument
        if not br.routed(doc, (br.WIRE,)):
            _message("No wires in this document.")
            return
        _ask_and_write(doc, f"{doc.Label}-from-to.csv", export_from_to)


class CutListCommand(_Command):
    def Activated(self):
        doc = App.ActiveDocument
        if not br.routed(doc, (br.PIPE, br.HOSE)):
            _message("No pipes or hoses in this document.")
            return
        _ask_and_write(doc, f"{doc.Label}-cut-list.csv", export_cut_list)


class TerminalsCommand(_Command):
    def Activated(self):
        doc = App.ActiveDocument
        if not br.terminal_list(doc):
            _message("No terminals, seals or connector accessories: route a wire to a pin with a terminal P/N first.")
            return
        _ask_and_write(doc, f"{doc.Label}-terminals.csv", export_terminals)


class BendRulesCommand(_Command):
    def IsActive(self):
        return True

    def Activated(self):
        BendRulesDialog(_gui().getMainWindow()).exec_()


class FrameCommand(_Command):
    def Activated(self):
        if self._busy():
            return
        bp = _bp()
        selected = [o for o in _gui().Selection.getSelection() if o.TypeId in bp.LCS_TYPES]
        if selected:
            _gui().Control.showDialog(FramePanel(frame=selected[0]))
            return
        sel = _gui().Selection.getSelectionEx("", 0)
        pick = next(((s.Object, sub) for s in sel for sub in (s.SubElementNames or [""])
                     if not br.is_path(s.Object) and not br.is_routed(s.Object)), None)
        if pick is None:
            _message("Select a face, a hole edge or a vertex of the connector or fitting (or an LCS to edit).")
            return
        found = bp.frame_at(*pick)
        if found is not None:
            _gui().Control.showDialog(FramePanel(frame=found[0]))
            return
        _gui().Control.showDialog(FramePanel(pick=pick))


class FittingCommand(_Command):
    def Activated(self):
        if self._busy():
            return
        bf = _bf()
        chosen = [o for o in _gui().Selection.getSelection() if bf.is_fitting(o) or bf.is_body(o)]
        sel = _gui().Selection.getSelectionEx("", 0)
        pick = next(((s.Object, sub) for s in sel for sub in (s.SubElementNames or [""])
                     if sub and not br.is_path(s.Object) and not br.is_routed(s.Object)), None)
        if chosen and pick is None:
            fitting = chosen[0] if bf.is_fitting(chosen[0]) else bf.container_of(chosen[0])
            _gui().Control.showDialog(FittingPanel(fitting=fitting))
            return
        if pick is None:
            frames = [o for o in _gui().Selection.getSelection() if _bp().is_frame(o)]
            pick = (frames[0], "") if frames else None
        _gui().Control.showDialog(FittingPanel(pick=pick))
        clear_selection_later()


def install():
    if _state["installed"]:
        return
    Gui = _gui()
    import bruceworx_harness

    bruceworx_harness.install_guard()
    Gui.addCommand("BruceWorx_RoutePath", RoutePathCommand(
        "BruceWorx_RoutePath", "Route Path",
        "A centreline for wires, pipes and hoses: pick vertices, circle centres, faces or datums in the 3D view. "
        "Select an existing route path to edit it."))
    Gui.addCommand("BruceWorx_RouteAddPoint", AddPointCommand(
        "BruceWorx_RouteAddPoint", "Add Point", "Append the selected geometry to the open Route Path panel or the selected path."))
    Gui.addCommand("BruceWorx_RouteClip", ClipCommand(
        "BruceWorx_RouteClip", "Clip", "Attach the nearest point of the selected path to the selected geometry, so the route follows it."))
    Gui.addCommand("BruceWorx_RouteWire", ItemCommand(
        "BruceWorx_RouteWire", "Wire", "An electrical wire on the selected path: gauge, insulation, colour, signal, from/to, slack.", br.WIRE))
    Gui.addCommand("BruceWorx_RoutePipe", ItemCommand(
        "BruceWorx_RoutePipe", "Pipe", "A rigid tube on the selected path: size, wall, bend radius, cut length and bend table.", br.PIPE))
    Gui.addCommand("BruceWorx_RouteHose", ItemCommand(
        "BruceWorx_RouteHose", "Hose", "A flexible hose on the selected path: ID, OD, minimum bend radius, slack.", br.HOSE))
    Gui.addCommand("BruceWorx_RouteBundle", BundleCommand(
        "BruceWorx_RouteBundle", "Bundle", "Group the selected wires (or every wire on the path) into a bundle with a combined OD."))
    Gui.addCommand("BruceWorx_RouteHarness", HarnessCommand(
        "BruceWorx_RouteHarness", "Harness",
        "Group the selected wires, tubes, hoses and connector links in a Harness container (Cable assembly or Tube "
        "assembly) in the assembly; its stock material already counts on the assembly BOM."))
    Gui.addCommand("BruceWorx_RouteMakeVaultItem", MakeVaultItemCommand(
        "BruceWorx_RouteMakeVaultItem", "Make vault item",
        "Save the selected Harness as its own vault item under a new part number (category Harnesses) and link it "
        "into the assembly. Its paths keep following the parts of this assembly while both files are open."))
    Gui.addCommand("BruceWorx_RouteFromTo", FromToCommand(
        "BruceWorx_RouteFromTo", "From-to list", "CSV: wire, signal, gauge, colour, from, to, length."))
    Gui.addCommand("BruceWorx_RouteCutList", CutListCommand(
        "BruceWorx_RouteCutList", "Cut list", "CSV: pipe or hose, size, cut length, bend table."))
    Gui.addCommand("BruceWorx_RouteTerminals", TerminalsCommand(
        "BruceWorx_RouteTerminals", "Terminal list",
        "CSV: the terminals, seals and connector accessories to order, with manufacturer, MPN and Standard Library item."))
    Gui.addCommand("BruceWorx_RouteBendRules", BendRulesCommand(
        "BruceWorx_RouteBendRules", "Bend Rules",
        "Default minimum bend radius per wire, hose and tube (a preset, a multiple of OD or a fixed radius) and the tube CLR."))
    Gui.addCommand("BruceWorx_RouteFrame", FrameCommand(
        "BruceWorx_RouteFrame", "Pin / Port",
        "A pin or port frame on a connector or fitting: pick a face, a hole edge or a vertex (+Z out of it), "
        "or select an LCS to edit: name, gauge range, terminal and seal P/N, port type and size, straight length."))
    Gui.addCommand("BruceWorx_RouteFitting", FittingCommand(
        "BruceWorx_RouteFitting", "Fitting",
        "A push-to-connect, barb, NPT/BSPP or compression fitting from the size tables, placed on the selected face, "
        "hole edge or port; its ports are frames a route path attaches to."))
    try:
        import bruceworx_browser

        if br.row_badges not in bruceworx_browser.ROW_EXTRAS:
            bruceworx_browser.ROW_EXTRAS.append(br.row_badges)
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx routing: Browser badge not installed: {exc}\n")
    _state["installed"] = True
