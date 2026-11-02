# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad projects as vault items (ECD-05, 30 Oct 2026): the electrical part lives in the vault.

A KiCad project is a folder, not one file. In a folder vault (pdm_folder) an item's file
versions are single objects, so the project is packed into one archive per version::

    objects/<ab>/<sha256>.zip            <P/N>.kicad.zip: the project folder, deflated
    work/<actor>/<P/N>/                  the work copy: the KiCad project, unpacked
        <name>.kicad_pro .kicad_sch .kicad_pcb, project libraries, 3dmodels/, ...
        keystone-part.json               the part's properties (description, manufacturer, ...)
        thumbnails/Thumbnail.png         KiCad's iso render: the vault thumbnail (the Data
                                         panel reads it from the archive like an .FCStd's)
        .keystone/<P/N>.FCStd            the board in 3D, made by Vector Labs from the .kicad_pcb
                                         (derived, never checked in, remade when it changes)
        .keystone/<P/N>-light.FCStd      the light model: board and components, no copper or
                                         print layers (for large assemblies; 31 Oct)

The item is an ordinary item (type ``cots`` by default, the Standard Libraries' bought and
library parts), so journals stay readable by every Vector Labs; what makes it a KiCad item is its
file: a version whose name ends in ``.kicad.zip`` (:func:`is_ecad`). pdm_workspace.Workspace
treats such an item's work copy as this folder: check-out makes the files writable, check-in
packs them (:func:`pack`) after refreshing the thumbnail (:func:`prepare_checkin`), Get Latest
and Undo Check-Out unpack (:func:`unpack`), and every other file is read-only on disk, so KiCad
opens a project that is not checked out read-only.

Identity: the work copy's state is the *tree digest* (:func:`tree_digest`, SHA-256 over the
sorted relative paths and file hashes), not the archive's bytes, so a zip written by another
Python never makes an unchanged project look modified. Left out of the archive and the digest:
what KiCad writes on its own (``*-backups/``, ``fp-info-cache``, ``*.lck``, ``.history``,
``_autosave-*``, ``*.kicad_prl`` local view settings), caches (``__pycache__``) and
``.keystone/``.

