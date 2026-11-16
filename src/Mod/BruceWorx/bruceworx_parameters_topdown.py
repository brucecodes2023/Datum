# SPDX-License-Identifier: LGPL-2.1-or-later
"""Top-down design across files (roadmap MOD-02).

Decided 24 Oct: one spreadsheet "Parameters" at the root of the top-assembly file holds every dimension
and spec; part files read it with cross-file expressions (Root#Parameters.plate_t), which the Parameters
tab (bruceworx_parameters) already writes. This module adds what makes that a top-down workflow:

* A skeleton: a layout sketch (or any shape) in the top file is bound into the Body of each part file
  that should follow it (Link Skeleton: a PartDesign SubShapeBinder named "Skeleton"), so features of the
  part can be sketched on it, and dimensions can read its named constraints
  (Root#Skeleton.Constraints.span).
* Changes reach the part files: FreeCAD 1.1 rebuilds the open files that read the top file when the top
  file is recomputed (checked 12 Nov 2026, headless and GUI, for a sheet value and a skeleton datum).
  ``propagate`` (Rebuild From Top) also marks those readers first, for a change that did not mark them:
  every open file that reads the document is rebuilt, in one recompute.
* The "Used in" column (D) of the sheet names, for each parameter, the parts (and drawings) whose
  dimensions read it, across the open files.
* Check References lists what reads another file and cannot: a file not found, a file not open, an
  object or parameter that is gone, a binder whose source did not load.

Off switch for the Used in column following edits in the GUI: Preferences/BruceWorx/TopDownUsedIn
(default on).
"""

from __future__ import annotations

import os

import bruceworx_parameters as P

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "TopDownUsedIn"
USED_IN_COL = "D"
USED_IN_HEADER = "Used in"
SKELETON_NAME = "Skeleton"
BINDER_TYPES = ("PartDesign::SubShapeBinder", "PartDesign::ShapeBinder")
# what an object in another file holds the top file by, without being a top-down reader: an assembly's
# links to its parts (rebuilding the assembly on every part edit would solve it each time)
_NOT_READERS = ("App::Link", "App::LinkElement", "App::LinkGroup")
CMD_LINK = "BruceWorx_LinkSkeleton"
CMD_CHECK = "BruceWorx_CheckReferences"
CMD_REBUILD = "BruceWorx_RebuildFromTop"
ICON_LINK = "Heuretes_LinkSkeleton.svg"
ICON_CHECK = "Heuretes_CheckReferences.svg"
ICON_REBUILD = "Heuretes_RebuildFromTop.svg"

_state = {"observer": None, "busy": False, "box": None, "used_in_pending": False}


