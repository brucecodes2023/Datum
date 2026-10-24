# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pick to feature, error chips and dependency lines in the Browser (roadmap UIX-11, Nxt review).

* Pick to feature (Preferences/BruceWorx/PickTrace): pick a face, edge or
  vertex in the 3D view and the feature that made it gets a soft accent
  highlight in the Browser (scrolled into view; the selection is left alone)
  and a ring on its chip in the history timeline. FreeCAD's element map knows:
  ``getElementHistory(element, recursive=True, sameType=True)`` lists the
  features an element passed through, newest first, and stops where it changed
  type (a fillet face made from an edge stops at the fillet). The oldest solid
  feature in that list made it. A pattern copies its originals' faces with
  the original's history, so when a pattern lists the feature as an original
  and the picked element is not on that feature's own shape, the pattern made it.
* Error chips (BrowserErrorChips): a feature whose recompute failed gets a red
  ERROR chip after its name (bruceworx_browser.ROW_EXTRAS; drawn in C++,
  kind "error"). Clicking the chip opens the feature, as double-clicking the
  row does; its tooltip gives the error.
* Dependency lines (BrowserDependencyLines): hovering a Browser row draws thin
  lines to the rows it uses (Sketch → Pad) and the rows that use it, drawn on
  a transparent layer over the tree, no C++. The previous-feature link of a
  PartDesign body (BaseFeature) is left out: the order already shows it.
  Containers (bodies, parts, groups, origins) get no lines.

All three default on.
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
TRACE_PREF = "PickTrace"
CHIP_PREF = "BrowserErrorChips"
LINES_PREF = "BrowserDependencyLines"
SWITCHES = (TRACE_PREF, CHIP_PREF, LINES_PREF)
OVERLAY_NAME = "BruceWorxTreeOverlay"

ERROR_CHIP = "ERROR"
ERROR_KIND = "error"
CHIP_TIP = "{label} failed: {why}\nClick the chip (or double-click the row) to open it."

# Not "made" anything: the element history passes through these.
NOT_MAKERS = (
    "PartDesign::Body",
    "Sketcher::SketchObject",
    "App::Part",
    "App::Link",
    "App::LinkGroup",
    "App::DocumentObjectGroup",
)
PATTERN_TYPES = (
    "PartDesign::LinearPattern",
    "PartDesign::PolarPattern",
    "PartDesign::Mirrored",
    "PartDesign::MultiTransform",
    "PartDesign::Scaled",
)
CONTAINER_TYPES = ("App::Origin", "PartDesign::Body", "App::Part", "App::DocumentObjectGroup", "Assembly::AssemblyObject")
ELEMENT_KINDS = ("Face", "Edge", "Vertex")


# ----- pure helpers (unit tested) ---------------------------------------------------------


def maker_index(types):
    """Index of the feature that made an element, from its history's type ids (newest first).

    The oldest entry that is a solid feature; None when there is none.
    """
    found = None
    for i, type_id in enumerate(types):
        if type_id not in NOT_MAKERS:
            found = i
    return found


def pattern_candidates(chain, maker):
    """Names of patterns newer than ``maker`` in ``chain`` that list it as an original.

    ``chain``: [(name, type id, [original names])] newest first.
    """
    out = []
    for name, type_id, originals in chain:
        if name == maker:
            break
        if type_id in PATTERN_TYPES and maker in (originals or ()):
            out.append(name)
    return out


def element_kind(subname):
    """"Face3" for "Body.Pad.Face3" or "Body.;#5:1;:H1,F.Face3"; "" when not a face, edge or vertex."""
    last = (subname or "").rsplit(".", 1)[-1]
    return last if last.rstrip("0123456789") in ELEMENT_KINDS and last[-1:].isdigit() else ""


def is_error(state, valid=True):
    """A failed recompute: State lists Invalid or Error, or isValid() said no."""
    state = list(state or ())
    return "Invalid" in state or "Error" in state or not valid


def error_text(status):
    """The error from getStatusString(), or a plain word when FreeCAD has none."""
    text = (status or "").strip()
    return text if text and text not in ("Valid", "Touched") else "recompute failed"


def elbow(src, dst, lane):
    """Points of a connector from row point ``src`` (x, y) to ``dst`` through the vertical ``lane``."""
    (x1, y1), (x2, y2) = src, dst
    return [(x1, y1), (lane, y1), (lane, y2), (x2, y2)]


