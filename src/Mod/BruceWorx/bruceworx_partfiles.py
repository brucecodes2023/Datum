# SPDX-License-Identifier: LGPL-2.1-or-later
"""One part per file, and sub-assemblies (roadmap ASM-01 and ASM-05).

FreeCAD already stores a link to another file relative to the document that holds it, so an assembly
folder moved as a whole opens intact. What this module adds:

* make_external(obj): a body (or solid, or part container) modelled inside an assembly or a multi-body
  file becomes its own .FCStd next to that file and is replaced, in place, by a link to it. The link
  takes the body's placement; joints, grounds and anything else that pointed at the body point at the
  link. The change to the open document is one undo step; the new file stays on disk when it is undone.
* repair(doc): after an open with links that could not be restored, each missing file is looked for by
  its file name under the document's folder (then one folder up), the link is pointed at it, and the
  report says what was re-pointed, what is still missing and what was ambiguous. With the GUI this runs
  by itself when a document with broken links is opened, and tells the user.
* link_report(path): the links a saved file holds, and which are absolute (they would break on a move).
* insert(assembly, path, rigid): a part file comes in as a link, an assembly file as a sub-assembly
  (the stock Assembly::AssemblyLink), rigid or flexible.
* set_flexible(link, on): the per-instance switch between a rigid sub-assembly (moves as one body) and a
  flexible one (its joints are solved in the parent). It is the stock Rigid property; this is the
  "fix-up" of the roadmap row: one undo step, parent recomputed.

Commands (install()): Make External, Repair Links, Flexible Sub-assembly.
Off switch: Preferences/BruceWorx/PartFiles (the commands go inactive and nothing runs on open);
RepairLinksOnOpen switches only the automatic repair.
"""

from __future__ import annotations

import os
import re
import zipfile
from xml.etree import ElementTree

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "PartFiles"
PREF_ON_OPEN = "RepairLinksOnOpen"
CMD_EXTERNAL = "BruceWorx_MakeExternal"
CMD_REPAIR = "BruceWorx_RepairLinks"
CMD_FLEXIBLE = "BruceWorx_FlexibleSubAssembly"
ICON_EXTERNAL = "BruceWorx_MakeExternal.svg"
ICON_REPAIR = "BruceWorx_RepairLinks.svg"
ICON_FLEXIBLE = "BruceWorx_FlexibleSubAssembly.svg"
MAX_SEARCHED = 20000  # files looked at for one repair
SKIPPED_DIRS = {".git", ".pixi", "__pycache__", "node_modules"}
EXTERNAL_KINDS = ("PartDesign::Body", "App::Part", "Part::Feature")
_state = {"observer": None, "seen": set(), "box": None}


