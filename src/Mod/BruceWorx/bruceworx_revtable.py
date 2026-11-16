# SPDX-License-Identifier: LGPL-2.1-or-later
"""Revision table and release stamp on drawings (roadmap DRW-05).

A drawing page of ours (bruceworx_drawing: one of the BruceWorx templates) gets, when its part has a
part number:

* a PRELIMINARY stamp while the part's revision is a pre-release number (WIP 01, 02 ...), above the
  title block, with the part number and revision ("P/N-REV") under it. Releasing the part (rev A)
  removes the stamp;
* a revision table in the top-right corner of the frame once the part has been released: one row per
  released revision, from the vault's own records (the approve of rev A, the release of each ECO):
  REV, DESCRIPTION (the release note), ECO, DATE and APPROVED (who released it). The title row
  prints the part number and revision.

Both are TechDraw views on the page, made and refilled by bruceworx_drawing.fill_page; where the user
dragged them stays. Nothing is shown without a part number.

Off switch: Preferences/BruceWorx/DrawingRevisionTable (default on).
"""

from __future__ import annotations

import textwrap
from xml.sax.saxutils import escape

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "DrawingRevisionTable"
ROLE_PROP = "BW_RevRole"  # on our views: "table" or "stamp"
GROUP = "BruceWorx"
STAMP_TEXT = "PRELIMINARY"
STAMP_COLOR = (0.75, 0.0, 0.0, 0.0)
STAMP_SIZE = 6.0  # mm
FRAME_MARGIN = 10.0  # make_templates._frame: the frame is 10 mm in from the top and right edges
TITLE_BLOCK_H = 56.0  # make_templates: the title block's height (bottom-right of the frame)
TITLE_BLOCK_W = 180.0
# Columns of the table: (heading, width mm). The description wraps.
COLUMNS = (("REV", 9.0), ("DESCRIPTION", 58.0), ("ECO", 18.0), ("DATE", 19.0), ("APPROVED", 20.0))
ROW_H = 5.0
LINE_H = 3.6  # a wrapped description line


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


def _ds():
    from bruceworx import drawing_standards

    return drawing_standards


# ----- what the vault says ------------------------------------------------------------------------


def rows_of(item, ecos=None) -> list:
    """[{rev, description, eco, date, by}] of an item's released revisions, oldest first, from the
    release records the vault keeps (approve for A, release_eco for later letters)."""
    from bruceworx import pdm

    ds = _ds()
    ecos = ecos or {}
    out = []
    for rev in item.revisions:
        if pdm.is_prerelease(rev.code):
            continue
        record = rev.record("release_eco") or rev.record("approve")
        if record is None:
            continue
        eco = record.get("eco") or rev.eco or ""
        note = record.get("note") or getattr(ecos.get(eco), "note", "") or ""
        out.append({"rev": rev.code, "description": " ".join(str(note).split()), "eco": eco,
                    "date": ds.date_text(record.get("at", "")) if record.get("at") else "",
                    "by": ds.actor_name(record.get("by", ""))})
    return out


def history(root, part_number):
    """(rows, revision code, prerelease) of the vault's item, or None when the vault has no such item."""
    if not root or not part_number:
        return None
    try:
        from bruceworx import pdm
        from bruceworx.pdm_folder import FolderVault

        vault = FolderVault(root)
        item = vault.items.get(part_number)
        if item is None:
            return None
        return rows_of(item, vault.ecos), item.latest.code, pdm.is_prerelease(item.latest.code)
    except Exception as exc:  # noqa: BLE001 -- an unreadable vault must not stop the drawing
        import FreeCAD

        FreeCAD.Console.PrintLog(f"BruceWorx revision table: vault not read: {exc}\n")
        return None


def pn_rev(part_number, code) -> str:
    """"LE-01-01017-001-A"; a pre-release "LE-01-01017-001 WIP 02"."""
    text = _ds().revision_text(code)
    if not text:
        return part_number
    return f"{part_number} {text}" if text.startswith("WIP") else f"{part_number}-{text}"


# ----- drawing it ---------------------------------------------------------------------------------


class _Style:
    """make_templates.py's title-block style (that script is not installed), so the table matches it."""

    FONT = "osifont"
    LINE = "#000000"
    LABEL_COLOR = "#5f6368"
    BLOCK_W = 0.35
    RULE_W = 0.18
    LABEL_SIZE = 1.8
    PAD = 1.5
    VALUE_SIZE = 2.8


def _wrap(text, width_mm, size):
    """Lines of ``text`` that fit ``width_mm`` at letter height ``size`` (osifont: about 0.6 size per
    letter; a long word is cut)."""
    per_line = max(4, int((width_mm - 2) / (0.6 * size)))
    return textwrap.wrap(text, per_line, break_long_words=True) or [""]


