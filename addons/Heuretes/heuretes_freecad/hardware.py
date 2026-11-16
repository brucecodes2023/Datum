# SPDX-License-Identifier: LGPL-2.1-or-later
"""Threads, holes and hardware (MOD-05), free of FreeCAD.

Three things the GUI module (``bruceworx_hardware.py`` in src/Mod/BruceWorx) builds on:

* **Threads.** ISO metric coarse threads (ISO 261 sizes, ISO 68-1 profile): major, minor and
  tap-drill diameters for a designation such as ``"M3"`` or ``"M3x0.5"``, and the callout text
  of a cosmetic thread.
* **Hole presets.** ``config/hole_presets.json`` (built in) and ``<vault>/hole_presets.json``
  (a preset of the same name replaces the built-in one): heat-set insert holes for 3D-printed
  parts (CNC Kitchen and ruthex inserts, M2-M5), tapped, clearance, counterbore and countersink
  holes. :func:`hole_properties` turns a preset into the PartDesign Hole property values, in
  the order they must be set; :func:`hole_callout` writes the drawing callout from a hole's
  values.
* **Fasteners as vault COTS items.** A fastener from the bundled Fasteners add-on has a key
  (``"ISO4762-M3x40"``) and a description (``"Socket head cap screw ISO 4762 M3x40"``). The
  vault's catalogue, ``<vault>/hardware.json``, maps each key to the vault item (item type
  ``cots``) that stands for it, so the second M3x40 placed anywhere is the same part number.
  :func:`match_description` recognises the COTS parts a vault already has from their
  description (``"Socket head cap screw M3x40 (COTS)"``, ``"Ball bearing 683ZZ ..."``).

Lengths are millimetres throughout.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field

from heuretes_freecad.config_path import config_dir

PRESETS_FILE = "hole_presets.json"
PRESETS_SCHEMA = "bruceworx.hole-presets"
CATALOGUE_FILE = "hardware.json"
CATALOGUE_FORMAT = "bruceworx.hardware"
CATALOGUE_VERSION = 1
KINDS = ("heatset", "tapped", "clearance", "counterbore", "countersink")

# Depth, counterbore and countersink symbols (ASME Y14.5 / ISO 129-1), in osifont.
DEPTH = "↧"
CBORE = "⌴"
CSINK = "⌵"
DIA = "Ø"

# --- threads ------------------------------------------------------------------------------

# ISO 261 coarse pitches, the sizes the presets and the cosmetic thread offer (mm).
METRIC_COARSE = {
    "M1.6": 0.35, "M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M3.5": 0.6, "M4": 0.7, "M5": 0.8,
    "M6": 1.0, "M8": 1.25, "M10": 1.5, "M12": 1.75, "M14": 2.0, "M16": 2.0, "M20": 2.5,
    "M24": 3.0,
}
# ISO 273 clearance holes (fine, medium, coarse), the sizes above.
CLEARANCE = {
    "M1.6": (1.7, 1.8, 2.0), "M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1),
    "M3": (3.2, 3.4, 3.6), "M3.5": (3.7, 3.9, 4.2), "M4": (4.3, 4.5, 4.8), "M5": (5.3, 5.5, 5.8),
    "M6": (6.4, 6.6, 7.0), "M8": (8.4, 9.0, 10.0), "M10": (10.5, 11.0, 12.0),
    "M12": (13.0, 13.5, 14.5), "M14": (15.0, 15.5, 16.5), "M16": (17.0, 17.5, 18.5),
    "M20": (21.0, 22.0, 24.0), "M24": (25.0, 26.0, 28.0),
}
FITS = ("Fine", "Medium", "Coarse")
_DESIGNATION = re.compile(r"^\s*M\s*(\d+(?:\.\d+)?)\s*(?:[x×]\s*(\d+(?:\.\d+)?))?\s*$", re.I)


@dataclass(frozen=True)
class Thread:
    """An ISO metric thread. ``minor`` is the internal thread's D1 (the nut's bore, what a
    drawing shows as the inner circle of a tapped hole); ``minor_external`` the bolt's d3."""

    size: str  # "M3"
    pitch: float
    major: float
    minor: float
    minor_external: float
    tap_drill: float

    @property
    def designation(self) -> str:
        """``"M3x0.5"``; ISO writes the pitch of a coarse thread only when asked, so do we."""
        return f"{self.size}x{_num(self.pitch)}"

    def callout(self, length=None, internal=True, tol_class=None) -> str:
        """``"M3x0.5-6H ↧6"`` (internal) or ``"M3x0.5-6g ↧10"`` (external)."""
        cls = tol_class or ("6H" if internal else "6g")
        text = f"{self.designation}-{cls}"
        if length:
            text += f" {DEPTH}{_num(length)}"
        return text


