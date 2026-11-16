# SPDX-License-Identifier: LGPL-2.1-or-later
"""How an agent sees and changes a design in few calls (roadmap AUT-06). No GUI here.

Four things, each behind one bridge tool (bruceworx_bridge_tools):

* summary(doc)          the parts, how each can be edited, sizes, masses, the Parameters sheet, the
                        assemblies and what is hidden or in error, as a short text;
* render(doc, ...)      shaded views and sections drawn here in software (numpy), so they work without
                        a window and never move the user's camera; one PNG with a legend;
* fit(doc, a, b)        how two parts meet: clash, contact or gap, and their coaxial holes and shafts
                        with the diametral clearance of each;
* snapshot() / diff()   what a parameter edit changed (volume and size per part).

Parts are found the same way for all four (parts(doc)): the occurrences of every assembly
(bruceworx_interference), else the document's top-level solids, folders opened.
Lengths are mm.
"""

from __future__ import annotations

import io
import math
import struct
import zlib

PALETTE = ((66, 133, 200), (226, 131, 52), (88, 166, 92), (200, 82, 82), (140, 110, 190), (150, 110, 90),
           (214, 120, 180), (120, 130, 140), (180, 170, 60), (70, 170, 180), (150, 190, 235), (245, 185, 130),
           (160, 215, 160), (235, 160, 160), (195, 180, 225), (205, 180, 165))
BACKGROUND = (255, 255, 255)
EDGE = (25, 28, 32)
VIEWS = {  # name: (towards the viewer, up)
    "iso": ((1, -1, 1), (0, 0, 1)), "iso_back": ((-1, 1, 1), (0, 0, 1)), "iso_below": ((1, -1, -1), (0, 0, 1)),
    "front": ((0, -1, 0), (0, 0, 1)), "back": ((0, 1, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)), "bottom": ((0, 0, -1), (0, -1, 0)),
    "right": ((1, 0, 0), (0, 0, 1)), "left": ((-1, 0, 0), (0, 0, 1)),
}
SECTION_VIEW = {"x": "right", "y": "front", "z": "top"}
MAX_VIEWS = 4
MAX_LEGEND = 20
MAX_TRIANGLES = 120000
AXIS_TOL_DEG = 0.5  # two axes this close in direction are parallel
COAXIAL_TOL = 0.05  # mm between two axes that count as one
MAX_ROWS = 40  # longest list in a text report


class AgentError(Exception):
    """Something to say to the caller in one sentence."""


def _app():
    import FreeCAD

    return FreeCAD


# ----- the parts of a document ----------------------------------------------------------------------


class Item:
    """One part as placed: ``obj`` is the object that defines it (a link's target), ``shape`` its solids
    in the document's frame, ``key`` its object name or its path in the assembly."""

    __slots__ = ("label", "key", "obj", "shape", "hidden", "assembly")

    def __init__(self, label, key, obj, shape, hidden=False, assembly=None):
        self.label, self.key, self.obj, self.shape = label, key, obj, shape
        self.hidden, self.assembly = hidden, assembly


def _resolve(part_key):
    App = _app()
    doc_name, _, name = part_key.partition("#")
    try:
        return App.getDocument(doc_name).getObject(name)
    except Exception:  # noqa: BLE001
        return None


