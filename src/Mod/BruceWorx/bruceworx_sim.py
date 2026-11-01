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
  ``SafetyFactor[i] = allowable / sigma_eq[i]``, ``allowable = Knockdown * yield``
  (the FDM knockdown is SIM-03; ``Knockdown`` is 1 until then). A card without
  a yield strength uses the ultimate tensile strength, with a note.
* The equivalent stress ``sigma_eq`` is the study's ``StressMeasure`` (30 Oct,
  S3): von Mises (default), maximum principal (Rankine: the largest principal
  stress by magnitude, max(|s1|, |s3|)) or Tresca (s1 - s3). Changing it
  recomputes the safety factor from the stored fields; no new solve.
* The safety-factor colour bands are the study's ``SFThresholds`` and
  ``SFColors`` (30 Oct, S2): n thresholds make n + 1 bands; the defaults come
  from the preference ``Mod/BruceWorx/Sim`` (1 and 2: red, amber, green).
* ``find_ccx`` looks for the solver in the FEM preference, then beside the
  program (Vector Labs bundles it as ``Contents/bin/ccx``), then the pixi
  environment of a development tree, then PATH. ``ensure_ccx_pref`` writes the
  first find into the preference once, so a fresh profile solves without a
  visit to the FEM settings.
* ``write_report`` writes the study report as LaTeX (report.tex and the
  renders in a folder beside the document) and ``compile_report`` makes the
  PDF with Tectonic (bundled in Vector Labs), else latexmk or pdflatex (MacTeX);
  without any, the .tex folder stays for the user (30 Oct, S4).

Theory (plan chapter 60): static K u = f; sigma_vM from the stress tensor
(CalculiX reports the nodal tensor, FreeCAD computes von Mises and the
principal stresses s1 >= s2 >= s3); SF = sigma_allow / sigma_eq per node,
min SF = sigma_allow / max sigma_eq.
"""

from __future__ import annotations

import datetime
import math
import os
import shutil
import subprocess
import time

CCX_PREFS = "User parameter:BaseApp/Preferences/Mod/Fem/Ccx"
CCX_PREF_NAME = "ccxBinaryPath"
MESH_DIVISOR = 30.0  # max element size = bounding-box diagonal / 30
FINENESS = ("VeryCoarse", "Coarse", "Moderate", "Fine", "VeryFine")
DEFAULT_FINENESS = "Moderate"
SF_CAP = 100.0  # a node with (almost) no stress reports this safety factor
KNOCKDOWN = 1.0  # SIM-03: allowable = Knockdown * yield; 1 until the FDM model lands
SIM_PREFS = "User parameter:BaseApp/Preferences/Mod/BruceWorx/Sim"

# The equivalent stress the safety factor divides (S3, 30 Oct): (key, label, formula for the report).
STRESS_MEASURES = (
    ("vonMises", "von Mises", r"\sigma_{eq}=\sigma_{vM}=\sqrt{\frac{1}{2}\left[(\sigma_1-\sigma_2)^2+(\sigma_2-\sigma_3)^2+(\sigma_3-\sigma_1)^2\right]}"),
    ("MaxPrincipal", "Maximum principal (Rankine)", r"\sigma_{eq}=\max(|\sigma_1|,|\sigma_3|)\quad\mbox{(the largest principal stress by magnitude)}"),
    ("Tresca", "Tresca", r"\sigma_{eq}=\sigma_1-\sigma_3\quad\mbox{(twice the maximum shear stress)}"),
)
DEFAULT_MEASURE = "vonMises"
# Safety-factor bands (S2, 30 Oct): n ascending thresholds make n + 1 bands, one colour each.
DEFAULT_SF_THRESHOLDS = (1.0, 2.0)
DEFAULT_SF_COLORS = ("#D91C1C", "#F2A007", "#3FA34D")

# (key, label, available). The frame carries every type; all five run (modal and buckling:
# bruceworx_sim_eigen, thermal and thermal stress: bruceworx_sim_thermal, 30 Oct).
STUDY_TYPES = (
    ("static", "Static stress", True),
    ("modal", "Modal frequencies", True),
    ("buckling", "Structural buckling", True),
    ("thermal", "Thermal", True),
    ("thermal_stress", "Thermal stress", True),
    ("external_flow", "External flow", True),  # the Airflow group (SIM-06, bruceworx_cfd)
    ("prop_wash", "Prop wash", True),  # actuator disks on the motors (SIM-06 phase 2, bruceworx_cfd_wash)
)
AIRFLOW_KINDS = ("external_flow", "prop_wash")  # studies run by OpenFOAM, with no CalculiX members

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
    found.append(os.path.join(home, "bin", "ccx"))  # Vector Labs: Contents/bin/ccx (make-vectorlabs.sh)
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


def _number_in(value, unit):
    """A Quantity (or a string) in ``unit``, or None."""
    try:
        if isinstance(value, str):
            import FreeCAD

            value = FreeCAD.Units.Quantity(value)
        number = float(value.getValueAs(unit))
        return None if number != number else number
    except Exception:
        return None


def _card_hdt(uuid):
    """The heat deflection temperature (deg C) a Vector Labs card records in its BruceWorx block, or None."""
    try:
        import bruceworx_materials

        return bruceworx_materials.hdt_of(uuid)
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
        "k": _number_in(value("ThermalConductivity"), "W/m/K"),
        "cp": _number_in(value("SpecificHeat"), "J/kg/K"),
        "alpha": _number_in(value("ThermalExpansionCoefficient"), "1/K"),
        "hdt": _card_hdt(getattr(material, "UUID", "")),
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


LIBRARY = "Vector Labs"  # the material library with yield strengths (S1, 30 Oct; the materials agent's cards)
STOCK_LIBRARY = "System"  # FreeCAD's own cards, the FEM ones among them


def library_rank(library):
    """The order of the libraries in the material picker: Vector Labs, the vault's, the stock cards, the rest."""
    if library == LIBRARY:
        return 0
    if "vault" in (library or "").lower():
        return 1
    if library == STOCK_LIBRARY:
        return 2
    return 3


def material_cards(manager=None):
    """Every card with E and nu, as card_values dicts: the Vector Labs library first, then the vault's,
    then the stock (FEM) cards, then the others; by name within a library."""
    manager = manager or material_manager()
    cards = []
    for _uuid, material in manager.Materials.items():
        values = card_values(material)
        if usable(values):
            cards.append(values)
    cards.sort(key=lambda v: (library_rank(v["library"]), v["library"], v["name"].lower()))
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
    # the thermal numbers CalculiX needs for the thermal studies (SIM-01, 30 Oct)
    if values.get("k"):
        data["ThermalConductivity"] = f"{values['k']:.6g} W/m/K"
    if values.get("cp"):
        data["SpecificHeat"] = f"{values['cp']:.6g} J/kg/K"
    if values.get("alpha"):
        data["ThermalExpansionCoefficient"] = f"{values['alpha'] * 1e6:.6g} um/m/K"
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
    # top-level objects: no parent but FEM objects (a study's mesh takes its model as a child), visible
    # ones first (an earlier study's results hide the model)
    tops = [obj for obj in getattr(doc, "Objects", []) or []
            if not [p for p in getattr(obj, "InList", []) or [] if not p.TypeId.startswith("Fem::")]]
    for obj in tops:
        vobj = getattr(obj, "ViewObject", None)
        if vobj is None or vobj.Visibility:
            add(obj)
    if not found:
        for obj in tops:
            add(obj)
    return found


def mesh_divisor():
    """The default element size is the bounding-box diagonal over this: the preference MeshDivisor, else 30."""
    try:
        value = _prefs().GetFloat("MeshDivisor", 0.0)
    except Exception:
        value = 0.0
    return value if value > 0 else MESH_DIVISOR


def default_mesh_size(obj):
    """Bounding-box diagonal / 30 (the preference MeshDivisor), in mm; 1 mm when the shape is empty."""
    shape = shape_of(obj)
    if shape is None:
        return 1.0
    return max(0.01, shape.BoundBox.DiagonalLength / mesh_divisor())


# ----- the mesh-size estimate (30 Oct bug round: Netgen hangs or makes 340k nodes) -----------------------
#
# Netgen runs in-process and cannot be interrupted, so the guard is an estimate made before meshing.
# Fitted to Netgen Moderate on a cube, a cylinder and a plate (h = MaxSize):
#   tets  ~ max(0.8 * V / (h^3 / (6 sqrt 2)),  5 * f * A / h^2)      volume term, thin/curved surface term
#   nodes ~ 0.3 * tets (first order), 1.85 * tets (second order: edge nodes)
# f grows with the fineness (Netgen refines curved surfaces; flat faces hardly change).

MESH_WARN_NODES = 200000
MESH_MAX_NODES = 1000000
_SURFACE_FINENESS = {"VeryCoarse": 1.5, "Coarse": 1.5, "Moderate": 1.0, "Fine": 2.3, "VeryFine": 7.0}


def mesh_warn_nodes():
    """The node count above which the pre-check warns (preference MeshWarnNodes, default 200 000)."""
    try:
        value = _prefs().GetInt("MeshWarnNodes", 0)
    except Exception:
        value = 0
    return value if value > 0 else MESH_WARN_NODES


def mesh_max_nodes(analysis=None):
    """The node count above which the pre-check blocks meshing: the study's MeshNodeLimit when set,
    else the preference MeshMaxNodes, default 1 000 000."""
    if analysis is not None and "MeshNodeLimit" in analysis.PropertiesList and int(analysis.MeshNodeLimit) > 0:
        return int(analysis.MeshNodeLimit)
    try:
        value = _prefs().GetInt("MeshMaxNodes", 0)
    except Exception:
        value = 0
    return value if value > 0 else MESH_MAX_NODES


def estimate_mesh_nodes(volume, area, max_size, second_order=True, fineness=DEFAULT_FINENESS):
    """Estimated Netgen node count for a shape of this volume (mm^3) and surface area (mm^2)."""
    h = max(float(max_size), 1e-6)
    by_volume = 0.8 * volume / (h ** 3 / (6.0 * math.sqrt(2.0)))
    by_surface = 5.0 * _SURFACE_FINENESS.get(fineness, 1.0) * area / (h * h)
    tets = max(by_volume, by_surface)
    return int(round(tets * (1.85 if second_order else 0.3)))


def mesh_size_for_nodes(volume, area, target, second_order=True, fineness=DEFAULT_FINENESS):
    """The MaxSize at which the estimate reaches ``target`` nodes (bisection on the estimate)."""
    lo, hi = 1e-3, 1e6
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if estimate_mesh_nodes(volume, area, mid, second_order, fineness) > target:
            lo = mid
        else:
            hi = mid
    return hi


def _round_size(value):
    """Two significant figures, rounded up so the suggestion stays under the target."""
    if value <= 0:
        return value
    scale = 10 ** (math.floor(math.log10(value)) - 1)
    return math.ceil(value / scale) * scale


