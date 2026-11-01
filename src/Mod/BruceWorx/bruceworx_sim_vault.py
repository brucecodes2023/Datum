# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulations in the vault, the FreeCAD side (PDM-14, 1 Nov 2026).

The vault side (no FreeCAD) is bruceworx/vault_simulations.py: entry names, the summary format, "is it current".
Here, with a document open:

* ``extra_entries`` is what a save adds to the file, beside the picture and preview mesh (bruceworx_preview):
  the part's geometric fingerprint (``bruceworx/geometry.json``) for a part, assembly or bought item, and for a
  simulation also the summary (``bruceworx/simulation.json``) the Files list and the part's Simulations tab read
  without opening the file.
* ``geometry_fingerprint`` is the SIM-07 one: H("geo", geometry_settings(shape)) of the analysed shape, so a
  study's ``geo/part`` node and a part's check-in compare directly (the part's shape is the study's model
  candidate, the study's own shape is the copy it analyses).
* ``make_simulation_file`` copies a part's solid into a new document, which becomes ``<P/N>.vlsim``; the
  object ``Simulation source`` records which version of the part the copy came from and its fingerprint.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os

import bruceworx_sim as sim
from bruceworx import vault_simulations as vs

SOURCE_LABEL = "Simulation source"
SOURCE_PROP = "Source"
STUDY_LABELS = ("static", "modal", "buckling", "thermal", "thermal_stress")


# ----- the fingerprint ------------------------------------------------------------------------------


def fingerprint_of_shape(shape) -> str:
    """H("geo", geometry_settings(shape)): the SIM-07 ``geo/part`` fingerprint (bruceworx_sim_model.fingerprints)."""
    from bruceworx_sim_model import geometry_settings

    text = json.dumps(("geo", geometry_settings(shape)), sort_keys=True, default=str)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def model_shape(doc):
    """The shape of the document's first model candidate (what a new study would analyse), or None."""
    found = sim.model_candidates(doc)
    return sim.shape_of(found[0]) if found else None


def geometry_of_doc(doc) -> dict | None:
    """{"fingerprint", "settings"} of a part's document, None when it has no solid."""
    from bruceworx_sim_model import geometry_settings

    shape = model_shape(doc)
    if shape is None:
        return None
    return {"fingerprint": fingerprint_of_shape(shape), "settings": geometry_settings(shape)}


def source_object(doc):
    for obj in doc.Objects:
        if obj.Label == SOURCE_LABEL and SOURCE_PROP in obj.PropertiesList:
            return obj
    return None


def source_of(doc) -> dict:
    """What the simulation copy came from: {"part", "sha256", "fingerprint"} (empty when not recorded)."""
    obj = source_object(doc)
    return dict(getattr(obj, SOURCE_PROP, None) or {}) if obj is not None else {}


def set_source(doc, part: str, sha256: str, fingerprint: str) -> None:
    obj = source_object(doc)
    if obj is None:
        obj = doc.addObject("App::FeaturePython", "SimulationSource")
        obj.Label = SOURCE_LABEL
        obj.addProperty("App::PropertyMap", SOURCE_PROP, "Vault", "The part version this copy of the solid came from")
    setattr(obj, SOURCE_PROP, {"part": part, "sha256": sha256, "fingerprint": fingerprint})


# ----- the summary -----------------------------------------------------------------------------------


def _study(doc):
    found = sim.studies(doc)
    return found[0] if found else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summary_of(doc, path: str = "", previous: dict | None = None) -> dict:
    """The summary written into a simulation file at every save (all keys are optional for a reader)."""
    import bruceworx_sim_model as model

    out = {"format": vs.SUMMARY_FORMAT, "saved_at": _now()}
    analysis = _study(doc)
    source = source_of(doc)
    out["source"] = source
    if analysis is None:
        out.update({"study": "", "has_results": False})
        return out
    members = sim.members(analysis)
    values = sim.study_material(analysis) or {}
    out["study"] = analysis.Label.replace("Study: ", "", 1)
    out["study_type"] = getattr(analysis, "StudyType", "static") if "StudyType" in analysis.PropertiesList else "static"
    solver = members["solver"]
    out["solver"] = (solver.Label if solver is not None else "") or ""
    out["material"] = {k: values.get(k) for k in ("name", "E", "nu", "yield", "uts", "density") if values.get(k) is not None}
    if members["mesh"] is not None:
        out["mesh"] = sim.mesh_stats(members["mesh"])
    try:
        issues = sim.precheck(analysis)
        errors = [t for lvl, t in issues if lvl == "error"]
        out["precheck"] = "errors" if errors else ("warnings" if issues else "ready")
        out["precheck_text"] = (errors or [t for _l, t in issues])[:3]
    except Exception as exc:  # noqa: BLE001 -- the summary never blocks a save
        out["precheck"] = "unknown"
        out["precheck_text"] = [str(exc)]
    result = sim.latest_result(analysis)
    out["has_results"] = result is not None
    prev = previous or {}
    if result is None and prev.get("results_stripped"):  # saved without its results: the numbers it had stay
        out.update({k: v for k, v in prev.items() if k in _KEPT})
        out["results_stripped"] = True
    if result is not None:
        extrema = sim.result_extrema(result)
        for key in ("max_vm", "max_disp", "min_sf"):
            out[key] = _number(extrema.get(key))
        if extrema.get("min_sf_away") is not None:
            out["min_sf_away"] = _number(extrema["min_sf_away"])
        out["measure"] = extrema.get("measure", "vonMises")
        if extrema.get("bore"):
            out["bore"] = {k: v for k, v in extrema["bore"].items() if isinstance(v, (int, float, str))}
        if extrema.get("burst"):
            out["burst"] = {k: v for k, v in extrema["burst"].items() if isinstance(v, (int, float, str))}
        prev = previous or {}
        same = all(prev.get(k) == out.get(k) for k in ("max_vm", "max_disp", "min_sf"))
        out["solved_at"] = prev.get("solved_at") if same and prev.get("solved_at") else _now()
    try:
        out["geometry"] = model.fingerprints(analysis)["geo/part"]
    except Exception:  # noqa: BLE001
        shape = model_shape(doc)
        out["geometry"] = fingerprint_of_shape(shape) if shape is not None else ""
    return out


_KEPT = ("max_vm", "max_disp", "min_sf", "min_sf_away", "measure", "bore", "burst", "solved_at")


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def solid_mesh(doc) -> bytes:
    """The preview mesh (bruceworx_previewmesh format) of the study's solid, shown or not; b"" without one."""
    import MeshPart
    import bruceworx_previewmesh as pm

    shape = model_shape(doc)
    if shape is None or not shape.Faces:
        return b""
    box = shape.BoundBox
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=max(box.DiagonalLength * pm.DEFLECTION, pm.MIN_DEFLECTION),
                                  AngularDeflection=pm.ANGULAR, Relative=False)
    points, facets = mesh.Topology
    if not facets:
        return b""
    return pm.encode([{"name": "Model", "color": pm.GREY, "points": [c for p in points for c in (p.x, p.y, p.z)],
                       "triangles": [i for f in facets for i in f]}])