def parts(doc, include_hidden=False):
    """[Item] for the document; hidden parts only when asked (then marked)."""
    import Part

    import bruceworx_interference as bi

    out = []
    for asm in bi.assemblies(doc):
        if any(p.TypeId == "Assembly::AssemblyObject" for p in asm.InListRecursive):
            continue  # a sub-assembly: its parts come through the top one
        shown = {o.sub for o in bi.occurrences(asm)}
        for occ in bi.occurrences(asm, include_hidden=True):
            hidden = occ.sub not in shown
            if hidden and not include_hidden:
                continue
            out.append(Item(occ.label, occ.sub.rstrip("."), _resolve(occ.part), occ.shape, hidden, asm.Name))

    def walk(objects, top=None, prefix="", label_prefix="", parent_hidden=False):
        """``top`` and ``prefix``: the part container the objects are in and the path to them inside it."""
        for obj in objects:
            if obj.TypeId == "Assembly::AssemblyObject" or obj.TypeId in bi.SKIPPED_TYPES:
                continue
            hidden = parent_hidden or not getattr(obj, "Visibility", True)
            if hidden and not include_hidden:
                continue
            if obj.isDerivedFrom("App::DocumentObjectGroup"):
                walk(list(obj.Group), top, prefix if top is None else f"{prefix}{obj.Name}.", label_prefix, hidden)
                continue
            if obj.isDerivedFrom("App::Part"):  # a container: its parts, placed by it
                walk(list(obj.Group), top or obj, "" if top is None else f"{prefix}{obj.Name}.",
                     f"{label_prefix}{obj.Label} / ", hidden)
                continue
            if not hasattr(obj, "Shape") and not obj.isDerivedFrom("App::Link"):
                continue
            try:
                shape = Part.getShape(obj) if top is None else \
                    Part.getShape(top, f"{prefix}{obj.Name}.", needSubElement=False, transform=True)
            except Exception:  # noqa: BLE001
                continue
            if shape.isNull() or not shape.Solids:
                continue
            solids = shape.Solids[0] if len(shape.Solids) == 1 else Part.makeCompound(shape.Solids)
            target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
            key = obj.Name if top is None else f"{top.Name}.{prefix}{obj.Name}"
            out.append(Item(f"{label_prefix}{obj.Label}", key, target or obj, solids, hidden))

    walk([o for o in doc.Objects if not o.InList])
    return out


def common_container(items):
    """ "Container / " when every item's label starts with it, else ""."""
    if not items or " / " not in items[0].label:
        return ""
    first = items[0].label.split(" / ", 1)[0] + " / "
    return first if all(i.label.startswith(first) for i in items) else ""


def part_key(item):
    """The same for every placing of one part."""
    obj = item.obj
    return f"{obj.Document.Name}#{obj.Name}" if obj is not None else item.key


def find(items, ref):
    """The item named ``ref`` (label, object name or assembly path; case does not matter)."""
    want = str(ref).strip().lower()
    for item in items:
        if want in (item.label.lower(), item.key.lower()) or (item.obj is not None and want == item.obj.Name.lower()):
            return item
    short = [i for i in items if i.label.lower().rsplit(" / ", 1)[-1] == want]  # without its containers
    if len(short) == 1:
        return short[0]
    names = ", ".join(i.label for i in items[:MAX_ROWS]) or "none"
    raise AgentError(f"no part named {ref!r}; the parts are: {names}")


# ----- summary --------------------------------------------------------------------------------------


def _num(value, digits=2):
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _size(box):
    return "×".join(_num(v) for v in (box.XLength, box.YLength, box.ZLength))


def editable(obj, doc=None):
    """How the part's shape can be changed, in a few words."""
    if obj is None:
        return "missing link target"
    try:
        import bruceworx_codecad as cc

        if cc.is_code_part(obj):
            return f"code part, {len(cc.dimensions(obj))} dimensions"
    except Exception:  # noqa: BLE001
        pass
    if obj.isDerivedFrom("PartDesign::Body"):
        features = [f for f in obj.Group if f.isDerivedFrom("PartDesign::Feature")]
        return f"body, {len(features)} features"
    if obj.TypeId == "Part::Feature":
        return "plain solid, no parameters"
    try:
        import bruceworx_cots as bc

        info = bc.info_of(obj)
        if info:
            loose = len(info.get("unchecked", []))
            return f"bought part {info['key']}" + (f", {loose} numbers not checked" if loose else "")
    except Exception:  # noqa: BLE001
        pass
    if obj.isDerivedFrom("App::Part"):
        return "part container" if doc is None or obj.Document is doc else f"linked file {obj.Document.Label}"
    return obj.TypeId.split("::")[-1].lower() + " feature"


def _mass_text(obj):
    """ "12.3 g PETG" when the part has a mass, else ""."""
    if obj is None:
        return ""
    try:
        from heuretes_freecad import partprops

        text = partprops.computed_mass(obj)
        kg = float(str(text).split()[0]) if text else 0.0
        if kg <= 0:
            return ""
        material = partprops.computed_material(obj) or ""
        return f"{_num(kg * 1000.0)} g {material}".strip()
    except Exception:  # noqa: BLE001
        return ""