def mesh_estimate_issues(analysis, mesh):
    """("warning"|"error", text) for the node count the mesh settings are about to make; [] when fine."""
    shape_obj = getattr(mesh, "Shape", None)
    shape = shape_of(shape_obj) if shape_obj is not None else None
    if shape is None or not shape.Solids:
        return []
    volume = sum(solid.Volume for solid in shape.Solids)
    area = sum(solid.Area for solid in shape.Solids)
    size = float(mesh.MaxSize)
    second = bool(mesh.SecondOrder)
    fineness = str(mesh.Fineness)
    nodes = estimate_mesh_nodes(volume, area, size, second, fineness)
    limit, warn = mesh_max_nodes(analysis), min(mesh_warn_nodes(), mesh_max_nodes(analysis))
    if nodes <= warn:
        return []

    def suggest(target):
        return _round_size(mesh_size_for_nodes(volume, area, target, second, fineness))

    def num(value):
        return f"{value:,}".replace(",", " ")

    if nodes > limit:
        return [("error", f"Mesh too large: MaxSize {size:g} mm makes about {num(nodes)} nodes, over the limit of "
                          f"{num(limit)}, and Netgen cannot be stopped once started. Use a MaxSize of "
                          f"{suggest(warn):g} mm or more, simplify the geometry, or raise the study's MeshNodeLimit")]
    return [("warning", f"Large mesh: MaxSize {size:g} mm makes about {num(nodes)} nodes, over {num(warn)}. "
                        f"A MaxSize of {suggest(warn):g} mm or more would stay under")]


def mesh_shape_object(doc, model, fuse=False):
    """The object the Netgen mesh takes its shape from: the model, or a compound of a container's bodies.

    fuse (the thermal studies): the bodies are fused, so touching bodies share one conforming mesh and
    heat flows across their contact as if bonded (a contact conductance comes with SIM-02)."""
    if "Shape" in getattr(model, "PropertiesList", ()):
        return model
    bodies = [b for b in _bodies(model) if is_model(b)]
    if len(bodies) == 1:
        return bodies[0]
    if fuse:
        compound = doc.addObject("Part::MultiFuse", f"{model.Name}_Fused")
        compound.Label = f"{model.Label} (bodies, bonded)"
        compound.Shapes = bodies
        doc.recompute()
        return compound
    compound = doc.addObject("Part::Compound", f"{model.Name}_Compound")
    compound.Label = f"{model.Label} (bodies)"
    compound.Links = bodies
    doc.recompute()
    return compound


# ----- the study -------------------------------------------------------------------------------


class StudyPlan:
    """What New Study asks for."""

    def __init__(self, name="Study 1", study_type="static", model=None, material_uuid="", material=None,
                 max_size=None, fineness=DEFAULT_FINENESS, second_order=True, knockdown=KNOCKDOWN,
                 stress_measure=None, sf_thresholds=None, sf_colors=None, options=None):
        self.name = name
        self.study_type = study_type
        self.model = model
        self.material_uuid = material_uuid
        self.material = material  # card_values; looked up from the UUID when None
        self.max_size = max_size
        self.fineness = fineness
        self.second_order = second_order
        self.knockdown = knockdown
        self.stress_measure = stress_measure  # None: the preference (von Mises by default)
        self.sf_thresholds = sf_thresholds  # None: the preference's bands
        self.sf_colors = sf_colors
        # a modal or buckling study's settings by property name (bruceworx_sim_eigen): ModeCount,
        # FrequencyMin/Max, Prestressed, ExcitationCheck, ..., TargetLoadFactor, Nonlinear, ...
        self.options = dict(options or {})


# ----- the stress measure and the safety-factor bands (S2, S3: 30 Oct) ---------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(SIM_PREFS)


def measure_keys():
    return [key for key, _label, _formula in STRESS_MEASURES]


def measure_label(key):
    return dict((k, label) for k, label, _f in STRESS_MEASURES).get(key, key)


def measure_formula(key):
    return dict((k, formula) for k, _l, formula in STRESS_MEASURES).get(key, "")


def default_measure():
    key = _prefs().GetString("StressMeasure", DEFAULT_MEASURE)
    return key if key in measure_keys() else DEFAULT_MEASURE


def set_default_measure(key):
    if key not in measure_keys():
        raise StudyError(f"unknown stress measure {key!r}")
    _prefs().SetString("StressMeasure", key)


def equivalent_stress(measure, vm, s1, s3):
    """One node's equivalent stress (MPa) for a measure; s1 >= s3 are the extreme principal stresses."""
    if measure == "MaxPrincipal":
        return max(abs(s1), abs(s3))
    if measure == "Tresca":
        return s1 - s3
    return vm


def principal_stresses(sxx, syy, szz, sxy, sxz, syz):
    """(s1, s2, s3), s1 >= s2 >= s3, of a symmetric stress tensor (the eigenvalues, as FEM's resulttools)."""
    import numpy

    tensor = numpy.array([[sxx, sxy, sxz], [sxy, syy, syz], [sxz, syz, szz]], dtype=float)
    values = sorted((float(v) for v in numpy.linalg.eigvalsh(tensor)), reverse=True)
    return values[0], values[1], values[2]


def von_mises(s1, s2, s3):
    return math.sqrt(((s1 - s2) ** 2 + (s2 - s3) ** 2 + (s3 - s1) ** 2) / 2.0)


def equivalent_field(result, measure):
    """(values, measure used): the result's equivalent stress per node.

    Von Mises is the result's own field; Rankine and Tresca use its principal stresses
    (PrincipalMax = s1, PrincipalMin = s3). A result without them falls back to von Mises.
    """
    vm = list(result.vonMises)
    if measure in ("MaxPrincipal", "Tresca"):
        p1 = list(getattr(result, "PrincipalMax", []) or [])
        p3 = list(getattr(result, "PrincipalMin", []) or [])
        if vm and len(p1) == len(vm) and len(p3) == len(vm):
            return [equivalent_stress(measure, v, a, c) for v, a, c in zip(vm, p1, p3)], measure
    return vm, "vonMises"


def study_measure(analysis):
    """The study's stress measure, or the preference's for a study made before 30 Oct."""
    if analysis is not None and "StressMeasure" in analysis.PropertiesList:
        key = str(analysis.StressMeasure)
        if key in measure_keys():
            return key
    return default_measure()


def set_study_measure(analysis, key):
    """Sets the study's stress measure and recomputes the safety factor of its latest result; min SF."""
    if key not in measure_keys():
        raise StudyError(f"unknown stress measure {key!r}")
    ensure_study_properties(analysis)
    analysis.StressMeasure = key
    result = latest_result(analysis)
    return add_safety_factor(result, analysis) if result is not None else None


def _hex(color):
    """'#RRGGBB' from '#rrggbb', 'rrggbb' or an (r, g, b) of 0..1 floats or 0..255 integers."""
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
            raise StudyError(f"not a colour: {color!r}")
        return "#" + text.upper()
    values = list(color)[:3]
    if all(isinstance(v, float) and v <= 1.0 for v in values):
        values = [v * 255.0 for v in values]
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02X}" for v in values)


def rgb(color):
    """(r, g, b) in 0..1 of a '#RRGGBB' colour."""
    text = _hex(color).lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def check_bands(thresholds, colors):
    """Validated (thresholds, colours): ascending positive thresholds, one more colour than thresholds."""
    thresholds = [float(t) for t in thresholds]
    colors = [_hex(c) for c in colors]
    if not thresholds:
        raise StudyError("the bands need at least one threshold")
    if any(t <= 0 for t in thresholds):
        raise StudyError("the safety-factor thresholds must be above 0")
    if any(b <= a for a, b in zip(thresholds, thresholds[1:])):
        raise StudyError("the safety-factor thresholds must rise from band to band")
    if len(colors) != len(thresholds) + 1:
        raise StudyError(f"{len(thresholds)} thresholds make {len(thresholds) + 1} bands: one colour each")
    return thresholds, colors


def default_bands():
    """(thresholds, colours) from the preference Mod/BruceWorx/Sim, else 1 and 2: red, amber, green."""
    prefs = _prefs()
    try:
        thresholds = [float(t) for t in prefs.GetString("SFThresholds", "").split(";") if t.strip()]
        colors = [c for c in prefs.GetString("SFColors", "").split(";") if c.strip()]
        if thresholds or colors:
            return check_bands(thresholds, colors)
    except (StudyError, ValueError):
        pass
    return list(DEFAULT_SF_THRESHOLDS), list(DEFAULT_SF_COLORS)


def save_default_bands(thresholds, colors):
    """Writes the bands into the preference: new studies start with them."""
    thresholds, colors = check_bands(thresholds, colors)
    prefs = _prefs()
    prefs.SetString("SFThresholds", ";".join(f"{t:g}" for t in thresholds))
    prefs.SetString("SFColors", ";".join(colors))
    return thresholds, colors


def reset_default_bands():
    prefs = _prefs()
    prefs.RemString("SFThresholds")
    prefs.RemString("SFColors")
    return default_bands()


def study_bands(analysis):
    """The study's (thresholds, colours); the preference's when the study has none (or broken ones)."""
    if analysis is not None and "SFThresholds" in analysis.PropertiesList:
        try:
            return check_bands(list(analysis.SFThresholds), list(analysis.SFColors))
        except StudyError:
            pass
    return default_bands()


def set_study_bands(analysis, thresholds, colors):
    thresholds, colors = check_bands(thresholds, colors)
    ensure_study_properties(analysis)
    analysis.SFThresholds = thresholds
    analysis.SFColors = colors
    return thresholds, colors


def band_index(sf, thresholds):
    """The band a safety factor falls in: 0 below the first threshold, len(thresholds) above the last."""
    for i, upper in enumerate(thresholds):
        if sf < upper:
            return i
    return len(thresholds)


def band_labels(thresholds):
    """'below 1', '1 to 2', ..., 'above 2' for the legend and the report."""
    labels = [f"below {thresholds[0]:g}"]
    labels += [f"{a:g} to {b:g}" for a, b in zip(thresholds, thresholds[1:])]
    labels.append(f"above {thresholds[-1]:g}")
    return labels


def band_counts(factors, thresholds):
    """How many nodes fall in each band."""
    counts = [0] * (len(thresholds) + 1)
    for sf in factors:
        counts[band_index(sf, thresholds)] += 1
    return counts


def ensure_study_properties(analysis):
    """Adds the 30 Oct properties (StressMeasure, SFThresholds, SFColors) to a study that lacks them."""
    props = analysis.PropertiesList
    if "StressMeasure" not in props:
        analysis.addProperty("App::PropertyEnumeration", "StressMeasure", "Study",
                             "[BW] The equivalent stress the safety factor divides: vonMises, MaxPrincipal "
                             "(Rankine, max |principal|) or Tresca (s1 - s3)")
        analysis.StressMeasure = measure_keys()
        analysis.StressMeasure = default_measure()
    if "MeshNodeLimit" not in props:
        analysis.addProperty("App::PropertyInteger", "MeshNodeLimit", "Study",
                             "[BW] Hard limit on the estimated mesh nodes for this study; 0 = the preference (1 000 000)")
    if "Mesher" not in props:
        analysis.addProperty("App::PropertyEnumeration", "Mesher", "Study",
                             "[BW] The volume mesher tried first: Netgen, or Gmsh (the other is the next try when it fails)")
        analysis.Mesher = ["Netgen", "Gmsh"]
        analysis.Mesher = "Netgen"
    if "MeshTimeLimit" not in props:
        analysis.addProperty("App::PropertyFloat", "MeshTimeLimit", "Study",
                             "[BW] Seconds one Netgen try may take in its worker process; 0 = automatic "
                             "(max(120, 0.0007 x estimated nodes))")
    if "SFThresholds" not in props:
        thresholds, colors = default_bands()
        analysis.addProperty("App::PropertyFloatList", "SFThresholds", "Study",
                             "[BW] Safety-factor band edges, ascending: n thresholds make n + 1 colour bands")
        analysis.addProperty("App::PropertyStringList", "SFColors", "Study",
                             "[BW] One colour (#RRGGBB) per safety-factor band, lowest band first")
        analysis.SFThresholds = thresholds
        analysis.SFColors = colors


