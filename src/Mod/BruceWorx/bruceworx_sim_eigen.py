# SPDX-License-Identifier: LGPL-2.1-or-later
"""Modal frequencies and structural buckling studies (roadmap SIM-01, next step; 30 Oct 2026).

The eigenvalue study types on the static-stress frame of bruceworx_sim: the same study (a
Fem::FemAnalysis), Netgen Tet10 mesh, bundled CalculiX, pre-check and LaTeX report. Headless
(FreeCADCmd runs all of it); the panels and the mode-shape playback are in bruceworx_sim_gui.

Modal (``StudyType`` "modal"): K phi = omega^2 M phi, f = omega / 2 pi, with CalculiX ``*FREQUENCY``.
  * ``ModeCount`` modes (8 by default) and an optional range ``FrequencyMin``..``FrequencyMax`` (Hz,
    0 = open). The material needs a density (a pre-check line).
  * Free-free is allowed: without supports the six rigid-body modes (f < 1 Hz) are asked for on top
    of ``ModeCount``, skipped in the table and reported. CalculiX drops a rigid mode whose
    eigenvalue comes out slightly negative; those are counted from its mode numbers.
  * The frequency table carries the effective modal mass in X, Y and Z as a percentage of the total
    effective mass, both from CalculiX's .dat file (its "E F F E C T I V E   M O D A L   M A S S"
    and "T O T A L   E F F E C T I V E   M A S S" blocks, written for every *FREQUENCY step).
  * ``Prestressed`` (M4, 30 Oct): a linear *STATIC step with the study's loads, then
    ``*STEP, PERTURBATION`` / ``*FREQUENCY``: the modes about the loaded state, with its stress
    stiffness (CalculiX takes the stiffness at the end of the previous step only with PERTURBATION;
    without it the frequencies are those of the unloaded part). The input is FreeCAD's static
    input with the density and the second step added (``patch_input``).
  * The excitation check (M3): ``ExcitationCheck`` off by default; ``ExcitationSource`` MotorSpeed
    (``MotorRpmMin``..``MotorRpmMax`` x ``BladeCount``: the blade-pass band, and the 1x rpm band) or
    FrequencyBand (``BandMin``..``BandMax`` Hz). A mode inside a band is amber in the table and the
    report.

Buckling (``StudyType`` "buckling"): (K + lambda K_G(sigma_0)) psi = 0 with CalculiX ``*BUCKLE``.
  * ``ModeCount`` load factors (4 by default); the study's loads are the reference load.
  * Negative factors (buckling under the reversed load) are listed but not counted as the first.
  * ``TargetLoadFactor`` (B1): 3 by default (preference ``Mod/BruceWorx/Sim/TargetBLF``); the first
    positive factor is red below 1, amber from 1 to the target, green at or above it.
  * The buckled shapes are read from the .frd file here (``read_frd_sets``), not by FreeCAD's FRD
    import: it keys a result set on a rising step value, so two equal or negative factors merge.
  * ``Nonlinear`` (B3): a geometrically nonlinear static run (``*STEP, NLGEOM``) on the mesh with an
    imperfection seeded from psi_1, amplitude ``ImperfectionAmplitude`` mm (0 = 1 % of the smallest
    section dimension, see ``section_size``), the load stepped by CalculiX's automatic *STATIC
    increments up to ``NonlinearLoadLimit`` x lambda_1 (CalculiX has no Riks arc-length method).
    The collapse load factor is the knee of the curve, where its tangent falls below 1 % of its
    initial slope (``KNEE_FRACTION``), else the last converged increment when the run stops
    converging (a limit point). The monitored displacement is the node where psi_1 peaks, along
    psi_1; the curve is cut where the run leaves the equilibrium path (``clean_curve``).

Mode shapes play by moving the result mesh's nodes: u(t) = s phi sin(2 pi t), s such that the largest
displacement is ``ModeScale`` (10 %) of the bounding-box diagonal.
"""

from __future__ import annotations

import math
import os
import re

MODAL_MODES = 8  # M1
BUCKLING_MODES = 4  # B2
RIGID_HZ = 1.0  # a mode below 1 Hz is a rigid-body mode (M5)
RIGID_MODES = 6
DEFAULT_TARGET_BLF = 3.0  # B1
DEFAULT_MODE_SCALE = 0.10  # largest displacement of a played mode / bounding-box diagonal
IMPERFECTION_FRACTION = 0.01  # B3: 1 % of the smallest section dimension
NONLINEAR_LOAD_LIMIT = 1.2  # the nonlinear run steps the load up to 1.2 x lambda_1
KNEE_FRACTION = 0.01  # the knee: the curve's tangent below 1 % of its initial slope
NONLINEAR_INCREMENT = 0.02  # *STATIC initial and largest increment (fraction of the step)
EXCITATION_SOURCES = ("MotorSpeed", "FrequencyBand")
STATUS_COLORS = {"red": "#D91C1C", "amber": "#F2A007", "green": "#3FA34D", "grey": "#8A8A8A"}
TARGET_PREF = "TargetBLF"
NONCONSERVATIVE_NOTE = (
    "Linear buckling is non-conservative: it assumes a perfect shape and a linear material. Real parts "
    "(struts with an initial bow, thin and printed shells) buckle below lambda_1, often well below; design "
    "codes apply knock-down factors, so compare lambda_1 with the target, not with 1, or run the nonlinear option.")


class EigenError(Exception):
    """A modal or buckling step cannot go on."""


# ----- the study ---------------------------------------------------------------------------------


def study_kind(analysis):
    """'static', 'modal', 'buckling' or another StudyType."""
    if analysis is None or "StudyType" not in analysis.PropertiesList:
        return "static"
    return str(analysis.StudyType or "static")


def is_eigen(analysis):
    return study_kind(analysis) in ("modal", "buckling")


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/BruceWorx/Sim")


def default_target():
    """The target buckling load factor new studies start with: the preference, else 3."""
    value = _prefs().GetFloat(TARGET_PREF, 0.0)
    return value if value > 0 else DEFAULT_TARGET_BLF


def save_default_target(value):
    value = float(value)
    if value <= 0:
        raise EigenError("the target load factor must be above 0")
    _prefs().SetFloat(TARGET_PREF, value)
    return value


def reset_default_target():
    _prefs().RemFloat(TARGET_PREF)
    return default_target()


def _add(analysis, kind, name, group, doc, value=None):
    if name in analysis.PropertiesList:
        return False
    analysis.addProperty(kind, name, group, "[BW] " + doc)
    if value is not None:
        setattr(analysis, name, value)
    return True