:func:`import_project` makes a new item from a KiCad project folder (``bw-hooks vault
kicad-import``, Vector Labs' Import KiCad Project as Standard Part...); :func:`update_project`
checks a newer copy of the project in as the next version (``vault kicad-update``);
:func:`launch` opens a project, schematic or board in KiCad. FreeCAD-free.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field

from bruceworx import kicad_bridge as kb

ARCHIVE_SUFFIX = ".kicad.zip"
PROPERTIES_FILE = "keystone-part.json"
THUMBNAIL = "thumbnails/Thumbnail.png"
DERIVED_DIR = ".keystone"
DEFAULT_ITEM_TYPE = "cots"  # a library part (decision 30 Oct: no new item type, see the ECD chapter)
ITEM_TYPES = ("cots", "part")
PROPERTY_KEYS = ("Description", "Manufacturer", "ManufacturerPartNumber", "Value")
SKIP_DIRS = {".git", ".svn", ".hg", ".history", "__pycache__", DERIVED_DIR, ".bw", ".ipynb_checkpoints"}
SKIP_DIR_SUFFIXES = ("-backups",)
SKIP_FILES = ("fp-info-cache", ".DS_Store", "Thumbs.db", "*.kicad_prl", "*.lck", "_autosave-*", "*.pyc",
              "._*", "*.bak", "*-bak", "*.tmp", "*~")
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
LAUNCH_ENV = "BW_KICAD_LAUNCH"  # "dry": build the command, start nothing (tests)
EDITORS = {
    ".kicad_pro": kb.KICAD_APP,
    ".kicad_sch": kb.KICAD_APP + "/Contents/Applications/eeschema.app",
    ".kicad_pcb": kb.KICAD_APP + "/Contents/Applications/pcbnew.app",
}
EXECUTABLES = {".kicad_pro": "kicad", ".kicad_sch": "eeschema", ".kicad_pcb": "pcbnew"}
_TITLE = re.compile(r'\(title_block\s*\(title\s+"((?:[^"\\]|\\.)*)"')


# ----- the project folder ----------------------------------------------------------------------


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(SKIP_DIR_SUFFIXES)


def _skip_file(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SKIP_FILES)


def excluded(rel: str, patterns=()) -> bool:
    """Whether the relative path ``rel`` matches one of ``patterns`` (a glob on the path, or a
    folder name such as ``build`` that takes everything under it)."""
    for pattern in patterns or ():
        pattern = pattern.strip().strip("/")
        if pattern and (fnmatch.fnmatchcase(rel, pattern) or rel.startswith(pattern + "/")):
            return True
    return False


def project_files(folder: str, exclude=()) -> list[str]:
    """The files of a KiCad project folder that belong in the vault, as sorted POSIX paths
    relative to ``folder``, less those ``exclude`` names (:func:`excluded`). Symbolic links are
    left out."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d)
                             and not os.path.islink(os.path.join(dirpath, d)))
        rel = os.path.relpath(dirpath, folder)
        for name in filenames:
            full = os.path.join(dirpath, name)
            if _skip_file(name) or os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append((name if rel == "." else os.path.join(rel, name)).replace(os.sep, "/"))
    return sorted(r for r in out if not excluded(r, exclude))


@dataclass
class Project:
    folder: str
    stem: str
    pro: str
    sch: str = ""  # "" when the project has no schematic of that name
    pcb: str = ""

    @property
    def files(self) -> dict:
        return {"project": self.pro, "schematic": self.sch, "board": self.pcb}


def find_project(folder: str, name: str = "") -> Project:
    """The KiCad project in ``folder`` (its top level): the only ``.kicad_pro``, or the one
    named ``name`` (a stem or file name)."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"no such folder: {folder}")
    found = sorted(n for n in os.listdir(folder) if n.endswith(".kicad_pro") and not n.startswith("._"))
    if name:
        stem = os.path.basename(name)
        for ext in (".kicad_pro", ".kicad_pcb", ".kicad_sch", ".pro"):  # a stem may hold dots: Leviathan_V1.3
            if stem.endswith(ext):
                stem = stem[:-len(ext)]
                break
        found = [n for n in found if n[:-len(".kicad_pro")] == stem]
    if not found:
        raise ValueError(f"{folder} holds no KiCad project (.kicad_pro)" + (f" named {name}" if name else ""))
    if len(found) > 1:
        raise ValueError(f"{folder} holds {len(found)} KiCad projects ({', '.join(found)}); name the one to use")
    stem = found[0][:-len(".kicad_pro")]

    def file(ext):
        path = os.path.join(folder, stem + ext)
        return path if os.path.isfile(path) else ""

    return Project(folder, stem, os.path.join(folder, found[0]), file(".kicad_sch"), file(".kicad_pcb"))


def _file_sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(folder: str) -> str:
    """SHA-256 over the project's files (paths and contents): the work copy's identity."""
    digest = hashlib.sha256()
    for rel in project_files(folder):
        digest.update(f"{rel}\0{_file_sha(os.path.join(folder, rel))}\n".encode("utf-8"))
    return digest.hexdigest()


def signature(folder: str) -> str:
    """A cheap fingerprint of the files' names, sizes and modification times (a cache key)."""
    digest = hashlib.sha1()  # noqa: S324 -- a cache key, not a security check
    for rel in project_files(folder):
        info = os.stat(os.path.join(folder, rel))
        digest.update(f"{rel}\0{info.st_size}\0{info.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()


def archive_bytes(folder: str) -> bytes:
    """The project folder as a zip: sorted entries, fixed dates and modes, so the same files
    give the same bytes (and the same vault object) on this Mac."""
    files = project_files(folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for rel in files:
            info = zipfile.ZipInfo(rel, ZIP_DATE)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(folder, rel), "rb") as handle:
                archive.writestr(info, handle.read(), compresslevel=6)
    return buffer.getvalue()


def pack(folder: str, out: str) -> str:
    """Write the project folder to the archive ``out`` (see :func:`archive_bytes`); returns its
    SHA-256. The folder must hold a KiCad project."""
    find_project(folder)
    data = archive_bytes(folder)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, out)
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    parts = name.replace("\\", "/").split("/")
    if not name or name.startswith("/") or any(p in ("", "..") for p in parts[:-1]) or parts[-1] in ("", ".."):
        raise ValueError(f"unsafe name in a KiCad archive: {name!r}")
    return "/".join(parts)


def _remove_project_files(folder: str) -> None:
    """Remove what :func:`project_files` lists (and the folders they leave empty); KiCad's own
    files (locks, backups) and ``.keystone/`` stay."""
    for rel in project_files(folder):
        path = os.path.join(folder, rel)
        os.remove(path)
    for dirpath, _dirnames, _files in sorted(os.walk(folder, topdown=False), key=lambda e: -len(e[0])):
        if dirpath != folder and not os.listdir(dirpath):
            os.rmdir(dirpath)


def unpack(archive: str, folder: str, writable: bool = False) -> list[str]:
    """Put the archive's files in ``folder`` in place of the project files there; read-only
    unless ``writable``. Returns the relative paths written."""
    with zipfile.ZipFile(archive) as source:
        infos = [i for i in source.infolist() if not i.is_dir()]
        names = [_safe_name(i.filename) for i in infos]
        if os.path.isdir(folder):
            _remove_project_files(folder)
        os.makedirs(folder, exist_ok=True)
        for info, rel in zip(infos, names):
            dest = os.path.join(folder, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with source.open(info) as data, open(dest, "wb") as out:
                shutil.copyfileobj(data, out)
            os.chmod(dest, 0o644 if writable else 0o444)
    return names


def set_writable(folder: str, writable: bool) -> None:
    """Make the project's files writable or read-only (folders stay writable, so KiCad can keep
    its lock and cache files beside a read-only project)."""
    for rel in project_files(folder):
        path = os.path.join(folder, rel)
        mode = os.stat(path).st_mode
        os.chmod(path, (mode | stat.S_IWUSR) if writable
                 else (mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)))


# ----- properties and thumbnail -----------------------------------------------------------------


def read_properties(folder: str) -> dict:
    try:
        with open(os.path.join(folder, PROPERTIES_FILE), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_properties(folder: str, values: dict) -> str:
    path = os.path.join(folder, PROPERTIES_FILE)
    if os.path.exists(path):
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    data = {k: v for k, v in values.items() if v not in (None, "")}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def title_of(project: Project) -> str:
    """The board's title block title, else the schematic's, else ""."""
    for path in (project.pcb, project.sch):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                match = _TITLE.search(handle.read(200000))
        except OSError:
            continue
        if match and match.group(1).strip():
            return re.sub(r"\\(.)", r"\1", match.group(1)).strip()
    return ""


def prepare_checkin(folder: str, cli: str | None = None) -> str:
    """Before a check-in: KiCad's iso render of the board as ``thumbnails/Thumbnail.png`` (cached
    by the board's content, so an unchanged board keeps the same picture). Returns the picture's
    path, or "" when there is no board or no kicad-cli."""
    project = find_project(folder)
    if not project.pcb or not kb.find_cli(cli):
        return ""
    png = kb.cached_render(project.pcb, cli=cli)
    dest = os.path.join(folder, *THUMBNAIL.split("/"))
    if os.path.isfile(dest) and _file_sha(dest) == _file_sha(png):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IWUSR)
    shutil.copyfile(png, dest)
    return dest


def model_report(project: Project) -> list[dict]:
    """The board's 3D models as the bridge resolves them ({"model", "resolved", "found"})."""
    if not project.pcb:
        return []
    with open(project.pcb, encoding="utf-8") as handle:
        _text, report = kb.resolve_models(handle.read(), project.folder)
    return report


# ----- the vault ----------------------------------------------------------------------------------


def is_archive_name(filename: str) -> bool:
    return str(filename or "").lower().endswith(ARCHIVE_SUFFIX)


def is_ecad(vault, part_number: str) -> bool:
    """Whether the item's file is a KiCad project (its latest version is a ``.kicad.zip``)."""
    try:
        version = vault.latest_version(part_number)
    except Exception:  # noqa: BLE001 -- unknown item
        return False
    return version is not None and is_archive_name(version.filename)


def ecad_items(vault) -> list[str]:
    return sorted(pn for pn in vault.model.items if is_ecad(vault, pn))


def item_for_path(path: str):
    """``(vault root, actor, part number)`` when ``path`` lies in the work copy of a vault item's
    KiCad project (``<vault>/work/<actor>/<P/N>/...``), else None."""
    current = os.path.abspath(path)
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return None
        actor_dir = parent
        work_dir = os.path.dirname(actor_dir)
        root = os.path.dirname(work_dir)
        if os.path.basename(work_dir) == "work" and root != work_dir:
            try:
                refs = [n for n in os.listdir(root) if n.endswith(".bwvault") and not n.startswith("._")]
            except OSError:
                refs = []
            if refs:
                return root, os.path.basename(actor_dir), os.path.basename(current)
        current = parent


LIGHT_SUFFIX = "-light"


def derived_path(folder: str, part_number: str, light: bool = False) -> str:
    """Where Vector Labs keeps the board in 3D for this work copy (the full or the light model)."""
    return os.path.join(folder, DERIVED_DIR, part_number + (LIGHT_SUFFIX if light else "") + ".FCStd")


def derived_stamp(folder: str, extra: dict | None = None, light: bool = False) -> str:
    """What the 3D model is made from: the board's content, the part's properties, ``extra``
    (the item's revision, description, the recipe version) and whether it is the light model.
    A different stamp remakes it."""
    project = find_project(folder)
    data = {"board": kb.board_hash(project.pcb) if project.pcb else "", "properties": read_properties(folder),
            "extra": extra or {}}
    if light:
        data["light"] = True
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def derived_fresh(folder: str, part_number: str, stamp: str, light: bool = False) -> bool:
    path = derived_path(folder, part_number, light)
    try:
        with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as handle:
            saved = json.load(handle).get("stamp")
    except (OSError, ValueError, AttributeError):
        return False
    return saved == stamp and os.path.isfile(path)


def record_derived(folder: str, part_number: str, stamp: str, light: bool = False) -> None:
    path = os.path.splitext(derived_path(folder, part_number, light))[0] + ".json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"stamp": stamp}, handle)


@dataclass
class ImportResult:
    part_number: str
    sha256: str
    files: int
    description: str
    item_type: str
    work_copy: str = ""
    thumbnail: bool = False
    missing_models: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _copy_project(source: str, dest: str, exclude=()) -> int:
    files = project_files(source, exclude)
    for rel in files:
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(source, *rel.split("/")), target)
        os.chmod(target, 0o644)
    return len(files)


