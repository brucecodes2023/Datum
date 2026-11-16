# SPDX-License-Identifier: LGPL-2.1-or-later
"""A parts list on the drawing, and balloons that carry its item numbers (roadmap DRW-02).

* make(page, assembly) puts a parts list on the page, above the title block: one row per part
  the assembly places directly (a sub-assembly is one row), with the columns plan chapter 40
  gives for DRW-02: item, part number, revision, description, material, quantity. The header is
  on top and the items run down, as in that chapter's sheet figure.
* Item numbers are kept on the list (a map part -> number). A part added later gets the next
  free number; a part removed loses its row and its number is not given to another part. So
  balloons already on the sheet stay right. renumber() closes the gaps when the user wants it.
* add_balloons(view, parts_list) puts one balloon per row on a view, its arrow on the first
  placing of that part. A balloon remembers its part, not its number: refresh() writes the number
  the list has for it now.
* A document observer refreshes the lists when an assembly's contents change.

The list is a TechDraw symbol view (an SVG table in mm, drawn as vectors), not a new TechDraw
object type: that would need C++. TechDraw's DXF page export writes part views only, so the
table is in the PDF and not in the DXF.

Off switch: Preferences/BruceWorx/DrawingPartsList (default on).
"""

from __future__ import annotations

import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "DrawingPartsList"
COMMAND = "BruceWorx_PartsList"
ICON = "Heuretes_PartsList.svg"
GROUP = "BruceWorx"
LIST_PROP = "BW_PartsListOf"  # on the list: the assembly (an XLink)
ITEMS_PROP = "BW_Items"  # on the list: part key -> item number
SIZE_PROP = "BW_Size"  # on the list: the table's width and height written last, mm
BALLOON_PROP = "BW_BalloonPart"  # on a balloon: the part key it points at
BALLOON_LIST_PROP = "BW_BalloonList"  # on a balloon: the list's name
# Columns: plan chapter 40 (DRW-02): "item, P/N, rev, description, material, qty"; headings as its figure.
COLUMNS = (("item", "NO.", 10.0), ("part_number", "PART NUMBER", 42.0), ("revision", "REV", 10.0),
           ("description", "DESCRIPTION", 58.0), ("material", "MATERIAL", 28.0), ("qty", "QTY", 10.0))
ROW = 6.0  # mm
TEXT = 2.8  # mm cap height asked of the font
MARGIN = 10.0  # the templates' border
TITLE_BLOCK = 56.0  # its height over the border (bruceworx_drawing.new_drawing)
_state = {"observer": None, "scheduled": False, "busy": False}


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- rows from the assembly -----------------------------------------------------------------------


def part_key(target):
    """A name for a part that survives closing and reopening: its file and its object."""
    doc = target.Document
    return f"{os.path.basename(doc.FileName) if doc.FileName else doc.Name}#{target.Name}"


def components(assembly):
    """[(key, target, quantity, first placing)] for what the assembly places directly, in tree order."""
    import bruceworx_bom as bb

    order, found = [], {}
    for obj in getattr(assembly, "Group", []) or []:
        target, count = bb._target(obj)
        if target is None or count <= 0 or target is assembly or not bb._is_line(target):
            continue
        if target.isDerivedFrom("Part::Feature") and not target.isDerivedFrom("PartDesign::Body") \
                and not target.isDerivedFrom("Assembly::AssemblyObject"):
            shape = getattr(target, "Shape", None)
            if shape is None or shape.isNull() or not shape.Solids:
                continue  # a sketch or a datum in the assembly is not a part
        key = part_key(target)
        if key not in found:
            found[key] = [target, 0, obj]
            order.append(key)
        found[key][1] += count
    return [(key, found[key][0], found[key][1], found[key][2]) for key in order]


def rows(assembly, items):
    """The list's rows, sorted by item number. ``items`` (key -> number) gains the new parts."""
    import bruceworx_drawing as drawing

    out = []
    used = [int(v) for v in items.values()]
    for key, target, qty, _first in components(assembly):
        if key not in items:
            number = max(used, default=0) + 1
            used.append(number)
            items[key] = str(number)
        owner = drawing.part_of(target) or target
        values = drawing.part_values(owner, drawing.vault_root())
        out.append({"key": key, "item": int(items[key]), "part_number": values["part_number"],
                    "revision": values["revision"], "description": values["title"],
                    "material": values["material"], "qty": qty})
    out.sort(key=lambda r: r["item"])
    return out


