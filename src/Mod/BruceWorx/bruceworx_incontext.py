# SPDX-License-Identifier: LGPL-2.1-or-later
"""In-context editing (roadmap ASM-07): edit a part inside its assembly and take geometry from the parts
around it.

A part is its own file and comes into the assembly as a link (ASM-01, bruceworx_partfiles). Editing it
"in context" means its body is the active body while the assembly is on screen, so the parts around it
can be picked. A picked edge, face or part becomes a reference: a stock PartDesign SubShapeBinder in the
edited body whose Support is the other part's component (a link in the assembly) and whose Context is
the assembly path to the binder. FreeCAD then places the bound geometry in the edited part's own frame
and re-places it whenever the assembly is recomputed, also across files: move the motor mount and the
bracket's holes, cut from the reference, follow. Nothing here is a new link type; what this module adds:

* reference(root, body_path, source_paths): the binder, made from assembly paths (what a 3D pick
  gives). The Support is the component the two paths part at, never the assembly that holds both, so
  the reference does not make the bracket depend on itself.
* references(docs) / status(binder): every binder with a context, and whether it still matches its
  source: "ok", "stale" (the source moved or changed since the binder was last computed; a locked one
  stays so until updated), "broken" (the source is gone) or "detached" (a copy, no longer linked).
* update(binder), lock(binder, on), break_link(binder): Update recomputes it (a locked one too, and it
  stays locked), Lock freezes it (BindMode Frozen), Break keeps the shape and drops the link
  (BindMode Detached; cannot be undone other than by Undo).
* edit_in_context(root, body_path) / finish(): the body becomes the active body in place, in the
  assembly's view (stock MDIView active object with a parent and a path).

Commands (install()): Edit in Context, Reference from Assembly, In-Context References.
Off switch: Preferences/BruceWorx/InContext (the commands go inactive).
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "InContext"
CMD_EDIT = "BruceWorx_EditInContext"
CMD_REFERENCE = "BruceWorx_ReferenceFromAssembly"
CMD_REFERENCES = "BruceWorx_InContextReferences"
ICON_EDIT = "BruceWorx_EditInContext.svg"
ICON_REFERENCE = "BruceWorx_ReferenceFromAssembly.svg"
ICON_REFERENCES = "BruceWorx_InContextReferences.svg"
BINDER = "PartDesign::SubShapeBinder"
TOL = 1e-6  # mm: the bound geometry and its source agree
OK, STALE, BROKEN, DETACHED, UNCHECKED = "ok", "stale", "broken", "detached", "not checked"
_state = {"dialog": None, "box": None}


class InContextError(Exception):
    """Something to tell the user in one sentence."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled():
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- paths -----------------------------------------------------------------------------------------

def plain(sub):
    """A pick's path without its mapped element name: "BracketLink.Body.;Face1;:He9f,F.Face1" (what the
    GUI selection gives) is "BracketLink.Body.Face1"."""
    sub = sub or ""
    if ";" not in sub:
        return sub
    try:
        import Part

        path, _mapped, element = Part.splitSubname(sub)
        return path + element
    except Exception:  # noqa: BLE001
        return sub[:sub.index(";")] + sub.rsplit(".", 1)[-1]


def _names(sub):
    """("MountLink", "Body") and the element ("Edge3", or "" for a whole object) of an assembly path."""
    parts = (sub or "").split(".")
    return [p for p in parts[:-1] if p], parts[-1]


def walk(root, sub):
    """The objects an assembly path names, root first: (root, "MountLink.Body.Edge3") gives
    [root, MountLink, Body]. Below a link the names are looked up in the linked file too."""
    out, doc, docs = [root], root.Document, [root.Document]
    for name in _names(sub)[0]:
        item = None
        for d in [doc] + docs:
            item = d.getObject(name)
            if item is not None:
                break
        if item is None:
            raise InContextError(f"{name} is not in {out[-1].Label}")
        out.append(item)
        linked = item.getLinkedObject(True)
        if linked is not None and linked.Document is not doc:
            doc = linked.Document
            docs.append(doc)
    return out


def is_body(obj):
    return obj is not None and obj.isDerivedFrom("PartDesign::Body")


