# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dimensions of a code part on the model (roadmap AUT-05; the parts are bruceworx_codecad).

Select one code part and its dimensions show in the 3D view as chips, "plate_l = 80 mm".
Double-click a chip, type a value (or "=other * 2"), Return: one undo step, and the part rebuilds.
A dimension bound to the Parameters sheet is changed in the sheet, so everything else that reads
the name follows; one that was unbound on the part is changed on the part.

Where a dimension goes. A script does not say which geometry a name measures, so it is found:

* measures(shape) lists what can be measured: full circles (diameter), arcs (radius), straight
  edges (length) and the three extents of the bounding box.
* A dimension may attach to a measure of the same value, but only after a check: the script is
  run once more with that one dimension nudged, and the nudged shape must have a measure of the
  same kind with the nudged value. Equal numbers by coincidence fail the check. The checks run
  one per timer tick after the chips are up, and are remembered per script.
* An attached dimension is drawn as a dimension line with its chip at the line; the others
  (angles, counts, plain numbers, and lengths with nothing to attach to) are a row along the top
  of the view.

The check costs one run of the script per length; it is skipped when that would take longer than
CHECK_BUDGET seconds in all, and the dimensions are then only listed.

Off switch: Preferences/BruceWorx/CodeCadDimensions (bool, default on).
"""

from __future__ import annotations

import math

import bruceworx_codecad as cc

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ENABLED_KEY = "CodeCadDimensions"
KIND_ORDER = ("diameter", "edge", "extent", "radius")  # which measure a length takes when several fit
NUDGE = (1.0371, 0.0113)   # value -> value * a + b for the check: an odd step no other feature has
CHECK_BUDGET = 4.0         # seconds of script runs the checks of one part may take
MAX_LISTED = 14            # chips in the list before "+ n more"
LIST_TOP = 10              # px from the top of the view to the row of listed dimensions
TOL = 1e-6
UNITS = {"length": "mm", "angle": "°", "count": "", "number": ""}

_verified = {}  # (script hash, output index, name) -> tuple of kinds that passed the check


# ----- what can be measured ----------------------------------------------------------------------------


class Measure:
    """One measurable thing: ``kind``, its ``value`` in mm, and the two points it spans."""

    def __init__(self, kind, value, a, b):
        self.kind, self.value, self.a, self.b = kind, value, a, b

    def __repr__(self):
        return f"Measure({self.kind}, {self.value:g})"


def _same(a, b):
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


def measures(shape):
    """Everything on the shape a length dimension could mean."""
    import FreeCAD

    V = FreeCAD.Vector
    out = []
    if shape is None or shape.isNull():
        return out
    box = shape.BoundBox
    centre = box.Center
    seen = set()
    for edge in shape.Edges:
        curve = edge.Curve
        name = type(curve).__name__
        if name == "Circle":
            r, c, axis = curve.Radius, curve.Center, curve.Axis
            full = abs(abs(edge.LastParameter - edge.FirstParameter) - 2 * math.pi) < 1e-6
            key = ("c", round(c.x, 6), round(c.y, 6), round(c.z, 6), round(r, 6), full)
            if key in seen:
                continue
            seen.add(key)
            if full:
                d = c - centre
                d = d - axis * d.dot(axis)                 # in the circle's plane, pointing outboard
                if d.Length < 1e-9:
                    d = axis.cross(V(0, 0, 1))
                    if d.Length < 1e-9:
                        d = V(1, 0, 0)
                d.normalize()
                out.append(Measure("diameter", 2 * r, c - d * r, c + d * r))
            else:
                mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
                out.append(Measure("radius", r, V(c), mid))
        elif name == "Line" and edge.Length > TOL:
            a, b = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
            out.append(Measure("edge", edge.Length, a, b))
    corners = ((V(box.XMin, box.YMin, box.ZMin), V(box.XMax, box.YMin, box.ZMin)),
               (V(box.XMax, box.YMin, box.ZMin), V(box.XMax, box.YMax, box.ZMin)),
               (V(box.XMax, box.YMax, box.ZMin), V(box.XMax, box.YMax, box.ZMax)))
    for (a, b), length in zip(corners, (box.XLength, box.YLength, box.ZLength)):
        if length > TOL:
            out.append(Measure("extent", length, a, b))
    return out


def _rank(measure, centre):
    """Among measures of one kind and value: the outboard one, then the top one, then a stable order."""
    mid = (measure.a + measure.b) * 0.5
    return (-round((mid - centre).Length, 6), -round(mid.z, 6), round(mid.x, 6), round(mid.y, 6))


def match(dims, shape, allowed=None):
    """{name: Measure} for the length dimensions that have something to attach to.

    ``allowed`` maps a name to the kinds that passed the check; a name not in it attaches to nothing
    (None: no check, any kind of the same value will do)."""
    found = measures(shape)
    if not found:
        return {}
    centre = shape.BoundBox.Center
    used, out, taken = set(), {}, []

    def spread(measure):
        """Prefer geometry away from the dimensions already placed, so they do not pile onto one corner."""
        mid = (measure.a + measure.b) * 0.5
        apart = min(((mid - other).Length for other in taken), default=0.0)
        return (-round(apart, 6),) + _rank(measure, centre)

    for dim in dims:
        if dim["kind"] != "length" or dim["value"] <= TOL:
            continue
        kinds = KIND_ORDER if allowed is None else [k for k in KIND_ORDER if k in allowed.get(dim["name"], ())]
        for kind in kinds:
            fits = [m for m in found if m.kind == kind and id(m) not in used and _same(m.value, dim["value"])]
            if fits:
                best = min(fits, key=spread)
                used.add(id(best))
                taken.append((best.a + best.b) * 0.5)
                out[dim["name"]] = best
                break
    return out


def nudged(value):
    return value * NUDGE[0] + NUDGE[1]


def check(code, values, name, index=0, shape=None):
    """The kinds of measure that really follow ``name``: run the script with it nudged and compare."""
    key = (cc.script_hash(code), index, name)
    if key in _verified:
        return _verified[key]
    value = values[name]
    kinds = ()
    try:
        if shape is None:
            shape = cc.run_cached(code, values).shapes[index][1]
        before = measures(shape)
        after = measures(cc.run(code, dict(values, **{name: nudged(value)})).shapes[index][1])
        kinds = tuple(kind for kind in KIND_ORDER
                      if any(m.kind == kind and _same(m.value, value) for m in before)
                      and any(m.kind == kind and _same(m.value, nudged(value)) for m in after))
    except Exception:
        kinds = ()  # the nudged value does not build: attach to nothing
    _verified[key] = kinds
    return kinds


def known(code, index, name):
    """The check's answer when it was made already, else None."""
    return _verified.get((cc.script_hash(code), index, name))


