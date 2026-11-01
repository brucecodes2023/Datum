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
import bruceworx_sim_eigen as eigen
import bruceworx_sim_post as post
import bruceworx_sim_thermal as thermal
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_TITLE = "Study"
CONSTRAINT_COMMANDS = (
    ("Fixed", "FEM_ConstraintFixed"),
    ("Force", "FEM_ConstraintForce"),
    ("Pressure", "FEM_ConstraintPressure"),
    ("Displacement", "FEM_ConstraintDisplacement"),
)
THERMAL_BUTTONS = (("Temperature", "temperature"), ("Heat flux", "flux"), ("Heat source", "source"),
                   ("Convection", "convection"))
RENDER_SIZE = (900, 600)
TYPE_HINTS = {
    "static": "Static stress: linear elastic, small displacements (CalculiX).",
    "modal": "Modal frequencies: K φ = ω² M φ (CalculiX *FREQUENCY). Needs a density; no supports = free-free "
             "(the six rigid-body modes are skipped). Loads count only when Prestressed.",
    "thermal": "Thermal: heat conduction in the part (CalculiX *HEAT TRANSFER), steady state or a warm-up over time. "
               "Needs k (and rho, cp for a transient run) and a Temperature or Convection on a face. Temperatures are "
               "banded against the material's heat deflection temperature.",
    "thermal_stress": "Thermal stress, one way: the steady temperature field, then the stress it causes (alpha (T - T_ref)). "
                      "Needs k and alpha, a Temperature or Convection, and supports.",
    "prop_wash": "Prop wash (OpenFOAM simpleFoam): each propeller is an actuator disk with its hover thrust from the "
                 "Mass & Balance; the study reports the wake speed at the disks and the force on every part, "
                 "especially the download on the arms and frame. Needs motors marked with a ThrustAxis, the "
                 "propeller diameter and OpenFOAM-v2606.app.",
    "external_flow": "External flow (OpenFOAM simpleFoam): drag and lift on the model at a speed and angle of attack, "
                     "Cd and Cl on a reference area, surface pressure and the flow on a plane. Needs OpenFOAM-v2606.app.",
    "buckling": "Structural buckling: load factors λ under the study's loads (CalculiX *BUCKLE). Linear buckling is "
                "non-conservative; the nonlinear run seeds an imperfection from mode 1.",
}

_state = {"installed": False, "panel": None, "reopen": None, "probe": None, "shown": {}, "classes": None, "mode": {},
          "range": {}}
MODE_FPS = 25  # mode playback: frames per second, one cycle per second


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
    """{(id, ...): (r, g, b)} per band, the form ViewProviderFemMesh.NodeColor and ElementColor take (one
    entry per band); nodes are node or element ids."""
    thresholds, colors = bands
    groups = {}
    for node, sf in zip(nodes, factors):
        groups.setdefault(sim.band_index(sf, thresholds), []).append(node)
    return {tuple(ids): sim.rgb(colors[i]) for i, ids in groups.items()}


def _brittle(uuid):
    """True for a Vector Labs card marked brittle (acrylic, CFRP, G10: the materials agent's BruceWorx block)."""
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
        return (f"<b>Safety factor</b> = allowable / {sim.measure_label(measure)} stress per node; each element "
                f"in the band of its lowest node<br>{chips}{basis}")
    if kind == "temperature":
        data = thermal.stored(analysis or sim.study_of(result))
        return (f"<b>Temperature</b>, °C: blue {data.get('tmin', 0):.1f} to red {data.get('tmax', 0):.1f}" if data
                else "<b>Temperature</b>, °C")
    if kind == "tempbands":
        analysis = analysis or sim.study_of(result)
        edges, colors = thermal.bands(analysis)
        if not edges:
            return "No limit: the material has no heat deflection temperature"
        names = (f"below {edges[0]:.0f} °C", f"{edges[0]:.0f} to {edges[1]:.0f} °C", f"{edges[1]:.0f} °C and above")
        chips = "".join(f"<span style='background:{c};color:{_text_on(c)};padding:1px 6px;margin-right:4px'>&nbsp;{n}&nbsp;</span>"
                        for c, n in zip(colors, names))
        _limit, how = thermal.hdt_limit(analysis)
        return (f"<b>Temperature against the limit</b> ({how}); each element in the band of its hottest node<br>{chips}")
    if kind in SCALAR_KEYS and analysis is not None and _custom_paint(analysis, kind):
        return scalar_legend(analysis, kind)
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
    if kind in ("temperature", "tempbands"):
        return show_temperature(analysis, kind, result)
    if kind in SCALAR_KEYS and _custom_paint(analysis, kind):
        return show_scalar(analysis, kind, result)
    _remove_section_view(analysis)
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
        # crisp bands (30 Oct follow-up 1): each element in the band of its lowest node, so a band edge is an
        # element edge instead of a blur across one element. ElementColor fills ElementColorArray, which the
        # view provider paints only in the ByElement colour mode.
        elements = sim.element_min_factors(result)
        if elements:
            vobj.ElementColor = band_colors(list(elements), list(elements.values()), sim.study_bands(analysis))
            _color_mode(vobj, "ByElement")
        else:
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


# ----- the rotor contours (SIM-04): cylindrical stress, colour maps, fixed range, section ------------------------------

# the shown kinds that colour with the study's own colour map and range: kind -> bruceworx_sim_post field key
SCALAR_KEYS = {"stress": "vonMises", "displacement": "displacement", "radial": "radial", "hoop": "hoop", "axial": "axial",
               "shear": "shear"}


def _custom_paint(analysis, kind):
    """True when a field is painted by show_scalar: a cylindrical stress always; von Mises and displacement when the study
    has another colour map, a fixed range or a section (otherwise FEM's own scalar colouring, as before)."""
    key = SCALAR_KEYS[kind]
    return (key in post.CYLINDRICAL or post.study_colormap(analysis) != post.DEFAULT_COLORMAP
            or post.fixed_range(analysis, key) is not None or post.study_section(analysis) != "none")


def scalar_legend(analysis, kind):
    shown = _state["range"].get(analysis.Name)
    key = SCALAR_KEYS[kind]
    label, unit, _prop = post.FIELDS[key]
    if not shown or shown[0] != key:
        return f"<b>{label}</b>, {unit}"
    _key, lo, hi, cmap, pinned = shown
    note = " (fixed range)" if pinned else ""
    return f"<b>{label}</b>, {unit}, {cmap}{note}<br>{post.legend_chips(cmap, lo, hi)}"


def _remove_section_view(analysis):
    """Drops the section mesh object, so the whole result mesh shows."""
    obj = analysis.Document.getObject("SectionMesh_" + analysis.Name)
    if obj is not None:
        try:
            analysis.Document.removeObject(obj.Name)
        except Exception:
            pass


def show_scalar(analysis, kind, result=None):
    """Colours the result mesh by a per-node field with the study's colour map (Rainbow, Turbo, Jet) between the data's range
    or the study's fixed range for the field, optionally cut open (half or quarter section about the axis).
    kind: stress, displacement, radial, hoop, axial, shear.  Returns True when drawn."""
    result = result or sim.latest_result(analysis)
    if result is None:
        _warn("No results yet: solve the study first")
        return False
    key = SCALAR_KEYS[kind]
    try:
        values = post.field_values(result, key, analysis)
    except (post.PostError, sim.StudyError) as exc:
        _warn(str(exc))
        return False
    nodes = list(result.NodeNumbers)
    vectors = list(result.DisplacementVectors)
    has_vectors = len(vectors) == len(nodes) and bool(nodes)
    cmap = post.study_colormap(analysis)
    pinned = post.fixed_range(analysis, key)
    lo, hi = pinned or (min(values), max(values))
    if not hi > lo:
        lo, hi = lo - 0.5 * (abs(lo) or 1.0), hi + 0.5 * (abs(hi) or 1.0)
    mode = post.study_section(analysis)
    _hide_for_result(analysis, result)
    target = result.Mesh
    keep = None
    if mode != "none":
        try:
            target, keep = post.section_mesh(analysis, result, mode)
        except (post.PostError, sim.StudyError) as exc:
            _warn(f"Section: {exc}")
            mode = "none"
    if mode == "none":
        _remove_section_view(analysis)
    else:
        result.Mesh.ViewObject.Visibility = False
        analysis.Document.recompute()
        target.ViewObject.Visibility = True
        try:
            target.ViewObject.DisplayMode = "Faces & Wireframe"
        except Exception:
            pass
    vobj = target.ViewObject
    pick = [i for i, n in enumerate(nodes) if keep is None or n in keep]
    sel_nodes = [nodes[i] for i in pick]
    if has_vectors:
        vobj.setNodeDisplacementByVectors(sel_nodes, [vectors[i] for i in pick])
    _color_mode(vobj, "ByNode")
    vobj.NodeColor = post.node_colors([values[i] for i in pick], lo, hi, cmap, ids=sel_nodes)
    if has_vectors:
        if kind == "displacement":
            from femtaskpanels.task_result_mechanical import get_displacement_scale_factor

            vobj.applyDisplacement(float(get_displacement_scale_factor(result)))
        else:
            vobj.applyDisplacement(0.0)
    _state["shown"][analysis.Name] = kind
    _state["range"][analysis.Name] = (key, lo, hi, cmap, pinned is not None)
    try:
        analysis.Document.recompute()
    except Exception:
        pass
    return True


def show_temperature(analysis, kind, result):
    """Colours the result mesh by temperature ('temperature', smooth) or by the bands against the heat
    deflection temperature ('tempbands': each element in the band of its hottest node)."""
    if "TempC" not in result.PropertiesList or not len(result.TempC):
        _warn("This result has no temperatures")
        return False
    nodes = list(result.NodeNumbers)
    vobj = result.Mesh.ViewObject
    _hide_for_result(analysis, result)
    if kind == "temperature":
        _color_mode(vobj, "Overall")
        vobj.setNodeColorByScalars(nodes, list(result.TempC))
    else:
        edges, colors = thermal.bands(analysis)
        if not edges:
            _warn("The material has no heat deflection temperature: set a limit on the study to see the bands")
            return False
        elements = thermal.element_max_temps(result)
        groups = {}
        for elem, celsius in elements.items():
            groups.setdefault(thermal.band_index_c(celsius, edges), []).append(elem)
        vobj.ElementColor = {tuple(ids): sim.rgb(colors[i]) for i, ids in groups.items()}
        _color_mode(vobj, "ByElement")
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


# ----- mode shapes (modal and buckling) ------------------------------------------------------------


def _hide_other_results(analysis, result):
    for other in sim.members(analysis)["results"]:
        mesh = getattr(other, "Mesh", None)
        if other is not result and mesh is not None and mesh is not result.Mesh and mesh.ViewObject is not None:
            mesh.ViewObject.Visibility = False


def show_mode(analysis, result, factor=None):
    """Shows a mode shape: the result mesh deformed by factor x the mode (the study's scale, 10 % of the
    bounding-box diagonal, when None), coloured by the mode's displacement. Returns the factor."""
    nodes = list(result.NodeNumbers)
    vectors = list(result.DisplacementVectors)
    _hide_for_result(analysis, result)
    _hide_other_results(analysis, result)
    vobj = result.Mesh.ViewObject
    if not nodes or len(vectors) != len(nodes):
        _warn("The mode has no shape")
        return 0.0
    vobj.setNodeDisplacementByVectors(nodes, vectors)
    _color_mode(vobj, "Overall")
    vobj.setNodeColorByScalars(nodes, list(result.DisplacementLengths))
    factor = eigen.mode_scale(analysis, result) if factor is None else float(factor)
    vobj.applyDisplacement(factor)
    _state["shown"][analysis.Name] = "mode"
    _state["mode"][analysis.Name] = result.Name
    return factor


