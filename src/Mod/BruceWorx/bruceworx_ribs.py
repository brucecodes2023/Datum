# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rib stations: a sketch repeated along its normal, scaled per station, driven by the Parameters sheet
(roadmap MOD-02, top-down design; the wing of 12 Nov 2026).

Select a sketch in a body (the root rib), then Construct > Rib Stations. For a count of N it makes
N - 1 further stations, each a datum plane parallel to the root sketch and a rib section on it, and
(when asked) a loft from the root through them. Three rows join the Parameters sheet:

    <name>_span    how far the last station is from the root
    <name>_ribs    how many stations, the root included
    <name>_taper   the size of the last rib as a fraction of the root (1 = no taper)

Station k of N - 1 sits at span * k / (N - 1) and is scaled 1 - (1 - taper) * k / (N - 1), about the
root sketch's origin. Those are expressions on the planes and ribs, so changing span or taper in the
Parameters tab rebuilds the planes, the ribs and the loft through FreeCAD's own recompute. Changing the
count adds or removes stations: that cannot happen inside a recompute, so a document observer does it
right after one (its own undo step, "Rib stations").

A rib section is a Part::Part2DObjectPython (RibProxy): its shape is the root sketch's shape, scaled, on
its own plane. It is made again from the root at every recompute, so it follows every edit of the root
sketch. It is geometry only: it has no constraints and cannot be opened in the Sketcher; edit the root.
One rib can be given its own Scale (clear its expression in the Property editor) for a custom planform.

