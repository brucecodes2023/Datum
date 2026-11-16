# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx part numbers: ``{PROG}-{DT}-{PPPPP}-{SSS}``, for example ``LE-01-01001-001``.

``PPPPP`` is the next parent drawing for a program and document type.
``SSS`` is the next sub-assembly under that parent. Callers pass a
:class:`NumberSource` (a local JSON file, or the BruceWorx Ops API).

The part number carries no revision (decided 19 Oct 2026, BW-PDM-03). The
revision is metadata of the item (``bruceworx.pdm``: 01, 02, ... in work, A at
the first release) and is printed next to the number as ``P/N-REV``.

Numbering files written before that decision declare the old form
``{PROG}-{DT}-{PPPPP}-{SSS}-{REV}`` (:data:`LEGACY_FORMAT`) and still issue
numbers with a revision until they are migrated
(:func:`bruceworx.sources.migrate_numbering_file`). Existing numbers migrate
unchanged except for the revision: :func:`migrate_part_number` and
:func:`migrate_labels`.
"""

from __future__ import annotations

import re

PN_FORMAT = "{PROG}-{DT}-{PPPPP}-{SSS}"
LEGACY_FORMAT = "{PROG}-{DT}-{PPPPP}-{SSS}-{REV}"
FORMATS = (PN_FORMAT, LEGACY_FORMAT)
# The form new vaults write (vault_setup, since 25 Oct 2026, FND-08): the P/N without revision.
# Files declaring LEGACY_FORMAT issue P/N-REV until migrated.
PART_NUMBER_FORMAT = PN_FORMAT
PARENT_WIDTH = 5
SUB_WIDTH = 3
DEFAULT_NEXT_PARENT = 1001
DEFAULT_NEXT_SUB = 1
_PROGRAM_RE = re.compile(r"[A-Z][A-Z0-9]{0,7}\Z")
_REVISION_RE = re.compile(r"[A-Z]{1,3}\Z")
_FIELDS = (
    r"(?P<program>[A-Z][A-Z0-9]{0,7})-"
    r"(?P<document_type>\d{2})-"
    r"(?P<parent>\d{5})-"
    r"(?P<sub>\d{3})"
)
PN_RE = re.compile(_FIELDS + r"\Z")
PART_NUMBER_RE = re.compile(_FIELDS + r"-(?P<revision>[A-Z]{1,3})\Z")
# A number as printed (P/N-REV): legacy letters, or a pdm pre-release such as 02.
_PRINTED_RE = re.compile(_FIELDS + r"(?:-(?P<revision>[A-Z]{1,3}|\d{2,3}))?\Z")


def normalize_program(program: str) -> str:
    """Return the canonical program code, or raise ValueError."""
    text = str(program).strip().upper()
    if not _PROGRAM_RE.fullmatch(text):
        raise ValueError(
            f"program {program!r} must be 1-8 letters or digits and start with a letter"
        )
    return text


def normalize_revision(revision: str) -> str:
    """Return the canonical revision, or raise ValueError."""
    text = str(revision).strip().upper()
    if not _REVISION_RE.fullmatch(text):
        raise ValueError(f"revision {revision!r} must be 1-3 letters")
    return text


def normalize_document_type(document_type: str) -> str:
    """Return a two-digit document type, or raise ValueError."""
    text = str(document_type).strip()
    if not text.isdigit() or len(text) > 2:
        raise ValueError(
            f"document type {document_type!r} must be a 2-digit code such as 01"
        )
    number = int(text)
    if number < 0 or number > 99:
        raise ValueError(f"document type {document_type!r} is outside 00..99")
    return f"{number:02d}"


def normalize_parent(parent) -> str:
    """Return a five-digit parent drawing number."""
    number = _bounded_int(parent, 99999, "parent drawing number")
    return f"{number:0{PARENT_WIDTH}d}"


def normalize_sub(sub) -> str:
    """Return a three-digit sub-assembly number."""
    number = _bounded_int(sub, 999, "sub-assembly number")
    return f"{number:0{SUB_WIDTH}d}"


def format_part_number(
    program: str,
    document_type: str,
    parent,
    sub,
    revision: str | None = None,
) -> str:
    """Format one part number. ``parent`` and ``sub`` are unpadded integers or digits.

    Without ``revision`` this is the P/N. A ``revision`` (1-3 letters) gives the
    legacy ``P/N-REV`` form, which only unmigrated numbering files still issue.
    """
    program = normalize_program(program)
    document_type = normalize_document_type(document_type)
    parent = normalize_parent(parent)
    sub = normalize_sub(sub)
    formatted = f"{program}-{document_type}-{parent}-{sub}"
    if revision is not None:
        formatted = f"{formatted}-{normalize_revision(revision)}"
        pattern = PART_NUMBER_RE
    else:
        pattern = PN_RE
    if not pattern.fullmatch(formatted):
        raise ValueError(f"refusing to emit invalid part number {formatted!r}")
    return formatted


def parse_part_number(text: str) -> dict:
    """Split a part number into its fields. Values stay zero-padded.

    Accepts the P/N and the legacy ``P/N-REV`` form; ``revision`` is None for a P/N.
    """
    candidate = str(text).strip().upper()
    match = PART_NUMBER_RE.fullmatch(candidate) or PN_RE.fullmatch(candidate)
    if not match:
        raise ValueError(
            f"part number {text!r} is not {PN_FORMAT} (example LE-01-01001-001)"
        )
    fields = match.groupdict()
    fields.setdefault("revision", None)
    return fields


def split_revision(text: str) -> tuple[str, str | None]:
    """``LE-01-01001-001-A`` -> ``("LE-01-01001-001", "A")``.

    A P/N comes back with None. A printed number with a pre-release revision
    (``LE-01-01001-001-02``) splits too. Raises ValueError for anything that is
    not a BruceWorx number, so a free-form label is never cut.
    """
    match = _PRINTED_RE.fullmatch(str(text).strip().upper())
    if not match:
        raise ValueError(
            f"{text!r} is not a part number ({PN_FORMAT}, optionally followed by -REV)"
        )
    fields = match.groupdict()
    pn = f"{fields['program']}-{fields['document_type']}-{fields['parent']}-{fields['sub']}"
    return pn, fields["revision"]


def migrate_part_number(text: str) -> str:
    """The P/N of an existing number: unchanged except that ``-REV`` is dropped."""
    return split_revision(text)[0]


def is_part_number(text: str) -> bool:
    """True for a P/N or a printed ``P/N-REV``."""
    try:
        split_revision(text)
    except ValueError:
        return False
    return True


def _bounded_int(value, upper: int, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if not text.isdigit():
            raise ValueError(f"{label} must be an integer, got {value!r}")
        number = int(text)
    if number < 0 or number > upper:
        raise ValueError(f"{label} {value!r} is outside 0..{upper}")
    return number


def part_objects(doc, names: list[str] | None = None) -> list:
    """Solid objects to number or export.

    Explicit ``names`` match ``Name`` or ``Label``. Otherwise keep objects
    that own a solid and drop anything that is a child of another kept
    object, so a PartDesign Body is numbered and its Pad is not.
    """
    objects = list(getattr(doc, "Objects", []) or [])
    if names:
        return _resolve_names(objects, names)
    candidates = [obj for obj in objects if _has_solid(obj)]
    child_names = set()
    kept = {obj.Name for obj in candidates}
    for obj in candidates:
        for child in getattr(obj, "OutList", None) or []:
            child_name = getattr(child, "Name", None)
            if child_name in kept:
                child_names.add(child_name)
    return [obj for obj in candidates if obj.Name not in child_names]


def assign_part_numbers(
    doc,
    source,
    program: str,
    document_type: str,
    objects=None,
    revision: str | None = None,
    parent: str | None = None,
):
    """Set object Labels and the document Label from ``source``.

    ``objects`` None selects :func:`part_objects`. With no ``parent``, the
    first number takes a new parent drawing and sub 001; further objects are
    the next subs under that parent. An explicit ``parent`` numbers every
    object as the next sub of that drawing. An empty selection still numbers
    the document Label with one allocation.

    Returns a dict suitable for JSON. Does not save the document.
    """
    targets = part_objects(doc) if objects is None else list(objects)
    count = len(targets) if targets else 1
    numbers = _allocate_group(
        source, program, document_type, count, revision=revision, parent=parent
    )
    if not targets:
        doc.Label = numbers[0]
        return _assignment_payload(doc, numbers[0], [])

    assignments = []
    for obj, number in zip(targets, numbers):
        old_label = getattr(obj, "Label", "")
        obj.Label = number
        assignments.append(
            {
                "name": obj.Name,
                "old_label": old_label,
                "part_number": number,
            }
        )
    doc.Label = numbers[0]
    return _assignment_payload(doc, numbers[0], assignments)


def migrate_labels(doc, objects=None) -> dict:
    """Drop ``-REV`` from part-number labels (BW-PDM-03 migration).

    Every object (default: all of ``doc.Objects``) whose ``Label`` or
    ``PartNumber`` property is a printed ``P/N-REV`` gets the P/N; so does the
    document Label. Free-form labels and labels that are already a P/N stay as
    they are, so running it twice changes nothing. The revision is returned with
    each change and written nowhere: the vault owns an item's revision (pdm.py).

    Returns a dict suitable for JSON. Does not save the document.
    """
    targets = list(getattr(doc, "Objects", []) or []) if objects is None else list(objects)
    changes = []
    for obj in targets:
        for attribute in ("Label", "PartNumber"):
            value = getattr(obj, attribute, None)
            migrated = _migrated(value)
            if migrated is None:
                continue
            setattr(obj, attribute, migrated[0])
            changes.append({
                "name": obj.Name,
                "property": attribute,
                "old": value,
                "part_number": migrated[0],
                "revision": migrated[1],
            })
    document = None
    migrated = _migrated(getattr(doc, "Label", None))
    if migrated is not None:
        document = {"old": doc.Label, "part_number": migrated[0], "revision": migrated[1]}
        doc.Label = migrated[0]
    return {
        "document_name": getattr(doc, "Name", ""),
        "document_label": getattr(doc, "Label", ""),
        "document": document,
        "changes": changes,
        "changed": bool(changes or document),
    }


def _migrated(value):
    """(P/N, revision) when ``value`` is a printed P/N-REV, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        pn, revision = split_revision(value)
    except ValueError:
        return None
    return (pn, revision) if revision is not None else None


