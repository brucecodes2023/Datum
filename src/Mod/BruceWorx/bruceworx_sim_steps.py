# SPDX-License-Identifier: LGPL-2.1-or-later
"""Study steps (roadmap SIM-07 phase P2, plan answer W5): a study is an ordered list of steps.

The steps a study can hold today, and the CalculiX input each combination writes (``*STEP`` blocks):

    Stationary                          *STATIC                                       (StudyType static)
    Eigenfrequency                      *FREQUENCY                                    (modal)
    Stationary, Eigenfrequency          *STATIC, then *STEP, PERTURBATION *FREQUENCY  (modal, Prestressed:
                                        the modes about the loaded state, stress stiffening included)
    Linear buckling                     *BUCKLE                                       (buckling)
    Heat transfer                       *HEAT TRANSFER                                (thermal; steady or transient)
    Heat transfer, Stationary           *HEAT TRANSFER, STEADY STATE, then *STATIC with the temperatures of the
                                        first step as the load (thermal_stress, written by ``two_step_input``)

``steps(study)`` derives the list from the study type, so every study made before today has its steps and opens
unchanged: a "Thermal stress" study is the steps Heat transfer + Stationary. ``add_step`` and ``remove_step`` change
the study type to the one the new list means (the properties it needs are added; its loads, supports, material and
mesh are kept). Time dependent and steady CFD are not combinable with other steps yet (a transient heat run is the
Heat transfer step's own "Time dependent" setting). Combinations other than the table are refused with the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import bruceworx_sim as sim

TYPES = {"stationary": "Stationary", "modal": "Eigenfrequency", "buckling": "Linear buckling", "heat": "Heat transfer"}
# step type lists -> (StudyType, prestressed)
COMBOS = {
    ("stationary",): ("static", False),
    ("modal",): ("modal", False),
    ("stationary", "modal"): ("modal", True),
    ("buckling",): ("buckling", False),
    ("heat",): ("thermal", False),
    ("heat", "stationary"): ("thermal_stress", False),
}
PRELOAD_LABEL = "Preload (stationary)"


class StepError(ValueError):
    """A step list that cannot be built; the text says why."""


@dataclass
class Step:
    key: str  # "s1", "s2"
    type: str  # stationary, modal, buckling, heat
    label: str


def types(study):
    """The study's step types in order, derived from its StudyType."""
    kind = sim.study_kind(study)
    if kind in sim.AIRFLOW_KINDS:
        return []
    if kind == "modal" and bool(getattr(study, "Prestressed", False)) and "Prestressed" in study.PropertiesList:
        return ["stationary", "modal"]
    for combo, (k, pre) in COMBOS.items():
        if k == kind and not (kind == "modal" and pre):
            return list(combo)
    return ["stationary"]


def steps(study):
    """[Step] of the study (a one step study has one)."""
    kinds = types(study)
    out = []
    for i, t in enumerate(kinds, 1):
        label = TYPES[t]
        if t == "stationary" and kinds == ["stationary", "modal"]:
            label = PRELOAD_LABEL
        out.append(Step(f"s{i}", t, label))
    return out


def additions(study):
    """[(type, label)] of the steps that can be added after the last one."""
    cur = types(study)
    return [(t, TYPES[t]) for t in TYPES if tuple(cur + [t]) in COMBOS] if cur else []


def _reason(new):
    if tuple(new) in COMBOS:
        return ""
    names = " + ".join(TYPES[t] for t in new)
    if new and new[0] not in ("stationary", "modal", "buckling", "heat"):
        return f"unknown step type {new[0]!r}"
    return (f"the steps {names} are not supported together yet. Supported: "
            + "; ".join(" + ".join(TYPES[t] for t in c) for c in COMBOS))


def set_steps(study, new_types):
    """Makes the study's step list ``new_types`` (one undo step). Raises StepError for a combination that is not supported."""
    new = list(new_types)
    if sim.study_kind(study) in sim.AIRFLOW_KINDS:
        raise StepError("an airflow study has one steady CFD step")
    why = _reason(new)
    if why:
        raise StepError(why)
    kind, prestressed = COMBOS[tuple(new)]
    doc = study.Document
    doc.openTransaction("Study steps")
    try:
        convert(study, kind, prestressed)
    except Exception as exc:
        doc.abortTransaction()
        raise StepError(str(exc)) from exc
    doc.commitTransaction()
    return steps(study)


def add_step(study, step_type):
    """Appends a step; StepError when the result is not a supported list."""
    if step_type not in TYPES:
        raise StepError(f"unknown step type {step_type!r} (stationary, modal, buckling or heat)")
    return set_steps(study, types(study) + [step_type])


def remove_step(study, key):
    """Removes a step by key ("s1"...); StepError when what is left is not supported or it is the only step."""
    current = steps(study)
    keep = [s.type for s in current if s.key != key]
    if len(keep) == len(current):
        raise StepError(f"no step {key!r}")
    if not keep:
        raise StepError("a study needs at least one step")
    return set_steps(study, keep)