class TopDownError(Exception):
    """A plain-words reason a top-down step cannot be done."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled():
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- who reads whom ------------------------------------------------------------------------------


def _reads_from(obj, doc):
    """True when ``obj`` (in another file) reads ``doc`` top-down: an expression naming the file, or a
    binder of one of its objects. A link of an assembly to its part is not."""
    if obj.Document is doc or obj.TypeId in _NOT_READERS or obj.TypeId.startswith("Assembly::"):
        return False
    prefix = f"{doc.Name}#"
    if any(prefix in (e or "") for _p, e in (getattr(obj, "ExpressionEngine", None) or ())):
        return True
    if obj.TypeId in BINDER_TYPES or obj.isDerivedFrom("PartDesign::ShapeBinder"):
        return any(o.Document is doc for o in (obj.OutList or ()))
    return False


def readers(doc, sources=None):
    """The objects in other open files that read ``doc`` top-down (of ``sources`` only, when given:
    objects of ``doc``). Direct readers only."""
    found = {}
    for src in (sources if sources is not None else doc.Objects):
        try:
            users = src.InList
        except Exception:  # noqa: BLE001 -- deleted meanwhile
            continue
        for obj in users:
            if obj.Document is not doc and obj.FullName not in found and _reads_from(obj, doc):
                found[obj.FullName] = obj
    return [found[k] for k in sorted(found)]


def downstream(doc, sources=None):
    """Every object in another file that a change of ``doc`` (or of ``sources``) reaches top-down: the
    readers and what depends on them, in their files. The assembly links back into ``doc`` are left out."""
    out = {}
    for reader in readers(doc, sources):
        out[reader.FullName] = reader
        for obj in reader.InListRecursive:
            if obj.Document is not doc:
                out.setdefault(obj.FullName, obj)
    return [out[k] for k in sorted(out)]


def propagate(doc, sources=None):
    """Rebuild the open files that read ``doc``, in one recompute (FreeCAD orders it across the files).
    Returns the names of the files rebuilt ([] when nothing reads ``doc``)."""
    reached = downstream(doc, sources)
    if not reached:
        return []
    direct = {o.FullName for o in readers(doc, sources)}
    for obj in reached:
        if obj.FullName in direct:
            obj.touch()  # FreeCAD does not mark readers in other files
    was, _state["busy"] = _state["busy"], True
    try:
        doc.recompute(reached)
    finally:
        _state["busy"] = was
    return sorted({o.Document.Name for o in reached})


# ----- the Used in column ---------------------------------------------------------------------------


def _where(obj, sheet_doc):
    """How the Used in column names an object: "Bracket" in the sheet's own file, "Bracket file: Pad"
    elsewhere (the Body or drawing page the feature belongs to, when it has one)."""
    owner = obj
    try:
        body = obj.getParentGeoFeatureGroup()
    except Exception:  # noqa: BLE001
        body = None
    if body is not None:
        owner = body
    for parent in getattr(obj, "InList", ()) or ():
        if parent.isDerivedFrom("TechDraw::DrawPage"):
            owner = parent
            break
    label = owner.Label if owner is obj else f"{owner.Label} ({obj.Label})"
    if obj.Document is sheet_doc:
        return label
    return f"{obj.Document.Label}: {label}"


def used_in(sheet):
    """{alias: [where]} for the sheet's parameters: what reads each, in every open file (the sheet's own
    cells that build on another parameter are left out)."""
    App = _app()
    aliases = [a for _c, a in P.sheet_aliases(sheet)]
    out = {a: [] for a in aliases}
    own_ref, own_label = sheet.Name, sheet.Label
    far_ref = f"{sheet.Document.Name}#{sheet.Name}"
    far_label = f"{sheet.Document.Name}#<<{sheet.Label}>>"
    for doc in App.listDocuments().values():
        for obj in doc.Objects:
            if obj is sheet:
                continue
            exprs = [e for _p, e in (getattr(obj, "ExpressionEngine", None) or ()) if e]
            if not exprs:
                continue
            for alias in aliases:
                if doc is sheet.Document:
                    hit = any(P.uses_alias(e, alias, own_ref, own_label) for e in exprs)
                else:
                    hit = any(P.uses_alias(e, alias, far_ref) or f"{far_label}.{alias}" in e for e in exprs)
                if hit:
                    where = _where(obj, sheet.Document)
                    if where not in out[alias]:
                        out[alias].append(where)
    return out


def used_in_text(places):
    return ", ".join(places)


def _cell_text(sheet, cell):
    """A cell's text as typed, without the quote FreeCAD keeps in front of text."""
    content = sheet.getContents(cell) or ""
    return content[1:] if content.startswith("'") else content


