# SPDX-License-Identifier: LGPL-2.1-or-later
"""Generate the drawing templates in Templates/ (DRW-01). Run from anywhere:

    python3 src/Mod/BruceWorx/make_templates.py

One title-block design for every sheet, so the ten SVGs stay consistent:
ANSI A-D landscape, ISO A4-A1 landscape, ANSI A and ISO A4 portrait. The
files are TechDraw SVG templates: sizes in mm, and each field a
``<text freecad:editable="name"><tspan>`` that TechDraw keeps in the page
template's EditableTexts (src/Mod/TechDraw/App/DrawSVGTemplate.cpp). Every
tspan of an editable text gets the whole value, so a multi-line block is one
field per line (tol1..tol4). bruceworx_drawing.py fills the fields; the field
names are drawing_standards.FIELDS.

Two parts are switched by bruceworx_drawing.customize_svg in the copy a page
uses: the logo slot (``<rect id="bw-logo-slot">``, replaced by an ``<image>``)
and the projection symbol (groups ``bw-proj-third`` / ``bw-proj-first``, one
of them ``display="none"``).

No brand text is drawn on the sheet: the company comes from the vault.
BRAND is the one place the product name appears (SVG metadata only).
"""

import os

BRAND = "BruceWorx"  # metadata only; rename the product here
FONT = "osifont"  # bundled with TechDraw (Gui/Resources/fonts), also its dimension font
LABEL_COLOR = "#5f6368"
LINE = "#000000"
FRAME_W = 0.5  # sheet frame
BLOCK_W = 0.35  # title block outline
RULE_W = 0.18  # lines inside the title block
TICK_W = 0.25  # zone ticks and centring marks
LABEL_SIZE = 1.8  # field captions
PAD = 1.5  # captions and values start this far from a cell's left line
VALUE_SIZE = 2.8  # most values

# key -> (family, size letter, width, height, zones across, zones down)
SHEETS = {
    "ANSI_A_Landscape": ("ANSI", "A", 279.4, 215.9, 4, 4),
    "ANSI_A_Portrait": ("ANSI", "A", 215.9, 279.4, 4, 4),
    "ANSI_B_Landscape": ("ANSI", "B", 431.8, 279.4, 8, 4),
    "ANSI_C_Landscape": ("ANSI", "C", 558.8, 431.8, 8, 6),
    "ANSI_D_Landscape": ("ANSI", "D", 863.6, 558.8, 8, 4),
    "ISO_A4_Landscape": ("ISO", "A4", 297.0, 210.0, 6, 4),
    "ISO_A4_Portrait": ("ISO", "A4", 210.0, 297.0, 4, 6),
    "ISO_A3_Landscape": ("ISO", "A3", 420.0, 297.0, 8, 6),
    "ISO_A2_Landscape": ("ISO", "A2", 594.0, 420.0, 12, 8),
    "ISO_A1_Landscape": ("ISO", "A1", 841.0, 594.0, 16, 12),
}

# The title block: 180 x 56 mm, in the bottom-right corner of the frame.
TB_W, TB_H = 180.0, 56.0
LEFT_W = 76.0  # company, tolerances, approvals; the rest is title, numbers, properties
ZONE_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"  # no I or O


