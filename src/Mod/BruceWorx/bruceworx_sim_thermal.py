# SPDX-License-Identifier: LGPL-2.1-or-later
"""Thermal and thermal-stress studies (SIM-01, the last two study types).

Headless: the study's settings and CalculiX step, the thermal setup objects (fixed temperature, heat
flux, heat source in watts on a body, convection), the pre-check, the results (temperature in bands
against the material's heat deflection temperature, the temperature-versus-time curves of a transient
run, the time to reach the limit, the heat balance) and the report's LaTeX. The panels are in
bruceworx_sim_gui.

* Thermal: CalculiX ``*HEAT TRANSFER`` on the study's C3D10 mesh, steady state or transient (backward
  Euler, fixed increments from the initial temperature).
* Thermal stress: ``*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE``. The temperature field is solved
  first and loads the structure as the thermal strain alpha (T - T_ref); the deformation never changes
  the heat flow (one way). T_ref is the material's ``*EXPANSION, ZERO``.

CalculiX works in mm, t, s and K; every number the user sees here is in degrees Celsius, W, W/m^2 and
W/m^2/K, and the study keeps Celsius.
"""

import json
import math
import os
import re

KELVIN = 273.15
KINDS = ("thermal", "thermal_stress")
DEFAULT_AMBIENT = 20.0  # deg C
DEFAULT_END_TIME = 600.0  # s
DEFAULT_STEP = 10.0  # s
DEFAULT_MARGIN = 10.0  # K: amber this close below the heat deflection temperature
MAX_OUTPUTS = 60  # results kept of a transient run
MAX_INCREMENTS = 2000  # CalculiX's INC limit in the step
BAND_COLORS = ("#3FA34D", "#F2A007", "#D91C1C")  # below HDT - margin, within the margin, above HDT
SIM_PREFS = "User parameter:BaseApp/Preferences/Mod/BruceWorx/Sim"
# Convection presets (H3): film coefficient h in W/m^2/K. Estimates only.
CONVECTION_PRESETS = (("Still air", 10.0), ("Light airflow", 25.0), ("Fan or prop wash", 75.0))
CONVECTION_NOTE = ("These coefficients are estimates: the real h depends on the shape, the orientation and the "
                   "flow, and can be a factor of two off.")
THERMAL_TYPES = ("Fem::ConstraintTemperature", "Fem::ConstraintHeatflux", "Fem::ConstraintInitialTemperature",
                 "Fem::ConstraintPython")


class ThermalError(Exception):
    """A thermal study cannot go on: the message says what is wrong."""


def study_kind(analysis):
    if analysis is None or "StudyType" not in analysis.PropertiesList:
        return "static"
    return str(analysis.StudyType or "static")


def is_thermal(analysis):
    return study_kind(analysis) in KINDS


def _get(analysis, name, default=None):
    return getattr(analysis, name) if name in analysis.PropertiesList else default


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(SIM_PREFS)


def default_margin():
    try:
        value = _prefs().GetFloat("TempMargin", 0.0)
    except Exception:
        value = 0.0
    return value if value > 0 else DEFAULT_MARGIN


def save_default_margin(value):
    _prefs().SetFloat("TempMargin", float(value))


def default_colors():
    return list(BAND_COLORS)


def _add(analysis, kind, name, doc, value):
    if name not in analysis.PropertiesList:
        analysis.addProperty(kind, name, "Thermal", "[BW] " + doc)
        setattr(analysis, name, value)


def ensure_properties(analysis):
    """Adds the thermal settings (they apply at the next solve) to a thermal study."""
    if not is_thermal(analysis):
        return
    _add(analysis, "App::PropertyBool", "Transient", "Solve the warm-up or cool-down over time instead of the steady state", False)
    _add(analysis, "App::PropertyFloat", "EndTime", "Transient: the end time (s)", DEFAULT_END_TIME)
    _add(analysis, "App::PropertyFloat", "TimeStep", "Transient: the time step (s)", DEFAULT_STEP)
    _add(analysis, "App::PropertyFloat", "InitialTemperature", "The temperature (deg C) the whole part starts at (a transient run's start)", DEFAULT_AMBIENT)
    _add(analysis, "App::PropertyFloat", "ReferenceTemperature", "Thermal stress: the stress-free temperature T_ref (deg C); the thermal strain is alpha (T - T_ref)", DEFAULT_AMBIENT)
    _add(analysis, "App::PropertyFloat", "HDTLimit", "The temperature limit (deg C) the results are banded against; 0 = the material card's heat deflection temperature", 0.0)
    _add(analysis, "App::PropertyFloat", "TempMargin", "Amber from this many kelvin below the limit", default_margin())
    _add(analysis, "App::PropertyStringList", "TempColors", "Three band colours: below the margin, within the margin, above the limit", default_colors())
    _add(analysis, "App::PropertyVector", "ProbePoint", "The point whose temperature a transient run plots (the probe click sets it)", (0.0, 0.0, 0.0))
    _add(analysis, "App::PropertyBool", "ProbeSet", "ProbePoint was chosen (else the centre of the model's bounding box)", False)
    _add(analysis, "App::PropertyString", "ThermalSummary", "The last solve's temperatures, curves and heat balance (JSON)", "")


def apply_options(analysis, options):
    """Sets study properties from a StudyPlan's options dict (names as the properties)."""
    ensure_properties(analysis)
    for key, value in (options or {}).items():
        if key not in analysis.PropertiesList:
            raise ThermalError(f"a {study_kind(analysis)} study has no setting {key!r}")
        setattr(analysis, key, value)
    if study_kind(analysis) == "thermal_stress" and bool(analysis.Transient):
        raise ThermalError("a thermal stress study is steady state (one way: the temperature field first, then the stress)")
    check_times(analysis)


