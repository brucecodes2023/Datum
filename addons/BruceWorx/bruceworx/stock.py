# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stock material and the purchasing BOM (BW-ROU-06, 30 Oct 2026). No FreeCAD import.

Wire, tube, hose and sleeving are bought by the metre, so each kind is one *Stock material* vault item (category
"Stock material", ``QuantityUnit`` m, with its Manufacturer and MPN: ordering uses those, the P/N is for finding it).
Every routed item names its stock item; the harness's BOM lists the stock items by length. The flattened vault BOM
sums the lengths across all harnesses and quantities, and this module turns that sum into what to buy:

    L_buy = ceil((1 + s) * sum(n_k L_k) / L_unit) * L_unit

``s`` is the stock item's scrap allowance (``ScrapPercent``, default 10 %) and ``L_unit`` its purchase unit
(``PurchaseUnit`` in metres, a 30 m spool; 0 or empty rounds nothing). The two live in the stock item's file; a vault
without FreeCAD reads them from its Document.xml (:func:`read_stock`).
"""

from __future__ import annotations

import math
import tempfile
import xml.etree.ElementTree as ET
import zipfile

CATEGORY = "Stock material"
HARNESS_CATEGORY = "Harnesses"
DEFAULT_SCRAP = 10.0
KINDS = ("Wire", "Tube", "Hose", "Sleeving")
PURCHASING_COLUMNS = ("Part number", "Description", "Manufacturer", "MPN", "Used", "Scrap", "Purchase unit", "Buy")
_PROPS = ("StockKind", "StockKey", "ScrapPercent", "PurchaseUnit", "QuantityUnit", "Manufacturer",
          "ManufacturerPartNumber")


def buy_length(used, scrap_percent=DEFAULT_SCRAP, unit=0.0) -> float:
    """Metres to order for ``used`` metres: scrap added, then up to a whole number of purchase units."""
    used = max(0.0, float(used))
    need = used * (1.0 + max(0.0, float(scrap_percent)) / 100.0)
    unit = float(unit or 0.0)
    if unit > 0:
        return round(max(1, math.ceil(need / unit - 1e-9)) * unit, 6) if need > 1e-12 else 0.0
    return round(need, 6)


def read_stock(path) -> dict:
    """The stock properties of a stock item's FCStd (Document.xml), or {}: StockKind, StockKey, ScrapPercent (float),
    PurchaseUnit (float, metres), QuantityUnit, Manufacturer, ManufacturerPartNumber."""
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("Document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return {}
    for obj in root.iter("Object"):
        found = {}
        for prop in obj.iter("Property"):
            name = prop.get("name")
            if name not in _PROPS:
                continue
            child = next(iter(prop), None)
            if child is not None:
                found[name] = child.get("value", "")
        if "StockKind" in found:
            for key in ("ScrapPercent", "PurchaseUnit"):
                try:
                    found[key] = float(found.get(key, ""))
                except ValueError:
                    found[key] = DEFAULT_SCRAP if key == "ScrapPercent" else 0.0
            return found
    return {}


def info_from_vault(vault, part_numbers, folder=None) -> dict:
    """{P/N: {"scrap", "unit", ...}} read from the latest file of each stock item (an item with no file, or no stock
    properties, gets the default scrap and no purchase unit)."""
    out = {}
    own = folder is None
    folder = folder or tempfile.mkdtemp(prefix="bw-stock-")
    try:
        for pn in part_numbers:
            props = {}
            try:
                if vault.latest_version(pn) is not None:
                    props = read_stock(vault.fetch(pn, folder))
            except Exception:  # noqa: BLE001 -- a file that cannot be read buys with the defaults
                props = {}
            out[pn] = {"scrap": props.get("ScrapPercent", DEFAULT_SCRAP), "unit": props.get("PurchaseUnit", 0.0),
                       "props": props}
    finally:
        if own:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
    return out


def purchasing_rows(flat_rows, info=None) -> list:
    """Rows of the flattened BOM (bom.bom_rows(..., flattened=True)) that are metered, as what to buy.

    ``info``: {P/N: {"scrap": percent, "unit": metres}} (or a callable P/N -> that); missing entries buy with the
    default scrap and no rounding. A count line (no unit) is not stock and is left out."""
    out = []
    for row in flat_rows:
        if not row.get("unit"):
            continue
        pn = row["part_number"]
        entry = (info(pn) if callable(info) else (info or {}).get(pn)) or {}
        scrap = float(entry.get("scrap", DEFAULT_SCRAP))
        unit = float(entry.get("unit", 0.0) or 0.0)
        used = float(row["total"])
        out.append({"part_number": pn, "description": row.get("description", ""),
                    "manufacturer": row.get("manufacturer", ""), "mpn": row.get("mpn", ""), "used": used,
                    "scrap": scrap, "purchase_unit": unit, "buy": buy_length(used, scrap, unit),
                    "unit": row["unit"]})
    return out


def _num(value) -> str:
    return f"{float(value):g}"


def purchasing_cells(rows) -> list:
    """CSV cells: lengths in metres, ``Scrap`` as a percent, ``Purchase unit`` blank when nothing is rounded."""
    return [(r["part_number"], r["description"], r["manufacturer"], r["mpn"], f"{_num(r['used'])} {r['unit']}",
             f"{_num(r['scrap'])} %", f"{_num(r['purchase_unit'])} {r['unit']}" if r["purchase_unit"] else "",
             f"{_num(r['buy'])} {r['unit']}") for r in rows]


def stock_key(kind, *parts) -> str:
    """The key a stock item is found by: ``wire|22 AWG|red|PVC``, lower-cased and trimmed so a retyped spec matches."""
    return "|".join([str(kind).strip().lower()] + [" ".join(str(p).split()).lower() for p in parts])


def find_by_key(vault, key, folder=None):
    """The part number of the stock item with this key, or None."""
    own = folder is None
    folder = folder or tempfile.mkdtemp(prefix="bw-stock-")
    try:
        for pn, item in vault.items.items():
            if getattr(item, "category", "") != CATEGORY:
                continue
            try:
                props = read_stock(vault.fetch(pn, folder))
            except Exception:  # noqa: BLE001
                continue
            if props.get("StockKey") == key:
                return pn
    finally:
        if own:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
    return None


def list_stock(vault, kind="", folder=None) -> list:
    """Stock items of the vault (category "Stock material"), optionally of one kind: [{"pn", "description", "kind",
    "key", "manufacturer", "mpn", "scrap", "unit"}], sorted by description."""
    own = folder is None
    folder = folder or tempfile.mkdtemp(prefix="bw-stock-")
    rows = []
    try:
        for pn, item in vault.items.items():
            if getattr(item, "category", "") != CATEGORY:
                continue
            try:
                props = read_stock(vault.fetch(pn, folder))
            except Exception:  # noqa: BLE001
                props = {}
            if kind and props.get("StockKind", "").lower() != kind.lower():
                continue
            rows.append({"pn": pn, "description": item.description, "kind": props.get("StockKind", ""),
                         "key": props.get("StockKey", ""), "manufacturer": props.get("Manufacturer", ""),
                         "mpn": props.get("ManufacturerPartNumber", ""),
                         "scrap": props.get("ScrapPercent", DEFAULT_SCRAP), "unit": props.get("PurchaseUnit", 0.0)})
    finally:
        if own:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
    return sorted(rows, key=lambda r: r["description"].lower())