class PartFilesError(Exception):
    """Something to tell the user in one sentence."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled():
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


def repair_on_open():
    return enabled() and bool(_app().ParamGet(PREFS_PATH).GetBool(PREF_ON_OPEN, True))


# ----- the links a file holds -----------------------------------------------------------------------


def xlinks(path):
    """[{"object", "property", "file", "name"}] for every link to another file stored in ``path``
    (read from the file itself; nothing is opened in FreeCAD)."""
    out = []
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("Document.xml"))
    for obj in root.iter("Object"):
        name = obj.get("name")
        props = obj.find("Properties")
        if not name or props is None:
            continue
        for prop in props.findall("Property"):
            for node in prop.iter():
                if node.tag in ("XLink", "XLinkSub", "XLinkSubList") and node.get("file"):
                    out.append({"object": name, "property": prop.get("name"), "file": node.get("file"),
                                "name": node.get("name", ""), "simple": node.tag == "XLink" and node is prop.find("XLink")})
    return out


def link_report(path):
    """{"links": [...], "absolute": [...], "missing": [...]} for a saved file: absolute links break when
    the folder moves; missing ones have no file where the link says."""
    folder = os.path.dirname(os.path.abspath(path))
    links = xlinks(path)
    absolute = [link for link in links if os.path.isabs(link["file"])]
    missing = [link for link in links if not os.path.isfile(os.path.join(folder, link["file"]))]
    return {"links": links, "absolute": absolute, "missing": missing}


def _is_broken(obj, prop):
    try:
        return getattr(obj, prop) is None
    except Exception:  # noqa: BLE001
        return False


def broken_links(doc):
    """The links of an open document that were not restored: [{"object", "property", "file", "name"}]."""
    if not doc.FileName or not os.path.isfile(doc.FileName):
        return []
    out = []
    for link in xlinks(doc.FileName):
        obj = doc.getObject(link["object"])
        if obj is not None and link["simple"] and _is_broken(obj, link["property"]):
            out.append(link)
    return out


def _walk(root, budget):
    """Files under ``root`` as (name, path), nearest folders first, at most ``budget[0]`` of them."""
    pending = [root]
    while pending and budget[0] > 0:
        folder = pending.pop(0)
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in SKIPPED_DIRS and not entry.name.startswith("."):
                    pending.append(entry.path)
            elif entry.name.lower().endswith(".fcstd"):
                budget[0] -= 1
                yield entry.name, entry.path


def find_file(folder, relative, extra_roots=()):
    """(path, how) for a link ``relative`` to ``folder`` whose file is gone: "same name" when exactly one
    file of that name lies under the folder (then under the folder above, then ``extra_roots``);
    (None, "missing") or (None, "ambiguous: a, b") otherwise."""
    direct = os.path.normpath(os.path.join(folder, relative))
    if os.path.isfile(direct):
        return direct, "relative path"
    wanted = os.path.basename(relative.replace("\\", "/")).lower()
    budget = [MAX_SEARCHED]
    seen = set()
    for root in [folder, os.path.dirname(folder), *extra_roots]:
        root = os.path.abspath(root)
        if not root or root in seen or not os.path.isdir(root) or root == os.path.dirname(root):
            continue
        seen.add(root)
        found = [path for name, path in _walk(root, budget) if name.lower() == wanted]
        found = sorted(set(os.path.realpath(p) for p in found))
        if len(found) == 1:
            return found[0], "same name"
        if len(found) > 1:
            return None, "ambiguous: " + ", ".join(os.path.relpath(p, folder) for p in found[:4])
    return None, "missing"


def _open_quietly(path):
    """The open document for ``path``, opened hidden when it is not open yet."""
    App = _app()
    real = os.path.realpath(path)
    for doc in App.listDocuments().values():
        if doc.FileName and os.path.realpath(doc.FileName) == real:
            return doc
    active = App.ActiveDocument
    try:
        doc = App.openDocument(path, True)
    except TypeError:
        doc = App.openDocument(path)
    if active is not None:
        App.setActiveDocument(active.Name)
    return doc


def _target(doc, name):
    obj = doc.getObject(name) if name else None
    if obj is None:
        try:
            from bruceworx.freecad_io import main_object

            obj = main_object(doc)
        except Exception:  # noqa: BLE001
            obj = None
    if obj is None:  # a file with plain solids and no body, part or assembly: its first top-level solid
        for item in doc.Objects:
            shape = getattr(item, "Shape", None)
            if not item.InList and shape is not None and not shape.isNull() and shape.Solids:
                return item
    return obj


def repair(doc, extra_roots=(), dry=False):
    """Point the document's broken links at the files found for them.

    {"repointed": [{"object", "label", "was", "now", "how"}], "missing": [...], "ambiguous": [...]}.
    Paths are relative to the document. The document is changed (one undo step) but not saved;
    FreeCAD writes the new relative paths when the user saves. ``dry``: only look."""
    report = {"repointed": [], "missing": [], "ambiguous": []}
    broken = broken_links(doc)
    if not broken:
        return report
    folder = os.path.dirname(os.path.abspath(doc.FileName))
    todo = []
    for link in broken:
        obj = doc.getObject(link["object"])
        path, how = find_file(folder, link["file"], extra_roots)
        entry = {"object": obj.Name, "label": obj.Label, "was": link["file"]}
        if path is None:
            report["missing" if how == "missing" else "ambiguous"].append(dict(entry, why=how))
            continue
        todo.append((obj, link, path, how, entry))
    if dry:
        for _obj, _link, path, how, entry in todo:
            report["repointed"].append(dict(entry, now=os.path.relpath(path, folder), how=how))
        return report
    if not todo:
        return report
    doc.openTransaction("Repair links")
    try:
        for obj, link, path, how, entry in todo:
            target = _target(_open_quietly(path), link["name"])
            if target is None:
                report["missing"].append(dict(entry, why=f"{os.path.basename(path)} has no object {link['name']!r}"))
                continue
            setattr(obj, link["property"], target)
            report["repointed"].append(dict(entry, now=os.path.relpath(path, folder), how=how))
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return report


def report_text(report):
    lines = []
    for entry in report["repointed"]:
        lines.append(f"{entry['label']}: {entry['was']} is now {entry['now']}")
    for entry in report["ambiguous"]:
        lines.append(f"{entry['label']}: {entry['was']} was not re-pointed, {entry['why']}")
    for entry in report["missing"]:
        lines.append(f"{entry['label']}: {entry['was']} was not found")
    return "\n".join(lines)


# ----- make external --------------------------------------------------------------------------------


def can_externalise(obj):
    """Why ``obj`` cannot become its own file, or "" when it can."""
    if obj is None:
        return "nothing is selected"
    if not any(obj.isDerivedFrom(kind) for kind in EXTERNAL_KINDS) or obj.TypeId == "Assembly::AssemblyObject":
        return f"{obj.Label} is not a body, a solid or a part"
    if obj.isDerivedFrom("PartDesign::Feature") or obj.isDerivedFrom("Sketcher::SketchObject"):
        return f"{obj.Label} is a feature of a body; select the body"
    if any(p.isDerivedFrom("PartDesign::Body") for p in obj.InList):
        return f"{obj.Label} is inside a body; select the body"
    if not obj.Document.FileName:
        return "save this document first: the part file is written next to it"
    return ""


def _safe_name(label):
    name = re.sub(r"[^\w.\- ]+", "_", label).strip(" .") or "Part"
    return name


def _free_path(folder, name):
    path = os.path.join(folder, name + ".FCStd")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{name} {n}.FCStd")
        n += 1
    return path


def _container(obj):
    for parent in obj.InList:
        group = getattr(parent, "Group", None)
        if isinstance(group, (list, tuple)) and obj in group and not parent.isDerivedFrom("App::Origin"):
            return parent
    return None


def _tree(obj):
    """``obj`` and everything inside it (a body's features, a part's contents, their origins)."""
    App = _app()
    out, pending = [], [obj]
    while pending:
        item = pending.pop()
        if not isinstance(item, App.DocumentObject) or item in out:
            continue
        out.append(item)
        for prop in ("Group", "OriginFeatures"):
            value = getattr(item, prop, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
        pending.append(getattr(item, "Origin", None))
    return out


def _repoint(doc, old, link, inside):
    """Everything outside ``inside`` that pointed at ``old`` points at ``link``. Returns the names changed."""
    changed = []
    inner = {o.Name for o in inside}
    prefix = old.Name + "."

    def subs_of(subs):
        out = []
        for sub in subs:
            if sub.startswith(prefix):  # "Body.Pad.Face3" -> "Link.Face3": the link shows the body's shape
                element = sub.rsplit(".", 1)[-1]
                sub = f"{link.Name}.{element}"
            out.append(sub)
        return out

    for obj in doc.Objects:
        if obj.Name in inner or obj is link:
            continue
        for prop in obj.PropertiesList:
            if prop in ("Group", "_GroupTouched") or "Link" not in obj.getTypeIdOfProperty(prop):
                continue
            try:
                value = getattr(obj, prop)
            except Exception:  # noqa: BLE001
                continue
            new = None
            if value is old:
                new = link
            elif isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], (list, tuple)):
                target, subs = value
                if target is old:
                    new = (link, list(subs))
                elif any(str(s).startswith(prefix) for s in subs):
                    new = (target, subs_of([str(s) for s in subs]))
            elif isinstance(value, list) and any(v is old for v in value):
                new = [link if v is old else v for v in value]
            if new is None:
                continue
            try:
                setattr(obj, prop, new)
                changed.append(f"{obj.Name}.{prop}")
            except Exception:  # noqa: BLE001 -- a read-only or computed link
                pass
    return changed


def make_external(obj, path=None):
    """``obj`` becomes its own part file and is replaced by a link to it.

    {"link": the App::Link, "path": the new file, "repointed": [...], "also_copied": [labels of things
    the part depended on outside itself, copied into the part file with it]}."""
    App = _app()
    why = can_externalise(obj)
    if why:
        raise PartFilesError(why)
    doc = obj.Document
    folder = os.path.dirname(os.path.abspath(doc.FileName))
    path = os.path.abspath(path) if path else _free_path(folder, _safe_name(obj.Label))
    if os.path.exists(path):
        raise PartFilesError(f"{os.path.basename(path)} already exists")
    inside = _tree(obj)
    label, placement, container = obj.Label, App.Placement(obj.Placement), _container(obj)
    visible = getattr(obj, "Visibility", True)

    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        new = App.newDocument(re.sub(r"\W", "_", stem), stem, True)
    except TypeError:
        new = App.newDocument(re.sub(r"\W", "_", stem))
    try:
        # Saved before anything is copied in: once the source file has a link to another file, FreeCAD
        # refuses to copy from it into a document that has never been saved.
        new.saveAs(path)
        copied = new.copyObject(obj, True)
        copy = copied[-1] if isinstance(copied, (list, tuple)) else copied
        if copy.TypeId != obj.TypeId:  # the list's order is not promised: find the one that is the part
            copy = next(o for o in new.Objects if o.TypeId == obj.TypeId and o.Label == label)
        copy.Placement = App.Placement()
        new.recompute()
        expected = len(inside)
        also = [o.Label for o in new.Objects][expected:] if len(new.Objects) > expected else []
        new.save()
    except Exception as exc:
        App.closeDocument(new.Name)
        App.setActiveDocument(doc.Name)
        if os.path.exists(path):
            os.remove(path)
        raise PartFilesError(f"{label} could not be written as its own file: {exc}") from None
    App.setActiveDocument(doc.Name)

    doc.openTransaction("Make External")
    try:
        link = container.newObject("App::Link", "Link") if container is not None else doc.addObject("App::Link", "Link")
        link.LinkedObject = copy
        link.Placement = placement
        changed = _repoint(doc, obj, link, inside)
        names = [o.Name for o in inside if o is not obj] + [obj.Name]  # contents first, the part itself last
        for name in names:
            if doc.getObject(name) is not None:
                try:
                    doc.removeObject(name)
                except Exception:  # noqa: BLE001 -- an origin goes with its owner
                    pass
        link.Label = label
        link.Visibility = visible
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return {"link": link, "path": path, "repointed": changed, "also_copied": also}


# ----- sub-assemblies -------------------------------------------------------------------------------


def is_sub_assembly(obj):
    return obj is not None and obj.isDerivedFrom("Assembly::AssemblyLink")


def insert(assembly, path, rigid=True, placement=None):
    """Insert the part or assembly file ``path`` into ``assembly``: an assembly comes in as a
    sub-assembly (rigid, or flexible so its joints are solved here), anything else as a link."""
    App = _app()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise PartFilesError(f"no file at {path}")
    doc = assembly.Document
    if not doc.FileName:
        raise PartFilesError("save the assembly first: a link is stored relative to its file")
    source = _open_quietly(path)
    if source is doc:
        raise PartFilesError("a document cannot be a component of itself")
    target = _target(source, "")
    if target is None:
        raise PartFilesError(f"{os.path.basename(path)} has no part, body or assembly to insert")
    sub = target.TypeId == "Assembly::AssemblyObject"
    link = assembly.newObject("Assembly::AssemblyLink" if sub else "App::Link", target.Label)
    link.LinkedObject = target
    link.Label = target.Label
    doc.recompute()
    if placement is not None:
        link.Placement = placement
    if sub:
        link.Rigid = bool(rigid)  # after the placement: turning flexible passes it down to the children
    doc.recompute()
    App.setActiveDocument(doc.Name)
    return link


def set_flexible(link, on=True):
    """Make a sub-assembly flexible (its joints move in the parent) or rigid (one body). One undo step."""
    if not is_sub_assembly(link):
        raise PartFilesError(f"{getattr(link, 'Label', link)} is not a sub-assembly")
    if bool(link.Rigid) == (not on):
        return False
    doc = link.Document
    doc.openTransaction("Flexible sub-assembly" if on else "Rigid sub-assembly")
    try:
        link.Rigid = not on
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return True


# ----- GUI ------------------------------------------------------------------------------------------


def _icon(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _path(obj, sub):
    """The objects a selection path names, top first: "Body.Pad.Face3" under an assembly gives
    [assembly, body, pad]. Names are looked up in the document that holds the one before."""
    out = [obj]
    doc = obj.Document
    for name in sub.split(".")[:-1]:
        item = doc.getObject(name) if name else None
        if item is None:
            break
        out.append(item)
        linked = item.getLinkedObject(True) if item.isDerivedFrom("App::Link") else None
        if linked is not None and not is_sub_assembly(item):
            doc = linked.Document  # below a link the names are the linked file's
    return out


def _selected(want):
    """The selected objects that ``want`` accepts; of a picked face or feature, the nearest object on
    its path that does (a face of a pad gives the pad's body)."""
    import FreeCADGui as Gui

    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        subs = [s for s in sel.SubElementNames if s] or [""]
        for sub in subs:
            found = next((o for o in reversed(_path(sel.Object, sub)) if want(o)), None)
            if found is not None and found not in out:
                out.append(found)
    return out


def _tell(title, text, warn=False):
    """A message box that does not block (kept referenced until closed)."""
    import FreeCADGui as Gui
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setObjectName("BruceWorxPartFilesMessage")
    box.setIcon(QtWidgets.QMessageBox.Warning if warn else QtWidgets.QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    _state["box"] = box
    box.open()
    return box


class _MakeExternalCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_EXTERNAL), "MenuText": "Make External",
                "ToolTip": "The selected body becomes its own part file next to this one and is replaced here by "
                           "a link to it. Its position and its joints are kept."}

    def IsActive(self):
        return _app().ActiveDocument is not None and enabled()

    def Activated(self):
        done, problems = [], []
        for obj in _selected(lambda o: not can_externalise(o)) or _selected(lambda o: True)[:1]:
            try:
                result = make_external(obj)
            except PartFilesError as exc:
                problems.append(str(exc))
                continue
            line = f"{result['link'].Label}: {os.path.basename(result['path'])}"
            if result["also_copied"]:
                line += " (copied with it: " + ", ".join(result["also_copied"][:6]) + ")"
            done.append(line)
        if not done and not problems:
            problems.append("Select a body, a solid or a part first.")
        text = "\n".join(done + problems)
        if done:
            text += "\n\nUndo brings a body back into this file; the part file stays on disk."
        _tell("Make External" if done else "Nothing was made external", text, warn=bool(problems))


