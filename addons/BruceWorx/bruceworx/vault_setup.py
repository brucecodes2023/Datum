# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create a folder vault with its numbering, part properties and imported files.

The engine behind the Create Vault wizard (bruceworx_vaultwizard.py in the
BruceWorx module) and ``bw-hooks vault init`` / ``vault import``. Free of
FreeCAD, like pdm_folder.py; writing part numbers into imported files is a
callback the FreeCAD side passes in (:func:`bruceworx.freecad_io.write_part_number`).

What a new vault holds besides pdm_folder's ref file, objects/ and journal/::

    <folder>/
      numbering.json         the vault's part-number counters (sources.JsonNumberSource
                             format: PROG-DT-PPPPP-SSS, fixed form, no revision)
      document_types.json    the document-type codes and names chosen in the wizard
      part_properties.json   the company layer of the part-property schema
                             (partprops.load_schema reads it as the vault layer)
      drawing_standards.json the title-block settings (bruceworx.drawing_standards)

Steps, in wizard order:

1. Name and location: :func:`default_parent_dir` (iCloud Drive first),
   :func:`suggest_folder`, :func:`check_location`.
2. Part numbering: :class:`NumberingSetup`, :func:`validate_numbering`,
   :func:`example_number`.
3. Company part properties: :func:`suggested_properties`,
   :func:`validate_properties`.
4. Create: :func:`create_vault`.
5. Import existing files: :func:`scan_for_import`, :func:`plan_numbers`,
   :func:`run_import`. Originals are never changed: each file is copied to a
   staging folder (``<vault>/.import``), numbered there, and checked in from
   the copy.
6. The tables as one CSV file (wizard download/upload, Vault Settings):
   :func:`csv_template`, :func:`parse_csv`, :func:`settings_from_setup`,
   :func:`apply_to_setup`; after creation :func:`load_settings`,
   :func:`check_settings_change` and :func:`save_settings` (rows are added,
   renamed or retired, never deleted, and codes never change).

Part numbers and revisions: a new vault's numbering file issues the P/N
``PROG-DT-PPPPP-SSS`` without revision (what :func:`example_number` and
``ImportCandidate.proposed_part_number`` show; BW-PDM-03, FND-08). The revision
is the item's: pre-release ``01``, ``02``, ... then ``A`` at the first release.
An older vault whose numbering file still declares ``-REV`` issues ``P/N-REV``
until migrated (``sources.migrate_numbering_file``); the vault keys items by the
number without it (``pdm.strip_revision``).
``ImportReport.imported`` and the ``PartNumber`` property hold that vault
number.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from bruceworx import numbering, pdm

# Default document types offered by the wizard (editable there). Decided for the
# user: the shipped config/document_types.json is a placeholder table.
DEFAULT_DOCUMENT_TYPES = {"01": "Part", "02": "Assembly", "03": "Drawing", "04": "Simulation"}
DEFAULT_PROGRAM = "BW"
DEFAULT_FIRST_PARENT = 1001
# The revision an older numbering file (P/N-REV form) gives a program Vault Settings adds.
LEGACY_FIRST_REVISION = "A"
VAULT_PARENT_NAME = "BruceWorx Vaults"
NUMBERING_FILE = "numbering.json"
ASSEMBLIES_AS_PARTS = "assemblies_as_parts"  # numbering.json key (PDM-12): assemblies get the Part code
DOCUMENT_TYPES_FILE = "document_types.json"
PROPERTIES_FILE = "part_properties.json"
STAGING_DIR = ".import"
KINDS = ("part", "assembly", "drawing")  # import candidates; a simulation is brought in by naming its part (PDM-14)

ICLOUD_DRIVE = os.path.join("Library", "Mobile Documents", "com~apple~CloudDocs")
REF_SUFFIX = ".bwvault"  # pdm_folder.REF_SUFFIX
IMPORTED_FROM = "Imported from "  # check-in comment of an imported file, then its path
_BACKUP_RE = re.compile(r"\.(fcbak|fcstd\d+)\Z", re.IGNORECASE)
# FreeCAD's default labels say nothing about the part: the file name is used instead.
_GENERIC_LABEL_RE = re.compile(r"(Body|Part|Assembly|Unnamed)\d*\Z")
# Names a company property cannot take: FreeCAD's own object properties.
_FREECAD_NAMES = {
    "Label", "Label2", "Name", "Placement", "Shape", "Visibility", "State", "Group",
    "ExpressionEngine", "TypeId", "Content", "Document", "InList", "OutList",
    "PropertiesList", "Tip", "BaseFeature", "Origin", "Type", "Material", "ShapeMaterial",
}


@dataclass
class Issue:
    """A finding about a choice: level is "error" (blocks), "warning" or "info"."""

    level: str
    text: str


class _PlanIssue(Issue):
    """An Issue added by plan_numbers; dropped when the plan is made again."""


@dataclass
class ProgramSetup:
    code: str
    description: str = ""


@dataclass
class NumberingSetup:
    programs: list = field(default_factory=lambda: [ProgramSetup(DEFAULT_PROGRAM)])
    document_types: dict = field(default_factory=lambda: dict(DEFAULT_DOCUMENT_TYPES))
    first_parent: int = DEFAULT_FIRST_PARENT
    assemblies_as_parts: bool = False  # PDM-12: an assembly takes the Part document type


@dataclass
class VaultSetup:
    name: str
    folder: str
    numbering: NumberingSetup = field(default_factory=NumberingSetup)
    properties: list = field(default_factory=list)  # part_properties.json entries
    make_default: bool = True  # becomes BruceWorx/VaultPath (done by the caller)
    standards: dict = field(default_factory=dict)  # drawing standards, setting -> text (defaults for the rest)


@dataclass
class ImportCandidate:
    path: str
    kind: str  # "part", "assembly" or "drawing"
    label: str = ""
    existing_part_number: str = ""  # PartNumber found in the file, as written (may be invalid)
    proposed_part_number: str = ""  # PROG-DT-PPPPP-SSS, or the kept existing number
    document_type: str = ""
    include: bool = True
    issues: list = field(default_factory=list)  # [Issue]


@dataclass
class ImportReport:
    imported: list = field(default_factory=list)  # [(path, vault part number)]
    skipped: list = field(default_factory=list)  # [(path, reason)]
    failed: list = field(default_factory=list)  # [(path, error text)]


def _errors(issues) -> list:
    return [i for i in issues if i.level == "error"]


# --- 1. name and location -------------------------------------------------------


def icloud_drive(home: str | None = None) -> str:
    """iCloud Drive's folder (it may not exist)."""
    return os.path.join(home or os.path.expanduser("~"), ICLOUD_DRIVE)


def default_parent_dir(home: str | None = None) -> str:
    """iCloud Drive's "BruceWorx Vaults" when iCloud Drive exists, else ~/Documents/BruceWorx Vaults."""
    home = home or os.path.expanduser("~")
    cloud = icloud_drive(home)
    if os.path.isdir(cloud):
        return os.path.join(cloud, VAULT_PARENT_NAME)
    return os.path.join(home, "Documents", VAULT_PARENT_NAME)


def safe_folder_name(name: str) -> str:
    """``name`` as a folder name: ``/ \\ :``, characters Windows and sync
    services refuse, and control characters become ``-``; leading dots (a
    hidden folder) and trailing dots and spaces are dropped."""
    text = re.sub(r'[/\\:<>"|?*\x00-\x1f\x7f]', "-", str(name))
    text = re.sub(r"\s+", " ", text).strip().lstrip(".").rstrip(". ")
    return text or "Vault"


