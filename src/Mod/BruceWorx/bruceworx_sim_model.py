# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Model Builder's headless model layer (roadmap SIM-07 phase P1, 1 Nov 2026).

A COMSOL-style view over the objects a study already has. Nothing here creates or changes a document object unless
a function says so (``set_value``, ``record``); the GUI (tree widget, settings form) is built on this API.

    model_tree(analysis)        -> Node         the ordered tree: Global Definitions, Geometry, Materials, Physics,
                                                Mesh, Study, Results; keys are stable strings ("phys/Force")
    settings(node)              -> [Section]    the node's settings form, declared once (Section -> Field)
    get_value(node, field)      -> value        field may be a Field or its name
    set_value(node, field, v)   -> None         one document transaction; "500 N", "2 mm", "12000 rpm" accepted;
                                                a wrong unit raises ValueError. Never solves or re-meshes.
    state(analysis)             -> {key: "ok"|"stale"|"warn"|"todo"|"run"}   (a key that is absent has no mark)
    node_messages(analysis)     -> {key: [(level, text)]}    the pre-check findings, by node
    record(analysis, stage)     stage "mesh" | "solution": stores the fingerprints (called by sim.mesh_study,
                                sim.finish_solve and cfd.save_summary)
    set_running(analysis, stage, on)   "mesh" | "solution": marks the node "run" until cleared

Node kinds: study, section, params, selections, geometry, material, air, fixed, displacement, hold, force, pressure,
gravity, spin, temperature, heatflux, convection, heatsource, initialtemp, constraint, farfield, walls, turbulence,
disks, disk, mesh, step, solver, result, plot, table, probe, report.

Up-to-date tracking (plan chapter 69): h(n) = H(s(n), h(deps)); H is a short SHA-1 of the settings' canonical text.
The geometry part of s is the rounded volume, area, bounding box and face count of the analysed part, never
``Shape.hashCode`` (that changes with every save and load). Dependencies:

    mesh      = H(mesh settings, geometry)
    solution  = H(study + solver settings, mesh, materials, every Physics node)
    results   = solution

The analysis keeps the fingerprints of the last mesh and the last solution in ``BW_Fingerprints`` (an
App::PropertyMap added on the first ``record``). A compute node is "stale" when its stored fingerprint differs from
the current one; a study without stored fingerprints counts as up to date when it has a mesh or results. Display
settings (stress measure, band colours, probe point, pressure colour range) are not inputs of the solution. The
airflow Mesh node follows the solution (OpenFOAM meshes inside the run). Definition nodes are "todo" (something
required is missing, or a pre-check error) or "warn" (a pre-check warning); sections show their worst child.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field

import bruceworx_sim as sim

FINGERPRINT_PROP = "BW_Fingerprints"
STAGES = ("mesh", "solution")
_PRIORITY = ("run", "todo", "warn", "stale", "ok")  # the worst first, for a section's mark

_RUNNING = {}  # (document name, analysis name) -> {stage}

# ----- the data classes ------------------------------------------------------------------------------------------


@dataclass
class Node:
    key: str
    kind: str
    label: str
    obj: str = None  # the document object's Name, or None for a virtual node
    icon: str = "i-phys"
    children: list = dc_field(default_factory=list)
    doc: str = ""  # document and analysis names: a node is enough to find its objects
    analysis: str = ""
    extra: dict = dc_field(default_factory=dict)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, key):
        return next((n for n in self.walk() if n.key == key), None)


@dataclass
class Field:
    name: str
    label: str
    kind: str  # float, int, enum, bool, text, selection, vector, readonly
    unit: str = ""
    choices: tuple = ()
    help: str = ""
    prop: str = ""  # the property it reads and writes (default: name)
    on: str = "obj"  # "obj": the node's object; "analysis": the study
    access: str = ""  # a named getter/setter pair for the few fields that are not a plain property
    min: float = None
    max: float = None


@dataclass
class Section:
    title: str
    fields: list = dc_field(default_factory=list)


def _f(name, label, kind, unit="", choices=(), help="", prop=None, on="obj", access="", min=None, max=None):
    return Field(name, label, kind, unit, tuple(choices), help, prop or name, on, access, min, max)


# ----- small helpers -----------------------------------------------------------------------------------------------


def _doc(node):
    import FreeCAD

    return FreeCAD.getDocument(node.doc)


def _analysis(node):
    return _doc(node).getObject(node.analysis)


def _obj(node):
    return _doc(node).getObject(node.obj) if node.obj else None


def _props(obj):
    return obj.PropertiesList if obj is not None else []


def _get(obj, name, default=None):
    return getattr(obj, name) if name in _props(obj) else default


def kind_of(analysis):
    return sim.study_kind(analysis)


def is_airflow(analysis):
    return kind_of(analysis) in sim.AIRFLOW_KINDS


def _model(analysis):
    return _get(analysis, "Model")


def _label(analysis):
    text = analysis.Label
    return text[len("Study: "):] if text.startswith("Study: ") else text


def constraint_kind(obj):
    """The node kind of a FEM constraint object."""
    tid, label = obj.TypeId, obj.Label
    if tid == "Fem::ConstraintFixed":
        return "fixed"
    if tid == "Fem::ConstraintDisplacement":
        return "hold" if label.startswith("Hold") else "displacement"
    if tid == "Fem::ConstraintForce":
        return "force"
    if tid == "Fem::ConstraintPressure":
        return "pressure"
    if sim.is_self_weight(obj):
        return "gravity"
    if sim.is_spin_load(obj):
        return "spin"
    if tid == "Fem::ConstraintTemperature":
        return "temperature"
    if tid == "Fem::ConstraintHeatflux":
        return "convection" if str(_get(obj, "ConstraintType", "")) == "Convection" else "heatflux"
    if tid == "Fem::ConstraintInitialTemperature":
        return "initialtemp"
    if tid == "Fem::ConstraintPython" and getattr(getattr(obj, "Proxy", None), "Type", "") == "Fem::ConstraintBodyHeatSource":
        return "heatsource"
    return "constraint"