def witness(measure, centre, offset):
    """The line segments [(p, q)] that draw a measure, and where its label goes."""
    a, b = measure.a, measure.b
    along = b - a
    if along.Length < TOL:
        return [], a
    unit = along * (1.0 / along.Length)
    if measure.kind in ("diameter", "radius"):
        tip = b + unit * offset
        return [(a, tip)], tip
    mid = (a + b) * 0.5
    out = mid - centre
    out = out - unit * out.dot(unit)                      # square to the edge, away from the middle
    if out.Length < 1e-9:
        import FreeCAD

        out = unit.cross(FreeCAD.Vector(0, 0, 1))
        if out.Length < 1e-9:
            out = unit.cross(FreeCAD.Vector(1, 0, 0))
    out.normalize()
    shift = out * offset
    gap = out * (offset * 0.15)
    return [(a + gap, a + shift + gap), (b + gap, b + shift + gap), (a + shift, b + shift)], mid + shift


# ----- reading and writing a dimension ---------------------------------------------------------------------


def chip_text(dim):
    value = dim["value"]
    number = str(int(value)) if dim["kind"] == "count" else format(float(value), ".6g")
    unit = UNITS[dim["kind"]]
    return f"{dim['name']} = {number}{' ' if unit.isalpha() else ''}{unit}".rstrip()


def edit_text(dim):
    value = dim["value"]
    number = str(int(value)) if dim["kind"] == "count" else format(float(value), ".9g")
    unit = {"length": " mm", "angle": " deg"}.get(dim["kind"], "")
    return number + unit


def _bound_sheet(obj, dim):
    """(sheet, cell) when the dimension follows a Parameters sheet cell of the same name, else (None, None)."""
    import FreeCAD

    expr = (dim.get("expression") or "").strip()
    if not expr.endswith("." + dim["name"]):
        return None, None
    ref = expr[:-len(dim["name"]) - 1]
    doc = obj.Document
    if "#" in ref:
        doc_name, ref = ref.split("#", 1)
        try:
            doc = FreeCAD.getDocument(doc_name)
        except Exception:
            return None, None
    sheet = doc.getObject(ref.strip("<>"))
    if sheet is None or not sheet.isDerivedFrom("Spreadsheet::Sheet"):
        return None, None
    cell = sheet.getCellFromAlias(dim["name"])
    return (sheet, cell) if cell else (None, None)


