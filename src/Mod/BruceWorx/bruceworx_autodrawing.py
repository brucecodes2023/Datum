# SPDX-License-Identifier: LGPL-2.1-or-later
"""A first-pass drawing in one step (roadmap DRW-06): the user edits it from there.

auto_drawing(part) makes, on the vault's sheet for the part's units:

* the front, top and right views at an automatic scale (bruceworx_drawing.new_drawing, DRW-01),
* an isometric view in the free corner, at the same scale,
* the overall dimensions: width and height on the front view, depth on the top view (TechDraw's
  extent dimensions, which follow the view's outline),
* hole and thread callouts (bruceworx_hardware.add_callouts) on the view that looks down the most
  holes, each hole once; they then follow the holes (bruceworx_drawing_callouts),
* for an assembly: a parts list and balloons on the isometric view (bruceworx_partslist).

"Key dimensions" here means the overall sizes only. Choosing which feature dimensions a reader
needs is not attempted, and the DRW-06 acceptance (a reviewer keeps 70 % of the generated
dimensions on the corpus parts) has not been run.

Off switch: Preferences/BruceWorx/AutoDrawing (default on); off, the command is not registered.
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "AutoDrawing"
COMMAND = "BruceWorx_AutoDrawing"
ICON = "Heuretes_AutoDrawing.svg"
ISO_DIRECTION = (1.0, -1.0, 1.0)  # from the front-right-top, as the 3D view's isometric
HORIZONTAL, VERTICAL = 0, 1  # TechDraw.makeExtentDim directions
DIM_GAP = 9.0  # mm from the outline to a dimension's text


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


def _views(group):
    return {v.Type: v for v in group.Views}


def _is_assembly(part):
    target = part.getLinkedObject(True) if hasattr(part, "getLinkedObject") else part
    return target is not None and target.isDerivedFrom("Assembly::AssemblyObject")


def add_isometric(page, group):
    """An isometric view of what the group shows, in the top right corner of the sheet."""
    import FreeCAD

    doc = page.Document
    view = doc.addObject("TechDraw::DrawViewPart", "Isometric")
    page.addView(view)
    view.Source = list(group.Source)
    view.XSource = list(group.XSource)
    view.Direction = FreeCAD.Vector(*ISO_DIRECTION)
    if "XDirection" in view.PropertiesList:
        view.XDirection = FreeCAD.Vector(1, 1, 0)
    view.ScaleType = "Custom"
    view.Scale = group.getScale() if hasattr(group, "getScale") else group.Scale
    width, height = float(page.Template.Width), float(page.Template.Height)
    view.X = width * 0.80
    view.Y = height * 0.74
    view.Label = "Isometric"
    return view


def wait_for_geometry(views, seconds=8.0):
    """In the window TechDraw projects a view in the background; wait until each has its edges
    (a dimension made before that measures nothing). Headless they are there at once."""
    import time

    import FreeCAD

    if not FreeCAD.GuiUp:
        return True
    import FreeCADGui

    end = time.time() + seconds
    while time.time() < end:
        try:
            if all(v.getVisibleEdges() for v in views):
                return True
        except Exception:  # noqa: BLE001
            pass
        FreeCADGui.updateGui()
        time.sleep(0.02)
    return False


def half_extent(view):
    """(half width, half height) of what the view draws, in page mm."""
    import bruceworx_drawing_callouts as follow

    return follow.half_extent(view)


def add_overall_dimensions(group, gap=DIM_GAP):
    """Width and height on the front view, depth on the top view, each outside the outline
    (below, or to the left: the right view is on the other side). Returns the dimensions made."""
    import TechDraw

    views = _views(group)
    made = []
    front, top = views.get("Front"), views.get("Top")
    wait_for_geometry([v for v in (front, top) if v is not None])
    for view, direction in ((front, HORIZONTAL), (front, VERTICAL), (top, VERTICAL)):
        if view is None:
            continue
        try:
            dim = TechDraw.makeExtentDim(view, [], direction)
        except Exception:  # noqa: BLE001 -- a view with no geometry yet
            dim = None
        if dim is None:
            continue
        half_w, half_h = half_extent(view)
        if direction == HORIZONTAL:  # the text's place, page mm from the view's centre, Y up
            dim.X, dim.Y = 0.0, -(half_h + gap)
        else:
            dim.X, dim.Y = -(half_w + gap), 0.0
        made.append(dim)
    return made


def hole_axis_count(view):
    """How many of the view's holes it looks straight down."""
    import bruceworx_hardware as bh

    count = 0
    direction = view.Direction
    for obj, _text, _anchor in bh.callout_targets(view):
        axis = None
        try:
            if bh.is_hole(obj):
                circles = [e for e in obj.Profile[0].Shape.Edges if type(e.Curve).__name__ == "Circle"]
                axis = circles[0].Curve.Axis if circles else None
            else:
                axis = bh.thread_face(obj).Surface.Axis
        except Exception:  # noqa: BLE001
            axis = None
        if axis is not None and abs(abs(axis.normalize().dot(direction.normalize())) - 1.0) < 1e-6:
            count += 1
    return count


