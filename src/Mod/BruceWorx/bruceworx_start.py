# SPDX-License-Identifier: LGPL-2.1-or-later
"""The BruceWorx Start page (roadmap §13.4, step C5; UIX-06, PDM-04).

FreeCAD's Start page is the C++ StartGui::StartView: an MDI view named
"StartView" whose central widget is a QStackedWidget. Page 0 is the
first-start page, page 1 the documents page (Mod/Start/Gui/StartView.cpp).
This module adds the BruceWorx page to that stack and shows it instead of
page 1, so the Start tab, its close button and ShowOnStartup stay stock.

* Left: New (Part, Assembly, Drawing, Sketch, Simulation study), Open and
  Import, then one row per vault under VAULT. The open vault
  (Preferences/BruceWorx/VaultPath) keeps the menu to open, create, import
  into, configure or close a vault (task dialogs in bruceworx_vaultwizard).
  Each vault beside it, such as Standard Libraries next to Vector, is its own
  row and opens that vault. Picking a folder that is not a vault opens Create
  Vault on it. The New buttons do what the stock buttons do: Std_New, the
  workbench, then its first command.
* Right, with a vault open: My work (PDM-04 plan, 23 Oct): files checked out
  to me, waiting for review, and newer in the vault than my work copy, read
  with bruceworx/pdm_workspace.py as the actor pinned for the vault; a row
  opens the work copy, fetching it first when there is none.
* Right: the recent files (BaseApp/Preferences/RecentFiles, MRU0...) as cards
  with the thumbnail from the .FCStd zip, then as a table with part number,
  revision, lifecycle state, date and location. P/N, revision and state are
  the part properties in the file's Document.xml (bruceworx/partprops.py);
  the vault's own record wins when the part is in the open vault. Files are
  read on a worker thread and cached by modification time.
* Foot: the examples (the stock page's examples folder) and the stock
  "show at startup" switch (Mod/Start/ShowOnStartup).

The page is selected again whenever Start_Start makes a new view
(QMdiArea.subWindowActivated) and whenever the view goes back to page 1 by
itself (first-start page dismissed). The first-start page (page 0) is left
alone; "Getting started" opens it.

* PDM-10 (24 Oct): the main area has two tabs, the vault's dashboard
  (bruceworx_dashboard; the default while a vault is open; vault items only)
  and Recent files (every recent file on this Mac; the default with no vault).
  The worker keeps the vault and reads only new journal lines; the page looks
  every 5 s while it is visible and after this FreeCAD's vault operations.
  Preferences/BruceWorx/VaultDashboard off keeps the page above as it was.

Preferences/BruceWorx/StartPage (default on). Off shows the stock page again.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET

import bruceworx_dashboard as bwdash

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "StartPage"
START_PATH = "User parameter:BaseApp/Preferences/Mod/Start"
RECENT_PATH = "User parameter:BaseApp/Preferences/RecentFiles"
VIEW_NAME = "StartView"
PAGE_NAME = "BruceWorxStart"
DOCUMENTS_PAGE = 1  # the stock documents page in StartView's stack
FIRST_START_PAGE = 0
CARD_COUNT = 5
THUMBNAIL = "thumbnails/Thumbnail.png"
STATES = ("WIP", "In Review", "Released", "Obsolete")  # bruceworx/pdm.py
REF_SUFFIX = ".bwvault"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
KINDS = ("part", "assembly", "drawing", "simulation", "other")
FILTERS = (("All", None), ("Parts", "part"), ("Assemblies", "assembly"), ("Drawings", "drawing"))
POLL_MS = 5000  # the dashboard looks at the vault this often while it is visible (PDM-10)


# ----- recent files -----------------------------------------------------------


def recent_files(group, limit: int = 50) -> list[str]:
    """MRU0, MRU1 ... from the RecentFiles group, in order, without blanks or
    repeats. The group's RecentFiles value is how many it keeps."""
    count = group.GetInt("RecentFiles", 0)
    out = []
    for i in range(max(count, 0) or limit):
        path = group.GetString(f"MRU{i}", "")
        if not path:
            break
        if path not in out:
            out.append(path)
    return out


# ----- reading a .FCStd -------------------------------------------------------


def _value(prop):
    """A string, enumeration or integer property's value from Document.xml."""
    node = prop.find("String")
    if node is not None:
        return node.get("value", "")
    node = prop.find("Integer")
    if node is not None:
        try:
            index = int(node.get("value", "0"))
        except ValueError:
            return ""
        names = [e.get("value", "") for e in prop.findall("CustomEnumList/Enum")]
        names = names or list(STATES)
        return names[index] if 0 <= index < len(names) else ""
    return ""


def _kind(types, item_type) -> str:
    if item_type in ("assembly", "drawing"):
        return item_type
    if any(t.startswith("Assembly::") for t in types):
        return "assembly"
    if any(t.startswith("Fem::FemAnalysis") for t in types):
        return "simulation"
    solid = any(t.startswith(("PartDesign::", "Part::", "Sketcher::")) for t in types)
    if any(t == "TechDraw::DrawPage" for t in types) and not solid:
        return "drawing"
    if solid or "App::Part" in types:
        return "part"
    return "other"


def _rank(type_name) -> int:
    """Which object's part properties stand for the file: the assembly, then
    an App::Part, then a body, then anything else."""
    if type_name.startswith("Assembly::"):
        return 0
    if type_name == "App::Part":
        return 1
    if type_name == "PartDesign::Body":
        return 2
    return 3


def parse_document_xml(data: bytes) -> dict:
    """Part number, revision, lifecycle state and kind from Document.xml."""
    root = ET.fromstring(data)
    types = {}
    for obj in root.findall("Objects/Object"):
        types[obj.get("name", "")] = obj.get("type", "")
    found = []
    for order, obj in enumerate(root.findall("ObjectData/Object")):
        props = {}
        for prop in obj.findall("Properties/Property"):
            name = prop.get("name")
            if name in ("PartNumber", "Revision", "LifecycleState", "ItemType"):
                props[name] = _value(prop).strip()
        if props.get("PartNumber"):
            found.append((_rank(types.get(obj.get("name", ""), "")), order, props))
    best = min(found, key=lambda f: f[:2])[2] if found else {}
    item_type = best.get("ItemType", "")
    return {
        "pn": best.get("PartNumber", ""),
        "rev": best.get("Revision", ""),
        "state": best.get("LifecycleState", ""),
        "item_type": item_type,
        "kind": _kind(set(types.values()), item_type),
    }


def read_fcstd(path: str) -> dict:
    """What the page shows about a file: parse_document_xml's fields plus the
    thumbnail PNG bytes (or None). Unreadable files give empty fields."""
    info = {"pn": "", "rev": "", "state": "", "item_type": "", "kind": "other", "thumbnail": None}
    simulation = str(path).lower().endswith(".vlsim")  # PDM-14: a simulation file is a study whatever it holds
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "Document.xml" in names:
                info.update(parse_document_xml(zf.read("Document.xml")))
            if THUMBNAIL in names:
                info["thumbnail"] = zf.read(THUMBNAIL)
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        info["error"] = str(exc)
    if simulation:
        info["kind"], info["item_type"] = "simulation", "simulation"
    return info


class InfoCache:
    """read_fcstd results by path, kept while the file's mtime and size match."""

    def __init__(self, reader=read_fcstd):
        self.reader = reader
        self._lock = threading.Lock()
        self._items = {}

    @staticmethod
    def key(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def get(self, path):
        """The cached info if the file has not changed since, else None."""
        key = self.key(path)
        with self._lock:
            hit = self._items.get(path)
        return hit[1] if hit and key is not None and hit[0] == key else None

    def load(self, path):
        """The info, read only when the file changed (or was never read)."""
        key = self.key(path)
        if key is None:
            return None
        with self._lock:
            hit = self._items.get(path)
        if hit and hit[0] == key:
            return hit[1]
        info = self.reader(path)
        with self._lock:
            self._items[path] = (key, info)
        return info


# ----- the vault --------------------------------------------------------------


def parse_time(text: str):
    try:
        when = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


def week_start(now: _dt.datetime) -> _dt.datetime:
    """Monday 00:00 of ``now``'s week, in ``now``'s time zone."""
    day = now - _dt.timedelta(days=now.weekday())
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def vault_summary(items, events, actor: str, now: _dt.datetime, eco_items=None) -> dict:
    """The vault box counts.

    ``items``: the rows of FolderVault.status() (state, checked_out_by,
    part_number). ``events``: (op, at, args) from the journal; approve and
    release_eco release parts. ``eco_items``: ECO number -> its part numbers.
    ``now`` must carry a time zone.
    """
    eco_items = eco_items or {}
    since = week_start(now)
    released = set()
    for op, at, args in events:
        when = parse_time(at)
        if when is None or when < since or when > now:
            continue
        if op == "approve":
            released.add(args.get("part_number"))
        elif op == "release_eco":
            released.update(eco_items.get(args.get("number"), ()))
    return {
        "checked_out": sum(1 for row in items if row.get("checked_out_by") == actor),
        "in_review": sum(1 for row in items if row.get("state") == "In Review"),
        "released_week": len(released - {None}),
        "items": len(items),
    }


def vault_signature(root: str):
    """Changes whenever a journal file or a work folder's state changes: the cache key for the vault."""
    folder = os.path.join(root, "journal")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".jsonl"))
        sig = [(n, os.stat(os.path.join(folder, n)).st_mtime, os.stat(os.path.join(folder, n)).st_size)
               for n in names]
    except OSError:
        return ()
    work = os.path.join(root, "work")
    try:
        actors = sorted(os.listdir(work))
    except OSError:
        actors = []
    for actor in actors:
        state = os.path.join(work, actor, ".bw", "state.json")
        try:
            sig.append((state, os.stat(state).st_mtime))
        except OSError:
            pass
    return tuple(sig)


