# SPDX-License-Identifier: LGPL-2.1-or-later
"""A vault's drawing standards (DRW-01): what every title block of the vault says.

Stored in ``<vault>/drawing_standards.json`` and edited in Vault Settings (and
as ``standard`` rows of the vault's CSV, see ``vault_setup``). Free of FreeCAD,
like vault_setup.py; the title-block filler (``bruceworx_drawing.py`` in the
BruceWorx module) reads it through :func:`load` and :func:`title_block_fields`.

Settings and defaults (plan section "DRW-01 plan")::

    company          "" -> the vault's name
    logo             a file copied into the vault (SVG or PNG), "" for none
    standard         ASME Y14.5-2018 | ASME Y14.5-2009 | ISO GPS
    projection       third | first
    units            mm | in: the vault's default units (new documents, PDM-09)
    default_sheet    ANSI_B_Landscape (one of SHEETS): the sheet of a mm drawing
    tolerance_mode   decimal | iso2768 | per_drawing
    tol_1_place ...  X.X 0.5, X.XX 0.25, X.XXX 0.1 (mm), angles 1 degree
    iso2768_class    f | m | c | v
    in_default_sheet ANSI_B_Landscape: the sheet of an inch drawing
    in_tol_1_place.. X.X .1, X.XX .01, X.XXX .005 (in), angles 1 degree
    surface_finish   default Ra, "" for no note
    break_edges      "break sharp edges" size, "" for no note

Two profiles (PDM-09 A): the base fields are the millimetre profile, the
``in_`` fields the inch one; :func:`profile` picks the view for a drawing by
its model's units. Inch drawings are ASME (ANSI sheets, decimal tolerances
written .005, dimensions %.3f); mm drawings keep DRW-01's choices. A version-1
file whose units were "in" had typed its tolerances in inches: they move to the
inch profile when it is read.

Also here, because the filler needs them and they are pure: the
general-tolerance block text, the sheet table, and who drew, checked and
approved an item, read from the vault's journal (:func:`signatures`).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

STANDARDS_FILE = "drawing_standards.json"
FORMAT = "bruceworx.drawing-standards"
FORMAT_VERSION = 2  # 2: the inch profile (in_* keys), PDM-09
LOGO_BASENAME = "drawing_logo"  # the copied logo: <vault>/drawing_logo.<ext>
LOGO_EXTENSIONS = (".svg", ".png", ".jpg", ".jpeg")

STANDARDS = ("ASME Y14.5-2018", "ASME Y14.5-2009", "ISO GPS")
PROJECTIONS = ("third", "first")
UNITS = ("mm", "in")
TOLERANCE_MODES = ("decimal", "iso2768", "per_drawing")
ISO2768_CLASSES = ("f", "m", "c", "v")

# Sheet key -> (label, width mm, height mm). The template file is <key>.svg.
SHEETS = {
    "ANSI_A_Landscape": ("ANSI A landscape (11 × 8.5 in)", 279.4, 215.9),
    "ANSI_A_Portrait": ("ANSI A portrait (8.5 × 11 in)", 215.9, 279.4),
    "ANSI_B_Landscape": ("ANSI B landscape (17 × 11 in)", 431.8, 279.4),
    "ANSI_C_Landscape": ("ANSI C landscape (22 × 17 in)", 558.8, 431.8),
    "ANSI_D_Landscape": ("ANSI D landscape (34 × 22 in)", 863.6, 558.8),
    "ISO_A4_Landscape": ("ISO A4 landscape (297 × 210 mm)", 297.0, 210.0),
    "ISO_A4_Portrait": ("ISO A4 portrait (210 × 297 mm)", 210.0, 297.0),
    "ISO_A3_Landscape": ("ISO A3 landscape (420 × 297 mm)", 420.0, 297.0),
    "ISO_A2_Landscape": ("ISO A2 landscape (594 × 420 mm)", 594.0, 420.0),
    "ISO_A1_Landscape": ("ISO A1 landscape (841 × 594 mm)", 841.0, 594.0),
}
DEFAULT_SHEET = "ANSI_B_Landscape"

_LABELS = {
    "standard": {s: s for s in STANDARDS},
    "projection": {"third": "Third angle", "first": "First angle"},
    "units": {"mm": "Millimetres (mm)", "in": "Inches (in)"},
    "tolerance_mode": {"decimal": "By decimal places", "iso2768": "ISO 2768 class",
                       "per_drawing": "Per drawing"},
    "iso2768_class": {"f": "f (fine)", "m": "m (medium)", "c": "c (coarse)", "v": "v (very coarse)"},
    "default_sheet": {k: v[0] for k, v in SHEETS.items()},
    "in_default_sheet": {k: v[0] for k, v in SHEETS.items()},
}
_CHOICES = {
    "standard": STANDARDS,
    "projection": PROJECTIONS,
    "units": UNITS,
    "tolerance_mode": TOLERANCE_MODES,
    "iso2768_class": ISO2768_CLASSES,
    "default_sheet": tuple(SHEETS),
    "in_default_sheet": tuple(SHEETS),
}
MM_NUMBERS = ("tol_1_place", "tol_2_place", "tol_3_place", "tol_angle")
IN_NUMBERS = ("in_tol_1_place", "in_tol_2_place", "in_tol_3_place", "in_tol_angle")
_NUMBERS = MM_NUMBERS + IN_NUMBERS
# The inch profile's keys and the base (mm) key each stands in for.
IN_KEYS = {"in_default_sheet": "default_sheet", "in_tol_1_place": "tol_1_place", "in_tol_2_place": "tol_2_place",
           "in_tol_3_place": "tol_3_place", "in_tol_angle": "tol_angle"}
ASME_DEFAULT = "ASME Y14.5-2018"


@dataclass
class Issue:
    """A finding: level is "error" (blocks saving), "warning" or "info"."""

    level: str
    text: str


@dataclass
class DrawingStandards:
    company: str = ""
    logo: str = ""
    standard: str = "ASME Y14.5-2018"
    projection: str = "third"
    units: str = "mm"
    default_sheet: str = DEFAULT_SHEET
    tolerance_mode: str = "decimal"
    tol_1_place: float = 0.5
    tol_2_place: float = 0.25
    tol_3_place: float = 0.1
    tol_angle: float = 1.0
    iso2768_class: str = "m"
    in_default_sheet: str = DEFAULT_SHEET  # the inch profile (PDM-09): ANSI B, ASME decimal tolerances
    in_tol_1_place: float = 0.1
    in_tol_2_place: float = 0.01
    in_tol_3_place: float = 0.005
    in_tol_angle: float = 1.0
    surface_finish: str = ""
    break_edges: str = ""
    extra: dict = field(default_factory=dict, compare=False)  # keys a newer version wrote; kept on save


# What each setting is, for the CSV description column and tool tips.
DESCRIPTIONS = {
    "company": "Company name in the title block (blank: the vault's name)",
    "logo": "Logo file in the vault folder (SVG or PNG); blank for none",
    "standard": "Dimensioning and tolerancing standard: " + " | ".join(STANDARDS),
    "projection": "Projection: third (ASME) | first (ISO)",
    "units": "Default units of the vault's new documents and drawings: mm | in",
    "default_sheet": "Sheet of a new mm drawing: " + " | ".join(SHEETS),
    "tolerance_mode": "General tolerances: decimal | iso2768 | per_drawing",
    "tol_1_place": "Tolerance of X.X dimensions on mm drawings (± mm)",
    "tol_2_place": "Tolerance of X.XX dimensions on mm drawings (± mm)",
    "tol_3_place": "Tolerance of X.XXX dimensions on mm drawings (± mm)",
    "tol_angle": "Tolerance of angles on mm drawings (± degrees)",
    "iso2768_class": "ISO 2768 tolerance class: f | m | c | v",
    "in_default_sheet": "Sheet of a new inch drawing (ASME): " + " | ".join(SHEETS),
    "in_tol_1_place": "Tolerance of X.X dimensions on inch drawings (± in)",
    "in_tol_2_place": "Tolerance of X.XX dimensions on inch drawings (± in)",
    "in_tol_3_place": "Tolerance of X.XXX dimensions on inch drawings (± in)",
    "in_tol_angle": "Tolerance of angles on inch drawings (± degrees)",
    "surface_finish": "Default surface finish Ra (blank: no note)",
    "break_edges": "Break sharp edges, e.g. 0.2-0.5 (blank: no note)",
}
KEYS = tuple(f.name for f in dataclasses.fields(DrawingStandards) if f.name != "extra")


# --- values -------------------------------------------------------------------------


def _norm(text) -> str:
    return re.sub(r"[\s_\-()×x.]+", "", str(text or "")).lower()


def _choice(key: str, text):
    """The canonical value for a choice typed loosely ("Third angle", "ISO 2768", "ansi b
    landscape"), or None."""
    want = _norm(text)
    if not want:
        return None
    aliases = {
        "projection": {"3rd": "third", "thirdangle": "third", "1st": "first", "firstangle": "first"},
        "units": {"millimetres": "mm", "millimeters": "mm", "millimetresmm": "mm", "inch": "in",
                  "inches": "in", "inchesin": "in"},
        "tolerance_mode": {"iso": "iso2768", "decimalplaces": "decimal", "bydecimalplaces": "decimal",
                           "perdrawing": "per_drawing", "none": "per_drawing", "iso2768class": "iso2768"},
        "standard": {"asme": "ASME Y14.5-2018", "asmey145": "ASME Y14.5-2018", "iso": "ISO GPS"},
    }.get(key, {})
    if want in aliases:
        return aliases[want]
    for value in _CHOICES[key]:
        if want in (_norm(value), _norm(_LABELS[key].get(value, ""))):
            return value
    if key == "iso2768_class" and len(want) == 1 and want in ISO2768_CLASSES:
        return want
    if key in ("default_sheet", "in_default_sheet"):  # "B", "A3", "ANSI B", "A4 portrait"
        found = [k for k in SHEETS if _norm(k).replace("ansi", "").replace("iso", "").startswith(
            want.replace("ansi", "").replace("iso", ""))]
        if found:
            return found[0]
    return None


def label(key: str, value) -> str:
    """A setting's value as shown in Vault Settings (choices get their label)."""
    return _LABELS.get(key, {}).get(value, str(value))


def choices(key: str) -> list:
    """[(value, label)] for a choice setting."""
    return [(v, label(key, v)) for v in _CHOICES[key]]


def format_number(value) -> str:
    """0.5 -> "0.5", 1.0 -> "1", 0.25 -> "0.25" (no float noise)."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def to_text(standards: DrawingStandards, key: str) -> str:
    """One setting as text (the CSV ``name`` cell)."""
    value = getattr(standards, key)
    return format_number(value) if key in _NUMBERS else str(value or "")


def set_text(standards: DrawingStandards, key: str, text) -> Issue | None:
    """Set one setting from text; returns an error Issue (and changes nothing) when it is not valid."""
    if key not in KEYS:
        return Issue("error", f"{key!r} is not a drawing standard ({', '.join(KEYS)}).")
    text = str(text if text is not None else "").strip()
    if key in _CHOICES:
        value = _choice(key, text)
        if value is None:
            return Issue("error", f"{key} {text!r} must be one of {', '.join(_CHOICES[key])}.")
    elif key in _NUMBERS:
        try:
            value = float(text.lstrip("±").replace(",", ".").replace("°", ""))
        except ValueError:
            return Issue("error", f"{key} {text!r} is not a number.")
        if not 0 < value < 1000:
            return Issue("error", f"{key} {text!r} must be more than 0.")
    else:
        value = text
    setattr(standards, key, value)
    return None


def validate(standards: DrawingStandards, vault_root: str | None = None) -> list:
    """Issues with the settings; errors block saving."""
    issues = []
    for key, allowed in _CHOICES.items():
        if getattr(standards, key) not in allowed:
            issues.append(Issue("error", f"{key} {getattr(standards, key)!r} must be one of {', '.join(allowed)}."))
    for key in _NUMBERS:
        value = getattr(standards, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1000:
            issues.append(Issue("error", f"{key} {value!r} must be a number more than 0."))
    if not (standards.tol_3_place <= standards.tol_2_place <= standards.tol_1_place) and not issues:
        issues.append(Issue("warning", "Tolerances usually get tighter with more decimal places."))
    if not (standards.in_tol_3_place <= standards.in_tol_2_place <= standards.in_tol_1_place) and not issues:
        issues.append(Issue("warning", "Inch tolerances usually get tighter with more decimal places."))
    if standards.in_tol_1_place > 0.25 and not issues:
        issues.append(Issue("warning", f"X.X ±{format_number(standards.in_tol_1_place)} in is wide for an inch "
                                       "drawing; .1 in is usual."))
    if len(standards.company) > 60:
        issues.append(Issue("warning", "The company name is long; the title block may cut it."))
    if standards.logo:
        if os.path.splitext(standards.logo)[1].lower() not in LOGO_EXTENSIONS:
            issues.append(Issue("error", f"Logo {standards.logo!r} must be SVG, PNG or JPEG."))
        elif vault_root and not os.path.isfile(logo_path(vault_root, standards)):
            issues.append(Issue("warning", f"Logo {standards.logo} is not in the vault folder."))
    if standards.projection == "first" and standards.standard.startswith("ASME"):
        issues.append(Issue("info", "ASME drawings normally use third-angle projection."))
    return issues


# --- file -------------------------------------------------------------------------------


def root_of(path: str) -> str:
    """The vault folder for a vault folder or its ref-file path."""
    path = os.path.abspath(os.path.expanduser(path))
    return os.path.dirname(path) if os.path.isfile(path) else path


def standards_path(vault_root: str) -> str:
    return os.path.join(root_of(vault_root), STANDARDS_FILE)


def vault_name(vault_root: str) -> str:
    """The name in the vault's ref file, else the folder name."""
    root = root_of(vault_root)
    try:
        refs = sorted(n for n in os.listdir(root) if n.endswith(".bwvault"))
        if refs:
            with open(os.path.join(root, refs[0]), encoding="utf-8") as handle:
                name = str(json.load(handle).get("name") or "").strip()
            if name:
                return name
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.basename(root.rstrip(os.sep))


def from_dict(data: dict) -> tuple:
    """(DrawingStandards, [Issue]): unknown keys are kept in ``extra``; a bad value keeps its
    default and gives a warning (a file must always open)."""
    standards = DrawingStandards()
    issues = []
    settings = data.get("settings", data) if isinstance(data, dict) else {}
    if not isinstance(settings, dict):
        settings = {}
    for key, value in settings.items():
        if key not in KEYS:
            standards.extra[key] = value
            continue
        found = set_text(standards, key, value)
        if found is not None:
            issues.append(Issue("warning", found.text + " The default is used."))
    version = data.get("version", 1) if isinstance(data, dict) else 1
    if (version is None or int(version) < 2) and not any(k in settings for k in IN_KEYS):
        migrate_inch_profile(standards)
    return standards, issues


def migrate_inch_profile(standards: DrawingStandards) -> bool:
    """A version-1 inch vault typed its sheet and tolerances in inches into the base fields: they
    become the inch profile where they differ from the mm defaults, and the mm profile gets its
    defaults back. Returns True when anything moved."""
    if standards.units != "in":
        return False
    defaults = DrawingStandards()
    moved = False
    for in_key, key in IN_KEYS.items():
        value = getattr(standards, key)
        if value != getattr(defaults, key):
            setattr(standards, in_key, value)
            setattr(standards, key, getattr(defaults, key))
            moved = True
    return moved


def to_dict(standards: DrawingStandards) -> dict:
    settings = dict(standards.extra)
    for key in KEYS:
        settings[key] = getattr(standards, key)
    return {"format": FORMAT, "version": FORMAT_VERSION, "settings": settings}


def load(vault_root: str) -> DrawingStandards:
    """The vault's standards; the defaults when it has no file (or an unreadable one)."""
    return load_with_issues(vault_root)[0]


def load_with_issues(vault_root: str) -> tuple:
    path = standards_path(vault_root)
    if not os.path.isfile(path):
        return DrawingStandards(), []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return DrawingStandards(), [Issue("warning", f"{path} cannot be read ({exc}); the defaults are used.")]
    return from_dict(data)


def exists(vault_root: str) -> bool:
    return os.path.isfile(standards_path(vault_root))


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


def save(vault_root: str, standards: DrawingStandards) -> str:
    """Write the vault's drawing_standards.json atomically; ValueError (nothing written) on an error."""
    problems = [i.text for i in validate(standards) if i.level == "error"]
    if problems:
        raise ValueError("\n".join(problems))
    path = standards_path(vault_root)
    _replace_bytes(path, (json.dumps(to_dict(standards), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def logo_path(vault_root: str, standards: DrawingStandards) -> str:
    """Absolute path of the logo, or "" when there is none."""
    if not standards.logo:
        return ""
    if os.path.isabs(standards.logo):
        return standards.logo
    return os.path.join(root_of(vault_root), standards.logo)


def import_logo(vault_root: str, source: str) -> str:
    """Copy a logo file into the vault as drawing_logo.<ext>; returns the name to store in ``logo``."""
    ext = os.path.splitext(source)[1].lower()
    if ext not in LOGO_EXTENSIONS:
        raise ValueError(f"{os.path.basename(source)}: the logo must be SVG, PNG or JPEG.")
    name = LOGO_BASENAME + (".jpg" if ext == ".jpeg" else ext)
    dest = os.path.join(root_of(vault_root), name)
    if os.path.abspath(source) != os.path.abspath(dest):
        tmp = f"{dest}.{uuid.uuid4().hex[:8]}.tmp"
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
    return name


# --- CSV rows (vault_setup's "standard" table) ------------------------------------------------


def csv_rows(standards: DrawingStandards) -> list:
    """Rows of the vault CSV: table=standard, code=setting, name=value, description=what it is."""
    return [{"table": "standard", "code": key, "name": to_text(standards, key),
             "description": DESCRIPTIONS.get(key, "")} for key in KEYS]


def from_texts(texts: dict, base: DrawingStandards | None = None) -> tuple:
    """(DrawingStandards, [Issue]) from setting -> text (CSV rows), over ``base`` (default: defaults)."""
    standards = dataclasses.replace(base) if base is not None else DrawingStandards()
    if base is not None:
        standards.extra = dict(base.extra)
    issues = []
    for key, text in texts.items():
        found = set_text(standards, key, text)
        if found is not None:
            issues.append(found)
    return standards, issues


def as_texts(standards: DrawingStandards) -> dict:
    return {key: to_text(standards, key) for key in KEYS}


# --- title-block text ------------------------------------------------------------------------


def company_name(standards: DrawingStandards, vault_root: str | None) -> str:
    return standards.company.strip() or (vault_name(vault_root) if vault_root else "")


def standard_note(standards: DrawingStandards) -> str:
    if standards.standard == "ISO GPS":
        return "Tolerancing per ISO 8015 (ISO GPS)"
    return f"Interpret per {standards.standard}"


def units_text(standards: DrawingStandards) -> str:
    return {"mm": "mm", "in": "inch"}.get(standards.units, standards.units)


def profile(standards: DrawingStandards, units: str | None = None) -> DrawingStandards:
    """The standards as a drawing in ``units`` ("mm" | "in"; default the vault's) sees them: the
    inch profile puts its sheet and tolerances in the base fields, is ASME (question 4: inch
    drawings on ANSI sheets under ASME; an ISO GPS vault's inch drawings cite ASME Y14.5-2018)
    and tolerances by decimal places (ISO 2768 is metric). The mm profile is the standards as
    they are. ``extra`` is shared."""
    units = normalize_units(units if units else standards.units)
    out = dataclasses.replace(standards, units=units)
    out.extra = standards.extra
    if units == "in":
        for in_key, key in IN_KEYS.items():
            setattr(out, key, getattr(standards, in_key))
        if not out.standard.startswith("ASME"):
            out.standard = ASME_DEFAULT
        if out.tolerance_mode == "iso2768":
            out.tolerance_mode = "decimal"
    return out


def normalize_units(units) -> str:
    return _choice("units", units) or "mm"


def dimension_format(units: str, tolerance: bool = False) -> str:
    """TechDraw's FormatSpec for a dimension on a drawing in ``units``: "%.2w" for mm (trailing
    zeros dropped, TechDraw's default) and "%.3f" for inch (ASME: as many places as the
    tolerance). ``tolerance``: the over/under tolerance spec ("%+.2w" / "%+.3f")."""
    spec = "%.3f" if normalize_units(units) == "in" else "%.2w"
    return spec.replace("%", "%+", 1) if tolerance else spec


DIMENSION_FORMATS = ("%.2w", "%.3f", "%+.2w", "%+.3f")  # what the filler may replace (ours or TechDraw's default)


def tolerance_text(value, units: str) -> str:
    """A tolerance as the block writes it: "0.5" for mm, ".005" for inch (ASME drops the zero)."""
    text = format_number(value)
    if normalize_units(units) == "in" and text.startswith("0."):
        return text[1:]
    return text


def projection_text(standards: DrawingStandards) -> str:
    return "Third angle" if standards.projection == "third" else "First angle"


GAP = "  ·  "  # between the pairs (Qt's SVG text collapses runs of any spaces, em spaces too)


def tolerance_lines(standards: DrawingStandards, lines: int = 4) -> list:
    """The general-tolerance block, ``lines`` strings (blank padded)."""
    unit = standards.units
    out = []
    if standards.tolerance_mode == "decimal":
        tol = [tolerance_text(getattr(standards, k), unit) for k in MM_NUMBERS[:3]]
        out.append(f"X.X ±{tol[0]}{GAP}X.XX ±{tol[1]}")
        out.append(f"X.XXX ±{tol[2]}{GAP}Angles ±{format_number(standards.tol_angle)}°")
    elif standards.tolerance_mode == "iso2768":
        out.append(f"ISO 2768-{standards.iso2768_class}")
        out.append(f"Linear and angular: class {standards.iso2768_class}")
    else:
        out.append("As noted on the drawing")
    if standards.surface_finish:
        out.append(f"Surface finish Ra {standards.surface_finish}" + (" µm" if unit == "mm" else " µin"))
    if standards.break_edges:
        out.append(f"Break sharp edges {standards.break_edges}")
    out = out[:lines]
    return out + [""] * (lines - len(out))


def sheet_size_text(sheet: str) -> str:
    """"B" for ANSI B, "A3" for ISO A3."""
    parts = sheet.split("_")
    return parts[1] if len(parts) > 1 else sheet


def template_name(sheet: str) -> str:
    if sheet not in SHEETS:
        sheet = DEFAULT_SHEET
    return sheet + ".svg"


def sheet_of_template(name: str) -> str | None:
    """The sheet key of one of our template file names (any folder), or None."""
    base = os.path.splitext(os.path.basename(str(name or "")))[0]
    base = re.sub(r"-[0-9a-f]{8,}\Z", "", base)  # customised copies: <key>-<hash>.svg
    return base if base in SHEETS else None


# --- who drew, checked and approved (from the journal) -----------------------------------------


def actor_name(actor: str) -> str:
    """``bruce@macbook`` -> ``bruce``."""
    return str(actor or "").split("@")[0]


def date_text(stamp: str) -> str:
    """An ISO time stamp as a local date (YYYY-MM-DD)."""
    try:
        when = _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return str(stamp)[:10]
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.date().isoformat()


def signatures(events, part_number: str) -> dict:
    """{"drawn": (name, date) | None, "checked": ..., "approved": ...} for the item's current revision.

    ``events`` are journal events in order (objects with op, args, actor, at:
    ``FolderVault._read_events()``). Drawn: the last check-in (else the
    creation). Checked: the submit for review (the author's checklist).
    Approved: the approval, or the release of an ECO that holds the item. A
    new revision (a reject, or an ECO opened for the item) clears checked and
    approved.
    """
    out = {"drawn": None, "checked": None, "approved": None}
    ecr_items, eco_items = {}, {}
    for event in events:
        args = getattr(event, "args", {}) or {}
        op = getattr(event, "op", "")
        sign = (actor_name(getattr(event, "actor", "")), date_text(getattr(event, "at", "")))
        if op == "raise_ecr":
            ecr_items[args.get("number")] = list(args.get("items") or [])
            continue
        if op == "open_eco":
            items = [pn for ecr in args.get("ecr_numbers") or [] for pn in ecr_items.get(ecr, [])]
            eco_items[args.get("number")] = items
            if part_number in items:
                out["checked"] = out["approved"] = None
            continue
        if op == "release_eco":
            if part_number in eco_items.get(args.get("number"), []):
                out["approved"] = sign
            continue
        if args.get("part_number") != part_number:
            continue
        if op in ("create_item", "checkin"):
            out["drawn"] = sign
        elif op == "submit":
            out["checked"] = sign
        elif op == "reject":
            out["checked"] = out["approved"] = None
        elif op == "approve":
            out["approved"] = sign
    return out


def revision_text(code: str, state: str | None = None) -> str:
    """The title block's revision: "WIP 02" before release (numeric), "A" after."""
    code = str(code or "").strip()
    if code.isdigit():
        return f"WIP {code}"
    return code


# --- the title block ---------------------------------------------------------------------------

# Template field names (freecad:editable) of the BruceWorx title block.
FIELDS = (
    "company", "title", "part_number", "revision", "material", "finish", "mass", "scale", "sheet",
    "units", "projection", "tol1", "tol2", "tol3", "tol4", "standard_note", "drawn_by", "drawn_date",
    "checked_by", "checked_date", "approved_by", "approved_date",
)


def format_mass(kilograms) -> str:
    """0.135 -> "135 g"; 2.5 -> "2.5 kg"; None or 0 -> ""."""
    try:
        kg = float(kilograms)
    except (TypeError, ValueError):
        return ""
    if kg <= 0:
        return ""
    if kg < 1:
        grams = kg * 1000
        return f"{grams:.0f} g" if grams >= 10 else f"{format_number(round(grams, 2))} g"
    return f"{format_number(round(kg, 3))} kg"


def title_block_fields(standards: DrawingStandards, vault_root: str | None = None, part: dict | None = None,
                       sign: dict | None = None, page: dict | None = None) -> dict:
    """field -> text for the title block. Fields with nothing to say are left out (the filler keeps
    what the sheet has).

    ``part``: title, part_number, revision, state, material, finish, mass_kg. ``sign``:
    :func:`signatures`. ``page``: scale, sheet ("1 of 2").
    """
    part, sign, page = part or {}, sign or {}, page or {}
    out = {}
    company = company_name(standards, vault_root)
    if company:
        out["company"] = company
    out["units"] = units_text(standards)
    out["projection"] = projection_text(standards)
    out["standard_note"] = standard_note(standards)
    for index, line in enumerate(tolerance_lines(standards), 1):
        out[f"tol{index}"] = line
    for key in ("title", "part_number", "material", "finish"):
        if part.get(key):
            out[key] = str(part[key])
    if part.get("revision"):
        out["revision"] = revision_text(part["revision"], part.get("state"))
    mass = format_mass(part.get("mass_kg"))
    if mass:
        out["mass"] = mass
    for role in ("drawn", "checked", "approved"):
        found = sign.get(role)
        if found:
            out[f"{role}_by"], out[f"{role}_date"] = found
    for key in ("scale", "sheet"):
        if page.get(key):
            out[key] = str(page[key])
    return out


def scale_text(scale: float) -> str:
    """0.5 -> "1:2", 2 -> "2:1", 1 -> "1:1" (nearest simple fraction)."""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return ""
    if scale <= 0:
        return ""
    best = (1, 1, abs(scale - 1))
    for den in range(1, 101):
        num = round(scale * den)
        if num < 1:
            continue
        err = abs(scale - num / den)
        if err < best[2] - 1e-9:
            best = (num, den, err)
    num, den, _err = best
    from math import gcd  # noqa: PLC0415

    g = gcd(num, den)
    return f"{num // g}:{den // g}"


# --- keeping what the user typed ----------------------------------------------------------------


def merge_fields(current: dict, wanted: dict, filled: dict, defaults: dict | None = None) -> tuple:
    """(texts, filled, kept): what to write into the sheet's EditableTexts.

    ``current`` is the sheet's EditableTexts, ``wanted`` the values the
    vault and part give, ``filled`` what we wrote last time (field -> text),
    ``defaults`` the template's own texts. A field is written when it still
    shows what we wrote last (or the template's default, or nothing, if we
    never wrote it); otherwise the user typed it and it is kept. Returns the
    new EditableTexts, the new ``filled`` map, and the kept field names.
    """
    defaults = defaults or {}
    texts = dict(current)
    new_filled = dict(filled)
    kept = []
    for name, value in wanted.items():
        if name not in current:
            continue
        have = current.get(name, "")
        if name in filled:
            ours = have == filled[name]
        else:
            ours = have.strip() == "" or have == defaults.get(name, "")
        if not ours:
            kept.append(name)
            new_filled.pop(name, None)
            continue
        texts[name] = value
        new_filled[name] = value
    return texts, new_filled, sorted(kept)