def ensure_properties(analysis):
    """The modal or buckling properties of a study (by its StudyType); nothing for a static one."""
    kind = study_kind(analysis)
    if kind == "modal":
        _add(analysis, "App::PropertyInteger", "ModeCount", "Modal",
             "Modes to find (8 by default); a free-free study finds the six rigid-body modes on top", MODAL_MODES)
        _add(analysis, "App::PropertyFloat", "FrequencyMin", "Modal", "Lowest frequency (Hz) to look for; 0 = no limit", 0.0)
        _add(analysis, "App::PropertyFloat", "FrequencyMax", "Modal", "Highest frequency (Hz) to look for; 0 = no limit", 0.0)
        _add(analysis, "App::PropertyBool", "Prestressed", "Modal",
             "A static step with the study's loads first, then the modes with its stress stiffening", False)
        _add(analysis, "App::PropertyBool", "ExcitationCheck", "Excitation",
             "Mark the modes inside an excitation band amber (off by default)", False)
        if _add(analysis, "App::PropertyEnumeration", "ExcitationSource", "Excitation",
                "MotorSpeed: rpm range x blade count (blade pass, and 1x rpm); FrequencyBand: a plain band"):
            analysis.ExcitationSource = list(EXCITATION_SOURCES)
            analysis.ExcitationSource = EXCITATION_SOURCES[0]
        _add(analysis, "App::PropertyFloat", "MotorRpmMin", "Excitation", "Lowest motor speed (rpm)", 3000.0)
        _add(analysis, "App::PropertyFloat", "MotorRpmMax", "Excitation", "Highest motor speed (rpm)", 12000.0)
        _add(analysis, "App::PropertyInteger", "BladeCount", "Excitation", "Blades per propeller (blade-pass = rpm / 60 x blades)", 2)
        _add(analysis, "App::PropertyFloat", "BandMin", "Excitation", "Excitation band, lower edge (Hz)", 0.0)
        _add(analysis, "App::PropertyFloat", "BandMax", "Excitation", "Excitation band, upper edge (Hz)", 0.0)
    elif kind == "buckling":
        _add(analysis, "App::PropertyInteger", "ModeCount", "Buckling", "Buckling load factors to find (4 by default)", BUCKLING_MODES)
        _add(analysis, "App::PropertyFloat", "TargetLoadFactor", "Buckling",
             "Target buckling load factor: lambda_1 red below 1, amber below the target, green at or above it",
             default_target())
        _add(analysis, "App::PropertyBool", "Nonlinear", "Buckling",
             "Also run a geometrically nonlinear static analysis with an imperfection seeded from mode 1", False)
        _add(analysis, "App::PropertyFloat", "ImperfectionAmplitude", "Buckling",
             "Imperfection amplitude (mm) along mode 1; 0 = 1 % of the smallest section dimension", 0.0)
        _add(analysis, "App::PropertyFloat", "NonlinearLoadLimit", "Buckling",
             "The nonlinear run steps the load up to this multiple of lambda_1", NONLINEAR_LOAD_LIMIT)
    else:
        return
    _add(analysis, "App::PropertyFloat", "ModeScale", "Results",
         "A played mode's largest displacement as a fraction of the bounding-box diagonal (0.1 = 10 %)", DEFAULT_MODE_SCALE)


def apply_options(analysis, options):
    """Sets study properties from a StudyPlan's options dict (names as the properties)."""
    ensure_properties(analysis)
    for key, value in (options or {}).items():
        if key not in analysis.PropertiesList:
            raise EigenError(f"a {study_kind(analysis)} study has no setting {key!r}")
        setattr(analysis, key, value)
    if "ModeCount" in analysis.PropertiesList and int(analysis.ModeCount) < 1:
        raise EigenError("at least one mode")


def _get(analysis, name, default=None):
    return getattr(analysis, name) if name in analysis.PropertiesList else default


# ----- the model and the solver ----------------------------------------------------------------------


def _members(analysis):
    import bruceworx_sim as sim

    return sim.members(analysis)


def free_free(analysis):
    """A study without supports."""
    return not _members(analysis)["supports"]


def model_shape(analysis):
    """The shape the study meshes (its Model, else its mesh's Shape object), or None."""
    import bruceworx_sim as sim

    model = _get(analysis, "Model")
    if model is None:
        mesh = _members(analysis)["mesh"]
        model = getattr(mesh, "Shape", None) if mesh is not None else None
    return sim.shape_of(model) if model is not None else None


def section_size(shape):
    """The smallest section dimension (mm) of a shape: the smaller of its bounding box's shortest edge
    and its hydraulic diameter 4 V / S. 4 V / S is the side of a square bar, the diameter of a round one
    and twice the wall of a thin plate or shell (the end faces aside), so a long bar gives its width and a
    thin shell (whose bounding box is large) a measure of its wall."""
    bb = shape.BoundBox
    shortest = min(bb.XLength, bb.YLength, bb.ZLength)
    area = float(shape.Area)
    hydraulic = 4.0 * float(shape.Volume) / area if area > 0 else shortest
    return min(shortest, hydraulic) if shortest > 0 else hydraulic


def imperfection_amplitude(analysis):
    """(amplitude mm, how): the study's ImperfectionAmplitude, else 1 % of section_size."""
    value = float(_get(analysis, "ImperfectionAmplitude", 0.0) or 0.0)
    if value > 0:
        return value, "set on the study"
    shape = model_shape(analysis)
    if shape is None:
        return 0.0, "no model"
    size = section_size(shape)
    return IMPERFECTION_FRACTION * size, (f"1 % of the smallest section dimension {size:.4g} mm (the smaller of the "
                                          f"bounding box's shortest edge and the hydraulic diameter 4V/S)")


def requested_modes(analysis):
    """The mode count CalculiX is asked for: ModeCount, plus the six rigid modes of a free-free modal study."""
    count = max(1, int(_get(analysis, "ModeCount", MODAL_MODES if study_kind(analysis) == "modal" else BUCKLING_MODES)))
    if study_kind(analysis) == "modal" and free_free(analysis) and float(_get(analysis, "FrequencyMin", 0.0) or 0.0) < RIGID_HZ:
        count += RIGID_MODES
    return count


def configure_solver(analysis, solver):
    """The CalculiX solver's settings for the study's type, from the study's properties (at each solve)."""
    ensure_properties(analysis)
    kind = study_kind(analysis)
    solver.GeometricalNonlinearity = "linear"
    if kind == "modal":
        prestressed = bool(_get(analysis, "Prestressed", False))
        solver.AnalysisType = "static" if prestressed else "frequency"  # the frequency step is added (patch_input)
        solver.EigenmodesCount = requested_modes(analysis)
        solver.EigenmodeLowLimit = f"{float(_get(analysis, 'FrequencyMin', 0.0) or 0.0)} Hz"
        solver.EigenmodeHighLimit = f"{float(_get(analysis, 'FrequencyMax', 0.0) or 0.0)} Hz"
        solver.Label = "CalculiX (prestressed modal)" if prestressed else "CalculiX (modal)"
    elif kind == "buckling":
        solver.AnalysisType = "buckling"
        solver.BucklingFactors = requested_modes(analysis)
        solver.Label = "CalculiX (buckling)"


def _density_t_mm3(analysis):
    import bruceworx_sim as sim

    values = sim.study_material(analysis) or {}
    density = values.get("density")
    return density * 1e-12 if density else None  # kg/m^3 -> t/mm^3


def frequency_card(count, fmin=0.0, fmax=0.0):
    """The *FREQUENCY data line: the count, and the range when one is set."""
    if fmin or fmax:
        return f"{int(count)},{float(fmin):.13G},{float(fmax):.13G}"
    return f"{int(count)}"


def perturbation_step(count, fmin=0.0, fmax=0.0):
    """The second step of a prestressed modal study: the modes about the loaded state."""
    return ("\n" + 59 * "*" + "\n"
            "** BruceWorx prestressed modal: the modes about the state of the static step above\n"
            "** (PERTURBATION: the stiffness at its end, with the stress stiffening)\n"
            "*STEP, PERTURBATION\n"
            "*FREQUENCY\n"
            f"{frequency_card(count, fmin, fmax)}\n"
            "*NODE FILE\nU\n*EL FILE\nS, E\n"
            "*END STEP\n")


def add_density(text, density):
    """A *DENSITY card after each material's *ELASTIC data line, unless the input has one (FreeCAD writes it
    only for a frequency analysis). A study has one solid material."""
    if re.search(r"^\*DENSITY", text, flags=re.M | re.I):
        return text
    return re.sub(r"(^\*ELASTIC[^\n]*\n[^\n]*\n)", lambda m: m.group(1) + f"*DENSITY\n{density:.13G}\n", text,
                  flags=re.M | re.I)


