# SPDX-License-Identifier: LGPL-2.1-or-later
"""Renumber a vault item (BW-PDM-12, 31 Oct 2026; plan chapter 61b).

The vault has no way to rename an item otherwise. The journal operation ``renumber``
(:meth:`bruceworx.pdm_folder.FolderVault.renumber`) re-keys the item, its versions, locks, BOMs and
references and keeps the old number as an alias; this module does what surrounds it:

* :func:`suggest`: the next free number, with the document type the vault's setting gives;
* :func:`plan`: what a renumber would do and what stops it (the dialog and ``--dry-run`` show it);
* :func:`linkers`, :func:`cross_vault_users`: the items whose latest file links the item here and in
  the vaults beside this one;
* :func:`renumber`: the file step, then the single journal line. The item's latest file gets the new
  ``PartNumber`` and is stored as ``<new>.FCStd``; the latest file of every item linking it has the
  link re-pointed (``pdm_workspace.rewrite_links``, ``clear_link_errors``, as the import does). Those
  are new file versions of each item's current revision: same revision code, same state. The objects
  are written first, the journal line last, so a crash leaves unused objects and nothing else.

Writing the PartNumber needs FreeCAD (``freecad_io.write_part_number``; a ``writer`` can be passed). No
FreeCAD import here.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid

from bruceworx import numbering, pdm, vault_setup
from bruceworx.pdm import LifecycleError
from bruceworx.pdm_workspace import SUFFIX, clear_link_errors, linked_files, rewrite_links

STAGING = "renumber"  # under <vault>/.import/


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0]


def suggest(vault, part_number: str, document_type: str = "", program: str = "") -> str:
    """The vault's next free number for ``part_number``: its own program (else the first) and the
    document type the vault's rules give its kind (an assembly takes the Part code when the setting
    says so; ``document_type`` overrides). Nothing is reserved. Raises ValueError when the counter
    would give a number that is an item or a former number (a counter that fell behind)."""
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    part_number = vault.resolve(part_number)
    item = vault.items[part_number]
    source = JsonNumberSource(vault_setup.numbering_path(vault.root))
    data = source._load()
    programs = source.programs()
    if not programs:
        raise ValueError("the vault's numbering has no program")
    program = numbering.normalize_program(program) if program else vault_setup.program_of(part_number, vault.root)
    kind = item.item_type if item.item_type in ("assembly", "drawing", "simulation") else "part"
    code = (numbering.normalize_document_type(document_type) if document_type
            else vault_setup.document_type_for_item(kind, vault_setup._vault_document_types(source, data), data))
    key = pdm.strip_revision(source.preview(program, code))
    if key in vault.items or key in vault.model.aliases:
        raise ValueError(f"{key} is taken; the vault's counter for {program}/{code} is behind the numbers issued")
    return key


def linkers(vault, part_number: str) -> list:
    """The items whose latest file links ``part_number``'s file (``<P/N>.FCStd``): parent assemblies,
    drawings, configuration rows' files. KiCad items and items with no file are not read."""
    out = []
    for other, item in vault.items.items():
        if other == part_number or item.configuration_of:
            continue
        version = vault.latest_version(other)
        if version is None or not version.filename.lower().endswith(SUFFIX.lower()):
            continue
        path = os.path.join(vault.root, version.object_name)
        if os.path.isfile(path) and any(_stem(name) == part_number for name in linked_files(path)):
            out.append(other)
    return sorted(out)


def cross_vault_users(vault, part_number: str) -> list:
    """``[{"vault": name, "folder": path, "items": [P/N...]}]``: the items in the vaults beside this one
    (``vault_setup.neighbour_vaults``) whose latest file links ``<P/N>.FCStd``. Those links resolve through
    this vault's aliases when the project file opens and are re-pointed at the item's next check-in."""
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415

    out = []
    for name, folder in vault_setup.neighbour_vaults(vault.root):
        if os.path.abspath(folder) == os.path.abspath(vault.root):
            continue
        try:
            other = FolderVault(folder, actor=vault.actor)
            found = linkers(other, part_number)
        except Exception:  # noqa: BLE001 -- an unreadable neighbour is not a reason to stop
            continue
        if found:
            out.append({"vault": name, "folder": folder, "items": found})
    return out