def table_svg(rows, title) -> tuple:
    """(svg text, width, height) of the revision table, origin at its top-left, sizes in mm."""
    mt = _Style
    width = sum(w for _h, w in COLUMNS)
    size, label = mt.VALUE_SIZE * 0.9, mt.LABEL_SIZE
    body = []
    heights = []
    for row in rows:
        lines = _wrap(row["description"], COLUMNS[1][1], size)
        heights.append(max(ROW_H, 2.4 + LINE_H * len(lines)))
        body.append(lines)
    height = ROW_H * 2 + sum(heights)
    font = f'font-family="{mt.FONT}"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" '
             f'viewBox="0 0 {width} {height}">',
             f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" stroke="{mt.LINE}" '
             f'stroke-width="{mt.BLOCK_W}"/>',
             f'<text x="{mt.PAD}" y="{ROW_H - 1.4}" {font} font-size="{size}" font-weight="bold">'
             f'REVISIONS</text>',
             f'<text x="{width - mt.PAD}" y="{ROW_H - 1.4}" {font} font-size="{size}" text-anchor="end">'
             f'{escape(title)}</text>',
             f'<line x1="0" y1="{ROW_H}" x2="{width}" y2="{ROW_H}" stroke="{mt.LINE}" stroke-width="{mt.RULE_W}"/>',
             f'<line x1="0" y1="{2 * ROW_H}" x2="{width}" y2="{2 * ROW_H}" stroke="{mt.LINE}" '
             f'stroke-width="{mt.RULE_W}"/>']
    x = 0.0
    for heading, w in COLUMNS:
        parts.append(f'<text x="{x + w / 2}" y="{2 * ROW_H - 1.5}" {font} font-size="{label}" '
                     f'fill="{mt.LABEL_COLOR}" text-anchor="middle">{heading}</text>')
        if x > 0:
            parts.append(f'<line x1="{x}" y1="{ROW_H}" x2="{x}" y2="{height}" stroke="{mt.LINE}" '
                         f'stroke-width="{mt.RULE_W}"/>')
        x += w
    y = 2 * ROW_H
    for row, lines, h in zip(rows, body, heights):
        x = 0.0
        cells = (row["rev"], None, row["eco"], row["date"], row["by"])
        for (_heading, w), value in zip(COLUMNS, cells):
            if value is None and len(lines) == 1:
                parts.append(f'<text x="{x + mt.PAD}" y="{y + h / 2 + size * 0.35}" {font} font-size="{size}">'
                             f'{escape(lines[0])}</text>')
            elif value is None:
                for i, line in enumerate(lines):
                    parts.append(f'<text x="{x + mt.PAD}" y="{y + 1.4 + LINE_H * (i + 1) - 0.8}" {font} '
                                 f'font-size="{size}">{escape(line)}</text>')
            else:
                parts.append(f'<text x="{x + w / 2}" y="{y + h / 2 + size * 0.35}" {font} font-size="{size}" '
                             f'text-anchor="middle">{escape(str(value))}</text>')
            x += w
        y += h
        parts.append(f'<line x1="0" y1="{y}" x2="{width}" y2="{y}" stroke="{mt.LINE}" stroke-width="{mt.RULE_W}"/>')
    parts.append("</svg>")
    return "\n".join(parts), width, height


def _ours(page, role):
    return next((v for v in page.Views if v is not None and ROLE_PROP in v.PropertiesList
                 and getattr(v, ROLE_PROP) == role), None)


def _mark(view, role):
    view.addProperty("App::PropertyString", ROLE_PROP, GROUP, "What BruceWorx made this view for")
    setattr(view, ROLE_PROP, role)
    view.setEditorMode(ROLE_PROP, 2)


def _remove(page, role):
    view = _ours(page, role)
    if view is not None:
        page.Document.removeObject(view.Name)
        return True
    return False


def _sheet_size(page):
    template = page.Template
    return float(template.Width), float(template.Height)


def _set_stamp(page, part_number, code):
    lines = [STAMP_TEXT, pn_rev(part_number, code)]
    view = _ours(page, "stamp")
    made = view is None
    if made:
        view = page.Document.addObject("TechDraw::DrawViewAnnotation", "PreliminaryStamp")
        view.Label = "Preliminary stamp"
        _mark(view, "stamp")
        view.TextSize = STAMP_SIZE
        view.TextColor = STAMP_COLOR
        view.TextStyle = "Bold"
        width, _height = _sheet_size(page)
        view.X = width - FRAME_MARGIN - TITLE_BLOCK_W / 2
        view.Y = FRAME_MARGIN + TITLE_BLOCK_H + STAMP_SIZE * 1.5
        page.addView(view)
    if list(view.Text) != lines:
        view.Text = lines
        return True
    return made


