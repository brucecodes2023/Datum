# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad bridge: ``kicad-cli`` wrappers and a ``.kicad_pcb`` reader (ECD-01..06).

FreeCAD-free, so the vault, the MCP tools and FreeCAD's Insert Board share one
engine. What it does:

* :func:`find_cli` -- ``kicad-cli`` from ``BW_KICAD_CLI``, the KiCad app, or PATH.
* :func:`prepare_board` -- writes a TEMPORARY copy of a board (never the
  user's file) with its 3D model paths resolved: ``${KICADn_3DMODEL_DIR}``,
  ``${KIPRJMOD}``, KiCad's ``kicad_common.json`` variables, relative paths,
  and ``.wrl`` swapped for the ``.step`` next to it (KiCad's own
  ``--subst-models`` needs the ``.wrl`` to exist; this KiCad ships STEP only).
* :func:`export_step`, :func:`cached_step` -- the board as STEP with copper,
  mask and silkscreen; cached by board content and options.
* :func:`render`, :func:`cached_render` -- KiCad's 3D render (the vault thumbnail).
* :func:`drc` -- a DRC summary.
* :func:`board_info` -- size from Edge.Cuts, copper layer count, footprints,
  mounting holes, stackup finish and colours, in KiCad and STEP coordinates.
* Board materials (ECD-06): :func:`classify_body`, :func:`classify_component`
  and the component mass table (:func:`load_mass_table`, :func:`lookup_mass`).

Coordinates: KiCad's y axis points down. The STEP export puts its origin at
:attr:`BoardInfo.origin` (KiCad coordinates), so a KiCad point (x, y) lands at
STEP (x - ox, oy - y); :meth:`BoardInfo.to_step` does this.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field

CLI_ENV = "BW_KICAD_CLI"
CACHE_ENV = "BW_KICAD_CACHE"
KICAD_APP = "/Applications/KiCad/KiCad.app"
DEFAULT_CLI = KICAD_APP + "/Contents/MacOS/kicad-cli"
SHARED_SUPPORT = KICAD_APP + "/Contents/SharedSupport"
MODELS_DIR = SHARED_SUPPORT + "/3dmodels"
TEMPLATE_DIR = SHARED_SUPPORT + "/template"
# Bumped when the export or the model resolution changes, so old cache entries are not used.
CACHE_VERSION = 1

STEP_DEFAULTS = {
    "tracks": True,
    "pads": True,
    "zones": True,
    "silkscreen": True,
    "soldermask": True,
    "inner_copper": False,
    "components": True,
    "dnp": False,  # do-not-populate parts are not on the real board
    "fuse": False,
    "origin": "auto",  # auto | drill | grid | page | center | [x, y] (KiCad mm)
}

# The light model (ECD-05, 31 Oct: large assemblies): the board body and the components, as KiCad's
# own default export; no tracks, pads, zones, silkscreen, solder mask or inner copper. The ESC board
# goes from 30 MB / 19,752 faces to about 2.6 MB / 1,430 faces.
LIGHT_OPTIONS = {"tracks": False, "pads": False, "zones": False, "silkscreen": False, "soldermask": False,
                 "inner_copper": False}


def light_options(options: dict | None = None) -> dict:
    """``options`` with the copper and print layers off (:data:`LIGHT_OPTIONS`)."""
    return dict(options or {}, **LIGHT_OPTIONS)


def is_light(options: dict | None) -> bool:
    """Whether STEP options leave out every copper and print layer (a light model)."""
    opts = step_options(options) if options is not None else dict(STEP_DEFAULTS)
    return not any(opts.get(key) for key in LIGHT_OPTIONS)


class KiCadError(RuntimeError):
    """kicad-cli is missing or failed, or a board cannot be read."""


# ----- kicad-cli ---------------------------------------------------------------


def find_cli(explicit: str | None = None) -> str | None:
    """Path of ``kicad-cli``: ``explicit``, ``$BW_KICAD_CLI``, the KiCad app,
    then PATH. None when there is none."""
    for candidate in (explicit, os.environ.get(CLI_ENV), DEFAULT_CLI):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("kicad-cli")


def _cli(cli: str | None) -> str:
    path = find_cli(cli)
    if not path:
        raise KiCadError(f"kicad-cli not found: install KiCad or set {CLI_ENV}")
    return path


def _run(args: list[str], timeout: float, cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)
    except subprocess.TimeoutExpired as exc:
        raise KiCadError(f"kicad-cli timed out after {timeout:.0f} s: {' '.join(args[1:4])}") from exc
    return result


def _failure(result: subprocess.CompletedProcess, what: str) -> KiCadError:
    lines = [ln for ln in (result.stderr + "\n" + result.stdout).splitlines()
             if ln.strip() and "NSCocoaErrorDomain" not in ln]
    return KiCadError(f"{what} failed (exit {result.returncode}): " + " | ".join(lines[-6:]))


def cli_version(cli: str | None = None) -> str:
    result = _run([_cli(cli), "version"], timeout=30)
    if result.returncode != 0:
        raise _failure(result, "kicad-cli version")
    return result.stdout.strip()


# ----- s-expressions -----------------------------------------------------------

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
_ESCAPE = re.compile(r"\\(.)")


def parse_sexpr(text: str) -> list:
    """The first s-expression in ``text`` as nested lists of strings. Quoted
    strings come back unquoted; numbers stay strings (see :func:`num`)."""
    stack: list[list] = []
    root = None
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token == "(":
            node: list = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise KiCadError("unbalanced ')' in s-expression")
            node = stack.pop()
            if not stack:
                root = node
                break
        elif stack:
            if token[0] == '"':
                token = _ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), token[1:-1])
            stack[-1].append(token)
    if root is None:
        raise KiCadError("no complete s-expression found")
    return root


