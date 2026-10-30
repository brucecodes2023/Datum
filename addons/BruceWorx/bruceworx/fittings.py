# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fittings from size tables: push-to-connect, hose barbs, NPT/BSPP threaded and compression
(BW-ROU-05, 30 Oct 2026). No FreeCAD import.

A fitting is described by ``(family, style, size, thread)``; :func:`layout` turns that into
simplified, dimensionally sensible geometry as primitives (cylinders, cones, hex prisms, spheres
and the bores cut from them) in the fitting's own frame, plus its **ports**: where a tube, hose or
thread enters, the outward direction (+Z of the port frame), the size and the engagement (tube or
hose inside or over the fitting). The FreeCAD side (``bruceworx_fittings.py``) builds the solid
and one port frame per port.

Sources (the Built chapter of ROU-05 has them in full):

* NPT: ASME B1.20.1, pipe OD at the thread, threads per inch and the effective thread length L2;
  the taper 1 in 16 on the diameter. Tap drills from the usual tap-drill charts.
* BSPP: ISO 228-1, major diameter and threads per inch. Male thread lengths are typical catalogue
  values (approximate).
* Push-to-connect: body diameters and lengths in the style of the SMC KQ2 / Festo QS catalogue
  tables, rounded; the insertion depth is approximated as OD + 11 mm.
* Compression: nut hexes as in Swagelok-style catalogues (7/16, 9/16, 11/16, 7/8 in for 1/8 to
  1/2 in tube), body hexes and insertion depths approximate.
* Barbs: no standard governs barbs; the crest is about 20 % over the hose ID, the bore 60 % of it.

Every number is for layout and routing (where the tube enters, how much straight it needs), not
for manufacture: check the vendor's drawing before release.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

INCH = 25.4

# ----------------------------------------------------------------------------------------------
# Threads.


@dataclass(frozen=True)
class Thread:
    name: str  # "1/8 NPT", "1/4 BSPP", "M5"
    standard: str  # "NPT", "BSPP", "Metric thread"
    major: float  # mm: NPT the pipe OD at the thread (B1.20.1 "D"), BSPP the major diameter
    tpi: float  # threads per inch (metric: 25.4 / pitch)
    length: float  # mm: male thread length (NPT: L2, the effective thread)
    tap: float  # mm: tap drill of the female thread (the bore drawn)
    hex: float  # mm across flats of a male fitting's hex on this thread

    @property
    def pitch(self):
        return INCH / self.tpi

    @property
    def tapered(self):
        return self.standard == "NPT"


THREADS = {t.name: t for t in (
    Thread("M5", "Metric thread", 5.0, INCH / 0.8, 5.0, 4.2, 8.0),
    # ASME B1.20.1: D = 0.405 / 0.540 / 0.675 / 0.840 in; 27 / 18 / 18 / 14 TPI;
    # L2 = 0.2639 / 0.4018 / 0.4078 / 0.5337 in. Tap drills 11/32, 7/16, 37/64, 23/32 in.
    Thread("1/8 NPT", "NPT", 0.405 * INCH, 27, 0.2639 * INCH, 11 / 32 * INCH, 12.0),
    Thread("1/4 NPT", "NPT", 0.540 * INCH, 18, 0.4018 * INCH, 7 / 16 * INCH, 17.0),
    Thread("3/8 NPT", "NPT", 0.675 * INCH, 18, 0.4078 * INCH, 37 / 64 * INCH, 21.0),
    Thread("1/2 NPT", "NPT", 0.840 * INCH, 14, 0.5337 * INCH, 23 / 32 * INCH, 26.0),
    # ISO 228-1: major 9.728 / 13.157 / 16.662 / 20.955 mm; 28 / 19 / 19 / 14 TPI.
    Thread("1/8 BSPP", "BSPP", 9.728, 28, 8.0, 8.8, 12.0),
    Thread("1/4 BSPP", "BSPP", 13.157, 19, 11.0, 11.8, 17.0),
    Thread("3/8 BSPP", "BSPP", 16.662, 19, 12.0, 15.25, 21.0),
    Thread("1/2 BSPP", "BSPP", 20.955, 14, 15.0, 19.0, 26.0),
)}
PIPE_THREADS = [n for n in THREADS if n != "M5"]


def thread(name):
    try:
        return THREADS[name]
    except KeyError:
        raise ValueError(f"unknown thread {name!r}") from None