def patch_input(analysis, inp_path):
    """Adds what FreeCAD's writer cannot write: for a prestressed modal study the density and the
    perturbation frequency step after its static step. Returns True when the file changed."""
    if study_kind(analysis) != "modal" or not bool(_get(analysis, "Prestressed", False)):
        return False
    density = _density_t_mm3(analysis)
    if not density:
        raise EigenError("the material has no density: a modal study needs the mass")
    with open(inp_path, encoding="utf-8") as handle:
        text = handle.read()
    text = add_density(text, density)
    text = text.rstrip("\n") + "\n" + perturbation_step(requested_modes(analysis), _get(analysis, "FrequencyMin", 0.0) or 0.0,
                                                         _get(analysis, "FrequencyMax", 0.0) or 0.0)
    with open(inp_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


# ----- reading CalculiX's output -----------------------------------------------------------------------

_HEADER = re.compile(r"\n[ \t]*(?:[A-Z] ){3,}")
_FLOAT = r"[-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?"


def _block(text, header, last=True):
    """The text after a spaced-capitals header (its last occurrence) up to the next such header."""
    at = text.rfind(header) if last else text.find(header)
    if at < 0:
        return ""
    body = text[at + len(header):]
    nxt = _HEADER.search(body)
    return body[:nxt.start()] if nxt else body


def _rows(block, width):
    """(index, [floats]) of the rows 'n  v1 v2 ...' with at least `width` numbers."""
    rows = []
    for line in block.split("\n"):
        parts = line.split()
        if len(parts) >= width + 1 and parts[0].isdigit():
            try:
                rows.append((int(parts[0]), [float(p.replace("D", "E")) for p in parts[1:width + 1]]))
            except ValueError:
                continue
    return rows


def read_dat(path):
    """The eigenvalue tables of a CalculiX .dat file (the last step's):

    {"eigen": [(mode, eigenvalue, frequency Hz)], "mass": {mode: [x, y, z, rx, ry, rz]},
     "total": [x, y, z, rx, ry, rz] or None, "buckling": [(mode, factor)]}.
    """
    with open(path, encoding="latin-1") as handle:
        text = handle.read()
    out = {"eigen": [], "mass": {}, "total": None, "buckling": []}
    eigen = _block(text, "E I G E N V A L U E   O U T P U T")
    out["eigen"] = [(n, v[0], v[2]) for n, v in _rows(eigen, 4)]
    out["mass"] = {n: v for n, v in _rows(_block(text, "E F F E C T I V E   M O D A L   M A S S"), 6)}
    total = _block(text, "T O T A L   E F F E C T I V E   M A S S")
    numbers = [float(x) for x in re.findall(_FLOAT, total)]
    out["total"] = numbers[:6] if len(numbers) >= 6 else None
    out["buckling"] = [(n, v[0]) for n, v in _rows(_block(text, "B U C K L I N G   F A C T O R   O U T P U T"), 1)]
    return out


def read_frd_sets(path, fields=("DISP",)):
    """The result sets of a CalculiX .frd file in order: [{"set": n, "value": step value (time, frequency or
    buckling factor), "modal": bool, "disp": {node: (x, y, z)}}]. Fixed columns: a negative value runs into
    the set number ("102-1.24311E+05")."""
    sets, current, block = [], None, None
    with open(path, encoding="latin-1") as handle:
        for line in handle:
            if line.startswith("  100CL"):
                number, value = int(line[7:12]), float(line[12:24])
                if current is None or current["set"] != number:
                    current = {"set": number, "value": value, "modal": "MODAL" in line}
                    sets.append(current)
                block = None
            elif line.startswith(" -4"):
                name = line[5:13].strip()
                block = name.lower() if name in fields else None
                if block and current is not None:
                    current[block] = {}
            elif block and line.startswith(" -1") and current is not None:
                current[block][int(line[3:13])] = tuple(float(line[13 + 12 * i:25 + 12 * i]) for i in range(3))
            elif line.startswith(" -3"):
                block = None
    return sets


def read_monitor(path, nset="BWMON"):
    """(time, (ux, uy, uz)) of every increment's *NODE PRINT of a one-node set in a .dat file."""
    with open(path, encoding="latin-1") as handle:
        text = handle.read()
    pattern = re.compile(rf"displacements \(vx,vy,vz\) for set {nset} and time\s+({_FLOAT})\s*\n\s*\n"
                         rf"\s*\d+\s+({_FLOAT})\s+({_FLOAT})\s+({_FLOAT})", re.I)
    return [(float(t), (float(x), float(y), float(z))) for t, x, y, z in pattern.findall(text)]


# ----- results ----------------------------------------------------------------------------------------


def _result_props(result):
    props = result.PropertiesList
    if "ModeNumber" not in props:
        result.addProperty("App::PropertyInteger", "ModeNumber", "Mode", "[BW] CalculiX's mode number")
    if "ModeFrequency" not in props:
        result.addProperty("App::PropertyFloat", "ModeFrequency", "Mode", "[BW] The mode's frequency (Hz)")
        result.addProperty("App::PropertyFloatList", "ModalMass", "Mode",
                           "[BW] Effective modal mass in X, Y, Z (% of the total effective mass)")
    if "LoadFactor" not in props:
        result.addProperty("App::PropertyFloat", "LoadFactor", "Mode", "[BW] The buckling load factor")


def mode_results(analysis):
    """The study's mode results (modal: the eigenmodes; buckling: the buckled shapes), in mode order."""
    found = [r for r in _members(analysis)["results"] if int(getattr(r, "Eigenmode", 0) or 0) > 0]
    return sorted(found, key=lambda r: int(r.Eigenmode))


def prestress_result(analysis):
    """The static step's result of a prestressed modal study, or None."""
    for r in _members(analysis)["results"]:
        if int(getattr(r, "Eigenmode", 0) or 0) == 0 and "Prestress" in r.Label:
            return r
    return None


def _base(fea):
    return os.path.splitext(fea.inp_file_name)[0]


def finish_modal(analysis, fea):
    """Loads a modal run (FreeCAD's FRD import), then sets each mode's frequency, ccx mode number and
    effective modal mass from the .dat file. Returns the mode results."""
    import bruceworx_sim as sim

    with sim._no_post_pipeline():
        fea.load_results()
    dat = _base(fea) + ".dat"
    data = read_dat(dat) if os.path.isfile(dat) else {"eigen": [], "mass": {}, "total": None}
    total = data.get("total") or []
    results = mode_results(analysis)
    for index, result in enumerate(results):
        _result_props(result)
        if index < len(data["eigen"]):
            number, _eig, frequency = data["eigen"][index]
        else:
            number, frequency = int(result.Eigenmode), float(getattr(result, "EigenmodeFrequency", 0.0) or 0.0)
        result.ModeNumber = int(number)
        result.ModeFrequency = float(frequency)
        try:
            result.EigenmodeFrequency = float(frequency)  # FreeCAD's .dat match is off by the dropped rigid modes
        except Exception:
            pass
        mass = data["mass"].get(number)
        if mass and len(total) >= 3:
            result.ModalMass = [100.0 * m / t if t else 0.0 for m, t in zip(mass[:3], total[:3])]
        else:
            result.ModalMass = []
        result.Label = f"Mode {index + 1}: {frequency:.4g} Hz"
    for result in _members(analysis)["results"]:
        if int(getattr(result, "Eigenmode", 0) or 0) == 0:
            result.Label = "Prestress (static step)"
    if not results:
        raise EigenError("CalculiX found no modes (see the Report view)")
    return results


def finish_buckling(analysis, fea):
    """Reads a buckling run: the buckled shapes from the .frd (its first set is the reference state) and
    the load factors from the .dat; one result object per mode. Returns the mode results."""
    import FreeCAD
    import ObjectsFem
    from femresult import resulttools
    from feminout import importCcxFrdResults, importToolsFem

    frd = _base(fea) + ".frd"
    dat = _base(fea) + ".dat"
    if not os.path.isfile(frd):
        raise EigenError("CalculiX wrote no .frd file (see the Report view)")
    factors = read_dat(dat)["buckling"] if os.path.isfile(dat) else []
    sets = [s for s in read_frd_sets(frd) if "disp" in s]
    modes = sets[1:] if len(sets) == len(factors) + 1 or (sets and sets[0]["value"] == 0.0) else sets
    if not modes:
        raise EigenError("CalculiX found no buckling modes (see the Report view)")
    mesh = importToolsFem.make_femmesh(importCcxFrdResults.read_frd_result(frd))
    doc = analysis.Document
    numbers = None
    results = []
    for index, data in enumerate(modes):
        number, factor = factors[index] if index < len(factors) else (index + 1, data["value"])
        name = f"CCX_BucklingMode_{index + 1}_Results"
        result = ObjectsFem.makeResultMechanical(doc, name)
        result_mesh = ObjectsFem.makeMeshResult(doc, name + "_Mesh")
        result_mesh.FemMesh = mesh
        result.Mesh = result_mesh
        disp = {node: FreeCAD.Vector(*vec) for node, vec in data["disp"].items()}
        importToolsFem.fill_femresult_mechanical(result, {"disp": disp, "number": index + 1, "time": factor})
        analysis.addObject(result)
        if numbers is None:
            result = resulttools.compact_result(result)
            numbers = list(result.NodeNumbers)
        else:
            result.NodeNumbers = numbers
        resulttools.add_disp_apps(result)
        _result_props(result)
        result.ModeNumber = int(number)
        result.LoadFactor = float(factor)
        result.Label = f"Buckling mode {index + 1}: λ = {factor:.4g}"
        results.append(result)
    try:
        fea.load_results_ccxdat()  # the .dat text in the study, as for a static run
    except Exception:
        pass
    doc.recompute()
    return results


def finish(analysis, fea):
    kind = study_kind(analysis)
    if kind == "modal":
        return finish_modal(analysis, fea)
    if kind == "buckling":
        return finish_buckling(analysis, fea)
    raise EigenError(f"not an eigenvalue study: {kind}")


# ----- the tables -----------------------------------------------------------------------------------------


def excitation_bands(analysis):
    """[(label, f_low, f_high)] of the study's excitation check; [] when it is off."""
    if study_kind(analysis) != "modal" or not bool(_get(analysis, "ExcitationCheck", False)):
        return []
    source = str(_get(analysis, "ExcitationSource", EXCITATION_SOURCES[0]))
    if source == "FrequencyBand":
        lo, hi = sorted((float(_get(analysis, "BandMin", 0.0) or 0.0), float(_get(analysis, "BandMax", 0.0) or 0.0)))
        return [("band", lo, hi)] if hi > 0 else []
    lo, hi = sorted((float(_get(analysis, "MotorRpmMin", 0.0) or 0.0), float(_get(analysis, "MotorRpmMax", 0.0) or 0.0)))
    blades = max(1, int(_get(analysis, "BladeCount", 2) or 1))
    if hi <= 0:
        return []
    return [(f"blade pass ({blades} blades)", lo / 60.0 * blades, hi / 60.0 * blades), ("1x rpm", lo / 60.0, hi / 60.0)]


def band_of(frequency, bands):
    """The label of the first band a frequency lies in, or ''."""
    for label, lo, hi in bands:
        if lo <= frequency <= hi:
            return label
    return ""


def blf_status(factor, target):
    """'red' below 1, 'amber' from 1 to the target, 'green' at or above it; 'grey' for a negative factor."""
    if factor is None or factor <= 0:
        return "grey"
    if factor < 1.0:
        return "red"
    if factor < target:
        return "amber"
    return "green"


def target_factor(analysis):
    value = float(_get(analysis, "TargetLoadFactor", 0.0) or 0.0)
    return value if value > 0 else default_target()


def modal_rows(analysis):
    """[{"index", "mode", "frequency", "mass": [x, y, z] %, "rigid", "band", "result"}] in order."""
    bands = excitation_bands(analysis)
    rows = []
    for index, result in enumerate(mode_results(analysis)):
        frequency = float(getattr(result, "ModeFrequency", 0.0) or getattr(result, "EigenmodeFrequency", 0.0) or 0.0)
        rows.append({"index": index + 1, "mode": int(getattr(result, "ModeNumber", index + 1) or index + 1),
                     "frequency": frequency, "mass": list(getattr(result, "ModalMass", []) or []),
                     "rigid": frequency < RIGID_HZ, "band": "" if frequency < RIGID_HZ else band_of(frequency, bands),
                     "result": result})
    return rows


def rigid_count(analysis, rows=None):
    """Rigid-body modes of a free-free run: the ones below 1 Hz in the table and the ones CalculiX dropped
    for a slightly negative eigenvalue (its mode numbers then start above 1)."""
    rows = modal_rows(analysis) if rows is None else rows
    if not rows:
        return 0
    dropped = max(0, min(r["mode"] for r in rows) - 1)
    return dropped + sum(1 for r in rows if r["rigid"])


def buckling_rows(analysis):
    """[{"index", "mode", "factor", "status", "first", "result"}]: "first" marks lambda_1, the lowest
    positive factor (a negative factor is listed but never the first)."""
    target = target_factor(analysis)
    rows = []
    for index, result in enumerate(mode_results(analysis)):
        factor = float(getattr(result, "LoadFactor", 0.0) or 0.0)
        rows.append({"index": index + 1, "mode": int(getattr(result, "ModeNumber", index + 1) or index + 1),
                     "factor": factor, "status": blf_status(factor, target), "first": False, "result": result})
    positive = [r for r in rows if r["factor"] > 0]
    if positive:
        min(positive, key=lambda r: r["factor"])["first"] = True
    return rows


def first_factor(analysis):
    """lambda_1 (the lowest positive load factor) or None."""
    for row in buckling_rows(analysis):
        if row["first"]:
            return row["factor"]
    return None


def first_buckling_result(analysis):
    for row in buckling_rows(analysis):
        if row["first"]:
            return row["result"]
    return None


# ----- playback ---------------------------------------------------------------------------------------------


def mode_scale(analysis, result, fraction=None):
    """The displacement factor that makes a mode's largest displacement `fraction` (the study's ModeScale,
    10 % by default) of the model's bounding-box diagonal."""
    fraction = float(fraction if fraction is not None else (_get(analysis, "ModeScale", DEFAULT_MODE_SCALE) or DEFAULT_MODE_SCALE))
    lengths = list(getattr(result, "DisplacementLengths", []) or [])
    peak = max(lengths) if lengths else 0.0
    shape = model_shape(analysis)
    if shape is None:
        try:
            nodes = result.Mesh.FemMesh.Nodes.values()
            xs, ys, zs = zip(*((v.x, v.y, v.z) for v in nodes))
            diag = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2)
        except Exception:
            diag = 0.0
    else:
        diag = shape.BoundBox.DiagonalLength
    return fraction * diag / peak if peak > 0 else 0.0


