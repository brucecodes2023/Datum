# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault's structure for the Start page dashboard's Structure tab (UAT, 25 Oct 2026).

Three trees, built from one ``structure`` snapshot (no FreeCAD needed; the dashboard reads it on its
worker thread):

* Drawings: every drawing under the part or assembly it documents, then the parts and assemblies that
  have no drawing yet, then drawings whose part is missing.
* Product: each top assembly down to every sub-assembly and part, with quantities, each row with its
  drawings (an indented BOM).
* Where used: a part up through every assembly that uses it, to the top.

An assembly's lines are its recorded BOM (PDM-06, at check-in). An assembly checked in without one has
its lines read from the links in its latest file (``file_links``), marked "links", so a vault made
before BOMs were recorded still shows its structure.
"""

from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET

from bruceworx import pdm

_PN_FILE = re.compile(r"^(?P<pn>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)\.FCStd$", re.I)
LINK_TYPES = ("App::Link",)


# ----- reading a file's links ---------------------------------------------------------------------


def file_links(path: str) -> dict:
    """{P/N: occurrences} of the vault files an assembly file links to, from its Document.xml: one per
    App::Link to another file (an array link counts its elements)."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Document.xml"))
    types = {o.get("name"): o.get("type") for o in root.iter("Object") if o.get("type")}
    out = {}
    for obj in root.iter("Object"):
        name = obj.get("name")
        if types.get(name) not in LINK_TYPES:
            continue
        target, count = None, 1
        for prop in obj.iter("Property"):
            pname = prop.get("name")
            if pname == "LinkedObject":
                xlink = prop.find("XLink")
                if xlink is not None and xlink.get("file"):
                    target = os.path.basename(xlink.get("file"))
            elif pname == "ElementCount":
                value = prop.find("Integer")
                if value is not None and int(value.get("value", "0")) > 0:
                    count = int(value.get("value"))
        match = _PN_FILE.match(target or "")
        if match:
            pn = match.group("pn").upper()
            out[pn] = out.get(pn, 0) + count
    return out


# ----- the snapshot -------------------------------------------------------------------------------


def structure(vault, links=None) -> dict:
    """Everything the three trees need, JSON-ready. ``links(pn)`` gives an assembly's {child: qty}
    from its latest file (default: read it from the vault's object store)."""
    if links is None:
        links = lambda pn: _links_of(vault, pn)  # noqa: E731
    items, drawings = {}, {}
    for pn, item in vault.items.items():
        code = item.latest.code if item.latest else ""
        items[pn] = {"display": pdm.display_part_number(pn, code), "description": item.description,
                     "type": item.item_type, "state": item.state, "revision": code,
                     "obsolete": bool(getattr(item, "obsolete", False))}
        if item.drawing_of:
            drawings.setdefault(item.drawing_of, []).append(pn)
    bom, sources = {}, {}
    recorded = getattr(vault, "bom", {}) or {}
    for pn, item in vault.items.items():
        if item.item_type != "assembly":
            continue
        lines = recorded.get(pn)
        if lines:
            bom[pn] = [[child, qty] for child, qty in lines]
            sources[pn] = "bom"
            continue
        try:
            found = links(pn) or {}
        except Exception:  # noqa: BLE001 -- an unreadable file: no lines, said so
            sources[pn] = "unreadable"
            continue
        found = {c: q for c, q in found.items() if c in vault.items and c != pn}
        if found:
            bom[pn] = [[child, qty] for child, qty in sorted(found.items())]
            sources[pn] = "links"
    for lst in drawings.values():
        lst.sort()
    return {"items": items, "bom": bom, "sources": sources, "drawings": drawings}


def _links_of(vault, pn):
    version = vault.latest_version(pn) if hasattr(vault, "latest_version") else None
    if version is None:
        return {}
    path = os.path.join(vault.root, version.object_name)
    if not os.path.exists(path):
        return {}
    return file_links(path)


# ----- the trees (rows: dicts with "children") ----------------------------------------------------


def _node(s, pn, qty=None):
    info = s["items"].get(pn, {"display": pn, "description": "", "type": "", "state": "", "revision": ""})
    return {"pn": pn, "display": info["display"], "description": info["description"], "type": info["type"],
            "state": info["state"], "qty": qty, "drawings": list(s["drawings"].get(pn, ())),
            "source": s["sources"].get(pn, ""), "children": []}


def tops(s) -> list:
    """Assemblies no other assembly uses, in P/N order."""
    used = {child for lines in s["bom"].values() for child, _q in lines}
    return sorted(pn for pn, info in s["items"].items()
                  if info["type"] == "assembly" and pn not in used and not info.get("obsolete"))


def product_tree(s) -> list:
    """[top node]: each assembly's lines below it, recursively; a cycle in a damaged vault is cut."""
    def down(pn, qty, path):
        node = _node(s, pn, qty)
        if pn in path:
            node["cycle"] = True
            return node
        for child, q in s["bom"].get(pn, ()):
            node["children"].append(down(child, q, path | {pn}))
        return node

    return [down(top, None, frozenset()) for top in tops(s)]


def flattened(s, top) -> dict:
    """{P/N: total quantity} of everything under ``top`` (quantities multiplied down the levels)."""
    totals = {}

    def walk(pn, factor, path):
        for child, q in s["bom"].get(pn, ()):
            if child in path:
                continue
            totals[child] = totals.get(child, 0) + factor * q
            walk(child, factor * q, path | {pn})

    walk(top, 1, frozenset())
    return totals


def drawing_tree(s) -> dict:
    """{"documented": [item node with its drawings], "undrawn": [part and assembly nodes without one],
    "orphans": [drawing nodes whose part is not in the vault]}."""
    documented, undrawn = [], []
    for pn in sorted(s["items"]):
        info = s["items"][pn]
        if info["type"] == "drawing" or info.get("obsolete"):
            continue
        node = _node(s, pn)
        if node["drawings"]:
            node["children"] = [_node(s, d) for d in node["drawings"]]
            documented.append(node)
        else:
            undrawn.append(node)
    orphans = [_node(s, d) for owner, dwgs in sorted(s["drawings"].items()) if owner not in s["items"] for d in dwgs]
    return {"documented": documented, "undrawn": undrawn, "orphans": orphans}


def where_used(s, pn) -> list:
    """[parent node]: each assembly that uses ``pn`` (with its quantity there), up to the tops."""
    parents = {}
    for parent, lines in s["bom"].items():
        for child, q in lines:
            parents.setdefault(child, []).append((parent, q))

    def up(child, path):
        out = []
        for parent, q in sorted(parents.get(child, ())):
            node = _node(s, parent, q)
            if parent in path:
                node["cycle"] = True
            else:
                node["children"] = up(parent, path | {parent})
            out.append(node)
        return out

    return up(pn, frozenset({pn}))