ICONS = {"fixed": "i-fixed", "displacement": "i-fixed", "hold": "i-fixed", "force": "i-force", "pressure": "i-press",
         "gravity": "i-grav"}  # every other constraint uses i-phys

STEP_LABELS = {"static": "Stationary", "modal": "Eigenfrequency", "buckling": "Linear buckling",
               "thermal": "Heat transfer", "thermal_stress": "Thermal stress", "external_flow": "Steady CFD",
               "prop_wash": "Steady CFD (actuator disks)"}


def motors(analysis):
    """The motor parts of an airflow study's model: parts that carry a ThrustAxis (one actuator disk each)."""
    found, seen = [], set()

    def visit(obj, depth):
        if obj is None or obj.Name in seen or depth > 6:
            return
        seen.add(obj.Name)
        if "ThrustAxis" in _props(obj):
            found.append(obj)
            return
        linked = getattr(obj, "LinkedObject", None)
        if linked is not None and linked is not obj:
            visit(linked, depth + 1)
        for child in list(getattr(obj, "Group", None) or []) + list(getattr(obj, "Links", None) or []):
            visit(child, depth + 1)

    visit(_model(analysis), 0)
    return found


# ----- the tree ----------------------------------------------------------------------------------------------------


def model_tree(analysis):
    """The Model Builder tree of a study (read only: no object is created or changed)."""
    doc, name = analysis.Document.Name, analysis.Name
    kind = kind_of(analysis)
    air = kind in sim.AIRFLOW_KINDS

    def node(key, nkind, label, obj=None, icon=None, children=None, **extra):
        return Node(key, nkind, label, obj.Name if obj is not None else None,
                    icon or ICONS.get(nkind, "i-phys"), list(children or []), doc, name, extra)

    members = sim.members(analysis)
    root = node("study", "study", _label(analysis), analysis, "i-study")
    root.children = sections = []

    sections.append(node("defs", "section", "Global Definitions", icon="i-defs", children=[
        node("defs/params", "params", "Parameters", icon="i-param", placeholder=True),
        node("defs/sels", "selections", "Selections", icon="i-sel", placeholder=True)]))

    model = _model(analysis)
    geo = []
    if model is not None:
        geo.append(node("geo/part", "geometry", model.Label, model, "i-geo"))
    sections.append(node("geo", "section", "Geometry", icon="i-geo", children=geo))

    mats = [node(f"mat/{m.Name}", "material", m.Label, m, "i-mat") for m in members["materials"]]
    if air:
        mats = [node("mat/air", "air", "Air", icon="i-mat")]
    sections.append(node("mat", "section", "Materials", icon="i-mat", children=mats))

    phys = []
    if air:
        phys.append(node("phys/farfield", "farfield", "Inlet and far field", icon="i-flow"))
        phys.append(node("phys/walls", "walls", "Walls", icon="i-flow"))
        phys.append(node("phys/turbulence", "turbulence", "Turbulence", icon="i-flow"))
        if kind == "prop_wash":
            phys.append(node("phys/disks", "disks", "Actuator disks", icon="i-disk", children=[
                node(f"phys/disk/{m.Name}", "disk", m.Label, m, "i-disk") for m in motors(analysis)]))
    else:
        for obj in members["supports"] + members["loads"] + members["others"]:
            ckind = constraint_kind(obj)
            phys.append(node(f"phys/{obj.Name}", ckind, obj.Label, obj, ICONS.get(ckind, "i-phys")))
    sections.append(node("phys", "section", "Physics", icon="i-phys", children=phys))

    mesh = members["mesh"]
    if air:
        mesh_nodes = [node("mesh/main", "mesh", "Mesh (snappyHexMesh)", icon="i-mesh")]
    else:
        mesh_nodes = [node("mesh/main", "mesh", mesh.Label, mesh, "i-mesh")] if mesh is not None else []
    sections.append(node("mesh", "section", "Mesh", icon="i-mesh", children=mesh_nodes))

    solver = members["solver"]
    step = [node("step/main", "step", STEP_LABELS.get(kind, kind), icon="i-step")]
    if air:
        step.append(node("step/solver", "solver", "OpenFOAM", icon="i-solver"))
    elif solver is not None:
        step.append(node("step/solver", "solver", solver.Label, solver, "i-solver"))
    sections.append(node("step", "section", "Study", icon="i-step", children=step))

    res = []
    for obj in members["results"]:
        res.append(node(f"results/{obj.Name}", "result", obj.Label, obj, "i-results"))
    for key, label, nkind, icon in _plots(analysis):
        res.append(node(f"results/{key}", nkind, label, icon=icon))
    sections.append(node("results", "section", "Results", icon="i-results", children=res))
    return root


def _plots(analysis):
    """The virtual result nodes of a study type: (key, label, kind, icon)."""
    kind = kind_of(analysis)
    out = []
    if kind in sim.AIRFLOW_KINDS:
        out += [("forces", "Forces", "table", "i-results"), ("pressure", "Surface pressure", "plot", "i-plot")]
    elif kind == "modal":
        out.append(("modes", "Mode shapes", "plot", "i-plot"))
    elif kind == "buckling":
        out.append(("modes", "Buckling modes", "plot", "i-plot"))
    elif kind == "thermal":
        out.append(("temperature", "Temperature", "plot", "i-plot"))
    else:
        if kind == "thermal_stress":
            out.append(("temperature", "Temperature", "plot", "i-plot"))
        out += [("stress", "Stress", "plot", "i-plot"), ("displacement", "Displacement", "plot", "i-plot"),
                ("safety", "Safety factor", "plot", "i-plot")]
        if any(sim.is_spin_load(o) for o in getattr(analysis, "Group", []) or []):
            out.append(("hoop", "Hoop and radial stress", "plot", "i-plot"))
    if kind not in sim.AIRFLOW_KINDS and kind not in ("modal", "buckling"):
        out.append(("probe", "Probe", "probe", "i-probe"))
    out.append(("report", "Report", "report", "i-report"))
    return out


