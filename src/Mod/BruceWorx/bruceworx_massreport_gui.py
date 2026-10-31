# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inspect > Mass & Balance: the Mass Report and Drone Balance commands and their panels (ROB-03, ROB-04).

Both open a task panel on the active document (the selected assembly, else the active one, else the document).
Mass Report: the table, totals, CG and inertia; Export CSV; a red plumb line and sphere at the CG on the model.
Drone Balance: mark motors (select a motor part, Mark as motor), attach a thrust CSV to a motor, type the pack
capacity, and read thrust to weight, hover, CG offset, per-motor shares and flight time with amber/red checks.
The maths is in bruceworx_massreport.py and bruceworx/balance.py.
"""

from __future__ import annotations

import os

import bruceworx_massreport as mr
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY_NAME = "BruceWorxCG"
CG_RGB = (0.86, 0.1, 0.1)
LEVEL_COLOURS = {"ok": "#2e8b57", "amber": "#d98200", "red": "#c62828"}
_state = {"installed": False, "panel": None}


def _message(text):
    from PySide import QtWidgets

    QtWidgets.QMessageBox.information(None, "Mass & Balance", text)


def _selection():
    import FreeCADGui as Gui

    return [o for o in Gui.Selection.getSelection()]


def _view(doc):
    import FreeCADGui as Gui

    gdoc = Gui.getDocument(doc.Name) if doc is not None else None
    view = gdoc.activeView() if gdoc is not None else None
    return view if view is not None and hasattr(view, "getSceneGraph") else None


# ----- the CG marker on the canvas ---------------------------------------------------------------------------------


def clear_marker(view):
    if view is None:
        return
    root = view.getSceneGraph()
    for i in range(root.getNumChildren() - 1, -1, -1):
        if root.getChild(i).getName().getString() == OVERLAY_NAME:
            root.removeChild(i)


def show_marker(view, report):
    """A red sphere at the CG and a plumb line through it; returns False without a view or a CG."""
    from pivy import coin

    clear_marker(view)
    if view is None or report.cg is None:
        return False
    span = max((((report.cg[0] - i.centre[0]) ** 2 + (report.cg[1] - i.centre[1]) ** 2
                 + (report.cg[2] - i.centre[2]) ** 2) ** 0.5 for i in report.instances), default=10.0)
    span = max(span, 10.0)
    top = coin.SoAnnotation()
    top.setName(OVERLAY_NAME)
    pick = coin.SoPickStyle()
    pick.style = coin.SoPickStyle.UNPICKABLE
    top.addChild(pick)
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*CG_RGB)
    mat.emissiveColor.setValue(*CG_RGB)
    top.addChild(mat)
    style = coin.SoDrawStyle()
    style.lineWidth = 3
    top.addChild(style)
    x, y, z = report.cg
    line = coin.SoSeparator()
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 2, [(x, y, z - span * 1.2), (x, y, z + span * 1.2)])
    line.addChild(coords)
    ls = coin.SoLineSet()
    ls.numVertices.setValue(2)
    line.addChild(ls)
    top.addChild(line)
    ball = coin.SoSeparator()
    tr = coin.SoTranslation()
    tr.translation.setValue(x, y, z)
    ball.addChild(tr)
    sphere = coin.SoSphere()
    sphere.radius.setValue(span * 0.06)
    ball.addChild(sphere)
    top.addChild(ball)
    view.getSceneGraph().addChild(top)
    return True


# ----- panels ------------------------------------------------------------------------------------------------------


def _fmt(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def _table(columns):
    from PySide import QtWidgets

    t = QtWidgets.QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.verticalHeader().hide()
    t.horizontalHeader().setStretchLastSection(True)
    t.setMinimumHeight(160)
    return t


def _cell(text, align_right=False, colour=None):
    from PySide import QtCore, QtGui, QtWidgets

    item = QtWidgets.QTableWidgetItem(text)
    if align_right:
        item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    if colour:
        item.setForeground(QtGui.QBrush(QtGui.QColor(colour)))
    return item


class MassReportPanel:
    """Task panel: the mass report of the document's assembly."""

    def __init__(self, doc, objects=()):
        from PySide import QtCore, QtWidgets

        self.doc, self.objects, self.report = doc, list(objects), None
        w = self.form = QtWidgets.QWidget()
        w.setWindowTitle("Mass Report")
        lay = QtWidgets.QVBoxLayout(w)
        self.title = QtWidgets.QLabel()
        lay.addWidget(self.title)
        self.table = _table(["Item", "Qty", "Mass source", "Mass (g)", "Share"])
        lay.addWidget(self.table)
        self.totals = QtWidgets.QLabel()
        self.totals.setWordWrap(True)
        self.totals.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.totals)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Refresh", self.refresh), ("Export CSV…", self.export), ("Show CG", self.mark)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot(fn))
            row.addWidget(b)
        lay.addLayout(row)
        self.refresh()

    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        import FreeCADGui as Gui

        clear_marker(_view(self.doc))
        _state["panel"] = None
        Gui.Control.closeDialog()
        return True

    accept = reject

    def refresh(self):
        report = self.report = mr.build_report(self.doc, self.objects)
        self.title.setText(f"<b>{report.root_label}</b>: {sum(r.qty for r in report.rows)} items")
        self.table.setRowCount(len(report.rows))
        for i, r in enumerate(report.rows):
            colour = LEVEL_COLOURS["amber"] if r.source in ("none",) or r.missing else None
            self.table.setItem(i, 0, _cell(r.label))
            self.table.setItem(i, 1, _cell(str(r.qty), True))
            self.table.setItem(i, 2, _cell(mr.SOURCES.get(r.source, r.source), colour=colour))
            self.table.setItem(i, 3, _cell(_fmt(r.mass * mr.G_PER_KG), True))
            self.table.setItem(i, 4, _cell(f"{r.share * 100:.1f} %", True))
        self.table.resizeColumnsToContents()
        lines = [f"<b>Total {report.mass * mr.G_PER_KG:.2f} g</b>"]
        if report.cg:
            lines.append("CG (mm): " + ", ".join(f"{v:.2f}" for v in report.cg))
            for label, t in (("about CG", report.tensor), ("about origin", report.tensor_origin())):
                g = mr.G_PER_KG
                lines.append(f"Inertia {label} (g mm²): Ixx {t[0][0] * g:.0f}, Iyy {t[1][1] * g:.0f}, "
                             f"Izz {t[2][2] * g:.0f}, Ixy {t[0][1] * g:.0f}, Ixz {t[0][2] * g:.0f}, "
                             f"Iyz {t[1][2] * g:.0f}")
        if report.missing:
            lines.append(f"<span style='color:{LEVEL_COLOURS['amber']}'>{report.missing} without a mass: give them a "
                         "material, a MassOverride, DatasheetMass or MeasuredMass.</span>")
        self.totals.setText("<br>".join(lines))

    def export(self):
        from PySide import QtWidgets

        if self.report is None:
            return
        folder = os.path.dirname(getattr(self.doc, "FileName", "") or "") or os.path.expanduser("~")
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self.form, "Export mass report", os.path.join(folder, f"{self.report.root_label}-mass.csv"),
            "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(self.report.csv_text())
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self.form, "Export CSV", f"Could not write {path}:\n{exc}")

    def mark(self):
        if self.report is not None and not show_marker(_view(self.doc), self.report):
            _message("There is no centre of gravity to show yet.")