def check_times(analysis):
    if not bool(_get(analysis, "Transient", False)):
        return
    end, step = float(analysis.EndTime), float(analysis.TimeStep)
    if end <= 0 or step <= 0:
        raise ThermalError("a transient run needs an end time and a time step above zero")
    if step > end:
        raise ThermalError("the time step is longer than the end time")
    if math.ceil(end / step - 1e-9) > MAX_INCREMENTS:
        raise ThermalError(f"{math.ceil(end / step):.0f} time steps: at most {MAX_INCREMENTS} (raise the step)")


def transient(analysis):
    return study_kind(analysis) == "thermal" and bool(_get(analysis, "Transient", False))


def step_count(analysis):
    return int(math.ceil(float(analysis.EndTime) / float(analysis.TimeStep) - 1e-9))


# ----- the study's objects ---------------------------------------------------------------------------


def _members(analysis):
    import bruceworx_sim as sim

    return sim.members(analysis)


def initial_temperature_object(analysis):
    for obj in _members(analysis)["others"]:
        if obj.TypeId == "Fem::ConstraintInitialTemperature":
            return obj
    return None


def make_initial_temperature(doc, analysis):
    """The study's initial temperature (CalculiX needs one for any thermomechanical step): all nodes."""
    import ObjectsFem

    ensure_properties(analysis)
    obj = initial_temperature_object(analysis)
    if obj is None:
        obj = ObjectsFem.makeConstraintInitialTemperature(doc, "InitialTemperature")
        obj.Label = "Initial temperature"
        analysis.addObject(obj)
    obj.initialTemperature = f"{float(analysis.InitialTemperature) + KELVIN} K"
    return obj


def configure_solver(analysis, solver):
    """The CalculiX solver's settings for the study, from the study's properties (at each solve)."""
    ensure_properties(analysis)
    check_times(analysis)
    kind = study_kind(analysis)
    solver.AnalysisType = "thermomech"
    solver.ThermoMechType = "pure heat transfer" if kind == "thermal" else "uncoupled"
    solver.GeometricalNonlinearity = "linear"
    solver.MaterialNonlinearity = "linear"
    solver.IterationsControlParameterTimeUse = False
    if transient(analysis):
        step, end = float(analysis.TimeStep), float(analysis.EndTime)
        solver.ThermoMechSteadyState = False
        solver.AutomaticIncrementation = False  # fixed increments: the results fall on the step times
        solver.TimeInitialIncrement = f"{step} s"
        solver.TimeMaximumIncrement = f"{step} s"
        solver.TimeMinimumIncrement = f"{step / 1000.0} s"
        solver.TimePeriod = f"{end} s"
        solver.IncrementsMaximum = MAX_INCREMENTS
        solver.OutputFrequency = max(1, int(math.ceil(step_count(analysis) / float(MAX_OUTPUTS))))
    else:
        solver.ThermoMechSteadyState = True
        solver.AutomaticIncrementation = True
        solver.TimeInitialIncrement = "1 s"
        solver.TimeMaximumIncrement = "1 s"
        solver.TimeMinimumIncrement = "1e-05 s"
        solver.TimePeriod = "1 s"
        solver.OutputFrequency = 1
    solver.Label = "CalculiX (thermal)" if kind == "thermal" else "CalculiX (thermal stress)"
    initial = initial_temperature_object(analysis)
    if initial is not None:
        initial.initialTemperature = f"{float(analysis.InitialTemperature) + KELVIN} K"
    for mat in _members(analysis)["materials"]:  # the stress-free temperature of the thermal strain
        data = dict(mat.Material or {})
        data["ThermalExpansionReferenceTemperature"] = f"{float(analysis.ReferenceTemperature) + KELVIN} K"
        if data != dict(mat.Material or {}):
            mat.Material = data


def _refs(refs):
    out = []
    for obj, sub in refs:
        subs = list(sub) if isinstance(sub, (list, tuple)) else [sub]
        out.append((obj, subs[0] if len(subs) == 1 else subs))
    return out


def _add_to(analysis, obj, label):
    obj.Label = label
    analysis.addObject(obj)
    obj.Document.recompute()
    return obj


def add_temperature(analysis, refs, celsius, label=None):
    """A fixed temperature (deg C) on faces, edges or vertices: refs = [(object, "Face3"), ...]."""
    import ObjectsFem

    obj = ObjectsFem.makeConstraintTemperature(analysis.Document, "Temperature")
    obj.ConstraintType = "Temperature"
    obj.Temperature = f"{float(celsius) + KELVIN} K"
    obj.References = _refs(refs)
    return _add_to(analysis, obj, label or f"Temperature {celsius:g} °C")


def add_heat_flux(analysis, refs, w_per_m2, label=None):
    """A heat flux (W/m^2, positive into the part) on faces."""
    import ObjectsFem

    obj = ObjectsFem.makeConstraintHeatflux(analysis.Document, "HeatFlux")
    obj.ConstraintType = "DFlux"
    obj.DFlux = f"{float(w_per_m2)} W/m^2"
    obj.References = _refs(refs)
    return _add_to(analysis, obj, label or f"Heat flux {w_per_m2:g} W/m²")


def add_convection(analysis, refs, h, ambient_c=DEFAULT_AMBIENT, label=None):
    """Convection (film coefficient h in W/m^2/K, ambient temperature in deg C) on faces."""
    import ObjectsFem

    obj = ObjectsFem.makeConstraintHeatflux(analysis.Document, "Convection")
    obj.ConstraintType = "Convection"
    obj.FilmCoef = f"{float(h)} W/m^2/K"
    obj.AmbientTemp = f"{float(ambient_c) + KELVIN} K"
    obj.References = _refs(refs)
    return _add_to(analysis, obj, label or f"Convection h = {h:g} W/m²K, {ambient_c:g} °C")


