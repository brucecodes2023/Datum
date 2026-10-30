# SPDX-License-Identifier: LGPL-2.1-or-later
"""Connector starter library (BW-ROU-05, 30 Oct 2026): connector parts with pin frames.

Each connector becomes one FreeCAD part file: an ``App::Part`` (the vault item) holding the
body and one pin frame per contact, the frames made by ``bruceworx_routing_ports`` (rou05's
schema: an ``App::LocalCoordinateSystem`` whose origin is where the wire enters, +Z out along
the wire). Where the geometry comes from (your C1 answer, 30 Oct):

* **KiCad**: the body is the KiCad library's STEP model and the pins are the footprint's
  pads (pad number = pin name). KiCad's models are in footprint coordinates with y flipped,
  so a pad at (x, y) on the footprint is at (x, -y, 0) in the part.
* **Parametric**: where the installed KiCad library has no model (every wire-side housing,
  Micro-Fit, XT30/XT90/MR30, bullets, Deutsch DT) the body is a simple outline from the
  catalogue dimensions below. Pins still come from a KiCad footprint's pads where one exists
  (a wire housing takes its vertical header's pads, so a housing placed where its header is
  placed sits mated), else from the family's pitch.

The tables (:data:`JST`, :func:`catalog`) are plain data: pitch, pin counts, MPNs, crimp
terminals, AWG range, seals, mating P/Ns. :func:`catalog` turns them into :class:`Entry`
records with pin positions and directions, :func:`write_manifest` writes the manifest
(JSON + CSV), :func:`build_part` (FreeCAD only) writes one ``.FCStd``, and
:func:`import_library` checks the parts into a vault as ``cots`` items numbered by the
vault (``vault_setup.new_item``, as New COTS Item does). FreeCAD-free except build_part.

Run: ``python -m bruceworx.connector_library manifest --out DIR`` (no FreeCAD), then under
FreeCADCmd with ``BW_CONNLIB_ARGS="build --out DIR"`` (see :func:`main`), then
``... import --vault <Standard Libraries> --manifest DIR/manifest.json [--dry-run]``.

Dimensions: JST from the JST datasheets (XH, PH, GH, SH: housing size, mated height, side
entry length). Micro-Fit, Dupont, XT, MR30, bullets and DT are typical catalogue values,
about +-1 mm; see the ROU-05 connector library chapter.
"""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field

from bruceworx import kicad_bridge as kb

LIBRARY_VERSION = 1  # bump when the generated parts change
ITEM_TYPE = "cots"
COMMENT_PREFIX = "Connector library: "
KICAD_LICENSE = ("KiCad library 3D model, CC-BY-SA 4.0 with the KiCad libraries exception (designs using it are "
                 "not Adapted Material; https://www.kicad.org/libraries/license/). Attribution: KiCad Library "
                 "Team and contributors, kicad-packages3D.")
PARAMETRIC_LICENSE = "Parametric outline made by BruceWorx from catalogue dimensions; no third-party model."
PARAMETRIC_SOURCE = "Parametric outline (no KiCad model)"

# Frame placement modes (rou05 interface section 2).
FRAME_NORMAL, FRAME_MATED = "", "Mated plug"

WHITE = (0.93, 0.91, 0.84)
BLACK = (0.13, 0.13, 0.14)
YELLOW = (0.96, 0.78, 0.10)
GREY = (0.36, 0.37, 0.38)
GOLD = (0.86, 0.70, 0.32)


# ----- data ------------------------------------------------------------------------------------


@dataclass
class Pin:
    name: str
    pos: tuple  # (x, y, z) mm in the part
    dir: tuple  # unit +Z of the frame: out along the wire

    def as_dict(self) -> dict:
        return {"name": self.name, "pos": [round(v, 4) for v in self.pos], "dir": list(self.dir)}


@dataclass
class Entry:
    """One connector part."""

    mpn: str
    family: str  # ConnectorFamily
    role: str  # ConnectorRole
    manufacturer: str
    description: str
    positions: int
    pitch: float
    mating: list = field(default_factory=list)
    terminal: str = ""  # crimp contact P/N ("" = solder cup / PCB)
    gauge_min: int = 0  # AWG, thinnest (larger number)
    gauge_max: int = 0  # AWG, thickest (smaller number)
    seal: str = ""
    accessory: str = ""  # e.g. DT wedgelock
    notes: str = ""
    footprint: str = ""  # "Lib:Name" of the KiCad footprint the pins come from
    model: str = ""  # "<lib>.3dshapes/<file>.step" relative to the KiCad 3D model dir; "" = parametric
    body: list = field(default_factory=list)  # parametric primitives (see build_shape)
    pins: list = field(default_factory=list)
    frame_at: str = FRAME_NORMAL
    colour: tuple = WHITE
    file: str = ""  # the part file, relative to the library folder

    @property
    def source(self) -> str:
        return "kicad" if self.model else "parametric"

    @property
    def model_source(self) -> str:
        return f"KiCad 3D library: {self.model}" if self.model else PARAMETRIC_SOURCE

    @property
    def license(self) -> str:
        return KICAD_LICENSE if self.model else PARAMETRIC_LICENSE

    def as_dict(self) -> dict:
        data = asdict(self)
        data["pins"] = [p.as_dict() for p in self.pins]
        data["source"] = self.source
        data["model_source"] = self.model_source
        data["license"] = self.license
        return data


# ----- KiCad footprints ------------------------------------------------------------------------


class KiCadMissing(FileNotFoundError):
    """The KiCad footprint or model library is not installed."""


def footprints_dir() -> str:
    return os.environ.get("BW_KICAD_FOOTPRINTS") or os.path.join(kb.SHARED_SUPPORT, "footprints")


def models_dir() -> str:
    return os.environ.get("BW_KICAD_3DMODELS") or kb.MODELS_DIR


def kicad_available() -> bool:
    return os.path.isdir(os.path.join(footprints_dir(), "Connector_JST.pretty"))


