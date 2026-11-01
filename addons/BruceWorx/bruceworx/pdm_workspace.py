# SPDX-License-Identifier: LGPL-2.1-or-later
"""The working folder of a folder vault, and the status of every file in it (BW-PDM-04).

Plan: roadmap section 7.3 (approved 23 Oct 2026). Working files live inside the vault,
one folder per user and Mac, written by that Mac only (the journal's rule)::

    <vault>/work/<actor>/<P/N>.FCStd           files you work on, named by part number
    <vault>/work/<actor>/.bw/state.json        per file: the vault version it started from
    <vault>/work/<actor>/.rev/<P/N>-<rev>.FCStd    older revisions, read-only

Files checked out to you are writable; every other work file is read-only on disk, so
FreeCAD cannot save it by accident (the FreeCAD side then offers a check-out).

Status of an item p from five facts (plan, theory section): the lock holder L, the latest
vault version v, the version the work copy started from b, the work copy's own hash w,
and the lifecycle state s. The first matching row wins:

    not in the vault                 -> NOT_IN_VAULT
    L = other                        -> CHECKED_OUT_OTHER   (read-only)
    L = me and w != b                -> MODIFIED            (writable)
    L = me                           -> CHECKED_OUT         (writable)
    w exists and b != v              -> NEWER               (read-only)
    s = Released                     -> RELEASED            (read-only)
    otherwise                        -> UP_TO_DATE          (read-only)

A check-in needs a comment (the user's rule, 23 Oct). No FreeCAD import: the FreeCAD side
(bruceworx_vaultclient.py) drives this module.

KiCad items (ECD-05, bruceworx.ecad_vault): an item whose file is a packed KiCad project
(``<P/N>.kicad.zip``) has a folder as its work copy, ``<work>/<P/N>/``. Its local hash is the
version it was placed from while the folder's files (their tree digest) are unchanged; check-in
packs the folder, the other operations unpack into it, and read-only means the files in it.

Links (plan section 4): work files link each other as plain ``<P/N>.FCStd``. Before a check-in,
:meth:`Workspace.fix_links` points every other link at its vault item, and files that are not in
the vault yet are imported first through a callback (vault_setup.import_linked_files).

iCloud (plan section 1, "Optimise Mac Storage"): a file iCloud evicted is either *dataless*
(macOS 14 and later: the name is there, the content is not; reading it blocks until it has
downloaded) or replaced by a ``.<name>.icloud`` stand-in (older macOS). :func:`needs_download`
tells; before reading a vault object or a work file the workspace calls ``ensure_local(paths)``,
which the FreeCAD side sets to a download request with a progress dialog. Status passes never
read a dataless file: its hash comes from the cache in state.json, else the base is assumed.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import plistlib
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass, field

from bruceworx import pdm
from bruceworx.pdm_folder import FolderVault, LifecycleError, sha256_of

WORK_DIR = "work"
STATE_DIR = ".bw"
STATE_FILE = "state.json"
REV_DIR = ".rev"
SUFFIX = ".FCStd"

NOT_IN_VAULT = "not_in_vault"
CHECKED_OUT_OTHER = "checked_out_other"
MODIFIED = "modified"
CHECKED_OUT = "checked_out"
NEWER = "newer"
RELEASED = "released"
UP_TO_DATE = "up_to_date"
LABELS = {
    NOT_IN_VAULT: "Not in vault",
    CHECKED_OUT_OTHER: "Checked out by someone else",
    MODIFIED: "Checked out to me, modified",
    CHECKED_OUT: "Checked out to me",
    NEWER: "Newer version in the vault",
    RELEASED: "Released",
    UP_TO_DATE: "Checked in, up to date",
}
WRITABLE = (MODIFIED, CHECKED_OUT)
_XLINK = re.compile(rb'<XLink[^>]*\sfile="([^"]*)"')
SF_DATALESS = 0x40000000  # st_flags: content only in the cloud (sys/stat.h)
ICLOUD_SUFFIX = ".icloud"


class NotDownloadedError(FileNotFoundError):
    """Files in iCloud that have not downloaded to this Mac (yet)."""

    def __init__(self, paths, text=""):
        self.paths = list(paths)
        names = ", ".join(os.path.basename(p) for p in self.paths[:3]) + (" ..." if len(self.paths) > 3 else "")
        super().__init__(text or f"{len(self.paths)} file(s) have not downloaded from iCloud to this Mac: "
                         f"{names}. Check that the Mac is online and signed in to iCloud, then try again.")


def icloud_placeholder(path: str) -> str:
    """The ``.<name>.icloud`` stand-in iCloud leaves for an evicted file (macOS 13 and older), or ""."""
    folder, name = os.path.split(path)
    stand_in = os.path.join(folder, "." + name + ICLOUD_SUFFIX)
    return stand_in if os.path.isfile(stand_in) else ""


def is_dataless(path: str, stat_fn=os.stat) -> bool:
    """True when the file's content is in the cloud only (macOS 14 and later)."""
    try:
        return bool(getattr(stat_fn(path), "st_flags", 0) & SF_DATALESS)
    except OSError:
        return False