def _add_study_properties(analysis, plan):
    if "StudyType" not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyString", "StudyType", "Study", "[BW] static, modal, buckling, thermal, thermal_stress")
        analysis.addProperty("App::PropertyLink", "Model", "Study", "[BW] The body or part the study analyses")
        analysis.addProperty("App::PropertyString", "MaterialUUID", "Study", "[BW] The material card the study was set up with")
        analysis.addProperty("App::PropertyFloat", "Knockdown", "Study", "[BW] allowable = Knockdown * yield (SIM-03 FDM knockdown; 1 = none)")
    ensure_study_properties(analysis)
    analysis.StudyType = plan.study_type
    analysis.Model = plan.model
    analysis.MaterialUUID = plan.material_uuid or ""
    analysis.Knockdown = plan.knockdown
    if plan.stress_measure:
        if plan.stress_measure not in measure_keys():
            raise StudyError(f"unknown stress measure {plan.stress_measure!r}")
        analysis.StressMeasure = plan.stress_measure
    if plan.sf_thresholds is not None or plan.sf_colors is not None:
        thresholds, colors = study_bands(analysis)
        set_study_bands(analysis, plan.sf_thresholds or thresholds, plan.sf_colors or colors)
    if plan.study_type in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        try:
            eigen.apply_options(analysis, plan.options)
        except eigen.EigenError as exc:
            raise StudyError(str(exc)) from exc
    elif plan.study_type in ("thermal", "thermal_stress"):
        import bruceworx_sim_thermal as thermal

        try:
            thermal.apply_options(analysis, plan.options)
        except thermal.ThermalError as exc:
            raise StudyError(str(exc)) from exc
    elif plan.options:
        raise StudyError(f"a {plan.study_type} study takes no options")


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
        raise StudyError(f"{plan.study_type} studies are planned, not built yet")
    if plan.model is None or not is_model(plan.model):
        raise StudyError("the study needs a body or part with solids")
    if plan.study_type in AIRFLOW_KINDS:  # airflow studies have no CalculiX members (bruceworx_cfd)
        import bruceworx_cfd as cfd

        try:
            analysis = cfd.new_airflow_study(doc, plan.model, plan.name, plan.options, kind=plan.study_type)
        except cfd.CfdError as exc:
            raise StudyError(str(exc)) from exc
        return {"analysis": analysis, "solver": None, "material": None, "mesh": None, "shape": None}
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
    if plan.study_type in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        eigen.configure_solver(analysis, solver)
    thermal_study = plan.study_type in ("thermal", "thermal_stress")
    if thermal_study:
        import bruceworx_sim_thermal as thermal

        thermal.make_initial_temperature(doc, analysis)
        thermal.configure_solver(analysis, solver)

    material = ObjectsFem.makeMaterialSolid(doc, "Material")
    if usable(values):
        material.Material = fem_material_dict(values, material.Material)
        material.Label = f"Material: {values['name']}"
    else:
        material.Label = "Material (none chosen)"
    analysis.addObject(material)

    shape = mesh_shape_object(doc, plan.model, fuse=thermal_study)
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


def is_self_weight(obj):
    """FEM's Self weight is a Python constraint (TypeId Fem::ConstraintPython), with no faces: it loads the whole model."""
    return obj.TypeId == "Fem::ConstraintSelfWeight" or getattr(getattr(obj, "Proxy", None), "Type", "") == "Fem::ConstraintSelfWeight"


def is_spin_load(obj):
    """FEM's Centrifugal load is a Python constraint too (SIM-04: bruceworx_sim_post adds it)."""
    return obj.TypeId == "Fem::ConstraintCentrif" or getattr(getattr(obj, "Proxy", None), "Type", "") == "Fem::ConstraintCentrif"


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
        elif tid in LOAD_TYPES or is_self_weight(obj) or is_spin_load(obj):
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


TET10_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))  # mid-edge nodes 5..10 of a Tet10: 1-2, 2-3, 3-1, 1-4, 2-4, 3-4


def used_node_ids(fem):
    """The ids of the nodes some volume element uses (else some face or edge element, for a surface mesh)."""
    used = set()
    kinds = (fem.Volumes,) if fem.VolumeCount else (fem.Faces, fem.Edges)
    for ids in kinds:
        for eid in ids:
            used.update(fem.getElementNodes(eid))
    return used


def face_connected_volumes(fem):
    """(keep, dropped_elements, clusters): the volume element ids of the largest body whose tetrahedra share faces (union-find
    over each face's sorted corner-node triplet), the number of elements outside it and the number of such clusters.  Elements
    that touch the body only at a node or an edge are a mechanism for the solver.  Only tetrahedra are examined; a mesh with
    other volume types is returned whole."""
    ids = list(fem.Volumes)
    corners = {}
    for vid in ids:
        nodes = fem.getElementNodes(vid)
        if len(nodes) not in (4, 10):
            return set(ids), 0, 0
        corners[vid] = nodes[:4]
    parent = {vid: vid for vid in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen = {}
    for vid in ids:
        n = corners[vid]
        for tri in ((n[0], n[1], n[2]), (n[0], n[1], n[3]), (n[0], n[2], n[3]), (n[1], n[2], n[3])):
            key = tuple(sorted(tri))
            other = seen.setdefault(key, vid)
            if other != vid:
                ra, rb = find(vid), find(other)
                if ra != rb:
                    parent[ra] = rb
    groups = {}
    for vid in ids:
        groups.setdefault(find(vid), []).append(vid)
    if len(groups) <= 1:
        return set(ids), 0, 0
    biggest = max(groups.values(), key=len)
    return set(biggest), len(ids) - len(biggest), len(groups) - 1


def rebuild_mesh(fem, drop_unused=True, upgrade=False, volumes=None):
    """A new FemMesh with the same elements; ``drop_unused`` leaves out the nodes no element uses (a failed second-order
    step of Netgen leaves ~100k of them: a support picked by nearest node can land on one and the wheel is then free),
    ``upgrade`` turns linear tetrahedra into Tet10 with straight mid-edge nodes (FEM / Abaqus C3D10 order: edges 1-2, 2-3,
    3-1, 1-4, 2-4, 3-4).  Node and element ids are kept; the new mid-edge nodes number on from the largest id.
    ``volumes``: the volume element ids to keep (the others, and the faces and edges that need their nodes, are left out)."""
    import Fem

    keep = None
    if volumes is not None:
        keep = set()
        for vid in volumes:
            keep.update(fem.getElementNodes(vid))
    elif drop_unused:
        keep = used_node_ids(fem)
    new = Fem.FemMesh()
    nodes = fem.Nodes
    for nid, v in nodes.items():
        if keep is None or nid in keep:
            new.addNode(v.x, v.y, v.z, nid)
    mids = {}
    top = [max(nodes) if nodes else 0]

    def mid(a, b):
        key = (a, b) if a < b else (b, a)
        if key not in mids:
            top[0] += 1
            va, vb = nodes[a], nodes[b]
            new.addNode(0.5 * (va.x + vb.x), 0.5 * (va.y + vb.y), 0.5 * (va.z + vb.z), top[0])
            mids[key] = top[0]
        return mids[key]

    def quad(ids, pairs):
        return list(ids) + [mid(ids[i], ids[j]) for i, j in pairs]

    for vid in fem.Volumes:
        if volumes is not None and vid not in volumes:
            continue
        ids = list(fem.getElementNodes(vid))
        new.addVolume(quad(ids, TET10_EDGES) if upgrade and len(ids) == 4 else ids, vid)
    for fid in fem.Faces:
        ids = list(fem.getElementNodes(fid))
        if keep is not None and not all(n in keep for n in ids):
            continue
        new.addFace(quad(ids, ((0, 1), (1, 2), (2, 0))) if upgrade and len(ids) == 3 else ids, fid)
    for eid in fem.Edges:
        ids = list(fem.getElementNodes(eid))
        if keep is not None and not all(n in keep for n in ids):
            continue
        new.addEdge(quad(ids, ((0, 1),)) if upgrade and len(ids) == 2 else ids, eid)
    return new


def record_stage(analysis, stage):
    """Stores the Model Builder fingerprint of a finished stage (bruceworx_sim_model.record); never raises."""
    try:
        import bruceworx_sim_model as model

        model.record(analysis, stage)
    except Exception as exc:
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"SIM-07: could not record the {stage} fingerprint: {exc}\n")


def _has_volumes(fem):
    return fem is not None and bool(fem.NodeCount) and bool(fem.VolumeCount)


def _mesh_in_place(mesh):
    """Recomputes the study's Netgen object on the shape it links; returns its FemMesh (maybe empty or surface-only)."""
    mesh.touch()
    mesh.Document.recompute([mesh])
    return mesh.FemMesh


NETGEN_COPY_PROPS = ("MaxSize", "MinSize", "Fineness", "GrowthRate", "NbSegsPerEdge", "NbSegsPerRadius", "Optimize",
                     "AllowQuad", "SecondOrder", "UserSizeMap", "SurfaceElementSize", "MeshSizeFile")


def repaired_copy(shape):
    """A rebuilt copy of ``shape`` (the original is not touched): written to a temporary BREP file and read back.  The copy is
    the same geometry (distance 0, volume equal to 1e-6) but Netgen meshes it where it does not mesh the solid read from
    a saved .FCStd (the impeller case of 1 Nov: surface only, or minutes without an answer).  ShapeFix is not used: on that
    solid it made Netgen slower, not better.  Returns None when nothing usable comes back."""
    import os
    import tempfile

    import Part

    fixed = shape.copy()
    tmpdir = tempfile.mkdtemp(prefix="bw-meshfix-")
    path = os.path.join(tmpdir, "shape.brep")
    try:
        fixed.exportBrep(path)
        back = Part.Shape()
        back.read(path)
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass
    return back if back.Solids and not back.isNull() else None


def _mesh_on_copy(mesh, shape, second_order):
    """Meshes ``shape`` (a repaired copy) in a scratch document with the study's Netgen settings; returns the FemMesh.
    The user's document is not touched: the scratch document is closed again."""
    import FreeCAD
    import ObjectsFem

    scratch = FreeCAD.newDocument("BW_MeshRetry", "BW_MeshRetry", True)
    try:
        feature = scratch.addObject("Part::Feature", "S")
        feature.Shape = shape
        scratch.recompute()
        netgen = ObjectsFem.makeMeshNetgenLegacy(scratch, "M")
        netgen.Shape = feature
        for name in NETGEN_COPY_PROPS:
            if name in netgen.PropertiesList and hasattr(mesh, name):
                try:
                    setattr(netgen, name, getattr(mesh, name))
                except Exception:
                    pass
        netgen.SecondOrder = bool(second_order)
        netgen.touch()
        scratch.recompute([netgen])
        return rebuild_mesh(netgen.FemMesh, drop_unused=False)  # detached from the scratch document
    finally:
        FreeCAD.closeDocument(scratch.Name)


def _shape_volume_area(shape):
    return sum(s.Volume for s in shape.Solids), sum(s.Area for s in shape.Solids)


def _mesher_of(analysis):
    try:
        return str(analysis.Mesher) if analysis is not None and "Mesher" in analysis.PropertiesList else "Netgen"
    except Exception:
        return "Netgen"