Off switch: Preferences > BruceWorx > "Rib stations" (RibStations).
"""

from __future__ import annotations

import os
import re

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "RibStations"
COMMAND = "BruceWorx_RibStations"
ICON = "Heuretes_RibStations.svg"
SET_TYPE = "BruceWorxRibStations"  # the controller's Proxy.Type
MIN_COUNT, MAX_COUNT = 2, 200
DEFAULTS = {"count": 4, "span": 100.0, "taper": 1.0}  # what the panel shows first; not a design rule
_state = {"observer": None, "busy": False, "panel": None}


class RibError(Exception):
    """Something to tell the user in one sentence."""


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- the rib section ------------------------------------------------------------------------------


class RibProxy:
    """A copy of ``Source``'s shape scaled by ``Scale`` about the source sketch's origin, on the plane
    this object is attached to."""

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, obj):
        import FreeCAD

        source = getattr(obj, "Source", None)
        if source is None or source.Shape.isNull():
            raise RuntimeError("the rib's source sketch is missing or empty")
        scale = float(obj.Scale)
        if scale <= 0:
            raise RuntimeError(f"the rib's scale is {scale:g}; it must be above 0 (taper too strong?)")
        shape = source.Shape.copy()
        shape.Placement = FreeCAD.Placement()  # the source sketch's own plane coordinates
        if abs(scale - 1.0) > 1e-12:
            shape.scale(scale, FreeCAD.Vector(0, 0, 0))
        obj.positionBySupport()
        shape.Placement = obj.Placement
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, _state):
        return None


class RibView:
    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON)

    def dumps(self):
        return None

    def loads(self, _state):
        return None


class SetProxy:
    """The controller of one row of stations: what belongs to it and how many there should be."""

    Type = SET_TYPE

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, _obj):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def is_rib(obj):
    return obj is not None and isinstance(getattr(obj, "Proxy", None), RibProxy)


def is_set(obj):
    return obj is not None and isinstance(getattr(obj, "Proxy", None), SetProxy)


def sets(doc):
    return [o for o in doc.Objects if is_set(o)]


# ----- names and the Parameters sheet ---------------------------------------------------------------


def alias_base(label, taken):
    """A Parameters name for a sketch labelled ``label``: letters, digits and _, not starting with a
    digit, and with none of its three rows taken."""
    import bruceworx_parameters as P

    base = re.sub(r"[^0-9A-Za-z_]+", "_", label or "").strip("_").lower() or "ribs"
    if base[0].isdigit():
        base = "ribs_" + base
    taken = set(taken)
    candidate, i = base, 1
    while any(f"{candidate}_{s}" in taken or not P.valid_alias(f"{candidate}_{s}") for s in ("span", "ribs", "taper")):
        i += 1
        candidate = f"{base}{i}"
    return candidate


def _add_rows(doc, base, count, span, taper):
    import bruceworx_parameters as P

    sheet = P.find_sheet(doc) or P.make_sheet(doc)
    # a cell that starts with "-" is read as text, so a negative span goes in as a formula
    rows = ((f"{base}_span", f"{span:g} mm" if span >= 0 else f"={span:g} mm", "Rib stations: root to last station"),
            (f"{base}_ribs", str(int(count)), "Rib stations: how many, the root included"),
            (f"{base}_taper", f"{taper:g}", "Rib stations: size of the last rib (1 = same as the root)"))
    for alias, value, comment in rows:
        row = P.next_row(sheet.getUsedCells())
        sheet.set(f"A{row}", alias)
        sheet.set(f"B{row}", value)
        sheet.setAlias(f"B{row}", alias)
        sheet.set(f"C{row}", comment)
    sheet.recompute()
    if sheet.Document is not doc:
        sheet.Document.recompute()
    return sheet


def _expressions(controller, k):
    """(plane offset, rib scale) expressions of station ``k``."""
    ref, base = controller.SheetRef, controller.Base
    last = f"({ref}.{base}_ribs - 1)"
    return (f"{ref}.{base}_span * {k} / {last}",
            f"1 - (1 - {ref}.{base}_taper) * {k} / {last}")


# ----- making and syncing ---------------------------------------------------------------------------


def _body_of(obj):
    for parent in obj.InList:
        if parent.isDerivedFrom("PartDesign::Body"):
            return parent
    return None


def _members(controller, prop):
    doc = controller.Document
    return [o for o in (doc.getObject(n) for n in getattr(controller, prop)) if o is not None]


def _add_station(controller, root, body, k):
    import FreeCAD

    doc = controller.Document
    offset, scale = _expressions(controller, k)
    plane = body.newObject("PartDesign::Plane", "RibPlane")
    plane.Label = f"{root.Label} plane {k}"
    plane.AttachmentSupport = [(root, "")]
    plane.MapMode = "ObjectXY"  # the root sketch's own plane, wherever that is
    plane.AttachmentOffset = FreeCAD.Placement()
    plane.setExpression("AttachmentOffset.Base.z", offset)
    plane.Visibility = False
    rib = doc.addObject("Part::Part2DObjectPython", "Rib")
    RibProxy(rib)
    rib.addProperty("App::PropertyLink", "Source", "Rib", "The sketch this rib copies")
    rib.addProperty("App::PropertyFloat", "Scale", "Rib", "Size relative to the source sketch")
    rib.addProperty("App::PropertyString", "RibSet", "Rib", "The rib stations this belongs to")
    rib.setEditorMode("RibSet", 1)
    rib.Source, rib.Scale, rib.RibSet = root, 1.0, controller.Name
    rib.Label = f"{root.Label} rib {k}"
    body.addObject(rib)
    rib.AttachmentSupport = [(plane, "")]
    rib.MapMode = "FlatFace"
    rib.setExpression("Scale", scale)
    if FreeCAD.GuiUp:
        RibView(rib.ViewObject)
    controller.Planes = list(controller.Planes) + [plane.Name]
    controller.Ribs = list(controller.Ribs) + [rib.Name]
    return plane, rib


def _set_sections(controller):
    doc = controller.Document
    loft = doc.getObject(controller.Loft) if controller.Loft else None
    if loft is not None:
        loft.Sections = [(rib, [""]) for rib in _members(controller, "Ribs")]


def wanted(controller):
    return max(MIN_COUNT, min(int(controller.Count), MAX_COUNT))


def out_of_step(controller):
    """Does the number of stations differ from the count asked for?"""
    return len(_members(controller, "Ribs")) != wanted(controller) - 1


def sync(controller):
    """Add or remove stations at the far end until there are ``Count`` (the root included), and point the
    loft at them. No transaction and no recompute here. Returns how many were added (negative: removed)."""
    doc = controller.Document
    root = doc.getObject(controller.Root)
    body = _body_of(root) if root is not None else None
    if body is None:
        raise RibError(f"{controller.Label}: its root sketch is gone or no longer in a body")
    ribs, planes = _members(controller, "Ribs"), _members(controller, "Planes")
    controller.Ribs, controller.Planes = [r.Name for r in ribs], [p.Name for p in planes]
    target, before = wanted(controller) - 1, len(ribs)
    while len(ribs) > target:
        rib, plane = ribs.pop(), planes.pop() if len(planes) > target else None
        controller.Ribs = [r.Name for r in ribs]
        controller.Planes = [p.Name for p in planes]
        _set_sections(controller)  # the loft lets go before its section is removed
        doc.removeObject(rib.Name)
        if plane is not None:
            doc.removeObject(plane.Name)
    while len(ribs) < target:
        plane, rib = _add_station(controller, root, body, len(ribs) + 1)
        planes.append(plane)
        ribs.append(rib)
    _set_sections(controller)
    return len(ribs) - before


def make(root, count=4, span=100.0, taper=1.0, loft=True, base=None):
    """Rib stations from ``root`` (a sketch in a body). Returns the controller object. Runs inside the
    caller's transaction; recompute afterwards."""
    import FreeCAD

    import bruceworx_parameters as P

    if root is None or not root.isDerivedFrom("Part::Part2DObject") or is_rib(root):
        raise RibError("rib stations start from a sketch: select the root rib's sketch")
    body = _body_of(root)
    if body is None:
        raise RibError(f"{root.Label} is not in a body; rib stations are made in the sketch's body")
    count = int(count)
    if not MIN_COUNT <= count <= MAX_COUNT:
        raise RibError(f"the number of ribs must be {MIN_COUNT} to {MAX_COUNT}, the root included")
    if taper <= 0:
        raise RibError("taper is the size of the last rib as a fraction of the root; it must be above 0")
    doc = root.Document
    sheet = P.find_sheet(doc)
    taken = {alias for _cell, alias in P.sheet_aliases(sheet)}
    base = base or alias_base(root.Label, taken)
    sheet = _add_rows(doc, base, count, float(span), float(taper))
    controller = doc.addObject("App::FeaturePython", "RibStations")
    SetProxy(controller)
    for kind, name, text in (("App::PropertyString", "Root", "The root sketch"),
                             ("App::PropertyString", "Base", "The start of its Parameters names"),
                             ("App::PropertyString", "SheetRef", "How expressions name the Parameters sheet"),
                             ("App::PropertyStringList", "Planes", "The station planes, root outwards"),
                             ("App::PropertyStringList", "Ribs", "The rib sections, root outwards"),
                             ("App::PropertyString", "Loft", "The loft through the ribs, if any"),
                             ("App::PropertyInteger", "Count", "How many stations, the root included")):
        controller.addProperty(kind, name, "Rib stations", text)
        if name != "Count":
            controller.setEditorMode(name, 1)
    controller.Label = f"{root.Label} rib stations"
    controller.Root, controller.Base, controller.SheetRef = root.Name, base, P.sheet_ref(sheet, doc)
    controller.Count = count
    controller.setExpression("Count", f"{controller.SheetRef}.{base}_ribs")
    if FreeCAD.GuiUp:
        controller.ViewObject.Proxy = 0
    sync(controller)
    if loft:
        feature = body.newObject("PartDesign::AdditiveLoft", "RibLoft")
        feature.Label = f"{root.Label} loft"
        feature.Profile = (root, [""])
        controller.Loft = feature.Name
        _set_sections(controller)
    return controller