def convert(study, kind, prestressed=False):
    """Changes the study's type to ``kind`` (a StudyType) keeping its members; adds the settings the type needs."""
    import ObjectsFem

    doc = study.Document
    old = sim.study_kind(study)
    study.StudyType = kind
    members = sim.members(study)
    solver = members["solver"]
    if kind in ("modal", "buckling"):
        import bruceworx_sim_eigen as eigen

        eigen.ensure_properties(study)
        if kind == "modal":
            study.Prestressed = bool(prestressed)
    if kind in ("thermal", "thermal_stress"):
        import bruceworx_sim_thermal as thermal

        thermal.ensure_properties(study)
        if kind == "thermal_stress" and bool(study.Transient):
            study.Transient = False  # a thermal stress study is steady state
        if thermal.initial_temperature_object(study) is None:
            thermal.make_initial_temperature(doc, study)
    elif old in ("thermal", "thermal_stress"):
        initial = None
        try:
            import bruceworx_sim_thermal as thermal

            initial = thermal.initial_temperature_object(study)
        except Exception:
            pass
        if initial is not None:
            doc.removeObject(initial.Name)
    if solver is not None:
        if kind == "static":
            solver.AnalysisType = "static"
            solver.GeometricalNonlinearity = "linear"
            solver.Label = "CalculiX (static)"
        elif kind in ("modal", "buckling"):
            import bruceworx_sim_eigen as eigen

            eigen.configure_solver(study, solver)
        else:
            import bruceworx_sim_thermal as thermal

            thermal.configure_solver(study, solver)


# ----- Heat transfer, then Stationary: two *STEP blocks -----------------------------------------------------------

LEGACY_SINGLE_STEP = False  # True: the old one-block *UNCOUPLED TEMPERATURE-DISPLACEMENT input (for comparisons)
_BANNER = re.compile(r"^\*{20,}\s*$")
_THERMAL_CARD = re.compile(r"^\*(FILM|DFLUX|CFLUX|RADIATE|HEAT GENERATION|DFLUX)\b", re.I)


def _thermal_block(block):
    """A block of the step body that only drives the temperature field: fixed temperatures (DOF 11), films, fluxes."""
    lines = [ln for ln in block.splitlines() if ln.strip() and not ln.startswith("**")]
    if not lines:
        return False
    if any(_THERMAL_CARD.match(ln) for ln in lines):
        return True
    data = [ln for ln in lines if not ln.startswith("*")]
    return bool(data) and all(re.search(r",\s*11\s*,\s*11\s*,", ln) for ln in data) and any(ln.upper().startswith("*BOUNDARY") for ln in lines)


def two_step_input(analysis, inp_path):
    """Rewrites the CalculiX input of a thermal stress study into two steps: ``*HEAT TRANSFER, STEADY STATE`` (the
    temperature boundary conditions, films, fluxes; NT and heat flow output) and ``*STATIC`` (supports, forces; U, S, E, NT).
    The temperatures of the first step are the thermal load of the second (CalculiX keeps the nodal temperatures
    from step to step). Returns True when the file was rewritten; False when it has no uncoupled step."""
    if sim.study_kind(analysis) != "thermal_stress" or LEGACY_SINGLE_STEP:
        return False
    with open(inp_path, encoding="utf-8") as handle:
        text = handle.read()
    m = re.search(r"^\*STEP[^\n]*\n\*UNCOUPLED TEMPERATURE-DISPLACEMENT[^\n]*\n([^\n]*)\n", text, flags=re.M)
    end = text.find("*END STEP", m.end() if m else 0)
    if m is None or end < 0:
        return False
    control = m.group(1).strip() or "1.0,1.0,1e-05,1.0"
    body = text[m.end():end]
    blocks, current = [], []
    for line in body.splitlines():
        if _BANNER.match(line) and current:
            blocks.append("\n".join(current))
            current = []
        current.append(line)
    blocks.append("\n".join(current))
    thermal, mech, prints_heat, prints_mech = [], [], [], []
    for block in blocks:
        if "*NODE FILE" in block or "*NODE PRINT" in block or "*OUTPUT" in block or "*EL FILE" in block:
            lines = block.splitlines()
            i = 0
            while i < len(lines):
                ln = lines[i]
                if ln.upper().startswith("*NODE PRINT") and i + 1 < len(lines):
                    pair = ln + "\n" + lines[i + 1]
                    (prints_heat if lines[i + 1].strip().upper() == "RFL" else prints_mech).append(pair)
                    i += 2
                    continue
                if ln.upper().startswith("*OUTPUT"):
                    prints_mech.append(ln)
                i += 1
            continue
        (thermal if _thermal_block(block) else mech).append(block)
    step1 = ("*STEP, INC=2000\n** BruceWorx step 1: Heat transfer (steady state)\n*HEAT TRANSFER, STEADY STATE\n" + control + "\n"
             + "\n".join(thermal) + "\n*NODE FILE\nNT\n*EL FILE\nHFL\n" + "\n".join(prints_heat) + "\n*END STEP\n")
    step2 = ("\n*STEP, INC=2000\n** BruceWorx step 2: Stationary stress with the temperatures of step 1\n*STATIC\n" + control + "\n"
             + "\n".join(mech) + "\n*NODE FILE\nU, NT\n*EL FILE\nS, E\n"
             + "\n".join(p for p in prints_mech if not p.startswith("*OUTPUT")) + "\n*END STEP\n")
    new = text[:m.start()] + step1 + step2 + text[end + len("*END STEP"):].lstrip("\n")
    with open(inp_path, "w", encoding="utf-8") as handle:
        handle.write(new)
    return True


def label_step_results(analysis):
    """Names the result objects after their steps: Heat transfer (temperatures only), Stationary (stress)."""
    results = [r for r in sim.members(analysis)["results"] if "Temperature" in r.PropertiesList and len(r.Temperature)]
    for r in results:
        if "DisplacementVectors" in r.PropertiesList and len(r.DisplacementVectors):
            r.Label = "Stationary: stress and displacement"
        else:
            r.Label = "Heat transfer: temperatures"