MY_WORK_SECTIONS = (
    ("checked_out", "Checked out to me"),
    ("in_review", "Waiting for review"),
    ("newer", "Newer in the vault"),
    ("ecos_open", "ECOs open"),  # PDM-05: bruceworx_changes.my_work_rows
)


def work_row(status) -> dict:
    """A My work row from a pdm_workspace.FileStatus."""
    return {
        "pn": status.part_number,
        "display": status.display,
        "description": status.description,
        "status": status.label,
        "code": status.code,
        "path": status.path,
        "kind": status.item_type or "part",
    }


def my_work_sections(snapshot) -> list:
    """[(title, rows)] for the non-empty My work groups of a vault snapshot, in page order."""
    work = (snapshot or {}).get("my_work") or {}
    return [(title, work.get(key, [])) for key, title in MY_WORK_SECTIONS if work.get(key)]


def pinned_actor(ref_path: str, saved_for) -> str:
    """The actor pinned for this vault (bruceworx_vaultclient saves it per vault id), or "".

    saved_for(vault_id) returns the saved actor; reading the ref file is cheap (a small JSON).
    """
    try:
        with open(ref_path, encoding="utf-8") as handle:
            vault_id = json.load(handle).get("vault_id", "")
    except (OSError, ValueError):
        return ""
    return saved_for(vault_id) if vault_id else ""


def find_vault_ref(path: str) -> str:
    """The vault's ref file for a folder or ref file, or "" if it is not one."""
    path = os.path.abspath(os.path.expanduser(path or ""))
    if os.path.isfile(path):
        return path if path.endswith(REF_SUFFIX) else ""
    if not os.path.isdir(path):
        return ""
    refs = sorted(n for n in os.listdir(path) if n.endswith(REF_SUFFIX))
    return os.path.join(path, refs[0]) if refs else ""


def vault_folder(vault_path: str, root: str = "") -> str:
    """The vault's folder: its root once read, else the VaultPath setting (a folder or its .bwvault file)."""
    folder = root or os.path.expanduser(vault_path or "")
    if folder.lower().endswith(".bwvault"):
        folder = os.path.dirname(folder)
    return os.path.normpath(folder) if folder else ""


def in_folder(path: str, folder: str) -> bool:
    """True when ``path`` lies inside ``folder`` (the vault's own files and work copies)."""
    if not folder:
        return False
    path, folder = os.path.normpath(os.path.expanduser(path)), os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def read_vault(path: str, actor: str = "", vault=None, options=None, read_ms=None) -> dict:
    """A snapshot of the vault for the page (replays the journal; worker thread).

    actor is the identity pinned for this vault (see pinned_actor); "" uses user@mac. ``vault`` is a
    FolderVault already read (the worker keeps one and reads only new journal lines); ``options``
    go to the dashboard's activity feed (vault_dashboard.snapshot: days, show_all, mine_only, limit).
    The workspace is opened read-only: the worker never writes state.json (PDM-10).
    """
    from bruceworx.pdm_folder import FolderVault  # the hooks add-on

    if vault is None:
        started = time.perf_counter()
        vault = FolderVault(os.path.expanduser(path), actor=actor or None)
        read_ms = (time.perf_counter() - started) * 1000
    status = vault.status()
    kept = getattr(vault, "events", None)  # the vault keeps what it read (PDM-10); older add-ons do not
    events = [(e.op, e.at, e.args) for e in (kept if kept is not None else vault._read_events())]
    ecos = {num: list(eco.items) for num, eco in vault.ecos.items()}
    summary = vault_summary(status["items"], events, vault.actor,
                            _dt.datetime.now().astimezone(), ecos)
    work = _workspace(vault)
    out = {
        "name": status.get("vault") or os.path.basename(vault.root),
        "root": vault.root,
        "summary": summary,
        "parts": {row["part_number"]: {"rev": row["revision"], "state": row["state"]}
                  for row in status["items"]},
        "missing": len(status.get("missing_objects", [])),
        "problems": len(status.get("problems", [])),
        "my_work": _my_work(vault, work),
    }
    try:
        from bruceworx import vault_dashboard
    except ImportError:  # an older hooks add-on: the page as before
        return out
    out["dashboard"] = vault_dashboard.snapshot(vault, work, read_ms=read_ms, **(options or {}))
    return out


def _workspace(vault):
    """The pinned actor's work folder, read-only (None with an older hooks add-on)."""
    try:
        from bruceworx.pdm_workspace import Workspace
    except ImportError:
        return None
    try:
        return Workspace(vault, read_only=True)
    except TypeError:  # before PDM-10
        return Workspace(vault)


def _my_work(vault, work=None) -> dict:
    work = work if work is not None else _workspace(vault)
    if work is None:
        return {}
    work = work.my_work()
    out = {key: [work_row(st) for st in getattr(work, key, [])] for key, _title in MY_WORK_SECTIONS}
    for row in out["in_review"]:
        row["status"] = "In review"  # the reason it is listed, not its file status
    try:
        import bruceworx_changes

        out["ecos_open"] = bruceworx_changes.my_work_rows(vault)
    except Exception:  # noqa: BLE001 -- the page shows the rest
        pass
    return out


def storage_name(root: str) -> str:
    """Where the vault folder lives, as the box's second line says it."""
    low = root.replace("\\", "/").lower()
    if "/mobile documents/com~apple~clouddocs" in low or "/icloud" in low:
        return "iCloud Drive"
    for name in ("Dropbox", "OneDrive", "Google Drive", "Box"):
        if f"/{name.lower()}" in low:
            return name
    return shorten_path(root)


# ----- text ---------------------------------------------------------------------


def _local(ts):
    return _dt.datetime.fromtimestamp(ts)


def format_modified(ts: float, now: _dt.datetime) -> str:
    """Table date: "Today 14:52", "Yesterday", "Oct 16", "Oct 16, 2025"."""
    when = _local(ts)
    days = (now.date() - when.date()).days
    if days == 0:
        return f"Today {when:%H:%M}"
    if days == 1:
        return "Yesterday"
    text = f"{MONTHS[when.month - 1]} {when.day}"
    return text if when.year == now.year else f"{text}, {when.year}"


def format_edited(ts: float, now: _dt.datetime) -> str:
    """Card date: "Edited 12 min ago" within the hour, else as the table."""
    seconds = (now - _local(ts)).total_seconds()
    if 0 <= seconds < 60:
        return "Edited just now"
    if 0 <= seconds < 3600:
        return f"Edited {int(seconds // 60)} min ago"
    return format_modified(ts, now)


def badge(state: str, rev: str = "", short: bool = False):
    """(style key, text) for a lifecycle state badge, or None for no state.
    Cards (short=False) add the revision: "WIP 02", "Rev A", "In review 03"."""
    key = {"WIP": "wip", "In Review": "rev", "Released": "rel", "Obsolete": "obs"}.get(state)
    if key is None:
        return None
    text = {"wip": "WIP", "rev": "In review", "rel": "Released", "obs": "Obsolete"}[key]
    if not short and rev:
        text = f"Rev {rev}" if key == "rel" else (f"{text} {rev}" if key != "obs" else text)
    return key, text