def _use_worker(mesh, analysis):
    """True when Netgen runs in a worker process: the study sets a MeshTimeLimit, or the estimate is over
    ``WORKER_MIN_NODES``; a small model meshes in this process (a worker costs about a second)."""
    import bruceworx_sim_meshworker as mw

    try:
        if analysis is not None and "MeshTimeLimit" in analysis.PropertiesList and float(analysis.MeshTimeLimit) > 0:
            return True
        if _mesher_of(analysis) == "Gmsh":
            return True
        shape = shape_of(getattr(mesh, "Shape", None))
        if shape is None or not shape.Solids:
            return False
        return _estimate_for(mesh, shape) >= mw.WORKER_MIN_NODES
    except Exception:
        return False


def _estimate_for(mesh, shape):
    volume, area = _shape_volume_area(shape)
    return estimate_mesh_nodes(volume, area, float(mesh.MaxSize), bool(mesh.SecondOrder), str(mesh.Fineness))


def _mesh_in_workers(mesh, analysis, say, progress=None):
    """Netgen in worker processes with time limits (bruceworx_sim_meshworker).  Tries, each with its own limit and all inside
    a total: (1) the shape (as a BREP, which is also the "repaired copy" that the in-process fallback makes) at the asked
    order; (2) first order, when second order was asked (the caller upgrades to Tet10); (3) Gmsh, when a gmsh binary is
    found.  Returns (FemMesh, notes of the failed tries); raises StudyError listing every try when all fail."""
    import bruceworx_sim_meshworker as mw

    shape = shape_of(getattr(mesh, "Shape", None))
    second = bool(getattr(mesh, "SecondOrder", False))
    limit = mw.time_limit(analysis, _estimate_for(mesh, shape))
    total_end = time.time() + mw.TOTAL_FACTOR * limit
    base = mw.netgen_settings(mesh, NETGEN_COPY_PROPS, second)
    order = ", second order" if second else ""
    netgen = [("Netgen" + order, "netgen", second)] + ([("Netgen, first order (Tet10 after)", "netgen", False)] if second else [])
    gmsh = [("Gmsh" + order, "gmsh", second)] if mw.gmsh_binary() else []
    if not gmsh and _mesher_of(analysis) == "Gmsh":
        say("warning", "The study asks for Gmsh but no gmsh program was found (bundled copy, FEM preference, PATH): using Netgen")
    tries = (gmsh + netgen) if _mesher_of(analysis) == "Gmsh" else (netgen + gmsh)
    done = []
    say("info", f"Meshing in a worker process, up to {limit:.0f} s per try")
    for n, (label, kind, order2) in enumerate(tries, 1):
        remaining = total_end - time.time()
        if remaining < 5:
            done.append(f"try {n} ({label}): skipped, the total time is used up")
            break
        label = f"Try {n}: {label}"
        settings = dict(base, SecondOrder=bool(order2))
        try:
            fem, info = mw.run_worker(shape, settings, min(limit, remaining), label, progress, kind)
        except mw.MeshCancelled:
            say("warning", "Meshing cancelled")
            raise StudyError("Meshing cancelled: " + ("; ".join(done) if done else "no mesh was made"))
        if info["status"] == "ok":
            say("ok" if not done else "warning", info["text"])
            mesh.FemMesh = fem
            mesh.purgeTouched()  # a later recompute must not run Netgen in this process (the hang the worker avoids)
            return mesh.FemMesh, ([f"{'; '.join(done)}; meshed by {label} ({info['seconds']} s)"] if done else [])
        done.append(f"{info['text']} ({info['seconds']} s)")
        say("warning", info["text"] + (f" ({info['seconds']} s)" if info["status"] != "timeout" else "")
            + ("; trying the next" if n < len(tries) else ""))
    raise StudyError("Netgen made no volume mesh. " + " | ".join(done) + ". Try a larger MaxSize, heal or simplify the shape, "
                     "raise the study's MeshTimeLimit, or mesh the part in pieces")


def _mesh_with_fallbacks(mesh, progress=None, analysis=None):
    """Netgen on the study's shape; when it returns no volume elements (surface only, or nothing), retries, at most twice:
    (1) a repaired copy of the shape at the asked order; (2) when second order was asked, the repaired copy at first
    order (the caller upgrades the linear tetrahedra to Tet10).  Returns (FemMesh, [what was tried and used]); the
    list is empty when the first try worked.  Each try is logged (events) and reported through ``progress``."""
    import FreeCAD

    def say(level, text):
        FreeCAD.Console.PrintWarning(f"SIM-04: {text}\n") if level == "warning" else FreeCAD.Console.PrintMessage(f"SIM-04: {text}\n")
        try:
            import bruceworx_sim_events as events

            events.message(level, text)
        except Exception:
            pass
        if progress:
            progress(text)

    if _use_worker(mesh, analysis):
        return _mesh_in_workers(mesh, analysis, say, progress)
    fem = _mesh_in_place(mesh)
    if _has_volumes(fem):
        return fem, []
    got = f"{fem.NodeCount} nodes, {fem.FaceCount} faces, no volume elements" if fem is not None and fem.NodeCount else "no mesh"
    try:
        original = shape_of(getattr(mesh, "Shape", None))
    except Exception:
        original = None
    if original is None or original.isNull() or not original.Solids:
        return fem, []
    second = bool(getattr(mesh, "SecondOrder", False))
    say("warning", f"Netgen returned {got} for the shape as it is: retrying on a repaired copy (BREP round trip)")
    try:
        copy = repaired_copy(original)
    except Exception as exc:
        copy = None
        say("warning", f"Could not repair the shape: {exc}")
    if copy is None:
        return fem, []
    tries = [(second, "a repaired copy of the shape" + (" (second order)" if second else ""))]
    if second:
        tries.append((False, "a repaired copy at first order (the tetrahedra are upgraded to Tet10 after)"))
    last = fem
    for n, (order2, label) in enumerate(tries):
        try:
            last = _mesh_on_copy(mesh, copy, order2)
        except Exception as exc:
            say("warning", f"Meshing {label} failed: {exc}")
            continue
        if _has_volumes(last):
            say("warning", f"Meshed {label}: {last.NodeCount} nodes, {last.VolumeCount} volume elements")
            return last, [f"Netgen returned {got} for the shape as saved; meshed {label} instead"]
        say("warning", f"{label}: {last.NodeCount} nodes, no volume elements"
            + ("; trying first order" if n + 1 < len(tries) else ""))
    return fem, []


def mesh_study(analysis, progress=None):
    """Meshes the study (see ``_mesh_study``) and tells the run panes (SIM-07)."""
    import bruceworx_sim_events as events

    with events.stage("Mesh"):
        stats = _mesh_study(analysis, events.tee(progress))
    events.message("ok", f"Meshed: {stats['nodes']} nodes, {stats['elements']} elements in {stats['seconds']} s")
    return stats


def _mesh_study(analysis, progress=None):
    """Generates the study's mesh (recompute of the Netgen object); returns mesh_stats.

    A mesh without volume elements (Netgen sometimes returns the surface only for a complex solid) is an error. Nodes no
    element uses are dropped, and so is every group of elements not connected to the largest body through shared faces. When second order was asked and Netgen returned linear tetrahedra, they are upgraded to Tet10
    with straight mid-edge nodes (said in the progress text and in ``stats['notes']``)."""
    mesh = members(analysis)["mesh"]
    if mesh is None:
        raise StudyError("the study has no mesh object")
    if progress:
        progress("Meshing…")
    t0 = time.time()
    fem, fallback = _mesh_with_fallbacks(mesh, progress, analysis)
    if fallback:  # made on a copy of the shape: the study's mesh object takes the result
        mesh.FemMesh = fem
        fem = mesh.FemMesh
    if not is_meshed(mesh):
        raise StudyError("Netgen made no mesh (an open or invalid shape?)")
    if not fem.VolumeCount:
        raise StudyError(f"Netgen made a surface mesh only ({fem.NodeCount} nodes, {fem.FaceCount} faces, no volume elements)"
                         f"{' even after: ' + '; '.join(fallback) if fallback else ''}: "
                         "the solid is probably too complex or not closed. Try a larger MaxSize, a coarser Fineness, "
                         "heal or simplify the shape, or mesh it in pieces")
    notes = list(fallback)
    first = len(fem.getElementNodes(next(iter(fem.Volumes)))) == 4
    upgrade = bool(getattr(mesh, "SecondOrder", False)) and first
    orphans = fem.NodeCount - len(used_node_ids(fem))
    keep, loose, clusters = face_connected_volumes(fem)
    import bruceworx_sim_defs as defs

    try:
        size_file = defs.prepare_mesh(analysis, mesh)  # named selections, parameters, local sizes (SIM-07 P2)
    except defs.DefsError as exc:
        raise StudyError(str(exc)) from exc
    if size_file and progress:
        progress("Local mesh sizes: " + os.path.basename(size_file))
    if upgrade or orphans or loose:
        if upgrade:
            notes.append("Second order was asked but Netgen returned linear tetrahedra: upgraded them to Tet10 with straight mid-edge nodes")
        if orphans:
            notes.append(f"Dropped {orphans} nodes no element uses (left by a failed second-order step)")
        if loose:
            notes.append(f"Dropped {loose} elements in {clusters} clusters that touch the body only at a node or an edge "
                         "(not connected through faces: a mechanism for the solver)")
        for text in notes:
            if progress:
                progress(text)
        mesh.FemMesh = rebuild_mesh(fem, drop_unused=True, upgrade=upgrade, volumes=keep if loose else None)
    stats = mesh_stats(mesh)
    stats["seconds"] = round(time.time() - t0, 1)
    stats["notes"] = notes
    stats["fallback"] = list(fallback)
    record_stage(analysis, "mesh")
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


def study_kind(analysis):
    """The study's type key: static, modal, buckling (a study made before 30 Oct is static)."""
    if analysis is None or "StudyType" not in analysis.PropertiesList:
        return "static"
    return str(analysis.StudyType or "static")