_PAD_CACHE: dict = {}


def footprint_pads(lib_id: str) -> list:
    """``[(number, x, y)]`` of the electrical pads of footprint ``Lib:Name`` in PART coordinates
    (KiCad y flipped), in pad order. Pads with no number or ``MP`` (pegs, mounting pads) are left
    out; a number used twice (several pads of one pin) keeps its first pad."""
    if lib_id in _PAD_CACHE:
        return _PAD_CACHE[lib_id]
    lib, _, name = lib_id.partition(":")
    path = os.path.join(footprints_dir(), lib + ".pretty", name + ".kicad_mod")
    if not os.path.isfile(path):
        raise KiCadMissing(path)
    with open(path, encoding="utf-8") as handle:
        node = kb.parse_sexpr(handle.read())
    pads, seen = [], set()
    for pad in kb.find_all(node, "pad"):
        number = pad[1] if len(pad) > 1 and isinstance(pad[1], str) else ""
        if not number or number.upper() == "MP" or number in seen:
            continue
        if len(pad) > 2 and pad[2] == "np_thru_hole":
            continue
        at = kb.find(pad, "at")
        seen.add(number)
        pads.append((number, kb.num(at[1]), -kb.num(at[2]) + 0.0))
    _PAD_CACHE[lib_id] = pads
    return pads


def footprint_model(lib_id: str) -> str:
    """The footprint's first 3D model as ``<lib>.3dshapes/<file>.step`` when that STEP file is
    installed, else ""."""
    lib, _, name = lib_id.partition(":")
    path = os.path.join(footprints_dir(), lib + ".pretty", name + ".kicad_mod")
    with open(path, encoding="utf-8") as handle:
        node = kb.parse_sexpr(handle.read())
    for model in kb.find_all(node, "model"):
        if len(model) < 2 or not isinstance(model[1], str):
            continue
        offset = kb.find(kb.find(model, "offset") or ["offset"], "xyz")
        rotate = kb.find(kb.find(model, "rotate") or ["rotate"], "xyz")
        if any(kb.num(v) for v in (offset or [None])[1:] + (rotate or [None])[1:]):
            continue  # an offset or turned model: not used (none of this library's footprints has one)
        rel = re.sub(r"^\$\{[^}]+\}/", "", model[1])
        stem, ext = os.path.splitext(rel)
        for candidate in (rel, stem + ".step", stem + ".stp"):
            if os.path.isfile(os.path.join(models_dir(), candidate)):
                return candidate
    return ""


# ----- helpers for the tables ------------------------------------------------------------------


def centroid(points) -> tuple:
    pts = list(points)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def box_about(cx, cy, w, d, z0, h) -> tuple:
    """A box ``w`` (x) by ``d`` (y) centred on (cx, cy), from z0 up by h."""
    return ("box", (cx - w / 2, cy - d / 2, z0), (w, d, h))


def cavities(pads, z_top, size, depth=1.5) -> list:
    """Square contact cavities cut into a housing's rear face (top at z_top)."""
    return [("cut", "box", (x - size / 2, y - size / 2, z_top - depth), (size, size, depth + 0.01))
            for _n, x, y in pads]


def stubs(pads, r=0.32, length=3.0) -> list:
    """Pins under a parametric board-side part (through-hole tails)."""
    return [("cyl", (x, y, -length), r, length, (0, 0, 1)) for _n, x, y in pads]


def pins_at(pads, z, direction=(0, 0, 1)) -> list:
    return [Pin(n, (x, y, z), direction) for n, x, y in pads]


def grid_pads(cols, rows, pitch, order="rows") -> list:
    """Pads of a generic grid, pin 1 at the origin; rows run along x. ``order``: "rows" numbers
    row 1 left to right then row 2 left to right; "u" goes back along row 2 (Deutsch)."""
    pads, k = [], 1
    for r in range(rows):
        xs = list(range(cols))
        if order == "u" and r % 2 == 1:
            xs.reverse()
        for c in xs:
            pads.append((str(k), c * pitch, -r * pitch))
            k += 1
    return pads


def awg(text: str) -> tuple:
    a, b = (int(v) for v in text.split("-"))
    return max(a, b), min(a, b)


# ----- JST wire-to-board families (datasheet values) ------------------------------------------

# housing W = pitch*(n-1) + w0; D, H: housing depth and height; z0: housing bottom above the PCB when
# mated on the vertical header (mated height = z0 + H); side: mated length from the side header's rear
# edge to the housing's rear face; rear: how far the side header reaches behind its pad row (+y);
# axis: the wire axis height above the PCB on the side header.
JST = {
    "XH": dict(family="JST-XH", pitch=2.5, w0=4.8, D=5.7, H=7.5, z0=2.3, side=14.3, rear=2.30, axis=3.05,
               housing="XHP-{n}", vertical="B{n}B-XH-A", horizontal="S{n}B-XH-A",
               vfp="Connector_JST:JST_XH_B{n}B-XH-A_1x{n:02d}_P2.50mm_Vertical",
               hfp="Connector_JST:JST_XH_S{n}B-XH-A_1x{n:02d}_P2.50mm_Horizontal",
               terminal="SXH-001T-P0.6", awg="28-22",
               notes="SXH-002T-P0.6 for AWG 30-26; wire insulation OD 0.9-1.9 mm"),
    "PH": dict(family="JST-PH", pitch=2.0, w0=3.8, D=4.5, H=6.85, z0=1.15, side=9.6, rear=1.35, axis=2.4,
               housing="PHR-{n}", vertical="B{n}B-PH-K", horizontal="S{n}B-PH-K",
               vfp="Connector_JST:JST_PH_B{n}B-PH-K_1x{n:02d}_P2.00mm_Vertical",
               hfp="Connector_JST:JST_PH_S{n}B-PH-K_1x{n:02d}_P2.00mm_Horizontal",
               terminal="SPH-002T-P0.5S", awg="30-24",
               notes="SPH-004T-P0.5S for AWG 32-28; wire insulation OD 0.8-1.5 mm"),
    "GH": dict(family="JST-GH", pitch=1.25, w0=2.5, D=4.15, H=5.7, z0=1.6, side=7.15, rear=0.63, axis=2.15,
               housing="GHR-{n:02d}V-S", vertical="BM{n:02d}B-GHS-TBT", horizontal="SM{n:02d}B-GHS-TB",
               vfp="Connector_JST:JST_GH_BM{n:02d}B-GHS-TBT_1x{n:02d}-1MP_P1.25mm_Vertical",
               hfp="Connector_JST:JST_GH_SM{n:02d}B-GHS-TB_1x{n:02d}-1MP_P1.25mm_Horizontal",
               terminal="SSHL-002T-P0.2", awg="30-26", notes="wire insulation OD 0.76-1.0 mm"),
    "SH": dict(family="JST-SH", pitch=1.0, w0=4.0, D=2.8, H=5.0, z0=1.3, side=6.25, rear=0.48, axis=1.48,
               housing="SHR-{n:02d}V-S-B", vertical="BM{n:02d}B-SRSS-TB", horizontal="SM{n:02d}B-SRSS-TB",
               vfp="Connector_JST:JST_SH_BM{n:02d}B-SRSS-TB_1x{n:02d}-1MP_P1.00mm_Vertical",
               hfp="Connector_JST:JST_SH_SM{n:02d}B-SRSS-TB_1x{n:02d}-1MP_P1.00mm_Horizontal",
               terminal="SSH-003T-P0.2-H", awg="32-28", notes="wire insulation OD 0.4-0.8 mm"),
}
JST_COUNTS = range(2, 9)


