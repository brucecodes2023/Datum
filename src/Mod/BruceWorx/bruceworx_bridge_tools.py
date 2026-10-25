# SPDX-License-Identifier: LGPL-2.1-or-later
"""What the Claude Code bridge can do (AUT-03): the tools behind bruceworx_bridge.

call(tool, args, headless) runs one tool with checked arguments (bruceworx_bridge.validate)
and returns something JSON can carry. Modelling tools and run_python run inside one
document transaction, so Edit > Undo takes each call back; a failed call is rolled back.

Objects are named by Name or Label. "doc" defaults to the active document.
Vault tools drive the hooks add-on's engine (bruceworx/pdm_workspace.py, vault_setup.py)
on the vault named by Preferences/BruceWorx/VaultPath, with the actor pinned for that
vault at Preferences/BruceWorx/VaultActors/<vault id> (the same pin the vault client uses).
"""

from __future__ import annotations

import base64
import contextlib
import io
import math
import os
import tempfile
import traceback

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ACTORS_PATH = PREFS_PATH + "/VaultActors"
TRANSACTION_KINDS = ("model", "python")
MAX_LIST = 300  # longest list a read tool returns
_session = {}  # run_python's namespace, kept between calls until the session is revoked


class ToolError(Exception):
    """A tool failed in a way worth one plain sentence."""


def reset_session():
    _session.clear()


def _app():
    import FreeCAD

    return FreeCAD


# ----- dispatch ---------------------------------------------------------------------------


def call(tool: str, args: dict, headless: bool):
    import bruceworx_bridge as bb

    kind = bb.kind_of(tool)
    func = globals().get("t_" + tool)
    if func is None:
        raise ToolError(f"{tool} is not implemented")
    if kind not in TRANSACTION_KINDS:
        return func(args)
    App = _app()
    doc = _doc(args.get("doc"), required=False) if "doc" in bb.SPECS[tool][1] else App.ActiveDocument
    if doc is None:
        return func(args)
    if not App.GuiUp and not doc.UndoMode:
        doc.UndoMode = 1  # FreeCADCmd documents start without undo
    doc.openTransaction(f"Claude: {tool}")
    try:
        result = func(args)
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return result


# ----- lookups and conversions --------------------------------------------------------------


def _doc(name=None, required=True):
    App = _app()
    if name:
        try:
            return App.getDocument(name)
        except Exception:
            for doc in App.listDocuments().values():
                if doc.Label == name:
                    return doc
            raise ToolError(f"no open document named {name!r}; list_documents shows the open ones")
    doc = App.ActiveDocument
    if doc is None and required:
        raise ToolError("no active document; create one with new_document or open one with open_document")
    return doc


def _obj(doc, ref):
    obj = doc.getObject(ref)
    if obj is None:
        matches = doc.getObjectsByLabel(ref)
        obj = matches[0] if matches else None
    if obj is None:
        raise ToolError(f"{doc.Name} has no object named {ref!r}; object_tree lists them")
    return obj


def _vec(values, name="vector"):
    App = _app()
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or len(values) not in (2, 3):
        raise ToolError(f"{name} must be [x, y, z]")
    values = [float(v) for v in values] + ([0.0] if len(values) == 2 else [])
    return App.Vector(*values)


def jsonable(value, depth=0):
    """A property value as JSON: numbers, text, lists, and plain dicts for FreeCAD types."""
    App = _app()
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, App.Vector):
        return [round(value.x, 9), round(value.y, 9), round(value.z, 9)]
    if isinstance(value, App.Placement):
        axis = value.Rotation.Axis
        return {"base": jsonable(value.Base), "axis": jsonable(axis),
                "angle_deg": round(math.degrees(value.Rotation.Angle), 9)}
    if isinstance(value, App.Rotation):
        return {"axis": jsonable(value.Axis), "angle_deg": round(math.degrees(value.Angle), 9)}
    if isinstance(value, App.Units.Quantity):
        return value.UserString
    if isinstance(value, App.DocumentObject):
        return value.Name
    if isinstance(value, (list, tuple)):
        items = [jsonable(v, depth + 1) for v in value[:MAX_LIST]]
        return items + ([f"... {len(value) - MAX_LIST} more"] if len(value) > MAX_LIST else [])
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in list(value.items())[:MAX_LIST]}
    if type(value).__name__ in ("Shape", "TopoShape", "Solid", "Compound"):
        return _shape_summary(value)
    if hasattr(value, "BoundBox") and hasattr(value, "isNull"):
        return _shape_summary(value)
    return str(value)


def _shape_summary(shape):
    try:
        if shape.isNull():
            return {"null": True}
        box = shape.BoundBox
        return {"solids": len(shape.Solids), "faces": len(shape.Faces), "edges": len(shape.Edges),
                "volume_mm3": round(shape.Volume, 6), "bbox": _bbox(box)}
    except Exception as exc:
        return str(exc)


def _bbox(box):
    def r(values):
        return [round(v, 9) + 0.0 for v in values]

    return {"min": r([box.XMin, box.YMin, box.ZMin]), "max": r([box.XMax, box.YMax, box.ZMax]),
            "size": r([box.XLength, box.YLength, box.ZLength])}


def _recompute(doc, *objects):
    doc.recompute()
    bad = [o for o in objects if o is not None and hasattr(o, "isValid") and not o.isValid()]
    if bad:
        details = []
        for obj in bad:
            status = ", ".join(str(s) for s in (getattr(obj, "State", []) or []))
            details.append(f"{obj.Name} ({obj.TypeId}) failed: {status}")
        raise ToolError("; ".join(details))


def _summary(obj):
    out = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    shape = getattr(obj, "Shape", None)
    if shape is not None and not shape.isNull():
        out["volume_mm3"] = round(shape.Volume, 6)
        out["bbox"] = _bbox(shape.BoundBox)
    return out


def _body_of(obj):
    try:
        body = obj.getParentGeoFeatureGroup()
    except Exception:
        body = None
    if body is None or not body.isDerivedFrom("PartDesign::Body"):
        return None
    return body


def _hide(obj):
    try:
        obj.Visibility = False
    except Exception:
        pass


# ----- read tools -----------------------------------------------------------------------------


def t_list_documents(_args):
    App = _app()
    active = App.ActiveDocument
    out = []
    for doc in App.listDocuments().values():
        out.append({"name": doc.Name, "label": doc.Label, "file": doc.FileName or None,
                    "objects": len(doc.Objects), "active": doc is active,
                    "modified": bool(getattr(doc, "Modified", False))})
    return out