def set_dimension(obj, name, text):
    """Change one dimension of a code part from typed text, in one undo step. Returns where it was written:
    "sheet" or "part". Raises CodeCadError for text that is not a value or an expression."""
    import FreeCAD

    import bruceworx_parameters as P

    dim = next((d for d in cc.dimensions(obj) if d["name"] == name), None)
    if dim is None:
        raise cc.CodeCadError(f"{obj.Label} has no dimension {name}")
    kind, body = P.parse_edit(text)
    if kind == "clear":
        raise cc.CodeCadError("type a value")
    unit = {"length": "mm", "angle": "deg"}.get(dim["kind"], "")
    if kind == "value":
        try:
            quantity = FreeCAD.Units.Quantity(body if P.has_unit(body) or not unit else f"{body} {unit}")
        except Exception:
            raise cc.CodeCadError(f"{text!r} is not a value") from None
        if dim["kind"] == "count" and abs(quantity.Value - round(quantity.Value)) > 1e-9:
            raise cc.CodeCadError(f"{name} is a count: type a whole number")
    sheet, cell = _bound_sheet(obj, dim)
    doc = obj.Document
    doc.openTransaction(f"{obj.Label}: {name}")
    try:
        if sheet is not None:
            sheet.set(cell, P.cell_content(text, unit))
            where = "sheet"
        elif kind == "value":
            obj.setExpression(name, None)
            setattr(obj, name, int(round(quantity.Value)) if dim["kind"] == "count" else quantity.Value)
            where = "part"
        else:
            obj.setExpression(name, body)
            where = "part"
        if sheet is not None and sheet.Document is not doc:
            sheet.Document.recompute()
            P.update_dependents(sheet)
        doc.recompute()
        if sheet is not None:  # an expression that means nothing leaves the cell without a number
            value = sheet.get(name)
            if not isinstance(getattr(value, "Value", value), (int, float)):
                raise ValueError(f"{text!r} is not a value or an expression of other parameters")
    except Exception as exc:
        doc.abortTransaction()
        doc.recompute()
        raise cc.CodeCadError(f"{name}: {exc}") from None
    doc.commitTransaction()
    return where


def global_shape(obj):
    """The part's shape where it sits in the 3D view (inside an App::Part or not)."""
    shape = obj.Shape.copy()
    try:
        outer = obj.getGlobalPlacement().multiply(obj.Placement.inverse())
        if not outer.isIdentity():
            shape.transformShape(outer.toMatrix(), True)
    except Exception:
        pass
    return shape


# ----- the overlay ---------------------------------------------------------------------------------------------

_state = {"installed": False, "observers": [], "overlay": None, "classes": None, "timer": None}


def enabled():
    import FreeCAD

    import bruceworx_codecad_gui

    return bruceworx_codecad_gui.enabled() and bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(ENABLED_KEY, True))