def thread(designation: str) -> Thread:
    """The ISO metric thread ``"M3"`` / ``"M3x0.5"`` / ``"M8x1"`` (a fine pitch is kept)."""
    match = _DESIGNATION.match(str(designation or ""))
    if not match:
        raise ValueError(f"not an ISO metric thread: {designation!r}")
    major = float(match.group(1))
    size = f"M{_num(major)}"
    pitch = float(match.group(2)) if match.group(2) else METRIC_COARSE.get(size)
    if pitch is None:
        raise ValueError(f"{size} has no coarse pitch here; give it, e.g. {size}x1")
    # ISO 68-1: H = 0.866025 P; D1 = D - 1.082532 P; d3 = d - 1.226869 P.
    return Thread(size, pitch, major, round(major - 1.082532 * pitch, 3),
                  round(major - 1.226869 * pitch, 3), round(major - pitch, 2))


def clearance(size: str, fit: str = "Medium") -> float:
    """ISO 273 clearance hole for ``size`` ("M3") and fit (Fine, Medium, Coarse)."""
    size = thread(size).size
    if size not in CLEARANCE:
        raise ValueError(f"no ISO 273 clearance for {size}")
    if fit not in FITS:
        raise ValueError(f"fit is one of {', '.join(FITS)}, not {fit!r}")
    return CLEARANCE[size][FITS.index(fit)]


def thread_size_enum(size: str, enums) -> str:
    """The Hole's ThreadSize entry for ``size``: ``"M3x0.5"`` from FreeCAD 1.1's list (older
    lists say ``"M3"``)."""
    t = thread(size)
    for wanted in (t.designation, t.size):
        for entry in enums:
            if entry == wanted:
                return entry
    for entry in enums:
        if re.match(rf"^{re.escape(t.size)}x", entry):
            return entry
    raise ValueError(f"the Hole offers no {t.size}")


# --- hole presets -------------------------------------------------------------------------


@dataclass
class Preset:
    name: str
    kind: str
    thread: str
    diameter: float | None = None  # heat-set: the printed hole
    depth: float | None = None  # hole depth; None: through all (clearance) or the user's
    thread_depth: float | None = None  # tapped
    fit: str = "Medium"
    head: str = ""  # counterbore / countersink: the Hole's cut table ("ISO 4762")
    insert_length: float | None = None
    insert_od: float | None = None
    min_wall: float | None = None
    inserts: list = field(default_factory=list)
    checked: bool = True
    layer: str = "built-in"

    @property
    def insert(self) -> str:
        """``"M3x5.7"``, the insert a heat-set hole is for."""
        if self.kind != "heatset" or not self.insert_length:
            return ""
        return f"{thread(self.thread).size}x{_num(self.insert_length)}"

    def describe(self) -> str:
        """One line for a list: what the hole is and its numbers."""
        if self.kind == "heatset":
            vendors = "; ".join(self.inserts)
            return (f"{DIA}{_num(self.diameter)} {DEPTH}{_num(self.depth)} for a {self.insert} insert"
                    + (f" ({vendors})" if vendors else ""))
        if self.kind == "tapped":
            t = thread(self.thread)
            return f"{t.callout(self.thread_depth)}, drill {DIA}{_num(t.tap_drill)} {DEPTH}{_num(self.depth)}"
        return f"{DIA}{_num(clearance(self.thread, self.fit))} {self.fit.lower()} clearance" + (
            f", {self.head} head" if self.head else "")


