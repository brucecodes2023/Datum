# SPDX-License-Identifier: LGPL-2.1-or-later
"""Auto-activate the assembly or body (ASM, Bruce 2 Nov 2026).

Bruce: "when I open a file, that one becomes the active one". The joint and assemble tools stay greyed until
the assembly is activated (a double-click on it in the tree). When a document becomes the active one and
nothing is being edited, its single top Assembly is activated the same way, and the tree scrolls to it.
A document without an assembly but with exactly one PartDesign Body gets that body as the active body.
A document with neither but exactly one root App::Part (what the body import makes: a Part holding a plain
Part::Feature) gets that Part as the active part.

Rules (``should_activate`` is FreeCAD-free and tested):
* the preference ``AutoActivateAssembly`` (BaseApp/Preferences/BruceWorx, default on) is on;
* the document has exactly one top Assembly::AssemblyObject (one no other object of the SAME document holds:
  an assembly that another open file links to is still the top one here), or no assembly and exactly one Body;
* no task dialog is open, nothing is in edit in this document, and no other document has anything in edit
  except an active assembly (that edit is reset first: the printer's assembly lets go for a sub-assembly
  opened after it, and takes it back when its tab is chosen again); no part or body is already active here
  (an assembly is active only while it is in edit: the view's marker may outlive the edit);
* the document is not loading or recomputing (the check is repeated for a few seconds, then given up);
* the current workbench belongs to the Design workspace (PartDesign, Assembly, Part, Start): a Simulation,
  Drawing or Manufacture session is left where it is.

``setEdit`` on the Assembly view provider does not switch workbenches (only its double-click does, through
``assureWorkbench``), so the workspace Bruce is in stays put.
The stopgap add-on VLAutoActivate does the same; if it is loaded this module stays out of its way.
"""

from __future__ import annotations

import sys

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "AutoActivateAssembly"
DELAY_MS = 150
RETRY_MS = 250
RETRIES = 20
CLEAR_MS = 400
DESIGN_WORKBENCHES = ("", "NoneWorkbench", "StartWorkbench", "PartDesignWorkbench", "AssemblyWorkbench", "PartWorkbench")

_observer = None
_installed = False
_warned = False


# ----- pure helpers (unit tested) -----------------------------------------------------------------


def should_activate(enabled, n_assemblies, dialog_open, editing, active_part, busy, workbench, n_bodies=0, n_parts=0):
    """None when the assembly (or the single body) should be activated now, else why not (a short reason)."""
    if not enabled:
        return "preference off"
    if n_assemblies != 1 and not (n_assemblies == 0 and (n_bodies == 1 or n_parts == 1)):
        return "no single assembly, body or part"
    if dialog_open:
        return "task dialog open"
    if editing:
        return "something is in edit"
    if active_part:
        return "an assembly, part or body is already active"
    if busy:
        return "document busy"
    if workbench not in DESIGN_WORKBENCHES:
        return "not the Design workspace"
    return None


def other_addon_loaded(modules=None):
    return "VLAutoActivate" in (sys.modules if modules is None else modules)


# ----- GUI ----------------------------------------------------------------------------------------


def enabled():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True)


def _assemblies(doc):
    """The assemblies nothing in this same document holds. Not RootObjects: an assembly that another open
    document links to has that cross-document link in its InList and is not a root."""
    return [
        o for o in doc.Objects
        if o.isDerivedFrom("Assembly::AssemblyObject") and not any(p.Document is doc for p in o.InList)
    ]


def _bodies(doc):
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def _root_parts(doc):
    """The App::Parts nothing in this same document holds."""
    return [o for o in doc.Objects if o.TypeId == "App::Part" and not any(p.Document is doc for p in o.InList)]


def _is_assembly_edit(vp):
    """True when the view provider in edit is an assembly's (the Python wrapper may not expose .Object)."""
    obj = getattr(vp, "Object", None)
    if obj is not None:
        return obj.isDerivedFrom("Assembly::AssemblyObject")
    return "ViewProviderAssembly" in str(getattr(vp, "TypeId", ""))


def _active_assembly_edits(skip_name):
    """The Gui documents (not ``skip_name``) whose edit is an active assembly, and True when any other
    document has something else in edit (sketch, body feature, simulation ...)."""
    import FreeCAD as App
    import FreeCADGui as Gui

    assembly_docs, other = [], False
    for d in App.listDocuments().values():
        gd = Gui.getDocument(d.Name)
        if gd is None or d.Name == skip_name:
            continue
        vp = gd.getInEdit()
        if vp is None:
            continue
        if _is_assembly_edit(vp):
            assembly_docs.append(gd)
        else:
            other = True
    return assembly_docs, other


