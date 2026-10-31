# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM reference model: items, revisions, lifecycle, change control, BOM (BW-PDM-02).

Storage-independent. Whichever home BW-PDM-01 picks (Ops as PLM, Ops + Git
LFS, a FreeCAD vault, or an open-source PLM), it must behave exactly like this
module; the tests in ``tests/test_pdm.py`` are the contract. No FreeCAD import.

Decisions this encodes (roadmap interview, 18-19 Oct 2026):

* The part number never contains the revision. Drawings and release packages
  print ``P/N-REV`` (:func:`display_part_number`).
* Pre-release revisions are numeric, ``01, 02, ...`` (ASME Y14.35 practice).
  Every review rejection moves to the next number.
* The first approval releases letter ``A``. After that a letter changes only
  through an approved ECR -> ECO. Letters skip I, O, Q, S, X and Z, and run
  ``A .. Y, AA, AB, ...``.
* States: WIP -> In Review -> Released; an item can be retired to Obsolete.
  "Superseded" is a flag, not a state: on an older released letter, or on a
  rejected pre-release number that work has moved past.
* Released revisions are read-only.
* A drawing is its own item, linked to its part. An ECO that opens the part
  opens the drawing too, and both release together.

Approved 24 Oct 2026 (PDM-02 A3, A9, A10 and the PDM-06 plan):

* Every decision needs its text here, so every client and a server get the
  same rule: a release note (approve, release ECO), a reject reason, an ECR
  problem, a decision reason and a retire reason.
* The BOM is kept per revision. Each line names the child item and the child
  revision seen when the BOM was recorded; release pins it. Where-used walks
  these resolved lines backwards, so it names exact assembly revisions; the
  ones that only float to a newer child are a second list ("may pick up").
* Every BOM line is a vault item: lines that are not are kept as ``missing``
  and stop the release.
* Retire is refused while a current released revision uses the item, by its
  BOM or, for a revision without a recorded BOM, by its released file's links
  (``file_link_users``, set by a store that has files).

MOD-03 (25 Oct 2026, built on the plan's recommendations):

* A configuration of a file with a P/N of its own is a *configuration item*
  (:meth:`Vault.add_configuration`); ``configuration_of`` names the file's
  item. It has no file and shares the file item's revisions (one file, one
  revision, as in SolidWorks PDM; :class:`ConfigurationRevisions`), so it is
  always in its file's state.
  Lifecycle steps on it are refused with the file's P/N; retiring the file's
  item retires its configurations.

PDM-09 (26 Oct 2026, plan approved 24 Oct):

* A *project* is a journal record (:meth:`Vault.create_project`): a code, a name, the
  numbering program, its top assembly, the units it is drawn in (``mm`` or ``in``) and
  its drawing profile. Its structure is never stored twice: it is the top assembly's
  BOM. :meth:`Vault.update_project` renames, re-points or closes it.

``Vault.strict`` (default on) applies these rules. A store replaying a journal
written before them turns it off while replaying: history is accepted as it
was recorded, new operations are checked. A replay also skips the BOM cycle
check per line; the store runs :meth:`Vault.find_cycle` once at the end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WIP = "WIP"
IN_REVIEW = "In Review"
RELEASED = "Released"
OBSOLETE = "Obsolete"
STATES = (WIP, IN_REVIEW, RELEASED, OBSOLETE)
INITIAL_STATE = WIP  # a new item's first revision

ITEM_TYPES = ("part", "assembly", "drawing", "cots")
UNITS = ("mm", "in")  # PDM-09: the two unit profiles a project or vault is drawn in
PROJECT_STATES = ("Open", "Closed")

# ASME Y14.35: skip letters that read like digits.
RELEASE_LETTERS = "ABCDEFGHJKLMNPRTUVWY"
PRERELEASE_WIDTH = 2

# Change numbers, per vault: ECR-0001, ECO-0001 (PDM-05, 23 Oct 2026).
ECR_PREFIX = "ECR"
ECO_PREFIX = "ECO"
CHANGE_WIDTH = 4


class LifecycleError(ValueError):
    """A transition the lifecycle does not allow."""


# ---------------------------------------------------------------------------
# Revision codes.


def is_prerelease(code: str) -> bool:
    return code.isdigit()


def first_prerelease() -> str:
    return "1".zfill(PRERELEASE_WIDTH)


def next_prerelease(code: str) -> str:
    if not is_prerelease(code):
        raise ValueError(f"{code!r} is not a pre-release revision")
    return str(int(code) + 1).zfill(PRERELEASE_WIDTH)


def release_index(code: str) -> int:
    """``A`` -> 1, ``Y`` -> 20, ``AA`` -> 21 (bijective base 20)."""
    if not code or any(ch not in RELEASE_LETTERS for ch in code):
        raise ValueError(f"{code!r} is not a release revision (letters {RELEASE_LETTERS})")
    value = 0
    for ch in code:
        value = value * len(RELEASE_LETTERS) + RELEASE_LETTERS.index(ch) + 1
    return value


def release_code(index: int) -> str:
    if index < 1:
        raise ValueError("release index starts at 1")
    base = len(RELEASE_LETTERS)
    letters = []
    while index:
        index, rem = divmod(index - 1, base)
        letters.append(RELEASE_LETTERS[rem])
    return "".join(reversed(letters))


def first_release() -> str:
    return RELEASE_LETTERS[0]


def next_release(code: str) -> str:
    return release_code(release_index(code) + 1)


def validate_revision(code: str) -> str:
    if is_prerelease(code):
        return code
    release_index(code)
    return code