def lanes(left, count, step=4, gap=6, floor=2):
    """x of ``count`` vertical lanes left of ``left``: the first nearest the rows."""
    return [max(floor, left - gap - i * step) for i in range(count)]


# ----- FreeCAD side ------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def _on(name):
    return bool(_prefs().GetBool(name, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def obj_in_error(obj):
    try:
        valid = obj.isValid()
    except Exception:
        valid = True
    return is_error(getattr(obj, "State", ()), valid)


def is_container(obj):
    if getattr(obj, "TypeId", "") in CONTAINER_TYPES:
        return True
    try:
        return obj.hasExtension("App::GroupExtension")
    except Exception:
        return False


def _point_on(shape):
    """A point on a face, edge or vertex."""
    if shape.ShapeType == "Vertex":
        return shape.Point
    if shape.ShapeType == "Edge":
        a, b = shape.ParameterRange
        return shape.valueAt((a + b) / 2)
    u0, u1, v0, v1 = shape.ParameterRange
    point = shape.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
    try:
        if shape.isInside(point, 1e-6, True):
            return point
    except Exception:
        pass
    return shape.Vertexes[0].Point if shape.Vertexes else point


def _on_shape(element, shape, tol=1e-4):
    """True when ``element`` lies on the boundary of ``shape`` (both in the same frame)."""
    import Part

    kind = element.ShapeType
    parts = shape.Faces if kind == "Face" else shape.Edges if kind == "Edge" else shape.Vertexes
    if not parts:
        return False
    try:
        return Part.Compound(parts).distToShape(Part.Vertex(_point_on(element)))[0] < tol
    except Exception:
        return True


def _history(owner, element):
    """owner.getElementHistory, without the element map's warnings about names it cannot trace."""
    import FreeCAD

    try:
        level = FreeCAD.getLogLevel("ElementMap")
        FreeCAD.setLogLevel("ElementMap", "Error")
    except Exception:
        level = None
    try:
        return owner.getElementHistory(element, recursive=True, sameType=True)
    except Exception:
        return []
    finally:
        if level is not None:
            FreeCAD.setLogLevel("ElementMap", level)


def trace(owner, element):
    """The feature that made ``element`` (e.g. "Face3") of ``owner``'s shape, or None."""
    if owner is None or not element:
        return None
    if getattr(owner, "TypeId", "") == "PartDesign::Body" and getattr(owner, "Tip", None) is not None:
        owner = owner.Tip  # same element names, body-local frame like its features
    history = _history(owner, element)
    if not history:
        return None
    objs = [h[0] for h in history if hasattr(h[0], "TypeId")]
    index = maker_index([o.TypeId for o in objs])
    if index is None:
        return None
    maker = objs[index]
    chain = [(o.Name, o.TypeId, [x.Name for x in (getattr(o, "Originals", None) or [])]) for o in objs]
    patterns = pattern_candidates(chain, maker.Name)
    if patterns:
        try:
            picked = owner.Shape.getElement(element)
            if not _on_shape(picked, maker.Shape):
                return owner.Document.getObject(patterns[-1])  # the oldest pattern that copied it
        except Exception:
            pass
    return maker


def trace_selection():
    """The maker of the one face/edge/vertex picked in the 3D view, else None."""
    import FreeCADGui

    sel = FreeCADGui.Selection.getSelectionEx("", 0)
    if len(sel) != 1 or len(sel[0].SubElementNames) != 1:
        return None
    top, sub = sel[0].Object, sel[0].SubElementNames[0]
    element = element_kind(sub)
    if not element:
        return None
    try:
        owner = top.getSubObject(sub, retType=1)
    except Exception:
        owner = None
    return trace(owner or top, element)


# ----- Browser: items, overlay, chips -----------------------------------------------------


def _trees():
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    return [t for t in mw.findChildren(QtWidgets.QTreeWidget) if t.metaObject().className() == "Gui::TreeWidget"]


# Rows are handled as model indexes: QTreeWidgetItem wrappers of the tree's own items go
# stale when FreeCAD rebuilds rows, and a stale one crashes.


def _doc_of_index(index):
    import FreeCAD

    top = index
    while top.parent().isValid():
        top = top.parent()
    label = top.data()
    for doc in FreeCAD.listDocuments().values():
        if doc.Label == label:
            return doc
    return None


def index_object(index):
    """The document object a Browser row shows (by label within its document)."""
    if index is None or not index.isValid() or not index.parent().isValid():
        return None
    index = index.siblingAtColumn(0)
    doc = _doc_of_index(index)
    if doc is None:
        return None
    found = doc.getObjectsByLabel(str(index.data()))
    return found[0] if found else None


def visible_rows(tree):
    """[(model index, document label)] for every object row the user can see."""
    model = tree.model()
    out = []

    def walk(parent, doc_label):
        for row in range(model.rowCount(parent)):
            if tree.isRowHidden(row, parent):
                continue
            index = model.index(row, 0, parent)
            if doc_label is None:
                if tree.isExpanded(index):
                    walk(index, index.data())
                continue
            out.append((index, doc_label))
            if model.rowCount(index) and tree.isExpanded(index):
                walk(index, doc_label)

    walk(tree.rootIndex(), None)
    return out


def object_indexes(tree, objs):
    """{object name: [visible row indexes]} for ``objs``."""
    wanted = {(o.Document.Label, o.Label): o.Name for o in objs if o is not None}
    out = {}
    for index, doc_label in visible_rows(tree):
        name = wanted.get((doc_label, index.data()))
        if name is not None:
            out.setdefault(name, []).append(index)
    return out


def dependencies(obj):
    """(uses, used_by): the objects ``obj`` refers to and those that refer to it, for lines."""
    if obj is None or is_container(obj):
        return [], []
    base = getattr(obj, "BaseFeature", None)
    def unique(objs):
        seen, out = set(), []
        for o in objs:
            if o.Name not in seen:
                seen.add(o.Name)
                out.append(o)
        return out

    uses = unique(o for o in obj.OutList if o is not base and not is_container(o))
    used_by = unique(o for o in obj.InList if getattr(o, "BaseFeature", None) is not obj and not is_container(o))
    return uses, used_by


def _name_right(tree, index):
    """x in the viewport where the row's name ends (the chips follow)."""
    from PySide import QtCore, QtGui, QtWidgets

    rect = tree.visualRect(index)
    height = tree.iconSize().height()
    if height <= 0:
        height = tree.style().pixelMetric(QtWidgets.QStyle.PM_SmallIconSize, None, tree)
    icon = index.data(QtCore.Qt.DecorationRole)
    if isinstance(icon, QtGui.QIcon) and not icon.isNull():
        # The tree draws the visibility eye and the object icon as one wide icon.
        width = icon.actualSize(QtCore.QSize(height * 8, height)).width()
    else:
        width = height
    return rect.left() + width + 8 + tree.fontMetrics().horizontalAdvance(str(index.data()))


def _object(key):
    """The object for a (document name, object name) key, or None."""
    import FreeCAD

    if key is None:
        return None
    doc = FreeCAD.listDocuments().get(key[0])
    return doc.getObject(key[1]) if doc else None


def _key(obj):
    return (obj.Document.Name, obj.Name) if obj is not None else None


def _make_overlay_class():
    from PySide import QtCore, QtGui, QtWidgets

    class Overlay(QtWidgets.QWidget):
        """Transparent layer over one tree's viewport: trace highlight and dependency lines.

        It keeps object keys, not rows: rows are looked up when painting, so a rebuilt
        tree never leaves it holding a deleted row.
        """

        def __init__(self, tree):
            super().__init__(tree.viewport())
            self.tree = tree
            self.setObjectName(OVERLAY_NAME)
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
            self.traced = None  # (doc name, object name)
            self.hovered = None  # (doc name, object name)
            self.lines = []  # (key used, key that uses it)
            self.resize(tree.viewport().size())
            tree.viewport().installEventFilter(self)
            tree.verticalScrollBar().valueChanged.connect(self.update)
            tree.expanded.connect(self.update)
            tree.collapsed.connect(self.update)
            self.show()

        # --- colours from the tree's own QSS properties (BruceWorx.qss tokens)
        def _color(self, prop, fallback):
            value = self.tree.property(prop)
            return QtGui.QColor(value) if isinstance(value, QtGui.QColor) and value.isValid() else fallback

        def accent(self):
            return self._color("badgeAccentColor", self.tree.palette().color(QtGui.QPalette.Highlight))

        def quiet(self):
            text = self.tree.palette().color(QtGui.QPalette.Text)
            return self._color("rowMetaColor", QtGui.QColor(text.red(), text.green(), text.blue(), 130))

        # --- events from the viewport
        def eventFilter(self, obj, event):
            try:
                kind = event.type()
                if kind == QtCore.QEvent.Resize:
                    self.resize(obj.size())
                elif kind == QtCore.QEvent.MouseMove:
                    self.hover(index_object(self.tree.indexAt(event.position().toPoint())))
                elif kind == QtCore.QEvent.Leave:
                    self.hover(None)
                elif kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                    return self.chip_click(event.position().toPoint())
                elif kind == QtCore.QEvent.ToolTip:
                    return self.chip_tip(event)
            except Exception:
                pass
            return False

        def hover(self, obj):
            key = _key(obj)
            if key == self.hovered:
                return
            self.hovered = key
            self.lines = []
            if obj is not None and _on(LINES_PREF):
                uses, used_by = dependencies(obj)
                self.lines = [(_key(o), key) for o in uses] + [(key, _key(o)) for o in used_by]
            self.update()

        def _error_object(self, pos):
            index = self.tree.indexAt(pos)
            obj = index_object(index)
            if obj is None or not _on(CHIP_PREF) or is_container(obj) or not obj_in_error(obj):
                return None
            if pos.x() < _name_right(self.tree, index.siblingAtColumn(0)):
                return None
            return obj

        def chip_click(self, pos):
            obj = self._error_object(pos)
            if obj is None:
                return False
            QtCore.QTimer.singleShot(0, lambda o=obj: open_feature(o))
            return True

        def chip_tip(self, event):
            obj = self._error_object(event.pos())
            if obj is None:
                return False
            try:
                why = obj.getStatusString()
            except Exception:
                why = ""
            QtWidgets.QToolTip.showText(
                event.globalPos(), CHIP_TIP.format(label=obj.Label, why=error_text(why)), self.tree
            )
            return True

        # --- painting
        def _rects(self, keys):
            """{key: [(row rect, has children)]} for the visible rows of ``keys``."""
            objs = {k: _object(k) for k in keys if k is not None}
            rows = object_indexes(self.tree, [o for o in objs.values() if o is not None])
            model = self.tree.model()
            return {k: [(self.tree.visualRect(i), model.rowCount(i) > 0) for i in rows.get(o.Name, [])]
                    for k, o in objs.items() if o}

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            try:
                painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                self._paint(painter)
            except Exception:
                pass
            finally:
                painter.end()

        def _paint(self, painter):
            area = self.rect()
            if self.traced is not None:
                accent = self.accent()
                fill = QtGui.QColor(accent)
                fill.setAlpha(46)
                edge = QtGui.QColor(accent)
                edge.setAlpha(170)
                for rect, _kids in self._rects([self.traced]).get(self.traced, []):
                    if not rect.isValid() or not rect.intersects(area):
                        continue
                    box = QtCore.QRectF(rect.left() - 2, rect.top() + 1, area.right() - rect.left() - 2, rect.height() - 2)
                    painter.setPen(QtGui.QPen(edge, 1))
                    painter.setBrush(fill)
                    painter.drawRoundedRect(box, 4, 4)
            if not self.lines:
                return
            rects = self._rects({k for pair in self.lines for k in pair})
            pairs = [(ra, rb) for a, b in self.lines for ra in rects.get(a, []) for rb in rects.get(b, [])
                     if ra[0] != rb[0]]
            if not pairs:
                return
            color = self.quiet()
            # Lanes run in the parent's indentation column, clear of the expand arrows;
            # a stub ends just before a row's own arrow.
            indent = self.tree.indentation()
            left = min(min(ra[0].left(), rb[0].left()) for ra, rb in pairs)

            def end(rect, kids):
                return (rect.left() - (indent - 1 if kids else 2), rect.center().y())

            for ((ra, ka), (rb, kb)), lane in zip(pairs, lanes(left - indent + 6, len(pairs), step=3)):
                src, dst = end(ra, ka), end(rb, kb)
                path = QtGui.QPainterPath()
                pts = elbow(src, dst, lane)
                path.moveTo(*pts[0])
                for p in pts[1:]:
                    path.lineTo(*p)
                painter.setPen(QtGui.QPen(color, 1))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawPath(path)
                # Arrowhead into the row that uses the other.
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(color)
                x, y = dst
                painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y), QtCore.QPointF(x - 4, y - 3),
                                                     QtCore.QPointF(x - 4, y + 3)]))

        def show_trace(self, obj):
            self.traced = _key(obj)
            if obj is not None:
                rows = object_indexes(self.tree, [obj]).get(obj.Name, [])
                if rows:
                    self.tree.scrollTo(rows[0])
            self.update()

    return Overlay


