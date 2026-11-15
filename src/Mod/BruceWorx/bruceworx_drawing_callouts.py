# SPDX-License-Identifier: LGPL-2.1-or-later
"""Hole and thread callouts that follow the model (roadmap DRW-04).

Hole Callouts (bruceworx_hardware, MOD-05) writes a leader and a text for every Hole and cosmetic
thread a drawing view shows; until now the text stayed as written until the command was run again.
Here the callouts are kept up to date:

* when a Hole or a cosmetic thread is recomputed, every callout that describes it (in this or
  another open document) gets the text hardware.hole_callout makes from the hole now: quantity,
  diameter, THRU or depth, counterbore, countersink, thread size, pitch, class and depth, in the
  order plan chapter 27 (section 6, "Callouts on the drawing") fixed;
* the leader's arrow follows the hole when the hole moves, unless the arrow was dragged off it
  (the place written last is remembered on the leader);
* where the text and the shoulder were dragged stays;
* a callout made on a view of a projection group (every New Drawing view) is put on the page: Hole
  Callouts left those on no page, so they were not drawn, and put the arrow at the hole's place
  from the model origin instead of from the view's centre. Both are corrected here, without
  changing bruceworx_hardware.

A callout whose hole was deleted is left as it is (TechDraw shows it; the user removes it).
Hole tables with ordinate positions (also named in the DRW-04 text) are not built here.

Off switch: Preferences/BruceWorx/DrawingCalloutsFollow (default on).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "DrawingCalloutsFollow"
AT_PROP = "BW_CalloutAt"  # on a leader: where its arrow was put for the hole, page mm from the view's centre
GROUP = "BruceWorx"
_state = {"observer": None, "scheduled": False, "busy": False}


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


def _bh():
    import bruceworx_hardware

    return bruceworx_hardware


def callouts(doc):
    """[(leader, text object)] of the document's hole callouts."""
    bh = _bh()
    out = []
    for obj in doc.Objects:
        if bh.CALLOUT_PROP in obj.PropertiesList and obj.isDerivedFrom("TechDraw::DrawRichAnno"):
            leader = getattr(obj, "AnnoParent", None)
            if leader is not None and bh.CALLOUT_PROP in leader.PropertiesList:
                out.append((leader, obj))
    return out


def page_of(view):
    """The page a view is on, also through its projection group."""
    seen, todo = set(), [view]
    while todo:
        obj = todo.pop()
        if obj.Name in seen:
            continue
        seen.add(obj.Name)
        for parent in obj.InList:
            if parent.isDerivedFrom("TechDraw::DrawPage"):
                return parent
            if parent.isDerivedFrom("TechDraw::DrawProjGroup"):
                todo.append(parent)
    return None