def find_node(analysis, key):
    return model_tree(analysis).find(key)


# ----- fingerprints ------------------------------------------------------------------------------------------------

_SKIP = {"Label", "Label2", "Visibility", "Proxy", "Placement", "Scale", "Points", "Normals", "NormalDirection",
         "DirectionVector", "ExpressionEngine", "Shape", "FemMesh", "Tool", "WorkingDirectory", "WorkingDir",
         "Results", "Uid", "Group", "UUID", "Tangents"}
# study properties that only change how results are shown, or that the solver writes back
_DISPLAY = {"MeshNodeLimit", "StressMeasure", "SFThresholds", "SFColors", "MaterialUUID", "Model", "CaseFolder",
            "CfdSummary", "ThermalSummary", "TempMargin", "TempColors", "HDTLimit", "ProbePoint", "ProbeSet",
            "PressureFixed", "PressureMin", "PressureMax", "ModeScale", FINGERPRINT_PROP}
_STUDY_GROUPS = {"Study", "Modal", "Excitation", "Buckling", "Thermal", "Airflow"}


def _ser(value):
    """A canonical, save-stable text of a property value."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return f"{value:.9g}"
    if hasattr(value, "Value") and hasattr(value, "Unit"):
        return f"{float(value.Value):.9g} {value.Unit}"
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return [f"{value.x:.9g}", f"{value.y:.9g}", f"{value.z:.9g}"]
    if hasattr(value, "TypeId") and hasattr(value, "Name"):
        return f"obj:{value.Name}"
    if isinstance(value, dict):
        return {str(k): _ser(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and hasattr(value[0], "TypeId") and isinstance(value[1], (list, tuple)):
            return [value[0].Name, sorted(str(s) for s in value[1])]
        items = [_ser(v) for v in value]
        return items
    return str(value)


def _refs_ser(value):
    """The sorted (object, sub-element) pairs of a link-sub list."""
    out = []
    for item in value or []:
        if isinstance(item, tuple) and len(item) == 2 and hasattr(item[0], "Name"):
            subs = item[1] if isinstance(item[1], (list, tuple)) else [item[1]]
            out += [(item[0].Name, str(s)) for s in subs]
    return sorted(out)


def _obj_settings(obj, only_groups=None, skip=()):
    out = {}
    for prop in obj.PropertiesList:
        if prop in _SKIP or prop in skip:
            continue
        if only_groups is not None and obj.getGroupOfProperty(prop) not in only_groups:
            continue
        try:
            value = getattr(obj, prop)
        except Exception:
            continue
        if prop in ("References", "RotationAxis", "Direction"):
            out[prop] = _refs_ser(value if prop != "Direction" else [value] if value else [])
        else:
            out[prop] = _ser(value)
    return out


def _hash(*parts):
    text = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def geometry_settings(shape):
    """Save-stable numbers of a shape: rounded volume, area, bounding box, face and solid counts."""
    if shape is None:
        return {"none": True}
    bb = shape.BoundBox
    return {"volume": f"{shape.Volume:.6g}", "area": f"{shape.Area:.6g}",
            "bbox": [f"{v:.5g}" for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
            "faces": len(shape.Faces), "solids": len(shape.Solids)}


def _model_shape(analysis):
    model = _model(analysis)
    shape = sim.shape_of(model) if model is not None else None
    if shape is None:
        mesh = sim.members(analysis)["mesh"]
        shape_obj = _get(mesh, "Shape")
        shape = sim.shape_of(shape_obj) if shape_obj is not None else None
    return shape


def fingerprints(analysis):
    """{node key: fingerprint} for the nodes that have one (geometry, materials, Physics nodes, mesh, study,
    solver, and the compute stages "mesh/main", "step/main", "results")."""
    m = sim.members(analysis)
    out = {}
    out["geo/part"] = _hash("geo", geometry_settings(_model_shape(analysis)))
    mat_fps = []
    for mat in m["materials"]:
        out[f"mat/{mat.Name}"] = _hash("mat", _obj_settings(mat))
        mat_fps.append(out[f"mat/{mat.Name}"])
    phys_fps = []
    for obj in m["supports"] + m["loads"] + m["others"]:
        out[f"phys/{obj.Name}"] = _hash("phys", obj.TypeId, _obj_settings(obj))
        phys_fps.append(out[f"phys/{obj.Name}"])
    study = _hash("study", kind_of(analysis), _obj_settings(analysis, _STUDY_GROUPS, _DISPLAY))
    out["study"] = study
    solver = _hash("solver", _obj_settings(m["solver"])) if m["solver"] is not None else ""
    out["step/solver"] = solver
    if is_airflow(analysis):
        mesh_fp = _hash("mesh", out["geo/part"], study)  # OpenFOAM meshes inside the run
    else:
        mesh = m["mesh"]
        mesh_fp = _hash("mesh", out["geo/part"], _obj_settings(mesh, {"MeshParams"}) if mesh is not None else "")
    out["mesh/main"] = mesh_fp
    out["step/main"] = _hash("solution", study, solver, mesh_fp, sorted(mat_fps), sorted(phys_fps), out["geo/part"])
    out["results"] = _hash("results", out["step/main"])
    return out


def stored_fingerprints(analysis):
    return dict(_get(analysis, FINGERPRINT_PROP, None) or {})


def record(analysis, stage):
    """Stores the current fingerprint of a finished stage ("mesh" or "solution"). Adds the property on first use.

    A new mesh makes an older solution out of date (a study from before today has none stored: it is marked)."""
    if stage == "results":
        stage = "solution"
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r} (mesh or solution)")
    if FINGERPRINT_PROP not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyMap", FINGERPRINT_PROP, "Study",
                             "[BW] Fingerprints of the last mesh and solution (Model Builder: out-of-date marks)")
        try:
            analysis.setPropertyStatus(FINGERPRINT_PROP, "Hidden")
        except Exception:
            pass
    stored = stored_fingerprints(analysis)
    current = fingerprints(analysis)
    if stage == "mesh":
        stored["mesh"] = current["mesh/main"]
        if "solution" not in stored and _has_results(analysis):
            stored["solution"] = "stale"
    else:
        stored["solution"] = current["step/main"]
        if "mesh" not in stored:
            stored["mesh"] = current["mesh/main"]
    analysis.BW_Fingerprints = stored


def set_running(analysis, stage, on=True):
    keys = _RUNNING.setdefault((analysis.Document.Name, analysis.Name), set())
    (keys.add if on else keys.discard)(stage)


def _running(analysis):
    return _RUNNING.get((analysis.Document.Name, analysis.Name), set())


def _has_results(analysis):
    if is_airflow(analysis):
        return bool(_get(analysis, "CfdSummary", ""))
    return bool(sim.members(analysis)["results"])


# ----- the state ---------------------------------------------------------------------------------------------------


def _worst(states):
    states = [s for s in states if s]
    for level in _PRIORITY:
        if level in states:
            return level
    return None


def state(analysis, findings=None, precheck=True):
    """{node key: mark}. ``findings`` is node_messages() output to reuse; ``precheck=False`` skips the pre-check
    (the intrinsic "todo" marks stay), which matters for airflow studies whose pre-check tessellates the model."""
    tree = model_tree(analysis)
    if findings is None:
        findings = node_messages(analysis, tree) if precheck else {}
    current = fingerprints(analysis)
    stored = stored_fingerprints(analysis)
    running = _running(analysis)
    m = sim.members(analysis)
    air = is_airflow(analysis)
    have_results = _has_results(analysis)
    out = {}

    def finding_state(key):
        levels = {lvl for lvl, _text in findings.get(key, [])}
        return "todo" if "error" in levels else "warn" if "warning" in levels else "ok"

    def stage_state(stage, have, cur):
        if stage in running:
            return "run"
        if not have:
            return "todo"
        old = stored.get(stage)
        return "ok" if old is None or old == cur else "stale"

    solution = stage_state("solution", have_results, current["step/main"])
    if air:
        mesh_state = "run" if "mesh" in running else solution
    else:
        mesh_state = stage_state("mesh", m["mesh"] is not None and sim.is_meshed(m["mesh"]), current["mesh/main"])

    def combine(compute, key):  # a compute node's own mark, else the pre-check's
        return compute if compute != "ok" else finding_state(key)

    for n in tree.walk():
        if n.kind in ("params", "selections", "section", "study"):
            continue  # placeholders carry no mark; sections are rolled up below
        if n.key == "mesh/main":
            out[n.key] = combine(mesh_state, n.key)
        elif n.key == "step/main":
            out[n.key] = combine(solution, n.key)
        elif n.kind in ("result", "plot", "table", "probe", "report"):
            out[n.key] = solution if have_results else "todo"
        else:
            obj = analysis.Document.getObject(n.obj) if n.obj else None
            mark = finding_state(n.key)
            out[n.key] = "todo" if mark == "ok" and _missing(analysis, n, obj) else mark

    def roll(n):
        if not n.children:
            return out.get(n.key)
        mark = _worst([roll(c) for c in n.children])
        if mark:
            out[n.key] = mark
        return mark

    for sec in tree.children:
        if sec.key == "defs":
            continue
        roll(sec)
        for key, own in (("mat", "material"), ("geo", "model")):
            if sec.key == key and not sec.children:
                out[key] = "todo"
        sec_findings = finding_state(sec.key)  # a finding aimed at the section itself ("No support")
        if sec_findings != "ok":
            out[sec.key] = _worst([out.get(sec.key), sec_findings])
    top = _worst([out.get(s.key) for s in tree.children])
    if top:
        out["study"] = top
    return out


def _missing(analysis, n, obj):
    """An intrinsic "todo": a required thing a definition node lacks."""
    if n.kind == "geometry":
        shape = _model_shape(analysis)
        return shape is None or not shape.Solids
    if obj is not None and obj.isDerivedFrom("Fem::Constraint") and "References" in obj.PropertiesList:
        if n.kind in ("gravity", "spin", "initialtemp"):
            return False
        return len(obj.References) == 0
    return False


# ----- messages ----------------------------------------------------------------------------------------------------

_RULES = (  # (words in the lower-cased finding, node key), first match wins
    (("separate solids", "no model", "no parts", "watertight"), "geo/part"),
    (("mesh", "nodes", "netgen", "cells", "preset", "y+", "domain"), "mesh/main"),
    (("solver", "ccx", "calculix", "openfoam"), "step/solver"),
    (("propeller", "motor", "thrust", "disk"), "phys/disks"),
    (("turbulence",), "phys/turbulence"),
    (("material", "young", "density", "yield", "conductivity"), "mat"),
    (("support", "rigid-body", "free-free", "load", "force", "pressure", "spin", "balance", "temperature",
      "convection", "heat"), "phys"),
)


def node_messages(analysis, tree=None):
    """The pre-check findings by node key: {key: [(level, text)]} with level "error" or "warning".

    A finding that starts with an object's label ("Force: ...") goes to that object's node; the rest by keywords;
    anything else to the study node."""
    tree = tree or model_tree(analysis)
    labels = sorted(((n.label, n.key) for n in tree.walk() if n.obj and n.kind not in ("study",)),
                    key=lambda p: -len(p[0]))
    keys = {n.key for n in tree.walk()}
    out = {}
    try:
        issues = sim.precheck(analysis)
    except Exception as exc:  # a broken study must still open
        issues = [("error", f"The pre-check failed: {exc}")]
    for level, text in issues:
        level = "error" if level == "error" else "warning"
        key = None
        for label, lkey in labels:
            if text.startswith(label + ":"):
                key = lkey
                break
        if key is None:
            low = text.lower()
            for words, target in _RULES:
                if any(w in low for w in words):
                    key = target
                    break
        if key is None or key not in keys:
            if key == "mat" and not [k for k in keys if k.startswith("mat/")]:
                key = "mat"
            elif key not in keys:
                key = "study"
        out.setdefault(key, []).append((level, text))
    return out


# ----- settings declarations ---------------------------------------------------------------------------------------

REFS = ("References", "Faces, edges or vertices the setting applies to")


def _refs_fields():
    return [_f("References", "Selection", "selection", help="Faces, edges or vertices the setting applies to")]


def _readonly(name, label, unit="", help=""):
    return _f(name, label, "readonly", unit, help=help, access="info:" + name)


def _enum_choices(obj, prop):
    try:
        return tuple(obj.getEnumerationsOfProperty(prop))
    except Exception:
        return ()


def settings(node):
    """The node's settings form: a list of Section. Fields appear for the node's study type only."""
    analysis = _analysis(node)
    obj = _obj(node)
    kind = kind_of(analysis)
    air = kind in sim.AIRFLOW_KINDS
    k = node.kind
    S = []

    def add(title, *fields):
        fields = [f for f in fields if f is not None and _available(analysis, obj, f)]
        if fields:
            S.append(Section(title, fields))

    def A(name, label, fkind, unit="", choices=(), help="", **kw):
        return _f(name, label, fkind, unit, choices, help, on="analysis", **kw)

    if k == "study":
        add("Study", _f("Label", "Label", "text", on="analysis", access="label"),
            _readonly("type", "Type"), _readonly("model", "Analysed part"))
        if not air:
            add("Safety factor", A("Knockdown", "Knockdown factor", "float", "", min=0.0,
                                   help="allowable stress = knockdown x yield strength (1 = none)"),
                A("StressMeasure", "Stress measure", "enum", choices=sim.measure_keys(),
                  help="The equivalent stress the safety factor divides"))
    elif k == "geometry":
        add("Geometry", _readonly("part", "Part"), _readonly("solids", "Solids"), _readonly("volume", "Volume", "mm^3"),
            _readonly("area", "Surface area", "mm^2"), _readonly("size", "Bounding box", "mm"))
    elif k == "material":
        cards = _card_choices()
        add("Material", _f("Label", "Label", "text", access="label"),
            _f("card", "Material", "enum", choices=[name for name, _v in cards], access="card",
               help="A card from the material library; its numbers are copied into the study"))
        add("Properties", _readonly("E", "Young's modulus", "MPa"), _readonly("nu", "Poisson's ratio"),
            _readonly("density", "Density", "kg/m^3"), _readonly("yield", "Yield strength", "MPa"))
    elif k in ("fixed", "displacement", "hold"):
        add("Selection", *_refs_fields())
        if k != "fixed":
            fields = []
            for ax in "xyz":
                fields += [_f(f"{ax}Free", f"{ax.upper()} free", "bool", help="Off: the displacement below is imposed"),
                           _f(f"{ax}Displacement", f"{ax.upper()} displacement", "float", "mm")]
            add("Displacement", *fields)
    elif k == "force":
        add("Selection", *_refs_fields())
        add("Force", _f("Force", "Force", "float", "N", min=0.0),
            _f("Direction", "Direction", "selection", help="A straight edge or a flat face: the force acts along it"),
            _f("Reversed", "Reversed", "bool"))
    elif k == "pressure":
        add("Selection", *_refs_fields())
        add("Pressure", _f("Pressure", "Pressure", "float", "MPa"), _f("Reversed", "Reversed", "bool"))
    elif k == "gravity":
        add("Gravity", _f("GravityAcceleration", "Acceleration", "float", "m/s^2"),
            _f("GravityDirection", "Direction", "vector"))
    elif k == "spin":
        add("Spin", _f("rpm", "Speed", "float", "rpm", access="spin_rpm", min=0.0),
            _f("RotationAxis", "Axis", "selection", help="A circular edge, cylindrical face or straight edge"))
    elif k == "temperature":
        add("Selection", *_refs_fields())
        add("Temperature", _f("Temperature", "Temperature", "float", "degC"))
    elif k == "heatflux":
        add("Selection", *_refs_fields())
        add("Heat flux", _f("DFlux", "Heat flux", "float", "W/m^2", help="Positive into the part"))
    elif k == "convection":
        add("Selection", *_refs_fields())
        add("Convection", _f("FilmCoef", "Film coefficient h", "float", "W/m^2/K", min=0.0),
            _f("AmbientTemp", "Ambient temperature", "float", "degC"))
    elif k == "heatsource":
        add("Selection", *_refs_fields())
        add("Heat source", _f("TotalPower", "Total power", "float", "W"))
    elif k == "initialtemp":
        add("Initial temperature", _f("initialTemperature", "Temperature", "float", "degC"))
    elif k == "constraint":
        add("Selection", *_refs_fields())
    elif k == "mesh":
        if air:
            add("Mesh", A("MeshPreset", "Mesh", "enum", choices=_presets(), help="Cell budget and refinement of the OpenFOAM mesh"))
        else:
            add("Size", _f("MaxSize", "Maximum element size", "float", "mm", min=0.001),
                _f("Fineness", "Fineness", "enum", choices=sim.FINENESS),
                _f("SecondOrder", "Second order (Tet10)", "bool"),
                A("MeshNodeLimit", "Node limit", "int", help="Hard limit on the estimated nodes; 0 = the preference"))
            add("Status", _readonly("nodes", "Nodes"), _readonly("elements", "Elements"))
    elif k == "step":
        add("Study step", _readonly("step", "Type"), *_step_fields(analysis))
    elif k == "solver":
        if air:
            add("OpenFOAM", _readonly("openfoam", "Installation"))
        else:
            add("CalculiX", _readonly("ccx", "Binary"), _f("MatrixSolverType", "Matrix solver", "enum",
                choices=_enum_choices(obj, "MatrixSolverType")),
                _f("GeometricalNonlinearity", "Geometric nonlinearity", "enum",
                   choices=_enum_choices(obj, "GeometricalNonlinearity")) if kind == "static" else None)
    elif k == "air":
        add("Air", A("AirTemperature", "Temperature", "float", "degC", min=-60.0, max=60.0),
            A("AirAltitude", "Altitude", "float", "m", min=0.0, max=10000.0,
              help="Standard atmosphere: sets the density and viscosity"))
    elif k == "farfield":
        add("Inlet", A("FlowSpeed", "Speed", "float", "m/s", min=0.0, max=1000.0),
            A("AngleOfAttack", "Angle of attack", "float", "deg", min=-90.0, max=90.0,
              help="Pitch about the model's Y axis; the flow runs along +X"))
        add("Far field", A("FarField", "Far-field condition", "enum", choices=("slip", "symmetry")),
            A("DomainUpstream", "Upstream distance", "float", "L", min=1.0, max=50.0,
              help="In characteristic lengths L (the largest size of the model)"),
            A("DomainDownstream", "Downstream distance", "float", "L", min=1.0, max=100.0),
            A("DomainLateral", "Sideways distance", "float", "L", min=1.0, max=50.0),
            A("DomainWake", "Wake side", "float", "L", min=1.0, max=100.0) if kind == "prop_wash" else None)
    elif k == "walls":
        add("Force coefficients", A("ReferenceArea", "Reference area", "float", "m^2", min=0.0,
                                    help="0 = the frontal area of the bounding box") if kind == "external_flow" else None,
            A("ReferenceLength", "Reference length", "float", "m", min=0.0,
              help="0 = the model length along the flow") if kind == "external_flow" else None,
            _readonly("walls", "Condition"))
    elif k == "turbulence":
        add("Turbulence", A("Turbulence", "Model", "enum", choices=("kOmegaSST", "laminar")))
    elif k in ("disks", "disk"):
        if k == "disk":
            add("Motor", _readonly("motor", "Motor part"))
        add("Actuator disk (every motor)",
            A("PropDiameter", "Propeller diameter", "float", "mm", min=0.0, help="One disk of this diameter per marked motor"),
            A("HubDiameter", "Hub diameter", "float", "mm", min=0.0, help="0 = 20 % of the diameter"),
            A("TypedThrust", "Thrust per motor", "float", "N", min=0.0, help="0 = the hover share from Mass & Balance"),
            A("DiskOffset", "Disk offset", "float", "mm", min=0.0, help="0 = just above the motor part"))
    elif k == "plot":
        key = node.key.split("/")[-1]
        if key == "stress":
            add("Quantity", A("StressMeasure", "Stress measure", "enum", choices=sim.measure_keys()))
        elif key == "modes":
            add("Deformation", A("ModeScale", "Largest displacement", "float", "", min=0.0, max=1.0,
                                 help="Fraction of the bounding-box diagonal"))
        elif key == "pressure":
            add("Colour range", A("PressureFixed", "Fixed range", "bool"), A("PressureMin", "Lowest", "float", "Pa"),
                A("PressureMax", "Highest", "float", "Pa"))
    return S


def _presets():
    import bruceworx_cfd as cfd

    return tuple(cfd.PRESETS)


def _step_fields(analysis):
    """The solution step's settings of a study type, as the study's own properties."""
    kind = kind_of(analysis)
    A = lambda *a, **kw: _f(*a, on="analysis", **kw)  # noqa: E731
    if kind == "modal":
        return [A("ModeCount", "Number of modes", "int", min=1), A("FrequencyMin", "Lowest frequency", "float", "Hz", min=0.0),
                A("FrequencyMax", "Highest frequency", "float", "Hz", min=0.0),
                A("Prestressed", "Prestressed by the loads", "bool"),
                A("ExcitationCheck", "Check an excitation band", "bool"),
                A("ExcitationSource", "Excitation source", "enum", choices=("MotorSpeed", "FrequencyBand")),
                A("MotorRpmMin", "Lowest motor speed", "float", "rpm"), A("MotorRpmMax", "Highest motor speed", "float", "rpm"),
                A("BladeCount", "Blades per propeller", "int", min=1),
                A("BandMin", "Band, lower edge", "float", "Hz"), A("BandMax", "Band, upper edge", "float", "Hz")]
    if kind == "buckling":
        return [A("ModeCount", "Number of load factors", "int", min=1),
                A("TargetLoadFactor", "Target load factor", "float", "", min=0.0),
                A("Nonlinear", "Also run a nonlinear analysis", "bool"),
                A("ImperfectionAmplitude", "Imperfection amplitude", "float", "mm", min=0.0),
                A("NonlinearLoadLimit", "Nonlinear load limit", "float", "", min=0.0),
                A("CollapseDisplacement", "Collapse displacement", "float", "mm", min=0.0)]
    if kind in ("thermal", "thermal_stress"):
        out = [A("InitialTemperature", "Initial temperature", "float", "degC")]
        if kind == "thermal":
            out += [A("Transient", "Time dependent", "bool"), A("EndTime", "End time", "float", "s", min=0.0),
                    A("TimeStep", "Time step", "float", "s", min=0.0)]
        else:
            out.append(A("ReferenceTemperature", "Stress-free temperature", "float", "degC"))
        return out
    return []


def _card_choices():
    """[(display name, card values)] of the material library; a name used twice gets its library appended."""
    cards = sim.material_cards()
    counts = {}
    for c in cards:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    return [(c["name"] if counts[c["name"]] == 1 else f"{c['name']} ({c['library']})", c) for c in cards]


def _available(analysis, obj, f):
    """A declared field exists for this study: its property is there, or it is computed."""
    if f.access:
        return True
    target = analysis if f.on == "analysis" else obj
    return f.prop in _props(target)


def _field(node, field):
    if isinstance(field, Field):
        return field
    for section in settings(node):
        for f in section.fields:
            if f.name == field:
                return f
    raise KeyError(f"{node.key} has no setting {field!r}")


# ----- reading -----------------------------------------------------------------------------------------------------


def _quantity_value(prop_value, unit):
    """A property value as a number in ``unit`` (degC: a temperature quantity is in kelvin)."""
    if unit == "degC":
        return float(prop_value.Value) - 273.15 if hasattr(prop_value, "Value") else float(prop_value)
    if hasattr(prop_value, "getValueAs"):
        return float(prop_value.getValueAs("1/min" if unit == "rpm" else unit)) if unit and unit != "L" else float(prop_value.Value)
    return float(prop_value)


def get_value(node, field):
    """A field's current value: numbers in the field's unit, enum/text strings, bools, (x, y, z) tuples, and for a
    selection a list of (object name, sub-element name)."""
    f = _field(node, field)
    analysis = _analysis(node)
    target = analysis if f.on == "analysis" else _obj(node)
    if f.access:
        return _access_get(node, f, analysis, target)
    value = getattr(target, f.prop)
    if f.kind == "float":
        return _quantity_value(value, f.unit)
    if f.kind == "int":
        return int(value)
    if f.kind == "bool":
        return bool(value)
    if f.kind in ("enum", "text"):
        return str(value)
    if f.kind == "vector":
        return (float(value.x), float(value.y), float(value.z))
    if f.kind == "selection":
        return _selection_get(value, single=f.prop == "Direction")
    return value


def _selection_get(value, single=False):
    items = [value] if single and value else list(value or [])
    out = []
    for item in items:
        if item is None:
            continue
        obj, subs = item[0], item[1]
        subs = [subs] if isinstance(subs, str) else list(subs)
        out += [(obj.Name, s) for s in subs] if subs else [(obj.Name, "")]
    return out


def _info(node, name):
    analysis = _analysis(node)
    obj = _obj(node)
    kind = kind_of(analysis)
    if name == "type":
        return dict((k, l) for k, l, _ok in sim.STUDY_TYPES).get(kind, kind)
    if name == "model":
        model = _model(analysis)
        return model.Label if model is not None else ""
    if name in ("part", "solids", "volume", "area", "size"):
        shape = _model_shape(analysis)
        if shape is None:
            return ""
        return {"part": obj.Label if obj is not None else "", "solids": len(shape.Solids), "volume": round(shape.Volume, 3),
                "area": round(shape.Area, 3),
                "size": " x ".join(f"{v:.4g}" for v in (shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength))}[name]
    if name in ("E", "nu", "density", "yield"):
        values = None
        if obj is not None:
            data = obj.Material or {}
            values = {"E": sim._quantity_mpa(data.get("YoungsModulus")), "nu": sim._number(data.get("PoissonRatio")),
                      "density": sim._density_kg_m3(data.get("Density")),
                      "yield": sim._quantity_mpa(data.get("YieldStrength"))}
        value = (values or {}).get(name)
        return value if value is not None else ""
    if name == "nodes":
        return sim.mesh_stats(obj)["nodes"] if obj is not None else 0
    if name == "elements":
        return sim.mesh_stats(obj)["elements"] if obj is not None else 0
    if name == "step":
        return STEP_LABELS.get(kind, kind)
    if name == "ccx":
        return sim.find_ccx() or "not found"
    if name == "openfoam":
        import bruceworx_cfd as cfd

        found = cfd.find_openfoam()
        return found["launcher"] if found else "not installed"
    if name == "walls":
        return "No-slip walls on every part of the model"
    if name == "motor":
        return obj.Label if obj is not None else ""
    return ""


def _access_get(node, f, analysis, target):
    kind, _sep, name = f.access.partition(":")
    if kind == "info":
        return _info(node, name)
    if kind == "label":
        return target.Label[len("Study: "):] if f.on == "analysis" and target.Label.startswith("Study: ") else target.Label
    if kind == "spin_rpm":
        import bruceworx_sim_post as post

        return post.spin_rpm(target)
    if kind == "card":
        uuid = _get(analysis, "MaterialUUID", "")
        cards = _card_choices()
        for name_, values in cards:
            if uuid and values["uuid"] == uuid:
                return name_
        return (target.Material or {}).get("Name", "")
    raise KeyError(f.access)


# ----- writing -----------------------------------------------------------------------------------------------------


def _unit_quantity(unit):
    import FreeCAD

    return FreeCAD.Units.Quantity("1 " + {"rpm": "1/min", "degC": "K"}.get(unit, unit))


def parse_number(f, value):
    """A number (or Quantity string) in the field's unit; raises ValueError for a wrong unit or a bad range."""
    import FreeCAD

    unit = f.unit
    if isinstance(value, bool):
        raise ValueError(f"{f.label}: a number is expected")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", ".") if str(value).count(",") == 1 and "." not in str(value) else str(value).strip()
        text = text.replace("°C", "degC")
        try:
            number = float(text)
        except ValueError:
            number = None
        if number is None:
            if unit in ("", "L"):
                try:
                    number = float(text.rstrip("L").strip()) if unit == "L" else None
                except ValueError:
                    number = None
                if number is None:
                    raise ValueError(f"{f.label}: '{value}' is not a number")
            else:
                number = _parse_quantity(f, text)
    if f.min is not None and number < f.min:
        raise ValueError(f"{f.label}: {number:g} is below the lowest value {f.min:g}{(' ' + unit) if unit else ''}")
    if f.max is not None and number > f.max:
        raise ValueError(f"{f.label}: {number:g} is above the highest value {f.max:g}{(' ' + unit) if unit else ''}")
    if f.kind == "int":
        if abs(number - round(number)) > 1e-9:
            raise ValueError(f"{f.label}: a whole number is expected, got {value}")
        return int(round(number))
    return number