def check_number(vault, old: str, new: str = "", document_type: str = "") -> tuple:
    """``(new, suggested, problems)`` for giving ``old`` the number ``new`` (blank: the next free one):
    the number in the vault's format, and what stops it that does not need the files (a number that is
    taken or a former one, a KiCad item, a configuration, an item in review). Cheap: the dialog asks
    on every keystroke."""
    item = vault.items[old]
    problems = []
    typed = str(new or "").strip()
    suggested = not typed
    try:
        new = suggest(vault, old, document_type) if suggested else vault_setup.existing_key(typed) or ""
        if not new:
            problems.append(f"{typed!r} is not a part number of this vault's format (PROG-DT-PPPPP-SSS)")
    except (KeyError, ValueError) as exc:
        new = ""
        problems.append(str(exc))
    if item.configuration_of:
        problems.append(f"{old} is configuration {item.configuration!r} of {item.configuration_of}; "
                        f"renumber {item.configuration_of}, its file")
    from bruceworx import ecad_vault  # noqa: PLC0415

    if ecad_vault.is_ecad(vault, old):
        problems.append(f"{old} is a KiCad project item; renumbering those is not supported yet")
    if item.latest.state == pdm.IN_REVIEW:
        problems.append(f"{old} is in review; finish the review first")
    if new == old:
        problems.append(f"{old} already has that number")
    elif new and new in vault.items:
        problems.append(f"{new} is already an item in this vault")
    elif new and new in vault.model.aliases:
        problems.append(f"{new} was the number of {vault.model.aliases[new]}; a former number is not reused")
    return new, suggested, problems


def plan(vault, part_number: str, new: str = "", document_type: str = "", cross: bool = True) -> dict:
    """What renumbering ``part_number`` to ``new`` (default: the next free number) would do, and what
    stops it: ``{"old", "new", "suggested", "state", "linkers", "cross_vault", "problems"}``. Nothing is
    written. The reason is checked by :func:`renumber`, not here (the dialog asks for it separately).
    ``cross=False`` leaves out the other vaults (reading them replays their journals)."""
    old = vault.resolve(part_number)
    if old not in vault.items:
        raise LifecycleError(f"{part_number} is not an item in this vault")
    new, suggested, problems = check_number(vault, old, new, document_type)
    found = [] if vault.items[old].configuration_of else linkers(vault, old)
    for pn in [old] + found:
        holder = vault.locks.get(pn)
        if holder:
            problems.append(f"{pn} is checked out by {holder}; renumbering needs it checked in")
    return {"old": old, "new": new, "suggested": suggested, "state": vault.items[old].state, "linkers": found,
            "cross_vault": cross_vault_users(vault, old) if cross else [], "problems": problems}


def default_writer(path: str, part_number: str, revision: str) -> None:
    """Write the new PartNumber into a staged copy (FreeCAD needed), keeping its revision."""
    from bruceworx import freecad_io  # noqa: PLC0415

    freecad_io.write_part_number(path, part_number, revision=revision)


def _link_mapper(old: str, new: str):
    """mapper for rewrite_links: a link to ``<old>.FCStd`` (with any folder in front) becomes ``<new>``."""
    def mapper(link):
        head, sep, tail = link.rpartition("/")
        stem, ext = os.path.splitext(tail)
        return head + sep + new + ext if stem == old else None

    return mapper