def head(node) -> str | None:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else None


def find(node: list, name: str):
    """First child list whose head is ``name``, or None."""
    for child in node[1:]:
        if head(child) == name:
            return child
    return None


def find_all(node: list, name: str) -> list:
    return [child for child in node[1:] if head(child) == name]


def value(node: list, name: str, default=None):
    """First atom of the child ``name``: ``(name atom ...)``."""
    child = find(node, name)
    if child is None or len(child) < 2 or isinstance(child[1], list):
        return default
    return child[1]


def num(text, default: float = 0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _xy(node: list | None) -> tuple[float, float] | None:
    if node is None or len(node) < 3:
        return None
    return num(node[1]), num(node[2])


# ----- board reading -----------------------------------------------------------


@dataclass
class Footprint:
    ref: str
    value: str
    library: str
    name: str
    x: float
    y: float
    rotation: float
    side: str  # "top" or "bottom"
    attributes: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    models: list = field(default_factory=list)

    @property
    def lib_id(self) -> str:
        return f"{self.library}:{self.name}" if self.library else self.name

    @property
    def dnp(self) -> bool:
        return "dnp" in self.attributes or self.properties.get("dnp", "").lower() in ("yes", "true", "1")


@dataclass
class Hole:
    ref: str
    x: float  # KiCad coordinates, mm
    y: float
    drill: float
    source: str  # "footprint" (a MountingHole footprint) or "npth" (a large unplated hole)


@dataclass
class BoardInfo:
    path: str
    title: str
    thickness: float
    copper_layers: int
    bbox: tuple  # Edge.Cuts (xmin, ymin, xmax, ymax), KiCad coordinates
    origin: tuple  # STEP origin in KiCad coordinates (see STEP_DEFAULTS["origin"])
    aux_origin: tuple | None
    grid_origin: tuple | None
    copper_finish: str
    mask_color: str
    silk_color: str
    footprints: list = field(default_factory=list)
    holes: list = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def to_step(self, x: float, y: float) -> tuple[float, float]:
        """KiCad point -> the STEP export's x, y (mm)."""
        return x - self.origin[0], self.origin[1] - y

    def with_origin(self, origin) -> "BoardInfo":
        self.origin = resolve_origin(self, origin)
        return self

    def as_dict(self) -> dict:
        out = asdict(self)
        out["width"], out["height"] = self.width, self.height
        out["holes_step"] = [dict(ref=h.ref, x=self.to_step(h.x, h.y)[0], y=self.to_step(h.x, h.y)[1],
                                  drill=h.drill) for h in self.holes]
        return out


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """KiCad's RotatePoint: a footprint-local point turned by the footprint's
    angle (counter-clockwise on screen, y down)."""
    if not degrees:
        return x, y
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def _arc_points(start, mid, end) -> list[tuple[float, float]]:
    """Points bounding a three-point arc: its ends plus the axis extremes it passes."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, mid, end]
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)

    def ang(p):
        return math.atan2(p[1] - uy, p[0] - ux) % (2 * math.pi)

    a1, a2, a3 = ang(start), ang(mid), ang(end)
    ccw = (a2 - a1) % (2 * math.pi) < (a3 - a1) % (2 * math.pi)
    lo, span = (a1, (a3 - a1) % (2 * math.pi)) if ccw else (a3, (a1 - a3) % (2 * math.pi))
    pts = [start, mid, end]
    for k in range(4):
        theta = k * math.pi / 2
        if (theta - lo) % (2 * math.pi) <= span:
            pts.append((ux + r * math.cos(theta), uy + r * math.sin(theta)))
    return pts


def _graphic_points(node: list) -> list[tuple[float, float]]:
    """Outline points of one gr_/fp_ line, rect, arc, circle, poly or curve."""
    kind = head(node).split("_", 1)[-1]
    if kind in ("line", "rect"):
        a, b = _xy(find(node, "start")), _xy(find(node, "end"))
        return [p for p in (a, b) if p]
    if kind == "arc":
        a, m, b = _xy(find(node, "start")), _xy(find(node, "mid")), _xy(find(node, "end"))
        if a and m and b:
            return _arc_points(a, m, b)
        return [p for p in (a, b) if p]
    if kind == "circle":
        c, e = _xy(find(node, "center")), _xy(find(node, "end"))
        if not (c and e):
            return []
        r = math.hypot(e[0] - c[0], e[1] - c[1])
        return [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
    if kind in ("poly", "curve"):
        pts = find(node, "pts")
        out = []
        for item in (pts or [])[1:]:
            if head(item) == "xy":
                out.append(_xy(item))
            elif head(item) == "arc":
                a, m, b = _xy(find(item, "start")), _xy(find(item, "mid")), _xy(find(item, "end"))
                if a and m and b:
                    out.extend(_arc_points(a, m, b))
        return [p for p in out if p]
    return []


def _layer_of(node: list) -> str | None:
    return value(node, "layer")


def _footprint(node: list) -> tuple[Footprint, list]:
    lib_id = node[1] if len(node) > 1 and isinstance(node[1], str) else ""
    library, _, name = lib_id.rpartition(":")
    at = find(node, "at") or ["at", "0", "0"]
    x, y = num(at[1]), num(at[2])
    rot = num(at[3]) if len(at) > 3 and isinstance(at[3], str) else 0.0
    props = {}
    for prop in find_all(node, "property"):
        if len(prop) > 2 and isinstance(prop[1], str) and isinstance(prop[2], str):
            props[prop[1]] = prop[2]
    for text in find_all(node, "fp_text"):  # KiCad 6 and older
        if len(text) > 2 and text[1] in ("reference", "value"):
            props.setdefault(text[1].capitalize(), text[2])
    attr = find(node, "attr")
    attributes = [a for a in (attr or [])[1:] if isinstance(a, str)]
    models = [m[1] for m in find_all(node, "model") if len(m) > 1 and isinstance(m[1], str)]
    side = "bottom" if (_layer_of(node) or "F.Cu").startswith("B.") else "top"
    fp = Footprint(props.get("Reference", ""), props.get("Value", ""), library, name, x, y, rot, side,
                   attributes, props, models)
    return fp, node


def _is_mounting_hole(fp: Footprint) -> bool:
    return "mountinghole" in fp.library.lower() or fp.name.lower().startswith("mountinghole")


def board_info(source: str, origin="auto", min_npth_drill: float = 2.0) -> BoardInfo:
    """Read a ``.kicad_pcb`` (path or text). Mounting holes are the footprints
    from a MountingHole library (one hole each, at its largest drill) plus any
    other unplated hole of at least ``min_npth_drill`` mm (smaller ones are
    usually connector pegs)."""
    if "\n" not in source and os.path.isfile(source):
        path = os.path.abspath(source)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    else:
        path, text = "", source
    root = parse_sexpr(text)
    if head(root) != "kicad_pcb":
        raise KiCadError(f"not a KiCad board: {path or 'text'}")

    general = find(root, "general") or ["general"]
    thickness = num(value(general, "thickness"), 1.6)
    layers = find(root, "layers") or ["layers"]
    copper = [entry for entry in layers[1:]
              if isinstance(entry, list) and len(entry) > 1 and str(entry[1]).endswith(".Cu")]
    title = value(find(root, "title_block") or ["title_block"], "title", "") or ""

    setup = find(root, "setup") or ["setup"]
    aux = _xy(find(setup, "aux_axis_origin"))
    grid = _xy(find(setup, "grid_origin"))
    stackup = find(setup, "stackup") or ["stackup"]
    finish = value(stackup, "copper_finish", "None") or "None"
    mask_color, silk_color = "Green", "White"
    for layer in find_all(stackup, "layer"):
        name = layer[1] if len(layer) > 1 else ""
        color = value(layer, "color")
        if color and name == "F.Mask":
            mask_color = color
        elif color and name == "F.SilkS":
            silk_color = color

    edge_points: list[tuple[float, float]] = []
    footprints: list[Footprint] = []
    holes: list[Hole] = []
    for child in root[1:]:
        tag = head(child)
        if tag and tag.startswith("gr_") and _layer_of(child) == "Edge.Cuts":
            edge_points.extend(_graphic_points(child))
        elif tag in ("footprint", "module"):
            fp, node = _footprint(child)
            footprints.append(fp)
            for graphic in node[1:]:
                gtag = head(graphic)
                if gtag and gtag.startswith("fp_") and _layer_of(graphic) == "Edge.Cuts":
                    for px, py in _graphic_points(graphic):
                        dx, dy = rotate(px, py, fp.rotation)
                        edge_points.append((fp.x + dx, fp.y + dy))
            holes.extend(_holes_of(fp, node, min_npth_drill))
    if edge_points:
        xs, ys = [p[0] for p in edge_points], [p[1] for p in edge_points]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
    info = BoardInfo(path, title, thickness, len(copper), tuple(round(v, 6) for v in bbox), (0.0, 0.0),
                     aux, grid, finish, mask_color, silk_color, footprints, _dedupe(holes))
    info.origin = resolve_origin(info, origin)
    return info


def _holes_of(fp: Footprint, node: list, min_npth_drill: float) -> list[Hole]:
    pads = []
    for pad in find_all(node, "pad"):
        kind = pad[2] if len(pad) > 2 and isinstance(pad[2], str) else ""
        drill = find(pad, "drill")
        size = num(drill[1]) if drill and len(drill) > 1 and isinstance(drill[1], str) else 0.0
        if drill and len(drill) > 1 and drill[1] == "oval":
            size = min(num(drill[2]), num(drill[3] if len(drill) > 3 else drill[2]))
        at = find(pad, "at") or ["at", "0", "0"]
        dx, dy = rotate(num(at[1]), num(at[2]), fp.rotation)
        pads.append((kind, size, fp.x + dx, fp.y + dy))
    if _is_mounting_hole(fp):
        drilled = [p for p in pads if p[1] > 0]
        if drilled:
            _kind, size, x, y = max(drilled, key=lambda p: p[1])
            return [Hole(fp.ref, x, y, size, "footprint")]
        return [Hole(fp.ref, fp.x, fp.y, 0.0, "footprint")]
    return [Hole(fp.ref, x, y, size, "npth") for kind, size, x, y in pads
            if kind == "np_thru_hole" and size >= min_npth_drill]


def _dedupe(holes: list[Hole]) -> list[Hole]:
    out: list[Hole] = []
    for hole in holes:
        if not any(abs(h.x - hole.x) < 1e-3 and abs(h.y - hole.y) < 1e-3 for h in out):
            out.append(hole)
    return out


def resolve_origin(info: BoardInfo, origin="auto") -> tuple[float, float]:
    """The STEP origin in KiCad coordinates. ``auto``: the drill/place origin
    when the board sets one, else the board outline's bottom-left corner."""
    xmin, ymin, xmax, ymax = info.bbox
    corner = (xmin, ymax)
    if isinstance(origin, (list, tuple)):
        return float(origin[0]), float(origin[1])
    if origin == "auto":
        return info.aux_origin if info.aux_origin and any(info.aux_origin) else corner
    if origin == "drill":
        return info.aux_origin or (0.0, 0.0)
    if origin == "grid":
        return info.grid_origin or (0.0, 0.0)
    if origin == "page":
        return (0.0, 0.0)
    if origin == "center":
        return ((xmin + xmax) / 2, (ymin + ymax) / 2)
    if origin == "corner":
        return corner
    raise ValueError(f"unknown origin {origin!r}")


# ----- 3D model paths ----------------------------------------------------------

_VAR = re.compile(r"\$\{([^}]+)\}|\$\(([^)]+)\)")
_MODEL = re.compile(r'(\(model\s+)("(?:[^"\\]|\\.)*"|[^\s()"]+)')
_MODEL_VAR = re.compile(r"^KICAD\d*_3DMODEL_DIR$|^KISYS3DMOD$")
_VRML = (".wrl", ".vrml")
_STEP_EXT = (".step", ".stp", ".STEP", ".STP", ".stpz")


def kicad_config_dir() -> str | None:
    """KiCad's settings folder of the newest version on this machine."""
    roots = [os.path.expanduser("~/Library/Preferences/kicad"), os.path.expanduser("~/.config/kicad")]
    for root in roots:
        versions = [d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)]
        versions = [d for d in versions if re.match(r"^\d+(\.\d+)*$", os.path.basename(d))]
        if versions:
            return max(versions, key=lambda d: tuple(int(p) for p in os.path.basename(d).split(".")))
    return None