def sync_document(doc, inside_recompute=False):
    """Bring every set of the document into step with its count; one undo step. Returns what changed.

    ``inside_recompute``: called from the document's own "recomputed" signal, where a second document
    recompute is refused: the stations and the loft are then recomputed one by one, in order."""
    changed = []
    todo = [c for c in sets(doc) if _safe(out_of_step, c)]
    if not todo:
        return changed
    doc.openTransaction("Rib stations")
    try:
        for controller in todo:
            changed.append((controller.Name, sync(controller)))
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    if not inside_recompute:
        doc.recompute()
        return changed
    for controller in todo:
        loft = doc.getObject(controller.Loft) if controller.Loft else None
        for obj in _members(controller, "Planes") + _members(controller, "Ribs") + ([loft] if loft else []):
            obj.recompute()
        controller.purgeTouched()
    return changed


def _safe(func, *args):
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 -- an object on its way out
        return False


class _Observer:
    """After a recompute: has a count changed? Then add or remove stations (not possible inside one)."""

    def slotRecomputedDocument(self, doc):
        if _state["busy"] or not any(_safe(out_of_step, c) for c in sets(doc)):
            return
        import FreeCAD

        def run(inside=False):
            if _state["busy"]:
                return
            _state["busy"] = True
            try:
                sync_document(doc, inside)
            except Exception as exc:  # noqa: BLE001
                FreeCAD.Console.PrintError(f"Rib stations: {exc}\n")
            finally:
                _state["busy"] = False

        if FreeCAD.GuiUp:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, run)  # outside whatever the user's edit is still doing
        else:
            run(True)  # no event loop to wait for: do it here, object by object