def _parse_quantity(f, text):
    import FreeCAD

    unit = f.unit
    if unit == "degC":
        low = text.replace(" ", "")
        for suffix, shift in (("degC", 0.0), ("C", 0.0), ("K", -273.15)):
            if low.endswith(suffix):
                try:
                    return float(low[:-len(suffix)]) + shift
                except ValueError:
                    break
        raise ValueError(f"{f.label}: expected a temperature such as '20 degC' or '293 K', got '{text}'")
    text = text.replace("rpm", "1/min") if unit == "rpm" else text
    try:
        q = FreeCAD.Units.Quantity(text)
    except Exception:
        raise ValueError(f"{f.label}: cannot read '{text}' as a value in {unit}") from None
    if q.Unit != _unit_quantity(unit).Unit:
        raise ValueError(f"{f.label}: expected a value in {unit} (for example '5 {unit}'), "
                         f"got '{text}' ({q.Unit.Type or 'a different unit'})")
    return float(q.getValueAs("1/min" if unit == "rpm" else unit))


def _selection_set(refs, single=False):
    """[(object name or object, sub or [subs])] -> the property value."""
    import FreeCAD

    doc = None
    grouped = {}
    order = []
    for item in refs or []:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError(f"a selection item is (object, sub-element), got {item!r}")
        obj, subs = item
        if isinstance(obj, str):
            obj = _lookup(obj)
        subs = [subs] if isinstance(subs, str) else list(subs)
        if obj is None:
            raise ValueError(f"no such object in the selection: {item[0]!r}")
        for sub in subs:
            if sub:
                _check_sub(obj, sub)
        if obj.Name not in grouped:
            grouped[obj.Name] = (obj, [])
            order.append(obj.Name)
        grouped[obj.Name][1].extend(s for s in subs if s)
    result = [(grouped[n][0], grouped[n][1]) for n in order]
    if single:
        if len(result) > 1 or (result and len(result[0][1]) > 1):
            raise ValueError("this setting takes one edge or face")
        return (result[0][0], result[0][1]) if result else None
    return result


