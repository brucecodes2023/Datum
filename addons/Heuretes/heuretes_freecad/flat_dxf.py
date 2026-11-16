# SPDX-License-Identifier: LGPL-2.1-or-later
"""A small AutoCAD R12 DXF writer and reader for flat patterns (MOD-06, MFG-03).

R12 (AC1009) is the dialect laser and punch software reads most widely. Entities are
LINE, ARC, CIRCLE and TEXT on the shop layers:

    CUT        outer and inner outlines (always)
    BEND_UP    bend centre lines, bend up   (dash-dot)
    BEND_DOWN  bend centre lines, bend down (dash-dot)
    BEND       bend lines whose direction is not known (old unfolder)
    ETCH       part number and revision, single-stroke TEXT (font txt)

Coordinates come in millimetres and are written in the file's units ("mm" or "in");
$INSUNITS and $MEASUREMENT say which (readers that know only R12 ignore them). Free of
FreeCAD: ``flat_export`` turns a FreeCAD flat into the entity dicts used here.

Entity dicts: {"type": "line", "layer", "start": (x, y), "end": (x, y)},
{"type": "arc", "layer", "center", "radius", "start_angle", "end_angle"} (degrees,
counter-clockwise), {"type": "circle", "layer", "center", "radius"},
{"type": "text", "layer", "insert", "height", "text"} (middle-centre aligned).
"""

from __future__ import annotations

import math

MM_PER_IN = 25.4
CUT, BEND_UP, BEND_DOWN, BEND, ETCH = "CUT", "BEND_UP", "BEND_DOWN", "BEND", "ETCH"
# layer -> (ACI colour, line type)
LAYERS = {
    CUT: (7, "CONTINUOUS"),
    BEND_UP: (3, "DASHDOT"),
    BEND_DOWN: (1, "DASHDOT"),
    BEND: (5, "DASHDOT"),
    ETCH: (6, "CONTINUOUS"),
}
DASHDOT_MM = (6.0, -1.5, 0.0, -1.5)  # dash, gap, dot, gap


def line(layer, start, end) -> dict:
    return {"type": "line", "layer": layer, "start": tuple(start[:2]), "end": tuple(end[:2])}


def arc(layer, center, radius, start_angle, end_angle) -> dict:
    return {"type": "arc", "layer": layer, "center": tuple(center[:2]), "radius": float(radius),
            "start_angle": float(start_angle) % 360.0, "end_angle": float(end_angle) % 360.0}


def circle(layer, center, radius) -> dict:
    return {"type": "circle", "layer": layer, "center": tuple(center[:2]), "radius": float(radius)}


def text(layer, insert, height, value) -> dict:
    return {"type": "text", "layer": layer, "insert": tuple(insert[:2]), "height": float(height), "text": str(value)}


# --- geometry ------------------------------------------------------------------------------


def _in_sweep(angle, start, end) -> bool:
    """True when angle (degrees) is on the counter-clockwise sweep start -> end."""
    sweep = (end - start) % 360.0 or 360.0
    return (angle - start) % 360.0 <= sweep + 1e-9


def points_of(entity) -> list:
    """Points that bound an entity (its ends and the arc's extreme points)."""
    kind = entity["type"]
    if kind == "line":
        return [entity["start"], entity["end"]]
    if kind == "text":
        return [entity["insert"]]
    cx, cy = entity["center"]
    r = entity["radius"]
    if kind == "circle":
        return [(cx - r, cy - r), (cx + r, cy + r)]
    a0, a1 = entity["start_angle"], entity["end_angle"]
    out = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in (a0, a1)]
    out += [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
            for a in (0.0, 90.0, 180.0, 270.0) if _in_sweep(a, a0, a1)]
    return out


def bounding_box(entities, layers=None):
    """(xmin, ymin, xmax, ymax) over entities (on layers, when given); None when empty."""
    points = [p for e in entities if layers is None or e["layer"] in layers
              for p in points_of(e) if e["type"] != "text"]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def scaled(entity, factor: float) -> dict:
    """A copy with every length times factor (angles kept)."""
    out = dict(entity)
    for key in ("start", "end", "center", "insert"):
        if key in out:
            out[key] = tuple(v * factor for v in out[key])
    for key in ("radius", "height"):
        if key in out:
            out[key] = out[key] * factor
    return out


# --- writer -------------------------------------------------------------------------------------


def _num(value) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0.0" if text in ("", "-0") else text


class _Out:
    def __init__(self):
        self.lines = []

    def pair(self, code, value):
        self.lines.append(f"{code:>3}")
        self.lines.append(value if isinstance(value, str) else _num(value) if isinstance(value, float) else str(value))

    def point(self, base, xy, z=0.0):
        self.pair(base, float(xy[0]))
        self.pair(base + 10, float(xy[1]))
        self.pair(base + 20, float(z))