def add_heat_source(analysis, body, watts, label=None):
    """Heat generated in a body (W, spread over its volume). A body of several solids shares the power by volume.
    Returns the constraint objects (one per solid)."""
    import ObjectsFem
    import bruceworx_sim as sim

    shape = sim.shape_of(body)
    if shape is None or not shape.Solids:
        raise ThermalError(f"{body.Label} has no solid to heat")
    volumes = [s.Volume for s in shape.Solids]
    total = sum(volumes)
    made = []
    for index, volume in enumerate(volumes, 1):
        part = float(watts) * volume / total
        obj = ObjectsFem.makeConstraintBodyHeatSource(analysis.Document, "HeatSource")
        obj.Mode = "Total Power"
        obj.TotalPower = f"{part} W"
        obj.References = [(body, f"Solid{index}")]
        text = f"Heat source {part:g} W in {body.Label}" + (f" (solid {index})" if len(volumes) > 1 else "")
        made.append(_add_to(analysis, obj, label or text))
    return made


def is_body_heat_source(obj):
    return obj.TypeId == "Fem::ConstraintPython" and getattr(getattr(obj, "Proxy", None), "Type", "") == "Fem::ConstraintBodyHeatSource"


def thermal_objects(analysis):
    """The study's thermal setup objects by role: temperatures, fluxes, convections, sources, initial."""
    out = {"temperatures": [], "fluxes": [], "convections": [], "sources": [], "initial": []}
    for obj in getattr(analysis, "Group", []) or []:
        if obj.TypeId == "Fem::ConstraintTemperature" and obj.ConstraintType == "Temperature":
            out["temperatures"].append(obj)
        elif obj.TypeId == "Fem::ConstraintHeatflux":
            out["convections" if obj.ConstraintType == "Convection" else "fluxes"].append(obj)
        elif is_body_heat_source(obj):
            out["sources"].append(obj)
        elif obj.TypeId == "Fem::ConstraintInitialTemperature":
            out["initial"].append(obj)
    return out


def source_watts(obj):
    """The power (W) of a body heat source."""
    try:
        if obj.Mode == "Total Power":
            return float(obj.TotalPower.getValueAs("W"))
        import bruceworx_sim as sim

        rate = float(obj.DissipationRate.getValueAs("W/kg"))  # per unit mass: needs the density
        total = 0.0
        for ref, subs in obj.References:
            sub = subs[0] if isinstance(subs, (list, tuple)) else subs
            shape = ref.getSubObject(sub)
            values = sim.study_material(sim.study_of(obj)) or {}
            total += rate * (values.get("density") or 0.0) * 1e-9 * shape.Volume
        return total
    except Exception:
        return 0.0


def describe(obj):
    """The value text of a thermal setup object, for the study's list and the report."""
    if obj.TypeId == "Fem::ConstraintTemperature":
        return f"{float(obj.Temperature.getValueAs('K')) - KELVIN:g} °C"
    if obj.TypeId == "Fem::ConstraintHeatflux":
        if obj.ConstraintType == "Convection":
            return (f"convection h = {float(obj.FilmCoef.getValueAs('W/m^2/K')):g} W/m²K, "
                    f"ambient {float(obj.AmbientTemp.getValueAs('K')) - KELVIN:g} °C")
        if obj.ConstraintType == "DFlux":
            return f"heat flux {float(obj.DFlux.getValueAs('W/m^2')):g} W/m²"
        return "radiation (not used by the thermal studies)"
    if obj.TypeId == "Fem::ConstraintInitialTemperature":
        return f"{float(obj.initialTemperature.getValueAs('K')) - KELVIN:g} °C"
    if is_body_heat_source(obj):
        return f"{source_watts(obj):g} W"
    return ""


# ----- the pre-check ---------------------------------------------------------------------------------


def material_issues(label, data, analysis):
    """What the material card lacks for this thermal study: [("error", text)]."""
    import bruceworx_sim as sim

    issues = []
    if not sim._number_in(data.get("ThermalConductivity"), "W/m/K"):
        issues.append(("error", f"{label}: no thermal conductivity k"))
    if study_kind(analysis) == "thermal_stress" and not sim._number_in(data.get("ThermalExpansionCoefficient"), "1/K"):
        issues.append(("error", f"{label}: no thermal expansion coefficient alpha: a thermal stress study needs it"))
    if transient(analysis):
        if not sim._density_kg_m3(data.get("Density")):
            issues.append(("error", f"{label}: no density: a transient run needs the heat capacity"))
        if not sim._number_in(data.get("SpecificHeat"), "J/kg/K"):
            issues.append(("error", f"{label}: no specific heat: a transient run needs it"))
    return issues


def boundary_issues(analysis, m=None):
    """The thermal study's own checks: a temperature or convection boundary, the settings."""
    issues = []
    objs = thermal_objects(analysis)
    anchored = [o for o in objs["temperatures"] + objs["convections"] if o.References]
    if not anchored:
        issues.append(("error", "No temperature or convection boundary: without one the temperature is undefined "
                                "(add a Temperature or a Convection on a face)"))
    for obj in objs["fluxes"]:
        if not float(obj.DFlux.getValueAs("W/m^2")):
            issues.append(("warning", f"{obj.Label}: the heat flux is zero"))
    for obj in objs["convections"]:
        if not float(obj.FilmCoef.getValueAs("W/m^2/K")):
            issues.append(("warning", f"{obj.Label}: the film coefficient is zero (no heat leaves)"))
    for obj in objs["sources"]:
        if not source_watts(obj):
            issues.append(("warning", f"{obj.Label}: the heat source is zero"))
    if study_kind(analysis) == "thermal" and not (objs["sources"] or objs["fluxes"]) and not transient(analysis):
        temps = {round(float(o.Temperature.getValueAs("K")), 3) for o in objs["temperatures"]}
        if len(temps) == 1 and not objs["convections"]:
            issues.append(("warning", "Only one temperature and no heat input: the whole part sits at that temperature"))
    try:
        check_times(analysis)
    except ThermalError as exc:
        issues.append(("error", str(exc)))
    if study_kind(analysis) == "thermal_stress" and bool(_get(analysis, "Transient", False)):
        issues.append(("error", "A thermal stress study is steady state"))
    return issues


