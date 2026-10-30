# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulation studies (roadmap SIM-01, static stress end to end; 27 Oct 2026).

The headless core of the Simulation workspace. A *study* is a stock FEM analysis
(``Fem::FemAnalysis``) with four [BW] properties (``StudyType``, ``Model``,
``MaterialUUID``, ``Knockdown``) and the stock members a static CalculiX run
needs: the ccxtools solver, one solid material, the loads and constraints and
a Netgen mesh. Everything here works in FreeCADCmd; the task panels, the field
colouring and the PDF are in bruceworx_sim_gui.

* ``new_study`` creates the objects from a ``StudyPlan`` (name, type, model,
  material card, mesh size). The mesh is the legacy Netgen object
  (``Fem::FemMeshShapeNetgenObject``, the C++ plugin that is built in); the
  Python Netgen mesher needs the ``netgen`` package, which the environment
  does not have. Default element size: bounding-box diagonal / 30, Moderate,
  second order (Tet10).
* ``precheck`` lists what is missing before a solve: model, material (E, nu),
  mesh, supports (rigid-body motion heuristics), loads, empty references, ccx.
* ``solve`` writes the CalculiX input with the stock ``FemToolsCcx``, runs ccx,
  loads the results and adds the safety factor to the result object:
  ``SafetyFactor[i] = allowable / vonMises[i]``, ``allowable = Knockdown * yield``
  (the FDM knockdown is SIM-03; ``Knockdown`` is 1 until then). A card without
  a yield strength uses the ultimate tensile strength, with a note.
* ``find_ccx`` looks for the solver in the FEM preference, then beside the
  program (Keystone bundles it as ``Contents/bin/ccx``), then the pixi
  environment of a development tree, then PATH. ``ensure_ccx_pref`` writes the
  first find into the preference once, so a fresh profile solves without a
  visit to the FEM settings.
* ``report_html`` writes the study report (model, material, mesh, loads,
  results, renders) as one self-contained HTML file.

