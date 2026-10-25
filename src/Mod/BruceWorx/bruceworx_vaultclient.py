# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault client in FreeCAD (BW-PDM-04, plan approved 23 Oct 2026).

The engine is the hooks add-on's bruceworx/pdm_workspace.py (status, check out, check in,
...) and bruceworx/vault_move.py; this module puts it in the window:

* Session: the vault named by Preferences/BruceWorx/VaultPath, opened with the actor
  pinned for it (BruceWorx/VaultActors/<vault id>, saved the first time), one shared
  Workspace. It refreshes when the journal folder changes (QFileSystemWatcher, debounced)
  and when VaultPath changes; add_listener() tells the other parts.
* Commands (BruceWorx menu, Cmd-K, Browser and Vault panel right-click): Check Out,
  Check In... (task panel; a comment is required), Undo Check-Out..., Get Latest,
  Get Revision..., Submit for Review, Approve, Break Lock..., Open from Vault, the Vault
  panel and Show in Vault Panel.
* Saving: vault files not checked out to you are read-only on disk. When FreeCAD starts
  saving one (slotStartSaveDocument), the client asks "Check out <P/N> to save?"; Yes
  takes the lock and makes the file writable before FreeCAD writes, No lets FreeCAD's
  save fail with its own message. FreeCAD's backup files are turned off for each save
  of a file inside the vault (every check-in is a version; the user's answer, 23 Oct).
  The first edit of such a document offers a check-out; its tab shows a lock.
* The vault's file list (thumbnails, P/N-REV, state chip and status icon, search and
  filters) on the Start page's Files tab: the vault is browsed on the dashboard only
  (the user's choice, 25 Oct; the floating card over the window was see-through).
* Browser badges (bruceworx_browser.ROW_EXTRAS) and the status-bar readout
  ("Vault <name> · 2 checked out · 1 newer"; a click opens the Files tab).
* Move Vault and Break Lock, started from Vault Settings (bruceworx_vaultwizard).

The pure helpers at the top are tested in TestBruceWorxVaultClient.py.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import zipfile

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ACTORS_PATH = PREFS_PATH + "/VaultActors"
DOCUMENT_PATH = "User parameter:BaseApp/Preferences/Document"
SUFFIX = ".FCStd"
LOCK = "\U0001F512"

# Status codes, as bruceworx/pdm_workspace.py names them (a test checks they agree).
NOT_IN_VAULT = "not_in_vault"
CHECKED_OUT_OTHER = "checked_out_other"
MODIFIED = "modified"
CHECKED_OUT = "checked_out"
NEWER = "newer"
RELEASED = "released"
UP_TO_DATE = "up_to_date"
WRITABLE = (MODIFIED, CHECKED_OUT)

# Lifecycle states (bruceworx/pdm.py).
WIP, IN_REVIEW, STATE_RELEASED, OBSOLETE = "WIP", "In Review", "Released", "Obsolete"

# One icon per status (figure pdm04-states): [(icon, colour role)], drawn at the right of a row.
STATUS_ICONS = {
    NOT_IN_VAULT: [("not_in_vault", "secondary")],
    UP_TO_DATE: [("checked_in", "secondary")],
    CHECKED_OUT: [("lock_me", "accent")],
    CHECKED_OUT_OTHER: [("lock_other", "error")],
    NEWER: [("newer", "warn")],
    MODIFIED: [("lock_me", "accent"), ("pencil", "accent")],
    RELEASED: [("released", "ok")],
}
STATUS_LABELS = {
    NOT_IN_VAULT: "Not in vault",
    UP_TO_DATE: "Checked in (latest)",
    CHECKED_OUT: "Checked out by me",
    CHECKED_OUT_OTHER: "Checked out by someone else",
    NEWER: "Newer version in vault",
    MODIFIED: "Modified locally",
    RELEASED: "Released",
}
# The lifecycle chip in the Vault panel: state -> (text, colour role).
STATE_CHIPS = {
    WIP: ("WIP", "warn"),
    IN_REVIEW: ("IN REVIEW", "accent"),
    STATE_RELEASED: ("RELEASED", "ok"),
    OBSOLETE: ("OBSOLETE", "dim"),
}
FILTERS = (("all", "All"), ("parts", "Parts"), ("assemblies", "Assemblies"), ("drawings", "Drawings"),
           ("checked_out", "Checked out"), ("changes", "Changes"))  # changes: ECRs and ECOs (bruceworx_changes)
FILTER_TYPES = {"parts": ("part", "cots"), "assemblies": ("assembly",), "drawings": ("drawing",)}


# ----- pure helpers (unit tested) ---------------------------------------------------------


def part_number_for_file(path, work_root):
    """The part number of a work file (<work root>/<P/N>.FCStd), or "" for any other file.

    Files in the work folder are named by part number (plan section 4); older revisions
    in .rev/ and files elsewhere are not work files.
    """
    if not path or not work_root:
        return ""
    folder, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    if ext.lower() != SUFFIX.lower() or not stem:
        return ""
    if os.path.realpath(folder) != os.path.realpath(work_root):
        return ""
    return stem


def inside(path, folder):
    """Whether path is folder or lies under it (real paths)."""
    if not path or not folder:
        return False
    real, root = os.path.realpath(path), os.path.realpath(folder)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def status_icons(code):
    """[(icon, colour role)] for a status code."""
    return list(STATUS_ICONS.get(code, []))


def actor_name(actor, me):
    """"you" for your own actor, else the user part of user@mac."""
    if not actor:
        return ""
    if actor == me:
        return "you"
    return actor.split("@")[0] or actor


def browser_marks(code, lock="", me=""):
    """Badges after the revision chip in the Browser: [(text, kind)].

    The tree draws badges as text (Gui/Tree.cpp), so the lock is the padlock glyph;
    the text after it takes the kind's colour (accent: yours, warn: someone else's).
    """
    if code == CHECKED_OUT:
        return [(LOCK, "accent")]
    if code == MODIFIED:
        return [(LOCK + " ✎", "accent")]
    if code == CHECKED_OUT_OTHER:
        return [(f"{LOCK} {actor_name(lock, me)}".strip(), "warn")]
    if code == NEWER:
        return [("↓ newer", "warn")]
    return []


def status_suffix(checked_out, newer):
    """What the status-bar vault readout adds: " · 2 checked out · 1 newer"."""
    parts = []
    if checked_out:
        parts.append(f"{checked_out} checked out")
    if newer:
        parts.append(f"{newer} newer")
    return "".join(" · " + p for p in parts)


def comment_ok(text):
    """A check-in needs a comment (the user's rule, 23 Oct)."""
    return bool((text or "").strip())


def checkin_button_text(count):
    if count <= 0:
        return "Check In"
    return f"Check In {count} File" + ("" if count == 1 else "s")


def version_change(base, local):
    """ "a41c9e0 → c82f5d1" for the Check In panel."""
    left = base[:7] if base else "new"
    right = local[:7] if local else "?"
    return left if left == right else f"{left} → {right}"


def parse_time(text):
    try:
        when = _dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


def relative_time(text, now=None):
    """ "just now", "5 min ago", "2 h ago", "yesterday", "14 Oct" (local time)."""
    when = parse_time(text) if not isinstance(text, _dt.datetime) else text
    if when is None:
        return ""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    seconds = (now - when).total_seconds()
    local, today = when.astimezone(), now.astimezone()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if local.date() == today.date():
        return f"{int(seconds // 3600)} h ago"
    if (today.date() - local.date()).days == 1:
        return "yesterday"
    text = f"{local.day} {local.strftime('%b')}"
    return text if local.year == today.year else f"{text} {local.year}"


def row_status(code, state, lock, me, versions=(), fetched="", now=None):
    """(text, colour role) for the third line of a Vault panel row."""
    last = versions[-1] if versions else None
    if not versions and code != CHECKED_OUT:
        return "No file yet", "secondary"
    if code == CHECKED_OUT:
        when = relative_time(fetched, now) if fetched else ""
        return "Checked out by you" + (f" · {when}" if when else ""), "accent"
    if code == MODIFIED:
        return "Checked out by you · modified", "accent"
    if code == CHECKED_OUT_OTHER:
        return f"Checked out by {actor_name(lock, me)} · read-only", "secondary"
    if code == NEWER:
        return f"Newer version · {relative_time(last.at, now)}", "warn"
    if code == RELEASED:
        return f"Released {relative_time(last.at, now)} · read-only", "secondary"
    if state == IN_REVIEW:
        return "In review · read-only", "secondary"
    if state == OBSOLETE:
        return "Obsolete · read-only", "secondary"
    return f"Checked in · {actor_name(last.actor, me)}, {relative_time(last.at, now)}", "secondary"


# Search words that filter on a field (PDM-10: the Start page dashboard's tiles and rows open the panel
# with them): state:wip, type:part, lock:me, status:newer, change:open. Values are matched loosely.
QUERY_STATES = {"wip": WIP, "review": IN_REVIEW, "inreview": IN_REVIEW, "in_review": IN_REVIEW,
                "released": STATE_RELEASED, "obsolete": OBSOLETE}
QUERY_LOCKS = {"me": WRITABLE, "mine": WRITABLE, "other": (CHECKED_OUT_OTHER,), "others": (CHECKED_OUT_OTHER,),
               "any": WRITABLE + (CHECKED_OUT_OTHER,)}


def query_word_matches(row, key, value):
    """Whether a row passes one ``key:value`` search word; None when key is not a field word."""
    value = value.lower()
    if key == "state":
        wanted = QUERY_STATES.get(value.replace(" ", ""))
        if row.get("item_type") == "change":
            return str(row.get("state", "")).lower() == value
        return row.get("state") == wanted
    if key == "type":
        return str(row.get("item_type", "")).lower() == {"parts": "part", "assemblies": "assembly",
                                                         "drawings": "drawing", "bought": "cots"}.get(value, value)
    if key == "lock":
        if value == "none":
            return row.get("code") not in QUERY_LOCKS["any"]
        return row.get("code") in QUERY_LOCKS.get(value, ())
    if key == "status":
        return row.get("code") == value
    if key == "change":
        return row.get("item_type") == "change" and value in (str(row.get("state", "")).lower(),
                                                              str(row.get("kind", "")).lower())
    return None


def matches(row, kind="all", query=""):
    """Whether a Vault panel row (dict) passes the filter chip and the search text."""
    if (row.get("item_type") == "change") != (kind == "changes"):
        return False  # ECR/ECO rows show under Changes only, and only they do
    if kind == "checked_out":
        if row.get("code") not in WRITABLE:
            return False
    elif kind in FILTER_TYPES and row.get("item_type") not in FILTER_TYPES[kind]:
        return False
    words = (query or "").lower().split()
    text = " ".join(str(row.get(k, "")) for k in ("part_number", "display", "description")).lower()
    for word in words:
        key, sep, value = word.partition(":")
        found = query_word_matches(row, key, value) if sep and value else None
        if found is None:
            found = word in text
        if not found:
            return False
    return True


class BackupSwitch:
    """Turns FreeCAD's backup files off for one save and puts the setting back.

    group is BaseApp/Preferences/Document (CreateBackupFiles, read by
    App::Document::saveToFile after the file is written, so a change between
    slotStartSaveDocument and slotFinishSaveDocument applies to that save only).
    """

    KEY = "CreateBackupFiles"

    def __init__(self, group):
        self.group = group
        self.saved = None  # (was the key set, its value)

    def suspend(self):
        if self.saved is None:
            present = any(kind == "Boolean" and name == self.KEY for kind, name, _v in (self.group.GetContents() or []))
            self.saved = (present, bool(self.group.GetBool(self.KEY, True)))
        self.group.SetBool(self.KEY, False)

    def restore(self):
        if self.saved is None:
            return
        present, value = self.saved
        self.saved = None
        if present:
            self.group.SetBool(self.KEY, value)
        else:
            self.group.RemBool(self.KEY)

    @property
    def active(self):
        return self.saved is not None


def moved_path(path, old_root, new_root):
    """Where a file under old_root is after the vault moved to new_root ("" if not under it)."""
    if not inside(path, old_root):
        return ""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(old_root))
    return os.path.join(new_root, rel)


def size_text(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000.0
    return ""


# ----- icons (SVG, one source for commands and the panel) ---------------------------------

_SVG = {
    "lock_me": '<path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" fill="none" stroke="{c}" stroke-width="2.2"/>'
               '<rect x="4.5" y="10" width="15" height="11.5" rx="2.6" fill="{c}"/>'
               '<path d="M8.8 15.7l2.2 2.2 4.2-4.3" fill="none" stroke="#fff" stroke-width="1.9" '
               'stroke-linecap="round" stroke-linejoin="round"/>',
    "lock_other": '<path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" fill="none" stroke="{c}" stroke-width="2.2"/>'
                  '<rect x="4.5" y="10" width="15" height="11.5" rx="2.6" fill="{c}"/>'
                  '<circle cx="12" cy="15" r="1.7" fill="#fff"/><path d="M12 15.5v2.6" stroke="#fff" '
                  'stroke-width="1.6" stroke-linecap="round"/>',
    "unlock": '<path d="M8 10.5V7.5a4 4 0 0 1 7.6-1.7" fill="none" stroke="{c}" stroke-width="2.2" '
              'stroke-linecap="round"/><rect x="4.5" y="10" width="15" height="11.5" rx="2.6" fill="{c}"/>',
    "checked_in": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="1.8"/>'
                  '<path d="M8 12.3l2.7 2.7L16.2 9.5" fill="none" stroke="{c}" stroke-width="1.8" '
                  'stroke-linecap="round" stroke-linejoin="round"/>',
    "newer": '<circle cx="12" cy="12" r="10" fill="{c}"/><path d="M12 6.5v10M7.6 12.4l4.4 4.4 4.4-4.4" '
             'fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "released": '<path d="M12 2.2l8.3 3.1v6.1c0 5.1-3.5 8.8-8.3 10.4-4.8-1.6-8.3-5.3-8.3-10.4V5.3z" fill="{c}"/>'
                '<path d="M8.2 12.1l2.7 2.7 5-5.1" fill="none" stroke="#fff" stroke-width="2.1" '
                'stroke-linecap="round" stroke-linejoin="round"/>',
    "pencil": '<path d="M4 20l1.1-4.6L15.6 4.9a2.2 2.2 0 0 1 3.1 0l.4.4a2.2 2.2 0 0 1 0 3.1L8.6 18.9z" fill="{c}"/>',
    "not_in_vault": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="1.7" '
                    'stroke-dasharray="3.1 2.5"/><path d="M12 8v8M8 12h8" stroke="{c}" stroke-width="1.7" '
                    'stroke-linecap="round"/>',
    "cloud": '<path d="M7 18.5h10.3a4.1 4.1 0 0 0 .5-8.2 6 6 0 0 0-11.5 1.3A3.5 3.5 0 0 0 7 18.5z" fill="none" '
             'stroke="{c}" stroke-width="1.7" stroke-linejoin="round"/>',
    "checkin": '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5" fill="none" stroke="{c}" stroke-width="2.1" '
               'stroke-linecap="round" stroke-linejoin="round"/><path d="M4 14v4.5A1.5 1.5 0 0 0 5.5 20h13a1.5 '
               '1.5 0 0 0 1.5-1.5V14" fill="none" stroke="{c}" stroke-width="2.1" stroke-linecap="round"/>',
    "undo": '<path d="M9 6L4.5 10.5 9 15" fill="none" stroke="{c}" stroke-width="2.1" stroke-linecap="round" '
            'stroke-linejoin="round"/><path d="M5 10.5h9a5 5 0 0 1 0 10h-3" fill="none" stroke="{c}" '
            'stroke-width="2.1" stroke-linecap="round"/>',
    "history": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="1.9"/><path d="M12 7v5.2l3.4 2" '
               'fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    "submit": '<path d="M3.5 11.2L20.5 4l-6.8 16.5-2.6-6.7z" fill="none" stroke="{c}" stroke-width="1.9" '
              'stroke-linejoin="round"/><path d="M11.1 13.8L20.5 4" stroke="{c}" stroke-width="1.9"/>',
    "open": '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.5l2 2H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 '
            '0 0 1-1.5-1.5z" fill="none" stroke="{c}" stroke-width="1.9" stroke-linejoin="round"/>',
    "panel": '<rect x="3.5" y="4" width="17" height="16" rx="2.5" fill="none" stroke="{c}" stroke-width="1.8"/>'
             '<path d="M7 9h10M7 12.5h10M7 16h6" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
    "refresh": '<path d="M19 12a7 7 0 1 1-2.05-4.95" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"/>'
               '<path d="M19.5 4v4h-4" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round"/>',
    "more": '<circle cx="5.5" cy="12" r="1.8" fill="{c}"/><circle cx="12" cy="12" r="1.8" fill="{c}"/>'
            '<circle cx="18.5" cy="12" r="1.8" fill="{c}"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6" fill="none" stroke="{c}" stroke-width="2"/><path d="M15 15l5 5" '
              'stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "sort": '<path d="M8 4v15M4.5 15.5L8 19l3.5-3.5M16 20V5M12.5 8.5L16 5l3.5 3.5" fill="none" stroke="{c}" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
}

# Fixed colours for command icons (menus can't follow the theme per icon).
_COMMAND_COLOURS = {"accent": "#0A84FF", "error": "#E0383E", "warn": "#C27C00", "ok": "#1F9D55",
                    "secondary": "#8E8E93"}


def svg_icon(kind, colour):
    body = _SVG[kind].replace("{c}", colour)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
            + body + "</svg>")