def _children(obj):
    if obj.isDerivedFrom("App::Origin"):
        return []
    group = getattr(obj, "Group", None)
    return list(group) if isinstance(group, (list, tuple)) else []


def _node(obj, seen):
    seen.add(obj.Name)
    node = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    if hasattr(obj, "isValid") and not obj.isValid():
        node["error"] = True
    if obj.isDerivedFrom("PartDesign::Body") and getattr(obj, "Tip", None) is not None:
        node["tip"] = obj.Tip.Name
    if obj.isDerivedFrom("App::Link") and getattr(obj, "LinkedObject", None) is not None:
        linked = obj.LinkedObject
        node["links_to"] = f"{linked.Document.Name}#{linked.Name}"
    kids = [c for c in _children(obj) if c.Name not in seen]
    if kids:
        node["children"] = [_node(c, seen) for c in kids]
    return node


def t_object_tree(args):
    doc = _doc(args["doc"])
    inner = set()
    for obj in doc.Objects:
        for child in _children(obj):
            inner.add(child.Name)
        if obj.isDerivedFrom("App::Origin"):
            inner.update(o.Name for o in getattr(obj, "OriginFeatures", []) or [])
            if obj.InList:  # a body's or part's own origin
                inner.add(obj.Name)
    seen = set()
    roots = []
    for obj in doc.Objects:
        if obj.Name in inner or obj.Name in seen:
            continue
        roots.append(_node(obj, seen))
    return {"doc": doc.Name, "label": doc.Label, "file": doc.FileName or None, "tree": roots}


def t_get_properties(args):
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    props = {}
    for name in obj.PropertiesList:
        if name in ("ExpressionEngine", "Proxy", "_ElementMapVersion"):
            continue
        try:
            value = getattr(obj, name)
        except Exception as exc:
            value = f"<{exc}>"
        props[name] = {"type": obj.getTypeIdOfProperty(name).replace("App::Property", ""),
                       "group": obj.getGroupOfProperty(name), "value": jsonable(value)}
    return {"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "properties": props}


# ----- dialog boxes ---------------------------------------------------------------------------


def _dialogs():
    """Visible dialogs and message boxes, the active modal one first; never the bridge's consent prompt."""
    from PySide import QtWidgets
    import bruceworx_bridge as bb

    app = QtWidgets.QApplication.instance()
    if app is None:
        return []
    found = []
    modal = app.activeModalWidget()
    if modal is not None and modal.isVisible():
        found.append(modal)
    for w in app.topLevelWidgets():
        if w not in found and w.isVisible() and isinstance(w, QtWidgets.QDialog):
            found.append(w)
    return [w for w in found if w.objectName() != bb.DIALOG_NAME]


def dialog_buttons(widget):
    """The texts of a dialog's visible push buttons, without mnemonics ("&Yes" -> "Yes")."""
    from PySide import QtWidgets

    return [b.text().replace("&", "") for b in widget.findChildren(QtWidgets.QPushButton) if b.isVisible() and b.text()]


def _describe_dialog(widget):
    from PySide import QtWidgets

    if isinstance(widget, QtWidgets.QMessageBox):
        text, info = widget.text(), widget.informativeText()
    else:
        labels = [lab.text() for lab in widget.findChildren(QtWidgets.QLabel) if lab.isVisible() and lab.text()]
        text, info = " | ".join(labels[:6]), ""
    fields = [e.text() for e in widget.findChildren(QtWidgets.QLineEdit) if e.isVisible()]
    return {"title": widget.windowTitle(), "class": widget.metaObject().className(), "text": text,
            "info": info, "buttons": dialog_buttons(widget), "fields": fields, "modal": widget.isModal()}


def t_dialogs(_args):
    return {"dialogs": [_describe_dialog(w) for w in _dialogs()]}


def pick_dialog(described, title=None):
    """Index of the dialog to answer: the one whose title contains ``title``, else the first (active) one.
    macOS message boxes have no title, so their text and informative text are searched too."""
    if not described:
        return None
    if title:
        wanted = title.lower()
        for keys in (("title",), ("text", "info")):
            for i, d in enumerate(described):
                if any(wanted in (d.get(k) or "").lower() for k in keys):
                    return i
        return None
    return 0


def t_answer_dialog(args):
    from PySide import QtCore, QtWidgets

    dialogs = _dialogs()
    index = pick_dialog([_describe_dialog(w) for w in dialogs], args.get("title"))
    if index is None:
        raise ToolError("no such dialog is open" if args.get("title") else "no dialog is open")
    dialog = dialogs[index]
    if args.get("text") is not None:
        if isinstance(dialog, QtWidgets.QInputDialog):
            dialog.setTextValue(args["text"])
        else:
            edits = [e for e in dialog.findChildren(QtWidgets.QLineEdit) if e.isVisible()]
            if len(edits) != 1:
                raise ToolError(f"{dialog.windowTitle()!r} has {len(edits)} text fields; cannot type into it")
            edits[0].setText(args["text"])
    wanted = args["button"].replace("&", "").strip().lower()
    buttons = [b for b in dialog.findChildren(QtWidgets.QPushButton) if b.isVisible()]
    match = [b for b in buttons if b.text().replace("&", "").strip().lower() == wanted]
    if not match:
        raise ToolError(f"{dialog.windowTitle()!r} has no button {args['button']!r}; it has {dialog_buttons(dialog)}")
    button = match[0]
    # Queued, so it runs after this answer is sent (the click may end the dialog's loop). invokeMethod on the
    # click slot, because PySide6 can drop a Python callable given to QTimer.singleShot before it fires.
    QtCore.QMetaObject.invokeMethod(button, "click", QtCore.Qt.QueuedConnection)
    return {"dialog": dialog.windowTitle(), "clicked": button.text().replace("&", "")}


def t_selection(_args):
    App = _app()
    if not App.GuiUp:
        return {"headless": True, "selection": []}
    import FreeCADGui

    out = []
    for sel in FreeCADGui.Selection.getSelectionEx("", 0):
        out.append({"doc": sel.DocumentName, "object": sel.ObjectName,
                    "label": sel.Object.Label if sel.Object else "",
                    "elements": list(sel.SubElementNames),
                    "points": [jsonable(p) for p in sel.PickedPoints]})
    return {"headless": False, "selection": out}


def t_measure(args):
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ToolError(f"{obj.Name} has no shape to measure")
    out = {"name": obj.Name, "label": obj.Label, "volume_mm3": shape.Volume, "area_mm2": shape.Area,
           "bbox": _bbox(shape.BoundBox), "solids": len(shape.Solids)}
    try:
        centre = shape.CenterOfMass if shape.Solids else shape.CenterOfGravity
        out["center_of_mass"] = jsonable(centre)
    except Exception:
        pass
    material = getattr(obj, "ShapeMaterial", None)
    if material is not None:
        out["material"] = getattr(material, "Name", "")
    try:
        from bruceworx import partprops

        mass = partprops.computed_mass(obj)
        if mass is not None:
            out["mass"] = mass
            if partprops.computed_material(obj) == "":
                out["mass_note"] = "no material chosen; set ShapeMaterial for a real mass"
    except Exception:
        pass
    return out


_VIEWS = {"iso": "viewIsometric", "front": "viewFront", "back": "viewRear", "top": "viewTop",
          "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight"}


def _fit_objects(doc):
    """Fit the view to the document's visible objects (fitAll would include the ground grid)."""
    import FreeCADGui

    kept = FreeCADGui.Selection.getSelectionEx("", 0)
    shown = [o for o in doc.Objects if getattr(o, "Visibility", False) and not o.InList
             and getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    if not shown:
        FreeCADGui.SendMsgToActiveView("ViewFit")
        return
    FreeCADGui.Selection.clearSelection()
    for obj in shown:
        FreeCADGui.Selection.addSelection(obj)
    FreeCADGui.SendMsgToActiveView("ViewSelection")
    FreeCADGui.Selection.clearSelection()
    for sel in kept:
        if sel.SubElementNames:
            for sub in sel.SubElementNames:
                FreeCADGui.Selection.addSelection(sel.DocumentName, sel.ObjectName, sub)
        else:
            FreeCADGui.Selection.addSelection(sel.DocumentName, sel.ObjectName)


def t_screenshot(args):
    App = _app()
    if not App.GuiUp:
        raise ToolError("screenshot needs the FreeCAD window; this is a headless FreeCAD")
    import FreeCADGui

    gdoc = FreeCADGui.ActiveDocument
    view = getattr(gdoc, "ActiveView", None) if gdoc else None
    if view is None or not hasattr(view, "saveImage"):
        raise ToolError("no 3D view is active")
    name = (args["view"] or "").lower()
    if name:
        if name != "fit" and name not in _VIEWS:
            raise ToolError(f"view must be one of fit, {', '.join(_VIEWS)}")
        animated = view.isAnimationEnabled()
        view.setAnimationEnabled(False)  # the image is taken right away
        try:
            if name in _VIEWS:
                getattr(view, _VIEWS[name])()
            _fit_objects(gdoc.Document)
        finally:
            view.setAnimationEnabled(animated)
    width = max(64, min(int(args["width"]), 3000))
    height = max(64, min(int(args["height"]), 3000))
    fd, path = tempfile.mkstemp(prefix="bw-claude-", suffix=".png")
    os.close(fd)
    try:
        view.saveImage(path, width, height, "Current")
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"png_base64": base64.b64encode(data).decode("ascii"), "width": width, "height": height}


# ----- documents --------------------------------------------------------------------------------


def t_new_document(args):
    App = _app()
    doc = App.newDocument(args["name"] or "Unnamed")
    if args["label"]:
        doc.Label = args["label"]
    if not App.GuiUp:
        doc.UndoMode = 1
    App.setActiveDocument(doc.Name)
    return {"doc": doc.Name, "label": doc.Label}


def t_open_document(args):
    App = _app()
    path = os.path.abspath(os.path.expanduser(args["path"]))
    if not os.path.isfile(path):
        raise ToolError(f"no file at {path}")
    for doc in App.listDocuments().values():
        if doc.FileName and os.path.abspath(doc.FileName) == path:
            App.setActiveDocument(doc.Name)
            return {"doc": doc.Name, "label": doc.Label, "already_open": True}
    doc = App.openDocument(path)
    if not App.GuiUp:
        doc.UndoMode = 1
    App.setActiveDocument(doc.Name)
    return {"doc": doc.Name, "label": doc.Label, "objects": len(doc.Objects)}


def t_recompute(args):
    doc = _doc(args["doc"])
    doc.recompute()
    bad = [o.Name for o in doc.Objects if hasattr(o, "isValid") and not o.isValid()]
    return {"doc": doc.Name, "invalid": bad}


def t_undo(args):
    doc = _doc(args["doc"])
    steps = max(1, int(args["steps"]))
    names = list(doc.UndoNames)
    if not names:
        raise ToolError(f"{doc.Name} has nothing to undo")
    done = []
    for _ in range(min(steps, len(names))):
        done.append(doc.UndoNames[0])
        doc.undo()
    doc.recompute()
    return {"doc": doc.Name, "undone": done, "undo_left": list(doc.UndoNames)[:10]}


def t_save(args):
    doc = _doc(args["doc"])
    path = args["path"]
    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".fcstd"):
            path += ".FCStd"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc.saveAs(path)
    else:
        if not doc.FileName:
            raise ToolError(f"{doc.Name} has never been saved; pass a path")
        doc.save()
    return {"doc": doc.Name, "file": doc.FileName}