def precheck(analysis, results=True):
    """What is missing before a solve: a list of ("error"|"warning", text). Empty means ready.

    By study type: static and buckling need supports and a load (buckling: the reference load); a
    modal study needs a density, may be free-free (a warning: six rigid-body modes are skipped) and
    ignores its loads unless it is prestressed (then it needs supports and a load, as a static one).
    """
    issues = []
    kind = study_kind(analysis)
    if kind in AIRFLOW_KINDS:
        import bruceworx_cfd as cfd

        return [(level, text) for level, text in cfd.study_precheck(analysis)[0] if level != "info"]
    if kind in ("static", "thermal_stress"):  # SIM-04: the blade mass pressure follows the spin speed
        try:
            import bruceworx_sim_post as post

            post.update_blade_loads(analysis.Document, analysis=analysis)
        except Exception:
            pass
    prestressed = kind == "modal" and bool(getattr(analysis, "Prestressed", False))
    thermal_kind = kind in ("thermal", "thermal_stress")
    if thermal_kind:
        import bruceworx_sim_thermal as thermal
    m = members(analysis)
    if kind not in AIRFLOW_KINDS:
        import bruceworx_sim_defs as defs

        defs.sync(analysis)  # named selections into the objects that use them (SIM-07 P2)
        defs.apply(analysis)  # parameter expressions into the fields
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
            issues += mesh_estimate_issues(analysis, m["mesh"])
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
            elif kind == "modal":
                if not _density_kg_m3(data.get("Density")):
                    issues.append(("error", f"{mat.Label}: no density: a modal study needs the mass"))
            elif thermal_kind:
                issues += thermal.material_issues(mat.Label, data, analysis)
            elif kind == "static" and not _quantity_mpa(data.get("YieldStrength")):
                if _quantity_mpa(data.get("UltimateTensileStrength")):
                    issues.append(("warning", f"{mat.Label}: no yield strength; the safety factor uses the ultimate tensile strength"))
                else:
                    issues.append(("warning", f"{mat.Label}: no yield or ultimate strength; no safety factor"))

    if kind != "thermal":  # a thermal study has no structure to hold
        if not m["supports"]:
            if kind == "modal" and not prestressed:
                issues.append(("warning", "No supports: a free-free study; the first 6 modes are rigid-body modes "
                                          "(below 1 Hz), skipped in the table and reported"))
            elif prestressed:
                issues.append(("error", "No support: the static step of a prestressed modal study needs one"))
            else:
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
            if kind == "buckling":
                issues.append(("error", "No load: a buckling study needs a load (it is the reference load)"))
            elif prestressed:
                issues.append(("error", "No load: a prestressed modal study needs the loads to prestress with"))
            elif kind == "static":
                issues.append(("error", "No load: add a Force or Pressure"))
        elif kind == "modal" and not prestressed:
            issues.append(("warning", "The loads are ignored: a plain modal study finds the free vibration "
                                      "(Prestressed stiffens the modes with them)"))
    for obj in m["supports"] + m["loads"] + m["others"]:
        if _references_empty(obj) and not is_self_weight(obj) and not is_spin_load(obj) and obj.TypeId not in ("Fem::ConstraintSelfWeight", "Fem::ConstraintInitialTemperature"):
            issues.append(("error", f"{obj.Label}: no faces, edges or vertices chosen"))
        if obj.TypeId == "Fem::ConstraintForce" and not _value(obj, "Force"):
            issues.append(("error", f"{obj.Label}: the force is zero"))
        if obj.TypeId == "Fem::ConstraintPressure" and not _value(obj, "Pressure"):
            issues.append(("error", f"{obj.Label}: the pressure is zero"))
    if thermal_kind:
        issues += thermal.boundary_issues(analysis, m)
    if kind in ("static", "thermal_stress"):  # SIM-04: the spin loads (axis, speed, density, balance), also with heat
        import bruceworx_sim_post as post

        issues += post.precheck_spin(analysis, m)
        issues += post.precheck_blades(analysis, m)
        last = latest_result(analysis) if results and post.spin_loads(analysis) else None
        if last is not None:
            try:
                burst = post.burst_speed(last, analysis)
                if burst.get("below"):
                    issues.append(("error", f"Burst margin {burst['margin']:.3g} is below the limit {burst['limit']:g} "
                                            f"(n_burst {burst['burst_rpm']:.5g} rpm at {burst['rpm']:g} rpm): lower the speed, "
                                            "strengthen the part, or change the limit (study property BurstMarginLimit)"))
            except Exception:
                pass
    if m["solver"] is None:
        issues.append(("error", "No solver: New Study makes a CalculiX solver"))
    if not find_ccx():
        issues.append(("error", "CalculiX (ccx) not found: Vector Labs bundles it; set FEM > CalculiX > binary otherwise"))
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
    import bruceworx_sim_events

    progress = bruceworx_sim_events.tee(progress)
    issues = precheck(analysis, results=False)  # an old result must not block the next solve
    if not ready(issues):
        raise StudyError("; ".join(text for kind, text in issues if kind == "error"))
    m = members(analysis)
    if not is_meshed(m["mesh"]):
        mesh_study(analysis, progress)
    ensure_ccx_pref()
    _purge_results(analysis)
    eigen = None
    thermal = None
    if study_kind(analysis) in ("thermal", "thermal_stress"):
        import bruceworx_sim_thermal as thermal

        thermal.configure_solver(analysis, m["solver"])
    if study_kind(analysis) in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        eigen.configure_solver(analysis, m["solver"])
    fea = ccxtools.FemToolsCcx(analysis, m["solver"])
    fea.update_objects()
    if working_dir:  # FEM falls back to a temp folder when it is missing: make it, so the ccx files land there
        working_dir = os.path.abspath(str(working_dir))
        os.makedirs(working_dir, exist_ok=True)
    fea.setup_working_dir(working_dir or None)
    message = fea.check_prerequisites()
    if message:
        raise StudyError(message.strip())
    if progress:
        progress("Writing the CalculiX input…")
    import bruceworx_sim_events as events

    with events.stage("Write input"):
        fea.write_inp_file()
    if not fea.inp_file_name:
        raise StudyError("the CalculiX input file was not written")
    if eigen is not None:
        try:
            eigen.patch_input(analysis, fea.inp_file_name)  # prestressed modal: the perturbation step
        except eigen.EigenError as exc:
            raise StudyError(str(exc)) from exc
    if thermal is not None:
        thermal.patch_input(analysis, fea.inp_file_name)  # heat flow printed at the fixed temperatures, output rate
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
        import bruceworx_sim_steps as steps

        steps.two_step_input(analysis, fea.inp_file_name)  # thermal stress: *HEAT TRANSFER, then *STATIC (SIM-07 P2)
        frd.setupPipeline = lambda *args, **kwargs: None
        return self

    def __exit__(self, *exc):
        self.module.setupPipeline = self.saved
        return False


DISPLACEMENT_LIMIT = 1e3  # x the model size: beyond it the model is unsupported or singular


def check_displacement(result, analysis=None):
    """Raises StudyError when the largest displacement is over 1e3 times the model size: ccx returns such a solution (1e9 MPa,
    kilometre-scale displacements) without an error when the supports hold nothing, e.g. a support node no element uses."""
    lengths = list(getattr(result, "DisplacementLengths", []) or [])
    if not lengths:
        return
    top = max(lengths)
    size = 0.0
    try:
        nodes = result.Mesh.FemMesh.Nodes.values()
        xs, ys, zs = zip(*((v.x, v.y, v.z) for v in nodes))
        size = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2)
    except Exception:
        pass
    if size and top > DISPLACEMENT_LIMIT * size:
        raise StudyError(f"The solution is singular: the largest displacement is {top:.3g} mm on a model {size:.3g} mm across "
                         f"(over {DISPLACEMENT_LIMIT:g} times). The model is unsupported (a support on a node no element uses, "
                         "or too few supports) or the supports leave a rigid-body motion; check them and solve again")


def finish_solve(fea, progress=None):
    """Loads the results of a finished ccx run (see ``_finish_solve``) and tells the run panes (SIM-07)."""
    import bruceworx_sim_events as events

    with events.stage("Read results"):
        result = _finish_solve(fea, events.tee(progress))
    record_stage(fea.analysis, "solution")
    events.message("ok", "Results read")
    return result


def _finish_solve(fea, progress=None):
    """Loads the results of a finished ccx run, adds the safety factor; returns the result object.

    A modal or buckling study's results are its modes (bruceworx_sim_eigen.finish); this returns the
    first one. The nonlinear buckling run is separate (``solve`` runs it; the panel in a QProcess).
    """
    analysis = fea.analysis
    if progress:
        progress("Reading the results…")
    if study_kind(analysis) in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        try:
            modes = eigen.finish(analysis, fea)
        except eigen.EigenError as exc:
            raise StudyError(str(exc)) from exc
        return modes[0]
    with _no_post_pipeline():
        fea.load_results()
    results = members(analysis)["results"]
    if not results:
        raise StudyError("CalculiX wrote no results (see the Report view)")
    if study_kind(analysis) in ("thermal", "thermal_stress"):
        import bruceworx_sim_thermal as thermal

        try:
            done = thermal.finish(analysis, fea)
            if study_kind(analysis) == "thermal_stress":
                import bruceworx_sim_steps as steps

                steps.label_step_results(analysis)
            return done
        except thermal.ThermalError as exc:
            raise StudyError(str(exc)) from exc
    result = results[-1]
    check_displacement(result, analysis)
    add_safety_factor(result, analysis)
    return result


def solve(analysis, working_dir=None, progress=None):
    """The whole run, blocking: mesh if needed, write, ccx, read, safety factor. Returns the result."""
    import bruceworx_sim_events as events

    events.run_started(getattr(analysis, "Label", ""))
    progress = events.tee(progress)
    fea = prepare_solve(analysis, working_dir, progress)
    if progress:
        progress("Solving with CalculiX…")
    t0 = time.time()
    events.stage_started("Solve")
    code = fea.ccx_run()
    events.stage_finished("Solve", time.time() - t0, not code)
    if code:
        raise StudyError(f"CalculiX finished with exit code {code}: {(fea.ccx_stderr or '')[-400:]}")
    if progress:
        progress(f"CalculiX finished in {time.time() - t0:.1f} s")
    result = finish_solve(fea, progress)
    if study_kind(analysis) == "buckling" and bool(getattr(analysis, "Nonlinear", False)):
        import bruceworx_sim_eigen as eigen

        try:
            eigen.solve_nonlinear(analysis, fea, progress)
        except eigen.EigenError as exc:
            if progress:
                progress(f"Nonlinear buckling not run: {exc}")
    return result


def values_hdt(uuid):
    return _card_hdt(uuid) if uuid else None


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
            "k": _number_in(data.get("ThermalConductivity"), "W/m/K"),
            "cp": _number_in(data.get("SpecificHeat"), "J/kg/K"),
            "alpha": _number_in(data.get("ThermalExpansionCoefficient"), "1/K"),
            "hdt": values_hdt(uuid),
        }
        if usable(values):
            return values
    return None


def safety_factors(stresses, allowable):
    """SF per node: allowable / sigma_eq, capped at SF_CAP (also for a zero or NaN stress)."""
    out = []
    for stress in stresses:
        if not stress > 0 or allowable / stress > SF_CAP:  # NaN compares false: capped
            out.append(SF_CAP)
        else:
            out.append(allowable / stress)
    return out


def support_nodes(analysis, result=None):
    """The result mesh's nodes on the study's supports (sorted ids), matched by position from the study's
    mesh (the FRD import may renumber a compacted mesh)."""
    try:
        from femmesh import meshtools
    except Exception:
        return []
    m = members(analysis)
    mesh = getattr(m["mesh"], "FemMesh", None) if m["mesh"] is not None else None
    if mesh is None or not m["supports"]:
        return []
    ids = set()
    for sup in m["supports"]:
        try:
            ids.update(meshtools.get_femnodes_by_femobj_with_references(mesh, {"Object": sup}))
        except Exception:
            continue
    if result is None or not ids:
        return sorted(ids)
    nodes = result.Mesh.FemMesh.Nodes

    def key(v):
        return (round(v.x, 6), round(v.y, 6), round(v.z, 6))

    if all(i in nodes and key(nodes[i]) == key(mesh.getNodeById(i)) for i in list(ids)[:50]):
        return sorted(i for i in ids if i in nodes)
    where = {key(v): n for n, v in nodes.items()}
    return sorted(where[k] for k in (key(mesh.getNodeById(i)) for i in ids) if k in where)


POINT_SUPPORT_RADII = 2.0  # x the mesh's MaxSize: the nodes this close to a point support are left out of the "away" minimum