# ----- the session -----------------------------------------------------------------------

_state = {
    "session": None,
    "session_path": None,
    "error": "",
    "listeners": [],
    "installed": False,
}
_qt = {}  # Qt objects that must live as long as the session: watcher, timers, observers


def _console(text, level="log"):
    try:
        import FreeCAD

        {"log": FreeCAD.Console.PrintLog, "warn": FreeCAD.Console.PrintWarning,
         "error": FreeCAD.Console.PrintError, "msg": FreeCAD.Console.PrintMessage}[level](text + "\n")
    except Exception:
        print(text)


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def enabled():
    """Preferences/BruceWorx/VaultClient (default on)."""
    try:
        return bool(_prefs().GetBool("VaultClient", True))
    except Exception:
        return True


class Session:
    """The open vault: FolderVault with the pinned actor, and the user's Workspace."""

    def __init__(self, path, actors=None):
        import json

        from bruceworx import pdm_workspace as ws
        from bruceworx.pdm_folder import FolderVault, default_actor, find_ref_file

        self.path = path
        ref = find_ref_file(os.path.expanduser(path))
        with open(ref, encoding="utf-8") as handle:
            self.vault_id = str(json.load(handle).get("vault_id") or "")
        actor = default_actor()
        if actors is not None and self.vault_id:
            saved = actors.GetString(self.vault_id, "")
            actor = ws.pin_actor(saved, actor)
            if not saved.strip():
                actors.SetString(self.vault_id, actor)
        self.vault = FolderVault(ref, actor=actor)
        self.work = ws.Workspace(self.vault)
        self.name = self.vault.ref.get("name") or os.path.basename(self.vault.root)
        self.generation = 0
        self._statuses = None
        self.read_at = _dt.datetime.now(_dt.timezone.utc)

    @property
    def root(self):
        return self.vault.root

    @property
    def me(self):
        return self.vault.actor

    def refresh(self):
        self.vault.refresh()
        self.read_at = _dt.datetime.now(_dt.timezone.utc)
        self.invalidate()

    def invalidate(self):
        self.generation += 1
        self._statuses = None

    def statuses(self):
        if self._statuses is None:
            self._statuses = self.work.statuses()
        return self._statuses

    def status(self, pn):
        found = self.statuses().get(pn)
        return found if found is not None else self.work.status(pn)

    def pn_for(self, path):
        return part_number_for_file(path, self.work.root)

    def item_pn_for(self, path):
        """The part number when path is the work copy of a vault item, else ""."""
        pn = self.pn_for(path)
        return pn if pn and pn in self.vault.model.items else ""

    def counts(self):
        mine = newer = 0
        for st in self.statuses().values():
            mine += st.code in WRITABLE
            newer += st.code == NEWER
        return mine, newer

    def fetched(self, pn):
        return self.work.state.get("items", {}).get(pn, {}).get("fetched", "")


def session():
    """The open vault's Session, or None (no vault, an unreadable one, or the client is off)."""
    if not enabled():
        return None
    try:
        path = _prefs().GetString("VaultPath", "").strip()
    except Exception:
        return None
    if path != _state["session_path"]:
        _open_session(path)
    return _state["session"]


def _open_session(path):
    _state["session_path"] = path
    _state["session"] = None
    _state["error"] = ""
    if path:
        try:
            import FreeCAD

            _state["session"] = Session(path, FreeCAD.ParamGet(ACTORS_PATH))
            _state["session"].work.ensure_local = ensure_downloaded
        except Exception as exc:  # offline or not a vault: the client stays quiet
            _state["error"] = str(exc)
            _console(f"BruceWorx vault: {path}: {exc}")
    _watch()


def add_listener(listener):
    """listener() runs after the vault or a file status changed (debounced, GUI thread)."""
    if listener not in _state["listeners"]:
        _state["listeners"].append(listener)


def remove_listener(listener):
    if listener in _state["listeners"]:
        _state["listeners"].remove(listener)


def changed(refresh=False):
    """Something changed: re-read the journal (refresh) or only the file statuses, then tell everyone."""
    s = _state["session"]
    if s is not None:
        try:
            s.refresh() if refresh else s.invalidate()
        except Exception as exc:
            _console(f"BruceWorx vault: refresh failed: {exc}", "warn")
    timer = _qt.get("notify")
    if timer is not None:
        timer.start()
    else:
        _notify()


def _notify():
    for listener in list(_state["listeners"]):
        try:
            listener()
        except Exception as exc:
            _console(f"BruceWorx vault listener failed: {exc}", "warn")


def _watch():
    """Watch the journal folder and its files; a change re-reads the vault (debounced)."""
    watcher = _qt.get("watcher")
    if watcher is None:
        return
    old = list(watcher.files()) + list(watcher.directories())
    if old:
        watcher.removePaths(old)
    s = _state["session"]
    if s is None:
        return
    folder = os.path.join(s.root, "journal")
    paths = [folder] if os.path.isdir(folder) else []
    try:
        paths += [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".jsonl")]
    except OSError:
        pass
    if paths:
        watcher.addPaths(paths)


def _journal_changed(*_args):
    timer = _qt.get("journal")
    if timer is not None:
        timer.start()


def _journal_settled():
    changed(refresh=True)
    _watch()  # new journal files (another Mac, a sync copy) join the watch


# ----- FreeCAD documents -----------------------------------------------------------------


def _open_documents():
    import FreeCAD

    return list(FreeCAD.listDocuments().values())


def document_for(path):
    """The open document whose file is path, or None."""
    if not path:
        return None
    real = os.path.realpath(path)
    for doc in _open_documents():
        if doc.FileName and os.path.realpath(doc.FileName) == real:
            return doc
    return None


def _gui_modified(doc):
    try:
        import FreeCADGui

        gdoc = FreeCADGui.getDocument(doc.Name)
        return bool(gdoc and gdoc.Modified)
    except Exception:
        return False


def _set_unmodified(doc):
    try:
        import FreeCADGui

        gdoc = FreeCADGui.getDocument(doc.Name)
        if gdoc is not None:
            gdoc.Modified = False
    except Exception:
        pass


def pn_for_document(doc, s=None):
    s = s or session()
    if s is None or doc is None or not getattr(doc, "FileName", ""):
        return ""
    return s.item_pn_for(doc.FileName)


def pn_for_object(obj, s=None):
    """The vault item an object stands for: its document's, or (a link) the linked part's."""
    s = s or session()
    if s is None or obj is None:
        return ""
    target = obj
    try:
        if str(obj.TypeId) in ("App::Link", "App::LinkElement", "App::LinkGroup", "Assembly::AssemblyLink"):
            linked = obj.getLinkedObject(True)
            if linked is not None:
                target = linked
    except Exception:
        pass
    try:
        return pn_for_document(target.Document, s)
    except Exception:
        return ""


def current_target():
    """The item the commands act on: the selected object's, else the active document's."""
    import FreeCAD
    import FreeCADGui

    s = session()
    if s is None:
        return ""
    override = _state.get("target")
    if override:
        return override
    try:
        for obj in FreeCADGui.Selection.getSelection():
            pn = pn_for_object(obj, s)
            if pn:
                return pn
    except Exception:
        pass
    return pn_for_document(FreeCAD.ActiveDocument, s)


def _reload(doc):
    """Read a document again from its file (after Get Latest or Undo Check-Out replaced it)."""
    try:
        doc.restore()
        _set_unmodified(doc)
    except Exception as exc:
        _console(f"BruceWorx vault: {doc.Label} was not reloaded: {exc}", "warn")


def _replaced_files(before):
    """Open documents whose file content changed since before ({path: hash})."""
    from bruceworx.pdm_folder import sha256_of

    out = []
    for doc in _open_documents():
        path = os.path.realpath(doc.FileName) if doc.FileName else ""
        if path in before:
            try:
                now = sha256_of(path)
            except OSError:
                continue
            if now != before[path]:
                out.append(doc)
    return out


def _hash_open(s):
    from bruceworx.pdm_folder import sha256_of

    out = {}
    for doc in _open_documents():
        if doc.FileName and inside(doc.FileName, s.work.root):
            try:
                out[os.path.realpath(doc.FileName)] = sha256_of(doc.FileName)
            except OSError:
                pass
    return out


# ----- operations (the commands call these) -------------------------------------------------


def _window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _message(title, text, kind="information"):
    from PySide import QtWidgets

    getattr(QtWidgets.QMessageBox, kind)(_window(), title, text)


