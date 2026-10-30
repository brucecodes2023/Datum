# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Simulation workspace's commands and task panels (roadmap SIM-01; 27 Oct 2026).

The numbers come from bruceworx_sim; this module is the Fusion-style workflow
around them, in the ribbon's five groups (workspaces.json, Simulation):

* Study: **New Study** opens a task panel: study type (static stress; modal,
  buckling, thermal and thermal stress listed as planned), the model (the
  selection, the active Body or Part, else every visible solid), the material
  (prefilled from the part's own material; a combo of every library card with
  E and nu, the vault's library first; a warning when the part has none), and
  the mesh size (bounding-box diagonal / 30, Moderate, second order). Create
  makes the study's objects and opens the study panel.
* The **study panel** (BruceWorx_Study, also from Solve and Pre-check): the
  loads and constraints with Add Fixed / Force / Pressure / Displacement
  buttons that start the stock FEM panels (ours closes and comes back when
  theirs is done), Pre-check, Mesh and Refine with the mesh statistics, Solve
  (meshes if needed, runs ccx in a QProcess so the window stays live, reads the
  results, computes the safety factor), the results summary (max von Mises,
  max displacement, min safety factor), the field buttons, the probe and the
  report.
* Results: **Stress**, **Displacement** and **Safety Factor** colour the result
  mesh. The safety factor is drawn in the study's bands (30 Oct, S2: by default
  red below 1, amber to 2, green above; thresholds, colours and the number of
  bands are set in the panel's band editor or with **SF Bands**, and saved as
  the default in Mod/BruceWorx/Sim), with the legend in the panel. The panel
  also picks the stress measure the safety factor divides (S3: von Mises,
  maximum principal or Tresca); a change recomputes it without a new solve.
  **Probe** reads the nearest node's von Mises, displacement and safety factor
  at a clicked point. **Report** writes report.tex with three off-screen
  renders in a folder beside the document and compiles it to a PDF with
  Tectonic (bundled), latexmk or pdflatex, in a QProcess (S4).

