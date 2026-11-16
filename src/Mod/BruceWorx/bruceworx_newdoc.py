# SPDX-License-Identifier: LGPL-2.1-or-later
"""New document asks what it is for: a part, an assembly, or a hybrid (parts and their assembly in one file).

* File > New, its shortcut and the "+" beside the document tabs open a three-entry menu instead of an
  empty, untyped document. Part starts a body in Part Design; Assembly starts an assembly on the
  Assembly tab; Hybrid starts an assembly with a first body inside it.
* A document's tab shows what it is: the part, assembly or hybrid icon, from what the document holds
  (kind_of), so it follows the document as it grows. A document that is none of these keeps FreeCAD's
  own icon.

Off switch: Preferences > BruceWorx > "New document asks: part, assembly or hybrid" (NewDocumentChooser);
New then makes an empty document as before, and the tabs keep FreeCAD's icons.
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "NewDocumentChooser"
COMMAND = "BruceWorx_NewDocument"
PART, ASSEMBLY, HYBRID, DRAWING, OTHER = "part", "assembly", "hybrid", "drawing", "other"
HYBRID_ICON = "Heuretes_Hybrid.svg"
TAKEN_PROPERTY = "bwNewDocument"  # on Std_New's action once it opens the menu
KIND_PROPERTY = "bwDocumentKind"  # on a document window: the kind its icon shows
CHOICES = (  # kind, menu text (with its key), what it starts
    (PART, "&Part", "One part: a body to sketch and build on."),
    (ASSEMBLY, "&Assembly", "Parts from other files, placed and joined."),
    (HYBRID, "&Hybrid", "Parts modelled and assembled in this one file."),
)
_state = {"menu": None, "observer": None, "timer": None, "icons": {}}


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- what a document is ---------------------------------------------------------------------------


def kind_from(types, own_geometry):
    """The kind for a document with objects of ``types`` (TypeIds); ``own_geometry``: it models
    something itself (a body, a solid, a code part) rather than only linking other files."""
    types = list(types)
    if any(t == "Assembly::AssemblyObject" for t in types):
        return HYBRID if own_geometry else ASSEMBLY
    if own_geometry:
        return PART
    if any(t == "TechDraw::DrawPage" for t in types):
        return DRAWING
    return OTHER


def _own_geometry(doc):
    for obj in doc.Objects:
        if obj.isDerivedFrom("PartDesign::Body"):
            return True
        if not obj.isDerivedFrom("Part::Feature") or obj.isDerivedFrom("Sketcher::SketchObject"):
            continue
        if obj.isDerivedFrom("PartDesign::Feature") or obj.isDerivedFrom("Part::Part2DObject"):
            continue  # a body's own features count through the body
        if obj.TypeId == "Assembly::AssemblyObject" or obj.TypeId.startswith(("Assembly::", "App::")):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull() and shape.Solids:
            return True
    return False


def kind_of(doc):
    """part, assembly, hybrid, drawing or other, from what the document holds now."""
    try:
        return kind_from([o.TypeId for o in doc.Objects], _own_geometry(doc))
    except Exception:  # noqa: BLE001 -- a document on its way out
        return OTHER


# ----- starting one ---------------------------------------------------------------------------------


def _new_hybrid():
    """An assembly, as New Assembly makes it, with a first body inside to model on."""
    import FreeCAD
    import FreeCADGui

    import bruceworx_start as start

    before = set(FreeCAD.listDocuments())
    start._new_assembly()
    doc = FreeCAD.ActiveDocument
    if doc is None or doc.Name in before:
        return
    assembly = next((o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"), None)
    if assembly is None:
        return
    doc.openTransaction("New body")
    try:
        body = assembly.newObject("PartDesign::Body", "Body")
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    doc.recompute()
    try:
        FreeCADGui.ActiveDocument.ActiveView.setActiveObject("pdbody", body)
        import bruceworx_workspaces as workspaces

        if workspaces.enabled():
            workspaces.show_tab("Design", "Solid")
    except Exception:  # noqa: BLE001 -- the body is there; which tab shows is a nicety
        pass


def create(kind):
    """Start a new document of ``kind`` (part, assembly or hybrid)."""
    import bruceworx_start as start

    if kind == PART:
        start._new_in("PartDesignWorkbench", "PartDesign_Body")
    elif kind == ASSEMBLY:
        start._new_assembly()
    elif kind == HYBRID:
        _new_hybrid()
    else:
        raise ValueError(f"no such kind of document: {kind}")
    refresh_icons()


def icon(kind):
    """The QIcon of a kind; a null icon for drawing and other (FreeCAD's own stays)."""
    from PySide import QtGui

    if kind in _state["icons"]:
        return _state["icons"][kind]
    if kind == HYBRID:
        made = QtGui.QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), HYBRID_ICON))
    elif kind in (PART, ASSEMBLY):
        import bruceworx_start as start

        made = start._kind_icon(kind)
    else:
        made = QtGui.QIcon()
    _state["icons"][kind] = made
    return made