# ----- the CalculiX input ----------------------------------------------------------------------------


def patch_input(analysis, inp_path):
    """Adds the heat-flow print for every fixed temperature (CalculiX ``RFL`` totals: the heat balance) and,
    for a long transient run, the output rate on the node and element files."""
    with open(inp_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    freq = 1
    if transient(analysis):
        freq = max(1, int(math.ceil(step_count(analysis) / float(MAX_OUTPUTS))))
    if freq > 1:
        text = re.sub(r"^\*NODE FILE\s*$", f"*NODE FILE, FREQUENCY={freq}", text, flags=re.M)
        text = re.sub(r"^\*EL FILE\s*$", f"*EL FILE, FREQUENCY={freq}", text, flags=re.M)
    prints = ""
    for obj in thermal_objects(analysis)["temperatures"]:
        prints += f"*NODE PRINT, NSET={obj.Name}, TOTALS=ONLY\nRFL\n"
    if prints and "*END STEP" in text:
        text = text.replace("*END STEP", prints + "*END STEP", 1)
    with open(inp_path, "w", encoding="utf-8") as handle:
        handle.write(text)


# ----- reading the run -------------------------------------------------------------------------------

_TET_FACE_CORNERS = {1: (1, 2, 3), 2: (1, 4, 2), 3: (2, 4, 3), 4: (3, 4, 1)}
_TET10_FACE_MIDS = {1: (5, 6, 7), 2: (8, 9, 5), 3: (9, 10, 6), 4: (10, 8, 7)}


def read_inp_geometry(path):
    """({node: (x, y, z)}, {element: [nodes]}, films [(element, face, T_ambient K, h mW/mm2/K)],
    fluxes [(element, face, q mW/mm2)]) of a CalculiX input written by FreeCAD."""
    nodes, elements, films, fluxes = {}, {}, [], []
    section, pending = None, None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("**"):
                continue
            if line.startswith("*"):
                key = line.split(",")[0].upper()
                section = key if key in ("*NODE", "*ELEMENT", "*FILM", "*DFLUX") else None
                if section == "*DFLUX" and "BF" in line.upper():
                    section = None
                pending = None
                continue
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if section == "*NODE" and len(parts) >= 4:
                nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
            elif section == "*ELEMENT":
                if pending is None:
                    pending = [int(p) for p in parts]
                else:
                    pending += [int(p) for p in parts]
                if len(pending) >= 5 and (len(pending) - 1 in (4, 10)):
                    elements[pending[0]] = pending[1:]
                    pending = None
            elif section == "*FILM" and len(parts) >= 4:
                films.append((int(parts[0]), int(parts[1].lstrip("Ff")), float(parts[2]), float(parts[3])))
            elif section == "*DFLUX" and len(parts) >= 3 and parts[1][:1].upper() == "S":
                fluxes.append((int(parts[0]), int(parts[1][1:]), float(parts[2])))
    return nodes, elements, films, fluxes


def _face_area_and_nodes(nodes, element, face):
    corners = _TET_FACE_CORNERS[face]
    ids = [element[i - 1] for i in corners]
    a, b, c = (nodes[i] for i in ids)
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    area = 0.5 * math.sqrt(sum(x * x for x in cross))
    mids = [element[i - 1] for i in _TET10_FACE_MIDS[face]] if len(element) == 10 else None
    return area, ids, mids


def read_totals(dat_path):
    """{NSET name: total heat generation (mW)} of the last increment in a CalculiX .dat (RFL totals)."""
    totals = {}
    if not os.path.isfile(dat_path):
        return totals
    text = open(dat_path, encoding="utf-8", errors="replace").read()
    for match in re.finditer(r"total heat generation for set (\S+) and time\s+\S+\s+([-+0-9.eE]+)", text):
        totals[match.group(1).upper()] = float(match.group(2))
    return totals


def heat_balance(analysis, inp_path, temps_k, dat_path=None):
    """The heat in and out of the part at the end of the run, in watts:

    sources (the body heat sources), fluxes (heat flux faces, in), fixed (the heat the fixed temperatures
    supply, CalculiX ``RFL``; negative = leaves), convection (leaves). ``temps_k`` is {node: K}.
    Returns {"terms": [(label, W)], "in": W, "out": W, "residual": W, "relative": fraction}."""
    nodes, elements, films, fluxes = read_inp_geometry(inp_path)
    objs = thermal_objects(analysis)
    terms = []
    for obj in objs["sources"]:
        terms.append((obj.Label, source_watts(obj)))
    flux_w = 0.0
    for element, face, q in fluxes:
        area, _ids, _mids = _face_area_and_nodes(nodes, elements[element], face)
        flux_w += q * area / 1000.0
    for obj in objs["fluxes"]:
        pass  # listed in the terms through the total below
    if objs["fluxes"] or fluxes:
        terms.append(("Heat flux faces", flux_w))
    conv_w = 0.0
    for element, face, t_amb, h in films:
        area, ids, mids = _face_area_and_nodes(nodes, elements[element], face)
        if mids and all(i in temps_k for i in mids):
            mean = sum(temps_k[i] for i in mids) / 3.0
        else:
            mean = sum(temps_k[i] for i in ids) / 3.0
        conv_w += h * area * (mean - t_amb) / 1000.0
    if objs["convections"] or films:
        terms.append(("Convection to the surroundings", -conv_w))
    dat = dat_path or os.path.splitext(inp_path)[0] + ".dat"
    totals = read_totals(dat)
    for obj in objs["temperatures"]:
        if obj.Name.upper() in totals:
            terms.append((obj.Label, totals[obj.Name.upper()] / 1000.0))
    into = sum(w for _label, w in terms if w > 0)
    out = -sum(w for _label, w in terms if w < 0)
    residual = into - out
    scale = max(into, out, 1e-12)
    return {"terms": terms, "in": into, "out": out, "residual": residual, "relative": abs(residual) / scale}


# ----- results ---------------------------------------------------------------------------------------


def results_by_time(analysis):
    """The study's result objects in time order."""
    import bruceworx_sim as sim

    found = [r for r in sim.members(analysis)["results"] if "Temperature" in r.PropertiesList and len(r.Temperature)]
    return sorted(found, key=lambda r: float(getattr(r, "Time", 0.0) or 0.0))


def hdt_limit(analysis):
    """(limit deg C, where it comes from) or (None, '')."""
    import bruceworx_sim as sim

    value = float(_get(analysis, "HDTLimit", 0.0) or 0.0)
    if value > 0:
        return value, "set on the study"
    values = sim.study_material(analysis) or {}
    if values.get("hdt"):
        return float(values["hdt"]), f"heat deflection temperature of {values.get('name', 'the material')}"
    return None, ""


def bands(analysis):
    """([lower edge, upper edge] deg C, colours) of the temperature bands, or ([], colours) without a limit."""
    limit, _how = hdt_limit(analysis)
    colors = list(_get(analysis, "TempColors", None) or BAND_COLORS)
    if len(colors) != 3:
        colors = list(BAND_COLORS)
    if limit is None:
        return [], colors
    margin = float(_get(analysis, "TempMargin", DEFAULT_MARGIN) or DEFAULT_MARGIN)
    return [limit - margin, limit], colors


def band_index_c(celsius, edges):
    """0 below the margin, 1 within it (amber), 2 at or above the limit (red)."""
    if not edges:
        return 0
    if celsius >= edges[1]:
        return 2
    return 1 if celsius >= edges[0] else 0


def temperatures_c(result):
    """{node number: deg C} of a thermal result."""
    return {n: t - KELVIN for n, t in zip(result.NodeNumbers, result.Temperature)}


def element_max_temps(result):
    """{element: highest node temperature, deg C}: the crisp bands colour each element by it."""
    temps = temperatures_c(result)
    fm = result.Mesh.FemMesh
    elements = list(fm.Volumes) or list(fm.Faces)
    out = {}
    for elem in elements:
        values = [temps[n] for n in fm.getElementNodes(elem) if n in temps]
        if values:
            out[elem] = max(values)
    return out


def _nearest(result, point):
    best, best_d = None, float("inf")
    for number, vec in result.Mesh.FemMesh.Nodes.items():
        d = (vec.x - point[0]) ** 2 + (vec.y - point[1]) ** 2 + (vec.z - point[2]) ** 2
        if d < best_d:
            best, best_d = number, d
    return best, math.sqrt(best_d)


def probe_point(analysis, result):
    """(point, how): the study's ProbePoint, else the centre of the result mesh's bounding box."""
    if bool(_get(analysis, "ProbeSet", False)):
        p = analysis.ProbePoint
        return (p.x, p.y, p.z), "the probe point"
    nodes = list(result.Mesh.FemMesh.Nodes.values())
    xs, ys, zs = [v.x for v in nodes], [v.y for v in nodes], [v.z for v in nodes]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2), "the centre of the bounding box"