def is_simulation_path(path) -> bool:
    return str(path or "").lower().endswith(vs.SUFFIX)


_PREVIOUS = {}  # real path -> the summary the file had before FreeCAD rewrote it (FreeCAD drops unknown entries)


def remember_previous(path: str) -> None:
    """Called just before a save: keep the summary the file has now (solved_at, and the numbers of a copy saved
    without results), because the save drops every entry FreeCAD does not know."""
    if is_simulation_path(path) and os.path.exists(path):
        summary = vs.read_entry(path, vs.SUMMARY_ENTRY)
        if summary:
            _PREVIOUS[os.path.realpath(path)] = summary


def extra_entries(doc, path: str, kind: str) -> dict:
    """Zip entries to add to the file just saved to ``path``: {name: bytes}. ``kind`` is the vault item type."""
    entries = {}
    if kind == "simulation" or is_simulation_path(path):
        previous = _PREVIOUS.pop(os.path.realpath(path), None) or (
            vs.read_entry(path, vs.SUMMARY_ENTRY) if os.path.exists(path) else None)
        entries[vs.SUMMARY_ENTRY] = json.dumps(summary_of(doc, path, previous), indent=1).encode("utf-8")
        mesh = solid_mesh(doc)  # a study hides its solid under the result: the preview shows the solid anyway
        if mesh:
            entries["bruceworx/preview.bin"] = mesh
        return entries
    if kind in ("part", "assembly", "cots"):
        geo = geometry_of_doc(doc)
        if geo:
            entries[vs.GEOMETRY_ENTRY] = json.dumps(geo).encode("utf-8")
    return entries


# ----- making a simulation file ---------------------------------------------------------------------


def make_simulation_file(part_path: str, out_path: str, part: str, part_sha: str = "") -> str:
    """Open the part's file hidden, copy its solid into a new document and save that as ``out_path``
    (``.vlsim``); the copy keeps the part's placement so the fingerprints agree. Returns the fingerprint."""
    import FreeCAD
    import Part
    import bruceworx_preview as preview

    with preview.opened(part_path) as src:
        shape = model_shape(src)
        if shape is None:
            raise ValueError("the part has no solid to analyse")
        copy = shape.copy()
        label = src.Label
    doc = FreeCAD.newDocument(os.path.splitext(os.path.basename(out_path))[0])
    try:
        obj = doc.addObject("Part::Feature", "Model")
        obj.Label = f"{label} (copy)"
        obj.Shape = Part.Shape(copy)
        doc.recompute()
        fingerprint = fingerprint_of_shape(obj.Shape)
        set_source(doc, part, part_sha, fingerprint)
        doc.recompute()
        doc.saveAs(out_path)
        stamp(doc, out_path)
    finally:
        FreeCAD.closeDocument(doc.Name)
    return fingerprint