def shorten_path(folder: str, home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    if home and (folder == home or folder.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + folder[len(home.rstrip(os.sep)):]
    return folder


def location_text(path: str, vault_root: str = "", home: str | None = None) -> str:
    """"Vault / Fixtures" inside the vault folder, else the folder, with ~."""
    folder = os.path.dirname(os.path.abspath(path))
    if vault_root:
        root = os.path.abspath(vault_root)
        if folder == root:
            return "Vault"
        if folder.startswith(root + os.sep):
            return "Vault / " + os.path.relpath(folder, root).replace(os.sep, " / ")
    return shorten_path(folder, home)


def merge_vault(info: dict, parts=None) -> dict:
    """P/N, revision and state to show: the vault's record when the part is in
    the open vault, else the file's. ``in_vault`` is None without a vault."""
    pn = info.get("pn", "")
    out = {"pn": pn, "rev": info.get("rev", ""), "state": info.get("state", ""), "in_vault": None}
    if parts is not None:
        record = parts.get(pn) if pn else None
        out["in_vault"] = record is not None
        if record:
            out["rev"], out["state"] = record.get("rev", out["rev"]), record.get("state", out["state"])
    return out


def matches(name: str, pn: str, kind: str, kind_filter=None, text: str = "") -> bool:
    if kind_filter and kind != kind_filter:
        return False
    text = (text or "").strip().lower()
    return not text or text in name.lower() or text in (pn or "").lower()


def example_title(filename: str) -> str:
    """"EngineBlock.FCStd" -> "Engine block", "FEMExample.FCStd" -> "FEM"."""
    stem = os.path.splitext(os.path.basename(filename))[0].replace("_", " ")
    stem = re.sub(r"Example$", "", stem).strip() or stem
    words = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", stem).split()
    out = []
    for i, word in enumerate(words):
        if len(word) > 1 and word.isupper():
            out.append(word)
        else:
            out.append(word.capitalize() if i == 0 else word.lower())
    return " ".join(out)


# ----- GUI ---------------------------------------------------------------------

_installed = False
_observer = None
_groups = []  # the observers live on these wrappers; keep them
_bridge = None
_loader = None
_classes = None
_icons = {}


def enabled() -> bool:
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _log(message):
    try:
        import FreeCAD

        FreeCAD.Console.PrintLog(f"BruceWorx Start page: {message}\n")
    except Exception:
        pass


def _icon(names, workbench=""):
    """The first of ``names`` found in the Qt resources, else the workbench's icon."""
    from PySide import QtCore, QtGui

    key = (tuple(names), workbench)
    if key in _icons and not _icons[key].isNull():
        return _icons[key]
    icon = QtGui.QIcon()
    for name in names:
        path = f":/icons/{name}.svg"
        if QtCore.QFile.exists(path):
            icon = QtGui.QIcon(path)
            break
    if icon.isNull() and workbench:
        try:
            import FreeCADGui

            wb = FreeCADGui.listWorkbenches().get(workbench)
            path = getattr(wb, "Icon", "") if wb else ""
            if isinstance(path, str) and os.path.isfile(path):
                icon = QtGui.QIcon(path)
        except Exception:
            pass
    _icons[key] = icon
    return icon


KIND_ICONS = {
    "part": (("PartDesign_Body", "PartDesignWorkbench"), "PartDesignWorkbench"),
    "assembly": (("AssemblyWorkbench", "Assembly_CreateAssembly"), "AssemblyWorkbench"),
    "drawing": (("actions/TechDraw_PageDefault", "TechDraw_PageDefault", "TechDrawWorkbench"), "TechDrawWorkbench"),
    "simulation": (("FemWorkbench", "FEM_Analysis"), "FemWorkbench"),
    "other": (("document-new",), ""),
}


def _kind_icon(kind):
    names, wb = KIND_ICONS.get(kind, KIND_ICONS["other"])
    return _icon(names, wb)


# Actions --------------------------------------------------------------------------


def _run(*commands):
    import FreeCADGui

    for name in commands:
        FreeCADGui.runCommand(name)


def _post_start():
    """As StartView::postStart: close the Start tab if Mod/Start/closeStart says so."""
    import FreeCAD

    if not FreeCAD.ParamGet(START_PATH).GetBool("closeStart", False):
        return
    for view in _views():
        sub = view.parentWidget()
        while sub is not None and sub.metaObject().className() != "QMdiSubWindow":
            sub = sub.parentWidget()
        if sub is not None:
            sub.close()


def _new_in(workbench, *commands):
    """Std_New, then the workbench and its first commands (StartView::new*File)."""
    import FreeCADGui

    _run("Std_New")
    try:
        FreeCADGui.activateWorkbench(workbench)
    except Exception as exc:
        _log(f"{workbench}: {exc}")
        return
    _run(*commands)
    _post_start()


NEW_ITEMS = (
    ("Part", KIND_ICONS["part"], lambda: _new_in("PartDesignWorkbench", "PartDesign_Body")),
    ("Assembly", KIND_ICONS["assembly"],
     lambda: _new_in("AssemblyWorkbench", "Assembly_CreateAssembly", "Std_Refresh")),
    ("Drawing", KIND_ICONS["drawing"], lambda: _new_in("TechDrawWorkbench", "TechDraw_PageDefault")),
    ("Sketch", (("Sketcher_NewSketch", "general/Sketcher_NewSketch", "SketcherWorkbench"), "SketcherWorkbench"),
     lambda: _new_in("SketcherWorkbench", "Sketcher_NewSketch")),
    ("Simulation study", KIND_ICONS["simulation"], lambda: _new_in("FemWorkbench", "FEM_Analysis")),
)


def open_file(path: str) -> None:
    """Open a recent or example file as the stock cards do (ModuleIO::openFile)."""
    import FreeCADGui
    from PySide import QtWidgets

    if not os.path.isfile(path):
        QtWidgets.QMessageBox.critical(FreeCADGui.getMainWindow(), "File not found",
                                       f"The file '{path}' cannot be opened.")
        return
    try:
        FreeCADGui.loadFile(path)
    except Exception as exc:
        import FreeCAD

        FreeCAD.Console.PrintError(f"{exc}\n")


def _shortcut(command):
    from PySide import QtGui

    try:
        import FreeCADGui

        text = FreeCADGui.Command.get(command).getShortcut()
    except Exception:
        return ""
    return QtGui.QKeySequence(text).toString(QtGui.QKeySequence.NativeText) if text else ""


def open_vault_item(pn: str, path: str = "") -> None:
    """Open a My work row: its work copy, fetched (read-only, latest) first when there is none."""
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    try:
        import bruceworx_vaultclient
    except ImportError:
        bruceworx_vaultclient = None
    # The vault client offers a check-out when the file can be checked out.
    if bruceworx_vaultclient is not None and bruceworx_vaultclient.open_from_vault(pn):
        return
    if path and os.path.isfile(path):
        open_file(path)
        return
    vault_path = FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "").strip()
    try:
        from bruceworx.pdm_folder import FolderVault
        from bruceworx.pdm_workspace import Workspace

        vault = FolderVault(os.path.expanduser(vault_path), actor=_actor_for(vault_path) or None)
        path = Workspace(vault).get_latest(pn)
    except Exception as exc:
        QtWidgets.QMessageBox.warning(FreeCADGui.getMainWindow(), "Open from vault", f"{pn}: {exc}")
        return
    open_file(path)


ACTORS_PATH = "User parameter:BaseApp/Preferences/BruceWorx/VaultActors"


def _actor_for(path: str) -> str:
    """The actor bruceworx_vaultclient pinned for the vault at path, or ""."""
    import FreeCAD

    ref = find_vault_ref(path)
    group = FreeCAD.ParamGet(ACTORS_PATH)
    return pinned_actor(ref, lambda vault_id: group.GetString(vault_id, "")) if ref else ""


def close_vault(parent=None) -> None:
    """Stop using the open vault (the vault folder is not touched)."""
    import FreeCAD

    FreeCAD.ParamGet(PREFS_PATH).SetString("VaultPath", "")


def create_vault(parent=None, folder: str = "") -> None:
    """The Create Vault task dialog (bruceworx_vaultwizard); it sets VaultPath itself."""
    import bruceworx_vaultwizard

    bruceworx_vaultwizard.show_create(folder=folder)


def import_files(parent=None) -> None:
    """The Import Files into Vault task dialog for the open vault."""
    import bruceworx_vaultwizard

    bruceworx_vaultwizard.show_import()


def vault_settings(parent=None) -> None:
    """The Vault Settings task dialog for the open vault."""
    import bruceworx_vaultwizard

    bruceworx_vaultwizard.show_settings()


def open_vault(parent=None) -> None:
    """Pick the vault folder and make it the open vault (BruceWorx/VaultPath).
    A folder that is not a vault yet opens the Create Vault wizard on it."""
    import FreeCAD
    from PySide import QtWidgets

    prefs = FreeCAD.ParamGet(PREFS_PATH)
    start = prefs.GetString("VaultPath", "") or os.path.expanduser("~")
    folder = QtWidgets.QFileDialog.getExistingDirectory(parent, "Open vault", os.path.expanduser(start))
    if not folder:
        return
    if not find_vault_ref(folder):
        try:
            import bruceworx_vaultwizard  # noqa: F401
        except ImportError:
            pass
        else:
            create_vault(parent, folder)
            return
        answer = QtWidgets.QMessageBox.question(
            parent, "Open vault",
            f"{folder} is not a BruceWorx vault: it has no {REF_SUFFIX} file.\n\n"
            "Make it a new vault? Its files, journal and ref file are written into this folder.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            from bruceworx.pdm_folder import FolderVault

            FolderVault.init(folder, os.path.basename(folder.rstrip(os.sep)) or "Vault")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(parent, "Open vault", f"The vault was not created: {exc}")
            return
    prefs.SetString("VaultPath", folder)


# Background reading ----------------------------------------------------------------


class _Loader:
    """One worker thread: reads files and the vault, hands results to the GUI
    thread through the bridge's signals. Requests are coalesced by key."""

    def __init__(self):
        self.cache = InfoCache()
        self.vault_cache = {}  # path -> (signature, snapshot, feed options)
        self.actors = {}  # path -> the actor pinned for that vault
        self.options = {}  # path -> the dashboard's feed options
        self.vaults = {}  # path -> (actor, FolderVault): read again by its new journal lines only
        self._cond = threading.Condition()
        self._queue = []
        self._thread = None

    def request_file(self, path):
        self._put(("file", path))

    def request_vault(self, path, actor="", options=None):
        self.actors[path] = actor
        if options is not None:
            self.options[path] = dict(options)
        self._put(("vault", path))

    def cached_vault(self, path):
        hit = self.vault_cache.get(path)
        ref = find_vault_ref(path)
        if hit and ref and hit[0] == vault_signature(os.path.dirname(ref)) and hit[2] == self.options.get(path, {}):
            return hit[1]
        return None

    def _put(self, job):
        with self._cond:
            if job not in self._queue:
                self._queue.append(job)
            self._cond.notify()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="BruceWorxStart", daemon=True)
                self._thread.start()

    def _work(self):
        while True:
            with self._cond:
                if not self._queue:
                    self._cond.wait(30)
                    if not self._queue:
                        self._thread = None
                        return
                kind, path = self._queue.pop(0)
            try:
                if kind == "file":
                    info = self.cache.load(path)
                    if info is not None and _alive(_bridge):
                        _bridge.fileRead.emit(path, info)
                else:
                    snap = self._vault(path)
                    if _alive(_bridge):
                        _bridge.vaultRead.emit(path, snap)
            except Exception as exc:  # never let the worker die
                _log(f"{path}: {exc}")

    def _vault(self, path):
        """The snapshot: the cached one while nothing changed (a 0.1 ms signature check), else the
        kept FolderVault reads its new journal lines (or replays, see FolderVault.update)."""
        ref = find_vault_ref(path)
        if not ref:
            return {"error": "No vault found at " + path}
        sig = vault_signature(os.path.dirname(ref))
        options = self.options.get(path, {})
        hit = self.vault_cache.get(path)
        syncing = hit and ((hit[1].get("dashboard") or {}).get("health") or {}).get("syncing")
        if hit and hit[0] == sig and hit[2] == options and not syncing:
            return hit[1]
        actor = self.actors.get(path, "")
        try:
            from bruceworx.pdm_folder import FolderVault

            started = time.perf_counter()
            kept = self.vaults.get(path)
            if kept is not None and kept[0] == actor and hasattr(kept[1], "update"):
                vault = kept[1]
                vault.update()
            else:
                vault = FolderVault(os.path.expanduser(path), actor=actor or None)
                self.vaults[path] = (actor, vault)
            read_ms = (time.perf_counter() - started) * 1000
            snap = read_vault(path, actor, vault=vault, options=options, read_ms=read_ms)
        except ImportError:
            snap = {"error": "The vault needs the BruceWorx hooks add-on"}
        except Exception as exc:
            self.vaults.pop(path, None)
            snap = {"error": f"Vault unreadable: {exc}"}
        self.vault_cache[path] = (sig, snap, options)
        return snap


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtGui, QtWidgets

    class Bridge(QtCore.QObject):
        fileRead = QtCore.Signal(str, object)
        vaultRead = QtCore.Signal(str, object)

    class ItemButton(QtWidgets.QPushButton):
        """A row on the left: icon, text and, at the right, the shortcut."""

        def __init__(self, text, icon=None, key="", parent=None):
            super().__init__(text, parent)
            self.setObjectName("bwStartItem")
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setFlat(True)
            if icon is not None:
                self.setIcon(icon)
            self.setIconSize(QtCore.QSize(20, 20))
            if key:
                lay = QtWidgets.QHBoxLayout(self)
                lay.setContentsMargins(0, 0, 10, 0)
                lay.addStretch(1)
                label = QtWidgets.QLabel(key, self)
                label.setObjectName("bwStartKey")
                label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
                lay.addWidget(label)

    def link(text, slot, parent=None):
        button = QtWidgets.QPushButton(text, parent)
        button.setObjectName("bwStartLink")
        button.setFlat(True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def label(text, name, parent=None):
        lab = QtWidgets.QLabel(text, parent)
        lab.setObjectName(name)
        return lab

    def badge_label(state, rev, short, parent=None):
        b = badge(state, rev, short)
        if b is None:
            return None
        lab = label(b[1], "bwStartBadge", parent)
        lab.setProperty("state", b[0])
        lab.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        return lab

    class Card(QtWidgets.QFrame):
        """A recent file: thumbnail, name, P/N with state badge, date."""

        def __init__(self, path, parent=None):
            super().__init__(parent)
            self.path = path
            self.setObjectName("bwStartCard")
            self.setAttribute(QtCore.Qt.WA_Hover)
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setToolTip(path)
            self.setMinimumWidth(120)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(1, 1, 1, 1)
            lay.setSpacing(0)
            self.thumb = label("", "bwStartThumb", self)
            self.thumb.setAlignment(QtCore.Qt.AlignCenter)
            self.thumb.setFixedHeight(128)
            self.thumb.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            lay.addWidget(self.thumb)
            info = QtWidgets.QWidget(self)
            info.setObjectName("bwStartCardInfo")
            self.info = QtWidgets.QVBoxLayout(info)
            self.info.setContentsMargins(10, 7, 10, 9)
            self.info.setSpacing(2)
            lay.addWidget(info)
            self._pixmap = None

        def fill(self, name, pn, state, rev, when, pixmap, kind, missing):
            self.name = label(name, "bwStartCardName", self)
            self.name.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            self.info.addWidget(self.name)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(label(pn or ("Not found" if missing else "No part number"), "bwStartMuted", self))
            b = badge_label(state, rev, False, self)
            if b is not None:
                row.addWidget(b)
            row.addStretch(1)
            self.info.addLayout(row)
            self.info.addWidget(label(when, "bwStartMuted", self))
            self._pixmap = pixmap
            if pixmap is None:
                self.thumb.setPixmap(_kind_icon(kind).pixmap(48, 48))
            self.setEnabled(not missing)
            if missing:
                self.setToolTip(f"Not found: {self.path}")

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if self._pixmap is not None and not self._pixmap.isNull():
                ratio = self.devicePixelRatioF()
                size = QtCore.QSize(self.thumb.width() - 2, self.thumb.height() - 2) * ratio
                pm = self._pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                pm.setDevicePixelRatio(ratio)
                self.thumb.setPixmap(pm)

        def mouseReleaseEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.pos()):
                QtCore.QTimer.singleShot(0, lambda p=self.path: open_file(p))
            super().mouseReleaseEvent(event)

    class Table(QtWidgets.QTreeWidget):
        """Recent files as rows; columns share the width as in the mock."""

        COLUMNS = ("NAME", "PART NUMBER", "REV", "STATE", "MODIFIED", "LOCATION")
        WEIGHTS = (2.3, 1.3, 0.6, 0.9, 1.1, 1.4)
        ROW = 30

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwStartTable")
            self.setColumnCount(len(self.COLUMNS))
            self.setHeaderLabels(list(self.COLUMNS))
            self.setRootIsDecorated(False)
            self.setUniformRowHeights(True)
            self.setIndentation(0)
            self.setIconSize(QtCore.QSize(16, 16))
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.header().setStretchLastSection(False)
            self.header().setSectionsMovable(False)
            self.header().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
            self.header().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            self.itemActivated.connect(self._open)
            self.itemDoubleClicked.connect(self._open)

        def _open(self, item, _col=0):
            path = item.data(0, QtCore.Qt.UserRole)
            if path:
                QtCore.QTimer.singleShot(0, lambda: open_file(path))

        def fit(self):
            rows = self.topLevelItemCount()
            height = self.header().sizeHint().height() + rows * self.ROW + 2 * self.frameWidth() + 1
            self.setFixedHeight(height)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            width = self.viewport().width()
            total = sum(self.WEIGHTS)
            used = 0
            for col, weight in enumerate(self.WEIGHTS[:-1]):
                w = int(width * weight / total)
                self.setColumnWidth(col, w)
                used += w
            self.setColumnWidth(len(self.WEIGHTS) - 1, max(width - used, 40))


    class WorkTable(Table):
        """My work: files checked out to me, waiting for review, newer in the vault."""

        COLUMNS = ("PART NUMBER", "DESCRIPTION", "STATUS")
        WEIGHTS = (1.4, 2.4, 1.6)

        def _open(self, item, _col=0):
            pn = item.data(0, QtCore.Qt.UserRole + 1)
            if pn:
                path = item.data(0, QtCore.Qt.UserRole)
                QtCore.QTimer.singleShot(0, lambda: open_vault_item(pn, path))

        def fill(self, sections):
            self.clear()
            for title, rows in sections:
                head = QtWidgets.QTreeWidgetItem(self)
                head.setText(0, f"{title.upper()}  ·  {len(rows)}")
                head.setFlags(QtCore.Qt.NoItemFlags)
                head.setFirstColumnSpanned(True)
                head.setSizeHint(0, QtCore.QSize(0, self.ROW))
                for row in rows:
                    item = QtWidgets.QTreeWidgetItem(self)
                    item.setText(0, row["display"])
                    item.setIcon(0, _kind_icon(row["kind"]))
                    item.setText(1, row["description"])
                    item.setText(2, row["status"])
                    item.setToolTip(0, row["path"] or "Not in your work folder yet: opening fetches it")
                    item.setData(0, QtCore.Qt.UserRole, row["path"])
                    item.setData(0, QtCore.Qt.UserRole + 1, row["pn"])
                    item.setSizeHint(0, QtCore.QSize(0, self.ROW))
            self.fit()

    class RecentSection(QtWidgets.QWidget):
        """Recent files as cards (and, on the Recent files tab, a table): a title, the
        All / Parts / Assemblies / Drawings filter and a text filter."""

        def __init__(self, page, title, with_table=True, parent=None):
            super().__init__(parent)
            self.page = page
            self.kind_filter = None
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(10)
            self.title = label(title, "bwStartTitle", self)
            head.addWidget(self.title)
            head.addSpacing(4)
            seg = QtWidgets.QFrame(self)
            seg.setObjectName("bwStartSegments")
            seg_lay = QtWidgets.QHBoxLayout(seg)
            seg_lay.setContentsMargins(0, 0, 0, 0)
            seg_lay.setSpacing(0)
            self.seg_group = QtWidgets.QButtonGroup(self)
            for i, (text, kind) in enumerate(FILTERS):
                b = QtWidgets.QPushButton(text, seg)
                b.setObjectName("bwStartSegment")
                b.setCheckable(True)
                b.setChecked(i == 0)
                b.setProperty("position", "first" if i == 0 else ("last" if i == len(FILTERS) - 1 else "mid"))
                b.clicked.connect(lambda _c=False, k=kind: self._set_filter(k))
                self.seg_group.addButton(b)
                seg_lay.addWidget(b)
            head.addWidget(seg)
            head.addStretch(1)
            self.filter_edit = QtWidgets.QLineEdit(self)
            self.filter_edit.setObjectName("bwStartFilter")
            self.filter_edit.setPlaceholderText("Filter by name or P/N")
            self.filter_edit.setClearButtonEnabled(True)
            self.filter_edit.setFixedWidth(260)
            self.filter_edit.textChanged.connect(lambda _t: self.page._timer.start())
            head.addWidget(self.filter_edit)
            lay.addLayout(head)
            self.cards_box = QtWidgets.QWidget(self)
            self.cards = QtWidgets.QGridLayout(self.cards_box)
            self.cards.setContentsMargins(0, 0, 0, 0)
            self.cards.setHorizontalSpacing(14)
            for col in range(CARD_COUNT):
                self.cards.setColumnStretch(col, 1)
            lay.addWidget(self.cards_box)
            self.table = Table(self) if with_table else None
            if self.table is not None:
                lay.addWidget(self.table)
            self.empty = label("No recent files yet. Files you open or save are listed here.", "bwStartMuted", self)
            self.empty.setWordWrap(True)
            lay.addWidget(self.empty)

        def _set_filter(self, kind):
            self.kind_filter = kind
            self.page._timer.start()

        def render(self, entries, folder, parts, vault_root, now):
            """Draw ``entries`` [(path, exists, mtime)]; returns (shown, total)."""
            page = self.page
            rows = []
            text = self.filter_edit.text()
            for path, exists, mtime in entries:
                info = page.infos.get(path, {})
                shown = merge_vault(info, parts)
                name = os.path.basename(path)
                if not matches(name, shown["pn"], info.get("kind", "other"), self.kind_filter, text):
                    continue
                rows.append((path, exists, mtime, info, shown))

            while self.cards.count():
                item = self.cards.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            for col, (path, exists, mtime, info, shown) in enumerate(rows[:CARD_COUNT]):
                card = Card(path, self.cards_box)
                when = format_edited(mtime, now) if exists else "Not found"
                card.fill(os.path.splitext(os.path.basename(path))[0], shown["pn"], shown["state"],
                          shown["rev"], when, page._pixmap(path, mtime, info), info.get("kind", "other"),
                          not exists)
                self.cards.addWidget(card, 0, col)
            self.cards_box.setVisible(bool(rows))

            if self.table is not None:
                self.table.clear()
                for path, exists, mtime, info, shown in rows:
                    item = QtWidgets.QTreeWidgetItem(self.table)
                    item.setText(0, os.path.basename(path))
                    item.setIcon(0, _kind_icon(info.get("kind", "other")))
                    item.setText(1, shown["pn"] or "—")
                    item.setText(2, shown["rev"])
                    item.setText(4, format_modified(mtime, now) if exists else "Not found")
                    item.setText(5, location_text(path, vault_root))
                    item.setToolTip(0, path if exists else f"Not found: {path}")
                    item.setToolTip(5, os.path.dirname(path))
                    item.setData(0, QtCore.Qt.UserRole, path if exists else "")
                    item.setSizeHint(0, QtCore.QSize(0, Table.ROW))
                    if not exists:
                        item.setDisabled(True)
                    b = badge_label(shown["state"], shown["rev"], True)
                    if b is not None and exists:
                        cell = QtWidgets.QWidget()
                        cell.setObjectName("bwStartCell")
                        lay = QtWidgets.QHBoxLayout(cell)
                        lay.setContentsMargins(4, 0, 0, 0)
                        lay.addWidget(b)
                        lay.addStretch(1)
                        self.table.setItemWidget(item, 3, cell)
                    elif shown["in_vault"] is False:
                        item.setText(3, "Not in vault")
                self.table.fit()
                self.table.setVisible(bool(rows))
            self.empty.setVisible(not rows)
            if folder:
                self.empty.setText("No vault files opened yet. Files you open from this vault are listed here; "
                                   "files outside it are not." if not entries else "No vault file matches the filter.")
            else:
                self.empty.setText("No recent files yet. Files you open or save are listed here."
                                   if not entries else "No recent file matches the filter.")
            return len(rows), len(entries)

    class Page(QtWidgets.QWidget):
        """The BruceWorx page in StartView's stack."""

        def __init__(self, stack, parent=None):
            super().__init__(parent)
            self.stack = stack
            self.setObjectName(PAGE_NAME)
            self.entries = []  # (path, exists, mtime)
            self.infos = {}
            self.pixmaps = {}  # (path, mtime) -> QPixmap
            self.vault = None
            self.tab = ""  # "dashboard" or "recent" once picked by hand (PDM-10); "" follows the vault
            self._tab_vault = None  # the vault the hand-picked tab was picked for
            self._checked_at = None  # when the worker last looked at the vault
            self._drawn = None  # the dashboard snapshot on screen
            self._timer = QtCore.QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(60)
            self._timer.timeout.connect(self._render)
            # PDM-10: look at the vault every 5 s while the page is visible (a signature check;
            # the journal's new lines when it changed).
            self._poll = QtCore.QTimer(self)
            self._poll.setInterval(POLL_MS)
            self._poll.timeout.connect(self._poll_vault)
            self._build()

        # layout
        def _build(self):
            outer = QtWidgets.QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            side = QtWidgets.QFrame(self)
            side.setObjectName("bwStartSide")
            side.setFixedWidth(248)
            s = QtWidgets.QVBoxLayout(side)
            s.setContentsMargins(14, 18, 14, 14)
            s.setSpacing(2)
            s.addWidget(label("NEW", "bwStartCaption", side))
            for text, (names, wb), action in NEW_ITEMS:
                button = ItemButton(text, _icon(names, wb), "", side)
                button.clicked.connect(lambda _c=False, a=action: QtCore.QTimer.singleShot(0, a))
                s.addWidget(button)
            s.addSpacing(10)
            s.addWidget(label("OPEN", "bwStartCaption", side))
            opener = ItemButton("Open file…", _icon(("document-open",)), _shortcut("Std_Open"), side)
            opener.clicked.connect(lambda: QtCore.QTimer.singleShot(0, self._open))
            s.addWidget(opener)
            importer = ItemButton("Import STEP / STL…", _icon(("Std_Import",)), "", side)
            importer.clicked.connect(lambda: QtCore.QTimer.singleShot(0, lambda: _run("Std_Import")))
            s.addWidget(importer)
            s.addSpacing(10)
            s.addWidget(label("VAULT", "bwStartCaption", side))
            # The open vault, then one row for each vault beside it (Vector and Standard Libraries).
            self.vault_button = QtWidgets.QToolButton(side)
            self.vault_button.setObjectName("bwStartVaultSwitch")
            self.vault_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.vault_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.vault_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.vault_button.setCursor(QtCore.Qt.PointingHandCursor)
            self.vault_menu = QtWidgets.QMenu(self.vault_button)
            self.vault_button.setMenu(self.vault_menu)
            s.addWidget(self.vault_button)
            self.vault_places = QtWidgets.QWidget(side)
            self.vault_places.setObjectName("bwStartVaultPlaces")
            self.vault_places_layout = QtWidgets.QVBoxLayout(self.vault_places)
            self.vault_places_layout.setContentsMargins(0, 0, 0, 0)
            self.vault_places_layout.setSpacing(2)
            self._vault_place_sig = None
            self.vault_places.hide()
            s.addWidget(self.vault_places)
            s.addStretch(1)
            started = ItemButton("Getting started", _icon(("help-browser",)), "", side)
            started.clicked.connect(lambda: self.stack.setCurrentIndex(FIRST_START_PAGE))
            s.addWidget(started)
            settings = ItemButton("Settings", _icon(("preferences-system",)), "", side)
            settings.clicked.connect(lambda: QtCore.QTimer.singleShot(0, lambda: _run("Std_DlgPreferences")))
            s.addWidget(settings)
            outer.addWidget(side)

            scroll = QtWidgets.QScrollArea(self)
            scroll.setObjectName("bwStartScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.scroll = scroll
            main = QtWidgets.QWidget(scroll)
            main.setObjectName("bwStartMain")
            m = QtWidgets.QVBoxLayout(main)
            m.setContentsMargins(24, 14, 24, 18)
            m.setSpacing(14)

            # The two tabs of the page (PDM-10, the user's answer of 24 Oct): the vault's dashboard
            # and the recent files on this Mac.
            self.tabs_box = QtWidgets.QFrame(main)
            self.tabs_box.setObjectName("bwStartTabs")
            tl = QtWidgets.QHBoxLayout(self.tabs_box)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(18)
            self.tab_group = QtWidgets.QButtonGroup(self)
            self.tab_buttons = {}
            for key, text in bwdash.TABS:
                b = QtWidgets.QPushButton(text, self.tabs_box)
                b.setObjectName("bwStartTab")
                b.setCheckable(True)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(lambda _c=False, k=key: self.set_tab(k))
                self.tab_group.addButton(b)
                self.tab_buttons[key] = b
                tl.addWidget(b)
            tl.addStretch(1)
            self.tabs_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
            m.addWidget(self.tabs_box)

            # Tab 1: the dashboard, or what to do without a vault.
            self.dash_box = QtWidgets.QWidget(main)
            d = QtWidgets.QVBoxLayout(self.dash_box)
            d.setContentsMargins(0, 0, 0, 0)
            d.setSpacing(14)
            self.dashboard = bwdash.classes()["Dashboard"](self, self.dash_box)
            d.addWidget(self.dashboard)
            self.vault_recent = RecentSection(self, "Recent in this vault", False, self.dashboard.recent_box)
            self.dashboard.recent.addWidget(self.vault_recent)
            self.vault_count = label("", "bwStartMuted", self.dashboard.recent_box)
            self.dashboard.recent.addWidget(self.vault_count)
            self.dash_wait = label("", "bwStartMuted", self.dash_box)
            self.dash_wait.setWordWrap(True)
            d.addWidget(self.dash_wait)
            self.no_vault = QtWidgets.QFrame(self.dash_box)
            self.no_vault.setObjectName("bwDashCard")
            nv = QtWidgets.QVBoxLayout(self.no_vault)
            nv.setContentsMargins(18, 16, 18, 16)
            nv.setSpacing(8)
            nv.addWidget(label("No vault is open", "bwDashName", self.no_vault))
            text = label("A vault keeps part numbers, revisions, check-out and release in one folder (iCloud Drive "
                         "works). Its dashboard shows here: what is in it, your work, changes and activity.",
                         "bwStartMuted", self.no_vault)
            text.setWordWrap(True)
            nv.addWidget(text)
            row = QtWidgets.QHBoxLayout()
            for text, slot, name in (("Open Vault…", lambda: open_vault(self), "bwDashButton"),
                                     ("Create Vault…", lambda: create_vault(self), "bwDashPrimary")):
                b = QtWidgets.QPushButton(text, self.no_vault)
                b.setObjectName(name)
                b.clicked.connect(lambda _c=False, f=slot: QtCore.QTimer.singleShot(0, f))
                row.addWidget(b)
            row.addStretch(1)
            nv.addLayout(row)
            d.addWidget(self.no_vault)
            m.addWidget(self.dash_box)

            # Tab 2: the vault's files as one tree (the list that floated over the window until 25 Oct;
            # the Structure tab folded into it), made when first shown with a vault open.
            self.files_box = QtWidgets.QWidget(main)
            self.files_layout = QtWidgets.QVBoxLayout(self.files_box)
            self.files_layout.setContentsMargins(0, 0, 0, 0)
            self.files_panel = None
            m.addWidget(self.files_box)

            # Tab 2: recent files (before PDM-10 the whole page: My work, then the recent list).
            self.recent_box = QtWidgets.QWidget(main)
            r = QtWidgets.QVBoxLayout(self.recent_box)
            r.setContentsMargins(0, 0, 0, 0)
            r.setSpacing(14)
            self.work_box = QtWidgets.QWidget(self.recent_box)
            w = QtWidgets.QVBoxLayout(self.work_box)
            w.setContentsMargins(0, 0, 0, 8)
            w.setSpacing(8)
            w.addWidget(label("My work", "bwStartTitle", self.work_box))
            self.work_table = WorkTable(self.work_box)
            w.addWidget(self.work_table)
            self.work_empty = label("Nothing is checked out to you, waiting for review or newer in the vault.",
                                    "bwStartMuted", self.work_box)
            w.addWidget(self.work_empty)
            r.addWidget(self.work_box)
            self.recent = RecentSection(self, "Recent", True, self.recent_box)
            r.addWidget(self.recent)
            foot = QtWidgets.QHBoxLayout()
            foot.setSpacing(4)
            self.count_label = label("", "bwStartMuted", self.recent_box)
            foot.addWidget(self.count_label)
            self.examples_box = QtWidgets.QWidget(self.recent_box)
            ex = QtWidgets.QHBoxLayout(self.examples_box)
            ex.setContentsMargins(0, 0, 0, 0)
            ex.setSpacing(4)
            ex.addWidget(label("·  Examples:", "bwStartMuted", self.examples_box))
            examples = self._examples()
            for i, path in enumerate(examples):
                if i:
                    ex.addWidget(label("·", "bwStartMuted", self.examples_box))
                ex.addWidget(link(example_title(path), lambda _c=False, p=path: open_file(p), self.examples_box))
            self.examples_box.setVisible(bool(examples))
            foot.addWidget(self.examples_box)
            foot.addStretch(1)
            self.show_box = QtWidgets.QCheckBox("Show this page at startup", self.recent_box)
            self.show_box.setObjectName("bwStartShowOnStartup")
            self.show_box.toggled.connect(self._show_on_startup)
            foot.addWidget(self.show_box)
            r.addLayout(foot)
            m.addWidget(self.recent_box)
            m.addStretch(1)
            scroll.setWidget(main)
            outer.addWidget(scroll, 1)

        def _examples(self):
            import FreeCAD

            if not FreeCAD.ParamGet(START_PATH).GetBool("ShowExamples", True):
                return []
            folder = os.path.join(FreeCAD.getResourceDir(), "examples")
            try:
                names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".fcstd"))
            except OSError:
                return []
            return [os.path.join(folder, n) for n in names]

        # actions
        def _open(self):
            _run("Std_Open")

        def _show_on_startup(self, checked):
            import FreeCAD

            FreeCAD.ParamGet(START_PATH).SetBool("ShowOnStartup", bool(checked))
            # Keep the stock page's (inverted) box in step.
            stock = self.stack.widget(DOCUMENTS_PAGE) if _alive(self.stack) else None
            if stock is not None:
                for box in stock.findChildren(QtWidgets.QCheckBox):
                    box.blockSignals(True)
                    box.setChecked(not checked)
                    box.blockSignals(False)

        def set_tab(self, key):
            """Pick a tab by hand; it stays until the vault changes."""
            self.tab = key
            self._tab_vault = (self.vault or {}).get("path")
            self._render()
            self.scroll.verticalScrollBar().setValue(0)

        def _ensure_files(self):
            """The vault's file list, made the first time the Files tab shows with a vault open."""
            if self.files_panel is not None and _alive(self.files_panel):
                return
            try:
                import bruceworx_vaultclient as vc
            except ImportError:
                return
            if vc.session() is None:
                return
            panel = vc._classes()["VaultPanel"](self.files_box)
            panel.setMinimumHeight(560)
            self.files_layout.addWidget(panel)
            self.files_panel = panel
            vc.register_panel(panel)

        def current_tab(self):
            path = (self.vault or {}).get("path")
            chosen = self.tab if self._tab_vault == path else ""
            return bwdash.default_tab(self.vault is not None, chosen)

        def request_feed(self, options):
            """The dashboard's feed range or filter changed: read the snapshot again with them."""
            if self.vault is not None:
                _loader.request_vault(self.vault["path"], _actor_for(self.vault["path"]), options)

        def checked_seconds(self):
            if self._checked_at is None:
                return None
            return max(0.0, time.monotonic() - self._checked_at)

        def _poll_vault(self):
            if not self.isVisible() or self.vault is None:
                self._poll.stop()
                return
            _loader.request_vault(self.vault["path"], _actor_for(self.vault["path"]))
            self.dashboard.update_header(self.checked_seconds())

        # data
        def refresh(self):
            """Read the recent list and the vault again; parse on the worker."""
            import FreeCAD

            self.show_box.blockSignals(True)
            self.show_box.setChecked(FreeCAD.ParamGet(START_PATH).GetBool("ShowOnStartup", True))
            self.show_box.blockSignals(False)
            entries = []
            for path in recent_files(FreeCAD.ParamGet(RECENT_PATH)):
                key = InfoCache.key(path)
                entries.append((path, key is not None, key[0] if key else 0))
                if key is not None:
                    info = _loader.cache.get(path)
                    if info is not None:
                        self.infos[path] = info
                    else:
                        self.infos.pop(path, None)
                        _loader.request_file(path)
            self.entries = entries
            path = FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "").strip()
            if path:
                snap = _loader.cached_vault(path)
                if self.vault is not None and self.vault.get("path") == path and snap is None:
                    pass  # keep the last snapshot on screen while a new one is read (PDM-10)
                else:
                    self.vault = dict(snap, path=path) if snap else {"path": path, "loading": True}
                if snap is None:
                    _loader.request_vault(path, _actor_for(path))
                else:
                    self._checked_at = time.monotonic()
                if self.isVisible() and bwdash.enabled() and not self._poll.isActive():
                    self._poll.start()
            else:
                self.vault = None
                self._poll.stop()
            self._render()

        def file_read(self, path, info):
            if any(p == path for p, _e, _m in self.entries):
                self.infos[path] = info
                self._timer.start()

        def vault_read(self, path, snap):
            if self.vault is not None and self.vault.get("path") == path:
                self._checked_at = time.monotonic()
                if self.vault.get("dashboard") is not None and snap.get("dashboard") is self.vault.get("dashboard"):
                    self.dashboard.update_header(self.checked_seconds())
                    return  # nothing changed: only the "read ... ago" line moves
                self.vault = dict(snap, path=path)
                self._timer.start()

        # drawing
        def _render(self):
            import datetime

            now = datetime.datetime.now()
            self._render_vault()
            parts = self.vault.get("parts") if self.vault and "parts" in self.vault else None
            vault_root = self.vault.get("root", "") if self.vault else ""
            folder = vault_folder(self.vault.get("path", ""), vault_root) if self.vault else ""
            # With a vault open the page is the vault's: files from elsewhere are not listed (24 Oct).
            vault_entries = [e for e in self.entries if in_folder(e[0], folder)] if folder else []
            dashboard = bwdash.enabled()
            tab = self.current_tab() if dashboard else "recent"
            self.tabs_box.setVisible(dashboard)
            for key, button in self.tab_buttons.items():
                button.setChecked(key == tab)
            self.dash_box.setVisible(tab == "dashboard")
            self.recent_box.setVisible(tab == "recent")
            self.files_box.setVisible(tab == "files")
            if tab == "files":
                self._ensure_files()
            if dashboard:
                # The Recent files tab: every recent file on this Mac (the user's answer of 24 Oct).
                self.work_box.setVisible(False)
                self.recent.title.setText("Recent files")
                shown, total = self.recent.render(self.entries, "", parts, vault_root, now)
                word = "recent file"
            else:
                # Before PDM-10: My work above the recent list, kept to the vault folder.
                self.recent.title.setText("Recent")
                entries = vault_entries if folder else self.entries
                shown, total = self.recent.render(entries, folder, parts, vault_root, now)
                word = "vault file" if folder else "recent file"
            self.count_label.setText(f"Showing {shown} of {total} {word}{'s' if total != 1 else ''}")
            if tab == "dashboard":
                self._render_dashboard(vault_entries, folder, parts, vault_root, now)

        def _render_dashboard(self, entries, folder, parts, vault_root, now):
            snap = (self.vault or {}).get("dashboard")
            self.no_vault.setVisible(self.vault is None)
            self.dashboard.setVisible(snap is not None)
            waiting = self.vault is not None and snap is None
            self.dash_wait.setVisible(waiting)
            if waiting:
                if self.vault.get("error"):
                    self.dash_wait.setText(self.vault["error"])
                elif self.vault.get("loading"):
                    self.dash_wait.setText("Reading the vault…")
                else:
                    self.dash_wait.setText("The dashboard needs a newer BruceWorx hooks add-on.")
                return
            if snap is None:
                return
            if snap is not self._drawn:
                self.dashboard.fill(snap, self.checked_seconds())
                self._drawn = snap
            else:
                self.dashboard.update_header(self.checked_seconds())
            shown, total = self.vault_recent.render(entries, folder, parts, vault_root, now)
            self.vault_recent.setVisible(bool(total))
            if total:
                self.vault_count.setText(f"Showing {shown} of {total} vault file{'s' if total != 1 else ''} "
                                         "· only files in this vault are listed while it is open")
            else:
                self.vault_count.setText("No vault files opened yet. Files you open from this vault are listed "
                                         "here; files outside it are not.")

        def _pixmap(self, path, mtime, info):
            data = info.get("thumbnail")
            if not data:
                return None
            key = (path, mtime)
            if key not in self.pixmaps:
                pm = QtGui.QPixmap()
                pm.loadFromData(data, "PNG")
                self.pixmaps[key] = pm if not pm.isNull() else None
            return self.pixmaps[key]

        def _render_vault(self):
            vault = self.vault
            menu = self.vault_menu
            menu.clear()
            self._fill_vault_places(vault)
            if vault is None:
                self.vault_button.setText("No vault open  ▾")
                self.vault_button.setToolTip("Part numbers, check-out and release live in a vault folder.")
            else:
                name = vault.get("name") or os.path.basename(vault.get("path", "").rstrip(os.sep)) or "Vault"
                self.vault_button.setText(f"{name}  ▾")
                where = storage_name(vault.get("root", "")) if vault.get("root") else vault.get("path", "")
                tip = [where]
                if vault.get("missing"):
                    tip.append(f"{vault['missing']} file(s) still syncing")
                if vault.get("error"):
                    tip.append(vault["error"])
                self.vault_button.setToolTip("\n".join(t for t in tip if t))
            self.vault_button.setIcon(_icon(("BruceWorx_Vault", "folder"), ""))
            if vault is not None and bwdash.enabled():
                menu.addAction("Dashboard", lambda: self.set_tab("dashboard"))
                menu.addSeparator()
            menu.addAction("Open Vault…", lambda: open_vault(self))
            menu.addAction("Create Vault…", lambda: create_vault(self))
            if vault is not None:
                menu.addSeparator()
                menu.addAction("Import Files…", lambda: import_files(self))
                menu.addAction("Vault Settings…", lambda: vault_settings(self))
                menu.addSeparator()
                menu.addAction("Close Vault", lambda: close_vault(self))
            # My work (the page before PDM-10): only with a vault that has been read.
            ready = vault is not None and not vault.get("loading") and not vault.get("error")
            self.work_box.setVisible(ready and not bwdash.enabled())
            if ready and not bwdash.enabled():
                sections = my_work_sections(vault)
                self.work_table.fill(sections)
                self.work_table.setVisible(bool(sections))
                self.work_empty.setVisible(not sections)

        def _fill_vault_places(self, vault):
            """One sidebar row for each vault beside the open one."""
            import bruceworx_vaultclient as vc

            others = []
            if vault is not None:
                root = vault.get("root") or vault.get("path") or ""
                others = vc.other_places(root) if root else []
            sig = tuple(others)
            if sig == self._vault_place_sig:
                return
            self._vault_place_sig = sig
            lay = self.vault_places_layout
            while lay.count():
                widget = lay.takeAt(0).widget()
                if widget is not None:
                    widget.deleteLater()
            self.vault_places.setVisible(bool(others))
            for name, folder in others:
                button = QtWidgets.QPushButton(name, self.vault_places)
                button.setObjectName("bwStartVaultPlace")
                button.setIcon(_icon(("BruceWorx_Vault", "folder"), ""))
                button.setIconSize(QtCore.QSize(16, 16))
                button.setCursor(QtCore.Qt.PointingHandCursor)
                button.setFlat(True)
                button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                button.setToolTip(f"Open {name}")
                button.clicked.connect(lambda _c=False, f=folder: vc.use_place(f))
                lay.addWidget(button)

        def showEvent(self, event):
            super().showEvent(event)
            QtCore.QTimer.singleShot(0, self.refresh)

        def hideEvent(self, event):
            super().hideEvent(event)
            self._poll.stop()

    _classes = {"Bridge": Bridge, "Page": Page, "Card": Card, "Table": Table, "RecentSection": RecentSection}
    return _classes