def body_path(root, sub):
    """The path to the body a pick lies in (or that a picked component holds): "BracketLink.Body."
    for a face of the bracket. None when there is no body on it."""
    objs = walk(root, sub)
    names = _names(sub)[0]
    for i in range(len(objs) - 1, 0, -1):
        if is_body(objs[i]):
            return ".".join(names[:i]) + "."
    last = objs[-1].getLinkedObject(True) if len(objs) > 1 else None
    bodies = []
    if is_body(last):
        bodies = [last]
    elif last is not None and last.isDerivedFrom("App::Part"):
        bodies = [o for o in last.Group if is_body(o)]
    if len(bodies) == 1 and len(objs) > 1:
        inner = bodies[0].Name + "."  # also for a link straight to the body: "BracketLink.Body."
        return ".".join(names) + "." + inner
    return None


def _resolve(root, path):
    try:
        return root.getSubObject(path, retType=1)
    except Exception:  # noqa: BLE001
        return None


def split(root, target_path, source_path):
    """(component, path below it) for a reference from ``source_path`` into the body at ``target_path``:
    the component is where the two paths part, so the reference never points at an assembly that also
    holds the part being edited."""
    target = walk(root, target_path)
    source = walk(root, source_path)
    k = 1
    while k < len(source) and k < len(target) and source[k] is target[k]:
        k += 1
    if k >= len(source):
        raise InContextError("That is the part being edited or what holds it: pick another part")
    body = target[-1]
    if any(o is body or o.getLinkedObject(True) is body for o in source[k:]):
        raise InContextError(f"{body.Label} cannot take geometry from another copy of itself")
    names, element = _names(source_path)
    rest = names[k:] + [element]
    return source[k], ".".join(rest)


# ----- making a reference ----------------------------------------------------------------------------

def reference(root, target_path, source_paths, label=None):
    """A SubShapeBinder in the body at ``target_path`` (from ``root``) holding the geometry at
    ``source_paths`` (also from ``root``), placed in the body's frame through the assembly and kept there.
    The caller recomputes (or uses reference_and_recompute)."""
    if isinstance(source_paths, str):
        source_paths = [source_paths]
    if not source_paths:
        raise InContextError("Pick an edge, a face or a part of another part first")
    body = walk(root, target_path)[-1]
    if not is_body(body):
        raise InContextError(f"{body.Label} is not a body: edit a body in context first")
    support, order = {}, []
    for path in source_paths:
        obj, sub = split(root, target_path, path)
        if obj not in support:
            support[obj] = []
            order.append(obj)
        if sub not in support[obj]:
            support[obj].append(sub)
    if any(o.Document is not body.Document for o in order) and not body.Document.FileName:
        raise InContextError(f"Save {body.Document.Label} first: it will refer to another file")
    binder = body.newObject(BINDER, "Reference")
    sources = [f"{o.Label}{(': ' + ', '.join(s.split('.')[-1] or o.Label for s in support[o]))}"
               for o in order]
    binder.Label = label or ("Ref " + "; ".join(sources))[:120]
    path = _context_path(root, target_path, binder)
    binder.Context = (root, path)
    binder.Support = [(o, support[o]) for o in order]
    return binder


def _context_path(root, target_path, binder):
    for path in (target_path + binder.Name + ".",):
        if _resolve(root, path) is binder:
            return path
    raise InContextError(f"{binder.Label} cannot be reached from {root.Label}")


# ----- the references and their state ----------------------------------------------------------------

def _body_of(binder):
    return next((o for o in binder.InList if is_body(o)), None)


def is_reference(obj):
    """A SubShapeBinder with an assembly context that binds something from outside its own body (or one
    whose link was broken, which keeps its context)."""
    if not obj.isDerivedFrom(BINDER) or _context(obj)[0] is None:
        return False
    if str(obj.BindMode) == "Detached":
        return True
    body = _body_of(obj)
    own = set(o.Name for o in body.Group) | {body.Name} if body is not None else set()
    return any(o is not None and (o.Document is not obj.Document or o.Name not in own) for o, _s in obj.Support)


def references(docs=None):
    """Every SubShapeBinder with an assembly context in ``docs`` (default: all open documents)."""
    App = _app()
    docs = list(App.listDocuments().values()) if docs is None else docs
    return [o for d in docs for o in d.Objects if is_reference(o)]


def _mode(binder):
    return str(binder.BindMode)