def npt_taper_diameter(t, distance):
    """Diameter of an NPT thread ``distance`` mm from its large end (taper 1 in 16 on the diameter)."""
    return t.major - distance / 16.0 if t.tapered else t.major


# ----------------------------------------------------------------------------------------------
# Size tables.


@dataclass(frozen=True)
class PtcSize:
    """Push-to-connect, by tube OD."""

    name: str
    od: float
    body: float  # body (release collar housing) diameter
    length: float  # hex top to the socket mouth (straight male connector)
    arm: float  # elbow / tee: axis to the mouth
    thread: str  # the usual thread for this tube


PTC_SIZES = {s.name: s for s in (
    PtcSize("4 mm", 4.0, 10.4, 17.0, 17.0, "M5"),
    PtcSize("6 mm", 6.0, 12.8, 18.5, 19.0, "1/8 BSPP"),
    PtcSize("8 mm", 8.0, 15.2, 21.0, 22.0, "1/4 BSPP"),
    PtcSize("10 mm", 10.0, 18.5, 24.0, 26.0, "3/8 BSPP"),
    PtcSize("12 mm", 12.0, 20.9, 26.0, 29.0, "3/8 BSPP"),
    PtcSize("5/32 in", 5 / 32 * INCH, 10.4, 17.0, 17.0, "1/8 NPT"),
    PtcSize("1/4 in", 0.25 * INCH, 13.2, 19.0, 19.5, "1/8 NPT"),
    PtcSize("3/8 in", 0.375 * INCH, 17.8, 23.0, 25.0, "1/4 NPT"),
)}


@dataclass(frozen=True)
class BarbSize:
    """Hose barb, by hose ID."""

    name: str
    hose_id: float

    @property
    def crest(self):
        return round(1.2 * self.hose_id + 0.3, 2)

    @property
    def shank(self):
        return round(1.02 * self.hose_id, 2)

    @property
    def bore(self):
        return round(max(1.2, 0.6 * self.hose_id), 2)

    @property
    def length(self):
        """Barb length, tip to shoulder (what the hose covers)."""
        return round(max(8.0, 1.8 * self.hose_id + 2.0), 1)

    @property
    def collar(self):
        return round(self.crest + 2.5, 1)


BARB_SIZES = {s.name: s for s in (
    BarbSize("1/8 in", 0.125 * INCH), BarbSize("3/16 in", 0.1875 * INCH), BarbSize("1/4 in", 0.25 * INCH),
    BarbSize("5/16 in", 0.3125 * INCH), BarbSize("3/8 in", 0.375 * INCH), BarbSize("1/2 in", 0.5 * INCH),
    BarbSize("3 mm", 3.0), BarbSize("4 mm", 4.0), BarbSize("5 mm", 5.0), BarbSize("6 mm", 6.0),
    BarbSize("8 mm", 8.0), BarbSize("10 mm", 10.0), BarbSize("12 mm", 12.0),
)}


@dataclass(frozen=True)
class CompressionSize:
    """Compression (two-ferrule, Swagelok style), by tube OD."""

    name: str
    od: float
    nut: float  # nut hex across flats
    body: float  # body hex across flats

    @property
    def insertion(self):
        return round(0.6 * self.od + 4.0, 1)

    @property
    def nut_length(self):
        return round(0.75 * self.nut, 1)


COMPRESSION_SIZES = {s.name: s for s in (
    CompressionSize("1/8 in", 0.125 * INCH, 7 / 16 * INCH, 7 / 16 * INCH),
    CompressionSize("1/4 in", 0.25 * INCH, 9 / 16 * INCH, 0.5 * INCH),
    CompressionSize("3/8 in", 0.375 * INCH, 11 / 16 * INCH, 5 / 8 * INCH),
    CompressionSize("1/2 in", 0.5 * INCH, 7 / 8 * INCH, 13 / 16 * INCH),
    CompressionSize("4 mm", 4.0, 12.0, 12.0),
    CompressionSize("6 mm", 6.0, 14.0, 12.0),
    CompressionSize("8 mm", 8.0, 16.0, 15.0),
    CompressionSize("10 mm", 10.0, 19.0, 17.0),
    CompressionSize("12 mm", 12.0, 22.0, 20.0),
)}

