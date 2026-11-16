# SPDX-License-Identifier: LGPL-2.1-or-later
"""A vault's sheet-metal rules (MOD-06): material, thickness, bend radius, K-factor, relief.

Stored in ``<vault>/sheet_metal_rules.json`` (format ``bruceworx.sheet-metal-rules``) and as
``sheetmetal`` rows of the vault's CSV (see ``vault_setup``). Free of FreeCAD, like
drawing_standards.py; the Sheet-metal rule command (``bruceworx_sheetmetal.py`` in the
BruceWorx module) binds a part to a rule, and ``flat_export`` writes the shop DXF.

One rule fixes T and R, so R/T and one K are exact (plan question 3). Rules are added and
retired, never deleted, so old parts keep their rule.

Units: every length is stored in millimetres. Functions that show or read lengths take
``units`` ("mm" or "in"): the vault's or the document's units. A thickness may also be typed
as a gauge ("16 ga"), looked up in the gauge table of the rule's material.

Bend maths (plan section 2), angles in degrees, K in the ANSI convention (0..0.5):
    BA   = theta (R + K T)
    OSSB = tan(theta / 2) (R + T)
    BD   = 2 OSSB - BA
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field

RULES_FILE = "sheet_metal_rules.json"
FORMAT = "bruceworx.sheet-metal-rules"
FORMAT_VERSION = 1
CSV_TABLE = "sheetmetal"
UNITS = ("mm", "in")
MM_PER_IN = 25.4

# DXF export profile (plan question 5): CUT is always written.
PROFILE_KEYS = ("bend_layers", "etch")


@dataclass
class Issue:
    """A finding: level is "error" (blocks saving), "warning" or "info"."""

    level: str
    text: str


@dataclass
class Rule:
    name: str
    material: str
    thickness: float  # mm
    radius: float  # inner bend radius, mm
    k_factor: float  # ANSI, 0 < K <= 0.5
    relief_width: float  # mm
    relief_depth: float  # mm
    min_flange: float  # mm
    gauge: str = ""  # an alias such as "16 ga"; the thickness is what counts
    note: str = ""
    retired: bool = False
    extra: dict = field(default_factory=dict, compare=False)  # keys a newer version wrote


@dataclass
class RuleSet:
    rules: list = field(default_factory=list)  # [Rule]
    default: str = ""  # the rule a new part gets
    profile: dict = field(default_factory=lambda: {"bend_layers": True, "etch": True})
    extra: dict = field(default_factory=dict, compare=False)


# --- units ----------------------------------------------------------------------------


def norm_units(units) -> str:
    """"mm" or "in" from loose text ("inch", "Imperial", "US customary", "mm/kg/s ...")."""
    text = str(units or "").strip().lower()
    if text in ("in", "inch", "inches", '"') or text.startswith(("inch", "imperial", "us customary")):
        return "in"
    if re.search(r"\(in\b|\bin,|\binch|\bft-in\b", text):  # FreeCAD unit schema names
        return "in"
    return "mm"


def to_mm(value: float, units: str) -> float:
    return float(value) * (MM_PER_IN if norm_units(units) == "in" else 1.0)


def from_mm(value: float, units: str) -> float:
    return float(value) / (MM_PER_IN if norm_units(units) == "in" else 1.0)


def format_number(value, digits: int = 3) -> str:
    """1.5 -> "1.5", 2.0 -> "2", 0.05984 -> "0.0598" with digits=4 (no float noise)."""
    text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def length_digits(units: str) -> int:
    """Decimals a length is shown with: 3 for inch (PDM-09: the inch profile's decimals, .060), 3 for mm
    (trailing zeros dropped)."""
    return 3


def format_length(mm: float, units: str = "mm", unit: bool = True) -> str:
    """A length stored in mm, shown in units: "1.5 mm", "0.060 in" (inch keeps its three places, as
    a shop drawing writes a gauge thickness; mm drops trailing zeros)."""
    units = norm_units(units)
    value = from_mm(mm, units)
    if units == "in":
        text = f"{value:.{length_digits(units)}f}"
        if text == "-0.000":
            text = "0.000"
    else:
        text = format_number(value, length_digits(units))
    return f"{text} {units}" if unit else text


_LENGTH = re.compile(r'^\s*([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+))\s*(mm|millimet(?:er|re)s?|in|inch(?:es)?|")?\s*$',
                     re.IGNORECASE)
_GAUGE = re.compile(r"^\s*(?:#\s*)?(\d{1,2})\s*(?:ga|gauge|gage|g)\.?\s*$", re.IGNORECASE)


def parse_length(text, units: str = "mm", material: str = "") -> float:
    """mm from "1.5", "1.5 mm", "0.06 in", '0.06"' or a gauge ("16 ga", for material's gauge
    table). A bare number is in ``units``. ValueError when it is none of these."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return to_mm(text, units)
    raw = str(text if text is not None else "").strip()
    gauge = _GAUGE.match(raw)
    if gauge:
        return gauge_thickness(int(gauge.group(1)), material)
    found = _LENGTH.match(raw)
    if not found:
        raise ValueError(f"{raw!r} is not a length (e.g. 1.5 mm, 0.06 in or 16 ga).")
    value = float(found.group(1).replace(",", "."))
    unit = (found.group(2) or "").lower()
    if unit:
        units = "in" if unit.startswith("in") or unit == '"' else "mm"
    return to_mm(value, units)


# --- gauges -----------------------------------------------------------------------------

# Sheet gauge -> thickness in inches. Steel: Manufacturers' Standard Gauge; stainless: the
# stainless sheet gauge; aluminium (and copper, brass): Brown & Sharpe (AWG).
GAUGES = {
    "steel": {7: 0.1793, 8: 0.1644, 9: 0.1495, 10: 0.1345, 11: 0.1196, 12: 0.1046, 13: 0.0897,
              14: 0.0747, 15: 0.0673, 16: 0.0598, 17: 0.0538, 18: 0.0478, 19: 0.0418, 20: 0.0359,
              21: 0.0329, 22: 0.0299, 23: 0.0269, 24: 0.0239, 25: 0.0209, 26: 0.0179, 28: 0.0149,
              30: 0.0120},
    "stainless": {7: 0.1875, 8: 0.1719, 9: 0.1563, 10: 0.1406, 11: 0.1250, 12: 0.1094, 13: 0.0940,
                  14: 0.0781, 15: 0.0700, 16: 0.0625, 17: 0.0560, 18: 0.0500, 19: 0.0440,
                  20: 0.0375, 22: 0.0312, 24: 0.0250, 26: 0.0187, 28: 0.0156, 30: 0.0125},
    "aluminium": {6: 0.1620, 8: 0.1285, 10: 0.1019, 11: 0.0907, 12: 0.0808, 13: 0.0720,
                  14: 0.0641, 16: 0.0508, 18: 0.0403, 20: 0.0320, 22: 0.0253, 24: 0.0201,
                  26: 0.0159},
}


def material_family(material: str) -> str:
    """"aluminium", "stainless" or "steel" (the gauge table and the typical K column)."""
    text = str(material or "").lower()
    if re.search(r"stainless|\bss\b|\binox\b|\b(?:30[1-4]|316|321|430)\b", text):
        return "stainless"
    if re.search(r"alumin|\bal\b|\bal[\s-]?\d|\b(?:1100|2024|3003|5052|5083|6061|6082|7075)\b|copper|brass|\bcu\b",
                 text):
        return "aluminium"
    return "steel"


def gauge_thickness(gauge: int, material: str = "") -> float:
    """A gauge number's thickness in mm for the material's table; ValueError when it has none."""
    family = material_family(material)
    table = GAUGES[family]
    if gauge not in table:
        raise ValueError(f"{gauge} ga is not in the {family} gauge table ({min(table)}-{max(table)}).")
    return round(table[gauge] * MM_PER_IN, 5)


def gauge_text(gauge) -> str:
    """"16 ga" from 16, "16", "16ga", "#16 gauge"; "" for blank."""
    text = str(gauge if gauge is not None else "").strip()
    if not text:
        return ""
    found = _GAUGE.match(text) or re.match(r"^\s*#?\s*(\d{1,2})\s*$", text)
    if not found:
        raise ValueError(f"gauge {text!r} must be a number such as 16 ga.")
    return f"{int(found.group(1))} ga"


# --- typical values (plan section 2) ---------------------------------------------------------

# Rules of thumb, ANSI K, air bending / bottoming, by R vs T: (R < T, T <= R <= 3T, R > 3T).
TYPICAL_K = {
    "air": {"aluminium": (0.33, 0.40, 0.50), "steel": (0.38, 0.43, 0.50), "stainless": (0.40, 0.45, 0.50)},
    "bottoming": {"aluminium": (0.42, 0.46, 0.50), "steel": (0.44, 0.47, 0.50),
                  "stainless": (0.46, 0.48, 0.50)},
}


def typical_k(material: str, thickness: float, radius: float, method: str = "air") -> float:
    """The published rule-of-thumb K for a material and R/T (to be replaced by the shop's)."""
    low, mid, high = TYPICAL_K[method][material_family(material)]
    ratio = radius / thickness if thickness > 0 else 1.0
    if ratio < 1.0 - 1e-9:
        return low
    if ratio <= 3.0 + 1e-9:
        return mid
    return high


def din_to_ansi(k_din: float) -> float:
    """DIN 6935 k (0..1) to the ANSI K the rules store: K = k / 2."""
    return k_din / 2.0


def relief_size(thickness: float, radius: float) -> tuple:
    """(width, depth) of a bend relief: at least T wide and R + T deep."""
    return round(thickness, 5), round(radius + thickness, 5)


def min_flange(thickness: float, radius: float) -> float:
    """The shortest flange that sits on the die: about 2.5 T + R."""
    return round(2.5 * thickness + radius, 5)


def make_rule(name: str, material: str, thickness: float, radius: float | None = None,
              k_factor: float | None = None, gauge: str = "", note: str = "") -> Rule:
    """A rule with the typical values filled in: R = T, K by rule of thumb, relief, min flange."""
    radius = thickness if radius is None else radius
    k_factor = typical_k(material, thickness, radius) if k_factor is None else k_factor
    width, depth = relief_size(thickness, radius)
    return Rule(name, material, round(thickness, 5), round(radius, 5), k_factor, width, depth,
                min_flange(thickness, radius), gauge_text(gauge), note)


AL5052 = "Al 5052-H32"
CRS = "Steel CR 1008"
SS304 = "Stainless 304"


def seed_rules(units: str = "mm") -> RuleSet:
    """The rules a new vault starts with (24 Oct 2026): Al 5052-H32 1.0/1.5/2.0/3.0, CR steel
    16/14/12 ga, stainless 304 1.2/1.5, R = T, typical air-bending K. An inch vault gets the
    nearest US stock sizes: Al .040/.063/.080/.125, the same steel gauges, stainless 18/16 ga."""
    if norm_units(units) == "in":
        rules = [make_rule(f"AL5052-{t:.3f}".replace("-0.", "-."), AL5052, t * MM_PER_IN)
                 for t in (0.040, 0.063, 0.080, 0.125)]
        default = "AL5052-.063"
        stainless = [make_rule(f"SS304-{g}ga", SS304, gauge_thickness(g, SS304), gauge=f"{g} ga")
                     for g in (18, 16)]
    else:
        rules = [make_rule(f"AL5052-{t:.1f}", AL5052, t) for t in (1.0, 1.5, 2.0, 3.0)]
        default = "AL5052-1.5"
        stainless = [make_rule(f"SS304-{t:.1f}", SS304, t) for t in (1.2, 1.5)]
    steel = [make_rule(f"CRS-{g}ga", CRS, gauge_thickness(g, CRS), gauge=f"{g} ga") for g in (16, 14, 12)]
    rules = rules + steel + stainless
    for rule in rules:
        if rule.name == default:
            rule.note = "default"
    return RuleSet(rules=rules, default=default)


# --- bend maths -------------------------------------------------------------------------------


def bend_allowance(angle: float, radius: float, thickness: float, k_factor: float) -> float:
    """BA = theta (R + K T); angle in degrees (the bend, 90 for a right angle)."""
    return math.radians(abs(angle)) * (radius + k_factor * thickness)


def outside_setback(angle: float, radius: float, thickness: float) -> float:
    return math.tan(math.radians(abs(angle)) / 2.0) * (radius + thickness)


def bend_deduction(angle: float, radius: float, thickness: float, k_factor: float) -> float:
    return 2.0 * outside_setback(angle, radius, thickness) - bend_allowance(angle, radius, thickness, k_factor)


def flat_length(outside_lengths, bends, thickness: float) -> float:
    """Flat length from the outside (mould-line) lengths and bends [(angle, R, K)]."""
    return sum(outside_lengths) - sum(bend_deduction(a, r, thickness, k) for a, r, k in bends)


# --- rules ------------------------------------------------------------------------------------

_LENGTH_FIELDS = ("thickness", "radius", "relief_width", "relief_depth", "min_flange")
FIELDS = ("name", "material", "thickness", "radius", "k_factor", "relief_width", "relief_depth",
          "min_flange", "gauge", "note", "retired")
LABELS = {"name": "Rule", "material": "Material", "thickness": "T", "radius": "R", "k_factor": "K",
          "relief_width": "Relief w", "relief_depth": "Relief d", "min_flange": "Min flange",
          "gauge": "Gauge", "note": "Note", "retired": "Retired"}
DESCRIPTIONS = {
    "name": "Rule name, e.g. AL5052-1.5; a part keeps the name",
    "material": "Material, e.g. Al 5052-H32",
    "thickness": "Sheet thickness T (a gauge such as 16 ga is looked up for the material)",
    "radius": "Inner bend radius R",
    "k_factor": "K-factor, ANSI 0..0.5 (DIN k: halve it)",
    "relief_width": "Bend relief width (at least T)",
    "relief_depth": "Bend relief depth (at least R + T)",
    "min_flange": "Shortest flange (about 2.5 T + R)",
    "gauge": "Gauge alias shown with the rule, e.g. 16 ga",
    "note": "Free text",
    "retired": "Retired rules are not offered for new parts",
}


def find(rules: RuleSet, name: str):
    """The rule called name (any case), or None."""
    want = str(name or "").strip().lower()
    return next((r for r in rules.rules if r.name.lower() == want), None)


def active(rules: RuleSet) -> list:
    return [r for r in rules.rules if not r.retired]


def default_rule(rules: RuleSet):
    """The vault's default rule; else the first rule not retired; else None."""
    found = find(rules, rules.default)
    if found is not None and not found.retired:
        return found
    return next(iter(active(rules)), None)


def label(rule: Rule, units: str = "mm") -> str:
    """"AL5052-1.5 — Al 5052-H32, 1.5 mm, R 1.5, K 0.4" for pickers."""
    thickness = format_length(rule.thickness, units)
    if rule.gauge:
        thickness = f"{rule.gauge} ({thickness})"
    return (f"{rule.name} — {rule.material}, {thickness}, R {format_length(rule.radius, units, unit=False)}, "
            f"K {format_number(rule.k_factor, 3)}")


def stock_text(rule: Rule, units: str = "mm") -> str:
    """The Stock part property: "Sheet 1.5 Al 5052-H32", "Sheet 16 ga Steel CR 1008",
    "Sheet 0.063 in Al 5052-H32"."""
    if rule.gauge:
        size = rule.gauge
    elif norm_units(units) == "in":
        size = format_length(rule.thickness, "in")
    else:
        size = format_length(rule.thickness, "mm", unit=False)
    return f"Sheet {size} {rule.material}".strip()


def validate_rule(rule: Rule) -> list:
    issues = []
    name = rule.name.strip()
    if not name:
        issues.append(Issue("error", "A rule needs a name."))
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+\-/]*", name):
        issues.append(Issue("error", f"Rule {name!r}: use letters, digits, space and . _ + - /."))
    prefix = f"Rule {name or '?'}"
    if not rule.material.strip():
        issues.append(Issue("error", f"{prefix} needs a material."))
    for key in _LENGTH_FIELDS:
        value = getattr(rule, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 1000:
            issues.append(Issue("error", f"{prefix}: {LABELS[key]} {value!r} must be a length of 0 or more."))
    if not issues:
        if not rule.thickness > 0:
            issues.append(Issue("error", f"{prefix}: the thickness must be more than 0."))
        elif rule.radius < 0.5 * rule.thickness - 1e-9:
            issues.append(Issue("warning", f"{prefix}: R below T/2 may crack the bend."))
        if rule.thickness > 0:
            if rule.relief_width and rule.relief_width < rule.thickness - 1e-6:
                issues.append(Issue("warning", f"{prefix}: the relief is narrower than T."))
            if rule.relief_depth and rule.relief_depth < rule.radius + rule.thickness - 1e-6:
                issues.append(Issue("warning", f"{prefix}: the relief is shallower than R + T."))
    k = rule.k_factor
    if isinstance(k, bool) or not isinstance(k, (int, float)) or not 0 < k <= 0.5:
        issues.append(Issue("error", f"{prefix}: K {k!r} must be more than 0 and at most 0.5 (ANSI)."))
    if rule.gauge:
        try:
            gauge_text(rule.gauge)
        except ValueError as exc:
            issues.append(Issue("error", f"{prefix}: {exc}"))
    return issues


def validate(rules: RuleSet) -> list:
    """Issues with a rule set; errors block saving."""
    issues = []
    seen = set()
    for rule in rules.rules:
        issues += validate_rule(rule)
        key = rule.name.strip().lower()
        if key and key in seen:
            issues.append(Issue("error", f"Rule {rule.name} is listed twice."))
        seen.add(key)
    if rules.rules and not active(rules):
        issues.append(Issue("warning", "Every rule is retired: new parts get none."))
    if rules.default and find(rules, rules.default) is None:
        issues.append(Issue("error", f"The default rule {rules.default} is not in the table."))
    elif rules.default and find(rules, rules.default).retired:
        issues.append(Issue("warning", f"The default rule {rules.default} is retired."))
    for key in rules.profile:
        if key not in PROFILE_KEYS:
            issues.append(Issue("warning", f"DXF setting {key!r} is not known; it is kept."))
    return issues


def check_change(old: RuleSet, new: RuleSet) -> list:
    """Errors for what Vault Settings may not do: delete a rule or rename one (retire it)."""
    issues = []
    names = {r.name.lower() for r in new.rules}
    for rule in old.rules:
        if rule.name.lower() not in names:
            issues.append(Issue("error", f"Rule {rule.name} cannot be deleted: parts may use it. "
                                         "Retire it instead."))
    return issues


# --- text <-> rules (dialogs and the CSV) ----------------------------------------------------------


def field_text(rule: Rule, key: str, units: str = "mm") -> str:
    """One field as text, lengths in units without the unit."""
    value = getattr(rule, key)
    if key in _LENGTH_FIELDS:
        return format_length(value, units, unit=False)
    if key == "k_factor":
        return format_number(value, 3)
    if key == "retired":
        return "yes" if value else "no"
    return str(value or "")


def set_field(rule: Rule, key: str, text, units: str = "mm") -> Issue | None:
    """Set one field from text (lengths in units unless the text names one); returns an error
    Issue (and changes nothing) when the text is not valid."""
    if key not in FIELDS:
        return Issue("error", f"{key!r} is not a rule field ({', '.join(FIELDS)}).")
    raw = text if isinstance(text, (int, float)) and not isinstance(text, bool) else str(text or "").strip()
    try:
        if key in _LENGTH_FIELDS:
            value = parse_length(raw, units, rule.material)
            if key == "thickness" and _GAUGE.match(str(raw)) and not rule.gauge:
                rule.gauge = gauge_text(raw)
        elif key == "k_factor":
            value = float(str(raw).replace(",", "."))
        elif key == "gauge":
            value = gauge_text(raw)
        elif key == "retired":
            flag = _flag(raw)
            if flag is None:
                raise ValueError(f"retired {raw!r} must be yes or no")
            value = flag
        else:
            value = str(raw)
    except ValueError as exc:
        return Issue("error", f"{LABELS[key]}: {exc}")
    setattr(rule, key, round(value, 5) if key in _LENGTH_FIELDS else value)
    return None


_TRUE = {"yes", "y", "true", "1", "x"}
_FALSE = {"no", "n", "false", "0", ""}


def _flag(text):
    if isinstance(text, bool):
        return text
    value = str(text if text is not None else "").strip().lower()
    return True if value in _TRUE else False if value in _FALSE else None


# The CSV row of a rule (vault_setup's fixed columns): code = rule name, name = material,
# description = note, values = "T=1.5|R=1.5|K=0.4|relief=1.5x3|min_flange=5.25|gauge=16 ga"
# (lengths in the CSV's units unless a value names one), default = yes for the vault's default,
# retired = yes/no.
_CSV_KEYS = {"t": "thickness", "thickness": "thickness", "r": "radius", "radius": "radius",
             "k": "k_factor", "kfactor": "k_factor", "k_factor": "k_factor",
             "relief_width": "relief_width", "relief_w": "relief_width", "relief_depth": "relief_depth",
             "relief_d": "relief_depth", "min_flange": "min_flange", "minflange": "min_flange",
             "gauge": "gauge", "ga": "gauge"}


def csv_values(rule: Rule, units: str = "mm") -> str:
    parts = [f"T={field_text(rule, 'thickness', units)}", f"R={field_text(rule, 'radius', units)}",
             f"K={field_text(rule, 'k_factor')}",
             f"relief={field_text(rule, 'relief_width', units)}x{field_text(rule, 'relief_depth', units)}",
             f"min_flange={field_text(rule, 'min_flange', units)}"]
    if rule.gauge:
        parts.append(f"gauge={rule.gauge}")
    return "|".join(parts)


def csv_rows(rules: RuleSet, units: str = "mm") -> list:
    """Rows of the vault CSV (table=sheetmetal), one per rule."""
    return [{"table": CSV_TABLE, "code": r.name, "name": r.material, "description": r.note,
             "values": csv_values(r, units), "default": "yes" if r.name == rules.default else "",
             "retired": "yes" if r.retired else "no"} for r in rules.rules]


def rule_from_csv(values: dict, units: str = "mm") -> tuple:
    """(Rule or None, [Issue]) from one CSV row's cells (code, name, description, values, retired).
    Missing relief and min flange get the typical values; a missing K the typical K."""
    issues = []
    name = str(values.get("code", "")).strip()
    material = str(values.get("name", "")).strip()
    if not name:
        return None, [Issue("error", "sheet-metal rule needs a code (the rule name).")]
    given = {}
    for part in str(values.get("values", "")).split("|"):
        if not part.strip():
            continue
        key, sep, text = part.partition("=")
        key = re.sub(r"[\s\-]+", "_", key.strip().lower())
        if not sep:
            issues.append(Issue("error", f"rule {name}: {part.strip()!r} must be key=value."))
            continue
        if key == "relief":
            width, _x, depth = text.lower().partition("x")
            given["relief_width"], given["relief_depth"] = width.strip(), depth.strip()
            continue
        if key not in _CSV_KEYS:
            issues.append(Issue("error", f"rule {name}: {key!r} is not one of T, R, K, relief, min_flange, gauge."))
            continue
        given[_CSV_KEYS[key]] = text.strip()
    if "thickness" not in given:
        issues.append(Issue("error", f"rule {name}: values need T= (the thickness)."))
    if any(i.level == "error" for i in issues):
        return None, issues
    rule = Rule(name, material, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, note=str(values.get("description", "")).strip())
    for key in ("gauge", "thickness", "radius", "k_factor", "relief_width", "relief_depth", "min_flange"):
        if key in given and given[key] != "":
            found = set_field(rule, key, given[key], units)
            if found is not None:
                issues.append(Issue("error", f"rule {name}: {found.text}"))
    if any(i.level == "error" for i in issues):
        return None, issues
    if "radius" not in given:
        rule.radius = rule.thickness
    if "k_factor" not in given:
        rule.k_factor = typical_k(rule.material, rule.thickness, rule.radius)
    width, depth = relief_size(rule.thickness, rule.radius)
    rule.relief_width = rule.relief_width or width
    rule.relief_depth = rule.relief_depth or depth
    rule.min_flange = rule.min_flange or min_flange(rule.thickness, rule.radius)
    retired = _flag(values.get("retired"))
    if retired is None:
        issues.append(Issue("error", f"rule {name}: retired {values.get('retired')!r} must be yes or no."))
        return None, issues
    rule.retired = retired
    return rule, issues + [Issue("error", f"rule {name}: {i.text}") for i in validate_rule(rule)
                           if i.level == "error"]


def merge(base: RuleSet, rules: list, default: str = "") -> RuleSet:
    """base with rules added or replaced by name (a CSV never deletes one)."""
    out = RuleSet([dataclasses.replace(r) for r in base.rules], default or base.default, dict(base.profile),
                  dict(base.extra))
    for rule in rules:
        for index, old in enumerate(out.rules):
            if old.name.lower() == rule.name.lower():
                out.rules[index] = rule
                break
        else:
            out.rules.append(rule)
    return out


# --- file -------------------------------------------------------------------------------------------


def root_of(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    return os.path.dirname(path) if os.path.isfile(path) else path


def rules_path(vault_root: str) -> str:
    return os.path.join(root_of(vault_root), RULES_FILE)


def exists(vault_root: str) -> bool:
    return bool(vault_root) and os.path.isfile(rules_path(vault_root))


def to_dict(rules: RuleSet) -> dict:
    data = dict(rules.extra)
    entries = []
    for rule in rules.rules:
        entry = dict(rule.extra)
        for key in FIELDS:
            entry[key] = getattr(rule, key)
        entries.append(entry)
    data.update({"format": FORMAT, "version": FORMAT_VERSION, "units": "mm", "default": rules.default,
                 "dxf": dict(rules.profile), "rules": entries})
    return data


def from_dict(data: dict) -> tuple:
    """(RuleSet, [Issue]): a bad rule is left out with a warning (a file must always open)."""
    issues = []
    if not isinstance(data, dict):
        return RuleSet(), [Issue("warning", "The rules file is not a table; no rules are read.")]
    known = {"format", "version", "units", "default", "dxf", "rules"}
    out = RuleSet(default=str(data.get("default") or ""), extra={k: v for k, v in data.items() if k not in known})
    profile = data.get("dxf")
    if isinstance(profile, dict):
        out.profile.update({k: bool(v) if k in PROFILE_KEYS else v for k, v in profile.items()})
    for index, entry in enumerate(data.get("rules") or []):
        if not isinstance(entry, dict):
            issues.append(Issue("warning", f"Rule {index + 1} is not a table; it is left out."))
            continue
        rule = Rule("", "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    extra={k: v for k, v in entry.items() if k not in FIELDS})
        bad = []
        for key in FIELDS:
            if key not in entry:
                continue
            value = entry[key]
            if key in _LENGTH_FIELDS or key == "k_factor":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    bad.append(key)
                    continue
                setattr(rule, key, float(value))
            elif key == "retired":
                rule.retired = bool(value)
            else:
                setattr(rule, key, str(value or ""))
        errors = [i.text for i in validate_rule(rule) if i.level == "error"]
        if bad or errors:
            what = rule.name or f"{index + 1}"
            issues.append(Issue("warning", f"Rule {what} is left out: "
                                           + "; ".join(errors + [f"{k} is not a number" for k in bad])))
            continue
        out.rules.append(rule)
    return out, issues


def load_with_issues(vault_root: str | None, units: str = "mm") -> tuple:
    """(RuleSet, [Issue]); the starting rules (in units) when the vault has no file."""
    if not exists(vault_root or ""):
        return seed_rules(units), []
    path = rules_path(vault_root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return seed_rules(units), [Issue("warning", f"{path} cannot be read ({exc}); the starting rules are used.")]
    return from_dict(data)


def load(vault_root: str | None, units: str = "mm") -> RuleSet:
    return load_with_issues(vault_root, units)[0]


def _replace_bytes(path: str, data: bytes) -> None:
    """Replace path atomically: a ``.tmp`` sibling (iCloud does not sync those), then rename."""
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def save(vault_root: str, rules: RuleSet) -> str:
    """Write <vault>/sheet_metal_rules.json atomically; ValueError (nothing written) on an error."""
    problems = [i.text for i in validate(rules) if i.level == "error"]
    if problems:
        raise ValueError("\n".join(problems))
    path = rules_path(vault_root)
    _replace_bytes(path, (json.dumps(to_dict(rules), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def ensure_saved(vault_root: str, units: str = "mm") -> RuleSet:
    """The vault's rules, writing the starting rules first when it has no file, so the rules a
    part takes stay as they were when it took them."""
    rules = load(vault_root, units)
    if vault_root and not exists(vault_root):
        save(vault_root, rules)
    return rules


# --- bend table (DRW-07) -----------------------------------------------------------------------------

BEND_TABLE_HEADER = ("Bend", "Dir", "Angle", "R", "K", "BA")


def bend_table(bends, units: str = "mm") -> list:
    """Rows for the bend table from bends [{direction, angle, radius, k_factor, ba}], lengths in units:
    [("1", "Up", "90°", "2", "0.44", "4.18")]."""
    rows = []
    for number, bend in enumerate(bends, 1):
        direction = {"up": "Up", "down": "Down"}.get(str(bend.get("direction", "")).lower(), "—")
        rows.append((str(number), direction, f"{format_number(bend['angle'], 1)}°",
                     format_number(from_mm(bend["radius"], units), 2 if norm_units(units) == "mm" else 3),
                     format_number(bend["k_factor"], 3),
                     format_number(from_mm(bend["ba"], units), 2 if norm_units(units) == "mm" else 3)))
    return rows
