# SPDX-License-Identifier: LGPL-2.1-or-later
"""From-to cut list (BW-WIR-04). Plain Python: rows are dicts.

``objects.cut_list`` builds the rows from the wires in a document. A wire
that crosses a joint has a range of lengths; its cut length is the longest
one plus the allowance, never the length in the pose the file was saved in.
"""

from __future__ import annotations

import csv
import io

SCHEMA = "bruceworx.wire-cut-list.v1"
COLUMNS = ("wire", "gauge", "color", "from", "to", "length_mm", "min_mm", "max_mm", "allowance_mm", "cut_mm", "status")


def cut_length(length: float, maximum: float | None, allowance: float, round_up_to: float = 1.0) -> float:
    """Longest routed length plus the allowance, rounded up to a whole
    ``round_up_to`` mm (0 leaves it unrounded)."""
    longest = max(length, maximum or 0.0)
    total = longest + allowance
    if round_up_to > 0:
        steps = total / round_up_to
        whole = int(steps)
        if steps - whole > 1e-9:
            whole += 1
        return whole * round_up_to
    return total


def row(name, gauge, color, start, end, length, minimum=None, maximum=None, allowance=0.0, status="ok",
        round_up_to=1.0) -> dict:
    return {
        "wire": name,
        "gauge": gauge,
        "color": color,
        "from": start,
        "to": end,
        "length_mm": round(length, 3),
        "min_mm": round(minimum if minimum is not None else length, 3),
        "max_mm": round(maximum if maximum is not None else length, 3),
        "allowance_mm": round(allowance, 3),
        "cut_mm": round(cut_length(length, maximum, allowance, round_up_to), 3) if status == "ok" else "",
        "status": status,
    }


def totals(rows) -> list[dict]:
    """Wire to buy: cut length summed per gauge and colour."""
    sums: dict[tuple, list] = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        entry = sums.setdefault((r["gauge"], r["color"]), [0, 0.0])
        entry[0] += 1
        entry[1] += float(r["cut_mm"])
    return [
        {"gauge": gauge, "color": color, "wires": count, "total_mm": round(total, 3)}
        for (gauge, color), (count, total) in sorted(sums.items())
    ]


def to_csv(rows) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({key: r.get(key, "") for key in COLUMNS})
    return out.getvalue()


def as_json(rows) -> dict:
    rows = list(rows)
    return {
        "schema": SCHEMA,
        "ok": all(r["status"] == "ok" for r in rows),
        "wires": rows,
        "totals": totals(rows),
    }
