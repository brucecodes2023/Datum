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

LIBRARY_VERSION = 2  # bump when the generated parts change
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
    accessory: str = ""  # MPN of a part counted once per connector (DT wedgelock)
    terminal_mfr: str = ""  # manufacturers of the above (filled by catalog() from ACCESSORIES)
    seal_mfr: str = ""
    accessory_mfr: str = ""
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


# ----- Dupont 2.54 mm: Amphenol FCI Mini-PV --------------------------------------------------------

# Sources (30 Oct): FCI customer drawing 65043 rev BG (dual row: width = columns x 2.54, depth 5.08, height
# 13.84; odd positions in one row, even in the other, as KiCad's PinHeader_2xNN pads); single-row 65039 and the
# 48235-000LF contact (22-26 AWG) from distributor listings. The housing seats on the header's 2.54 mm spacer.
DUPONT = dict(family="Dupont 2.54", pitch=2.54, H=13.84, z0=2.54, terminal="48235-000LF", awg="26-22",
              manufacturer="Amphenol FCI", counts=[(1, n) for n in range(2, 9)] + [(2, n) for n in range(2, 9)],
              suffix={2: "035", 3: "034", 4: "033", 5: "032", 6: "031", 7: "030", 8: "029"},
              series={1: "65039", 2: "65043"})


def dupont_mpn(rows: int, cols: int) -> str:
    return f"{DUPONT['series'][rows]}-{DUPONT['suffix'][cols]}LF"


def _dupont() -> list:
    t = DUPONT
    gmin, gmax = awg(t["awg"])
    out = []
    for rows, cols in t["counts"]:
        n = rows * cols
        tag = f"{rows}x{cols:02d}"
        housing, header = dupont_mpn(rows, cols), f"HDR254-M-{tag}"
        fp = f"Connector_PinHeader_2.54mm:PinHeader_{tag}_P2.54mm_Vertical"
        pads = footprint_pads(fp)
        cx, cy = centroid((x, y) for _n, x, y in pads)
        xs = {round(x, 3) for _n, x, _y in pads}
        ys = {round(y, 3) for _n, _x, y in pads}
        w, d = 2.54 * len(xs) - 0.1, 2.54 * len(ys) - 0.1
        top = t["z0"] + t["H"]
        common = dict(family=t["family"], positions=n, pitch=2.54)
        out.append(Entry(
            housing, role="Wire housing", manufacturer=t["manufacturer"], mating=[header], footprint=fp,
            terminal=t["terminal"], gauge_min=gmin, gauge_max=gmax,
            description=f"Connector, Mini-PV (Dupont) 2.54 mm wire housing, {tag} ({housing})",
            body=[box_about(cx, cy, w, d, t["z0"], t["H"])] + cavities(pads, top, 1.2),
            pins=pins_at(pads, top), colour=BLACK,
            notes="Amphenol FCI Mini-PV housing (the original 'Dupont'); clones fit; mates a male 2.54 mm header",
            **common))
        out.append(Entry(
            header, role="Header", manufacturer="Generic", mating=[housing], footprint=fp, model=footprint_model(fp),
            description=f"Connector, pin header 2.54 mm, male, vertical, {tag} ({header})",
            pins=pins_at(pads, top), frame_at=FRAME_MATED, colour=BLACK,
            body=[box_about(cx, cy, w, d, 0.0, 2.54)] + stubs(pads),
            notes=f"generic 2.54 mm header (no MPN chosen); pin frames at the rear face of the mated {housing}",
            **common))
    return out


# ----- Molex Micro-Fit 3.0 (dual row) -----------------------------------------------------------