Theory (plan chapter 60): static K u = f; sigma_vM from the stress tensor
(CalculiX reports the nodal tensor, FreeCAD computes von Mises);
SF = sigma_allow / sigma_vM per node, min SF = sigma_allow / max sigma_vM.
"""

from __future__ import annotations

import base64
import datetime
import html
import math
import os
import shutil
import time

CCX_PREFS = "User parameter:BaseApp/Preferences/Mod/Fem/Ccx"
CCX_PREF_NAME = "ccxBinaryPath"
MESH_DIVISOR = 30.0  # max element size = bounding-box diagonal / 30
FINENESS = ("VeryCoarse", "Coarse", "Moderate", "Fine", "VeryFine")
DEFAULT_FINENESS = "Moderate"
SF_CAP = 100.0  # a node with (almost) no stress reports this safety factor
KNOCKDOWN = 1.0  # SIM-03: allowable = Knockdown * yield; 1 until the FDM model lands

# (key, label, available). The frame carries every type; only static runs today.
STUDY_TYPES = (
    ("static", "Static stress", True),
    ("modal", "Modal frequencies", False),
    ("buckling", "Structural buckling", False),
    ("thermal", "Thermal", False),
    ("thermal_stress", "Thermal stress", False),
)

SUPPORT_TYPES = ("Fem::ConstraintFixed", "Fem::ConstraintDisplacement", "Fem::ConstraintTransform",
                 "Fem::ConstraintRigidBody")
LOAD_TYPES = ("Fem::ConstraintForce", "Fem::ConstraintPressure", "Fem::ConstraintSelfWeight",
              "Fem::ConstraintCentrif", "Fem::ConstraintBearing", "Fem::ConstraintGear", "Fem::ConstraintPulley",
              "Fem::ConstraintTemperature", "Fem::ConstraintHeatflux")


class StudyError(Exception):
    """A study cannot go on: the message says what is missing."""


# ----- CalculiX --------------------------------------------------------------------------------


def _home():
    import FreeCAD

    return FreeCAD.getHomePath().rstrip("/")


def ccx_candidates():
    """Where ccx may be, in order: the preference, the bundle, the development environment, PATH."""
    import FreeCAD

    found = []
    pref = FreeCAD.ParamGet(CCX_PREFS).GetString(CCX_PREF_NAME, "")
    if pref:
        found.append(pref)
    home = _home()
    found.append(os.path.join(home, "bin", "ccx"))  # Keystone: Contents/bin/ccx (make-keystone.sh)
    found.append(os.path.join(home, "..", "..", ".pixi", "envs", "default", "bin", "ccx"))  # build/release
    which = shutil.which("ccx")
    if which:
        found.append(which)
    return [os.path.normpath(p) for p in found]


def find_ccx():
    """The ccx binary to run, or None."""
    for path in ccx_candidates():
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def ensure_ccx_pref():
    """Writes the first ccx found into the FEM preference when it is empty (FemToolsCcx reads it)."""
    import FreeCAD

    prefs = FreeCAD.ParamGet(CCX_PREFS)
    current = prefs.GetString(CCX_PREF_NAME, "")
    if current and os.path.isfile(current):
        return current
    path = find_ccx()
    if path:
        prefs.SetString(CCX_PREF_NAME, path)
    return path


def ccx_version(path=None):
    """'2.23' from ``ccx -v``, or ''."""
    import subprocess

    path = path or find_ccx()
    if not path:
        return ""
    try:
        out = subprocess.run([path, "-v"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""
    for word in out.replace("\n", " ").split():
        if word[:1].isdigit() and "." in word:
            return word.strip(".")
    return ""


# ----- materials -------------------------------------------------------------------------------


def _quantity_mpa(value):
    """A card value (Quantity or text) in MPa, or None."""
    import FreeCAD

    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = FreeCAD.Units.Quantity(value)
        if hasattr(value, "getValueAs"):
            return float(value.getValueAs("MPa"))
        return float(value)
    except Exception:
        return None


def _number(value):
    """A unitless card value (Poisson's ratio) as float, or None."""
    import FreeCAD

    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = FreeCAD.Units.Quantity(value)
        return float(getattr(value, "Value", value))
    except Exception:
        return None


def _density_kg_m3(value):
    import FreeCAD

    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = FreeCAD.Units.Quantity(value)
        return float(value.getValueAs("kg/m^3"))
    except Exception:
        return None


def card_values(material):
    """The engineering values of a Materials.Material: E and nu in MPa, yield, UTS, density."""
    props = set(getattr(material, "PhysicalProperties", []) or [])

    def value(name):
        if name not in props:
            return None
        try:
            return material.getPhysicalValue(name)
        except Exception:
            return None

    return {
        "uuid": getattr(material, "UUID", ""),
        "name": getattr(material, "Name", ""),
        "library": getattr(material, "LibraryName", ""),
        "E": _quantity_mpa(value("YoungsModulus")),
        "nu": _number(value("PoissonRatio")),
        "yield": _quantity_mpa(value("YieldStrength")),
        "uts": _quantity_mpa(value("UltimateTensileStrength")),
        "density": _density_kg_m3(value("Density")),
    }


def usable(values):
    """A card a static study can run with: E and nu present."""
    return bool(values and values.get("E") and values.get("nu") is not None)


def allowable_stress(values, knockdown=KNOCKDOWN):
    """(allowable MPa, basis): yield strength times the knockdown; UTS with a note when no yield; (None, note)."""
    if not values:
        return None, "no material"
    if values.get("yield"):
        return values["yield"] * knockdown, "yield strength"
    if values.get("uts"):
        return values["uts"] * knockdown, "ultimate tensile strength (the card has no yield strength)"
    return None, "the card has neither a yield nor an ultimate tensile strength"


def material_manager():
    import Materials

    return Materials.MaterialManager()


def material_cards(manager=None):
    """Every card with E and nu, as card_values dicts: vault library first, then the others by name."""
    manager = manager or material_manager()
    cards = []
    for _uuid, material in manager.Materials.items():
        values = card_values(material)
        if usable(values):
            cards.append(values)

    def key(values):
        library = values["library"]
        rank = 0 if "vault" in library.lower() else (2 if library in ("System", "BruceWorx") else 1)
        return (rank, library, values["name"].lower())

    cards.sort(key=key)
    return cards


def card(uuid, manager=None):
    """card_values of the card with this UUID, or None."""
    if not uuid:
        return None
    try:
        return card_values((manager or material_manager()).getMaterial(uuid))
    except Exception:
        return None


def _bodies(obj, seen=None):
    """The shape-bearing objects of obj: itself with a ShapeMaterial, else its group members and links."""
    seen = seen if seen is not None else set()
    if obj is None or obj.Name in seen:
        return []
    seen.add(obj.Name)
    props = getattr(obj, "PropertiesList", ())
    if "ShapeMaterial" in props:
        return [obj]
    found = []
    linked = getattr(obj, "LinkedObject", None)
    if linked is not None and linked is not obj:
        found += _bodies(linked, seen)
    for child in getattr(obj, "Group", None) or []:
        found += _bodies(child, seen)
    return found


def material_of(obj):
    """The Materials.Material of a body or of the first body in a part that has one chosen; None."""
    for body in _bodies(obj):
        material = getattr(body, "ShapeMaterial", None)
        if material is not None and getattr(material, "Name", "Default") != "Default":
            return material
    return None


def fem_material_dict(values, base=None):
    """The dict a Fem material object takes, from card values."""
    data = dict(base or {})
    data["Name"] = values.get("name") or "Material"
    data["YoungsModulus"] = f"{values['E']:.6g} MPa"
    data["PoissonRatio"] = f"{values['nu']:.6g}"
    if values.get("density"):
        data["Density"] = f"{values['density']:.6g} kg/m^3"
    if values.get("yield"):
        data["YieldStrength"] = f"{values['yield']:.6g} MPa"
    if values.get("uts"):
        data["UltimateTensileStrength"] = f"{values['uts']:.6g} MPa"
    return data


# ----- the model ------------------------------------------------------------------------------


def shape_of(obj):
    """The global shape of obj, or None."""
    try:
        shape = obj.Shape if hasattr(obj, "Shape") else None
    except Exception:
        shape = None
    if shape is None or shape.isNull():
        try:
            import Part

            shape = Part.getShape(obj)
        except Exception:
            return None
    if shape is None or shape.isNull():
        return None
    return shape


def is_model(obj):
    """An object a study can mesh: it has solids."""
    if obj is None or obj.TypeId.startswith("Fem::"):
        return False
    shape = shape_of(obj)
    return bool(shape and shape.Solids)


def model_candidates(doc, selection=None):
    """The objects a new study may analyse: the selection's models, the active Body or Part, visible roots."""
    found = []

    def add(obj):
        if obj is not None and is_model(obj) and obj not in found:
            found.append(obj)

    for obj in selection or []:
        add(obj)
    if found:
        return found
    try:
        import FreeCADGui as Gui

        for kind in ("pdbody", "part"):
            add(Gui.ActiveDocument.ActiveView.getActiveObject(kind) if Gui.ActiveDocument else None)
    except Exception:
        pass
    if found:
        return found
    for obj in getattr(doc, "RootObjects", []) or []:
        vobj = getattr(obj, "ViewObject", None)
        if vobj is not None and not vobj.Visibility:
            continue
        add(obj)
    return found


def default_mesh_size(obj):
    """Bounding-box diagonal / 30, in mm; 1 mm when the shape is empty."""
    shape = shape_of(obj)
    if shape is None:
        return 1.0
    return max(0.01, shape.BoundBox.DiagonalLength / MESH_DIVISOR)


def mesh_shape_object(doc, model):
    """The object the Netgen mesh takes its shape from: the model, or a compound of a container's bodies."""
    if "Shape" in getattr(model, "PropertiesList", ()):
        return model
    bodies = [b for b in _bodies(model) if is_model(b)]
    if len(bodies) == 1:
        return bodies[0]
    compound = doc.addObject("Part::Compound", f"{model.Name}_Compound")
    compound.Label = f"{model.Label} (bodies)"
    compound.Links = bodies
    doc.recompute()
    return compound


# ----- the study -------------------------------------------------------------------------------


class StudyPlan:
    """What New Study asks for."""

    def __init__(self, name="Study 1", study_type="static", model=None, material_uuid="", material=None,
                 max_size=None, fineness=DEFAULT_FINENESS, second_order=True, knockdown=KNOCKDOWN):
        self.name = name
        self.study_type = study_type
        self.model = model
        self.material_uuid = material_uuid
        self.material = material  # card_values; looked up from the UUID when None
        self.max_size = max_size
        self.fineness = fineness
        self.second_order = second_order
        self.knockdown = knockdown


def _add_study_properties(analysis, plan):
    if "StudyType" not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyString", "StudyType", "Study", "[BW] static, modal, buckling, thermal, thermal_stress")
        analysis.addProperty("App::PropertyLink", "Model", "Study", "[BW] The body or part the study analyses")
        analysis.addProperty("App::PropertyString", "MaterialUUID", "Study", "[BW] The material card the study was set up with")
        analysis.addProperty("App::PropertyFloat", "Knockdown", "Study", "[BW] allowable = Knockdown * yield (SIM-03 FDM knockdown; 1 = none)")
    analysis.StudyType = plan.study_type
    analysis.Model = plan.model
    analysis.MaterialUUID = plan.material_uuid or ""
    analysis.Knockdown = plan.knockdown


def new_study(doc, plan):
    """Creates the study's objects; returns {"analysis", "solver", "material", "mesh", "shape"}.

    The study type must be available (static); a planned type raises StudyError.
    """
    import FreeCAD
    import ObjectsFem

    if FreeCAD.GuiUp:
        import FemGui  # noqa: F401 -- registers the FEM view providers before the objects exist

    available = {key for key, _label, ok in STUDY_TYPES if ok}
    if plan.study_type not in available:
        raise StudyError(f"{plan.study_type} studies are planned, not built yet; static stress runs today")
    if plan.model is None or not is_model(plan.model):
        raise StudyError("the study needs a body or part with solids")
    values = plan.material or card(plan.material_uuid)
    analysis = ObjectsFem.makeAnalysis(doc, "Study")
    analysis.Label = f"Study: {plan.name}"
    _add_study_properties(analysis, plan)

    solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
    solver.Label = "CalculiX (static)"
    solver.AnalysisType = "static"
    solver.GeometricalNonlinearity = "linear"
    solver.ThermoMechSteadyState = False
    solver.MatrixSolverType = "default"
    solver.IterationsControlParameterTimeUse = False
    solver.SplitInputWriter = False
    solver.WorkingDir = ""
    analysis.addObject(solver)

    material = ObjectsFem.makeMaterialSolid(doc, "Material")
    if usable(values):
        material.Material = fem_material_dict(values, material.Material)
        material.Label = f"Material: {values['name']}"
    else:
        material.Label = "Material (none chosen)"
    analysis.addObject(material)

    shape = mesh_shape_object(doc, plan.model)
    mesh = ObjectsFem.makeMeshNetgenLegacy(doc, "Mesh")
    mesh.Label = f"Mesh: {plan.model.Label}"
    mesh.Shape = shape
    mesh.MaxSize = plan.max_size or default_mesh_size(plan.model)
    mesh.Fineness = plan.fineness if plan.fineness in FINENESS else DEFAULT_FINENESS
    mesh.SecondOrder = bool(plan.second_order)
    analysis.addObject(mesh)
    return {"analysis": analysis, "solver": solver, "material": material, "mesh": mesh, "shape": shape}


def is_study(obj):
    return obj is not None and obj.TypeId == "Fem::FemAnalysis" and "StudyType" in obj.PropertiesList


def studies(doc):
    return [obj for obj in getattr(doc, "Objects", []) if is_study(obj)]


def study_of(obj):
    """The study (analysis) an object belongs to, or the object itself when it is one."""
    if obj is None:
        return None
    if obj.TypeId == "Fem::FemAnalysis":
        return obj
    for parent in getattr(obj, "InList", []) or []:
        if parent.TypeId == "Fem::FemAnalysis":
            return parent
    return None


def active_study(doc, selection=None):
    """The study to act on: the selection's, else the active analysis, else the document's only one."""
    for obj in selection or []:
        study = study_of(obj)
        if study is not None:
            return study
    try:
        import FemGui

        active = FemGui.getActiveAnalysis()
        if active is not None and active.Document is doc:
            return active
    except Exception:
        pass
    found = studies(doc) or [o for o in getattr(doc, "Objects", []) if o.TypeId == "Fem::FemAnalysis"]
    return found[-1] if found else None


def members(analysis):
    """The study's members by role: solver, material(s), mesh, supports, loads, others, results."""
    out = {"solver": None, "materials": [], "mesh": None, "supports": [], "loads": [], "others": [], "results": []}
    for obj in getattr(analysis, "Group", []) or []:
        tid = obj.TypeId
        if obj.isDerivedFrom("Fem::FemSolverObject"):
            out["solver"] = out["solver"] or obj
        elif obj.isDerivedFrom("App::MaterialObject"):
            out["materials"].append(obj)
        elif obj.isDerivedFrom("Fem::FemMeshObject"):
            # the study's own mesh has a Shape; a result mesh (CCX_Results_Mesh) has not
            if out["mesh"] is None or ("Shape" in obj.PropertiesList and "Shape" not in out["mesh"].PropertiesList):
                out["mesh"] = obj
        elif obj.isDerivedFrom("Fem::FemResultObject"):
            out["results"].append(obj)
        elif tid in SUPPORT_TYPES:
            out["supports"].append(obj)
        elif tid in LOAD_TYPES:
            out["loads"].append(obj)
        elif obj.isDerivedFrom("Fem::Constraint"):
            out["others"].append(obj)
    return out


def mesh_stats(mesh_obj):
    """nodes, elements, order ('second' for Tet10/Hexa20), max size."""
    stats = {"nodes": 0, "elements": 0, "order": "", "max_size": float(getattr(mesh_obj, "MaxSize", 0) or 0),
             "fineness": str(getattr(mesh_obj, "Fineness", ""))}
    fm = getattr(mesh_obj, "FemMesh", None)
    if fm is None:
        return stats
    stats["nodes"] = fm.NodeCount
    stats["elements"] = fm.VolumeCount or fm.FaceCount
    if fm.VolumeCount:
        first = next(iter(fm.Volumes))
        stats["order"] = "second" if len(fm.getElementNodes(first)) > 4 else "first"
    return stats


def is_meshed(mesh_obj):
    fm = getattr(mesh_obj, "FemMesh", None)
    return bool(fm is not None and fm.NodeCount)


def mesh_study(analysis, progress=None):
    """Generates the study's mesh (recompute of the Netgen object); returns mesh_stats."""
    mesh = members(analysis)["mesh"]
    if mesh is None:
        raise StudyError("the study has no mesh object")
    if progress:
        progress("Meshing…")
    t0 = time.time()
    mesh.touch()
    mesh.Document.recompute([mesh])
    if not is_meshed(mesh):
        raise StudyError("Netgen made no mesh (an open or invalid shape?)")
    stats = mesh_stats(mesh)
    stats["seconds"] = round(time.time() - t0, 1)
    if progress:
        progress(f"Meshed: {stats['nodes']} nodes, {stats['elements']} elements in {stats['seconds']} s")
    return stats


def refine_mesh(analysis, factor=0.7):
    """Halves the element size (factor on MaxSize) and re-meshes; returns mesh_stats."""
    mesh = members(analysis)["mesh"]
    if mesh is None:
        raise StudyError("the study has no mesh object")
    mesh.MaxSize = max(0.01, float(mesh.MaxSize) * factor)
    return mesh_study(analysis)


# ----- the pre-check ---------------------------------------------------------------------------


def _references_empty(obj):
    refs = getattr(obj, "References", None)
    return refs is not None and len(refs) == 0


def _value(obj, name, unit=None):
    """A property's number, in ``unit`` when given (a Quantity's Value is in internal units: mN, MPa)."""
    try:
        value = getattr(obj, name)
        if unit and hasattr(value, "getValueAs"):
            return float(value.getValueAs(unit))
        return float(getattr(value, "Value", value))
    except Exception:
        return None


def precheck(analysis):
    """What is missing before a solve: a list of ("error"|"warning", text). Empty means ready."""
    issues = []
    m = members(analysis)
    model = getattr(analysis, "Model", None) if "Model" in analysis.PropertiesList else None
    if m["mesh"] is None:
        issues.append(("error", "No mesh object: New Study makes one"))
    else:
        shape_obj = getattr(m["mesh"], "Shape", None)
        shape = shape_of(shape_obj) if shape_obj is not None else None
        if shape is None or not shape.Solids:
            issues.append(("error", "No model with solids to mesh"))
        elif len(shape.Solids) > 1:
            issues.append(("warning", f"{len(shape.Solids)} separate solids: they are not joined (contacts are SIM-02)"))
        if not is_meshed(m["mesh"]):
            issues.append(("warning", "Not meshed yet: Solve meshes first"))
    if model is None and m["mesh"] is None:
        issues.append(("error", "No model: pick a body or part"))

    if not m["materials"]:
        issues.append(("error", "No material: pick one in the study or add a FEM material"))
    else:
        for mat in m["materials"]:
            data = mat.Material or {}
            E = _quantity_mpa(data.get("YoungsModulus"))
            nu = _number(data.get("PoissonRatio"))
            if not E or nu is None:
                issues.append(("error", f"{mat.Label}: no Young's modulus or Poisson's ratio"))
            elif not _quantity_mpa(data.get("YieldStrength")):
                if _quantity_mpa(data.get("UltimateTensileStrength")):
                    issues.append(("warning", f"{mat.Label}: no yield strength; the safety factor uses the ultimate tensile strength"))
                else:
                    issues.append(("warning", f"{mat.Label}: no yield or ultimate strength; no safety factor"))

    if not m["supports"]:
        issues.append(("error", "No support: add a Fixed or Displacement constraint, or the model moves as a rigid body"))
    else:
        fixed_axes = set()
        for sup in m["supports"]:
            if sup.TypeId == "Fem::ConstraintFixed":
                fixed_axes |= {"x", "y", "z"}
            elif sup.TypeId == "Fem::ConstraintDisplacement":
                for axis in "xyz":
                    if not getattr(sup, f"{axis}Free", True):
                        fixed_axes.add(axis)
            else:
                fixed_axes |= {"x", "y", "z"}
        if fixed_axes != {"x", "y", "z"}:
            missing = ", ".join(sorted({"x", "y", "z"} - fixed_axes))
            issues.append(("warning", f"The supports leave {missing} free: rigid-body motion is likely"))
    if not m["loads"]:
        issues.append(("error", "No load: add a Force or Pressure"))
    for obj in m["supports"] + m["loads"] + m["others"]:
        if _references_empty(obj) and obj.TypeId != "Fem::ConstraintSelfWeight":
            issues.append(("error", f"{obj.Label}: no faces, edges or vertices chosen"))
        if obj.TypeId == "Fem::ConstraintForce" and not _value(obj, "Force"):
            issues.append(("error", f"{obj.Label}: the force is zero"))
        if obj.TypeId == "Fem::ConstraintPressure" and not _value(obj, "Pressure"):
            issues.append(("error", f"{obj.Label}: the pressure is zero"))
    if m["solver"] is None:
        issues.append(("error", "No solver: New Study makes a CalculiX solver"))
    if not find_ccx():
        issues.append(("error", "CalculiX (ccx) not found: Keystone bundles it; set FEM > CalculiX > binary otherwise"))
    return issues


def ready(issues):
    return not any(kind == "error" for kind, _text in issues)


# ----- the solve -------------------------------------------------------------------------------


def _purge_results(analysis):
    try:
        from femresult import resulttools

        resulttools.purge_results(analysis)
    except Exception:
        pass


def prepare_solve(analysis, working_dir=None, progress=None):
    """Meshes when needed, checks, writes the CalculiX input; returns the FemToolsCcx to run."""
    from femtools import ccxtools

    issues = precheck(analysis)
    if not ready(issues):
        raise StudyError("; ".join(text for kind, text in issues if kind == "error"))
    m = members(analysis)
    if not is_meshed(m["mesh"]):
        mesh_study(analysis, progress)
    ensure_ccx_pref()
    _purge_results(analysis)
    fea = ccxtools.FemToolsCcx(analysis, m["solver"])
    fea.update_objects()
    fea.setup_working_dir(working_dir)
    message = fea.check_prerequisites()
    if message:
        raise StudyError(message.strip())
    if progress:
        progress("Writing the CalculiX input…")
    fea.write_inp_file()
    if not fea.inp_file_name:
        raise StudyError("the CalculiX input file was not written")
    return fea


class _no_post_pipeline:
    """The FRD import without the VTK post pipeline it adds beside each result.

    A study colours the result mesh itself (bruceworx_sim_gui); the pipeline is not needed, and
    creating a Fem::FemPostPipeline in this GUI build crashes in SoFCColorGradient::setMarkerLabel
    (found 27 Oct 2026, every pipeline, also FEM_PostPipelineFromResult). FEM's own commands still
    make one for whoever asks.
    """

    def __enter__(self):
        import feminout.importCcxFrdResults as frd

        self.module, self.saved = frd, frd.setupPipeline
        frd.setupPipeline = lambda *args, **kwargs: None
        return self

    def __exit__(self, *exc):
        self.module.setupPipeline = self.saved
        return False


def finish_solve(fea, progress=None):
    """Loads the results of a finished ccx run, adds the safety factor; returns the result object."""
    analysis = fea.analysis
    if progress:
        progress("Reading the results…")
    with _no_post_pipeline():
        fea.load_results()
    results = members(analysis)["results"]
    if not results:
        raise StudyError("CalculiX wrote no results (see the Report view)")
    result = results[-1]
    add_safety_factor(result, analysis)
    return result


def solve(analysis, working_dir=None, progress=None):
    """The whole run, blocking: mesh if needed, write, ccx, read, safety factor. Returns the result."""
    fea = prepare_solve(analysis, working_dir, progress)
    if progress:
        progress("Solving with CalculiX…")
    t0 = time.time()
    code = fea.ccx_run()
    if code:
        raise StudyError(f"CalculiX finished with exit code {code}: {(fea.ccx_stderr or '')[-400:]}")
    if progress:
        progress(f"CalculiX finished in {time.time() - t0:.1f} s")
    return finish_solve(fea, progress)


def study_material(analysis):
    """card_values for the study: the card it was set up with, else the FEM material's own numbers."""
    uuid = getattr(analysis, "MaterialUUID", "") if "MaterialUUID" in analysis.PropertiesList else ""
    values = card(uuid)
    if usable(values):
        return values
    for mat in members(analysis)["materials"]:
        data = mat.Material or {}
        values = {
            "uuid": uuid, "name": data.get("Name", mat.Label), "library": "",
            "E": _quantity_mpa(data.get("YoungsModulus")), "nu": _number(data.get("PoissonRatio")),
            "yield": _quantity_mpa(data.get("YieldStrength")),
            "uts": _quantity_mpa(data.get("UltimateTensileStrength")),
            "density": _density_kg_m3(data.get("Density")),
        }
        if usable(values):
            return values
    return None


def safety_factors(von_mises, allowable):
    """SF per node: allowable / sigma_vM, capped at SF_CAP."""
    out = []
    for stress in von_mises:
        if stress <= 0 or allowable / stress > SF_CAP:
            out.append(SF_CAP)
        else:
            out.append(allowable / stress)
    return out


def add_safety_factor(result, analysis=None):
    """Adds SafetyFactor (per node), AllowableStress and AllowableBasis to a result object.

    Returns min SF, or None when the material has no strength value.
    """
    analysis = analysis or study_of(result)
    values = study_material(analysis) if analysis is not None else None
    knockdown = getattr(analysis, "Knockdown", KNOCKDOWN) if analysis is not None and "Knockdown" in analysis.PropertiesList else KNOCKDOWN
    allowable, basis = allowable_stress(values, knockdown or KNOCKDOWN)
    if "SafetyFactor" not in result.PropertiesList:
        result.addProperty("App::PropertyFloatList", "SafetyFactor", "Result", "[BW] allowable stress / von Mises stress per node")
        result.addProperty("App::PropertyFloat", "AllowableStress", "Result", "[BW] The allowable stress (MPa) the safety factor divides")
        result.addProperty("App::PropertyString", "AllowableBasis", "Result", "[BW] Where the allowable stress comes from")
        result.addProperty("App::PropertyFloat", "MinSafetyFactor", "Result", "[BW] The lowest safety factor")
    result.AllowableBasis = basis
    if not allowable or not len(result.vonMises):
        result.SafetyFactor = []
        result.AllowableStress = 0.0
        result.MinSafetyFactor = 0.0
        return None
    factors = safety_factors(result.vonMises, allowable)
    result.SafetyFactor = factors
    result.AllowableStress = allowable
    result.MinSafetyFactor = min(factors)
    return result.MinSafetyFactor


def latest_result(analysis):
    results = members(analysis)["results"]
    return results[-1] if results else None


def result_extrema(result):
    """max von Mises (MPa, node), max displacement (mm, node), min SF (node)."""
    out = {"max_vm": 0.0, "max_vm_node": None, "max_disp": 0.0, "max_disp_node": None, "min_sf": None, "min_sf_node": None}
    nodes = list(result.NodeNumbers)
    vm = list(result.vonMises)
    if vm:
        i = max(range(len(vm)), key=vm.__getitem__)
        out["max_vm"], out["max_vm_node"] = vm[i], nodes[i] if i < len(nodes) else None
    disp = list(result.DisplacementLengths)
    if disp:
        i = max(range(len(disp)), key=disp.__getitem__)
        out["max_disp"], out["max_disp_node"] = disp[i], nodes[i] if i < len(nodes) else None
    sf = list(getattr(result, "SafetyFactor", []) or [])
    if sf:
        i = min(range(len(sf)), key=sf.__getitem__)
        out["min_sf"], out["min_sf_node"] = sf[i], nodes[i] if i < len(nodes) else None
    return out


def nearest_node(result, point):
    """(node number, distance) of the mesh node nearest to a point (FreeCAD.Vector or x, y, z)."""
    nodes = result.Mesh.FemMesh.Nodes
    px, py, pz = (point.x, point.y, point.z) if hasattr(point, "x") else point
    best, best_d = None, float("inf")
    for number, vec in nodes.items():
        d = (vec.x - px) ** 2 + (vec.y - py) ** 2 + (vec.z - pz) ** 2
        if d < best_d:
            best, best_d = number, d
    return best, math.sqrt(best_d) if best is not None else None


def probe(result, point):
    """The values at the node nearest a point: node, distance, von Mises, displacement, SF."""
    node, distance = nearest_node(result, point)
    if node is None:
        return None
    numbers = list(result.NodeNumbers)
    try:
        i = numbers.index(node)
    except ValueError:
        return None
    sf = list(getattr(result, "SafetyFactor", []) or [])
    return {
        "node": node, "distance": distance,
        "von_mises": result.vonMises[i] if i < len(result.vonMises) else None,
        "displacement": result.DisplacementLengths[i] if i < len(result.DisplacementLengths) else None,
        "safety_factor": sf[i] if i < len(sf) else None,
    }


# ----- the summary and the report ---------------------------------------------------------------


def _describe(obj):
    """One line for a load or constraint: label, kind, references, value."""
    refs = getattr(obj, "References", None) or []
    where = ", ".join(f"{o.Label}.{s}" for o, subs in refs for s in (subs if isinstance(subs, (list, tuple)) else [subs]))
    kind = obj.TypeId.split("::")[-1].replace("Constraint", "")
    value = ""
    if obj.TypeId == "Fem::ConstraintForce":
        value = f"{_value(obj, 'Force', 'N') or 0:g} N" + (" (reversed)" if getattr(obj, "Reversed", False) else "")
    elif obj.TypeId == "Fem::ConstraintPressure":
        value = f"{_value(obj, 'Pressure', 'MPa') or 0:g} MPa" + (" (reversed)" if getattr(obj, "Reversed", False) else "")
    elif obj.TypeId == "Fem::ConstraintDisplacement":
        locked = [a for a in "xyz" if not getattr(obj, f"{a}Free", True)]
        value = "locked " + ", ".join(locked) if locked else "nothing locked"
    return {"label": obj.Label, "kind": kind, "where": where, "value": value}


def summary(analysis):
    """Everything the panel and the report show, as plain values."""
    m = members(analysis)
    values = study_material(analysis) or {}
    knockdown = getattr(analysis, "Knockdown", KNOCKDOWN) if "Knockdown" in analysis.PropertiesList else KNOCKDOWN
    allowable, basis = allowable_stress(values, knockdown or KNOCKDOWN) if values else (None, "no material")
    model = getattr(analysis, "Model", None) if "Model" in analysis.PropertiesList else None
    if model is None and m["mesh"] is not None:
        model = getattr(m["mesh"], "Shape", None)
    out = {
        "name": analysis.Label.replace("Study: ", "", 1),
        "type": getattr(analysis, "StudyType", "static") if "StudyType" in analysis.PropertiesList else "static",
        "document": analysis.Document.Label,
        "model": model.Label if model is not None else "",
        "material": values,
        "allowable": allowable,
        "basis": basis,
        "knockdown": knockdown,
        "mesh": mesh_stats(m["mesh"]) if m["mesh"] is not None else {},
        "supports": [_describe(o) for o in m["supports"]],
        "loads": [_describe(o) for o in m["loads"]],
        "others": [_describe(o) for o in m["others"]],
        "issues": precheck(analysis),
        "ccx": find_ccx() or "",
        "ccx_version": "",
        "result": None,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    result = latest_result(analysis)
    if result is not None:
        out["result"] = result_extrema(result)
        out["result"]["name"] = result.Label
    return out


def report_folder(doc):
    """Where the report goes: beside the document; the temp folder for an unsaved one."""
    import tempfile

    path = getattr(doc, "FileName", "") or ""
    if path:
        return os.path.dirname(path)
    return tempfile.gettempdir()


def report_path(analysis, extension="html"):
    doc = analysis.Document
    stem = os.path.splitext(os.path.basename(doc.FileName))[0] if doc.FileName else doc.Name
    name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in summary_name(analysis)).strip().replace(" ", "_")
    return os.path.join(report_folder(doc), f"{stem}_{name}_report.{extension}")


def summary_name(analysis):
    return analysis.Label.replace("Study: ", "", 1)


def _fmt(value, digits=3, unit=""):
    if value is None:
        return "–"
    if isinstance(value, float):
        if value >= SF_CAP:
            return f"> {SF_CAP:g}"
        text = f"{value:.{digits}g}"
    else:
        text = str(value)
    return f"{text} {unit}".strip()


def report_html(analysis, images=None, path=None, data=None):
    """Writes the study report as one HTML file; images = {"stress": png path, "displacement": ..., "safety": ...}."""
    data = data or summary(analysis)
    data["ccx_version"] = data.get("ccx_version") or ccx_version(data.get("ccx") or None)
    path = path or report_path(analysis)
    esc = html.escape
    mat = data["material"] or {}
    res = data["result"]
    rows = []

    def row(label, value):
        rows.append(f"<tr><th>{esc(label)}</th><td>{esc(str(value))}</td></tr>")

    row("Study", data["name"])
    row("Type", dict((k, l) for k, l, _ok in STUDY_TYPES).get(data["type"], data["type"]))
    row("Document", data["document"])
    row("Model", data["model"] or "–")
    row("Date", data["date"])
    row("Solver", f"CalculiX ccx {data['ccx_version'] or '?'}")
    material_rows = []
    if mat:
        for label, key, unit in (("Card", "name", ""), ("Library", "library", ""), ("Young's modulus E", "E", "MPa"),
                                 ("Poisson's ratio ν", "nu", ""), ("Yield strength", "yield", "MPa"),
                                 ("Ultimate tensile strength", "uts", "MPa"), ("Density", "density", "kg/m³")):
            value = mat.get(key)
            if value not in (None, ""):
                material_rows.append(f"<tr><th>{esc(label)}</th><td>{esc(_fmt(value, 4, unit))}</td></tr>")
        knock = "" if data["knockdown"] == 1 else f", knockdown {data['knockdown']:g}"
        material_rows.append(f"<tr><th>Allowable stress</th><td>{esc(_fmt(data['allowable'], 4, 'MPa'))} "
                             f"({esc(data['basis'])}{esc(knock)})</td></tr>")
    else:
        material_rows.append("<tr><td colspan=2>No material</td></tr>")
    mesh = data["mesh"] or {}
    mesh_rows = [
        f"<tr><th>Nodes</th><td>{mesh.get('nodes', 0)}</td></tr>",
        f"<tr><th>Elements</th><td>{mesh.get('elements', 0)} ({esc(mesh.get('order') or '?')} order)</td></tr>",
        f"<tr><th>Max element size</th><td>{esc(_fmt(mesh.get('max_size'), 3, 'mm'))}, {esc(mesh.get('fineness', ''))}</td></tr>",
    ]

    def items(entries):
        if not entries:
            return "<li>none</li>"
        return "".join(f"<li><b>{esc(e['label'])}</b> ({esc(e['kind'])}) {esc(e['value'])}"
                       f"{' on ' + esc(e['where']) if e['where'] else ''}</li>" for e in entries)

    result_rows = []
    if res:
        result_rows.append(f"<tr><th>Max von Mises stress</th><td>{esc(_fmt(res['max_vm'], 4, 'MPa'))} (node {res['max_vm_node']})</td></tr>")
        result_rows.append(f"<tr><th>Max displacement</th><td>{esc(_fmt(res['max_disp'], 4, 'mm'))} (node {res['max_disp_node']})</td></tr>")
        sf = res["min_sf"]
        verdict = "" if sf is None else (" — <b style='color:#b00020'>below 1: the part yields</b>" if sf < 1 else " — above 1")
        result_rows.append(f"<tr><th>Min safety factor</th><td>{esc(_fmt(sf, 3))} (node {res['min_sf_node']}){verdict}</td></tr>")
    else:
        result_rows.append("<tr><td colspan=2>Not solved yet</td></tr>")
    figures = []
    for key, caption in (("stress", "von Mises stress"), ("displacement", "Displacement"), ("safety", "Safety factor")):
        image = (images or {}).get(key)
        if image and os.path.isfile(image):
            with open(image, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
            figures.append(f"<figure><img src='data:image/png;base64,{encoded}' alt='{esc(caption)}'><figcaption>{esc(caption)}</figcaption></figure>")
    issues = data.get("issues") or []
    issue_items = "".join(f"<li class='{kind}'>{esc(text)}</li>" for kind, text in issues) or "<li>none</li>"
    doc_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(data['name'])} — study report</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 32px; color: #1d1d1f; }}
h1 {{ font-size: 22px; margin-bottom: 2px; }} h2 {{ font-size: 15px; margin-top: 22px; border-bottom: 1px solid #ccc; }}
table {{ border-collapse: collapse; }} th {{ text-align: left; padding: 2px 14px 2px 0; font-weight: 600; white-space: nowrap; }}
td {{ padding: 2px 0; }} figure {{ display: inline-block; margin: 8px 8px 8px 0; }} img {{ max-width: 420px; border: 1px solid #ddd; }}
figcaption {{ font-size: 12px; color: #555; }} li.error {{ color: #b00020; }} li.warning {{ color: #8a6d00; }}
.small {{ font-size: 11px; color: #777; }}
</style></head><body>
<h1>{esc(data['name'])}</h1>
<div class="small">Keystone simulation report, {esc(data['date'])}</div>
<h2>Study</h2><table>{''.join(rows)}</table>
<h2>Material</h2><table>{''.join(material_rows)}</table>
<h2>Mesh</h2><table>{''.join(mesh_rows)}</table>
<h2>Supports</h2><ul>{items(data['supports'])}</ul>
<h2>Loads</h2><ul>{items(data['loads'])}</ul>
<h2>Results</h2><table>{''.join(result_rows)}</table>
{''.join(figures)}
<h2>Pre-check</h2><ul>{issue_items}</ul>
<p class="small">Static linear analysis: K u = f; von Mises from the nodal stress tensor; safety factor = allowable stress / von Mises stress per node.</p>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(doc_html)
    return path


# ----- a benchmark --------------------------------------------------------------------------------


def cantilever(doc=None, length=100.0, width=10.0, height=10.0, load=1000.0, E=210000.0, nu=0.3,
               yield_strength=355.0, name="Cantilever", divisor=MESH_DIVISOR):
    """A steel cantilever study: fixed at x=0, a tip load in -z on the far face. Returns (doc, study objects, theory).

    theory: tip deflection P L^3 / (3 E I) with I = w h^3 / 12, and the bending stress M c / I at the root.
    """
    import FreeCAD
    import ObjectsFem

    doc = doc or FreeCAD.newDocument("Benchmark")
    box = doc.addObject("Part::Box", "Beam")
    box.Length, box.Width, box.Height = length, width, height
    doc.recompute()
    values = {"uuid": "", "name": "Steel (benchmark)", "library": "", "E": E, "nu": nu, "yield": yield_strength,
              "uts": None, "density": 7850.0}
    plan = StudyPlan(name=name, model=box, material=values, max_size=box.Shape.BoundBox.DiagonalLength / divisor)
    objs = new_study(doc, plan)
    analysis = objs["analysis"]
    fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
    fixed.References = [(box, "Face1")]
    analysis.addObject(fixed)
    force = ObjectsFem.makeConstraintForce(doc, "TipLoad")
    force.References = [(box, "Face2")]
    force.Force = f"{load} N"
    force.Direction = (box, ["Face6"])
    force.Reversed = True
    analysis.addObject(force)
    doc.recompute()
    inertia = width * height ** 3 / 12.0
    theory = {"deflection": load * length ** 3 / (3 * E * inertia),
              "stress": load * length * (height / 2) / inertia}
    return doc, objs, theory
