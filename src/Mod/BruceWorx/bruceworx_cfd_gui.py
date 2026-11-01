# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Airflow panels (SIM-06 phase 1): the External flow settings in New Study and the study panel.

* ``NewStudyBox``: speed, angle of attack, reference area, mesh preset (and the air and turbulence) in the
  New Study panel when the type is External flow.
* ``AirflowPanel``: the study panel. Settings, Pre-check (with Install OpenFOAM... when it is missing),
  Run / Stop with a live residual and force plot, then Load results (foamToVTK into FreeCAD's FEM post pipeline
  with a cut plane), the forces table, Open in ParaView and Report.

Every signal connection goes through bruceworx_slots.slot.
"""

from __future__ import annotations

import json
import os
import time

import bruceworx_cfd as cfd
import bruceworx_cfd_paraview as pvm
import bruceworx_sim as sim
import bruceworx_sim_events as events
from bruceworx_slots import slot

_classes = None


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


def _gui():
    import bruceworx_sim_gui as g

    return g


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    g = _gui()
    gc = g.classes()
    Panel = gc["Panel"]

    def hint(text=""):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        lab.setTextFormat(QtCore.Qt.RichText)
        return lab

    def button(text, handler, tip=""):
        b = QtWidgets.QPushButton(text)
        if tip:
            b.setToolTip(tip)
        receiver = slot(handler)
        b.clicked.connect(lambda *_a: receiver())
        return b

    def spin(value, low, high, decimals, suffix="", step=None):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(low, high)
        s.setDecimals(decimals)
        if suffix:
            s.setSuffix(suffix)
        if step:
            s.setSingleStep(step)
        s.setValue(float(value))
        return s

    def preset_combo(current):
        c = QtWidgets.QComboBox()
        for key, p in cfd.PRESETS.items():
            c.addItem(p["label"], key)
        c.setCurrentIndex(max(0, c.findData(current)))
        return c

    def turbulence_combo(current):
        c = QtWidgets.QComboBox()
        c.addItem("k-omega SST (turbulent)", "kOmegaSST")
        c.addItem("Laminar (slow, small flows)", "laminar")
        c.setCurrentIndex(max(0, c.findData(current)))
        return c

    def farfield_combo(current):
        c = QtWidgets.QComboBox()
        c.addItem("Slip walls", "slip")
        c.addItem("Symmetry planes", "symmetry")
        c.setCurrentIndex(max(0, c.findData(current)))
        return c

    class NewStudyBox:
        """The External flow part of New Study."""

        def __init__(self):
            self.widget = QtWidgets.QWidget()
            lay = QtWidgets.QFormLayout(self.widget)
            lay.setContentsMargins(0, 0, 0, 0)
            self.speed = spin(10.0, 0.001, 100.0, 4, " m/s", 1.0)
            self.aoa = spin(0.0, -90.0, 90.0, 1, " °", 1.0)
            self.area = spin(0.0, 0.0, 1.0e6, 6, " m²")
            self.area.setSpecialValueText("frontal area of the bounding box")
            self.preset = preset_combo(cfd.DEFAULT_PRESET)
            self.turbulence = turbulence_combo("kOmegaSST")
            self.temperature = spin(20.0, -60.0, 60.0, 1, " °C")
            self.altitude = spin(0.0, 0.0, 10000.0, 0, " m")
            lay.addRow("Speed", self.speed)
            lay.addRow("Angle of attack", self.aoa)
            lay.addRow("Reference area", self.area)
            lay.addRow("Mesh", self.preset)
            lay.addRow("Turbulence", self.turbulence)
            lay.addRow("Air temperature", self.temperature)
            lay.addRow("Altitude", self.altitude)
            self.layout = lay
            self.diameter = spin(0.0, 0.0, 2000.0, 1, " mm", 1.0)
            self.diameter.setSpecialValueText("enter the propeller diameter")
            self.diameter.setToolTip("One actuator disk of this diameter on every motor marked with a ThrustAxis")
            self.typed = spin(0.0, 0.0, 10000.0, 3, " N", 0.1)
            self.typed.setSpecialValueText("hover share from Mass & Balance")
            self.typed.setToolTip("Thrust of every motor; blank takes each motor's hover share of the weight")
            self.offset = spin(0.0, 0.0, 2000.0, 1, " mm", 1.0)
            self.offset.setSpecialValueText("just above the motor part")
            self.offset.setToolTip("Distance from the motor's centre along its ThrustAxis to the disk")
            self.hub = spin(0.0, 0.0, 1000.0, 1, " mm", 1.0)
            self.hub.setSpecialValueText("20 % of the diameter")
            self.hub.setToolTip("Hub of every propeller that does not carry its own HubDiameter: the disk is a ring, "
                                "no air is moved inside the hub")
            lay.addRow("Propeller diameter", self.diameter)
            lay.addRow("Hub diameter", self.hub)
            lay.addRow("Thrust per motor", self.typed)
            lay.addRow("Disk offset", self.offset)
            self.kind = cfd.AIR_KIND
            self.set_kind(self.kind)

        def set_kind(self, kind):
            """External flow shows the angle and the reference area; prop wash the propeller fields (speed 0 = hover)."""
            wash = kind == cfd.WASH_KIND
            self.speed.setMinimum(0.0 if wash else 0.001)
            if wash != (self.kind == cfd.WASH_KIND):
                self.speed.setValue(0.0 if wash else 10.0)
            self.kind = kind
            self.speed.setSpecialValueText("0 (hover)" if wash else "")
            for widget, on in ((self.aoa, not wash), (self.area, not wash), (self.diameter, wash), (self.hub, wash),
                               (self.typed, wash), (self.offset, wash)):
                widget.setVisible(on)
                label = self.layout.labelForField(widget)
                if label is not None:
                    label.setVisible(on)

        def options(self):
            out = {"FlowSpeed": self.speed.value(), "AngleOfAttack": self.aoa.value(),
                   "ReferenceArea": self.area.value(), "MeshPreset": self.preset.currentData(),
                   "Turbulence": self.turbulence.currentData(), "AirTemperature": self.temperature.value(),
                   "AirAltitude": self.altitude.value()}
            if self.kind == cfd.WASH_KIND:
                out.update({"PropDiameter": self.diameter.value(), "HubDiameter": self.hub.value(), "TypedThrust": self.typed.value(),
                            "DiskOffset": self.offset.value()})
            return out

    class PlotWidget(QtWidgets.QWidget):
        """Lines against the iteration; log scale for the residuals."""

        COLORS = ("#2d6cdf", "#d9381e", "#2c9c5a", "#c58a00", "#7a4bc2", "#008b8b")

        def __init__(self, title, log=False):
            super().__init__()
            self.title, self.log = title, log
            self.series = {}
            self.setMinimumHeight(110)

        def set_series(self, series):
            self.series = {k: v for k, v in series.items() if v}
            self.update()

        def paintEvent(self, _event):
            import math

            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            r = self.rect().adjusted(34, 16, -6, -16)
            pal = self.palette()
            p.setPen(pal.color(QtGui.QPalette.Mid))
            p.drawRect(r)
            p.setPen(pal.color(QtGui.QPalette.WindowText))
            p.drawText(self.rect().adjusted(4, 0, 0, 0), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, self.title)
            pts = [(x, y) for s in self.series.values() for x, y in s if (y > 0 or not self.log)]
            if not pts:
                p.end()
                return
            fy = (lambda y: math.log10(max(y, 1e-30))) if self.log else (lambda y: y)
            x0, x1 = min(x for x, _ in pts), max(x for x, _ in pts)
            vals = [fy(y) for _, y in pts]
            y0, y1 = min(vals), max(vals)
            if y1 - y0 < 1e-12:
                y0, y1 = y0 - 1, y1 + 1
            if x1 - x0 < 1:
                x1 = x0 + 1
            tx = lambda x: r.left() + (x - x0) / (x1 - x0) * r.width()
            ty = lambda y: r.bottom() - (fy(y) - y0) / (y1 - y0) * r.height()
            p.drawText(2, r.top() + 8, f"{y1:.1f}" if self.log else f"{y1:.3g}")
            p.drawText(2, r.bottom(), f"{y0:.1f}" if self.log else f"{y0:.3g}")
            p.drawText(r.left(), self.height() - 2, f"{int(x0)}")
            p.drawText(r.right() - 30, self.height() - 2, f"{int(x1)}")
            for i, (name, s) in enumerate(self.series.items()):
                p.setPen(QtGui.QPen(QtGui.QColor(self.COLORS[i % len(self.COLORS)]), 1.4))
                path = QtGui.QPainterPath()
                first = True
                for x, y in s:
                    if self.log and y <= 0:
                        continue
                    if first:
                        path.moveTo(tx(x), ty(y))
                        first = False
                    else:
                        path.lineTo(tx(x), ty(y))
                p.drawPath(path)
                p.drawText(r.left() + 4 + i * 44, r.top() + 12, name)
            p.end()

    class AirflowPanel(Panel):
        """Settings, pre-check, run with live plots, results and report of one External flow study."""

        title = "Airflow study"

        def __init__(self, analysis):
            super().__init__()
            self.analysis = analysis
            self.doc = analysis.Document
            cfd.ensure_properties(analysis)
            self.runner = None
            self.reader = None
            self.folder = ""
            self.timer = QtCore.QTimer()
            self.timer.setInterval(1000)
            self.timer.timeout.connect(slot(self._tick))
            self.pv_job = None  # background ParaView pictures (bruceworx_cfd_paraview.RenderJob)
            self.pv_timer = QtCore.QTimer()
            self.pv_timer.setInterval(700)
            self.pv_timer.timeout.connect(slot(self._poll_pictures))
            self._streams = None  # (FemPostPipeline, seed count) of the streamlines in the 3D view
            self.stream_job = None  # the worker process tracing them (bruceworx_cfd_paraview.StreamJob)
            self.stream_timer = QtCore.QTimer()
            self.stream_timer.setInterval(150)
            self.stream_timer.timeout.connect(slot(self._poll_streamlines))
            self.form = [self._build_setup(), self._build_run(), self._build_results()]
            self._show_summary()
            self.check()

        # --- boxes
        def _box(self, title):
            w = QtWidgets.QWidget()
            w.setWindowTitle(title)
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(8, 6, 8, 8)
            lay.setSpacing(6)
            return w

        def _build_setup(self):
            a = self.analysis
            w = self._box(f"Airflow: {sim.summary_name(a)}")
            form = QtWidgets.QFormLayout()
            self.speed = spin(a.FlowSpeed, 0.001, 100.0, 4, " m/s", 1.0)
            self.aoa = spin(a.AngleOfAttack, -90, 90, 1, " °", 1.0)
            self.area = spin(a.ReferenceArea, 0, 1e6, 6, " m²")
            self.area.setSpecialValueText("frontal area of the bounding box")
            self.length = spin(a.ReferenceLength, 0, 1e6, 4, " m")
            self.length.setSpecialValueText("model length along the flow")
            self.preset = preset_combo(a.MeshPreset)
            self.turbulence = turbulence_combo(a.Turbulence)
            self.farfield = farfield_combo(a.FarField)
            self.temperature = spin(a.AirTemperature, -60, 60, 1, " °C")
            self.altitude = spin(a.AirAltitude, 0, 10000, 0, " m")
            self.up = spin(a.DomainUpstream, 1, 50, 1, " L")
            self.down = spin(a.DomainDownstream, 1, 100, 1, " L")
            self.lat = spin(a.DomainLateral, 1, 50, 1, " L")
            self.up.setToolTip("Domain extent in characteristic lengths L (the largest size of the model)")
            for label, widget in (("Speed", self.speed), ("Angle of attack", self.aoa), ("Reference area", self.area),
                                  ("Reference length", self.length), ("Mesh", self.preset), ("Turbulence", self.turbulence),
                                  ("Air temperature", self.temperature), ("Altitude", self.altitude),
                                  ("Far field", self.farfield), ("Upstream", self.up), ("Downstream", self.down),
                                  ("Sideways", self.lat)):
                form.addRow(label, widget)
                signal = widget.currentIndexChanged if isinstance(widget, QtWidgets.QComboBox) else widget.valueChanged
                signal.connect(slot(self._settings_changed))
            self.wash_fields = []
            if cfd.is_wash(a):
                cfd.ensure_properties(a)
                self.diameter = spin(a.PropDiameter, 0, 2000, 1, " mm", 1.0)
                self.diameter.setSpecialValueText("enter the propeller diameter")
                self.hub = spin(a.HubDiameter, 0, 1000, 1, " mm", 1.0)
                self.hub.setSpecialValueText("20 % of the diameter")
                self.typed = spin(a.TypedThrust, 0, 10000, 3, " N", 0.1)
                self.typed.setSpecialValueText("hover share from Mass & Balance")
                self.offset = spin(a.DiskOffset, 0, 2000, 1, " mm", 1.0)
                self.offset.setSpecialValueText("just above the motor part")
                self.wake = spin(a.DomainWake, 1, 100, 1, " L")
                self.wake.setToolTip("Domain extent on the wake side, in characteristic lengths L")
                for label, widget in (("Propeller diameter", self.diameter), ("Hub diameter", self.hub),
                                      ("Thrust per motor", self.typed),
                                      ("Disk offset", self.offset), ("Wake side", self.wake)):
                    form.addRow(label, widget)
                    widget.valueChanged.connect(slot(self._settings_changed))
                    self.wash_fields.append(widget)
                for widget in (self.area, self.length):  # no Cd/Cl in a prop-wash study
                    widget.setVisible(False)
                    form.labelForField(widget).setVisible(False)
            w.layout().addLayout(form)
            self.model_info = hint()
            w.layout().addWidget(self.model_info)
            return w

        def _build_run(self):
            w = self._box("Run")
            self.openfoam_label = hint()
            w.layout().addWidget(self.openfoam_label)
            self.issues = hint()
            w.layout().addWidget(self.issues)
            row = QtWidgets.QHBoxLayout()
            self.install_button = button("Install OpenFOAM…", self.install_openfoam,
                                           "Open the openfoam-app page (or show the Homebrew command)")
            self.check_button = button("Pre-check", self.check)
            self.run_button = button("Run", self.run, "Write the case, mesh and solve in the background")
            self.stop_button = button("Stop", self.stop)
            self.stop_button.setEnabled(False)
            for b in (self.check_button, self.run_button, self.stop_button, self.install_button):
                row.addWidget(b)
            w.layout().addLayout(row)
            self.stage_label = hint()
            w.layout().addWidget(self.stage_label)
            self.res_plot = PlotWidget("Residuals", log=True)
            self.force_plot = PlotWidget("Drag force (N)")
            w.layout().addWidget(self.res_plot)
            w.layout().addWidget(self.force_plot)
            self.conv_label = hint()
            w.layout().addWidget(self.conv_label)
            return w

        def _build_results(self):
            w = self._box("Results")
            self.table = QtWidgets.QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(["Part", "Drag N", "Side N", "Lift N", "Cd", "Cl"])
            self.table.verticalHeader().setVisible(False)
            self.table.setMinimumHeight(110)
            self.table.horizontalHeader().setStretchLastSection(True)
            w.layout().addWidget(self.table)
            row = QtWidgets.QHBoxLayout()
            self.load_button = button("Show in 3D", self.load_results,
                                        "Convert the last time with foamToVTK and show it: surface pressure on the parts and a mid cut plane coloured by velocity")
            self.paraview_button = button("Open in ParaView", self.open_paraview)
            self.report_button = button("Report", self.report)
            for b in (self.load_button, self.paraview_button, self.report_button):
                row.addWidget(b)
            w.layout().addLayout(row)
            self.pfix_check = QtWidgets.QCheckBox("Fix pressure scale")
            self.pfix_check.setToolTip("Colour the surface pressure with this range instead of each run's own minimum and "
                                       "maximum, so runs can be compared by eye. Applies the next time you press Show in 3D.")
            self.pmin = spin(self.analysis.PressureMin, -1e6, 1e6, 1, " Pa", 10.0)
            self.pmax = spin(self.analysis.PressureMax, -1e6, 1e6, 1, " Pa", 10.0)
            self.pfill_button = button("This run", self.fill_pressure_range, "Take the range of the pressure shown now")
            self.pfix_check.setChecked(bool(self.analysis.PressureFixed))
            self.pfix_check.toggled.connect(slot(self._pressure_changed))
            for s_ in (self.pmin, self.pmax):
                s_.valueChanged.connect(slot(self._pressure_changed))
            row3 = QtWidgets.QHBoxLayout()
            for b in (self.pfix_check, self.pmin, self.pmax, self.pfill_button):
                row3.addWidget(b)
            w.layout().addLayout(row3)
            row2 = QtWidgets.QHBoxLayout()
            self.streams_check = QtWidgets.QCheckBox("Streamlines")
            self.streams_check.setToolTip("Trace streamlines with vtkStreamTracer from seeds upstream of the body (or over "
                                          "each propeller) and show them in the 3D view, coloured by speed")
            self.streams_check.toggled.connect(slot(self.toggle_streamlines))
            self.seeds_spin = QtWidgets.QSpinBox()
            self.seeds_spin.setRange(10, 2000)
            self.seeds_spin.setValue(pvm.SEEDS)
            self.seeds_spin.setSuffix(" seeds")
            self.seeds_spin.setToolTip("Number of seed points; each is followed upstream and downstream")
            self.seeds_spin.editingFinished.connect(slot(self._seeds_changed))
            self.pictures_button = button("Pictures", self.render_pictures,
                                          "Render iso, side and cut-plane pictures with ParaView in the background")
            self.streams_cancel = button("Cancel", self.cancel_streamlines, "Stop tracing the streamlines")
            self.streams_cancel.hide()
            for b in (self.streams_check, self.seeds_spin, self.streams_cancel, self.pictures_button):
                row2.addWidget(b)
            w.layout().addLayout(row2)
            self.picture_choice = QtWidgets.QComboBox()
            self._fill_picture_choice()
            self.picture_choice.currentIndexChanged.connect(slot(self._show_picture))
            self.picture = QtWidgets.QLabel()
            self.picture.setAlignment(QtCore.Qt.AlignCenter)
            self.picture.setMinimumHeight(1)
            w.layout().addWidget(self.picture_choice)
            w.layout().addWidget(self.picture)
            self.picture_choice.hide()
            self.picture.hide()
            self.result_label = hint()
            self.result_label.setOpenExternalLinks(True)
            w.layout().addWidget(self.result_label)
            note = pvm.precheck_text()
            self.pictures_button.setEnabled(not note)
            if note:
                self.pictures_button.setToolTip(note)
            self.paraview_button.setEnabled(cfd.paraview_app() is not None)
            if cfd.paraview_app() is None:
                self.paraview_button.setToolTip(pvm.precheck_text())
            else:
                self.paraview_button.setToolTip("Open ParaView with the case ready: surface pressure, a mid cut plane "
                                                "coloured by speed and streamlines")
            self._show_picture()
            return w

        # --- state
        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def release(self):
            self.timer.stop()
            self.pv_timer.stop()
            if self.pv_job is not None:
                self.pv_job.stop()
            self.stream_timer.stop()
            if self.stream_job is not None:
                self.stream_job.cancel()
            return super().release()

        def _settings_changed(self, *_a):
            a = self.analysis
            a.FlowSpeed, a.AngleOfAttack = self.speed.value(), self.aoa.value()
            a.ReferenceArea, a.ReferenceLength = self.area.value(), self.length.value()
            a.MeshPreset, a.Turbulence = self.preset.currentData(), self.turbulence.currentData()
            a.FarField = self.farfield.currentData()
            a.AirTemperature, a.AirAltitude = self.temperature.value(), self.altitude.value()
            a.DomainUpstream, a.DomainDownstream, a.DomainLateral = self.up.value(), self.down.value(), self.lat.value()
            if self.wash_fields:
                a.PropDiameter, a.TypedThrust, a.DiskOffset = self.diameter.value(), self.typed.value(), self.offset.value()
                a.HubDiameter = self.hub.value()
                a.DomainWake = self.wake.value()
            self.check()

        def check(self):
            """Pre-check: OpenFOAM, watertight parts, cells, y+."""
            install = cfd.find_openfoam()
            if install is None:
                self.openfoam_label.setText("<span style='color:#b00020'>OpenFOAM is not installed.</span> "
                                            + cfd.install_text())
            else:
                warn = f" <span style='color:#8a6d00'>{install['warning']}</span>" if install["warning"] else ""
                self.openfoam_label.setText(f"OpenFOAM {install['version']}: {install['app']}.{warn}")
            self.install_button.setVisible(install is None)
            try:
                issues, est = cfd.study_precheck(self.analysis)
            except Exception as exc:
                issues, est = [("error", f"Pre-check failed: {exc}")], None
            colors = {"error": "#b00020", "warning": "#8a6d00", "info": "#555"}
            self.issues.setText("<br>".join(f"<span style='color:{colors[l]}'>{t}</span>" for l, t in issues))
            events.from_precheck(issues)
            self.run_button.setEnabled(not any(l == "error" for l, _t in issues) and not self._running())
            return issues

        def precheck(self):  # the ribbon's Pre-check
            return self.check()

        def solve(self):  # the ribbon's Solve
            return self.run()

        def install_openfoam(self):
            self.stage_label.setText(cfd.install_openfoam_action())

        def _running(self):
            return self.runner is not None and self.runner.running()

        def run(self):
            try:
                self.runner, info = cfd.start_run(self.analysis)
            except cfd.CfdError as exc:
                self.stage_label.setText(f"<span style='color:#b00020'>{exc}</span>")
                return
            self.folder = info["folder"]
            self.reader = cfd.LogReader(self.folder)
            events.run_started(getattr(self.analysis, "Label", ""))
            self.feed = events.FoamFeed(self.folder)
            self.run_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.stage_label.setText(f"Running in {self.folder} on {info['n_procs']} core(s)…")
            g.status("Airflow: running")
            self.timer.start()

        def stop(self):
            if self.runner is not None:
                self.runner.stop()
            self._tick()

        def _tick(self):
            if self.runner is None or not g._alive(self.stage_label):
                self.timer.stop()
                return
            self.reader.update()
            try:
                self.feed.poll()
            except Exception:
                pass
            self.res_plot.set_series({f: v[-2000:] for f, v in self.reader.residuals.items()})
            try:
                info = json.load(open(os.path.join(self.folder, "vectorlabs.json")))
                drag = {}
                for part, d in info["parts"].items():
                    drag[part] = (cfd.wash().plot_series(self.folder, d["name"], info) if info.get("kind") == cfd.WASH_KIND
                                  else [(r[0], r[1]) for r in cfd.part_forces(self.folder, d["name"])[-2000:]])
                self.force_plot.set_series(drag)
            except Exception:
                pass
            stage = self.runner.stage()
            self.stage_label.setText(f"{stage or 'starting'}, iteration {self.reader.iteration}")
            if not self._running():
                self.timer.stop()
                self.stop_button.setEnabled(False)
                result = self.runner.result()
                try:
                    self.feed.finish(result)
                except Exception:
                    pass
                note = " (MPI did not start on this Mac: ran on one core)" if cfd.mpi_known_broken() else ""
                self.stage_label.setText(f"Run {result}{note}.")
                if result == "done":
                    summary = cfd.save_summary(self.analysis, self.folder, result)
                    self._show_summary()
                    if not pvm.precheck_text():
                        self.render_pictures()
                self.check()

        def _show_summary(self):
            raw = self.analysis.CfdSummary
            if not raw:
                return
            try:
                s = json.loads(raw)
            except ValueError:
                return
            if s.get("kind") == cfd.WASH_KIND:
                return self._show_wash(s)
            rows = s["forces"]["parts"] + [dict(s["forces"]["total"], part="Total")]
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                for j, key in enumerate(("part", "drag", "side", "lift", "Cd", "Cl")):
                    v = r.get(key)
                    text = v if isinstance(v, str) else ("--" if v is None else f"{v:.4g}")
                    self.table.setItem(i, j, QtWidgets.QTableWidgetItem(text))
            self.conv_label.setText(s["convergence"]["text"] + (
                " — <b>converged</b>" if s["convergence"]["converged"] else " — <b>not converged</b>"))

        def _show_wash(self, s):
            """Prop wash: force on every part, the download as a share of the thrust, and the speed at the disks."""
            self.table.setHorizontalHeaderLabels(["Part", "Fx N", "Fy N", "Fz N", "Download N", "Share of thrust"])
            rows = s["forces"]["parts"] + [dict(s["forces"]["total"], part="Total")]
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                for j, key in enumerate(("part", "fx", "fy", "fz", "download", "fraction")):
                    v = r.get(key)
                    if isinstance(v, str):
                        text = v
                    elif v is None:
                        text = "--"
                    else:
                        text = f"{v * 100:.2f} %" if key == "fraction" else f"{v:.4g}"
                    self.table.setItem(i, j, QtWidgets.QTableWidgetItem(text))
            disks = "; ".join(f"{d['label']}: {d['measured']:.2f} m/s (theory {d['theory']:.2f})"
                              for d in s.get("disks", []) if d.get("measured") is not None)
            conv = s["convergence"]
            verdict = f" — <b>{conv.get('label') or ('converged' if conv['converged'] else 'not converged')}</b>"
            detail = ""
            if conv.get("judged"):
                detail = "<br>Forces along the thrust axis, mean (swing): " + "; ".join(
                    f"{st['name']} {st['mean']:.3g} N ({st['swing'] * 100:.2g} %)" for st in conv["judged"])
                if conv.get("listed"):
                    detail += f"<br>Under {cfd.wash().SIGNIFICANT_SHARE * 100:.3g} % of the thrust, not judged: " + ", ".join(st["name"] for st in conv["listed"])
            self.conv_label.setText(conv["text"] + verdict + detail + (f"<br>Induced velocity at the disks: {disks}" if disks else ""))

        def _pressure_changed(self):
            a = self.analysis
            a.PressureFixed, a.PressureMin, a.PressureMax = self.pfix_check.isChecked(), self.pmin.value(), self.pmax.value()

        def fill_pressure_range(self):
            """Puts the range of the pressure shown last (this run's own) into the fixed range."""
            rng = getattr(self, "p_range", None)
            if rng is None:
                try:
                    rng = cfd.pressure_range(cfd.part_patch_files(self.analysis.CaseFolder))
                except Exception:
                    rng = None
            if rng is not None:
                self.pmin.setValue(rng[0])
                self.pmax.setValue(rng[1])

        def load_results(self):
            try:
                out = cfd.load_results(self.analysis)
            except Exception as exc:
                self.result_label.setText(f"<span style='color:#b00020'>{exc}</span>")
                return None
            parts = len(out.get("surfaces") or [])
            self.result_label.setText(
                "Showing the flow: " + (f"surface pressure on {parts} part{'s' if parts != 1 else ''}" if parts else "no part surface")
                + (", the mid cut plane coloured by velocity" if out["cut"] else "") + ". Tick Streamlines to add the flow lines.")
            self.p_range = out.get("p_range")
            if self.p_range:
                self.result_label.setText(self.result_label.text() + (
                    f" Pressure scale {self.p_range[0]:.4g} to {self.p_range[1]:.4g} Pa "
                    f"({'fixed' if out.get('p_fixed') else 'this run'})."))
            try:
                import FreeCADGui

                FreeCADGui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass
            return out

        def open_paraview(self):
            try:
                cfd.open_in_paraview(self.analysis.CaseFolder)
            except Exception as exc:
                self.result_label.setText(f"<span style='color:#b00020'>{exc}</span>")

        # --- streamlines in the 3D view (vtkStreamTracer of the app's VTK)
        def toggle_streamlines(self, on=None):
            on = self.streams_check.isChecked() if on is None else bool(on)
            if not on:
                if self._streams is not None:
                    self._streams[0].ViewObject.Visibility = False
                return None
            return self.show_streamlines()

        def _seeds_changed(self):
            if self.streams_check.isChecked() and (self._streams is None or self._streams[1] != self.seeds_spin.value()):
                self.show_streamlines()

        def show_streamlines(self):
            """Shows the streamlines of the case.  Tracing takes 2 to 4 s on a frame, so it runs in a worker process (the window
            stays alive, with a progress figure and Cancel); returns the object when it is already there, else None (it
            appears when the worker is done).  Without a worker Python it traces here behind a wait cursor."""
            count = self.seeds_spin.value()
            if self._streams is not None and self._streams[1] == count:
                self._streams[0].ViewObject.Visibility = True
                return self._streams[0]
            if self.stream_job is not None and self.stream_job.running():
                if self.stream_job.count == count:
                    return None
                self.stream_job.cancel()
            job = pvm.start_streamlines(self.analysis.CaseFolder, count)
            if job.state == "unavailable":
                return self._show_streamlines_here(count)
            self.stream_job = job
            self.streams_cancel.show()
            self.result_label.setText(f"Tracing streamlines from {count} seeds in the background…")
            self.stream_timer.start()
            return None

        def cancel_streamlines(self):
            if self.stream_job is not None:
                self.stream_job.cancel()
            self._end_streamlines("Streamlines stopped.")

        def _end_streamlines(self, text):
            self.stream_timer.stop()
            self.streams_cancel.hide()
            self.result_label.setText(text)

        def _poll_streamlines(self):
            job = self.stream_job
            if not g._alive(self.result_label) or job is None:
                self.stream_timer.stop()
                return
            if not job.poll():
                self.result_label.setText(f"Tracing streamlines from {job.count} seeds: {job.progress * 100:.0f} %…")
                return
            if job.state != "done":
                if job.state == "failed":
                    self._end_streamlines(f"<span style='color:#b00020'>Streamlines: {job.message}</span>")
                else:
                    self._end_streamlines("Streamlines stopped.")
                return
            if job.count != self.seeds_spin.value() and self.streams_check.isChecked():  # the count changed meanwhile
                self._end_streamlines("")
                self.show_streamlines()
                return
            try:
                if self._streams is not None:
                    self.doc.removeObject(self._streams[0].Name)
                    self._streams = None
                obj = pvm.show_streamlines(self.analysis, job.path)
            except Exception as exc:
                self._end_streamlines(f"<span style='color:#b00020'>Streamlines: {exc}</span>")
                return
            self._streams = (obj, job.count)
            obj.ViewObject.Visibility = self.streams_check.isChecked()
            self._end_streamlines(f"Streamlines from {job.count} seeds in {job.timings.get('total', 0):.1f} s "
                                  f"(tracing {job.timings.get('trace', 0):.1f} s, in the background).")

        def _show_streamlines_here(self, count):
            timings = {}
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                path = pvm.compute_streamlines(self.analysis.CaseFolder, count, timings=timings)
                if self._streams is not None:
                    self.doc.removeObject(self._streams[0].Name)
                    self._streams = None
                obj = pvm.show_streamlines(self.analysis, path)
            except Exception as exc:
                self.result_label.setText(f"<span style='color:#b00020'>Streamlines: {exc}</span>")
                return None
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self._streams = (obj, count)
            self.result_label.setText(f"Streamlines from {count} seeds in {timings.get('total', 0):.1f} s "
                                      f"(tracing {timings.get('trace', 0):.1f} s).")
            return obj

        # --- the ParaView pictures (a pvbatch child process, polled by a timer)
        def render_pictures(self):
            if self.pv_job is not None and self.pv_job.running():
                return
            folder = self.analysis.CaseFolder
            self.pv_job = pvm.start_render(folder)
            if not self.pv_job.running():
                self.result_label.setText(self.pv_job.message)
                return
            self.pictures_button.setEnabled(False)
            self.result_label.setText("Rendering pictures with ParaView in the background…")
            self.pv_timer.start()

        def _poll_pictures(self):
            if not g._alive(self.result_label):
                self.pv_timer.stop()
                return
            if self.pv_job is None or not self.pv_job.poll():
                return
            self.pv_timer.stop()
            self.pictures_button.setEnabled(not pvm.precheck_text())
            self.result_label.setText("Pictures: " + self.pv_job.message if self.pv_job.state == "done"
                                      else f"<span style='color:#8a6d00'>No pictures: {self.pv_job.message}</span>")
            self._show_picture()

        def _fill_picture_choice(self):
            """The picture list of this study: the three generic ones, and for prop wash the axis view, a cut through each pair of
            opposite disks and the wake surface.  Refilled when the case changes."""
            folder = getattr(self.analysis, "CaseFolder", "") if getattr(self, "analysis", None) is not None else ""
            kinds = [k for k, _c in pvm.picture_kinds(folder or None)]
            titles = pvm.picture_titles(folder or None)
            if kinds == [self.picture_choice.itemData(i) for i in range(self.picture_choice.count())]:
                return
            keep = self.picture_choice.currentData()
            self.picture_choice.blockSignals(True)
            self.picture_choice.clear()
            for k in kinds:
                self.picture_choice.addItem(titles.get(k, k), k)
            if keep in kinds:
                self.picture_choice.setCurrentIndex(kinds.index(keep))
            self.picture_choice.blockSignals(False)

        def _show_picture(self, *_a):
            folder = self.analysis.CaseFolder
            self._fill_picture_choice()
            path = pvm.images(folder).get(self.picture_choice.currentData()) if folder else None
            if not path:
                self.picture.hide()
                self.picture_choice.hide()
                return
            pix = QtGui.QPixmap(path)
            self.picture.setPixmap(pix.scaledToWidth(360, QtCore.Qt.SmoothTransformation))
            self.picture.setToolTip(path)
            self.picture.show()
            self.picture_choice.show()

        def report(self):
            if not self.analysis.CfdSummary:
                self.result_label.setText("Run the study before writing its report")
                return None
            files = g.report_study(self.analysis, None, done=slot(self._reported))
            return files

        def _reported(self, files):
            if not g._alive(self.result_label):
                return
            if files.get("pdf"):
                self.result_label.setText(f"Report: <a href='file://{files['pdf']}'>{os.path.basename(files['pdf'])}</a>")
            else:
                self.result_label.setText(f"{files.get('message', '')} Source: {files['dir']}")

    return {"NewStudyBox": NewStudyBox, "AirflowPanel": AirflowPanel, "PlotWidget": PlotWidget}


def open_airflow_panel(analysis):
    return _gui().show_panel(classes()["AirflowPanel"](analysis))