def _jst(key: str) -> list:
    t = JST[key]
    gmin, gmax = awg(t["awg"])
    out = []
    for n in JST_COUNTS:
        housing, vertical, horizontal = (t[k].format(n=n) for k in ("housing", "vertical", "horizontal"))
        vfp, hfp = t["vfp"].format(n=n), t["hfp"].format(n=n)
        pads = footprint_pads(vfp)
        cx, cy = centroid((x, y) for _n, x, y in pads)
        top = t["z0"] + t["H"]
        width = t["pitch"] * (n - 1) + t["w0"]
        common = dict(family=t["family"], manufacturer="JST", positions=n, pitch=t["pitch"])
        out.append(Entry(
            housing, role="Wire housing", mating=[vertical, horizontal],
            description=f"Connector, {t['family']} wire housing, {n}-pos, {t['pitch']:.2f} mm ({housing})",
            terminal=t["terminal"], gauge_min=gmin, gauge_max=gmax, notes=t["notes"], footprint=vfp,
            body=[box_about(cx, cy, width, t["D"], t["z0"], t["H"])] + cavities(pads, top, t["pitch"] * 0.55),
            pins=pins_at(pads, top), colour=WHITE, **common))
        out.append(Entry(
            vertical, role="Header", mating=[housing], footprint=vfp, model=footprint_model(vfp),
            description=f"Connector, {t['family']} header, vertical, {n}-pos, {t['pitch']:.2f} mm ({vertical})",
            pins=pins_at(pads, top), frame_at=FRAME_MATED, colour=WHITE,
            body=_header_outline(pads, width, t["D"] + 0.4, 0.8 * t["z0"] + 0.5 * t["H"], cx, cy),
            notes=f"pin frames at the rear face of the mated {housing}", **common))
        hpads = footprint_pads(hfp)
        exit_y = max(y for _n, _x, y in hpads) + t["rear"] - t["side"]
        out.append(Entry(
            horizontal, role="Header", mating=[housing], footprint=hfp, model=footprint_model(hfp),
            description=f"Connector, {t['family']} header, right angle, {n}-pos, {t['pitch']:.2f} mm ({horizontal})",
            pins=[Pin(num, (x, exit_y, t["axis"]), (0, -1, 0)) for num, x, _y in hpads],
            frame_at=FRAME_MATED, colour=WHITE,
            body=[("box", (min(x for _n, x, _y in hpads) - t["w0"] / 2, exit_y + t["H"],  0.0),
                   (width, t["side"] - t["H"], 2 * t["axis"]))],
            notes=f"pin frames at the rear face of the mated {housing}", **common))
    return out


def _header_outline(pads, width, depth, height, cx, cy) -> list:
    """A shrouded header (used only when KiCad has no model): a box with the tails under it."""
    return [box_about(cx, cy, width, depth, 0.0, height)] + stubs(pads)


# ----- Dupont 2.54 mm ---------------------------------------------------------------------------

DUPONT = dict(family="Dupont 2.54", pitch=2.54, H=14.0, z0=2.54, terminal="DUPONT-F-2.54 (generic female crimp)",
              awg="28-22", counts=[(1, n) for n in range(2, 9)] + [(2, n) for n in range(2, 9)])


def _dupont() -> list:
    t = DUPONT
    gmin, gmax = awg(t["awg"])
    out = []
    for rows, cols in t["counts"]:
        n = rows * cols
        tag = f"{rows}x{cols:02d}"
        housing, header = f"DUPONT-HSG-{tag}", f"HDR254-M-{tag}"
        fp = f"Connector_PinHeader_2.54mm:PinHeader_{tag}_P2.54mm_Vertical"
        pads = footprint_pads(fp)
        cx, cy = centroid((x, y) for _n, x, y in pads)
        xs = {round(x, 3) for _n, x, _y in pads}
        ys = {round(y, 3) for _n, _x, y in pads}
        w, d = 2.54 * len(xs), 2.54 * len(ys)
        top = t["z0"] + t["H"]
        common = dict(family=t["family"], manufacturer="Generic (Dupont-style)", positions=n, pitch=2.54)
        out.append(Entry(
            housing, role="Wire housing", mating=[header], footprint=fp, terminal=t["terminal"], gauge_min=gmin,
            gauge_max=gmax, description=f"Connector, Dupont 2.54 mm wire housing, {tag} ({housing})",
            body=[box_about(cx, cy, w, d, t["z0"], t["H"])] + cavities(pads, top, 1.2),
            pins=pins_at(pads, top), colour=BLACK,
            notes="generic Dupont-style housing; mates a male 2.54 mm pin header", **common))
        out.append(Entry(
            header, role="Header", mating=[housing], footprint=fp, model=footprint_model(fp),
            description=f"Connector, pin header 2.54 mm, male, vertical, {tag} ({header})",
            pins=pins_at(pads, top), frame_at=FRAME_MATED, colour=BLACK,
            body=[box_about(cx, cy, w, d, 0.0, 2.54)] + stubs(pads),
            notes=f"pin frames at the rear face of the mated {housing}", **common))
    return out