_overlay_class = None
_overlays = {}  # tree pointer -> Overlay


def _cpp(obj):
    try:
        import shiboken6

        return shiboken6.getCppPointer(obj)[0]
    except Exception:
        return id(obj)


def overlays():
    """Live overlays, one per Browser tree (for tests and scripts)."""
    global _overlay_class
    if _overlay_class is None:
        _overlay_class = _make_overlay_class()
    out = []
    for tree in _trees():
        key = _cpp(tree)
        overlay = _overlays.get(key)
        if not _alive(overlay):
            overlay = _overlay_class(tree)
            _overlays[key] = overlay
        out.append(overlay)
    return out


def open_feature(obj):
    """Open a feature as double-clicking its row does (its task dialog)."""
    import FreeCADGui

    try:
        vp = obj.ViewObject
        if vp is not None and vp.doubleClicked():
            return True
    except Exception:
        pass
    try:
        return bool(FreeCADGui.getDocument(obj.Document.Name).setEdit(obj.Name))
    except Exception:
        return False


def _timeline_bar():
    import FreeCADGui
    from PySide import QtWidgets

    dock = FreeCADGui.getMainWindow().findChild(QtWidgets.QDockWidget, "HistoryTimelineDock")
    bar = dock.widget() if dock is not None else None
    return bar if hasattr(bar, "set_traced") else None