def update_used_in(sheet):
    """Write the Used in column (D) of the sheet: one cell per parameter row. Changes nothing else and
    leaves the sheet clean (writing a note does not rebuild the model). Returns the rows changed."""
    if sheet is None:
        return []
    found = used_in(sheet)
    changed = []
    was, _state["busy"] = _state["busy"], True
    try:
        if _cell_text(sheet, f"{USED_IN_COL}1") != USED_IN_HEADER:
            sheet.set(f"{USED_IN_COL}1", USED_IN_HEADER)
            try:
                sheet.setStyle(f"{USED_IN_COL}1", "bold")
                sheet.setColumnWidth(USED_IN_COL, 260)
            except Exception:  # noqa: BLE001
                pass
            changed.append(1)
        for cell, alias in P.sheet_aliases(sheet):
            row = P.cell_rc(cell)[0]
            target = f"{USED_IN_COL}{row}"
            if cell.startswith(USED_IN_COL):
                continue  # an alias living in column D itself: not ours to overwrite
            text = used_in_text(found.get(alias, []))
            if _cell_text(sheet, target) != text:
                # quoted: a name starting with "=" or a digit stays text
                sheet.set(target, f"'{text}" if text else "")
                changed.append(row)
        if changed:
            sheet.recompute()
            sheet.purgeTouched()
    finally:
        _state["busy"] = was
    return changed


# ----- the skeleton ---------------------------------------------------------------------------------


def _body_of(obj):
    """The PartDesign Body a link, part or body leads to (the one Body of an App::Part), or None."""
    target = obj
    try:
        if obj.TypeId == "App::Link" or obj.isDerivedFrom("App::Link"):
            target = obj.getLinkedObject(True)
    except Exception:  # noqa: BLE001
        return None
    if target is None:
        return None
    if target.isDerivedFrom("PartDesign::Body"):
        return target
    try:
        body = target.getParentGeoFeatureGroup()
    except Exception:  # noqa: BLE001
        body = None
    if body is not None and body.isDerivedFrom("PartDesign::Body"):
        return body
    if target.TypeId == "App::Part":
        bodies = [o for o in target.Group if o.isDerivedFrom("PartDesign::Body")]
        if len(bodies) == 1:
            return bodies[0]
    return None


def link_skeleton(skeleton, body, name=SKELETON_NAME):
    """Bind ``skeleton`` (a sketch or any shape in the top file) into ``body`` of a part file: a
    SubShapeBinder that follows it. Reuses the body's binder of the same skeleton. The part's file must be
    saved when the skeleton is in another file (FreeCAD links between saved files only). Returns the
    binder. The caller recomputes."""
    if body is None or not body.isDerivedFrom("PartDesign::Body"):
        raise TopDownError("Pick a part with one Body to link the skeleton into")
    if skeleton.Document is not body.Document:
        for doc in (skeleton.Document, body.Document):
            if not doc.FileName:
                raise TopDownError(f"Save {doc.Label} first: a link between files needs both saved")
    for obj in body.Group:
        if obj.TypeId in BINDER_TYPES and any(o is skeleton for o in (obj.OutList or ())):
            return obj
    binder = body.newObject("PartDesign::SubShapeBinder", name)
    binder.Label = f"{name} ({skeleton.Label})" if name == SKELETON_NAME else name
    binder.Support = [(skeleton, ("",))]
    try:
        binder.ViewObject.Visibility = True
    except Exception:  # noqa: BLE001 -- headless
        pass
    return binder


def link_skeleton_to(skeleton, targets):
    """Link ``skeleton`` into the Body of each target (links of an assembly, parts or bodies). Returns
    [(target, binder)]; one undo step per file is left to the caller. Raises on a target without a Body."""
    out = []
    docs = set()
    for target in targets:
        body = _body_of(target)
        if body is None:
            raise TopDownError(f"{target.Label} has no single Body to link the skeleton into")
        out.append((target, link_skeleton(skeleton, body)))
        docs.add(body.Document)
    for doc in docs:
        doc.recompute()
    return out


# ----- check references -----------------------------------------------------------------------------