Every signal connection of these plain-Python panels goes through
bruceworx_slots.slot (PySide 6.8.3; see that module).
"""

from __future__ import annotations

import os
import tempfile
import time

import bruceworx_sim as sim
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_TITLE = "Study"
CONSTRAINT_COMMANDS = (
    ("Fixed", "FEM_ConstraintFixed"),
    ("Force", "FEM_ConstraintForce"),
    ("Pressure", "FEM_ConstraintPressure"),
    ("Displacement", "FEM_ConstraintDisplacement"),
)
RENDER_SIZE = (900, 600)

_state = {"installed": False, "panel": None, "reopen": None, "probe": None, "shown": {}, "classes": None}


# ----- small helpers ---------------------------------------------------------------------------------


def _doc():
    import FreeCAD

    return FreeCAD.ActiveDocument


def _selection():
    try:
        import FreeCADGui as Gui

        return Gui.Selection.getSelection()
    except Exception:
        return []


def _alive(widget):
    try:
        import shiboken6

        return widget is not None and shiboken6.isValid(widget)
    except Exception:
        return widget is not None


def status(text, seconds=6000):
    """A line in the status bar and the Report view."""
    import FreeCAD

    FreeCAD.Console.PrintMessage(f"[sim] {text}\n")
    try:
        import FreeCADGui as Gui

        Gui.getMainWindow().statusBar().showMessage(text, seconds)
    except Exception:
        pass


def _warn(text):
    import FreeCAD

    FreeCAD.Console.PrintWarning(f"[sim] {text}\n")
    status(text)


def close_dialog():
    """Closes whatever task dialog is open (ours or a stock one)."""
    import FreeCADGui as Gui

    try:
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
    except Exception:
        pass


def show_panel(panel):
    import FreeCADGui as Gui

    close_dialog()
    _state["panel"] = panel
    Gui.Control.showDialog(panel)
    return panel


def set_active(analysis):
    try:
        import FemGui

        FemGui.setActiveAnalysis(analysis)
    except Exception:
        pass


def current_study(doc=None):
    doc = doc or _doc()
    return sim.active_study(doc, _selection()) if doc is not None else None


# ----- the fields -------------------------------------------------------------------------------------


def band_colors(nodes, factors, bands):
    """{(node, ...): (r, g, b)} per band, the form ViewProviderFemMesh.NodeColor takes (one entry per band)."""
    thresholds, colors = bands
    groups = {}
    for node, sf in zip(nodes, factors):
        groups.setdefault(sim.band_index(sf, thresholds), []).append(node)
    return {tuple(ids): sim.rgb(colors[i]) for i, ids in groups.items()}


def _brittle(uuid):
    """True for a Keystone card marked brittle (acrylic, CFRP, G10: the materials agent's BruceWorx block)."""
    try:
        import bruceworx_materials

        info = bruceworx_materials.card(uuid)
        return bool(info and info.get("brittle"))
    except Exception:
        return False


def _text_on(color):
    """Black or white text, whichever reads on a band colour."""
    r, g, b = sim.rgb(color)
    return "#000" if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else "#fff"


def legend_html(kind, result=None, analysis=None):
    """The panel's legend for a shown field."""
    if kind == "safety":
        analysis = analysis or (sim.study_of(result) if result is not None else None)
        thresholds, colors = sim.study_bands(analysis)
        chips = "".join(
            f"<span style='background:{color};color:{_text_on(color)};padding:1px 6px;margin-right:4px'>"
            f"&nbsp;{label}&nbsp;</span>"
            for color, label in zip(colors, sim.band_labels(thresholds)))
        measure = getattr(result, "StressMeasure", "") if result is not None else ""
        measure = measure or sim.study_measure(analysis)
        basis = ""
        if result is not None and getattr(result, "AllowableStress", 0):
            basis = (f"<br><span style='color:#777'>allowable {result.AllowableStress:.4g} MPa "
                     f"({result.AllowableBasis})</span>")
        return f"<b>Safety factor</b> = allowable / {sim.measure_label(measure)} stress per node<br>{chips}{basis}"
    if kind == "stress":
        return "<b>von Mises stress</b>, MPa: blue low to red high"
    if kind == "displacement":
        return "<b>Displacement</b>, mm: blue low to red high, shape exaggerated"
    return ""


def _model_objects(analysis):
    m = sim.members(analysis)
    found = []
    model = getattr(analysis, "Model", None) if "Model" in analysis.PropertiesList else None
    if model is not None:
        found.append(model)
    if m["mesh"] is not None:
        found.append(m["mesh"])
        shape = getattr(m["mesh"], "Shape", None)
        if shape is not None:
            found.append(shape)
    return found


def _hide_for_result(analysis, result):
    for obj in _model_objects(analysis):
        vobj = getattr(obj, "ViewObject", None)
        if vobj is not None and obj is not result.Mesh:
            vobj.Visibility = False
    vobj = result.Mesh.ViewObject
    vobj.Visibility = True
    try:
        vobj.DisplayMode = "Faces & Wireframe"
    except Exception:
        pass
    try:
        if not result.ViewObject.Visibility:
            result.ViewObject.Visibility = True
    except Exception:
        pass


def show_field(analysis, kind, result=None):
    """Colours the result mesh by von Mises ('stress'), displacement ('displacement') or safety factor."""
    result = result or sim.latest_result(analysis)
    if result is None:
        _warn("No results yet: solve the study first")
        return False
    nodes = list(result.NodeNumbers)
    vobj = result.Mesh.ViewObject
    _hide_for_result(analysis, result)
    vectors = list(result.DisplacementVectors)
    has_vectors = len(vectors) == len(nodes) and bool(nodes)
    if has_vectors:
        vobj.setNodeDisplacementByVectors(nodes, vectors)  # applyDisplacement needs the vectors set first
    if kind in ("stress", "displacement"):
        _color_mode(vobj, "Overall")  # setNodeColorByScalars paints the material itself
    if kind == "stress":
        vobj.setNodeColorByScalars(nodes, list(result.vonMises))
        if has_vectors:
            vobj.applyDisplacement(0.0)
    elif kind == "displacement":
        from femtaskpanels.task_result_mechanical import get_displacement_scale_factor

        vobj.setNodeColorByScalars(nodes, list(result.DisplacementLengths))
        if has_vectors:
            vobj.applyDisplacement(float(get_displacement_scale_factor(result)))
    elif kind == "safety":
        factors = list(getattr(result, "SafetyFactor", []) or [])
        if not factors:
            _warn("No safety factor: the material has no yield or ultimate strength")
            return False
        # NodeColor fills NodeColorArray, which the view provider paints only in the ByNode colour mode
        vobj.NodeColor = band_colors(nodes, factors, sim.study_bands(analysis))
        _color_mode(vobj, "ByNode")
        if has_vectors:
            vobj.applyDisplacement(0.0)
    else:
        raise ValueError(kind)
    _state["shown"][analysis.Name] = kind
    try:
        result.Document.recompute()
    except Exception:
        pass
    return True


def _color_mode(vobj, mode):
    try:
        if vobj.ColorMode != mode:
            vobj.ColorMode = mode
    except Exception:
        pass


def shown_field(analysis):
    return _state["shown"].get(analysis.Name)


# ----- the probe ----------------------------------------------------------------------------------------


class ProbeObserver:
    """Reads the result at the mesh node nearest to a clicked point; reports through ``callback``."""

    def __init__(self, analysis, callback=None):
        self.analysis = analysis
        self.callback = callback
        self.last = None

    def addSelection(self, doc_name, obj_name, sub, point=None, *_rest):
        import FreeCAD

        result = sim.latest_result(self.analysis)
        if result is None or not point:
            return
        doc = FreeCAD.getDocument(doc_name)
        obj = doc.getObject(obj_name) if doc else None
        if obj is None or (obj is not result.Mesh and obj not in _model_objects(self.analysis)):
            return
        reading = sim.probe(result, FreeCAD.Vector(*point[:3]))
        if reading is None:
            return
        self.last = reading
        text = probe_text(reading)
        status(text, 12000)
        if self.callback:
            self.callback(reading)

    # the rest of the observer protocol
    def removeSelection(self, *_a):
        pass

    def setSelection(self, *_a):
        pass

    def clearSelection(self, *_a):
        pass


def probe_text(reading):
    sf = reading.get("safety_factor")
    sf_text = "–" if sf is None else ("> 100" if sf >= sim.SF_CAP else f"{sf:.3g}")
    return (f"Node {reading['node']}: von Mises {reading['von_mises']:.4g} MPa, "
            f"displacement {reading['displacement']:.4g} mm, safety factor {sf_text}")


def start_probe(analysis, callback=None):
    import FreeCADGui as Gui

    stop_probe()
    observer = ProbeObserver(analysis, callback)
    Gui.Selection.addObserver(observer)
    _state["probe"] = observer
    status("Probe: click a point on the result mesh")
    return observer


def stop_probe():
    observer = _state.get("probe")
    if observer is None:
        return
    try:
        import FreeCADGui as Gui

        Gui.Selection.removeObserver(observer)
    except Exception:
        pass
    _state["probe"] = None


def probing():
    return _state.get("probe") is not None


# ----- the report -----------------------------------------------------------------------------------------


def _view(doc):
    import FreeCADGui as Gui

    gdoc = Gui.getDocument(doc.Name) if doc is not None else None
    view = gdoc.activeView() if gdoc is not None else None
    return view if view is not None and hasattr(view, "saveImage") else None


def render_fields(analysis, folder, size=RENDER_SIZE):
    """Off-screen renders of the three fields: {"stress": path, "displacement": path, "safety": path}."""
    import FreeCADGui as Gui

    result = sim.latest_result(analysis)
    view = _view(analysis.Document)
    if result is None or view is None:
        return {}
    images = {}
    before = shown_field(analysis)
    try:
        animated = view.isAnimationEnabled()
        view.setAnimationEnabled(False)
    except Exception:
        animated = None
    try:
        for kind in ("stress", "displacement", "safety"):
            if not show_field(analysis, kind, result):
                continue
            Gui.updateGui()
            view.viewIsometric()
            view.fitAll()
            Gui.updateGui()
            path = os.path.join(folder, f"{analysis.Name}_{kind}.png")
            view.saveImage(path, size[0], size[1], "Current")
            if os.path.isfile(path):
                images[kind] = path
    finally:
        if animated is not None:
            view.setAnimationEnabled(animated)
        if before and before != "safety":
            show_field(analysis, before, result)
    return images


def report_study(analysis, folder=None, done=None):
    """Writes the LaTeX report with three renders and compiles it to a PDF.

    Blocking when ``done`` is None: returns write_report's {"dir", "tex", "pdf", "message"}. With a
    callback the compiler runs in a QProcess (Tectonic's first run fetches its TeX files, ~30 s) and
    ``done(files)`` is called when it finishes; the return value then has "pdf": None, "running": True.
    """
    with tempfile.TemporaryDirectory(prefix="bw-sim-render-") as tmp:
        images = render_fields(analysis, tmp)
        files = sim.write_report(analysis, images, folder, compile=False)
    kind, path = sim.find_tex()
    if kind is None:
        files["message"] = (f"No LaTeX compiler (Tectonic, latexmk, pdflatex) found: the report's source is in "
                            f"{files['dir']}")
        _warn(files["message"])
        if done:
            done(files)
        return files
    if done is None:
        files["pdf"], files["message"] = sim.compile_report(files)
        status(files["message"], 12000)
        return files
    return _compile_async(files, kind, path, done)


def _compile_async(files, kind, path, done):
    from PySide import QtCore

    old = _state.get("report_process")
    if old is not None and _alive(old) and old.state() != QtCore.QProcess.NotRunning:
        files["message"] = "A report is still being compiled"
        _warn(files["message"])
        done(files)
        return files
    if os.path.isfile(files["built"]):
        os.remove(files["built"])
    process = QtCore.QProcess()
    env = QtCore.QProcessEnvironment()
    for key, value in sim.tex_environment(path).items():
        env.insert(key, value)
    process.setProcessEnvironment(env)
    process.setWorkingDirectory(files["dir"])
    process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
    _state["report_process"] = process
    argv = sim.tex_command(kind, path)

    def finished(code=0, _exit_status=None):
        output = bytes(process.readAll()).decode("utf-8", "replace") if _alive(process) else ""
        files["pdf"] = sim.finish_report(files) if code == 0 else None
        files["running"] = False
        if files["pdf"]:
            files["message"] = f"Report compiled with {kind}: {files['pdf']}"
            status(files["message"], 12000)
        else:
            files["message"] = f"{kind} could not compile {files['tex']} (exit {code}): {output[-400:]}"
            _warn(files["message"])
        done(files)

    def failed(error):
        if process.state() == QtCore.QProcess.NotRunning and not files.get("pdf"):
            files["running"] = False
            files["message"] = f"{kind} did not start ({error})"
            _warn(files["message"])
            done(files)

    process.finished.connect(slot(finished))
    process.errorOccurred.connect(slot(failed))
    files["running"] = True
    status(f"Compiling the report with {kind}…" + (" (the first run fetches its TeX files)" if kind == "tectonic" else ""))
    process.start(argv[0], argv[1:])
    return files


def wait_for_report(timeout=600.0):
    """Pumps the GUI until the report compile started by report_study(done=...) ends (tests)."""
    import FreeCADGui as Gui
    from PySide import QtCore

    process = _state.get("report_process")
    end = time.time() + timeout
    while process is not None and _alive(process) and process.state() != QtCore.QProcess.NotRunning:
        if time.time() > end:
            return False
        process.waitForFinished(100)
        Gui.updateGui()
    Gui.updateGui()
    return True


# ----- the panels -----------------------------------------------------------------------------------------


def classes():
    """The panel classes (made once Qt is importable)."""
    if _state["classes"] is None:
        _state["classes"] = _make_classes()
    return _state["classes"]


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    def box(title, icon=None):
        w = QtWidgets.QWidget()
        w.setWindowTitle(title)
        if icon:
            w.setWindowIcon(QtGui.QIcon(icon))
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)
        return w

    def form_layout():
        lay = QtWidgets.QFormLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        return lay

    def hint(text=""):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        lab.setTextFormat(QtCore.Qt.RichText)
        return lab

    def measure_combo(current):
        combo = QtWidgets.QComboBox()
        for key, label, _formula in sim.STRESS_MEASURES:
            combo.addItem(label, key)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.setToolTip("The equivalent stress the safety factor divides: von Mises (ductile metals), maximum "
                         "principal max(|s1|, |s3|) (Rankine, brittle), Tresca s1 - s3 (conservative)")
        return combo

    def button(text, handler, tip=""):
        b = QtWidgets.QPushButton(text)
        if tip:
            b.setToolTip(tip)
        receiver = slot(handler)
        b.clicked.connect(lambda *_a: receiver())
        return b

    class Panel:
        """The task-dialog protocol FreeCAD's Control.showDialog expects (as the vault wizard's)."""

        title = PANEL_TITLE

        def __init__(self):
            self.buttons = None
            self.released = False

        def release(self):
            if _state["panel"] is self:
                _state["panel"] = None
            self.released = True
            return True

        def modifyStandardButtons(self, buttons):
            self.buttons = buttons
            self.setup_buttons()

        def setup_buttons(self):
            pass

        def ok_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Ok) if self.buttons is not None else None

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True

        def reject(self):
            return self.release()

    class NewStudyPanel(Panel):
        """New Study: type, model, material, mesh; Create makes the objects and opens the study panel."""

        title = "New Study"

        def __init__(self, doc=None, selection=None):
            super().__init__()
            self.doc = doc or _doc()
            self.cards = sim.material_cards()
            self.candidates = sim.model_candidates(self.doc, selection if selection is not None else _selection())
            self.form = [self._build_study(), self._build_model(), self._build_material(), self._build_mesh()]
            self._model_changed()

        # --- boxes
        def _build_study(self):
            w = box("Study", os.path.join(HERE, "BruceWorx_NewStudy.svg"))
            lay = form_layout()
            self.name_edit = QtWidgets.QLineEdit(f"Study {len(sim.studies(self.doc)) + 1}")
            lay.addRow("Name", self.name_edit)
            self.type_combo = QtWidgets.QComboBox()
            for key, label, available in sim.STUDY_TYPES:
                self.type_combo.addItem(label if available else f"{label}  (planned)", key)
                if not available:
                    item = self.type_combo.model().item(self.type_combo.count() - 1)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            lay.addRow("Type", self.type_combo)
            w.layout().addLayout(lay)
            w.layout().addWidget(hint("Static stress: linear elastic, small displacements (CalculiX). "
                                      "Modal, buckling, thermal and thermal stress follow on this frame."))
            return w

        def _build_model(self):
            w = box("Model", ":/icons/PartDesign_Body.svg")
            lay = form_layout()
            self.model_combo = QtWidgets.QComboBox()
            for obj in self.candidates:
                self.model_combo.addItem(obj.Label, obj.Name)
            self.model_combo.currentIndexChanged.connect(slot(self._model_changed))
            lay.addRow("Analyse", self.model_combo)
            w.layout().addLayout(lay)
            self.model_info = hint()
            w.layout().addWidget(self.model_info)
            return w

        def _build_material(self):
            w = box("Material", ":/icons/FEM_MaterialSolid.svg")
            lay = form_layout()
            self.material_combo = QtWidgets.QComboBox()
            self.material_combo.addItem("(none)", "")
            library = None
            for card in self.cards:
                if card["library"] != library:
                    library = card["library"]
                    heading = {sim.STOCK_LIBRARY: "FreeCAD (FEM)"}.get(library, library)
                    self.material_combo.addItem(f"— {heading} —", "")
                    item = self.material_combo.model().item(self.material_combo.count() - 1)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
                strength = f", yield {card['yield']:.4g} MPa" if card["yield"] else (
                    f", UTS {card['uts']:.4g} MPa" if card["uts"] else ", no strength value")
                self.material_combo.addItem(f"{card['name']}  (E {card['E']:.4g} MPa{strength})", card["uuid"])
            self.material_combo.currentIndexChanged.connect(slot(self._material_changed))
            lay.addRow("Card", self.material_combo)
            self.measure_combo = measure_combo(sim.default_measure())
            lay.addRow("Safety factor from", self.measure_combo)
            w.layout().addLayout(lay)
            self.material_warn = hint()
            w.layout().addWidget(self.material_warn)
            return w

        def _build_mesh(self):
            w = box("Mesh", ":/icons/FEM_MeshNetgenFromShape.svg")
            lay = form_layout()
            self.size_spin = QtWidgets.QDoubleSpinBox()
            self.size_spin.setRange(0.01, 100000.0)
            self.size_spin.setDecimals(3)
            self.size_spin.setSuffix(" mm")
            self.size_spin.setToolTip("Largest element: the bounding-box diagonal / 30 by default")
            lay.addRow("Max element", self.size_spin)
            self.fineness_combo = QtWidgets.QComboBox()
            for name in sim.FINENESS:
                self.fineness_combo.addItem(name, name)
            self.fineness_combo.setCurrentIndex(sim.FINENESS.index(sim.DEFAULT_FINENESS))
            lay.addRow("Fineness", self.fineness_combo)
            self.second_order = QtWidgets.QCheckBox("Second-order elements (Tet10)")
            self.second_order.setChecked(True)
            lay.addRow("", self.second_order)
            w.layout().addLayout(lay)
            w.layout().addWidget(hint("Netgen meshes at Solve (or Mesh). Refine halves the size for a convergence check."))
            return w

        # --- state
        def model(self):
            name = self.model_combo.currentData()
            return self.doc.getObject(name) if name else None

        def select_material(self, uuid):
            index = self.material_combo.findData(uuid) if uuid else 0
            self.material_combo.setCurrentIndex(max(0, index))
            return index > 0

        def material_uuid(self):
            return self.material_combo.currentData() or ""

        def _model_changed(self, *_a):
            model = self.model()
            if model is None:
                self.model_info.setText("<span style='color:#b00020'>No body or part with solids in the document.</span>")
                return
            shape = sim.shape_of(model)
            bb = shape.BoundBox
            self.model_info.setText(f"{len(shape.Solids)} solid{'s' if len(shape.Solids) != 1 else ''}, "
                                    f"{bb.XLength:.4g} × {bb.YLength:.4g} × {bb.ZLength:.4g} mm")
            self.size_spin.setValue(sim.default_mesh_size(model))
            material = sim.material_of(model)
            uuid = getattr(material, "UUID", "") if material is not None else ""
            if not (uuid and self.select_material(uuid)):
                self.select_material("")
                if material is not None:
                    self.material_warn.setText(f"<span style='color:#8a6d00'>The part's material "
                                               f"{material.Name} has no E and ν; pick a card.</span>")
                else:
                    self.material_warn.setText("<span style='color:#8a6d00'>The part has no material: pick a card "
                                               "(the study needs E and ν; the safety factor needs a yield strength).</span>")

        def _material_changed(self, *_a):
            uuid = self.material_uuid()
            card = next((c for c in self.cards if c["uuid"] == uuid), None)
            if card is None:
                return
            if card["yield"]:
                note = ""
                if _brittle(uuid):
                    note = (" <span style='color:#8a6d00'>A brittle card (no yield; the strength is the tensile "
                            "strength): Maximum principal suits it.</span>")
                self.material_warn.setText(f"E {card['E']:.4g} MPa, ν {card['nu']:.3g}, yield {card['yield']:.4g} MPa.{note}")
            elif card["uts"]:
                self.material_warn.setText(f"<span style='color:#8a6d00'>E {card['E']:.4g} MPa, ν {card['nu']:.3g}; no yield "
                                           f"strength: the safety factor uses the ultimate strength {card['uts']:.4g} MPa.</span>")
            else:
                self.material_warn.setText(f"<span style='color:#8a6d00'>E {card['E']:.4g} MPa, ν {card['nu']:.3g}; no strength "
                                           "value: stresses only, no safety factor.</span>")

        def plan(self):
            uuid = self.material_uuid()
            return sim.StudyPlan(
                name=self.name_edit.text().strip() or "Study",
                study_type=self.type_combo.currentData() or "static",
                model=self.model(),
                material_uuid=uuid,
                material=next((c for c in self.cards if c["uuid"] == uuid), None),
                max_size=self.size_spin.value(),
                fineness=self.fineness_combo.currentData(),
                second_order=self.second_order.isChecked(),
                stress_measure=self.measure_combo.currentData(),
            )

        def setup_buttons(self):
            ok = self.ok_button()
            if ok is not None:
                ok.setText("Create study")
                ok.setEnabled(self.model() is not None)

        def create(self):
            """Makes the study's objects; returns the analysis (None with a message when it cannot)."""
            plan = self.plan()
            if plan.model is None:
                _warn("New Study: no body or part to analyse")
                return None
            self.doc.openTransaction("New study")
            try:
                objs = sim.new_study(self.doc, plan)
            except sim.StudyError as exc:
                self.doc.abortTransaction()
                _warn(f"New Study: {exc}")
                return None
            self.doc.commitTransaction()
            analysis = objs["analysis"]
            set_active(analysis)
            if not plan.material:
                _warn("The study has no material yet: pick one before solving")
            status(f"Study '{plan.name}' created: add the supports and loads, then Solve")
            return analysis

        def accept(self):
            analysis = self.create()
            if analysis is None:
                return False
            self.release()
            QtCore.QTimer.singleShot(0, lambda: open_study_panel(analysis))
            return True

    class StudyPanel(Panel):
        """Loads and constraints, pre-check, mesh, solve, results, probe, report of one study."""

        title = "Study"

        def __init__(self, analysis):
            super().__init__()
            self.analysis = analysis
            self.doc = analysis.Document
            self.fea = None
            self.process = None
            self.started = 0.0
            self.form = [self._build_setup(), self._build_mesh(), self._build_solve(), self._build_results()]
            self.refresh()

        # --- boxes
        def _build_setup(self):
            w = box(f"Study: {sim.summary_name(self.analysis)}", os.path.join(HERE, "BruceWorx_NewStudy.svg"))
            self.members_list = QtWidgets.QListWidget()
            self.members_list.setMaximumHeight(120)
            self.members_list.itemDoubleClicked.connect(slot(self._edit_member))
            w.layout().addWidget(self.members_list)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            for text, command in CONSTRAINT_COMMANDS:
                row.addWidget(button(f"+ {text}", lambda cmd=command: self.add_constraint(cmd),
                                     f"Add a {text} constraint (the stock FEM panel; this one comes back after)"))
            w.layout().addLayout(row)
            row2 = QtWidgets.QHBoxLayout()
            row2.addWidget(button("Pre-check", self.precheck, "List what is missing before a solve"))
            row2.addStretch(1)
            w.layout().addLayout(row2)
            self.issues = hint()
            w.layout().addWidget(self.issues)
            return w

        def _build_mesh(self):
            w = box("Mesh", ":/icons/FEM_MeshNetgenFromShape.svg")
            lay = form_layout()
            self.size_spin = QtWidgets.QDoubleSpinBox()
            self.size_spin.setRange(0.01, 100000.0)
            self.size_spin.setDecimals(3)
            self.size_spin.setSuffix(" mm")
            self.size_spin.valueChanged.connect(slot(self._size_changed))
            lay.addRow("Max element", self.size_spin)
            w.layout().addLayout(lay)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(button("Mesh", self.mesh, "Generate the Netgen mesh now"))
            row.addWidget(button("Refine", self.refine, "Element size × 0.7, mesh again (convergence check)"))
            row.addStretch(1)
            w.layout().addLayout(row)
            self.mesh_info = hint()
            w.layout().addWidget(self.mesh_info)
            return w

        def _build_solve(self):
            w = box("Solve", ":/icons/FEM_SolverRun.svg")
            row = QtWidgets.QHBoxLayout()
            self.solve_button = button("Solve", self.solve, "Mesh if needed, run CalculiX, read the results")
            row.addWidget(self.solve_button)
            row.addStretch(1)
            w.layout().addLayout(row)
            self.progress = hint()
            w.layout().addWidget(self.progress)
            return w

        def _build_results(self):
            w = box("Results", os.path.join(HERE, "BruceWorx_SafetyFactor.svg"))
            self.summary = hint("Not solved yet.")
            w.layout().addWidget(self.summary)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(button("Stress", lambda: self.show("stress"), "Colour by von Mises stress"))
            row.addWidget(button("Displacement", lambda: self.show("displacement"), "Colour by displacement, exaggerated"))
            row.addWidget(button("Safety Factor", lambda: self.show("safety"), "Colour by safety factor in the study's bands"))
            w.layout().addLayout(row)
            lay = form_layout()
            self.measure_combo = measure_combo(sim.study_measure(self.analysis))
            self.measure_combo.currentIndexChanged.connect(slot(self._measure_changed))
            lay.addRow("Safety factor from", self.measure_combo)
            w.layout().addLayout(lay)
            self.legend = hint()
            w.layout().addWidget(self.legend)
            self.bands_button = QtWidgets.QPushButton("Bands…")
            self.bands_button.setCheckable(True)
            self.bands_button.setToolTip("Safety-factor bands: thresholds and colours of this study; save them as the default")
            self.bands_button.toggled.connect(slot(self.show_bands))
            row3 = QtWidgets.QHBoxLayout()
            row3.addWidget(self.bands_button)
            row3.addStretch(1)
            w.layout().addLayout(row3)
            w.layout().addWidget(self._build_bands())
            row2 = QtWidgets.QHBoxLayout()
            self.probe_button = QtWidgets.QPushButton("Probe")
            self.probe_button.setCheckable(True)
            self.probe_button.setToolTip("Click a point on the result mesh to read its values")
            self.probe_button.toggled.connect(slot(self.toggle_probe))
            row2.addWidget(self.probe_button)
            row2.addWidget(button("Report", self.report, "LaTeX report with the renders, compiled to a PDF beside the document"))
            row2.addStretch(1)
            w.layout().addLayout(row2)
            self.probe_label = hint()
            w.layout().addWidget(self.probe_label)
            return w

        def _build_bands(self):
            """The band editor: one row per band (its upper safety factor, its colour), hidden until Bands…."""
            self.bands_box = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(self.bands_box)
            lay.setContentsMargins(0, 0, 0, 0)
            self.bands_table = QtWidgets.QTableWidget(0, 2)
            self.bands_table.setHorizontalHeaderLabels(["Safety factor below", "Colour"])
            self.bands_table.horizontalHeader().setStretchLastSection(True)
            self.bands_table.verticalHeader().setVisible(False)
            self.bands_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            lay.addWidget(self.bands_table)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(button("+ Band", self.add_band, "Split the top band: one more threshold"))
            row.addWidget(button("− Band", self.remove_band, "Merge the top two bands"))
            row.addWidget(button("Apply", self.apply_bands, "Use these bands for this study"))
            row.addStretch(1)
            lay.addLayout(row)
            row2 = QtWidgets.QHBoxLayout()
            row2.setSpacing(4)
            row2.addWidget(button("Save as default", self.save_bands_default,
                                  "New studies start with these bands (Preferences: Mod/BruceWorx/Sim)"))
            row2.addWidget(button("Load default", self.load_default_bands, "Put the default bands in the editor"))
            row2.addStretch(1)
            lay.addLayout(row2)
            self.bands_note = hint()
            lay.addWidget(self.bands_note)
            self.load_bands(*sim.study_bands(self.analysis))
            self.bands_box.setVisible(False)
            return self.bands_box

        def show_bands(self, on=True):
            if self.bands_button.isChecked() != bool(on):
                self.bands_button.setChecked(bool(on))  # comes back here through toggled
                return
            if on:
                self.load_bands(*sim.study_bands(self.analysis))
            self.bands_box.setVisible(bool(on))

        def load_bands(self, thresholds, colors):
            """Fills the editor: len(thresholds) + 1 rows; the last band has no upper bound."""
            self._band_colors = list(colors)
            table = self.bands_table
            table.setRowCount(len(thresholds) + 1)
            for i in range(len(thresholds) + 1):
                if i < len(thresholds):
                    spin = QtWidgets.QDoubleSpinBox()
                    spin.setRange(0.01, 10000.0)
                    spin.setDecimals(2)
                    spin.setSingleStep(0.25)
                    spin.setValue(thresholds[i])
                    table.setCellWidget(i, 0, spin)
                else:
                    table.removeCellWidget(i, 0)
                    item = QtWidgets.QTableWidgetItem("(above the last)")
                    item.setFlags(QtCore.Qt.ItemIsEnabled)
                    table.setItem(i, 0, item)
                swatch = QtWidgets.QPushButton(self._band_colors[i])
                swatch.setToolTip("Pick the band's colour")
                swatch.clicked.connect(lambda *_a, row=i, receiver=slot(self.pick_band_color): receiver(row))
                table.setCellWidget(i, 1, swatch)
                self._paint_swatch(i)
            table.setFixedHeight(table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(table.rowCount())) + 4)
            self.bands_note.setText("")

        def _paint_swatch(self, row):
            color = self._band_colors[row]
            swatch = self.bands_table.cellWidget(row, 1)
            swatch.setText(color)
            swatch.setStyleSheet(f"background:{color};color:{_text_on(color)};border:none;padding:2px")

        def bands_from_editor(self):
            """(thresholds, colours) as the editor shows them (not checked)."""
            thresholds = [self.bands_table.cellWidget(i, 0).value() for i in range(self.bands_table.rowCount() - 1)]
            return thresholds, list(self._band_colors)

        def set_band_color(self, row, color):
            self._band_colors[row] = sim._hex(color)
            self._paint_swatch(row)

        def pick_band_color(self, row):
            color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._band_colors[row]), None, "Band colour")
            if color.isValid():
                self.set_band_color(row, color.name())

        def add_band(self):
            thresholds, colors = self.bands_from_editor()
            top = thresholds[-1] if thresholds else 1.0
            thresholds.append(round(top * 2, 2) if top < 5000 else top + 1)
            colors.append(colors[-1])
            self.load_bands(thresholds, colors)

        def remove_band(self):
            thresholds, colors = self.bands_from_editor()
            if len(thresholds) <= 1:
                self.bands_note.setText("<span style='color:#8a6d00'>One threshold is the least: two bands.</span>")
                return
            thresholds.pop()
            colors.pop(-2)  # the top band keeps its colour
            self.load_bands(thresholds, colors)

        def apply_bands(self):
            """The editor's bands on the study; the field is recoloured when it shows the safety factor."""
            try:
                sim.set_study_bands(self.analysis, *self.bands_from_editor())
            except sim.StudyError as exc:
                self.bands_note.setText(f"<span style='color:#b00020'>{exc}</span>")
                return False
            self.bands_note.setText("Applied to this study.")
            if shown_field(self.analysis) == "safety":
                self.show("safety")
            else:
                self._refresh_summary()
            return True

        def save_bands_default(self):
            try:
                sim.save_default_bands(*self.bands_from_editor())
            except sim.StudyError as exc:
                self.bands_note.setText(f"<span style='color:#b00020'>{exc}</span>")
                return False
            self.bands_note.setText("Saved: new studies start with these bands.")
            return True

        def load_default_bands(self):
            self.load_bands(*sim.default_bands())
            self.bands_note.setText("The default bands; Apply to use them here.")

        def _measure_changed(self, *_a):
            key = self.measure_combo.currentData()
            if not key or key == sim.study_measure(self.analysis) and "StressMeasure" in self.analysis.PropertiesList:
                return
            sim.set_study_measure(self.analysis, key)
            self.say(f"Safety factor from the {sim.measure_label(key)} stress")
            if shown_field(self.analysis) == "safety":
                self.show("safety")
            self._refresh_summary()

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def release(self):
            stop_probe()
            if self.process is not None and _alive(self.process) and self.process.state() != QtCore.QProcess.NotRunning:
                self.process.kill()
            return super().release()

        # --- state
        def refresh(self):
            """The members list, mesh line and summary from the document."""
            if not _alive(self.members_list):
                return
            m = sim.members(self.analysis)
            self.members_list.clear()
            entries = [(f"Material: {o.Label}", o) for o in m["materials"]]
            entries += [(f"Support: {d['label']} ({d['kind']}) {d['value']} {d['where']}".strip(), o)
                        for o, d in ((o, sim._describe(o)) for o in m["supports"])]
            entries += [(f"Load: {d['label']} ({d['kind']}) {d['value']} on {d['where'] or 'nothing'}", o)
                        for o, d in ((o, sim._describe(o)) for o in m["loads"])]
            entries += [(f"{d['kind']}: {d['label']} {d['where']}".strip(), o)
                        for o, d in ((o, sim._describe(o)) for o in m["others"])]
            for text, obj in entries:
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, obj.Name)
                self.members_list.addItem(item)
            if not entries:
                self.members_list.addItem("No material, supports or loads yet")
            mesh = m["mesh"]
            if mesh is not None:
                self.size_spin.blockSignals(True)
                self.size_spin.setValue(float(mesh.MaxSize))
                self.size_spin.blockSignals(False)
                stats = sim.mesh_stats(mesh)
                if stats["nodes"]:
                    self.mesh_info.setText(f"{stats['nodes']} nodes, {stats['elements']} elements, {stats['order']} order, {stats['fineness']}")
                else:
                    self.mesh_info.setText("Not meshed yet (Solve meshes first)")
            else:
                self.mesh_info.setText("<span style='color:#b00020'>No mesh object</span>")
            self._refresh_summary()

        def _refresh_summary(self):
            result = sim.latest_result(self.analysis)
            if result is None:
                self.summary.setText("Not solved yet.")
                self.legend.setText("")
                return
            ext = sim.result_extrema(result)
            sf = ext["min_sf"]
            if sf is None:
                sf_text = "<span style='color:#8a6d00'>no safety factor (no strength value on the material)</span>"
            elif sf < 1:
                sf_text = f"<b style='color:#b00020'>{sf:.3g}</b> at node {ext['min_sf_node']} — below 1"
            else:
                sf_text = f"<b style='color:#1f7a46'>{sf:.3g}</b> at node {ext['min_sf_node']}"
            self.summary.setText(
                f"Max von Mises <b>{ext['max_vm']:.4g} MPa</b> (node {ext['max_vm_node']})<br>"
                f"Max displacement <b>{ext['max_disp']:.4g} mm</b> (node {ext['max_disp_node']})<br>"
                f"Min safety factor {sf_text}")
            kind = shown_field(self.analysis)
            self.legend.setText(legend_html(kind, result, self.analysis) if kind else "")

        def _edit_member(self, item):
            name = item.data(QtCore.Qt.UserRole)
            obj = self.doc.getObject(name) if name else None
            if obj is None or obj.ViewObject is None:
                return
            self._leave_and_return(lambda: obj.ViewObject.doubleClicked())

        # --- setup
        def add_constraint(self, command):
            """Starts the stock FEM constraint panel; this panel comes back when it closes."""
            import FreeCADGui as Gui

            set_active(self.analysis)
            self._leave_and_return(lambda: Gui.runCommand(command))

        def _leave_and_return(self, action):
            analysis = self.analysis
            close_dialog()
            self.release()
            action()
            schedule_reopen(analysis)

        def precheck(self):
            issues = sim.precheck(self.analysis)
            if not issues:
                self.issues.setText("<span style='color:#1f7a46'>Ready to solve.</span>")
            else:
                lines = []
                for kind, text in issues:
                    color = "#b00020" if kind == "error" else "#8a6d00"
                    lines.append(f"<span style='color:{color}'>{'✕' if kind == 'error' else '!'} {text}</span>")
                self.issues.setText("<br>".join(lines))
            return issues

        # --- mesh
        def _size_changed(self, value):
            mesh = sim.members(self.analysis)["mesh"]
            if mesh is not None and abs(float(mesh.MaxSize) - value) > 1e-9:
                mesh.MaxSize = value

        def mesh(self):
            try:
                stats = sim.mesh_study(self.analysis, self.say)
            except sim.StudyError as exc:
                self._fail(f"Mesh: {exc}")
                return None
            self.refresh()
            return stats

        def refine(self):
            try:
                stats = sim.refine_mesh(self.analysis)
            except sim.StudyError as exc:
                self._fail(f"Refine: {exc}")
                return None
            self.say(f"Refined: {stats['nodes']} nodes, {stats['elements']} elements")
            self.refresh()
            return stats

        # --- solve
        def say(self, text):
            if _alive(self.progress):
                self.progress.setText(text)
            status(text)
            try:
                import FreeCADGui as Gui

                Gui.updateGui()
            except Exception:
                pass

        def _fail(self, text):
            if _alive(self.progress):
                self.progress.setText(f"<span style='color:#b00020'>{text}</span>")
            _warn(text)

        def solve(self, blocking=False):
            """Meshes if needed, writes the input, runs ccx (in a QProcess unless blocking) and reads the results."""
            if self.process is not None and _alive(self.process) and self.process.state() != QtCore.QProcess.NotRunning:
                self.say("CalculiX is still running")
                return False
            issues = self.precheck()
            if not sim.ready(issues):
                self._fail("Not ready: " + "; ".join(t for k, t in issues if k == "error"))
                return False
            try:
                self.fea = sim.prepare_solve(self.analysis, progress=self.say)
            except sim.StudyError as exc:
                self._fail(f"Solve: {exc}")
                return False
            self.refresh()
            self.started = time.time()
            if blocking:
                self.say("Solving with CalculiX…")
                code = self.fea.ccx_run()
                return self._finished(code, QtCore.QProcess.NormalExit)
            self.fea.setup_ccx()
            if not getattr(self.fea, "ccx_binary_present", False):
                self._fail("CalculiX not found")
                return False
            prefs = FreeCAD_params(sim.CCX_PREFS)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.insert("OMP_NUM_THREADS", str(prefs.GetInt("AnalysisNumCPUs", QtCore.QThread.idealThreadCount())))
            env.insert("PASTIX_MIXED_PRECISION", "1" if getattr(self.fea.solver, "PastixMixedPrecision", False) else "0")
            self.process = QtCore.QProcess()
            self.process.setProcessEnvironment(env)
            self.process.setWorkingDirectory(os.path.dirname(self.fea.inp_file_name))
            self.process.finished.connect(slot(self._finished))
            self.process.errorOccurred.connect(slot(self._process_error))
            base = os.path.splitext(os.path.basename(self.fea.inp_file_name))[0]
            self.solve_button.setEnabled(False)
            self.say("Solving with CalculiX… (the window stays live)")
            self.process.start(self.fea.ccx_binary, ["-i", base])
            return True

        def _process_error(self, error):
            self.solve_button.setEnabled(True)
            self._fail(f"CalculiX did not run ({error})")

        def _finished(self, code, exit_status):
            if _alive(self.solve_button):
                self.solve_button.setEnabled(True)
            if exit_status != QtCore.QProcess.NormalExit or code:
                self._fail(f"CalculiX finished with exit code {code}")
                return False
            try:
                result = sim.finish_solve(self.fea, self.say)
            except sim.StudyError as exc:
                self._fail(f"Results: {exc}")
                return False
            self.say(f"Solved in {time.time() - self.started:.1f} s")
            self.refresh()
            self.show("safety" if getattr(result, "SafetyFactor", None) else "stress")
            return True

        # --- results
        def show(self, kind):
            if show_field(self.analysis, kind):
                self.legend.setText(legend_html(kind, sim.latest_result(self.analysis), self.analysis))
                return True
            return False

        def toggle_probe(self, on):
            if on:
                if sim.latest_result(self.analysis) is None:
                    self.probe_button.setChecked(False)
                    _warn("No results to probe yet")
                    return
                start_probe(self.analysis, self._probed)
            else:
                stop_probe()

        def _probed(self, reading):
            if _alive(self.probe_label):
                self.probe_label.setText(probe_text(reading))

        def report(self, folder=None):
            """Writes the report; the PDF compiles in the background and its link shows when done."""
            if sim.latest_result(self.analysis) is None:
                _warn("Solve the study before writing its report")
                return None
            self.say("Writing the report…")
            files = report_study(self.analysis, folder, done=slot(self._reported))
            if files.get("running"):
                self.probe_label.setText(f"Compiling {os.path.basename(files['tex'])}…")
            return files

        def _reported(self, files):
            if not _alive(self.probe_label):
                return
            self.probe_label.setOpenExternalLinks(True)
            if files.get("pdf"):
                self.probe_label.setText(f"Report: <a href='file://{files['pdf']}'>{os.path.basename(files['pdf'])}</a> "
                                         f"(source: <a href='file://{files['dir']}'>{os.path.basename(files['dir'])}</a>)")
            else:
                self.probe_label.setText(f"<span style='color:#8a6d00'>{files.get('message', '')}</span><br>"
                                         f"Source: <a href='file://{files['dir']}'>{os.path.basename(files['dir'])}</a>")

    return {"Panel": Panel, "NewStudyPanel": NewStudyPanel, "StudyPanel": StudyPanel}