def suggest_folder(name: str, parent: str | None = None) -> str:
    """<parent>/<name>, with characters that are unsafe in a folder name replaced."""
    return os.path.join(parent or default_parent_dir(), safe_folder_name(name))


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _inside(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or under it (symlinks resolved)."""
    path, folder = _real(path), _real(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def _ancestors(path: str) -> list:
    out = []
    current = os.path.dirname(path)
    while current and current not in out:
        out.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return out


def _vault_refs(folder: str) -> list:
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(REF_SUFFIX) and not n.startswith("._"))
    except OSError:
        return []


def check_location(folder: str, home: str | None = None) -> list:
    """Issues with making a vault in folder (not created yet, or empty, is fine).

    Errors: the folder already is a vault, lies inside one, is a non-empty
    folder or a file, or cannot be created or written. Warnings: inside a git
    work tree or a build folder; a folder holding only hidden files. Info: the
    folder is synced by iCloud Drive (or may be, under ~/Documents and
    ~/Desktop).
    """
    if not str(folder or "").strip():
        return [Issue("error", "Choose a folder for the vault.")]
    issues = []
    home = home or os.path.expanduser("~")
    folder = os.path.abspath(os.path.expanduser(folder))
    if os.path.isdir(folder):
        refs = _vault_refs(folder)
        try:
            entries = os.listdir(folder)
        except OSError:
            entries = []
        visible = [n for n in entries if not n.startswith(".")]
        if refs:
            issues.append(Issue("error", f"{folder} already is a vault ({refs[0]}). Open it instead."))
        elif visible:
            issues.append(Issue(
                "error",
                f"{folder} is not empty ({len(visible)} items). Choose a new or empty folder; "
                "existing files can be imported in the last step.",
            ))
        elif entries:
            shown = ", ".join(sorted(entries)[:3])
            issues.append(Issue("warning", f"{folder} holds hidden files ({shown}); they stay."))
    elif os.path.exists(folder):
        issues.append(Issue("error", f"{folder} is a file, not a folder."))
    for ancestor in _ancestors(folder):
        refs = _vault_refs(ancestor)
        if refs:
            issues.append(Issue("error", f"{folder} is inside the vault {os.path.join(ancestor, refs[0])}."))
            break
    existing = folder
    while not os.path.exists(existing):
        parent = os.path.dirname(existing)
        if parent == existing:
            break
        existing = parent
    if not os.path.isdir(existing):
        if existing != folder:
            issues.append(Issue("error", f"Cannot create {folder}: {existing} is a file."))
    elif not os.access(existing, os.W_OK | os.X_OK):
        what = "write in" if existing == folder else "create folders in"
        issues.append(Issue("error", f"Cannot {what} {existing} (no permission)."))
    chain = [folder] + _ancestors(folder)
    for ancestor in chain:
        if os.path.exists(os.path.join(ancestor, ".git")):
            issues.append(Issue("warning", f"Inside the git work tree {ancestor}; git would see every vault file."))
            break
    for ancestor in chain:
        if os.path.isfile(os.path.join(ancestor, "CMakeCache.txt")) or os.path.basename(ancestor) == "build":
            issues.append(Issue("warning", f"Inside the build folder {ancestor}; a clean build may delete it."))
            break
    cloud = icloud_drive(home)
    if _inside(folder, cloud):
        issues.append(Issue("info", "Synced by iCloud Drive; one FreeCAD at a time writes to it."))
    elif os.path.isdir(cloud) and any(_inside(folder, os.path.join(home, sub)) for sub in ("Documents", "Desktop")):
        issues.append(Issue("info", "If Desktop & Documents sync is on, iCloud syncs this folder too."))
    return issues


# --- 2. numbering -----------------------------------------------------------------


def validate_numbering(setup: NumberingSetup) -> list:
    """Errors for codes the numbering file would refuse; empty when all is fine."""
    issues = []
    programs = set()
    if not setup.programs:
        issues.append(Issue("error", "Add at least one program code."))
    for program in setup.programs:
        code = getattr(program, "code", program)
        try:
            norm = numbering.normalize_program(code)
        except ValueError as exc:
            issues.append(Issue("error", f"Program: {exc}."))
            continue
        if norm in programs:
            issues.append(Issue("error", f"Program {norm} is listed twice."))
        programs.add(norm)
    types = setup.document_types or {}
    if not types:
        issues.append(Issue("error", "Add at least one document type."))
    codes = set()
    for raw, name in types.items():
        try:
            code = numbering.normalize_document_type(raw)
        except ValueError as exc:
            issues.append(Issue("error", f"Document type: {exc}."))
            continue
        if len(str(raw).strip()) != 2:
            issues.append(Issue("error", f"Document type {raw!r} must be two digits, such as {code}."))
            continue
        if code in codes:
            issues.append(Issue("error", f"Document type {code} is listed twice."))
        codes.add(code)
        if not str(name or "").strip():
            issues.append(Issue("error", f"Document type {code} needs a name."))
    first = setup.first_parent
    if isinstance(first, bool) or not isinstance(first, int) or not 1 <= first <= 99999:
        issues.append(Issue("error", f"First parent number {first!r} must be 1..99999 (PPPPP)."))
    return issues


def example_number(setup: NumberingSetup, program: str | None = None, document_type: str | None = None) -> str:
    """The first part number the vault would give, e.g. "BW-01-01001-001" (no revision).

    Defaults: the first program and the first document type. Raises
    ValueError when the setup is invalid.
    """
    if program is None:
        if not setup.programs:
            raise ValueError("no program")
        program = getattr(setup.programs[0], "code", setup.programs[0])
    if document_type is None:
        codes = list(setup.document_types or {})
        if not codes:
            raise ValueError("no document type")
        document_type = codes[0]
    return numbering.format_part_number(program, document_type, setup.first_parent, 1)


def numbering_data(setup: NumberingSetup) -> dict:
    """numbering.json content: every program x document type starts at first_parent; P/Ns carry no revision."""
    codes = sorted(numbering.normalize_document_type(code) for code in setup.document_types)
    programs = {}
    for program in setup.programs:
        code = numbering.normalize_program(getattr(program, "code", program))
        programs[code] = {
            "description": getattr(program, "description", "") or "",
            "counters": {dt: {"next_parent": int(setup.first_parent), "parents": {}} for dt in codes},
        }
    data = {
        "source": "json",
        "format": numbering.PART_NUMBER_FORMAT,
        "document_types_file": DOCUMENT_TYPES_FILE,
        # Where counters added later by Vault Settings start (sources.py ignores these).
        "first_parent": int(setup.first_parent),
        "programs": programs,
    }
    if setup.assemblies_as_parts:
        data[ASSEMBLIES_AS_PARTS] = True
    return data


def document_types_data(setup: NumberingSetup) -> dict:
    """document_types.json content (no placeholder flags)."""
    types = {numbering.normalize_document_type(code): {"name": str(name).strip()}
             for code, name in setup.document_types.items()}
    return {"types": dict(sorted(types.items()))}


# --- 3. part properties -------------------------------------------------------------


def suggested_properties() -> list:
    """Company properties offered by the wizard, as part_properties.json entries.

    None repeats a built-in property: config/part_properties.json already has
    Description, Finish, Vendor and VendorPartNumber.
    """
    return [
        {"name": "MaterialSpec", "type": "string", "bom": True,
         "doc": "Material specification, e.g. ASTM B221 6061-T6"},
        {"name": "HeatTreatment", "type": "string",
         "doc": "Heat treatment, e.g. harden and temper to 40-45 HRC"},
        {"name": "ToleranceClass", "type": "enum",
         "values": ["ISO 2768-f", "ISO 2768-m", "ISO 2768-c", "ISO 2768-v", "Per drawing"],
         "default": "ISO 2768-m", "doc": "General tolerance class"},
        {"name": "Customer", "type": "string", "doc": "Customer the part is made for"},
        {"name": "Project", "type": "string", "bom": True, "doc": "Project or job"},
    ]


def properties_layer(entries: list) -> dict:
    """The part_properties.json layer holding ``entries``."""
    from bruceworx.partprops import SCHEMA_ID  # noqa: PLC0415

    return {
        "schema": SCHEMA_ID,
        "version": 1,
        "note": "Company part properties of this vault (Create Vault wizard).",
        "properties": [dict(entry) for entry in entries],
        "renamed": {},
    }


def validate_properties(entries: list) -> list:
    """Issues found by loading the entries as a schema layer over the built-in one.

    A built-in property or a FreeCAD property name cannot be reused.
    """
    from bruceworx.partprops import SchemaError, load_schema  # noqa: PLC0415

    issues = []
    builtin = load_schema(extra_layers=[], user_layer=False)
    names = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            issues.append(Issue("error", f"Property entry {entry!r} must be an object."))
            continue
        name = str(entry.get("name", "")).strip()
        if name in names:
            issues.append(Issue("error", f"Property {name} is listed twice."))
        names.add(name)
        if name in builtin.properties:
            issues.append(Issue("error", f"{name} is a built-in property; choose another name."))
        elif name in _FREECAD_NAMES:
            issues.append(Issue("error", f"{name} is a FreeCAD property name; choose another name."))
    if _errors(issues):
        return issues
    tmp_dir = tempfile.mkdtemp(prefix="bw-props-")
    try:
        path = os.path.join(tmp_dir, PROPERTIES_FILE)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(properties_layer(entries or []), handle)
        load_schema(extra_layers=[path], user_layer=False)
    except SchemaError as exc:
        issues.append(Issue("error", str(exc).replace(path, "company properties")))
    except (TypeError, ValueError) as exc:
        issues.append(Issue("error", f"Properties cannot be saved: {exc}"))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return issues


# --- 4. create ------------------------------------------------------------------------


def _write_json(path: str, data: dict) -> None:
    from bruceworx.pdm_folder import _write_once  # noqa: PLC0415

    _write_once(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def create_vault(setup: VaultSetup, actor: str | None = None):
    """Make the folder a vault and write its numbering and property files; returns the FolderVault.

    Every choice is checked first: an error raises ValueError before anything
    is written. If writing a file fails once the vault exists, the vault stays
    valid (the ref file is written first) and the error is raised.
    """
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415

    if not str(setup.name or "").strip():
        raise ValueError("The vault needs a name.")
    problems = _errors(check_location(setup.folder))
    problems += _errors(validate_numbering(setup.numbering))
    if setup.properties:
        problems += _errors(validate_properties(setup.properties))
    from bruceworx import drawing_standards  # noqa: PLC0415

    standards, found = drawing_standards.from_texts(setup.standards or {})
    problems += _errors(found)
    if problems:
        raise ValueError("\n".join(issue.text for issue in problems))
    vault = FolderVault.init(os.path.expanduser(setup.folder), setup.name.strip(), actor=actor)
    _write_json(os.path.join(vault.root, DOCUMENT_TYPES_FILE), document_types_data(setup.numbering))
    _write_json(numbering_path(vault.root), numbering_data(setup.numbering))
    if setup.properties:
        _write_json(properties_path(vault.root), properties_layer(setup.properties))
    drawing_standards.save(vault.root, standards)
    return vault


def root_of(path: str) -> str:
    """The vault folder for a vault folder or its ref-file path."""
    path = os.path.abspath(os.path.expanduser(path))
    return os.path.dirname(path) if os.path.isfile(path) else path


def numbering_path(vault_root: str) -> str:
    """The vault's numbering.json (for gui.gui_config_path when a vault is open)."""
    return os.path.join(root_of(vault_root), NUMBERING_FILE)


LIBRARY_DIR = "Standard Libraries"


def library_beside(project_root: str) -> str:
    """The Standard Libraries vault next to a project vault (``Vault/Projects`` beside
    ``Vault/Standard Libraries``), or "" when that neighbour is not a vault."""
    parent = os.path.dirname(os.path.abspath(root_of(project_root)))
    candidate = os.path.join(parent, LIBRARY_DIR)
    if os.path.isdir(candidate) and _vault_refs(candidate):
        return candidate
    return ""


def neighbour_vaults(vault_root: str) -> list:
    """``(name, folder)`` for every vault in the same parent folder, this one first.

    Projects and Standard Libraries sit side by side under ``Vault/``. The dashboard
    uses this list to switch; a vault with no neighbour is a one-item list."""
    from bruceworx import drawing_standards

    root = os.path.abspath(root_of(vault_root))
    parent = os.path.dirname(root)
    found = []
    try:
        names = os.listdir(parent)
    except OSError:
        names = []
    for name in names:
        if name.startswith("."):
            continue
        folder = os.path.join(parent, name)
        if os.path.isdir(folder) and _vault_refs(folder):
            found.append((drawing_standards.vault_name(folder), os.path.abspath(folder)))
    found.sort(key=lambda item: (item[1] != root, item[0].lower()))
    return found


def properties_path(vault_root: str) -> str:
    """The vault's part_properties.json (partprops' vault layer)."""
    return os.path.join(root_of(vault_root), PROPERTIES_FILE)


# --- 5. import ------------------------------------------------------------------------


def _read_document(path: str) -> dict:
    """Object types, (rank, order, props) per object and XLink files from an
    .FCStd's Document.xml."""
    with zipfile.ZipFile(path) as zf:
        data = zf.read("Document.xml")
    root = ET.fromstring(data)
    types = {obj.get("name", ""): obj.get("type", "") for obj in root.findall("Objects/Object")}
    found = []
    for order, obj in enumerate(root.findall("ObjectData/Object")):
        props = {}
        for prop in obj.findall("Properties/Property"):
            if prop.get("name") in ("Label", "PartNumber"):
                value = prop.find("String")
                props[prop.get("name")] = (value.get("value", "") if value is not None else "").strip()
        found.append((_rank(types.get(obj.get("name", ""), "")), order, props))
    links = []
    for element in root.iter():
        if element.tag == "XLink" and element.get("file"):
            links.append(element.get("file"))
        elif element.tag == "DocMap" and element.get("path"):
            links.append(element.get("path"))
    return {"types": set(types.values()), "objects": sorted(found, key=lambda f: f[:2]), "links": links}


def _rank(type_name: str) -> int:
    """Which object stands for the file: the assembly, then an App::Part, then a body."""
    return {"Assembly::AssemblyObject": 0, "App::Part": 1, "PartDesign::Body": 2}.get(type_name, 9)


def _kind(types: set) -> str:
    if "Assembly::AssemblyObject" in types:
        return "assembly"
    solid = any(t == "App::Part" or t.startswith(("PartDesign::", "Part::")) for t in types)
    if "TechDraw::DrawPage" in types and not solid:
        return "drawing"
    return "part"


def existing_key(text: str) -> str | None:
    """The vault number (no revision) for a PartNumber value, or None when it is
    not ``PROG-DT-PPPPP-SSS``, with or without ``-REV``."""
    text = str(text or "").strip().upper()
    if not text:
        return None
    try:
        numbering.parse_part_number(text)
        return pdm.strip_revision(text)
    except ValueError:
        pass
    try:
        numbering.parse_part_number(text + "-A")
        return text
    except ValueError:
        return None


def scan_for_import(folder: str, recursive: bool = True, document_types: dict | None = None,
                    assemblies_as_parts: bool = False) -> list:
    """ImportCandidates for the .FCStd files under folder, read from Document.xml (no FreeCAD).

    Hidden folders and vault folders (holding a ``*.bwvault``) are not
    entered. FreeCAD backups (``*.FCBak``, ``*.FCStd1``), unreadable files and
    repeats of a file already listed (same content) are listed with
    include=False and an Issue. With ``document_types`` (code -> name),
    document_type is filled too (else :func:`plan_numbers` does it).
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    paths = []
    if not _vault_refs(folder):  # a vault's own files are in it already
        for current, dirs, files in os.walk(folder):
            if current != folder and _vault_refs(current):
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if recursive else []
            for name in files:
                if not name.startswith(".") and (name.lower().endswith(".fcstd") or _BACKUP_RE.search(name)):
                    paths.append(os.path.join(current, name))
    hashes = {}
    return [_candidate(path, hashes, document_types, assemblies_as_parts) for path in sorted(paths)]


def _candidate(path: str, hashes: dict, document_types: dict | None = None,
               assemblies_as_parts: bool = False) -> ImportCandidate:
    """One file's ImportCandidate (scan_for_import); hashes (sha256 -> path) finds repeats."""
    from bruceworx.pdm_folder import sha256_of  # noqa: PLC0415

    candidate = ImportCandidate(path=path, kind="part", label=os.path.splitext(os.path.basename(path))[0])
    if _BACKUP_RE.search(path):
        candidate.include = False
        candidate.issues.append(Issue("info", "FreeCAD backup file; not imported."))
        return candidate
    try:
        info = _read_document(path)
        digest = sha256_of(path)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
        candidate.include = False
        candidate.issues.append(Issue("error", f"Cannot read the file: {exc}"))
        return candidate
    candidate.kind = _kind(info["types"])
    main = info["objects"][0] if info["objects"] and info["objects"][0][0] < 9 else None
    if main and main[2].get("Label") and not _GENERIC_LABEL_RE.fullmatch(main[2]["Label"]):
        candidate.label = main[2]["Label"]
    candidate.existing_part_number = next(
        (props["PartNumber"].upper() for _r, _o, props in info["objects"] if props.get("PartNumber")), "")
    if candidate.existing_part_number and existing_key(candidate.existing_part_number) is None:
        candidate.issues.append(Issue(
            "warning",
            f"PartNumber {candidate.existing_part_number!r} is not "
            f"{numbering.PART_NUMBER_FORMAT}; it gets a new number.",
        ))
    if digest in hashes:
        candidate.include = False
        candidate.issues.append(Issue("warning", f"Same content as {hashes[digest]}; not imported twice."))
    else:
        hashes[digest] = path
    if document_types:
        candidate.document_type = document_type_for_item(
            candidate.kind, document_types, {ASSEMBLIES_AS_PARTS: assemblies_as_parts})
    return candidate


def document_type_for(kind: str, document_types: dict) -> str:
    """The code named like the kind ("Part", "Assembly", "Drawing"; any case),
    else the first code."""
    codes = [(numbering.normalize_document_type(code), str(name)) for code, name in document_types.items()]
    if not codes:
        raise ValueError("no document types")
    for code, name in codes:
        if name.strip().lower() == kind:
            return code
    return codes[0][0]


def document_type_for_item(kind: str, document_types: dict, data: dict | None = None) -> str:
    """The document type code a new number of ``kind`` takes (PDM-12): :func:`document_type_for`,
    except that an assembly takes the Part code when the vault's numbering data (numbering.json,
    as loaded) says ``assemblies_as_parts``. Drawings keep their own type. Every path that picks a
    code for a new number goes through here, so the setting holds everywhere."""
    if kind == "assembly" and (data or {}).get(ASSEMBLIES_AS_PARTS):
        kind = "part"
    return document_type_for(kind, document_types)


def _vault_document_types(source, data: dict) -> dict:
    """Document types offered for new numbers (retired ones left out)."""
    return {code: str(entry.get("name", "")) for code, entry in source._document_types(data).items()
            if not entry.get("retired")}


def _linked_files(path: str) -> list:
    """Real paths of the files an .FCStd links to (XLinks)."""
    try:
        links = _read_document(path)["links"]
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
        return []
    base = os.path.dirname(os.path.abspath(path))
    out = []
    for link in links:
        real = os.path.realpath(link if os.path.isabs(link) else os.path.join(base, link))
        if real not in out:
            out.append(real)
    return out


def _claim(data: dict, key: str) -> bool:
    """Record a kept vault number in numbering data, so new numbers pass it
    by. False when its program or document type has no counter there."""
    parsed = numbering.parse_part_number(key + "-A")
    entry = next((v for k, v in data["programs"].items() if str(k).upper() == parsed["program"]), None)
    if not isinstance(entry, dict):
        return False
    bucket = entry.setdefault("counters", {}).get(parsed["document_type"])
    if not isinstance(bucket, dict):
        return False
    slot = bucket.setdefault("parents", {}).setdefault(parsed["parent"], {"next_sub": numbering.DEFAULT_NEXT_SUB})
    slot["next_sub"] = max(int(slot.get("next_sub", numbering.DEFAULT_NEXT_SUB)), int(parsed["sub"]) + 1)
    if int(parsed["parent"]) >= int(bucket.get("next_parent", numbering.DEFAULT_NEXT_PARENT)):
        bucket["next_parent"] = int(parsed["parent"]) + 1
    return True


def _already_imported(vault) -> dict:
    """Content hashes and original paths already in the vault -> part number."""
    seen = {}
    for pn, versions in vault.versions.items():
        for version in versions:
            seen.setdefault(version.sha256, pn)
            if version.comment.startswith(IMPORTED_FROM):
                seen.setdefault(version.comment[len(IMPORTED_FROM):], pn)
    return seen


def plan_numbers(candidates: list, vault_root: str, program: str, document_types: dict | None = None) -> list:
    """Fill proposed_part_number for included candidates without one (a preview: nothing is reserved).

    Reads the vault's numbering.json and simulates the reservations on a copy,
    so consecutive candidates get consecutive numbers and nothing is written.
    Valid existing part numbers are kept (new numbers pass them by). A number
    the vault already has, a repeated number, or a file the vault already
    holds turns the candidate off with an Issue. Calling it again replaces the
    previous plan. Returns ``candidates``.
    """
    from bruceworx.pdm_folder import FolderVault, sha256_of  # noqa: PLC0415
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    program = numbering.normalize_program(program)
    source = JsonNumberSource(numbering_path(vault_root))
    data = copy.deepcopy(source._load())
    types = document_types or _vault_document_types(source, data)
    vault = FolderVault(root_of(vault_root))
    done = _already_imported(vault)
    for candidate in candidates:
        candidate.issues = [i for i in candidate.issues if not isinstance(i, _PlanIssue)]
        candidate.proposed_part_number = ""
    keys = {}
    for candidate in [c for c in candidates if c.include]:
        try:
            known = done.get(sha256_of(candidate.path)) or done.get(os.path.abspath(candidate.path))
        except OSError:
            known = None
        if known:
            candidate.include = False
            candidate.issues.append(_PlanIssue("warning", f"Already in the vault as {known}."))
            continue
        key = existing_key(candidate.existing_part_number)
        if key is None:
            continue
        if key in vault.items:
            candidate.include = False
            candidate.issues.append(_PlanIssue("error", f"{key} is already an item in the vault."))
        elif key in keys:
            candidate.include = False
            candidate.issues.append(_PlanIssue("error", f"{key} is also the number of {keys[key]}."))
        else:
            keys[key] = candidate.path
            candidate.proposed_part_number = candidate.existing_part_number
            candidate.document_type = numbering.parse_part_number(key + "-A")["document_type"]
            if not _claim(data, key):
                candidate.issues.append(_PlanIssue(
                    "info", f"{key} is kept; its program or document type is not in this vault's numbering."))
    parts = {os.path.realpath(c.path) for c in candidates if c.include and c.kind != "drawing"}
    for candidate in [c for c in candidates if c.include]:
        if candidate.kind == "drawing" and not parts.intersection(_linked_files(candidate.path)):
            candidate.issues.append(_PlanIssue(
                "warning", "None of the parts this drawing shows is being imported; it will be skipped."))
            continue
        if candidate.proposed_part_number:
            continue
        try:
            if not candidate.document_type:
                candidate.document_type = document_type_for_item(candidate.kind, types, data)
            candidate.proposed_part_number = source._issue(
                data, program, candidate.document_type, None, None, write=True)[0]
        except (KeyError, ValueError) as exc:
            candidate.issues.append(_PlanIssue("error", f"No number: {exc}"))
    return candidates


def _children_first(candidates: list) -> list:
    """Parts before the assemblies that link them (depth first), so links can be rewritten
    to numbers that already exist. Input order is kept otherwise; cycles stop at the visit."""
    by_path = {os.path.realpath(c.path): c for c in candidates}
    out, seen = [], set()

    def visit(candidate):
        key = os.path.realpath(candidate.path)
        if key in seen:
            return
        seen.add(key)
        for child in _linked_files(candidate.path):
            if child in by_path:
                visit(by_path[child])
        out.append(candidate)

    for candidate in candidates:
        visit(candidate)
    return out


def _link_mapper(original: str, staged: str, numbered: dict):
    """mapper for pdm_workspace.rewrite_links: a link to an imported file becomes <P/N>.FCStd."""
    names = {}
    for real, key in numbered.items():
        names.setdefault(os.path.basename(real), []).append(key)

    def mapper(old):
        for base in (os.path.dirname(os.path.abspath(original)), os.path.dirname(os.path.abspath(staged))):
            real = os.path.realpath(old if os.path.isabs(old) else os.path.join(base, old))
            if real in numbered:
                return numbered[real] + ".FCStd"
        keys = names.get(os.path.basename(old), [])
        return keys[0] + ".FCStd" if len(keys) == 1 else None

    return mapper


class _Skip(Exception):
    """A candidate that cannot be imported, found before a number is reserved."""


def run_import(vault, candidates: list, program: str, writer=None, progress=None, known=None) -> ImportReport:
    """Reserve numbers, copy each file to staging, call writer(copy, part_number), create_item, checkin.

    writer(path, part_number) writes the number into the copy (the FreeCAD side passes one);
    progress(done, total, path) is called after each file and may return False to stop.

    ``vault`` is a FolderVault (or its path). Valid existing part numbers are
    kept and claimed in numbering.json first; every other included file
    reserves one new number (a file that fails after that leaves its number
    unused). Parts and assemblies go first, then drawings, each linked to the
    first imported part it shows (a drawing with none is skipped). The vault
    item is the number without revision; the staged copy is
    ``<vault>/.import/<number>.FCStd``, removed after check-in. Originals are
    only read. ``proposed_part_number`` is set to the number given. ``known`` (real path ->
    vault number) names files already in the vault, so links to them are rewritten too.
    """
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    if not hasattr(vault, "create_item"):
        vault = FolderVault(vault)
    program = numbering.normalize_program(program)
    source = JsonNumberSource(numbering_path(vault.root))
    data = source._load()
    types = _vault_document_types(source, data)
    report = ImportReport()
    included = []
    for candidate in candidates:
        if candidate.include:
            included.append(candidate)
        else:
            reasons = [i.text for i in candidate.issues if i.level in ("error", "warning")]
            report.skipped.append((candidate.path, reasons[0] if reasons else "not selected"))
    kept = [key for key in (existing_key(c.existing_part_number) for c in included) if key]
    if kept:
        for key in kept:
            _claim(data, key)
        source._save(data)
    ordered = _children_first([c for c in included if c.kind != "drawing"]) + [
        c for c in included if c.kind == "drawing"]
    staging = os.path.join(vault.root, STAGING_DIR)
    numbered = dict(known or {})  # real path of an original -> vault number
    for done, candidate in enumerate(ordered, 1):
        try:
            key = _import_one(vault, source, candidate, program, types, writer, staging, numbered)
        except _Skip as exc:
            report.skipped.append((candidate.path, str(exc)))
        except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the import
            report.failed.append((candidate.path, str(exc) or type(exc).__name__))
        else:
            report.imported.append((candidate.path, key))
        if progress is not None and progress(done, len(ordered), candidate.path) is False:
            report.skipped.extend((rest.path, "import stopped") for rest in ordered[done:])
            break
    try:
        os.rmdir(staging)
    except OSError:
        pass
    return report


def _import_one(vault, source, candidate, program, types, writer, staging, numbered) -> str:
    kind = candidate.kind if candidate.kind in KINDS else "part"
    drawing_of = None
    if kind == "drawing":
        drawing_of = next((numbered[p] for p in _linked_files(candidate.path) if p in numbered), None)
        if drawing_of is None:
            raise _Skip("a drawing must name the part it shows; none of its parts were imported")
    key = existing_key(candidate.existing_part_number)
    if key is not None:
        if key in vault.items:
            raise _Skip(f"{key} is already an item in the vault")
        number = candidate.existing_part_number
    else:
        number = source.allocate(program, candidate.document_type or document_type_for_item(kind, types, source._load()))
        candidate.document_type = numbering.parse_part_number(number)["document_type"]
        key = pdm.strip_revision(number)
    candidate.proposed_part_number = number
    os.makedirs(staging, exist_ok=True)
    staged = os.path.join(staging, key + ".FCStd")
    shutil.copyfile(candidate.path, staged)
    try:
        if writer is not None:
            writer(staged, key)
        # Links to files imported before this one now point at their numbers (plan section 7.3).
        from bruceworx.pdm_workspace import clear_link_errors, rewrite_links  # noqa: PLC0415

        if rewrite_links(staged, _link_mapper(candidate.path, staged, numbered)):
            # The writer opened the copy away from its parts: FreeCAD saved "Link not restored".
            clear_link_errors(staged)
        vault.create_item(key, kind, candidate.label, drawing_of)
        vault.checkout(key)
        try:
            vault.checkin(key, staged, comment=IMPORTED_FROM + os.path.abspath(candidate.path))
        except Exception:
            vault.undo_checkout(key)
            raise
    finally:
        # The staged copy, and the backups FreeCAD makes when the writer saves it
        # (<key>.FCBak, <key>.<stamp>.FCBak, <key>.FCStd1), so the staging folder empties.
        for name in os.listdir(staging):
            if name == key + ".FCStd" or (name.startswith(key + ".") and re.search(r"\.(FCBak|FCStd\d+)$", name)):
                try:
                    os.remove(os.path.join(staging, name))
                except OSError:
                    pass
    numbered[os.path.realpath(candidate.path)] = key
    return key



def candidates_for_files(paths, known=None, vault_root: str = "") -> list:
    """ImportCandidates for paths and, transitively, the files they link to (check-in, plan section 4).

    Files in ``known`` (real path -> vault number) and files inside ``vault_root`` are not
    candidates: links to them are rewritten instead.
    """
    known = known or {}
    inside = os.path.realpath(vault_root) + os.sep if vault_root else None
    hashes, out, seen = {}, [], set()
    todo = [os.path.realpath(p) for p in paths]
    while todo:
        path = todo.pop(0)
        if path in seen or path in known or (inside and path.startswith(inside)) or not os.path.isfile(path):
            continue
        seen.add(path)
        candidate = _candidate(path, hashes)
        out.append(candidate)
        if candidate.include:
            todo.extend(_linked_files(path))
    return out


def program_of(part_number: str, vault_root: str) -> str:
    """The program for new numbers next to part_number: its own when the vault's numbering
    has it, else the vault's first program, else the default."""
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    try:
        data = JsonNumberSource(numbering_path(vault_root))._load()
        codes = {str(k).upper() for k in data.get("programs", {})}
        offered = JsonNumberSource(numbering_path(vault_root)).programs()
    except Exception:  # noqa: BLE001 -- a vault without numbering still answers
        codes, offered = set(), []
    try:
        own = numbering.parse_part_number(pdm.strip_revision(part_number) + "-A")["program"]
    except ValueError:
        own = ""
    if own and own in codes:
        return own
    return offered[0] if offered else (own or DEFAULT_PROGRAM)


NEW_ITEM_KINDS = ("part", "assembly", "cots")  # cots: a bought item (MOD-05), numbered as a part


def new_item(vault, kind: str, description: str, make_file, program: str = "", document_type: str = "",
             category: str = "") -> str:
    """Number a new part or assembly and check it in as WIP 01, without an import (FND-08).

    ``vault`` is a FolderVault; ``make_file(path, part_number)`` writes the FreeCAD file
    (a part with its part properties, the number and revision 01 already set). The number
    is previewed first and taken only once the file exists, so a file that cannot be made
    burns no number. ``program`` defaults to the vault's first program, ``document_type``
    to the code named like the kind. ``category`` (optional) files the item in the Vault panel's
    category group, recorded right after create_item. Returns the part number; the item is not
    checked out.
    """
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    if kind not in NEW_ITEM_KINDS:
        raise ValueError(f"a new item is a part, an assembly or a COTS item, not {kind!r}")
    description = str(description or "").strip()
    if not description:
        raise ValueError("a new item needs a description")
    source = JsonNumberSource(numbering_path(vault.root))
    data = source._load()
    programs = source.programs()
    if not programs:
        raise ValueError("the vault's numbering has no program")
    program = numbering.normalize_program(program) if program else programs[0]
    types = _vault_document_types(source, data)
    code = (numbering.normalize_document_type(document_type) if document_type
            else document_type_for_item("part" if kind == "cots" else kind, types, data))
    key = pdm.strip_revision(source.preview(program, code))
    if key in vault.items:
        raise ValueError(f"{key} is already an item in the vault")
    staging = os.path.join(vault.root, STAGING_DIR)
    os.makedirs(staging, exist_ok=True)
    staged = os.path.join(staging, key + ".FCStd")
    try:
        make_file(staged, key)
        if not os.path.isfile(staged):
            raise ValueError(f"no file was made for {key}")
        taken = pdm.strip_revision(source.allocate(program, code))
        if taken != key:
            raise ValueError(f"the number moved on while the file was made ({taken}, not {key}); try again")
        vault.create_item(key, kind, description)
        if category and str(category).strip():
            vault.set_category(key, category)
        vault.checkout(key)
        try:
            vault.checkin(key, staged, comment=f"New {kind}: {description}")
        except Exception:
            vault.undo_checkout(key)
            raise
    finally:
        for name in os.listdir(staging):
            if name == key + ".FCStd" or (name.startswith(key + ".") and re.search(r"\.(FCBak|FCStd\d+)$", name)):
                try:
                    os.remove(os.path.join(staging, name))
                except OSError:
                    pass
        try:
            os.rmdir(staging)
        except OSError:
            pass
    return key


def set_categories(vault, mapping, dry_run: bool = False) -> dict:
    """File many items at once: ``mapping`` is {part_number: category} ("" clears).

    Returns {"set": [pn...], "unchanged": [pn...], "missing": [pn...]}. Items already in that
    category are left alone (no journal line); unknown part numbers are reported, not raised.
    ``dry_run`` reports without writing. Used by ``bw-hooks vault set-category`` and by
    library imports that tag what they create.
    """
    from bruceworx import pdm as _pdm  # noqa: PLC0415

    out = {"set": [], "unchanged": [], "missing": []}
    for pn, category in mapping.items():
        item = vault.items.get(pn)
        if item is None:
            out["missing"].append(pn)
            continue
        if item.category == _pdm.normalize_category(category):
            out["unchanged"].append(pn)
            continue
        if not dry_run:
            vault.set_category(pn, category)
        out["set"].append(pn)
    return out


def import_linked_files(vault, paths, program: str, known=None, writer=None, progress=None):
    """Import files a checked-in assembly links from outside the vault, with what they link in turn.

    New part numbers come from the vault's numbering (``program``); a file the vault already
    holds (same content or same original) is not imported again. Returns (ImportReport,
    {real path: vault number}) for every given file now in the vault.
    """
    from bruceworx.pdm_folder import sha256_of  # noqa: PLC0415

    known = dict(known or {})
    candidates = candidates_for_files(paths, known, vault.root)
    plan_numbers(candidates, vault.root, program)
    report = run_import(vault, candidates, program, writer, progress, known=known)
    mapping = {os.path.realpath(p): key for p, key in report.imported}
    done = _already_imported(vault)
    for path in paths:
        real = os.path.realpath(path)
        if real in mapping:
            continue
        found = known.get(real) or done.get(real)
        if not found and os.path.isfile(real):
            try:
                found = done.get(sha256_of(real))
            except OSError:
                found = None
        if found:
            mapping[real] = found
    return report, mapping


# --- 6. CSV template and vault settings ----------------------------------------------------
#
# One CSV file holds every table the wizard and Vault Settings edit. Columns, in this order:
#   table, code, name, description, type, values, default, bom, retired
# table is "program", "document_type", "property" or "standard". Programs use code + description;
# document types code + name; properties code (the property name) + name (label) +
# description + type (string/integer/enum, as the part_properties.json layer) + values
# ("|"-separated, enum only) + default + bom (yes/no). retired (yes/no) hides a row from new
# use without deleting it. Blank lines and rows whose first cell starts with "#" are ignored.
# Drawing standards (bruceworx.drawing_standards, <vault>/drawing_standards.json) use code (the
# setting, e.g. projection) + name (its value) + description (what it is; not read back); they are
# settings, not codes: any value may change, nothing is retired. A CSV without standard rows (older
# files) leaves the vault's standards as they are.
#
# After creation the tables only grow: Vault Settings may add rows, rename names and
# descriptions, and retire or bring back a row, but not delete a row or change a code.
# Where retired rows are kept:
#   numbering.json        "retired": true on the program entry
#   document_types.json   "retired": true on the type entry
#   part_properties.json  the entry moves from "properties" to "retired_properties", which
#                         partprops.load_schema does not read: new parts no longer get the
#                         property and the BOM drops its column, while parts that have it
#                         keep their value (applying a schema never removes a property).
# sources.JsonNumberSource.programs()/document_types() leave retired codes out, so the
# Assign Part Number pickers stop offering them; numbers under them still allocate.

CSV_COLUMNS = ("table", "code", "name", "description", "type", "values", "default", "bom", "retired")
CSV_TABLES = ("program", "document_type", "property")
CSV_COMMENT = "# table: program | document_type | property"
RETIRED_PROPERTIES = "retired_properties"  # part_properties.json key the schema loader ignores
STANDARD_TABLE = "standard"  # drawing standards rows; not in CSV_TABLES (nothing to retire)
STANDARD_COMMENT = "# table standard: code = setting, name = value (drawing standards)"
_TABLE_LABELS = {"program": "Program", "document_type": "Document type", "property": "Property",
                 STANDARD_TABLE: "Drawing standard"}
_TABLE_ALIASES = {
    "program": "program", "programs": "program",
    "document_type": "document_type", "document_types": "document_type", "doc_type": "document_type",
    "doc_types": "document_type", "documenttype": "document_type",
    "property": "property", "properties": "property",
    "standard": STANDARD_TABLE, "standards": STANDARD_TABLE, "drawing_standard": STANDARD_TABLE,
    "drawing_standards": STANDARD_TABLE,
}
# The property keys a CSV row carries; other keys of an entry (e.g. required) are kept on save.
_CSV_PROPERTY_KEYS = ("type", "label", "doc", "values", "default", "bom")
_TYPE_ALIASES = {"integer": "int", "text": "string", "list": "enum", "choice": "enum", "boolean": "bool"}
_CSV_TYPE_NAMES = {"int": "integer"}
_TRUE = {"yes", "y", "true", "1", "x"}
_FALSE = {"no", "n", "false", "0", ""}


@dataclass
class VaultSettings:
    """What Vault Settings edits, read from and written to a vault's files."""

    programs: list = field(default_factory=list)  # [ProgramSetup]
    document_types: dict = field(default_factory=dict)  # code -> name
    properties: list = field(default_factory=list)  # part_properties.json entries
    retired: dict = field(default_factory=lambda: {t: set() for t in CSV_TABLES})  # table -> codes
    standards: dict = field(default_factory=dict)  # drawing standards: setting -> text; {} leaves them alone
    assemblies_as_parts: bool | None = None  # PDM-12; None leaves the vault's setting alone


def _flag(text):
    """True/False for yes/no/true/false/1/0 (blank is no), None for anything else."""
    if isinstance(text, bool):
        return text
    value = str(text if text is not None else "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def _yes(flag) -> str:
    return "yes" if flag else "no"


def _key(text: str) -> str:
    """A header or table cell as a key: "Document Type " -> "document_type"."""
    return re.sub(r"[\s\-]+", "_", str(text).replace("﻿", "").strip().lower())


def _retired_of(settings: VaultSettings, table: str) -> set:
    return set((settings.retired or {}).get(table) or ())


def example_settings() -> VaultSettings:
    """The rows of the downloadable template: one program, three document types, one property,
    and the default drawing standards."""
    from bruceworx import drawing_standards  # noqa: PLC0415

    material = suggested_properties()[0]
    return VaultSettings(
        programs=[ProgramSetup(DEFAULT_PROGRAM, "BruceWorx")],
        document_types=dict(DEFAULT_DOCUMENT_TYPES),
        properties=[{"name": material["name"], "type": "string", "label": "Material spec",
                     "doc": material["doc"], "bom": True}],
        standards=drawing_standards.as_texts(drawing_standards.DrawingStandards()),
    )


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return _yes(value)
    return str(value)


def _csv_rows(settings: VaultSettings):
    for program in settings.programs:
        code = getattr(program, "code", program)
        yield {"table": "program", "code": code, "description": getattr(program, "description", "") or "",
               "retired": _yes(code in _retired_of(settings, "program"))}
    for code, name in (settings.document_types or {}).items():
        yield {"table": "document_type", "code": code, "name": name,
               "retired": _yes(code in _retired_of(settings, "document_type"))}
    for entry in settings.properties:
        kind = entry.get("type", "string")
        yield {
            "table": "property", "code": entry.get("name", ""), "name": entry.get("label", ""),
            "description": entry.get("doc", ""), "type": _CSV_TYPE_NAMES.get(kind, kind),
            "values": "|".join(str(v) for v in entry.get("values") or []) if kind == "enum" else "",
            "default": _cell(entry.get("default")), "bom": _yes(entry.get("bom")),
            "retired": _yes(entry.get("name") in _retired_of(settings, "property")),
        }
    if settings.standards:
        from bruceworx.drawing_standards import DESCRIPTIONS  # noqa: PLC0415

        for key, text in settings.standards.items():
            yield {"table": STANDARD_TABLE, "code": key, "name": text, "description": DESCRIPTIONS.get(key, "")}


def csv_template(settings: VaultSettings | None = None) -> str:
    """CSV text: the header, a comment row, then the rows of settings (or example rows when None).

    Excel dialect (CRLF line ends). Write it with :func:`write_csv_file`,
    which adds the UTF-8 byte-order mark Excel needs to read accents.
    """
    settings = example_settings() if settings is None else settings
    out = io.StringIO()
    writer = csv.writer(out, dialect="excel")
    writer.writerow(CSV_COLUMNS)
    writer.writerow([CSV_COMMENT])
    if settings.standards:
        writer.writerow([STANDARD_COMMENT])
    for row in _csv_rows(settings):
        writer.writerow([row.get(column, "") for column in CSV_COLUMNS])
    return out.getvalue()


def write_csv_file(path: str, settings: VaultSettings | None = None) -> str:
    """Save :func:`csv_template` to path as UTF-8 with a byte-order mark; returns path."""
    data = csv_template(settings).encode("utf-8-sig")
    _replace_bytes(os.path.abspath(os.path.expanduser(path)), data)
    return path


def read_csv_file(path: str) -> tuple:
    """:func:`parse_csv` of a file (UTF-8 with or without BOM; Windows-1252 as a fallback)."""
    with open(os.path.expanduser(path), "rb") as handle:
        return parse_csv(handle.read())


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_csv(text) -> tuple:
    """(VaultSettings, [Issue]) from CSV text (or bytes); errors name the line.

    Tolerant of what spreadsheets do to a CSV: a byte-order mark, CRLF line
    ends, header case and spaces, columns in another order, missing optional
    columns, extra columns (ignored), ";" or tab as the separator, document
    type 01 saved as 1. Rows with an error are left out of the result.
    """
    if isinstance(text, (bytes, bytearray)):
        text = _decode(bytes(text))
    text = str(text).lstrip("﻿")
    settings = VaultSettings()
    issues = []
    first = next((ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")), "")
    delimiter = ","
    if "," not in first:
        delimiter = next((d for d in (";", "\t") if d in first), ",")
    reader = csv.reader(io.StringIO(text, newline=""), dialect="excel", delimiter=delimiter)
    header = None
    seen = {table: {} for table in CSV_TABLES + (STANDARD_TABLE,)}  # code -> line
    start = 1
    for row in reader:
        line, start = start, reader.line_num + 1
        cells = [cell.strip() for cell in row]
        if not any(cells) or cells[0].startswith("#"):
            continue
        if header is None:
            header = [_key(cell) for cell in cells]
            if "table" not in header or "code" not in header:
                issues.append(Issue("error", f"Line {line}: the first row must name the columns "
                                             f"({', '.join(CSV_COLUMNS)})."))
                return settings, issues
            extra = [cells[i] for i, key in enumerate(header) if key and key not in CSV_COLUMNS]
            if extra:
                issues.append(Issue("info", f"Line {line}: column {', '.join(extra)} is ignored."))
            continue
        values = {}
        for index, key in enumerate(header):
            if key in CSV_COLUMNS and key not in values and index < len(cells):
                values[key] = cells[index]
        _parse_row(values, line, settings, seen, issues)
    if header is None:
        issues.append(Issue("error", f"Line 1: the file is empty; the first row must name the columns "
                                     f"({', '.join(CSV_COLUMNS)})."))
    else:
        for table in ("program", "document_type"):
            if not seen[table]:
                issues.append(Issue("warning", f"No {table} rows."))
    return settings, issues


def _parse_row(values: dict, line: int, settings: VaultSettings, seen: dict, issues: list) -> None:
    def error(text):
        issues.append(Issue("error", f"Line {line}: {text}"))

    raw_table = values.get("table", "")
    table = _TABLE_ALIASES.get(_key(raw_table))
    if table is None:
        error(f"table {raw_table!r} must be program, document_type, property or standard.")
        return
    label = _TABLE_LABELS[table]
    if table == STANDARD_TABLE:
        _parse_standard(values, line, settings, seen, issues)
        return
    retired = _flag(values.get("retired"))
    if retired is None:
        error(f"retired {values.get('retired')!r} must be yes or no.")
        return
    code = values.get("code", "")
    match = re.fullmatch(r'="(.*)"', code)  # ="01", how some sheets keep a leading zero
    code = match.group(1).strip() if match else code
    if not code:
        error(f"{label} needs a code.")
        return
    try:
        if table == "program":
            code = numbering.normalize_program(code)
        elif table == "document_type":
            code = numbering.normalize_document_type(code)
    except ValueError as exc:
        error(f"{exc}.")
        return
    if code in seen[table]:
        error(f"{label} {code} is already on line {seen[table][code]}.")
        return
    seen[table][code] = line
    if table == "program":
        settings.programs.append(ProgramSetup(code, values.get("description") or values.get("name", "")))
    elif table == "document_type":
        name = values.get("name") or values.get("description", "")
        if not name:
            error(f"document type {code} needs a name.")
            return
        settings.document_types[code] = name
    else:
        entry = _property_entry(code, values, line, issues)
        if entry is None:
            return
        settings.properties.append(entry)
    if retired:
        settings.retired[table].add(code)


def _parse_standard(values: dict, line: int, settings: VaultSettings, seen: dict, issues: list) -> None:
    """A drawing-standard row: code is the setting, name its value (checked, stored as canonical text)."""
    from bruceworx import drawing_standards  # noqa: PLC0415

    key = _key(values.get("code", ""))
    if not key:
        issues.append(Issue("error", f"Line {line}: drawing standard needs a code (the setting)."))
        return
    if key in seen[STANDARD_TABLE]:
        issues.append(Issue("error", f"Line {line}: drawing standard {key} is already on line "
                                     f"{seen[STANDARD_TABLE][key]}."))
        return
    seen[STANDARD_TABLE][key] = line
    if _flag(values.get("retired")):
        issues.append(Issue("warning", f"Line {line}: drawing standards cannot be retired; retired is ignored."))
    scratch = drawing_standards.DrawingStandards()
    found = drawing_standards.set_text(scratch, key, values.get("name", ""))
    if found is not None:
        issues.append(Issue("error", f"Line {line}: {found.text}"))
        return
    settings.standards[key] = drawing_standards.to_text(scratch, key)


def _property_entry(name: str, values: dict, line: int, issues: list):
    from bruceworx.partprops import FREECAD_TYPES  # noqa: PLC0415

    def error(text):
        issues.append(Issue("error", f"Line {line}: property {name} {text}"))

    kind_text = values.get("type", "").strip().lower() or "string"
    kind = _TYPE_ALIASES.get(kind_text, kind_text)
    if kind not in FREECAD_TYPES:
        error(f"has type {values.get('type')!r}; use string, integer or enum.")
        return None
    entry = {"name": name, "type": kind}
    if values.get("name"):
        entry["label"] = values["name"]
    if values.get("description"):
        entry["doc"] = values["description"]
    choices = [v.strip() for v in values.get("values", "").split("|") if v.strip()]
    if kind == "enum":
        if not choices:
            error("is an enum: list its values, separated by |.")
            return None
        if len(set(choices)) != len(choices):
            error("lists a value twice.")
            return None
        entry["values"] = choices
    elif choices:
        issues.append(Issue("warning", f"Line {line}: property {name}: values are for enum properties; ignored."))
    if values.get("default"):
        try:
            entry["default"] = _typed_default(kind, values["default"], choices)
        except ValueError as exc:
            error(f"default: {exc}.")
            return None
    bom = _flag(values.get("bom"))
    if bom is None:
        error(f"bom {values.get('bom')!r} must be yes or no.")
        return None
    if bom:
        entry["bom"] = True
    found = validate_properties([entry])
    issues.extend(Issue(i.level, f"Line {line}: {i.text}") for i in found)
    return None if _errors(found) else entry


def _typed_default(kind: str, text: str, choices: list):
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a whole number") from None
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a number") from None
    if kind == "bool":
        flag = _flag(text)
        if flag is None:
            raise ValueError(f"{text!r} is not yes or no")
        return flag
    if kind == "enum" and text not in choices:
        raise ValueError(f"{text!r} is not one of the values")
    return text


def settings_from_setup(numbering: NumberingSetup, properties: list) -> VaultSettings:
    """The wizard's choices as VaultSettings (to fill a CSV download in the wizard).
    Codes are normalised where they are valid and kept as typed otherwise."""
    numbering = numbering or NumberingSetup()
    programs = []
    for program in numbering.programs:
        code = getattr(program, "code", program)
        programs.append(ProgramSetup(_normal("program", code), getattr(program, "description", "") or ""))
    types = {_normal("document_type", code): str(name).strip() for code, name in (numbering.document_types or {}).items()}
    return VaultSettings(programs=programs, document_types=types,
                         properties=[dict(entry) for entry in properties or []])


def _normal(table: str, code) -> str:
    try:
        if table == "program":
            return numbering.normalize_program(code)
        if table == "document_type":
            return numbering.normalize_document_type(code)
    except ValueError:
        pass
    return str(code).strip()


def apply_to_setup(settings: VaultSettings, numbering: NumberingSetup) -> tuple:
    """(NumberingSetup, properties) with settings' tables put into the wizard's setup (first_parent kept).

    A new vault has nothing to retire: retired rows are left out.
    """
    numbering = numbering or NumberingSetup()
    programs = [ProgramSetup(p.code, p.description) for p in settings.programs
                if p.code not in _retired_of(settings, "program")]
    types = {code: name for code, name in settings.document_types.items()
             if code not in _retired_of(settings, "document_type")}
    properties = [dict(entry) for entry in settings.properties
                  if entry.get("name") not in _retired_of(settings, "property")]
    setup = NumberingSetup(programs=programs, document_types=types, first_parent=numbering.first_parent)
    return setup, properties


# --- vault settings of an existing vault ------------------------------------------------


def _read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _numbering_raw(root: str):
    path = numbering_path(root)
    return _read_json(path) if os.path.isfile(path) else None


def _types_path(root: str, data) -> str:
    ref = (data or {}).get("document_types_file", DOCUMENT_TYPES_FILE)
    return ref if os.path.isabs(ref) else os.path.join(root, ref)


def _types_table(raw) -> dict:
    """The code -> entry table inside a document-type file (or an embedded table)."""
    if isinstance(raw, dict) and isinstance(raw.get("types"), dict):
        return raw["types"]
    return raw if isinstance(raw, dict) else {}


def _type_entries(root: str, data) -> dict:
    """Normalised code -> entry of the vault's document types (empty when there are none)."""
    if data is not None and "document_types" in data:
        raw = data["document_types"]
    else:
        path = _types_path(root, data)
        raw = _read_json(path) if os.path.isfile(path) else {}
    out = {}
    for key, entry in _types_table(raw).items():
        if key in {"note", "placeholder", "source", "types"} or not isinstance(entry, dict):
            continue
        out[numbering.normalize_document_type(key)] = entry
    return out


def _properties_raw(root: str):
    path = properties_path(root)
    return _read_json(path) if os.path.isfile(path) else None


def load_settings(vault_root: str) -> VaultSettings:
    """The vault's programs, document types and company properties, retired rows included.

    A vault made without numbering (plain ``vault init``) gives empty tables;
    :func:`save_settings` then creates its numbering files.
    """
    root = root_of(vault_root)
    settings = VaultSettings()
    data = _numbering_raw(root)
    for code, entry in ((data or {}).get("programs") or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        code = str(code).upper()
        settings.programs.append(ProgramSetup(code, str(entry.get("description", "") or "")))
        if entry.get("retired"):
            settings.retired["program"].add(code)
    for code, entry in _type_entries(root, data).items():
        settings.document_types[code] = str(entry.get("name", ""))
        if entry.get("retired"):
            settings.retired["document_type"].add(code)
    if (data or {}).get(ASSEMBLIES_AS_PARTS):  # None (unset) reads as off and is left alone on save
        settings.assemblies_as_parts = True
    layer = _properties_raw(root) or {}
    for entry in layer.get("properties") or []:
        settings.properties.append(dict(entry))
    for entry in layer.get(RETIRED_PROPERTIES) or []:
        settings.properties.append(dict(entry))
        settings.retired["property"].add(entry.get("name"))
    from bruceworx import drawing_standards  # noqa: PLC0415

    if drawing_standards.exists(root):
        settings.standards = drawing_standards.as_texts(drawing_standards.load(root))
    return settings


def _buckets(data):
    for entry in ((data or {}).get("programs") or {}).values():
        if isinstance(entry, dict):
            yield from (entry.get("counters") or {}).items()


def first_parent_of(data) -> int:
    """Where new counters of numbering data start: its recorded ``first_parent``,
    else the start of a counter never used, else the lowest parent issued."""
    value = (data or {}).get("first_parent")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 99999:
        return value
    untouched = [b.get("next_parent") for _code, b in _buckets(data)
                 if isinstance(b, dict) and not b.get("parents") and isinstance(b.get("next_parent"), int)]
    if untouched:
        return min(untouched)
    issued = [int(p) for _code, b in _buckets(data) if isinstance(b, dict)
              for p in (b.get("parents") or {}) if str(p).isdigit()]
    return min(issued) if issued else DEFAULT_FIRST_PARENT


def used_codes(vault_root: str) -> dict:
    """{"program": set, "document_type": set, "property": set}: codes items in the vault already use.

    A program or document type is used when an item's number has it, or when
    its counter in numbering.json has issued a number. Every property in the
    vault's layer counts as used (parts may hold a value for it).
    """
    from bruceworx.pdm_folder import FolderVault, VaultFormatError  # noqa: PLC0415

    root = root_of(vault_root)
    used = {table: set() for table in CSV_TABLES}
    try:
        items = FolderVault(root).items
    except (OSError, VaultFormatError):
        items = {}
    for key in items:
        number = existing_key(key)
        if number:
            parsed = numbering.parse_part_number(number + "-A")
            used["program"].add(parsed["program"])
            used["document_type"].add(parsed["document_type"])
    data = _numbering_raw(root)
    first = first_parent_of(data)
    for code, entry in ((data or {}).get("programs") or {}).items():
        if not isinstance(entry, dict):
            continue
        for dt, bucket in (entry.get("counters") or {}).items():
            if not isinstance(bucket, dict):
                continue
            next_parent = bucket.get("next_parent", first)
            if bucket.get("parents") or (isinstance(next_parent, int) and next_parent > first):
                used["program"].add(str(code).upper())
                used["document_type"].add(_normal("document_type", dt))
    layer = _properties_raw(root) or {}
    for entry in (layer.get("properties") or []) + (layer.get(RETIRED_PROPERTIES) or []):
        if isinstance(entry, dict) and entry.get("name"):
            used["property"].add(entry["name"])
    return used


def validate_settings(settings: VaultSettings) -> list:
    """Errors in settings on their own: codes, names, repeats, retired codes that
    have no row, and at least one program and document type still offered."""
    issues = []
    codes = {}
    for table, rows in (("program", [getattr(p, "code", p) for p in settings.programs]),
                        ("document_type", list(settings.document_types))):
        codes[table] = set()
        for raw in rows:
            try:
                code = (numbering.normalize_program(raw) if table == "program"
                        else numbering.normalize_document_type(raw))
            except ValueError as exc:
                issues.append(Issue("error", f"{_TABLE_LABELS[table]}: {exc}."))
                continue
            if code in codes[table]:
                issues.append(Issue("error", f"{_TABLE_LABELS[table]} {code} is listed twice."))
            codes[table].add(code)
    for code, name in settings.document_types.items():
        if not str(name or "").strip():
            issues.append(Issue("error", f"Document type {code} needs a name."))
    codes["property"] = {entry.get("name") for entry in settings.properties if isinstance(entry, dict)}
    for table in CSV_TABLES:
        retired = {_normal(table, code) for code in _retired_of(settings, table)}
        for code in sorted(retired - codes[table], key=str):
            issues.append(Issue("error", f"Retired {table.replace('_', ' ')} {code} has no row."))
        if table != "property" and codes[table] and not codes[table] - retired:
            issues.append(Issue("error", f"Keep at least one {table.replace('_', ' ')} that is not retired."))
    if not settings.programs:
        issues.append(Issue("error", "Add at least one program code."))
    if not settings.document_types:
        issues.append(Issue("error", "Add at least one document type."))
    if settings.properties:
        issues.extend(validate_properties(settings.properties))
    if settings.standards:
        from bruceworx import drawing_standards  # noqa: PLC0415

        _standards, found = drawing_standards.from_texts(settings.standards)
        issues.extend(Issue(i.level, f"Drawing standard: {i.text}") for i in found)
    return issues


def _csv_view(entry: dict) -> dict:
    """The part of a property entry a CSV row carries (for comparing)."""
    view = {k: entry[k] for k in _CSV_PROPERTY_KEYS if k in entry and k != "bom"}
    view["bom"] = bool(entry.get("bom"))
    if entry.get("type") != "enum":
        view.pop("values", None)
    return view


def check_settings_change(old: VaultSettings, new: VaultSettings, used: dict | None = None) -> list:
    """Issues for a change: adding and renaming are fine; deleting a row or changing a code is an
    error (retire it instead); changing a used property's type is an error.

    ``used`` is :func:`used_codes`; None counts every existing code as used.
    Errors block saving, warnings (renames, enum values dropped) should be
    shown, infos list what is added, retired or brought back. Also includes
    :func:`validate_settings` of ``new``.
    """
    issues = list(validate_settings(new))
    old_maps = {
        "program": {_normal("program", p.code): p.description or "" for p in old.programs},
        "document_type": {_normal("document_type", c): n for c, n in old.document_types.items()},
        "property": {e.get("name"): e for e in old.properties},
    }
    new_maps = {
        "program": {_normal("program", p.code): p.description or "" for p in new.programs},
        "document_type": {_normal("document_type", c): n for c, n in new.document_types.items()},
        "property": {e.get("name"): e for e in new.properties if isinstance(e, dict)},
    }

    def is_used(table, code):
        return used is None or code in (used.get(table) or ())

    for table in CSV_TABLES:
        label = _TABLE_LABELS[table]
        old_map, new_map = old_maps[table], new_maps[table]
        added = [code for code in new_map if code not in old_map]
        for code in [c for c in old_map if c not in new_map]:
            text = old_map[code] if table != "property" else old_map[code].get("label", "")
            twin = next((a for a in added if text and
                         (new_map[a] if table != "property" else new_map[a].get("label", "")) == text), None)
            note = " Items in the vault use it." if table != "property" and is_used(table, code) else ""
            if twin:
                issues.append(Issue("error", f"{label} {code} became {twin}: a code cannot change once the vault "
                                             f"has it. Keep {code} (set retired to yes) and add {twin} as a new row."
                                             + note))
            else:
                issues.append(Issue("error", f"{label} {code} is missing: a row cannot be deleted once the vault "
                                             f"has it. Keep the row and set retired to yes instead." + note))
        for code in added:
            issues.append(Issue("info", f"Adds {label.lower()} {code}."))
        old_retired = {_normal(table, c) for c in _retired_of(old, table)}
        new_retired = {_normal(table, c) for c in _retired_of(new, table)}
        for code in sorted((new_retired - old_retired) & set(old_map), key=str):
            what = ("new parts no longer get it and the BOM drops its column; parts that have it keep their "
                    "value" if table == "property" else
                    "it is no longer offered for new numbers; numbers already issued stay")
            issues.append(Issue("info", f"Retires {label.lower()} {code}: {what}."))
        for code in sorted((old_retired - new_retired) & set(new_map), key=str):
            issues.append(Issue("info", f"Brings back {label.lower()} {code}."))
        for code in [c for c in new_map if c in old_map]:
            if table != "property":
                if str(old_map[code]).strip() != str(new_map[code]).strip():
                    what = "description" if table == "program" else "name"
                    issues.append(Issue("warning", f"{label} {code}: {what} changes from {old_map[code]!r} "
                                                   f"to {new_map[code]!r}."))
                continue
            issues.extend(_property_change(code, old_map[code], new_map[code], is_used(table, code)))
    for key, text in (new.standards or {}).items():
        before = (old.standards or {}).get(key)
        if before is not None and before != text:
            issues.append(Issue("info", f"Drawing standard {key} changes from {before!r} to {text!r}."))
    return issues


def _property_change(name: str, old: dict, new: dict, used: bool) -> list:
    issues = []
    before, after = _csv_view(old), _csv_view(new)
    if before.get("type") != after.get("type"):
        if used:
            return [Issue("error", f"Property {name} cannot change from {before.get('type')} to "
                                   f"{after.get('type')}: parts hold {before.get('type')} values. Retire it "
                                   "and add a property with a new name instead.")]
        issues.append(Issue("warning", f"Property {name} changes from {before.get('type')} to {after.get('type')}."))
    for key, what in (("label", "name"), ("doc", "description")):
        if before.get(key, "") != after.get(key, ""):
            issues.append(Issue("warning", f"Property {name}: {what} changes from {before.get(key, '')!r} "
                                           f"to {after.get(key, '')!r}."))
    dropped = [v for v in before.get("values") or [] if v not in (after.get("values") or [])]
    if dropped and after.get("type") == "enum":
        issues.append(Issue("warning", f"Property {name}: parts set to {', '.join(map(repr, dropped))} get the "
                                       "default value the next time the schema is applied to them."))
    if before.get("default") != after.get("default") or before["bom"] != after["bom"]:
        issues.append(Issue("info", f"Property {name}: default or BOM column changes."))
    return issues


def settings_change_issues(vault_root: str, new: VaultSettings) -> list:
    """:func:`check_settings_change` against the vault's current settings (a dry run of :func:`save_settings`)."""
    return check_settings_change(load_settings(vault_root), new, used_codes(vault_root))


def _replace_bytes(path: str, data: bytes) -> None:
    """Replace path atomically: write a ``.tmp`` sibling (not synced by iCloud), then rename."""
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _replace_json(path: str, data: dict) -> None:
    _replace_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _merged_property(old: dict | None, new: dict) -> dict:
    """new, keeping old's keys a CSV row does not carry (required, source, ...)."""
    if old is None:
        return dict(new)
    out = {k: v for k, v in old.items() if k not in _CSV_PROPERTY_KEYS}
    out.update(new)
    return out


def save_settings(vault_root: str, new: VaultSettings) -> None:
    """Write the vault's numbering.json (new program x type counters added, existing counters kept),
    document_types.json and part_properties.json; retired rows stay in the files, marked.

    Raises ValueError (nothing written) when :func:`check_settings_change`
    finds an error. New counters start at the vault's first parent (recorded
    as ``first_parent`` in numbering.json). In an older numbering file (P/N-REV
    form) a new program takes the revision the others have. A vault without
    numbering gets its numbering files, in the P/N form.
    """
    root = root_of(vault_root)
    problems = _errors(settings_change_issues(root, new))
    if problems:
        raise ValueError("\n".join(issue.text for issue in problems))
    standards = None
    if new.standards:
        from bruceworx import drawing_standards  # noqa: PLC0415

        standards, _found = drawing_standards.from_texts(new.standards, drawing_standards.load(root))
    data = _numbering_raw(root)
    if data is None:
        data = {"source": "json", "format": numbering.PART_NUMBER_FORMAT, "document_types_file": DOCUMENT_TYPES_FILE,
                "first_parent": DEFAULT_FIRST_PARENT, "programs": {}}
    data.setdefault("first_parent", first_parent_of(data))
    if new.assemblies_as_parts is not None:
        if new.assemblies_as_parts:
            data[ASSEMBLIES_AS_PARTS] = True
        else:
            data.pop(ASSEMBLIES_AS_PARTS, None)
    programs = data.setdefault("programs", {})
    first = int(data["first_parent"])
    revision = None  # only an older P/N-REV file gives its programs a revision
    if data.get("format", numbering.LEGACY_FORMAT) == numbering.LEGACY_FORMAT:
        revisions = [e.get("revision") for e in programs.values() if isinstance(e, dict) and e.get("revision")]
        revision = numbering.normalize_revision(data.get("first_revision") or (revisions[0] if revisions
                                                                                else LEGACY_FIRST_REVISION))

    # Programs, and a counter for every program x document type that has none.
    retired = {t: {_normal(t, c) for c in _retired_of(new, t)} for t in CSV_TABLES}
    types = {numbering.normalize_document_type(c): str(n).strip() for c, n in new.document_types.items()}
    for program in new.programs:
        code = numbering.normalize_program(program.code)
        key = next((k for k in programs if str(k).upper() == code), None)
        if key is None:
            key = code
            programs[key] = {"description": "", "counters": {}}
            if revision is not None:
                programs[key]["revision"] = revision
        entry = programs[key]
        entry["description"] = program.description or ""
        _mark(entry, code in retired["program"])
        counters = entry.setdefault("counters", {})
        for dt in types:
            if not any(_normal("document_type", k) == dt for k in counters):
                counters[dt] = {"next_parent": first, "parents": {}}

    # Document types: embedded in numbering.json or in their own file.
    embedded = "document_types" in data
    types_path = _types_path(root, data)
    if embedded:
        raw_types = data["document_types"]
    else:
        raw_types = _read_json(types_path) if os.path.isfile(types_path) else {"types": {}}
    table = _types_table(raw_types)
    for code, name in types.items():
        key = next((k for k in table if k not in {"note", "placeholder", "source", "types"}
                    and _normal("document_type", k) == code), None)
        if key is None or not isinstance(table[key], dict):
            key = key or code
            table[key] = {}
        table[key]["name"] = name
        _mark(table[key], code in retired["document_type"])

    # Company properties: offered ones in "properties", retired ones in "retired_properties".
    layer = _properties_raw(root)
    if layer is None and new.properties:
        layer = properties_layer([])
    if layer is not None:
        before = {e.get("name"): e for e in (layer.get("properties") or []) + (layer.get(RETIRED_PROPERTIES) or [])
                  if isinstance(e, dict)}
        merged = [_merged_property(before.get(e.get("name")), e) for e in new.properties]
        layer["properties"] = [e for e in merged if e.get("name") not in retired["property"]]
        layer[RETIRED_PROPERTIES] = [e for e in merged if e.get("name") in retired["property"]]
        if not layer[RETIRED_PROPERTIES]:
            del layer[RETIRED_PROPERTIES]
        found = _errors(validate_properties(merged))
        if found:
            raise ValueError("\n".join(issue.text for issue in found))

    # Numbering first: a counter may exist before its type is listed, not the other way round.
    _replace_json(numbering_path(root), data)
    if not embedded:
        _replace_json(types_path, raw_types)
    if layer is not None:
        _replace_json(properties_path(root), layer)
    if standards is not None:
        drawing_standards.save(root, standards)


def _mark(entry: dict, retired: bool) -> None:
    if retired:
        entry["retired"] = True
    else:
        entry.pop("retired", None)