def _properties(project: Project, description: str, manufacturer: str, mpn: str, value: str,
                old: dict | None = None) -> dict:
    values = dict(old or {})
    values.update({"Description": description, "Manufacturer": manufacturer,
                   "ManufacturerPartNumber": mpn, "Value": value})
    values["KiCadProject"] = os.path.basename(project.pro)
    return {k: v for k, v in values.items() if v not in (None, "")}


def plan_import(vault, source: str, description: str = "", program: str = "", document_type: str = "",
                project_name: str = "", exclude=()) -> dict:
    """What :func:`import_project` would do, without writing anything: the part number it would take,
    the description, the files it would copy and the 3D models the board names that are not found."""
    from bruceworx import numbering, pdm, vault_setup  # noqa: PLC0415
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    source = os.path.abspath(os.path.expanduser(source))
    if os.path.isfile(source):
        project_name = project_name or source
        source = os.path.dirname(source)
    project = find_project(source, project_name)
    numbers = JsonNumberSource(vault_setup.numbering_path(vault.root))
    programs = numbers.programs()
    program = numbering.normalize_program(program) if program else (programs[0] if programs else "")
    types = vault_setup._vault_document_types(numbers, numbers._load())
    code = (numbering.normalize_document_type(document_type) if document_type
            else vault_setup.document_type_for_item("part", types, numbers._load()))
    files = project_files(source, exclude)
    return {"part_number": pdm.strip_revision(numbers.preview(program, code)) if program else "",
            "description": (description or "").strip() or title_of(project) or project.stem,
            "project": project.pro, "board": project.pcb, "schematic": project.sch, "files": files,
            "size": sum(os.path.getsize(os.path.join(source, *f.split("/"))) for f in files),
            "missing_models": [m["model"] for m in model_report(project) if not m["found"]]}