# Threaded (pipe) fittings: centre to face of an elbow or tee, by thread size.
THREADED_CENTRE = {"1/8": 12.0, "1/4": 15.0, "3/8": 18.0, "1/2": 22.0}

# ----------------------------------------------------------------------------------------------
# Families.

PTC = "Push-to-connect"
BARB = "Hose barb"
THREADED = "Threaded"
COMPRESSION = "Compression"
FAMILIES = (PTC, BARB, THREADED, COMPRESSION)
STYLES = {
    PTC: ("Straight", "Elbow", "Tee", "Bulkhead"),
    BARB: ("Straight", "Elbow", "Tee"),
    THREADED: ("Elbow", "Tee", "Adapter", "Plug"),
    COMPRESSION: ("Straight union", "Elbow"),
}
NO_THREAD = "None"
PORT_TYPE = {PTC: "Push-to-connect", BARB: "Barb", COMPRESSION: "Compression"}
SHORT = {PTC: "PTC", BARB: "BARB", THREADED: "THD", COMPRESSION: "CMP"}


def sizes(family):
    """The size names of ``family`` (threaded: the thread names)."""
    if family == PTC:
        return list(PTC_SIZES)
    if family == BARB:
        return list(BARB_SIZES)
    if family == COMPRESSION:
        return list(COMPRESSION_SIZES)
    if family == THREADED:
        return list(PIPE_THREADS)
    raise ValueError(f"unknown fitting family {family!r}")


def styles(family):
    try:
        return list(STYLES[family])
    except KeyError:
        raise ValueError(f"unknown fitting family {family!r}") from None


def has_thread(family, style):
    """Whether the fitting has a male thread to choose (``thread`` in :func:`layout`)."""
    return (family == PTC and style in ("Straight", "Elbow")) or (family == BARB and style in ("Straight", "Elbow"))


def threads(family, style, size=None):
    """Thread choices: M5 only for tubes up to 6 mm; barbs may have none (a barb-to-barb)."""
    if not has_thread(family, style):
        return []
    names = list(PIPE_THREADS)
    if family == PTC and (size is None or PTC_SIZES[size].od <= 6.5):
        names = ["M5"] + names
    if family == BARB:
        names = [NO_THREAD] + names
    return names


def default_thread(family, style, size):
    if not has_thread(family, style):
        return ""
    if family == PTC:
        return PTC_SIZES[size].thread
    return NO_THREAD


# ----------------------------------------------------------------------------------------------
# Layout: primitives and ports.


@dataclass
class Port:
    name: str
    type: str  # the port frame's PortType
    size: str
    tube_od: float
    origin: tuple
    direction: tuple
    engagement: float = 0.0


@dataclass
class Layout:
    family: str
    style: str
    size: str
    thread: str
    solids: list = field(default_factory=list)  # ("cyl"|"cone"|"hex"|"sphere", ...)
    bores: list = field(default_factory=list)  # ("cyl"|"cone", ...) cut from the solids
    ports: list = field(default_factory=list)
    description: str = ""
    code: str = ""

    def port(self, name):
        return next(p for p in self.ports if p.name == name)

    @property
    def mount(self):
        """The port the fitting is placed by: its thread port, else port 1."""
        return next((p for p in self.ports if p.type in ("NPT", "BSPP", "Metric thread")), self.ports[0])