def _f(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


class Svg:
    def __init__(self):
        self.parts = []

    def add(self, text):
        self.parts.append(text)

    def line(self, x1, y1, x2, y2, width=RULE_W, extra=""):
        self.add(f'<path d="M{_f(x1)},{_f(y1)} L{_f(x2)},{_f(y2)}" stroke="{LINE}" stroke-width="{width}" '
                 f'fill="none"{extra}/>')

    def rect(self, x, y, w, h, width=RULE_W):
        self.add(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" fill="none" stroke="{LINE}" '
                 f'stroke-width="{width}"/>')

    def text(self, x, y, value, size, color=LINE, anchor="start", field=None, autofill=None, ident=None):
        attrs = f'x="{_f(x)}" y="{_f(y)}" font-family="{FONT}" font-size="{_f(size)}" fill="{color}"'
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        if ident:
            attrs += f' id="{ident}"'
        if field:
            attrs += f' id="bw-field-{field}" freecad:editable="{field}"'
            if autofill:
                attrs += f' freecad:autofill="{autofill}"'
        value = (value or "").replace("&", "&amp;").replace("<", "&lt;")
        self.add(f'<text {attrs}><tspan x="{_f(x)}" y="{_f(y)}">{value}</tspan></text>')

    def label(self, x, y, value):
        self.text(x + PAD, y + 2.6, value, LABEL_SIZE, LABEL_COLOR)

    def field(self, x, y, name, size=VALUE_SIZE, anchor="start", autofill=None):
        self.text(x, y, "", size, anchor=anchor, field=name, autofill=autofill)


def _frame(svg, family, width, height, across, down):
    """Frame, zone ticks and labels, centring marks. Returns the frame rectangle."""
    left = 20.0 if family == "ISO" else 10.0
    margin = 10.0
    x0, y0, x1, y1 = left, margin, width - margin, height - margin
    svg.rect(x0, y0, x1 - x0, y1 - y0, FRAME_W)
    # Zones: numbers along the top and bottom, letters down the sides. ASME counts
    # numbers from the right and letters from the bottom; ISO from the top-left.
    zone_w, zone_h = (x1 - x0) / across, (y1 - y0) / down
    outer = 5.0  # ticks run from the frame halfway into the margin
    for i in range(1, across):
        x = x0 + i * zone_w
        svg.line(x, y0, x, y0 - outer, TICK_W)
        svg.line(x, y1, x, y1 + outer, TICK_W)
    for j in range(1, down):
        y = y0 + j * zone_h
        svg.line(x0, y, x0 - outer, y, TICK_W)
        svg.line(x1, y, x1 + outer, y, TICK_W)
    for i in range(across):
        number = str(across - i if family == "ANSI" else i + 1)
        cx = x0 + (i + 0.5) * zone_w
        svg.text(cx, y0 - 3.2, number, 2.5, LABEL_COLOR, "middle")
        svg.text(cx, y1 + 5.0, number, 2.5, LABEL_COLOR, "middle")
    for j in range(down):
        letter = ZONE_LETTERS[(down - 1 - j) if family == "ANSI" else j]
        cy = y0 + (j + 0.5) * zone_h + 0.9
        svg.text(x0 - (left / 2 if family == "ISO" else 5.0), cy, letter, 2.5, LABEL_COLOR, "middle")
        svg.text(x1 + 5.0, cy, letter, 2.5, LABEL_COLOR, "middle")
    # Centring marks (ISO 5457) on all four sides, from the trimmed edge to the frame
    # (not into it: the title block may sit there).
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    svg.line(mx, 0, mx, y0, FRAME_W)
    svg.line(mx, height, mx, y1, FRAME_W)
    svg.line(0, my, x0, my, FRAME_W)
    svg.line(width, my, x1, my, FRAME_W)
    return x0, y0, x1, y1


def _projection(svg, x, y, first):
    """Projection symbol in a 22 x 9 box at (x, y): a truncated cone and its end view.
    Third angle: the cone's small end faces the circles; first angle: it faces away."""
    ident = "bw-proj-first" if first else "bw-proj-third"
    hidden = ' display="none"' if first else ""
    svg.add(f'<g id="{ident}"{hidden}>')
    cy = y + 4.5
    big, small = 3.6, 1.8
    if first:
        pts = [(x + 2, cy - small), (x + 9, cy - big), (x + 9, cy + big), (x + 2, cy + small)]
    else:
        pts = [(x + 2, cy - big), (x + 9, cy - small), (x + 9, cy + small), (x + 2, cy + big)]
    d = "M" + " L".join(f"{_f(px)},{_f(py)}" for px, py in pts) + " Z"
    svg.add(f'<path d="{d}" fill="none" stroke="{LINE}" stroke-width="{RULE_W * 1.4:.2f}"/>')
    cx = x + 16
    svg.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(big)}" fill="none" stroke="{LINE}" '
            f'stroke-width="{RULE_W * 1.4:.2f}"/>')
    svg.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(small)}" fill="none" stroke="{LINE}" '
            f'stroke-width="{RULE_W * 1.4:.2f}"/>')
    dash = ' stroke-dasharray="2.4,0.6,0.4,0.6"'
    svg.line(x + 0.8, cy, cx + big + 1.2, cy, 0.13, dash)
    svg.line(cx, cy - big - 1.2, cx, cy + big + 1.2, 0.13, dash)
    svg.add("</g>")