def phase_factor(frame, frames_per_cycle):
    """sin(2 pi t) at a frame: the mode's amplitude at that frame, -1..1."""
    return math.sin(2.0 * math.pi * frame / max(1, int(frames_per_cycle)))


# ----- nonlinear buckling -------------------------------------------------------------------------------------


def scale_loads(text, factor):
    """The input with every *CLOAD and *DLOAD magnitude times `factor` (GRAV and CENTRIF: their magnitude
    field; a pressure: the last field). Prescribed displacements are left as they are."""
    out, mode = [], None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("*"):
            if not stripped.startswith("**"):
                key = stripped.split(",")[0].strip().upper()
                mode = key if key in ("*CLOAD", "*DLOAD") else None
            out.append(line)
            continue
        if mode and stripped:
            fields = [f.strip() for f in stripped.split(",")]
            try:
                if mode == "*CLOAD":
                    fields[2] = f"{float(fields[2]) * factor:.13G}"
                else:
                    i = 2 if fields[1].upper() in ("GRAV", "CENTRIF") else len(fields) - 1
                    fields[i] = f"{float(fields[i]) * factor:.13G}"
                line = ",".join(fields)
            except (IndexError, ValueError):
                pass
        out.append(line)
    return "\n".join(out)


def offset_nodes(text, offsets):
    """The input with the nodes of its '*Node, NSET=Nall' block moved by offsets {node: (dx, dy, dz)}."""
    out, inside = [], False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("*"):
            head = stripped.upper().replace(" ", "")
            inside = head.startswith("*NODE,") and "NSET=NALL" in head
            out.append(line)
            continue
        if inside and stripped:
            fields = stripped.split(",")
            try:
                node = int(fields[0])
                if node in offsets:
                    d = offsets[node]
                    line = (f"{node}, {float(fields[1]) + d[0]:.13G}, {float(fields[2]) + d[1]:.13G}, "
                            f"{float(fields[3]) + d[2]:.13G}")
            except (IndexError, ValueError):
                pass
        out.append(line)
    return "\n".join(out)