def _ask(title, text, yes="Yes", no="Cancel", default_yes=True):
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox(_window())
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(yes, QtWidgets.QMessageBox.AcceptRole)
    cancel = box.addButton(no, QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(ok if default_yes else cancel)
    box.exec()
    return box.clickedButton() is ok


def _run(title, fn, *args):
    """Run an engine call; a refusal (LifecycleError, ...) is shown, not raised."""
    try:
        return True, fn(*args)
    except Exception as exc:
        _message(title, str(exc) or type(exc).__name__, "warning")
        return False, None
    finally:
        changed()


def _display(s, pn):
    st = s.status(pn)
    return st.display


def check_out(pn=None):
    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    try:  # released or in review: say why, offer Raise ECR... (PDM-05)
        import bruceworx_changes

        if bruceworx_changes.explain_refused_checkout(s, pn):
            return False
    except ImportError:
        pass
    before = _hash_open(s)
    ok, _path = _run("Check Out", s.work.checkout, pn)
    if ok:
        for doc in _replaced_files(before):
            _reload(doc)
        _console(f"BruceWorx vault: checked out {pn}", "msg")
    _update_tabs()
    return ok


def undo_check_out(pn=None, confirm=True):
    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    st = s.status(pn)
    doc = document_for(s.work.path(pn))
    if confirm and (st.code == MODIFIED or (doc is not None and _gui_modified(doc))):
        since = relative_time(s.fetched(pn))
        text = f"Discard the changes to {st.display}" + (f" made since its check-out ({since})" if since else "") + \
            "?\n\nThe file goes back to the version you checked out and the lock is released."
        if not _ask("Undo Check-Out", text, "Discard Changes", "Cancel", default_yes=False):
            return False
    before = _hash_open(s)
    ok, _r = _run("Undo Check-Out", s.work.undo_checkout, pn)
    if ok:
        reload = _replaced_files(before)
        if doc is not None and doc not in reload and _gui_modified(doc):
            reload.append(doc)
        for d in reload:
            _reload(d)
    _update_tabs()
    return ok


def get_latest(pn=None):
    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    doc = document_for(s.work.path(pn))
    if doc is not None and _gui_modified(doc):
        if not _ask("Get Latest", f"{doc.Label} has unsaved changes. Get Latest replaces it with the vault's "
                    "latest version and the changes are lost.", "Get Latest", "Cancel", default_yes=False):
            return False
    before = _hash_open(s)
    ok, _r = _run("Get Latest", s.work.get_latest, pn)
    if ok:
        for d in _replaced_files(before):
            _reload(d)
    _update_tabs()
    return ok


def version_rows(s, pn):
    """[(label, sha256)] of an item's versions, newest first, for Get Revision."""
    rows = []
    versions = list(s.vault.versions.get(pn, []))
    for index, v in reversed(list(enumerate(versions, 1))):
        when = parse_time(v.at)
        stamp = when.astimezone().strftime("%d %b %Y %H:%M") if when else v.at
        comment = (v.comment or "").splitlines()[0][:60] if v.comment else ""
        rows.append((f"Version {index} · rev {v.revision} · {stamp} · {actor_name(v.actor, s.me)}"
                     + (f" — {comment}" if comment else ""), v.sha256))
    return rows


def get_revision(pn=None, sha=None):
    from PySide import QtWidgets

    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    rows = version_rows(s, pn)
    if not rows:
        _message("Get Revision", f"{pn} has no file in the vault yet.")
        return False
    if sha is None:
        labels = [label for label, _sha in rows]
        choice, ok = QtWidgets.QInputDialog.getItem(
            _window(), "Get Revision", f"Open an earlier version of {_display(s, pn)} read-only:", labels, 0, False)
        if not ok:
            return False
        sha = rows[labels.index(choice)][1]
    ok, path = _run("Get Revision", s.work.get_revision, pn, sha)
    if ok and path:
        _load(path)
    return ok


def submit(pn=None):
    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    if s.vault.locks.get(pn):
        holder = actor_name(s.vault.locks[pn], s.me)
        _message("Submit for Review", f"{_display(s, pn)} is checked out by {holder}. Check it in first.")
        return False
    ok, _r = _run("Submit for Review", s.vault.submit, pn)
    return ok


def approve(pn=None):
    try:  # the release checklist panel (PDM-05)
        import bruceworx_changes

        return bruceworx_changes.approve(pn)
    except ImportError:
        pass
    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    st = s.status(pn)
    if not _ask("Approve", f"Approve {st.display} and release it as revision A?\n\nA released revision never "
                "changes; later changes go through ECR → ECO.", "Approve", "Cancel"):
        return False
    ok, _r = _run("Approve", s.vault.approve, pn)
    return ok


def break_lock(pn=None, parent=None):
    """Break a lock: pn's, or one picked from every lock in the vault."""
    from PySide import QtWidgets

    s = session()
    if s is None:
        _message("Break Lock", "Open a vault first.")
        return False
    locks = dict(s.vault.locks)
    if pn is None:
        target = current_target() if parent is None else ""
        pn = target if target in locks else None
    if pn is None:
        if not locks:
            _message("Break Lock", f"Nothing in {s.name} is checked out.")
            return False
        labels = [f"{_display(s, p)} — checked out by {actor_name(a, s.me)}" for p, a in sorted(locks.items())]
        choice, ok = QtWidgets.QInputDialog.getItem(parent or _window(), "Break Lock",
                                                    "Release the lock on:", labels, 0, False)
        if not ok:
            return False
        pn = sorted(locks)[labels.index(choice)]
    holder = locks.get(pn)
    if not holder:
        _message("Break Lock", f"{_display(s, pn)} is not checked out.")
        return False
    text = (f"Break the lock {actor_name(holder, s.me)} holds on {_display(s, pn)}?\n\nUse this only after a crash "
            "or a lost Mac: changes in that check-out can no longer be checked in. The break is recorded in "
            "the journal.")
    if not _ask("Break Lock", text, "Break Lock", "Cancel", default_yes=False):
        return False
    ok, _r = _run("Break Lock", s.work.break_lock, pn)
    if ok and holder == s.me and os.path.exists(s.work.path(pn)):
        try:  # your own work copy goes back to read-only, as after Undo Check-Out
            from bruceworx.pdm_workspace import _set_writable

            _set_writable(s.work.path(pn), False)
        except Exception:
            pass
    _update_tabs()
    return ok


def _load(path):
    import FreeCADGui

    try:
        FreeCADGui.loadFile(path)
    except Exception as exc:
        _console(f"BruceWorx vault: {path}: {exc}", "error")


def open_from_vault(pn=None, ask=True):
    """Open an item's work copy (fetched read-only first when there is none).

    A file that could be checked out asks first: check out and open, or open read-only.
    """
    import FreeCADGui

    s = session()
    pn = pn or current_target()
    if s is None:
        return False
    if not pn:
        show_panel(True, focus_search=True)
        return True
    if pn not in s.vault.model.items and (pn in s.vault.ecrs or pn in s.vault.ecos):
        import bruceworx_changes

        return bruceworx_changes.show_change(pn)
    path = s.work.path(pn)
    st = s.status(pn)
    doc = document_for(path)
    if doc is not None:
        try:
            FreeCADGui.getDocument(doc.Name).ActiveView  # noqa: B018 -- a document with a view
            FreeCADGui.ActiveDocument = FreeCADGui.getDocument(doc.Name)
            views = FreeCADGui.getDocument(doc.Name).mdiViewsOfType("Gui::View3DInventor")
            if views:
                FreeCADGui.getMainWindow().setActiveWindow(views[0])
            else:
                _load(path)
        except Exception:
            _load(path)
        return True
    from bruceworx.pdm_workspace import icloud_placeholder

    if not os.path.exists(path) and not icloud_placeholder(path):  # an evicted copy is downloaded, not replaced
        if not s.vault.versions.get(pn):
            _message("Open from Vault", f"{st.display} has no file in the vault yet.")
            return False
        ok, _r = _run("Open from Vault", s.work.get_latest, pn)
        if not ok:
            return False
        st = s.status(pn)
    if ask and st.code in (UP_TO_DATE, NEWER) and st.state == WIP and not s.vault.locks.get(pn):
        from PySide import QtWidgets

        box = QtWidgets.QMessageBox(_window())
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Open from Vault")
        box.setText(f"Check out {st.display} to edit it?")
        box.setInformativeText("Opened without a check-out, the file is read-only.")
        out = box.addButton("Check Out and Open", QtWidgets.QMessageBox.AcceptRole)
        ro = box.addButton("Open Read-Only", QtWidgets.QMessageBox.NoRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(out)
        box.exec()
        if box.clickedButton() is out:
            if not check_out(pn):
                return False
        elif box.clickedButton() is not ro:
            return False
    ok, _r = _run("Open from Vault", s.work.make_local, pn)  # iCloud: the file and what it links
    if not ok:
        return False
    _load(s.work.path(pn))
    _update_tabs()
    return True


def check_in(pn=None):
    """Open the Check In task panel for pn (default: the current target)."""
    import FreeCADGui

    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return False
    if FreeCADGui.Control.activeDialog():
        _message("Check In", "Another command is open in the task panel. Close it (OK or Cancel) first.")
        return False
    try:
        panel = _classes()["CheckInPanel"](s, pn)
    except Exception as exc:
        _message("Check In", f"Check In could not open: {exc}", "warning")
        return False
    _state["panel"] = panel
    FreeCADGui.Control.showDialog(panel)
    return True


def checkin_rows(s, pn):
    """The files a check-in of pn offers: [dict], pn first, then what it links (transitively).

    A row is ticked when it is checked out to you and changed (on disk or unsaved in
    FreeCAD); files you have not checked out are listed greyed (not checkable).
    """
    from bruceworx.pdm_workspace import linked_files

    order, seen, outside = [pn], {pn}, []
    todo = [pn]
    while todo:
        current = todo.pop(0)
        for name in linked_files(s.work.path(current)):
            target = os.path.splitext(os.path.basename(name))[0]
            if target in s.vault.model.items:
                if target not in seen:
                    seen.add(target)
                    order.append(target)
                    todo.append(target)
            elif name and (current, name) not in outside:
                outside.append((current, name))
    rows = []
    for index, item in enumerate(order):
        st = s.status(item)
        doc = document_for(s.work.path(item))
        unsaved = doc is not None and _gui_modified(doc)
        mine = st.code in WRITABLE
        changed_ = st.code == MODIFIED or (mine and unsaved)
        note = {MODIFIED: "modified", CHECKED_OUT: "not changed", CHECKED_OUT_OTHER:
                f"checked out by {actor_name(st.lock, s.me)}", NEWER: "newer version in the vault",
                RELEASED: "released", UP_TO_DATE: "not checked out"}.get(st.code, STATUS_LABELS.get(st.code, ""))
        if mine and unsaved:
            note = "unsaved · saved first"
        rows.append({
            "part_number": item,
            "display": st.display,
            "description": st.description or item,
            "item_type": st.item_type,
            "code": st.code,
            "enabled": mine,
            "checked": mine and (changed_ or index == 0),
            "version": version_change(st.base, st.local) if changed_ and st.code == MODIFIED else
            (st.local or st.latest or "")[:7],
            "note": note,
            "document": doc.Name if doc is not None else "",
            "unsaved": unsaved,
            "main": index == 0,
        })
    return rows, outside


def link_summary(fixes):
    """(text lines, files to import) for the Check In panel's Links box, from LinkFix rows."""
    relink = sorted({f.link for f in fixes if f.target})
    imports = sorted({f.path for f in fixes if f.needs_import})
    missing = sorted({f.link for f in fixes if not f.target and not f.exists})
    lines = []
    if relink:
        lines.append(f"{len(relink)} link{'s' if len(relink) != 1 else ''} to vault files "
                     f"{'are' if len(relink) != 1 else 'is'} pointed at the part number"
                     f"{'s' if len(relink) != 1 else ''} before the check-in.")
    if imports:
        names = ", ".join(os.path.basename(p) for p in imports)
        lines.append(f"Not in the vault yet: {names}.")
    if missing:
        names = ", ".join(os.path.basename(p) for p in missing)
        lines.append(f"Not found: {names}. {'These links are' if len(missing) != 1 else 'This link is'} "
                     "checked in as written.")
    return lines, imports


def _writer():
    """freecad_io.write_part_number (gives imported files their part number), or None."""
    try:
        from bruceworx import freecad_io

        return getattr(freecad_io, "write_part_number", None)
    except Exception:
        return None


def checkin_links(s, pns, import_outside=True):
    """Point the links of pns at <P/N>.FCStd (plan section 4), importing outside files first when asked.

    Returns (links changed, [LinkFix] left as written, [ImportReport]).
    """
    from bruceworx import vault_setup as vs

    reports = []

    def importer(paths, known):
        program = vs.program_of(pns[0], s.root)
        report, mapping = vs.import_linked_files(s.vault, paths, program, known, _writer())
        reports.append(report)
        for path, key in report.imported:
            _console(f"BruceWorx vault: imported {os.path.basename(path)} as {key}", "msg")
        return mapping

    changed, left = s.work.fix_links(pns, importer if import_outside else None)
    return changed, left, reports


def run_checkin(s, rows, comment, keep=False, fix_links=True, import_outside=True, notes=None):
    """Save unsaved ones, fix links, then check in every ticked row. Returns (done [pn], failed [(pn, text)]).

    notes (a dict) receives "imported" [(path, pn)], "not_imported" [(path, why)], "left"
    [LinkFix] and "relinked" (paths whose links changed).
    """
    import FreeCAD

    done, failed = [], []
    notes = notes if notes is not None else {}
    for row in rows:
        if row.get("unsaved") and row.get("document"):
            try:
                FreeCAD.getDocument(row["document"]).save()
                _set_unmodified(FreeCAD.getDocument(row["document"]))
            except Exception as exc:
                failed.append((row["part_number"], f"not saved: {exc}"))
    s.invalidate()
    pns = [r["part_number"] for r in rows if not any(f[0] == r["part_number"] for f in failed)]
    if fix_links and pns:
        before = {pn: s.work.local_hash(s.work.path(pn)) for pn in pns}
        try:
            _changed, left, reports = checkin_links(s, pns, import_outside)
        except Exception as exc:  # nothing goes in with half-fixed links
            changed()
            return [], failed + [(pns[0], f"links not fixed, nothing checked in: {exc}")]
        notes["left"] = left
        notes["imported"] = [x for r in reports for x in r.imported]
        left_paths = {f.path for f in left}
        notes["not_imported"] = [x for r in reports for x in r.failed] + [
            x for r in reports for x in r.skipped if os.path.realpath(x[0]) in left_paths]
        notes["relinked"] = [s.work.path(pn) for pn in pns if s.work.local_hash(s.work.path(pn)) != before[pn]]
        s.invalidate()
    for row in rows:
        pn = row["part_number"]
        if any(f[0] == pn for f in failed):
            continue
        try:
            data = [(hook, hook.prepare(s, pn, notes)) for hook in CHECKIN_HOOKS]  # may refuse
            s.work.checkin(pn, comment, keep_checked_out=keep)
            done.append(pn)
        except Exception as exc:
            failed.append((pn, str(exc)))
            continue
        for hook, value in data:
            try:
                hook.record(s, pn, value, notes)
            except Exception as exc:  # the version is in; say what was not recorded
                failed.append((pn, f"checked in, but: {exc}"))
    changed()
    return done, failed


# Check-in hooks: objects with prepare(s, pn, notes) -> data (raise to refuse, before the file is
# stored) and record(s, pn, data, notes) after it is. bruceworx_bom adds the assembly's BOM (PDM-06).
CHECKIN_HOOKS = []


# ----- the save prompt, backups and first edits --------------------------------------------

_backups = None


def _backup_switch():
    global _backups
    if _backups is None:
        import FreeCAD

        _backups = BackupSwitch(FreeCAD.ParamGet(DOCUMENT_PATH))
    return _backups


def _offer_save_checkout(s, pn, filename):
    """Before FreeCAD writes a vault file: make sure it may (check out), or say why not."""
    s.invalidate()
    st = s.status(pn)
    if st.code in WRITABLE:
        if not os.access(filename, os.W_OK):
            from bruceworx.pdm_workspace import _set_writable

            _set_writable(filename, True)
        return True
    title = "Save"
    item = s.vault.model.items.get(pn)
    state = item.state if item is not None else ""
    if st.code == UP_TO_DATE and state == WIP and not st.lock:
        text = (f"Check out {st.display} to save?\n\nThe file is read-only because it is not checked out to you. "
                "Check Out takes the lock so the save can go ahead; without it the save fails.")
        if not _ask(title, text, "Check Out", "Don't Save", default_yes=True):
            return False
        try:
            s.work.checkout(pn, with_links=False)
        except Exception as exc:
            _message(title, f"{st.display} was not checked out: {exc}", "warning")
            return False
        _console(f"BruceWorx vault: checked out {pn} to save it", "msg")
        changed()
        _update_tabs_later()
        return True
    if st.code == RELEASED:
        try:
            import bruceworx_changes

            if bruceworx_changes.explain_refused_checkout(s, pn, saving=True):
                return False
        except ImportError:
            pass
    reasons = {
        CHECKED_OUT_OTHER: f"{st.display} is checked out by {actor_name(st.lock, s.me)}.",
        NEWER: f"A newer version of {st.display} is in the vault. Save your changes under another name, "
               "or Get Latest and make them again.",
        RELEASED: f"{st.display} is released. Changes go through ECR → ECO.",
    }
    text = reasons.get(st.code) or f"{st.display} is {state or 'not editable'} and cannot change now."
    _message(title, text + "\n\nThe file is read-only, so this save fails.")
    return False


def offers_first_edit(doc_name, active_name):
    """Whether a transaction opening in doc_name is your first edit of it. Only one in the active document
    is: a part opened to be linked gets transactions from the assembly's command (the vault writing its
    LifecycleState), which are not edits of the part. Saving any vault file still asks for its check-out."""
    return active_name is None or active_name == doc_name


class _DocumentObserver:
    """Save prompt, no backups for vault files, the first-edit offer, lifecycle-state writes."""

    def __init__(self):
        self.asked = set()  # documents whose first edit already offered a check-out
        self.was_modified = {}

    def slotStartSaveDocument(self, doc, filename):
        s = session()
        if s is None or not inside(filename, s.root):
            return
        pn = s.item_pn_for(filename)
        if pn and os.path.exists(filename):
            try:
                _offer_save_checkout(s, pn, filename)
            except Exception as exc:
                _console(f"BruceWorx vault: save check failed: {exc}", "warn")
        _backup_switch().suspend()
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, _backup_switch().restore)  # a refused save never finishes

    def slotFinishSaveDocument(self, doc, filename):
        if _backups is not None and _backups.active:
            _backups.restore()
        s = _state["session"]
        if s is not None and inside(filename, s.root):
            changed()

    def slotOpenTransaction(self, doc, name):
        """The first edit of a read-only vault file offers a check-out (once per document)."""
        if doc.Name in self.asked or getattr(doc, "Restoring", False):
            return
        import FreeCAD

        active = FreeCAD.ActiveDocument
        if not offers_first_edit(doc.Name, active.Name if active is not None else None):
            return
        s = _state["session"]
        pn = pn_for_document(doc, s) if s is not None else ""
        if not pn:
            return
        st = s.status(pn)
        item = s.vault.model.items.get(pn)
        if st.code != UP_TO_DATE or item is None or item.state != WIP:
            return
        self.asked.add(doc.Name)
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, lambda: _offer_first_edit(doc.Name, pn))

    def slotBeforeChangeObject(self, obj, prop):
        if prop == "LifecycleState":
            self.was_modified[obj.Document.Name] = _gui_modified(obj.Document)

    def slotChangedObject(self, obj, prop):
        """The vault writing LifecycleState into a read-only file is not an edit you could save."""
        if prop != "LifecycleState":
            return
        name = obj.Document.Name
        if self.was_modified.pop(name, True):
            return
        s = _state["session"]
        if s is None or not pn_for_document(obj.Document, s) or s.status(pn_for_document(obj.Document, s)).writable:
            return
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, lambda: _clear_modified(name))

    def slotActivateDocument(self, _doc):
        _update_tabs_later()

    def slotDeletedDocument(self, doc):
        self.asked.discard(doc.Name)
        _update_tabs_later()

    def slotRelabelDocument(self, _doc):
        _update_tabs_later()


