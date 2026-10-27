# SPDX-License-Identifier: LGPL-2.1-or-later
"""Folder-backed PDM vault (BW-PDM-01 choice, 19 Oct 2026).

Any folder works: local disk, iCloud Drive, Dropbox, OneDrive, a NAS share.
The vault never relies on the sync service for anything but copying files,
and it is laid out so a sync service has nothing to conflict over::

    <folder>/
      <name>.bwvault            ref file: identifies the vault; written once
      objects/<ab>/<sha256>.<ext>   file versions; written once, never changed
      journal/<actor>.jsonl     append-only event log, one writer per file
      outbox/<actor>.jsonl      released BOMs for Ops (PDM-06), one line per BOM, append-only

The ref file is what a reader opens (FreeCAD, the CLI, later Ops) to view
and drive the vault. All state is rebuilt by replaying the journal through
:class:`bruceworx.pdm.Vault`, so the lifecycle rules are exactly the
reference model's.

Scope: one writer at a time (one FreeCAD instance). Several journals are
read and merged in timestamp order so a second device can be added later,
but simultaneous edits from two instances are not reconciled.

Sync-service hazards handled here:

* Duplicate copies (iCloud ``x 2.jsonl``, Dropbox ``x (conflicted copy)``,
  Syncthing ``x.sync-conflict-...``): every ``*.jsonl`` in ``journal/`` is
  read and events are de-duplicated by id, so a copy never loses or doubles
  an event.
* A half-synced last line is ignored until the rest arrives.
* Objects are written under a ``.tmp`` name (iCloud does not sync those)
  and renamed when complete; reads verify the SHA-256.
* A journal entry can arrive before its object: :meth:`FolderVault.missing_objects`
  lists them and :meth:`FolderVault.fetch` says so instead of failing oddly.

Listeners (:meth:`FolderVault.add_listener`) hear which items changed
lifecycle state, after an operation is recorded or a refresh replays new
events. FreeCAD uses this to write the part's ``LifecycleState`` property
(``bruceworx.partprops.attach_vault``); this module stays free of FreeCAD.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import uuid
from dataclasses import dataclass, field

from bruceworx import pdm
from bruceworx.pdm import WIP, LifecycleError

FORMAT = "bruceworx.vault"
FORMAT_VERSION = 1
REF_SUFFIX = ".bwvault"
OBJECTS = "objects"
JOURNAL = "journal"
OUTBOX = "outbox"
_ACTOR_SAFE = re.compile(r"[^A-Za-z0-9._@-]+")

# Lifecycle operations replayed through pdm.Vault, with their argument names.
# Names after the first optional one (PDM-05: checklist, note, reason, attachments)
# are written only when used, so journals from before them read exactly as they did.
LIFECYCLE_OPS = {
    "create_item": ("part_number", "item_type", "description", "drawing_of"),
    "submit": ("part_number",),
    "reject": ("part_number", "reason", "checklist", "attachments"),
    "approve": ("part_number", "note", "checklist", "attachments"),
    "retire": ("part_number", "reason"),
    "raise_ecr": ("number", "problem", "items", "attachments"),
    "decide_ecr": ("number", "approved", "reason", "attachments"),
    "open_eco": ("number", "ecr_numbers"),
    "release_eco": ("number", "note", "checklists", "attachments"),
    "set_bom": ("parent", "lines", "revision", "sha256", "missing"),
    "set_item_type": ("part_number", "item_type"),  # MOD-05: part <-> cots
    "add_configuration": ("part_number", "configuration_of", "configuration", "description"),  # MOD-03
    # PDM-09 (26 Oct 2026): a project is a journal record naming its top assembly.
    "create_project": ("code", "name", "program", "top", "units", "drawing_profile", "description"),
    "update_project": ("code", "name", "top", "units", "drawing_profile", "description", "status"),
}
FILE_OPS = ("checkout", "undo_checkout", "checkin")
# A check-in may also carry (PDM-09) ``made_from`` (for a drawing: P/N -> the SHA-256 of the version
# of each file it was made from, its closure C(D)) and ``fingerprint`` (for a part: its geometry,
# see bruceworx.product_tree). Both are optional and come from the FreeCAD side; a journal without
# them replays as before. Every Keystone copy must know an operation before the vault uses it.
CHECKIN_EXTRAS = ("made_from", "fingerprint")


class VaultFormatError(ValueError):
    """The folder or ref file is not a BruceWorx vault this code can read."""


@dataclass
class FileVersion:
    sha256: str
    size: int
    filename: str
    revision: str
    actor: str
    at: str
    comment: str = ""
    # PDM-09: what a drawing was made from ({P/N: sha256}) and a part's geometry fingerprint
    # ({"volume", "area", "centre", "box"}); None for versions checked in without them.
    made_from: dict | None = None
    fingerprint: dict | None = None

    @property
    def object_name(self) -> str:
        return object_name(self.sha256, self.filename)


@dataclass
class Event:
    id: str
    actor: str
    seq: int
    at: str
    op: str
    args: dict = field(default_factory=dict)

    def sort_key(self):
        return (self.at, self.actor, self.seq)

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "actor": self.actor, "seq": self.seq, "at": self.at,
             "op": self.op, "args": self.args},
            sort_keys=True, separators=(",", ":"),
        )


def object_name(sha256: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return os.path.join(OBJECTS, sha256[:2], sha256 + ext)


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def default_actor() -> str:
    """``user@host``; ``BW_PDM_ACTOR`` overrides."""
    actor = os.environ.get("BW_PDM_ACTOR") or f"{getpass.getuser()}@{socket.gethostname().split('.')[0]}"
    return _ACTOR_SAFE.sub("-", actor)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def find_ref_file(path: str) -> str:
    """Accept the ref file itself or the vault folder."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return path
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    refs = sorted(n for n in os.listdir(path) if n.endswith(REF_SUFFIX))
    if not refs:
        raise VaultFormatError(f"{path} has no {REF_SUFFIX} file")
    # A sync service may have duplicated the ref file; all copies are identical.
    return os.path.join(path, refs[0])