def _v(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


Z, NZ, X, NX, Y = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _male_thread(lay, t, base, direction, name):
    """A male thread from ``base`` along ``direction`` (NPT tapered), its bore and its port."""
    if t.tapered:
        lay.solids.append(("cone", base, direction, t.major, npt_taper_diameter(t, t.length), t.length))
    else:
        lay.solids.append(("cyl", base, direction, t.major, t.length))
    end = _add(base, _v(direction, t.length))
    lay.ports.append(Port(name, t.standard, t.name, t.major, end, direction, t.length))


def _female_thread(lay, t, mouth, direction, depth_body, name):
    """A female thread opening at ``mouth`` facing ``direction`` (bored to the tap drill)."""
    lay.bores.append(("cyl", mouth, _v(direction, -1.0), t.tap, min(depth_body, t.length + 2.0)))
    lay.ports.append(Port(name, t.standard, t.name, t.major, mouth, direction, t.length))


def _ptc_socket(lay, s, mouth, direction, name):
    """A push-to-connect socket: release collar at the mouth, tube bore to the insertion depth."""
    insertion = s.od + 11.0
    collar = (s.body * 0.86, 3.0)
    lay.solids.append(("cyl", _add(mouth, _v(direction, -collar[1])), direction, collar[0], collar[1] + 1.5))
    lay.bores.append(("cyl", _add(mouth, _v(direction, 1.5)), _v(direction, -1.0), s.od, insertion + 1.5))
    lay.ports.append(Port(name, PORT_TYPE[PTC], s.name, s.od, _add(mouth, _v(direction, 1.5)), direction, insertion))


def _barb(lay, b, root, direction, name):
    """A barb from its shoulder at ``root`` along ``direction``: shank and three ridges."""
    length = b.length
    lay.solids.append(("cyl", root, direction, b.shank, length))
    ridge = length / 3.0
    for k in range(3):
        start = _add(root, _v(direction, length - (k + 1) * ridge))
        lay.solids.append(("cone", start, direction, b.crest, b.shank, ridge))
    tip = _add(root, _v(direction, length))
    lay.bores.append(("cyl", _add(tip, _v(direction, 0.5)), _v(direction, -1.0), b.bore, length + 0.5))
    lay.ports.append(Port(name, PORT_TYPE[BARB], b.name, b.hose_id, tip, direction, length))


def _compression_end(lay, c, face, direction, name):
    """A compression nut ending at ``face`` and the tube bore behind it."""
    lay.solids.append(("hex", _add(face, _v(direction, -c.nut_length)), direction, c.nut, c.nut_length))
    lay.bores.append(("cyl", _add(face, _v(direction, 0.5)), _v(direction, -1.0), c.od, c.insertion + 0.5))
    lay.ports.append(Port(name, PORT_TYPE[COMPRESSION], c.name, c.od, face, direction, c.insertion))


def layout(family, style, size, thread_name=None):
    """The geometry and ports of a fitting. ``thread_name`` for the styles with a male thread
    (default: the size's usual thread; a barb's default is none, a barb-to-barb connector)."""
    if style not in styles(family):
        raise ValueError(f"{family} has no style {style!r} (choose {', '.join(styles(family))})")
    if size not in sizes(family):
        raise ValueError(f"{family} has no size {size!r}")
    if has_thread(family, style):
        thread_name = thread_name or default_thread(family, style, size)
        if thread_name not in threads(family, style, size):
            raise ValueError(f"{family} {size} takes no thread {thread_name!r}")
    else:
        thread_name = ""
    lay = Layout(family, style, size, thread_name)
    t = THREADS.get(thread_name) if thread_name and thread_name != NO_THREAD else None
    if family == PTC:
        _ptc(lay, PTC_SIZES[size], t)
    elif family == BARB:
        _barb_fitting(lay, BARB_SIZES[size], t)
    elif family == THREADED:
        _threaded(lay, THREADS[size])
    else:
        _compression(lay, COMPRESSION_SIZES[size])
    lay.description = describe(family, style, size, thread_name)
    lay.code = type_code(family, style, size, thread_name)
    return lay


def _ptc(lay, s, t):
    if lay.style in ("Straight", "Elbow"):
        _male_thread(lay, t, (0.0, 0.0, 0.0), NZ, "2")
        hex_h = round(max(4.0, 0.35 * t.hex), 1)
        lay.solids.append(("hex", (0.0, 0.0, 0.0), Z, max(t.hex, s.body + 1.0), hex_h))
        lay.bores.append(("cyl", (0.0, 0.0, -t.length - 0.5), Z, max(1.5, min(t.tap, s.od) * 0.7), t.length + hex_h + 1.0))
        if lay.style == "Straight":
            lay.solids.append(("cyl", (0.0, 0.0, hex_h), Z, s.body, s.length - hex_h - 3.0))
            _ptc_socket(lay, s, (0.0, 0.0, s.length), Z, "1")
        else:
            zc = hex_h + s.body / 2.0
            lay.solids.append(("cyl", (0.0, 0.0, hex_h), Z, s.body, s.body))
            lay.solids.append(("cyl", (0.0, 0.0, zc), X, s.body, s.arm - 3.0))
            lay.bores.append(("cyl", (0.0, 0.0, zc), X, s.od * 0.7, s.arm))
            lay.bores.append(("cyl", (0.0, 0.0, 0.0), Z, s.od * 0.7, zc))
            _ptc_socket(lay, s, (s.arm, 0.0, zc), X, "1")
        lay.ports.sort(key=lambda p: p.name)
        return
    if lay.style == "Tee":
        lay.solids.append(("cyl", (0.0, 0.0, -s.body / 2.0), Z, s.body, s.body))
        for name, d in (("1", NX), ("2", X), ("3", Y)):
            lay.solids.append(("cyl", (0.0, 0.0, 0.0), d, s.body, s.arm - 3.0))
            lay.bores.append(("cyl", (0.0, 0.0, 0.0), d, s.od * 0.7, s.arm))
            _ptc_socket(lay, s, _v(d, s.arm), d, name)
        return
    # Bulkhead union: a threaded body through a panel, a nut, a socket each end.
    half = s.length
    panel_thread = s.body + 4.0
    lay.solids.append(("cyl", (0.0, 0.0, -half + 3.0), Z, panel_thread, 2 * half - 6.0))
    lay.solids.append(("hex", (0.0, 0.0, -half * 0.35), Z, panel_thread + 5.0, 4.0))
    lay.solids.append(("hex", (0.0, 0.0, half * 0.2), Z, panel_thread + 5.0, 4.0))
    lay.bores.append(("cyl", (0.0, 0.0, -half), Z, s.od * 0.7, 2 * half))
    _ptc_socket(lay, s, (0.0, 0.0, -half), NZ, "1")
    _ptc_socket(lay, s, (0.0, 0.0, half), Z, "2")


def _barb_fitting(lay, b, t):
    if lay.style in ("Straight", "Elbow") and t is not None:
        _male_thread(lay, t, (0.0, 0.0, 0.0), NZ, "2")
        hex_h = round(max(4.0, 0.35 * t.hex), 1)
        lay.solids.append(("hex", (0.0, 0.0, 0.0), Z, max(t.hex, b.collar), hex_h))
        lay.bores.append(("cyl", (0.0, 0.0, -t.length - 0.5), Z, max(1.2, min(t.tap * 0.7, b.bore)), t.length + hex_h + 1.0))
        if lay.style == "Straight":
            _barb(lay, b, (0.0, 0.0, hex_h), Z, "1")
        else:
            body = max(b.collar, t.hex * 0.8)
            zc = hex_h + body / 2.0
            lay.solids.append(("cyl", (0.0, 0.0, hex_h), Z, body, body))
            lay.solids.append(("cyl", (0.0, 0.0, zc), X, body, body / 2.0 + 1.0))
            lay.bores.append(("cyl", (0.0, 0.0, 0.0), Z, b.bore, zc))
            lay.bores.append(("cyl", (0.0, 0.0, zc), X, b.bore, body / 2.0 + 1.0))
            _barb(lay, b, (body / 2.0 + 1.0, 0.0, zc), X, "1")
        lay.ports.sort(key=lambda p: p.name)
        return
    # Barb-to-barb: straight, elbow or tee around a central collar.
    dirs = {"Straight": (("1", NZ), ("2", Z)), "Elbow": (("1", NX), ("2", Z)),
            "Tee": (("1", NX), ("2", X), ("3", Z))}[lay.style]
    if lay.style == "Straight":
        lay.solids.append(("cyl", (0.0, 0.0, -1.5), Z, b.collar, 3.0))
        reach = 1.5
    else:
        lay.solids.append(("sphere", (0.0, 0.0, 0.0), b.collar))
        reach = round(b.collar / 2.0 + 1.0, 2)  # past the sphere: no tangent faces for the fuse
        for _name, d in dirs:
            lay.solids.append(("cyl", (0.0, 0.0, 0.0), d, round(0.85 * b.collar, 2), reach))
            lay.bores.append(("cyl", (0.0, 0.0, 0.0), d, b.bore, reach))
    for name, d in dirs:
        _barb(lay, b, _v(d, reach), d, name)


def _threaded(lay, t):
    key = t.name.split()[0]
    centre = THREADED_CENTRE[key]
    boss = round(t.major + 5.0, 1)
    if lay.style in ("Elbow", "Tee"):
        dirs = (("1", NX), ("2", Z)) if lay.style == "Elbow" else (("1", NX), ("2", X), ("3", Z))
        lay.solids.append(("sphere", (0.0, 0.0, 0.0), boss))
        for name, d in dirs:
            lay.solids.append(("cyl", (0.0, 0.0, 0.0), d, boss, centre))
            lay.bores.append(("cyl", (0.0, 0.0, 0.0), d, t.tap * 0.75, centre))
            _female_thread(lay, t, _v(d, centre), d, centre, name)
        return
    hex_h = round(max(4.0, 0.45 * t.hex), 1)
    if lay.style == "Adapter":  # hex nipple, male both ends
        lay.solids.append(("hex", (0.0, 0.0, -hex_h / 2.0), Z, t.hex, hex_h))
        _male_thread(lay, t, (0.0, 0.0, -hex_h / 2.0), NZ, "1")
        _male_thread(lay, t, (0.0, 0.0, hex_h / 2.0), Z, "2")
        lay.bores.append(("cyl", (0.0, 0.0, -hex_h / 2.0 - t.length), Z, t.tap * 0.75, hex_h + 2 * t.length))
        return
    # Plug: hex head, male thread down.
    lay.solids.append(("hex", (0.0, 0.0, 0.0), Z, t.hex, hex_h))
    _male_thread(lay, t, (0.0, 0.0, 0.0), NZ, "1")


def _compression(lay, c):
    if lay.style == "Straight union":
        body_l = round(0.6 * c.body + 2.0, 1)
        lay.solids.append(("hex", (0.0, 0.0, -body_l / 2.0), Z, c.body, body_l))
        face = body_l / 2.0 + c.nut_length
        lay.bores.append(("cyl", (0.0, 0.0, -face), Z, c.od * 0.75, 2 * face))
        _compression_end(lay, c, (0.0, 0.0, -face), NZ, "1")
        _compression_end(lay, c, (0.0, 0.0, face), Z, "2")
        return
    arm = round(c.body / 2.0 + 3.0, 1)
    lay.solids.append(("sphere", (0.0, 0.0, 0.0), c.body))
    for name, d in (("1", NX), ("2", Z)):
        lay.solids.append(("cyl", (0.0, 0.0, 0.0), d, c.body * 0.9, arm))
        lay.bores.append(("cyl", (0.0, 0.0, 0.0), d, c.od * 0.75, arm))
        _compression_end(lay, c, _v(d, arm + c.nut_length), d, name)


# ----------------------------------------------------------------------------------------------
# Words.

STYLE_WORDS = {
    (PTC, "Straight"): "male connector", (PTC, "Elbow"): "male elbow", (PTC, "Tee"): "union tee",
    (PTC, "Bulkhead"): "bulkhead union",
    (BARB, "Straight"): "straight", (BARB, "Elbow"): "elbow", (BARB, "Tee"): "tee",
    (THREADED, "Elbow"): "90° elbow, female", (THREADED, "Tee"): "tee, female",
    (THREADED, "Adapter"): "hex nipple, male", (THREADED, "Plug"): "hex head plug",
    (COMPRESSION, "Straight union"): "union", (COMPRESSION, "Elbow"): "union elbow",
}


def describe(family, style, size, thread_name=""):
    """A BOM description: "Push-to-connect male elbow, 6 mm tube x 1/8 BSPP"."""
    words = STYLE_WORDS[(family, style)]
    if family == THREADED:
        return f"Pipe fitting, {words}, {size}"
    if family == BARB:
        what = f"Hose barb {words}, {size} hose ID"
        if thread_name and thread_name != NO_THREAD:
            return f"{what} x {thread_name}"
        return what + (" both ends" if style == "Straight" else "")
    what = f"{family} {words}, {size} tube"
    if thread_name and thread_name != NO_THREAD:
        what += f" x {thread_name}"
    return what


def type_code(family, style, size, thread_name=""):
    """A generic type code for P/N assignment ("PTC-ELBOW-6MM-1/8BSPP"), not a vendor's."""
    parts = [SHORT[family], style.upper().replace(" ", ""), size.upper().replace(" ", "")]
    if thread_name and thread_name != NO_THREAD:
        parts.append(thread_name.upper().replace(" ", ""))
    return "-".join(parts)


def all_fittings():
    """Every (family, style, size, thread) the tables offer (each style's default thread)."""
    out = []
    for family in FAMILIES:
        for style in styles(family):
            for size in sizes(family):
                out.append((family, style, size, default_thread(family, style, size)))
    return out