_DOC_FOR_LOOKUP = []


def _lookup(name):
    return _DOC_FOR_LOOKUP[-1].getObject(name) if _DOC_FOR_LOOKUP else None


def _check_sub(obj, sub):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return
    try:
        shape.getElement(sub)
    except Exception:
        raise ValueError(f"{obj.Label} has no {sub}") from None


def set_value(node, field, value):
    """Writes a field into its object or the study, in one document transaction (undo reverts it as one step).

    Numbers may be given as a Quantity string with a unit ("500 N", "2 mm", "12000 rpm", "20 degC"); a unit of the
    wrong kind raises ValueError, and so does a value out of range. Nothing is recomputed, meshed or solved."""
    f = _field(node, field)
    if f.kind == "readonly":
        raise ValueError(f"{f.label} cannot be changed")
    analysis = _analysis(node)
    doc = analysis.Document
    target = analysis if f.on == "analysis" else _obj(node)
    _DOC_FOR_LOOKUP.append(doc)
    try:
        new = _parse(f, value, target, analysis)
    finally:
        _DOC_FOR_LOOKUP.pop()
    doc.openTransaction(f"Set {f.label}")
    try:
        _write(node, f, target, analysis, new)
        if target is not None and target.TypeId.startswith("Fem::Constraint"):
            try:
                doc.recompute([target])  # a constraint redraws its symbols; the mesh is never recomputed here
            except Exception:
                pass
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