class FolderVault:
    """A :class:`pdm.Vault` whose every change is an event in a folder."""

    def __init__(self, ref_path: str, actor: str | None = None):
        self.ref_path = find_ref_file(ref_path)
        self.root = os.path.dirname(self.ref_path)
        with open(self.ref_path, encoding="utf-8") as handle:
            try:
                self.ref = json.load(handle)
            except json.JSONDecodeError as exc:
                raise VaultFormatError(f"{self.ref_path}: {exc}") from exc
        if self.ref.get("format") != FORMAT:
            raise VaultFormatError(f"{self.ref_path} is not a {FORMAT} file")
        if int(self.ref.get("version", 0)) > FORMAT_VERSION:
            raise VaultFormatError(
                f"{self.ref_path} is format {self.ref['version']}; this BruceWorx reads up to {FORMAT_VERSION}"
            )
        self.actor = _ACTOR_SAFE.sub("-", actor) if actor else default_actor()
        self.journal_path = os.path.join(self.root, JOURNAL, f"{self.actor}.jsonl")
        self.listeners: list = []
        self.model = None
        self.refresh()

    # ----- creation -----------------------------------------------------

    @classmethod
    def init(cls, folder: str, name: str, actor: str | None = None) -> "FolderVault":
        folder = os.path.abspath(folder)
        os.makedirs(folder, exist_ok=True)
        if any(n.endswith(REF_SUFFIX) for n in os.listdir(folder)):
            raise VaultFormatError(f"{folder} already holds a vault")
        safe = _ACTOR_SAFE.sub("-", name).strip("-") or "vault"
        ref = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "vault_id": str(uuid.uuid4()),
            "name": name,
            "created": _now(),
            "layout": {"objects": OBJECTS, "journal": JOURNAL},
        }
        for sub in (OBJECTS, JOURNAL):
            os.makedirs(os.path.join(folder, sub), exist_ok=True)
        ref_path = os.path.join(folder, safe + REF_SUFFIX)
        _write_once(ref_path, (json.dumps(ref, indent=2) + "\n").encode("utf-8"))
        return cls(ref_path, actor=actor)

    # ----- replay ---------------------------------------------------------

    def refresh(self):
        """Rebuild all state from the journal (after a sync, or on open)."""
        before = self.states() if self.model is not None else None
        self.model = pdm.Vault()
        # Rules newer than a journal line do not refuse it on replay; _record checks them.
        self.model.strict = False
        self.model.file_link_users = self._file_link_users
        self.versions: dict[str, list[FileVersion]] = {}
        self.locks: dict[str, str] = {}
        self.lock_times: dict[str, str] = {}  # P/N -> when its current lock was taken (PDM-10)
        self.released_files: dict[tuple[str, str], str] = {}
        self.problems: list[str] = []
        self.events: list[Event] = []  # every event, in replay order: readers need no second read
        self._seen: set[str] = set()
        self._seq = 0
        self._offsets: dict[str, tuple] = {}  # journal file -> (bytes up to its last full line, inode)
        self._replay(self._read_events(self._offsets))
        if before is not None:
            self._notify(before)

    def _replay(self, events):
        """Apply events in order, then check the BOMs for a cycle once (PDM-10: a linear replay)."""
        boms = False
        for event in events:
            self._seen.add(event.id)
            self.events.append(event)
            if event.actor == self.actor:
                self._seq = max(self._seq, event.seq)
            try:
                self._apply(event)
            except (LifecycleError, ValueError, KeyError, TypeError) as exc:
                self.problems.append(f"{event.at} {event.actor} {event.op}: {exc}")
            boms = boms or event.op == "set_bom"
        if boms:
            cycle = self.model.find_cycle()
            if cycle:
                self.problems.append(f"BOM cycle after merging the journals: {' -> '.join(cycle)}")

    def update(self) -> bool:
        """Read only what the journal files gained since the last read (PDM-10).

        New events that all sort after the last one applied are applied to the state held. Anything
        else replays the whole journal: a late line from another Mac (or one whose clock is behind),
        a file that shrank, was replaced or went away. Returns True when the state may have changed.
        """
        folder = os.path.join(self.root, JOURNAL)
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith(".jsonl"))
        except OSError:
            names = []
        if set(self._offsets) - set(names):
            self.refresh()
            return True
        new, problems, offsets = [], [], dict(self._offsets)
        for name in names:
            path = os.path.join(folder, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            start, inode = offsets.get(name, (0, info.st_ino))
            if info.st_ino != inode or info.st_size < start:
                self.refresh()
                return True
            if info.st_size == start:
                continue
            found, used = self._read_file(path, name, start, problems)
            offsets[name] = (start + used, info.st_ino)
            new.extend(e for e in found if e.id not in self._seen)
        self._offsets = offsets
        self.problems.extend(problems)
        if not new:
            return bool(problems)
        new = sorted({e.id: e for e in reversed(new)}.values(), key=Event.sort_key)
        if self.events and new[0].sort_key() <= self.events[-1].sort_key():
            self.refresh()
            return True
        before = self.states()
        self._replay(new)
        self._notify(before)
        return True

    @staticmethod
    def _read_file(path, name, start, problems):
        """The events in a journal file's complete lines from byte ``start``, and the bytes used.
        A last line without its newline is still syncing; it waits for the next read."""
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read()
        used = data.rfind(b"\n") + 1
        events = []
        for line in data[:used].decode("utf-8", "replace").split("\n"):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                events.append(Event(raw["id"], raw["actor"], int(raw["seq"]), raw["at"], raw["op"],
                                    raw.get("args", {})))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                problems.append(f"{name}: unreadable journal line skipped")
        return events, used

    def _read_events(self, offsets=None) -> list[Event]:
        """Every event of every journal file, de-duplicated by id, in replay order. ``offsets``
        (refresh only) records how far each file was read, for :meth:`update`."""
        folder = os.path.join(self.root, JOURNAL)
        events: dict[str, Event] = {}
        if not os.path.isdir(folder):
            return []
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(folder, name)
            try:
                inode = os.stat(path).st_ino
            except OSError:
                continue
            found, used = self._read_file(path, name, 0, self.problems)
            if offsets is not None:
                offsets[name] = (used, inode)
            for event in found:
                events.setdefault(event.id, event)
        return sorted(events.values(), key=Event.sort_key)

    def _apply(self, event: Event):
        args = event.args
        if event.op in LIFECYCLE_OPS:
            self.model.context = {"by": event.actor, "at": event.at}
            try:
                result = getattr(self.model, event.op)(**args)
            finally:
                self.model.context = {}
            if event.op == "approve":
                self._freeze(args["part_number"])
            elif event.op == "release_eco":
                for pn in self.model.ecos[args["number"]].items:
                    self._freeze(pn)
            return result
        pn = args.get("part_number")
        item = self.model.items.get(pn)
        if item is not None and item.configuration_of:  # MOD-03: a configuration has no file of its own
            raise LifecycleError(f"{pn} is configuration {item.configuration!r} of {item.configuration_of}; "
                                 f"its file is {item.configuration_of}")
        if event.op == "checkout":
            self._require_wip(pn, "check out")
            holder = self.locks.get(pn)
            if holder and holder != event.actor:
                raise LifecycleError(f"{pn} is checked out by {holder}")
            if holder != event.actor:
                self.lock_times[pn] = event.at  # a second check-out keeps the lock's start
            self.locks[pn] = event.actor
        elif event.op == "undo_checkout":
            if self.locks.get(pn) not in (None, event.actor) and not args.get("force"):
                raise LifecycleError(f"{pn} is checked out by {self.locks[pn]}")
            self.locks.pop(pn, None)
            self.lock_times.pop(pn, None)
        elif event.op == "checkin":
            self._require_wip(pn, "check in")
            if self.locks.get(pn) != event.actor:
                raise LifecycleError(f"{pn} is not checked out by {event.actor}")
            version = FileVersion(
                args["sha256"], int(args["size"]), args["filename"],
                self.model.items[pn].latest.code, event.actor, event.at, args.get("comment", ""),
                made_from=args.get("made_from") or None, fingerprint=args.get("fingerprint") or None,
            )
            self.versions.setdefault(pn, []).append(version)
            if not args.get("keep_lock"):
                self.locks.pop(pn, None)
                self.lock_times.pop(pn, None)
            return version
        else:
            raise ValueError(f"unknown operation {event.op!r}")
        return None

    def _freeze(self, pn):
        item = self.model.items[pn]
        versions = self.versions.get(pn)
        if versions:
            self.released_files[(pn, item.released.code)] = versions[-1].sha256

    def _require_wip(self, pn, action):
        rev = self.model.items[pn].latest
        if rev.state != WIP:
            raise LifecycleError(f"cannot {action} {pn} rev {rev.code}: it is {rev.state}, not {WIP}")

    # ----- recording --------------------------------------------------------

    def _record(self, op: str, args: dict):
        """Validate by applying to the in-memory state, then append the event.
        A refused operation writes nothing."""
        self._seq += 1
        event = Event(str(uuid.uuid4()), self.actor, self._seq, _now(), op, _jsonable(args))
        before = self.states()
        self.model.strict = True
        try:
            result = self._apply(event)
        except Exception:
            self._seq -= 1
            raise
        finally:
            self.model.strict = False
        os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._seen.add(event.id)
        self.events.append(event)
        if op in ("approve", "release_eco"):
            self._send_released_boms(event)
        self._notify(before)
        return result

    # ----- lifecycle state listeners ---------------------------------------

    def add_listener(self, listener):
        """Call ``listener({part_number: state})`` whenever items change state."""
        if listener not in self.listeners:
            self.listeners.append(listener)

    def remove_listener(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def states(self) -> dict:
        """Every item's lifecycle state (:attr:`pdm.Item.state`), by part number."""
        return {pn: item.state for pn, item in self.model.items.items()}

    def state_of(self, part_number) -> str | None:
        item = self.model.items.get(part_number)
        return item.state if item is not None else None

    def _notify(self, before: dict):
        changes = {pn: state for pn, state in self.states().items() if before.get(pn) != state}
        if not changes:
            return
        for listener in list(self.listeners):
            try:
                listener(dict(changes))
            except Exception as exc:  # noqa: BLE001 -- the event is recorded; a listener cannot undo it
                self.problems.append(f"state listener failed: {exc}")

    # pdm.Vault surface: same names and arguments, so the contract tests run
    # unchanged against a folder.
    @property
    def items(self):
        return self.model.items

    @property
    def ecrs(self):
        return self.model.ecrs

    @property
    def ecos(self):
        return self.model.ecos

    @property
    def bom(self):
        return self.model.bom

    def create_item(self, part_number, item_type="part", description="", drawing_of=None):
        return self._record("create_item", dict(part_number=part_number, item_type=item_type,
                                                description=description, drawing_of=drawing_of))

    def set_item_type(self, part_number, item_type):
        return self._record("set_item_type", dict(part_number=part_number, item_type=item_type))

    def add_configuration(self, part_number, configuration_of, configuration, description=""):
        return self._record("add_configuration", dict(part_number=part_number, configuration_of=configuration_of,
                                                      configuration=configuration, description=description))

    def configurations_of(self, part_number):
        return self.model.configurations_of(part_number)

    # ----- projects (PDM-09) ------------------------------------------------------

    @property
    def projects(self):
        return self.model.projects

    def create_project(self, code, name, program, top, units="mm", drawing_profile="", description=""):
        return self._record("create_project", _optional(dict(code=code, name=name, program=program, top=top,
                                                             units=units), drawing_profile=drawing_profile,
                                                        description=description))

    def update_project(self, code, name=None, top=None, units=None, drawing_profile=None, description=None,
                       status=None):
        args = {k: v for k, v in dict(name=name, top=top, units=units, drawing_profile=drawing_profile,
                                      description=description, status=status).items() if v is not None}
        return self._record("update_project", dict(code=code, **args))

    def projects_of(self, top):
        return self.model.projects_of(top)

    def submit(self, part_number):
        holder = self.locks.get(part_number)
        if holder:  # the file in review must be the one in the vault (PDM-05)
            raise LifecycleError(f"{part_number} is checked out by {holder}; check it in first")
        return self._record("submit", dict(part_number=part_number))

    def reject(self, part_number, reason="", checklist=None, attachments=None):
        return self._record("reject", _optional(dict(part_number=part_number, reason=reason),
                                                checklist=checklist, attachments=self.attach(attachments)))

    def approve(self, part_number, note="", checklist=None, attachments=None):
        return self._record("approve", _optional(dict(part_number=part_number), note=note,
                                                 checklist=checklist, attachments=self.attach(attachments)))

    def retire(self, part_number, reason=""):
        """Retire a released item (Obsolete); the reason is required and recorded (PDM-05). The
        released files' links count as uses for items without a BOM (pdm.Vault.retire)."""
        return self._record("retire", dict(part_number=part_number, reason=reason))

    def raise_ecr(self, number, problem, items, attachments=None):
        """``number`` None takes the next ``ECR-0001`` number. ``attachments`` are file
        paths (copied into the vault as write-once objects) or already stored records."""
        number = number or self.next_ecr_number()  # the journal records the number taken
        return self._record("raise_ecr", _optional(dict(number=number, problem=problem, items=list(items)),
                                                   attachments=self.attach(attachments)))

    def decide_ecr(self, number, approved, reason="", attachments=None):
        return self._record("decide_ecr", _optional(dict(number=number, approved=bool(approved)), reason=reason,
                                                    attachments=self.attach(attachments)))

    def open_eco(self, number, ecr_numbers):
        """``number`` None takes the next ``ECO-0001`` number."""
        number = number or self.next_eco_number()
        return self._record("open_eco", dict(number=number, ecr_numbers=list(ecr_numbers)))

    def release_eco(self, number, note="", checklists=None, attachments=None):
        return self._record("release_eco", _optional(dict(number=number), note=note, checklists=checklists,
                                                     attachments=self.attach(attachments)))

    def next_ecr_number(self):
        return self.model.next_ecr_number()

    def next_eco_number(self):
        return self.model.next_eco_number()

    def set_bom(self, parent, lines, revision=None, sha256="", missing=None):
        """Record a revision's BOM (PDM-06). The journal line names the revision and, per line,
        the child revision it resolved to, so a replay needs no guessing."""
        item = self.model.items.get(parent)
        if item is None:
            raise LifecycleError(f"unknown item {parent}")
        revision = revision or item.latest.code
        resolved = []
        for raw in lines:
            line = pdm.bom_line(raw)
            child = self.model.items.get(line.child)
            if line.rev is None and child is not None:
                line.rev = child.latest.code
            resolved.append(line.as_dict())
        gaps = [_gap(m) for m in (missing or ())]
        return self._record("set_bom", _optional(dict(parent=parent, lines=resolved, revision=revision),
                                                 sha256=sha256, missing=gaps))

    def drawings_of(self, part_number):
        return self.model.drawings_of(part_number)

    def bom_of(self, part_number, revision=None):
        return self.model.bom_of(part_number, revision)

    def cycle_path(self, parent, children):
        return self.model.cycle_path(parent, children)

    def explode(self, top, revision=None):
        return self.model.explode(top, revision)

    def flattened(self, top, revision=None):
        return self.model.flattened(top, revision)

    def indented(self, top, revision=None):
        return self.model.indented(top, revision)

    def where_used(self, part_number, released_only=False):
        return self.model.where_used(part_number, released_only=released_only)

    def where_used_revisions(self, part_number, revision=None):
        return self.model.where_used_revisions(part_number, revision)

    def newer_children(self, parent, revision=None):
        return self.model.newer_children(parent, revision)

    def _file_link_users(self, part_number):
        """Released items whose released revision has no BOM and whose released file links to
        ``part_number`` (PDM-02 A10: file links are a use until a BOM is recorded)."""
        from bruceworx.pdm_workspace import linked_files  # pdm_workspace imports this module

        users = set()
        for pn, item in self.model.items.items():
            rev = item.released
            if pn == part_number or rev is None or rev.bom is not None:
                continue
            version = self.released_version(pn)
            path = os.path.join(self.root, version.object_name) if version else ""
            if not path or not os.path.exists(path):
                continue
            if any(os.path.splitext(os.path.basename(n))[0] == part_number for n in linked_files(path)):
                users.add(pn)
        return users

    # ----- the outbox for Ops (PDM-06) ----------------------------------------

    @property
    def outbox_path(self) -> str:
        return os.path.join(self.root, OUTBOX, f"{self.actor}.jsonl")

    def _send_released_boms(self, event: Event):
        """One outbox line per released BOM of this approval or ECO release. Ops (once it has an
        API) stores them by (P/N, revision, SHA-256), so sending a line twice changes nothing."""
        if event.op == "approve":
            pns, eco = [event.args["part_number"]], None
        else:
            pns, eco = list(self.model.ecos[event.args["number"]].items), event.args["number"]
        lines = []
        for pn in pns:
            item = self.model.items[pn]
            rev = item.released
            if rev is None or rev.bom is None:
                continue
            sha = self.released_files.get((pn, rev.code)) or rev.bom_version
            lines.append(json.dumps({
                "key": f"{pn}@{rev.code}:{sha}", "event": event.id, "at": event.at, "by": event.actor,
                "part_number": pn, "revision": rev.code, "description": item.description, "sha256": sha,
                "eco": eco, "lines": [dict(line.as_dict(), description=self.model.items[line.child].description)
                                      if line.child in self.model.items else line.as_dict() for line in rev.bom],
            }, sort_keys=True, separators=(",", ":")))
        if not lines:
            return
        os.makedirs(os.path.dirname(self.outbox_path), exist_ok=True)
        with open(self.outbox_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def outbox(self) -> list:
        """Every released BOM sent to the outbox (all devices), oldest first, one per key."""
        folder = os.path.join(self.root, OUTBOX)
        found = {}
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            if not name.endswith(".jsonl"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                text = handle.read()
            for line in text.split("\n")[: None if text.endswith("\n") else -1]:
                try:
                    row = json.loads(line) if line.strip() else None
                except json.JSONDecodeError:
                    row = None
                if row and row.get("key"):
                    found.setdefault(row["key"], row)
        return sorted(found.values(), key=lambda r: (r.get("at", ""), r["key"]))

    # ----- files ------------------------------------------------------------

    def checkout(self, part_number, workspace: str | None = None) -> str | None:
        """Take the lock. With ``workspace``, also copy the latest version
        there and return its path (the place FreeCAD edits and saves)."""
        self._record("checkout", dict(part_number=part_number))
        if workspace and self.versions.get(part_number):
            return self.fetch(part_number, workspace)
        return None

    def undo_checkout(self, part_number, force=False):
        return self._record("undo_checkout", dict(part_number=part_number, force=bool(force)))

    def checkin(self, part_number, path: str, comment: str = "", keep_lock: bool = False,
                made_from: dict | None = None, fingerprint: dict | None = None):
        """Store ``path`` as a new version. Unchanged content only releases the lock. A comment
        is required (PDM-02 A3). ``made_from`` and ``fingerprint`` (PDM-09, see CHECKIN_EXTRAS)
        are written only when given."""
        if not (comment or "").strip():
            raise LifecycleError("a check-in comment is required")
        if self.locks.get(part_number) != self.actor:
            raise LifecycleError(f"{part_number} is not checked out by {self.actor}")
        digest = sha256_of(path)
        current = self.latest_version(part_number)
        if current is not None and current.sha256 == digest:
            if not keep_lock:
                self.undo_checkout(part_number)
            return current
        stored = self.store_object(path, digest)
        return self._record("checkin", _optional(dict(part_number=part_number, comment=comment,
                                                      keep_lock=bool(keep_lock), **stored),
                                                 made_from=made_from, fingerprint=fingerprint))

    def version_with(self, part_number, sha256: str) -> FileVersion | None:
        """The version of ``part_number`` with this SHA-256, or None."""
        return next((v for v in self.versions.get(self.file_item(part_number), []) if v.sha256 == sha256), None)

    def store_object(self, path: str, digest: str | None = None) -> dict:
        """Copy a file into ``objects/`` (write-once, by SHA-256); returns its record."""
        digest = digest or sha256_of(path)
        filename = os.path.basename(path)
        target = os.path.join(self.root, object_name(digest, filename))
        if not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(path, "rb") as handle:
                _write_once(target, handle.read())
        return {"sha256": digest, "size": os.path.getsize(path), "filename": filename}

    def attach(self, attachments) -> list:
        """Attachments as journal records: paths are stored first, records pass through."""
        records = []
        for entry in attachments or ():
            if isinstance(entry, dict):
                records.append({"sha256": entry["sha256"], "size": int(entry["size"]),
                                "filename": entry["filename"]})
            else:
                records.append(self.store_object(os.fspath(entry)))
        return records

    def attachment_path(self, record: dict) -> str:
        """Where an attachment's object lives in the vault (read it, never write it)."""
        return os.path.join(self.root, object_name(record["sha256"], record["filename"]))

    def fetch_attachment(self, record: dict, dest_dir: str) -> str:
        """Copy an attachment out of the vault under its original name, verifying it."""
        source = self.attachment_path(record)
        if not os.path.exists(source):
            raise FileNotFoundError(f"{record['filename']} has not synced to this device yet ({source})")
        if sha256_of(source) != record["sha256"]:
            raise LifecycleError(f"{source} does not match its SHA-256; the copy is damaged or incomplete")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, record["filename"])
        shutil.copyfile(source, dest)
        return dest

    def file_item(self, part_number) -> str:
        """The item whose file holds ``part_number``: itself, or a configuration's file item (MOD-03)."""
        item = self.model.items.get(part_number)
        return item.configuration_of if item is not None and item.configuration_of else part_number

    def latest_version(self, part_number) -> FileVersion | None:
        part_number = self.file_item(part_number)
        versions = self.versions.get(part_number)
        return versions[-1] if versions else None

    def released_version(self, part_number) -> FileVersion | None:
        part_number = self.file_item(part_number)
        item = self.model.items[part_number]
        if item.released is None:
            return None
        sha = self.released_files.get((part_number, item.released.code))
        return next((v for v in self.versions.get(part_number, []) if v.sha256 == sha), None)

    def fetch(self, part_number, dest_dir: str, version: FileVersion | None = None) -> str:
        """Copy a version (default: latest) out of the vault, verifying it."""
        version = version or self.latest_version(part_number)
        if version is None:
            raise LifecycleError(f"{part_number} has no file versions")
        source = os.path.join(self.root, version.object_name)
        if not os.path.exists(source):
            raise FileNotFoundError(
                f"{part_number} version {version.sha256[:12]} has not synced to this device yet ({source})"
            )
        if sha256_of(source) != version.sha256:
            raise LifecycleError(f"{source} does not match its SHA-256; the copy is damaged or incomplete")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, version.filename)
        shutil.copyfile(source, dest)
        return dest

    def _object_names(self) -> list[str]:
        """Every object the journal names: file versions, then ECR attachments (repeats kept)."""
        names = [version.object_name for versions in self.versions.values() for version in versions]
        for ecr in self.model.ecrs.values():
            names += [object_name(record["sha256"], record["filename"]) for record in ecr.attachments]
        return names

    def missing_objects(self) -> list[str]:
        """Objects the journal names that are not in the folder at all: still syncing from another
        device. One iCloud evicted (dataless, or its ``.<name>.icloud`` stand-in on macOS 13 and
        older) is not missing: it downloads when read (:meth:`object_health`, PDM-10)."""
        from bruceworx.pdm_workspace import icloud_placeholder  # pdm_workspace imports this module

        missing = []
        for name in self._object_names():
            path = os.path.join(self.root, name)
            if not os.path.exists(path) and not icloud_placeholder(path):
                missing.append(name)
        return missing

    def object_health(self, stat_fn=os.stat) -> dict:
        """Where the objects are, for the dashboard's iCloud line (each object once): ``listed``,
        ``local`` (on this Mac), ``cloud`` (in iCloud only, downloads on open), ``stand_ins`` (the
        cloud ones that are a ``.icloud`` stand-in: not copyable as they are) and ``missing``."""
        from bruceworx.pdm_workspace import icloud_placeholder, is_dataless  # noqa: PLC0415

        out = {"listed": 0, "local": 0, "cloud": [], "stand_ins": [], "missing": []}
        for name in dict.fromkeys(self._object_names()):
            path = os.path.join(self.root, name)
            out["listed"] += 1
            if os.path.lexists(path):
                if is_dataless(path, stat_fn):
                    out["cloud"].append(name)
                else:
                    out["local"] += 1
            elif icloud_placeholder(path):
                out["cloud"].append(name)
                out["stand_ins"].append(name)
            else:
                out["missing"].append(name)
        return out

    def changes(self) -> list[dict]:
        """ECRs and ECOs with their state, newest first, as JSON-ready rows."""
        rows = []
        for ecr in self.model.ecrs.values():
            raised = ecr.records[0] if ecr.records else {}
            decided = next((r for r in reversed(ecr.records) if r.get("op") == "decide_ecr"), {})
            ecos = [eco.number for eco in self.model.ecos.values() if ecr.number in eco.ecrs]
            rows.append({
                "kind": "ECR", "number": ecr.number, "state": ecr.status, "title": ecr.problem,
                "items": list(ecr.items), "attachments": list(ecr.attachments), "reason": ecr.reason,
                "by": raised.get("by", ""), "at": raised.get("at", ""), "decided_at": decided.get("at", ""),
                "ecos": ecos,
            })
        for eco in self.model.ecos.values():
            opened = eco.records[0] if eco.records else {}
            released = next((r for r in reversed(eco.records) if r.get("op") == "release_eco"), {})
            pending = [pn for pn in eco.items if self.model.items[pn].latest.state != pdm.IN_REVIEW]
            rows.append({
                "kind": "ECO", "number": eco.number, "state": eco.status,
                "title": "; ".join(self.model.ecrs[n].problem for n in eco.ecrs if n in self.model.ecrs),
                "items": list(eco.items), "ecrs": list(eco.ecrs), "note": eco.note,
                "by": opened.get("by", ""), "at": opened.get("at", ""), "released_at": released.get("at", ""),
                "pending": pending if eco.status == "Open" else [],
            })
        rows.sort(key=lambda row: (row["at"], row["number"]), reverse=True)
        return rows

    # ----- reading ------------------------------------------------------------

    def status(self) -> dict:
        """Everything a reader shows, as JSON-ready data."""
        rows = []
        for pn, item in sorted(self.model.items.items()):
            rev = item.latest
            latest = self.latest_version(pn)
            rows.append({
                "part_number": pn,
                "type": item.item_type,
                "description": item.description,
                "revision": rev.code,
                "state": item.state,
                "released": item.released.code if item.released else None,
                "display": pdm.display_part_number(pn, rev.code),
                "checked_out_by": self.locks.get(pn),
                "file": latest.filename if latest else None,
                "sha256": latest.sha256 if latest else None,
                "versions": len(self.versions.get(pn, [])),
            })
        return {
            "vault": self.ref.get("name"),
            "vault_id": self.ref.get("vault_id"),
            "root": self.root,
            "actor": self.actor,
            "items": rows,
            "projects": [{"code": p.code, "name": p.name, "program": p.program, "top": p.top, "units": p.units,
                          "drawing_profile": p.drawing_profile, "status": p.status}
                         for p in self.model.projects.values()],
            "missing_objects": self.missing_objects(),
            "problems": list(self.problems),
        }


def _write_once(path: str, data: bytes) -> None:
    """Write under a ``.tmp`` name (not synced by iCloud), then rename."""
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)



def _gap(entry) -> dict:
    """A missing-line record; a metered one ([BW] ROU-04) keeps its unit and a fractional qty."""
    if not isinstance(entry, dict):
        return {"name": str(entry), "qty": 1}
    unit = str(entry.get("unit") or "")
    gap = {"name": str(entry["name"]), "qty": float(entry.get("qty", 1)) if unit else int(entry.get("qty", 1))}
    if unit:
        gap["unit"] = unit
    return gap

def _optional(args: dict, **extra) -> dict:
    """``args`` plus the optional fields that are set; unset ones stay out of the journal."""
    args = dict(args)
    args.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return args


def _jsonable(args: dict) -> dict:
    return json.loads(json.dumps(args))
