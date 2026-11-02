# SPDX-License-Identifier: LGPL-2.1-or-later
"""Auto-activate the assembly (ASM, Bruce 2 Nov 2026).

Bruce: "when I open a file, that one becomes the active one". The joint and assemble tools stay greyed until
the assembly is activated (a double-click on it in the tree). When a document becomes the active one and
nothing is being edited, its single root Assembly is activated the same way, and the tree scrolls to it.

Rules (``should_activate`` is FreeCAD-free and tested):
* the preference ``AutoActivateAssembly`` (BaseApp/Preferences/BruceWorx, default on) is on;
* the document has exactly one root Assembly::AssemblyObject;
* no task dialog is open, no document is in edit (sketch, body feature, ...), no assembly, part or body is already active;
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


def should_activate(enabled, n_assemblies, dialog_open, editing, active_part, busy, workbench):
    """None when the assembly should be activated now, else why not (a short reason)."""
    if not enabled:
        return "preference off"
    if n_assemblies != 1:
        return "no single assembly"
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
    return [o for o in doc.RootObjects if o.isDerivedFrom("Assembly::AssemblyObject")]


def _state(name):
    """The facts about document ``name`` for should_activate, or None when it is not the active one."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if doc is None or doc.Name != name or Gui.getDocument(name) is None:
        return None
    editing = any(
        Gui.getDocument(d.Name) is not None and Gui.getDocument(d.Name).getInEdit() is not None
        for d in App.listDocuments().values()
    )
    view = Gui.getDocument(name).ActiveView
    active_part = False
    for key in ("assembly", "part", "pdbody"):
        try:
            active_part = active_part or view.getActiveObject(key) is not None
        except Exception:
            pass
    busy = bool(getattr(doc, "Restoring", False) or getattr(doc, "Recomputing", False))
    wb = Gui.activeWorkbench()
    return {
        "enabled": enabled(),
        "n": len(_assemblies(doc)),
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
                              facts["active_part"], facts["busy"], facts["workbench"])
        if why == "document busy" and retries > 0:
            QtCore.QTimer.singleShot(RETRY_MS, lambda: activate(name, retries - 1))
            return why
        if why:
            return why
        doc = App.ActiveDocument
        asm = _assemblies(doc)[0]
        Gui.ActiveDocument.setEdit(asm)
        # Bring it into view in the tree, then drop the selection so the first joint pick starts clean.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, asm.Name)
        QtCore.QTimer.singleShot(CLEAR_MS, Gui.Selection.clearSelection)
        return None
    except Exception as exc:  # never get in the way of opening a file
        App.Console.PrintLog(f"BruceWorx auto-activate: {exc}\n")
        return "error"


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