class _RepairLinksCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_REPAIR), "MenuText": "Repair Links",
                "ToolTip": "Find the files of links that did not open (by file name, under this file's folder) "
                           "and point the links at them."}

    def IsActive(self):
        doc = _app().ActiveDocument
        return doc is not None and bool(doc.FileName) and enabled()

    def Activated(self):
        doc = _app().ActiveDocument
        report = repair(doc)
        if not any(report.values()):
            _tell("No broken links", f"Every link in {doc.Label} opened.")
        else:
            show_report(doc, report)


class _FlexibleCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_FLEXIBLE), "MenuText": "Flexible Sub-assembly",
                "ToolTip": "Switch the selected sub-assembly between rigid (it moves as one body) and flexible "
                           "(its own joints move in this assembly)."}

    def IsActive(self):
        if _app().ActiveDocument is None or not enabled():
            return False
        return bool(_selected(is_sub_assembly))

    def Activated(self):
        changed = []
        for obj in _selected(is_sub_assembly):
            set_flexible(obj, bool(obj.Rigid))
            changed.append(f"{obj.Label} is now {'rigid' if obj.Rigid else 'flexible'}")
        if changed:
            import FreeCADGui as Gui

            Gui.getMainWindow().statusBar().showMessage("; ".join(changed), 6000)