def FreeCAD_params(path):
    import FreeCAD

    return FreeCAD.ParamGet(path)


# ----- opening the panels, coming back after a stock panel ----------------------------------------------


def open_new_study(doc=None):
    doc = doc or _doc()
    if doc is None:
        _warn("New Study needs an open document")
        return None
    panel = classes()["NewStudyPanel"](doc)
    if not panel.candidates:
        _warn("New Study: the document has no body or part with solids")
    return show_panel(panel)


def open_study_panel(analysis=None):
    analysis = analysis or current_study()
    if analysis is None:
        _warn("No study in the document: New Study first")
        return None
    set_active(analysis)
    return show_panel(classes()["StudyPanel"](analysis))


def schedule_reopen(analysis):
    """Reopens the study panel once no task dialog is open (the stock constraint panel was closed)."""
    from PySide import QtCore

    timer = _state.get("reopen")
    if timer is None or not _alive(timer):
        timer = QtCore.QTimer()
        timer.setInterval(300)
        timer.timeout.connect(_poll_reopen)
        _state["reopen"] = timer
    _state["reopen_for"] = analysis
    _state["reopen_wait"] = 2  # ticks before the first look: the stock panel opens after our close
    timer.start()


def _poll_reopen():
    import FreeCADGui as Gui

    analysis = _state.get("reopen_for")
    timer = _state.get("reopen")
    if _state.get("reopen_wait", 0) > 0:
        _state["reopen_wait"] -= 1
        return
    try:
        busy = Gui.Control.activeDialog() is not None
    except Exception:
        busy = False
    if busy:
        return
    if timer is not None:
        timer.stop()
    _state["reopen_for"] = None
    try:
        alive = analysis is not None and analysis.Document is not None and analysis.Document.getObject(analysis.Name) is not None
    except Exception:
        alive = False
    if alive:
        open_study_panel(analysis)