def broken_references(doc):
    """[{"object", "property", "reference", "reason"}] of what in ``doc`` reads another file and cannot."""
    App = _app()
    out = []

    def add(obj_label, prop, ref, reason):
        row = {"object": obj_label, "property": prop, "reference": ref, "reason": reason}
        if row not in out:
            out.append(row)

    missing_files = set()
    if doc.FileName and os.path.isfile(doc.FileName):
        try:
            import bruceworx_partfiles as pf

            for link in pf.link_report(doc.FileName)["missing"]:
                obj = doc.getObject(link["object"])
                missing_files.add(os.path.splitext(os.path.basename(link["file"]))[0])
                add(obj.Label if obj is not None else link["object"], link["property"], link["file"],
                    f"file not found: {link['file']}")
        except Exception as exc:  # noqa: BLE001 -- an unreadable file: say so
            add(doc.Label, "", doc.FileName, f"could not read its links: {exc}")
    open_docs = App.listDocuments()
    for obj in doc.Objects:
        for prop, expr in (getattr(obj, "ExpressionEngine", None) or ()):
            refs = P.referenced_sheets([expr])
            if not refs:
                continue
            reason = ""
            for doc_name, obj_name in sorted(refs):
                other = open_docs.get(doc_name)
                if other is None:
                    reason = ("its file was not found" if doc_name in missing_files
                              else f"{doc_name} is not open")
                    break
                target = ((other.getObjectsByLabel(obj_name[2:-2]) or [None])[0] if obj_name.startswith("<<")
                          else other.getObject(obj_name))
                if target is None:
                    reason = f"{doc_name} has no {obj_name.strip('<>')}"
                    break
            if not reason:
                try:
                    obj.evalExpression(expr)
                except Exception as exc:  # noqa: BLE001 -- a parameter renamed or deleted
                    reason = str(exc)
            if reason:
                add(obj.Label, prop, expr, reason)
        if obj.TypeId in BINDER_TYPES:
            support = getattr(obj, "Support", None)
            if not support:
                add(obj.Label, "Support", "", "its source did not load")
    return out


def report_text(doc, rows):
    if not rows:
        return f"Every reference of {doc.Label} to another file resolves."
    lines = [f"{len(rows)} reference(s) of {doc.Label} cannot be read:"]
    for row in rows:
        what = f"{row['object']}.{row['property']}" if row["property"] else row["object"]
        ref = f" ({row['reference']})" if row["reference"] else ""
        lines.append(f"- {what}{ref}: {row['reason']}")
    return "\n".join(lines)


# ----- the Used in column follows edits (GUI) ------------------------------------------------------


class _Observer:
    """An expression set or cleared: the Used in column of the sheet it reads follows."""

    def slotChangedObject(self, obj, prop):
        if prop == "ExpressionEngine" and not _state["busy"] and _app().GuiUp and enabled():
            try:
                if obj.Document.Restoring:
                    return
            except Exception:  # noqa: BLE001
                return
            _schedule_used_in(obj.Document.Name)


def _schedule_used_in(doc_name):
    if _state.get("used_in_pending"):
        return
    _state["used_in_pending"] = True
    from PySide import QtCore

    def run():
        _state["used_in_pending"] = False
        doc = _app().listDocuments().get(doc_name)
        if doc is None:
            return
        try:
            sheet = P.find_sheet(doc)
            if sheet is not None:
                update_used_in(sheet)
        except Exception as exc:  # noqa: BLE001
            _app().Console.PrintLog(f"BruceWorx top-down: Used in: {exc}\n")

    QtCore.QTimer.singleShot(300, run)


def install_observer():
    if _state["observer"] is None and enabled():
        _state["observer"] = _Observer()
        _app().addDocumentObserver(_state["observer"])
    return _state["observer"]


def uninstall_observer():
    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        _app().removeDocumentObserver(observer)


# ----- commands ---------------------------------------------------------------------------------------