def _clear_modified(name):
    import FreeCAD

    try:
        _set_unmodified(FreeCAD.getDocument(name))
    except Exception:
        pass


def _offer_first_edit(name, pn):
    import FreeCAD

    s = session()
    try:
        doc = FreeCAD.getDocument(name)
    except Exception:
        return
    if s is None or s.status(pn).code != UP_TO_DATE:
        return
    st = s.status(pn)
    text = (f"{doc.Label} ({st.display}) is not checked out to you, so changes to it cannot be saved.\n\n"
            "Check it out now?")
    if _ask("Check Out", text, "Check Out", "Not Now", default_yes=True):
        check_out(pn)


# ----- tabs: a lock on documents you cannot save -----------------------------------------------

def _update_tabs_later():
    timer = _qt.get("tabs")
    if timer is not None:
        timer.start()


def _update_tabs():
    """Prefix the tab of a vault file that is not checked out to you with a lock."""
    from PySide import QtWidgets

    try:
        mw = _window()
        mdi = mw.findChild(QtWidgets.QMdiArea)
    except Exception:
        return
    if mdi is None:
        return
    s = _state["session"]
    locked = {}
    if s is not None:
        rev = os.path.join(s.work.root, ".rev")
        for doc in _open_documents():
            pn = pn_for_document(doc, s)
            if pn and not s.status(pn).writable:
                locked[doc.Label] = STATUS_LABELS.get(s.status(pn).code, "").lower()
            elif doc.FileName and inside(doc.FileName, rev):
                locked[doc.Label] = "an earlier revision"
            if doc.Label in locked:
                locked[doc.Label] = f"{pn or doc.Label} is read-only: {locked[doc.Label]}."
    for sub in mdi.subWindowList():
        title = sub.windowTitle()
        bare = title[len(LOCK) + 1:] if title.startswith(LOCK + " ") else title
        label = bare.split(" : ")[0].replace("[*]", "")
        st = locked.get(label)
        want = f"{LOCK} {bare}" if st is not None else bare
        if want != title:
            sub.setWindowTitle(want)
        if st is not None:
            sub.setToolTip(st)


# ----- commands ------------------------------------------------------------------------------

def _target_status():
    s = session()
    pn = current_target() if s is not None else ""
    if not pn:
        return s, "", None
    try:
        return s, pn, s.status(pn)
    except Exception:
        return s, pn, None


def _active_check_out(s, pn, st):
    item = s.vault.model.items.get(pn)
    # Released: clickable, and check_out explains why not and offers Raise ECR... (PDM-05).
    return st is not None and item is not None and item.state in (WIP, STATE_RELEASED) and not st.lock


def _active_mine(s, pn, st):
    return st is not None and st.code in WRITABLE


def _active_get_latest(s, pn, st):
    return st is not None and st.code not in WRITABLE and bool(s.vault.versions.get(pn))


def _active_revision(s, pn, st):
    return bool(pn and s.vault.versions.get(pn))


def _active_submit(s, pn, st):
    item = s.vault.model.items.get(pn)
    return item is not None and item.state == WIP and not s.vault.locks.get(pn) and bool(s.vault.versions.get(pn))


def _active_approve(s, pn, st):
    item = s.vault.model.items.get(pn)
    return item is not None and item.state == IN_REVIEW and item.released is None


def _active_break(s, pn, st):
    return bool(s.vault.locks)


def _active_open(s, pn, st):
    return True


def _active_show(s, pn, st):
    return bool(pn)


# name, menu text, tooltip, icon, colour, function, predicate(session, pn, status), shortcut
COMMANDS = (
    ("BruceWorx_VaultCheckOut", "Check Out", "Take the lock on this file so you can change and save it. "
     "An assembly brings the parts it links, read-only.", "lock_me", "accent", check_out, _active_check_out, ""),
    ("BruceWorx_VaultCheckIn", "Check In…", "Store your changes in the vault as a new version, with a "
     "comment, and release the lock.", "checkin", "accent", check_in, _active_mine, "Shift+Ctrl+I"),
    ("BruceWorx_VaultUndoCheckOut", "Undo Check-Out…", "Discard the changes since check-out and release "
     "the lock.", "undo", "secondary", undo_check_out, _active_mine, ""),
    ("BruceWorx_VaultGetLatest", "Get Latest", "Replace your read-only copy with the vault's latest version.",
     "newer", "warn", get_latest, _active_get_latest, ""),
    ("BruceWorx_VaultGetRevision", "Get Revision…", "Open an earlier version read-only.", "history",
     "secondary", get_revision, _active_revision, ""),
    ("BruceWorx_VaultSubmit", "Submit for Review", "Put the work revision up for review; it is read-only "
     "until approved or rejected.", "submit", "accent", submit, _active_submit, ""),
    ("BruceWorx_VaultApprove", "Approve…", "Approve the revision in review with the release checklist: it "
     "is released as A.",
     "released", "ok", approve, _active_approve, ""),
    ("BruceWorx_VaultBreakLock", "Break Lock…", "Release a lock nobody can release normally (a crash, a "
     "lost Mac). Recorded in the journal.", "unlock", "error", lambda: break_lock(), _active_break, ""),
    ("BruceWorx_VaultOpen", "Open from Vault", "Open the file from the vault's working folder; read-only "
     "unless it is checked out to you.", "open", "secondary", open_from_vault, _active_open, ""),
    ("BruceWorx_VaultShowInPanel", "Show in Vault Panel", "Show this file in the Vault panel.", "panel",
     "secondary", lambda: show_in_panel(), _active_show, ""),
)
PANEL_COMMAND = "BruceWorx_VaultPanel"
# The right-click menus (Browser, Vault panel): in this order, None is a separator.
CONTEXT_COMMANDS = ("BruceWorx_VaultCheckOut", "BruceWorx_VaultCheckIn", "BruceWorx_VaultUndoCheckOut",
                    "BruceWorx_VaultGetLatest", "BruceWorx_VaultGetRevision", None, "BruceWorx_VaultSubmit",
                    "BruceWorx_VaultApprove", None, "BruceWorx_VaultOpen", "BruceWorx_VaultShowInPanel")


def _icon_name(kind, colour):
    return f"BruceWorx_Vault_{kind}_{colour}"


class _Command:
    def __init__(self, name, text, tip, icon, colour, fn, active, shortcut):
        self.name, self.text, self.tip = name, text, tip
        self.icon, self.colour, self.fn, self.active, self.shortcut = icon, colour, fn, active, shortcut

    def GetResources(self):
        res = {"Pixmap": _icon_name(self.icon, self.colour), "MenuText": self.text, "ToolTip": self.tip}
        if self.shortcut:
            res["Accel"] = self.shortcut
        return res

    def IsActive(self):
        try:
            s, pn, st = _target_status()
            if s is None:
                return False
            if self.name in ("BruceWorx_VaultBreakLock", "BruceWorx_VaultOpen"):
                return bool(self.active(s, pn, st))
            return bool(pn) and bool(self.active(s, pn, st))
        except Exception:
            return False

    def Activated(self):
        try:
            self.fn()
        except Exception as exc:
            _message(self.text.rstrip("…"), str(exc), "warning")


class _PanelCommand:
    def GetResources(self):
        return {"Pixmap": _icon_name("panel", "secondary"), "MenuText": "Vault Files",
                "ToolTip": "The vault's files with their status, on the Start page's Files tab.", "Checkable": False}

    def IsActive(self):
        return session() is not None

    def Activated(self, checked=None):
        show_panel(True, focus_search=True)


def available(name, pn=None):
    """Whether command name can act on pn (default: the current target), without Command.isActive()."""
    s = session()
    if s is None:
        return False
    for entry in COMMANDS:
        if entry[0] == name:
            pn = pn or current_target()
            st = s.status(pn) if pn else None
            if name in ("BruceWorx_VaultBreakLock", "BruceWorx_VaultOpen"):
                return bool(entry[6](s, pn, st))
            return bool(pn) and bool(entry[6](s, pn, st))
    return False


def add_item_commands(entries, after="BruceWorx_VaultApprove"):
    """More item commands (bruceworx_changes.py) in the right-click menus and the Vault menu, after
    ``after``. entries are COMMANDS tuples; the caller registers the FreeCAD commands. Before late()."""
    global COMMANDS, CONTEXT_COMMANDS
    known = {e[0] for e in COMMANDS}
    COMMANDS = COMMANDS + tuple(e for e in entries if e[0] not in known)
    names = list(CONTEXT_COMMANDS)
    at = names.index(after) + 1 if after in names else len(names) - 2
    for entry in entries:
        if entry[0] not in names:
            names.insert(at, entry[0])
            at += 1
    CONTEXT_COMMANDS = tuple(names)


def run_for(name, pn):
    """Run command name on pn (the Vault panel's menu): the commands read the target from here."""
    _state["target"] = pn
    try:
        for entry in COMMANDS:
            if entry[0] == name:
                if name == "BruceWorx_VaultBreakLock":
                    return break_lock(pn)
                if name == "BruceWorx_VaultShowInPanel":
                    return show_in_panel(pn)
                return entry[5](pn)
    finally:
        _state["target"] = None
    return None


class _Manipulator:
    """The vault commands in the Browser's right-click menu, and the panel in the View menu."""

    def modifyContextMenu(self, recipient):
        if recipient != "Tree" or not enabled():
            return []
        try:
            if not current_target():
                return []
        except Exception:
            return []
        items = []
        for name in CONTEXT_COMMANDS:
            items.append({"insert": name or "Separator", "menuItem": "Std_ToggleFreeze"})
        items.append({"insert": "Separator", "menuItem": "Std_ToggleFreeze"})
        return items

    def modifyMenuBar(self):
        return [{"insert": PANEL_COMMAND, "menuItem": "Std_DockViewMenu", "after": ""}]


# ----- the Vault panel ------------------------------------------------------------------------

_classes_cache = None


def _classes():
    global _classes_cache
    if _classes_cache is None:
        _classes_cache = _make_classes()
    return _classes_cache