def kicad_env(project_dir: str | None = None, models_dir: str | None = None) -> dict:
    """Variables for model paths, weakest first: every ``KICADn_3DMODEL_DIR``
    (and ``KISYS3DMOD``) to KiCad's bundled library, then ``kicad_common.json``,
    then the process environment; ``KIPRJMOD`` is the board's folder."""
    env: dict[str, str] = {}
    models = models_dir or os.environ.get("BW_KICAD_3DMODELS") or MODELS_DIR
    if os.path.isdir(models):
        env["KISYS3DMOD"] = models
        for n in range(4, 13):
            env[f"KICAD{n}_3DMODEL_DIR"] = models
    config = kicad_config_dir()
    if config:
        try:
            with open(os.path.join(config, "kicad_common.json"), encoding="utf-8") as handle:
                common = json.load(handle)
            for key, val in ((common.get("environment") or {}).get("vars") or {}).items():
                if isinstance(val, str) and val:
                    env[key] = val
        except (OSError, ValueError):
            pass
    for key, val in os.environ.items():
        if key.startswith(("KICAD", "KISYS")) and val:
            env[key] = val
    if project_dir:
        env["KIPRJMOD"] = project_dir
    return env


def _expand(path: str, env: dict) -> str:
    def sub(match):
        name = match.group(1) or match.group(2)
        if name in env:
            return env[name]
        if _MODEL_VAR.match(name) and "KISYS3DMOD" in env:
            return env["KISYS3DMOD"]
        return match.group(0)

    return _VAR.sub(sub, path)


