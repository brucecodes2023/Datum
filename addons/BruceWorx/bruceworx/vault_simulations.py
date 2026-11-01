# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulations in the vault (PDM-14, 1 Nov 2026).

A simulation is an item of its own (type ``simulation``, document type 04), linked to the part or assembly it
analyses by ``simulation_of`` as a drawing is by ``drawing_of``. Its file is a FreeCAD document zip saved as
``<P/N>.vlsim``: the study, a copy of the solid, the results, and (written at every save) a summary the
vault reads without opening the document. This module holds the vault side; it needs no FreeCAD.
"""

from __future__ import annotations

import os
import re

from bruceworx import numbering, pdm, vault_setup

SIM_TYPE_NAME = "Simulation"
SIM_TYPE_CODE = "04"
SUFFIX = ".vlsim"


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