def import_project(vault, source: str, comment: str, description: str = "", manufacturer: str = "",
                   mpn: str = "", value: str = "", program: str = "", document_type: str = "",
                   item_type: str = DEFAULT_ITEM_TYPE, project_name: str = "", workspace=None,
                   render: bool = True, cli: str | None = None, exclude=()) -> ImportResult:
    """Number a new item, copy the KiCad project ``source`` into it and check it in (WIP 01).

    The source folder is only read. The copy leaves out KiCad's backups, caches and local view
    settings (see the module doc), and what ``exclude`` names (:func:`excluded`, e.g. ``build``).
    ``description`` defaults to the board's (or schematic's) title.
    With ``workspace`` (a pdm_workspace.Workspace on ``vault``) the work copy is placed in the
    vault's work folder, read-only. The number is taken only once the archive exists, as for
    vault_setup.new_item. Returns an :class:`ImportResult`."""
    from bruceworx import pdm, vault_setup  # noqa: PLC0415 -- vault_setup imports pdm_workspace
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    if not (comment or "").strip():
        raise ValueError("a check-in comment is required")
    if item_type not in ITEM_TYPES:
        raise ValueError(f"a KiCad item is a {' or a '.join(ITEM_TYPES)}, not {item_type!r}")
    source = os.path.abspath(os.path.expanduser(source))
    if os.path.isfile(source):
        project_name = project_name or source
        source = os.path.dirname(source)
    project = find_project(source, project_name)
    if not project.pcb:
        raise ValueError(f"{project.stem}: no board ({project.stem}.kicad_pcb) beside the project")
    if os.path.realpath(source).startswith(os.path.realpath(vault.root) + os.sep):
        raise ValueError("the KiCad project is already inside this vault; import a copy from outside it")
    description = (description or "").strip() or title_of(project) or project.stem
    for pn in ecad_items(vault):
        if vault.model.items[pn].description.strip().lower() == description.lower():
            raise ValueError(f"{pn} is already a KiCad item named {description!r}; "
                             "check a new version in with kicad-update, or give another description")

    numbers = JsonNumberSource(vault_setup.numbering_path(vault.root))
    data = numbers._load()
    programs = numbers.programs()
    if not programs:
        raise ValueError("the vault's numbering has no program")
    from bruceworx import numbering  # noqa: PLC0415

    program = numbering.normalize_program(program) if program else programs[0]
    types = vault_setup._vault_document_types(numbers, data)
    code = (numbering.normalize_document_type(document_type) if document_type
            else vault_setup.document_type_for_item("part", types, data))
    key = pdm.strip_revision(numbers.preview(program, code))
    if key in vault.items:
        raise ValueError(f"{key} is already an item in the vault")

    staging_root = os.path.join(vault.root, vault_setup.STAGING_DIR)
    staged = os.path.join(staging_root, key)
    archive = os.path.join(staging_root, key + ARCHIVE_SUFFIX)
    result = ImportResult(key, "", 0, description, item_type)
    try:
        if os.path.isdir(staged):
            shutil.rmtree(staged)
        result.files = _copy_project(source, staged, exclude)
        staged_project = find_project(staged, project.stem)
        write_properties(staged, _properties(staged_project, description, manufacturer, mpn, value))
        result.missing_models = [m["model"] for m in model_report(staged_project) if not m["found"]]
        if result.missing_models:
            result.warnings.append(f"{len(result.missing_models)} 3D model(s) not found; the board shows "
                                   "without them until they are in the project or KiCad's library")
        if render:
            try:
                result.thumbnail = bool(prepare_checkin(staged, cli))
            except Exception as exc:  # noqa: BLE001 -- a picture is never a reason not to import
                result.warnings.append(f"no thumbnail: {exc}")
        pack(staged, archive)
        taken = pdm.strip_revision(numbers.allocate(program, code))
        if taken != key:
            raise ValueError(f"the number moved on while the project was packed ({taken}, not {key}); try again")
        vault.create_item(key, item_type, description)
        vault.checkout(key)
        try:
            version = vault.checkin(key, archive, comment=comment.strip())
        except Exception:
            vault.undo_checkout(key)
            raise
        result.sha256 = version.sha256
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        for path in (archive, archive + ".tmp"):
            if os.path.exists(path):
                os.remove(path)
        try:
            os.rmdir(staging_root)
        except OSError:
            pass
    if workspace is not None:
        result.work_copy = workspace.get_latest(key)
    return result