def _state(name):
    """The facts about document ``name`` for should_activate, or None when it is not the active one."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if doc is None or doc.Name != name or Gui.getDocument(name) is None:
        return None
    assembly_docs, other_edit = _active_assembly_edits(name)
    editing = other_edit or Gui.getDocument(name).getInEdit() is not None
    view = Gui.getDocument(name).ActiveView
    # An active assembly here is this document's edit (counted in ``editing``). The view's "assembly" marker
    # alone is not: it can stay set after another file took the edit, and then the tab never got it back.
    active_part = False
    for key in ("part", "pdbody"):
        try:
            active_part = active_part or view.getActiveObject(key) is not None
        except Exception:
            pass
    # no 3D view yet (a file loaded only because another file links it): wait for the view to appear
    busy = view is None or bool(getattr(doc, "Restoring", False) or getattr(doc, "Recomputing", False))
    wb = Gui.activeWorkbench()
    return {
        "enabled": enabled(),
        "n": len(_assemblies(doc)),
        "bodies": len(_bodies(doc)),
        "parts": len(_root_parts(doc)),
        "reset": assembly_docs,
        "dialog": Gui.Control.activeDialog(),
        "editing": editing,
        "active_part": active_part,
        "busy": busy,
        "workbench": wb.name() if wb is not None else "",
    }


def activate(name, retries=RETRIES):
    """Activate the assembly of document ``name`` if the rules allow; retry while the document is busy."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide import QtCore

    try:
        facts = _state(name)
        if facts is None:
            return None
        why = should_activate(facts["enabled"], facts["n"], facts["dialog"], facts["editing"],
                              facts["active_part"], facts["busy"], facts["workbench"], facts["bodies"], facts["parts"])
        if why == "document busy" and retries > 0:
            QtCore.QTimer.singleShot(RETRY_MS, lambda: activate(name, retries - 1))
            return why
        if why:
            return why
        doc = App.ActiveDocument
        asms = _assemblies(doc)
        if not asms:
            return _activate_body(doc)
        asm = asms[0]
        for gd in facts["reset"]:  # let go of the assembly active in another file
            gd.resetEdit()
        Gui.getDocument(name).setEdit(asm)
        # Bring it into view in the tree, then drop the selection so the first joint pick starts clean.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, asm.Name)
        QtCore.QTimer.singleShot(CLEAR_MS, Gui.Selection.clearSelection)
        return None
    except Exception as exc:  # never get in the way of opening a file
        App.Console.PrintLog(f"BruceWorx auto-activate: {exc}\n")
        return "error"


def _activate_body(doc):
    """Make the document's single PartDesign Body the active body (and its App::Part the active part)."""
    import FreeCADGui as Gui

    bodies = _bodies(doc)
    view = Gui.getDocument(doc.Name).ActiveView
    if len(bodies) != 1:
        return _activate_part(doc)
    body = bodies[0]
    view.setActiveObject("pdbody", body)
    parts = [o for o in body.InList if o.TypeId == "App::Part"]
    if parts:
        view.setActiveObject("part", parts[0])
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, body.Name)
    from PySide import QtCore

    QtCore.QTimer.singleShot(CLEAR_MS, Gui.Selection.clearSelection)
    return None


def _activate_part(doc):
    """Make the document's single root App::Part the active part (an imported part: Part > Body feature)."""
    import FreeCADGui as Gui
    from PySide import QtCore

    part = _root_parts(doc)[0]
    Gui.getDocument(doc.Name).ActiveView.setActiveObject("part", part)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name)
    QtCore.QTimer.singleShot(CLEAR_MS, Gui.Selection.clearSelection)
    return None


class _DocObserver:
    def slotActivateDocument(self, gui_doc):
        try:
            name = gui_doc.Document.Name
            from PySide import QtCore

            QtCore.QTimer.singleShot(DELAY_MS, lambda: activate(name))  # after the open or tab switch has finished
        except Exception:
            pass


def install():
    """Watch the active document (opened, or its tab chosen)."""
    global _observer, _installed, _warned
    if _installed:
        return
    import FreeCAD as App
    import FreeCADGui as Gui

    if other_addon_loaded():
        if not _warned:
            _warned = True
            App.Console.PrintMessage("BruceWorx auto-activate: the VLAutoActivate add-on is loaded, leaving it to that.\n")
        return
    _observer = _DocObserver()
    Gui.addDocumentObserver(_observer)
    _installed = True