def display_part_number(part_number: str, revision: str) -> str:
    """The form printed on drawings and in release packages."""
    return f"{part_number}-{validate_revision(revision)}"


def next_change_number(prefix: str, existing) -> str:
    """The next ``PREFIX-0001`` number after the highest one in ``existing``.

    Numbers written by hand in another form are ignored, never reused or parsed."""
    pattern = re.compile(rf"{re.escape(prefix)}-(\d+)")
    highest = 0
    for number in existing:
        match = pattern.fullmatch(str(number))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:0{CHANGE_WIDTH}d}"


def strip_revision(legacy: str) -> str:
    """``LE-01-01001-001-A`` -> ``LE-01-01001-001`` (migration of old labels)."""
    head, sep, tail = legacy.rpartition("-")
    if sep and tail and (tail.isalpha() or tail.isdigit()) and head.count("-") >= 3:
        return head
    return legacy


# ---------------------------------------------------------------------------
# Data model.


@dataclass
class Revision:
    code: str
    state: str = WIP
    eco: str | None = None  # ECO that opened it; None for the pre-release line
    superseded: bool = False
    history: list = field(default_factory=list)
    # Review decisions as dicts: {"op": "approve" | "reject" | "release_eco" | "retire", "note" / "reason",
    # "checklist", "attachments", and "by" / "at" when the store knows them}. Optional fields
    # (PDM-05); older journals simply have none.
    records: list = field(default_factory=list)
    # PDM-06: this revision's BOM ([BomLine]; None until one is recorded), the file version it
    # was read from, and lines that are not vault items ([{"name", "qty"}]).
    bom: list | None = None
    bom_version: str = ""
    bom_missing: list = field(default_factory=list)
    was: str = ""  # the pre-release number a first release came from ("01" for A)

    def record(self, op):
        """The last decision of kind ``op`` on this revision, or None."""
        return next((r for r in reversed(self.records) if r.get("op") == op), None)

    @property
    def editable(self) -> bool:
        return self.state == WIP


class ConfigurationRevision:
    """A configuration item's view of one revision of its file's item (MOD-03): the same code, state,
    history and records (changing them changes the file item's), but a node of its own in the BOM
    graph (which keys revisions by identity) and BOM fields of its own."""

    _SHARED = ("code", "state", "eco", "superseded", "history", "records", "was")

    def __init__(self, rev):
        object.__setattr__(self, "_rev", rev)
        self.bom = None
        self.bom_version = ""
        self.bom_missing = []

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_rev"), name)

    def __setattr__(self, name, value):
        if name in self._SHARED:
            setattr(self._rev, name, value)
        else:
            object.__setattr__(self, name, value)


class ConfigurationRevisions:
    """The revisions of a configuration item: views of its file item's list, kept in step with it."""

    def __init__(self, master):
        self._master = master
        self._views = {}

    def _view(self, rev):
        view = self._views.get(id(rev))
        if view is None or view._rev is not rev:
            view = self._views[id(rev)] = ConfigurationRevision(rev)
        return view

    def __iter__(self):
        return (self._view(r) for r in self._master.revisions)

    def __reversed__(self):
        return (self._view(r) for r in reversed(self._master.revisions))

    def __len__(self):
        return len(self._master.revisions)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self._view(r) for r in self._master.revisions[index]]
        return self._view(self._master.revisions[index])

    def append(self, _rev):
        raise LifecycleError("a configuration item's revisions are its file item's")


def normalize_category(category) -> str:
    """Trim and collapse whitespace; at most 60 characters; None -> ""."""
    text = " ".join(str(category or "").split())
    if len(text) > 60:
        raise ValueError("a category name is at most 60 characters")
    return text


@dataclass
class Item:
    part_number: str
    item_type: str = "part"
    description: str = ""
    drawing_of: str | None = None  # for drawings: the part they document
    revisions: list = field(default_factory=list)
    obsolete: bool = False
    # MOD-03: a configuration item names the file's item and its row; its revisions are the file's.
    configuration_of: str | None = None
    configuration: str = ""
    # Vault-panel grouping label ("Connectors"); "" = uncategorized. Not part of the part number.
    category: str = ""

    @property
    def latest(self) -> Revision:
        return self.revisions[-1]

    @property
    def state(self) -> str:
        """The state a part shows: its latest revision's, Obsolete once retired."""
        return OBSOLETE if self.obsolete else self.latest.state

    @property
    def released(self) -> Revision | None:
        for rev in reversed(self.revisions):
            if rev.state == RELEASED and not rev.superseded:
                return rev
        return None


@dataclass
class ECR:
    number: str
    problem: str
    items: list
    status: str = "Open"  # Open -> Approved | Rejected
    attachments: list = field(default_factory=list)  # [{"sha256", "size", "filename"}]
    reason: str = ""  # why it was approved or rejected
    records: list = field(default_factory=list)  # raise, decide (see Revision.records)


@dataclass
class ECO:
    number: str
    ecrs: list
    items: list
    status: str = "Open"  # Open -> Released (no Cancel until one is designed, PDM-02 A5)
    note: str = ""  # the release note
    records: list = field(default_factory=list)  # open, release


@dataclass
class Project:
    """A project (PDM-09): what the Structure view's picker lists and the units default of new
    documents made in it. Its structure is its top assembly's BOM."""

    code: str
    name: str
    program: str
    top: str  # the top assembly's P/N
    units: str = "mm"
    drawing_profile: str = ""  # the drawing standards profile ("" = the vault's)
    description: str = ""
    status: str = "Open"  # Open | Closed
    records: list = field(default_factory=list)  # create, update (see Revision.records)