# Attaching to StartView -------------------------------------------------------------


def _main_window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _views():
    from PySide import QtWidgets

    mw = _main_window()
    return [v for v in mw.findChildren(QtWidgets.QWidget, VIEW_NAME)] if mw else []


def _stack(view):
    from PySide import QtWidgets

    central = view.centralWidget() if hasattr(view, "centralWidget") else None
    if isinstance(central, QtWidgets.QStackedWidget):
        return central
    return view.findChild(QtWidgets.QStackedWidget)


def _page(stack):
    for i in range(stack.count()):
        if stack.widget(i).objectName() == PAGE_NAME:
            return stack.widget(i)
    return None


def _on_page_changed(stack, index):
    """StartView went back to its documents page by itself: show ours."""
    if index == DOCUMENTS_PAGE and _alive(stack) and enabled():
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, lambda: _select(stack))


def _select(stack):
    if not _alive(stack) or not enabled():
        return
    page = _page(stack)
    if page is not None and stack.currentIndex() == DOCUMENTS_PAGE:
        stack.setCurrentWidget(page)


def attach(view) -> bool:
    """Add the page to ``view``'s stack (once) and select it unless the
    first-start page is showing. Returns False if the view is not as expected."""
    stack = _stack(view)
    if stack is None or stack.count() < 2:
        _log("StartView has no page stack; the stock page stays")
        return False
    page = _page(stack)
    if page is None:
        page = _make_classes()["Page"](stack)
        stack.addWidget(page)
        stack.currentChanged.connect(lambda index, s=stack: _on_page_changed(s, index))
    if stack.currentIndex() == DOCUMENTS_PAGE:
        stack.setCurrentWidget(page)
    return True