# Sources (30 Oct): Molex SD-43025-002 (receptacle width A = 3.00 x columns + 0.85, depth 6.86 (7.57 for 2 and 4
# circuits), 14.00 long, latch on the row-2 side; 43020 + 43025 mated 24.77 long); Molex product pages of
# 43045-0212 / -1415 (vertical, mated height 17.64 above the PCB); SD-43045-001 (header width 3.00 x columns +
# 3.65). Circuit 1..n/2 in row 1, the rest in row 2 the same way (as KiCad's 43045 pads). Header body height and
# depth, the plug's width and depth: estimates.
MICROFIT = dict(family="Molex Micro-Fit 3.0", pitch=3.0, counts=(2, 4, 6, 8, 10, 12),
                f_terminal="43030-0007", m_terminal="43031-0007", awg="24-20",
                w0=0.85, D=6.86, D_small=7.57, L=14.0, mated=17.64, plug=24.77 - 14.0,
                header_w0=3.65, header_d=9.9, header_h=9.9)


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
        depth = t["D_small"] if n <= 4 else t["D"]
        top = t["mated"]
        z0 = top - t["L"]
        bottom = z0 - t["plug"]
        latch_y = cy - depth / 2 - 1.4  # on the row-2 side (KiCad's second row, part -y), 1.4 proud
        common = dict(family=t["family"], manufacturer="Molex", positions=n, pitch=3.0)
        out.append(Entry(
            rec, role="Receptacle", mating=[header, plug], footprint=fp, terminal=t["f_terminal"],
            gauge_min=gmin, gauge_max=gmax, colour=BLACK,
            description=f"Connector, Molex Micro-Fit 3.0 receptacle, dual row, {n}-pos ({rec})",
            body=[box_about(cx, cy, w, depth, z0, t["L"]), ("box", (cx - 1.5, latch_y, z0 + 3.0), (3.0, 1.4, 7.0))]
            + cavities(pads, top, 1.9),
            pins=pins_at(pads, top), notes="female terminals 43030; mates header 43045 or plug 43020", **common))
        out.append(Entry(
            plug, role="Plug", mating=[rec], footprint=fp, terminal=t["m_terminal"], gauge_min=gmin, gauge_max=gmax,
            colour=BLACK, description=f"Connector, Molex Micro-Fit 3.0 plug, dual row, {n}-pos ({plug})",
            body=[box_about(cx, cy, w + 0.6, depth + 0.6, bottom, t["plug"])],
            pins=pins_at(pads, bottom, (0, 0, -1)),
            notes="male terminals 43031; wire-to-wire mate of 43025 (same placement = mated; 24.77 mm mated)", **common))
        hw = 3.0 * cols + t["header_w0"]
        out.append(Entry(
            header, role="Header", mating=[rec], footprint=fp, model=footprint_model(fp), colour=BLACK,
            description=f"Connector, Molex Micro-Fit 3.0 header, vertical, dual row, {n}-pos ({header})",
            body=_header_outline(pads, hw, t["header_d"], t["header_h"], cx, cy)
            + [("cut", "box", (cx - w / 2 - 0.1, cy - depth / 2 - 0.1, z0), (w + 0.2, depth + 0.2, t["header_h"]))],
            pins=pins_at(pads, top), frame_at=FRAME_MATED,
            notes=f"pin frames at the rear face of the mated {rec} (17.64 mm mated height)", **common))
    return out


# ----- power: AMASS XT30 / XT60 / XT90 / MR30, 3.5 mm bullets ------------------------------------