def needs_download(path: str, stat_fn=os.stat) -> bool:
    """True when path is in iCloud but not on this Mac: dataless, or only its stand-in is there."""
    if os.path.lexists(path):
        return is_dataless(path, stat_fn)
    return bool(icloud_placeholder(path))


def cloud_size(path: str, stat_fn=os.stat) -> int:
    """The size of a file still to download (a dataless file keeps its size; a stand-in names it)."""
    if os.path.lexists(path):
        try:
            return stat_fn(path).st_size
        except OSError:
            return 0
    stand_in = icloud_placeholder(path)
    try:
        with open(stand_in, "rb") as handle:
            return int(plistlib.load(handle).get("NSURLFileSizeKey", 0))
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return 0


def pending_downloads(paths, stat_fn=os.stat) -> list:
    """The paths that still need downloading, in order, without repeats."""
    out = []
    for path in paths:
        if path not in out and needs_download(path, stat_fn):
            out.append(path)
    return out


def status_code(in_vault, lock, me, base, local, latest, state):
    """The status row for the five facts (see the module docstring)."""
    if not in_vault:
        return NOT_IN_VAULT
    if lock and lock != me:
        return CHECKED_OUT_OTHER
    if lock == me:
        return MODIFIED if local is not None and local != base else CHECKED_OUT
    if local is not None and base != latest:
        return NEWER
    if state == pdm.RELEASED:
        return RELEASED
    return UP_TO_DATE


def pin_actor(saved: str, current: str) -> str:
    """The actor to use for a vault: the one saved at first open, else the current one.

    macOS can rename the Mac ("-2" after a name clash), which would change user@mac and
    orphan your own locks; the FreeCAD side saves the first actor per vault id and keeps it.
    """
    return saved.strip() or current


@dataclass
class FileStatus:
    part_number: str
    code: str
    path: str = ""  # the work copy, or "" when there is none
    base: str = ""
    local: str = ""
    latest: str = ""
    lock: str = ""
    state: str = ""
    revision: str = ""
    description: str = ""
    item_type: str = ""

    @property
    def label(self):
        return LABELS[self.code]

    @property
    def writable(self):
        return self.code in WRITABLE

    @property
    def display(self):
        return pdm.display_part_number(self.part_number, self.revision) if self.revision else self.part_number


@dataclass
class MyWork:
    checked_out: list = field(default_factory=list)  # [FileStatus]
    in_review: list = field(default_factory=list)
    newer: list = field(default_factory=list)


@dataclass
class LinkFix:
    """A link in a work file that does not resolve through the work folder (plan section 4)."""

    holder: str  # part number of the work file that holds the link
    link: str  # the file attribute as written
    path: str  # where it points (real path)
    target: str = ""  # the vault item it stands for; "" when the file is not in the vault
    exists: bool = True  # the file is there (or in iCloud)

    @property
    def needs_import(self):
        return not self.target and self.exists


def resolve_link(holder_path: str, link: str) -> str:
    """Where a link resolves: relative to the folder of the file holding it."""
    base = os.path.dirname(os.path.abspath(holder_path))
    return os.path.realpath(link if os.path.isabs(link) else os.path.join(base, link))