def install_observer():
    import FreeCAD

    if _state["observer"] is None and enabled():
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])


def remove_observer():
    import FreeCAD

    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        try:
            FreeCAD.removeDocumentObserver(observer)
        except Exception:  # noqa: BLE001
            pass


# ----- GUI ------------------------------------------------------------------------------------------


def selected_root():
    """The sketch selected (not one of our rib sections), or None."""
    import FreeCADGui as Gui

    for obj in Gui.Selection.getSelection():
        if obj.isDerivedFrom("Part::Part2DObject") and not is_rib(obj) and _body_of(obj) is not None:
            return obj
    return None


def _panel_class():
    from PySide import QtWidgets

    class RibStationsPanel:
        def __init__(self, root):
            self.root = root
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle("Rib stations")
            self.form.setObjectName("BruceWorxRibStations")
            layout = QtWidgets.QFormLayout(self.form)
            layout.addRow("Root rib", QtWidgets.QLabel(root.Label, self.form))
            self.count = QtWidgets.QSpinBox(self.form)
            self.count.setRange(MIN_COUNT, MAX_COUNT)
            self.count.setValue(DEFAULTS["count"])
            self.count.setToolTip("How many ribs, the root included.")
            layout.addRow("Ribs", self.count)
            self.span = QtWidgets.QDoubleSpinBox(self.form)
            self.span.setRange(-1e6, 1e6)
            self.span.setDecimals(3)
            self.span.setSuffix(" mm")
            self.span.setValue(DEFAULTS["span"])
            self.span.setToolTip("From the root to the last rib, along the root sketch's normal. "
                                 "Negative: the other side.")
            layout.addRow("Span", self.span)
            self.taper = QtWidgets.QDoubleSpinBox(self.form)
            self.taper.setRange(1.0, 1000.0)
            self.taper.setDecimals(1)
            self.taper.setSuffix(" %")
            self.taper.setValue(DEFAULTS["taper"] * 100.0)
            self.taper.setToolTip("The size of the last rib as a share of the root. 100 %: no taper.")
            layout.addRow("Last rib size", self.taper)
            self.loft = QtWidgets.QCheckBox("Loft through the ribs", self.form)
            self.loft.setChecked(True)
            layout.addRow("", self.loft)
            note = QtWidgets.QLabel("Span, ribs and taper become rows of the Parameters sheet; change them "
                                    "there afterwards.", self.form)
            note.setWordWrap(True)
            layout.addRow(note)

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def accept(self):
            import FreeCADGui as Gui

            doc = self.root.Document
            doc.openTransaction("Rib stations")
            try:
                self.made = make(self.root, self.count.value(), self.span.value(), self.taper.value() / 100.0,
                                 self.loft.isChecked())
            except Exception as exc:  # noqa: BLE001
                doc.abortTransaction()
                QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Rib stations", str(exc))
                return False
            doc.commitTransaction()
            _state["panel"] = None
            Gui.Control.closeDialog()
            doc.recompute()
            return True

        def reject(self):
            import FreeCADGui as Gui

            _state["panel"] = None
            Gui.Control.closeDialog()
            return True

    return RibStationsPanel


def start():
    """The command. Returns the panel, or None when there is no sketch to start from."""
    import FreeCADGui as Gui
    from PySide import QtWidgets

    root = selected_root()
    if root is None:
        QtWidgets.QMessageBox.information(
            Gui.getMainWindow(), "Rib stations",
            "Select the root rib's sketch first (a sketch in a body). Rib stations repeat it along its "
            "normal, scaled per station, and can loft through the result.")
        return None
    if Gui.Control.activeDialog():
        return None
    panel = _state["panel"] = _panel_class()(root)
    Gui.Control.showDialog(panel)
    return panel


class _Command:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Rib Stations",
                "ToolTip": "Repeat the selected sketch along its normal as scaled rib sections on their own "
                           "planes, with a loft through them. Span, rib count and taper are rows of the "
                           "Parameters sheet; the ribs follow the root sketch."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None and enabled()

    def Activated(self):
        start()


def install():
    import FreeCADGui
    from PySide import QtWidgets

    FreeCADGui.addCommand(COMMAND, _Command())
    install_observer()
    QtWidgets.QApplication.instance().aboutToQuit.connect(lambda: remove_observer())