def _context(binder):
    ctx = binder.Context
    if isinstance(ctx, tuple):
        root, path = ctx[0], ctx[1] if len(ctx) > 1 else ""
        if isinstance(path, (list, tuple)):
            path = path[0] if path else ""
        return root, path
    return ctx, ""


def _source_root_paths(binder):
    """[(assembly path of each bound element, the component, its path)] through the binder's context."""
    root, ctx = _context(binder)
    names = _names(ctx)[0]
    chain = walk(root, ctx)
    out = []
    for obj, subs in binder.Support:
        prefix = None
        for i in range(len(chain) - 1, -1, -1):
            children = chain[i].getSubObjects() if hasattr(chain[i], "getSubObjects") else []
            if obj.Name + "." in children:
                prefix = names[:i]
                break
        if prefix is None:
            return None
        for sub in (subs or [""]):
            out.append((".".join(prefix + [obj.Name]) + "." + sub, obj, sub))
    return out


def _signature(shape):
    """What is compared: each edge's length and centre of mass, in order (not the box round the shape: with
    the GUI up that comes from the display mesh and is not exact)."""
    return sorted((e.Length, e.CenterOfMass.x, e.CenterOfMass.y, e.CenterOfMass.z) for e in shape.Edges)


def _same(a, b, tol=TOL * 10):
    return len(a) == len(b) and all(abs(x - y) <= tol for ea, eb in zip(a, b) for x, y in zip(ea, eb))


def status(binder):
    """(state, note) of one reference: ok, stale, broken, detached or not checked."""
    import Part

    mode = _mode(binder)
    if mode == "Detached":
        return DETACHED, "a copy, no longer linked to its source"
    if not binder.Support:
        return BROKEN, "it binds nothing"
    root, ctx = _context(binder)
    if root is None:
        return UNCHECKED, "no assembly context"
    for obj, _subs in binder.Support:
        if obj is None or not obj.isAttachedToDocument():
            return BROKEN, "its source is gone"
    try:
        paths = _source_root_paths(binder)
    except InContextError as exc:
        return BROKEN, str(exc)
    if paths is None:
        return BROKEN, "its source is no longer in the assembly"
    shapes = []
    for path, obj, sub in paths:
        try:
            shape = root.getSubObject(path)
        except Exception:  # noqa: BLE001
            shape = None
        if shape is None or not hasattr(shape, "isNull") or shape.isNull():
            return BROKEN, f"{obj.Label} has no {sub or 'shape'} any more"
        shapes.append(shape)
    if not binder.Placement.isIdentity() or float(getattr(binder, "Offset", 0) or 0) != 0:
        return UNCHECKED, "it has its own offset"
    try:
        actual = root.getSubObject(ctx)
    except Exception:  # noqa: BLE001
        actual = None
    if actual is None or actual.isNull():
        return STALE, "it has no shape yet: recompute"
    expected = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
    if _same(_signature(actual), _signature(expected)):
        return OK, "matches its source"
    if mode == "Frozen":
        return STALE, "locked; its source has moved or changed since"
    return STALE, "its source has moved or changed: recompute"


def update(binder):
    """Recompute the reference from its source now; a locked one stays locked."""
    if _mode(binder) == "Detached":
        raise InContextError(f"{binder.Label} is detached: it has no source to update from")
    frozen = _mode(binder) == "Frozen"
    if frozen:
        binder.BindMode = "Synchronized"
    binder.touch()
    binder.Document.recompute()
    if frozen:
        binder.BindMode = "Frozen"
    return status(binder)


def lock(binder, on=True):
    """Freeze the reference (it keeps its shape when the source moves) or let it follow again."""
    if _mode(binder) == "Detached":
        raise InContextError(f"{binder.Label} is detached: it cannot follow its source again")
    binder.BindMode = "Frozen" if on else "Synchronized"
    if not on:
        binder.Document.recompute()


def break_link(binder):
    """Keep the shape, drop the link to the source (Detached)."""
    binder.BindMode = "Detached"
    binder.Document.recompute()


def stale(docs=None):
    """[(binder, state, note)] of the references that are not ok."""
    out = []
    for b in references(docs):
        state, note = status(b)
        if state not in (OK, DETACHED):
            out.append((b, state, note))
    return out


# ----- GUI ---------------------------------------------------------------------------------------------