def t_export_step(args):
    doc = _doc(args["doc"])
    path = os.path.abspath(os.path.expanduser(args["path"]))
    if not path.lower().endswith((".step", ".stp")):
        raise ToolError("path must end in .step or .stp")
    if args["objects"]:
        objects = [_obj(doc, str(name)) for name in args["objects"]]
    else:
        from bruceworx.numbering import part_objects

        objects = part_objects(doc)
        if not objects:
            raise ToolError(f"{doc.Name} has no solid to export; name the objects")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from bruceworx.freecad_io import export_step

    export_step(objects, path)
    return {"path": path, "bytes": os.path.getsize(path), "objects": [o.Name for o in objects]}


# ----- PartDesign --------------------------------------------------------------------------------


def _activate_body(body):
    App = _app()
    if not App.GuiUp:
        return
    try:
        import FreeCADGui

        gdoc = FreeCADGui.getDocument(body.Document.Name)
        gdoc.ActiveView.setActiveObject("pdbody", body)
    except Exception:
        pass


def t_new_body(args):
    doc = _doc(args["doc"])
    body = doc.addObject("PartDesign::Body", "Body")
    if args["label"]:
        body.Label = args["label"]
    doc.recompute()
    _activate_body(body)
    return {"body": body.Name, "label": body.Label}