def update_project(workspace, part_number: str, source: str, comment: str, description: str | None = None,
                   manufacturer: str | None = None, mpn: str | None = None, value: str | None = None,
                   keep_checked_out: bool = False, exclude=()):
    """Check a newer copy of a KiCad project in as the next version of ``part_number``: check it
    out (unless it is out to you already), replace the project files in the work copy with the
    source's (the part's properties are kept, the given ones changed), check in. An unchanged
    project only releases the lock. Returns the vault's FileVersion."""
    vault = workspace.vault
    if not is_ecad(vault, part_number):
        raise ValueError(f"{part_number} is not a KiCad item")
    if not (comment or "").strip():
        raise ValueError("a check-in comment is required")
    source = os.path.abspath(os.path.expanduser(source))
    project_name = ""
    if os.path.isfile(source):
        project_name, source = source, os.path.dirname(source)
    project = find_project(source, project_name)
    folder = workspace.path(part_number)
    if vault.locks.get(part_number) != vault.actor:
        workspace.checkout(part_number)
    old = read_properties(folder)
    for rel in project_files(folder):
        if rel != PROPERTIES_FILE:
            os.remove(os.path.join(folder, *rel.split("/")))
    _copy_project(source, folder, exclude)
    changes = {"Description": description, "Manufacturer": manufacturer, "ManufacturerPartNumber": mpn,
               "Value": value}
    props = dict(old)
    props.update({k: v for k, v in changes.items() if v is not None})
    props["KiCadProject"] = os.path.basename(project.pro)
    write_properties(folder, props)
    return workspace.checkin(part_number, comment, keep_checked_out=keep_checked_out)


# ----- KiCad --------------------------------------------------------------------------------------


def launch_command(path: str) -> list[str]:
    """The command that opens a ``.kicad_pro`` in KiCad, a ``.kicad_sch`` in the schematic editor
    or a ``.kicad_pcb`` in the board editor."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in EDITORS:
        raise ValueError(f"not a KiCad project, schematic or board: {path}")
    if sys.platform == "darwin":
        app = EDITORS[ext] if os.path.isdir(EDITORS[ext]) else kb.KICAD_APP
        return ["open", "-a", app, path]
    return [shutil.which(EXECUTABLES[ext]) or EXECUTABLES[ext], path]


def launch(path: str, runner=None) -> list[str]:
    """Open ``path`` in KiCad (detached). With ``$BW_KICAD_LAUNCH=dry`` nothing starts. Returns
    the command."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    command = launch_command(path)
    if os.environ.get(LAUNCH_ENV, "").lower() == "dry":
        return command
    if runner is not None:
        runner(command)
    else:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return command


def editor_file(project: Project, which: str = "project") -> str:
    """The file to open for ``which`` (project, schematic or board)."""
    path = project.files.get(which, "")
    if not path:
        raise FileNotFoundError(f"{project.stem} has no {which} file")
    return path