def shown_mode(analysis):
    name = _state["mode"].get(analysis.Name)
    return analysis.Document.getObject(name) if name else None


def show_study_mesh(analysis):
    """The study's own mesh (with the supports' symbols) for the report: no result shown."""
    m = sim.members(analysis)
    for result in m["results"]:
        mesh = getattr(result, "Mesh", None)
        if mesh is not None and mesh.ViewObject is not None:
            mesh.ViewObject.Visibility = False
    for obj in _model_objects(analysis):
        if obj.ViewObject is not None:
            obj.ViewObject.Visibility = obj is m["mesh"]
    if m["mesh"] is not None and m["mesh"].ViewObject is not None:
        try:
            m["mesh"].ViewObject.DisplayMode = "Faces & Wireframe"
        except Exception:
            pass
    for obj in m["supports"] + m["loads"]:
        if obj.ViewObject is not None:
            obj.ViewObject.Visibility = True
    _state["shown"][analysis.Name] = "mesh"


class ModePlayer:
    """Plays a mode in the 3D view: u(t) = s phi sin(2 pi t), one cycle per second (a QTimer moving the
    result mesh's nodes, as the static study's deformed view does)."""

    def __init__(self, fps=MODE_FPS, period=1.0):
        self.fps, self.period = fps, period
        self.timer = None
        self.analysis = self.result = None
        self.scale = 0.0
        self.frame = 0

    def start(self, analysis, result, scale=None):
        from PySide import QtCore

        self.stop()
        self.analysis, self.result = analysis, result
        self.scale = show_mode(analysis, result, scale)
        self.frame = 0
        if self.timer is None:
            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(slot(self.tick))
        self.timer.setInterval(max(5, int(round(1000.0 / self.fps))))
        self.timer.start()

    def tick(self):
        result = self.result
        try:
            vobj = result.Mesh.ViewObject if result is not None else None
        except Exception:  # the result was deleted (a new solve)
            vobj = None
        if vobj is None:
            self.stop()
            return
        self.frame += 1
        vobj.applyDisplacement(self.scale * eigen.phase_factor(self.frame, self.fps * self.period))

    def playing(self):
        return self.timer is not None and _alive(self.timer) and self.timer.isActive()

    def stop(self):
        if self.timer is not None and _alive(self.timer):
            self.timer.stop()
        try:
            if self.result is not None and self.result.Mesh.ViewObject is not None:
                self.result.Mesh.ViewObject.applyDisplacement(self.scale)  # rest at the peak
        except Exception:
            pass


def _mode_of_selection(selection):
    """The mode result a selection points at (a mode result or its mesh, of a modal or buckling study)."""
    for obj in selection or []:
        candidates = [obj] + [o for o in getattr(obj, "InList", []) or [] if o.isDerivedFrom("Fem::FemResultObject")]
        for cand in candidates:
            if cand.isDerivedFrom("Fem::FemResultObject") and int(getattr(cand, "Eigenmode", 0) or 0) > 0:
                study = sim.study_of(cand)
                if study is not None and eigen.is_eigen(study):
                    return cand
    return None


def open_mode_timeline(result, return_to_study=False):
    """A mode shape on the Motion Study timeline (play, scrub, Save frames to PNG and MP4); with
    return_to_study the study panel comes back when it closes."""
    analysis = sim.study_of(result)
    if analysis is None:
        return None
    return show_panel(classes()["ModeTimelinePanel"](analysis, result, return_to_study))


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
        reading["point"] = tuple(point[:3])
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
    if reading.get("temperature") is not None and reading.get("von_mises") is None:
        return f"Node {reading['node']}: {reading['temperature']:.2f} °C"
    sf = reading.get("safety_factor")
    sf_text = "–" if sf is None else ("> 100" if sf >= sim.SF_CAP else f"{sf:.3g}")
    temp = f", {reading['temperature']:.2f} °C" if reading.get("temperature") is not None else ""
    return (f"Node {reading['node']}: von Mises {reading['von_mises']:.4g} MPa, "
            f"displacement {reading['displacement']:.4g} mm, safety factor {sf_text}{temp}")


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
    if eigen.is_eigen(analysis):
        return render_modes(analysis, folder, view, size)
    images = {}
    before = shown_field(analysis)
    try:
        animated = view.isAnimationEnabled()
        view.setAnimationEnabled(False)
    except Exception:
        animated = None
    try:
        kinds = ("stress", "displacement", "safety")
        if thermal.is_thermal(analysis):
            kinds = tuple(k for k, _c in thermal.figures_for(thermal.summary_part(analysis)))
        for kind in kinds:
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
        if before and before not in ("safety", "tempbands"):
            show_field(analysis, before, result)
    return images


def render_modes(analysis, folder, view, size=RENDER_SIZE):
    """Off-screen renders of a modal or buckling study: the mesh (modal) and the report's modes, each at
    its scaled peak: {"mesh": path, "mode1": path, ...}."""
    import FreeCADGui as Gui

    part = eigen.summary_part(analysis)
    results = eigen.mode_results(analysis)
    before = shown_mode(analysis)
    images = {}
    try:
        animated = view.isAnimationEnabled()
        view.setAnimationEnabled(False)
    except Exception:
        animated = None
    try:
        for kind, _caption in eigen.figures_for(part):
            if kind == "mesh":
                show_study_mesh(analysis)
            else:
                row = dict(eigen.figure_rows(part))[kind]
                show_mode(analysis, results[row["index"] - 1])
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
        if before is not None:
            show_mode(analysis, before)
    return images