# ----- commands -----------------------------------------------------------------------------------------


def _with_doc():
    return _doc() is not None


def _with_study():
    return current_study() is not None


def _with_result():
    study = current_study()
    return study is not None and sim.latest_result(study) is not None


def run_precheck():
    panel = _state.get("panel")
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released:
        return panel.precheck()
    panel = open_study_panel()
    return panel.precheck() if panel is not None else None


def run_mesh():
    panel = open_study_panel()
    return panel.mesh() if panel is not None else None


def run_refine():
    panel = open_study_panel()
    return panel.refine() if panel is not None else None


def run_solve():
    panel = open_study_panel()
    return panel.solve() if panel is not None else None


def run_show(kind):
    study = current_study()
    if study is None:
        _warn("No study to show")
        return False
    panel = _state.get("panel")
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        return panel.show(kind)
    return show_field(study, kind)


def run_probe():
    study = current_study()
    if study is None:
        return False
    panel = _state.get("panel")
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        panel.probe_button.setChecked(not panel.probe_button.isChecked())
        return True
    if probing():
        stop_probe()
    else:
        start_probe(study)
    return True


def run_bands():
    panel = open_study_panel()
    if panel is not None:
        panel.show_bands(True)
    return panel


def run_report():
    study = current_study()
    if study is None:
        return None
    panel = _state.get("panel")
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        return panel.report()
    return report_study(study, done=lambda files: None)