def show_report(doc, report):
    ok = report["repointed"] and not report["missing"] and not report["ambiguous"]
    title = f"Links of {doc.Label} were re-pointed" if report["repointed"] else f"{doc.Label} has broken links"
    text = report_text(report)
    if report["repointed"]:
        text += "\n\nSave the file to keep the new paths. Undo puts the links back."
    return _tell(title, text, warn=not ok)


def check_on_open(doc):
    """Repair a just-opened document once; the message when something was found."""
    if not repair_on_open() or doc.Name in _state["seen"]:
        return None
    if getattr(doc, "Restoring", False) or not doc.FileName:
        return None
    _state["seen"].add(doc.Name)
    try:
        report = repair(doc)
    except Exception as exc:  # noqa: BLE001 -- never in the way of opening a file
        _app().Console.PrintWarning(f"BruceWorx link repair: {exc}\n")
        return None
    if any(report.values()):
        _app().Console.PrintMessage("BruceWorx link repair, " + doc.Label + ":\n" + report_text(report) + "\n")
        return show_report(doc, report) if _app().GuiUp else report
    return None


class _DocObserver:
    """An opened file is activated once its objects are back (Python observers get no finish-restore)."""

    def slotActivateDocument(self, doc):
        if doc.Name in _state["seen"] or not doc.FileName:
            return
        from PySide import QtCore

        name = doc.Name
        QtCore.QTimer.singleShot(0, lambda: _check_named(name))

    def slotDeletedDocument(self, doc):
        _state["seen"].discard(doc.Name)


def _check_named(name):
    doc = _app().listDocuments().get(name)
    if doc is not None:
        check_on_open(doc)


def _shutdown():
    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        try:
            _app().removeDocumentObserver(observer)
        except Exception:  # noqa: BLE001
            pass


def install():
    import FreeCADGui
    from PySide import QtWidgets

    FreeCADGui.addCommand(CMD_EXTERNAL, _MakeExternalCommand())
    FreeCADGui.addCommand(CMD_REPAIR, _RepairLinksCommand())
    FreeCADGui.addCommand(CMD_FLEXIBLE, _FlexibleCommand())
    if _state["observer"] is None and enabled():
        _state["observer"] = _DocObserver()
        _app().addDocumentObserver(_state["observer"])
        QtWidgets.QApplication.instance().aboutToQuit.connect(lambda: _shutdown())