def report_study(analysis, folder=None, done=None):
    """Writes the LaTeX report with three renders and compiles it to a PDF.

    Blocking when ``done`` is None: returns write_report's {"dir", "tex", "pdf", "message"}. With a
    callback the compiler runs in a QProcess (Tectonic's first run fetches its TeX files, ~30 s) and
    ``done(files)`` is called when it finishes; the return value then has "pdf": None, "running": True.
    """
    with tempfile.TemporaryDirectory(prefix="bw-sim-render-") as tmp:
        images = {} if sim.study_kind(analysis) in sim.AIRFLOW_KINDS else render_fields(analysis, tmp)
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

    def count_spin(value, maximum=200):
        spin = QtWidgets.QSpinBox()
        spin.setRange(1, maximum)
        spin.setValue(int(value))
        return spin

    def hz_spin(value=0.0):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.0, 1.0e7)
        spin.setDecimals(1)
        spin.setSuffix(" Hz")
        spin.setSpecialValueText("none")
        spin.setValue(float(value))
        return spin

    def temp_spin(value, suffix, low, high, decimals):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(float(value))
        return spin

    def target_spin(value):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.1, 1000.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.5)
        spin.setValue(float(value))
        spin.setToolTip("Target buckling load factor: lambda_1 red below 1, amber below the target, green above")
        return spin

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
            self._type_changed()

        # --- boxes
        def _build_study(self):
            w = box("Study", os.path.join(HERE, "BruceWorx_NewStudy.svg"))
            lay = form_layout()
            self.name_edit = QtWidgets.QLineEdit(f"Study {len(sim.studies(self.doc)) + 1}")
            lay.addRow("Name", self.name_edit)
            self.type_combo = QtWidgets.QComboBox()
            air_group = False
            for key, label, available in sim.STUDY_TYPES:
                if key in sim.AIRFLOW_KINDS and not air_group:  # the Airflow group heading
                    air_group = True
                    self.type_combo.addItem("— Airflow —", "")
                    head = self.type_combo.model().item(self.type_combo.count() - 1)
                    head.setFlags(head.flags() & ~QtCore.Qt.ItemIsEnabled)
                self.type_combo.addItem(label if available else f"{label}  (planned)", key)
                if not available:
                    item = self.type_combo.model().item(self.type_combo.count() - 1)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            lay.addRow("Type", self.type_combo)
            w.layout().addLayout(lay)
            # modal: modes, range, prestressed; buckling: modes, target, nonlinear (shown by type)
            self.modal_box = QtWidgets.QWidget()
            mlay = form_layout()
            self.modal_box.setLayout(mlay)
            self.modal_count = count_spin(eigen.default_modes("modal"))
            mlay.addRow("Modes", self.modal_count)
            self.fmin_spin, self.fmax_spin = hz_spin(), hz_spin()
            rng = QtWidgets.QHBoxLayout()
            rng.addWidget(self.fmin_spin)
            rng.addWidget(QtWidgets.QLabel("to"))
            rng.addWidget(self.fmax_spin)
            mlay.addRow("Range", rng)
            self.prestressed_check = QtWidgets.QCheckBox("Prestressed (a static step with the loads first)")
            mlay.addRow("", self.prestressed_check)
            w.layout().addWidget(self.modal_box)
            self.buckling_box = QtWidgets.QWidget()
            blay = form_layout()
            self.buckling_box.setLayout(blay)
            self.buckling_count = count_spin(eigen.default_modes("buckling"))
            blay.addRow("Load factors", self.buckling_count)
            self.target_spin = target_spin(eigen.default_target())
            blay.addRow("Target factor", self.target_spin)
            self.nonlinear_check = QtWidgets.QCheckBox("Nonlinear run too (imperfection from mode 1)")
            blay.addRow("", self.nonlinear_check)
            w.layout().addWidget(self.buckling_box)
            self.thermal_box = QtWidgets.QWidget()
            tlay = form_layout()
            self.thermal_box.setLayout(tlay)
            self.transient_check = QtWidgets.QCheckBox("Transient (warm-up or cool-down over time)")
            tlay.addRow("", self.transient_check)
            self.end_spin = temp_spin(thermal.DEFAULT_END_TIME, " s", 0.1, 1.0e7, 1)
            tlay.addRow("End time", self.end_spin)
            self.step_spin = temp_spin(thermal.DEFAULT_STEP, " s", 0.001, 1.0e6, 3)
            tlay.addRow("Time step", self.step_spin)
            self.initial_spin = temp_spin(thermal.DEFAULT_AMBIENT, " °C", -273.0, 5000.0, 1)
            self.initial_spin.setToolTip("The temperature the whole part starts at")
            tlay.addRow("Initial temperature", self.initial_spin)
            self.reference_spin = temp_spin(thermal.DEFAULT_AMBIENT, " °C", -273.0, 5000.0, 1)
            self.reference_spin.setToolTip("Thermal stress: the stress-free temperature T_ref")
            self.reference_label = QtWidgets.QLabel("Stress-free T_ref")
            tlay.addRow(self.reference_label, self.reference_spin)
            self.transient_check.toggled.connect(slot(self._type_changed))
            w.layout().addWidget(self.thermal_box)
            import bruceworx_cfd_gui as cfd_gui

            self.air_box = cfd_gui.classes()["NewStudyBox"]()
            w.layout().addWidget(self.air_box.widget)
            self.type_hint = hint()
            w.layout().addWidget(self.type_hint)
            self.type_combo.currentIndexChanged.connect(slot(self._type_changed))
            return w

        def _type_changed(self, *_a):
            kind = self.type_combo.currentData() or "static"
            self.modal_box.setVisible(kind == "modal")
            self.buckling_box.setVisible(kind == "buckling")
            self.thermal_box.setVisible(kind in thermal.KINDS)
            air = kind in sim.AIRFLOW_KINDS
            self.air_box.widget.setVisible(air)
            self.air_box.set_kind(kind)
            for part in self.form[2:]:  # no material or CalculiX mesh for airflow
                part.setVisible(not air)
            self.transient_check.setVisible(kind == "thermal")
            for widget in (self.end_spin, self.step_spin):
                widget.setEnabled(kind == "thermal" and self.transient_check.isChecked())
            self.reference_label.setVisible(kind == "thermal_stress")
            self.reference_spin.setVisible(kind == "thermal_stress")
            for widget in (self.measure_label, self.measure_combo):
                widget.setVisible(kind in ("static", "thermal_stress"))
            self.type_hint.setText(TYPE_HINTS.get(kind, ""))

        def options(self):
            """The study's modal or buckling settings from the panel."""
            kind = self.type_combo.currentData() or "static"
            if kind == "modal":
                return {"ModeCount": self.modal_count.value(), "FrequencyMin": self.fmin_spin.value(),
                        "FrequencyMax": self.fmax_spin.value(), "Prestressed": self.prestressed_check.isChecked()}
            if kind == "buckling":
                return {"ModeCount": self.buckling_count.value(), "TargetLoadFactor": self.target_spin.value(),
                        "Nonlinear": self.nonlinear_check.isChecked()}
            if kind == "thermal":
                return {"Transient": self.transient_check.isChecked(), "EndTime": self.end_spin.value(),
                        "TimeStep": self.step_spin.value(), "InitialTemperature": self.initial_spin.value()}
            if kind == "thermal_stress":
                return {"InitialTemperature": self.reference_spin.value(),
                        "ReferenceTemperature": self.reference_spin.value()}
            if kind in sim.AIRFLOW_KINDS:
                return self.air_box.options()
            return {}

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
            self.measure_label = QtWidgets.QLabel("Safety factor from")
            lay.addRow(self.measure_label, self.measure_combo)
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
            self.size_spin.setToolTip(f"Largest element: the bounding-box diagonal / {sim.mesh_divisor():g} by default")
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
                options=self.options(),
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
            if plan.study_type in sim.AIRFLOW_KINDS:
                status(f"Airflow study '{plan.name}' created: Pre-check, then Run")
                return analysis
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
            self.nl_process = None
            self.nl_info = None
            self.started = 0.0
            self.kind = sim.study_kind(analysis)
            self.player = ModePlayer()
            eigen.ensure_properties(analysis)
            if thermal.is_thermal(analysis):
                thermal.ensure_properties(analysis)
            results = (self._build_results() if self.kind == "static" else
                       self._build_thermal() if self.kind in thermal.KINDS else self._build_modes())
            self.form = [self._build_setup(), self._build_mesh(), self._build_solve(), results]
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
            if self.kind != "thermal":
                for text, command in CONSTRAINT_COMMANDS:
                    row.addWidget(button(f"+ {text}", lambda cmd=command: self.add_constraint(cmd),
                                         f"Add a {text} constraint (the stock FEM panel; this one comes back after)"))
                if self.kind in ("static", "thermal_stress"):
                    row.addWidget(button("+ Spin", self.add_spin,
                                         "A spinning load: centrifugal force at an rpm about the axis of the selected circular edge "
                                         "or cylindrical face (else the model's bore or rim)"))
                    row.addWidget(button("+ Hold for spin", self.hold_for_spin,
                                         "Holds a spinning wheel so it can still grow: axial-only on the selected flat face "
                                         "(else the biggest one square to the axis) plus a minimal 3-2-1 point set. A Fixed "
                                         "bore would stop the bore growing and kill its hoop stress"))
                    row.addWidget(button("+ Blade mass", self.add_blade_mass,
                                         "The pull of the blades on the selected rim face(s): blade count, mass and radius of the "
                                         "centre of mass; F = m r omega^2 per blade at the spin speed, spread over the faces"))
                w.layout().addLayout(row)
            if self.kind in thermal.KINDS:
                row_t = QtWidgets.QHBoxLayout()
                row_t.setSpacing(4)
                for text, key in THERMAL_BUTTONS:
                    row_t.addWidget(button(f"+ {text}", lambda k=key: self.add_thermal(k),
                                           f"Add a {text.lower()} on the faces (or body) selected in the 3D view"))
                w.layout().addLayout(row_t)
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
            if self.kind in ("modal", "buckling"):
                w.layout().addWidget(self._build_eigen_options())
            if self.kind in thermal.KINDS:
                w.layout().addWidget(self._build_thermal_options())
            self.progress = hint()
            w.layout().addWidget(self.progress)
            return w

        def _build_thermal_options(self):
            """The thermal settings of the study (they apply at the next Solve)."""
            a = self.analysis
            box_ = QtWidgets.QWidget()
            lay = form_layout()
            box_.setLayout(lay)
            self.transient_check = QtWidgets.QCheckBox("Transient")
            self.transient_check.setChecked(bool(a.Transient))
            self.transient_check.setVisible(self.kind == "thermal")
            lay.addRow("", self.transient_check)
            self.end_spin = temp_spin(a.EndTime, " s", 0.1, 1.0e7, 1)
            self.step_spin = temp_spin(a.TimeStep, " s", 0.001, 1.0e6, 3)
            self.initial_spin = temp_spin(a.InitialTemperature, " °C", -273.0, 5000.0, 1)
            self.reference_spin = temp_spin(a.ReferenceTemperature, " °C", -273.0, 5000.0, 1)
            lay.addRow("End time", self.end_spin)
            lay.addRow("Time step", self.step_spin)
            lay.addRow("Initial temperature", self.initial_spin)
            if self.kind == "thermal_stress":
                lay.addRow("Stress-free T_ref", self.reference_spin)
            self.limit_spin = temp_spin(a.HDTLimit, " °C", 0.0, 5000.0, 1)
            self.limit_spin.setSpecialValueText("the card's HDT")
            self.limit_spin.setToolTip("The temperature limit the results are banded against; 0 = the material card's heat "
                                       "deflection temperature")
            self.margin_spin = temp_spin(a.TempMargin, " K", 0.1, 1000.0, 1)
            self.margin_spin.setToolTip("Amber from this many kelvin below the limit")
            lay.addRow("Limit", self.limit_spin)
            lay.addRow("Amber within", self.margin_spin)
            for widget in (self.end_spin, self.step_spin, self.initial_spin, self.reference_spin, self.limit_spin,
                           self.margin_spin):
                widget.valueChanged.connect(slot(self._thermal_options_changed))
            self.transient_check.toggled.connect(slot(self._thermal_options_changed))
            self._thermal_enable()
            return box_

        def _thermal_enable(self):
            on = self.kind == "thermal" and self.transient_check.isChecked()
            self.end_spin.setEnabled(on)
            self.step_spin.setEnabled(on)

        def _thermal_options_changed(self, *_a):
            a = self.analysis
            a.Transient = self.transient_check.isChecked() and self.kind == "thermal"
            a.EndTime, a.TimeStep = self.end_spin.value(), self.step_spin.value()
            a.InitialTemperature, a.ReferenceTemperature = self.initial_spin.value(), self.reference_spin.value()
            a.HDTLimit, a.TempMargin = self.limit_spin.value(), self.margin_spin.value()
            self._thermal_enable()
            if self.kind == "thermal_stress":
                a.InitialTemperature = a.ReferenceTemperature
            try:
                thermal.configure_solver(a, sim.members(a)["solver"])
            except thermal.ThermalError as exc:
                self.progress.setText(f"<span style='color:#b00020'>{exc}</span>")
            self._refresh_summary()

        def add_thermal(self, key):
            """Adds a thermal setup object on the selection (a dialog asks for the value)."""
            if add_thermal_object(key, self.analysis):
                self.refresh()

        def _build_thermal(self):
            """The thermal results: temperature and bands, the time step, the numbers, the probe, the report."""
            w = box("Results", os.path.join(HERE, "BruceWorx_SafetyFactor.svg"))
            self.summary = hint("Not solved yet.")
            w.layout().addWidget(self.summary)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(button("Temperature", lambda: self.show("temperature"), "Colour by temperature"))
            row.addWidget(button("HDT bands", lambda: self.show("tempbands"),
                                 "Colour against the heat deflection temperature: red above, amber within the margin"))
            w.layout().addLayout(row)
            if self.kind == "thermal_stress":
                row_s = QtWidgets.QHBoxLayout()
                row_s.setSpacing(4)
                row_s.addWidget(button("Stress", lambda: self.show("stress"), "Colour by von Mises stress"))
                row_s.addWidget(button("Displacement", lambda: self.show("displacement"), "Colour by displacement, exaggerated"))
                row_s.addWidget(button("Safety Factor", lambda: self.show("safety"), "Colour by safety factor"))
                w.layout().addLayout(row_s)
                lay = form_layout()
                self.measure_combo = measure_combo(sim.study_measure(self.analysis))
                self.measure_combo.currentIndexChanged.connect(slot(self._measure_changed))
                lay.addRow("Safety factor from", self.measure_combo)
                w.layout().addLayout(lay)
            lay_t = form_layout()
            self.time_combo = QtWidgets.QComboBox()
            self.time_combo.currentIndexChanged.connect(slot(self._time_changed))
            self.time_label = QtWidgets.QLabel("Time step")
            lay_t.addRow(self.time_label, self.time_combo)
            w.layout().addLayout(lay_t)
            self.legend = hint()
            w.layout().addWidget(self.legend)
            row2 = QtWidgets.QHBoxLayout()
            self.probe_button = QtWidgets.QPushButton("Probe")
            self.probe_button.setCheckable(True)
            self.probe_button.setToolTip("Click a point on the result mesh to read its temperature (a transient run plots it)")
            self.probe_button.toggled.connect(slot(self.toggle_probe))
            row2.addWidget(self.probe_button)
            row2.addWidget(button("Report", self.report, "LaTeX report with the renders, compiled to a PDF beside the document"))
            row2.addStretch(1)
            w.layout().addLayout(row2)
            self.probe_label = hint()
            w.layout().addWidget(self.probe_label)
            return w

        def selected_result(self):
            """The result the time combo points at (the last one by default)."""
            results = thermal.results_by_time(self.analysis)
            index = self.time_combo.currentIndex()
            if not results:
                return None
            return results[index] if 0 <= index < len(results) else results[-1]

        def _time_changed(self, *_a):
            kind = shown_field(self.analysis)
            if kind and kind in ("temperature", "tempbands"):
                self.show(kind)

        def _fill_times(self):
            results = thermal.results_by_time(self.analysis)
            show = len(results) > 1
            self.time_combo.setVisible(show)
            self.time_label.setVisible(show)
            if len(results) != self.time_combo.count():
                self.time_combo.blockSignals(True)
                self.time_combo.clear()
                for r in results:
                    self.time_combo.addItem(f"{float(r.Time):.4g} s")
                self.time_combo.setCurrentIndex(len(results) - 1)
                self.time_combo.blockSignals(False)

        def _thermal_summary(self):
            a = self.analysis
            self._fill_times()
            data = thermal.stored(a)
            if not data:
                self.summary.setText("Not solved yet.")
                self.legend.setText("")
                return
            lines = [f"Temperature <b>{data['tmin']:.1f}</b> to <b>{data['tmax']:.1f} °C</b> (hottest node {data['hot_node']}"
                     + (f", at {data['tmax_time']:.4g} s" if data["transient"] else "") + ")"]
            limit = data.get("limit")
            if limit is not None:
                edges, _c = thermal.bands(a)
                if data["tmax"] >= limit:
                    word, color = "above the limit", "#b00020"
                elif data["tmax"] >= edges[0]:
                    word, color = "within the margin", "#8a6d00"
                else:
                    word, color = "below the margin", "#1f7a46"
                lines.append(f"Limit {limit:.0f} °C ({data['limit_how']}): <b style='color:{color}'>{word}</b>")
                if data["transient"]:
                    ttl = data.get("time_to_limit")
                    lines.append("Time to reach the limit: " + ("never in this run" if ttl is None else f"<b>{ttl:.4g} s</b>"))
            else:
                lines.append("<span style='color:#8a6d00'>The material has no heat deflection temperature: no bands "
                             "(set a limit above).</span>")
            if data["transient"]:
                lines.append(f"Probe node {data['probe_node']} ({data['probe_how']}): "
                             f"{data['probe_curve'][-1]:.1f} °C at the end")
            bal = data.get("balance")
            if bal and "error" not in bal:
                if data["transient"]:
                    lines.append(f"Heat in {bal['in']:.4g} W, out {bal['out']:.4g} W (the rest heats the part)")
                else:
                    lines.append(f"Heat balance: in {bal['in']:.4g} W, out {bal['out']:.4g} W "
                                 f"(residual {100 * bal['relative']:.2g} %)")
            if self.kind == "thermal_stress":
                result = sim.latest_result(a)
                if result is not None:
                    ext = sim.result_extrema(result)
                    lines.append(f"Max von Mises <b>{ext['max_vm']:.4g} MPa</b>, max displacement <b>{ext['max_disp']:.4g} mm</b>"
                                 + (f", min safety factor <b>{ext['min_sf']:.3g}</b>" if ext["min_sf"] is not None else ""))
                    if ext.get("burst"):
                        lines.append(burst_html(ext["burst"]).replace("<br>", "", 1))
            self.summary.setText("<br>".join(lines))
            kind = shown_field(a)
            self.legend.setText(legend_html(kind, self.selected_result(), a) if kind else "")

        def _build_eigen_options(self):
            """The modal or buckling settings of the study (they apply at the next Solve)."""
            a = self.analysis
            box_ = QtWidgets.QWidget()
            lay = form_layout()
            box_.setLayout(lay)
            self.count_spin = count_spin(a.ModeCount)
            self.count_spin.valueChanged.connect(slot(self._options_changed))
            if self.kind == "modal":
                lay.addRow("Modes", self.count_spin)
                self.fmin_spin, self.fmax_spin = hz_spin(a.FrequencyMin), hz_spin(a.FrequencyMax)
                rng = QtWidgets.QHBoxLayout()
                rng.addWidget(self.fmin_spin)
                rng.addWidget(QtWidgets.QLabel("to"))
                rng.addWidget(self.fmax_spin)
                lay.addRow("Range", rng)
                self.prestressed_check = QtWidgets.QCheckBox("Prestressed (a static step with the loads first)")
                self.prestressed_check.setChecked(bool(a.Prestressed))
                lay.addRow("", self.prestressed_check)
                for widget in (self.fmin_spin, self.fmax_spin):
                    widget.valueChanged.connect(slot(self._options_changed))
                self.prestressed_check.toggled.connect(slot(self._options_changed))
            else:
                lay.addRow("Load factors", self.count_spin)
                self.nonlinear_check = QtWidgets.QCheckBox("Nonlinear run (imperfection from mode 1, NLGEOM)")
                self.nonlinear_check.setChecked(bool(a.Nonlinear))
                lay.addRow("", self.nonlinear_check)
                self.imperfection_spin = QtWidgets.QDoubleSpinBox()
                self.imperfection_spin.setRange(0.0, 1000.0)
                self.imperfection_spin.setDecimals(4)
                self.imperfection_spin.setSuffix(" mm")
                self.imperfection_spin.setSpecialValueText("auto (1 % of the section)")
                self.imperfection_spin.setValue(float(a.ImperfectionAmplitude))
                lay.addRow("Imperfection", self.imperfection_spin)
                self.limit_spin = QtWidgets.QDoubleSpinBox()
                self.limit_spin.setRange(0.1, 10.0)
                self.limit_spin.setDecimals(2)
                self.limit_spin.setSingleStep(0.1)
                self.limit_spin.setSuffix(" × λ1")
                self.limit_spin.setValue(float(a.NonlinearLoadLimit))
                lay.addRow("Load up to", self.limit_spin)
                self.disp_limit_spin = QtWidgets.QDoubleSpinBox()
                self.disp_limit_spin.setRange(0.0, 1.0e5)
                self.disp_limit_spin.setDecimals(3)
                self.disp_limit_spin.setSuffix(" mm")
                self.disp_limit_spin.setSpecialValueText("off (the knee only)")
                self.disp_limit_spin.setToolTip("The collapse factor is the load at this monitored displacement when "
                                                "the run reaches it before the knee")
                self.disp_limit_spin.setValue(float(a.CollapseDisplacement))
                lay.addRow("Displacement limit", self.disp_limit_spin)
                self.nonlinear_check.toggled.connect(slot(self._options_changed))
                for widget in (self.imperfection_spin, self.limit_spin, self.disp_limit_spin):
                    widget.valueChanged.connect(slot(self._options_changed))
                for widget in (self.imperfection_spin, self.limit_spin, self.disp_limit_spin):
                    widget.setEnabled(bool(a.Nonlinear))
            return box_

        def _options_changed(self, *_a):
            a = self.analysis
            a.ModeCount = self.count_spin.value()
            if self.kind == "modal":
                a.FrequencyMin, a.FrequencyMax = self.fmin_spin.value(), self.fmax_spin.value()
                a.Prestressed = self.prestressed_check.isChecked()
            else:
                a.Nonlinear = self.nonlinear_check.isChecked()
                a.ImperfectionAmplitude = self.imperfection_spin.value()
                a.NonlinearLoadLimit = self.limit_spin.value()
                a.CollapseDisplacement = self.disp_limit_spin.value()
                for widget in (self.imperfection_spin, self.limit_spin, self.disp_limit_spin):
                    widget.setEnabled(bool(a.Nonlinear))

        def _build_modes(self):
            """The modal or buckling results: the table, play and scale, the timeline, the checks, the report."""
            a = self.analysis
            title = "Modes" if self.kind == "modal" else "Buckling load factors"
            w = box(title, os.path.join(HERE, "BruceWorx_MotionStudy.svg"))
            self.summary = hint("Not solved yet.")
            w.layout().addWidget(self.summary)
            headers = (["Mode", "f (Hz)", "X %", "Y %", "Z %"] if self.kind == "modal" else ["Mode", "λ", ""])
            self.modes_table = QtWidgets.QTableWidget(0, len(headers))
            self.modes_table.setHorizontalHeaderLabels(headers)
            self.modes_table.verticalHeader().setVisible(False)
            self.modes_table.horizontalHeader().setStretchLastSection(True)
            self.modes_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.modes_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.modes_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.modes_table.setMaximumHeight(200)
            self.modes_table.itemSelectionChanged.connect(slot(self._mode_selected))
            w.layout().addWidget(self.modes_table)
            row = QtWidgets.QHBoxLayout()
            self.play_button = QtWidgets.QPushButton("Play")
            self.play_button.setCheckable(True)
            self.play_button.setToolTip("Animate the selected mode: u = s φ sin 2πt, one cycle per second")
            self.play_button.toggled.connect(slot(self.toggle_play))
            row.addWidget(self.play_button)
            self.scale_spin = QtWidgets.QDoubleSpinBox()
            self.scale_spin.setRange(0.1, 100.0)
            self.scale_spin.setDecimals(1)
            self.scale_spin.setSuffix(" % of diagonal")
            self.scale_spin.setToolTip("The played mode's largest displacement as a share of the bounding-box diagonal")
            self.scale_spin.setValue(100.0 * float(a.ModeScale))
            self.scale_spin.valueChanged.connect(slot(self._scale_changed))
            row.addWidget(self.scale_spin)
            row.addWidget(button("Motion Study…", self.open_timeline,
                                 "The selected mode on the Motion Study timeline: scrub, loop, Save frames (PNG, MP4)"))
            row.addStretch(1)
            w.layout().addLayout(row)
            if self.kind == "modal":
                w.layout().addWidget(self._build_excitation())
            else:
                w.layout().addWidget(self._build_target())
            row2 = QtWidgets.QHBoxLayout()
            self.probe_button = QtWidgets.QPushButton("Probe")  # the static panel's; unused for modes
            self.probe_button.setVisible(False)
            row2.addWidget(button("Report", self.report, "LaTeX report with the renders, compiled to a PDF beside the document"))
            row2.addStretch(1)
            w.layout().addLayout(row2)
            self.legend = hint()
            w.layout().addWidget(self.legend)
            self.probe_label = hint()
            w.layout().addWidget(self.probe_label)
            return w

        def _build_excitation(self):
            a = self.analysis
            wbox = QtWidgets.QWidget()
            lay = form_layout()
            wbox.setLayout(lay)
            self.excite_check = QtWidgets.QCheckBox("Excitation check: modes inside a band turn amber")
            self.excite_check.setChecked(bool(a.ExcitationCheck))
            lay.addRow("", self.excite_check)
            self.source_combo = QtWidgets.QComboBox()
            self.source_combo.addItem("Motor speed × blades", "MotorSpeed")
            self.source_combo.addItem("Frequency band", "FrequencyBand")
            self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(str(a.ExcitationSource))))
            lay.addRow("Source", self.source_combo)
            self.rpm_min, self.rpm_max = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
            for spin, value in ((self.rpm_min, a.MotorRpmMin), (self.rpm_max, a.MotorRpmMax)):
                spin.setRange(0.0, 1.0e6)
                spin.setDecimals(0)
                spin.setSingleStep(500)
                spin.setSuffix(" rpm")
                spin.setValue(float(value))
            rpm = QtWidgets.QHBoxLayout()
            rpm.addWidget(self.rpm_min)
            rpm.addWidget(QtWidgets.QLabel("to"))
            rpm.addWidget(self.rpm_max)
            lay.addRow("Motor", rpm)
            self.blades_spin = count_spin(a.BladeCount, 64)
            lay.addRow("Blades", self.blades_spin)
            self.band_min, self.band_max = hz_spin(a.BandMin), hz_spin(a.BandMax)
            band = QtWidgets.QHBoxLayout()
            band.addWidget(self.band_min)
            band.addWidget(QtWidgets.QLabel("to"))
            band.addWidget(self.band_max)
            lay.addRow("Band", band)
            self.excite_check.toggled.connect(slot(self._excitation_changed))
            self.source_combo.currentIndexChanged.connect(slot(self._excitation_changed))
            for spin in (self.rpm_min, self.rpm_max, self.blades_spin, self.band_min, self.band_max):
                spin.valueChanged.connect(slot(self._excitation_changed))
            self.bands_note = hint()
            lay.addRow("", self.bands_note)
            return wbox

        def _excitation_changed(self, *_a):
            a = self.analysis
            a.ExcitationCheck = self.excite_check.isChecked()
            a.ExcitationSource = self.source_combo.currentData()
            a.MotorRpmMin, a.MotorRpmMax = self.rpm_min.value(), self.rpm_max.value()
            a.BladeCount = self.blades_spin.value()
            a.BandMin, a.BandMax = self.band_min.value(), self.band_max.value()
            self._refresh_summary()

        def _build_target(self):
            a = self.analysis
            wbox = QtWidgets.QWidget()
            lay = form_layout()
            wbox.setLayout(lay)
            row = QtWidgets.QHBoxLayout()
            self.target_spin = target_spin(eigen.target_factor(a))
            self.target_spin.valueChanged.connect(slot(self._target_changed))
            row.addWidget(self.target_spin)
            self.target_default_button = button("Save as default", self.save_target_default,
                                                "New buckling studies start with this target (Mod/BruceWorx/Sim)")
            row.addWidget(self.target_default_button)
            lay.addRow("Target λ1", row)
            self.bands_note = hint()
            lay.addRow("", self.bands_note)
            return wbox

        def _target_changed(self, value):
            self.analysis.TargetLoadFactor = float(value)
            self._refresh_summary()

        def save_target_default(self):
            eigen.save_default_target(self.target_spin.value())
            self.bands_note.setText(f"Saved: new buckling studies start with target {self.target_spin.value():g}.")
            return True

        def _scale_changed(self, value):
            self.analysis.ModeScale = float(value) / 100.0
            result = shown_mode(self.analysis)
            if result is not None:
                playing = self.player.playing()
                self.player.stop()
                if playing:
                    self.player.start(self.analysis, result)
                else:
                    show_mode(self.analysis, result)

        def selected_mode(self):
            row = self.modes_table.currentRow()
            results = eigen.mode_results(self.analysis)
            if 0 <= row < self.modes_table.rowCount():
                item = self.modes_table.item(row, 0)
                index = int(item.data(QtCore.Qt.UserRole)) if item is not None else row + 1
                if 0 < index <= len(results):
                    return results[index - 1]
            return None

        def select_mode(self, index):
            """Selects the table row of mode `index` (1-based) and shows it."""
            for row in range(self.modes_table.rowCount()):
                if int(self.modes_table.item(row, 0).data(QtCore.Qt.UserRole)) == index:
                    self.modes_table.selectRow(row)
                    self._mode_selected()
                    return True
            return False

        def _mode_selected(self, *_a):
            result = self.selected_mode()
            if result is None:
                return
            if self.player.playing():
                self.player.start(self.analysis, result)
            else:
                show_mode(self.analysis, result)
            self.legend.setText(f"<b>{result.Label}</b>: shape scaled to {self.scale_spin.value():g} % of the diagonal; "
                                "colour: the mode's displacement, low to high")

        def toggle_play(self, on):
            if on:
                result = self.selected_mode() or (eigen.mode_results(self.analysis) or [None])[0]
                if result is None:
                    self.play_button.setChecked(False)
                    _warn("No modes yet: solve the study first")
                    return
                self.player.start(self.analysis, result)
                self.play_button.setText("Pause")
            else:
                self.player.stop()
                self.play_button.setText("Play")

        def open_timeline(self):
            result = self.selected_mode() or (eigen.mode_results(self.analysis) or [None])[0]
            if result is None:
                _warn("No modes yet: solve the study first")
                return None
            self.player.stop()
            return open_mode_timeline(result, return_to_study=True)

        def _fill_modes(self):
            """The table from the study's results; returns the summary HTML."""
            table = self.modes_table
            table.setRowCount(0)
            a = self.analysis
            if self.kind == "modal":
                rows = eigen.modal_rows(a)
                shown = [r for r in rows if not r["rigid"]]
                table.setRowCount(len(shown))
                for i, r in enumerate(shown):
                    mass = r["mass"] + [None] * (3 - len(r["mass"]))
                    cells = [str(r["index"]), f"{r['frequency']:.5g}"] + [("–" if m is None else f"{m:.3g}") for m in mass[:3]]
                    for c, text in enumerate(cells):
                        item = QtWidgets.QTableWidgetItem(text)
                        if r["band"]:
                            item.setBackground(QtGui.QBrush(QtGui.QColor(eigen.STATUS_COLORS["amber"])))
                            item.setToolTip(f"Inside the {r['band']} band")
                        table.setItem(i, c, item)
                    table.item(i, 0).setData(QtCore.Qt.UserRole, r["index"])
                if not rows:
                    return "Not solved yet."
                parts = []
                if eigen.free_free(a):
                    parts.append(f"Free-free: {eigen.rigid_count(a, rows)} rigid-body modes (below {eigen.RIGID_HZ:g} Hz) skipped")
                if bool(a.Prestressed) and eigen.prestress_result(a) is not None:
                    parts.append("Prestressed: the modes of the loaded state")
                if shown:
                    parts.insert(0, f"First mode <b>{shown[0]['frequency']:.4g} Hz</b>; {len(shown)} modes")
                bands = eigen.excitation_bands(a)
                if bands:
                    excited = [str(r["index"]) for r in shown if r["band"]]
                    band_text = ", ".join(f"{label} {lo:.4g}–{hi:.4g} Hz" for label, lo, hi in bands)
                    parts.append((f"<b style='color:#8a6d00'>Modes {', '.join(excited)} inside the excitation band</b>"
                                  if excited else "<span style='color:#1f7a46'>No mode inside the excitation band</span>")
                                 + f" ({band_text})")
                return "<br>".join(parts)
            rows = eigen.buckling_rows(a)
            table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                note = "λ1" if r["first"] else ("reversed load" if r["factor"] <= 0 else "")
                for c, text in enumerate((str(r["index"]), f"{r['factor']:.5g}", note)):
                    item = QtWidgets.QTableWidgetItem(text)
                    if c == 1:
                        item.setForeground(QtGui.QBrush(QtGui.QColor(eigen.STATUS_COLORS[r["status"]])))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    table.setItem(i, c, item)
                table.item(i, 0).setData(QtCore.Qt.UserRole, r["index"])
            if not rows:
                return "Not solved yet."
            first = eigen.first_factor(a)
            target = eigen.target_factor(a)
            if first is None:
                text = ("<b style='color:#b00020'>No positive load factor</b>: the load puts the part in tension only "
                        "(it does not buckle under it)")
            else:
                status = eigen.blf_status(first, target)
                color = eigen.STATUS_COLORS[status]
                verdict = {"red": "below 1: it buckles under the load", "amber": f"below the target {target:g}",
                           "green": f"at or above the target {target:g}"}[status]
                text = f"λ1 = <b style='color:{color}'>{first:.4g}</b>, {verdict}"
            curve = eigen.nonlinear_data(eigen.first_buckling_result(a))
            if curve:
                if curve["collapse"]:
                    text += f"<br>Nonlinear: collapse at <b>{curve['collapse']:.4g}</b> ({curve['basis']})"
                else:
                    text += f"<br>Nonlinear: {curve['basis']}"
            text += ("<br><span style='color:#777'>Linear buckling is non-conservative: imperfect parts buckle below "
                     "λ1. Compare λ1 with the target, or run the nonlinear option.</span>")
            return text

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
            row_c = QtWidgets.QHBoxLayout()
            row_c.setSpacing(4)
            for text, kind, tip in (("Radial", "radial", "Radial stress σrr about the spin axis"),
                                    ("Hoop", "hoop", "Hoop (circumferential) stress σθθ about the spin axis"),
                                    ("Axial", "axial", "Axial stress σzz along the spin axis"),
                                    ("Shear", "shear", "Shear stress σrθ in the plane across the axis")):
                row_c.addWidget(button(text, lambda k=kind: self.show(k), tip))
            w.layout().addLayout(row_c)
            w.layout().addWidget(self._build_contour_options())
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

        def _build_contour_options(self):
            """Colour map, fixed range, section and the cylindrical-stress axis of the contour views, with export and picture."""
            a = self.analysis
            box_ = QtWidgets.QWidget()
            lay = form_layout()
            box_.setLayout(lay)
            self.cmap_combo = QtWidgets.QComboBox()
            for name in post.COLORMAPS:
                self.cmap_combo.addItem(name, name)
            self.cmap_combo.setCurrentIndex(max(0, self.cmap_combo.findData(post.study_colormap(a))))
            self.cmap_combo.currentIndexChanged.connect(slot(self._contour_changed))
            lay.addRow("Colour map", self.cmap_combo)
            self.section_combo = QtWidgets.QComboBox()
            for key, label in (("none", "Whole part"), ("half", "Half cut"), ("quarter", "Quarter cut")):
                self.section_combo.addItem(label, key)
            self.section_combo.setCurrentIndex(max(0, self.section_combo.findData(post.study_section(a))))
            self.section_combo.setToolTip("Cut the part open about the axis (elements on the far side are left out) to see inside a wheel")
            self.section_combo.currentIndexChanged.connect(slot(self._contour_changed))
            lay.addRow("Section", self.section_combo)
            row = QtWidgets.QHBoxLayout()
            self.range_check = QtWidgets.QCheckBox("Fixed")
            self.range_check.setToolTip("Pin the colour bar of the shown field to this range, remembered for the study, so runs compare by eye")
            self.range_min = temp_spin(0.0, "", -1.0e7, 1.0e7, 3)
            self.range_max = temp_spin(1.0, "", -1.0e7, 1.0e7, 3)
            row.addWidget(self.range_check)
            row.addWidget(self.range_min)
            row.addWidget(self.range_max)
            lay.addRow("Colour range", row)
            for widget in (self.range_min, self.range_max):
                widget.valueChanged.connect(slot(self._range_changed))
            self.range_check.toggled.connect(slot(self._range_changed))
            self.axis_label = hint()
            lay.addRow("Axis", self.axis_label)
            row2 = QtWidgets.QHBoxLayout()
            row2.setSpacing(4)
            row2.addWidget(button("Pick axis", self.pick_axis, "Use the selected circular edge or cylindrical face as the axis of the "
                                                                "radial, hoop and axial stress"))
            row2.addWidget(button("Spin axis", lambda: self.pick_axis(clear=True), "Back to the spin load's axis (else the model's bore or rim)"))
            row2.addStretch(1)
            lay.addRow("", row2)
            row3 = QtWidgets.QHBoxLayout()
            row3.setSpacing(4)
            row3.addWidget(button("Export .vtu", self.export_vtu, "Write the result with every field, also the cylindrical stress"))
            row3.addWidget(button("ParaView", self.open_paraview, "Open the exported result in ParaView, coloured by the shown field"))
            row3.addWidget(button("Picture", self.picture, "Save a picture of the shown field from the 3D view (PNG)"))
            row3.addStretch(1)
            lay.addRow("", row3)
            self._sync_range_fields()
            self._show_axis()
            return box_

        def _field_key(self):
            kind = _state["shown"].get(self.analysis.Name)
            return SCALAR_KEYS.get(kind)

        def _sync_range_fields(self):
            """The fixed-range boxes follow the shown field: its stored range, else the data's own."""
            key = self._field_key() or "hoop"
            pinned = post.fixed_range(self.analysis, key)
            shown = _state["range"].get(self.analysis.Name)
            lo, hi = pinned or ((shown[1], shown[2]) if shown and shown[0] == key else (0.0, 1.0))
            for widget, value in ((self.range_min, lo), (self.range_max, hi)):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
            self.range_check.blockSignals(True)
            self.range_check.setChecked(pinned is not None)
            self.range_check.blockSignals(False)

        def _show_axis(self):
            try:
                point, direction = post.default_axis(self.analysis)
                source = "picked" if post.study_axis(self.analysis) else ("spin load" if post.spin_loads(self.analysis) else "model")
                self.axis_label.setText(f"{source}: ({direction[0]:.2f}, {direction[1]:.2f}, {direction[2]:.2f}) through "
                                        f"({point[0]:.4g}, {point[1]:.4g}, {point[2]:.4g}) mm")
            except Exception:
                self.axis_label.setText("none found: pick a circular edge or a cylindrical face")

        def _contour_changed(self, *_a):
            post.set_study_colormap(self.analysis, self.cmap_combo.currentData())
            post.set_study_section(self.analysis, self.section_combo.currentData())
            kind = _state["shown"].get(self.analysis.Name)
            if kind in SCALAR_KEYS:
                self.show(kind)

        def _range_changed(self, *_a):
            key = self._field_key()
            if key is None:
                return
            was = post.fixed_range(self.analysis, key) is not None
            if not self.range_check.isChecked() and not was:
                return  # typing a limit changes nothing until Fixed is ticked
            try:
                if self.range_check.isChecked():
                    post.set_fixed_range(self.analysis, key, self.range_min.value(), self.range_max.value())
                else:
                    post.set_fixed_range(self.analysis, key, None)
            except post.PostError as exc:
                self.legend.setText(f"<span style='color:#b00020'>{exc}</span>")
                return
            kind = _state["shown"][self.analysis.Name]
            if show_field(self.analysis, kind):  # redraw without putting the boxes back to the data's range
                self.legend.setText(legend_html(kind, sim.latest_result(self.analysis), self.analysis))

        def pick_axis(self, clear=False):
            """The axis of the cylindrical stress: the selected circular edge or cylindrical face, or back to the spin axis."""
            try:
                if clear:
                    post.set_study_axis(self.analysis, None)
                else:
                    refs = [r for r in selected_refs() if r[1].startswith(("Edge", "Face"))]
                    if not refs:
                        _warn("Select a circular edge or a cylindrical face in the 3D view first")
                        return
                    post.set_study_axis(self.analysis, refs[0])
            except post.PostError as exc:
                _warn(str(exc))
                return
            self._show_axis()
            kind = _state["shown"].get(self.analysis.Name)
            if kind in post.CYLINDRICAL:
                sim.latest_result(self.analysis) and post.cylindrical_stress(sim.latest_result(self.analysis), analysis=self.analysis)
                self.show(kind)

        def add_spin(self):
            """+ Spin: asks for the speed; the axis is the selected circular edge or cylindrical face, else the model's."""
            return add_spin_object(self.analysis, panel=self)

        def hold_for_spin(self):
            return hold_for_spin_object(self.analysis, panel=self)

        def add_blade_mass(self):
            return add_blade_object(self.analysis, panel=self)

        def export_vtu(self, path=None):
            return export_result(self.analysis, path)

        def open_paraview(self):
            return open_in_paraview(self.analysis)

        def picture(self, path=None):
            return save_picture(self.analysis, path)

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
            self.player.stop()
            for process in (self.process, self.nl_process):
                if process is not None and _alive(process) and process.state() != QtCore.QProcess.NotRunning:
                    process.kill()
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
            entries += [(f"Load: {d['label']} ({d['kind']}) {d['value']} on {d['where'] or ('the whole model' if d['kind'] in ('Spin', 'SelfWeight') else 'nothing')}", o)
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
            if self.kind in thermal.KINDS:
                if _alive(self.summary):
                    self._thermal_summary()
                return
            if self.kind in ("modal", "buckling"):
                if _alive(self.summary):
                    self.summary.setText(self._fill_modes())
                return
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
            away = ""
            if ext.get("min_sf_away") is not None:
                color = "#b00020" if ext["min_sf_away"] < 1 else "#1f7a46"
                away = (f"<br>Away from the supports <b style='color:{color}'>{ext['min_sf_away']:.3g}</b> at node "
                        f"{ext['min_sf_away_node']} <span style='color:#777'>({ext['support_nodes']} nodes on supports left "
                        + (f"out, every node within {ext['support_radius']:.3g} mm of a point support included: " if ext.get("support_radius")
                           else "out: ")
                        + "a fixed face's edges and a point pin are stress singularities that grow with mesh refinement)</span>")
            burst_text = burst_html(ext.get("burst"))
            bore = ext.get("bore") or {}
            bore_text = ""
            if bore:
                bore_text = (f"<br>Bore only: hoop mean <b>{bore['hoop_mean']:.4g}</b> / max <b>{bore['hoop_max']:.4g}</b> / "
                             f"mid-plane <b>{bore['hoop_mid']:.4g}</b> MPa, von Mises max <b>{bore['vonMises_max']:.4g}</b> MPa"
                             + (f", lowest safety factor on the bore <b>{bore['bore_min_sf']:.3g}</b> (not the part's)"
                                if bore.get("bore_min_sf") is not None else "")
                             + f"<br><span style='color:#777'>The safety factor uses the {ext['measure']} equivalent stress, "
                             "never the hoop stress.</span>")
            self.summary.setText(
                f"Max von Mises <b>{ext['max_vm']:.4g} MPa</b> (node {ext['max_vm_node']})<br>"
                f"Max displacement <b>{ext['max_disp']:.4g} mm</b> (node {ext['max_disp_node']})<br>"
                f"Min safety factor, whole part: {sf_text}{away}{burst_text}{bore_text}")
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
            self.player.stop()
            if blocking:
                self.say("Solving with CalculiX…")
                code = self.fea.ccx_run()
                return self._finished(code, QtCore.QProcess.NormalExit, blocking=True)
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

        def _finished(self, code, exit_status, blocking=False):
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
            if self.kind in ("modal", "buckling"):
                if self.modes_table.rowCount():
                    self.modes_table.selectRow(0)
                    self._mode_selected()
                if self.kind == "buckling" and bool(self.analysis.Nonlinear):
                    return self.solve_nonlinear(blocking)
                return True
            if self.kind in thermal.KINDS:
                self.show("tempbands" if thermal.bands(self.analysis)[0] else "temperature")
                return True
            self.show("safety" if getattr(result, "SafetyFactor", None) else "stress")
            return True

        def solve_nonlinear(self, blocking=False):
            """The nonlinear buckling run after the linear one: a second ccx (a QProcess unless blocking)."""
            try:
                self.nl_info = eigen.prepare_nonlinear(self.analysis, self.fea.inp_file_name)
            except eigen.EigenError as exc:
                self._fail(f"Nonlinear buckling not run: {exc}")
                return False
            info = self.nl_info
            self.say(f"Nonlinear buckling: imperfection {info['amplitude']:.3g} mm, load up to {info['limit']:g} × λ1…")
            if blocking:
                code, _tail = eigen.run_ccx(info["dir"], info["base"])
                return self._nonlinear_finished(code, QtCore.QProcess.NormalExit)
            self.nl_process = QtCore.QProcess()
            self.nl_process.setWorkingDirectory(info["dir"])
            self.nl_process.finished.connect(slot(self._nonlinear_finished))
            self.nl_process.errorOccurred.connect(slot(self._process_error))
            self.solve_button.setEnabled(False)
            self.nl_process.start(sim.find_ccx(), ["-i", info["base"]])
            return True

        def _nonlinear_finished(self, code, exit_status=None):
            if _alive(self.solve_button):
                self.solve_button.setEnabled(True)
            try:
                curve = eigen.finish_nonlinear(self.analysis, self.nl_info, code or 0)
            except eigen.EigenError as exc:
                self._fail(f"Nonlinear buckling: {exc}")
                return False
            if curve["collapse"]:
                self.say(f"Nonlinear buckling: collapse at load factor {curve['collapse']:.4g} "
                         f"({len(curve['factors'])} increments, {time.time() - self.started:.0f} s in all)")
            else:
                self.say(f"Nonlinear buckling: {curve['basis']}")
            self._refresh_summary()
            return True

        # --- results
        def show(self, kind):
            result = self.selected_result() if self.kind in thermal.KINDS else None
            if show_field(self.analysis, kind, result):
                self.legend.setText(legend_html(kind, result or sim.latest_result(self.analysis), self.analysis))
                if hasattr(self, "range_check"):
                    self._sync_range_fields()
                    self._show_axis()
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
            if self.kind == "thermal" and reading.get("point"):
                thermal.update_probe(self.analysis, reading["point"])
                self._thermal_summary()
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

    import bruceworx_motion as bm

    class ModeTimelinePanel(Panel, bm.TimelineMixin):
        """A mode shape on the Motion Study timeline (ROB-05): the frames of u = s φ sin 2πt over a number
        of cycles, played, scrubbed and saved (PNG frames and an MP4) by the same timeline as a motion study."""

        title = "Motion Study"

        def __init__(self, analysis, result, return_to_study=False):
            super().__init__()
            self.analysis, self.result = analysis, result
            self.return_to_study = return_to_study
            self.fps, self.cycles, self.period = MODE_FPS, 2, 1.0
            self.frames = self.frame_count()
            self.frame = 0
            self.scale = eigen.mode_scale(analysis, result)
            w = box(f"Motion Study: {result.Label}", os.path.join(HERE, "BruceWorx_MotionStudy.svg"))
            w.setObjectName("bwModeTimeline")
            lay = form_layout()
            self.cycles_spin = count_spin(self.cycles, 100)
            lay.addRow("Cycles", self.cycles_spin)
            self.period_spin = QtWidgets.QDoubleSpinBox()
            self.period_spin.setRange(0.1, 60.0)
            self.period_spin.setDecimals(2)
            self.period_spin.setSuffix(" s per cycle")
            self.period_spin.setValue(self.period)
            lay.addRow("Shown at", self.period_spin)
            self.fps_spin = count_spin(self.fps, 120)
            self.fps_spin.setSuffix(" fps")
            lay.addRow("Frame rate", self.fps_spin)
            w.layout().addLayout(lay)
            self.build_timeline(w.layout(), "Loop", 1.0)
            self.status = hint()
            w.layout().addWidget(self.status)
            w.layout().addWidget(hint("A mode has no amplitude: the shape is scaled so its largest displacement is "
                                      f"{100 * float(analysis.ModeScale):g} % of the bounding-box diagonal (the study's "
                                      "Mode scale), and shown slowed down to one cycle per period."))
            self.form = w
            for spin in (self.cycles_spin, self.period_spin, self.fps_spin):
                spin.valueChanged.connect(slot(self._settings_changed))
            show_mode(analysis, result, 0.0)
            self._settings_changed()
            self.show_frame(0)

        def frame_count(self):
            return max(2, int(round(self.cycles * self.period * self.fps)))

        def _settings_changed(self, *_a):
            self.cycles, self.period, self.fps = self.cycles_spin.value(), self.period_spin.value(), self.fps_spin.value()
            self.frames = self.frame_count()
            self.scrub.blockSignals(True)
            self.scrub.setRange(0, self.frames - 1)
            self.scrub.blockSignals(False)
            self.set_speed()
            self.status.setText(f"{self.frames} frames: {self.cycles} cycles of {self.period:g} s at {self.fps} fps")

        def factor(self, index):
            return self.scale * eigen.phase_factor(index, self.period * self.fps)

        def show_frame(self, index):
            index = max(0, min(self.frames - 1, int(index)))
            self.frame = index
            try:
                self.result.Mesh.ViewObject.applyDisplacement(self.factor(index))
            except Exception:
                return
            self.time_label.setText(f"{self.current_time():.2f} s")
            if self.scrub.value() != index:
                self.scrub.blockSignals(True)
                self.scrub.setValue(index)
                self.scrub.blockSignals(False)
            try:
                import FreeCADGui as Gui

                Gui.updateGui()
            except Exception:
                pass

        def ensure_solved(self):
            return self.frames > 0

        def current_time(self):
            return self.frame / float(self.fps)

        def timeline_fps(self):
            return self.fps

        def save_frames(self, folder=None):
            if folder is None:
                folder = QtWidgets.QFileDialog.getExistingDirectory(self.form, "Folder for the frames")
                if not folder:
                    return None
            self.buttons["play"].setChecked(False)
            paths, mp4 = bm.save_frames(None, None, self.frames, folder, fps=self.fps, show=self.show_frame)
            self.status.setText(f"{len(paths)} frames saved to {folder}" + (f", and {os.path.basename(mp4)}" if mp4
                                                                           else " (no ffmpeg on the PATH: no MP4)"))
            return paths, mp4

        def modifyStandardButtons(self, buttons):
            self.dialog_buttons = buttons  # self.buttons are the timeline's

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def release(self):
            if _alive(self.timer):
                self.timer.stop()
            try:
                show_mode(self.analysis, self.result)
            except Exception:
                pass
            return super().release()

        def reject(self):
            self.release()
            close_dialog()
            if self.return_to_study:
                schedule_reopen(self.analysis)
            return True

        def accept(self):
            return self.reject()

    class SimPreferencesPage:
        """Preferences > BruceWorx > Simulation (30 Oct follow-up 5): the defaults new studies start with,
        all in Mod/BruceWorx/Sim."""

        def __init__(self, *_args):
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle("Simulation")
            self.form.setObjectName("bwSimPreferences")
            lay = QtWidgets.QVBoxLayout(self.form)
            static = QtWidgets.QGroupBox("Static stress")
            f1 = QtWidgets.QFormLayout(static)
            self.thresholds = QtWidgets.QLineEdit()
            self.thresholds.setToolTip("Safety-factor band edges, ascending, separated by ';' (n edges make n + 1 bands)")
            f1.addRow("Band edges", self.thresholds)
            self.colors = QtWidgets.QLineEdit()
            self.colors.setToolTip("One #RRGGBB colour per band, lowest band first, separated by ';'")
            f1.addRow("Band colours", self.colors)
            self.measure = measure_combo(sim.default_measure())
            f1.addRow("Stress measure", self.measure)
            lay.addWidget(static)
            eig = QtWidgets.QGroupBox("Modal and buckling")
            f2 = QtWidgets.QFormLayout(eig)
            self.modal_modes = count_spin(eigen.default_modes("modal"))
            f2.addRow("Modal modes", self.modal_modes)
            self.buckling_modes = count_spin(eigen.default_modes("buckling"))
            f2.addRow("Buckling load factors", self.buckling_modes)
            self.target = target_spin(eigen.default_target())
            f2.addRow("Target buckling load factor", self.target)
            self.load_limit = QtWidgets.QDoubleSpinBox()
            self.load_limit.setRange(0.1, 10.0)
            self.load_limit.setDecimals(2)
            self.load_limit.setSingleStep(0.1)
            self.load_limit.setSuffix(" × λ1")
            self.load_limit.setToolTip("How far the nonlinear buckling run steps the load")
            f2.addRow("Nonlinear run: load up to", self.load_limit)
            lay.addWidget(eig)
            general = QtWidgets.QGroupBox("Mesh and report")
            f3 = QtWidgets.QFormLayout(general)
            self.divisor = QtWidgets.QDoubleSpinBox()
            self.divisor.setRange(2.0, 1000.0)
            self.divisor.setDecimals(1)
            self.divisor.setToolTip("Default largest element = bounding-box diagonal / this")
            f3.addRow("Mesh size: diagonal /", self.divisor)
            self.warn_nodes = QtWidgets.QSpinBox()
            self.warn_nodes.setRange(1000, 100000000)
            self.warn_nodes.setSingleStep(50000)
            self.warn_nodes.setToolTip("The pre-check warns when the estimated mesh has more nodes than this")
            f3.addRow("Mesh warning above (nodes)", self.warn_nodes)
            self.max_nodes = QtWidgets.QSpinBox()
            self.max_nodes.setRange(1000, 100000000)
            self.max_nodes.setSingleStep(100000)
            self.max_nodes.setToolTip("The pre-check blocks meshing above this; a study can override it (MeshNodeLimit)")
            f3.addRow("Mesh limit (nodes)", self.max_nodes)
            row = QtWidgets.QHBoxLayout()
            self.tex = QtWidgets.QLineEdit()
            self.tex.setPlaceholderText("automatic: Tectonic in Vector Labs, else latexmk or pdflatex")
            row.addWidget(self.tex, 1)
            row.addWidget(button("…", self.browse_tex, "Pick a tectonic, latexmk or pdflatex binary"))
            f3.addRow("TeX compiler", row)
            lay.addWidget(general)
            air = QtWidgets.QGroupBox("Airflow")
            f4 = QtWidgets.QFormLayout(air)
            self.openfoam = QtWidgets.QLineEdit()
            self.openfoam.setPlaceholderText("automatic: OpenFOAM-v2606.app in /Applications or ~/Applications")
            f4.addRow("OpenFOAM app", self.openfoam)
            lay.addWidget(air)
            self.note = hint()
            lay.addWidget(self.note)
            lay.addStretch(1)

        def browse_tex(self):
            path, _f = QtWidgets.QFileDialog.getOpenFileName(self.form, "TeX compiler")
            if path:
                self.tex.setText(path)

        def loadSettings(self):
            prefs = sim._prefs()
            thresholds, colors = sim.default_bands()
            self.thresholds.setText(";".join(f"{x:g}" for x in thresholds))
            self.colors.setText(";".join(colors))
            self.measure.setCurrentIndex(max(0, self.measure.findData(sim.default_measure())))
            self.modal_modes.setValue(eigen.default_modes("modal"))
            self.buckling_modes.setValue(eigen.default_modes("buckling"))
            self.target.setValue(eigen.default_target())
            self.load_limit.setValue(eigen.default_load_limit())
            self.divisor.setValue(sim.mesh_divisor())
            self.warn_nodes.setValue(sim.mesh_warn_nodes())
            self.max_nodes.setValue(sim.mesh_max_nodes())
            self.tex.setText(prefs.GetString(sim.TEX_PREF, ""))
            self.openfoam.setText(prefs.GetString("OpenFOAMPath", ""))

        def saveSettings(self):
            """Writes the page; bands that do not check out are left as they were, with the reason shown."""
            prefs = sim._prefs()
            try:
                thresholds = [float(x) for x in self.thresholds.text().split(";") if x.strip()]
                colors = [c.strip() for c in self.colors.text().split(";") if c.strip()]
                sim.save_default_bands(thresholds, colors)
                self.note.setText("")
            except (ValueError, sim.StudyError) as exc:
                self.note.setText(f"<span style='color:#b00020'>Bands not saved: {exc}</span>")
                _warn(f"Simulation preferences: bands not saved: {exc}")
            sim.set_default_measure(self.measure.currentData())
            prefs.SetInt("ModalModes", self.modal_modes.value())
            prefs.SetInt("BucklingModes", self.buckling_modes.value())
            eigen.save_default_target(self.target.value())
            prefs.SetFloat("NonlinearLoadLimit", self.load_limit.value())
            prefs.SetFloat("MeshDivisor", self.divisor.value())
            prefs.SetInt("MeshWarnNodes", self.warn_nodes.value())
            prefs.SetInt("MeshMaxNodes", self.max_nodes.value())
            prefs.SetString(sim.TEX_PREF, self.tex.text().strip())
            prefs.SetString("OpenFOAMPath", self.openfoam.text().strip())

    return {"Panel": Panel, "NewStudyPanel": NewStudyPanel, "StudyPanel": StudyPanel,
            "ModeTimelinePanel": ModeTimelinePanel, "SimPreferencesPage": SimPreferencesPage}


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
    if sim.study_kind(analysis) in sim.AIRFLOW_KINDS:
        import bruceworx_cfd_gui as cfd_gui

        return show_panel(cfd_gui.classes()["AirflowPanel"](analysis))
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
    if panel is not None and hasattr(panel, "bands_button"):
        panel.show_bands(True)
    return panel


def _with_modes():
    study = current_study()
    return study is not None and eigen.is_eigen(study) and bool(eigen.mode_results(study))


def run_modes():
    """Mode Shapes: the study panel at its mode list, playing the selected mode."""
    panel = open_study_panel()
    if panel is None or panel.kind not in ("modal", "buckling"):
        return panel
    if panel.modes_table.rowCount() and panel.modes_table.currentRow() < 0:
        panel.modes_table.selectRow(0)
    panel.play_button.setChecked(True)
    return panel


def run_report():
    study = current_study()
    if study is None:
        return None
    panel = _state.get("panel")
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        return panel.report()
    return report_study(study, done=lambda files: None)


# ----- the thermal setup (Temperature, Heat flux, Heat source, Convection) -----------------------------------


def selected_refs():
    """[(object, 'Face3'), ...] of the faces, edges and vertices selected in the 3D view."""
    import FreeCADGui as Gui

    refs = []
    for sel in Gui.Selection.getSelectionEx():
        for sub in sel.SubElementNames:
            if sub.startswith(("Face", "Edge", "Vertex")):
                refs.append((sel.Object, sub))
    return refs


def selected_bodies(analysis):
    """The objects selected as whole bodies; the study's model when nothing is."""
    import FreeCADGui as Gui

    found = [sel.Object for sel in Gui.Selection.getSelectionEx() if sim.shape_of(sel.Object) is not None]
    if not found:
        model = getattr(analysis, "Model", None)
        found = [model] if model is not None and sim.shape_of(model) is not None else []
    return found


def ask_values(key):
    """The value dialog of a thermal setup command; {...} or None when cancelled."""
    from PySide import QtWidgets

    dialog = QtWidgets.QDialog()
    titles = {"temperature": "Temperature", "flux": "Heat flux", "source": "Heat source", "convection": "Convection"}
    dialog.setWindowTitle(titles[key])
    form = QtWidgets.QFormLayout(dialog)

    def spin(value, suffix, low, high, decimals=2):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(low, high)
        s.setDecimals(decimals)
        s.setSuffix(suffix)
        s.setValue(value)
        return s

    fields = {}
    combo = None
    if key == "temperature":
        fields["celsius"] = spin(80.0, " °C", -273.0, 5000.0)
        form.addRow("Temperature", fields["celsius"])
    elif key == "flux":
        fields["w_per_m2"] = spin(1000.0, " W/m²", -1.0e9, 1.0e9)
        form.addRow("Heat flux (into the part)", fields["w_per_m2"])
    elif key == "source":
        fields["watts"] = spin(1.0, " W", -1.0e6, 1.0e6, 3)
        form.addRow("Heat generated in the body", fields["watts"])
    else:
        combo = QtWidgets.QComboBox()
        for name, h in thermal.CONVECTION_PRESETS:
            combo.addItem(f"{name}  (h = {h:g} W/m²K)", h)
        combo.addItem("Typed value", None)
        fields["h"] = spin(thermal.CONVECTION_PRESETS[1][1], " W/m²K", 0.0, 1.0e5)
        fields["ambient_c"] = spin(thermal.DEFAULT_AMBIENT, " °C", -273.0, 5000.0)
        form.addRow("Preset", combo)
        form.addRow("h", fields["h"])
        form.addRow("Ambient", fields["ambient_c"])

        def preset_changed(*_a):
            h = combo.currentData()
            if h is not None:
                fields["h"].setValue(h)
            fields["h"].setEnabled(h is None)

        combo.currentIndexChanged.connect(slot(preset_changed))
        combo.setCurrentIndex(1)
        preset_changed()
        note = QtWidgets.QLabel(thermal.CONVECTION_NOTE)
        note.setWordWrap(True)
        form.addRow(note)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if not dialog.exec():
        return None
    return {name: field.value() for name, field in fields.items()}


def add_thermal_object(key, analysis=None, values=None, refs=None, bodies=None):
    """Adds a Temperature, Heat flux, Heat source or Convection to the thermal study: on the selected faces
    (a Heat source: on the selected bodies, else the study's model). ``values``/``refs``/``bodies`` let a
    script skip the dialog and the selection. Returns the objects made ([] with a message when it cannot)."""
    analysis = analysis or current_study()
    if analysis is None or not thermal.is_thermal(analysis):
        _warn("Open a Thermal or Thermal stress study first")
        return []
    if key == "source":
        bodies = bodies if bodies is not None else selected_bodies(analysis)
        if not bodies:
            _warn("Heat source: select a body in the 3D view")
            return []
    else:
        refs = refs if refs is not None else selected_refs()
        if not refs:
            _warn("Select the faces in the 3D view first")
            return []
    values = values if values is not None else ask_values(key)
    if values is None:
        return []
    doc = analysis.Document
    doc.openTransaction("Thermal setup")
    try:
        if key == "temperature":
            made = [thermal.add_temperature(analysis, refs, values["celsius"])]
        elif key == "flux":
            made = [thermal.add_heat_flux(analysis, refs, values["w_per_m2"])]
        elif key == "convection":
            made = [thermal.add_convection(analysis, refs, values["h"], values.get("ambient_c", thermal.DEFAULT_AMBIENT))]
        else:
            made = []
            for body in bodies:
                made += thermal.add_heat_source(analysis, body, values["watts"])
    except (thermal.ThermalError, ValueError) as exc:
        doc.abortTransaction()
        _warn(f"Thermal setup: {exc}")
        return []
    doc.commitTransaction()
    status(f"Added {', '.join(o.Label for o in made)}")
    return made


def burst_html(burst):
    """The panel's burst speed line(s) (after a line break), '' when the study does not spin."""
    if not burst:
        return ""
    if burst.get("burst_rpm"):
        return (f"<br>Burst speed <b>{burst['burst_rpm']:.5g} rpm</b>, margin n<sub>burst</sub>/n "
                + (f"<b style='color:#b00020'>{burst['margin']:.3g} — ERROR: below the limit {burst['limit']:g}</b>"
                   if burst.get("below") else f"<b>{burst['margin']:.3g}</b>")
                + f" <span style='color:#777'>(average hoop stress over the section "
                f"{burst['avg_hoop']:.4g} MPa, UTS {burst['uts']:.4g} MPa from {burst['uts_source']}; assumes stress "
                "grows with speed squared)</span>")
    return f"<br><span style='color:#8a6d00'>{burst.get('note') or 'No burst speed'}</span>"


def add_blade_object(analysis=None, count=None, mass_g=None, r_cg=None, faces=None, panel=None):
    """Adds a Blade mass load on the selected faces (the rim): blades ``count`` x ``mass_g`` grams, centre of mass ``r_cg`` mm from
    the axis (each asked for in a dialog when None); see ``bruceworx_sim_post.add_blade_mass``.  Returns the load or None."""
    analysis = analysis or current_study()
    if analysis is None or sim.study_kind(analysis) not in ("static", "thermal_stress"):
        _warn("Open a Static stress or Thermal stress study first")
        return None
    if faces is None:
        faces = [r for r in selected_refs() if r[1].startswith("Face")]
    if not faces:
        _warn("Select the rim face(s) the blades pull on first")
        return None
    from PySide import QtWidgets

    def ask(label, value, low, high, decimals):
        got, ok = QtWidgets.QInputDialog.getDouble(None, "Blade mass", label, value, low, high, decimals)
        return got if ok else None

    if count is None:
        count = ask("Blades carried by the selected faces (a share of the wheel's for a sector model)", 24, 1e-6, 10000, 3)
    if count is not None and mass_g is None:
        mass_g = ask("Mass of one blade, g", 10.0, 1e-6, 1e6, 4)
    if mass_g is not None and r_cg is None:
        r_cg = ask("Radius of a blade's centre of mass, mm", 50.0, 1e-6, 1e5, 3)
    if None in (count, mass_g, r_cg):
        return None
    doc = analysis.Document
    doc.openTransaction("Add blade mass")
    try:
        load = post.add_blade_mass(analysis, faces, count, mass_g, r_cg)
    except post.PostError as exc:
        doc.abortTransaction()
        _warn(f"Blade mass: {exc}")
        return None
    doc.commitTransaction()
    status(f"Added {load.Label}: {load.BladePullN:.4g} N per blade, {load.Pressure}")
    if panel is not None and _alive(panel.members_list):
        panel.refresh()
    return load


def add_spin_object(analysis=None, rpm=None, axis=None, panel=None):
    """Adds a Spin (centrifugal) load: ``rpm`` (asked for in a dialog when None) about ``axis`` (see
    ``bruceworx_sim_post.add_spin``; None: the selected circular edge or cylindrical face, else the model's own).
    Returns the load, or None with a message when it cannot."""
    analysis = analysis or current_study()
    if analysis is None or sim.study_kind(analysis) not in ("static", "thermal_stress"):
        _warn("Open a Static stress study first")
        return None
    if axis is None:
        refs = [r for r in selected_refs() if r[1].startswith(("Edge", "Face"))]
        axis = refs[0] if refs else None
    if rpm is None:
        from PySide import QtWidgets

        value, ok = QtWidgets.QInputDialog.getDouble(None, "Spin", "Speed, rpm", 10000.0, 0.001, 1.0e7, 1)
        if not ok:
            return None
        rpm = value
    doc = analysis.Document
    doc.openTransaction("Add spin")
    try:
        spin = post.add_spin(analysis, rpm, axis)
    except post.PostError as exc:
        doc.abortTransaction()
        _warn(f"Spin: {exc}")
        return None
    doc.commitTransaction()
    status(f"Added {spin.Label}")
    if panel is not None and _alive(panel.members_list):
        panel.refresh()
        panel._show_axis()
    return spin


def hold_for_spin_object(analysis=None, face=None, panel=None):
    """+ Hold for spin: ``bruceworx_sim_post.hold_for_spin`` on the selected flat face (else the biggest one square to the axis)."""
    analysis = analysis or current_study()
    if analysis is None or sim.study_kind(analysis) not in ("static", "thermal_stress"):
        _warn("Open a Static stress study first")
        return None
    if face is None:
        refs = [r for r in selected_refs() if r[1].startswith("Face")]
        face = refs[0] if refs else None
    doc = analysis.Document
    doc.openTransaction("Hold for spin")
    try:
        made = post.hold_for_spin(analysis, face=face)
    except post.PostError as exc:
        doc.abortTransaction()
        _warn(f"Hold for spin: {exc}")
        return None
    doc.commitTransaction()
    status("Added an axial hold and a 3-2-1 point set")
    if panel is not None and _alive(panel.members_list):
        panel.refresh()
    return made


def export_result(analysis, path=None):
    """Writes the latest result to a .vtu (a file dialog asks when no path is given); returns the path or None."""
    if sim.latest_result(analysis) is None:
        _warn("Solve the study before exporting")
        return None
    if path is None:
        from PySide import QtWidgets

        base = os.path.splitext(analysis.Document.FileName)[0] if analysis.Document.FileName else os.path.join(tempfile.gettempdir(), analysis.Name)
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(None, "Export the result", base + "_result.vtu", "VTK unstructured grid (*.vtu)")
        if not path:
            return None
    try:
        post.export_vtu(analysis, path)
    except (post.PostError, sim.StudyError) as exc:
        _warn(f"Export: {exc}")
        return None
    status(f"Wrote {path}")
    return path


def open_in_paraview(analysis):
    """Exports the result next to the document and opens ParaView on it with the shown field, the study's colour map and
    section; returns the script path or None."""
    import subprocess

    import bruceworx_cfd_paraview as pv

    gui, _pvpython, _pvbatch = pv.paraview_executables()
    if gui is None:
        _warn(pv.precheck_text())
        return None
    result = sim.latest_result(analysis)
    if result is None:
        _warn("Solve the study before opening ParaView")
        return None
    folder = os.path.join(os.path.dirname(analysis.Document.FileName) if analysis.Document.FileName else tempfile.gettempdir(),
                          f"{analysis.Name}_paraview")
    os.makedirs(folder, exist_ok=True)
    try:
        vtu = post.export_vtu(analysis, os.path.join(folder, "result.vtu"))
        key = SCALAR_KEYS.get(_state["shown"].get(analysis.Name), "hoop") if _state["shown"].get(analysis.Name) in SCALAR_KEYS else "hoop"
        script = post.write_paraview_script(analysis, vtu, key, os.path.join(folder, "view.py"))
    except (post.PostError, sim.StudyError) as exc:
        _warn(f"ParaView: {exc}")
        return None
    subprocess.Popen([gui, f"--script={script}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return script


def save_picture(analysis, path=None, size=(1400, 900), view="iso"):
    """Saves the 3D view as it shows the study (the field on the screen, else the hoop stress) to a PNG; returns the path."""
    import FreeCADGui as Gui

    result = sim.latest_result(analysis)
    gview = _view(analysis.Document)
    if result is None or gview is None:
        _warn("A solved study and a 3D view are needed for a picture")
        return None
    kind = _state["shown"].get(analysis.Name)
    if kind not in SCALAR_KEYS and kind != "safety":
        kind = "hoop" if post.spin_loads(analysis) else "stress"
        show_field(analysis, kind, result)
    if path is None:
        from PySide import QtWidgets

        base = os.path.splitext(analysis.Document.FileName)[0] if analysis.Document.FileName else os.path.join(tempfile.gettempdir(), analysis.Name)
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(None, "Save the picture", f"{base}_{kind}.png", "PNG (*.png)")
        if not path:
            return None
    Gui.updateGui()
    gview.viewIsometric()
    gview.fitAll()
    Gui.updateGui()
    gview.saveImage(path, size[0], size[1], "Current")
    return path if os.path.isfile(path) else None


def _with_thermal_study():
    study = current_study()
    return study is not None and thermal.is_thermal(study)


def _with_static_study():
    study = current_study()
    return study is not None and sim.study_kind(study) == "static"


def run_spin():
    panel = _state.get("panel")
    study = current_study()
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        return panel.add_spin()
    return add_spin_object(study)


def run_add(key):
    panel = _state.get("panel")
    study = current_study()
    if isinstance(panel, classes()["StudyPanel"]) and not panel.released and panel.analysis is study:
        return panel.add_thermal(key)
    return add_thermal_object(key, study)


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
    ("BruceWorx_ModeShapes", "Mode Shapes", os.path.join(HERE, "BruceWorx_MotionStudy.svg"),
     "Play the mode shapes of a modal or buckling study (Motion Study puts one on the timeline)", _with_modes, run_modes),
    ("BruceWorx_SimTemperature", "Temperature", "FEM_ConstraintTemperature",
     "Fix the temperature of the selected faces (a thermal study)", _with_thermal_study, lambda: run_add("temperature")),
    ("BruceWorx_SimHeatFlux", "Heat flux", "FEM_ConstraintHeatflux",
     "A heat flux in W/m² into the selected faces", _with_thermal_study, lambda: run_add("flux")),
    ("BruceWorx_SimHeatSource", "Heat source", "FEM_ConstraintBodyHeatSource",
     "Heat generated in the selected body, in watts (an ESC or a motor)", _with_thermal_study, lambda: run_add("source")),
    ("BruceWorx_SimConvection", "Convection", "FEM_ConstraintHeatflux",
     "Convection to the air on the selected faces: still air, light airflow, prop wash, or a typed h", _with_thermal_study,
     lambda: run_add("convection")),
    ("BruceWorx_SimSpin", "Spin", "FEM_ConstraintCentrif",
     "A spinning load: centrifugal force at an rpm about the selected circular edge or cylindrical face (a rotor, a wheel)",
     _with_static_study, run_spin),
    ("BruceWorx_ShowHoop", "Hoop Stress", "FEM_ResultShow",
     "Colour the result by hoop (circumferential) stress about the spin axis; radial, axial and shear are in the Study panel",
     _with_result, lambda: run_show("hoop")),
    ("BruceWorx_ShowTemperature", "Temperature", "FEM_ResultShow",
     "Colour the result by temperature", _with_result, lambda: run_show("temperature")),
    ("BruceWorx_ShowHDTBands", "HDT bands", os.path.join(HERE, "BruceWorx_SafetyFactor.svg"),
     "Colour the temperature against the heat deflection temperature: red above, amber within the margin",
     _with_result, lambda: run_show("tempbands")),
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
    try:  # Preferences > BruceWorx > Simulation (30 Oct follow-up 5)
        Gui.addPreferencePage(classes()["SimPreferencesPage"], "BruceWorx")
    except Exception as exc:
        _warn(f"The Simulation preferences page was not added: {exc}")
    try:  # a mode shape plays on the Motion Study timeline (ROB-05) when one is selected
        import bruceworx_motion as bm

        bm.register_source(_mode_of_selection, open_mode_timeline)
    except Exception as exc:
        _warn(f"Mode shapes are not on the Motion Study timeline: {exc}")
    _state["installed"] = True