def add_hole_callouts(group):
    """Callouts on the view that looks down the most holes (the front view when none does)."""
    import bruceworx_hardware as bh

    views = _views(group)
    ordered = [views[t] for t in ("Front", "Top", "Right") if t in views]
    if not ordered:
        return []
    best = max(ordered, key=hole_axis_count)
    if not bh.callout_targets(best):
        return []
    made = bh.add_callouts(best)
    import bruceworx_drawing_callouts as follow

    follow.refresh(best.Document, recompute=False)  # the arrows onto the holes, and followed from now on
    return made


def auto_drawing(part, sheet=None, root=None, doc=None):
    """The first-pass drawing of ``part``. Returns {"page", "isometric", "dimensions", "callouts",
    "parts_list", "balloons"}."""
    import bruceworx_drawing as drawing

    page = drawing.new_drawing(part, sheet, root, doc)
    target_doc = page.Document
    group = next(v for v in page.Views if v.isDerivedFrom("TechDraw::DrawProjGroup"))
    out = {"page": page, "parts_list": None, "balloons": []}
    out["isometric"] = add_isometric(page, group)
    target_doc.recompute()
    out["dimensions"] = add_overall_dimensions(group)
    out["callouts"] = add_hole_callouts(group)
    if _is_assembly(part):
        import bruceworx_partslist as pl

        assembly = part.getLinkedObject(True) if hasattr(part, "getLinkedObject") else part
        out["parts_list"] = pl.make(page, assembly)
        out["balloons"] = pl.add_balloons(out["isometric"], out["parts_list"])
    target_doc.recompute()
    drawing.fill_page(page, root)  # the dimensions get the sheet's format
    target_doc.recompute()
    return out


class _AutoDrawingCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Auto Drawing",
                "ToolTip": "A first-pass drawing of the selected part or assembly: three views and an isometric, "
                           "overall dimensions, hole callouts and, for an assembly, a parts list with balloons. "
                           "Edit it from there."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        import FreeCADGui as Gui
        from PySide import QtWidgets

        import bruceworx_drawing as drawing

        part = drawing._selected_part()
        if part is None:
            QtWidgets.QMessageBox.information(Gui.getMainWindow(), "Auto Drawing",
                                              "Select the part or assembly to draw first.")
            return
        doc = part.Document
        doc.openTransaction("Auto drawing")
        try:
            made = auto_drawing(part)
            doc.commitTransaction()
        except Exception as exc:  # noqa: BLE001
            doc.abortTransaction()
            QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Auto Drawing", str(exc))
            return
        try:
            made["page"].ViewObject.doubleClicked()
        except Exception:  # noqa: BLE001 -- the page is made; opening it is a nicety
            pass


def install():
    if not enabled():
        return
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND, _AutoDrawingCommand())