def _title_block(svg, right, bottom, size_letter):
    x0, y0 = right - TB_W, bottom - TB_H
    xl = x0 + LEFT_W  # left/right split
    svg.add('<g id="bw-title-block">')
    svg.add(f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(TB_W)}" height="{_f(TB_H)}" fill="none" '
            f'stroke="{LINE}" stroke-width="{BLOCK_W}"/>')
    svg.line(xl, y0, xl, bottom, BLOCK_W)

    # Left: company and logo (14), general tolerances (20), approvals (22).
    r1, r2 = y0 + 14, y0 + 34
    svg.line(x0, r1, xl, r1)
    svg.line(x0, r2, xl, r2)
    svg.field(x0 + PAD, y0 + 9.0, "company", 4.0)
    svg.add(f'<rect id="bw-logo-slot" x="{_f(xl - 27)}" y="{_f(y0 + 2)}" width="25" height="10" '
            'fill="none" stroke="none"/>')
    svg.label(x0, r1, "UNLESS OTHERWISE SPECIFIED")
    for index in range(4):
        svg.field(x0 + PAD, r1 + 7.0 + index * 3.5, f"tol{index + 1}", 2.5)
    # Approvals: role | name | date, a caption row and three rows.
    col_name, col_date = x0 + 18, x0 + 52
    head = r2 + 4.0
    row_h = (bottom - head) / 3
    svg.line(x0, head, xl, head)
    svg.text(col_name + PAD, head - 1.2, "NAME", LABEL_SIZE, LABEL_COLOR)
    svg.text(col_date + PAD, head - 1.2, "DATE", LABEL_SIZE, LABEL_COLOR)
    for index, role in enumerate(("drawn", "checked", "approved")):
        top = head + index * row_h
        if index:
            svg.line(x0, top, xl, top)
        base = top + row_h - 1.4
        svg.text(x0 + PAD, base, role.upper(), LABEL_SIZE, LABEL_COLOR)
        svg.field(col_name + PAD, base, f"{role}_by", 2.5)
        svg.field(col_date + PAD, base, f"{role}_date", 2.5)
    svg.line(col_name, r2, col_name, bottom)
    svg.line(col_date, r2, col_date, bottom)

    # Right: title (14), part number | rev (12), material | finish (10),
    # mass | scale | units | sheet (10), size | projection | standard (10).
    w = right - xl
    rows = [y0 + 14, y0 + 26, y0 + 36, y0 + 46]
    for y in rows:
        svg.line(xl, y, right, y)
    svg.label(xl, y0, "TITLE")
    svg.field(xl + PAD, y0 + 10.6, "title", 4.2)
    split = xl + w * 0.7
    svg.line(split, rows[0], split, rows[1])
    svg.label(xl, rows[0], "PART NUMBER")
    svg.field(xl + PAD, rows[1] - 2.2, "part_number", 3.6)
    svg.label(split, rows[0], "REV")
    svg.field(split + PAD, rows[1] - 2.2, "revision", 3.6)
    half = xl + w / 2
    svg.line(half, rows[1], half, rows[2])
    svg.label(xl, rows[1], "MATERIAL")
    svg.field(xl + PAD, rows[2] - 1.8, "material")
    svg.label(half, rows[1], "FINISH")
    svg.field(half + PAD, rows[2] - 1.8, "finish")
    cells = [("MASS", "mass", None, 0.27), ("SCALE", "scale", "scale", 0.23), ("UNITS", "units", None, 0.2),
             ("SHEET", "sheet", None, 0.3)]
    x = xl
    for caption, name, autofill, share in cells:
        if x > xl:
            svg.line(x, rows[2], x, rows[3])
        svg.label(x, rows[2], caption)
        svg.field(x + PAD, rows[3] - 1.8, name, autofill=autofill)
        x += w * share
    # Bottom row: sheet size, projection, standard note.
    size_w, proj_w = 16.0, 42.0
    svg.line(xl + size_w, rows[3], xl + size_w, bottom)
    svg.line(xl + size_w + proj_w, rows[3], xl + size_w + proj_w, bottom)
    svg.label(xl, rows[3], "SIZE")
    svg.text(xl + size_w / 2, bottom - 1.6, size_letter, 3.6, anchor="middle")
    px = xl + size_w
    _projection(svg, px + 0.5, rows[3] + 0.6, first=False)
    _projection(svg, px + 0.5, rows[3] + 0.6, first=True)
    svg.text(px + 24, rows[3] + 4.2, "PROJECTION", LABEL_SIZE, LABEL_COLOR)
    svg.field(px + 24, bottom - 2.0, "projection", 2.2)
    svg.field(px + proj_w + PAD, rows[3] + 4.6, "standard_note", 2.2)
    svg.text(px + proj_w + PAD, bottom - 2.0, "DO NOT SCALE DRAWING", LABEL_SIZE, LABEL_COLOR)
    svg.add("</g>")


def build(key):
    family, letter, width, height, across, down = SHEETS[key]
    svg = Svg()
    svg.add('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    svg.add(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/" version="1.1" '
            f'width="{_f(width)}mm" height="{_f(height)}mm" viewBox="0 0 {_f(width)} {_f(height)}">')
    svg.add(f"<title>{family} {letter} {'portrait' if 'Portrait' in key else 'landscape'}</title>")
    svg.add(f'<metadata><dc:source>{BRAND} drawing template {key}, generated by make_templates.py'
            '</dc:source></metadata>')
    _x0, _y0, x1, y1 = _frame(svg, family, width, height, across, down)
    _title_block(svg, x1, y1, letter)
    svg.add("</svg>")
    return "\n".join(svg.parts) + "\n"


def main(out_dir=None):
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "Templates")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for key in SHEETS:
        path = os.path.join(out_dir, key + ".svg")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(build(key))
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in main():
        print(written_path)