def renumber(vault, part_number: str, reason: str, new: str = "", document_type: str = "", writer=None,
             fingerprint_of=None, workspace=None, dry_run: bool = False) -> dict:
    """Renumber an item (PDM-12). Returns :func:`plan`'s dict plus ``"files"`` (P/N -> SHA-256 of the
    file versions written; empty for a dry run) and ``"replaced"`` (work copies replaced).

    Refused (LifecycleError, nothing written to the journal) when the plan has problems, the reason is
    empty, or the geometry fingerprint of the new file differs from the old file's. ``writer(path, new,
    revision)`` writes the P/N into a staged copy (default: FreeCAD's ``write_part_number``);
    ``fingerprint_of(path)`` reads a fingerprint (default: ``freecad_io.fingerprint_of_file``).
    The view data FreeCADCmd drops is put back (``connector_library.restore_view_data``). ``workspace``
    (optional) replaces the read-only work copy under the old number afterwards."""
    result = plan(vault, part_number, new, document_type)
    result.update(files={}, replaced=[], dry_run=bool(dry_run))
    reason = str(reason or "").strip()
    if result["problems"]:
        raise LifecycleError("; ".join(result["problems"]))
    if dry_run:
        return result
    if not reason:
        raise LifecycleError("a reason for renumbering is required")
    old, new = result["old"], result["new"]
    staging = os.path.join(vault.root, vault_setup.STAGING_DIR, f"{STAGING}-{uuid.uuid4().hex[:8]}")
    files = {}
    try:
        files[new] = _renumber_file(vault, old, new, staging, writer or default_writer, fingerprint_of)
        for pn in result["linkers"]:
            record = _relink_file(vault, pn, old, new, staging)
            if record is not None:
                files[pn] = record
        files = {pn: record for pn, record in files.items() if record is not None}
        _reserve(vault, new)
        vault.renumber(old, new, reason, files)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.rmdir(os.path.join(vault.root, vault_setup.STAGING_DIR))
        except OSError:
            pass
    result["files"] = {pn: record["sha256"] for pn, record in files.items()}
    if workspace is not None:
        result["replaced"] = workspace.refresh_renamed()
    return result


def _renumber_file(vault, old, new, staging, writer, fingerprint_of):
    """The item's latest file, with the new PartNumber, stored as ``<new>.FCStd``; None without a file."""
    version = vault.latest_version(old)
    if version is None:
        return None
    original = vault.fetch(old, os.path.join(staging, "original"), version)
    staged = os.path.join(staging, "new", new + (os.path.splitext(version.filename)[1] or SUFFIX))  # .vlsim stays .vlsim
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    shutil.copyfile(original, staged)
    writer(staged, new, vault.items[old].latest.code)
    clear_link_errors(staged)  # the writer opened the copy away from its parts: FreeCAD saved "Link not restored"
    from bruceworx.connector_library import restore_view_data  # noqa: PLC0415

    restore_view_data(original, staged)
    if version.fingerprint:
        if fingerprint_of is None:
            from bruceworx import freecad_io  # noqa: PLC0415

            fingerprint_of = freecad_io.fingerprint_of_file
        from bruceworx.product_tree import fingerprints_equal  # noqa: PLC0415

        now = fingerprint_of(staged)
        if now and not fingerprints_equal(version.fingerprint, now):
            raise LifecycleError(f"the geometry of {old} changed while its number was written; nothing was renumbered")
    return vault.store_object(staged)


def _relink_file(vault, pn, old, new, staging):
    """``pn``'s latest file with its links to ``<old>.FCStd`` pointing at ``<new>.FCStd``, stored; None
    when nothing changed. Same name, same revision: a new version of the file."""
    version = vault.latest_version(pn)
    path = vault.fetch(pn, os.path.join(staging, "links", re.sub(r"[^\w.-]", "_", pn)), version)
    if not rewrite_links(path, _link_mapper(old, new)):
        return None
    clear_link_errors(path)  # the copy was never opened with its parts beside it
    return vault.store_object(path)


def _reserve(vault, new: str) -> None:
    """Take ``new`` in the vault's numbering, so a later new number passes it by (a typed number too)."""
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    source = JsonNumberSource(vault_setup.numbering_path(vault.root))
    data = source._load()
    if vault_setup._claim(data, new):
        source._save(data)