# ----- the table ------------------------------------------------------------------------------------


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fit(text, width):
    """``text`` cut to what a column holds (osifont is narrow: about 0.6 of its height per letter)."""
    room = max(1, int((width - 2.0) / (TEXT * 0.62)))
    text = str(text)
    return text if len(text) <= room else text[: max(1, room - 1)] + "…"


def table_size(count):
    return sum(c[2] for c in COLUMNS), ROW * (count + 1)


def svg(list_rows):
    """The table as an SVG in mm: header on top, one row per item."""
    width, height = table_size(len(list_rows))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{width}mm" height="{height}mm" '
           f'viewBox="0 0 {width} {height}">',
           f'<g fill="none" stroke="#000000" stroke-width="0.35">'
           f'<rect x="0.175" y="0.175" width="{width - 0.35}" height="{height - 0.35}"/>']
    x = 0.0
    for _key, _title, col in COLUMNS[:-1]:
        x += col
        out.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{height}" stroke-width="0.25"/>')
    for i in range(1, len(list_rows) + 1):
        out.append(f'<line x1="0" y1="{ROW * i}" x2="{width}" y2="{ROW * i}" stroke-width="{0.35 if i == 1 else 0.18}"/>')
    out.append("</g>")
    out.append(f'<g font-family="osifont" font-size="{TEXT / 0.7:.2f}" fill="#000000" text-anchor="middle">')
    lines = [{key: title for key, title, _w in COLUMNS}] + [dict(r, item=str(r["item"]), qty=str(r["qty"]))
                                                           for r in list_rows]
    for i, line in enumerate(lines):
        x = 0.0
        y = ROW * i + ROW / 2 + TEXT / 2
        for key, _title, col in COLUMNS:
            text = _fit(line.get(key) or ("–" if i and key == "revision" else ""), col)
            if i and key in ("description", "part_number", "material"):
                out.append(f'<text x="{x + 1.2}" y="{y:.2f}" text-anchor="start">{_esc(text)}</text>')
            else:
                out.append(f'<text x="{x + col / 2}" y="{y:.2f}">{_esc(text)}</text>')
            x += col
    out.append("</g></svg>")
    return "\n".join(out)


# ----- the list object ------------------------------------------------------------------------------


def is_parts_list(obj):
    return LIST_PROP in getattr(obj, "PropertiesList", ())


def parts_lists(doc):
    return [o for o in doc.Objects if is_parts_list(o)]


def page_of(view):
    """The page a view is on, also through its projection group."""
    import bruceworx_drawing_callouts as follow

    return follow.page_of(view)


def _page_size(page):
    template = page.Template
    return float(template.Width), float(template.Height)


def assembly_of_page(page):
    """The assembly the page draws: the first view source that is one (links followed), or None."""
    for view in page.Views:
        views = list(getattr(view, "Views", [])) or [view]
        for item in [view] + views:
            for source in list(getattr(item, "Source", []) or []) + list(getattr(item, "XSource", []) or []):
                target = source.getLinkedObject(True) if hasattr(source, "getLinkedObject") else source
                if target is not None and target.isDerivedFrom("Assembly::AssemblyObject"):
                    return target
    return None


def make(page, assembly):
    """A parts list of ``assembly`` on ``page``, above the title block. Returns the list object."""
    import FreeCAD

    doc = page.Document
    obj = doc.addObject("TechDraw::DrawViewSymbol", "PartsList")
    obj.Label = "Parts list"
    obj.addProperty("App::PropertyXLink", LIST_PROP, GROUP, "The assembly this parts list reads")
    obj.addProperty("App::PropertyMap", ITEMS_PROP, GROUP, "Item number of each part (kept when rows change)")
    obj.addProperty("App::PropertyVector", SIZE_PROP, GROUP, "Width and height of the table, mm")
    obj.setEditorMode(SIZE_PROP, 2)
    setattr(obj, LIST_PROP, assembly)
    if "ScaleType" in obj.PropertiesList:
        obj.ScaleType = "Custom"
    obj.Scale = 1.0
    page.addView(obj)
    _write(obj)
    width, height = _page_size(page)
    w, h = table_size(len(getattr(obj, ITEMS_PROP)))
    # its right edge on the border, its foot 2 mm over the title block (X, Y are the centre, Y up)
    obj.X = width - MARGIN - w / 2
    obj.Y = MARGIN + TITLE_BLOCK + 2.0 + h / 2
    setattr(obj, SIZE_PROP, FreeCAD.Vector(w, h, 0))
    return obj