@dataclass
class BomLine:
    """One BOM line of a parent revision (PDM-06): a child item, how many per parent, the find
    number, and the child revision it resolves to (seen at check-in, pinned at release)."""

    child: str
    qty: int | float = 1
    find: int | None = None
    rev: str | None = None
    unit: str = ""  # [BW] ROU-04: "" counts each; "m" and the like meter the quantity
    # [BW] ROU-05 (30 Oct): what to order by: the child's manufacturer and manufacturer P/N, read
    # from the part at check-in ("" for a made part). The P/N above is for finding it.
    manufacturer: str = ""
    mpn: str = ""

    def as_dict(self) -> dict:
        out = {"child": self.child, "qty": self.qty}
        if self.find is not None:
            out["find"] = self.find
        if self.rev:
            out["rev"] = self.rev
        if self.unit:
            out["unit"] = self.unit
        if self.manufacturer:
            out["manufacturer"] = self.manufacturer
        if self.mpn:
            out["mpn"] = self.mpn
        return out


def bom_line(raw) -> BomLine:
    """A BomLine from a BomLine, a dict ({"child", "qty", "find", "rev"}) or a tuple
    ``(child, qty[, find[, rev]])``, as older journals and callers write them."""
    if isinstance(raw, BomLine):
        return BomLine(raw.child, raw.qty, raw.find, raw.rev, raw.unit, raw.manufacturer, raw.mpn)
    if isinstance(raw, dict):
        find = raw.get("find")
        unit = str(raw.get("unit") or "")
        return BomLine(str(raw["child"]), _count(raw.get("qty", 1), unit), None if find in (None, "") else int(find),
                       raw.get("rev") or None, unit, str(raw.get("manufacturer") or ""), str(raw.get("mpn") or ""))
    raw = list(raw)
    find = raw[2] if len(raw) > 2 else None
    return BomLine(str(raw[0]), _count(raw[1] if len(raw) > 1 else 1),
                   None if find in (None, "") else int(find), (raw[3] if len(raw) > 3 else None) or None)


def _count(qty, unit: str = ""):
    """Quantities are counts (PDM-06), unless a unit meters them (ROU-04: a wire's metres), when a
    fraction is fine."""
    if unit:
        return float(qty)
    if isinstance(qty, bool) or (isinstance(qty, float) and not qty.is_integer()):
        raise ValueError(f"quantity must be a whole number, not {qty!r}")
    return int(qty)


@dataclass
class Use:
    """One where-used answer: an assembly revision that holds the part (PDM-06)."""

    part_number: str
    revision: str
    state: str
    current: bool  # the item's current released revision (or its latest, when nothing is released)
    quantity: int  # in total, through every path
    path: list  # [(P/N, revision)] from this revision down to the part
    exact: bool = True  # False: "may pick up", it floats to a newer released child