_traced = None  # (doc, name) shown now


def show_trace(obj):
    """Highlight ``obj`` (None clears) in the Browser and on the timeline."""
    global _traced
    key = (obj.Document.Name, obj.Name) if obj is not None else None
    if key == _traced:
        return
    _traced = key
    for overlay in overlays():
        overlay.show_trace(obj)
    bar = _timeline_bar()
    if bar is not None:
        try:
            bar.set_traced(obj.Name if obj is not None else None)
        except Exception:
            pass


def _update_trace():
    try:
        obj = trace_selection() if _on(TRACE_PREF) else None
    except Exception:
        obj = None
    show_trace(obj)


def error_chip(obj, meta, marks):
    """bruceworx_browser.ROW_EXTRAS filter: the red ERROR chip on a failed feature."""
    if _on(CHIP_PREF) and not is_container(obj) and obj_in_error(obj):
        marks = list(marks) + [(ERROR_CHIP, ERROR_KIND)]
    return meta, marks


# ----- install -----------------------------------------------------------------------------

_installed = False
_sel_observer = None
_prefs_observer = None
_group = None
_timer = None


class _SelectionObserver:
    def _later(self, *_args):
        if _timer is not None:
            _timer.start()

    addSelection = removeSelection = setSelection = clearSelection = _later