# Sources (30 Oct): AMASS XT catalogue (Feb 2026, china-amass.com) and the AMASS spec sheets for XT30PW-M,
# XT60PW-M, XT90PW-M, MR30PW-M and the V1.2 compiled sheets (LCSC-hosted). Wire connectors stand on their mating
# face at the top with the solder cups below z=0 (as KiCad's XT60 vertical model). length: body along the mating
# axis per MPN; cup: solder cup behind the body. PCB right-angle parts: h height above the PCB, w width, face:
# pin row -> mating face, rear: pin row -> back; mate_len: the mated wire connector's cup mouths beyond the face
# (XT60 from the 27.60 mm mated length; the others scaled from it, estimates).
POWER = {
    "XT30": dict(pitch=5.0, n=2, w=10.2, d=5.2, cup=3.0, awg="18-16",
                 length={"XT30U-M": 13.7, "XT30U-F": 12.4},
                 wire=("XT30U-M", "XT30U-F"), pw=("XT30PW-M", "XT30PW-F"),
                 pwdim={"XT30PW-M": dict(h=5.2, w=9.9, face=13.05, rear=0.9, mate_len=5.9),
                        "XT30PW-F": dict(h=5.15, w=10.2, face=14.0, rear=0.9, mate_len=6.3)},
                 vfp="Connector_AMASS:AMASS_{mpn}_1x02_P5.0mm_Vertical",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P2.50mm_Horizontal", note="rated on 16 AWG"),
    "XT60": dict(pitch=7.2, n=2, w=15.75, d=8.1, cup=4.5, awg="14-12",
                 length={"XT60-M": 20.5, "XT60-F": 20.2},
                 wire=("XT60-M", "XT60-F"), pw=("XT60PW-M", "XT60PW-F"),
                 pwdim={"XT60PW-M": dict(h=8.4, w=15.5, face=14.7, rear=3.5, mate_len=9.4),
                        "XT60PW-F": dict(h=8.45, w=15.9, face=13.75, rear=3.5, mate_len=9.5)},
                 vfp="Connector_AMASS:AMASS_{mpn}_1x02_P7.20mm_Vertical",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P7.20mm_Horizontal", note="rated on 12 AWG"),
    "XT90": dict(pitch=11.0, n=2, w=21.0, d=10.3, cup=4.5, awg="10-8",
                 length={"XT90H-M": 30.7, "XT90H-F": 29.7},
                 wire=("XT90H-M", "XT90H-F"), pw=("XT90PW-M",),
                 pwdim={"XT90PW-M": dict(h=10.2, w=20.8, face=24.6, rear=2.51, mate_len=13.0)}, vfp="",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x02_P10.90mm_Horizontal", note="rated on 10 AWG"),
    "MR30": dict(pitch=3.5, n=3, w=11.9, d=4.9, cup=2.0, awg="18-16",
                 length={"MR30-M": 13.4, "MR30-F": 12.4},
                 wire=("MR30-M", "MR30-F"), pw=("MR30PW-M", "MR30PW-FB"),
                 pwdim={"MR30PW-M": dict(h=4.9, w=11.5, face=13.05, rear=0.9, mate_len=5.5),
                        "MR30PW-FB": dict(h=4.9, w=11.9, face=14.0, rear=0.9, mate_len=5.9)}, vfp="",
                 pwfp="Connector_AMASS:AMASS_{mpn}_1x03_P3.50mm_Horizontal", note="rated on 16 AWG"),
}


def _wire_power(key: str, t: dict, mpn: str, mates: list) -> Entry:
    gmin, gmax = awg(t["awg"])
    fp = t["vfp"].format(mpn=mpn) if t["vfp"] else ""
    pads = footprint_pads(fp) if fp else grid_pads(t["n"], 1, t["pitch"])
    model = footprint_model(fp) if fp else ""
    cx, cy = centroid((x, y) for _n, x, y in pads)
    male = mpn.endswith("-M")
    body = [box_about(cx, cy, t["w"], t["d"], 0.0, t["length"][mpn])]
    body += [("cyl", (x, y, -t["cup"]), min(t["d"] * 0.32, t["pitch"] * 0.3), t["cup"], (0, 0, 1))
             for _n, x, y in pads]
    return Entry(
        mpn, family=key, role="Plug" if male else "Receptacle", manufacturer="AMASS", positions=t["n"],
        pitch=t["pitch"], mating=mates, gauge_min=gmin, gauge_max=gmax, footprint=fp, model=model,
        description=f"Connector, AMASS {key} {'male' if male else 'female'}, solder cup ({mpn})",
        body=body, pins=pins_at(pads, -t["cup"], (0, 0, -1)), colour=YELLOW,
        notes=f"solder cup: no crimp terminal; the frame is at the cup mouth, +Z down the wire; {t['note']}")


def _pcb_power(key: str, t: dict, mpn: str, mates: list) -> Entry:
    fp = t["pwfp"].format(mpn=mpn)
    pads = footprint_pads(fp)
    dim = t["pwdim"][mpn]
    # The right-angle body runs from the pads towards the board edge: +y in the part (KiCad -y), the side of
    # the footprint's locating pegs.
    xs = [x for _n, x, _y in pads]
    cx = (min(xs) + max(xs)) / 2
    body = ([("box", (cx - dim["w"] / 2, -dim["rear"], 0.0), (dim["w"], dim["face"] + dim["rear"], dim["h"]))]
            + stubs(pads, r=0.6))
    exit_y = dim["face"] + dim["mate_len"]
    return Entry(
        mpn, family=key, role="Header", manufacturer="AMASS", positions=t["n"], pitch=t["pitch"], mating=mates,
        footprint=fp, model=footprint_model(fp), colour=YELLOW,
        description=f"Connector, AMASS {key} PCB right angle {'male' if '-M' in mpn else 'female'} ({mpn})",
        body=body, pins=[Pin(num, (x, exit_y, dim["h"] / 2), (0, 1, 0)) for num, x, _y in pads],
        frame_at=FRAME_MATED, notes=f"pin frames at the solder cups of the mated {mates[0]}")


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

# Sources (30 Oct): Deutsch industrial catalogue (size 16 contacts 0460-202-16141 / 0462-201-16141, 20-16 AWG;
# wedgelocks WnP for DT04, WnS for DT06); TE drawing C-2286003 (DT06-2S: 15.0 W x 15.8 H x 28.8 L; one-piece rear
# grommet and front seal inside the housing, so NO per-wire seal); TE part pages: contact spacing 5.46 (2-way),
# 4.45-4.47 (4- and 12-way), 3-way triangular, 12-way two rows. The receptacle's outline, the 3-way triangle size
# and the numbering (1..k along row 1, back along row 2) are estimates.
DT = dict(family="Deutsch DT", counts=(2, 3, 4, 6, 8, 12), awg="20-16",
          pin="0460-202-16141", socket="0462-201-16141", plug_seal="114017",
          pitch={2: 5.46, 3: 5.0, 4: 4.47, 6: 4.45, 8: 4.45, 12: 4.45},
          layout={2: (2, 1), 3: (0, 0), 4: (2, 2), 6: (3, 2), 8: (4, 2), 12: (6, 2)},
          plug_w0=9.5, plug_h0=15.8, plug_len=28.8, rec_extra=6.0, rec_len=36.0, overlap=15.0)


def dt_pads(n: int) -> list:
    p = DT["pitch"][n]
    if n == 3:  # triangular: 1 and 2 side by side, 3 below between them
        return [("1", 0.0, 0.0), ("2", p, 0.0), ("3", p / 2, -p * math.sqrt(3) / 2)]
    cols, rows = DT["layout"][n]
    return grid_pads(cols, rows, p, order="u")


def _dt() -> list:
    t = DT
    gmin, gmax = awg(t["awg"])
    out = []
    for n in t["counts"]:
        pads = dt_pads(n)
        cx, cy = centroid((x, y) for _n, x, y in pads)
        xs = [x for _n, x, _y in pads]
        ys = [y for _n, _x, y in pads]
        pw, ph = (max(xs) - min(xs)) + t["plug_w0"], (max(ys) - min(ys)) + t["plug_h0"]
        rec, plug = f"DT04-{n}P", f"DT06-{n}S"
        common = dict(family=t["family"], manufacturer="TE Connectivity (Deutsch)", positions=n,
                      pitch=t["pitch"][n], gauge_min=gmin, gauge_max=gmax, colour=GREY)
        note = (f"wire seal integral (one-piece rear grommet): no per-wire seal; {t['plug_seal']} seals an unused "
                "cavity; receptacle outline and cavity numbering estimated")
        out.append(Entry(
            rec, role="Receptacle", mating=[plug], terminal=t["pin"], accessory=f"W{n}P",
            description=f"Connector, Deutsch DT receptacle, {n}-way, pin contacts ({rec})",
            body=[box_about(cx, cy, pw + t["rec_extra"], ph + t["rec_extra"], 0.0, t["rec_len"])]
            + cavities(pads, t["rec_len"], 2.2, 2.0),
            pins=pins_at(pads, t["rec_len"]), notes=note + f"; wedgelock W{n}P", **common))
        out.append(Entry(
            plug, role="Plug", mating=[rec], terminal=t["socket"], accessory=f"W{n}S",
            description=f"Connector, Deutsch DT plug, {n}-way, socket contacts ({plug})",
            body=[box_about(cx, cy, pw, ph, t["overlap"] - t["plug_len"], t["plug_len"])],
            pins=pins_at(pads, t["overlap"] - t["plug_len"], (0, 0, -1)),
            notes=note + f"; wedgelock W{n}S; same placement as {rec} = mated", **common))
    return out


# ----- terminals, seals, wedgelocks (their own library parts, ordered by manufacturer P/N) --------

ACCESSORY_ROLES = ("Terminal", "Seal", "Wedgelock")
# mpn: (manufacturer, role, family, description, AWG range or "", size (w, d, length) of the outline)
ACCESSORIES = {
    "SXH-001T-P0.6": ("JST", "Terminal", "JST-XH", "Crimp terminal, JST XH, female", "28-22", (1.6, 1.6, 10.9)),
    "SPH-002T-P0.5S": ("JST", "Terminal", "JST-PH", "Crimp terminal, JST PH, female", "30-24", (1.3, 1.3, 8.6)),
    "SSHL-002T-P0.2": ("JST", "Terminal", "JST-GH", "Crimp terminal, JST GH, female", "30-26", (0.8, 0.8, 4.8)),
    "SSH-003T-P0.2-H": ("JST", "Terminal", "JST-SH", "Crimp terminal, JST SH, female", "32-28", (0.8, 0.8, 3.9)),
    "48235-000LF": ("Amphenol FCI", "Terminal", "Dupont 2.54", "Crimp terminal, Mini-PV (Dupont) 2.54 mm, female",
                    "26-22", (1.1, 1.1, 14.5)),
    "43030-0007": ("Molex", "Terminal", "Molex Micro-Fit 3.0", "Crimp terminal, Molex Micro-Fit 3.0, female",
                   "24-20", (1.8, 1.8, 13.0)),
    "43031-0007": ("Molex", "Terminal", "Molex Micro-Fit 3.0", "Crimp terminal, Molex Micro-Fit 3.0, male",
                   "24-20", (1.8, 1.8, 15.0)),
    "0460-202-16141": ("TE Connectivity (Deutsch)", "Terminal", "Deutsch DT", "Crimp contact, Deutsch size 16, pin",
                       "20-16", (2.4, 2.4, 25.0)),
    "0462-201-16141": ("TE Connectivity (Deutsch)", "Terminal", "Deutsch DT", "Crimp contact, Deutsch size 16, socket",
                       "20-16", (2.6, 2.6, 24.0)),
    "114017": ("TE Connectivity (Deutsch)", "Seal", "Deutsch DT", "Sealing plug, Deutsch size 16 cavity", "",
               (2.5, 2.5, 10.0)),
}
for _n in (2, 3, 4, 6, 8, 12):
    ACCESSORIES[f"W{_n}P"] = ("TE Connectivity (Deutsch)", "Wedgelock", "Deutsch DT",
                              f"Wedgelock, Deutsch DT {_n}-way receptacle", "", (6.0 + 2.5 * _n, 4.0, 3.0))
    ACCESSORIES[f"W{_n}S"] = ("TE Connectivity (Deutsch)", "Wedgelock", "Deutsch DT",
                              f"Wedgelock, Deutsch DT {_n}-way plug", "", (5.0 + 2.5 * _n, 3.5, 3.0))


def _accessories() -> list:
    out = []
    for mpn, (mfr, role, family, text, gauge, (w, d, length)) in ACCESSORIES.items():
        gmin, gmax = awg(gauge) if gauge else (0, 0)
        rng = f", {gmin}-{gmax} AWG" if gauge else ""
        body = ([("cyl", (0, 0, 0), w / 2, length, (0, 0, 1))] if role == "Seal"
                else [("box", (-w / 2, -d / 2, 0.0), (w, d, length))])
        out.append(Entry(mpn, family=family, role=role, manufacturer=mfr, description=f"{text}{rng} ({mpn})",
                         positions=0, pitch=0.0, gauge_min=gmin, gauge_max=gmax, body=body,
                         colour=GOLD if role == "Terminal" else (GREY if role == "Wedgelock" else (0.2, 0.5, 0.25)),
                         notes="ordered by its manufacturer P/N; connector pins name it in TerminalPN/SealPN"))
    return out


def is_accessory(entry) -> bool:
    return entry.role in ACCESSORY_ROLES


def _fill_manufacturers(entries) -> None:
    for e in entries:
        e.terminal_mfr = ACCESSORIES[e.terminal][0] if e.terminal in ACCESSORIES else ""
        e.seal_mfr = ACCESSORIES[e.seal][0] if e.seal in ACCESSORIES else ""
        e.accessory_mfr = ACCESSORIES[e.accessory][0] if e.accessory in ACCESSORIES else ""


# ----- the catalogue ----------------------------------------------------------------------------

FAMILY_ORDER = ("JST-XH", "JST-PH", "JST-GH", "JST-SH", "Dupont 2.54", "Molex Micro-Fit 3.0", "XT30", "XT60",
                "XT90", "MR30", "Bullet 3.5 mm", "Deutsch DT")


def safe_name(mpn: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", mpn).strip("_")


def catalog() -> list:
    """Every connector of the starter library as :class:`Entry` records (needs KiCad's footprints)."""
    if not kicad_available():
        raise KiCadMissing(f"KiCad footprints not found in {footprints_dir()}")
    entries = _accessories()
    for key in JST:
        entries += _jst(key)
    entries += _dupont() + _microfit() + _power() + _bullets() + _dt()
    _fill_manufacturers(entries)
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
        for kind, mpn in (("terminal", e.terminal), ("seal", e.seal), ("accessory", e.accessory)):
            if mpn and (mpn not in by_mpn or not is_accessory(by_mpn[mpn])):
                problems.append(f"{e.mpn}: {kind} {mpn} is not a library part")
        if is_accessory(e):
            if e.pins or e.mating:
                problems.append(f"{e.mpn}: a {e.role.lower()} has no pins or mates")
            continue
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
    """Per family: connector parts, from KiCad, parametric, pin-count range, and the number of
    terminals/seals/wedgelocks (for the Built chapter)."""
    rows = {}
    for e in entries:
        row = rows.setdefault(e.family, {"family": e.family, "parts": 0, "kicad": 0, "parametric": 0,
                                         "positions": set(), "accessories": 0})
        if is_accessory(e):
            row["accessories"] += 1
            continue
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
               "terminal_mfr", "terminal", "gauge_min", "gauge_max", "seal_mfr", "seal", "accessory_mfr", "accessory",
               "source", "model", "footprint", "frame_at")
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
        if App.GuiUp and getattr(body, "ViewObject", None) is not None:
            body.ViewObject.ShapeColor = tuple(entry.colour)  # saved in GuiDocument.xml (GUI builds only)
        if is_accessory(entry):
            _string(part, "AccessoryKind", entry.role, "Terminal, seal or wedgelock")
            _string(part, "ConnectorFamily", entry.family, "Connector family it belongs to")
            if entry.gauge_min:
                _string(part, "WireGauge", f"{entry.gauge_min}-{entry.gauge_max} AWG", "Wire range")
        else:
            accessories = ([f"{entry.accessory}|{entry.accessory_mfr}||1|{ACCESSORIES[entry.accessory][1]}"]
                           if entry.accessory else [])
            try:
                bp.set_connector(part, family=entry.family, mating_pn="; ".join(entry.mating), role=entry.role,
                                 frame_at=entry.frame_at, accessories=accessories)
            except TypeError:  # a ports module older than v1.2
                bp.set_connector(part, family=entry.family, mating_pn="; ".join(entry.mating), role=entry.role,
                                 frame_at=entry.frame_at)
            xdir = _x_dir(entry)
            for pin in entry.pins:
                placement = (pin.pos, pin.dir, xdir) if xdir else (pin.pos, pin.dir)
                bp.make_pin(part, pin.name, placement, GaugeMin=entry.gauge_min, GaugeMax=entry.gauge_max,
                            TerminalPN=entry.terminal, TerminalMfr=entry.terminal_mfr, SealPN=entry.seal,
                            SealMfr=entry.seal_mfr)
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


def fill_items(path: str, items: dict) -> int:
    """Write the Standard Library numbers of the terminals, seals and accessories (``items``: MPN ->
    vault P/N) into the connector file ``path``: the pins' ``TerminalItem``/``SealItem`` and the STD
    field of ``Accessories``. Saves when something changed; returns how many values were set. FreeCAD
    only (the vault import's writer calls it on the staged copy)."""
    import FreeCAD as App  # noqa: PLC0415

    try:
        doc = App.openDocument(path, hidden=True)
    except TypeError:
        doc = App.openDocument(path)
    changed = 0
    try:
        for obj in doc.Objects:
            if getattr(obj, "BWFrame", "") == "Pin":
                for pn_prop, item_prop in (("TerminalPN", "TerminalItem"), ("SealPN", "SealItem")):
                    want = items.get(getattr(obj, pn_prop, ""), "")
                    if item_prop in obj.PropertiesList and want and getattr(obj, item_prop) != want:
                        setattr(obj, item_prop, want)
                        changed += 1
            if "Accessories" in getattr(obj, "PropertiesList", []):
                rows = []
                for row in obj.Accessories:
                    fields = (row.split("|") + [""] * 5)[:5]
                    if items.get(fields[0]) and fields[2] != items[fields[0]]:
                        fields[2] = items[fields[0]]
                        changed += 1
                    rows.append("|".join(fields))
                if rows != list(obj.Accessories):
                    obj.Accessories = rows
        if changed:
            doc.recompute()
            doc.save()
    finally:
        App.closeDocument(doc.Name)
    return changed


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


def restore_view_data(original: str, staged: str) -> bool:
    """Put the original file's view data (GuiDocument.xml and the files it names, the thumbnail) back
    into ``staged`` when a headless save (FreeCADCmd numbering the copy) dropped it, so the part keeps
    its colours. The writers add no objects, so the object names GuiDocument.xml refers to still hold.
    FreeCAD-free; returns True when something was restored."""
    import zipfile  # noqa: PLC0415

    with zipfile.ZipFile(original) as src:
        names = set(src.namelist())
        if "GuiDocument.xml" not in names:
            return False
        with zipfile.ZipFile(staged) as dst:
            have = set(dst.namelist())
            if "GuiDocument.xml" in have:
                return False
            entries = [(info, dst.read(info.filename)) for info in dst.infolist()]
        doc_xml = src.read("Document.xml")
        gui = [n for n in sorted(names - have) if n not in ("Document.xml",) and not n.endswith(".brp")
               and not n.endswith(".Shape.brp") and (n == "GuiDocument.xml" or n.startswith("thumbnails/")
               or doc_xml.find(n.encode()) < 0)]
        tmp = staged + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info, data in entries:
                out.writestr(info, data)
            for name in gui:
                out.writestr(name, src.read(name))
    os.replace(tmp, staged)
    return True


def _accessories_first(entries) -> list:
    return [e for e in entries if is_accessory(e)] + [e for e in entries if not is_accessory(e)]


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
    for entry in _accessories_first(entries):
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


def import_library(vault, entries, folder: str, program: str = "", writer=None, progress=None,
                   item_writer=None) -> list:
    """Check every built part into ``vault`` as a new ``cots`` item (vault_setup.new_item: the number
    is previewed, the file copied to staging, ``writer(path, part_number)`` writes the number into
    the copy under FreeCAD, then the number is taken and the item checked in as WIP 01). Parts whose
    description is already an item are skipped, so a second run adds only what is new. The library
    folder is only read. Terminals, seals and wedgelocks go first; then ``item_writer(path, items)``
    (``items``: their MPN -> STD number, also those already in the vault) writes the numbers into each
    connector's pins (:func:`fill_items`). Returns the :class:`ImportPlan` list with the numbers taken."""
    from bruceworx import vault_setup  # noqa: PLC0415

    have = _imported_descriptions(vault)
    items = {}  # accessory MPN -> its STD number
    plans = []
    ordered = _accessories_first(entries)
    for done, entry in enumerate(ordered, 1):
        path = os.path.join(folder, entry.file)
        plan = ImportPlan("", entry.mpn, entry.description, path)
        key = entry.description.strip().lower()
        if key in have:
            plan.skip = f"already in the vault as {have[key]}"
            if is_accessory(entry):
                items[entry.mpn] = have[key]
        elif not os.path.isfile(path):
            plan.skip = "part file not built"
        else:
            def make_file(staged, number, _path=path, _accessory=is_accessory(entry)):
                shutil.copyfile(_path, staged)
                os.chmod(staged, 0o644)
                if writer is not None:
                    writer(staged, number)
                if item_writer is not None and not _accessory and items:
                    item_writer(staged, dict(items))
                restore_view_data(_path, staged)

            plan.part_number = vault_setup.new_item(vault, ITEM_TYPE, entry.description, make_file, program)
            have[key] = plan.part_number
            if is_accessory(entry):
                items[entry.mpn] = plan.part_number
        plans.append(plan)
        if progress is not None:
            progress(done, len(ordered), plan)
    return plans


def write_numbers(plans, entries, path: str) -> str:
    """The ordering list after an import (or a dry run): vault P/N, manufacturer, MPN, description,
    role, one row per part. Ordering uses Manufacturer + MPN; the STD number is for finding it."""
    by_mpn = {e.mpn: e for e in entries}
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("part_number", "manufacturer", "mpn", "description", "role", "status"))
        for plan in plans:
            e = by_mpn[plan.mpn]
            existing = plan.skip.rsplit(" ", 1)[-1] if plan.skip.startswith("already in the vault as ") else ""
            writer.writerow((plan.part_number or existing, e.manufacturer, e.mpn, e.description, e.role,
                             plan.skip or "new"))
    return path


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
            print(f"{row['family']:22} {row['parts']:4} parts  kicad {row['kicad']:3}  parametric {row['parametric']:3}  accessories {row['accessories']:2}"
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

        on = freecad_io.freecad_available()
        plans = import_library(vault, entries, folder, args.program,
                               writer=freecad_io.write_part_number if on else None,
                               item_writer=fill_items if on else None)
    report = write_numbers(plans, entries, os.path.join(
        folder, "vault_numbers_plan.csv" if args.dry_run else "vault_numbers.csv"))
    for plan in plans:
        print(f"{plan.part_number or '-':22} {plan.mpn:22} {plan.skip or plan.description}")
    print(f"ordering list (vault P/N, manufacturer, MPN): {report}")
    print(f"{sum(1 for p in plans if p.part_number)} of {len(plans)} parts "
          f"{'would be imported' if args.dry_run else 'imported'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