def nonlinear_input(text, offsets, load_factor, monitor, increment=NONLINEAR_INCREMENT):
    """The linear buckling input turned into the nonlinear run: imperfect nodes, loads x load_factor,
    *STEP, NLGEOM with automatic *STATIC increments over a unit step (time t = fraction of the load),
    the monitored node printed to the .dat at every increment."""
    text = offset_nodes(text, offsets)
    text = scale_loads(text, load_factor)
    text, n = re.subn(r"^\*STEP[^\n]*$", f"*NSET, NSET=BWMON\n{int(monitor)}\n*STEP, NLGEOM, INC=1000", text,
                      count=1, flags=re.M)
    if not n:
        raise EigenError("no *STEP in the buckling input")
    text, n = re.subn(r"^\*BUCKLE[^\n]*\n[^\n]*\n", f"*STATIC\n{increment:g},1.0,1E-06,{increment:g}\n", text,
                      count=1, flags=re.M)
    if not n:
        raise EigenError("no *BUCKLE step in the buckling input")
    text = re.sub(r"^\*OUTPUT, FREQUENCY=\d+\s*\n", "", text, flags=re.M)
    text = re.sub(r"^\*EL FILE[^\n]*\n[^\n]*\n", "", text, flags=re.M)  # the curve needs the monitor only
    text = re.sub(r"^\*END STEP\s*$", "*NODE PRINT, NSET=BWMON, FREQUENCY=1\nU\n*END STEP", text, count=1, flags=re.M)
    return text


def prepare_nonlinear(analysis, linear_inp, folder=None):
    """Writes the nonlinear buckling input beside the linear one (folder 'nonlinear'); returns the run's
    description {"dir", "inp", "base", "monitor", "direction", "amplitude", "how", "lambda1", "limit"}."""
    result = first_buckling_result(analysis)
    if result is None:
        raise EigenError("no positive load factor: the nonlinear run needs mode 1 (is the load in tension only?)")
    lambda1 = float(result.LoadFactor)
    numbers = list(result.NodeNumbers)
    vectors = list(result.DisplacementVectors)
    if not numbers or len(numbers) != len(vectors):
        raise EigenError("mode 1 has no shape")
    lengths = [v.Length for v in vectors]
    peak_index = max(range(len(lengths)), key=lengths.__getitem__)
    peak = lengths[peak_index]
    if peak <= 0:
        raise EigenError("mode 1 has no displacement")
    amplitude, how = imperfection_amplitude(analysis)
    if amplitude <= 0:
        raise EigenError("no imperfection amplitude")
    offsets = {n: (v.x * amplitude / peak, v.y * amplitude / peak, v.z * amplitude / peak) for n, v in zip(numbers, vectors)}
    limit = float(_get(analysis, "NonlinearLoadLimit", NONLINEAR_LOAD_LIMIT) or NONLINEAR_LOAD_LIMIT)
    with open(linear_inp, encoding="utf-8") as handle:
        text = handle.read()
    monitor = numbers[peak_index]
    text = nonlinear_input(text, offsets, limit * lambda1, monitor)
    folder = folder or os.path.join(os.path.dirname(linear_inp), "nonlinear")
    os.makedirs(folder, exist_ok=True)
    inp = os.path.join(folder, "nonlinear.inp")
    with open(inp, "w", encoding="utf-8") as handle:
        handle.write(text)
    direction = vectors[peak_index]
    return {"dir": folder, "inp": inp, "base": "nonlinear", "monitor": int(monitor),
            "direction": (direction.x / peak, direction.y / peak, direction.z / peak),
            "amplitude": amplitude, "how": how, "lambda1": lambda1, "limit": limit}


def collapse_point(points, stopped, knee=KNEE_FRACTION):
    """(load factor, basis) of a nonlinear curve [(displacement, load factor)], from 0:

    the knee, the first point after which the tangent d(lambda)/du falls below `knee` times its initial
    slope; else, when the run stopped early (it stopped converging, or the curve was cut), its last good
    point; else (None, ...): the curve stays stiff up to the highest load of the run."""
    pts = [(0.0, 0.0)] + [(abs(u), lam) for u, lam in points]
    if len(pts) < 3:
        return (pts[-1][1] if stopped and len(pts) > 1 else None), "too few converged increments"
    slopes = []
    for (u0, l0), (u1, l1) in zip(pts, pts[1:]):
        du = u1 - u0
        slopes.append((l1 - l0) / du if du > 1e-12 else float("inf"))
    initial = next((s for s in slopes if math.isfinite(s) and s > 0), None)
    if initial is not None:
        for i, slope in enumerate(slopes):
            if slope < knee * initial:
                return pts[i][1], f"the knee: the curve's slope falls below {100 * knee:g} % of its initial slope"
    if stopped:
        return pts[-1][1], (stopped if isinstance(stopped, str)
                            else "the last converged increment: the run stopped converging there (a limit point)")
    return None, "no collapse up to the highest load of the run"


def clean_curve(points):
    """(points kept, note): a curve [(u, lambda)] cut where the run left the equilibrium path, i.e. where the
    monitored displacement changes sign or shrinks while the load grows. Under load control a nearly perfect
    strut past its bifurcation can jump to the other side and then converge back towards the straight
    (unstable) state; those increments are not a load path."""
    kept = []
    for u, lam in points:
        if kept:
            pu, pl = kept[-1]
            if (pu and u * pu < 0) or (abs(u) < abs(pu) and lam > pl):
                return kept, (f"the curve stops at load factor {pl:.4g}: past it the run left the equilibrium path "
                              "(the monitored displacement turned back)")
        kept.append((u, lam))
    return kept, ""