class BalancePanel:
    """Task panel: drone balance and thrust to weight."""

    def __init__(self, doc, objects=()):
        from PySide import QtWidgets

        self.doc, self.objects, self.report, self.result = doc, list(objects), None, None
        self.motors = []
        w = self.form = QtWidgets.QWidget()
        w.setWindowTitle("Drone Balance")
        lay = QtWidgets.QVBoxLayout(w)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.table = _table(["Motor", "Thrust table", "Max (g)", "Hover (g)", "Throttle", "Current (A)"])
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Mark selected as motor", self.mark_motor), ("Attach thrust CSV…", self.attach)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot(fn))
            row.addWidget(b)
        lay.addLayout(row)
        cap = QtWidgets.QHBoxLayout()
        cap.addWidget(QtWidgets.QLabel("Pack capacity (mAh, 0 = from the battery part)"))
        self.capacity = QtWidgets.QDoubleSpinBox()
        self.capacity.setRange(0, 100000)
        self.capacity.setDecimals(0)
        self.capacity.setSingleStep(100)
        self.capacity.valueChanged.connect(slot(self.refresh))
        cap.addWidget(self.capacity)
        lay.addLayout(cap)
        self.checks = QtWidgets.QLabel()
        self.checks.setWordWrap(True)
        lay.addWidget(self.checks)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Refresh", self.refresh), ("Show CG", self.show_cg)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot(fn))
            row.addWidget(b)
        lay.addLayout(row)
        self.refresh()

    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        import FreeCADGui as Gui

        clear_marker(_view(self.doc))
        _state["panel"] = None
        Gui.Control.closeDialog()
        return True

    accept = reject

    def refresh(self):
        from bruceworx import balance

        self.report = report = mr.build_report(self.doc, self.objects)
        self.motors = mr.motors_of(report)
        typed = self.capacity.value()
        notes, self.result = [], None
        try:
            self.result, notes = mr.balance_of(report, typed if typed > 0 else None)
        except balance.ThrustError as exc:
            notes = [str(exc)]
        res = self.result
        head = f"<b>{report.root_label}</b>: {report.mass * mr.G_PER_KG:.1f} g"
        if report.cg:
            head += ", CG " + ", ".join(f"{v:.2f}" for v in report.cg) + " mm"
        self.summary.setText(head)
        self.table.setRowCount(len(self.motors))
        for i, (inst, _axis, path) in enumerate(self.motors):
            m = res.motors[i] if res is not None else None
            self.table.setItem(i, 0, _cell(f"{inst.label} #{i + 1}"))
            self.table.setItem(i, 1, _cell(os.path.basename(path) if path else "(none)",
                                           colour=None if path else LEVEL_COLOURS["amber"]))
            self.table.setItem(i, 2, _cell(_fmt(m.max_thrust, 0) if m else "-", True))
            self.table.setItem(i, 3, _cell(_fmt(m.share, 1) if m else "-", True))
            self.table.setItem(i, 4, _cell(_fmt(m.throttle, 0) + " %" if m and m.throttle is not None else "-", True))
            self.table.setItem(i, 5, _cell(_fmt(m.current, 1) if m else "-", True))
        self.table.resizeColumnsToContents()
        lines = []
        if res is not None:
            lines.append(f"T/W {_fmt(res.tw)}, hover throttle {_fmt(res.hover_throttle, 0)} %, hover current "
                         f"{_fmt(res.hover_current, 1)} A, flight time {_fmt(res.flight_min, 1)} min (0.8 C / I)")
            for c in res.checks:
                lines.append(f"<span style='color:{LEVEL_COLOURS[c.level]}'><b>{c.level.upper()}</b> {c.name}: "
                             f"{c.text}</span>")
        lines += [f"<span style='color:{LEVEL_COLOURS['amber']}'>{n}</span>" for n in notes]
        self.checks.setText("<br>".join(lines))

    def _targets(self):
        out = []
        for o in _selection():
            try:
                out.append(o.getLinkedObject(True) or o)
            except Exception:  # noqa: BLE001
                out.append(o)
        return out

    def mark_motor(self):
        targets = self._targets()
        if not targets:
            _message("Select a motor part first.")
            return
        self.doc.openTransaction("Mark motor")
        for t in targets:
            mr.mark_motor(t)
        self.doc.commitTransaction()
        self.refresh()

    def attach(self):
        from PySide import QtWidgets

        row = self.table.currentRow()
        targets = ([self.motors[row][0].target] if 0 <= row < len(self.motors)
                   else [t for t in self._targets() if mr.THRUST_AXIS in t.PropertiesList])
        if not targets:
            _message("Pick a motor row (or select a marked motor), then attach its thrust table.")
            return
        folder = os.path.dirname(getattr(self.doc, "FileName", "") or "") or os.path.expanduser("~")
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self.form, "Thrust table (CSV)", folder,
                                                         "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            mr.read_table(path)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self.form, "Thrust table", f"Could not read {path}:\n{exc}")
            return
        self.doc.openTransaction("Attach thrust table")
        for t in targets:
            mr.mark_motor(t, table_path=path)
        self.doc.commitTransaction()
        self.refresh()

    def show_cg(self):
        if self.report is not None and not show_marker(_view(self.doc), self.report):
            _message("There is no centre of gravity to show yet.")


# ----- commands ----------------------------------------------------------------------------------------------------


def open_panel(kind):
    import FreeCAD
    import FreeCADGui as Gui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    if _state["panel"] is not None:
        _state["panel"].reject()
    if Gui.Control.activeDialog():
        _message("Close the open task panel first.")
        return None
    panel = (MassReportPanel if kind == "mass" else BalancePanel)(doc, _selection())
    _state["panel"] = panel
    Gui.Control.showDialog(panel)
    return panel


class _Command:
    def __init__(self, kind, icon, text, tip):
        self.kind, self.icon, self.text, self.tip = kind, icon, text, tip

    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, self.icon), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        open_panel(self.kind)


def install():
    """Register the commands (InitGui)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_MassReport", _Command(
        "mass", "BruceWorx_MassProperties.svg", "Mass Report",
        "Every part of the assembly with quantity, mass source, mass and share, the total, the centre of gravity "
        "and the inertia; export CSV and show the CG on the model."))
    Gui.addCommand("BruceWorx_DroneBalance", _Command(
        "balance", "BruceWorx_DroneBalance.svg", "Drone Balance",
        "Thrust to weight, hover throttle and current, flight time, CG offset from the thrust centre and each "
        "motor's hover share, with checks."))
    _state["installed"] = True