def write(entities, units: str = "mm") -> str:
    """DXF text for entities given in mm, written in units ("mm" or "in")."""
    units = "in" if str(units).lower().startswith("in") else "mm"
    factor = 1.0 / MM_PER_IN if units == "in" else 1.0
    entities = [scaled(e, factor) for e in entities]
    used = [CUT] + sorted({e["layer"] for e in entities} - {CUT}, key=list(LAYERS).index)
    box = bounding_box(entities) or (0.0, 0.0, 0.0, 0.0)
    out = _Out()
    out.pair(0, "SECTION")
    out.pair(2, "HEADER")
    out.pair(9, "$ACADVER")
    out.pair(1, "AC1009")
    out.pair(9, "$INSUNITS")
    out.pair(70, 1 if units == "in" else 4)
    out.pair(9, "$MEASUREMENT")
    out.pair(70, 0 if units == "in" else 1)
    out.pair(9, "$EXTMIN")
    out.point(10, box[:2])
    out.pair(9, "$EXTMAX")
    out.point(10, box[2:])
    out.pair(0, "ENDSEC")

    out.pair(0, "SECTION")
    out.pair(2, "TABLES")
    out.pair(0, "TABLE")
    out.pair(2, "LTYPE")
    out.pair(70, 2)
    out.pair(0, "LTYPE")
    out.pair(2, "CONTINUOUS")
    out.pair(70, 0)
    out.pair(3, "Solid line")
    out.pair(72, 65)
    out.pair(73, 0)
    out.pair(40, 0.0)
    dashes = [d * factor for d in DASHDOT_MM]
    out.pair(0, "LTYPE")
    out.pair(2, "DASHDOT")
    out.pair(70, 0)
    out.pair(3, "Dash dot __ . __ .")
    out.pair(72, 65)
    out.pair(73, len(dashes))
    out.pair(40, float(sum(abs(d) for d in dashes)))
    for dash in dashes:
        out.pair(49, float(dash))
    out.pair(0, "ENDTAB")
    out.pair(0, "TABLE")
    out.pair(2, "LAYER")
    out.pair(70, len(used))
    for name in used:
        colour, ltype = LAYERS.get(name, (7, "CONTINUOUS"))
        out.pair(0, "LAYER")
        out.pair(2, name)
        out.pair(70, 0)
        out.pair(62, colour)
        out.pair(6, ltype)
    out.pair(0, "ENDTAB")
    out.pair(0, "TABLE")
    out.pair(2, "STYLE")
    out.pair(70, 1)
    out.pair(0, "STYLE")
    out.pair(2, "STANDARD")
    out.pair(70, 0)
    out.pair(40, 0.0)
    out.pair(41, 1.0)
    out.pair(50, 0.0)
    out.pair(71, 0)
    out.pair(42, 2.5 * factor)
    out.pair(3, "txt")
    out.pair(4, "")
    out.pair(0, "ENDTAB")
    out.pair(0, "ENDSEC")

    out.pair(0, "SECTION")
    out.pair(2, "ENTITIES")
    for e in entities:
        kind = e["type"]
        out.pair(0, kind.upper())
        out.pair(8, e["layer"])
        if kind == "line":
            out.point(10, e["start"])
            out.point(11, e["end"])
        elif kind in ("arc", "circle"):
            out.point(10, e["center"])
            out.pair(40, float(e["radius"]))
            if kind == "arc":
                out.pair(50, float(e["start_angle"]))
                out.pair(51, float(e["end_angle"]))
        elif kind == "text":
            out.point(10, e["insert"])
            out.pair(40, float(e["height"]))
            out.pair(1, e["text"])
            out.pair(7, "STANDARD")
            out.pair(72, 4)  # middle (centre of the text box)
            out.point(11, e["insert"])
        else:
            raise ValueError(f"unknown DXF entity {kind!r}")
    out.pair(0, "ENDSEC")
    out.pair(0, "EOF")
    return "\r\n".join(out.lines) + "\r\n"


def save(path: str, entities, units: str = "mm") -> str:
    """Write the DXF (ASCII, CRLF) to path; returns path."""
    with open(path, "w", encoding="ascii", errors="replace", newline="") as handle:
        handle.write(write(entities, units))
    return path


# --- reader (tests and checks) ------------------------------------------------------------------


def read(data: str) -> dict:
    """{"header": {var: value}, "layers": {name: (colour, ltype)}, "entities": [dicts]} of a DXF
    this module (or another R12 writer) made. Values are in the file's units."""
    tokens = [t.strip() for t in data.splitlines()]
    pairs = [(int(tokens[i]), tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)]
    out = {"header": {}, "layers": {}, "entities": []}
    section = None
    current = None
    var = None

    def flush():
        if current is None:
            return
        kind = current.pop("_type")
        if section == "ENTITIES" and kind in ("LINE", "ARC", "CIRCLE", "TEXT"):
            g = current
            layer = g.get(8, "0")
            if kind == "LINE":
                out["entities"].append(line(layer, (float(g[10]), float(g[20])), (float(g[11]), float(g[21]))))
            elif kind == "ARC":
                out["entities"].append(arc(layer, (float(g[10]), float(g[20])), float(g[40]), float(g[50]),
                                           float(g[51])))
            elif kind == "CIRCLE":
                out["entities"].append(circle(layer, (float(g[10]), float(g[20])), float(g[40])))
            else:
                out["entities"].append(text(layer, (float(g.get(11, g[10])), float(g.get(21, g[20]))),
                                            float(g[40]), g.get(1, "")))
        elif section == "TABLES" and kind == "LAYER":
            out["layers"][current.get(2, "")] = (int(current.get(62, 7)), current.get(6, "CONTINUOUS"))

    for code, value in pairs:
        if code == 0:
            flush()
            current = None
            if value in ("SECTION", "ENDSEC", "EOF", "TABLE", "ENDTAB"):
                if value == "ENDSEC":
                    section = None
                continue
            current = {"_type": value}
            continue
        if code == 2 and current is None:
            section = section or value
            continue
        if section == "HEADER":
            if code == 9:
                var = value
            elif var is not None:
                out["header"].setdefault(var, value)
            continue
        if current is not None:
            current.setdefault(code, value)
    flush()
    return out