def _parameters(doc):
    """(sheet label, [(alias, value text)]) of the Parameters sheet the document uses."""
    try:
        import bruceworx_parameters as bp

        sheet = bp.find_sheet(doc)
        if sheet is None:
            return "", []
        rows = []
        for _cell, alias in bp.sheet_aliases(sheet):
            try:
                value = sheet.get(alias)
            except Exception:  # noqa: BLE001
                continue
            rows.append((alias, value.UserString if hasattr(value, "UserString") else _num(float(value), 4)
                         if isinstance(value, (int, float)) else str(value)))
        return sheet.Label, rows
    except Exception:  # noqa: BLE001
        return "", []


def _clip(names, limit=8):
    names = list(names)
    return ", ".join(names[:limit]) + (f", +{len(names) - limit} more" if len(names) > limit else "")


def summary(doc):
    """{"text": the design in a few lines, "parts": count, "hidden": count, "errors": count}."""
    import bruceworx_interference as bi

    every = parts(doc, include_hidden=True)
    shown = [i for i in every if not i.hidden]
    hidden = [i for i in every if i.hidden]
    lines = [f"{doc.Label}" + (f" ({doc.FileName})" if doc.FileName else " (not saved)")
             + f": {len(shown)} parts shown, {len(hidden)} hidden. Lengths mm, volumes mm3."]
    inside = common_container(every)  # all in one container: name it once
    if inside:
        lines.append(f"all inside {inside[:-3]}")
    if shown:
        lines.append("parts (label | how it is edited | size x×y×z | volume | mass):")
    groups = {}
    for item in shown:  # the same part placed several times is one row
        label = item.label[len(inside):]
        row = (label.rsplit(" [", 1)[0] if label.endswith("]") else label, editable(item.obj, doc),
               _size(item.shape.BoundBox), _num(item.shape.Volume, 1), _mass_text(item.obj))
        groups[row] = groups.get(row, 0) + 1
    for (label, how, size, volume, mass), count in list(groups.items())[:MAX_ROWS]:
        times = f" ×{count}" if count > 1 else ""
        lines.append(f"  {label}{times} | {how} | {size} | {volume}" + (f" | {mass}" if mass else ""))
    if len(groups) > MAX_ROWS:
        lines.append(f"  ... {len(groups) - MAX_ROWS} more")
    if hidden:
        lines.append(f"hidden: {_clip(i.label[len(inside):] for i in hidden)}")
    sheet, rows = _parameters(doc)
    if rows:
        shown_rows = ", ".join(f"{a}={v}" for a, v in rows[:60])
        lines.append(f"parameters ({sheet}): {shown_rows}" + (f", +{len(rows) - 60} more" if len(rows) > 60 else ""))
    for asm in bi.assemblies(doc):
        try:
            import bruceworx_api_asm as api

            lines.append(f"assembly {asm.Label}: {len(api.parts(asm))} parts, {len(api.joints(asm))} joints, "
                         f"grounded: {_clip(o.Label for o in api.grounded(asm)) or 'none'}")
        except Exception:  # noqa: BLE001
            lines.append(f"assembly {asm.Label}")
    bad = [o for o in doc.Objects if hasattr(o, "isValid") and not o.isValid()]
    if bad:
        lines.append(f"in error: {_clip(f'{o.Label} ({o.TypeId})' for o in bad)}")
    try:
        import bruceworx_codecad as cc

        locked = cc.untrusted(doc)
        if locked:
            lines.append(f"code parts waiting for approval: {_clip(o.Label for o in locked)}")
    except Exception:  # noqa: BLE001
        pass
    return {"text": "\n".join(lines), "parts": len(shown), "hidden": len(hidden), "errors": len(bad)}


# ----- views and sections, drawn in software --------------------------------------------------------


def basis(view):
    """(right, up, towards the viewer) unit vectors of a named view."""
    import numpy as np

    if view not in VIEWS:
        raise AgentError(f"view must be one of {', '.join(VIEWS)}")
    n, up = (np.array(v, dtype=float) for v in VIEWS[view])
    n /= np.linalg.norm(n)
    up = up - up.dot(n) * n
    up /= np.linalg.norm(up)
    return np.cross(up, n), up, n


def parse_section(section):
    """(axis index, at, keep sign or 0 for "the side away from the viewer"), or None."""
    if not section:
        return None
    axis = str(section.get("axis", "")).lower()
    if axis not in ("x", "y", "z"):
        raise AgentError('section needs "axis": "x", "y" or "z", and "at": the plane\'s position in mm')
    keep = str(section.get("keep", "")).strip()
    if keep not in ("", "+", "-"):
        raise AgentError('section "keep" is "+" or "-": the side of the plane to show')
    return "xyz".index(axis), float(section.get("at", 0.0)), {"": 0, "+": 1, "-": -1}[keep]