def _svg_pixmap(kind, colour, size, dpr):
    from PySide import QtCore, QtGui

    data = QtCore.QByteArray(svg_icon(kind, colour).encode("utf-8"))
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(buffer, b"svg")
    px = int(round(size * dpr))
    reader.setScaledSize(QtCore.QSize(px, px))
    image = reader.read()
    buffer.close()
    pixmap = QtGui.QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _make_classes():
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    icon_cache = {}

    def icon_pixmap(kind, colour, size, dpr):
        key = (kind, colour.name() if hasattr(colour, "name") else str(colour), size, dpr)
        if key not in icon_cache:
            name = colour.name() if hasattr(colour, "name") else str(colour)
            icon_cache[key] = _svg_pixmap(kind, name, size, dpr)
        return icon_cache[key]

    def themed_icon(widget, kind, role="secondary", size=16):
        colour = widget.palette().color(QtGui.QPalette.WindowText)
        if role == "secondary":
            colour.setAlpha(255)
            colour = QtGui.QColor(colour)
        return QtGui.QIcon(icon_pixmap(kind, colour, size, widget.devicePixelRatioF()))

    class VaultList(QtWidgets.QTreeView):
        """The Files tree. Its colours are theme tokens, set from BruceWorx.qss (qproperty-*); the
        row backgrounds (hover, selection, the open file's accent bar) are painted across the row."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.panel = parent
            self._hover = None
            self.same, self.uses = set(), set()  # where used: rows' paths (PATH_ROLE)
            pal = self.palette()
            self._c = {
                "text": pal.color(QtGui.QPalette.Text),
                "secondary": QtGui.QColor("#6E6E73"),
                "accent": pal.color(QtGui.QPalette.Highlight),
                "ok": QtGui.QColor("#1F9D55"),
                "warn": QtGui.QColor("#C27C00"),
                "error": QtGui.QColor("#D93A3F"),
                "border": QtGui.QColor("#D9D9DE"),
                "hover": QtGui.QColor("#E6E6EA"),
                "selected": QtGui.QColor("#DCE9FB"),
                "card": QtGui.QColor("#FFFFFF"),
            }

        def colour(self, role):
            if role == "dim":
                c = QtGui.QColor(self._c["secondary"])
                c.setAlpha(150)
                return c
            return self._c.get(role, self._c["text"])

        def _getter(key):
            return lambda self: self._c[key]

        def _setter(key):
            def setter(self, value):
                self._c[key] = QtGui.QColor(value)
                self.viewport().update()
            return setter

        textColor = QtCore.Property(QtGui.QColor, _getter("text"), _setter("text"))
        secondaryColor = QtCore.Property(QtGui.QColor, _getter("secondary"), _setter("secondary"))
        accentColor = QtCore.Property(QtGui.QColor, _getter("accent"), _setter("accent"))
        okColor = QtCore.Property(QtGui.QColor, _getter("ok"), _setter("ok"))
        warnColor = QtCore.Property(QtGui.QColor, _getter("warn"), _setter("warn"))
        errorColor = QtCore.Property(QtGui.QColor, _getter("error"), _setter("error"))
        borderColor = QtCore.Property(QtGui.QColor, _getter("border"), _setter("border"))
        hoverColor = QtCore.Property(QtGui.QColor, _getter("hover"), _setter("hover"))
        selectedColor = QtCore.Property(QtGui.QColor, _getter("selected"), _setter("selected"))
        cardColor = QtCore.Property(QtGui.QColor, _getter("card"), _setter("card"))

        def drawRow(self, painter, option, index):
            whole = QtCore.QRect(0, option.rect.top(), self.viewport().width(), option.rect.height())
            first = index.siblingAtColumn(0)
            selection = self.selectionModel()
            key = first.data(PATH_ROLE)
            if selection is not None and selection.isSelected(first):
                painter.fillRect(whole, self.colour("selected"))
            elif self._hover is not None and self._hover == key:
                painter.fillRect(whole, self.colour("hover"))
            elif key in self.same:  # the selected file, listed again elsewhere
                painter.fillRect(whole, soft(self.colour("accent"), 46))
            elif key in self.uses:  # an assembly the selected file is used in
                painter.fillRect(whole, soft(self.colour("accent"), 20))
            row = self.panel.row_for(first) if self.panel is not None else None
            if row is not None and row.get("active"):
                painter.fillRect(QtCore.QRect(0, whole.top() + 5, 3, whole.height() - 10), self.colour("accent"))
            super().drawRow(painter, option, index)

        def mouseMoveEvent(self, event):
            index = self.indexAt(event.position().toPoint())
            hover = index.siblingAtColumn(0).data(PATH_ROLE) if index.isValid() else None
            if hover != self._hover:
                self._hover = hover
                self.viewport().update()
            super().mouseMoveEvent(event)

        def leaveEvent(self, event):
            self._hover = None
            self.viewport().update()
            super().leaveEvent(event)

    ROW_HEIGHT = 30
    FOOTER = "Double-click opens · select a file to see where it is used · editing offers Check Out"
    COLUMNS = ("Part number", "Description", "Qty", "State", "Status")
    PN_ROLE = QtCore.Qt.UserRole
    QTY_ROLE = QtCore.Qt.UserRole + 1
    CONTEXT_ROLE = QtCore.Qt.UserRole + 2  # shown only as the parent of a match: dimmed
    GROUP_ROLE = QtCore.Qt.UserRole + 3
    PATH_ROLE = QtCore.Qt.UserRole + 4  # P/Ns from the top, to keep a folded branch folded
    TIP_WIDTH = 220  # the largest a thumbnail is drawn in the file name's tooltip

    def soft(colour, alpha=40):
        c = QtGui.QColor(colour)
        c.setAlpha(alpha)
        return c

    def _esc(text):
        return (str(text or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    class Delegate(QtWidgets.QStyledItemDelegate):
        """One line per file: P/N-REV, description, quantity, state chip, status (Get latest)."""

        def __init__(self, panel):
            super().__init__(panel)
            self.panel = panel

        def sizeHint(self, option, index):
            return QtCore.QSize(option.rect.width(), ROW_HEIGHT)

        def button_rect(self, rect):
            fm = QtGui.QFontMetrics(self.panel.small_font)
            w = fm.horizontalAdvance("Get latest") + 18
            return QtCore.QRect(rect.right() - w - 8, rect.center().y() - 10, w, 20)

        def paint(self, painter, option, index):
            view = self.panel.list
            rect = option.rect
            first = index.siblingAtColumn(0)
            column = index.column()
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            group = first.data(GROUP_ROLE)
            if group:
                if column == 0:
                    painter.setFont(self.panel.title_font)
                    painter.setPen(view.colour("secondary"))
                    painter.drawText(rect.adjusted(4, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, group)
                painter.restore()
                return
            row = self.panel.row_for(first)
            if row is None:
                painter.restore()
                return
            dim = bool(first.data(CONTEXT_ROLE))
            text_colour = view.colour("dim" if dim else "text")
            secondary = view.colour("dim" if dim else "secondary")
            r = rect.adjusted(4 if column == 0 else 8, 0, -6, 0)
            middle = QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft
            if column == 0:
                painter.setFont(self.panel.title_font)
                fm = QtGui.QFontMetrics(self.panel.title_font)
                pn = row["part_number"]
                rev = ("-" + row["revision"]) if row.get("revision") else ""
                pn_text = fm.elidedText(pn, QtCore.Qt.ElideRight, max(20, r.width() - fm.horizontalAdvance(rev)))
                painter.setPen(text_colour)
                painter.drawText(r, middle, pn_text)
                painter.setPen(secondary)
                painter.drawText(r.adjusted(fm.horizontalAdvance(pn_text), 0, 0, 0), middle, rev)
            elif column == 1:
                painter.setFont(self.panel.body_font)
                fm = QtGui.QFontMetrics(self.panel.body_font)
                painter.setPen(text_colour)
                painter.drawText(r, middle, fm.elidedText(row.get("description") or "", QtCore.Qt.ElideRight, r.width()))
            elif column == 2:
                qty = first.data(QTY_ROLE)
                if qty:
                    painter.setFont(self.panel.body_font)
                    painter.setPen(text_colour)
                    painter.drawText(r, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, f"{qty:g}"
                                     if isinstance(qty, float) else str(qty))
            elif column == 3:
                chip_text, chip_role = row.get("chip") or STATE_CHIPS.get(row.get("state"), (
                    (row.get("state") or "").upper(), "secondary"))
                if chip_text:
                    fm = QtGui.QFontMetrics(self.panel.chip_font)
                    cw = fm.horizontalAdvance(chip_text) + 10
                    chip = QtCore.QRectF(r.left(), rect.center().y() - (fm.height() + 3) / 2, cw, fm.height() + 3)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(soft(view.colour(chip_role), 26 if dim else 46))
                    painter.drawRoundedRect(chip, 4, 4)
                    painter.setFont(self.panel.chip_font)
                    painter.setPen(secondary if dim else view.colour(chip_role))
                    painter.drawText(chip, QtCore.Qt.AlignCenter, chip_text)
            elif column == 4:
                dpr = view.devicePixelRatioF()
                x = r.left()
                for kind, role in status_icons(row["code"]):
                    painter.drawPixmap(QtCore.QPointF(x, rect.center().y() - 8),
                                       icon_pixmap(kind, secondary if dim else view.colour(role), 16, dpr))
                    x += 20
                right = r.right()
                if row["code"] == NEWER:
                    b = self.button_rect(rect)
                    right = b.left() - 6
                    painter.setPen(view.colour("border"))
                    painter.setBrush(view.colour("card"))
                    painter.drawRoundedRect(QtCore.QRectF(b).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                    painter.setPen(view.colour("text"))
                    painter.setFont(self.panel.small_font)
                    painter.drawText(b, QtCore.Qt.AlignCenter, "Get latest")
                painter.setFont(self.panel.small_font)
                fm = QtGui.QFontMetrics(self.panel.small_font)
                painter.setPen(secondary if dim else view.colour(row["status_role"]))
                width = max(10, right - x)
                painter.drawText(QtCore.QRect(x, rect.top(), width, rect.height()), middle,
                                 fm.elidedText(row["status_text"], QtCore.Qt.ElideRight, width))
            painter.restore()

        def editorEvent(self, event, model, option, index):
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton \
                    and index.column() == len(COLUMNS) - 1:
                row = self.panel.row_for(index)
                if row is not None and row["code"] == NEWER and \
                        self.button_rect(option.rect).contains(event.position().toPoint()):
                    pn = row["part_number"]
                    QtCore.QTimer.singleShot(0, lambda: run_for("BruceWorx_VaultGetLatest", pn))
                    return True
            return False

        def helpEvent(self, event, view, option, index):
            row = self.panel.row_for(index)
            if row is not None and event.type() == QtCore.QEvent.ToolTip:
                status = STATUS_LABELS.get(row["code"], "")
                if index.column() == 0:  # the file name: its picture, as Explorer and Finder do
                    QtWidgets.QToolTip.showText(event.globalPos(), self.panel.tooltip_html(row), view)
                else:
                    QtWidgets.QToolTip.showText(event.globalPos(), f"{row['display']} — {status}", view)
                return True
            return super().helpEvent(event, view, option, index)

    class ChipFlow(QtWidgets.QLayout):
        """The filter chips, left to right, wrapping to a second line when the card is narrow."""

        def __init__(self, spacing=4):
            super().__init__()
            self._items = []
            self.setSpacing(spacing)
            self.setContentsMargins(0, 0, 0, 0)

        def addItem(self, item):
            self._items.append(item)

        def count(self):
            return len(self._items)

        def itemAt(self, index):
            return self._items[index] if 0 <= index < len(self._items) else None

        def takeAt(self, index):
            return self._items.pop(index) if 0 <= index < len(self._items) else None

        def expandingDirections(self):
            return QtCore.Qt.Orientations(0)

        def hasHeightForWidth(self):
            return True

        def heightForWidth(self, width):
            return self._place(QtCore.QRect(0, 0, width, 0), apply=False)

        def setGeometry(self, rect):
            super().setGeometry(rect)
            self._place(rect, apply=True)

        def sizeHint(self):
            return self.minimumSize()

        def minimumSize(self):
            size = QtCore.QSize()
            for item in self._items:
                size = size.expandedTo(item.minimumSize())
            return size

        def _place(self, rect, apply):
            x, y, line = rect.x(), rect.y(), 0
            gap = self.spacing()
            for item in self._items:
                hint = item.sizeHint()
                if x + hint.width() > rect.right() + 1 and line:
                    x, y, line = rect.x(), y + line + gap, 0
                if apply:
                    item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
                x += hint.width() + gap
                line = max(line, hint.height())
            return y + line - rect.y()

    class VaultPanel(QtWidgets.QWidget):
        """The Vault card: header, search, filter chips, the list, a footer."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwVaultPanel")
            self.setWindowTitle("Vault")
            self.rows = {}
            self.nodes = []
            self.structure = {"items": {}, "bom": {}, "sources": {}, "drawings": {}}
            self.thumb_files = {}
            self.links_cache = {}
            self.collapsed = set()
            self._filling = False
            self.kind = "all"
            self.sort_by_time = False  # a tree reads as a BOM: by part number; the sort button swaps
            base = QtGui.QFont(self.font())
            self.title_font = QtGui.QFont(base)
            self.title_font.setPixelSize(13)
            self.title_font.setWeight(QtGui.QFont.DemiBold)
            self.body_font = QtGui.QFont(base)
            self.body_font.setPixelSize(12)
            self.small_font = QtGui.QFont(base)
            self.small_font.setPixelSize(11)
            self.chip_font = QtGui.QFont(base)
            self.chip_font.setPixelSize(10)
            self.chip_font.setWeight(QtGui.QFont.Bold)
            self.chip_font.setLetterSpacing(QtGui.QFont.PercentageSpacing, 104)

            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            top = QtWidgets.QWidget()
            top.setObjectName("bwVaultTop")
            tl = QtWidgets.QVBoxLayout(top)
            tl.setContentsMargins(12, 8, 12, 6)
            tl.setSpacing(8)
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(6)
            self.cloud = QtWidgets.QLabel()
            self.name = QtWidgets.QLabel("Vault")
            self.name.setObjectName("bwVaultName")
            self.sync = QtWidgets.QLabel("")
            self.sync.setObjectName("bwVaultSync")
            self.refresh_button = QtWidgets.QToolButton()
            self.refresh_button.setObjectName("bwVaultTool")
            self.refresh_button.setToolTip("Read the vault again")
            self.refresh_button.clicked.connect(lambda: changed(refresh=True))
            self.more = QtWidgets.QToolButton()
            self.more.setObjectName("bwVaultTool")
            self.more.setToolTip("Vault")
            self.more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.more_menu = QtWidgets.QMenu(self.more)
            self.more_menu.aboutToShow.connect(self.fill_more)
            self.more.setMenu(self.more_menu)
            head.addWidget(self.cloud)
            head.addWidget(self.name)
            head.addWidget(self.sync, 1)
            head.addWidget(self.refresh_button)
            head.addWidget(self.more)
            tl.addLayout(head)
            self.search = QtWidgets.QLineEdit()
            self.search.setObjectName("bwVaultSearch")
            self.search.setPlaceholderText("Search part number or description")
            self.search.setClearButtonEnabled(True)
            self.search_action = self.search.addAction(QtGui.QIcon(), QtWidgets.QLineEdit.LeadingPosition)
            self.search.textChanged.connect(lambda _t: self.fill())
            tl.addWidget(self.search)
            chips = ChipFlow(4)  # wraps: six chips do not fit a 400 px card on one line
            self.chips = {}
            group = QtWidgets.QButtonGroup(self)
            group.setExclusive(True)
            for key, text in FILTERS:
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.setCheckable(True)
                b.setChecked(key == "all")
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                chip_font = QtGui.QFont(b.font())
                chip_font.setPixelSize(12)
                b.setFont(chip_font)
                b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                b.clicked.connect(lambda _c=False, k=key: self.set_kind(k))
                group.addButton(b)
                chips.addWidget(b)
                self.chips[key] = b
            self.chip_group = group
            tl.addLayout(chips)
            count = QtWidgets.QHBoxLayout()
            self.count = QtWidgets.QLabel("")
            self.count.setObjectName("bwVaultCount")
            self.sort = QtWidgets.QToolButton()
            self.sort.setObjectName("bwVaultSort")
            self.sort.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.sort.setLayoutDirection(QtCore.Qt.RightToLeft)
            self.sort.clicked.connect(self.toggle_sort)
            count.addWidget(self.count, 1)
            count.addWidget(self.sort)
            tl.addLayout(count)
            lay.addWidget(top)
            self.list = VaultList(self)
            self.list.setObjectName("bwVaultList")
            self.list.setMouseTracking(True)
            self.list.setUniformRowHeights(True)
            self.list.setIndentation(16)
            self.list.setAllColumnsShowFocus(True)
            self.list.setExpandsOnDoubleClick(False)  # double-click opens the file
            self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
            self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.model = QtGui.QStandardItemModel(0, len(COLUMNS), self.list)
            self.model.setHorizontalHeaderLabels(list(COLUMNS))
            self.list.setModel(self.model)
            header = self.list.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            for column, width in ((0, 260), (2, 40), (3, 84), (4, 200)):
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
                self.list.setColumnWidth(column, width)
            self.delegate = Delegate(self)
            self.list.setItemDelegate(self.delegate)
            self.list.doubleClicked.connect(self.open_index)
            self.list.expanded.connect(lambda index: self.fold(index, False))
            self.list.collapsed.connect(lambda index: self.fold(index, True))
            self.list.selectionModel().currentChanged.connect(lambda current, _p: self.show_where_used(current))
            self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.context_menu)
            lay.addWidget(self.list, 1)
            self.empty = QtWidgets.QLabel("")
            self.empty.setObjectName("bwVaultEmpty")
            self.empty.setAlignment(QtCore.Qt.AlignCenter)
            self.empty.setWordWrap(True)
            self.empty.hide()
            lay.addWidget(self.empty, 1)
            self.footer = QtWidgets.QLabel(FOOTER)
            self.footer.setWordWrap(True)
            self.footer.setObjectName("bwVaultFooter")
            self.footer.setContentsMargins(12, 6, 12, 8)
            lay.addWidget(self.footer)
            self.clock = QtCore.QTimer(self)
            self.clock.setInterval(60000)
            self.clock.timeout.connect(self.update_header)
            self.clock.start()
            self.restyle_icons()
            add_listener(self.refresh)
            self.refresh()

        # --- data
        def row_for(self, index):
            pn = index.siblingAtColumn(0).data(PN_ROLE) if index.isValid() else None
            return self.rows.get(pn)

        def refresh(self):
            if not _alive(self):
                remove_listener(self.refresh)
                return
            s = session()
            self.rows = {}
            active = ""
            try:
                import FreeCAD

                active = pn_for_document(FreeCAD.ActiveDocument, s) if s is not None else ""
            except Exception:
                pass
            if s is not None:
                now = _dt.datetime.now(_dt.timezone.utc)
                for pn, st in s.statuses().items():
                    versions = s.vault.versions.get(pn, [])
                    text, role = row_status(st.code, st.state, st.lock, s.me, versions, s.fetched(pn), now)
                    last = versions[-1] if versions else None
                    self.rows[pn] = {
                        "part_number": pn, "display": st.display, "revision": st.revision,
                        "description": st.description, "state": st.state, "code": st.code,
                        "item_type": st.item_type, "status_text": text, "status_role": role,
                        "sha": last.sha256 if last else "", "object": os.path.join(s.root, last.object_name)
                        if last else "", "at": last.at if last else "", "active": pn == active,
                    }
                try:  # ECRs and ECOs, under the Changes chip (PDM-05)
                    import bruceworx_changes

                    self.rows.update(bruceworx_changes.change_rows(s.vault, s.me, now))
                except ImportError:
                    pass
            self.nodes = self.file_tree(s)
            self.update_header()
            self.fill()

        def file_tree(self, s):
            """The vault as one tree (vault_trees.file_tree): top assemblies, their parts, every
            item's drawings. An assembly's file links are read once per version."""
            if s is None:
                return []
            from bruceworx import vault_trees

            def links(pn):
                row = self.rows.get(pn) or {}
                sha, path = row.get("sha"), row.get("object")
                if not sha or not path or not os.path.exists(path):
                    return {}
                if sha not in self.links_cache:
                    self.links_cache[sha] = vault_trees.file_links(path)
                return self.links_cache[sha]

            try:
                self.structure = vault_trees.structure(s.vault, links=links)
                return vault_trees.file_tree(self.structure)
            except Exception as exc:  # noqa: BLE001 -- a damaged vault still lists its files, flat
                _console(f"Vault: the Files tree could not be built ({exc}); listing the files flat", "warn")
                self.structure = {"items": {}, "bom": {}, "sources": {}, "drawings": {}}
                return [{"pn": pn, "qty": None, "children": []} for pn in sorted(self.rows)]

        def update_header(self):
            if not _alive(self):
                return
            s = _state["session"]
            if s is None:
                self.name.setText("No vault open")
                self.sync.setText("")
                return
            self.name.setText(s.name)
            missing = len(s.vault.missing_objects())
            if missing:
                text = f"{missing} file{'s' if missing != 1 else ''} still downloading"
            else:
                text = f"Read {relative_time(s.read_at)}"
            dot = self.list.colour("warn" if missing else "ok").name()
            self.sync.setText(f'<span style="color:{dot}">●</span> {text}')
            self.sync.setToolTip(f"{s.root}\nYou are {s.me} in this vault.")
            mine = sum(1 for r in self.rows.values() if r["code"] in WRITABLE)
            self.chips["checked_out"].setText(f"Checked out {mine}" if mine else "Checked out")
            todo = sum(1 for r in self.rows.values() if r.get("item_type") == "change" and r.get("pending"))
            self.chips["changes"].setText(f"Changes {todo}" if todo else "Changes")

        def fill(self):
            if not _alive(self):
                return
            query = self.search.text()
            changes = self.kind == "changes"
            hits = {pn for pn, r in self.rows.items() if matches(r, self.kind, query)}
            filtering = bool(query.strip()) or self.kind != "all"
            if changes:  # ECRs and ECOs are not files: a plain list
                nodes = [{"pn": pn, "qty": None, "children": []} for pn in self.rows
                         if self.rows[pn].get("item_type") == "change"]
            else:
                nodes = list(self.nodes)
                listed = set()

                def collect(ns):
                    for n in ns:
                        listed.add(n["pn"])
                        collect(n["children"])

                collect(nodes)
                extra = [{"pn": pn, "qty": None, "children": []} for pn in sorted(self.rows)
                         if pn not in listed and self.rows[pn].get("item_type") != "change"]
                if extra:  # in the workspace but not (yet) in the vault's replay
                    nodes.append({"pn": None, "group": "Other files", "qty": None, "children": extra})
            current = self.row_for(self.list.currentIndex())
            current = current["part_number"] if current else None
            self._filling = True
            try:
                self.model.removeRows(0, self.model.rowCount())
                expand = []
                for node in self.prune(nodes, hits):
                    self.model.appendRow(self.make_items(node, "", expand))
                for item, path in expand:
                    self.list.setExpanded(item.index(), filtering or path not in self.collapsed)
                if current:
                    self.find(current, reveal=False)
            finally:
                self._filling = False
            self.fit_name_column()
            self.show_where_used(self.list.currentIndex())
            total = sum(1 for r in self.rows.values() if (r.get("item_type") == "change") == changes)
            shown = len(hits)
            word = "change" if changes else "item"
            self.count.setText(f"{shown} {word}{'s' if shown != 1 else ''}" +
                               (f" of {total}" if shown != total else ""))
            self.sort.setText("Modified" if self.sort_by_time else "Part number")
            s = _state["session"]
            if s is None:
                self.empty.setText(_state["error"] and f"The vault cannot be read:\n{_state['error']}" or
                                   "No vault is open.\nOpen one from the Start page.")
            elif not total and changes:
                self.empty.setText("No ECRs or ECOs yet.\nRaise ECR… on a released item starts one.")
            elif not total:
                self.empty.setText("The vault is empty.\nBruceWorx → Import Files into Vault… adds files.")
            else:
                self.empty.setText("Nothing matches.")
            self.empty.setVisible(not hits)
            self.list.setVisible(bool(hits))

        def prune(self, nodes, hits):
            """The tree cut to the matches and the assemblies above them (marked "context"), each
            level sorted: drawings first, then by the sort button; the loose group last."""
            out = []
            for node in nodes:
                children = self.prune(node["children"], hits)
                if node["pn"] is None:
                    if children:
                        out.append(dict(node, children=children))
                elif node["pn"] in hits or children:
                    out.append(dict(node, children=children, context=node["pn"] not in hits))

            def row(n):
                return self.rows.get(n["pn"]) or {}

            out.sort(key=lambda n: n["pn"] or "")
            if self.sort_by_time:
                out.sort(key=lambda n: row(n).get("at") or "", reverse=True)
            out.sort(key=lambda n: (n["pn"] is None, row(n).get("item_type") != "drawing"))
            return out

        def make_items(self, node, parent_path, expand):
            path = f"{parent_path}/{node['pn'] or node.get('group')}"
            first = QtGui.QStandardItem(node.get("group") or (self.rows.get(node["pn"]) or {}).get("display", ""))
            first.setData(node["pn"], PN_ROLE)
            first.setData(node.get("qty"), QTY_ROLE)
            first.setData(bool(node.get("context")), CONTEXT_ROLE)
            first.setData(node.get("group") or "", GROUP_ROLE)
            first.setData(path, PATH_ROLE)
            items = [first] + [QtGui.QStandardItem() for _c in COLUMNS[1:]]
            for item in items:
                item.setEditable(False)
            for child in node["children"]:
                first.appendRow(self.make_items(child, path, expand))
            if node["children"]:
                expand.append((first, path))
            return items

        def fold(self, index, folded):
            if self._filling or self.search.text().strip() or self.kind != "all":
                return
            path = index.siblingAtColumn(0).data(PATH_ROLE)
            if path:
                (self.collapsed.add if folded else self.collapsed.discard)(path)

        def fit_name_column(self):
            """The Part number column wide enough for the deepest, longest P/N-REV (within reason)."""
            fm = QtGui.QFontMetrics(self.title_font)
            widest = 0

            def walk(parent, depth):
                nonlocal widest
                for r in range(self.model.rowCount(parent)):
                    index = self.model.index(r, 0, parent)
                    row = self.rows.get(index.data(PN_ROLE)) or {}
                    text = index.data(GROUP_ROLE) or row.get("part_number", "") + (
                        "-" + row["revision"] if row.get("revision") else "")
                    widest = max(widest, (depth + 1) * self.list.indentation() + fm.horizontalAdvance(text) + 16)
                    if self.list.isExpanded(index):
                        walk(index, depth + 1)

            walk(QtCore.QModelIndex(), 0)
            self.list.setColumnWidth(0, max(200, min(460, widest)))

        def find(self, pn, reveal=True):
            """Select the first row of ``pn`` in the tree (``reveal``: opening its branch); False when it
            is not in the tree."""
            if not self.model.rowCount():
                return False
            found = self.model.match(self.model.index(0, 0), PN_ROLE, pn, 1,
                                     QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive)
            if not found:
                return False
            index = found[0]
            parent = index.parent()
            while reveal and parent.isValid():
                self.list.expand(parent)
                parent = parent.parent()
            self.list.setCurrentIndex(index)
            if reveal:
                self.list.scrollTo(index)
            return True

        def show_where_used(self, current):
            """Where used, in the tree: the other rows of the selected file and every assembly above
            any of them are tinted; the footer names the assemblies that use it directly."""
            same, uses = set(), set()
            row = self.row_for(current)
            if row is not None and self.model.rowCount():
                me = current.siblingAtColumn(0)
                for index in self.model.match(self.model.index(0, 0), PN_ROLE, row["part_number"], -1,
                                              QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive):
                    if index != me:
                        same.add(index.data(PATH_ROLE))
                    parent = index.parent()
                    while parent.isValid():
                        if not parent.data(GROUP_ROLE):
                            uses.add(parent.data(PATH_ROLE))
                        parent = parent.parent()
            self.list.same, self.list.uses = same, uses
            self.footer.setText(self.where_used_text(row))
            self.list.viewport().update()

        def where_used_text(self, row):
            if row is None or row.get("item_type") == "change":
                return FOOTER
            st, pn = self.structure, row["part_number"]

            def name(p):
                return (self.rows.get(p) or {}).get("display") or p

            if row.get("item_type") == "drawing":
                owner = next((o for o, dwgs in st["drawings"].items() if pn in dwgs), None)
                return f"{row['display']} · drawing of {name(owner)}" if owner else \
                    f"{row['display']} · drawing of a part that is not in the vault"
            parents = sorted((parent, q) for parent, lines in st["bom"].items() for child, q in lines if child == pn)
            if not parents:
                return f"{row['display']} · not used in any assembly"
            n = len(parents)
            listed = ", ".join(f"{name(parent)} ×{q:g}" if isinstance(q, float) else f"{name(parent)} ×{q}"
                               for parent, q in parents)
            return f"{row['display']} · used in {n} assembl{'y' if n == 1 else 'ies'}: {listed}"

        def tooltip_html(self, row):
            """The file name's tooltip: the file's picture (its saved thumbnail), P/N-REV, description
            and status."""
            lines = [f"<b>{_esc(row['display'])}</b>"]
            if row.get("description"):
                lines.append(_esc(row["description"]))
            status = STATUS_LABELS.get(row["code"], "")
            if status:
                lines.append(f'<span style="color:{self.list.colour("secondary").name()}">{_esc(status)}</span>')
            picture = ""
            path = self.thumb_file(row)
            if path:
                size = QtGui.QImageReader(path).size()
                width = min(TIP_WIDTH, size.width()) if size.isValid() and size.width() > 0 else TIP_WIDTH
                url = QtCore.QUrl.fromLocalFile(path).toString()
                picture = f'<img src="{url}" width="{width}"><br>'
            return f"<div>{picture}{'<br>'.join(lines)}</div>"

        def thumb_file(self, row):
            """The thumbnail saved in the latest version's file, as a PNG on disk (for the tooltip's
            rich text), or "" when it has none."""
            sha = row.get("sha")
            if not sha or row.get("item_type") == "change":
                return ""
            if sha not in self.thumb_files:
                path = ""
                try:
                    with zipfile.ZipFile(row["object"]) as archive:
                        data = archive.read("thumbnails/Thumbnail.png")
                    folder = os.path.join(tempfile.gettempdir(), "bruceworx-thumbnails")
                    os.makedirs(folder, exist_ok=True)
                    path = os.path.join(folder, sha + ".png")
                    if not os.path.exists(path):
                        with open(path + ".part", "wb") as out:
                            out.write(data)
                        os.replace(path + ".part", path)
                except (OSError, KeyError, zipfile.BadZipFile):
                    path = ""
                self.thumb_files[sha] = path
            return self.thumb_files[sha]

        # --- interaction
        def set_kind(self, key):
            self.kind = key
            self.fill()

        def toggle_sort(self):
            self.sort_by_time = not self.sort_by_time
            self.fill()

        def select(self, pn):
            if self.find(pn):
                return True
            # Not shown: clear the search and take the chip it lives under (ECR/ECO rows: Changes).
            row = self.rows.get(pn)
            want = "changes" if row is not None and row.get("item_type") == "change" else "all"
            if self.kind != want or self.search.text():
                self.chips[want].setChecked(True)
                self.kind = want
                self.search.clear()
                self.fill()
                return self.find(pn)
            return False

        def open_index(self, index):
            row = self.row_for(index)
            if row is not None:
                pn = row["part_number"]
                QtCore.QTimer.singleShot(0, lambda: run_for("BruceWorx_VaultOpen", pn))

        def context_menu(self, pos):
            index = self.list.indexAt(pos)
            row = self.row_for(index)
            if row is None:
                return
            self.list.setCurrentIndex(index)
            menu = build_item_menu(self, row["part_number"], in_panel=True)
            menu.exec(self.list.viewport().mapToGlobal(pos))

        def fill_more(self):
            self.more_menu.clear()
            try:  # the vault's dashboard is the Start page (PDM-10)
                import bruceworx_start

                act = self.more_menu.addAction("Dashboard")
                act.triggered.connect(lambda _c=False: bruceworx_start.show_dashboard())
                self.more_menu.addSeparator()
            except ImportError:
                pass
            entries = (("Vault Settings…", "BruceWorx_VaultSettings"),
                       ("Import Files into Vault…", "BruceWorx_ImportIntoVault"))
            for text, name in entries:
                act = self.more_menu.addAction(text)
                act.triggered.connect(lambda _c=False, n=name: FreeCADGui.runCommand(n))
            act = self.more_menu.addAction("Break Lock…")
            act.setEnabled(bool(_state["session"] and _state["session"].vault.locks))
            act.triggered.connect(lambda _c=False: break_lock())
            act = self.more_menu.addAction("Move Vault…")
            act.triggered.connect(lambda _c=False: move_vault_dialog())
            self.more_menu.addSeparator()
            act = self.more_menu.addAction("Show in Finder")
            s = _state["session"]
            act.setEnabled(s is not None)
            if s is not None:
                act.triggered.connect(lambda _c=False, r=s.root: QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(r)))

        def restyle_icons(self):
            colour = self.list.colour("secondary")
            dpr = self.devicePixelRatioF()
            self.cloud.setPixmap(icon_pixmap("cloud", colour, 18, dpr))
            self.refresh_button.setIcon(QtGui.QIcon(icon_pixmap("refresh", colour, 16, dpr)))
            self.more.setIcon(QtGui.QIcon(icon_pixmap("more", colour, 16, dpr)))
            self.search_action.setIcon(QtGui.QIcon(icon_pixmap("search", colour, 14, dpr)))
            self.sort.setIcon(QtGui.QIcon(icon_pixmap("sort", colour, 13, dpr)))

        def changeEvent(self, event):
            if event.type() in (QtCore.QEvent.StyleChange, QtCore.QEvent.PaletteChange):
                QtCore.QTimer.singleShot(0, lambda: _alive(self) and self.restyle_icons())
            super().changeEvent(event)

    # ----- Check In task panel ------------------------------------------------------------

    def box(title, icon=None):
        w = QtWidgets.QWidget()
        w.setWindowTitle(title)
        if icon is not None:
            w.setWindowIcon(icon)
        return w

    def hint(text):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        return lab

    class CheckInPanel:
        """Check In: a comment (required), the files that go in, keep checked out, close."""

        def __init__(self, s, pn):
            self.s = s
            self.pn = pn
            self.buttons = None
            self.released = False
            self.rows, self.outside = checkin_rows(s, pn)
            main = self.rows[0]
            icon = QtGui.QIcon(_svg_pixmap("checkin", _COMMAND_COLOURS["accent"], 16, 2.0))
            first = box("Check In", icon)
            lay = QtWidgets.QVBoxLayout(first)
            rev = s.status(pn).revision
            intro = QtWidgets.QLabel(
                f"{_html(main['description'])} <b>{_html(main['display'])}</b>" +
                (" and the parts it links that you changed" if len(self.rows) > 1 else "") +
                f". Each file gets a new version; the revision stays {_html(rev)} until a review.")
            intro.setObjectName("bwVaultIntro")
            intro.setWordWrap(True)
            lay.addWidget(intro)
            lay.addSpacing(4)
            lay.addWidget(QtWidgets.QLabel("Comment"))
            self.comment = QtWidgets.QPlainTextEdit()
            self.comment.setObjectName("bwVaultComment")
            self.comment.setPlaceholderText("What changed, and why")
            self.comment.setTabChangesFocus(True)
            self.comment.setFixedHeight(76)
            # Fixed height: with the default Expanding policy the box spreads its lines apart.
            self.comment.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.comment.textChanged.connect(self.update_ok)
            lay.addWidget(self.comment)
            self.comment_hint = hint("A comment is required.")
            lay.addWidget(self.comment_hint)
            files = box("Files")
            fl = QtWidgets.QVBoxLayout(files)
            self.tree = QtWidgets.QTreeWidget()
            self.tree.setObjectName("bwVaultFiles")
            self.tree.setColumnCount(2)
            self.tree.setHeaderLabels(["Item", "Version"])
            self.tree.setRootIsDecorated(False)
            self.tree.setUniformRowHeights(False)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.tree.setFocusPolicy(QtCore.Qt.NoFocus)
            mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            for row in self.rows:
                item = QtWidgets.QTreeWidgetItem([f"{row['description']}\n{row['display']}",
                                                  f"{row['version']}\n{row['note']}"])
                item.setData(0, QtCore.Qt.UserRole, row["part_number"])
                flags = QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
                if not row["enabled"]:
                    flags = QtCore.Qt.ItemIsUserCheckable
                item.setFlags(flags)
                item.setCheckState(0, QtCore.Qt.Checked if row["checked"] else QtCore.Qt.Unchecked)
                item.setFont(1, mono)
                kinds = {"assembly": "assembly", "drawing": "drawing"}
                item.setToolTip(0, f"{row['display']} ({kinds.get(row['item_type'], 'part')})")
                self.tree.addTopLevelItem(item)
            self.tree.header().setStretchLastSection(True)
            self.tree.setColumnWidth(0, 180)
            self.tree.itemChanged.connect(lambda *_a: self.update_ok())
            rows = max(1, min(len(self.rows), 6))
            self.tree.setFixedHeight(self.tree.header().sizeHint().height() + rows * 38 + 6)
            fl.addWidget(self.tree)
            self.summary = hint("")
            fl.addWidget(self.summary)
            self.form = [first, files]
            try:
                self.fixes = s.work.link_fixes([r["part_number"] for r in self.rows if r["enabled"]])
            except Exception as exc:  # e.g. not downloaded from iCloud: the check-in says so
                _console(f"BruceWorx vault: links not read: {exc}", "warn")
                self.fixes = []
            self.import_files = None
            if self.fixes:
                lines, imports = link_summary(self.fixes)
                links = box("Links")
                ll = QtWidgets.QVBoxLayout(links)
                for line in lines:
                    ll.addWidget(hint(line))
                if imports:
                    n = len(imports)
                    self.import_files = QtWidgets.QCheckBox(
                        "Import it into the vault with a new part number and link to it" if n == 1 else
                        f"Import these {n} files into the vault with new part numbers and link to them")
                    self.import_files.setObjectName("bwVaultImportLinks")
                    self.import_files.setChecked(True)
                    self.import_files.setToolTip(
                        "The originals are only read. Unticked, the links are checked in as written and the "
                        "assembly is incomplete on another Mac.")
                    ll.addWidget(self.import_files)
                self.form.append(links)
            options = box("Options")
            ol = QtWidgets.QVBoxLayout(options)
            self.keep = QtWidgets.QCheckBox("Keep checked out after check-in")
            self.close = QtWidgets.QCheckBox("Close the checked-in files")
            self.close.setChecked(True)
            ol.addWidget(self.keep)
            ol.addWidget(self.close)
            self.keep.toggled.connect(lambda on: self.close.setEnabled(not on))
            self.form.append(options)
            self.update_ok()
            QtCore.QTimer.singleShot(0, lambda: _alive(self.comment) and self.comment.setFocus())

        def chosen(self):
            out = []
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.checkState(0) == QtCore.Qt.Checked and self.rows[i]["enabled"]:
                    out.append(self.rows[i])
            return out

        def update_ok(self):
            if self.released:
                return
            n = len(self.chosen())
            ok = comment_ok(self.comment.toPlainText()) and n > 0
            self.comment_hint.setVisible(not comment_ok(self.comment.toPlainText()))
            total = len(self.rows)
            skipped = sum(1 for r in self.rows if not r["enabled"])
            self.summary.setText(f"{n} of {total} file{'s' if total != 1 else ''}" +
                                 (f" · {skipped} not checked out to you" if skipped else ""))
            button = self.ok_button()
            if button is not None:
                button.setEnabled(ok)
                button.setText(checkin_button_text(n))

        def ok_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Ok) if self.buttons is not None else None

        def modifyStandardButtons(self, buttons):
            self.buttons = buttons
            self.update_ok()

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True

        def release(self):
            _state["panel"] = None
            self.released = True
            return True

        def reject(self):
            return self.release()

        def accept(self):
            text = self.comment.toPlainText()
            rows = self.chosen()
            if not comment_ok(text) or not rows:
                self.update_ok()
                return False
            keep = self.keep.isChecked()
            close = self.close.isChecked() and not keep
            notes = {}
            importing = self.import_files is not None and self.import_files.isChecked()
            done, failed = run_checkin(self.s, rows, text.strip(), keep, import_outside=importing, notes=notes)
            if failed:
                _message("Check In", "Not checked in:\n" + "\n".join(f"{pn}: {why}" for pn, why in failed),
                         "warning")
            if notes.get("not_imported"):
                _message("Check In", "Not imported; the links to these files were checked in as written:\n" +
                         "\n".join(f"{os.path.basename(p)}: {why}" for p, why in notes["not_imported"]), "warning")
            if done:
                _console(f"BruceWorx vault: checked in {', '.join(done)}", "msg")
            try:
                import bruceworx_bom

                bom_text = bruceworx_bom.checkin_text(notes)
            except Exception:
                bom_text = ""
            if bom_text:
                _message("Check In", bom_text, "warning")
            paths = [self.s.work.path(pn) for pn in done]
            relinked = notes.get("relinked", [])
            self.release()
            QtCore.QTimer.singleShot(0, lambda: _after_checkin(paths, close, relinked))
            return True

    def _html(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    return {"VaultPanel": VaultPanel, "CheckInPanel": CheckInPanel, "VaultList": VaultList}


def _after_checkin(paths, close, relinked=()):
    import FreeCAD

    if close:
        docs = [d for d in (document_for(p) for p in paths) if d is not None]
        # Close assemblies before the parts they link.
        for doc in docs:
            try:
                FreeCAD.closeDocument(doc.Name)
            except Exception as exc:
                _console(f"BruceWorx vault: {exc}", "warn")
    # Open documents whose links were rewritten on disk: read them again, so a later save
    # does not write the old links back.
    for doc in [d for d in (document_for(p) for p in relinked) if d is not None]:
        _reload(doc)
    changed()
    _update_tabs()


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def build_item_menu(parent, pn, in_panel=False):
    """The vault right-click menu for pn (Vault panel), the same commands as the Browser's."""
    import FreeCADGui
    from PySide import QtGui, QtWidgets

    s = session()
    if s is not None and pn not in s.vault.model.items and (pn in s.vault.ecrs or pn in s.vault.ecos):
        import bruceworx_changes

        return bruceworx_changes.build_change_menu(parent, pn)
    menu = QtWidgets.QMenu(parent)
    if s is None:
        return menu
    st = s.status(pn)
    head = menu.addAction(f"VAULT  {st.display}")
    head.setEnabled(False)
    for name in CONTEXT_COMMANDS:
        if name is None:
            menu.addSeparator()
            continue
        if in_panel and name == "BruceWorx_VaultShowInPanel":
            continue
        entry = next(e for e in COMMANDS if e[0] == name)
        act = menu.addAction(FreeCADGui.getIcon(_icon_name(entry[3], entry[4])) or QtGui.QIcon(), entry[1])
        act.setToolTip(entry[2])
        act.setEnabled(available(name, pn))
        act.triggered.connect(lambda _c=False, n=name: run_for(n, pn))
    return menu



def register_panel(panel):
    """The Start page's Files tab holds the vault's list (the user's choice, 25 Oct: the vault is browsed
    on the dashboard only; the floating card over the window is gone)."""
    _state["panel"] = panel


def embedded_panel():
    panel = _state.get("panel")
    return panel if panel is not None and _alive(panel) else None


def _with_panel(fn, tries=20):
    """Run fn(panel) once the Files tab has made its list (the Start page builds it when first shown)."""
    from PySide import QtCore

    panel = embedded_panel()
    if panel is not None:
        fn(panel)
    elif tries:
        QtCore.QTimer.singleShot(100, lambda: _with_panel(fn, tries - 1))


def show_panel(on=True, focus_search=False):
    """Bring the Start page forward on its Files tab: the vault's files with their status."""
    if not on:
        return True
    if session() is None:
        _message("Vault", "Open a vault first (Start page \u2192 Open vault\u2026).")
        return False
    try:
        import bruceworx_start

        bruceworx_start.show_tab("files")
    except Exception as exc:  # noqa: BLE001
        _console(f"BruceWorx vault: the Files tab did not open: {exc}", "warn")
        return False
    if focus_search:
        _with_panel(lambda panel: panel.search.setFocus())
    return True


def show_in_panel(pn=None):
    pn = pn or current_target()
    if not pn or not show_panel(True):
        return False
    _with_panel(lambda panel: panel.select(pn))
    return True


def show_filtered(kind="all", query="", select=None):
    """The Files tab on a filter chip and search words (``type:part state:wip``), selecting ``select``
    (a P/N or change number). The dashboard's tiles and rows use it."""
    from PySide import QtCore

    if not show_panel(True):
        return False

    def apply(panel):
        k = kind if kind in panel.chips else "all"
        panel.chips[k].setChecked(True)
        panel.kind = k
        panel.search.blockSignals(True)
        panel.search.setText(query or "")
        panel.search.blockSignals(False)
        panel.fill()
        if select:
            QtCore.QTimer.singleShot(150, lambda: _alive(panel) and panel.select(select))

    _with_panel(apply)
    return True


def _sync_panel_action():
    """Nothing to keep in step any more: the command opens the Files tab (kept for the listener list)."""


# ----- Browser badges and the status bar -----------------------------------------------------

def browser_extra(obj, meta, marks):
    """bruceworx_browser.ROW_EXTRAS: the vault's revision, state and lock for vault files."""
    s = _state["session"]
    if s is None or not enabled():
        return meta, marks
    try:
        import bruceworx_browser as browser
    except ImportError:
        return meta, marks
    own = browser.part_values(obj)
    target = obj if own else browser.linked_part(obj)
    doc = getattr(target, "Document", None) if target is not None else None
    if doc is None or browser.part_values(target) is None:
        return meta, marks  # nothing to look up: a link whose part has gone, or not a document object
    pn = pn_for_document(doc, s)
    if not pn:
        return meta, marks
    st = s.status(pn)
    extra = browser_marks(st.code, st.lock, s.me)
    if own:
        # The P/N as quiet text (dropped first when the row is narrow), then the vault's state
        # and revision chip and the lock, which stay.
        held = [m for m in marks if m[0].endswith(" held")]
        return meta or pn, browser.part_badges("", st.revision, st.state) + extra + held
    return browser.component_meta(pn, st.revision), list(marks) + extra


def _status_suffix():
    s = _state["session"]
    if s is None:
        return ""
    mine, newer = s.counts()
    return status_suffix(mine, newer)


def _update_browser():
    try:
        import bruceworx_browser

        bruceworx_browser.apply_rows()
    except Exception:
        pass


def _update_statusbar():
    try:
        import bruceworx_statusbar
        from PySide import QtCore, QtWidgets

        bruceworx_statusbar.update()
        label = _window().statusBar().findChild(QtWidgets.QLabel, "vaultReadout")
        if label is not None and not label.property("bwVaultClick"):
            label.setProperty("bwVaultClick", True)
            label.setCursor(QtCore.Qt.PointingHandCursor)
            label.installEventFilter(_qt["click"])
        if label is not None:
            label.setToolTip((label.toolTip() or "") .split("\n")[0] + "\nClick to show the Vault panel.")
    except Exception as exc:
        _console(f"BruceWorx vault status bar: {exc}")


# ----- NSFileManager from ctypes: the Trash and iCloud downloads ------------------------------------

def _file_manager():
    """(msg, NSFileManager.defaultManager, file_url(path)) through the Objective-C runtime."""
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    send = objc.objc_msgSend

    def msg(obj, sel, *args, argtypes=(), restype=ctypes.c_void_p):
        send.restype = restype
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + list(argtypes)
        return send(obj, objc.sel_registerName(sel), *args)

    def file_url(path):
        text = msg(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", os.fsencode(path),
                   argtypes=[ctypes.c_char_p])
        return msg(objc.objc_getClass(b"NSURL"), b"fileURLWithPath:", text, argtypes=[ctypes.c_void_p])

    return msg, msg(objc.objc_getClass(b"NSFileManager"), b"defaultManager"), file_url


def trash(path):
    """Move path to the Trash (NSFileManager trashItemAtURL); True when it went."""
    import ctypes

    try:
        msg, manager, file_url = _file_manager()
        ok = msg(manager, b"trashItemAtURL:resultingItemURL:error:", file_url(path), None, None,
                 argtypes=[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], restype=ctypes.c_bool)
        return bool(ok) and not os.path.exists(path)
    except Exception as exc:
        _console(f"BruceWorx vault: Trash failed: {exc}", "warn")
        return False


DOWNLOAD_STALL = 120  # seconds without a finished file before a download is given up


def request_download(path):
    """Ask iCloud to download path (NSFileManager startDownloadingUbiquitousItemAtURL:error:).

    True when iCloud took the request. For a ``.<name>.icloud`` stand-in the URL is the real
    name. False for a file outside iCloud (another sync service) or when the call fails.
    """
    import ctypes

    try:
        msg, manager, file_url = _file_manager()
        return bool(msg(manager, b"startDownloadingUbiquitousItemAtURL:error:", file_url(path), None,
                        argtypes=[ctypes.c_void_p, ctypes.c_void_p], restype=ctypes.c_bool))
    except Exception as exc:
        _console(f"BruceWorx vault: iCloud download request failed: {exc}", "warn")
        return False


def _read_first_byte(path):
    """Reading a dataless file makes the file provider download it (Dropbox, OneDrive, ...)."""
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError:
        pass


def duration_text(seconds):
    """120 -> "2 minutes", 60 -> "1 minute", 45 -> "45 seconds"."""
    seconds = int(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute{'s' if n != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def download_text(total, size):
    """The progress dialog's text."""
    return f"Downloading {total} file{'s' if total != 1 else ''} ({size_text(size)}) from iCloud\u2026"


def ensure_downloaded(paths, title="iCloud", stall=None):
    """Download files iCloud keeps only in the cloud, with a progress dialog; the workspace's ensure_local.

    Gives up when no file finished for ``stall`` seconds (DOWNLOAD_STALL) or on Cancel, and
    raises NotDownloadedError with the reason; the operation then stops with that message.
    """
    import threading
    import time

    from bruceworx import pdm_workspace as ws

    pending = ws.pending_downloads(paths)
    if not pending:
        return
    stall = DOWNLOAD_STALL if stall is None else stall
    for path in pending:
        if not request_download(path) and os.path.lexists(path):
            threading.Thread(target=_read_first_byte, args=(path,), daemon=True).start()
    total, size = len(pending), sum(ws.cloud_size(p) for p in pending)
    _console(f"BruceWorx vault: downloading {total} file(s) from iCloud: "
             + ", ".join(os.path.basename(p) for p in pending), "msg")
    dialog = None
    try:
        from PySide import QtCore, QtWidgets

        if QtWidgets.QApplication.instance() is not None:
            dialog = QtWidgets.QProgressDialog(download_text(total, size), "Cancel", 0, total, _window())
            bar = dialog.findChild(QtWidgets.QProgressBar)
            if bar is not None:
                bar.setFormat("%v of %m")
            dialog.setWindowTitle(title)
            dialog.setWindowModality(QtCore.Qt.WindowModal)
            dialog.setMinimumDuration(300)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
    except Exception:
        dialog = None
    left, last, deadline, stopped = pending, total, time.monotonic() + stall, False
    try:
        while True:
            left = ws.pending_downloads(left)
            if len(left) < last:
                last, deadline = len(left), time.monotonic() + stall
            if dialog is not None:
                dialog.setValue(total - len(left))
            if not left:
                return
            if dialog is not None and dialog.wasCanceled():
                stopped = True
                break
            if time.monotonic() > deadline:
                break
            # Sleep and pump events rather than wait on a timer: macOS App Nap may hold timers
            # back for minutes while FreeCAD is in the background.
            time.sleep(0.15)
            if dialog is not None:
                QtWidgets.QApplication.processEvents()
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
    names = ", ".join(os.path.basename(p) for p in left[:3]) + (" ..." if len(left) > 3 else "")
    if stopped:
        text = f"The download from iCloud was stopped; {len(left)} file(s) are not on this Mac yet: {names}."
    else:
        text = (f"{len(left)} file(s) did not download from iCloud: {names}. Nothing arrived for "
                f"{duration_text(stall)}. Check that this Mac is online and signed in to iCloud, then try again.")
    raise ws.NotDownloadedError(left, text)


# ----- Move Vault ----------------------------------------------------------------------------------


def move_vault_dialog(root=None, destination=None, answers=None):
    """Move the open vault to another folder: plan, close its files, copy with progress, switch, reopen.

    destination and answers ({"save": bool, "trash": bool}) let a script run it without dialogs.
    """
    import threading

    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    from bruceworx import vault_move

    answers = answers or {}
    s = session()
    root = root or (s.root if s is not None else "")
    if not root:
        _message("Move Vault", "Open a vault first.")
        return False
    mw = _window()
    if destination is None:
        destination = QtWidgets.QFileDialog.getExistingDirectory(
            mw, "Move Vault: choose the new place (the vault folder goes inside it)",
            os.path.dirname(root.rstrip(os.sep)))
    if not destination:
        return False
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        plan = vault_move.plan_move(root, destination)
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()
    issues = "\n".join(("• " if level == "error" else "– ") + text for level, text in plan.issues)
    if not plan.ok:
        _message("Move Vault", f"The vault cannot move to {plan.target}:\n\n{issues}", "warning")
        return False
    name = s.name if s is not None else os.path.basename(root)
    docs = [d for d in _open_documents() if d.FileName and inside(d.FileName, root)]
    text = (f"Move {name} ({plan.files} files, {size_text(plan.size)}) to\n{plan.target}?\n\n"
            "Everything is copied and checked first; the old folder stays as it is until you choose "
            "to put it in the Trash." + (f"\n\n{issues}" if issues else "") +
            (f"\n\n{len(docs)} open file{'s' if len(docs) != 1 else ''} from the vault close and open "
             "again from the new place." if docs else ""))
    if "go" not in answers and not _ask("Move Vault", text, "Move", "Cancel"):
        return False
    # Close the vault's documents (save first if asked).
    modified = [d for d in docs if _gui_modified(d)]
    if modified:
        save = answers.get("save")
        if save is None:
            box = QtWidgets.QMessageBox(mw)
            box.setWindowTitle("Move Vault")
            box.setText("Save the changes to " + ", ".join(d.Label for d in modified) + " before the move?")
            yes = box.addButton("Save", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Don't Save", QtWidgets.QMessageBox.DestructiveRole)
            cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
            box.setDefaultButton(yes)
            box.exec()
            if box.clickedButton() is cancel:
                return False
            save = box.clickedButton() is yes
        if save:
            for d in modified:
                try:
                    d.save()
                except Exception as exc:
                    _message("Move Vault", f"{d.Label} was not saved ({exc}). Nothing was moved.", "warning")
                    return False
    reopen = [(d.FileName, d is FreeCAD.ActiveDocument) for d in docs]
    for d in docs:
        _set_unmodified(d)
    for d in docs:
        try:
            FreeCAD.closeDocument(d.Name)
        except Exception:
            pass
    # Copy on a worker thread (file copying only); progress comes back by a queued signal.
    progress = QtWidgets.QProgressDialog(f"Moving {name}…", "Stop", 0, 1000, mw)
    progress.setWindowTitle("Move Vault")
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    class Bridge(QtCore.QObject):
        step = QtCore.Signal(int, str)
        done = QtCore.Signal(object, object)

    bridge = Bridge()
    result = {}
    loop = QtCore.QEventLoop()
    stop = {"flag": False}

    def on_step(value, rel):
        if progress.wasCanceled():
            stop["flag"] = True
        progress.setValue(value)
        progress.setLabelText(f"Moving {name}…\n{rel}")

    def on_done(new_root, error):
        result["root"], result["error"] = new_root, error
        loop.quit()

    bridge.step.connect(on_step, QtCore.Qt.QueuedConnection)
    bridge.done.connect(on_done, QtCore.Qt.QueuedConnection)

    def report(done, total, rel):
        bridge.step.emit(int(1000 * done / max(total, 1)), rel)
        return not stop["flag"]

    def work():
        try:
            bridge.done.emit(vault_move.move_vault(plan, progress=report), None)
        except BaseException as exc:  # noqa: BLE001 -- reported in the GUI
            bridge.done.emit(None, exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    loop.exec()
    thread.join(5)
    progress.close()
    new_root = result.get("root")
    if not new_root:
        error = result.get("error")
        what = "The move was stopped" if isinstance(error, InterruptedError) else f"The move failed: {error}"
        _message("Move Vault", f"{what}. The vault is still at {root}; nothing there changed.", "warning")
        for path, _active in reopen:
            _load(path)
        return False
    # Switch FreeCAD to the new place, matching how VaultPath was stored (folder or ref file).
    old_value = _prefs().GetString("VaultPath", "").strip()
    new_value = vault_move.ref_in(new_root) if os.path.isfile(os.path.expanduser(old_value)) else new_root
    if old_value and inside(os.path.expanduser(old_value), root) or not old_value:
        _prefs().SetString("VaultPath", new_value)
    changed(refresh=True)
    active = None
    for path, was_active in reopen:
        new_path = moved_path(path, root, new_root)
        if new_path and os.path.exists(new_path):
            _load(new_path)
            if was_active:
                active = new_path
    if active:
        _load(active)
    trash_it = answers.get("trash")
    if trash_it is None:
        trash_it = _ask("Move Vault", f"The vault is now at\n{new_root}\n\nMove the old copy at\n{root}\n"
                        "to the Trash?", "Move to Trash", "Keep It", default_yes=False)
    trashed = trash(root) if trash_it else False
    _message("Move Vault", f"{name} moved to {new_root}." +
             (" The old copy is in the Trash." if trashed else
              (" The old copy could not be put in the Trash; it is still at " + root + "." if trash_it else
               " The old copy is still at " + root + ".")))
    try:
        import bruceworx_start

        bruceworx_start._refresh_pages()
    except Exception:
        pass
    FreeCADGui.updateGui()
    return new_root


# ----- install ---------------------------------------------------------------------------------

def _register_icons():
    import FreeCADGui

    pairs = {(e[3], e[4]) for e in COMMANDS} | {("panel", "secondary")}
    for kind, role in pairs:
        FreeCADGui.addIcon(_icon_name(kind, role), svg_icon(kind, _COMMAND_COLOURS[role]).encode("utf-8"), "SVG")


def _install_menu():
    import FreeCADGui
    from PySide import QtGui, QtWidgets

    import bruceworx_menu

    window = _window()
    menu = bruceworx_menu.get_menu()
    if menu is None:
        return
    sub = window.findChild(QtWidgets.QMenu, "BruceWorxVaultMenu")
    if sub is None:
        sub = QtWidgets.QMenu("Vault", window)
        sub.setObjectName("BruceWorxVaultMenu")
        names = [PANEL_COMMAND, None] + list(CONTEXT_COMMANDS[:-2]) + [None, "BruceWorx_VaultBreakLock"]
        actions = []
        for name in names:
            if name is None:
                sub.addSeparator()
                continue
            if name == PANEL_COMMAND:
                text, icon = "Vault Panel", _icon_name("panel", "secondary")
            else:
                entry = next(e for e in COMMANDS if e[0] == name)
                text, icon = entry[1], _icon_name(entry[3], entry[4])
            act = sub.addAction(FreeCADGui.getIcon(icon) or QtGui.QIcon(), text)
            act.triggered.connect(lambda _c=False, n=name: FreeCADGui.runCommand(n))
            actions.append((name, act))

        def update():
            for name, act in actions:
                act.setEnabled(session() is not None if name == PANEL_COMMAND else available(name))

        sub.aboutToShow.connect(update)
    bruceworx_menu.add_action(sub.menuAction(), "BruceWorxMenu_Vault")


class _PrefObserver:
    def onChange(self, _group, name):
        if name in ("VaultPath", "VaultClient"):
            timer = _qt.get("prefs")
            if timer is not None:
                timer.start()


def _vault_path_changed():
    _state["session_path"] = None  # session() opens the new one
    session()
    changed()


def _shutdown():
    for key in ("journal", "notify", "prefs", "tabs"):
        timer = _qt.get(key)
        if timer is not None:
            timer.stop()
    _state["listeners"].clear()
    if _backups is not None and _backups.active:
        _backups.restore()


def install():
    """Commands, menus, the document observer, the watcher, Browser and status-bar hooks."""
    if _state["installed"]:
        return
    _state["installed"] = True
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    _register_icons()
    for entry in COMMANDS:
        FreeCADGui.addCommand(entry[0], _Command(*entry))
    FreeCADGui.addCommand(PANEL_COMMAND, _PanelCommand())

    def timer(key, ms, slot):
        t = QtCore.QTimer()
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(slot)
        _qt[key] = t

    timer("journal", 400, _journal_settled)
    timer("notify", 60, _notify)
    timer("prefs", 100, _vault_path_changed)
    timer("tabs", 150, _update_tabs)
    watcher = QtCore.QFileSystemWatcher()
    watcher.directoryChanged.connect(_journal_changed)
    watcher.fileChanged.connect(_journal_changed)
    _qt["watcher"] = watcher

    class _Click(QtCore.QObject):
        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                QtCore.QTimer.singleShot(0, lambda: show_panel(True))
                return True
            return False

    _qt["click"] = _Click()
    _qt["observer"] = _DocumentObserver()
    FreeCAD.addDocumentObserver(_qt["observer"])
    _qt["prefs_observer"] = _PrefObserver()
    _qt["prefs_group"] = FreeCAD.ParamGet(PREFS_PATH)  # keep the handle, see BruceWorx.install
    _qt["prefs_group"].Attach(_qt["prefs_observer"])
    _qt["manipulator"] = _Manipulator()
    FreeCADGui.addWorkbenchManipulator(_qt["manipulator"])
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)

    add_listener(_update_browser)
    add_listener(_update_statusbar)
    add_listener(_update_tabs)
    add_listener(_sync_panel_action)
    try:
        import bruceworx_browser

        if browser_extra not in bruceworx_browser.ROW_EXTRAS:
            bruceworx_browser.ROW_EXTRAS.append(browser_extra)
    except Exception as exc:
        _console(f"BruceWorx vault: no Browser badges: {exc}")
    try:
        import bruceworx_statusbar

        bruceworx_statusbar.VAULT_SUFFIX.append(_status_suffix)
    except Exception as exc:
        _console(f"BruceWorx vault: no status-bar counts: {exc}")

    def late():
        try:
            _install_menu()
        except Exception as exc:
            _console(f"BruceWorx vault: menu not added: {exc}", "warn")
        session()
        changed()
        _prefs().RemBool("VaultPanelVisible")  # the floating card it brought back is gone

    QtCore.QTimer.singleShot(0, late)
