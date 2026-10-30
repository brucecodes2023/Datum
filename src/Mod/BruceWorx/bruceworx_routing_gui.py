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
* ``BruceWorx_RouteBendRules``: the Bend Rules dialog (ROU-02, 30 Oct), the default minimum bend
  rule per wire, hose and tube and the tube CLR, kept in the preference group
  ``Mod/BruceWorx/Routing``. Each item panel can override the rule for its item.

Every Qt signal to a bound method goes through ``slot`` (bruceworx_slots). The selection observer
is not a Qt signal: FreeCAD calls its methods directly.
"""

import os

import FreeCAD as App

import bruceworx_routing as br
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
RED = "<span style='color:#c0392b'>{}</span>"
COMMANDS = ("BruceWorx_RoutePath", "BruceWorx_RouteAddPoint", "BruceWorx_RouteClip", "BruceWorx_RouteWire",
            "BruceWorx_RoutePipe", "BruceWorx_RouteHose", "BruceWorx_RouteBundle", "BruceWorx_RouteFromTo",
            "BruceWorx_RouteCutList", "BruceWorx_RouteBendRules")
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
        return form

    def _load(self):
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
        path.Points = [p for p, _o, _s in self.points]
        br.set_attachments(path, [(i, o, s) for i, (_p, o, s) in enumerate(self.points) if o is not None])
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

    @staticmethod
    def summary(path):
        """One line: length, bends, tightest radius, the check."""
        text = f"Length {path.Length.Value:.1f} mm · {path.Bends} bend{'s' if path.Bends != 1 else ''}"
        if path.MinRadius.Value > 0:
            text += f" · tightest R {path.MinRadius.Value:.1f} mm"
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
        self._rule_rows(QtWidgets, grid)
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

    def _show_rule(self):
        """The rule widgets from the object (after a recompute the radius is the rule's)."""
        building, self._building = self._building, True
        try:
            self.rule.setCurrentText(self.obj.BendRule)
            self.factor.setValue(float(self.obj.BendFactor))
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


def _ask_and_write(doc, name, writer):
    _QtCore, _QtGui, QtWidgets = _qt()
    start = os.path.join(os.path.expanduser("~/Desktop"), name)
    path, _f = QtWidgets.QFileDialog.getSaveFileName(_gui().getMainWindow(), "Export CSV", start, "CSV (*.csv)")
    if path:
        writer(doc, path)
    return path


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


class BendRulesCommand(_Command):
    def IsActive(self):
        return True

    def Activated(self):
        BendRulesDialog(_gui().getMainWindow()).exec_()


def install():
    if _state["installed"]:
        return
    Gui = _gui()
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
    Gui.addCommand("BruceWorx_RouteFromTo", FromToCommand(
        "BruceWorx_RouteFromTo", "From-to list", "CSV: wire, signal, gauge, colour, from, to, length."))
    Gui.addCommand("BruceWorx_RouteCutList", CutListCommand(
        "BruceWorx_RouteCutList", "Cut list", "CSV: pipe or hose, size, cut length, bend table."))
    Gui.addCommand("BruceWorx_RouteBendRules", BendRulesCommand(
        "BruceWorx_RouteBendRules", "Bend Rules",
        "Default minimum bend radius per wire, hose and tube (a preset, a multiple of OD or a fixed radius) and the tube CLR."))
    try:
        import bruceworx_browser

        if br.row_badges not in bruceworx_browser.ROW_EXTRAS:
            bruceworx_browser.ROW_EXTRAS.append(br.row_badges)
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx routing: Browser badge not installed: {exc}\n")
    _state["installed"] = True
