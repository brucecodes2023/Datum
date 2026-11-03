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
  filters; more columns, such as file size, from Columns… or the header) on the Start page's Files tab: the vault is browsed on the dashboard only
  (the user's choice, 25 Oct; the floating card over the window was see-through).
* Browser badges (bruceworx_browser.ROW_EXTRAS) and the status-bar readout
  ("Vault <name> · 2 checked out · 1 newer"; a click opens the Files tab).
* Move Vault and Break Lock, started from Vault Settings (bruceworx_vaultwizard).

The pure helpers at the top are tested in TestBruceWorxVaultClient.py.
"""

from __future__ import annotations

import datetime as _dt
import json
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
           ("simulations", "Simulations"), ("checked_out", "Checked out"), ("changes", "Changes"))  # changes: ECRs and ECOs (bruceworx_changes)
FILTER_TYPES = {"parts": ("part", "cots"), "assemblies": ("assembly",), "drawings": ("drawing",),
                "simulations": ("simulation",)}
SIM_COLUMNS = ("Simulation", "Simulation of", "Study", "Result", "Geometry", "Status")  # the Simulations chip (PDM-14)


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
    if ext.lower() not in (SUFFIX.lower(), ".vlsim") or not stem:  # .vlsim: a simulation item (PDM-14)
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
                                                         "drawings": "drawing", "bought": "cots",
                                                         "simulations": "simulation"}.get(value, value)
    if key == "lock":
        if value == "none":
            return row.get("code") not in QUERY_LOCKS["any"]
        return row.get("code") in QUERY_LOCKS.get(value, ())
    if key == "status":
        return row.get("code") == value
    if key == "change":
        return row.get("item_type") == "change" and value in (str(row.get("state", "")).lower(),
                                                              str(row.get("kind", "")).lower())
    if key == "program":
        from bruceworx import vault_trees

        return vault_trees.program_code(row.get("part_number")) == value.upper()
    return None


UNCATEGORIZED = "Uncategorized"
LOOSE = "Not in an assembly"  # vault_trees.LOOSE: the group of parts no assembly uses
CATEGORY_PREF = "VaultCategories"  # JSON in Preferences/BruceWorx: {vault root: {"hidden": [...], "collapsed": [...]}}


def category_prefs(root, group=None):
    """(hidden, collapsed): the category names hidden and the category groups folded in this vault
    (by its folder), as remembered in the Vector Labs preferences. "" stands for Uncategorized."""
    try:
        group = group if group is not None else _prefs()
        saved = json.loads(group.GetString(CATEGORY_PREF, "{}") or "{}").get(str(root), {})
        return set(saved.get("hidden", [])), set(saved.get("collapsed", []))
    except Exception:  # noqa: BLE001 -- no preferences or a damaged value: nothing hidden
        return set(), set()


def save_category_prefs(root, hidden, collapsed, group=None):
    try:
        group = group if group is not None else _prefs()
        data = json.loads(group.GetString(CATEGORY_PREF, "{}") or "{}")
        if hidden or collapsed:
            data[str(root)] = {"hidden": sorted(hidden), "collapsed": sorted(collapsed)}
        else:
            data.pop(str(root), None)
        group.SetString(CATEGORY_PREF, json.dumps(data, sort_keys=True))
    except Exception as exc:  # noqa: BLE001 -- a preference that cannot be kept is not worth an error
        _console(f"Vault: category choices not remembered ({exc})", "log")


def category_counts(rows):
    """{category: items} over the file rows ("" = uncategorized); change rows (ECR/ECO) are not items."""
    counts = {}
    for r in rows.values():
        if r.get("item_type") != "change":
            counts[r.get("category") or ""] = counts.get(r.get("category") or "", 0) + 1
    return counts


def group_by_category(nodes, rows, hidden=(), show_hidden=False):
    """The tree's top level cut into one group node per category: named groups A-Z, then
    Uncategorized. A top-level group node ("Not in an assembly", "Other files") is opened up: its
    categorized items join their category's group. Uncategorized parts no assembly uses sit in
    Uncategorized level with the assemblies (30 Oct); what is left of any other group stays in it.
    Groups in ``hidden`` are left out unless ``show_hidden`` (a search), in which case they are kept
    and marked. When no item has a category the nodes come back as they were.
    Returns (nodes, number of items hidden)."""
    def category(node):
        return (rows.get(node["pn"]) or {}).get("category") or ""

    flat = [c for n in nodes for c in (n["children"] if n["pn"] is None else [n])]
    if not any(category(n) for n in flat):
        return nodes, 0
    buckets, rest = {}, []
    for node in nodes:
        if node["pn"] is not None:
            buckets.setdefault(category(node), []).append(node)
            continue
        left = []
        loose = node.get("group") == LOOSE
        for child in node["children"]:
            if category(child) or loose:
                buckets.setdefault(category(child), []).append(child)
            else:
                left.append(child)
        if left:
            rest.append(dict(node, children=left))
    members = {name: list(items) for name, items in buckets.items()}
    members[""] = members.get("", []) + rest
    out, hidden_items = [], 0
    for name in sorted(members, key=lambda c: (c == "", c.casefold())):
        items = members[name]
        count = sum(len(n["children"]) if n["pn"] is None else 1 for n in items)
        if not count:
            continue
        is_hidden = name in hidden
        if is_hidden and not show_hidden:
            hidden_items += count
            continue
        label = name or UNCATEGORIZED
        out.append({"pn": None, "group": f"{label}  \u00b7  {count}" + ("  \u00b7  hidden" if is_hidden else ""),
                    "key": f"cat:{name}", "category": name, "qty": None, "children": items})
    return out, hidden_items


TYPE_GROUPS = (("assembly", "Assemblies", ("assembly",)), ("part", "Parts", ("part", "cots")),
               ("drawing", "Drawings", ("drawing",)), ("simulation", "Simulations", ("simulation",)))


def group_by_type(nodes, rows, prefix=""):
    """Item nodes cut into Assemblies, Parts and Drawings groups, in that order (PDM-13: the flat file
    list, one row per file). Nodes of any other kind (ECR/ECO rows) stay after the groups. ``prefix``
    keeps the groups' keys apart inside a category group."""
    def kind(node):
        return (rows.get(node["pn"]) or {}).get("item_type") or ""

    out, placed = [], set()
    for key, label, kinds in TYPE_GROUPS:
        members = [n for n in nodes if n["pn"] is not None and kind(n) in kinds]
        placed.update(id(n) for n in members)
        if members:
            out.append({"pn": None, "group": f"{label}  \u00b7  {len(members)}", "key": f"{prefix}type:{key}",
                        "kind": key, "qty": None, "children": members})
    return out + [n for n in nodes if id(n) not in placed]


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
    text = " ".join(str(row.get(k, "")) for k in ("part_number", "display", "description", "former")).lower()
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


# ----- the Files table's columns (PDM-13: Bruce, 2 Nov: "I should also be able to see file size") ------

# Every column the Files table can show, in the order Columns… offers them: (key, header, width, align).
# All of them come from the vault's own records (items, versions, locks), never from the files, so a
# vault of hundreds of items still lists at once. Part number and Description are always shown.
DESCRIPTION_MIN = 220  # px: the Description column never shrinks below this (the table scrolls instead)
FILE_COLUMNS = (
    ("part_number", "Part number", 300, "left"),
    ("description", "Description", 0, "left"),  # 0: stretches to fill the row
    ("used_in", "Used in", 60, "right"),
    ("drawing", "Drawing", 78, "left"),
    ("state", "State", 84, "left"),
    ("status", "Status", 170, "left"),
    ("revision", "Revision", 70, "left"),
    ("type", "Type", 80, "left"),
    ("category", "Category", 110, "left"),
    ("size", "File size", 76, "right"),
    ("modified", "Modified", 130, "left"),
    ("modified_by", "Modified by", 96, "left"),
    ("checked_out_by", "Checked out by", 110, "left"),
    ("versions", "Versions", 66, "right"),
    ("comment", "Last comment", 220, "left"),
    ("program", "Program", 70, "left"),
)
COLUMN_TITLES = {key: title for key, title, _w, _a in FILE_COLUMNS}
DEFAULT_COLUMNS = ("part_number", "description", "used_in", "drawing", "state", "status")
REQUIRED_COLUMNS = ("part_number", "description")
COLUMNS_PREF = "VaultColumns"  # Preferences/BruceWorx: the Files table's column keys, comma-separated, in order
TYPE_NAMES = {"part": "Part", "assembly": "Assembly", "drawing": "Drawing", "cots": "COTS",
              "simulation": "Simulation"}


def parse_columns(text):
    """The Files table's column keys from the preference (comma-separated): known keys, each once, in
    the saved order, Part number first and Description always in. Nothing saved gives the six of old."""
    keys = []
    for word in str(text or "").split(","):
        key = word.strip()
        if key in COLUMN_TITLES and key not in keys:
            keys.append(key)
    if not keys:
        return DEFAULT_COLUMNS
    keys = [k for k in keys if k != "part_number"]
    if "description" not in keys:
        keys.insert(0, "description")
    return ("part_number",) + tuple(keys)


def serialize_columns(keys):
    return ",".join(parse_columns(",".join(keys)))


def column_prefs(group=None):
    """The chosen column keys (Preferences/BruceWorx/VaultColumns)."""
    try:
        group = group if group is not None else _prefs()
        return parse_columns(group.GetString(COLUMNS_PREF, ""))
    except Exception:  # noqa: BLE001 -- no preferences: the default columns
        return DEFAULT_COLUMNS


def save_column_prefs(keys, group=None):
    """Keep the choice; the default is kept as "" (nothing chosen), so it follows a later default."""
    keys = parse_columns(",".join(keys))
    try:
        group = group if group is not None else _prefs()
        group.SetString(COLUMNS_PREF, "" if keys == DEFAULT_COLUMNS else serialize_columns(keys))
    except Exception as exc:  # noqa: BLE001 -- a preference that cannot be kept is not worth an error
        _console(f"Vault: column choice not remembered ({exc})", "log")
    return keys


def move_column(order, key, step):
    """order with key moved one place up (step -1) or down (+1); Part number stays first."""
    order = list(order)
    if key not in order or key == "part_number":
        return order
    i = order.index(key)
    j = i + step
    if j < 0 or j >= len(order) or order[j] == "part_number":
        return order
    order[i], order[j] = order[j], order[i]
    return order


def date_text(text, now=None):
    """ "Today, 14:05", "Yesterday, 09:12", "14 Oct 2026, 09:12" (local time): a column of dates."""
    when = parse_time(text) if not isinstance(text, _dt.datetime) else text
    if when is None:
        return ""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    local, today = when.astimezone(), now.astimezone().date()
    clock = local.strftime("%H:%M")
    if local.date() == today:
        return f"Today, {clock}"
    if (today - local.date()).days == 1:
        return f"Yesterday, {clock}"
    return f"{local.day} {local.strftime('%b')} {local.year}, {clock}"


def column_cell(key, row, me="", now=None):
    """(text, sort value) of one Files-table cell from its row (the dict VaultPanel.refresh builds).
    The sort value is a number for sizes, dates and counts, so they sort as numbers; None when the
    cell is empty (sorted last). Used in and Drawing need the vault's structure: the panel fills them."""
    def text(value):
        value = str(value or "")
        return value, (value.casefold() or None)

    if key == "part_number":
        return text(row.get("display") or row.get("part_number"))
    if key == "description":
        return text(row.get("description"))
    if key == "state":
        return text(row.get("state"))
    if key == "status":
        return text(row.get("status_text"))
    if key == "revision":
        return text(row.get("revision"))
    if key == "type":
        kind = row.get("item_type") or ""
        return text(row.get("kind") if kind == "change" else TYPE_NAMES.get(kind, kind.title()))
    if key == "category":
        return text(row.get("category"))
    if key == "program":
        return text(row.get("program"))
    if key == "modified_by":
        return text(actor_name(row.get("actor"), me))
    if key == "checked_out_by":
        return text(actor_name(row.get("lock"), me))
    if key == "comment":
        lines = str(row.get("comment") or "").strip().splitlines()
        return text(lines[0] if lines else "")
    if key == "size":
        size = row.get("size")
        return (size_text(size), size) if isinstance(size, int) and not isinstance(size, bool) else ("", None)
    if key == "modified":
        when = parse_time(row.get("at")) if row.get("at") else None
        return (date_text(when, now), when.timestamp()) if when else ("", None)
    if key == "versions":
        count = row.get("versions")
        return (str(count), count) if isinstance(count, int) and row.get("item_type") != "change" else ("", None)
    return "", None


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
    # The file types (PDM-13): a part is one block, an assembly a stack of blocks, a drawing a sheet.
    "part": '<path d="M12 2.7l8.4 4.8v9L12 21.3l-8.4-4.8v-9z" fill="{c}" fill-opacity=".16" stroke="{c}" '
            'stroke-width="1.8" stroke-linejoin="round"/><path d="M3.9 7.7L12 12.3l8.1-4.6M12 12.3V21" fill="none" '
            'stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>',
    "assembly": '<path d="M7.5 2.4l5.1 2.9v5.7L7.5 13.9 2.4 11V5.3z" fill="{c}" fill-opacity=".16" stroke="{c}" '
                'stroke-width="1.6" stroke-linejoin="round"/><path d="M16.5 2.4l5.1 2.9v5.7l-5.1 2.9-5.1-2.9" '
                'fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/><path d="M12 10.2l5.1 2.9v5.7'
                'L12 21.6l-5.1-2.9v-5.7z" fill="{c}" fill-opacity=".34" stroke="{c}" stroke-width="1.6" '
                'stroke-linejoin="round"/>',
    # PDM-14: a simulation is a block with a stress curve through it.
    "simulation": '<path d="M12 2.7l8.4 4.8v9L12 21.3l-8.4-4.8v-9z" fill="{c}" fill-opacity=".12" stroke="{c}" '
                  'stroke-width="1.6" stroke-linejoin="round"/><path d="M6.5 15.5c2-1 2.5-6.5 5.5-6.5s3.5 5.5 5.5 6.5" '
                  'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
    "drawing": '<path d="M4.5 2.7h10.5l4.5 4.5v14.1h-15z" fill="{c}" fill-opacity=".12" stroke="{c}" '
               'stroke-width="1.7" stroke-linejoin="round"/><path d="M15 2.7v4.5h4.5M7.8 16.5h8.4M12.9 13.5v6.3'
               'M7.8 9.5h4.8" fill="none" stroke="{c}" stroke-width="1.6"/>',
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


def neighbour_places(root):
    """(name, folder) for this vault and the others beside it, or []."""
    if not str(root or "").strip():
        return []
    try:
        from bruceworx import vault_setup

        return vault_setup.neighbour_vaults(root)
    except Exception:
        return []


def other_places(root):
    """(name, folder) for the vaults beside this one, not including it."""
    current = os.path.abspath(root) if str(root or "").strip() else ""
    if not current:
        return []
    return [(name, os.path.abspath(folder)) for name, folder in neighbour_places(root)
            if os.path.abspath(folder) != current]


def use_place(folder):
    """Open another vault beside the current one (Projects or Standard Libraries)."""
    folder = os.path.abspath(folder)
    current = os.path.abspath(_prefs().GetString("VaultPath", "").strip() or folder)
    if folder != current:
        _prefs().SetString("VaultPath", folder)


def add_place_actions(menu, root):
    """Checkable entries for the vaults beside this one. Returns how many were added."""
    places = neighbour_places(root)
    if len(places) < 2:
        return 0
    current = os.path.abspath(root)
    for name, folder in places:
        act = menu.addAction(name)
        act.setCheckable(True)
        act.setChecked(os.path.abspath(folder) == current)
        act.triggered.connect(lambda _c=False, f=folder: use_place(f))
    return len(places)


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


def affected_documents(s, plan):
    """The open documents Renumber would replace (PDM-12): the work copies of the item and of every file
    linking it, as ``[(P/N, document)]``."""
    out = []
    for pn in [plan["old"]] + list(plan["linkers"]):
        doc = document_for(s.work.path(pn))
        if doc is not None:
            out.append((pn, doc))
    return out


def renumber_summary(plan, documents=()):
    """The text under the Renumber dialog's fields: what will be rewritten, who else uses the number,
    and what stops it."""
    old, new = plan["old"], plan["new"] or "the new number"
    lines = [f"{old}'s file is stored as {new}.FCStd with its PartNumber written; the revision, state and "
             "geometry stay as they are."]
    if plan["linkers"]:
        lines.append(f"Links are re-pointed in {len(plan['linkers'])} file(s), as new versions of their current "
                     "revision: " + ", ".join(plan["linkers"]))
    for other in plan["cross_vault"]:
        lines.append(f"In {other['vault']}: {', '.join(other['items'])} link the old file name. They resolve "
                     "through this vault's former numbers and are re-pointed at their next check-in.")
    if documents:
        lines.append("Open documents are closed first: " + ", ".join(pn for pn, _doc in documents))
    lines += [f"Cannot renumber: {text}" for text in plan["problems"]]
    return "\n\n".join(lines)


def _renumber_dialog(s, pn, plan, documents, answers=None):
    """(new number, reason) from the Renumber dialog, or None when cancelled (or refused, when
    ``answers`` {"new", "reason"} stand in for the typing so a script can run it without showing it)."""
    from PySide import QtWidgets

    from bruceworx import renumber

    dialog = QtWidgets.QDialog(_window())
    dialog.setWindowTitle("Renumber")
    form = QtWidgets.QFormLayout(dialog)
    form.addRow("Item", QtWidgets.QLabel(f"{_display(s, pn)}  ·  {plan['state']}"))
    number = QtWidgets.QLineEdit(plan["new"])
    number.setPlaceholderText("the next free number")
    form.addRow("New number", number)
    reason = QtWidgets.QLineEdit()
    reason.setPlaceholderText("why (required); recorded in the activity log")
    form.addRow("Reason", reason)
    summary = QtWidgets.QLabel()
    summary.setWordWrap(True)
    summary.setMinimumWidth(460)
    form.addRow(summary)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
    ok.setText("Renumber")
    form.addRow(buttons)
    state = {"new": plan["new"]}

    def update(*_args):
        typed = number.text().strip()
        found, _suggested, problems = renumber.check_number(s.vault, pn, typed)
        shown = dict(plan, new=found, problems=problems + [p for p in plan["problems"] if "checked out" in p])
        state["new"] = found
        summary.setText(renumber_summary(shown, documents))
        ok.setEnabled(bool(found) and not shown["problems"] and bool(reason.text().strip()))

    number.textChanged.connect(update)
    reason.textChanged.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    if answers is not None:
        number.setText(answers.get("new", plan["new"]))
        reason.setText(answers.get("reason", ""))
        update()
        return (state["new"], reason.text().strip()) if ok.isEnabled() else None
    update()
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return state["new"], reason.text().strip()


def renumber_item(pn=None, new=None, reason=None, answers=None):
    """Renumber... (PDM-12): give the item a new part number, with the reason in the activity log.

    Suggests the next free number (the typed one is checked), shows what will be rewritten and the
    users in other vaults, asks to close the affected open documents and refuses while one has unsaved
    changes. ``new`` and ``reason`` (or ``answers`` {"new", "reason", "close"}) let a script run it
    without dialogs. Returns the new part number, or "" when nothing was renumbered."""
    import FreeCAD

    from bruceworx import renumber

    s = session()
    pn = pn or current_target()
    if s is None or not pn:
        return ""
    answers = dict(answers or {})
    if new is not None:
        answers["new"] = new
    if reason is not None:
        answers["reason"] = reason
    pn = s.vault.resolve(pn)
    try:
        plan = renumber.plan(s.vault, pn, answers.get("new", ""))
    except Exception as exc:
        _message("Renumber", str(exc), "warning")
        return ""
    documents = affected_documents(s, plan)
    dirty = [doc.Label for _pn, doc in documents if _gui_modified(doc)]
    if dirty:
        _message("Renumber", "Save or close these documents first, they have unsaved changes:\n\n"
                 + "\n".join(dirty), "warning")
        return ""
    scripted = bool(answers)  # a script answers for the dialog and the questions
    picked = _renumber_dialog(s, pn, plan, documents, answers if scripted else None)
    if picked is None:
        return ""
    new, reason = picked
    if documents and not scripted:
        names = ", ".join(doc.Label for _pn, doc in documents)
        if not _ask("Renumber", f"Renumbering closes {names}. Close them now?", "Close and Renumber", "Cancel"):
            return ""
    for _pn, doc in documents:
        FreeCAD.closeDocument(doc.Name)
    ok, result = _run("Renumber", renumber.renumber, s.vault, pn, reason, new, "", None, None, s.work)
    if not ok:
        return ""
    changed(refresh=True)
    _console(f"BruceWorx vault: renumbered {pn} -> {result['new']}", "msg")
    if not scripted:
        _message("Renumber", f"{pn} is now {result['new']}. The old number still finds it.")
    return result["new"]


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
    if getattr(s.vault.model.items.get(pn), "configuration_of", None):  # MOD-03: its file, in that row
        import bruceworx_configs_gui

        return bruceworx_configs_gui.open_configuration_item(pn)
    if s.work.is_folder(pn):  # ECD-05: a KiCad project; its board opens in 3D (Open in KiCad: the project)
        import bruceworx_board_gui

        return bruceworx_board_gui.open_board_item(pn)
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
    # PDM-13 (1 Nov): you check out when you edit, not when you open ("let me open something and when I go to edit
    # something ask me to check-out the file"): a plain open is read-only. The old question stays behind a preference.
    if (ask and _prefs().GetBool("AskCheckOutOnOpen", False) and st.code in (UP_TO_DATE, NEWER) and st.state == WIP
            and not s.vault.locks.get(pn)):
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
    try:  # a big assembly opens from its preview meshes (Fast Open, PRF-03); parts and small assemblies do not
        import bruceworx_fastopen

        if bruceworx_fastopen.maybe_fast_open(s, pn) is not None:
            _update_tabs()
            return True
    except Exception as exc:  # noqa: BLE001 -- Fast Open is a speed-up, never a reason not to open
        _console(f"BruceWorx vault: Fast Open did not run ({exc}); opening the file", "warn")
    ok, _r = _run("Open from Vault", s.work.make_local, pn)  # iCloud: the file and what it links
    if not ok:
        return False
    _load(s.work.path(pn))
    _update_tabs()
    return True


def make_new_file(path, pn, kind, description, vault_root):
    """Write a new part (an App::Part holding a Body) or assembly for New Part in Vault, with
    the vault's part properties, its number, revision 01 and description; saved and closed."""
    import FreeCAD

    from bruceworx import partprops, pdm

    doc = FreeCAD.newDocument(pn.replace("-", "_"), hidden=True)
    try:
        doc.Label = pn
        try:
            import bruceworx_units

            bruceworx_units.apply_units(doc, bruceworx_units.default_units(vault_root, doc))  # PDM-09
        except Exception as exc:  # noqa: BLE001 -- a part without the vault's units is still a part
            FreeCAD.Console.PrintWarning(f"New Part in Vault: units not applied: {exc}\n")
        if kind == "assembly":
            main = doc.addObject("Assembly::AssemblyObject", "Assembly")
            main.newObject("Assembly::JointGroup", "Joints")
        else:
            main = doc.addObject("App::Part", "Part")
            main.addObject(doc.addObject("PartDesign::Body", "Body"))
        main.Label = description
        report = partprops.load_schema(extra_layers=partprops.vault_layers(vault_root)).apply(main)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise ValueError("; ".join(clash))
        main.PartNumber = pn
        main.Revision = pdm.first_prerelease()
        if "Description" in main.PropertiesList:
            main.Description = description
        doc.recompute()
        doc.saveAs(path)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _new_part_dialog(s, default_program=""):
    """(kind, description, program, category) from a small dialog, or None when cancelled."""
    from PySide import QtWidgets

    from bruceworx import vault_setup
    from bruceworx.sources import JsonNumberSource

    source = JsonNumberSource(vault_setup.numbering_path(s.root))
    programs = source.programs()
    dialog = QtWidgets.QDialog(_window())
    dialog.setWindowTitle("New Part in Vault")
    form = QtWidgets.QFormLayout(dialog)
    kind = QtWidgets.QComboBox()
    kind.addItems(["Part", "Assembly"])
    form.addRow("Type", kind)
    program = QtWidgets.QComboBox()
    program.addItems(programs)
    if default_program in programs:  # PDM-15: New part in <project>
        program.setCurrentText(default_program)
    if len(programs) > 1:
        form.addRow("Program", program)
    text = QtWidgets.QLineEdit()
    text.setPlaceholderText("e.g. Motor bracket")
    form.addRow("Description", text)
    category = QtWidgets.QComboBox()  # optional; the vault's own categories as suggestions
    category.setEditable(True)
    category.addItem("")
    category.addItems([c for c in sorted(category_counts(_panel_rows(s)), key=str.casefold) if c])
    category.lineEdit().setPlaceholderText("optional, e.g. Connectors")
    form.addRow("Category", category)
    number = QtWidgets.QLabel()
    form.addRow("Number", number)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
    ok.setText("Create")
    form.addRow(buttons)

    def update(*_args):
        word = kind.currentText().lower()
        try:
            data = source._load()
            code = vault_setup.document_type_for_item(word, vault_setup._vault_document_types(source, data), data)
            from bruceworx import pdm

            number.setText(f"{pdm.strip_revision(source.preview(program.currentText(), code))}  ·  WIP 01")
        except Exception as exc:
            number.setText(str(exc))
        ok.setEnabled(bool(text.text().strip()))

    for signal in (kind.currentIndexChanged, program.currentIndexChanged, text.textChanged):
        signal.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    update()
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return kind.currentText().lower(), text.text().strip(), program.currentText(), category.currentText().strip()


def _panel_rows(s):
    """{pn: {"category": ...}} for the vault's items, to suggest its existing categories."""
    return {pn: {"category": item.category} for pn, item in s.vault.items.items()}


def new_part(kind=None, description=None, program="", open_it=True, category=""):
    """New Part in Vault (FND-08): number a new part or assembly, check it in as WIP 01 and check
    it out to you; it opens ready to model. Without ``kind`` and ``description`` a dialog asks.
    Returns the part number, or "" when cancelled or refused."""
    s = session()
    if s is None:
        _message("New Part in Vault", "Open a vault first (Preferences > BruceWorx, or the Start page).")
        return ""
    if kind is None or description is None:
        answer = _new_part_dialog(s, program)
        if answer is None:
            return ""
        kind, description, program, category = answer
    from bruceworx import vault_setup

    def make(path, pn):
        make_new_file(path, pn, kind, description, s.root)

    ok, pn = _run("New Part in Vault", vault_setup.new_item, s.vault, kind, description, make, program,
                  "", category)
    if not ok:
        return ""
    s.refresh()
    ok, _path = _run("New Part in Vault", s.work.checkout, pn)
    if not ok:
        return pn
    _console(f"BruceWorx vault: new {kind} {pn} ({description}), WIP 01, checked out to you", "msg")
    if open_it:
        _load(s.work.path(pn))
        _activate_new(s.work.path(pn))
        _update_tabs()
    return pn


def _activate_new(path):
    """Make the new part's Body the active body (and its Part the active part), ready for a sketch."""
    import FreeCADGui

    doc = document_for(path)
    if doc is None:
        return
    try:
        view = FreeCADGui.getDocument(doc.Name).ActiveView
        part = doc.getObject("Part")
        body = doc.getObject("Body")
        if part is not None:
            view.setActiveObject("part", part)
        if body is not None:
            view.setActiveObject("pdbody", body)
    except Exception:  # no 3D view yet: the user activates the body by hand
        pass


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


def run_checkin(s, rows, comment, keep=False, fix_links=True, import_outside=True, notes=None, include_results=True):
    """Save unsaved ones, fix links, then check in every ticked row. Returns (done [pn], failed [(pn, text)]).

    notes (a dict) receives "imported" [(path, pn)], "not_imported" [(path, why)], "left"
    [LinkFix] and "relinked" (paths whose links changed).
    ``include_results`` (PDM-14): False stores a simulation's copy without its result objects and result meshes.
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
        kicad = s.work.is_folder(pn)  # ECD-05: a KiCad project: KiCad's render is its picture, no fingerprint
        try:  # the version's thumbnail: the model in the iso view, centred (the Files tab's tooltip)
            import bruceworx_preview

            if not kicad:
                bruceworx_preview.stamp_work_file(s, pn)
        except Exception as exc:  # noqa: BLE001 -- the picture is never a reason not to check in
            _console(f"Vault: no iso picture for {pn} ({exc})", "warn")
        extras = {}
        for fn in (() if kicad else CHECKIN_EXTRAS):  # PDM-09: made_from (drawings), fingerprint (parts); never a reason not to check in
            try:
                extras.update(fn(s, pn) or {})
            except Exception as exc:  # noqa: BLE001
                _console(f"Vault: {pn}: check-in extras not recorded ({exc})", "warn")
        try:
            data = [(hook, hook.prepare(s, pn, notes)) for hook in CHECKIN_HOOKS]  # may refuse
            transform = None
            if not include_results and s.vault.items[pn].item_type == "simulation":
                import bruceworx_sim_vault

                transform = bruceworx_sim_vault.stripped_copy
            s.work.checkin(pn, comment, keep_checked_out=keep, transform=transform, **extras)
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


def _include_results_pref() -> bool:
    """The "Include results" box as the user left it (per user: their FreeCAD profile); results in by default."""
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH).GetBool("SimIncludeResults", True)


def _set_include_results_pref(on: bool) -> None:
    import FreeCAD

    FreeCAD.ParamGet(PREFS_PATH).SetBool("SimIncludeResults", bool(on))


# Check-in hooks: objects with prepare(s, pn, notes) -> data (raise to refuse, before the file is
# stored) and record(s, pn, data, notes) after it is. bruceworx_bom adds the assembly's BOM (PDM-06).
CHECKIN_HOOKS = []
# Check-in extras: callables (s, pn) -> {"made_from": ..., "fingerprint": ...} written into the check-in
# event itself (PDM-09; bruceworx_project.checkin_extras). A failure is logged, the check-in goes on.
CHECKIN_EXTRAS = []


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


def offers_first_edit(doc_name, active_name, edit_name=None):
    """Whether a transaction opening in doc_name is your first edit of it. One in the active document is, and
    one in the document being edited in place (PDM-13: double-click a linked part in an assembly: its own
    document is in edit while the assembly is active). A part opened to be linked gets transactions from the
    assembly's command (the vault writing its LifecycleState), which are not edits of the part. Saving any
    vault file still asks for its check-out."""
    if edit_name is not None:  # an edit is running: only the document being edited is (not the assembly hosting it)
        return edit_name == doc_name
    return active_name is None or active_name == doc_name


def _edit_document_name():
    """The name of the document whose object the 3D view is editing in place, or None."""
    try:
        import FreeCADGui

        # In place, the 3D view editing is the assembly's (editDocument) while the object in edit is the part's:
        # the part's document is the one being edited (live check 2 Nov: the assembly was offered, not the part).
        gui = FreeCADGui.editDocument() or FreeCADGui.ActiveDocument
        vp = gui.getInEdit() if gui is not None else None
        obj = getattr(vp, "Object", None) if vp is not None else None
        if obj is not None:
            return obj.Document.Name
        return FreeCADGui.editDocument().Document.Name if FreeCADGui.editDocument() is not None else None
    except Exception:  # noqa: BLE001
        return None


class _DocumentObserver:
    """Save prompt, no backups for vault files, the first-edit offer, lifecycle-state writes."""

    def __init__(self):
        self.asked = set()  # documents whose first edit already offered a check-out
        self.was_modified = {}

    def slotStartSaveDocument(self, doc, filename):
        if str(filename).lower().endswith(".vlsim"):  # PDM-14: the summary the file has, before the save drops it
            try:
                import bruceworx_sim_vault

                bruceworx_sim_vault.remember_previous(filename)
            except Exception:  # noqa: BLE001 -- never a reason to refuse a save
                pass
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
            pn = s.item_pn_for(filename)
            item = s.vault.items.get(pn) if pn else None
            if item is not None and item.item_type in ("part", "assembly", "cots", "drawing", "simulation"):
                from PySide import QtCore

                # After the save has returned: the iso picture and the preview mesh into the saved file (PDM-13);
                # PDM-14: also a part's fingerprint, a simulation's summary.
                QtCore.QTimer.singleShot(0, lambda name=doc.Name, path=filename: _stamp_saved(name, path))
                return
        if str(filename).lower().endswith(".vlsim"):  # PDM-14: a simulation file anywhere gets its summary at every save
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, lambda name=doc.Name, path=filename: _stamp_saved(name, path))

    def slotOpenTransaction(self, doc, name):
        """The first edit of a read-only vault file offers a check-out (once per document), when the preference
        OfferCheckOutOnFirstEdit is on. Off by default (Bruce, 2 Nov: "can we not force the checkout dialogue
        unless I do it or go to save it?"): Check Out and the question on save stay."""
        if doc.Name in self.asked or getattr(doc, "Restoring", False):
            return
        if not _prefs().GetBool("OfferCheckOutOnFirstEdit", False):
            return
        import FreeCAD

        active = FreeCAD.ActiveDocument
        if not offers_first_edit(doc.Name, active.Name if active is not None else None, _edit_document_name()):
            return
        s = _state["session"]
        pn = pn_for_document(doc, s) if s is not None else ""
        if not pn:
            return
        st = s.status(pn)
        item = s.vault.model.items.get(pn)
        if st.code not in (UP_TO_DATE, NEWER) or item is None or item.state != WIP:
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


def _stamp_saved(name, path):
    import FreeCAD

    doc = FreeCAD.listDocuments().get(name)
    if doc is None or not doc.FileName or os.path.realpath(doc.FileName) != os.path.realpath(path):
        return
    try:
        import bruceworx_preview

        kind = "simulation" if str(path).lower().endswith(".vlsim") else ""
        s = _state["session"]
        pn = s.item_pn_for(path) if s is not None else ""
        if pn and not kind:
            kind = s.vault.items[pn].item_type
        if bruceworx_preview.stamp_saved(doc, path, kind):
            changed()
    except Exception as exc:  # noqa: BLE001 -- the save itself is done; only its picture is missing
        _console(f"BruceWorx vault: no picture for {os.path.basename(path)} ({exc})", "warn")


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
    if s is None or s.status(pn).code not in (UP_TO_DATE, NEWER):
        return
    st = s.status(pn)
    newer = st.code == NEWER
    text = (f"{doc.Label} ({st.display}) is not checked out to you, so changes to it cannot be saved.\n\n"
            + ("A newer version is in the vault: checking out brings it in, and your open copy is reloaded "
               "from it.\n\n" if newer else "") + "Check it out now?")
    if _ask("Check Out", text, "Check Out", "Not Now", default_yes=True):
        if check_out(pn) and newer:
            try:
                fresh = FreeCAD.getDocument(name)
                _reload(fresh)
            except Exception:  # noqa: BLE001
                _message("Check Out", f"{st.display} was checked out and brought up to date. Close and reopen "
                                      "it to see the newer version.", "info")


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


def _active_renumber(s, pn, st):
    return pn in s.vault.model.items


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
    ("BruceWorx_VaultRenumber", "Renumber…", "Give this item a new part number. The old number keeps "
     "finding it; every change is recorded in the activity log.", "pencil", "secondary", renumber_item,
     _active_renumber, ""),
    ("BruceWorx_VaultBreakLock", "Break Lock…", "Release a lock nobody can release normally (a crash, a "
     "lost Mac). Recorded in the journal.", "unlock", "error", lambda: break_lock(), _active_break, ""),
    ("BruceWorx_VaultOpen", "Open from Vault", "Open the file from the vault's working folder; read-only "
     "unless it is checked out to you.", "open", "secondary", open_from_vault, _active_open, ""),
    ("BruceWorx_VaultShowInPanel", "Show in Vault Panel", "Show this file in the Vault panel.", "panel",
     "secondary", lambda: show_in_panel(), _active_show, ""),
)
PANEL_COMMAND = "BruceWorx_VaultPanel"
NEW_PART_COMMAND = "BruceWorx_VaultNewPart"
# The right-click menus (Browser, Vault panel): in this order, None is a separator.
CONTEXT_COMMANDS = ("BruceWorx_VaultCheckOut", "BruceWorx_VaultCheckIn", "BruceWorx_VaultUndoCheckOut",
                    "BruceWorx_VaultGetLatest", "BruceWorx_VaultGetRevision", None, "BruceWorx_VaultSubmit",
                    "BruceWorx_VaultApprove", "BruceWorx_VaultRenumber", None, "BruceWorx_VaultOpen",
                    "BruceWorx_VaultShowInPanel")


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


class _NewPartCommand:
    def GetResources(self):
        return {"Pixmap": _icon_name("checkin", "accent"), "MenuText": "New Part in Vault…",
                "ToolTip": "Make a new numbered part or assembly in the open vault (WIP 01, checked out to "
                           "you), without importing a file."}

    def IsActive(self):
        return session() is not None

    def Activated(self):
        try:
            new_part()
        except Exception as exc:
            _message("New Part in Vault", str(exc), "warning")


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

    ROW_HEIGHT = 36  # room for the thumbnail (PDM-13)
    FOOTER = "Double-click opens · select a file to see where it is used · editing offers Check Out"
    # The columns are chosen by key (FILE_COLUMNS, Columns…); the Simulations chip has its own (PDM-14).
    SIM_KEYS = ("part_number", "sim_of", "sim_study", "sim_result", "sim_geometry", "status")
    SIM_WIDTHS = {"sim_study": 230, "sim_result": 170, "sim_geometry": 110, "status": 170}
    FILE_WIDTHS = {key: width for key, _t, width, _a in FILE_COLUMNS}
    RIGHT_ALIGNED = {key for key, _t, _w, align in FILE_COLUMNS if align == "right"}
    STRETCH = ("description", "sim_of")
    THUMB = QtCore.QSize(34, 26)
    PN_ROLE = QtCore.Qt.UserRole
    QTY_ROLE = QtCore.Qt.UserRole + 1
    CONTEXT_ROLE = QtCore.Qt.UserRole + 2  # shown only as the parent of a match: dimmed
    GROUP_ROLE = QtCore.Qt.UserRole + 3
    KEY_ROLE = QtCore.Qt.UserRole + 5  # a category group header: "cat:<name>"
    PATH_ROLE = QtCore.Qt.UserRole + 4  # P/Ns from the top, to keep a folded branch folded
    KIND_ROLE = QtCore.Qt.UserRole + 6  # a type group header (PDM-13): "assembly", "part", "drawing"
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
            key = self.panel.column_key(column)
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            group = first.data(GROUP_ROLE)
            if group:
                if column == 0:
                    painter.setFont(self.panel.title_font)
                    painter.setPen(view.colour("secondary"))
                    left = 4
                    kind = first.data(KIND_ROLE)
                    if kind:  # a type group (PDM-13): its icon before the name
                        painter.drawPixmap(QtCore.QPointF(rect.left() + 4, rect.center().y() - 7),
                                           icon_pixmap(kind, self.panel.type_colour(kind), 14, view.devicePixelRatioF()))
                        left = 24
                    painter.drawText(rect.adjusted(left, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, group)
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
            if key == "part_number":
                kind = row.get("item_type") or ""
                if kind != "change":  # the file's type and picture (PDM-13)
                    icon_kind = {"assembly": "assembly", "drawing": "drawing", "simulation": "simulation"}.get(kind, "part")
                    painter.drawPixmap(QtCore.QPointF(r.left(), rect.center().y() - 8),
                                       icon_pixmap(icon_kind, self.panel.type_colour(kind), 16, view.devicePixelRatioF()))
                    box = QtCore.QRectF(r.left() + 22, rect.center().y() - THUMB.height() / 2, THUMB.width(),
                                        THUMB.height())
                    pix = self.panel.thumb_pixmap(row)
                    painter.setPen(QtGui.QPen(view.colour("border"), 1))
                    painter.setBrush(view.colour("card"))
                    painter.drawRoundedRect(box, 4, 4)
                    if pix is not None:
                        size = pix.deviceIndependentSize()
                        scale = min((box.width() - 2) / max(size.width(), 1), (box.height() - 2) / max(size.height(), 1))
                        w, h = size.width() * scale, size.height() * scale
                        painter.drawPixmap(QtCore.QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h),
                                           pix, QtCore.QRectF(pix.rect()))
                    r = r.adjusted(22 + THUMB.width() + 8, 0, 0, 0)
                painter.setFont(self.panel.title_font)
                fm = QtGui.QFontMetrics(self.panel.title_font)
                pn = row["part_number"]
                rev = ("-" + row["revision"]) if row.get("revision") else ""
                pn_text = fm.elidedText(pn, QtCore.Qt.ElideRight, max(20, r.width() - fm.horizontalAdvance(rev)))
                painter.setPen(text_colour)
                painter.drawText(r, middle, pn_text)
                painter.setPen(secondary)
                painter.drawText(r.adjusted(fm.horizontalAdvance(pn_text), 0, 0, 0), middle, rev)
                if self.panel.bubbles.get(pn):  # PDM-14: a simulation of this part was made from older geometry
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(view.colour("warn"))
                    painter.drawEllipse(QtCore.QRectF(rect.right() - 16, rect.center().y() - 5, 10, 10))
            elif key == "sim_of":
                sim = self.panel.sim_info.get(row["part_number"]) or {}
                painter.setFont(self.panel.body_font)
                fm = QtGui.QFontMetrics(self.panel.body_font)
                painter.setPen(text_colour)
                text = f"{sim.get('of_display', '')}  {(self.panel.rows.get(sim.get('of')) or {}).get('description', '')}"
                painter.drawText(r, middle, fm.elidedText(text.strip(), QtCore.Qt.ElideRight, r.width()))
            elif key == "sim_study":
                sim = self.panel.sim_info.get(row["part_number"]) or {}
                painter.setFont(self.panel.small_font)
                fm = QtGui.QFontMetrics(self.panel.small_font)
                painter.setPen(secondary)
                painter.drawText(r, middle, fm.elidedText(sim.get("study", ""), QtCore.Qt.ElideRight, r.width()))
            elif key == "sim_result":
                sim = self.panel.sim_info.get(row["part_number"]) or {}
                painter.setFont(self.panel.small_font)
                fm = QtGui.QFontMetrics(self.panel.small_font)
                painter.setPen(secondary)
                painter.drawText(r, middle, fm.elidedText(sim.get("result", ""), QtCore.Qt.ElideRight, r.width()))
            elif key == "sim_geometry":
                sim = self.panel.sim_info.get(row["part_number"]) or {}
                text = sim.get("judgement", "")
                role = {"current": "ok", "part changed": "warn"}.get(text, "secondary")
                if text:
                    fm = QtGui.QFontMetrics(self.panel.chip_font)
                    cw = fm.horizontalAdvance(text) + 10
                    chip = QtCore.QRectF(r.left(), rect.center().y() - (fm.height() + 3) / 2, cw, fm.height() + 3)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(soft(view.colour(role), 26 if dim else 46))
                    painter.drawRoundedRect(chip, 4, 4)
                    painter.setFont(self.panel.chip_font)
                    painter.setPen(secondary if dim else view.colour(role))
                    painter.drawText(chip, QtCore.Qt.AlignCenter, text)
            elif key == "description":
                painter.setFont(self.panel.body_font)
                fm = QtGui.QFontMetrics(self.panel.body_font)
                painter.setPen(text_colour)
                painter.drawText(r, middle, fm.elidedText(row.get("description") or "", QtCore.Qt.ElideRight, r.width()))
            elif key == "used_in":  # how many assemblies use the file (PDM-13)
                text = self.panel.used_in_text(row["part_number"])
                if text:
                    painter.setFont(self.panel.body_font)
                    painter.setPen(secondary)
                    painter.drawText(r, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, text)
            elif key == "drawing":  # Yes, None (a gap), COTS
                text, role = self.panel.drawing_cell(row)
                if text:
                    painter.setFont(self.panel.small_font)
                    x = r.left()
                    if role in ("ok", "warn"):
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setBrush(secondary if dim else view.colour(role))
                        painter.drawRoundedRect(QtCore.QRectF(x, rect.center().y() - 4, 8, 8), 2, 2)
                        x += 13
                    painter.setPen(secondary)
                    painter.drawText(QtCore.QRect(x, rect.top(), r.right() - x, rect.height()), middle, text)
            elif key == "state":
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
            elif key == "status":
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
            elif key:  # the columns chosen in Columns…: plain text from the vault's records (PDM-13)
                text = self.panel.cell_text(key, row)
                if text:
                    painter.setFont(self.panel.small_font)
                    fm = QtGui.QFontMetrics(self.panel.small_font)
                    painter.setPen(secondary)
                    align = QtCore.Qt.AlignVCenter | (QtCore.Qt.AlignRight if key in RIGHT_ALIGNED else
                                                      QtCore.Qt.AlignLeft)
                    painter.drawText(r, align, fm.elidedText(text, QtCore.Qt.ElideRight, r.width()))
            painter.restore()

        def editorEvent(self, event, model, option, index):
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton \
                    and self.panel.column_key(index.column()) == "status":
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
                key = self.panel.column_key(index.column())
                if key == "part_number":  # the file name: its picture, as Explorer and Finder do
                    QtWidgets.QToolTip.showText(event.globalPos(), self.panel.tooltip_html(row), view)
                elif key == "used_in":  # the assemblies, by name
                    QtWidgets.QToolTip.showText(event.globalPos(), self.panel.where_used_text(row), view)
                elif key == "comment" and row.get("comment"):  # the whole comment: the cell shows its first line
                    QtWidgets.QToolTip.showText(event.globalPos(), row["comment"], view)
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

    import bruceworx_vaultbrowser

    browser = bruceworx_vaultbrowser.make_classes({"icon_pixmap": icon_pixmap, "soft": soft, "ChipFlow": ChipFlow})

    class ColumnsDialog(QtWidgets.QDialog):
        """Columns…: the data the Files table shows, and their order (PDM-13). Part number stays first and,
        with Description, is always shown; Reset to Default brings back the six columns of old."""

        def __init__(self, keys, parent=None):
            super().__init__(parent)
            self.setObjectName("bwVaultColumns")
            self.setWindowTitle("Columns")
            lay = QtWidgets.QVBoxLayout(self)
            note = QtWidgets.QLabel("Tick the data the file list shows; Move Up and Move Down set the order.\n"
                                    "Part number and Description are always shown.")
            note.setWordWrap(True)
            lay.addWidget(note)
            row = QtWidgets.QHBoxLayout()
            self.list = QtWidgets.QListWidget()
            self.list.setMinimumHeight(320)
            self.list.currentRowChanged.connect(lambda _r: self.update_buttons())
            row.addWidget(self.list, 1)
            side = QtWidgets.QVBoxLayout()
            self.up = QtWidgets.QPushButton("Move Up")
            self.up.clicked.connect(lambda _c=False: self.move(-1))
            self.down = QtWidgets.QPushButton("Move Down")
            self.down.clicked.connect(lambda _c=False: self.move(1))
            self.reset = QtWidgets.QPushButton("Reset to Default")
            self.reset.clicked.connect(lambda _c=False: self.fill(DEFAULT_COLUMNS))
            for button in (self.up, self.down):
                side.addWidget(button)
            side.addStretch(1)
            side.addWidget(self.reset)
            row.addLayout(side)
            lay.addLayout(row)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            lay.addWidget(buttons)
            self.fill(parse_columns(",".join(keys)))

        def fill(self, keys, order=None, current=None):
            """The chosen columns first, in their order, then the others as FILE_COLUMNS lists them."""
            order = order or list(keys) + [k for k, _t, _w, _a in FILE_COLUMNS if k not in keys]
            self.list.clear()
            for key in order:
                item = QtWidgets.QListWidgetItem(COLUMN_TITLES[key])
                item.setData(QtCore.Qt.UserRole, key)
                flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                if key in REQUIRED_COLUMNS:
                    item.setToolTip("Always shown")
                else:
                    flags |= QtCore.Qt.ItemIsUserCheckable
                item.setFlags(flags)
                item.setCheckState(QtCore.Qt.Checked if key in keys or key in REQUIRED_COLUMNS else QtCore.Qt.Unchecked)
                self.list.addItem(item)
            self.list.setCurrentRow(order.index(current) if current in order else 0)
            self.update_buttons()

        def order(self):
            return [self.list.item(i).data(QtCore.Qt.UserRole) for i in range(self.list.count())]

        def chosen(self):
            return [self.list.item(i).data(QtCore.Qt.UserRole) for i in range(self.list.count())
                    if self.list.item(i).checkState() == QtCore.Qt.Checked]

        def move(self, step):
            item = self.list.currentItem()
            if item is None:
                return
            key = item.data(QtCore.Qt.UserRole)
            self.fill(self.chosen(), move_column(self.order(), key, step), key)

        def update_buttons(self):
            row = self.list.currentRow()
            self.up.setEnabled(row > 1)  # nothing goes above Part number
            self.down.setEnabled(0 < row < self.list.count() - 1)

    class VaultPanel(QtWidgets.QWidget):
        """The Files tab (PDM-13): navigation at the left; in the middle the header, search, filter chips,
        one row per file and a footer; the selected file's preview at the right."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwVaultPanel")
            self.setWindowTitle("Vault")
            self.rows = {}
            self.nodes = []
            self.structure = {"items": {}, "bom": {}, "sources": {}, "drawings": {}}
            self.sim_info = {}  # simulation P/N -> vault_trees.simulations row (PDM-14)
            self.bubbles = {}  # part P/N -> [simulation P/N] marked "part changed"
            self.thumb_files = {}
            self.links_cache = {}
            self.collapsed = set()
            self.hidden_categories = set()
            self._cat_root = None
            self._hidden_items = 0
            self._filling = False
            self.pictures = None  # Refresh Pictures: the P/Ns still to render, while it runs
            self.kind = "all"
            self.scope = bruceworx_vaultbrowser.ALL  # the navigation's choice (PDM-13)
            self.programs = {}
            self.used = {}
            self.thumbs = {}
            self._mesh_sig = None
            self._contents_cache = {}
            self.sort_by_time = False  # by part number; the sort button swaps
            self.sort_column, self.sort_descending = None, False  # a click on a column's header (PDM-13)
            self.columns = column_prefs()  # the Files table's column keys (Columns…)
            self.widths = {}  # key -> the width the user gave a file column, kept across chips
            self._shown = None  # (keys, simulations) the header was last built for
            self._applying = False
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

            outer = QtWidgets.QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            self.splitter.setObjectName("bwVaultSplitter")
            self.splitter.setChildrenCollapsible(False)
            self.splitter.setHandleWidth(1)
            outer.addWidget(self.splitter)
            self.nav = browser["NavPane"]()
            self.nav.setMinimumWidth(150)
            self.nav.scopeChosen.connect(self.set_scope)
            self.splitter.addWidget(self.nav)
            center = QtWidgets.QWidget()
            center.setObjectName("bwVaultCenter")
            self.splitter.addWidget(center)
            lay = QtWidgets.QVBoxLayout(center)
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
            self.places = QtWidgets.QToolButton()
            self.places.setObjectName("bwVaultTool")
            self.places.setText("▾")
            self.places.setToolTip("Projects or Standard Libraries")
            self.places.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.places.setCursor(QtCore.Qt.PointingHandCursor)
            self.places_menu = QtWidgets.QMenu(self.places)
            self.places.setMenu(self.places_menu)
            self.places_menu.aboutToShow.connect(self._fill_places)
            self.places.hide()
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
            head.addWidget(self.places)
            head.addWidget(self.sync, 1)
            head.addWidget(self.refresh_button)
            head.addWidget(self.more)
            tl.addLayout(head)
            self.place_box = QtWidgets.QWidget()
            self.place_box.setObjectName("bwVaultPlaces")
            self.place_rows = QtWidgets.QVBoxLayout(self.place_box)
            self.place_rows.setContentsMargins(0, 0, 0, 0)
            self.place_rows.setSpacing(2)
            self._place_sig = None
            self.place_box.hide()
            tl.addWidget(self.place_box)
            self.crumbs = QtWidgets.QLabel("")
            self.crumbs.setObjectName("bwVaultCrumbs")
            tl.addWidget(self.crumbs)
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
            self.categories = QtWidgets.QToolButton()
            self.categories.setObjectName("bwVaultSort")
            self.categories.setText("Categories")
            self.categories.setToolTip("Show or hide the vault's categories (Connectors, ...)")
            self.categories.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.categories_menu = QtWidgets.QMenu(self.categories)
            self.categories_menu.aboutToShow.connect(self.fill_categories_menu)
            self.categories.setMenu(self.categories_menu)
            self.categories.hide()  # only when the vault has categories
            count.addWidget(self.count, 1)
            count.addWidget(self.categories)
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
            self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)  # Build Package (PDM-13)
            self.list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.model = QtGui.QStandardItemModel(0, len(self.columns), self.list)
            self.list.setModel(self.model)
            header = self.list.header()
            header.setStretchLastSection(False)
            header.setFirstSectionMovable(False)  # Part number stays first: the tree hangs from it
            header.setSectionsClickable(True)
            header.sectionClicked.connect(self.sort_by_column)
            header.sectionMoved.connect(self.column_moved)  # dragging a header keeps the new order
            header.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            header.customContextMenuRequested.connect(self.header_menu)
            self.apply_columns()
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
            self.preview = browser["PreviewPane"](self)
            self.preview.setMinimumWidth(300)
            self.splitter.addWidget(self.preview)
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setStretchFactor(2, 0)
            self.splitter.setSizes([210, 600, 440])
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

        # --- PDM-13: the browser's helpers
        def session(self):
            return _state["session"]

        def type_colour(self, kind):
            """Part blue, assembly amber, drawing grey."""
            return self.list.colour({"assembly": "warn", "drawing": "secondary"}.get(kind, "accent"))

        def used_in_text(self, pn):
            return bruceworx_vaultbrowser.used_in_text(self.used.get(pn, ()))

        def drawing_cell(self, row):
            return bruceworx_vaultbrowser.drawing_cell(self.structure, row["part_number"], row)

        def _contents(self, pn):
            from bruceworx import vault_trees

            if pn not in self._contents_cache:
                self._contents_cache[pn] = vault_trees.contents(self.structure, pn)
            return self._contents_cache[pn]

        def thumb_pixmap(self, row):
            """The row's picture, scaled for the list (cached per version)."""
            sha = row.get("sha") or ""
            if not sha:
                return None
            if sha not in self.thumbs:
                path = self.thumb_file(row)
                pix = QtGui.QPixmap(path) if path else QtGui.QPixmap()
                if not pix.isNull():
                    dpr = self.devicePixelRatioF()
                    pix = pix.scaled(int(THUMB.width() * dpr * 2), int(THUMB.height() * dpr * 2),
                                     QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    pix.setDevicePixelRatio(dpr * 2)
                self.thumbs[sha] = None if pix.isNull() else pix
            return self.thumbs[sha]

        def set_scope(self, key):
            """The navigation picked a project, an assembly, a view or a category."""
            self.scope = key or bruceworx_vaultbrowser.ALL
            self._fill_nav()
            self.fill()

        def _fill_nav(self):
            self.nav.fill(bruceworx_vaultbrowser.nav_entries(self.structure, self.rows, self.programs), self.scope)

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
                from bruceworx import vault_trees

                now = _dt.datetime.now(_dt.timezone.utc)
                for pn, st in s.statuses().items():
                    versions = s.vault.versions.get(pn, [])
                    text, role = row_status(st.code, st.state, st.lock, s.me, versions, s.fetched(pn), now)
                    last = versions[-1] if versions else None
                    self.rows[pn] = {
                        "part_number": pn, "display": st.display, "revision": st.revision,
                        "description": st.description, "state": st.state, "code": st.code,
                        "item_type": st.item_type, "status_text": text, "status_role": role,
                        "category": getattr(s.vault.items.get(pn), "category", ""),
                        "former": " ".join(s.vault.former_numbers(pn)),  # PDM-12: search finds the old number
                        "sha": last.sha256 if last else "", "object": os.path.join(s.root, last.object_name)
                        if last else "", "at": last.at if last else "", "active": pn == active,
                        # Columns… (PDM-13): the latest version, the lock and the program, never the file
                        "size": last.size if last else None, "actor": last.actor if last else "",
                        "comment": last.comment if last else "", "versions": len(versions), "lock": st.lock,
                        "program": vault_trees.program_code(pn),
                    }
                try:  # ECRs and ECOs, under the Changes chip (PDM-05)
                    import bruceworx_changes

                    self.rows.update(bruceworx_changes.change_rows(s.vault, s.me, now))
                except ImportError:
                    pass
            self.nodes = self.file_tree(s)
            from bruceworx import vault_trees

            self.used = vault_trees.used_in(self.structure)
            self.programs = vault_trees.program_names(s.root) if s is not None else {}
            self._contents_cache = {}
            sig = tuple(sorted((pn, r.get("sha")) for pn, r in self.rows.items()))
            if sig != self._mesh_sig:  # new versions: their meshes and pictures are read again
                self._mesh_sig = sig
                self.preview.forget()
                self.thumbs = {}
            self.preview.pn = None  # show the selected file afresh after the fill
            self._fill_nav()
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
                self.sim_info = {r["pn"]: r for r in vault_trees.simulations(self.structure)}
                self.bubbles = vault_trees.parts_with_changed_simulation(self.structure)
                return vault_trees.file_tree(self.structure)
            except Exception as exc:  # noqa: BLE001 -- a damaged vault still lists its files, flat
                _console(f"Vault: the Files tree could not be built ({exc}); listing the files flat", "warn")
                self.structure = {"items": {}, "bom": {}, "sources": {}, "drawings": {}}
                self.sim_info, self.bubbles = {}, {}
                return [{"pn": pn, "qty": None, "children": []} for pn in sorted(self.rows)]

        def update_header(self):
            if not _alive(self):
                return
            s = _state["session"]
            if s is None:
                self.name.setText("No vault open")
                self.places.hide()
                self._fill_place_rows("")
                self.sync.setText("")
                return
            self.name.setText(s.name)
            self.places.setVisible(len(neighbour_places(s.root)) > 1)
            self._fill_place_rows(s.root)
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
            scope = bruceworx_vaultbrowser.ALL if changes else self.scope
            hits = {pn for pn, r in self.rows.items() if matches(r, self.kind, query)
                    and bruceworx_vaultbrowser.in_scope(scope, pn, r, self.structure, self._contents)}
            filtering = bool(query.strip()) or self.kind != "all"
            if changes:  # ECRs and ECOs are not files: a plain list
                nodes = [{"pn": pn, "qty": None, "children": []} for pn in self.rows
                         if self.rows[pn].get("item_type") == "change"]
            else:  # one row per file (PDM-13): what an assembly uses is in the preview pane
                nodes = [{"pn": pn, "qty": None, "children": []} for pn in sorted(hits)]
            self._sync_category_prefs()
            self._hidden_items = 0
            if not changes:
                # A search looks through hidden categories too (their groups are marked "hidden").
                nodes, self._hidden_items = group_by_category(
                    nodes, self.rows, self.hidden_categories, show_hidden=bool(query.strip()))
                if self._hidden_items:
                    listed_now = set()

                    def seen(ns):
                        for n in ns:
                            listed_now.add(n["pn"])
                            seen(n["children"])

                    seen(nodes)
                    hits = hits & listed_now
                if any(n["pn"] is None for n in nodes):  # category groups, each cut by type
                    nodes = [dict(n, children=group_by_type(n["children"], self.rows, f"{n.get('key')}/"))
                             if n["pn"] is None else n for n in nodes]
                else:
                    nodes = group_by_type(nodes, self.rows)
            s_now = _state["session"]
            self.crumbs.setText("  \u203a  ".join([s_now.name if s_now is not None else "Vault"] +
                                                bruceworx_vaultbrowser.scope_title(scope, self.rows, self.programs)))
            counts = category_counts(self.rows)
            self.categories.setVisible(not changes and any(counts))
            self.categories.setText(f"Categories ({len(self.hidden_categories & set(counts))} hidden)"
                                    if self.hidden_categories & set(counts) else "Categories")
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
                    # A group's name runs across the row (PDM-13: the list's columns are narrow).
                    self.list.setFirstColumnSpanned(item.row(), item.index().parent(), True)
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
                               (f" of {total}" if shown != total else "") +
                               (f" · {self._hidden_items} in hidden categories" if self._hidden_items else ""))
            self.sort.setText(COLUMN_TITLES.get(self.sort_column) or ("Modified" if self.sort_by_time else "Part number"))
            s = _state["session"]
            if s is None:
                self.empty.setText(_state["error"] and f"The vault cannot be read:\n{_state['error']}" or
                                   "No vault is open.\nOpen one from the Start page.")
            elif not total and changes:
                self.empty.setText("No ECRs or ECOs yet.\nRaise ECR… on a released item starts one.")
            elif not total:
                self.empty.setText("The vault is empty.\nBruceWorx → Vault → New Part in Vault… makes a part;\n"
                                   "Import Files into Vault… adds files.")
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
            if self.sort_column:  # a column's header: numbers as numbers, empty cells last either way
                values = {id(n): self.sort_value(self.sort_column, row(n)) for n in out}
                out.sort(key=lambda n: (0, "") if values[id(n)] is None else (1, values[id(n)]),
                         reverse=self.sort_descending)
                out.sort(key=lambda n: values[id(n)] is None)
            elif self.sort_by_time:
                out.sort(key=lambda n: row(n).get("at") or "", reverse=True)
            out.sort(key=lambda n: (n["pn"] is None, row(n).get("item_type") != "drawing"))
            return out

        def make_items(self, node, parent_path, expand):
            path = f"{parent_path}/{node['pn'] or node.get('key') or node.get('group')}"
            first = QtGui.QStandardItem(node.get("group") or (self.rows.get(node["pn"]) or {}).get("display", ""))
            first.setData(node["pn"], PN_ROLE)
            first.setData(node.get("qty"), QTY_ROLE)
            first.setData(bool(node.get("context")), CONTEXT_ROLE)
            first.setData(node.get("group") or "", GROUP_ROLE)
            first.setData(node.get("key"), KEY_ROLE)
            first.setData(path, PATH_ROLE)
            first.setData(node.get("kind"), KIND_ROLE)
            items = [first] + [QtGui.QStandardItem() for _c in self.column_keys()[1:]]
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
                if index.siblingAtColumn(0).data(KEY_ROLE):  # a category group: remembered per vault
                    self._save_category_prefs()

        # --- categories
        def _sync_category_prefs(self):
            """Read this vault's hidden and folded category groups when the vault changes."""
            s = _state["session"]
            root = s.root if s is not None else None
            if root == self._cat_root:
                return
            self._cat_root = root
            self.hidden_categories, folded = category_prefs(root) if root else (set(), set())
            self.collapsed = {p for p in self.collapsed if not p.startswith("/cat:")} | {f"/cat:{c}" for c in folded}

        def _save_category_prefs(self):
            if self._cat_root:
                folded = {p[len("/cat:"):] for p in self.collapsed if p.startswith("/cat:")}
                save_category_prefs(self._cat_root, self.hidden_categories, folded)

        def set_category_hidden(self, name, hide):
            (self.hidden_categories.add if hide else self.hidden_categories.discard)(name)
            self._save_category_prefs()
            self.fill()

        def show_all_categories(self):
            self.hidden_categories.clear()
            self._save_category_prefs()
            self.fill()

        def fill_categories_menu(self):
            """One checkbox per category (checked = shown), Show all, and the group folding."""
            menu = self.categories_menu
            menu.clear()
            for name, n in sorted(category_counts(self.rows).items(), key=lambda kv: (kv[0] == "", kv[0].casefold())):
                action = menu.addAction(f"{name or UNCATEGORIZED}  ({n})")
                action.setCheckable(True)
                action.setChecked(name not in self.hidden_categories)
                action.toggled.connect(lambda on, c=name: self.set_category_hidden(c, not on))
            menu.addSeparator()
            menu.addAction("Show all").triggered.connect(lambda _c=False: self.show_all_categories())

        def group_menu(self, key, pos):
            name = key[len("cat:"):]
            menu = QtWidgets.QMenu(self)
            hidden = name in self.hidden_categories
            act = menu.addAction(f"Show {name or UNCATEGORIZED}" if hidden else f"Hide {name or UNCATEGORIZED}")
            act.triggered.connect(lambda _c=False: self.set_category_hidden(name, not hidden))
            menu.addAction("Show all categories").triggered.connect(lambda _c=False: self.show_all_categories())
            menu.exec(self.list.viewport().mapToGlobal(pos))

        def fit_name_column(self):
            """The Part number column wide enough for the deepest, longest P/N-REV (within reason)."""
            fm = QtGui.QFontMetrics(self.title_font)
            widest = 0

            def walk(parent, depth):
                nonlocal widest
                for r in range(self.model.rowCount(parent)):
                    index = self.model.index(r, 0, parent)
                    row = self.rows.get(index.data(PN_ROLE)) or {}
                    if not index.data(GROUP_ROLE):  # group headers span the row (PDM-13); not measured
                        text = row.get("part_number", "") + ("-" + row["revision"] if row.get("revision") else "")
                        picture = 22 + THUMB.width() + 8 if row.get("item_type") != "change" else 0
                        widest = max(widest, (depth + 1) * self.list.indentation() + picture +
                                     fm.horizontalAdvance(text) + 16)
                    if self.list.isExpanded(index):
                        walk(index, depth + 1)

            walk(QtCore.QModelIndex(), 0)
            self.list.setColumnWidth(0, max(200, min(420, widest)))

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
            pn = row["part_number"] if row is not None else None
            if pn != self.preview.pn:
                self.preview.show_item(pn)

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
            changed = self.bubbles.get(row["part_number"])
            if changed:  # the bubble flag (PDM-14)
                lines.append(f'<span style="color:{self.list.colour("warn").name()}">Simulation out of date: '
                             f'{_esc(", ".join(changed))} was made from an older version of this part</span>')
            picture = ""
            path = self.thumb_file(row)
            if path:
                size = QtGui.QImageReader(path).size()
                width = min(TIP_WIDTH, size.width()) if size.isValid() and size.width() > 0 else TIP_WIDTH
                url = QtCore.QUrl.fromLocalFile(path).toString()
                picture = f'<img src="{url}" width="{width}"><br>'
            return f"<div>{picture}{'<br>'.join(lines)}</div>"

        def thumb_file(self, row):
            """The latest version's picture as a PNG on disk (for the tooltip's rich text): the iso
            picture Refresh Pictures cached, else the thumbnail saved in the file, or ""."""
            sha = row.get("sha")
            if not sha or row.get("item_type") == "change":
                return ""
            try:
                import bruceworx_preview

                cached = bruceworx_preview.cached(sha)
                if cached:
                    return cached
            except ImportError:
                pass
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
        def sim_mode(self):
            return self.kind == "simulations"

        def set_kind(self, key):
            self.kind = key
            self.apply_columns()
            self.fill()

        def toggle_sort(self):
            if self.sort_column:  # from a column's order back to the button's own
                self.sort_column, self.sort_by_time = None, False
            else:
                self.sort_by_time = not self.sort_by_time
            self.update_sort_indicator()
            self.fill()

        # --- the columns (PDM-13): chosen in Columns… or the header's menu, kept in VaultColumns
        def column_keys(self):
            return SIM_KEYS if self.sim_mode() else self.columns

        def column_key(self, column):
            keys = self.column_keys()
            return keys[column] if 0 <= column < len(keys) else None

        def cell_text(self, key, row):
            s = _state["session"]
            return column_cell(key, row, s.me if s is not None else "")[0]

        def sort_value(self, key, row):
            if not row:
                return None
            if key == "used_in":
                return len(self.used.get(row["part_number"], ())) or None
            if key == "drawing":
                return self.drawing_cell(row)[0].casefold() or None
            s = _state["session"]
            return column_cell(key, row, s.me if s is not None else "")[1]

        def apply_columns(self):
            """The header for the chosen columns (or the Simulations chip's), the rows emptied for the
            fill that follows. Widths the user gave the file columns are kept by key."""
            header = self.list.header()
            if self._shown is not None and not self._shown[1]:
                for column, key in enumerate(self._shown[0]):
                    if key not in STRETCH and column < self.model.columnCount():
                        self.widths[key] = self.list.columnWidth(column)
            keys, sim = self.column_keys(), self.sim_mode()
            self._applying = True
            self._filling = True
            try:
                self.model.removeRows(0, self.model.rowCount())
                self.model.setColumnCount(len(keys))
                self.model.setHorizontalHeaderLabels(list(SIM_COLUMNS) if sim else [COLUMN_TITLES[k] for k in keys])
                for column in range(len(keys)):  # a dragged header: the new order is in keys now
                    header.moveSection(header.visualIndex(column), column)
                for column, key in enumerate(keys):
                    if key in STRETCH:
                        header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
                        continue
                    header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
                    width = SIM_WIDTHS.get(key) if sim else self.widths.get(key)
                    self.list.setColumnWidth(column, width or FILE_WIDTHS.get(key) or 100)
            finally:
                self._applying = False
                self._filling = False
            self._shown = (keys, sim)
            self.fit_description()
            self.update_sort_indicator()

        def fit_description(self):
            """Description fills the row, but never below DESCRIPTION_MIN px: with many columns it keeps
            its width and the table scrolls sideways instead (Bruce's first look: "Voro..." with two more)."""
            if getattr(self, "_shown", None) is None or self._shown[1]:
                return
            keys = self._shown[0]
            if "description" not in keys:
                return
            header = self.list.header()
            column = keys.index("description")
            if not getattr(self, "_fit_filter", None):
                class _Fit(QtCore.QObject):
                    def eventFilter(inner, _obj, event):
                        if event.type() == QtCore.QEvent.Resize:
                            QtCore.QTimer.singleShot(0, self.fit_description)
                        return False

                self._fit_filter = _Fit(self.list)
                self.list.viewport().installEventFilter(self._fit_filter)
            others = sum(self.list.columnWidth(c) for c in range(len(keys)) if c != column)
            room = self.list.viewport().width() - others
            if room < DESCRIPTION_MIN:
                if header.sectionResizeMode(column) != QtWidgets.QHeaderView.Interactive:
                    header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
                self.list.setColumnWidth(column, DESCRIPTION_MIN)
            elif header.sectionResizeMode(column) != QtWidgets.QHeaderView.Stretch:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)

        def set_columns(self, keys):
            """Show these columns, in this order, at once; remembered in the preferences."""
            self.columns = save_column_prefs(keys)
            if self.sort_column not in self.columns:
                self.sort_column = None
            if not self.sim_mode():
                self.apply_columns()
                self.fill()

        def show_column(self, key, on):
            keys = [k for k in self.columns if k != key] + ([key] if on else [])
            self.set_columns(keys)

        def column_moved(self, _logical, _old, _new):
            if self._applying or self.sim_mode():
                return
            header = self.list.header()
            order = [self.column_key(header.logicalIndex(v)) for v in range(header.count())]
            QtCore.QTimer.singleShot(0, lambda: _alive(self) and self.set_columns([k for k in order if k]))

        def header_menu(self, pos):
            """Right-click on the header: a tick per column, Columns… and Reset to default."""
            if self.sim_mode():
                return
            menu = QtWidgets.QMenu(self)
            for key, title, _w, _a in FILE_COLUMNS:
                act = menu.addAction(title)
                act.setCheckable(True)
                act.setChecked(key in self.columns)
                act.setEnabled(key not in REQUIRED_COLUMNS)
                act.toggled.connect(lambda on, k=key: self.show_column(k, on))
            menu.addSeparator()
            menu.addAction("Columns…").triggered.connect(lambda _c=False: self.columns_dialog())
            act = menu.addAction("Reset to Default")
            act.setEnabled(self.columns != DEFAULT_COLUMNS)
            act.triggered.connect(lambda _c=False: self.set_columns(DEFAULT_COLUMNS))
            menu.exec(self.list.header().mapToGlobal(pos))

        def columns_dialog(self):
            dialog = ColumnsDialog(self.columns, self)
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                self.set_columns(dialog.chosen())

        def sort_by_column(self, column):
            """A click on a column's header sorts each group by it; a second click reverses."""
            key = self.column_key(column)
            if self.sim_mode() or key is None:
                return
            if key == self.sort_column:
                self.sort_descending = not self.sort_descending
            else:
                self.sort_column, self.sort_descending = key, False
            self.update_sort_indicator()
            self.fill()

        def update_sort_indicator(self):
            header = self.list.header()
            shown = bool(self.sort_column) and not self.sim_mode() and self.sort_column in self.columns
            header.setSortIndicatorShown(shown)
            if shown:
                header.setSortIndicator(self.columns.index(self.sort_column),
                                        QtCore.Qt.DescendingOrder if self.sort_descending else QtCore.Qt.AscendingOrder)

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
            key = index.siblingAtColumn(0).data(KEY_ROLE) if index.isValid() else None
            if row is None and key:
                self.group_menu(key, pos)
                return
            if row is None:
                return
            picked = self.selected_pns()
            if row["part_number"] not in picked:
                self.list.setCurrentIndex(index)
                picked = [row["part_number"]]
            if len(picked) > 1:  # several rows: what they share is a package of their drawings (PDM-13)
                menu = QtWidgets.QMenu(self)
            else:
                menu = build_item_menu(self, row["part_number"], in_panel=True)
                menu.addSeparator()
            act = menu.addAction(f"Build Package… ({len(picked)} selected)" if len(picked) > 1 else "Build Package…")
            act.triggered.connect(lambda _c=False, pns=picked: self.build_package(pns))
            menu.exec(self.list.viewport().mapToGlobal(pos))

        def selected_pns(self):
            out = []
            for index in self.list.selectionModel().selectedRows(0):
                pn = index.data(PN_ROLE)
                if pn and (self.rows.get(pn) or {}).get("item_type") != "change" and pn not in out:
                    out.append(pn)
            return out

        def build_package(self, pns):
            import bruceworx_package

            bruceworx_package.export_dialog(self, pns)

        def _fill_places(self):
            self.places_menu.clear()
            s = _state["session"]
            if s is not None:
                add_place_actions(self.places_menu, s.root)

        def _fill_place_rows(self, root):
            """One row under the vault name for each vault beside it."""
            others = other_places(root)
            sig = tuple(others)
            if sig == self._place_sig:
                return
            self._place_sig = sig
            lay = self.place_rows
            while lay.count():
                widget = lay.takeAt(0).widget()
                if widget is not None:
                    widget.deleteLater()
            self.place_box.setVisible(bool(others))
            for name, folder in others:
                button = QtWidgets.QPushButton(name, self.place_box)
                button.setObjectName("bwVaultPlace")
                button.setCursor(QtCore.Qt.PointingHandCursor)
                button.setFlat(True)
                button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                button.setToolTip(f"Open {name}")
                button.clicked.connect(lambda _c=False, f=folder: use_place(f))
                lay.addWidget(button)

        def fill_more(self):
            self.more_menu.clear()
            try:  # the vault's dashboard is the Start page (PDM-10)
                import bruceworx_start

                act = self.more_menu.addAction("Dashboard")
                act.triggered.connect(lambda _c=False: bruceworx_start.show_dashboard())
                self.more_menu.addSeparator()
            except ImportError:
                pass
            entries = (("New Part in Vault…", NEW_PART_COMMAND), ("Vault Settings…", "BruceWorx_VaultSettings"),
                       ("Import Files into Vault…", "BruceWorx_ImportIntoVault"))
            for text, name in entries:
                act = self.more_menu.addAction(text)
                act.triggered.connect(lambda _c=False, n=name: FreeCADGui.runCommand(n))
            act = self.more_menu.addAction("Break Lock…")
            act.setEnabled(bool(_state["session"] and _state["session"].vault.locks))
            act.triggered.connect(lambda _c=False: break_lock())
            act = self.more_menu.addAction("Move Vault…")
            act.triggered.connect(lambda _c=False: move_vault_dialog())
            act = self.more_menu.addAction("Refresh Pictures")
            act.setToolTip("Render an iso picture of every part and assembly checked in before pictures "
                           "were made at check-in; kept on this Mac, the vault is not changed")
            act.setEnabled(_state["session"] is not None and self.pictures is None)
            act.triggered.connect(lambda _c=False: self.refresh_pictures())
            act = self.more_menu.addAction("Columns…")
            act.setToolTip("Choose the data the file list shows (file size, modified, ...) and their order")
            act.triggered.connect(lambda _c=False: self.columns_dialog())
            self.more_menu.addSeparator()
            act = self.more_menu.addAction("Show in Finder")
            s = _state["session"]
            act.setEnabled(s is not None)
            if s is not None:
                act.triggered.connect(lambda _c=False, r=s.root: QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(r)))

        def refresh_pictures(self):
            """Render the missing iso pictures one file at a time (the window stays live), into the
            cache on this Mac; the footer counts down."""
            import bruceworx_preview

            s = _state["session"]
            if s is None or self.pictures is not None:
                return
            todo = [pn for pn in sorted(s.vault.items) if bruceworx_preview.needs_picture(s, pn)]
            self.pictures = {"todo": todo, "total": len(todo), "done": 0, "empty": [], "failed": []}
            self._next_picture()

        def _next_picture(self):
            import bruceworx_preview

            job, s = self.pictures, _state["session"]
            if job is None or not _alive(self):
                return
            if not job["todo"] or s is None:
                self.pictures = None
                text = (f"Pictures refreshed: {job['done']} new" if job["total"] else "Every picture is up to date")
                for key, words in (("empty", "nothing to draw"), ("failed", "could not be opened")):
                    pns = job[key]
                    if pns:
                        text += f" · {len(pns)} {words} ({', '.join(pns[:3])}{'…' if len(pns) > 3 else ''})"
                self.thumb_files.clear()
                self.thumbs = {}  # the list's pictures and the 3D previews are read again (PDM-13)
                self.preview.forget()
                self.preview.pn = None
                self.show_where_used(self.list.currentIndex())
                self.list.viewport().update()
                self.footer.setText(text)
                return
            pn = job["todo"].pop(0)
            self.footer.setText(f"Refreshing pictures · {job['total'] - len(job['todo'])} of {job['total']} · {pn}")
            try:
                if bruceworx_preview.refresh_one(s, pn):
                    job["done"] += 1
                else:
                    job["empty"].append(pn)
            except Exception as exc:  # noqa: BLE001 -- one bad file does not stop the rest
                _console(f"Vault: no picture for {pn} ({exc})", "warn")
                job["failed"].append(pn)
            QtCore.QTimer.singleShot(0, lambda: _alive(self) and self._next_picture())

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
            self.include_results = None
            if any(self.s.vault.items[r["part_number"]].item_type == "simulation" for r in self.rows
                   if r["part_number"] in self.s.vault.items):  # PDM-14: results are big; you choose
                self.include_results = QtWidgets.QCheckBox("Include results (simulations)")
                self.include_results.setObjectName("bwVaultIncludeResults")
                self.include_results.setChecked(_include_results_pref())
                self.include_results.setToolTip(
                    "Unticked, the version keeps the study, the solid copy, the mesh settings and the summary but "
                    "not the result objects or result meshes (a few MB instead of 150). Your work copy loses them "
                    "too; the file you had is kept in the work folder's .results folder.")
            self.keep = QtWidgets.QCheckBox("Keep checked out after check-in")
            self.close = QtWidgets.QCheckBox("Close the checked-in files")
            self.close.setChecked(True)
            if self.include_results is not None:
                ol.addWidget(self.include_results)
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
            include = self.include_results is None or self.include_results.isChecked()
            if self.include_results is not None:
                _set_include_results_pref(include)
            done, failed = run_checkin(self.s, rows, text.strip(), keep, import_outside=importing, notes=notes,
                                       include_results=include)
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

    return {"VaultPanel": VaultPanel, "CheckInPanel": CheckInPanel, "VaultList": VaultList,
            "ColumnsDialog": ColumnsDialog}


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
        names = [PANEL_COMMAND, NEW_PART_COMMAND, None] + list(CONTEXT_COMMANDS[:-2]) + [None, "BruceWorx_VaultBreakLock"]
        actions = []
        for name in names:
            if name is None:
                sub.addSeparator()
                continue
            if name == PANEL_COMMAND:
                text, icon = "Vault Panel", _icon_name("panel", "secondary")
            elif name == NEW_PART_COMMAND:
                text, icon = "New Part in Vault…", _icon_name("checkin", "accent")
            else:
                entry = next(e for e in COMMANDS if e[0] == name)
                text, icon = entry[1], _icon_name(entry[3], entry[4])
            act = sub.addAction(FreeCADGui.getIcon(icon) or QtGui.QIcon(), text)
            act.triggered.connect(lambda _c=False, n=name: FreeCADGui.runCommand(n))
            actions.append((name, act))

        def update():
            for name, act in actions:
                act.setEnabled(session() is not None if name in (PANEL_COMMAND, NEW_PART_COMMAND)
                               else available(name))

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
    FreeCADGui.addCommand(NEW_PART_COMMAND, _NewPartCommand())

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