COMMANDS = (
    ("BruceWorx_NewStudy", "New Study", os.path.join(HERE, "BruceWorx_NewStudy.svg"),
     "Set up a simulation study: type, model, material, mesh; then its loads and constraints", _with_doc, open_new_study),
    ("BruceWorx_Study", "Study", os.path.join(HERE, "BruceWorx_NewStudy.svg"),
     "The study's loads, constraints, mesh, solve and results panel", _with_study, open_study_panel),
    ("BruceWorx_StudyPreCheck", "Pre-check", os.path.join(HERE, "BruceWorx_PreCheck.svg"),
     "List what the study still needs before it can solve", _with_study, run_precheck),
    ("BruceWorx_StudyMesh", "Mesh", "FEM_MeshNetgenFromShape",
     "Generate the study's Netgen mesh", _with_study, run_mesh),
    ("BruceWorx_StudyRefine", "Refine", "FEM_MeshRegion",
     "Element size × 0.7 and mesh again, for a convergence check", _with_study, run_refine),
    ("BruceWorx_SolveStudy", "Solve", "FEM_SolverRun",
     "Mesh if needed, run CalculiX, read the results and the safety factor", _with_study, run_solve),
    ("BruceWorx_ShowStress", "Stress", "FEM_ResultShow",
     "Colour the result by von Mises stress", _with_result, lambda: run_show("stress")),
    ("BruceWorx_ShowDisplacement", "Displacement", "FEM_PostFilterWarp",
     "Colour the result by displacement, shape exaggerated", _with_result, lambda: run_show("displacement")),
    ("BruceWorx_ShowSafetyFactor", "Safety Factor", os.path.join(HERE, "BruceWorx_SafetyFactor.svg"),
     "Colour the result by safety factor: red below 1", _with_result, lambda: run_show("safety")),
    ("BruceWorx_StudyProbe", "Probe", os.path.join(HERE, "BruceWorx_Probe.svg"),
     "Read stress, displacement and safety factor at a clicked point", _with_result, run_probe),
    ("BruceWorx_SafetyFactorBands", "SF Bands", os.path.join(HERE, "BruceWorx_SafetyFactor.svg"),
     "Safety-factor bands of the study: thresholds and colours, saved as the default if you like", _with_study, run_bands),
    ("BruceWorx_StudyReport", "Report", os.path.join(HERE, "BruceWorx_StudyReport.svg"),
     "Write the study report (LaTeX with renders, compiled to a PDF) beside the document", _with_result, run_report),
)


class _Command:
    def __init__(self, name, text, icon, tip, active, handler):
        self.name, self.text, self.icon, self.tip, self.active, self.handler = name, text, icon, tip, active, handler

    def GetResources(self):
        return {"Pixmap": self.icon, "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        try:
            return bool(self.active())
        except Exception:
            return False

    def Activated(self):
        self.handler()


def install():
    import FreeCADGui as Gui

    if _state["installed"]:
        return
    for name, text, icon, tip, active, handler in COMMANDS:
        Gui.addCommand(name, _Command(name, text, icon, tip, active, handler))
    _state["installed"] = True