def point_support_nodes(analysis, result):
    """(node numbers, radius mm): the nodes of the result within ``POINT_SUPPORT_RADII`` x MaxSize of a support that is a
    vertex (a point pin has a stress spike round it); ([], 0.0) when the supports are faces or edges."""
    m = members(analysis)
    mesh = m["mesh"]
    size = float(getattr(mesh, "MaxSize", 0) or 0) if mesh is not None else 0.0
    points = []
    for sup in m["supports"]:
        for obj, subs in getattr(sup, "References", None) or []:
            for sub in subs:
                if sub.startswith("Vertex"):
                    try:
                        points.append(shape_of(obj).getElement(sub).Point)
                    except Exception:
                        continue
    if not points or not size:
        return [], 0.0
    radius = POINT_SUPPORT_RADII * size
    out = set()
    for number, v in result.Mesh.FemMesh.Nodes.items():
        if any((v.x - p.x) ** 2 + (v.y - p.y) ** 2 + (v.z - p.z) ** 2 <= radius * radius for p in points):
            out.add(number)
    return sorted(out), radius


def element_min_factors(result):
    """{element id: the lowest safety factor of its nodes} of a result's mesh (volumes, else faces): the
    crisp safety-factor view colours each element by it (30 Oct follow-up 1)."""
    factors = dict(zip(result.NodeNumbers, getattr(result, "SafetyFactor", []) or []))
    if not factors:
        return {}
    fm = result.Mesh.FemMesh
    elements = list(fm.Volumes) or list(fm.Faces)
    out = {}
    for elem in elements:
        values = [factors[n] for n in fm.getElementNodes(elem) if n in factors]
        if values:
            out[elem] = min(values)
    return out


def add_safety_factor(result, analysis=None):
    """Adds SafetyFactor (per node), AllowableStress, AllowableBasis, StressMeasure and MinSafetyFactor
    to a result object, with the study's stress measure.

    Returns min SF, or None when the material has no strength value.
    """
    analysis = analysis or study_of(result)
    values = study_material(analysis) if analysis is not None else None
    knockdown = getattr(analysis, "Knockdown", KNOCKDOWN) if analysis is not None and "Knockdown" in analysis.PropertiesList else KNOCKDOWN
    allowable, basis = allowable_stress(values, knockdown or KNOCKDOWN)
    if "SafetyFactor" not in result.PropertiesList:
        result.addProperty("App::PropertyFloatList", "SafetyFactor", "Result", "[BW] allowable stress / equivalent stress per node")
        result.addProperty("App::PropertyFloat", "AllowableStress", "Result", "[BW] The allowable stress (MPa) the safety factor divides")
        result.addProperty("App::PropertyString", "AllowableBasis", "Result", "[BW] Where the allowable stress comes from")
        result.addProperty("App::PropertyFloat", "MinSafetyFactor", "Result", "[BW] The lowest safety factor")
    if "StressMeasure" not in result.PropertiesList:
        result.addProperty("App::PropertyString", "StressMeasure", "Result",
                           "[BW] The equivalent stress the safety factor used: vonMises, MaxPrincipal or Tresca")
        result.addProperty("App::PropertyFloat", "MaxEquivalentStress", "Result",
                           "[BW] The largest equivalent stress (MPa) of that measure")
    result.AllowableBasis = basis
    stresses, used = equivalent_field(result, study_measure(analysis))
    result.StressMeasure = used
    result.MaxEquivalentStress = max(stresses) if stresses else 0.0
    if not allowable or not stresses:
        result.SafetyFactor = []
        result.AllowableStress = 0.0
        result.MinSafetyFactor = 0.0
        return None
    factors = safety_factors(stresses, allowable)
    result.SafetyFactor = factors
    result.AllowableStress = allowable
    result.MinSafetyFactor = min(factors)
    # 30 Oct follow-up 2: a fully fixed face makes a stress singularity at its edges, so the minimum is
    # also given without the nodes on the supports
    if "SupportNodes" not in result.PropertiesList:
        result.addProperty("App::PropertyIntegerList", "SupportNodes", "Result",
                           "[BW] The nodes on the supports (a fixed face's edges are a stress singularity)")
        result.addProperty("App::PropertyFloat", "MinSafetyFactorAwayFromSupports", "Result",
                           "[BW] The lowest safety factor of the nodes not on a support")
    on_support = set(support_nodes(analysis, result)) if analysis is not None else set()
    near, radius = point_support_nodes(analysis, result) if analysis is not None else ([], 0.0)
    on_support |= set(near)
    if "SupportExcludeRadius" not in result.PropertiesList:
        result.addProperty("App::PropertyFloat", "SupportExcludeRadius", "Result",
                           "[BW] Nodes within this distance (mm) of a point support are left out of the away-from-supports minimum")
    result.SupportExcludeRadius = radius
    result.SupportNodes = sorted(on_support)
    away = [sf for n, sf in zip(result.NodeNumbers, factors) if n not in on_support]
    result.MinSafetyFactorAwayFromSupports = min(away) if away else result.MinSafetyFactor
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
    out["min_sf_away"], out["min_sf_away_node"], out["support_nodes"] = None, None, 0
    if sf:
        i = min(range(len(sf)), key=sf.__getitem__)
        out["min_sf"], out["min_sf_node"] = sf[i], nodes[i] if i < len(nodes) else None
        on_support = set(getattr(result, "SupportNodes", []) or [])
        out["support_nodes"] = len(on_support)
        away = [j for j in range(min(len(sf), len(nodes))) if nodes[j] not in on_support]
        if away and on_support:
            j = min(away, key=sf.__getitem__)
            out["min_sf_away"], out["min_sf_away_node"] = sf[j], nodes[j]
    out["support_radius"] = float(getattr(result, "SupportExcludeRadius", 0.0) or 0.0)
    out["measure"] = str(getattr(result, "StressMeasure", "") or "vonMises")
    out["max_eq"] = float(getattr(result, "MaxEquivalentStress", 0.0) or out["max_vm"])
    out["burst"] = {}
    try:  # SIM-04: a spinning rotor reports its burst speed beside the safety factor
        import bruceworx_sim_post as post

        an = study_of(result)
        if an is not None and post.spin_loads(an):
            out["burst"] = post.burst_speed(result, an)
    except Exception:
        pass
    out["bore"] = {}
    try:  # SIM-04: a spinning rotor with a bore reports it (mean, max and mid-plane hoop, von Mises max)
        import bruceworx_sim_post as post

        an = study_of(result)
        if an is not None and post.spin_loads(an):
            out["bore"] = post.bore_stress(result, an)
    except Exception:
        pass
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
        "temperature": (result.Temperature[i] - 273.15) if i < len(getattr(result, "Temperature", []) or []) else None,
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
    elif obj.TypeId == "Fem::ConstraintPressure" and "BladeCount" in obj.PropertiesList:
        kind = "Blade mass"
        value = (f"{obj.BladeCount:g} x {obj.BladeMassG:g} g at {obj.BladeCgRadius.Value:g} mm: {obj.BladePullN:.4g} N each, "
                 f"{obj.BladeTotalN:.5g} N over {obj.BladeArea:.4g} mm2 = {_value(obj, 'Pressure', 'MPa') or 0:.4g} MPa")
    elif obj.TypeId == "Fem::ConstraintPressure":
        value = f"{_value(obj, 'Pressure', 'MPa') or 0:g} MPa" + (" (reversed)" if getattr(obj, "Reversed", False) else "")
    elif is_spin_load(obj):
        import bruceworx_sim_post as post

        kind, value = "Spin", f"{post.spin_rpm(obj):g} rpm"
    elif obj.TypeId == "Fem::ConstraintDisplacement":
        locked = [a for a in "xyz" if not getattr(obj, f"{a}Free", True)]
        value = "locked " + ", ".join(locked) if locked else "nothing locked"
    elif obj.TypeId in ("Fem::ConstraintTemperature", "Fem::ConstraintHeatflux", "Fem::ConstraintInitialTemperature") or (
            obj.TypeId == "Fem::ConstraintPython" and "TotalPower" in obj.PropertiesList):
        import bruceworx_sim_thermal as thermal

        value = thermal.describe(obj)
        if obj.TypeId == "Fem::ConstraintPython":
            kind = "HeatSource"
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
        "measure": study_measure(analysis),
        "bands": study_bands(analysis),
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
    if out["type"] in AIRFLOW_KINDS:
        try:
            out["airflow"] = __import__("json").loads(analysis.CfdSummary or "null")
        except ValueError:
            out["airflow"] = None
        return out
    if out["type"] in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        out["eigen"] = eigen.summary_part(analysis)
        return out
    if out["type"] in ("thermal", "thermal_stress"):
        import bruceworx_sim_thermal as thermal

        out["thermal"] = thermal.summary_part(analysis)
    result = latest_result(analysis)
    if result is not None:
        out["result"] = result_extrema(result)
        out["result"]["name"] = result.Label
        factors = list(getattr(result, "SafetyFactor", []) or [])
        out["result"]["band_counts"] = band_counts(factors, out["bands"][0]) if factors else None
    return out


def report_folder(doc):
    """Where the report goes: beside the document; the temp folder for an unsaved one."""
    import tempfile

    path = getattr(doc, "FileName", "") or ""
    if path:
        return os.path.dirname(path)
    return tempfile.gettempdir()


def report_stem(analysis):
    """'<document>_<study>_report', the name of the report's folder and PDF."""
    doc = analysis.Document
    stem = os.path.splitext(os.path.basename(doc.FileName))[0] if doc.FileName else doc.Name
    name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in summary_name(analysis)).strip().replace(" ", "_")
    return f"{stem}_{name}_report"


def report_paths(analysis, folder=None):
    """Where the report goes: {"dir": the LaTeX folder, "tex": its report.tex, "built": the PDF the compiler
    writes there, "target": the PDF's copy beside the document}.

    folder defaults to report_folder (beside the document: for a vault file that is the vault item's
    working folder, so a release package can carry it).
    """
    folder = folder or report_folder(analysis.Document)
    stem = report_stem(analysis)
    directory = os.path.join(folder, stem)
    return {"dir": directory, "tex": os.path.join(directory, "report.tex"), "target": os.path.join(folder, stem + ".pdf"),
            "built": os.path.join(directory, "report.pdf")}


def summary_name(analysis):
    return analysis.Label.replace("Study: ", "", 1)


def _fmt(value, digits=3, unit="", cap=False):
    """A number for the report; cap=True for a safety factor (SF_CAP and above read "> 100")."""
    if value is None:
        return "–"
    if isinstance(value, float):
        if cap and value >= SF_CAP:
            return f"> {SF_CAP:g}"
        text = f"{value:.{digits}g}"
        if "e+" in text:  # 210000, not 2.1e+05
            text = f"{float(text):.0f}"
    else:
        text = str(value)
    return f"{text} {unit}".strip()


# ----- the LaTeX report (S4, 30 Oct) ----------------------------------------------------------------

_TEX_SPECIALS = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
                 "^": r"\^{}", "_": r"\_", "%": r"\%", "~": r"\~{}"}
_TEX_UNICODE = {"–": "--", "—": "---", "×": r"$\times$", "ν": r"$\nu$", "σ": r"$\sigma$", "°": r"$^\circ$",
                "³": r"$^3$", "²": r"$^2$", "µ": r"$\mu$", "μ": r"$\mu$", "≤": r"$\le$", "≥": r"$\ge$",
                "…": r"\dots{}", "·": r"$\cdot$", "−": "-", "✕": "x", "Ø": r"\O{}", "ø": r"\o{}",
                "λ": r"$\lambda$", "ψ": r"$\psi$", "φ": r"$\phi$", "ω": r"$\omega$"}