def set_probe(analysis, point):
    ensure_properties(analysis)
    analysis.ProbePoint = tuple(point)
    analysis.ProbeSet = True


def time_to_limit(times, maxima, limit):
    """The first time the maximum temperature reaches the limit (linear between steps); 0 if it starts
    there, None if it never does."""
    if limit is None or not maxima:
        return None
    if maxima[0] >= limit:
        return times[0]
    for (t0, m0), (t1, m1) in zip(zip(times, maxima), zip(times[1:], maxima[1:])):
        if m0 < limit <= m1:
            return t0 + (t1 - t0) * (limit - m0) / (m1 - m0)
    return None


def finish(analysis, fea):
    """After the CalculiX run and the result import: the temperatures in deg C on every result, the
    summary (JSON on the study) and, for thermal stress, the safety factor. Returns the last result."""
    import bruceworx_sim as sim

    results = results_by_time(analysis)
    if not results:
        raise ThermalError("CalculiX wrote no temperatures (see the Report view)")
    for result in results:
        if "TempC" not in result.PropertiesList:
            result.addProperty("App::PropertyFloatList", "TempC", "Result", "[BW] Temperature per node, deg C")
        result.TempC = [t - KELVIN for t in result.Temperature]
    last = results[-1]
    times = [float(getattr(r, "Time", 0.0) or 0.0) for r in results]
    maxima = [max(r.TempC) for r in results]
    minima = [min(r.TempC) for r in results]
    out = {"steps": len(results), "times": times, "max": maxima, "min": minima, "transient": transient(analysis),
           "tmax": max(maxima), "tmin": min(minima)}
    # the hottest node over the whole run and the probe node
    top = max(range(len(results)), key=lambda i: maxima[i])
    hot_index = max(range(len(results[top].TempC)), key=results[top].TempC.__getitem__)
    out["hot_node"] = int(results[top].NodeNumbers[hot_index])
    out["tmax_time"] = times[top]
    point, how = probe_point(analysis, last)
    probe_node, distance = _nearest(last, point)
    out["probe_node"], out["probe_point"], out["probe_how"], out["probe_distance"] = int(probe_node), list(point), how, distance

    def series(node):
        values = []
        for r in results:
            numbers = list(r.NodeNumbers)
            values.append(r.TempC[numbers.index(node)])
        return values

    out["hot_curve"], out["probe_curve"] = series(out["hot_node"]), series(out["probe_node"])
    limit, limit_how = hdt_limit(analysis)
    out["limit"], out["limit_how"] = limit, limit_how
    out["margin"] = float(_get(analysis, "TempMargin", DEFAULT_MARGIN))
    out["time_to_limit"] = time_to_limit(times, maxima, limit) if out["transient"] else None
    inp = getattr(fea, "inp_file_name", "")
    try:
        balance = heat_balance(analysis, inp, {n: t for n, t in zip(last.NodeNumbers, last.Temperature)}) if inp else None
    except Exception as exc:
        balance = {"error": str(exc)}
    out["balance"] = balance
    analysis.ThermalSummary = json.dumps(out)
    if study_kind(analysis) == "thermal_stress":
        sim.add_safety_factor(last, analysis)
    return last