def _find_body(doc, ref):
    if ref:
        body = _obj(doc, ref)
        if not body.isDerivedFrom("PartDesign::Body"):
            raise ToolError(f"{body.Name} is a {body.TypeId}, not a body")
        return body
    App = _app()
    if App.GuiUp:
        try:
            import FreeCADGui

            active = FreeCADGui.getDocument(doc.Name).ActiveView.getActiveObject("pdbody")
            if active is not None:
                return active
        except Exception:
            pass
    bodies = [o for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
    if len(bodies) == 1:
        return bodies[0]
    if not bodies:
        raise ToolError(f"{doc.Name} has no body; create one with new_body")
    raise ToolError(f"{doc.Name} has {len(bodies)} bodies; name one ({', '.join(b.Name for b in bodies)})")


_PLANES = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}
_AXES = {"X": "X_Axis", "Y": "Y_Axis", "Z": "Z_Axis"}


def _origin_feature(body, role):
    for feature in body.Origin.OriginFeatures:
        if feature.Role == role:
            return feature
    raise ToolError(f"{body.Name} has no {role}")


def _support(doc, body, plane):
    text = (plane or "XY").strip()
    if text.upper() in _PLANES:
        return [(_origin_feature(body, _PLANES[text.upper()]), "")]
    if ":" in text:
        name, element = text.split(":", 1)
    elif "." in text:
        name, element = text.rsplit(".", 1)
    else:
        raise ToolError("plane must be XY, XZ, YZ or Object:FaceN")
    obj = _obj(doc, name)
    if not element.startswith("Face"):
        raise ToolError(f"{text}: a sketch goes on a face (FaceN)")
    return [(obj, element)]