class Vault:
    """In-memory reference of the lifecycle rules. A real store implements the
    same operations over its own persistence."""

    def __init__(self):
        self.items: dict[str, Item] = {}
        self.ecrs: dict[str, ECR] = {}
        self.ecos: dict[str, ECO] = {}
        self.projects: dict[str, Project] = {}  # PDM-09
        # Merged into every decision record; a store sets who and when ({"by", "at"}).
        self.context: dict = {}
        # The rules of 24 Oct (required texts, BOM items, file links); a store replaying an older
        # journal turns this off while it replays.
        self.strict = True
        # file_link_users(part_number) -> {P/N}: released items whose released revision has no BOM
        # but whose released file links to part_number. A store with files sets it (A10).
        self.file_link_users = None

    def _entry(self, op, **fields):
        entry = {"op": op}
        entry.update(self.context)
        entry.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
        return entry

    def next_ecr_number(self):
        return next_change_number(ECR_PREFIX, self.ecrs)

    def next_eco_number(self):
        return next_change_number(ECO_PREFIX, self.ecos)

    # ----- items ------------------------------------------------------

    def create_item(self, part_number, item_type="part", description="", drawing_of=None):
        if part_number in self.items:
            raise LifecycleError(f"{part_number} already exists")
        if item_type not in ITEM_TYPES:
            raise ValueError(f"unknown item type {item_type!r}")
        if item_type == "drawing" and drawing_of not in self.items:
            raise LifecycleError("a drawing must name the part it documents")
        item = Item(part_number, item_type, description, drawing_of)
        item.revisions.append(Revision(first_prerelease(), INITIAL_STATE, history=["created"]))
        self.items[part_number] = item
        return item

    def set_item_type(self, part_number, item_type):
        """Part <-> COTS (MOD-05): a bought item that was made as a part, or the other way.
        Only between "part" and "cots"; refused for an obsolete item and when nothing changes.
        Recorded on the latest revision. The file's ItemType property follows at its next
        check-in; released files are not touched."""
        item = self._own(part_number, "change the type of")
        if item_type not in ("part", "cots"):
            raise ValueError(f"an item type changes only between part and cots, not to {item_type!r}")
        if item.item_type not in ("part", "cots"):
            raise LifecycleError(f"{part_number} is a{'n' if item.item_type[0] in 'aeiou' else ''} "
                                 f"{item.item_type}; only parts and COTS items change type")
        if item.obsolete:
            raise LifecycleError(f"{part_number} is obsolete")
        if item.item_type == item_type:
            raise LifecycleError(f"{part_number} is already {item_type}")
        rev = item.latest
        rev.history.append(f"item type: {item.item_type} -> {item_type}")
        rev.records.append(self._entry("set_item_type", item_type=item_type, was=item.item_type))
        item.item_type = item_type
        return item

    def set_category(self, part_number, category):
        """Tag an item with a category ("" clears it). Pure organisation: it never touches the
        part number, revision or state, so it is allowed in every state, obsolete included (a
        retired connector can still be filed away). Nothing is recorded on the revision; the
        journal event is the record. Unknown items raise KeyError via the lookup."""
        if part_number not in self.items:
            raise LifecycleError(f"{part_number} is not an item in this vault")
        item = self.items[part_number]
        item.category = normalize_category(category)
        return item

    def drawings_of(self, part_number):
        return [i for i in self.items.values() if i.drawing_of == part_number]

    # ----- configurations (MOD-03) ------------------------------------

    def add_configuration(self, part_number, configuration_of, configuration, description=""):
        """A configuration of ``configuration_of``'s file with a P/N of its own. It shares that item's
        revisions and state. Adding it again for the same file updates its row name and description
        (a renamed row); a P/N that names any other item is refused."""
        master = self.items.get(configuration_of)
        if master is None:
            raise LifecycleError(f"unknown item {configuration_of}")
        if master.configuration_of:
            raise LifecycleError(f"{configuration_of} is itself a configuration of {master.configuration_of}")
        if master.item_type not in ("part", "assembly", "cots"):
            raise LifecycleError(f"a {master.item_type} has no configurations")
        if master.obsolete:
            raise LifecycleError(f"{configuration_of} is obsolete")
        if not (configuration or "").strip():
            raise LifecycleError("a configuration item needs the configuration's name")
        if part_number == configuration_of:
            raise LifecycleError(f"{part_number} is the file's own number; that configuration needs no item")
        item = self.items.get(part_number)
        if item is not None:
            if item.configuration_of != configuration_of:
                raise LifecycleError(f"{part_number} already exists")
            item.configuration, item.description = configuration, description or item.description
            return item
        item = Item(part_number, master.item_type, description, configuration_of=configuration_of,
                    configuration=configuration)
        item.revisions = ConfigurationRevisions(master)  # one file, one revision
        self.items[part_number] = item
        master.latest.history.append(f"configuration {configuration}: {part_number}")
        return item

    def configurations_of(self, part_number):
        """The configuration items of a file's item, in the order they were added."""
        return [i for i in self.items.values() if i.configuration_of == part_number]

    def _own(self, part_number, action):
        """Lifecycle steps happen on the file's item, not on one of its configurations (MOD-03)."""
        item = self.items[part_number]
        if item.configuration_of:
            raise LifecycleError(f"{part_number} is configuration {item.configuration!r} of "
                                 f"{item.configuration_of}; {action} {item.configuration_of}, its file")
        return item

    # ----- projects (PDM-09) ------------------------------------------

    def create_project(self, code, name, program, top, units="mm", drawing_profile="", description=""):
        """A project record. ``code`` is unique; ``top`` names an assembly item that is not
        obsolete; ``units`` is ``mm`` or ``in``. The name and program are required."""
        code = str(code or "").strip()
        if not code:
            raise LifecycleError("a project needs a code")
        if code in self.projects:
            raise LifecycleError(f"project {code} already exists")
        self._text(name, "a project name")
        self._text(program, "the project's numbering program")
        self._check_top(top)
        if units not in UNITS:
            raise ValueError(f"units must be one of {', '.join(UNITS)}, not {units!r}")
        project = Project(code, str(name).strip(), str(program).strip().upper(), top, units,
                          drawing_profile or "", description or "")
        project.records.append(self._entry("create_project"))
        self.projects[code] = project
        return project

    def update_project(self, code, name=None, top=None, units=None, drawing_profile=None, description=None,
                       status=None):
        """Rename a project, point it at another top assembly, change its units or drawing
        profile, or close (and reopen) it. Fields left None keep their value."""
        project = self.projects.get(code)
        if project is None:
            raise LifecycleError(f"unknown project {code}")
        changes = {}
        if name is not None:
            self._text(name, "a project name")
            changes["name"] = str(name).strip()
        if top is not None:
            self._check_top(top)
            changes["top"] = top
        if units is not None:
            if units not in UNITS:
                raise ValueError(f"units must be one of {', '.join(UNITS)}, not {units!r}")
            changes["units"] = units
        if drawing_profile is not None:
            changes["drawing_profile"] = drawing_profile
        if description is not None:
            changes["description"] = description
        if status is not None:
            if status not in PROJECT_STATES:
                raise ValueError(f"a project is {' or '.join(PROJECT_STATES)}, not {status!r}")
            changes["status"] = status
        if not changes:
            raise LifecycleError(f"nothing to change on project {code}")
        for key, value in changes.items():
            setattr(project, key, value)
        project.records.append(self._entry("update_project", **changes))
        return project

    def _check_top(self, top):
        item = self.items.get(top)
        if item is None:
            raise LifecycleError(f"unknown item {top}")
        if self.strict and item.item_type != "assembly":
            raise LifecycleError(f"{top} is a {item.item_type}; a project's top is an assembly")
        if self.strict and item.obsolete:
            raise LifecycleError(f"{top} is obsolete")

    def projects_of(self, top):
        """The open projects whose top assembly is ``top``, in creation order."""
        return [p for p in self.projects.values() if p.top == top and p.status == "Open"]

    # ----- review and release ----------------------------------------

    def submit(self, part_number):
        self._own(part_number, "submit")
        rev = self._latest(part_number)
        self._require(rev, WIP, "submit")
        rev.state = IN_REVIEW
        rev.history.append("submitted")
        return rev

    def reject(self, part_number, reason="", checklist=None, attachments=None):
        item = self._own(part_number, "reject")
        rev = item.latest
        self._require(rev, IN_REVIEW, "reject")
        self._text(reason, "a reason for the rejection")
        rev.history.append(f"rejected: {reason}" if reason else "rejected")
        rev.records.append(self._entry("reject", reason=reason, checklist=checklist, attachments=attachments))
        if rev.eco is None:
            # Pre-release line: the rejected number stays in history; work
            # continues on the next number.
            rev.superseded = True
            nxt = Revision(next_prerelease(rev.code), history=[f"after rejection of {rev.code}"])
            item.revisions.append(nxt)
            return nxt
        rev.state = WIP  # ECO working letter is only spent on release
        return rev

    def approve(self, part_number, note="", checklist=None, attachments=None):
        """Release a pre-release revision as ``A``. ECO revisions release
        through :meth:`release_eco`, together with the rest of the ECO.

        ``note`` (the release note) and ``checklist`` (the release checklist as
        ticked, see ``release_check.py``) are recorded with the approval."""
        item = self._own(part_number, "approve")
        rev = item.latest
        self._require(rev, IN_REVIEW, "approve")
        if rev.eco is not None:
            raise LifecycleError(f"{part_number} rev {rev.code} releases with {rev.eco}")
        if item.released is not None:
            raise LifecycleError(f"{part_number} is already released; changes need an ECO")
        self._text(note, "a release note")
        self._require_bom_items(part_number, rev)
        rev.history.append(f"released from {rev.code}")
        rev.records.append(self._entry("approve", note=note, checklist=checklist, attachments=attachments))
        rev.was = rev.code
        rev.code = first_release()
        rev.state = RELEASED
        self._pin(rev)
        return rev

    def retire(self, part_number, reason=""):
        """Retire a released item (Obsolete): its released revision becomes Obsolete and
        nothing changes it again. Refused while a current released revision of an assembly
        uses it (by its BOM, or by its released file's links when it has no BOM), or while an
        ECO has its next revision in work. ``reason`` is required and recorded. The item's
        configuration items (MOD-03) retire with it, and their uses count as its own."""
        item = self._own(part_number, "retire")
        if item.obsolete:
            raise LifecycleError(f"{part_number} is already obsolete")
        if item.released is None:
            raise LifecycleError(f"{part_number} has no released revision to retire")
        latest = item.latest
        if latest.state in (WIP, IN_REVIEW) and latest.eco is not None:
            raise LifecycleError(f"{part_number} rev {latest.code} is in work under {latest.eco}; "
                                 f"release {latest.eco} first")
        self._text(reason, "a reason for retiring it")
        users = {f"{use.part_number} rev {use.revision}"
                 for pn in [part_number] + [c.part_number for c in self.configurations_of(part_number)]
                 for use in self.where_used_revisions(pn)
                 if use.exact and use.current and use.state == RELEASED}
        if self.strict and self.file_link_users is not None:
            for pn in self.file_link_users(part_number) or ():
                other = self.items.get(pn)
                if other is not None and other.released is not None and pn != part_number:
                    users.add(f"{pn} rev {other.released.code} (file link)")
        if users:
            raise LifecycleError(f"{part_number} is still used by {', '.join(sorted(users))}")
        rev = item.released
        rev.history.append(f"retired: {reason}" if reason else "retired")
        rev.records.append(self._entry("retire", reason=reason))
        rev.state = OBSOLETE
        item.obsolete = True
        for config in self.configurations_of(part_number):
            config.obsolete = True
        return rev

    # ----- change control ----------------------------------------------

    def raise_ecr(self, number, problem, items, attachments=None):
        """``number`` None takes the next ``ECR-0001`` number."""
        number = number or self.next_ecr_number()
        if number in self.ecrs:
            raise LifecycleError(f"{number} already exists")
        if not items:
            raise LifecycleError("an ECR needs at least one affected item")
        self._text(problem, "a problem description")
        for pn in items:
            self._own(pn, "raise the ECR on")
            if self.items[pn].obsolete:
                raise LifecycleError(f"{pn} is obsolete and no longer changes")
            if self.items[pn].released is None:
                raise LifecycleError(f"{pn} is not released; fix it in its pre-release line")
        ecr = ECR(number, problem, list(items), attachments=list(attachments or []))
        ecr.records.append(self._entry("raise_ecr"))
        self.ecrs[number] = ecr
        return ecr

    def decide_ecr(self, number, approved, reason="", attachments=None):
        ecr = self.ecrs[number]
        if ecr.status != "Open":
            raise LifecycleError(f"{number} is already {ecr.status}")
        self._text(reason, "a reason for the decision")
        ecr.status = "Approved" if approved else "Rejected"
        ecr.reason = reason or ""
        ecr.records.append(self._entry("decide_ecr", approved=bool(approved), reason=reason,
                                       attachments=attachments))
        return ecr

    def open_eco(self, number, ecr_numbers):
        """``number`` None takes the next ``ECO-0001`` number."""
        number = number or self.next_eco_number()
        if number in self.ecos:
            raise LifecycleError(f"{number} already exists")
        if not ecr_numbers:
            raise LifecycleError("an ECO needs at least one approved ECR")
        ecrs = [self.ecrs[n] for n in ecr_numbers]
        for ecr in ecrs:
            if ecr.status != "Approved":
                raise LifecycleError(f"{ecr.number} is {ecr.status}, not Approved")
        affected = []
        for ecr in ecrs:
            for pn in ecr.items:
                # A retired drawing stays retired; the part's live drawings come along.
                for related in [pn] + [d.part_number for d in self.drawings_of(pn) if not d.obsolete]:
                    if related not in affected:
                        affected.append(related)
        # Check every item before changing any, so a refusal leaves no partial ECO.
        for pn in affected:
            item = self.items[pn]
            if item.obsolete:
                raise LifecycleError(f"{pn} is obsolete and no longer changes")
            if item.latest.state in (WIP, IN_REVIEW):
                raise LifecycleError(f"{pn} already has work in progress ({item.latest.code})")
        for pn in affected:
            item = self.items[pn]
            base = item.released.code if item.released else None
            code = next_release(base) if base else first_release()
            item.revisions.append(Revision(code, eco=number, history=[f"opened by {number}"]))
        eco = ECO(number, list(ecr_numbers), affected)
        eco.records.append(self._entry("open_eco"))
        self.ecos[number] = eco
        return eco

    def release_eco(self, number, note="", checklists=None, attachments=None):
        """Release every item of the ECO together. ``checklists`` maps a part
        number to its release checklist; each released revision records its own
        with the shared ``note``."""
        eco = self.ecos[number]
        if eco.status != "Open":
            raise LifecycleError(f"{number} is {eco.status}")
        pending = [pn for pn in eco.items if self.items[pn].latest.state != IN_REVIEW]
        if pending:
            raise LifecycleError(f"{number}: not in review yet: {', '.join(pending)}")
        self._text(note, "a release note")
        for pn in eco.items:
            self._require_bom_items(pn, self.items[pn].latest)
        released = []
        for pn in eco.items:
            item = self.items[pn]
            old = item.released
            if old is not None:
                old.superseded = True
            rev = item.latest
            rev.state = RELEASED
            rev.history.append(f"released by {number}")
            rev.records.append(self._entry("release_eco", eco=number, note=note,
                                           checklist=(checklists or {}).get(pn)))
            released.append(rev)
        for rev in released:  # after all are released, so items of this ECO pin each other's new letter
            self._pin(rev)
        eco.status = "Released"
        eco.note = note or ""
        eco.records.append(self._entry("release_eco", note=note, attachments=attachments))
        return eco

    # ----- BOM (per revision, PDM-06) -------------------------------------------

    @property
    def bom(self):
        """Each item's current BOM as ``{parent: [(child, qty)]}``: the lines of its latest
        revision that has a BOM. The per-revision lines are :attr:`Revision.bom`."""
        out = {}
        for pn, item in self.items.items():
            rev = self._bom_revision(item)
            if rev is not None:
                out[pn] = [(line.child, line.qty) for line in rev.bom]
        return out

    def set_bom(self, parent, lines, revision=None, sha256="", missing=None):
        """Record the BOM of ``parent`` at ``revision`` (default: its latest revision), read from
        the file version ``sha256``. ``lines`` are BomLines, dicts or ``(child, qty[, find[, rev]])``;
        a line without ``rev`` resolves to the child's latest revision now. ``missing`` lists what
        the assembly holds that is not a vault item (``[{"name", "qty"}]``); it stops the release.

        Refused for an unknown child, a quantity below 1, a revision that is not WIP (a released
        BOM is frozen), or a BOM that would contain itself; the previous BOM is kept."""
        item = self.items.get(parent)
        if item is None:
            raise LifecycleError(f"unknown item {parent}")
        self._own(parent, "record the BOM of")
        rev = self._revision(item, revision) if revision else item.latest
        if rev is None:
            raise LifecycleError(f"{parent} has no revision {revision}")
        if self.strict and rev.state != WIP:
            raise LifecycleError(f"{parent} rev {rev.code} is {rev.state}: its BOM is frozen")
        new = []
        for raw in lines:
            line = bom_line(raw)
            child = self.items.get(line.child)
            if line.qty <= 0:
                raise ValueError("quantity must be positive")
            if child is None:
                raise LifecycleError(f"unknown child {line.child}")
            if line.rev and self._revision(child, line.rev) is None:
                raise LifecycleError(f"{line.child} has no revision {line.rev}")
            line.rev = line.rev or child.latest.code
            new.append(line)
        gaps = []
        for entry in missing or ():
            name, qty, unit = ((entry.get("name"), entry.get("qty", 1), str(entry.get("unit") or ""))
                               if isinstance(entry, dict) else (entry, 1, ""))
            gap = {"name": str(name), "qty": _count(qty, unit)}
            if unit:
                gap["unit"] = unit  # [BW] ROU-04
            for key in ("manufacturer", "mpn"):  # [BW] ROU-05: what to order by
                if isinstance(entry, dict) and entry.get(key):
                    gap[key] = str(entry[key])
            gaps.append(gap)
        previous = (rev.bom, rev.bom_version, rev.bom_missing)
        rev.bom, rev.bom_version, rev.bom_missing = new, sha256 or "", gaps
        # A replay (strict off) skips the whole-graph check: each line was checked when it was
        # written, and the store checks once after the replay (find_cycle; PDM-10, linear replay).
        cycle = self._cycle() if self.strict else None
        if cycle:
            rev.bom, rev.bom_version, rev.bom_missing = previous
            raise LifecycleError(f"BOM of {parent} would contain itself: {' -> '.join(cycle)}")
        return rev

    def find_cycle(self):
        """``[a, b, ..., a]`` when the items' current BOMs contain a cycle, else None. A store runs it
        once after replaying a journal (two devices' lines can make one that neither wrote)."""
        return self._cycle()

    def cycle_path(self, parent, children):
        """``[parent, child, ..., parent]`` when a BOM of ``parent`` holding ``children`` would
        contain itself (through the items' current BOMs), else None. Lets a client stop a check-in
        before it stores anything."""
        graph = self.bom
        for start in children:
            back = {start: None}
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node == parent:
                    path = []
                    while node is not None:
                        path.append(node)
                        node = back[node]
                    return [parent] + list(reversed(path))
                for child, _qty in graph.get(node, ()):
                    if child not in back:
                        back[child] = node
                        queue.append(child)
        return None

    def bom_of(self, part_number, revision=None) -> list:
        """The BomLines of a revision (default: the latest revision with a BOM); [] when none."""
        rev = self._start(part_number, revision)
        return list(rev.bom or ())

    def explode(self, top, revision=None):
        """Total quantity of every item under ``top`` at ``revision`` (default: its latest revision
        with a BOM), ``top`` itself excluded: sum over paths of the product of quantities, in
        topological order over the resolved revisions."""
        return self._totals(top, revision)[0]

    def flattened(self, top, revision=None):
        """Leaf items only (parts, COTS, revisions without a BOM), with total quantities."""
        return self._totals(top, revision)[1]

    def indented(self, top, revision=None) -> list:
        """The indented BOM of a revision, depth first in line order. Rows: {"level", "find",
        "part_number", "revision", "qty" (per parent), "total", "leaf", "missing"}; lines that are
        not vault items come last at their level, with "missing" True and the name as part number."""
        start = self._start(top, revision)
        rows = []

        def walk(rev, level, mult, seen):
            for line in rev.bom or ():
                child = self._resolve(line)
                if child is None:
                    continue
                total = mult * line.qty
                deeper = bool(child.bom) and id(child) not in seen
                rows.append({"level": level, "find": line.find, "part_number": line.child,
                             "revision": child.code, "qty": line.qty, "total": total,
                             "leaf": not deeper, "missing": False, "unit": line.unit,
                             "manufacturer": line.manufacturer, "mpn": line.mpn})
                if deeper:
                    walk(child, level + 1, total, seen | {id(child)})
            for gap in rev.bom_missing:
                rows.append({"level": level, "find": None, "part_number": gap["name"], "revision": "",
                             "qty": gap["qty"], "total": mult * gap["qty"], "leaf": True, "missing": True,
                             "unit": gap.get("unit", ""), "manufacturer": gap.get("manufacturer", ""),
                             "mpn": gap.get("mpn", "")})

        walk(start, 1, 1, {id(start)})
        return rows

    def where_used(self, part_number, released_only=False):
        """Every assembly with a revision that holds ``part_number`` (any revision of it) through
        resolved BOM lines, directly or indirectly. ``released_only``: only assemblies whose current
        released revision holds it (what stops a retire)."""
        return {use.part_number for use in self.where_used_revisions(part_number)
                if use.exact and (not released_only or (use.current and use.state == RELEASED))}

    def where_used_revisions(self, part_number, revision=None) -> list:
        """Which assembly revisions use ``part_number`` (at ``revision``, default any), as
        :class:`Use` rows: first the exact ones (reverse reachability over the resolved lines),
        then "may pick up": live revisions (current released, or in work) that reach it only when
        their lines float to the latest released child."""
        item = self.items[part_number]
        if revision:
            target = self._revision(item, revision)
            if target is None:
                raise LifecycleError(f"{part_number} has no revision {revision}")
            targets = {id(target)}
        else:
            targets = {id(rev) for rev in item.revisions}
        owner, exact_edges = self._graph()
        exact = self._reach(targets, exact_edges)
        uses = [self._use(owner, exact_edges, node, path, targets, True) for node, path in exact.items()]
        _owner, float_edges = self._graph(floating=True)
        for node, path in self._reach(targets, float_edges).items():
            pn, rev = owner[node]
            if node not in exact and self._live(self.items[pn], rev):
                uses.append(self._use(owner, float_edges, node, path, targets, False))
        uses.sort(key=lambda u: (not u.exact, not u.current, u.part_number, self._order(u.part_number, u.revision)))
        return uses

    def newer_children(self, parent, revision=None) -> list:
        """"Newer child released": lines of a revision whose child has a newer released letter than
        the revision the line is pinned to, as ``[(child, pinned, released)]``."""
        out = []
        for line in self._start(parent, revision).bom or ():
            child = self.items.get(line.child)
            target = self._resolve(line)
            if child is None or target is None or child.released is None or target is child.released:
                continue
            if is_prerelease(target.code) or release_index(target.code) < release_index(child.released.code):
                out.append((line.child, target.code, child.released.code))
        return out

    # ----- helpers ---------------------------------------------------------

    def _latest(self, part_number):
        return self.items[part_number].latest

    @staticmethod
    def _require(rev, state, action):
        if rev.state != state:
            raise LifecycleError(f"cannot {action} rev {rev.code}: it is {rev.state}, not {state}")

    def _text(self, text, what):
        """Required texts (PDM-02 A3): every decision says why."""
        if self.strict and not (text or "").strip():
            raise LifecycleError(f"{what} is required")

    def _require_bom_items(self, part_number, rev):
        """Every BOM line must be a vault item before a release (PDM-06)."""
        if self.strict and rev.bom_missing:
            names = ", ".join(gap["name"] for gap in rev.bom_missing)
            raise LifecycleError(f"{part_number} rev {rev.code} has BOM lines that are not vault items: {names}. "
                                 "Add them to the vault and check the assembly in again")

    @staticmethod
    def _revision(item, code):
        """The revision called ``code`` now, or the release that came from pre-release ``code``."""
        for rev in reversed(item.revisions):
            if rev.code == code:
                return rev
        for rev in reversed(item.revisions):
            if rev.was == code:
                return rev
        return None

    @staticmethod
    def _bom_revision(item):
        for rev in reversed(item.revisions):
            if rev.bom is not None:
                return rev
        return None

    def _start(self, part_number, revision):
        item = self.items.get(part_number)
        if item is None:
            raise LifecycleError(f"unknown item {part_number}")
        if revision:
            rev = self._revision(item, revision)
            if rev is None:
                raise LifecycleError(f"{part_number} has no revision {revision}")
            return rev
        return self._bom_revision(item) or item.latest

    def _resolve(self, line):
        """The child revision a line stands for: its recorded (pinned) revision; a line without one
        floats."""
        child = self.items.get(line.child)
        if child is None:
            return None
        rev = self._revision(child, line.rev) if line.rev else None
        return rev or child.released or child.latest

    def _float(self, line):
        child = self.items.get(line.child)
        return None if child is None else (child.released or child.latest)

    def _pin(self, rev):
        """At release, each line keeps the child revision it was recorded with; a rejected number
        moves on to the child's released (else latest) revision."""
        for line in rev.bom or ():
            child = self.items.get(line.child)
            if child is None:
                continue
            target = self._revision(child, line.rev) if line.rev else None
            if target is None or (target.superseded and is_prerelease(target.code)):
                target = child.released or child.latest
            line.rev = target.code

    @staticmethod
    def _live(item, rev):
        if item.obsolete:
            return False
        return rev is item.released or (rev is item.latest and rev.state in (WIP, IN_REVIEW))

    def _order(self, part_number, code):
        item = self.items.get(part_number)
        revs = item.revisions if item else []
        return next((i for i, r in enumerate(revs) if r.code == code), len(revs))

    def _graph(self, floating=False):
        """({id(rev): (P/N, rev)}, {id(parent rev): [(id(child rev), qty)]}) over every revision."""
        owner, edges = {}, {}
        for pn, item in self.items.items():
            for rev in item.revisions:
                owner[id(rev)] = (pn, rev)
        for item in self.items.values():
            for rev in item.revisions:
                if not rev.bom:
                    continue
                out = edges.setdefault(id(rev), [])
                for line in rev.bom:
                    target = self._float(line) if floating else self._resolve(line)
                    if target is not None:
                        out.append((id(target), line.qty))
        return owner, edges

    @staticmethod
    def _reach(targets, edges):
        """Every node that reaches one of ``targets``, with one path down to it (breadth first over
        the reversed edges, so the path is a shortest one)."""
        parents = {}
        for parent, lines in edges.items():
            for child, _qty in lines:
                parents.setdefault(child, []).append(parent)
        path = {t: [t] for t in targets}
        queue = list(targets)
        while queue:
            node = queue.pop(0)
            for parent in parents.get(node, ()):
                if parent not in path:
                    path[parent] = [parent] + path[node]
                    queue.append(parent)
        return {node: p for node, p in path.items() if node not in targets}

    def _use(self, owner, edges, node, path, targets, exact):
        memo = {}

        def count(n):
            if n not in memo:
                memo[n] = 0  # acyclic; guards a damaged journal
                memo[n] = sum(q * (1 if c in targets else count(c)) for c, q in edges.get(n, ()))
            return memo[n]

        pn, rev = owner[node]
        item = self.items[pn]
        current = rev is item.released or (item.released is None and rev is item.latest and not item.obsolete)
        return Use(pn, rev.code, rev.state, current, count(node),
                   [(owner[n][0], owner[n][1].code) for n in path], exact)

    def _totals(self, top, revision):
        start = self._start(top, revision)
        owner, edges = self._graph()
        order, seen = [], set()

        def visit(node):
            if node in seen:
                return
            seen.add(node)
            for child, _qty in edges.get(node, ()):
                visit(child)
            order.append(node)

        visit(id(start))
        total = {id(start): 1}
        for node in reversed(order):
            for child, qty in edges.get(node, ()):
                total[child] = total.get(child, 0) + total.get(node, 0) * qty
        total.pop(id(start))
        exploded, leaves = {}, {}
        for node, qty in total.items():
            pn = owner[node][0]
            exploded[pn] = exploded.get(pn, 0) + qty
            if not edges.get(node):
                leaves[pn] = leaves.get(pn, 0) + qty
        return exploded, leaves

    def _cycle(self):
        """A path that comes back to its start in the items' current BOMs, or None."""
        graph = {pn: [child for child, _qty in lines] for pn, lines in self.bom.items()}
        state, stack = {}, []

        def visit(node):
            if state.get(node) == 1:
                return stack[stack.index(node):] + [node]
            if state.get(node) == 2:
                return None
            state[node] = 1
            stack.append(node)
            for child in graph.get(node, ()):
                found = visit(child)
                if found:
                    return found
            stack.pop()
            state[node] = 2
            return None

        for node in list(graph):
            found = visit(node)
            if found:
                return found
        return None
