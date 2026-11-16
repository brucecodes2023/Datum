# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stock material vault items for routed wires, tubes, hoses and sleeving (BW-ROU-06, 30 Oct 2026).

A stock item is a COTS vault item (category "Stock material") bought by the metre: a file with an App::Part that
carries ``StockKind``, ``StockKey`` (what finds it again: kind and spec), ``Manufacturer`` and
``ManufacturerPartNumber`` (what is ordered), ``QuantityUnit`` m, ``ScrapPercent`` (default 10) and ``PurchaseUnit``
(metres of one spool or length; 0 rounds nothing). ``stock_of(item)`` says what a routed item is cut from;
``ensure_stock_item`` finds the vault item or makes it; ``assign`` puts the part number, manufacturer and MPN on the
item, where the BOM walker (bruceworx_bom) reads them. The maths and the purchasing BOM are
``bruceworx/stock.py`` and ``bruceworx/bom.py``, FreeCAD-free.
"""

import FreeCAD as App

from heuretes_freecad import stock as st

CATEGORY = st.CATEGORY


def _br():
    import bruceworx_routing

    return bruceworx_routing


def stock_of(obj):
    """(kind, key, description) of the stock a routed item is cut from."""
    br = _br()
    kind = br.kind_of(obj)
    if kind == br.WIRE:
        colour = br._colour_name(obj.Colour)
        insulation = obj.Insulation if obj.Insulation != "Auto" else "PVC"
        return ("Wire", st.stock_key("Wire", obj.Gauge, colour, insulation),
                f"Wire, {obj.Gauge}, {colour}, {insulation}")
    if kind == br.PIPE:
        size = obj.Size if obj.Size != "Custom" else f"{obj.OD.Value:g} x {obj.WallThickness.Value:g}"
        material = (obj.Material or "").strip()
        return ("Tube", st.stock_key("Tube", size, material),
                f"Tube, {size}" + (f", {material}" if material else ""))
    if kind == br.HOSE:
        size = f"{obj.ID.Value:g} x {obj.OD.Value:g}"
        return "Hose", st.stock_key("Hose", size), f"Hose, {size}"
    od = f"{obj.OD.Value:g}" if "OD" in obj.PropertiesList else ""
    return "Sleeving", st.stock_key("Sleeving", od), f"Sleeving, {od} mm"


def make_stock_file(path, pn, kind, key, description, manufacturer, mpn, scrap, unit, vault_root):
    """Write the file of a Stock material item: an App::Part with the vault's part properties (ItemType cots, the
    description, revision 01) and the stock properties above. Saved and closed."""
    from heuretes_freecad import partprops, pdm

    doc = App.newDocument(pn.replace("-", "_"), hidden=True)
    try:
        doc.Label = pn
        main = doc.addObject("App::Part", "Part")
        main.Label = description
        report = partprops.load_schema(extra_layers=partprops.vault_layers(vault_root)).apply(main)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise ValueError("; ".join(clash))
        main.PartNumber = pn
        main.Revision = pdm.first_prerelease()
        if "Description" in main.PropertiesList:
            main.Description = description
        if "ItemType" in main.PropertiesList:
            main.ItemType = "cots"
        for name, typ, value, doc_text in (
                ("StockKind", "App::PropertyString", kind, "Wire, Tube, Hose or Sleeving"),
                ("StockKey", "App::PropertyString", key, "What finds this stock item again: the kind and its spec"),
                ("QuantityUnit", "App::PropertyString", "m", "Bought by the metre"),
                ("ScrapPercent", "App::PropertyFloat", float(scrap), "Scrap allowance added to the summed length"),
                ("PurchaseUnit", "App::PropertyFloat", float(unit or 0.0),
                 "Metres in one spool or length bought (0: order the exact length)")):
            if name not in main.PropertiesList:
                main.addProperty(typ, name, "Stock", doc_text)
            setattr(main, name, value)
        for name, value in (("Manufacturer", manufacturer), ("ManufacturerPartNumber", mpn)):
            if name not in main.PropertiesList:
                main.addProperty("App::PropertyString", name, "Stock", "Printed on the BOM: what is ordered")
            setattr(main, name, value)
        for name in ("StockKind", "StockKey", "QuantityUnit"):
            main.setEditorMode(name, 1)
        doc.recompute()
        doc.saveAs(path)
    finally:
        App.closeDocument(doc.Name)


def ensure_stock_item(s, kind, key, description, manufacturer="", mpn="", scrap=st.DEFAULT_SCRAP, unit=0.0,
                      program=""):
    """The part number of the stock item with this key, made (numbered, WIP 01, filed under "Stock material")
    when the vault has none. ``s``: the vault client's Session (or anything with .vault and .root).
    Returns (part number, made)."""
    from heuretes_freecad import vault_setup

    pn = st.find_by_key(s.vault, key)
    if pn:
        return pn, False

    def make(path, number):
        make_stock_file(path, number, kind, key, description, manufacturer, mpn, scrap, unit, s.root)

    pn = vault_setup.new_item(s.vault, "cots", description, make, program, category=CATEGORY)
    return pn, True


def assign(obj, pn, manufacturer="", mpn=""):
    """Record the stock item on a routed item: its part number, manufacturer and MPN."""
    obj.StockItem = pn
    obj.Manufacturer = manufacturer
    obj.ManufacturerPartNumber = mpn


def assign_item(obj, row):
    """``row``: an entry of ``bruceworx.stock.list_stock``."""
    assign(obj, row["pn"], row["manufacturer"], row["mpn"])


def make_from_item(s, obj, manufacturer, mpn, scrap=st.DEFAULT_SCRAP, unit=0.0, program=""):
    """Create (or find) the stock item for ``obj``'s properties and put it on ``obj``. Returns (pn, made)."""
    kind, key, description = stock_of(obj)
    pn, made = ensure_stock_item(s, kind, key, description, manufacturer, mpn, scrap, unit, program)
    assign(obj, pn, manufacturer, mpn)
    return pn, made