FIGURES = (("stress", "von Mises stress (MPa)"), ("displacement", "Displacement (mm), shape exaggerated"),
           ("safety", "Safety factor bands"))


def tex_escape(text):
    """Plain text made safe for LaTeX (the specials, and the few non-Latin signs a report uses)."""
    out = []
    for ch in str(text):
        out.append(_TEX_SPECIALS.get(ch) or _TEX_UNICODE.get(ch) or ch)
    return "".join(out)


def _tex_value(value, digits=4, unit=""):
    text = tex_escape(_fmt(value, digits))
    return f"{text}\\,{unit}" if unit and value is not None else text


def report_tex(analysis, images=None, folder=None, data=None):
    """Writes report.tex (and copies the renders beside it as fig_<kind>.png); returns report_paths.

    images: {"stress": png, "displacement": png, "safety": png} (the GUI's off-screen renders); a
    headless report has none and says so.
    """
    data = data or summary(analysis)
    data["ccx_version"] = data.get("ccx_version") or ccx_version(data.get("ccx") or None)
    paths = report_paths(analysis, folder)
    os.makedirs(paths["dir"], exist_ok=True)
    if data.get("type") in AIRFLOW_KINDS:  # the ParaView pictures of the case, when they exist
        import bruceworx_cfd_paraview as pvm

        data["airflow_figures"] = pvm.report_figures(getattr(analysis, "CaseFolder", ""), paths["dir"])
    figures = []
    wanted = FIGURES
    if data.get("thermal"):
        import bruceworx_sim_thermal as thermal

        wanted = thermal.figures_for(data["thermal"])
    if data.get("eigen"):
        import bruceworx_sim_eigen as eigen

        wanted = eigen.figures_for(data["eigen"])
        for name in os.listdir(paths["dir"]):  # renders of an earlier run's other modes
            if name.startswith("fig_mode") and name.endswith(".png"):
                os.remove(os.path.join(paths["dir"], name))
    for kind, caption in wanted:
        image = (images or {}).get(kind)
        target = os.path.join(paths["dir"], f"fig_{kind}.png")
        if image and os.path.isfile(image):
            if os.path.abspath(image) != os.path.abspath(target):
                shutil.copyfile(image, target)
            figures.append((f"fig_{kind}.png", caption))
        elif os.path.isfile(target):
            os.remove(target)  # an old render must not stand in for this result
    with open(paths["tex"], "w", encoding="utf-8") as handle:
        handle.write(report_source(data, figures))
    return paths


