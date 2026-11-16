# SPDX-License-Identifier: LGPL-2.1-or-later
"""Metric and inch (PDM-09 A): the vault's unit default and the two profiles.

Every length FreeCAD stores is millimetres; units are a view. The vault's
``units`` setting (``drawing_standards.json``, ``mm`` or ``in``) is the default
for documents BruceWorx creates (New Part in Vault, the Start page's New
items, import, New Project). Two profiles, nothing else:

    mm  ->  Standard (mm, kg, s, °)       schema 0, 2 decimals
    in  ->  Imperial decimal (in, lb)     schema 3, 3 decimals

A document's own ``UnitSystem`` is the override (the status bar's unit button,
as in stock FreeCAD). Decimals follow the schema's length unit through the
``[BW]`` block in Gui/Application.cpp, which reads ``Units/DecimalsMetric`` and
``Units/DecimalsInch`` (the preference pack writes 2 and 3).

Also here: the status-bar readout's " · mm" / " · inch" beside the vault's
name, the Vault tab's badge when the active document's units differ from the
vault's, and the document observer that gives new documents the default.

For other modules (the New Project engine): :func:`apply_units` and
:func:`default_units`. A project's units go through :data:`DEFAULT_HOOKS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNITS_PATH = "User parameter:BaseApp/Preferences/Units"
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
DECIMALS_KEYS = {"mm": "DecimalsMetric", "in": "DecimalsInch"}
MM_PER_IN = 25.4


@dataclass(frozen=True)
class Profile:
    units: str  # "mm" | "in"
    index: int  # Document.UnitSystem / FreeCAD.Units schema index (stable in UnitsSchemasData.h)
    name: str  # the schema's internal name
    description: str  # the schema's menu text (English)
    decimals: int
    label: str  # what the status bar and title block say


PROFILES = {
    "mm": Profile("mm", 0, "Internal", "Standard (mm, kg, s, °)", 2, "mm"),
    "in": Profile("in", 3, "ImperialDecimal", "Imperial decimal (in, lb)", 3, "inch"),
}
# Schemas whose basic length unit is an inch or a foot (UnitsSchemasData.h): shown as "in".
INCH_SCHEMAS = {2, 3, 5, 7}

# Callables ``(doc) -> "mm" | "in" | None`` asked before the vault (a project's units, PDM-09 C).
DEFAULT_HOOKS = []

_state = {"observer": None, "installed": False}


# --- pure -------------------------------------------------------------------------------------


def norm_units(text) -> str:
    """"mm" or "in" from loose text: "inch", "Imperial decimal (in, lb)", "ft-in", "mm/kg"."""
    text = str(text or "").strip().lower()
    if text in ("in", "inch", "inches", '"') or text.startswith(("inch", "imperial", "us customary", "building us")):
        return "in"
    if re.search(r"\(in\b|\bin,|\binch|\bft-in\b|\(ft\b", text):
        return "in"
    return "mm"


def profile(units) -> Profile:
    return PROFILES[norm_units(units)]


def units_of_schema(schema) -> str:
    """"mm" or "in" for a schema given as its index or its description."""
    if isinstance(schema, int):
        return "in" if schema in INCH_SCHEMAS else "mm"
    text = str(schema or "")
    if text.isdigit():
        return units_of_schema(int(text))
    return norm_units(text)


def length_text(mm: float, units: str) -> str:
    """A stored length shown in the profile's decimals: 3.175 -> "0.125 in" / "3.18 mm"."""
    p = profile(units)
    value = float(mm) / (MM_PER_IN if p.units == "in" else 1.0)
    return f"{value:.{p.decimals}f} {p.units}"


# --- FreeCAD ------------------------------------------------------------------------------------


def document_units(doc=None) -> str:
    """"mm" or "in": the document's UnitSystem, else the application's schema."""
    import FreeCAD

    doc = doc or FreeCAD.ActiveDocument
    if doc is not None and "UnitSystem" in doc.PropertiesList:
        try:
            return units_of_schema(str(doc.UnitSystem))
        except Exception:  # noqa: BLE001
            pass
    try:
        return units_of_schema(int(FreeCAD.Units.getSchema()))
    except Exception:  # noqa: BLE001
        return "mm"


def vault_root() -> str:
    try:
        import bruceworx_drawing

        return bruceworx_drawing.vault_root()
    except Exception:  # noqa: BLE001
        return ""


def vault_units(root=None) -> str:
    """The vault's units setting (drawing standards); "" when no vault is open."""
    root = vault_root() if root is None else root
    if not root:
        return ""
    try:
        from heuretes_freecad import drawing_standards

        return norm_units(drawing_standards.load(root).units)
    except Exception:  # noqa: BLE001
        return ""