def resolve_model_path(path: str, env: dict, project_dir: str | None) -> tuple[str, bool]:
    """``(resolved path, exists)``. VRML is swapped for a STEP file of the same
    name when one exists (the STEP export cannot use VRML)."""
    if path.startswith("kicad-embed://"):
        return path, True
    resolved = os.path.expanduser(_expand(path, env))
    if "${" in resolved or "$(" in resolved:
        return path, False
    if not os.path.isabs(resolved) and project_dir:
        resolved = os.path.normpath(os.path.join(project_dir, resolved))
    stem, ext = os.path.splitext(resolved)
    if ext.lower() in _VRML:
        for step_ext in _STEP_EXT:
            if os.path.isfile(stem + step_ext):
                return stem + step_ext, True
    return resolved, os.path.isfile(resolved)


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def resolve_models(text: str, project_dir: str | None, env: dict | None = None) -> tuple[str, list[dict]]:
    """Board text with every ``(model ...)`` path resolved, and a report:
    one ``{"model", "resolved", "found"}`` per distinct model path."""
    env = kicad_env(project_dir) if env is None else env
    seen: dict[str, dict] = {}

    def sub(match):
        raw = match.group(2)
        original = _ESCAPE.sub(lambda m: m.group(1), raw[1:-1]) if raw.startswith('"') else raw
        if original not in seen:
            resolved, found = resolve_model_path(original, env, project_dir)
            seen[original] = {"model": original, "resolved": resolved, "found": found}
        entry = seen[original]
        if entry["resolved"] == original:
            return match.group(0)
        return match.group(1) + _quote(entry["resolved"])

    return _MODEL.sub(sub, text), list(seen.values())