def linked_files(path: str) -> list:
    """Files an .FCStd links to (XLink file attributes in Document.xml), as written."""
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read("Document.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return []
    seen = []
    for raw in _XLINK.findall(data):
        name = raw.decode("utf-8", "replace")
        if name and name not in seen:
            seen.append(name)
    return seen


def rewrite_links(path: str, mapper) -> int:
    """Rewrite the XLink file attributes in an .FCStd's Document.xml; returns how many changed.

    mapper(old) returns the new file attribute, or None to keep it. The zip is rewritten
    with every other entry unchanged (plan section 7.3, links resolve through the vault).
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        data = {info.filename: archive.read(info.filename) for info in infos}
    changed = 0

    def replace(match):
        nonlocal changed
        old = match.group(2).decode("utf-8", "replace")
        new = mapper(old)
        if not new or new == old:
            return match.group(0)
        changed += 1
        return match.group(1) + new.replace("&", "&amp;").replace('"', "&quot;").encode("utf-8") + match.group(3)

    document = re.sub(rb'(<XLink[^>]*\sfile=")([^"]*)(")', replace, data.get("Document.xml", b""))
    if not changed:
        return 0
    data["Document.xml"] = document
    _rewrite_zip(path, infos, data)
    return changed


_LINK_ERROR = re.compile(rb'(<Object\s[^>]*?)\s+Invalid="1"([^>]*?)\s+Error="Link not restored[^"]*"')


def clear_link_errors(path: str) -> int:
    """Drop the "Link not restored" error FreeCAD saved on objects whose linked file was not
    there when the file was last written (an import opens its copy away from the parts).

    The objects stay Touched, so FreeCAD recomputes them once the links resolve. Returns how
    many objects changed; the zip is rewritten only when one did.
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        data = {info.filename: archive.read(info.filename) for info in infos}
    document, changed = _LINK_ERROR.subn(rb"\1\2", data.get("Document.xml", b""))
    if not changed:
        return 0
    data["Document.xml"] = document
    _rewrite_zip(path, infos, data)
    return changed


def _rewrite_zip(path: str, infos, data: dict) -> None:
    writable = os.access(path, os.W_OK)
    if not writable:
        _set_writable(path, True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w") as out:
        for info in infos:
            out.writestr(info, data[info.filename], compress_type=info.compress_type)
    os.replace(tmp, path)
    if not writable:
        _set_writable(path, False)


def _set_writable(path: str, writable: bool) -> None:
    if os.path.isdir(path):  # a KiCad item's work copy: the files in it
        from bruceworx import ecad_vault  # noqa: PLC0415

        ecad_vault.set_writable(path, writable)
        return
    mode = os.stat(path).st_mode
    if writable:
        os.chmod(path, mode | stat.S_IWUSR)
    else:
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class Workspace:
    """One user's working folder in a FolderVault."""

    def __init__(self, vault: FolderVault, read_only: bool = False):
        self.vault = vault
        # read_only: a reader (the Start page's worker thread) never writes state.json, so it cannot
        # overwrite a base the GUI thread recorded meanwhile (PDM-10); operations are refused.
        self.read_only = read_only
        self.root = os.path.join(vault.root, WORK_DIR, vault.actor)
        self.state_path = os.path.join(self.root, STATE_DIR, STATE_FILE)
        self._hashes = {}  # path -> (size, mtime_ns, sha256)
        self._dirty = False  # new hashes not in state.json yet
        self.state = self._load_state()
        # ensure_local(paths) asks iCloud for files not on this Mac and waits (the FreeCAD side
        # sets it); without it a file that is not here stops the operation with a clear message.
        self.ensure_local = None

    # --- state -------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) and isinstance(data.get("items"), dict) else {"items": {}}
        except (OSError, ValueError):
            return {"items": {}}

    def _save_state(self) -> None:
        if self.read_only:
            raise PermissionError("this workspace was opened read-only")
        self._dirty = False
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, self.state_path)

    def _record_base(self, pn: str, sha: str) -> None:
        old = self.state["items"].get(pn, {})
        self.state["items"][pn] = {"base": sha, "fetched": _now()}
        if old.get("tree") and old.get("base") == sha:  # a KiCad work folder still holding that version
            self.state["items"][pn]["tree"] = old["tree"]
        self._save_state()

    def _writable(self) -> None:
        if self.read_only:
            raise PermissionError("this workspace was opened read-only")

    def base(self, pn: str) -> str:
        return self.state["items"].get(pn, {}).get("base", "")

    # --- paths and hashes ----------------------------------------------------
    def is_folder(self, pn: str) -> bool:
        """Whether pn's work copy is a folder: a KiCad project (ECD-05, bruceworx.ecad_vault)."""
        from bruceworx import ecad_vault  # noqa: PLC0415

        return ecad_vault.is_ecad(self.vault, pn)

    def path(self, pn: str) -> str:
        if self.is_folder(pn):
            return os.path.join(self.root, pn)
        return os.path.join(self.root, pn + SUFFIX)

    def _folder_tree(self, path: str) -> str:
        """The tree digest of a KiCad work folder, cached by its files' sizes and times."""
        from bruceworx import ecad_vault  # noqa: PLC0415

        key = ecad_vault.signature(path)
        cached = self._hashes.get(path)
        if cached and cached[0] == key and cached[1] == "tree":
            return cached[2]
        digest = ecad_vault.tree_digest(path)
        self._hashes[path] = (key, "tree", digest)
        return digest

    def _folder_hash(self, path: str) -> str:
        """A KiCad work folder's hash: the version it was placed from while its files are the ones
        placed (the tree digest recorded then), else ``tree-<digest>`` (modified)."""
        tree = self._folder_tree(path)
        pn = os.path.basename(path) if os.path.dirname(os.path.abspath(path)) == self.root else ""
        record = self.state["items"].get(pn, {}) if pn else {}
        if record.get("base") and record.get("tree") == tree:
            return record["base"]
        return "tree-" + tree

    def _record_tree(self, pn: str, path: str) -> None:
        self.state["items"].setdefault(pn, {})["tree"] = self._folder_tree(path)
        self._save_state()

    def local_hash(self, path: str) -> str | None:
        """SHA-256 of a work file, cached by size and modification time; None when absent.

        Work-folder hashes are kept in state.json too, so a file iCloud has since evicted
        (dataless, or only its stand-in left) keeps its status without being downloaded;
        with no cached hash it is taken to be unchanged since its base.
        """
        if os.path.isdir(path):
            return self._folder_hash(path)
        name = os.path.basename(path) if os.path.dirname(os.path.abspath(path)) == self.root else ""
        saved = self.state.get("hashes", {}).get(name) if name else None
        try:
            info = os.stat(path)
        except OSError:
            if not icloud_placeholder(path):
                return None
            if isinstance(saved, list) and len(saved) == 3:
                return saved[2]
            return self.base(os.path.splitext(os.path.basename(path))[0]) or None
        key = (info.st_size, info.st_mtime_ns)
        cached = self._hashes.get(path)
        if cached and cached[:2] == key:
            return cached[2]
        if isinstance(saved, list) and len(saved) == 3 and tuple(saved[:2]) == key:
            self._hashes[path] = (*key, saved[2])
            return saved[2]
        if getattr(info, "st_flags", 0) & SF_DATALESS:
            return self.base(os.path.splitext(os.path.basename(path))[0]) or None
        digest = sha256_of(path)
        self._remember(path, digest, info)
        return digest

    def _remember(self, path, digest, info=None) -> None:
        """Cache a work file's hash (in memory, and in state.json for the work folder)."""
        info = info or os.stat(path)
        key = (info.st_size, info.st_mtime_ns)
        self._hashes[path] = (*key, digest)
        if os.path.dirname(os.path.abspath(path)) == self.root:
            self.state.setdefault("hashes", {})[os.path.basename(path)] = [*key, digest]
            self._dirty = True

    def _ensure(self, paths) -> None:
        """Make sure files are on this Mac before they are read (iCloud); raise when they are not."""
        pending = pending_downloads(paths)
        if pending and self.ensure_local is not None:
            self.ensure_local(pending)
            pending = pending_downloads(pending)
        if pending:
            raise NotDownloadedError(pending)

    def make_local(self, pn: str) -> list:
        """Download the work copy of pn and every file it links (transitively) before it opens.

        Goes one link level at a time, so each level is one request. A linked vault item with no work
        copy yet is fetched read-only (Get Latest), so an assembly opens with its parts (30 Oct).
        Returns the paths read.
        """
        level, seen = [self.path(pn)], set()
        while level:
            level = [p for p in level if p not in seen]
            seen.update(level)
            self._ensure(level)
            nxt = []
            for path in level:
                for link in linked_files(path):
                    real = resolve_link(path, link)
                    if real in seen:
                        continue
                    if not (os.path.exists(real) or icloud_placeholder(real)):
                        self._fetch_missing(real)
                    if os.path.exists(real) or icloud_placeholder(real):
                        nxt.append(real)
            level = nxt
        return sorted(seen)

    def _fetch_missing(self, real: str) -> None:
        """Get Latest for a linked file that is a vault item's missing work copy; else nothing."""
        if self.read_only or os.path.dirname(real) != os.path.realpath(self.root):
            return
        item = self.vault.resolve(os.path.splitext(os.path.basename(real))[0])  # PDM-12: a former number
        if item in self.vault.model.items and self.vault.versions.get(item) and not self.is_folder(item):
            self.get_latest(item)

    def made_from(self, pn: str) -> dict:
        """What the work copy of ``pn`` was made from (PDM-09): for every vault item its file links,
        transitively through the work folder (its closure C(D)), the version the work copy of that
        item started from (its base), else the vault's latest version. {} when it links nothing."""
        out, todo, seen = {}, [pn], {pn}
        while todo:
            current = todo.pop()
            path = self.path(current)
            if not os.path.exists(path):
                continue
            for name in linked_files(path):
                target = os.path.splitext(os.path.basename(name))[0]
                if target in seen or target not in self.vault.model.items:
                    continue
                seen.add(target)
                latest = self.vault.latest_version(target)
                base = self.base(target) or (latest.sha256 if latest else "")
                if base:
                    out[target] = base
                todo.append(target)
        return out

    # --- status --------------------------------------------------------------
    def status(self, pn: str) -> FileStatus:
        vault = self.vault
        item = vault.model.items.get(pn)
        path = self.path(pn)
        local = self.local_hash(path)
        if item is None:
            return FileStatus(pn, NOT_IN_VAULT, path if local else "", local=local or "")
        latest = vault.latest_version(pn)
        lock = vault.locks.get(pn) or ""
        # PRF-04: a base or work copy from before a storage migration is the same version as its new hash
        code = status_code(True, lock, vault.actor, vault.canonical(self.base(pn) or None), vault.canonical(local),
                           latest.sha256 if latest else None, item.state)
        return FileStatus(
            pn, code, path if local else "", self.base(pn), local or "", latest.sha256 if latest else "",
            lock, item.state, item.latest.code, item.description, item.item_type,
        )

    def statuses(self) -> dict:
        out = {pn: self.status(pn) for pn in sorted(self.vault.model.items)}
        if self._dirty and not self.read_only:
            try:
                self._save_state()
            except OSError:
                pass
        return out

    def my_work(self) -> MyWork:
        work = MyWork()
        for st in self.statuses().values():
            if st.code in WRITABLE:
                work.checked_out.append(st)
            if st.state == pdm.IN_REVIEW:
                work.in_review.append(st)
            if st.code == NEWER:
                work.newer.append(st)
        return work

    def refresh_renamed(self) -> list:
        """Replace the read-only work copies of items that were renumbered (PDM-12): the copy under the
        former number is removed (with its state entry) and, when the item has a file and no work copy
        under its new number, the latest version is placed under it. A copy that is writable (edited
        by hand) is left alone. Returns ``[(former, new)]`` for the copies replaced."""
        self._writable()
        done = []
        for old, new in sorted(self.vault.model.aliases.items()):
            path = os.path.join(self.root, old + SUFFIX)
            if old in self.vault.model.items or not os.path.isfile(path) or os.access(path, os.W_OK):
                continue
            os.remove(path)
            self._hashes.pop(path, None)
            self.state["items"].pop(old, None)
            self.state.get("hashes", {}).pop(os.path.basename(path), None)
            target = self.vault.resolve(new)
            if (self.vault.latest_version(target) is not None and not self.is_folder(target)
                    and not os.path.exists(self.path(target)) and self.vault.locks.get(target) != self.vault.actor):
                self.get_latest(target)
            done.append((old, target))
        if done:
            self._save_state()
        return done

    # --- operations --------------------------------------------------------------
    def _place(self, pn: str, version, writable: bool) -> str:
        """Copy a vault version to <P/N>.FCStd in the work folder and record it as the base."""
        self._writable()
        os.makedirs(self.root, exist_ok=True)
        self._ensure([os.path.join(self.vault.root, version.object_name)])
        if self.is_folder(pn):
            return self._place_folder(pn, version, writable)
        dest = self.path(pn)
        if os.path.exists(dest):
            _set_writable(dest, True)
        fetched = self.vault.fetch(pn, self.root, version)
        if os.path.abspath(fetched) != os.path.abspath(dest):
            os.replace(fetched, dest)
        stand_in = icloud_placeholder(dest)
        if stand_in:  # the evicted copy this one replaces
            os.remove(stand_in)
        _set_writable(dest, writable)
        self._remember(dest, version.sha256)
        self._record_base(pn, version.sha256)
        return dest

    def _place_folder(self, pn: str, version, writable: bool) -> str:
        """A KiCad item: unpack the version into <P/N>/ and record it (and its tree) as the base."""
        from bruceworx import ecad_vault  # noqa: PLC0415

        dest = self.path(pn)
        staging = os.path.join(self.root, STATE_DIR, "fetch")
        fetched = self.vault.fetch(pn, staging, version)
        try:
            ecad_vault.unpack(fetched, dest, writable=writable)
        finally:
            os.remove(fetched)
        self._hashes.pop(dest, None)
        self._record_base(pn, version.sha256)
        self._record_tree(pn, dest)
        return dest

    def checkout(self, pn: str, with_links: bool = True) -> str:
        """Take the lock and put the latest version in the work folder, writable.

        An item with no file yet gets no work copy (the caller saves a new file there).
        With with_links, the files it links to that are vault items come along read-only.
        """
        self._writable()
        local = self.local_hash(self.path(pn))
        if local is not None and self.base(pn) and local != self.base(pn) and self.vault.locks.get(pn) != self.vault.actor:
            raise LifecycleError(f"{pn}: the work copy has changes that are not in the vault; check it out by hand")
        self.vault.checkout(pn)
        latest = self.vault.latest_version(pn)
        if latest is None:
            return self.path(pn)
        if self.vault.canonical(local) == latest.sha256 and local == latest.sha256:
            self._ensure([self.path(pn)])  # evicted by iCloud: download it to edit it
            _set_writable(self.path(pn), True)
            self._record_base(pn, latest.sha256)
            path = self.path(pn)
        else:
            path = self._place(pn, latest, writable=True)
        if with_links:
            self.fetch_links(pn)
        return path

    def fetch_links(self, pn: str) -> list:
        """Get latest, read-only, of every vault item the file links to (transitively)."""
        fetched, todo, seen = [], [pn], {pn}
        while todo:
            current = todo.pop()
            if os.path.exists(self.path(current)):
                self._ensure([self.path(current)])
            for name in linked_files(self.path(current)):
                target = os.path.splitext(os.path.basename(name))[0]
                if target in seen or target not in self.vault.model.items:
                    continue
                seen.add(target)
                st = self.status(target)
                if st.code not in WRITABLE and self.vault.latest_version(target) is not None:
                    self.get_latest(target)
                    fetched.append(target)
                todo.append(target)
        return fetched

    def checkin(self, pn: str, comment: str, keep_checked_out: bool = False, made_from=None, fingerprint=None):
        """Store the work copy as a new version. The comment is required. ``made_from`` and
        ``fingerprint`` go into the journal with the version (PDM-09, pdm_folder.CHECKIN_EXTRAS)."""
        self._writable()
        if not (comment or "").strip():
            raise ValueError("a check-in needs a comment")
        path = self.path(pn)
        if not os.path.exists(path) and not icloud_placeholder(path):
            raise FileNotFoundError(f"{pn}: there is no work copy to check in ({path})")
        self._ensure([path])
        if os.path.isdir(path):
            return self._checkin_folder(pn, path, comment.strip(), keep_checked_out)
        version = self.vault.checkin(pn, path, comment=comment.strip(), keep_lock=keep_checked_out,
                                     made_from=made_from, fingerprint=fingerprint)
        latest = self.vault.latest_version(pn)
        if latest is not None:
            self._record_base(pn, latest.sha256)
        if not keep_checked_out:
            _set_writable(path, False)
        return version

    def _checkin_folder(self, pn: str, path: str, comment: str, keep: bool):
        """A KiCad item: refresh the thumbnail, pack the folder, store it (ECD-05). An unchanged
        folder only releases the lock, as for a file."""
        from bruceworx import ecad_vault  # noqa: PLC0415

        vault = self.vault
        if vault.locks.get(pn) != vault.actor:
            raise LifecycleError(f"{pn} is not checked out by {vault.actor}")
        latest = vault.latest_version(pn)
        if latest is not None and self.local_hash(path) == latest.sha256:
            if not keep:
                vault.undo_checkout(pn)
                _set_writable(path, False)
            return latest
        try:
            ecad_vault.prepare_checkin(path)
        except Exception:  # noqa: BLE001 -- the picture is never a reason not to check in
            pass
        archive = os.path.join(self.root, STATE_DIR, "pack", pn + ecad_vault.ARCHIVE_SUFFIX)
        ecad_vault.pack(path, archive)
        try:
            version = vault.checkin(pn, archive, comment=comment, keep_lock=keep)
        finally:
            os.remove(archive)
        latest = vault.latest_version(pn)
        if latest is not None:
            self._record_base(pn, latest.sha256)
            self._hashes.pop(path, None)
            self._record_tree(pn, path)
        if not keep:
            _set_writable(path, False)
        return version

    def undo_checkout(self, pn: str) -> None:
        """Release the lock and put the version the work copy started from back."""
        self._writable()
        base = self.base(pn)
        self.vault.undo_checkout(pn)
        version = next((v for v in self.vault.versions.get(pn, []) if v.sha256 == base), None)
        if version is not None:
            self._place(pn, version, writable=False)
        elif os.path.exists(self.path(pn)):
            _set_writable(self.path(pn), False)

    def get_latest(self, pn: str) -> str:
        """Replace a read-only work copy with the latest version. Never touches a checked-out file."""
        if self.vault.locks.get(pn) == self.vault.actor:
            raise LifecycleError(f"{pn} is checked out to you; check it in or undo the check-out first")
        latest = self.vault.latest_version(pn)
        if latest is None:
            raise LifecycleError(f"{pn} has no file in the vault yet")
        return self._place(pn, latest, writable=False)

    def get_revision(self, pn: str, sha256: str) -> str:
        """An older version into .rev/<P/N>-<rev>.FCStd, read-only."""
        self._writable()
        version = next((v for v in self.vault.versions.get(pn, []) if v.sha256 == sha256), None)
        if version is None:
            raise LifecycleError(f"{pn} has no version {sha256[:12]}")
        folder = os.path.join(self.root, REV_DIR)
        self._ensure([os.path.join(self.vault.root, version.object_name)])
        if self.is_folder(pn):  # a KiCad item: the older project, unpacked read-only
            from bruceworx import ecad_vault  # noqa: PLC0415

            fetched = self.vault.fetch(pn, os.path.join(self.root, STATE_DIR, "fetch"), version)
            dest = os.path.join(folder, f"{pn}-{version.revision}-{sha256[:8]}")
            try:
                ecad_vault.unpack(fetched, dest)
            finally:
                os.remove(fetched)
            return dest
        fetched = self.vault.fetch(pn, folder, version)
        dest = os.path.join(folder, f"{pn}-{version.revision}-{sha256[:8]}{SUFFIX}")
        if os.path.exists(dest):
            _set_writable(dest, True)
        os.replace(fetched, dest)
        # An older assembly sits one folder down: point its links at the work copies beside it.
        items = self.vault.model.items
        def relink(old):  # PDM-12: a link to a former number reads the item's number now
            stem, ext = os.path.splitext(os.path.basename(old))
            now = self.vault.resolve(stem)
            return "../" + now + ext if now in items and "/" not in old else None

        rewrite_links(dest, relink)
        _set_writable(dest, False)
        return dest

    def break_lock(self, pn: str) -> None:
        """Release a lock nobody can release normally (a crash, a lost work folder); journaled."""
        self._writable()
        self.vault.undo_checkout(pn, force=True)

    # --- links (plan section 4) ----------------------------------------------------
    def known_files(self) -> dict:
        """Real path -> part number of files the vault already holds: work copies, and the
        originals that were imported (their check-in comment names them)."""
        from bruceworx.vault_setup import _already_imported  # noqa: PLC0415 -- vault_setup imports this module

        known = {os.path.realpath(k): pn for k, pn in _already_imported(self.vault).items() if os.path.isabs(k)}
        for pn in self.vault.model.items:
            known[os.path.realpath(self.path(pn))] = pn
        return known

    def link_fixes(self, pns) -> list:
        """The links in the work copies of pns that are not plain ``<P/N>.FCStd`` of a vault item.

        target is the vault item a link stands for: a file named by an item's part number
        (wherever it is), a file the vault imported, or a file with the content of a version;
        "" when the file is not in the vault (it can be imported) or cannot be found.
        """
        items = self.vault.model.items
        known, hashes = None, None
        out = []
        for pn in pns:
            holder = self.path(pn)
            if not os.path.exists(holder):
                continue
            self._ensure([holder])
            for link in linked_files(holder):
                stem = os.path.splitext(os.path.basename(link))[0]
                if link == stem + SUFFIX and stem in items:
                    continue
                real = resolve_link(holder, link)
                exists = os.path.isfile(real) or bool(icloud_placeholder(real))
                target = stem if stem in items else ""
                if not target and self.vault.resolve(stem) in items:
                    target = self.vault.resolve(stem)  # PDM-12: the file's number was changed
                if not target:
                    if known is None:
                        known = self.known_files()
                        hashes = {v.sha256: p for p, versions in self.vault.versions.items() for v in versions}
                    target = known.get(real, "")
                    if not target and os.path.isfile(real) and not needs_download(real):
                        target = hashes.get(sha256_of(real), "")
                out.append(LinkFix(pn, link, real, target, exists))
        return out

    def relink(self, pn: str, mapping: dict) -> int:
        """Point pn's links at ``<P/N>.FCStd``: mapping is {link as written: part number}.

        Each target gets a read-only work copy when it has none, so the link resolves.
        Returns how many links changed.
        """
        self._writable()
        changed = rewrite_links(self.path(pn), lambda old: mapping[old] + SUFFIX if old in mapping else None)
        for target in sorted(set(mapping.values())):
            if not os.path.exists(self.path(target)) and self.vault.latest_version(target) is not None \
                    and self.vault.locks.get(target) != self.vault.actor:
                self.get_latest(target)
        return changed

    def fix_links(self, pns, importer=None):
        """Before a check-in of pns: point every link at its vault item (plan section 4).

        importer(paths, known) imports files that are not in the vault yet and returns
        {real path: part number} (vault_setup.import_linked_files); without one those links
        stay as they are. Returns (links changed, [LinkFix] left as they are).
        """
        fixes = self.link_fixes(pns)
        wanted = sorted({f.path for f in fixes if f.needs_import})
        by_path = {}
        if wanted and importer is not None:
            by_path = importer(wanted, self.known_files()) or {}
        plan, left = {}, []
        for fix in fixes:
            target = fix.target or by_path.get(fix.path, "")
            if target:
                plan.setdefault(fix.holder, {})[fix.link] = target
            else:
                left.append(fix)
        changed = sum(self.relink(holder, mapping) for holder, mapping in plan.items())
        return changed, left