def _classes():
    if _state["classes"] is not None:
        return _state["classes"]
    from PySide import QtCore, QtGui, QtWidgets

    import bruceworx_oncanvas as oc

    class Chip(QtWidgets.QFrame):
        """One dimension in the view: a label that turns into a field on double-click."""

        def __init__(self, overlay, dim, parent):
            super().__init__(parent)
            self.overlay, self.dim = overlay, dim
            self.setObjectName("BWCodeDimChip")
            self.label = QtWidgets.QLabel(chip_text(dim), self)
            self.field = QtWidgets.QLineEdit(self)
            self.field.setObjectName("BWCodeDimField")
            self.field.hide()
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(7, 2, 7, 2)
            layout.addWidget(self.label)
            layout.addWidget(self.field)
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setToolTip("Double-click to change" + ("" if dim.get("expression") else " (set on the part, "
                                                        "not in the Parameters sheet)"))
            self.field.returnPressed.connect(lambda: self.commit())
            self.field.installEventFilter(self)
            self.style_as(False)
            self.adjustSize()

        def style_as(self, attached, bad=False):
            r, g, b = (int(c * 255) for c in oc.accent_rgb())
            base = self.palette().color(QtGui.QPalette.Base)
            text = self.palette().color(QtGui.QPalette.Text)
            edge = "#d43a2f" if bad else (f"rgb({r},{g},{b})" if attached else
                                          f"rgba({text.red()},{text.green()},{text.blue()},70)")
            self.setStyleSheet(
                f"#BWCodeDimChip {{ background: rgba({base.red()},{base.green()},{base.blue()},225); "
                f"border: 1px solid {edge}; border-radius: 9px; }} "
                f"#BWCodeDimChip QLabel {{ color: rgb({text.red()},{text.green()},{text.blue()}); font-size: 11px; }} "
                "#BWCodeDimField { border: none; background: transparent; font-size: 11px; min-width: 70px; }")

        def editing(self):
            return self.field.isVisible()

        def mouseDoubleClickEvent(self, event):
            self.start_edit()
            event.accept()

        def start_edit(self):
            self.label.setText(self.dim["name"] + " =")
            self.field.setText(edit_text(self.dim))
            self.field.show()
            self.field.selectAll()
            self.field.setFocus()
            self.adjustSize()
            self.raise_()

        def stop_edit(self):
            self.field.hide()
            self.label.setText(chip_text(self.dim))
            self.adjustSize()

        def commit(self, text=None):
            text = self.field.text() if text is None else text
            ok = self.overlay.commit(self.dim["name"], text)
            if not ok:
                self.style_as(self.dim["name"] in self.overlay.attached, bad=True)
                self.field.selectAll()
            return ok

        def eventFilter(self, _watched, event):
            if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
                self.stop_edit()
                self.style_as(self.dim["name"] in self.overlay.attached)
                return True
            if event.type() == QtCore.QEvent.FocusOut and self.editing():
                QtCore.QTimer.singleShot(0, lambda: self.overlay.alive() and self.editing() and self.stop_edit())
            return False

    class Overlay:
        """The dimensions of one code part in one 3D view."""

        def __init__(self, view, view_widget, obj):
            from pivy import coin

            self.coin, self.view, self.view_widget = coin, view, view_widget
            self.doc_name, self.obj_name = obj.Document.Name, obj.Name
            self.chips, self.attached, self.anchors = {}, {}, {}
            self.more = None
            self.message = ""
            self.pending = []
            self.camera = None
            self.root = coin.SoSeparator()
            top = coin.SoAnnotation()
            self.root.addChild(top)
            pick = coin.SoPickStyle()
            pick.style = coin.SoPickStyle.UNPICKABLE
            top.addChild(pick)
            light = coin.SoLightModel()
            light.model = coin.SoLightModel.BASE_COLOR
            top.addChild(light)
            colour = coin.SoBaseColor()
            colour.rgb.setValue(*oc.accent_rgb())
            top.addChild(colour)
            style = coin.SoDrawStyle()
            style.lineWidth = 1.5
            top.addChild(style)
            self.coords = coin.SoCoordinate3()
            self.lines = coin.SoLineSet()
            top.addChild(self.coords)
            top.addChild(self.lines)
            self.view.getSceneGraph().addChild(self.root)
            self.rebuild()

        def part(self):
            import FreeCAD

            try:
                obj = FreeCAD.getDocument(self.doc_name).getObject(self.obj_name)
            except Exception:
                return None
            return obj if cc.is_code_part(obj) else None

        def alive(self):
            import shiboken6

            return _state["overlay"] is self and shiboken6.isValid(self.view_widget)

        def editing(self):
            return any(chip.editing() for chip in self.chips.values() if self._ok(chip))

        @staticmethod
        def _ok(widget):
            import shiboken6

            return widget is not None and shiboken6.isValid(widget)

        def _drop_chips(self):
            for chip in list(self.chips.values()) + [self.more]:
                if self._ok(chip):
                    chip.hide()
                    chip.deleteLater()
            self.chips, self.more = {}, None

        def rebuild(self):
            """Chips and lines from the part as it is now."""
            obj = self.part()
            if obj is None or not self.alive():
                return
            dims = cc.dimensions(obj)
            code = getattr(obj, cc.SCRIPT, "")
            index = getattr(obj, cc.OUTPUT, 0)
            shape = global_shape(obj)
            ok = getattr(obj, cc.STATUS, "") == cc.UP_TO_DATE
            allowed, self.pending = {}, []
            for dim in dims:
                if dim["kind"] != "length" or dim["value"] <= TOL or not ok:
                    continue
                kinds = known(code, index, dim["name"])
                if kinds is None:
                    self.pending.append(dim["name"])
                else:
                    allowed[dim["name"]] = kinds
            budget = (getattr(obj, cc.SECONDS, 0.0) or 0.05) * len(self.pending)
            if budget > CHECK_BUDGET:
                self.pending = []
                self.message = "dimensions are listed only: this script takes too long to check each one"
            self.attached = match(dims, shape, allowed) if ok else {}
            box = shape.BoundBox
            offset = max(box.DiagonalLength * 0.07, 1.0)
            points, counts, self.anchors = [], [], {}
            for name, measure in self.attached.items():
                segments, label = witness(measure, box.Center, offset)
                for p, q in segments:
                    points += [(p.x, p.y, p.z), (q.x, q.y, q.z)]
                    counts.append(2)
                self.anchors[name] = label
            self.coords.point.setValues(0, len(points), points)
            self.coords.point.setNum(len(points))
            self.lines.numVertices.setValues(0, len(counts), counts)
            self.lines.numVertices.setNum(len(counts))
            self._drop_chips()
            listed = 0
            for dim in dims:
                is_attached = dim["name"] in self.attached
                if not is_attached:
                    listed += 1
                    if listed > MAX_LISTED:
                        continue
                chip = Chip(self, dim, self.view_widget)
                chip.style_as(is_attached)
                chip.show()
                self.chips[dim["name"]] = chip
            hidden = listed - MAX_LISTED
            if hidden > 0:
                self.more = QtWidgets.QLabel(f"+ {hidden} more in the Parameters tab", self.view_widget)
                self.more.setObjectName("BWCodeDimMore")
                self.more.setStyleSheet("font-size: 11px;")
                self.more.adjustSize()
                self.more.show()
            self.camera = None
            self.place()
            if self.pending:
                QtCore.QTimer.singleShot(30, lambda: self.alive() and self.check_next())

        def check_next(self):
            """One check per tick, so the view stays live; then show what it found."""
            obj = self.part()
            if obj is None or not self.pending or self.editing():
                if self.pending and obj is not None:
                    QtCore.QTimer.singleShot(400, lambda: self.alive() and self.check_next())
                return
            name = self.pending.pop(0)
            check(getattr(obj, cc.SCRIPT, ""), cc.values_of(obj), name, getattr(obj, cc.OUTPUT, 0))
            if self.pending:
                QtCore.QTimer.singleShot(0, lambda: self.alive() and self.check_next())
            else:
                self.rebuild()

        def check_all(self):
            """Every pending check now (tests, and callers that cannot wait for the timer)."""
            obj = self.part()
            while obj is not None and self.pending:
                name = self.pending.pop(0)
                check(getattr(obj, cc.SCRIPT, ""), cc.values_of(obj), name, getattr(obj, cc.OUTPUT, 0))
            self.rebuild()

        def _to_view(self, point):
            viewport = oc._graphics_viewport(self.view_widget)
            if viewport is None:
                return None
            x, y = self.view.getPointOnViewport(point)
            qx, qy = oc.viewport_to_qt(x, y, viewport.height(), viewport.devicePixelRatioF())
            return viewport.mapTo(self.view_widget, QtCore.QPoint(int(qx), int(qy)))

        def camera_key(self):
            try:
                cam = self.view.getCameraNode()
                pos, rot = cam.position.getValue().getValue(), cam.orientation.getValue().getValue()
                size = cam.height.getValue() if hasattr(cam, "height") else cam.heightAngle.getValue()
                return (tuple(round(v, 5) for v in pos), tuple(round(v, 6) for v in rot), round(size, 5),
                        self.view_widget.width(), self.view_widget.height())
            except Exception:
                return None

        def place(self):
            """Put each chip where it belongs for the camera as it is."""
            key = self.camera_key()
            if key is not None and key == self.camera:
                return
            self.camera = key
            width, height = self.view_widget.width(), self.view_widget.height()
            placed, listed = [], []
            for name, chip in self.chips.items():
                if not self._ok(chip):
                    continue
                anchor = self.anchors.get(name)
                spot = self._to_view(anchor) if anchor is not None else None
                if spot is None:
                    listed.append(chip)
                    continue
                x = min(max(spot.x() - chip.width() // 2, 2), max(width - chip.width() - 2, 2))
                y = min(max(spot.y() - chip.height() // 2, 2), max(height - chip.height() - 2, 2))
                rect = QtCore.QRect(x, y, chip.width(), chip.height())
                while any(rect.intersects(other) for other in placed):   # two dimensions at one spot: stack them
                    rect.translate(0, chip.height() + 3)
                placed.append(rect)
                chip.move(rect.topLeft())
                chip.raise_()
            # The rest are a row along the top, in the middle: the tree is at the left and the panel at the right.
            if self._ok(self.more):
                listed.append(self.more)
            room = max(int(width * 0.5), 240)
            rows, row, used = [], [], 0
            for chip in listed:
                if row and used + chip.width() > room:
                    rows.append((row, used))
                    row, used = [], 0
                row.append(chip)
                used += chip.width() + 6
            if row:
                rows.append((row, used))
            y = LIST_TOP
            for row, used in rows:
                x = max((width - used + 6) // 2, 2)
                for chip in row:
                    chip.move(x, y)
                    chip.raise_()
                    x += chip.width() + 6
                y += max(c.height() for c in row) + 4

        def commit(self, name, text):
            obj = self.part()
            if obj is None:
                return False
            try:
                set_dimension(obj, name, text)
            except cc.CodeCadError as exc:
                self.message = str(exc)
                chip = self.chips.get(name)
                if self._ok(chip):
                    chip.setToolTip(str(exc))
                return False
            self.message = ""
            QtCore.QTimer.singleShot(0, lambda: self.alive() and self.rebuild())
            return True

        def close(self):
            self._drop_chips()
            try:
                self.view.getSceneGraph().removeChild(self.root)
            except Exception:
                pass

    _state["classes"] = {"Overlay": Overlay, "Chip": Chip}
    return _state["classes"]


def overlay():
    return _state["overlay"]


def _close():
    current = _state["overlay"]
    _state["overlay"] = None
    if _state["timer"] is not None:
        _state["timer"].stop()
    if current is not None:
        current.close()


def update():
    """Show the dimensions of the selected code part in the active 3D view; none when nothing fits."""
    import FreeCADGui

    import bruceworx_codecad_gui
    import bruceworx_oncanvas as oc

    obj = bruceworx_codecad_gui.selected_part() if enabled() else None
    current = _state["overlay"]
    if obj is None:
        _close()
        return None
    gdoc = FreeCADGui.getDocument(obj.Document.Name)
    view = gdoc.activeView() if gdoc else None
    if view is None or not hasattr(view, "getSceneGraph"):
        _close()
        return None
    if current is not None and current.part() is obj and current.view is view and current.alive():
        if not current.editing():
            current.rebuild()
        return current
    _close()
    view_widget = oc._view_widget(view)
    if view_widget is None:
        return None
    created = _classes()["Overlay"].__new__(_classes()["Overlay"])
    _state["overlay"] = created
    created.__init__(view, view_widget, obj)
    if _state["timer"] is not None:
        _state["timer"].start()
    return created


def _later(work):
    from PySide import QtCore

    def run():
        try:
            work()
        except Exception as exc:  # an observer must never take the app down
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"BruceWorx code part dimensions: {exc}\n")

    QtCore.QTimer.singleShot(0, run)


class _SelectionObserver:
    def _changed(self, *_args):
        _later(update)

    addSelection = removeSelection = setSelection = clearSelection = _changed


class _DocObserver:
    def slotRecomputedDocument(self, doc):
        current = _state["overlay"]
        if current is not None and current.doc_name == doc.Name:
            _later(update)

    def slotDeletedDocument(self, doc):
        current = _state["overlay"]
        if current is not None and current.doc_name == doc.Name:
            _later(_close)

    def slotUndoDocument(self, doc):
        self.slotRecomputedDocument(doc)

    slotRedoDocument = slotUndoDocument


def _tick():
    current = _state["overlay"]
    if current is None:
        return
    if not current.alive() or current.part() is None:
        _close()
        return
    if not current.editing():
        current.place()


def _shutdown():
    for remove, observer in _state["observers"]:
        try:
            remove(observer)
        except Exception:
            pass
    _state["observers"] = []
    if _state["timer"] is not None:
        _state["timer"].stop()


def install():
    import FreeCAD
    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets

    if _state["installed"]:
        return
    _state["installed"] = True
    if not enabled():
        return
    selection = _SelectionObserver()
    Gui.Selection.addObserver(selection)
    _state["observers"].append((Gui.Selection.removeObserver, selection))
    observer = _DocObserver()
    FreeCAD.addDocumentObserver(observer)
    _state["observers"].append((FreeCAD.removeDocumentObserver, observer))
    timer = QtCore.QTimer(QtWidgets.QApplication.instance())
    timer.setInterval(60)   # chips follow the camera
    timer.timeout.connect(_tick)   # runs only while dimensions are on screen
    _state["timer"] = timer
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