def half_extent(view):
    """(half width, half height) of what the view draws, in page mm (from its shape's corners)."""
    import FreeCAD

    shape = source_shape(view)
    if shape is None:
        return 0.0, 0.0
    b = shape.BoundBox
    points = [view_point(view, FreeCAD.Vector(x, y, z))
              for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
    return max(abs(q[0]) for q in points) * _scale(view), max(abs(q[1]) for q in points) * _scale(view)


def source_shape(view):
    """What the view projects, as one shape in the document's frame (None when it shows nothing)."""
    import Part

    shapes = []
    for obj in list(getattr(view, "Source", []) or []) + list(getattr(view, "XSource", []) or []):
        try:
            shape = Part.getShape(obj)
        except Exception:  # noqa: BLE001
            continue
        if not shape.isNull():
            shapes.append(shape)
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def view_point(view, point):
    """Where a point of the model is drawn, in unscaled mm from the view's centre (x right, y up).

    view.projectPoint measures from the projected model origin, while a view is drawn centred on
    its shape (TechDraw.findCentroid), so the centre's own projection is taken off."""
    import TechDraw

    drawn = view.projectPoint(point)
    shape = source_shape(view)
    if shape is None:
        return drawn.x, drawn.y
    centre = view.projectPoint(TechDraw.findCentroid(shape, view.Direction))
    return drawn.x - centre.x, drawn.y - centre.y


def _scale(view):
    return view.getScale() if hasattr(view, "getScale") else view.Scale


def _arrow(view, anchor):
    """Where a point of the model lands on the page, in mm from the view's centre."""
    x, y = view_point(view, anchor)
    return x * _scale(view), y * _scale(view)


def _raw_arrow(view, anchor):
    """Where Hole Callouts (bruceworx_hardware.add_callouts) puts a new arrow: from the projected model
    origin, which is the hole only for a part centred on the origin."""
    point = view.projectPoint(anchor)
    return point.x * _scale(view), point.y * _scale(view)


def refresh(doc, recompute=True):
    """Bring the document's callouts up to date with their holes. Returns the text objects changed.
    ``recompute`` False leaves them touched (for a caller that is inside a recompute)."""
    import FreeCAD

    bh = _bh()
    changed, targets = [], {}
    for leader, anno in callouts(doc):
        view = getattr(leader, "LeaderParent", None)
        if view is None:
            continue
        if view.Name not in targets:
            try:
                targets[view.Name] = {t.Name: (text, anchor) for t, text, anchor in bh.callout_targets(view)}
            except Exception as exc:  # noqa: BLE001 -- a view whose source is gone
                FreeCAD.Console.PrintLog(f"BruceWorx callouts: {view.Label}: {exc}\n")
                targets[view.Name] = {}
        found = targets[view.Name].get(getattr(anno, bh.CALLOUT_PROP))
        if found is None:
            continue  # the hole is gone: the callout stays as it was
        text, anchor = found
        touched = False
        # Hole Callouts looks for the page among the view's own parents; a view of a projection group
        # (every New Drawing view) has the group there, so its callouts were on no page and not drawn.
        page = page_of(view)
        if page is not None:
            for item in (leader, anno):
                if item not in page.Views:
                    page.addView(item)
                    touched = True
        if text:
            rich = bh._rich(text)
            if anno.AnnoText != rich:
                anno.AnnoText = rich
                touched = True
        try:
            x, y = _arrow(view, anchor)
        except Exception:  # noqa: BLE001 -- no geometry yet
            x = y = None
        if x is not None:
            if AT_PROP not in leader.PropertiesList:
                # a new callout: adopt the arrow where it is, so later moves are followed. One still where
                # Hole Callouts put it counts as on the hole, and so moves onto it just below.
                leader.addProperty("App::PropertyVector", AT_PROP, GROUP, "Where the arrow was put for the hole")
                leader.setEditorMode(AT_PROP, 2)
                setattr(leader, AT_PROP, FreeCAD.Vector(leader.X.Value, leader.Y.Value, 0))
                touched = True
            at = getattr(leader, AT_PROP)
            on_hole = abs(leader.X.Value - at.x) < 1e-6 and abs(leader.Y.Value - at.y) < 1e-6
            if on_hole and (abs(at.x - x) > 1e-6 or abs(at.y - y) > 1e-6):
                leader.X, leader.Y = x, y
                setattr(leader, AT_PROP, FreeCAD.Vector(x, y, 0))
                touched = True
        if touched:
            changed.append(anno)
    if changed and recompute:
        doc.recompute()
    return changed


def refresh_all(recompute=True):
    import FreeCAD

    if _state["busy"]:
        return []
    _state["busy"] = True
    out = []
    try:
        for doc in list(FreeCAD.listDocuments().values()):
            if doc.Restoring:
                continue
            try:
                out += refresh(doc, recompute)
            except Exception as exc:  # noqa: BLE001 -- never break the edit that triggered this
                FreeCAD.Console.PrintLog(f"BruceWorx callouts: {doc.Label}: {exc}\n")
    finally:
        _state["busy"] = False
    return out


def _schedule():
    import FreeCAD

    if _state["scheduled"]:
        return
    _state["scheduled"] = True
    if not FreeCAD.GuiUp:
        return  # headless: slotRecomputedDocument does it, when the recompute that told us is over
    from PySide import QtCore

    def run():
        _state["scheduled"] = False
        refresh_all()

    QtCore.QTimer.singleShot(0, run)


class _Observer:
    """A recomputed Hole or cosmetic thread: its callouts follow."""

    def slotRecomputedObject(self, obj):
        if _state["busy"] or not enabled():
            return
        try:
            bh = _bh()
            if bh.is_hole(obj) or bh.is_cosmetic_thread(obj):
                _schedule()
        except Exception:  # noqa: BLE001
            pass

    def slotChangedObject(self, obj, prop):
        """A callout was just made (its mark is set last): put it on its page and its arrow on the hole."""
        if _state["busy"] or not enabled():
            return
        try:
            if prop == _bh().CALLOUT_PROP and obj.isDerivedFrom("TechDraw::DrawRichAnno") \
                    and not obj.Document.Restoring:
                _schedule()
        except Exception:  # noqa: BLE001
            pass

    def slotRecomputedDocument(self, _doc):
        import FreeCAD

        if FreeCAD.GuiUp or not _state["scheduled"] or _state["busy"]:
            return
        _state["scheduled"] = False
        refresh_all(recompute=False)  # still inside Document::recompute: the texts are set, not redrawn


def install():
    import FreeCAD

    if _state["observer"] is None and enabled():
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])
    return _state["observer"]


def uninstall():
    import FreeCAD

    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        FreeCAD.removeDocumentObserver(observer)