def _parse(f, value, target, analysis):
    if f.kind in ("float", "int"):
        return parse_number(f, value)
    if f.kind == "bool":
        if isinstance(value, str):
            low = value.strip().lower()
            if low not in ("true", "false", "yes", "no", "on", "off", "1", "0"):
                raise ValueError(f"{f.label}: yes or no expected, got '{value}'")
            return low in ("true", "yes", "on", "1")
        return bool(value)
    if f.kind == "enum":
        choices = f.choices or (_enum_choices(target, f.prop) if target is not None else ())
        if choices and str(value) not in choices:
            raise ValueError(f"{f.label}: '{value}' is not one of {', '.join(choices)}")
        return str(value)
    if f.kind == "text":
        return str(value)
    if f.kind == "vector":
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            raise ValueError(f"{f.label}: three numbers (x, y, z) expected")
        return tuple(float(v) for v in value)
    if f.kind == "selection":
        return _selection_set(value, single=f.prop == "Direction")
    raise ValueError(f"{f.label} cannot be changed")


def _write(node, f, target, analysis, new):
    import FreeCAD

    if f.access:
        kind = f.access.partition(":")[0]
        if kind == "label":
            target.Label = ("Study: " + new) if f.on == "analysis" and target.Label.startswith("Study: ") else new
        elif kind == "spin_rpm":
            import bruceworx_sim_post as post

            post.set_spin_speed(target, new)
        elif kind == "card":
            values = dict(_card_choices()).get(new)
            if values is None:
                raise ValueError(f"no material card {new!r}")
            target.Material = sim.fem_material_dict(values, target.Material)
            target.Label = f"Material: {values['name']}"
            if "MaterialUUID" in analysis.PropertiesList:
                analysis.MaterialUUID = values["uuid"] or ""
        else:
            raise ValueError(f"{f.label} cannot be changed")
        return
    current = getattr(target, f.prop)
    if f.kind == "float":
        if hasattr(current, "getValueAs"):
            if f.unit == "degC":
                setattr(target, f.prop, f"{new + 273.15!r} K")
            else:
                setattr(target, f.prop, f"{new!r} {f.unit}")
        else:
            setattr(target, f.prop, float(new))
    elif f.kind == "vector":
        setattr(target, f.prop, FreeCAD.Vector(*new))
    else:
        setattr(target, f.prop, new)