def update_probe(analysis, point):
    """Moves the probe to a clicked point and redraws its curve from the results already read (no new solve)."""
    results = results_by_time(analysis)
    data = stored(analysis)
    if not results or not data:
        return data
    set_probe(analysis, point)
    node, distance = _nearest(results[-1], point)
    values = [r.TempC[list(r.NodeNumbers).index(node)] for r in results]
    data.update({"probe_node": int(node), "probe_point": list(point), "probe_how": "the probe point",
                 "probe_distance": distance, "probe_curve": values})
    analysis.ThermalSummary = json.dumps(data)
    return data


def stored(analysis):
    """The last solve's summary dict (see finish), or {}."""
    text = _get(analysis, "ThermalSummary", "")
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def extrema_text(analysis):
    """One line for the panel: the hottest and coolest temperature."""
    data = stored(analysis)
    if not data:
        return ""
    return f"{data['tmin']:.1f} to {data['tmax']:.1f} °C"


# ----- the report ------------------------------------------------------------------------------------


def summary_part(analysis):
    """What the panel and the report show of a thermal study."""
    data = stored(analysis)
    objs = thermal_objects(analysis)
    edges, colors = bands(analysis)
    limit, how = hdt_limit(analysis)
    out = {"kind": study_kind(analysis), "data": data, "transient": transient(analysis),
           "end": float(_get(analysis, "EndTime", 0.0) or 0.0), "step": float(_get(analysis, "TimeStep", 0.0) or 0.0),
           "initial": float(_get(analysis, "InitialTemperature", DEFAULT_AMBIENT)),
           "reference": float(_get(analysis, "ReferenceTemperature", DEFAULT_AMBIENT)),
           "limit": limit, "limit_how": how, "edges": edges, "colors": colors,
           "margin": float(_get(analysis, "TempMargin", DEFAULT_MARGIN)),
           "counts": {k: len(v) for k, v in objs.items()}}
    results = results_by_time(analysis)
    if results and "TempC" in results[-1].PropertiesList:
        values = list(results[-1].TempC)
        counts = [0, 0, 0]
        for value in values:
            counts[band_index_c(value, edges)] += 1
        out["band_counts"] = counts if edges else None
    return out


def figures_for(part):
    """[(kind, caption)] of the renders: the temperature, its bands against the limit; thermal stress adds
    the static views."""
    figures = [("temperature", "Temperature, °C (end of the run)")]
    if part.get("limit") is not None:
        figures.append(("tempbands", f"Temperature against the limit {part['limit']:.0f} °C: red above, amber within "
                                     f"{part['margin']:g} K"))
    if part["kind"] == "thermal_stress":
        figures += [("stress", "von Mises stress (MPa)"), ("displacement", "Displacement (mm), shape exaggerated")]
    return figures


def report_preamble(part):
    lines = []
    for name, color in zip(("Cool", "Warm", "Hot"), part["colors"]):
        lines.append(rf"\definecolor{{temp{name}}}{{HTML}}{{{color.lstrip('#')}}}")
    data = part.get("data") or {}
    if part["transient"] and data.get("times"):
        lines += [r"\usepackage{pgfplots}", r"\pgfplotsset{compat=1.16}"]
    return lines


def _num(value, digits=4):
    if value is None:
        return "--"
    text = f"{value:.{digits}g}"
    return f"{value:.0f}" if "e+" in text else text


