# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulations in the vault (PDM-14, 1 Nov 2026).

A simulation is an item of its own (type ``simulation``, document type 04), linked to the part or assembly it
analyses by ``simulation_of`` as a drawing is by ``drawing_of``. Its file is a FreeCAD document zip saved as
``<P/N>.vlsim``: the study, a copy of the solid, the results, and (written at every save) a summary the
vault reads without opening the document. This module holds the vault side; it needs no FreeCAD.
"""

from __future__ import annotations

import json
import os
import re
import zipfile

from bruceworx import numbering, pdm, vault_setup

SIM_TYPE_NAME = "Simulation"
SIM_TYPE_CODE = "04"
SUFFIX = ".vlsim"
SUMMARY_ENTRY = "bruceworx/simulation.json"  # written into a simulation file at every save
GEOMETRY_ENTRY = "bruceworx/geometry.json"  # written into a part's file at every save and check-in
SUMMARY_FORMAT = 1
CURRENT, CHANGED, UNKNOWN = "current", "part changed", "unknown"
RESULT_TEXT = {True: "with results", False: "no results", None: ""}


def sim_document_type(vault_root: str) -> str:
    """The document type code simulations take in this vault: the one named Simulation. A vault made
    before PDM-14 has none, so one is added (04, else the first free code) and every program gets a
    counter for it; the vault's other numbers are untouched."""
    root = vault_setup.root_of(vault_root)
    settings = vault_setup.load_settings(root)
    for code, name in settings.document_types.items():
        if str(name).strip().lower() == SIM_TYPE_NAME.lower() and code not in settings.retired["document_type"]:
            return numbering.normalize_document_type(code)
    taken = {numbering.normalize_document_type(c) for c in settings.document_types}
    code = SIM_TYPE_CODE if SIM_TYPE_CODE not in taken else next(
        f"{n:02d}" for n in range(4, 100) if f"{n:02d}" not in taken)
    settings.document_types[code] = SIM_TYPE_NAME
    vault_setup.save_settings(root, settings)
    return code


def new_simulation(vault, part: str, description: str, make_file, program: str = "", category: str = "") -> str:
    """Number a new simulation of ``part`` and check it in as WIP 01 (not checked out).

    ``make_file(path, part_number)`` writes the ``.vlsim`` (a copy of the part's solid with the study
    started; see bruceworx_sim_vault). The number is previewed first and taken only once the file exists,
    as in :func:`vault_setup.new_item`. Returns the simulation's part number."""
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    part = vault.resolve(part)
    if part not in vault.items or vault.items[part].item_type not in ("part", "assembly", "cots"):
        raise ValueError(f"{part} is not a part or assembly of this vault")
    description = str(description or "").strip()
    if not description:
        raise ValueError("a new simulation needs a description")
    code = sim_document_type(vault.root)
    source = JsonNumberSource(vault_setup.numbering_path(vault.root))
    programs = source.programs()
    program = numbering.normalize_program(program) if program else (
        vault_setup.program_of(part, vault.root) if programs else "")
    if not program:
        raise ValueError("the vault's numbering has no program")
    key = pdm.strip_revision(source.preview(program, code))
    if key in vault.items:
        raise ValueError(f"{key} is already an item in the vault")
    staging = os.path.join(vault.root, vault_setup.STAGING_DIR)
    os.makedirs(staging, exist_ok=True)
    staged = os.path.join(staging, key + SUFFIX)
    try:
        make_file(staged, key)
        if not os.path.isfile(staged):
            raise ValueError(f"no file was made for {key}")
        taken = pdm.strip_revision(source.allocate(program, code))
        if taken != key:
            raise ValueError(f"the number moved on while the file was made ({taken}, not {key}); try again")
        vault.create_item(key, "simulation", description, simulation_of=part)
        if category and str(category).strip():
            vault.set_category(key, category)
        vault.checkout(key)
        try:
            vault.checkin(key, staged, comment=f"New simulation of {part}: {description}")
        except Exception:
            vault.undo_checkout(key)
            raise
    finally:
        for name in os.listdir(staging):
            if name == key + SUFFIX or (name.startswith(key + ".") and re.search(r"\.(FCBak|vlsim\d+)$", name)):
                try:
                    os.remove(os.path.join(staging, name))
                except OSError:
                    pass
        try:
            os.rmdir(staging)
        except OSError:
            pass
    return key