def _preset(raw: dict, layer: str) -> Preset:
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
        raise ValueError(f"{layer}: a preset needs a name")
    name = str(raw["name"]).strip()
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{layer}: {name}: kind is one of {', '.join(KINDS)}")
    thread(raw.get("thread"))
    preset = Preset(name=name, kind=kind, thread=str(raw["thread"]), layer=layer,
                    fit=str(raw.get("fit", "Medium")), head=str(raw.get("head", "")),
                    inserts=[str(i) for i in raw.get("inserts", [])], checked=bool(raw.get("checked", True)))
    for key in ("diameter", "depth", "thread_depth", "insert_length", "insert_od", "min_wall"):
        if raw.get(key) is not None:
            value = float(raw[key])
            if value <= 0:
                raise ValueError(f"{layer}: {name}: {key} must be above zero")
            setattr(preset, key, value)
    if kind == "heatset" and (preset.diameter is None or preset.depth is None):
        raise ValueError(f"{layer}: {name}: a heat-set hole needs diameter and depth")
    if kind == "heatset" and preset.insert_length and preset.depth < preset.insert_length:
        raise ValueError(f"{layer}: {name}: the hole is shallower than the insert")
    if kind == "tapped" and preset.thread_depth and preset.depth and preset.thread_depth > preset.depth:
        raise ValueError(f"{layer}: {name}: the thread is deeper than the hole")
    if kind in ("counterbore", "countersink") and not preset.head:
        raise ValueError(f"{layer}: {name}: name the head (the Hole's cut table, e.g. ISO 4762)")
    if kind != "heatset" and preset.fit not in FITS:
        raise ValueError(f"{layer}: {name}: fit is one of {', '.join(FITS)}")
    return preset