def _set_table(page, rows, title):
    svg, w, h = table_svg(rows, title)
    view = _ours(page, "table")
    made = view is None
    if made:
        view = page.Document.addObject("TechDraw::DrawViewSymbol", "RevisionTable")
        view.Label = "Revision table"
        _mark(view, "table")
        view.Scale = 1.0
        page.addView(view)
    if view.Symbol != svg:
        view.Symbol = svg
        made = True
    if made or "BW_RevSize" not in view.PropertiesList or tuple(view.BW_RevSize) != (w, h):
        # A view is placed by its centre: keep the table's top-right corner in the frame's corner
        # (unless the user moved it: then keep its top-right where they put it).
        width, height = _sheet_size(page)
        if "BW_RevSize" not in view.PropertiesList:
            view.addProperty("App::PropertyVector", "BW_RevSize", GROUP, "Size the table was placed for")
            view.setEditorMode("BW_RevSize", 2)
            right, top = width - FRAME_MARGIN, height - FRAME_MARGIN
        else:
            old = view.BW_RevSize
            right, top = view.X.Value + old.x / 2, view.Y.Value + old.y / 2
        view.X, view.Y = right - w / 2, top - h / 2
        import FreeCAD

        view.BW_RevSize = FreeCAD.Vector(w, h, 0)
        # placed or grown (a new row): views it now covers move down, as far as the title block allows
        _clear_of(page, view.X.Value - w / 2, view.Y.Value - h / 2)
        made = True
    return made


GAP = 5.0  # mm between the table or title block and a view moved clear of it


def _extent(view):
    """(left, bottom, right, top) of a drawn part view on the page, from its visible edges; None when it
    has none yet."""
    try:
        edges = view.getVisibleEdges()
    except Exception:  # noqa: BLE001
        return None
    if not edges:
        return None
    xs = [abs(v.Point.x) for e in edges for v in e.Vertexes] + [abs(e.BoundBox.XMax) for e in edges] \
        + [abs(e.BoundBox.XMin) for e in edges]
    ys = [abs(e.BoundBox.YMax) for e in edges] + [abs(e.BoundBox.YMin) for e in edges]
    half_w, half_h = max(xs), max(ys)  # drawn centred on the view's position
    x, y = _page_xy(view)
    return x - half_w, y - half_h, x + half_w, y + half_h


def part_views(page):
    """The page's part views, also those inside a projection group (page.Views lists only the group)."""
    out = []
    for view in page.Views:
        if view is None:
            continue
        if view.isDerivedFrom("TechDraw::DrawProjGroup"):
            out += [v for v in view.Views if v is not None and v.isDerivedFrom("TechDraw::DrawViewPart")]
        elif view.isDerivedFrom("TechDraw::DrawViewPart"):
            out.append(view)
    return out


def _page_xy(view):
    x, y = view.X.Value, view.Y.Value
    for parent in view.InList:
        if parent.isDerivedFrom("TechDraw::DrawProjGroup"):
            return x + parent.X.Value, y + parent.Y.Value
    return x, y


def _clear_of(page, left, bottom):
    """A new table at (left .. frame right, bottom .. frame top): move the page's projection groups and
    loose part views below it when they reach into it, as far as the title block allows."""
    movers = {}
    for view in part_views(page):
        box = _extent(view)
        if box is None:
            continue
        group = next((p for p in view.InList if p.isDerivedFrom("TechDraw::DrawProjGroup")), None)
        movers.setdefault((group or view).Name, [group or view, []])[1].append(box)
    floor = FRAME_MARGIN + TITLE_BLOCK_H + GAP
    for obj, boxes in movers.values():
        top = max(b[3] for b in boxes)
        low = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        if right <= left or top <= bottom - GAP:
            continue  # clear of the table already
        drop = top - (bottom - GAP)
        drop = min(drop, max(0.0, low - floor))
        if drop > 0:
            obj.Y = obj.Y.Value - drop


def apply(page, part_values) -> list:
    """Bring the page's stamp and revision table up to date with ``part_values``
    (bruceworx_drawing.part_values: part_number, revision; and the vault root under "root").
    Returns what changed: "stamp", "table", "-stamp", "-table"."""
    if not enabled() or getattr(page, "Template", None) is None:
        return []
    pn = part_values.get("part_number", "")
    if not pn:
        return []
    found = history(part_values.get("root", ""), pn)
    if found is not None:
        rows, code, prerelease = found
    else:  # no vault item: the part's own Revision property
        from bruceworx import pdm

        code = part_values.get("revision", "") or ""
        rows, prerelease = [], (not code) or pdm.is_prerelease(code)
    changed = []
    if prerelease:
        if _set_stamp(page, pn, code):
            changed.append("stamp")
    elif _remove(page, "stamp"):
        changed.append("-stamp")
    if rows:
        if _set_table(page, rows, pn_rev(pn, rows[-1]["rev"])):  # the last released, not one in work
            changed.append("table")
    elif _remove(page, "table"):
        changed.append("-table")
    return changed