def report_results(part, e):
    """The LaTeX of a thermal study's results."""
    data = part.get("data") or {}
    kind = part["kind"]
    lines = ["", r"\section*{Thermal setup}"]
    if part["transient"]:
        lines.append(rf"Transient: backward-Euler steps of {part['step']:g}\,s up to {part['end']:g}\,s (CalculiX "
                     rf"\texttt{{*HEAT TRANSFER}}), starting from {part['initial']:g}\,$^\circ$C everywhere.\par")
    elif kind == "thermal":
        lines.append(r"Steady state (CalculiX \texttt{*HEAT TRANSFER, STEADY STATE}): $\nabla\cdot(k\nabla T)+\dot q=0$ "
                     r"with the boundaries above.\par")
    else:
        lines.append(rf"One-way thermal stress (CalculiX \texttt{{*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE}}): "
                     rf"the steady temperature field first, then the stress it causes, with the thermal strain "
                     rf"$\alpha\,(T-T_{{ref}})$, $T_{{ref}}={part['reference']:g}\,^\circ$C. The deformation does not "
                     r"change the heat flow.\par")
    lines.append(r"{\small Convection coefficients are estimates; touching bodies are fused into one (perfectly bonded "
                 r"contact).}\par")
    lines += ["", r"\section*{Temperatures}"]
    if not data:
        return lines + ["Not solved yet."]
    lines.append(r"\begin{tabular}{@{}ll@{}}")
    lines.append(rf"Hottest point & \textbf{{{_num(data['tmax'], 4)}}}\,$^\circ$C (node {data['hot_node']}"
                 + (rf", at {_num(data['tmax_time'], 4)}\,s" if part["transient"] else "") + r")\\")
    lines.append(rf"Coolest point & {_num(data['tmin'], 4)}\,$^\circ$C\\")
    limit = part.get("limit")
    if limit is not None:
        edges = part["edges"]
        if data["tmax"] >= limit:
            verdict = r" \textcolor{tempHot}{\textbf{above the limit}}"
        elif data["tmax"] >= edges[0]:
            verdict = r" \textcolor{tempWarm}{\textbf{within the margin}}"
        else:
            verdict = r" \textcolor{tempCool}{\textbf{below the margin}}"
        lines.append(rf"Limit & {_num(limit, 4)}\,$^\circ$C ({e(part['limit_how'])}), amber from {_num(edges[0], 4)}\,$^\circ$C:{verdict}\\")
        if part["transient"]:
            ttl = data.get("time_to_limit")
            lines.append(r"Time to reach the limit & " + ("never in this run" if ttl is None else rf"{_num(ttl, 4)}\,s") + r"\\")
    else:
        lines.append(r"Limit & the material has no heat deflection temperature (no bands)\\")
    lines.append(r"\end{tabular}")
    counts = part.get("band_counts")
    if counts and limit is not None:
        edges = part["edges"]
        labels = (rf"below {_num(edges[0], 4)}\,$^\circ$C", rf"{_num(edges[0], 4)} to {_num(edges[1], 4)}\,$^\circ$C",
                  rf"{_num(edges[1], 4)}\,$^\circ$C and above")
        lines += ["", r"\medskip", r"\begin{tabular}{@{}lll@{}}", r"\toprule Band & Temperature & Nodes\\ \midrule"]
        for name, label, count in zip(("tempCool", "tempWarm", "tempHot"), labels, counts):
            lines.append(rf"\textcolor{{{name}}}{{\rule{{8mm}}{{3mm}}}} & {label} & {count}\\")
        lines += [r"\bottomrule", r"\end{tabular}"]
    if part["transient"] and data.get("times") and len(data["times"]) > 1:
        lines += ["", r"\section*{Temperature against time}",
                  rf"Hottest node {data['hot_node']} and the probe node {data['probe_node']} "
                  rf"({e(data['probe_how'])}, {_num(data['probe_distance'], 3)}\,mm from the point).\par",
                  r"\begin{center}\begin{tikzpicture}",
                  r"\begin{axis}[width=0.8\linewidth,height=0.42\linewidth,xlabel={Time (s)},ylabel={Temperature ($^\circ$C)},"
                  r"grid=major,legend pos=south east,legend style={font=\small}]"]
        for key, label in (("hot_curve", "hottest node"), ("probe_curve", "probe")):
            coords = " ".join(f"({t:.6g},{v:.6g})" for t, v in zip(data["times"], data[key]))
            lines += [rf"\addplot[thick,mark=none] coordinates {{{coords}}};", rf"\addlegendentry{{{label}}}"]
        if limit is not None:
            lines += [rf"\addplot[dashed,color=tempHot,domain={data['times'][0]:.6g}:{data['times'][-1]:.6g}] {{{limit:.6g}}};",
                      r"\addlegendentry{limit}"]
        lines += [r"\end{axis}", r"\end{tikzpicture}\end{center}"]
    lines += ["", r"\section*{Heat balance}"]
    balance = data.get("balance")
    if not balance or "error" in balance:
        lines.append("Not available" + (f": {e(balance['error'])}" if balance else "") + ".")
    else:
        lines.append(r"\begin{tabular}{@{}lr@{}}")
        for label, watts in balance["terms"]:
            direction = "in" if watts >= 0 else "out"
            lines.append(rf"{e(label)} ({direction}) & {_num(abs(watts), 4)}\,W\\")
        lines.append(rf"\midrule Heat in & {_num(balance['in'], 4)}\,W\\ Heat out & {_num(balance['out'], 4)}\,W\\")
        lines.append(r"\end{tabular}")
        if part["transient"]:
            lines.append(rf"\par At the last step {_num(abs(balance['residual']), 3)}\,W "
                         + ("heats" if balance["residual"] >= 0 else "cools") + r" the part (stored in its heat capacity); "
                         r"a transient run has no balance to close.")
        else:
            lines.append(rf"\par Residual {_num(abs(balance['residual']), 3)}\,W, {100 * balance['relative']:.2g}\,\% of the heat "
                         r"moved: the heat put in equals the heat taken out.")
    if kind == "thermal_stress":
        lines += ["", r"\section*{Stress}", r"See the stress, displacement and safety-factor views; the safety factor "
                  r"divides the material's allowable stress by the thermal-plus-mechanical stress."]
    return lines


def report_footer(part):
    if part["kind"] == "thermal_stress":
        return (r"Thermal stress, one way: CalculiX \texttt{*UNCOUPLED TEMPERATURE-DISPLACEMENT}; "
                r"$\varepsilon_{th}=\alpha\,(T-T_{ref})\,\mathbf{I}$.")
    return r"Heat conduction $\nabla\cdot(k\nabla T)+\dot q=0$ (and $\rho c_p\,\dot T$ when transient), CalculiX \texttt{*HEAT TRANSFER}."


# ----- benchmarks (SIM-05) ---------------------------------------------------------------------------

ALUMINIUM = {"uuid": "", "name": "Aluminium 6061 (benchmark)", "library": "", "E": 68900.0, "nu": 0.33, "yield": 241.0,
             "uts": 290.0, "density": 2700.0, "k": 167.0, "cp": 896.0, "alpha": 23.6e-6, "hdt": None}
STEEL = {"uuid": "", "name": "Steel (benchmark)", "library": "", "E": 210000.0, "nu": 0.3, "yield": 355.0, "uts": None,
         "density": 7850.0, "k": 50.0, "cp": 480.0, "alpha": 12e-6, "hdt": None}


def fin_theory(k, h, length, width, t_base, t_amb):
    """A straight fin of square section (side width, mm) with an insulated tip: (m 1/m, T_tip deg C, Q W)."""
    w = width * 1e-3
    area, perimeter = w * w, 4 * w
    L = length * 1e-3
    m = math.sqrt(h * perimeter / (k * area))
    theta = t_base - t_amb
    return m, t_amb + theta / math.cosh(m * L), math.sqrt(h * perimeter * k * area) * theta * math.tanh(m * L)