def _allocate_group(source, program, document_type, count, revision, parent):
    numbers = []
    active_parent = parent
    for _ in range(count):
        if active_parent is None:
            number = source.allocate(program, document_type, revision=revision)
            active_parent = parse_part_number(number)["parent"]
        else:
            number = source.allocate(
                program,
                document_type,
                revision=revision,
                parent=active_parent,
            )
        numbers.append(number)
    return numbers


def _assignment_payload(doc, part_number: str, assignments: list) -> dict:
    parsed = parse_part_number(part_number)
    return {
        "document_name": doc.Name,
        "document_label": doc.Label,
        "assignments": assignments,
        "part_number": part_number,
        "program": parsed["program"],
        "document_type": parsed["document_type"],
        "parent": parsed["parent"],
        "sub": parsed["sub"],
    }


def _resolve_names(objects, names: list[str]) -> list:
    wanted = []
    for name in names:
        text = str(name).strip()
        if text:
            wanted.append(text)
    if not wanted:
        raise ValueError("object list is empty")
    found = []
    missing = []
    for text in wanted:
        match = None
        for obj in objects:
            if obj.Name == text or getattr(obj, "Label", None) == text:
                match = obj
                break
        if match is None:
            missing.append(text)
        else:
            found.append(match)
    if missing:
        known = ", ".join(sorted(obj.Name for obj in objects))
        raise ValueError(f"objects not found: {', '.join(missing)} (known names: {known})")
    return found


def _has_solid(obj) -> bool:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return False
    is_null = getattr(shape, "isNull", None)
    if callable(is_null) and is_null():
        return False
    solids = getattr(shape, "Solids", None)
    if solids is None:
        # Test doubles stand in for a solid when they don't model Solids.
        return True
    try:
        return len(solids) > 0
    except TypeError:
        return False