# ----- reading without opening (PDM-14) -------------------------------------------------------------


def read_entry(path: str, name: str):
    """The JSON entry ``name`` of the zip at ``path`` (an .FCStd or .vlsim), or None when absent or unreadable.
    Reads the zip directory and that one entry only, never the document."""
    try:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read(name).decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile, ValueError):
        return None


def cache_dir() -> str:
    """Where entries of versions are remembered by the version's SHA-256 (this Mac only, never the vault)."""
    base = os.environ.get("BW_SIM_CACHE") or os.path.join(
        os.path.expanduser("~"), "Library", "Caches", "BruceWorx", "simulations")
    return base


def _cache_file(kind: str, sha: str) -> str:
    return os.path.join(cache_dir(), f"{sha}.{kind}.json")


def stored_entry(vault, version, name: str, kind: str):
    """The entry ``name`` of a stored version: from this Mac's cache, else from the stored file when it is on
    this Mac (a file still in iCloud is not downloaded to list it). None when neither has it."""
    if version is None:
        return None
    sha = vault.canonical(version.sha256) if hasattr(vault, "canonical") else version.sha256
    cached = _cache_file(kind, sha)
    try:
        with open(cached, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        pass
    path = os.path.join(vault.root, version.object_name)
    from bruceworx.pdm_workspace import needs_download  # noqa: PLC0415

    if not os.path.exists(path) or needs_download(path):
        return None
    data = read_entry(path, name)
    if data is not None:
        store_cached(kind, sha, data)
    return data


def store_cached(kind: str, sha: str, data: dict) -> None:
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        tmp = _cache_file(kind, sha) + ".part"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, _cache_file(kind, sha))
    except OSError:
        pass


def summary_of_version(vault, version):
    return stored_entry(vault, version, SUMMARY_ENTRY, "summary")


def geometry_of_version(vault, version):
    return stored_entry(vault, version, GEOMETRY_ENTRY, "geometry")


def judge(summary, part_geometry) -> str:
    """"current" when the simulation's copy has the part's latest geometry (equal fingerprints), "part changed"
    when they differ, "unknown" when either is not recorded (a part checked in before PDM-14, a study never saved)."""
    mine = (summary or {}).get("geometry") or ((summary or {}).get("source") or {}).get("fingerprint") or ""
    theirs = (part_geometry or {}).get("fingerprint") or ""
    if not mine or not theirs:
        return UNKNOWN
    return CURRENT if mine == theirs else CHANGED


def card(vault, pn: str) -> dict:
    """What a simulation's card and row show, from the latest version of the item ``pn``, without opening it:
    {"pn", "of", "state", "summary", "judgement", "has_results", "version", "reports"}."""
    item = vault.items[pn]
    version = vault.latest_version(pn)
    summary = summary_of_version(vault, version) if version is not None else None
    part = vault.resolve(item.simulation_of) if item.simulation_of else ""
    geometry = geometry_of_version(vault, vault.latest_version(part)) if part in vault.items else None
    return {"pn": pn, "of": part, "state": item.state, "summary": summary or {},
            "judgement": judge(summary, geometry), "has_results": (summary or {}).get("has_results"),
            "version": version.sha256 if version is not None else "", "part_geometry": geometry or {}}


def results_by_version(vault, pn: str) -> list:
    """[(FileVersion, True|False|None)] for the item's versions, oldest first: whether each carries results
    (None when its summary is not available, as for a file still in iCloud)."""
    out = []
    for version in vault.versions.get(pn, []):
        summary = summary_of_version(vault, version)
        out.append((version, None if summary is None else bool(summary.get("has_results"))))
    return out