def finish_nonlinear(analysis, info, returncode=0):
    """Reads the nonlinear run's curve and stores it on the mode-1 result: NonlinearLoadFactors,
    NonlinearDisplacements (mm, the monitored node along mode 1), CollapseLoadFactor (0 = none),
    CollapseBasis, MonitorNode, Imperfection (mm), ImperfectionBasis. Returns a dict of the same."""
    result = first_buckling_result(analysis)
    if result is None:
        raise EigenError("the linear buckling result is gone")
    dat = os.path.join(info["dir"], info["base"] + ".dat")
    points = []
    if os.path.isfile(dat):
        d = info["direction"]
        scale = info["limit"] * info["lambda1"]
        for t, u in read_monitor(dat):
            points.append((u[0] * d[0] + u[1] * d[1] + u[2] * d[2], t * scale))
    total = info["limit"] * info["lambda1"]
    finished = bool(points) and abs(points[-1][1] - total) < 1e-6 * max(1.0, total)
    diverged = bool(returncode) or not finished
    points, note = clean_curve(points)
    collapse, basis = collapse_point(points, "the last point on the equilibrium path" if note else diverged)
    if not points:
        basis = "the nonlinear run converged on no increment (see its .sta file)"
    props = result.PropertiesList
    if "NonlinearLoadFactors" not in props:
        result.addProperty("App::PropertyFloatList", "NonlinearLoadFactors", "Nonlinear", "[BW] Load factor per converged increment")
        result.addProperty("App::PropertyFloatList", "NonlinearDisplacements", "Nonlinear",
                           "[BW] Monitored displacement (mm) along mode 1 per increment")
        result.addProperty("App::PropertyFloat", "CollapseLoadFactor", "Nonlinear", "[BW] The nonlinear buckling load factor (0: none found)")
        result.addProperty("App::PropertyString", "CollapseBasis", "Nonlinear", "[BW] How the collapse load factor was found")
        result.addProperty("App::PropertyInteger", "MonitorNode", "Nonlinear", "[BW] The monitored node (mode 1's peak)")
        result.addProperty("App::PropertyFloat", "Imperfection", "Nonlinear", "[BW] Imperfection amplitude (mm) along mode 1")
        result.addProperty("App::PropertyString", "ImperfectionBasis", "Nonlinear", "[BW] Where the amplitude comes from")
        result.addProperty("App::PropertyString", "CurveNote", "Nonlinear", "[BW] Where the curve was cut, if it was")
    result.NonlinearLoadFactors = [lam for _u, lam in points]
    result.NonlinearDisplacements = [u for u, _lam in points]
    result.CollapseLoadFactor = float(collapse or 0.0)
    result.CollapseBasis = basis
    result.MonitorNode = int(info["monitor"])
    result.Imperfection = float(info["amplitude"])
    result.ImperfectionBasis = info["how"]
    result.CurveNote = note
    return nonlinear_data(result)


def nonlinear_data(result):
    """The stored nonlinear curve of a mode-1 result, or None."""
    if result is None or "NonlinearLoadFactors" not in result.PropertiesList:
        return None
    factors = list(result.NonlinearLoadFactors)
    return {"factors": factors, "displacements": list(result.NonlinearDisplacements),
            "collapse": float(result.CollapseLoadFactor) or None, "basis": result.CollapseBasis,
            "monitor": int(result.MonitorNode), "amplitude": float(result.Imperfection),
            "how": result.ImperfectionBasis, "max": max(factors) if factors else None,
            "note": getattr(result, "CurveNote", "") or ""}


def run_ccx(folder, base, timeout=3600):
    """Runs ccx -i base in folder (blocking); returns (returncode, stdout tail)."""
    import subprocess

    import bruceworx_sim as sim

    ccx = sim.find_ccx()
    if not ccx:
        raise EigenError("CalculiX (ccx) not found")
    run = subprocess.run([ccx, "-i", base], cwd=folder, capture_output=True, text=True, timeout=timeout)
    return run.returncode, (run.stdout or "")[-600:]


def solve_nonlinear(analysis, fea, progress=None):
    """The whole nonlinear buckling run after a linear one (blocking); returns nonlinear_data."""
    info = prepare_nonlinear(analysis, fea.inp_file_name)
    if progress:
        progress(f"Nonlinear buckling: imperfection {info['amplitude']:.3g} mm, load to {info['limit']:g} × λ1…")
    code, _tail = run_ccx(info["dir"], info["base"])
    return finish_nonlinear(analysis, info, code)


# ----- the summary and the report ------------------------------------------------------------------------------


def summary_part(analysis):
    """What the panel and the report show of a modal or buckling study."""
    kind = study_kind(analysis)
    out = {"kind": kind, "count": int(_get(analysis, "ModeCount", 0) or 0), "scale": float(_get(analysis, "ModeScale", DEFAULT_MODE_SCALE) or DEFAULT_MODE_SCALE)}
    if kind == "modal":
        rows = modal_rows(analysis)
        out.update({
            "rows": [{k: v for k, v in r.items() if k != "result"} for r in rows],
            "free": free_free(analysis), "rigid": rigid_count(analysis, rows),
            "range": (float(_get(analysis, "FrequencyMin", 0.0) or 0.0), float(_get(analysis, "FrequencyMax", 0.0) or 0.0)),
            "prestressed": bool(_get(analysis, "Prestressed", False)),
            "excitation": bool(_get(analysis, "ExcitationCheck", False)),
            "source": str(_get(analysis, "ExcitationSource", "")),
            "bands": excitation_bands(analysis),
            "rpm": (float(_get(analysis, "MotorRpmMin", 0.0) or 0.0), float(_get(analysis, "MotorRpmMax", 0.0) or 0.0)),
            "blades": int(_get(analysis, "BladeCount", 0) or 0),
        })
        out["excited"] = [r["index"] for r in out["rows"] if r["band"]]
    elif kind == "buckling":
        rows = buckling_rows(analysis)
        out.update({
            "rows": [{k: v for k, v in r.items() if k != "result"} for r in rows],
            "target": target_factor(analysis),
            "first": first_factor(analysis),
            "nonlinear": bool(_get(analysis, "Nonlinear", False)),
            "curve": nonlinear_data(first_buckling_result(analysis)),
            "limit": float(_get(analysis, "NonlinearLoadLimit", NONLINEAR_LOAD_LIMIT) or NONLINEAR_LOAD_LIMIT),
        })
        amplitude, how = imperfection_amplitude(analysis) if out["nonlinear"] else (None, "")
        out["amplitude"], out["how"] = amplitude, how
    return out


def figure_rows(part):
    """[(kind, row)] of the modes a report renders: the first three elastic modes (modal), the first two
    buckled shapes, positive factors first (buckling)."""
    rows = [r for r in part.get("rows") or [] if not r.get("rigid")]
    if part["kind"] == "modal":
        return [(f"mode{n}", row) for n, row in enumerate(rows[:3], 1)]
    ordered = [r for r in rows if r["factor"] > 0] + [r for r in rows if r["factor"] <= 0]
    return [(f"mode{n}", row) for n, row in enumerate(ordered[:2], 1)]


def figures_for(part):
    """[(kind, caption)] of the renders a report shows: the mesh and the first three modes (modal), the
    first two buckled shapes (buckling)."""
    figures = [("mesh", "The mesh and the supports")] if part["kind"] == "modal" else []
    for kind, row in figure_rows(part):
        if part["kind"] == "modal":
            figures.append((kind, f"Mode {row['index']}: {row['frequency']:.4g} Hz (shape scaled; colour: displacement)"))
        else:
            figures.append((kind, f"Buckled shape {row['index']}: load factor {row['factor']:.4g} (scaled)"))
    return figures


def _status_tex(status):
    return {"red": "bandRed", "amber": "bandAmber", "green": "bandGreen", "grey": "black!45"}[status]


def report_preamble(part):
    """Extra preamble lines (colours; pgfplots when there is a nonlinear curve)."""
    lines = [rf"\definecolor{{band{name.capitalize()}}}{{HTML}}{{{STATUS_COLORS[name].lstrip('#')}}}"
             for name in ("red", "amber", "green")]
    if part.get("curve") and part["curve"]["factors"]:
        lines += [r"\usepackage{pgfplots}", r"\pgfplotsset{compat=1.16}"]
    return lines