def report_source(data, figures=()):
    """The LaTeX source of a report from summary() data; figures = [(file, caption)]."""
    e = tex_escape
    mat = data.get("material") or {}
    res = data.get("result")
    thresholds, colors = data.get("bands") or (list(DEFAULT_SF_THRESHOLDS), list(DEFAULT_SF_COLORS))
    measure = (res or {}).get("measure") or data.get("measure") or DEFAULT_MEASURE
    lines = [
        r"\documentclass[10pt,a4paper]{article}",
        "% Vector Labs simulation report (BruceWorx SIM-01). Compile with tectonic, latexmk -pdf or pdflatex.",
        r"\usepackage{iftex}",
        r"\ifXeTeX\usepackage{fontspec}\else\usepackage[utf8]{inputenc}\usepackage[T1]{fontenc}\usepackage{lmodern}\fi",
        r"\usepackage[margin=20mm]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{xcolor}",
        r"\setlength{\parindent}{0pt}\setlength{\parskip}{4pt}",
    ]
    for i, color in enumerate(colors):
        lines.append(rf"\definecolor{{band{chr(65 + i)}}}{{HTML}}{{{_hex(color).lstrip('#')}}}")
    part = data.get("eigen")
    if part:
        import bruceworx_sim_eigen as eigen

        lines += eigen.report_preamble(part)
    if data.get("type") in AIRFLOW_KINDS:
        import bruceworx_cfd as cfd

        lines += [r"\begin{document}", rf"{{\Large\bfseries {e(data['name'])}}}\\[2pt]",
                  rf"{{\small\color{{black!60}} Vector Labs simulation report, {e(data['date'])}}}", ""]
        if data.get("airflow"):
            lines.append(cfd.report_section(data["airflow"]))
            lines.append(cfd.paraview().report_figures_tex(data.get("airflow_figures")))
        else:
            lines.append("Not run yet.")
        lines += _report_precheck(data.get("issues") or [])
        lines += ["", r"\vfill{\small\color{black!60} Incompressible steady RANS (OpenFOAM simpleFoam, "
                  r"$k$--$\omega$ SST or laminar); forces from the wall pressure and shear.}", r"\end{document}", ""]
        return "\n".join(lines)
    heat = data.get("thermal")
    if heat:
        import bruceworx_sim_thermal as thermal

        lines += thermal.report_preamble(heat)
    lines += [
        r"\begin{document}",
        rf"{{\Large\bfseries {e(data['name'])}}}\\[2pt]",
        rf"{{\small\color{{black!60}} Vector Labs simulation report, {e(data['date'])}}}",
        "",
        r"\section*{Study}",
        r"\begin{tabular}{@{}ll@{}}",
    ]
    type_label = dict((k, l) for k, l, _ok in STUDY_TYPES).get(data["type"], data["type"])
    for label, value in (("Study", data["name"]), ("Type", type_label), ("Document", data["document"]),
                         ("Model", data["model"] or "–"), ("Solver", f"CalculiX ccx {data.get('ccx_version') or '?'}")):
        lines.append(rf"{e(label)} & {e(value)}\\")
    lines += [r"\end{tabular}", "", r"\section*{Material}"]
    if mat:
        lines.append(r"\begin{tabular}{@{}ll@{}}")
        for label, key, unit in (("Card", "name", ""), ("Library", "library", ""), ("Young's modulus $E$", "E", "MPa"),
                                 (r"Poisson's ratio $\nu$", "nu", ""), (r"Yield strength $\sigma_y$", "yield", "MPa"),
                                 ("Ultimate tensile strength", "uts", "MPa"), ("Density", "density", r"kg/m$^3$"),
                                 ("Conductivity $k$", "k", "W/m/K"), ("Specific heat $c_p$", "cp", "J/kg/K"),
                                 (r"Expansion $\alpha$", "alpha", "1/K"), ("Heat deflection temp.", "hdt", r"$^\circ$C")):
            if key in ("k", "cp", "alpha", "hdt") and not heat:
                continue
            value = mat.get(key)
            if key == "alpha" and value:
                value, unit = value * 1e6, r"$\mu$m/m/K"
            if value not in (None, ""):
                label_tex = label if "$" in label else e(label)
                lines.append(rf"{label_tex} & {_tex_value(value, 4, unit) if unit else e(_fmt(value, 4))}\\")
        knock = "" if data["knockdown"] == 1 else f", knockdown {data['knockdown']:g}"
        if not part and not (heat and data["type"] == "thermal"):
            lines.append(rf"Allowable stress & {_tex_value(data['allowable'], 4, 'MPa')} ({e(data['basis'])}{e(knock)})\\")
        lines.append(r"\end{tabular}")
        if not part and not (heat and data["type"] == "thermal") and data["allowable"] and "ultimate" in (data["basis"] or ""):
            lines.append(r"\par\textbf{Note:} the card has no yield strength, so the safety factor divides the "
                         r"ultimate tensile strength. It says how far the part is from breaking, not from yielding; "
                         r"pick a card with a yield strength (the Vector Labs library has them) for a yield check.")
    else:
        lines.append("No material.")
    mesh = data.get("mesh") or {}
    lines += [
        "", r"\section*{Mesh}", r"\begin{tabular}{@{}ll@{}}",
        rf"Nodes & {mesh.get('nodes', 0)}\\",
        rf"Elements & {mesh.get('elements', 0)} ({e(mesh.get('order') or '?')} order)\\",
        rf"Max element size & {_tex_value(mesh.get('max_size'), 3, 'mm')}, {e(mesh.get('fineness', ''))}\\",
        r"\end{tabular}",
        r"\par{\small Netgen tetrahedra; the mesh-convergence study (SIM-04) was not run.}",
    ]

    def items(title, entries):
        out = ["", rf"\section*{{{title}}}"]
        if not entries:
            return out + ["None."]
        out.append(r"\begin{itemize}\setlength{\itemsep}{0pt}")
        for entry in entries:
            where = f" on {e(entry['where'])}" if entry["where"] else ""
            out.append(rf"\item \textbf{{{e(entry['label'])}}} ({e(entry['kind'])}) {e(entry['value'])}{where}")
        return out + [r"\end{itemize}"]

    lines += items("Supports", data.get("supports"))
    lines += items("Loads", data.get("loads"))
    if part:
        if part["kind"] == "modal" and not part.get("prestressed") and data.get("loads"):
            lines.append(r"{\small A plain modal study ignores the loads.}")
        lines += eigen.report_results(part, e)
        lines += _report_figures(figures, part.get("rows"))
        lines += _report_precheck(data.get("issues") or [])
        lines += ["", r"\vfill{\small\color{black!60} " + eigen.report_footer(part) + "}", r"\end{document}", ""]
        return "\n".join(lines)
    if heat:
        lines += thermal.report_results(heat, e)
        if data["type"] == "thermal":
            lines += _report_figures(figures, bool(heat.get("data")))
            lines += _report_precheck(data.get("issues") or [])
            lines += ["", r"\vfill{\small\color{black!60} " + thermal.report_footer(heat) + "}", r"\end{document}", ""]
            return "\n".join(lines)
    lines += ["", r"\section*{Results}"]
    if res:
        lines.append(r"\begin{tabular}{@{}ll@{}}")
        lines.append(rf"Max von Mises stress & {_tex_value(res['max_vm'], 4, 'MPa')} (node {res['max_vm_node']})\\")
        if measure != "vonMises":
            lines.append(rf"Max {e(measure_label(measure))} stress & {_tex_value(res.get('max_eq'), 4, 'MPa')}\\")
        lines.append(rf"Max displacement & {_tex_value(res['max_disp'], 4, 'mm')} (node {res['max_disp_node']})\\")
        sf = res["min_sf"]
        verdict = ""
        if sf is not None:
            verdict = r" \textcolor{bandA}{\textbf{below 1: the part fails}}" if sf < 1 else " (above 1)"
        lines.append(rf"Min safety factor, whole part & \textbf{{{e(_fmt(sf, 3, cap=True))}}} (node {res['min_sf_node']}){verdict}\\")
        if res.get("min_sf_away") is not None:
            near = (f", incl. every node within {_fmt(res['support_radius'], 3)} mm of a point support"
                    if res.get("support_radius") else "")
            lines.append(rf"Min safety factor away from the supports & \textbf{{{e(_fmt(res['min_sf_away'], 3, cap=True))}}} "
                         rf"(node {res['min_sf_away_node']}; {res['support_nodes']} nodes on supports left out{e(near)})\\")
        burst = res.get("burst") or {}
        if burst:
            if burst.get("below"):
                lines.append(rf"Burst margin check & \textcolor{{bandA}}{{\textbf{{ERROR: margin {_fmt(burst['margin'], 3)} is "
                             rf"below the limit {_fmt(burst['limit'], 3)}}}}}\\")
            if burst.get("burst_rpm"):
                lines.append(rf"Burst speed (Zwicky) & \textbf{{{_fmt(burst['burst_rpm'], 5)}}} rpm = "
                             rf"{_fmt(burst['margin'], 3)} $\times$ the {_fmt(burst['rpm'], 5)} rpm of the study\\")
                lines.append(rf"Burst margin $n_{{\mathrm{{burst}}}}/n$ & \textbf{{{_fmt(burst['margin'], 3)}}} "
                             rf"(average hoop stress over the meridional section {_tex_value(burst['avg_hoop'], 4, 'MPa')}, "
                             rf"UTS {_tex_value(burst['uts'], 4, 'MPa')}: {e(burst['uts_source'])})\\")
            else:
                lines.append(rf"Burst speed & {e(burst.get('note') or 'not available')}\\")
        bore = res.get("bore") or {}
        if bore:
            lines.append(rf"Bore (radius {_tex_value(bore['radius'], 4, 'mm')}, {_tex_value(bore['length'], 3, 'mm')} long, "
                         rf"z {_fmt(bore['z_min'], 4)} to {_fmt(bore['z_max'], 4)} mm) hoop: "
                         rf"mean / max / mid-plane & {_tex_value(bore['hoop_mean'], 4, '')} / {_tex_value(bore['hoop_max'], 4, '')} / "
                         rf"{_tex_value(bore['hoop_mid'], 4, 'MPa')}\\")
            lines.append(rf"Bore von Mises, max / mid-plane & {_tex_value(bore['vonMises_max'], 4, '')} / "
                         rf"{_tex_value(bore['vonMises_mid'], 4, 'MPa')}\\")
            if bore.get("bore_min_sf") is not None:
                lines.append(rf"Bore only: lowest safety factor & {e(_fmt(bore['bore_min_sf'], 3, cap=True))} "
                             r"(the bore's nodes, \emph{not} the part's: see Min safety factor above)\\")
            lines.append(rf"Safety factor basis & {e(measure_label(measure))} equivalent stress, never the hoop stress\\")
        lines.append(r"\end{tabular}")
        if burst.get("burst_rpm"):
            lines.append(r"\par{\small Burst speed: $n_{\mathrm{burst}} = n\sqrt{\mathrm{UTS}/\sigma_{\mathrm{avg}}}$, with "
                         r"$\sigma_{\mathrm{avg}}$ the hoop stress averaged over the disc's meridional section; it assumes the "
                         r"stress grows with the square of the speed (thermal stress does not) and uses the card's room "
                         r"temperature strength.}")
        if bore and bore["length"] > 0.5 * bore["radius"]:
            lines.append(r"\par{\small On a long bore the mean hoop stress understates the bore: the axial stress adds to "
                         r"the von Mises stress and the hoop stress varies along the bore. Read the mid-plane and von Mises "
                         r"values.}")
        if res.get("min_sf_away") is not None:
            lines.append(r"\par{\small A fully fixed face cannot contract sideways, so its edges carry a stress "
                         r"singularity that grows as the mesh is refined; the second minimum leaves the nodes on the "
                         r"supports out. Trust it only when the real support is not that stiff.}")
    else:
        lines.append("Not solved yet.")
    lines += [
        "", r"\section*{Safety factor}",
        rf"Safety factor per node $SF=\sigma_{{\mathrm{{allow}}}}/\sigma_{{eq}}$ with the "
        rf"\textbf{{{e(measure_label(measure))}}} equivalent stress:",
        rf"\[{measure_formula(measure)}\]",
        rf"Nodes with almost no stress show $SF>{SF_CAP:g}$.",
        "", r"\begin{tabular}{@{}lll@{}}", r"\toprule Band & Safety factor & Nodes\\ \midrule",
    ]
    counts = (res or {}).get("band_counts") or [None] * (len(thresholds) + 1)
    for i, label in enumerate(band_labels(thresholds)):
        count = "" if counts[i] is None else str(counts[i])
        lines.append(rf"\textcolor{{band{chr(65 + i)}}}{{\rule{{8mm}}{{3mm}}}} & {e(label)} & {count}\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    lines += _report_figures(figures, res)
    lines += _report_precheck(data.get("issues") or [])
    lines += [
        "", r"\vfill{\small\color{black!60} Static linear analysis: $K u = f$, small displacements, linear elastic "
        r"material; stresses from the nodal stress tensor of CalculiX.}",
        r"\end{document}", "",
    ]
    return "\n".join(lines)


def _report_figures(figures, solved):
    """The report's figures section; a line saying there are none when a solved study had no 3D view."""
    e = tex_escape
    lines = []
    if figures:
        lines += ["", r"\section*{Figures}"]
        for name, caption in figures:
            lines += [r"\begin{center}", rf"\includegraphics[width=0.78\linewidth]{{{name}}}\\[-2pt]",
                      rf"{{\small {e(caption)}}}", r"\end{center}"]
    elif solved:
        lines += ["", r"{\small No renders: the report was written without a 3D view (headless).}"]
    return lines


def _report_precheck(issues):
    e = tex_escape
    lines = ["", r"\section*{Pre-check}"]
    if issues:
        lines.append(r"\begin{itemize}\setlength{\itemsep}{0pt}")
        for kind, text in issues:
            prefix = r"\textbf{Error:} " if kind == "error" else "Warning: "
            lines.append(r"\item " + prefix + e(text))
        lines.append(r"\end{itemize}")
    else:
        lines.append("Nothing missing.")
    return lines


TEX_PREF = "TexPath"  # Mod/BruceWorx/Sim: a tectonic, latexmk or pdflatex binary to use first
MACTEX_BIN = "/Library/TeX/texbin"


def _tex_kind(path):
    name = os.path.basename(path).lower()
    for kind in ("tectonic", "latexmk", "pdflatex"):
        if name.startswith(kind):
            return kind
    return None


def tex_candidates():
    """(kind, path) in order: the preference, Tectonic in the bundle, in the pixi environment, on PATH;
    then latexmk and pdflatex on PATH or in MacTeX's /Library/TeX/texbin."""
    found = []
    pref = _prefs().GetString(TEX_PREF, "")
    if pref and _tex_kind(pref):
        found.append((_tex_kind(pref), pref))
    home = _home()
    found.append(("tectonic", os.path.join(home, "bin", "tectonic")))  # Vector Labs: Contents/bin/tectonic
    found.append(("tectonic", os.path.join(home, "..", "..", ".pixi", "envs", "default", "bin", "tectonic")))
    for kind in ("tectonic", "latexmk", "pdflatex"):
        which = shutil.which(kind)
        if which:
            found.append((kind, which))
        if kind != "tectonic":
            found.append((kind, os.path.join(MACTEX_BIN, kind)))
    return [(kind, os.path.normpath(path)) for kind, path in found]


def find_tex():
    """(kind, path) of the first LaTeX compiler found, or (None, None)."""
    for kind, path in tex_candidates():
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return kind, path
    return None, None


def tex_command(kind, path, tex="report.tex"):
    """The argv that compiles report.tex in its own folder."""
    if kind == "tectonic":
        return [path, "--keep-logs", "--chatter", "minimal", tex]
    if kind == "latexmk":
        return [path, "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex]
    return [path, "-interaction=nonstopmode", "-halt-on-error", tex]


TECTONIC_SEED = os.path.join("Resources", "tectonic-cache")  # in Vector Labs' Contents (make-vectorlabs.sh)


def tectonic_cache(home=None, user_cache=None):
    """Vector Labs' Tectonic cache, or None without a bundled seed: the seed (Contents/Resources/tectonic-cache,
    made at bundle time) copied once into ~/Library/Caches/Vector Labs/tectonic, writable, and copied again when
    the bundle's seed is newer. The first report then compiles offline (30 Oct follow-up 3)."""
    seed = os.path.join(home or _home(), TECTONIC_SEED)
    if not os.path.isdir(seed):
        return None
    target = user_cache or os.path.join(os.path.expanduser("~"), "Library", "Caches", "Vector Labs", "tectonic")
    stamp = os.path.join(target, ".vectorlabs-seed")
    want = f"{os.path.getmtime(seed):.0f}"
    try:
        with open(stamp, encoding="utf-8") as handle:
            current = handle.read().strip()
    except OSError:
        current = ""
    if current != want:
        try:
            shutil.copytree(seed, target, dirs_exist_ok=True)
            with open(stamp, "w", encoding="utf-8") as handle:
                handle.write(want)
        except OSError:
            return seed if os.access(seed, os.W_OK) else None
    return target


def tex_environment(path):
    """os.environ with the compiler's folder (and MacTeX's) on PATH: latexmk runs pdflatex by name.
    Vector Labs' Tectonic gets TECTONIC_CACHE_DIR, its pre-seeded cache (tectonic_cache)."""
    env = dict(os.environ)
    folders = [os.path.dirname(path), MACTEX_BIN]
    env["PATH"] = os.pathsep.join(folders + [env.get("PATH", "")])
    if _tex_kind(path) == "tectonic" and "TECTONIC_CACHE_DIR" not in env:
        try:
            cache = tectonic_cache()
        except Exception:
            cache = None
        if cache:
            env["TECTONIC_CACHE_DIR"] = cache
    return env


def finish_report(paths):
    """Copies the compiled report.pdf beside the document; returns the PDF path or None."""
    if not os.path.isfile(paths["built"]):
        return None
    shutil.copyfile(paths["built"], paths["target"])
    return paths["target"]


def compile_report(paths, timeout=600):
    """Compiles report.tex (blocking); returns (pdf path or None, message).

    Tectonic fetches the TeX files it lacks on its first run (network; about 40 MB into
    ~/Library/Caches/Tectonic) and is quick after that.
    """
    kind, path = find_tex()
    if kind is None:
        return None, (f"No LaTeX compiler (Tectonic, latexmk or pdflatex) was found: the report's source is in "
                      f"{paths['dir']}")
    if os.path.isfile(paths["built"]):
        os.remove(paths["built"])
    try:
        run = subprocess.run(tex_command(kind, path), cwd=paths["dir"], env=tex_environment(path),
                             capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return None, f"{kind} did not run: {exc}"
    pdf = finish_report(paths) if run.returncode == 0 else None
    if pdf is None:
        tail = (run.stdout + run.stderr)[-600:]
        return None, f"{kind} could not compile {paths['tex']} (exit {run.returncode}): {tail}"
    return pdf, f"Report compiled with {kind}: {pdf}"


def write_report(analysis, images=None, folder=None, compile=True):
    """report.tex (+ renders) and, when a compiler is there, the PDF: {"dir", "tex", "pdf" or None, "message"}."""
    paths = report_tex(analysis, images, folder)
    out = dict(paths)
    out["pdf"], out["message"] = (None, f"LaTeX source written: {paths['tex']}")
    if compile:
        out["pdf"], out["message"] = compile_report(paths)
    return out


# ----- a benchmark --------------------------------------------------------------------------------


def cantilever(doc=None, length=100.0, width=10.0, height=10.0, load=1000.0, E=210000.0, nu=0.3,
               yield_strength=355.0, name="Cantilever", divisor=MESH_DIVISOR, study_type="static", options=None):
    """A steel cantilever study: fixed at x=0, a tip load in -z on the far face. Returns (doc, study objects, theory).
    study_type "modal" or "buckling" makes that study on the same beam (options: its settings).

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
    plan = StudyPlan(name=name, model=box, material=values, max_size=box.Shape.BoundBox.DiagonalLength / divisor,
                     study_type=study_type, options=options)
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