# ----- Molex Micro-Fit 3.0 (dual row) -----------------------------------------------------------

MICROFIT = dict(family="Molex Micro-Fit 3.0", pitch=3.0, counts=(2, 4, 6, 8, 10, 12),
                f_terminal="43030-0007", m_terminal="43031-0007", awg="24-20",
                # receptacle: W = 3*cols + w0, D, H; z0: its bottom above the PCB when mated on the vertical
                # header; header height; plug length below the receptacle's mating face.
                w0=3.6, D=9.85, H=10.6, z0=2.0, header_h=9.6, plug=12.4)


def _microfit() -> list:
    t = MICROFIT
    gmin, gmax = awg(t["awg"])
    out = []
    for n in t["counts"]:
        cols = n // 2
        rec, plug, header = f"43025-{n:02d}00", f"43020-{n:02d}00", f"43045-{n:02d}12"
        fp = f"Connector_Molex:Molex_Micro-Fit_3.0_{header}_2x{cols:02d}_P3.00mm_Vertical"
        pads = footprint_pads(fp)
        cx, cy = centroid((x, y) for _n, x, y in pads)
        w = 3.0 * cols + t["w0"]
        top = t["z0"] + t["H"]
        bottom = t["z0"] - t["plug"]
        common = dict(family=t["family"], manufacturer="Molex", positions=n, pitch=3.0)
        out.append(Entry(
            rec, role="Receptacle", mating=[header, plug], footprint=fp, terminal=t["f_terminal"],
            gauge_min=gmin, gauge_max=gmax, colour=BLACK,
            description=f"Connector, Molex Micro-Fit 3.0 receptacle, dual row, {n}-pos ({rec})",
            body=[box_about(cx, cy, w, t["D"], t["z0"], t["H"])] + cavities(pads, top, 1.9),
            pins=pins_at(pads, top), notes="female terminals 43030; mates header 43045 or plug 43020", **common))
        out.append(Entry(
            plug, role="Plug", mating=[rec], footprint=fp, terminal=t["m_terminal"], gauge_min=gmin, gauge_max=gmax,
            colour=BLACK, description=f"Connector, Molex Micro-Fit 3.0 plug, dual row, {n}-pos ({plug})",
            body=[box_about(cx, cy, w - 0.8, t["D"] - 0.8, bottom, t["plug"])],
            pins=pins_at(pads, bottom, (0, 0, -1)),
            notes="male terminals 43031; wire-to-wire mate of 43025 (same placement = mated)", **common))
        out.append(Entry(
            header, role="Header", mating=[rec], footprint=fp, model=footprint_model(fp), colour=BLACK,
            description=f"Connector, Molex Micro-Fit 3.0 header, vertical, dual row, {n}-pos ({header})",
            body=_header_outline(pads, w + 0.6, t["D"] + 0.6, t["header_h"], cx, cy)
            + [("cut", "box", (cx - w / 2, cy - t["D"] / 2, t["z0"]), (w, t["D"], t["header_h"]))],
            pins=pins_at(pads, top), frame_at=FRAME_MATED,
            notes=f"pin frames at the rear face of the mated {rec}", **common))
    return out


# ----- power: AMASS XT30 / XT60 / XT90 / MR30, 3.5 mm bullets ------------------------------------