def cut(shape, axis, at, sign, reach):
    """The part of ``shape`` on the ``sign`` side of the plane; None when nothing is left."""
    import Part

    App = _app()
    low = [-reach, -reach, -reach]
    size = [2 * reach, 2 * reach, 2 * reach]
    if sign > 0:
        low[axis] = at
    else:
        low[axis] = at - 2 * reach
    box = Part.makeBox(size[0], size[1], size[2], App.Vector(*low))
    try:
        kept = shape.common(box)
    except Exception:  # noqa: BLE001
        return None
    return None if kept.isNull() or not kept.Faces else kept


def _mesh(shape, deflection):
    import numpy as np

    points, triangles = shape.tessellate(deflection)
    if not triangles:
        return None
    pts = np.array([(p.x, p.y, p.z) for p in points], dtype=float)
    return pts[np.array(triangles, dtype=np.int64)]  # (n, 3, 3)


def _edge_lines(shape, deflection):
    """Each edge as an (k, 3) array of points."""
    import numpy as np

    out = []
    for edge in shape.Edges:
        try:
            if type(edge.Curve).__name__ == "Line":
                pts = [v.Point for v in edge.Vertexes]
            else:
                pts = edge.discretize(Deflection=deflection)
        except Exception:  # noqa: BLE001 -- a degenerate edge
            continue
        if len(pts) >= 2:
            out.append(np.array([(p.x, p.y, p.z) for p in pts], dtype=float))
    return out