def _bar(doc, name, length, width, material, max_size, study_type, options):
    import bruceworx_sim as sim

    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = length, width, width
    doc.recompute()
    plan = sim.StudyPlan(name=name, model=box, material=dict(material), max_size=max_size, study_type=study_type,
                         options=options)
    return box, sim.new_study(doc, plan)


def conduction_bar(doc=None, length=100.0, width=10.0, t_hot=100.0, t_cold=20.0, max_size=5.0):
    """Aluminium 10 x 10 x 100 mm, the end faces at 100 and 20 deg C. Returns (doc, objs, theory):
    linear, T(mid) = 60 deg C, Q = k A dT / L = 13.36 W."""
    import FreeCAD

    doc = doc or FreeCAD.newDocument("ConductionBar")
    box, objs = _bar(doc, "Bar", length, width, ALUMINIUM, max_size, "thermal", {})
    add_temperature(objs["analysis"], [(box, "Face1")], t_hot, "Hot end")
    add_temperature(objs["analysis"], [(box, "Face2")], t_cold, "Cold end")
    area = (width * 1e-3) ** 2
    theory = {"mid": (t_hot + t_cold) / 2, "Q": ALUMINIUM["k"] * area * (t_hot - t_cold) / (length * 1e-3)}
    return doc, objs, theory


def fin(doc=None, length=100.0, width=10.0, h=25.0, t_base=80.0, t_amb=20.0, max_size=4.0):
    """The same bar as a fin: the base (Face1) at 80 deg C, the sides in air (h, 20 deg C), the tip insulated.
    Returns (doc, objs, theory {"m", "tip", "Q"})."""
    import FreeCAD

    doc = doc or FreeCAD.newDocument("Fin")
    box, objs = _bar(doc, "Fin", length, width, ALUMINIUM, max_size, "thermal", {})
    analysis = objs["analysis"]
    add_temperature(analysis, [(box, "Face1")], t_base, "Base")
    add_convection(analysis, [(box, f"Face{i}") for i in (3, 4, 5, 6)], h, t_amb, "Sides in air")
    m, tip, q = fin_theory(ALUMINIUM["k"], h, length, width, t_base, t_amb)
    return doc, objs, {"m": m, "tip": tip, "Q": q}


def cooling_block(doc=None, side=20.0, h=25.0, t_start=80.0, t_amb=20.0, step=3.0, end=600.0, max_size=5.0):
    """A small aluminium cube cooling in air (Bi << 1), a transient study with the starting temperature t_start.
    Returns (doc, objs, theory function T(t) deg C): the lumped T = Tinf + (T0 - Tinf) exp(-h A t / rho c V)."""
    import FreeCAD

    doc = doc or FreeCAD.newDocument("CoolingBlock")
    options = {"Transient": True, "EndTime": end, "TimeStep": step, "InitialTemperature": t_start}
    box = doc.addObject("Part::Box", "Block")
    box.Length = box.Width = box.Height = side
    doc.recompute()
    import bruceworx_sim as sim

    plan = sim.StudyPlan(name="Cooling", model=box, material=dict(ALUMINIUM), max_size=max_size, study_type="thermal",
                         options=options)
    objs = sim.new_study(doc, plan)
    add_convection(objs["analysis"], [(box, f"Face{i}") for i in range(1, 7)], h, t_amb, "All faces in air")
    s = side * 1e-3
    tau = ALUMINIUM["density"] * ALUMINIUM["cp"] * s ** 3 / (h * 6 * s * s)
    return doc, objs, (lambda t: t_amb + (t_start - t_amb) * math.exp(-t / tau)), tau


def heated_steel_bar(doc=None, length=100.0, width=10.0, rise=50.0, t_ref=20.0, held=False, max_size=5.0):
    """A steel bar heated by `rise` K above T_ref: both end faces at T_ref + rise. Free: one end face held in x and
    a vertex in y and z at the other (no restraint on the growth); held: both end faces fixed.
    Returns (doc, objs, theory {"growth" mm, "stress" MPa})."""
    import FreeCAD
    import ObjectsFem

    doc = doc or FreeCAD.newDocument("HeatedBar")
    box, objs = _bar(doc, "Bar", length, width, STEEL, max_size, "thermal_stress",
                     {"ReferenceTemperature": t_ref, "InitialTemperature": t_ref})
    analysis = objs["analysis"]
    add_temperature(analysis, [(box, "Face1")], t_ref + rise, "Hot end 1")
    add_temperature(analysis, [(box, "Face2")], t_ref + rise, "Hot end 2")
    if held:
        for face in ("Face1", "Face2"):
            fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed" + face)
            fixed.References = [(box, face)]
            analysis.addObject(fixed)
    else:
        pin = ObjectsFem.makeConstraintDisplacement(doc, "HoldX")
        pin.References = [(box, "Face1")]
        pin.xFree, pin.yFree, pin.zFree = False, True, True
        pin.xDisplacement = 0
        analysis.addObject(pin)
        for name, vertex, axes in (("HoldYZ", "Vertex1", (False, False)), ("HoldZ", "Vertex3", (True, False))):
            hold = ObjectsFem.makeConstraintDisplacement(doc, name)
            hold.References = [(box, vertex)]
            hold.xFree, hold.yFree, hold.zFree = True, axes[0], axes[1]
            if not axes[0]:
                hold.yDisplacement = 0
            if not axes[1]:
                hold.zDisplacement = 0
            analysis.addObject(hold)
    doc.recompute()
    alpha = STEEL["alpha"]
    return doc, objs, {"growth": alpha * length * rise, "stress": -STEEL["E"] * alpha * rise}
