# SPDX-License-Identifier: LGPL-2.1-or-later
"""BOM views of a vault and their CSV files (BW-PDM-06, plan approved 24 Oct 2026).

The BOM of an assembly revision is recorded at check-in (the FreeCAD side walks the open
assembly, bruceworx_bom.py) and kept per revision by :mod:`bruceworx.pdm`. This module turns
what the walker found into BOM lines, and a revision's BOM, its where-used and the outbox into
rows and CSV text. No FreeCAD import.

Quantities are counts (the user's answer: units later).
"""

from __future__ import annotations

import csv
import io
import os

from bruceworx import pdm

BOM_COLUMNS = ("Level", "Find", "Part number", "Revision", "Description", "Qty", "Total", "State", "Note")
FLAT_COLUMNS = ("Part number", "Revision", "Description", "Total", "State")
WHERE_USED_COLUMNS = ("Assembly", "Revision", "State", "Current", "Quantity", "Path", "List")
OUTBOX_COLUMNS = ("Assembly", "Revision", "ECO", "Released", "By", "SHA-256", "Find", "Part number",
                  "Child revision", "Description", "Qty")


def lines_from_walk(found, items) -> tuple:
    """(lines, missing) from the walker's rows ``[{"keys": [candidate P/Ns], "name", "qty"}]``, in
    order. A row is a vault item when one of its keys is; rows of the same item are summed and keep
    the first one's find number (1, 2, ... in order). ``items``: the vault's part numbers."""
    lines, missing, by_pn, by_name = [], [], {}, {}
    for row in found:
        qty = int(row.get("qty", 1))
        pn = next((k for k in row.get("keys", ()) if k and k in items), None)
        if pn is None:
            name = row.get("name") or "?"
            if name in by_name:
                by_name[name]["qty"] += qty
            else:
                by_name[name] = {"name": name, "qty": qty}
                missing.append(by_name[name])
            continue
        if pn in by_pn:
            by_pn[pn].qty += qty
        else:
            by_pn[pn] = pdm.BomLine(pn, qty, len(by_pn) + 1)
            lines.append(by_pn[pn])
    return lines, missing


def _describe(vault, pn):
    item = vault.items.get(pn)
    return item.description if item is not None else ""


def bom_rows(vault, pn, revision=None, flattened=False) -> list:
    """Rows to show or export: the indented BOM (with "newer" notes) or the flattened one."""
    model = getattr(vault, "model", vault)
    if flattened:
        rows = []
        start = model._start(pn, revision)
        totals = model.flattened(pn, start.code)
        resolved = {}
        for row in model.indented(pn, start.code):
            if row["leaf"] and not row["missing"]:
                resolved.setdefault(row["part_number"], set()).add(row["revision"])
        for child in sorted(totals):
            item = vault.items[child]
            rows.append({"part_number": child, "revision": "/".join(sorted(resolved.get(child, ()))),
                         "description": item.description, "total": totals[child], "state": item.state})
        return rows
    newer = {child: released for child, _pinned, released in model.newer_children(pn, revision)}
    rows = []
    for row in model.indented(pn, revision):
        item = vault.items.get(row["part_number"])
        note = "not a vault item" if row["missing"] else ""
        if row["level"] == 1 and row["part_number"] in newer:
            note = f"newer child released: {newer[row['part_number']]}"
        rows.append(dict(row, description=_describe(vault, row["part_number"]),
                         state=item.state if item is not None else "", note=note))
    return rows


def _csv(columns, rows) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def bom_csv(vault, pn, revision=None, flattened=False) -> str:
    rows = bom_rows(vault, pn, revision, flattened)
    if flattened:
        return _csv(FLAT_COLUMNS, [(r["part_number"], r["revision"], r["description"], r["total"], r["state"])
                                   for r in rows])
    return _csv(BOM_COLUMNS, [(r["level"], r["find"] or "", r["part_number"], r["revision"], r["description"],
                               r["qty"], r["total"], r["state"], r["note"]) for r in rows])


def where_used_csv(rows) -> str:
    """``rows``: release_check.where_used rows."""
    return _csv(WHERE_USED_COLUMNS, [
        (r["part_number"], r["revision"], r["state"], "yes" if r.get("released") else "", r.get("quantity") or "",
         " > ".join(r.get("path") or ()), "exact" if r.get("exact", True) else "may pick up") for r in rows])


def outbox_csv(rows) -> str:
    """The released BOMs of the outbox (FolderVault.outbox), one CSV row per BOM line."""
    out = []
    for row in rows:
        for line in row.get("lines", ()):
            out.append((row["part_number"], row["revision"], row.get("eco") or "", row.get("at", ""),
                        row.get("by", ""), row.get("sha256", ""), line.get("find", ""), line["child"],
                        line.get("rev", ""), line.get("description", ""), line["qty"]))
    return _csv(OUTBOX_COLUMNS, out)


def default_csv_name(pn, revision, flattened=False) -> str:
    return f"{pdm.display_part_number(pn, revision)}-BOM{'-flat' if flattened else ''}.csv"


def write_text(path, text) -> str:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path