def _layer(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema") != PRESETS_SCHEMA:
        raise ValueError(f"{path}: not a {PRESETS_SCHEMA} file")
    return raw.get("presets", [])


def load_presets(vault_root: str | None = None, builtin: str | None = None) -> dict:
    """Name -> Preset: the built-in presets, then the vault's (same name replaces)."""
    out = {}
    builtin = builtin or os.path.join(config_dir(), PRESETS_FILE)
    for raw in _layer(builtin):
        preset = _preset(raw, "built-in")
        out[preset.name] = preset
    if vault_root:
        path = os.path.join(vault_root, PRESETS_FILE)
        if os.path.isfile(path):
            for raw in _layer(path):
                preset = _preset(raw, "vault")
                out[preset.name] = preset
    return out


def hole_properties(preset: Preset, depth: float | None = None) -> list:
    """[(property, value)] for a PartDesign::Hole, in the order to set them (the thread type
    first: it rebuilds the size, class and cut lists). ``ThreadSize`` is the size ("M3");
    resolve it with :func:`thread_size_enum`. ``depth`` overrides the preset's."""
    depth = depth if depth is not None else preset.depth
    if preset.kind == "heatset":
        return [("ThreadType", "None"), ("Threaded", False), ("HoleCutType", "None"),
                ("Diameter", preset.diameter), ("DepthType", "Dimension"), ("Depth", depth),
                ("DrillPoint", "Flat"), ("Tapered", False)]
    t = thread(preset.thread)
    out = [("ThreadType", "ISOMetricProfile"), ("ThreadSize", t.size)]
    if preset.kind == "tapped":
        out += [("Threaded", True), ("ModelThread", False), ("ThreadClass", "6H"),
                ("HoleCutType", "None"), ("DepthType", "Dimension"), ("Depth", depth or 2 * t.major + 2),
                ("ThreadDepthType", "Dimension"), ("ThreadDepth", preset.thread_depth or 1.5 * t.major),
                ("DrillPoint", "Angled"), ("DrillPointAngle", 118.0)]
        return out
    out += [("Threaded", False), ("ThreadFit", preset.fit)]
    out.append(("HoleCutType", preset.head if preset.kind in ("counterbore", "countersink") else "None"))
    out += [("DepthType", "Dimension"), ("Depth", depth)] if depth else [("DepthType", "ThroughAll")]
    return out


def hole_callout(values: dict, count: int = 1, preset: Preset | None = None) -> str:
    """The drawing callout of a hole from its values (a PartDesign Hole's properties as plain
    numbers and strings: Threaded, ThreadType, ThreadSize, ThreadClass, Diameter, DepthType,
    Depth, ThreadDepthType, ThreadDepth, HoleCutType, HoleCutDiameter, HoleCutDepth,
    HoleCutCountersinkAngle). Lines are separated by "\\n"; ``count`` > 1 starts it "4X "."""
    lines = []
    through = values.get("DepthType") == "ThroughAll"
    depth = "THRU" if through else f"{DEPTH}{_num(values.get('Depth'))}"
    if values.get("Threaded") and str(values.get("ThreadType", "")).startswith("ISOMetric"):
        t = thread(str(values.get("ThreadSize")))
        tdepth = values.get("ThreadDepth") if values.get("ThreadDepthType") == "Dimension" else None
        cls = values.get("ThreadClass") or "6H"
        lines.append(t.callout(None if through else (tdepth or values.get("Depth")), True, cls)
                     + (" THRU" if through else ""))
        lines.append(f"{DIA}{_num(t.tap_drill)} {depth}" if not through else "")  # the tap drill
    else:
        lines.append(f"{DIA}{_num(values.get('Diameter'))} {depth}")
    cut = str(values.get("HoleCutType", "None"))
    if cut not in ("", "None"):
        sink = "countersink" in cut.lower() or cut in ("ISO 10642", "ISO 2009", "ISO 7046", "ISO 14582",
                                                       "ISO 10642 fine", "UNC 82", "UNF 82")
        if sink:
            lines.append(f"{CSINK} {DIA}{_num(values.get('HoleCutDiameter'))} x "
                         f"{_num(values.get('HoleCutCountersinkAngle', 90))}°")
        else:
            lines.append(f"{CBORE} {DIA}{_num(values.get('HoleCutDiameter'))} "
                         f"{DEPTH}{_num(values.get('HoleCutDepth'))}")
    if preset is not None and preset.kind == "heatset":
        lines.append(f"HEAT-SET INSERT {preset.insert}")
    lines = [line for line in lines if line]
    if count > 1:
        lines[0] = f"{count}X {lines[0]}"
    return "\n".join(lines)


# --- fasteners and the vault's hardware catalogue -------------------------------------------

# Fasteners add-on type -> what a BOM calls it. Others use the add-on's own description.
TYPE_NAMES = {
    "ISO4762": "Socket head cap screw", "DIN912": "Socket head cap screw",
    "ISO7380-1": "Button head socket screw", "ISO7380-2": "Button head socket screw with flange",
    "ISO10642": "Countersunk socket screw", "ISO2009": "Slotted countersunk screw",
    "ISO7045": "Pan head cross recess screw", "ISO14580": "Hexalobular socket cheese head screw",
    "ISO4014": "Hex head bolt", "ISO4017": "Hex head screw", "ISO4026": "Socket set screw, flat point",
    "ISO4027": "Socket set screw, cone point", "ISO4029": "Socket set screw, cup point",
    "ISO4032": "Hex nut", "ISO4035": "Thin hex nut", "ISO4033": "Hex nut, style 2",
    "DIN934": "Hex nut", "DIN985": "Nyloc nut", "ISO7040": "Nyloc nut", "ISO10511": "Thin nyloc nut",
    "ISO7089": "Washer", "ISO7090": "Washer, chamfered", "ISO7092": "Washer, small series",
    "ISO7093-1": "Washer, large series", "DIN125-A": "Washer", "DIN9021": "Washer, large",
    "DIN127-B": "Spring lock washer", "ISO8734": "Dowel pin", "ISO2338": "Parallel pin",
    "IUTHeatInsert": "Heat-set insert", "DIN7991": "Countersunk socket screw",
}
# Types without a length in their key (nuts, washers, inserts are one per size).
NO_LENGTH = ("nut", "washer", "insert")


def _type_code(fastener_type: str) -> str:
    return re.sub(r"\s+", "", str(fastener_type or "")).strip()


def _standard(code: str) -> str:
    """"ISO4762" -> "ISO 4762"; "ISO7380-1" -> "ISO 7380-1"."""
    return re.sub(r"^(ISO|DIN|ASME|EN|GOST)(?=\d)", r"\1 ", code)


def fastener_key(fastener_type: str, diameter: str, length=None) -> str:
    """``"ISO4762-M3x40"``, ``"ISO4032-M3"``: one vault item per key. Length in mm; a
    length of 0 or None (nuts, washers) is left out."""
    code = _type_code(fastener_type)
    if not code:
        raise ValueError("a fastener needs its type")
    size = re.sub(r"\s+", "", str(diameter or ""))
    if not size:
        raise ValueError("a fastener needs its diameter")
    key = f"{code}-{size}"
    if length not in (None, "", 0, 0.0, "0"):
        key += f"x{_num(float(str(length).replace('mm', '').strip()))}"
    return key


def fastener_description(fastener_type: str, diameter: str, length=None, addon_name: str = "") -> str:
    """``"Socket head cap screw ISO 4762 M3x40"``: the vault item's description."""
    code = _type_code(fastener_type)
    name = TYPE_NAMES.get(code) or _clean_addon_name(addon_name, code) or "Fastener"
    size = re.sub(r"\s+", "", str(diameter or ""))
    text = f"{name} {_standard(code)} {size}"
    if length not in (None, "", 0, 0.0, "0"):
        text += f"x{_num(float(str(length).replace('mm', '').strip()))}"
    return text


def _clean_addon_name(text: str, code: str) -> str:
    """The add-on's menu text "ISO 4762 Hexagon socket head cap screw" without the standard."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(rf"^{re.escape(_standard(code))}\s*:?\s*", "", text, flags=re.I)
    text = re.sub(rf"^{re.escape(code)}\s*:?\s*", "", text, flags=re.I)
    return text[:1].upper() + text[1:] if text else ""


_SCREW_WORDS = (
    (re.compile(r"socket\s*head\s*cap\s*screw|\bshcs\b", re.I), "ISO4762"),
    (re.compile(r"button\s*head", re.I), "ISO7380-1"),
    (re.compile(r"countersunk\s*socket|flat\s*head\s*socket", re.I), "ISO10642"),
    (re.compile(r"hex(agon)?\s*head\s*(bolt|screw)", re.I), "ISO4017"),
    (re.compile(r"set\s*screw|grub\s*screw", re.I), "ISO4026"),
)
_NUT_WORDS = (
    (re.compile(r"nyloc|lock\s*nut", re.I), "ISO7040"),
    (re.compile(r"hex(agon)?\s*nut|\bnut\b", re.I), "ISO4032"),
    (re.compile(r"\bwasher\b", re.I), "ISO7089"),
    (re.compile(r"heat[\s-]*set\s*insert|threaded\s*insert", re.I), "IUTHeatInsert"),
)
_SIZE_LENGTH = re.compile(r"\b(M\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\b", re.I)
_SIZE = re.compile(r"\b(M\d+(?:\.\d+)?)\b", re.I)
_BEARING = re.compile(r"\b(\d{3,5}(?:-?2?ZZ|-?2?RS|-?2Z|-?Z)?)\b", re.I)
_PIN = re.compile(r"dowel\s*pin\s*[Ø]?\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", re.I)
_COTS_WORDS = re.compile(r"\(\s*cots\s*\)|\bcots\b|\bpurchased\b|\bbought\b", re.I)


def match_description(text: str) -> str:
    """The hardware key a description names, or "": ``"Socket head cap screw M3x40 (COTS)"``
    -> ``"ISO4762-M3x40"``; ``"Ball bearing 683ZZ 3×7×3"`` -> ``"BEARING-683ZZ"``;
    ``"Dowel pin Ø3×10"`` -> ``"ISO8734-3x10"``."""
    text = str(text or "")
    for pattern, code in _SCREW_WORDS:
        if pattern.search(text):
            found = _SIZE_LENGTH.search(text)
            if found:
                return fastener_key(code, found.group(1).upper(), found.group(2))
    for pattern, code in _NUT_WORDS:
        if pattern.search(text):
            found = _SIZE.search(text)
            if found:
                return fastener_key(code, found.group(1).upper())
    pin = _PIN.search(text)
    if pin:
        return f"ISO8734-{_num(float(pin.group(1)))}x{_num(float(pin.group(2)))}"
    if re.search(r"\bbearing\b", text, re.I):
        found = _BEARING.search(text)
        if found:
            return "BEARING-" + found.group(1).upper().replace("-", "")
    return ""


def looks_cots(text: str) -> bool:
    """True when a description says the part is bought: "(COTS)", "purchased", or it names
    standard hardware (:func:`match_description`)."""
    return bool(_COTS_WORDS.search(str(text or ""))) or bool(match_description(text))


def catalogue_path(vault_root: str) -> str:
    return os.path.join(vault_root, CATALOGUE_FILE)


def load_catalogue(vault_root: str) -> dict:
    """Key -> entry ``{"part_number", "description", "source", ...}``; {} when there is none."""
    path = catalogue_path(vault_root)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("format") != CATALOGUE_FORMAT:
        raise ValueError(f"{path}: not a {CATALOGUE_FORMAT} file")
    if int(raw.get("version", 0)) > CATALOGUE_VERSION:
        raise ValueError(f"{path}: version {raw.get('version')} is newer than this BruceWorx reads")
    return {str(k): dict(v) for k, v in (raw.get("items") or {}).items()}


def save_catalogue(vault_root: str, items: dict) -> str:
    """Write the catalogue atomically (sorted, so a vault's history diffs cleanly)."""
    path = catalogue_path(vault_root)
    data = {"format": CATALOGUE_FORMAT, "version": CATALOGUE_VERSION,
            "note": "Standard hardware (MOD-05): hardware key -> the vault's COTS item. Written by BruceWorx.",
            "items": {k: items[k] for k in sorted(items)}}
    fd, tmp = tempfile.mkstemp(prefix=".hardware.", suffix=".json", dir=vault_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def find_item(key: str, catalogue: dict, items: dict) -> str:
    """The vault part number for ``key``: the catalogue's entry when that item still exists
    and is not obsolete, else an item whose description names the same hardware. ``items``:
    the vault's ``{pn: Item}`` (``pdm.Item``-like: description, item_type, obsolete)."""
    entry = catalogue.get(key)
    if entry:
        item = items.get(entry.get("part_number"))
        if item is not None and not getattr(item, "obsolete", False):
            return entry["part_number"]
    for pn in sorted(items):
        item = items[pn]
        if getattr(item, "obsolete", False) or getattr(item, "item_type", "part") not in ("part", "cots"):
            continue
        if match_description(getattr(item, "description", "")) == key:
            return pn
    return ""


def record(catalogue: dict, key: str, part_number: str, description: str, source: str = "Fasteners",
           **extra) -> dict:
    """Add or update ``key`` in ``catalogue`` (in place) and return its entry."""
    entry = dict(catalogue.get(key) or {})
    entry.update({"part_number": part_number, "description": description, "source": source})
    entry.update({k: v for k, v in extra.items() if v not in (None, "")})
    catalogue[key] = entry
    return entry


def cots_candidates(items: dict) -> list:
    """[(pn, key, description)] of vault items that are bought hardware but typed "part":
    the COTS parts made before MOD-05 (the gearbox's screws, bearings, pins, motor)."""
    out = []
    for pn in sorted(items):
        item = items[pn]
        if getattr(item, "item_type", "") != "part" or getattr(item, "obsolete", False):
            continue
        text = getattr(item, "description", "")
        if looks_cots(text):
            out.append((pn, match_description(text), text))
    return out


def _num(value) -> str:
    """3.0 -> "3", 6.70 -> "6.7", 0.45 -> "0.45"."""
    if value is None:
        return ""
    if hasattr(value, "Value"):
        value = value.Value
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text != "-0" else "0"