def menu(parent=None):
    """The three-entry menu; each entry starts its kind of document."""
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    parent = parent or FreeCADGui.getMainWindow()
    made = QtWidgets.QMenu(parent)
    made.setObjectName("BruceWorxNewDocumentMenu")
    made.setToolTipsVisible(True)
    for kind, text, tip in CHOICES:
        action = made.addAction(icon(kind), text)
        action.setObjectName("BruceWorxNew_" + kind)
        action.setToolTip(tip)
        action.setStatusTip(tip)
        action.triggered.connect(lambda _c=False, k=kind: QtCore.QTimer.singleShot(0, lambda: create(k)))
    return made


def choose(anchor=None):
    """Open the menu: under ``anchor`` (the "+" button), else in the middle of the window's top."""
    import FreeCADGui
    from PySide import QtCore

    if not enabled():
        FreeCADGui.runCommand("Std_New")
        return None
    window = FreeCADGui.getMainWindow()
    old = _state["menu"]
    if old is not None:
        try:
            old.close()
            old.deleteLater()
        except RuntimeError:
            pass
    made = _state["menu"] = menu(window)
    if anchor is not None:
        where = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height()))
    else:
        size = made.sizeHint()
        where = window.mapToGlobal(QtCore.QPoint((window.width() - size.width()) // 2, max(60, window.height() // 5)))
    made.popup(where)
    made.setActiveAction(made.actions()[0])
    return made


class _NewDocumentCommand:
    def GetResources(self):
        return {"MenuText": "New…", "ToolTip": "Start a part, an assembly, or a hybrid of both in one file."}

    def IsActive(self):
        return True

    def Activated(self):
        choose()


def take_over_new():
    """File > New and its shortcut open the menu. FreeCAD's own command stays for everything that
    runs Std_New by name (the Start page, scripts, this module)."""
    import FreeCADGui
    from PySide import QtGui

    action = FreeCADGui.getMainWindow().findChild(QtGui.QAction, "Std_New")
    if action is None or action.property(TAKEN_PROPERTY):
        return action is not None
    try:
        action.triggered.disconnect()
    except (RuntimeError, TypeError):
        return False
    action.triggered.connect(lambda _c=False: choose())
    action.setProperty(TAKEN_PROPERTY, True)
    return True


# ----- the tab shows the kind -----------------------------------------------------------------------


def _windows():
    """[(document, its QMdiSubWindows)] for the documents with a 3D view."""
    import FreeCAD
    import FreeCADGui

    out = []
    for doc in FreeCAD.listDocuments().values():
        try:
            views = FreeCADGui.getDocument(doc.Name).mdiViewsOfType("Gui::View3DInventor")
        except Exception:  # noqa: BLE001 -- a hidden document has no GUI side
            continue
        subs = []
        for view in views:
            widget = view.graphicsView() if hasattr(view, "graphicsView") else None
            while widget is not None and widget.metaObject().className() != "QMdiSubWindow":
                widget = widget.parentWidget()
            if widget is not None:
                subs.append(widget)
        if subs:
            out.append((doc, subs))
    return out


def refresh_icons():
    """Give every document window the icon of its kind. Returns {document name: kind}."""
    if not enabled():
        return {}
    kinds = {}
    for doc, subs in _windows():
        kind = kinds[doc.Name] = kind_of(doc)
        for sub in subs:
            if sub.property(KIND_PROPERTY) == kind:
                continue
            made = icon(kind)
            if made.isNull():
                if not sub.property(KIND_PROPERTY):
                    continue  # never changed: FreeCAD's icon is still there
                import FreeCADGui

                made = FreeCADGui.getMainWindow().windowIcon()
            sub.setWindowIcon(made)
            sub.setProperty(KIND_PROPERTY, kind)
    return kinds


def _later():
    timer = _state["timer"]
    if timer is not None:
        timer.start()


class _Observer:
    """A document's kind can change with every object made or removed; look again shortly after."""

    def slotCreatedDocument(self, _doc):
        _later()

    def slotActivateDocument(self, _doc):
        _later()

    def slotCreatedObject(self, _obj):
        _later()

    def slotDeletedObject(self, _obj):
        _later()

    def slotRecomputedDocument(self, _doc):
        _later()

    def slotUndoDocument(self, _doc):
        _later()

    def slotRedoDocument(self, _doc):
        _later()


def _shutdown():
    import FreeCAD

    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        try:
            FreeCAD.removeDocumentObserver(observer)
        except Exception:  # noqa: BLE001
            pass
    if _state["timer"] is not None:
        _state["timer"].stop()


def install():
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    FreeCADGui.addCommand(COMMAND, _NewDocumentCommand())
    if not enabled():
        return
    window = FreeCADGui.getMainWindow()
    timer = _state["timer"] = QtCore.QTimer(window)
    timer.setSingleShot(True)
    timer.setInterval(300)
    timer.timeout.connect(lambda: refresh_icons())
    _state["observer"] = _Observer()
    FreeCAD.addDocumentObserver(_state["observer"])
    QtWidgets.QApplication.instance().aboutToQuit.connect(lambda: _shutdown())

    def start(tries=20):
        if not take_over_new() and tries > 0:  # the action exists once the first workbench is up
            QtCore.QTimer.singleShot(500, lambda: start(tries - 1))

    QtCore.QTimer.singleShot(0, start)
    _later()