def prepare_board(board: str, workdir: str, env: dict | None = None) -> tuple[str, list[dict]]:
    """Copy ``board`` into ``workdir`` (same file name, its ``.kicad_pro`` too)
    with model paths resolved. Returns ``(copy, model report)``. The user's
    file is only read."""
    board = os.path.abspath(board)
    if not os.path.isfile(board):
        raise FileNotFoundError(board)
    project_dir = os.path.dirname(board)
    with open(board, encoding="utf-8") as handle:
        text = handle.read()
    text, report = resolve_models(text, project_dir, env)
    os.makedirs(workdir, exist_ok=True)
    copy = os.path.join(workdir, os.path.basename(board))
    if os.path.abspath(copy) == board:
        raise KiCadError("the work folder must not be the board's own folder")
    with open(copy, "w", encoding="utf-8") as handle:
        handle.write(text)
    project = os.path.splitext(board)[0] + ".kicad_pro"
    if os.path.isfile(project):
        shutil.copyfile(project, os.path.splitext(copy)[0] + ".kicad_pro")
    return copy, report


class _Workdir:
    """A temporary folder, removed afterwards (kept when BW_KICAD_KEEP is set)."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="bw-kicad-")
        return self.path

    def __exit__(self, *exc):
        if not os.environ.get("BW_KICAD_KEEP"):
            shutil.rmtree(self.path, ignore_errors=True)


# ----- export, render, DRC -----------------------------------------------------


def step_options(options: dict | None = None) -> dict:
    out = dict(STEP_DEFAULTS)
    for key, val in (options or {}).items():
        if key not in STEP_DEFAULTS:
            raise ValueError(f"unknown STEP option {key!r}; known: {', '.join(STEP_DEFAULTS)}")
        out[key] = list(val) if isinstance(val, tuple) else val
    return out


def step_args(options: dict, origin: tuple[float, float]) -> list[str]:
    """kicad-cli ``pcb export step`` flags for ``options`` (see STEP_DEFAULTS)."""
    flags = {
        "tracks": "--include-tracks",
        "pads": "--include-pads",
        "zones": "--include-zones",
        "silkscreen": "--include-silkscreen",
        "soldermask": "--include-soldermask",
        "inner_copper": "--include-inner-copper",
        "fuse": "--fuse-shapes",
    }
    args = [flag for key, flag in flags.items() if options.get(key)]
    if not options.get("components", True):
        args.append("--no-components")
    if not options.get("dnp", False):
        args.append("--no-dnp")
    args += ["--user-origin", f"{origin[0]:.6f}x{origin[1]:.6f}mm"]
    return args


@dataclass
class ExportResult:
    step: str
    seconds: float
    origin: tuple
    models: list
    cached: bool = False

    @property
    def missing_models(self) -> list[str]:
        return [m["model"] for m in self.models if not m["found"]]


def export_step(board: str, out: str, options: dict | None = None, cli: str | None = None,
                timeout: float = 600) -> ExportResult:
    """Export ``board`` to STEP at ``out`` (from a resolved temporary copy)."""
    opts = step_options(options)
    info = board_info(board, origin=opts["origin"])
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    start = time.monotonic()
    with _Workdir() as work:
        copy, report = prepare_board(board, work)
        tmp_out = os.path.join(work, "export.step")
        args = [_cli(cli), "pcb", "export", "step", *step_args(opts, info.origin), "-f", "-o", tmp_out, copy]
        result = _run(args, timeout, cwd=work)
        if result.returncode != 0 or not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
            raise _failure(result, "STEP export")
        shutil.move(tmp_out, out)
    return ExportResult(out, time.monotonic() - start, info.origin, report)


def cache_dir() -> str:
    return os.environ.get(CACHE_ENV) or os.path.expanduser("~/Library/Caches/bruceworx-kicad")


def board_hash(board: str) -> str:
    """sha256 of the board file's bytes."""
    digest = hashlib.sha256()
    with open(board, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(board: str, options: dict | None = None) -> str:
    opts = step_options(options)
    text = json.dumps({"board": board_hash(board), "options": opts, "v": CACHE_VERSION}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def cached_step(board: str, options: dict | None = None, cache: str | None = None,
                cli: str | None = None) -> ExportResult:
    """The board's STEP from ``<cache>/<key>.step``, exported on a miss. The
    key covers the board's bytes and the options, so an edited board exports
    again and an unchanged one is instant."""
    cache = cache or cache_dir()
    path = os.path.join(cache, cache_key(board, options) + ".step")
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        info = board_info(board, origin=step_options(options)["origin"])
        return ExportResult(path, 0.0, info.origin, [], cached=True)
    result = export_step(board, path + ".part", options, cli)
    os.replace(path + ".part", path)
    result.step = path
    return result


def render(board: str, out: str, side: str = "top", rotate: str | None = None, quality: str = "basic",
           width: int = 1600, height: int = 900, background: str | None = None, zoom: float | None = None,
           perspective: bool = False, floor: bool = False, cli: str | None = None,
           timeout: float = 300) -> str:
    """KiCad's own 3D render of ``board`` to PNG/JPEG ``out`` (models resolved).
    ``rotate`` is "x,y,z" in degrees, e.g. "-45,0,45" for an isometric view."""
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with _Workdir() as work:
        copy, _report = prepare_board(board, work)
        tmp_out = os.path.join(work, "render" + os.path.splitext(out)[1])
        args = [_cli(cli), "pcb", "render", "--side", side, "--quality", quality,
                "--width", str(int(width)), "--height", str(int(height))]
        if rotate:
            args += ["--rotate", rotate]
        if background:
            args += ["--background", background]
        if zoom:
            args += ["--zoom", str(zoom)]
        if perspective:
            args.append("--perspective")
        if floor:
            args.append("--floor")
        args += ["-o", tmp_out, copy]
        result = _run(args, timeout, cwd=work)
        if result.returncode != 0 or not os.path.isfile(tmp_out):
            raise _failure(result, "render")
        shutil.move(tmp_out, out)
    return out


THUMBNAIL_VIEW = {"side": "top", "rotate": "-45,0,45", "width": 512, "height": 512, "zoom": 0.9,
                  "background": "transparent"}


def cached_render(board: str, cache: str | None = None, cli: str | None = None, **view) -> str:
    """KiCad's render of ``board`` (default: the iso vault thumbnail, THUMBNAIL_VIEW) from
    ``<cache>/<key>.png``, rendered on a miss. Keyed like :func:`cached_step`: the board's bytes
    and the view, so the same board gives the same picture (and the same vault object) again."""
    opts = dict(THUMBNAIL_VIEW, **view)
    text = json.dumps({"board": board_hash(board), "render": opts, "v": CACHE_VERSION}, sort_keys=True)
    cache = cache or cache_dir()
    path = os.path.join(cache, hashlib.sha256(text.encode()).hexdigest()[:32] + ".png")
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    render(board, path + ".part.png", cli=cli, **opts)
    os.replace(path + ".part.png", path)
    return path


def drc(board: str, cli: str | None = None, refill_zones: bool = False, parity: bool = False,
        timeout: float = 600) -> dict:
    """Run KiCad's DRC on a temporary copy. Returns ``{"errors", "warnings",
    "unconnected", "parity", "by_type": {type: count}, "violations": [...]}``."""
    with _Workdir() as work:
        copy, _report = prepare_board(board, work)
        report = os.path.join(work, "drc.json")
        args = [_cli(cli), "pcb", "drc", "--format", "json", "--severity-all", "-o", report]
        if refill_zones:
            args.append("--refill-zones")
        if parity:
            args.append("--schematic-parity")
        result = _run(args + [copy], timeout, cwd=work)
        if not os.path.isfile(report):
            raise _failure(result, "DRC")
        with open(report, encoding="utf-8") as handle:
            data = json.load(handle)
    return summarize_drc(data)


def summarize_drc(data: dict) -> dict:
    violations = list(data.get("violations") or [])
    by_type: dict[str, int] = {}
    for item in violations:
        by_type[item.get("type", "?")] = by_type.get(item.get("type", "?"), 0) + 1
    return {
        "errors": sum(1 for v in violations if v.get("severity") == "error"),
        "warnings": sum(1 for v in violations if v.get("severity") == "warning"),
        "unconnected": len(data.get("unconnected_items") or []),
        "parity": len(data.get("schematic_parity") or []),
        "by_type": by_type,
        "violations": [{"type": v.get("type"), "severity": v.get("severity"),
                        "description": v.get("description")} for v in violations],
        "kicad_version": data.get("kicad_version", ""),
    }


# ----- materials (ECD-06) ------------------------------------------------------

# Material keys; the FreeCAD side maps them to material cards.
FR4, COPPER, PBT, EPOXY, CERAMIC, PLASTIC, METAL = (
    "fr4", "copper", "pbt", "epoxy", "ceramic", "plastic", "metal")
MASK, SILK = "soldermask", "silkscreen"  # appearance only: no volume

_BODY_SUFFIX = re.compile(r"_(PCB|pad|pads|track|tracks|via|vias|zone|zones|copper|soldermask|silkscreen)(\d{3})?$")
_BODY_KIND = {"PCB": FR4, "soldermask": MASK, "silkscreen": SILK}

# Footprint library (or model name) -> material, first match wins.
COMPONENT_RULES = [
    (re.compile(r"^(Connector|PinHeader|PinSocket|Pin_Header|Socket|Terminal|USB|RJ|Jumper)", re.I), PBT),
    (re.compile(r"^(Package_|SOIC|SOT|QFN|QFP|LQFP|TQFP|TSSOP|SSOP|MSOP|DFN|BGA|DIP|TO-|SOD|LGA|PLCC|"
                r"Diode_SMD|LED_SMD|Crystal|Oscillator|Inductor_SMD)", re.I), EPOXY),
    (re.compile(r"^(Capacitor_SMD|Resistor_SMD|C_\d{4}|R_\d{4}|Fuse_SMD)", re.I), CERAMIC),
]

DENSITY = {  # g/cm^3, for the FreeCAD-free mass estimate (the cards carry the same values)
    FR4: 1.85, COPPER: 8.96, PBT: 1.53, EPOXY: 1.95, CERAMIC: 3.9, PLASTIC: 1.2, METAL: 8.8,
}


def classify_body(label: str) -> str | None:
    """Material key of a board body from KiCad's STEP name ``<board>_PCB``,
    ``_pad``, ``_copper`` (tracks and zones), ``_via``, ``_soldermask``,
    ``_silkscreen``; None for anything else (a component)."""
    match = _BODY_SUFFIX.search(label)
    if not match:
        return None
    return _BODY_KIND.get(match.group(1), COPPER)


def classify_component(library: str = "", name: str = "") -> str:
    """Material key of a component from its footprint library, falling back
    to its footprint or model name; generic plastic when nothing matches."""
    for text in (library, name):
        if not text:
            continue
        for pattern, material in COMPONENT_RULES:
            if pattern.search(text):
                return material
    return PLASTIC


def is_metallic(rgb) -> bool:
    """A face colour that reads as metal: gold/brass, or a light, unsaturated
    grey (tin, nickel, silver). Used to give connector contacts metal."""
    r, g, b = (float(c) for c in rgb[:3])
    hi, lo = max(r, g, b), min(r, g, b)
    saturation = 0.0 if hi == 0 else (hi - lo) / hi
    gold = r > 0.6 and g > 0.45 and b < 0.6 * r and r >= g
    return gold or (hi > 0.55 and saturation < 0.15)


# ----- component mass table ----------------------------------------------------

MASS_TABLE_NAME = "component_masses.csv"


def mass_table_paths(vault: str | None = None) -> list[str]:
    """Mass tables, weakest first: the vault's, then the user's
    (``~/.config/bruceworx/component_masses.csv``)."""
    paths = []
    if vault:
        paths.append(os.path.join(vault, MASS_TABLE_NAME))
    paths.append(os.path.join(os.path.expanduser("~"), ".config", "bruceworx", MASS_TABLE_NAME))
    return paths


def load_mass_table(paths) -> dict[str, float]:
    """Merge CSV files of ``key,grams`` (header row optional; ``#`` comments).
    A key is a part number, a footprint ``Library:Name``, a footprint name or a
    value; matching ignores case. Later files win."""
    table: dict[str, float] = {}
    for path in paths if isinstance(paths, (list, tuple)) else [paths]:
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                if not row or row[0].strip().startswith("#") or len(row) < 2:
                    continue
                key, grams = row[0].strip(), row[1].strip()
                try:
                    table[key.lower()] = float(grams)
                except ValueError:
                    continue  # the header row
    return table


PART_NUMBER_FIELDS = ("MPN", "PartNumber", "Part Number", "P/N", "Manufacturer_Part_Number", "LCSC")


def lookup_mass(table: dict[str, float], footprint: Footprint | None = None, name: str = "") -> float | None:
    """Grams for a component: by part number field, footprint ``Lib:Name``,
    footprint name, then value (or ``name`` when no footprint matched)."""
    if not table:
        return None
    keys = []
    if footprint is not None:
        keys += [footprint.properties.get(f, "") for f in PART_NUMBER_FIELDS]
        keys += [footprint.lib_id, footprint.name, footprint.value]
    keys.append(name)
    for key in keys:
        if key and key.lower() in table:
            return table[key.lower()]
    return None


def match_footprint(info: BoardInfo, x: float, y: float, side: str | None = None,
                    tolerance: float = 0.05) -> Footprint | None:
    """The footprint at STEP point (x, y): a component model's origin is its
    footprint's position unless the model has an offset."""
    best, dist = None, tolerance
    for fp in info.footprints:
        if side and fp.side != side:
            continue
        sx, sy = info.to_step(fp.x, fp.y)
        d = math.hypot(sx - x, sy - y)
        if d <= dist:
            best, dist = fp, d
    return best