def _icon(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _tell(title, text, warn=False):
    import FreeCADGui as Gui
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setObjectName("BruceWorxTopDownMessage")
    box.setIcon(QtWidgets.QMessageBox.Warning if warn else QtWidgets.QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    _state["box"] = box
    box.open()
    return box


def _is_skeleton_source(obj):
    return obj.isDerivedFrom("Sketcher::SketchObject") or obj.isDerivedFrom("Part::Datum") \
        or obj.isDerivedFrom("App::Plane") or obj.TypeId == "Part::Feature"


def split_selection(objs):
    """(skeleton, targets) of a selection: the first sketch (or datum) and the links / parts / bodies."""
    skeleton, targets = None, []
    for obj in objs:
        if skeleton is None and _is_skeleton_source(obj):
            skeleton = obj
        elif _body_of(obj) is not None and obj not in targets:
            targets.append(obj)
    return skeleton, targets


def _selected_objects():
    import FreeCADGui as Gui

    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        subs = [s for s in sel.SubElementNames if s] or [""]
        for sub in subs:
            obj = sel.Object
            if sub:
                # the object on the path a link or a sketch is: "Link001.Body." -> Link001; "Skeleton." -> Skeleton
                first = sub.split(".")[0]
                picked = sel.Object.Document.getObject(first)
                if picked is not None:
                    obj = picked
            if obj not in out:
                out.append(obj)
    return out


class _LinkSkeletonCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_LINK), "MenuText": "Link Skeleton",
                "ToolTip": "Select a layout sketch and the parts that should follow it: the sketch is bound "
                           "into each part's Body (a binder named Skeleton), across files."}

    def IsActive(self):
        return _app().ActiveDocument is not None

    def Activated(self):
        skeleton, targets = split_selection(_selected_objects())
        if skeleton is None or not targets:
            _tell("Link Skeleton", "Select the layout sketch, then the parts (or their links in the assembly) "
                                   "that should follow it.", warn=True)
            return
        docs = {skeleton.Document}
        for target in targets:
            body = _body_of(target)
            if body is not None:
                docs.add(body.Document)
        for doc in docs:
            doc.openTransaction("Link Skeleton")
        try:
            made = link_skeleton_to(skeleton, targets)
        except TopDownError as exc:
            for doc in docs:
                doc.abortTransaction()
            _tell("Link Skeleton", str(exc), warn=True)
            return
        for doc in docs:
            doc.commitTransaction()
        _app().Console.PrintMessage(
            f"Linked {skeleton.Label} into {', '.join(t.Label for t, _b in made)}\n")


class _CheckReferencesCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_CHECK), "MenuText": "Check References",
                "ToolTip": "List what in this file reads another file and cannot (file missing, parameter "
                           "gone), and update the Used in column of the Parameters sheet."}

    def IsActive(self):
        return _app().ActiveDocument is not None

    def Activated(self):
        doc = _app().ActiveDocument
        rows = broken_references(doc)
        seen = {doc.Name}
        for obj in doc.Objects:  # the part files the assembly links
            target = obj.getLinkedObject(True) if obj.TypeId == "App::Link" else None
            other = getattr(target, "Document", None)
            if other is not None and other.Name not in seen:
                seen.add(other.Name)
                rows += [dict(r, object=f"{other.Label}: {r['object']}") for r in broken_references(other)]
        sheet = P.find_sheet(doc)
        if sheet is not None:
            update_used_in(sheet)
        _tell("Check References", report_text(doc, rows), warn=bool(rows))


class _RebuildFromTopCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_REBUILD), "MenuText": "Rebuild From Top",
                "ToolTip": "Rebuild this file and every open part file that reads its parameters or "
                           "skeleton, in one recompute."}

    def IsActive(self):
        return _app().ActiveDocument is not None

    def Activated(self):
        doc = _app().ActiveDocument
        doc.recompute()
        rebuilt = propagate(doc)
        sheet = P.local_sheet(doc)
        if sheet is not None:
            update_used_in(sheet)
        _app().Console.PrintMessage(
            f"Rebuilt {doc.Label}" + (f" and {', '.join(rebuilt)}\n" if rebuilt else " (no open file reads it)\n"))


def install():
    """The commands (GUI) and the observer that keeps the Used in column current."""
    import FreeCADGui

    FreeCADGui.addCommand(CMD_LINK, _LinkSkeletonCommand())
    FreeCADGui.addCommand(CMD_CHECK, _CheckReferencesCommand())
    FreeCADGui.addCommand(CMD_REBUILD, _RebuildFromTopCommand())
    install_observer()