def _geometry(items):
    """Part geometry for the sketch items, with a rectangle split in four lines."""
    import Part

    App = _app()
    geos, kinds = [], []
    for n, item in enumerate(items):
        if not isinstance(item, dict) or len(item) != 1:
            raise ToolError(f"geometry[{n}] must be one of {{line: [x1,y1,x2,y2]}}, {{circle: [cx,cy,r]}}, "
                            "{arc: [cx,cy,r,start_deg,end_deg]}, {rectangle: [x,y,width,height]}")
        (kind, values), = item.items()
        try:
            v = [float(x) for x in values]
        except (TypeError, ValueError):
            raise ToolError(f"geometry[{n}].{kind} takes numbers") from None
        need = {"line": 4, "circle": 3, "arc": 5, "rectangle": 4}.get(kind)
        if need is None or len(v) != need:
            raise ToolError(f"geometry[{n}]: {kind} needs {need} numbers" if need else
                            f"geometry[{n}]: unknown kind {kind!r} (line, circle, arc, rectangle)")
        if kind == "line":
            geos.append(Part.LineSegment(App.Vector(v[0], v[1], 0), App.Vector(v[2], v[3], 0)))
            kinds.append("line")
        elif kind == "circle":
            if v[2] <= 0:
                raise ToolError(f"geometry[{n}]: the radius must be positive")
            geos.append(Part.Circle(App.Vector(v[0], v[1], 0), App.Vector(0, 0, 1), v[2]))
            kinds.append("circle")
        elif kind == "arc":
            circle = Part.Circle(App.Vector(v[0], v[1], 0), App.Vector(0, 0, 1), v[2])
            geos.append(Part.ArcOfCircle(circle, math.radians(v[3]), math.radians(v[4])))
            kinds.append("arc")
        else:
            x, y, w, h = v
            if w == 0 or h == 0:
                raise ToolError(f"geometry[{n}]: a rectangle needs a width and a height")
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            for i in range(4):
                a, b = corners[i], corners[(i + 1) % 4]
                geos.append(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
                kinds.append("line")
    return geos, kinds


def auto_constraints(points, kinds, tol=1e-6):
    """Sketcher constraints for touching ends and axis-parallel lines.

    ``points``: per geometry, (start, end) as (x, y) tuples, or None for a circle.
    Returns tuples: ("Coincident", g1, pos1, g2, pos2), ("Horizontal", g), ("Vertical", g).
    Pure, so it is tested without FreeCAD.
    """
    out = []
    ends = []
    for g, pts in enumerate(points):
        if pts is None:
            continue
        ends.append((g, 1, pts[0]))
        ends.append((g, 2, pts[1]))
    joined = set()
    for i, (g1, p1, a) in enumerate(ends):
        for g2, p2, b in ends[i + 1:]:
            if g1 == g2 or (g2, p2) in joined:
                continue
            if abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol:
                out.append(("Coincident", g1, p1, g2, p2))
                joined.add((g2, p2))
    for g, (kind, pts) in enumerate(zip(kinds, points)):
        if kind != "line":
            continue
        (x1, y1), (x2, y2) = pts
        if abs(y1 - y2) <= tol and abs(x1 - x2) > tol:
            out.append(("Horizontal", g))
        elif abs(x1 - x2) <= tol and abs(y1 - y2) > tol:
            out.append(("Vertical", g))
    return out


def _ends(geo):
    try:
        s, e = geo.StartPoint, geo.EndPoint
        return ((s.x, s.y), (e.x, e.y))
    except Exception:
        return None


_DIMENSIONS = {"distance": "Distance", "distance_x": "DistanceX", "distance_y": "DistanceY",
               "radius": "Radius", "diameter": "Diameter", "angle": "Angle"}


def t_create_sketch(args):
    import Sketcher

    doc = _doc(args["doc"])
    body = _find_body(doc, args["body"])
    support = _support(doc, body, args["plane"])
    geos, kinds = _geometry(args["geometry"])
    if not geos:
        raise ToolError("geometry is empty")
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    if args["label"]:
        sketch.Label = args["label"]
    sketch.AttachmentSupport = support
    sketch.MapMode = "FlatFace"
    first = sketch.GeometryCount
    sketch.addGeometry(geos, False)
    constraints = []
    if args["auto_constraints"]:
        for c in auto_constraints([_ends(g) if k != "circle" else None for g, k in zip(geos, kinds)], kinds):
            if c[0] == "Coincident":
                constraints.append(Sketcher.Constraint("Coincident", first + c[1], c[2], first + c[3], c[4]))
            else:
                constraints.append(Sketcher.Constraint(c[0], first + c[1]))
    for n, dim in enumerate(args["dimensions"] or []):
        if not isinstance(dim, dict) or "kind" not in dim or "geometry" not in dim or "value" not in dim:
            raise ToolError(f"dimensions[{n}] must be {{kind, geometry, value}} "
                            f"(kind: {', '.join(_DIMENSIONS)})")
        kind = _DIMENSIONS.get(str(dim["kind"]).lower())
        if kind is None:
            raise ToolError(f"dimensions[{n}]: kind must be one of {', '.join(_DIMENSIONS)}")
        g = int(dim["geometry"])
        if not 0 <= g < len(geos):
            raise ToolError(f"dimensions[{n}]: geometry {g} is not in this sketch (0..{len(geos) - 1})")
        value = float(dim["value"])
        if kind == "Angle":
            value = math.radians(value)
        constraints.append(Sketcher.Constraint(kind, first + g, value))
    if constraints:
        sketch.addConstraint(constraints)
    _recompute(doc, sketch)
    wires = sketch.Shape.Wires if not sketch.Shape.isNull() else []
    return {"sketch": sketch.Name, "label": sketch.Label, "body": body.Name,
            "geometry": len(geos), "constraints": sketch.ConstraintCount,
            "closed_wires": sum(1 for w in wires if w.isClosed()), "open_wires": sum(1 for w in wires if not w.isClosed()),
            "malformed": list(getattr(sketch, "MalformedConstraints", []) or []),
            "conflicting": list(getattr(sketch, "Conflicting", []) or [])}


def _sketch_and_body(args):
    doc = _doc(args["doc"])
    sketch = _obj(doc, args["sketch"])
    body = _body_of(sketch)
    if body is None:
        raise ToolError(f"{sketch.Name} is not in a body")
    return doc, sketch, body


def _profile_feature(args, type_id, base_name, set_size):
    doc, sketch, body = _sketch_and_body(args)
    feature = body.newObject(type_id, base_name)
    if args.get("label"):
        feature.Label = args["label"]
    feature.Profile = sketch
    set_size(feature)
    _hide(sketch)
    _recompute(doc, feature)
    return {"feature": feature.Name, "label": feature.Label, "body": body.Name,
            "body_volume_mm3": round(body.Shape.Volume, 6) if not body.Shape.isNull() else 0.0}


def _symmetric(feature, on):
    if "SideType" in feature.PropertiesList:  # FreeCAD 1.1: Midplane is deprecated
        if on:
            feature.SideType = "Symmetric"
    else:
        feature.Midplane = on


def t_pad(args):
    def size(f):
        if args["through_all"]:
            f.Type = "ThroughAll"
        else:
            f.Length = float(args["length"])
        f.Reversed = bool(args["reversed"])
        _symmetric(f, bool(args["midplane"]))

    return _profile_feature(args, "PartDesign::Pad", "Pad", size)


def t_pocket(args):
    def size(f):
        if args["through_all"]:
            f.Type = "ThroughAll"
        else:
            f.Length = float(args["depth"])
        f.Reversed = bool(args["reversed"])
        _symmetric(f, bool(args["midplane"]))

    return _profile_feature(args, "PartDesign::Pocket", "Pocket", size)


def _axis_ref(doc, sketch, body, text):
    text = (text or "V_Axis").strip()
    if text in ("V_Axis", "H_Axis"):
        return (sketch, [text])
    if text.upper() in _AXES:
        return (_origin_feature(body, _AXES[text.upper()]), [""])
    if text.startswith("Edge"):
        return (sketch, [text])
    if ":" in text:
        name, element = text.split(":", 1)
        return (_obj(doc, name), [element])
    raise ToolError("axis must be V_Axis, H_Axis, X, Y, Z, EdgeN (of the sketch) or Object:EdgeN")


def t_revolve(args):
    doc, sketch, body = _sketch_and_body(args)
    type_id, name = ("PartDesign::Groove", "Groove") if args["groove"] else ("PartDesign::Revolution", "Revolution")
    feature = body.newObject(type_id, name)
    if args["label"]:
        feature.Label = args["label"]
    feature.Profile = sketch
    feature.ReferenceAxis = _axis_ref(doc, sketch, body, args["axis"])
    feature.Angle = float(args["angle"])
    _hide(sketch)
    _recompute(doc, feature)
    return {"feature": feature.Name, "label": feature.Label, "body": body.Name,
            "body_volume_mm3": round(body.Shape.Volume, 6)}


def t_hole(args):
    def size(f):
        f.Threaded = False
        f.Diameter = float(args["diameter"])
        if args["through_all"]:
            f.DepthType = "ThroughAll"
        else:
            f.DepthType = "Dimension"
            f.Depth = float(args["depth"])

    return _profile_feature(args, "PartDesign::Hole", "Hole", size)


def _edges(obj, edges):
    names = []
    for e in edges:
        text = str(e).split(".")[-1]
        if not text.startswith("Edge") or not text[4:].isdigit():
            raise ToolError(f"{e!r} is not an edge name (EdgeN)")
        if int(text[4:]) > len(obj.Shape.Edges) or int(text[4:]) < 1:
            raise ToolError(f"{obj.Name} has edges Edge1..Edge{len(obj.Shape.Edges)}; {text} is not one")
        names.append(text)
    if not names:
        raise ToolError("name at least one edge")
    return names


def _dress_up(args, pd_type, part_type, size_prop, size):
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    names = _edges(obj, args["edges"])
    if size <= 0:
        raise ToolError(f"{size_prop.lower()} must be positive")
    body = _body_of(obj) if obj.isDerivedFrom("PartDesign::Feature") else None
    if obj.isDerivedFrom("PartDesign::Body"):
        body, obj = obj, obj.Tip
        if obj is None:
            raise ToolError(f"{body.Name} has no features yet")
    if body is not None:
        feature = body.newObject(pd_type, pd_type.split("::")[1])
        feature.Base = (obj, names)
        setattr(feature, size_prop, size)
    else:
        feature = doc.addObject(part_type, part_type.split("::")[1])
        feature.Base = obj
        feature.Edges = [(int(n[4:]), size, size) for n in names]
        _hide(obj)
    _recompute(doc, feature)
    return _summary(feature)


def t_fillet(args):
    return _dress_up(args, "PartDesign::Fillet", "Part::Fillet", "Radius", float(args["radius"]))


def t_chamfer(args):
    return _dress_up(args, "PartDesign::Chamfer", "Part::Chamfer", "Size", float(args["size"]))


# ----- patterns ------------------------------------------------------------------------------------


def _is_pd_feature(obj):
    return obj.isDerivedFrom("PartDesign::Feature") and not obj.isDerivedFrom("PartDesign::Body")


def _axis_line(body, vector):
    """The body origin axis along ``vector`` (X, Y or Z), or None."""
    App = _app()
    v = App.Vector(vector)
    if v.Length < 1e-12:
        raise ToolError("a direction or axis cannot be zero")
    v.normalize()
    for role, axis in (("X_Axis", (1, 0, 0)), ("Y_Axis", (0, 1, 0)), ("Z_Axis", (0, 0, 1))):
        dot = v.dot(App.Vector(*axis))
        if abs(abs(dot) - 1) < 1e-9:
            return _origin_feature(body, role), dot < 0
    return None, False


def _axis_vector(value):
    App = _app()
    if isinstance(value, str):
        key = value.strip().upper()
        if key not in _AXES:
            raise ToolError("axis must be X, Y, Z or [x, y, z]")
        return App.Vector(*{"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[key])
    return _vec(value, "axis")


def t_rectangular_pattern(args):
    App = _app()
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    d1 = _vec(args["direction"] or [1, 0, 0], "direction")
    count, spacing = int(args["count"]), float(args["spacing"])
    count2 = int(args["count2"] or 1)
    if count < 1 or count2 < 1:
        raise ToolError("counts must be at least 1")
    d2 = _vec(args["direction2"] or [0, 1, 0], "direction2")
    if _is_pd_feature(obj):
        body = _body_of(obj)
        line1, rev1 = _axis_line(body, d1)
        if line1 is None:
            raise ToolError("a PartDesign pattern runs along X, Y or Z; use [1,0,0], [0,1,0] or [0,0,1]")
        feature = body.newObject("PartDesign::LinearPattern", "LinearPattern")
        feature.Originals = [obj]
        feature.Direction = (line1, [""])
        feature.Reversed = rev1
        feature.Mode = "Offset" if "Offset" in feature.getEnumerationsOfProperty("Mode") else feature.Mode
        feature.Offset = spacing
        feature.Length = spacing * (count - 1)
        feature.Occurrences = count
        if count2 > 1:
            line2, rev2 = _axis_line(body, d2)
            if line2 is None:
                raise ToolError("direction2 must be X, Y or Z for a PartDesign pattern")
            feature.Direction2 = (line2, [""])
            feature.Reversed2 = rev2
            if "Offset" in feature.getEnumerationsOfProperty("Mode2"):
                feature.Mode2 = "Offset"
            feature.Offset2 = float(args["spacing2"])
            feature.Length2 = float(args["spacing2"]) * (count2 - 1)
            feature.Occurrences2 = count2
        _hide(obj)
        _recompute(doc, feature)
        return dict(_summary(feature), engine="PartDesign::LinearPattern")
    import bruceworx_pattern as bp

    params = bp.default_params(bp.RECTANGULAR)
    params.update({"direction1": d1, "count1": count, "distance1": spacing, "type1": "Spacing",
                   "direction2": d2, "count2": count2, "distance2": float(args["spacing2"]), "type2": "Spacing"})
    array = bp.create_linked(obj, params, doc)
    _recompute(doc, array)
    return dict(_summary(array), engine="BruceWorx pattern", instances=int(array.ElementCount))


def t_circular_pattern(args):
    App = _app()
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    axis = _axis_vector(args["axis"])
    centre = _vec(args["center"] or [0, 0, 0], "center")
    count, angle = int(args["count"]), float(args["angle"])
    if count < 1:
        raise ToolError("count must be at least 1")
    if _is_pd_feature(obj):
        body = _body_of(obj)
        line, rev = _axis_line(body, axis)
        if line is None or centre.Length > 1e-9:
            raise ToolError("a PartDesign circular pattern turns about the body's X, Y or Z axis through the origin")
        feature = body.newObject("PartDesign::PolarPattern", "PolarPattern")
        feature.Originals = [obj]
        feature.Axis = (line, [""])
        feature.Reversed = rev
        feature.Angle = angle
        feature.Occurrences = count
        _hide(obj)
        _recompute(doc, feature)
        return dict(_summary(feature), engine="PartDesign::PolarPattern")
    import bruceworx_pattern as bp

    params = bp.default_params(bp.CIRCULAR)
    full = abs(angle - 360.0) < 1e-9
    params.update({"axis_base": centre, "axis_direction": axis, "count1": count,
                   "circular_type": "Full" if full else "Angle", "angle": angle})
    array = bp.create_linked(obj, params, doc)
    _recompute(doc, array)
    return dict(_summary(array), engine="BruceWorx pattern", instances=int(array.ElementCount))


# ----- properties and parameters ---------------------------------------------------------------------


def _convert(obj, prop, value):
    App = _app()
    doc = obj.Document
    kind = obj.getTypeIdOfProperty(prop)
    if kind.endswith("PropertyPlacement") and isinstance(value, dict):
        base = _vec(value.get("base", [0, 0, 0]), "base")
        axis = _vec(value.get("axis", [0, 0, 1]), "axis")
        return App.Placement(base, App.Rotation(axis, float(value.get("angle_deg", 0.0))))
    if ("Vector" in kind or kind.endswith("PropertyPosition") or kind.endswith("PropertyDirection")) \
            and isinstance(value, list):
        return _vec(value)
    if "PropertyLink" in kind and not kind.endswith("List"):
        if value in (None, ""):
            return None
        return _obj(doc, str(value))
    if kind.endswith("PropertyLinkList") and isinstance(value, list):
        return [_obj(doc, str(v)) for v in value]
    return value


def t_set_property(args):
    doc = _doc(args["doc"])
    obj = _obj(doc, args["object"])
    prop = args["property"]
    if prop not in obj.PropertiesList:
        raise ToolError(f"{obj.Name} has no property {prop!r}; get_properties lists them")
    if obj.getTypeIdOfProperty(prop) == "App::PropertyEnumeration":
        allowed = obj.getEnumerationsOfProperty(prop) or []
        if str(args["value"]) not in allowed:
            raise ToolError(f"{prop} must be one of {', '.join(allowed)}")
    try:
        setattr(obj, prop, _convert(obj, prop, args["value"]))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{obj.Name}.{prop}: {exc}") from None
    _recompute(doc, obj)
    return {"object": obj.Name, "property": prop, "value": jsonable(getattr(obj, prop))}


def t_set_parameter(args):
    doc = _doc(args["doc"])
    alias = args["alias"]
    sheets = [_obj(doc, args["spreadsheet"])] if args["spreadsheet"] else \
        [o for o in doc.Objects if o.isDerivedFrom("Spreadsheet::Sheet")]
    for sheet in sheets:
        cell = sheet.getCellFromAlias(alias)
        if cell:
            break
    else:
        cell = None
    if not cell:
        names = ", ".join(s.Name for s in sheets) or "none"
        raise ToolError(f"no spreadsheet cell has the alias {alias!r} (spreadsheets: {names})")
    value = args["value"]
    sheet.set(cell, str(value))
    _recompute(doc, sheet)
    return {"spreadsheet": sheet.Name, "cell": cell, "alias": alias, "value": jsonable(sheet.get(alias))}


# ----- assemblies ---------------------------------------------------------------------------------


def _assembly(doc, ref, create):
    if ref:
        asm = _obj(doc, ref)
        if asm.TypeId != "Assembly::AssemblyObject":
            raise ToolError(f"{asm.Name} is not an assembly")
        return asm
    App = _app()
    if App.GuiUp:
        try:
            import FreeCADGui

            active = FreeCADGui.getDocument(doc.Name).ActiveView.getActiveObject("part")
            if active is not None and active.TypeId == "Assembly::AssemblyObject":
                return active
        except Exception:
            pass
    found = [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"]
    if found:
        return found[0]
    if not create:
        raise ToolError(f"{doc.Name} has no assembly")
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    asm.newObject("Assembly::JointGroup", "Joints")
    return asm


def _joint_group(asm):
    for obj in asm.Group:
        if obj.TypeId == "Assembly::JointGroup":
            return obj
    return asm.newObject("Assembly::JointGroup", "Joints")


def _grounded(asm):
    import JointObject

    return [j for j in _joint_group(asm).Group if isinstance(getattr(j, "Proxy", None), JointObject.GroundedJoint)]


def t_add_component(args):
    App = _app()
    doc = _doc(args["doc"])
    path = os.path.abspath(os.path.expanduser(args["path"]))
    if not os.path.isfile(path):
        raise ToolError(f"no file at {path}")
    if not doc.FileName:
        raise ToolError(f"save {doc.Name} first (save with a path): a link to another file is stored "
                        "relative to the assembly's file")
    source = None
    for other in App.listDocuments().values():
        if other.FileName and os.path.abspath(other.FileName) == path:
            source = other
    if source is None:
        try:
            source = App.openDocument(path, True)
        except TypeError:
            source = App.openDocument(path)
        App.setActiveDocument(doc.Name)
    if source is doc:
        raise ToolError("a document cannot be a component of itself")
    from bruceworx.freecad_io import main_object

    part = main_object(source)
    if part is None:
        solids = [o for o in source.Objects if getattr(o, "Shape", None) is not None and not o.Shape.isNull()
                  and o.Shape.Solids and not o.InList]
        part = solids[0] if solids else None
    if part is None:
        raise ToolError(f"{os.path.basename(path)} has no part, body or assembly to insert")
    asm = _assembly(doc, args["assembly"], create=True)
    link = asm.newObject("App::Link", part.Label)
    link.LinkedObject = part
    link.Label = part.Label
    if args["placement"]:
        link.Placement = App.Placement(_vec(args["placement"], "placement"), App.Rotation())
    grounded = False
    if not _grounded(asm):
        import JointObject

        ground = _joint_group(asm).newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, link)
        if App.GuiUp:
            JointObject.ViewProviderGroundedJoint(ground.ViewObject)
        grounded = True
    _recompute(doc, link)
    return {"component": link.Name, "label": link.Label, "assembly": asm.Name,
            "links_to": f"{source.Name}#{part.Name}", "grounded": grounded}


def t_add_joint(args):
    import JointObject

    doc = _doc(args["doc"])
    kind = next((t for t in JointObject.JointTypes if t.lower() == args["type"].strip().lower()), None)
    if kind is None:
        raise ToolError(f"type must be one of {', '.join(JointObject.JointTypes)}")
    asm = _assembly(doc, args["assembly"], create=False)
    refs = []
    for ref, vertex in ((args["ref1"], args["vertex1"]), (args["ref2"], args["vertex2"])):
        path = ref.strip()
        if "." not in path:
            raise ToolError(f"{ref!r}: a reference is Component.ElementN, e.g. Base.Face6 (path inside the assembly)")
        if vertex:
            vertex = vertex.strip()
            if "." not in vertex:
                vertex = path.rsplit(".", 1)[0] + "." + vertex
        else:
            vertex = path
        head = path.split(".", 1)[0]
        if asm.getObject(head) is None and doc.getObject(head) is None:
            raise ToolError(f"{ref!r}: {head} is not in {asm.Name}")
        refs.append([asm, [path, vertex]])
    joint = _joint_group(asm).newObject("App::FeaturePython", kind)
    JointObject.Joint(joint, JointObject.JointTypes.index(kind))
    if hasattr(joint, "KeepPosition"):
        joint.KeepPosition = bool(args["keep_position"])
    if _app().GuiUp:
        JointObject.ViewProviderJoint(joint.ViewObject)
    joint.Proxy.setJointConnectors(joint, refs)
    _recompute(doc, joint)
    return {"joint": joint.Name, "type": kind, "assembly": asm.Name,
            "reference1": refs[0][1], "reference2": refs[1][1]}


# ----- Python ------------------------------------------------------------------------------------------


class _PythonError(Exception):
    def __init__(self, text):
        super().__init__(text)
        self.bridge_text = text


def t_run_python(args):
    App = _app()
    if args["reset"] or not _session:
        _session.clear()
        _session.update({"__name__": "__claude__", "App": App, "FreeCAD": App})
        if App.GuiUp:
            import FreeCADGui

            _session.update({"Gui": FreeCADGui, "FreeCADGui": FreeCADGui})
    _session["doc"] = App.ActiveDocument
    _session.pop("result", None)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(args["code"], "<claude>", "exec"), _session)
    except BaseException:
        text = traceback.format_exc(limit=8)
        parts = [text.strip()]
        if out.getvalue():
            parts.append("stdout:\n" + out.getvalue()[-4000:])
        if err.getvalue():
            parts.append("stderr:\n" + err.getvalue()[-4000:])
        raise _PythonError("\n".join(parts)) from None
    result = {"stdout": out.getvalue()[-20000:], "stderr": err.getvalue()[-8000:]}
    if "result" in _session:
        try:
            result["result"] = repr(_session["result"])[:20000]
        except Exception as exc:
            result["result"] = f"<repr failed: {exc}>"
    return result


# ----- vault ---------------------------------------------------------------------------------------------


class _Vault:
    def __init__(self, path=None):
        import json

        import FreeCAD

        from bruceworx import pdm_workspace as ws
        from bruceworx.pdm_folder import FolderVault, default_actor, find_ref_file

        path = path or FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "").strip()
        if not path:
            raise ToolError("no vault is set (Preferences > BruceWorx > VaultPath, or pass vault)")
        try:
            ref = find_ref_file(os.path.expanduser(path))
        except Exception as exc:
            raise ToolError(f"no vault at {path}: {exc}") from None
        with open(ref, encoding="utf-8") as handle:
            vault_id = str(json.load(handle).get("vault_id") or "")
        actor = default_actor()
        if vault_id:
            actors = FreeCAD.ParamGet(ACTORS_PATH)
            saved = actors.GetString(vault_id, "")
            actor = ws.pin_actor(saved, actor)
            if not saved.strip():
                actors.SetString(vault_id, actor)
        self.vault = FolderVault(ref, actor=actor)
        self.work = ws.Workspace(self.vault)
        self.root = self.vault.root
        self.name = self.vault.ref.get("name") or os.path.basename(self.root)


def _status(st):
    return {"part_number": st.part_number, "status": st.code, "label": st.label, "state": st.state,
            "revision": st.revision, "description": st.description, "type": st.item_type,
            "checked_out_by": st.lock or None, "work_copy": st.path or None, "writable": st.writable}


def _vault_errors(func):
    def wrapped(args):
        from bruceworx.pdm_folder import LifecycleError

        try:
            return func(args)
        except LifecycleError as exc:
            raise ToolError(str(exc)) from None

    wrapped.__name__ = func.__name__
    return wrapped


@_vault_errors
def t_vault_status(args):
    v = _Vault(args["vault"])
    out = {"vault": v.name, "root": v.root, "me": v.vault.actor}
    if args["part_number"]:
        out["item"] = _status(v.work.status(args["part_number"]))
        return out
    statuses = list(v.work.statuses().values())
    counts = {}
    for st in statuses:
        counts[st.code] = counts.get(st.code, 0) + 1
    out["counts"] = counts
    out["items"] = [_status(st) for st in statuses[:MAX_LIST]]
    if len(statuses) > MAX_LIST:
        out["more"] = len(statuses) - MAX_LIST
    return out


@_vault_errors
def t_vault_my_work(args):
    v = _Vault(args["vault"])
    work = v.work.my_work()
    return {"vault": v.name, "me": v.vault.actor,
            "checked_out": [_status(s) for s in work.checked_out],
            "in_review": [_status(s) for s in work.in_review],
            "newer": [_status(s) for s in work.newer]}


def _numbering(v):
    from bruceworx.sources import JsonNumberSource
    from bruceworx.vault_setup import numbering_path

    path = numbering_path(v.root)
    if not os.path.isfile(path):
        raise ToolError(f"{v.name} has no numbering.json")
    return JsonNumberSource(path), path


@_vault_errors
def t_vault_next_part_number(args):
    v = _Vault(args["vault"])
    source, _path = _numbering(v)
    try:
        number = source.preview(args["program"], args["document_type"])
    except (KeyError, ValueError) as exc:
        raise ToolError(f"{exc}; programs: {', '.join(source.programs())}; "
                        f"document types: {', '.join(source.document_types())}") from None
    return {"next": number, "reserved": False, "vault": v.name}


@_vault_errors
def t_vault_assign_part_number(args):
    from bruceworx.numbering import assign_part_numbers

    doc = _doc(args["doc"])
    v = _Vault(args["vault"])
    source, _path = _numbering(v)
    objects = [_obj(doc, str(n)) for n in args["objects"]] if args["objects"] else None
    try:
        result = assign_part_numbers(doc, source, args["program"], args["document_type"], objects=objects)
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from None
    return result


@_vault_errors
def t_vault_checkout(args):
    v = _Vault(args["vault"])
    path = v.work.checkout(args["part_number"])
    return {"part_number": args["part_number"], "work_copy": path, "exists": os.path.isfile(path),
            "status": _status(v.work.status(args["part_number"]))}


def _save_open_copy(path):
    """Save an open, changed document that is this work copy, so the check-in has the changes."""
    App = _app()
    for doc in App.listDocuments().values():
        if doc.FileName and os.path.abspath(doc.FileName) == os.path.abspath(path):
            doc.save()
            return doc.Name
    return None


@_vault_errors
def t_vault_checkin(args):
    comment = (args["comment"] or "").strip()
    if not comment:
        raise ToolError("a check-in needs a comment")
    v = _Vault(args["vault"])
    pn = args["part_number"]
    saved = None
    if v.vault.locks.get(pn) == v.vault.actor:
        saved = _save_open_copy(v.work.path(pn))
    version = v.work.checkin(pn, comment, keep_checked_out=bool(args["keep_checked_out"]))
    return {"part_number": pn, "revision": getattr(version, "revision", ""),
            "sha256": getattr(version, "sha256", ""), "saved_open_document": saved,
            "status": _status(v.work.status(pn))}


@_vault_errors
def t_vault_undo_checkout(args):
    v = _Vault(args["vault"])
    v.work.undo_checkout(args["part_number"])
    return {"part_number": args["part_number"], "status": _status(v.work.status(args["part_number"]))}


@_vault_errors
def t_vault_get_latest(args):
    v = _Vault(args["vault"])
    path = v.work.get_latest(args["part_number"])
    return {"part_number": args["part_number"], "work_copy": path,
            "status": _status(v.work.status(args["part_number"]))}


def _candidate(c):
    return {"path": c.path, "kind": c.kind, "label": c.label, "include": c.include,
            "existing_part_number": c.existing_part_number or None,
            "part_number": c.proposed_part_number or None, "document_type": c.document_type,
            "issues": [f"{i.level}: {i.text}" for i in c.issues]}


@_vault_errors
def t_vault_import(args):
    from bruceworx import vault_setup

    v = _Vault(args["vault"])
    folder = os.path.abspath(os.path.expanduser(args["folder"]))
    if not os.path.isdir(folder):
        raise ToolError(f"no folder at {folder}")
    candidates = vault_setup.scan_for_import(folder)
    vault_setup.plan_numbers(candidates, v.root, args["program"])
    if args["dry_run"]:
        return {"dry_run": True, "vault": v.name, "files": [_candidate(c) for c in candidates]}
    writer = None
    try:
        import bruceworx_vaultwizard

        writer = bruceworx_vaultwizard._writer(v.root)  # imported files take the vault's units (PDM-09)
    except Exception:
        try:
            from bruceworx import freecad_io

            writer = getattr(freecad_io, "write_part_number", None)
        except Exception:
            pass
    report = vault_setup.run_import(v.vault, candidates, args["program"], writer=writer)
    return {"dry_run": False, "vault": v.name,
            "imported": [{"path": p, "part_number": n} for p, n in report.imported],
            "skipped": [{"path": p, "reason": r} for p, r in report.skipped],
            "failed": [{"path": p, "error": e} for p, e in report.failed]}


@_vault_errors
def t_vault_settings_csv(args):
    from bruceworx import vault_setup

    v = _Vault(args["vault"])
    settings = vault_setup.load_settings(v.root)
    text = vault_setup.csv_template(settings)
    out = {"vault": v.name, "csv": text}
    if args["path"]:
        out["path"] = vault_setup.write_csv_file(os.path.abspath(os.path.expanduser(args["path"])), settings)
    return out