def stamp(doc, path: str) -> bool:
    """Picture, preview mesh and summary into the saved file (the same rewrite a vault save does)."""
    import bruceworx_preview as preview

    return preview.stamp_saved(doc, path, "simulation")


# ----- check-in without results (PDM-14, answer P4) -------------------------------------------------


def result_objects(doc) -> list:
    """The objects a study's solution adds: result objects, the result meshes beside the study's own mesh, and
    post-processing pipelines with their filters. The study, the solid copy, the mesh settings and the solver stay."""
    found = []

    def add(obj):
        if obj is not None and obj not in found:
            found.append(obj)

    for analysis in sim.studies(doc):
        members = sim.members(analysis)
        for result in members["results"]:
            mesh = getattr(result, "Mesh", None) if "Mesh" in result.PropertiesList else None
            add(result)
            add(mesh)
        for obj in getattr(analysis, "Group", []) or []:
            if obj.isDerivedFrom("Fem::FemMeshObject") and "Shape" not in obj.PropertiesList:
                add(obj)
    for obj in doc.Objects:
        if obj.TypeId.startswith("Fem::FemPost"):
            add(obj)
    return found


def strip_results(doc) -> int:
    """Remove the result objects from ``doc`` (children before the objects they hang from); returns how many went."""
    objs = result_objects(doc)
    names = [o.Name for o in objs]
    removed = 0
    for name in names:
        obj = doc.getObject(name)
        if obj is None:
            continue
        try:
            doc.removeObject(name)
            removed += 1
        except Exception:  # noqa: BLE001 -- already gone with its parent
            pass
    doc.recompute()
    return removed


def stripped_copy(path: str, staging: str) -> str:
    """A copy of the simulation file ``path`` in ``staging`` (same file name) without its results: the study,
    solid copy, mesh settings and summary stay, and the summary keeps the numbers it had (``results_stripped``).
    The work copy is not touched. Returns the copy's path."""
    import shutil

    import bruceworx_previewmesh as pm
    import bruceworx_preview as preview

    os.makedirs(staging, exist_ok=True)
    copy = os.path.join(staging, os.path.basename(path))
    shutil.copyfile(path, copy)
    full = vs.read_entry(copy, vs.SUMMARY_ENTRY) or {}
    with preview.opened(copy) as doc:
        strip_results(doc)
        doc.save()
    entries = dict(full)
    entries.update({"has_results": False, "results_stripped": True, "saved_at": _now()})
    pm.rewrite(copy, {vs.SUMMARY_ENTRY: json.dumps(entries, indent=1).encode("utf-8")})
    return copy


# ----- new simulation of a part, and bringing in an existing study ----------------------------------


def _latest_part(s, part: str):
    version = s.vault.latest_version(part)
    if version is None:
        raise ValueError(f"{part} has no file in the vault yet")
    return version, s.vault.canonical(version.sha256)


def new_simulation(s, part: str, description: str, checkout: bool = True) -> str:
    """Make the item, copy the part's solid into a new ``.vlsim`` and (``checkout``) take it. ``s`` is the vault
    client's session. Returns the simulation's part number."""
    import bruceworx_preview as preview

    part = s.vault.resolve(part)
    _version, sha = _latest_part(s, part)
    source = preview.source_for(s, part)

    def make(path, pn):
        make_simulation_file(source, path, part, sha)

    key = vs.new_simulation(s.vault, part, description, make)
    if checkout:
        s.invalidate()
        s.work.checkout(key, with_links=False)
    return key


def import_study(s, part: str, path: str, description: str = "", checkout: bool = True) -> str:
    """Bring in an existing study file (``.FCStd`` or ``.vlsim``; only read) as a simulation of ``part``: a copy
    gets the number, the source record and its summary, and is checked in as the first version."""
    import shutil

    import bruceworx_preview as preview

    part = s.vault.resolve(part)
    _version, sha = _latest_part(s, part)
    description = (description or os.path.splitext(os.path.basename(path))[0]).strip()

    def make(staged, pn):
        shutil.copyfile(path, staged)
        with preview.opened(staged) as doc:
            set_source(doc, part, sha, "")
            own = summary_of(doc).get("geometry", "")  # the study's own solid: what the card compares with the part
            set_source(doc, part, sha, own)
            doc.save()
            stamp(doc, staged)

    key = vs.new_simulation(s.vault, part, description, make)
    if checkout:
        s.invalidate()
        s.work.checkout(key, with_links=False)
    return key