def _icon(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _view():
    import FreeCADGui as Gui

    gdoc = Gui.ActiveDocument
    return gdoc.ActiveView if gdoc is not None else None


def active():
    """(body, root, path to the body) of the body edited in context in the active view, else None."""
    view = _view()
    if view is None or not hasattr(view, "getActiveObject"):
        return None
    try:
        body, root, sub = view.getActiveObject("pdbody", False)
    except Exception:  # noqa: BLE001
        return None
    if body is None or root is None or not sub or root is body:
        return None
    try:  # the stock tree activation names the link ("BracketLink."), this module the body itself
        if walk(root, sub)[-1] is not body:
            sub = body_path(root, sub) or sub
    except InContextError:
        return None
    return body, root, sub


def edit_in_context(root, path):
    """Make the body at ``path`` (from ``root``, in the assembly's view) the active body in place."""
    import FreeCADGui as Gui

    body = walk(root, path)[-1]
    if not is_body(body):
        raise InContextError(f"{body.Label} is not a body")
    gdoc = Gui.getDocument(root.Document.Name)
    editing = gdoc.getInEdit()
    if editing is not None and editing.Object.isDerivedFrom("Assembly::AssemblyObject"):
        gdoc.resetEdit()  # the assembly's own edit would take the next picks for joints
    view = gdoc.ActiveView
    view.setActiveObject("pdbody", root, path)
    found = view.getActiveObject("pdbody", False)
    if found[0] is not body:
        raise InContextError(f"{body.Label} could not be made active in {root.Label}")
    return body


def finish():
    view = _view()
    if view is not None:
        view.setActiveObject("pdbody", None)


def _picks():
    """[(root, path)] of the selection in the active document's view."""
    import FreeCADGui as Gui

    out = []
    doc = _app().ActiveDocument
    if doc is None:
        return out
    for sel in Gui.Selection.getSelectionEx(doc.Name, 0):
        for sub in ([s for s in sel.SubElementNames if s] or [""]):
            out.append((sel.Object, plain(sub)))
    return out


def reference_from_selection():
    """The reference made from the picked geometry into the body edited in context."""
    App = _app()
    now = active()
    if now is None:
        raise InContextError("Edit a part in context first (Edit in Context), then pick geometry of another part")
    body, root, path = now
    picks = [(r, s) for r, s in _picks() if s]
    if not picks:
        raise InContextError("Pick an edge, a face or a vertex of another part")
    if any(r is not root for r, _s in picks):
        raise InContextError(f"Pick in {root.Label}, the assembly the part is edited in")
    App.setActiveTransaction("Reference from assembly")
    try:
        binder = reference(root, path, [s for _r, s in picks])
        binder.Document.recompute()
    except Exception:
        App.closeActiveTransaction(True)
        raise
    App.closeActiveTransaction()
    return binder


def _tell(title, text, warn=False):
    import FreeCADGui as Gui
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setObjectName("BruceWorxInContextMessage")
    box.setIcon(QtWidgets.QMessageBox.Warning if warn else QtWidgets.QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    _state["box"] = box
    box.open()
    return box


def _status_bar(text):
    import FreeCADGui as Gui

    Gui.getMainWindow().statusBar().showMessage(text, 6000)


class _EditCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_EDIT), "MenuText": "Edit in Context",
                "ToolTip": "Edit the selected part where it sits in this assembly: its body becomes the active "
                           "body, and the parts around it can be picked for references. Run it again to finish."}

    def IsActive(self):
        return enabled() and _app().ActiveDocument is not None

    def Activated(self):
        now = active()
        if now is not None:
            finish()
            _status_bar(f"Finished editing {now[0].Label} in context")
            return
        for root, sub in _picks():
            path = body_path(root, sub)
            if path:
                try:
                    body = edit_in_context(root, path)
                except InContextError as exc:
                    _tell("Edit in Context", str(exc), warn=True)
                    return
                _status_bar(f"Editing {body.Label} in {root.Label}: pick another part's geometry and use "
                            "Reference from Assembly")
                return
        _tell("Edit in Context", "Select a part of this assembly (a component, or a face of it) first.", warn=True)