def detach(view) -> None:
    """Show the stock documents page again (the page stays in the stack)."""
    stack = _stack(view)
    if stack is not None and _page(stack) is not None and stack.currentWidget() is _page(stack):
        stack.setCurrentIndex(DOCUMENTS_PAGE)


def update(*_args) -> None:
    for view in _views():
        if not _alive(view):
            continue
        if enabled():
            attach(view)
        else:
            detach(view)


def _pages():
    out = []
    for view in _views():
        stack = _stack(view)
        page = _page(stack) if stack is not None else None
        if page is not None and _alive(page):
            out.append(page)
    return out


def _refresh_pages():
    for page in _pages():
        if page.isVisible():
            page.refresh()


def _vault_changed():
    """A vault operation in this FreeCAD (bruceworx_vaultclient listeners): read the vault now."""
    for page in _pages():
        if page.isVisible() and page.vault is not None:
            _loader.request_vault(page.vault["path"], _actor_for(page.vault["path"]))


def show_dashboard() -> None:
    """Bring the Start tab forward on the vault's dashboard (the Vault panel's menu, PDM-10)."""
    show_tab("dashboard")


def show_tab(key: str) -> None:
    """Bring the Start tab forward on one of its tabs ("dashboard", "files", "recent")."""
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    if not _pages():
        try:
            FreeCADGui.runCommand("Start_Start")
        except Exception as exc:
            _log(f"Start_Start: {exc}")

    def bring():
        for view in _views():
            sub = view.parentWidget()
            while sub is not None and not isinstance(sub, QtWidgets.QMdiSubWindow):
                sub = sub.parentWidget()
            if sub is not None and sub.mdiArea() is not None:
                sub.mdiArea().setActiveSubWindow(sub)
        for page in _pages():
            page.set_tab(key)

    QtCore.QTimer.singleShot(150, bring)