def _write(obj):
    """Rows, numbers and the SVG of one list. Returns its rows; keeps the foot and the right edge."""
    import FreeCAD

    assembly = getattr(obj, LIST_PROP)
    if assembly is None:
        return []
    items = dict(getattr(obj, ITEMS_PROP))
    list_rows = rows(assembly, items)
    present = {r["key"] for r in list_rows}
    # a number once given is kept for as long as the list lives, so it is never handed to another part
    if items != dict(getattr(obj, ITEMS_PROP)):
        setattr(obj, ITEMS_PROP, items)
    text = svg(list_rows)
    if obj.Symbol != text:
        obj.Symbol = text
    w, h = table_size(len(list_rows))
    old = getattr(obj, SIZE_PROP)
    if old.x > 0 and (abs(old.x - w) > 1e-9 or abs(old.y - h) > 1e-9):
        obj.X = obj.X.Value + (old.x - w) / 2
        obj.Y = obj.Y.Value + (h - old.y) / 2
        setattr(obj, SIZE_PROP, FreeCAD.Vector(w, h, 0))
    return [r for r in list_rows if r["key"] in present]


def item_numbers(obj):
    """{part key: item number} of the parts the list shows now."""
    assembly = getattr(obj, LIST_PROP)
    items = getattr(obj, ITEMS_PROP)
    if assembly is None:
        return {}
    return {key: int(items[key]) for key, _t, _q, _f in components(assembly) if key in items}


def renumber(obj):
    """Close the gaps: 1..n in the current order. Balloons follow at the next refresh (done here)."""
    assembly = getattr(obj, LIST_PROP)
    current = sorted(item_numbers(obj).items(), key=lambda kv: kv[1])
    setattr(obj, ITEMS_PROP, {key: str(i) for i, (key, _n) in enumerate(current, 1)})
    refresh(obj.Document)
    return assembly


# ----- balloons -------------------------------------------------------------------------------------


def balloons(doc):
    return [o for o in doc.Objects if BALLOON_PROP in o.PropertiesList]


def _centre_of(assembly, placing):
    """The middle of one placing of a part, in the assembly's document frame."""
    import Part

    try:
        shape = Part.getShape(assembly, placing.Name + ".", needSubElement=False, transform=True)
        if not shape.isNull():
            return shape.BoundBox.Center
    except Exception:  # noqa: BLE001
        pass
    return placing.Placement.Base


def add_balloons(view, parts_list, reach=(14.0, 13.0)):
    """One balloon per row on ``view`` (rows that already have one there keep it). Returns the new ones."""
    doc = view.Document
    page = page_of(parts_list) or page_of(view)
    assembly = getattr(parts_list, LIST_PROP)
    numbers = item_numbers(parts_list)
    have = {getattr(b, BALLOON_PROP) for b in balloons(doc) if getattr(b, "SourceView", None) == view}
    import bruceworx_drawing_callouts as follow

    scale = view.getScale() if hasattr(view, "getScale") else view.Scale
    half_w, half_h = follow.half_extent(view)
    made, counts = [], {}
    for b in balloons(doc):  # those already beside this view keep their places; new ones go below
        if getattr(b, "SourceView", None) == view:
            side = 1 if b.X.Value >= 0 else -1
            counts[side] = counts.get(side, 0) + 1
    for key, _target, _qty, first in components(assembly):
        if key in have or key not in numbers:
            continue
        # unscaled mm from the view's centre, as OriginX and OriginY want
        px, py = follow.view_point(view, _centre_of(assembly, first))
        balloon = doc.addObject("TechDraw::DrawViewBalloon", "Balloon")
        balloon.SourceView = view
        balloon.OriginX, balloon.OriginY = px, py
        # the bubble, in page mm from the view's centre: outside the outline, on the part's side
        # (alternating for a part on the centre line), one below the other so they do not meet
        side = (1 if px > 0 else -1) if abs(px * scale) > 1.0 else (1 if len(made) % 2 == 0 else -1)
        rank = counts[side] = counts.get(side, 0) + 1
        balloon.X = side * (half_w + reach[0])
        balloon.Y = half_h - reach[1] * (rank - 1)
        balloon.Text = str(numbers[key])
        balloon.addProperty("App::PropertyString", BALLOON_PROP, GROUP, "The part this balloon points at")
        balloon.addProperty("App::PropertyString", BALLOON_LIST_PROP, GROUP, "The parts list its number comes from")
        setattr(balloon, BALLOON_PROP, key)
        setattr(balloon, BALLOON_LIST_PROP, parts_list.Name)
        if page is not None:
            page.addView(balloon)
        made.append(balloon)
    return made