def default_units(root=None, doc=None) -> str:
    """The units a new document gets: a project's (DEFAULT_HOOKS), else the vault's, else the
    application's UserSchema."""
    for hook in list(DEFAULT_HOOKS):
        try:
            found = hook(doc)
        except Exception:  # noqa: BLE001
            found = None
        if found:
            return norm_units(found)
    found = vault_units(root)
    if found:
        return found
    try:
        import FreeCAD

        return units_of_schema(int(FreeCAD.ParamGet(UNITS_PATH).GetInt("UserSchema", 0)))
    except Exception:  # noqa: BLE001
        return "mm"


def apply_units(doc, units=None) -> str:
    """Give ``doc`` the profile of ``units`` ("mm" | "in"; default :func:`default_units`): its
    UnitSystem, and the GUI's schema and decimals at once when it is the active document. Returns
    the units applied. Lengths are not converted (they are millimetres either way)."""
    import FreeCAD

    p = profile(units if units else default_units(doc=doc))
    if "UnitSystem" in doc.PropertiesList:
        try:
            wanted = str(FreeCAD.Units.listSchemas(p.index))  # the menu text, translated
        except Exception:  # noqa: BLE001
            wanted = p.description
        if str(doc.UnitSystem) != wanted:
            doc.UnitSystem = p.index
    if FreeCAD.GuiUp and FreeCAD.ActiveDocument is doc:
        try:
            FreeCAD.setActiveDocument(doc.Name)  # re-runs the schema (and [BW] decimals) switch
        except Exception:  # noqa: BLE001
            pass
    return p.units


def write_decimals(store=None) -> None:
    """The two decimals keys the [BW] block reads, when missing (the pack writes them too)."""
    import FreeCAD

    group = FreeCAD.ParamGet(UNITS_PATH)
    for units, key in DECIMALS_KEYS.items():
        if group.GetInt(key, -1) < 0:
            group.SetInt(key, PROFILES[units].decimals)


# --- readouts -------------------------------------------------------------------------------------


def status_suffix(root=None) -> str:
    """" · mm" or " · inch" for the status bar's vault readout."""
    units = vault_units(root)
    return f" · {PROFILES[units].label}" if units else ""


def badge(doc=None, root=None) -> str:
    """"in" or "mm" when the active document's units differ from the vault's, else ""."""
    import FreeCAD

    doc = doc or FreeCAD.ActiveDocument
    vault = vault_units(root)
    if doc is None or not vault:
        return ""
    mine = document_units(doc)
    return mine if mine != vault else ""


# --- new documents ---------------------------------------------------------------------------------


class _Observer:
    """A document made while a vault is open takes the vault's (or project's) units. A file being
    opened passes through here too; its own UnitSystem is restored right after, so nothing is lost."""

    def slotCreatedDocument(self, doc):
        try:
            if not vault_root():
                return
            apply_units(doc, default_units(doc=doc))
        except Exception as exc:  # noqa: BLE001 -- never break document creation
            import FreeCAD

            FreeCAD.Console.PrintLog(f"BruceWorx units: {exc}\n")


def install_observer():
    import FreeCAD

    if _state["observer"] is None:
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])
    return _state["observer"]


def refresh():
    """The status bar and the Vault tab badge after the vault's standards changed."""
    try:
        import bruceworx_statusbar

        bruceworx_statusbar.update()
    except Exception:  # noqa: BLE001
        pass


def install():
    """GUI: the observer, the status-bar suffix and the Vault tab's badge."""
    if _state["installed"]:
        return
    _state["installed"] = True
    import FreeCAD

    try:
        write_decimals()
    except Exception as exc:  # noqa: BLE001
        FreeCAD.Console.PrintLog(f"BruceWorx units: decimals keys not written: {exc}\n")
    install_observer()
    try:
        import bruceworx_statusbar

        if status_suffix not in bruceworx_statusbar.VAULT_SUFFIX:
            bruceworx_statusbar.VAULT_SUFFIX.insert(0, status_suffix)  # right beside the name
    except Exception as exc:  # noqa: BLE001
        FreeCAD.Console.PrintLog(f"BruceWorx units: no status-bar readout: {exc}\n")
    try:
        import bruceworx_rightpanel

        bruceworx_rightpanel.BADGES["BWVault"] = badge
    except Exception as exc:  # noqa: BLE001
        FreeCAD.Console.PrintLog(f"BruceWorx units: no Vault tab badge: {exc}\n")