class _ReferenceCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_REFERENCE), "MenuText": "Reference from Assembly",
                "ToolTip": "Bring the picked edges, faces or parts of other parts into the part edited in context, "
                           "where they sit in the assembly. They follow when those parts move."}

    def IsActive(self):
        return enabled() and active() is not None

    def Activated(self):
        try:
            binder = reference_from_selection()
        except InContextError as exc:
            _tell("Reference from Assembly", str(exc), warn=True)
            return
        _status_bar(f"{binder.Label} added to {binder.Document.Label}")


class _ReferencesCommand:
    def GetResources(self):
        return {"Pixmap": _icon(ICON_REFERENCES), "MenuText": "In-Context References",
                "ToolTip": "The references parts take from their assemblies, whether each still matches its "
                           "source, and Update, Lock or Break for each."}

    def IsActive(self):
        return enabled() and _app().ActiveDocument is not None

    def Activated(self):
        show_dialog()


def _dialog_class():
    from PySide import QtWidgets

    class ReferencesDialog(QtWidgets.QDialog):
        COLUMNS = ("Reference", "In part", "Source", "Mode", "State")

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxInContextReferences")
            self.setWindowTitle("In-Context References")
            self.resize(760, 360)
            layout = QtWidgets.QVBoxLayout(self)
            self.tree = QtWidgets.QTreeWidget()
            self.tree.setHeaderLabels(list(self.COLUMNS))
            self.tree.setRootIsDecorated(False)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            layout.addWidget(self.tree)
            row = QtWidgets.QHBoxLayout()
            self.buttons = {}
            for key, text in (("update", "Update"), ("lock", "Lock"), ("unlock", "Unlock"),
                              ("break", "Break link"), ("refresh", "Check again")):
                button = QtWidgets.QPushButton(text)
                button.setObjectName(f"BruceWorxInContext_{key}")
                button.clicked.connect(lambda _c=False, k=key: self.act(k))
                row.addWidget(button)
                self.buttons[key] = button
            row.addStretch(1)
            close = QtWidgets.QPushButton("Close")
            close.clicked.connect(lambda _c=False: self.close())
            row.addWidget(close)
            layout.addLayout(row)
            self.rows = []
            self.fill()

        def fill(self):
            self.tree.clear()
            self.rows = []
            for binder in references():
                state, note = status(binder)
                sources = "; ".join(f"{o.Label}: {', '.join(s or '(whole)' for s in subs)}"
                                    for o, subs in binder.Support if o is not None) or "(none)"
                body = _body_of(binder)
                item = QtWidgets.QTreeWidgetItem([binder.Label, f"{body.Label if body else ''} ({binder.Document.Label})",
                                                  sources, _mode(binder), f"{state}: {note}"])
                self.tree.addTopLevelItem(item)
                self.rows.append(binder)
            for i in range(len(self.COLUMNS)):
                self.tree.resizeColumnToContents(i)

        def chosen(self):
            return [self.rows[self.tree.indexOfTopLevelItem(i)] for i in self.tree.selectedItems()]

        def act(self, key):
            App = _app()
            if key == "refresh":
                self.fill()
                return
            picked = self.chosen()
            if not picked:
                return
            App.setActiveTransaction({"update": "Update references", "lock": "Lock references",
                                      "unlock": "Unlock references", "break": "Break references"}[key])
            problems = []
            try:
                for binder in picked:
                    try:
                        if key == "update":
                            update(binder)
                        elif key == "lock":
                            lock(binder, True)
                        elif key == "unlock":
                            lock(binder, False)
                        elif key == "break":
                            break_link(binder)
                    except InContextError as exc:
                        problems.append(str(exc))
            finally:
                App.closeActiveTransaction()
            self.fill()
            if problems:
                _tell("In-Context References", "\n".join(problems), warn=True)

    return ReferencesDialog


def show_dialog():
    import FreeCADGui as Gui

    dialog = _state["dialog"]
    if dialog is None:
        dialog = _dialog_class()(Gui.getMainWindow())
        _state["dialog"] = dialog
    else:
        dialog.fill()
    dialog.show()
    dialog.raise_()
    return dialog


def install():
    import FreeCADGui

    FreeCADGui.addCommand(CMD_EDIT, _EditCommand())
    FreeCADGui.addCommand(CMD_REFERENCE, _ReferenceCommand())
    FreeCADGui.addCommand(CMD_REFERENCES, _ReferencesCommand())