# Wire connectors stand on their mating face at the top, solder cups below z=0 (as KiCad's XT60
# vertical model does). width/depth/height of the body; cup: cup depth below z=0; pw_len: right-angle
# PCB body length from the pad row towards the mating face; pw_h: its height; mate_len: the mated wire
# connector's length beyond the PCB part's face, to its cup mouths.
POWER = {
    "XT30": dict(pitch=5.0, n=2, w=10.2, d=5.2, h=11.0, cup=4.5, awg="18-16", pw_h=5.2, mate_len=15.5,
                 wire=("XT30U-M", "XT30U-F"), pw=("XT30PW-M", "XT30PW-F"),
                 vfp="Connector_AMASS:AMASS_{mpn}_1x02_P5.0mm_Vertical",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P2.50mm_Horizontal"),
    "XT60": dict(pitch=7.2, n=2, w=15.9, d=8.1, h=16.0, cup=4.5, awg="14-12", pw_h=8.2, mate_len=20.5,
                 wire=("XT60-M", "XT60-F"), pw=("XT60PW-M", "XT60PW-F"),
                 vfp="Connector_AMASS:AMASS_{mpn}_1x02_P7.20mm_Vertical",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P7.20mm_Horizontal"),
    "XT90": dict(pitch=10.9, n=2, w=21.0, d=10.8, h=21.5, cup=6.5, awg="10-8", pw_h=10.8, mate_len=27.0,
                 wire=("XT90-M", "XT90-F"), pw=("XT90PW-M",), vfp="",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P10.90mm_Horizontal"),
    "MR30": dict(pitch=3.5, n=3, w=13.0, d=6.2, h=12.0, cup=4.0, awg="18-16", pw_h=6.2, mate_len=14.5,
                 wire=("MR30-M", "MR30-F"), pw=("MR30PW-M", "MR30PW-FB"), vfp="",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x03_P3.50mm_Horizontal"),
}


def _wire_power(key: str, t: dict, mpn: str, mates: list) -> Entry:
    gmin, gmax = awg(t["awg"])
    fp = t["vfp"].format(mpn=mpn) if t["vfp"] else ""
    pads = footprint_pads(fp) if fp else grid_pads(t["n"], 1, t["pitch"])
    model = footprint_model(fp) if fp else ""
    cx, cy = centroid((x, y) for _n, x, y in pads)
    male = mpn.endswith("-M")
    body = [box_about(cx, cy, t["w"], t["d"], 0.0, t["h"])]
    body += [("cyl", (x, y, -t["cup"]), t["pitch"] * 0.22 if key == "MR30" else min(2.2, t["pitch"] * 0.3),
              t["cup"], (0, 0, 1)) for _n, x, y in pads]
    return Entry(
        mpn, family=key, role="Plug" if male else "Receptacle", manufacturer="AMASS", positions=t["n"],
        pitch=t["pitch"], mating=mates, gauge_min=gmin, gauge_max=gmax, footprint=fp, model=model,
        description=f"Connector, AMASS {key} {'male' if male else 'female'}, solder cup ({mpn})",
        body=body, pins=pins_at(pads, -t["cup"], (0, 0, -1)), colour=YELLOW,
        notes="solder cup: no crimp terminal; the frame is at the cup mouth, +Z down the wire")


def _pcb_power(key: str, t: dict, mpn: str, mates: list) -> Entry:
    fp = t["pwfp"].format(mpn=mpn)
    pads = footprint_pads(fp)
    # The right-angle body runs from the pads towards the board edge: +y in the part (KiCad -y),
    # the side of the locating pegs.
    face = 3.0 + _peg_reach(fp)
    xs = [x for _n, x, _y in pads]
    cx = (min(xs) + max(xs)) / 2
    body = [("box", (cx - t["w"] / 2, -1.5, 0.0), (t["w"], face + 1.5, t["pw_h"]))] + stubs(pads, r=0.6)
    exit_y = face + t["mate_len"]
    return Entry(
        mpn, family=key, role="Header", manufacturer="AMASS", positions=t["n"], pitch=t["pitch"], mating=mates,
        footprint=fp, model=footprint_model(fp), colour=YELLOW,
        description=f"Connector, AMASS {key} PCB right angle {'male' if '-M' in mpn else 'female'} ({mpn})",
        body=body, pins=[Pin(num, (x, exit_y, t["pw_h"] / 2), (0, 1, 0)) for num, x, _y in pads],
        frame_at=FRAME_MATED, notes=f"pin frames at the solder cups of the mated {mates[0]}")


def _peg_reach(lib_id: str) -> float:
    """How far the footprint's unnumbered pegs reach from the pad row towards the mating face (part +y)."""
    lib, _, name = lib_id.partition(":")
    path = os.path.join(footprints_dir(), lib + ".pretty", name + ".kicad_mod")
    with open(path, encoding="utf-8") as handle:
        node = kb.parse_sexpr(handle.read())
    ys = [-kb.num(kb.find(p, "at")[2]) for p in kb.find_all(node, "pad")
          if not (len(p) > 1 and isinstance(p[1], str) and p[1].isdigit())]
    return max(ys + [5.0])


def _power() -> list:
    out = []
    for key, t in POWER.items():
        m, f = t["wire"]
        pw = t["pw"]
        pcb_m = [p for p in pw if p.endswith("-M")]
        pcb_f = [p for p in pw if not p.endswith("-M")]
        out.append(_wire_power(key, t, m, [f] + pcb_f))
        out.append(_wire_power(key, t, f, [m] + pcb_m))
        for mpn in pcb_m:
            out.append(_pcb_power(key, t, mpn, [f]))
        for mpn in pcb_f:
            out.append(_pcb_power(key, t, mpn, [m]))
    return out


BULLET = dict(family="Bullet 3.5 mm", awg="16-14", male="BULLET-3.5-M", female="BULLET-3.5-F")


def _bullets() -> list:
    t = BULLET
    gmin, gmax = awg(t["awg"])
    common = dict(family=t["family"], manufacturer="Generic", positions=1, pitch=0.0, gauge_min=gmin,
                  gauge_max=gmax, colour=GOLD)
    male = Entry(
        t["male"], role="Bullet", mating=[t["female"]], description=f"Connector, 3.5 mm bullet, male ({t['male']})",
        body=[("cyl", (0, 0, 0), 1.75, 12.0, (0, 0, 1)), ("cyl", (0, 0, -8.0), 2.25, 8.0, (0, 0, 1)),
              ("cut", "cyl", (0, 0, -8.01), 1.6, 6.0, (0, 0, 1))],
        pins=[Pin("1", (0, 0, -8.0), (0, 0, -1))],
        notes="solder cup; pin points +Z; same placement as the female = mated", **common)
    female = Entry(
        t["female"], role="Bullet", mating=[t["male"]],
        description=f"Connector, 3.5 mm bullet, female ({t['female']})",
        body=[("cyl", (0, 0, 0), 2.3, 21.0, (0, 0, 1)), ("cut", "cyl", (0, 0, -0.01), 1.78, 12.5, (0, 0, 1)),
              ("cut", "cyl", (0, 0, 15.0), 1.6, 6.01, (0, 0, 1))],
        pins=[Pin("1", (0, 0, 21.0), (0, 0, 1))], notes="solder cup; socket opens at -Z", **common)
    return [male, female]


# ----- Deutsch DT --------------------------------------------------------------------------------

DT = dict(family="Deutsch DT", counts=(2, 3, 4, 6, 8, 12), pitch=5.0, awg="20-16",
          pin="0460-202-16141", socket="0462-201-16141", seal="114017",
          layout={2: (2, 1), 3: (3, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 12: (6, 2)},
          rec_len=30.0, plug_len=27.0, overlap=10.0)


def _dt() -> list:
    t = DT
    gmin, gmax = awg(t["awg"])
    out = []
    for n in t["counts"]:
        cols, rows = t["layout"][n]
        pads = grid_pads(cols, rows, t["pitch"], order="u")
        cx, cy = centroid((x, y) for _n, x, y in pads)
        w, d = cols * t["pitch"] + 8.0, rows * t["pitch"] + 7.0
        rec, plug = f"DT04-{n}P", f"DT06-{n}S"
        common = dict(family=t["family"], manufacturer="TE Connectivity (Deutsch)", positions=n, pitch=t["pitch"],
                      gauge_min=gmin, gauge_max=gmax, seal=t["seal"], colour=GREY)
        note = (f"wire seal integral in the rear grommet; {t['seal']} = sealing plug for an unused cavity; "
                "contact layout and spacing approximate")
        out.append(Entry(
            rec, role="Receptacle", mating=[plug], terminal=t["pin"], accessory=f"W{n}P",
            description=f"Connector, Deutsch DT receptacle, {n}-way, pin contacts ({rec})",
            body=[box_about(cx, cy, w, d, 0.0, t["rec_len"])] + cavities(pads, t["rec_len"], 2.2, 2.0),
            pins=pins_at(pads, t["rec_len"]), notes=note + f"; wedgelock W{n}P", **common))
        out.append(Entry(
            plug, role="Plug", mating=[rec], terminal=t["socket"], accessory=f"W{n}S",
            description=f"Connector, Deutsch DT plug, {n}-way, socket contacts ({plug})",
            body=[box_about(cx, cy, w - 2.4, d - 2.4, t["overlap"] - t["plug_len"], t["plug_len"])],
            pins=pins_at(pads, t["overlap"] - t["plug_len"], (0, 0, -1)),
            notes=note + f"; wedgelock W{n}S; same placement as {rec} = mated", **common))
    return out


# ----- the catalogue ----------------------------------------------------------------------------

FAMILY_ORDER = ("JST-XH", "JST-PH", "JST-GH", "JST-SH", "Dupont 2.54", "Molex Micro-Fit 3.0", "XT30", "XT60",
                "XT90", "MR30", "Bullet 3.5 mm", "Deutsch DT")


def safe_name(mpn: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", mpn).strip("_")


def catalog() -> list:
    """Every connector of the starter library as :class:`Entry` records (needs KiCad's footprints)."""
    if not kicad_available():
        raise KiCadMissing(f"KiCad footprints not found in {footprints_dir()}")
    entries = []
    for key in JST:
        entries += _jst(key)
    entries += _dupont() + _microfit() + _power() + _bullets() + _dt()
    for entry in entries:
        entry.file = safe_name(entry.mpn) + ".FCStd"
    return entries


def check(entries) -> list:
    """Problems with the catalogue (empty when it is sound): duplicate MPNs or files, mating that is
    not symmetric or names a part not in the library, pin counts, gauge ranges, frame directions."""
    problems = []
    by_mpn = {}
    for e in entries:
        if e.mpn in by_mpn:
            problems.append(f"{e.mpn}: listed twice")
        by_mpn[e.mpn] = e
    files = [e.file.lower() for e in entries]
    if len(set(files)) != len(files):
        problems.append("two parts share a file name")
    for e in entries:
        if len(e.pins) != e.positions:
            problems.append(f"{e.mpn}: {len(e.pins)} pins for {e.positions} positions")
        names = [p.name for p in e.pins]
        if len(set(names)) != len(names):
            problems.append(f"{e.mpn}: repeated pin names")
        for p in e.pins:
            if abs(math.sqrt(sum(c * c for c in p.dir)) - 1.0) > 1e-9:
                problems.append(f"{e.mpn} pin {p.name}: direction is not a unit vector")
        if e.gauge_min and e.gauge_max and e.gauge_min < e.gauge_max:
            problems.append(f"{e.mpn}: gauge range {e.gauge_min}-{e.gauge_max} AWG is backwards")
        if e.role in ("Wire housing", "Receptacle", "Plug", "Bullet") and not (e.gauge_min and e.gauge_max):
            problems.append(f"{e.mpn}: a wire-side part with no gauge range")
        if e.frame_at not in (FRAME_NORMAL, FRAME_MATED):
            problems.append(f"{e.mpn}: FrameAt {e.frame_at!r}")
        if not e.mating:
            problems.append(f"{e.mpn}: no mating part")
        for mate in e.mating:
            other = by_mpn.get(mate)
            if other is None:
                problems.append(f"{e.mpn}: mates {mate}, which is not in the library")
            elif e.mpn not in other.mating:
                problems.append(f"{e.mpn} mates {mate}, but {mate} does not list {e.mpn}")
            elif other.positions != e.positions:
                problems.append(f"{e.mpn} ({e.positions}) mates {mate} ({other.positions})")
    return problems


def summary(entries) -> list:
    """Per family: parts, from KiCad, parametric, pin-count range (for the Built chapter)."""
    rows = {}
    for e in entries:
        row = rows.setdefault(e.family, {"family": e.family, "parts": 0, "kicad": 0, "parametric": 0,
                                         "positions": set()})
        row["parts"] += 1
        row[e.source] += 1
        row["positions"].add(e.positions)
    out = []
    for fam in FAMILY_ORDER:
        if fam in rows:
            row = rows[fam]
            row["positions"] = sorted(row["positions"])
            out.append(row)
    return out


def write_manifest(entries, folder: str) -> str:
    """``manifest.json`` (everything) and ``manifest.csv`` (one row per part) in ``folder``."""
    os.makedirs(folder, exist_ok=True)
    data = {"format": "bruceworx.connector-library", "version": LIBRARY_VERSION,
            "kicad_footprints": footprints_dir(), "kicad_models": models_dir(),
            "parts": [e.as_dict() for e in entries]}
    path = os.path.join(folder, "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    columns = ("file", "mpn", "manufacturer", "description", "family", "role", "positions", "pitch", "mating",
               "terminal", "gauge_min", "gauge_max", "seal", "accessory", "source", "model", "footprint", "frame_at")
    with open(os.path.join(folder, "manifest.csv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for e in entries:
            row = e.as_dict()
            row["mating"] = "; ".join(e.mating)
            writer.writerow([row[c] for c in columns])
    return path


def read_manifest(path: str) -> list:
    """The parts of a manifest as :class:`Entry` records."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("format") != "bruceworx.connector-library":
        raise ValueError(f"{path} is not a connector library manifest")
    out = []
    for raw in data["parts"]:
        raw = dict(raw)
        for key in ("source", "model_source", "license"):
            raw.pop(key, None)
        raw["pins"] = [Pin(p["name"], tuple(p["pos"]), tuple(p["dir"])) for p in raw["pins"]]
        raw["colour"] = tuple(raw["colour"])
        raw["body"] = [_untuple(op) for op in raw["body"]]
        out.append(Entry(**raw))
    return out


def _untuple(op):
    return tuple(_untuple(v) if isinstance(v, list) else v for v in op)


# ----- FreeCAD: one part file --------------------------------------------------------------------


def build_shape(ops):
    """The parametric body: the union of the ``box``/``cyl`` primitives minus the ``cut`` ones."""
    import FreeCAD as App  # noqa: PLC0415
    import Part  # noqa: PLC0415

    def solid(kind, args):
        if kind == "box":
            (x, y, z), (dx, dy, dz) = args
            return Part.makeBox(dx, dy, dz, App.Vector(x, y, z))
        (x, y, z), r, h, axis = args
        return Part.makeCylinder(r, h, App.Vector(x, y, z), App.Vector(*axis))

    adds = [solid(op[0], op[1:]) for op in ops if op[0] != "cut"]
    cuts = [solid(op[1], op[2:]) for op in ops if op[0] == "cut"]
    shape = adds[0].fuse(adds[1:]) if len(adds) > 1 else adds[0]
    if cuts:
        shape = shape.cut(cuts)
    return shape.removeSplitter() if hasattr(shape, "removeSplitter") else shape


def _string(obj, name, value, doc, group="BruceWorx"):
    if name not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", name, group, doc)
    setattr(obj, name, value)


def _x_dir(entry):
    """+X of the frames: towards pin 2 (rou05: recommended), square to +Z."""
    if len(entry.pins) < 2:
        return None
    a, b = entry.pins[0], entry.pins[1]
    v = [b.pos[i] - a.pos[i] for i in range(3)]
    z = a.dir
    dot = sum(v[i] * z[i] for i in range(3))
    v = [v[i] - dot * z[i] for i in range(3)]
    norm = math.sqrt(sum(c * c for c in v))
    return tuple(c / norm for c in v) if norm > 1e-9 else None


def build_part(entry: Entry, out_path: str, schema_layers=None) -> str:
    """Write ``entry`` as a FreeCAD part file: an App::Part named after the MPN holding the body
    (KiCad STEP or parametric) and the pin frames (``bruceworx_routing_ports``), with the part
    properties (schema, ``ItemType`` cots, manufacturer, MPN, mating, licence). FreeCAD only."""
    import FreeCAD as App  # noqa: PLC0415
    import Part  # noqa: PLC0415

    import bruceworx_routing_ports as bp  # noqa: PLC0415 -- rou05's frame helpers (src/Mod/BruceWorx)

    name = safe_name(entry.mpn)
    doc = App.newDocument(name.replace("-", "_").replace(".", "_"))
    try:
        part = doc.addObject("App::Part", "Connector")
        part.Label = entry.mpn
        body = doc.addObject("Part::Feature", "Model")
        body.Label = f"{entry.mpn} body"
        if entry.model:
            shape = Part.read(os.path.join(models_dir(), entry.model))
        else:
            shape = build_shape(entry.body)
        body.Shape = shape
        if hasattr(body, "ShapeAppearance"):
            material = App.Material()
            material.DiffuseColor = entry.colour
            body.ShapeAppearance = [material]
        part.addObject(body)
        bp.set_connector(part, family=entry.family, mating_pn="; ".join(entry.mating), role=entry.role,
                         frame_at=entry.frame_at)
        xdir = _x_dir(entry)
        for pin in entry.pins:
            placement = (pin.pos, pin.dir, xdir) if xdir else (pin.pos, pin.dir)
            bp.make_pin(part, pin.name, placement, GaugeMin=entry.gauge_min, GaugeMax=entry.gauge_max,
                        TerminalPN=entry.terminal, SealPN=entry.seal)
        try:
            from bruceworx import partprops  # noqa: PLC0415

            partprops.load_schema([] if schema_layers is None else schema_layers).apply(part)
        except Exception as exc:  # noqa: BLE001 -- the part is still a connector without the schema
            App.Console.PrintWarning(f"connector library: part properties not applied: {exc}\n")
        values = {"Description": entry.description, "Vendor": entry.manufacturer,
                  "VendorPartNumber": entry.mpn}
        for key, val in values.items():
            if key in part.PropertiesList:
                setattr(part, key, val)
        if "ItemType" in part.PropertiesList:
            try:
                part.ItemType = ITEM_TYPE
            except Exception:  # noqa: BLE001 -- an older schema without cots
                pass
        _string(part, "Manufacturer", entry.manufacturer, "Manufacturer of the part")
        _string(part, "ManufacturerPartNumber", entry.mpn, "Manufacturer's part number")
        _string(part, "ConnectorPitch", f"{entry.pitch:g} mm" if entry.pitch else "", "Contact pitch")
        _string(part, "AccessoryPN", entry.accessory, "Accessory P/N (e.g. Deutsch wedgelock)")
        _string(part, "ConnectorNotes", entry.notes, "Notes from the connector library")
        _string(part, "Footprint", entry.footprint, "KiCad footprint the pins come from")
        _string(part, "ModelSource", entry.model_source, "Where the body comes from", group="Source")
        _string(part, "ModelLicense", entry.license, "Licence and attribution of the body", group="Source")
        _string(part, "LibraryVersion", f"connector library v{LIBRARY_VERSION}", "Generator version",
                group="Source")
        doc.recompute()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        doc.saveAs(os.path.abspath(out_path))
    finally:
        App.closeDocument(doc.Name)
    return out_path


def build_library(entries, folder: str, only=None, log=print) -> list:
    """Build every entry (or those whose MPN is in ``only``) into ``folder``; returns the paths."""
    out = []
    for entry in entries:
        if only and entry.mpn not in only:
            continue
        path = os.path.join(folder, entry.file)
        build_part(entry, path)
        out.append(path)
        log(f"built {entry.mpn} -> {path}")
    return out


# ----- vault import ------------------------------------------------------------------------------


@dataclass
class ImportPlan:
    part_number: str
    mpn: str
    description: str
    file: str
    skip: str = ""  # why the part is not imported ("" = it is)


def _imported_descriptions(vault) -> dict:
    return {item.description.strip().lower(): pn for pn, item in vault.model.items.items()}


def plan_import(vault, entries, folder: str, program: str = "") -> list:
    """What :func:`import_library` would do, writing nothing: the vault number each part would take
    (consecutive, from the vault's numbering, as for New COTS Item), or why it is skipped (already in
    the vault under the same description, or its file is missing)."""
    from bruceworx import numbering, pdm, vault_setup  # noqa: PLC0415
    from bruceworx.sources import JsonNumberSource  # noqa: PLC0415

    source = JsonNumberSource(vault_setup.numbering_path(vault.root))
    data = copy.deepcopy(source._load())
    programs = source.programs()
    program = numbering.normalize_program(program) if program else programs[0]
    types = vault_setup._vault_document_types(source, data)
    code = vault_setup.document_type_for("part", types)
    have = _imported_descriptions(vault)
    plans = []
    for entry in entries:
        path = os.path.join(folder, entry.file)
        plan = ImportPlan("", entry.mpn, entry.description, path)
        if entry.description.strip().lower() in have:
            plan.skip = f"already in the vault as {have[entry.description.strip().lower()]}"
        elif not os.path.isfile(path):
            plan.skip = "part file not built"
        else:
            plan.part_number = pdm.strip_revision(source._issue(data, program, code, None, None, write=True)[0])
        plans.append(plan)
    return plans


def import_library(vault, entries, folder: str, program: str = "", writer=None, progress=None) -> list:
    """Check every built part into ``vault`` as a new ``cots`` item (vault_setup.new_item: the number
    is previewed, the file copied to staging, ``writer(path, part_number)`` writes the number into
    the copy under FreeCAD, then the number is taken and the item checked in as WIP 01). Parts whose
    description is already an item are skipped, so a second run adds only what is new. The library
    folder is only read. Returns the :class:`ImportPlan` list with the numbers taken."""
    from bruceworx import vault_setup  # noqa: PLC0415

    have = _imported_descriptions(vault)
    plans = []
    for done, entry in enumerate(entries, 1):
        path = os.path.join(folder, entry.file)
        plan = ImportPlan("", entry.mpn, entry.description, path)
        if entry.description.strip().lower() in have:
            plan.skip = f"already in the vault as {have[entry.description.strip().lower()]}"
        elif not os.path.isfile(path):
            plan.skip = "part file not built"
        else:
            def make_file(staged, key, _path=path):
                shutil.copyfile(_path, staged)
                os.chmod(staged, 0o644)
                if writer is not None:
                    writer(staged, key)

            plan.part_number = vault_setup.new_item(vault, ITEM_TYPE, entry.description, make_file, program)
            have[entry.description.strip().lower()] = plan.part_number
        plans.append(plan)
        if progress is not None:
            progress(done, len(entries), plan)
    return plans


# ----- command line ------------------------------------------------------------------------------


def main(argv=None) -> int:
    """``manifest --out DIR`` | ``build --out DIR [--only MPN,...]`` (FreeCAD) |
    ``import --vault V --manifest M [--program P] [--dry-run]`` | ``check``.
    Under FreeCADCmd the arguments come from ``BW_CONNLIB_ARGS``."""
    import argparse  # noqa: PLC0415
    import shlex  # noqa: PLC0415

    if argv is None:
        argv = shlex.split(os.environ["BW_CONNLIB_ARGS"]) if os.environ.get("BW_CONNLIB_ARGS") else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="bruceworx.connector_library")
    parser.add_argument("action", choices=("check", "manifest", "build", "import"))
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--vault", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--program", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "check":
        entries = catalog()
        problems = check(entries)
        for row in summary(entries):
            print(f"{row['family']:22} {row['parts']:4} parts  kicad {row['kicad']:3}  parametric {row['parametric']:3}"
                  f"  positions {row['positions']}")
        for problem in problems:
            print("PROBLEM:", problem)
        return 1 if problems else 0
    if args.action in ("manifest", "build"):
        entries = catalog()
        problems = check(entries)
        if problems:
            raise SystemExit("catalogue problems:\n" + "\n".join(problems))
        path = write_manifest(entries, args.out)
        print(f"manifest: {path} ({len(entries)} parts)")
        if args.action == "build":
            only = {s.strip() for s in args.only.split(",") if s.strip()}
            build_library(entries, args.out, only or None)
        return 0
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415

    vault = FolderVault(args.vault)
    entries = read_manifest(args.manifest)
    folder = os.path.dirname(os.path.abspath(args.manifest))
    if args.dry_run:
        plans = plan_import(vault, entries, folder, args.program)
    else:
        from bruceworx import freecad_io  # noqa: PLC0415

        writer = freecad_io.write_part_number if freecad_io.freecad_available() else None
        plans = import_library(vault, entries, folder, args.program, writer=writer)
    for plan in plans:
        print(f"{plan.part_number or '-':22} {plan.mpn:22} {plan.skip or plan.description}")
    print(f"{sum(1 for p in plans if p.part_number)} of {len(plans)} parts "
          f"{'would be imported' if args.dry_run else 'imported'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