def _fmt(value, digits=4):
    if value is None:
        return "--"
    text = f"{value:.{digits}g}"
    if "e+" in text:
        text = f"{value:.0f}"
    return text


def report_results(part, escape):
    """The LaTeX of the results of a modal or buckling study."""
    e = escape
    lines = []
    if part["kind"] == "modal":
        lines += ["", r"\section*{Modal frequencies}"]
        if part["prestressed"]:
            lines.append(r"Prestressed: a static step with the loads above, then the modes about that state "
                         r"(CalculiX \texttt{*STEP, PERTURBATION}): the stress stiffening is in the frequencies.\par")
        else:
            lines.append(r"Free vibration $K\phi=\omega^2M\phi$, $f=\omega/2\pi$; the loads do not change the frequencies.\par")
        if part["free"]:
            lines.append(rf"Free-free (no supports): {part['rigid']} rigid-body modes below {RIGID_HZ:g}\,Hz were found "
                         r"and are left out of the table.\par")
        lo, hi = part["range"]
        if lo or hi:
            lines.append(rf"Searched from {_fmt(lo)} to {_fmt(hi) if hi else 'no limit'}\,Hz.\par")
        rows = [r for r in part["rows"] if not r["rigid"]]
        if rows:
            lines += [r"\begin{tabular}{@{}rrrrrl@{}}", r"\toprule Mode & $f$ (Hz) & $m_X$ (\%) & $m_Y$ (\%) & $m_Z$ (\%) & \\ \midrule"]
            for r in rows:
                mass = r["mass"] + [None] * (3 - len(r["mass"]))
                note = ""
                cell = _fmt(r["frequency"], 5)
                if r["band"]:
                    note = rf"\textcolor{{bandAmber}}{{\textbf{{in the {e(r['band'])} band}}}}"
                    cell = rf"\colorbox{{bandAmber!35}}{{{cell}}}"
                lines.append(rf"{r['index']} & {cell} & {_fmt(mass[0], 3)} & {_fmt(mass[1], 3)} & {_fmt(mass[2], 3)} & {note}\\")
            lines += [r"\bottomrule", r"\end{tabular}",
                      r"\par{\small Effective modal mass: the share of the total effective mass (CalculiX's .dat) each mode "
                      r"moves in a direction; a mode with a large share is excited strongly by a base motion that way.}"]
        else:
            lines.append("Not solved yet.")
        lines += ["", r"\section*{Excitation check}"]
        if not part["excitation"]:
            lines.append("Off (no excitation band set on the study).")
        else:
            if part["source"] == "MotorSpeed":
                rpm = part["rpm"]
                lines.append(rf"Motor speed {_fmt(rpm[0])}--{_fmt(rpm[1])}\,rpm, {part['blades']} blades.\par")
            lines.append(r"\begin{tabular}{@{}ll@{}}")
            for label, lo, hi in part["bands"]:
                lines.append(rf"{e(label.capitalize())} & {_fmt(lo)}--{_fmt(hi)}\,Hz\\")
            lines.append(r"\end{tabular}\par")
            if part["excited"]:
                lines.append(r"\textcolor{bandAmber}{\textbf{Modes inside a band: "
                             + ", ".join(str(i) for i in part["excited"]) + r".}} Resonance is likely; stiffen or detune the part.")
            else:
                lines.append(r"\textcolor{bandGreen}{\textbf{No mode inside a band.}}")
    else:
        target = part["target"]
        lines += ["", r"\section*{Buckling load factors}",
                  r"Linear (eigenvalue) buckling $\left(K+\lambda K_G(\sigma_0)\right)\psi=0$ under the study's loads as the "
                  r"reference load: the critical load is $\lambda$ times it. A negative $\lambda$ is buckling under the "
                  r"reversed load; it is listed but not counted as the first.\par"]
        rows = part["rows"]
        if rows:
            lines += [r"\begin{tabular}{@{}rrl@{}}", r"\toprule Mode & $\lambda$ & \\ \midrule"]
            for r in rows:
                note = r"\textbf{$\lambda_1$}" if r["first"] else ("reversed load" if r["factor"] <= 0 else "")
                lines.append(rf"{r['index']} & \textcolor{{{_status_tex(r['status'])}}}{{\textbf{{{_fmt(r['factor'], 5)}}}}} & {note}\\")
            lines += [r"\bottomrule", r"\end{tabular}\par"]
            first = part["first"]
            if first is None:
                lines.append(r"\textcolor{bandRed}{\textbf{No positive load factor}}: the load puts the part in tension "
                             r"only; it does not buckle under it (reverse the load to check the other way).")
            else:
                status = blf_status(first, target)
                verdict = {"red": "below 1: the part buckles under the load",
                           "amber": f"between 1 and the target {target:g}",
                           "green": f"at or above the target {target:g}"}[status]
                lines.append(rf"$\lambda_1=$ \textcolor{{{_status_tex(status)}}}{{\textbf{{{_fmt(first, 4)}}}}}: {e(verdict)} "
                             rf"(target {target:g}: red below 1, amber from 1 to {target:g}, green above).")
        else:
            lines.append("Not solved yet.")
        lines += ["", r"\medskip\noindent\fbox{\parbox{0.96\linewidth}{\small\textbf{Note.} " + e(NONCONSERVATIVE_NOTE)
                  .replace("lambda\\_1", r"$\lambda_1$") + "}}"]
        curve = part.get("curve")
        lines += ["", r"\section*{Nonlinear buckling}"]
        if not part["nonlinear"]:
            lines.append("Not run (the study's Nonlinear option is off).")
        elif not curve:
            lines.append("Not run yet.")
        else:
            lines.append(rf"Geometrically nonlinear static run (NLGEOM) on the mesh with an imperfection of "
                         rf"{_fmt(curve['amplitude'], 3)}\,mm along mode 1 ({e(curve['how'])}); the load stepped by "
                         rf"CalculiX's automatic increments up to {part['limit']:g}\,$\lambda_1$. Monitored: node {curve['monitor']}, "
                         r"where mode 1 peaks, along mode 1.\par")
            if curve["collapse"]:
                ratio = curve["collapse"] / part["first"] if part["first"] else None
                lines.append(rf"\textbf{{Collapse load factor {_fmt(curve['collapse'], 4)}}}"
                             + (rf" ({100 * ratio:.0f}\,\% of the linear $\lambda_1$)" if ratio else "")
                             + rf": {e(curve['basis'])}.\par")
            else:
                lines.append(rf"No collapse found: {e(curve['basis'])}.\par")
            if curve.get("note"):
                lines.append(rf"{{\small Note: {e(curve['note'])}.}}\par")
            if curve["factors"]:
                coords = " ".join(f"({u:.6g},{lam:.6g})" for u, lam in zip([0.0] + curve["displacements"], [0.0] + curve["factors"]))
                lines += [r"\begin{center}\begin{tikzpicture}", r"\begin{axis}[width=0.78\linewidth,height=0.42\linewidth,"
                          r"xlabel={Monitored displacement (mm)},ylabel={Load factor $\lambda$},grid=major,"
                          r"legend pos=south east,legend style={font=\small}]",
                          rf"\addplot[thick,color=black,mark=*,mark size=0.8pt] coordinates {{{coords}}};",
                          r"\addlegendentry{nonlinear run}"]
                if part["first"]:
                    lines += [rf"\addplot[dashed,color=bandRed,domain=0:{max([abs(u) for u in curve['displacements']] + [1e-9]):.6g}] {{{part['first']:.6g}}};",
                              r"\addlegendentry{linear $\lambda_1$}"]
                if curve["collapse"]:
                    lines += [rf"\addplot[only marks,mark=o,mark size=3pt,color=bandAmber,thick] coordinates "
                              rf"{{({_collapse_u(curve):.6g},{curve['collapse']:.6g})}};", r"\addlegendentry{collapse}"]
                lines += [r"\end{axis}", r"\end{tikzpicture}\end{center}"]
    return lines