class _PrefObserver:
    def __init__(self):
        self._timer = None

    def onChange(self, _group, name):
        from PySide import QtCore

        # A change in a sub-group (e.g. VaultActors/<id>) arrives with no name.
        name = name or ""
        if name == PREF_NAME:
            QtCore.QTimer.singleShot(0, update)
        elif name in ("VaultPath", "ShowOnStartup") or name.startswith("MRU") or name == "RecentFiles":
            # RecentFiles is cleared and written one key at a time: coalesce.
            if self._timer is None:
                self._timer = QtCore.QTimer()
                self._timer.setSingleShot(True)
                self._timer.setInterval(150)
                self._timer.timeout.connect(_refresh_pages)
            self._timer.start()


def install() -> None:
    """Watch for StartView (made by Start_Start shortly after start-up, and
    again whenever the Start tab is reopened) and put the page in."""
    global _installed, _observer, _bridge, _loader
    if _installed:
        return
    import FreeCAD
    from PySide import QtCore, QtWidgets

    classes = _make_classes()
    _bridge = classes["Bridge"]()
    _loader = _Loader()
    _bridge.fileRead.connect(lambda path, info: [p.file_read(path, info) for p in _pages()])
    _bridge.vaultRead.connect(lambda path, snap: [p.vault_read(path, snap) for p in _pages()])

    _observer = _PrefObserver()
    for path in (PREFS_PATH, RECENT_PATH, START_PATH):
        group = FreeCAD.ParamGet(path)
        # Keep the handle: a collected wrapper detaches its observer.
        group.Attach(_observer)
        _groups.append(group)

    def start(tries=50):
        mw = _main_window()
        area = mw.findChild(QtWidgets.QMdiArea) if mw else None
        if area is None:
            if tries:
                QtCore.QTimer.singleShot(200, lambda: start(tries - 1))
            return
        area.subWindowActivated.connect(lambda _sub: QtCore.QTimer.singleShot(0, update))
        update()
        try:  # the dashboard follows this FreeCAD's own vault operations at once (PDM-10)
            import bruceworx_vaultclient

            bruceworx_vaultclient.add_listener(_vault_changed)
        except ImportError:
            pass

    QtCore.QTimer.singleShot(0, start)
    _installed = True