class _PrefObserver:
    def onChange(self, _group, name):
        if name in SWITCHES:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, _apply_prefs)


def _apply_prefs():
    import FreeCADGui

    _update_trace()
    for overlay in overlays():
        overlay.hover(None)
    # A new hook object makes the tree ask every row again (chips on or off).
    try:
        import bruceworx_browser

        bruceworx_browser.apply_rows()
    except Exception:
        pass
    FreeCADGui.updateGui()


def _shutdown():
    global _timer
    import FreeCADGui

    if _timer is not None:
        _timer.stop()
        _timer = None
    if _sel_observer is not None:
        try:
            FreeCADGui.Selection.removeObserver(_sel_observer)
        except Exception:
            pass
    for overlay in list(_overlays.values()):
        if _alive(overlay) and _alive(overlay.tree):
            try:
                overlay.tree.viewport().removeEventFilter(overlay)
            except RuntimeError:
                pass
    _overlays.clear()


def install():
    """Error chips now; the selection trace and the tree layers once the Browser exists."""
    global _installed, _sel_observer, _prefs_observer, _group, _timer
    if _installed:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    import bruceworx_browser

    if error_chip not in bruceworx_browser.ROW_EXTRAS:
        bruceworx_browser.ROW_EXTRAS.append(error_chip)
    _timer = QtCore.QTimer()
    _timer.setSingleShot(True)
    _timer.setInterval(0)
    _timer.timeout.connect(_update_trace)
    _sel_observer = _SelectionObserver()
    FreeCADGui.Selection.addObserver(_sel_observer)
    _prefs_observer = _PrefObserver()
    _group = FreeCAD.ParamGet(PREFS_PATH)  # keep the handle: a collected wrapper detaches
    _group.Attach(_prefs_observer)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)

    def _late():
        if _timer is None:
            return  # quitting
        if not _trees():
            QtCore.QTimer.singleShot(500, _late)
            return
        overlays()

    QtCore.QTimer.singleShot(0, _late)
    _installed = True