def _fill(tri, colours, caps, zbuf, img):
    """Z-buffered flat triangles: ``tri`` (n, 3, 3) as pixel x, pixel y, depth (bigger is nearer)."""
    import numpy as np

    height, width = zbuf.shape
    lo = np.floor(tri[:, :, :2].min(axis=1)).astype(np.int64)
    hi = np.ceil(tri[:, :, :2].max(axis=1)).astype(np.int64)
    flat = tri.reshape(len(tri), 9).tolist()
    lo, hi = lo.tolist(), hi.tolist()
    for i, (x0, y0, z0, x1, y1, z1, x2, y2, z2) in enumerate(flat):
        ax, ay = max(lo[i][0], 0), max(lo[i][1], 0)
        bx, by = min(hi[i][0], width - 1), min(hi[i][1], height - 1)
        if ax > bx or ay > by:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        xs = np.arange(ax, bx + 1) + 0.5
        ys = (np.arange(ay, by + 1) + 0.5)[:, None]
        w0 = ((x1 - xs) * (y2 - ys) - (x2 - xs) * (y1 - ys)) / area
        w1 = ((x2 - xs) * (y0 - ys) - (x0 - xs) * (y2 - ys)) / area
        w2 = 1.0 - w0 - w1
        depth = w0 * z0 + w1 * z1 + w2 * z2
        window = zbuf[ay:by + 1, ax:bx + 1]
        hit = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (depth > window)
        if not hit.any():
            continue
        window[hit] = depth[hit]
        pixels = img[ay:by + 1, ax:bx + 1]
        pixels[hit] = colours[i]
        if caps[i]:  # a cut face: hatched
            stripes = hit & ((((xs - 0.5) + (ys - 0.5)).astype(np.int64) // 6) % 2 == 0)
            pixels[stripes] = colours[i] * 0.55
    return img


def _stroke(lines, zbuf, img, bias, thick):
    import numpy as np

    height, width = zbuf.shape
    for pts in lines:
        seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
        total = float(seg.sum())
        if total < 0.5:
            continue
        along = np.concatenate(([0.0], np.cumsum(seg)))
        t = np.linspace(0.0, total, int(total / 0.7) + 2)
        x, y, z = (np.interp(t, along, pts[:, k]) for k in range(3))
        for dx in range(thick):
            for dy in range(thick):
                px, py = (x + dx).astype(np.int64), (y + dy).astype(np.int64)
                ok = (px >= 0) & (px < width) & (py >= 0) & (py < height)
                px, py, zz = px[ok], py[ok], z[ok]
                seen = zz >= zbuf[py, px] - bias
                img[py[seen], px[seen]] = EDGE


def _draw_view(meshes, view, width, height, scale_px, cap_plane, deflection):
    """One view as a float image (height, width, 3). ``meshes``: [(triangles, edge lines, rgb)]."""
    import numpy as np

    right, up, towards = basis(view)
    every = np.concatenate([m[0].reshape(-1, 3) for m in meshes])
    u, v = every @ right, every @ up
    span_u, span_v = max(u.max() - u.min(), 1e-6), max(v.max() - v.min(), 1e-6)
    margin = 0.06 * min(width, height)
    scale = min((width - 2 * margin) / span_u, (height - 2 * margin) / span_v)
    off_u = (width - scale * span_u) / 2 - scale * u.min()
    off_v = (height - scale * span_v) / 2 + scale * v.max()

    def project(points):
        out = np.empty(points.shape, dtype=float)
        out[..., 0] = (points @ right) * scale + off_u
        out[..., 1] = off_v - (points @ up) * scale
        out[..., 2] = points @ towards
        return out

    light = np.array((-0.35, 0.45, 0.82))
    light /= np.linalg.norm(light)
    zbuf = np.full((height, width), -np.inf)
    img = np.empty((height, width, 3), dtype=float)
    img[:] = BACKGROUND
    for triangles, _lines, rgb in meshes:
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        length = np.linalg.norm(normals, axis=1)
        length[length == 0] = 1.0
        normals /= length[:, None]
        seen = np.stack((normals @ right, normals @ up, normals @ towards), axis=1)
        shade = 0.42 + 0.58 * np.abs(seen @ light)
        colours = np.array(rgb, dtype=float)[None, :] * shade[:, None]
        if cap_plane is None:
            caps = np.zeros(len(triangles), dtype=bool)
        else:
            axis, at = cap_plane
            caps = (np.abs(triangles[:, :, axis] - at) < 1e-5 * max(1.0, abs(at)) + 1e-6).all(axis=1)
        _fill(project(triangles), colours, caps.tolist(), zbuf, img)
    for _triangles, lines, _rgb in meshes:
        _stroke([project(line) for line in lines], zbuf, img, 2.5 * deflection + 1e-4, scale_px)
    return img, (span_u, span_v)


def _png(rgb):
    """PNG bytes of a uint8 (h, w, 3) array, without PIL."""
    height, width, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _compose(cells, captions, legend, width, height, supersample):
    """The views side by side (or 2 × 2) with captions and a legend; PNG bytes, final size."""
    import numpy as np

    cols = len(cells) if len(cells) <= 2 else 2
    rows = (len(cells) + cols - 1) // cols
    cell_h, cell_w = cells[0].shape[:2]
    sheet = np.empty((rows * cell_h, cols * cell_w, 3), dtype=float)
    sheet[:] = BACKGROUND
    for i, cell in enumerate(cells):
        r, c = divmod(i, cols)
        sheet[r * cell_h:(r + 1) * cell_h, c * cell_w:(c + 1) * cell_w] = cell
    small = sheet.reshape(sheet.shape[0] // supersample, supersample, sheet.shape[1] // supersample, supersample, 3)
    small = np.clip(small.mean(axis=(1, 3)), 0, 255).astype(np.uint8)
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 -- no captions without PIL; the caller's text carries them
        return _png(small), small.shape[1], small.shape[0]
    legend_rows = (len(legend) + 3) // 4 if legend else 0
    image = Image.new("RGB", (small.shape[1], small.shape[0] + 14 * legend_rows + (4 if legend_rows else 0)), BACKGROUND)
    image.paste(Image.fromarray(small), (0, 0))
    draw = ImageDraw.Draw(image)
    w, h = cell_w // supersample, cell_h // supersample
    for i, text in enumerate(captions):
        r, c = divmod(i, cols)
        draw.text((c * w + 6, r * h + 4), text, fill=(60, 60, 60))
        if c:
            draw.line((c * w, r * h, c * w, (r + 1) * h), fill=(215, 215, 215))
        if r:
            draw.line((c * w, r * h, (c + 1) * w, r * h), fill=(215, 215, 215))
    column = image.width // 4
    for i, (label, rgb) in enumerate(legend):
        x, y = (i % 4) * column + 6, small.shape[0] + 2 + (i // 4) * 14
        draw.rectangle((x, y + 2, x + 9, y + 11), fill=tuple(rgb), outline=(60, 60, 60))
        draw.text((x + 14, y), label[: max(6, column // 7)], fill=(40, 40, 40))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue(), image.width, image.height


def render(doc, views=None, section=None, objects=None, width=800, height=600, include_hidden=False):
    """{"png": bytes, "width", "height", "views": [...], "parts": [{"label", "color"}], "note"}.

    ``views``: up to four names from VIEWS (default iso; with a section, the view that faces the cut).
    ``section``: {"axis": "x"|"y"|"z", "at": mm, "keep": "+"|"-"}; cut faces are hatched.
    ``objects``: only these parts (labels, names or assembly paths)."""
    import numpy as np

    plane = parse_section(section)
    names = [str(v).lower() for v in (views or [])][:MAX_VIEWS]
    if not names:
        names = [SECTION_VIEW["xyz"[plane[0]]]] if plane else ["iso"]
    for name in names:
        basis(name)
    items = parts(doc, include_hidden)
    if objects:
        items = [find(items, ref) for ref in objects]
    if not items:
        raise AgentError("nothing to draw: the document has no visible solid")
    colours = {}
    for item in items:  # one colour per part, however often it is placed
        colours.setdefault(part_key(item), PALETTE[len(colours) % len(PALETTE)])
    inside = len(common_container(items))
    lows = np.array([[i.shape.BoundBox.XMin, i.shape.BoundBox.YMin, i.shape.BoundBox.ZMin] for i in items]).min(axis=0)
    highs = np.array([[i.shape.BoundBox.XMax, i.shape.BoundBox.YMax, i.shape.BoundBox.ZMax] for i in items]).max(axis=0)
    diagonal = float(np.linalg.norm(highs - lows)) or 1.0
    note = ""
    if plane:
        axis, at, sign = plane
        if not sign:  # show the side away from the first view's camera
            sign = -1 if basis(names[0])[2][axis] > 0 else 1
        reach = float(np.abs(np.concatenate((lows, highs))).max()) + diagonal + abs(at) + 1.0
        shapes = [(i, cut(i.shape, axis, at, sign, reach)) for i in items]
        shapes = [(i, s) for i, s in shapes if s is not None]
        if not shapes:
            raise AgentError(f"the section plane {'xyz'[axis]} = {_num(at)} leaves nothing on that side; the model "
                             f"spans {_num(lows[axis])} to {_num(highs[axis])} there")
        note = f"section {'xyz'[axis]} = {_num(at)}, showing the {'+' if sign > 0 else '-'} side; cut faces hatched"
    else:
        shapes = [(i, i.shape) for i in items]
    deflection = max(diagonal * 0.002, 0.005)
    meshes, total = [], 0
    for item, shape in shapes:
        triangles = _mesh(shape, deflection)
        if triangles is None:
            continue
        total += len(triangles)
        key = part_key(item)
        meshes.append((triangles, _edge_lines(shape, deflection), colours[key]))
    if not meshes:
        raise AgentError("nothing to draw: the shapes have no faces")
    if total > MAX_TRIANGLES:  # keep the drawing time bounded: every n-th triangle would leave holes, so say so
        note = (note + "; " if note else "") + f"{total} triangles: a large model, the drawing took longer"
    supersample = 2
    count = len(names)
    cols = count if count <= 2 else 2
    rows = (count + cols - 1) // cols
    width = max(160, min(int(width), 2400))
    height = max(120, min(int(height), 2400))
    cell_w, cell_h = (width // cols) * supersample, (height // rows) * supersample
    cells, captions = [], []
    for name in names:
        cell, (span_u, span_v) = _draw_view(meshes, name, cell_w, cell_h, supersample,
                                            (plane[0], plane[1]) if plane else None, deflection)
        cells.append(cell)
        captions.append(f"{name}  {_num(span_u, 1)} x {_num(span_v, 1)} mm")
    legend, seen = [], set()
    for item, _shape in shapes:
        key = part_key(item)
        if key in seen:
            continue
        seen.add(key)
        label = item.label[inside:]
        label = label.rsplit(" [", 1)[0] if label.endswith("]") else label
        legend.append((label, colours[key]))
    shown = legend[:MAX_LEGEND - 1] + [(f"+{len(legend) - MAX_LEGEND + 1} more", (255, 255, 255))] \
        if len(legend) > MAX_LEGEND else legend
    png, out_w, out_h = _compose(cells, captions, shown, width, height, supersample)
    return {"png": png, "width": out_w, "height": out_h, "views": captions, "note": note,
            "parts": [{"label": label, "color": "#%02x%02x%02x" % tuple(rgb)} for label, rgb in legend[:MAX_ROWS]]}


# ----- how two parts fit ----------------------------------------------------------------------------


class Cylinder:
    """A cylindrical feature of a shape: a hole (material outside) or a shaft (material inside)."""

    __slots__ = ("hole", "diameter", "point", "axis", "low", "high", "angle")

    def __init__(self, hole, diameter, point, axis, low, high, angle):
        self.hole, self.diameter, self.point, self.axis, self.low, self.high = hole, diameter, point, axis, low, high
        self.angle = angle  # radians of the circle its faces cover, added up

    @property
    def length(self):
        return self.high - self.low


def cylinders(shape):
    """[Cylinder] of the shape: the full ones only (a fillet or a rounded corner is not a hole or a shaft);
    the faces of one cylinder (two halves, say) are merged."""
    App = _app()
    found = {}
    for face in shape.Faces:
        surface = face.Surface
        if type(surface).__name__ != "Cylinder":
            continue
        axis = App.Vector(surface.Axis)
        if (axis.x, axis.y, axis.z) < (0, 0, 0):  # one direction for both ways round
            axis = axis * -1
        centre = App.Vector(surface.Center)
        u0, u1, v0, v1 = face.ParameterRange
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
        point = face.valueAt(u, v)
        radial = point - centre
        radial = radial - axis * radial.dot(axis)
        hole = face.normalAt(u, v).dot(radial) < 0
        # where the axis is nearest the origin, so the same line always has the same point
        base = centre - axis * centre.dot(axis)
        ends = [(face.valueAt(u, w) - base).dot(axis) for w in (v0, v1)]
        key = (hole, round(surface.Radius, 4), round(base.x, 3), round(base.y, 3), round(base.z, 3),
               round(axis.x, 4), round(axis.y, 4), round(axis.z, 4))
        low, high = min(ends), max(ends)
        if key in found:
            known = found[key]
            if low <= known.high + 1e-6 and high >= known.low - 1e-6:  # the same stretch of the line
                known.low, known.high = min(known.low, low), max(known.high, high)
                known.angle += u1 - u0
                continue
            key = key + (round(low, 3),)
        found[key] = Cylinder(hole, 2 * surface.Radius, base, axis, low, high, u1 - u0)
    return [c for c in found.values() if c.angle > 2 * math.pi - 1e-3]


def _coaxial(a, b):
    """The distance between the axes of two parallel cylinders, or None when they are not parallel."""
    cos = abs(a.axis.dot(b.axis))
    if cos < math.cos(math.radians(AXIS_TOL_DEG)):
        return None
    gap = b.point - a.point
    return (gap - a.axis * gap.dot(a.axis)).Length


def _where(c):
    mid = c.point + c.axis * ((c.low + c.high) / 2)
    return f"({_num(mid.x)}, {_num(mid.y)}, {_num(mid.z)})"


def fit(doc, first, second, gap=1.0):
    """{"text": the report, "kind", "distance", "volume", "pairs": coaxial hole/shaft pairs}.

    ``gap``: how far apart (mm) the two may be and still be listed as a clearance."""
    import bruceworx_interference as bi

    items = parts(doc, include_hidden=True)
    a, b = find(items, first), find(items, second)
    if a is b:
        raise AgentError("fit needs two different parts")
    oa = bi.Occurrence(a.obj or doc, a.key + ".", bi.object_key(a.obj) if a.obj is not None else a.key, a.label, a.shape)
    ob = bi.Occurrence(b.obj or doc, b.key + ".", bi.object_key(b.obj) if b.obj is not None else b.key, b.label, b.shape)
    result = bi.check_pair(oa, ob, gap=max(float(gap), 0.0), contacts=True, fit_keys=bi.fits(doc))
    out = {"first": a.label, "second": b.label}
    if result is None:
        distance = a.shape.distToShape(b.shape)[0]
        out.update(kind="clear", distance=round(distance, 4), volume=0.0)
        head = f"clear: nearest {_num(distance, 3)} mm apart"
    else:
        out.update(kind=result.kind, distance=round(result.distance, 4), volume=round(result.volume, 4))
        head = f"{result.kind}: {result.text()}"
        if result.kind == bi.FIT:
            head += " (marked as an intended fit)"
    lines = [f"{a.label} / {b.label}: {head}"]
    ca, cb = cylinders(a.shape), cylinders(b.shape)
    pairs, used_a, used_b = [], set(), set()
    for i, x in enumerate(ca):
        for j, y in enumerate(cb):
            offset = _coaxial(x, y)
            if offset is None or offset > COAXIAL_TOL:
                continue
            if not (x.hole or y.hole):
                continue
            used_a.add(i)
            used_b.add(j)
            if x.hole and y.hole:
                kind, text = "holes in line", f"⌀{_num(x.diameter, 3)} and ⌀{_num(y.diameter, 3)}"
                clearance = None
            else:
                hole, shaft = (x, y) if x.hole else (y, x)
                overlap = min(hole.high, shaft.high) - max(hole.low, shaft.low)
                if overlap <= 1e-6:
                    kind, clearance = "shaft in line with hole, not inside it", None
                    text = f"hole ⌀{_num(hole.diameter, 3)}, shaft ⌀{_num(shaft.diameter, 3)}"
                else:
                    clearance = hole.diameter - shaft.diameter
                    kind = "shaft in hole"
                    state = "clearance" if clearance > 1e-6 else ("interference" if clearance < -1e-6 else "size for size")
                    text = (f"hole ⌀{_num(hole.diameter, 3)} in {a.label if x.hole else b.label}, shaft "
                            f"⌀{_num(shaft.diameter, 3)}: {_num(abs(clearance), 3)} mm {state} on diameter, "
                            f"{_num(overlap)} mm engaged of a {_num(hole.length)} mm hole")
            pairs.append({"kind": kind, "at": _where(x), "first_diameter": round(x.diameter, 4),
                          "second_diameter": round(y.diameter, 4), "first_is_hole": x.hole, "second_is_hole": y.hole,
                          "clearance": None if clearance is None else round(clearance, 4), "text": text})
    groups = {}
    for pair in pairs:  # the same fit at several places is one line
        groups.setdefault((pair["kind"], pair["text"]), []).append(pair["at"])
    if groups:
        lines.append("coaxial features:")
    for (kind, text), places in list(groups.items())[:MAX_ROWS]:
        lines.append(f"  {len(places)} × {kind}: {text}; at {_clip(places, 4)}")

    def lonely(cyls, used, label):
        holes = {}
        for k, c in enumerate(cyls):
            if c.hole and k not in used:
                holes[round(c.diameter, 3)] = holes.get(round(c.diameter, 3), 0) + 1
        if holes:
            listed = ", ".join(f"{n} × ⌀{_num(d, 3)}" for d, n in sorted(holes.items())[:10])
            lines.append(f"holes in {label} with nothing in line in the other part: {listed}")

    lonely(ca, used_a, a.label)
    lonely(cb, used_b, b.label)
    out["pairs"] = [{k: v for k, v in p.items() if k != "text"} for p in pairs[:MAX_ROWS]]
    out["text"] = "\n".join(lines)
    return out


# ----- what an edit changed -------------------------------------------------------------------------


def snapshot(doc):
    """{key: (label, volume, (x, y, z) size)} of every part, hidden ones too."""
    out = {}
    for item in parts(doc, include_hidden=True):
        box = item.shape.BoundBox
        out[item.key] = (item.label, item.shape.Volume, (box.XLength, box.YLength, box.ZLength))
    return out


def diff(before, after):
    """[{"part", "volume": [from, to], "size": [from, to]}] for the parts that changed, plus added/removed."""
    changed = []
    for key, (label, volume, size) in after.items():
        if key not in before:
            changed.append({"part": label, "added": True})
            continue
        _label, old_volume, old_size = before[key]
        entry = {"part": label}
        if abs(volume - old_volume) > 1e-6 * max(1.0, abs(old_volume)):
            entry["volume"] = [round(old_volume, 3), round(volume, 3)]
        if any(abs(p - q) > 1e-6 for p, q in zip(size, old_size)):
            entry["size"] = [[round(v, 4) for v in old_size], [round(v, 4) for v in size]]
        if len(entry) > 1:
            changed.append(entry)
    for key, (label, _volume, _size) in before.items():
        if key not in after:
            changed.append({"part": label, "removed": True})
    return changed