def _collapse_u(curve):
    for u, lam in zip(curve["displacements"], curve["factors"]):
        if abs(lam - curve["collapse"]) < 1e-12 * max(1.0, abs(lam)):
            return u
    return 0.0


def report_footer(part):
    if part["kind"] == "modal":
        return (r"Linear modal analysis: $K\phi=\omega^2M\phi$ with the consistent mass matrix, undamped; CalculiX "
                r"\texttt{*FREQUENCY} (Lanczos/ARPACK).")
    return (r"Linear buckling: $(K+\lambda K_G)\psi=0$, CalculiX \texttt{*BUCKLE}; the nonlinear option is a "
            r"\texttt{*STATIC, NLGEOM} run with automatic load increments.")


# ----- benchmarks (SIM-05) ----------------------------------------------------------------------------------------

STEEL = {"uuid": "", "name": "Steel (benchmark)", "library": "", "E": 210000.0, "nu": 0.3, "yield": 355.0,
         "uts": None, "density": 7850.0}
CANTILEVER_BETA_L = (1.8751, 4.6941, 7.8548)  # clamped-free
FREE_BETA_L = 4.7300  # free-free, first elastic mode


def euler_bernoulli(beta_l, length, E, density, width, height):
    """f (Hz) of a bending mode: (beta L)^2 / (2 pi L^2) sqrt(E I / rho A), I about the `height` axis (mm, MPa, kg/m^3)."""
    rho = density * 1e-12  # t/mm^3
    radius_sq = height ** 2 / 12.0  # I / A
    return beta_l ** 2 / (2 * math.pi * length ** 2) * math.sqrt(E * radius_sq / rho)


def timoshenko(beta_l, length, E, nu, density, width, height):
    """The Euler-Bernoulli frequency lowered for shear and rotary inertia (the Rayleigh-Timoshenko first-order
    correction): f_T = f_EB / sqrt(1 + (beta r)^2 (1 + E / (kappa G))), r the radius of gyration, kappa the
    rectangle's shear coefficient 10 (1 + nu) / (12 + 11 nu) (Cowper). Within about 1 % of the exact Timoshenko
    values for L / h of 5 and above."""
    kappa = 10.0 * (1.0 + nu) / (12.0 + 11.0 * nu)
    G = E / (2.0 * (1.0 + nu))
    beta_r_sq = (beta_l / length) ** 2 * height ** 2 / 12.0
    return euler_bernoulli(beta_l, length, E, density, width, height) / math.sqrt(1.0 + beta_r_sq * (1.0 + E / (kappa * G)))


def euler_column(length, width, E, load):
    """lambda_1 of a clamped-free square column: P_cr / P = pi^2 E I / (2 L)^2 / P."""
    inertia = width ** 4 / 12.0
    return math.pi ** 2 * E * inertia / (2.0 * length) ** 2 / load


def tensioned_frequency(n, length, width, E, density, tension):
    """f_n (Hz) of a pinned-pinned beam under axial tension T: (n / 2L) sqrt(T / rho A + E I (n pi)^2 / (rho A L^2))."""
    rho_a = density * 1e-12 * width * width  # t/mm
    inertia = width ** 4 / 12.0
    return n / (2.0 * length) * math.sqrt(tension / rho_a + E * inertia * (n * math.pi) ** 2 / (rho_a * length ** 2))


def _bar(doc, name, length, width, max_size, study_type, options):
    import bruceworx_sim as sim

    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = length, width, width
    doc.recompute()
    plan = sim.StudyPlan(name=name, model=box, material=dict(STEEL), max_size=max_size, study_type=study_type,
                         options=options)
    return box, sim.new_study(doc, plan)


def free_bar(doc=None, length=200.0, width=10.0, max_size=5.0, options=None):
    """A free-free steel bar (no supports), a modal study. Returns (doc, objs, theory {"f1"})."""
    import FreeCAD

    doc = doc or FreeCAD.newDocument("FreeBar")
    _box, objs = _bar(doc, "Bar", length, width, max_size, "modal", options)
    theory = {"f1": euler_bernoulli(FREE_BETA_L, length, STEEL["E"], STEEL["density"], width, width),
              "f1_timoshenko": timoshenko(FREE_BETA_L, length, STEEL["E"], STEEL["nu"], STEEL["density"], width, width)}
    return doc, objs, theory


def column(doc=None, length=500.0, width=10.0, load=1000.0, max_size=5.0, options=None):
    """The Euler column: clamped at x=0, a compressive tip load on the far face. A buckling study.
    Returns (doc, objs, theory {"lambda1", "ratio"}): the second factor in the same plane is (3pi/2)^2/(pi/2)^2 = 9
    times the first; a square section has each mode twice (both planes)."""
    import FreeCAD
    import ObjectsFem

    doc = doc or FreeCAD.newDocument("Column")
    box, objs = _bar(doc, "Column", length, width, max_size, "buckling", options)
    analysis = objs["analysis"]
    fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
    fixed.References = [(box, "Face1")]
    analysis.addObject(fixed)
    force = ObjectsFem.makeConstraintForce(doc, "TipLoad")
    force.References = [(box, "Face2")]
    force.Force = f"{load} N"
    force.Direction = (box, ["Edge9"])  # along x
    force.Reversed = True  # towards the clamp: compression
    analysis.addObject(force)
    doc.recompute()
    return doc, objs, {"lambda1": euler_column(length, width, STEEL["E"], load), "ratio": 9.0}


def tensioned_beam(doc=None, length=500.0, width=10.0, tension=10000.0, max_size=5.0, options=None):
    """A pinned-pinned steel beam under axial tension T, a prestressed modal study: both end faces held in y
    and z (free to turn about y and z), x held at one corner; T pulls the two end faces apart.
    Returns (doc, objs, theory {"f1", "f2", "f1_free"})."""
    import FreeCAD
    import ObjectsFem

    doc = doc or FreeCAD.newDocument("Tensioned")
    opts = {"Prestressed": True, "ModeCount": 4}
    opts.update(options or {})
    box, objs = _bar(doc, "Beam", length, width, max_size, "modal", opts)
    analysis = objs["analysis"]
    for face in ("Face1", "Face2"):
        pin = ObjectsFem.makeConstraintDisplacement(doc, f"Pin{face}")
        pin.References = [(box, face)]
        pin.xFree, pin.yFree, pin.zFree = True, False, False
        pin.yDisplacement = pin.zDisplacement = 0
        analysis.addObject(pin)
    axial = ObjectsFem.makeConstraintDisplacement(doc, "Axial")
    axial.References = [(box, "Vertex1")]
    axial.xFree = False
    axial.xDisplacement = 0
    analysis.addObject(axial)
    for face, reverse in (("Face1", True), ("Face2", False)):
        pull = ObjectsFem.makeConstraintForce(doc, f"Tension{face}")
        pull.References = [(box, face)]
        pull.Force = f"{tension} N"
        pull.Direction = (box, ["Edge9"])
        pull.Reversed = reverse
        analysis.addObject(pull)
    doc.recompute()
    theory = {"f1": tensioned_frequency(1, length, width, STEEL["E"], STEEL["density"], tension),
              "f2": tensioned_frequency(2, length, width, STEEL["E"], STEEL["density"], tension),
              "f1_free": tensioned_frequency(1, length, width, STEEL["E"], STEEL["density"], 0.0)}
    return doc, objs, theory