# ----- keeping up -----------------------------------------------------------------------------------


def refresh(doc, recompute=True):
    """Rewrite the document's parts lists and the numbers in its balloons. Returns what changed."""
    changed = []
    for obj in parts_lists(doc):
        before = (obj.Symbol, dict(getattr(obj, ITEMS_PROP)))
        _write(obj)
        if before != (obj.Symbol, dict(getattr(obj, ITEMS_PROP))):
            changed.append(obj)
        numbers = item_numbers(obj)
        for balloon in balloons(doc):
            if getattr(balloon, BALLOON_LIST_PROP) != obj.Name:
                continue
            number = numbers.get(getattr(balloon, BALLOON_PROP))
            if number is not None and balloon.Text != str(number):
                balloon.Text = str(number)
                changed.append(balloon)
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
            if doc.Restoring or not parts_lists(doc):
                continue
            try:
                out += refresh(doc, recompute)
            except Exception as exc:  # noqa: BLE001 -- never break the edit that triggered this
                FreeCAD.Console.PrintLog(f"BruceWorx parts list: {doc.Label}: {exc}\n")
    finally:
        _state["busy"] = False
    return out


def _schedule():
    import FreeCAD

    if not FreeCAD.GuiUp:
        refresh_all(recompute=False)
        return
    if _state["scheduled"]:
        return
    _state["scheduled"] = True
    from PySide import QtCore

    def run():
        _state["scheduled"] = False
        refresh_all()

    QtCore.QTimer.singleShot(0, run)


def _any_lists():
    import FreeCAD

    return any(parts_lists(d) for d in FreeCAD.listDocuments().values() if not d.Restoring)


class _Observer:
    """An assembly gained, lost or re-counted a part: the lists that read it follow."""

    def slotChangedObject(self, obj, prop):
        if _state["busy"] or prop not in ("Group", "ElementCount", "LinkedObject", "VisibilityList", "PartNumber",
                                          "Revision", "Description", "MaterialName"):
            return
        try:
            doc = obj.Document
            if doc.Restoring or doc.Transacting or not enabled() or is_parts_list(obj):
                return
            if _any_lists():
                _schedule()
        except Exception:  # noqa: BLE001
            pass

    def slotUndoDocument(self, _doc):
        if enabled() and _any_lists():
            _schedule()

    def slotRedoDocument(self, _doc):
        if enabled() and _any_lists():
            _schedule()


class _PartsListCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Parts List and Balloons",
                "ToolTip": "On a drawing of an assembly: a parts list above the title block and a balloon per "
                           "item on the selected view. Both follow the assembly; item numbers are kept."}

    def IsActive(self):
        import FreeCAD

        doc = FreeCAD.ActiveDocument
        return doc is not None and any(o.isDerivedFrom("TechDraw::DrawPage") for o in doc.Objects)

    def Activated(self):
        import FreeCAD
        import FreeCADGui as Gui
        from PySide import QtWidgets

        doc = FreeCAD.ActiveDocument
        selected = [o for o in Gui.Selection.getSelection() if o.isDerivedFrom("TechDraw::DrawViewPart")]
        page = page_of(selected[0]) if selected else next(
            (o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")), None)
        assembly = assembly_of_page(page) if page is not None else None
        if assembly is None:
            QtWidgets.QMessageBox.information(Gui.getMainWindow(), "Parts List",
                                              "This drawing shows no assembly. Make a drawing of an assembly first.")
            return
        doc.openTransaction("Parts list")
        try:
            existing = [o for o in parts_lists(doc) if page_of(o) == page and getattr(o, LIST_PROP) == assembly]
            parts_list = existing[0] if existing else make(page, assembly)
            if selected:
                add_balloons(selected[0], parts_list)
            refresh(doc, recompute=False)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        doc.recompute()


def install():
    import FreeCAD

    if not enabled():
        return None
    if _state["observer"] is None:
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])
    if FreeCAD.GuiUp:
        import FreeCADGui

        FreeCADGui.addCommand(COMMAND, _PartsListCommand())
    return _state["observer"]


def uninstall():
    import FreeCAD

    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        FreeCAD.removeDocumentObserver(observer)
