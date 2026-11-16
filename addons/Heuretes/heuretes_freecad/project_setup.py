# SPDX-License-Identifier: LGPL-2.1-or-later
"""New Project (BW-PDM-09, plan section 4; built 26 Oct 2026): the typed structure as a tree, its
checks, the number plan and the order of work. Free of FreeCAD, like vault_setup.py; the file
writer (bruceworx_project.py in the BruceWorx module) does steps 2-5 of the plan with these.

The structure, as typed (plan): one line per item; two spaces or a tab per level; ``x3`` is the
quantity per parent (default 1); a line with children is an assembly; ``cots`` marks a bought part
(no drawing); a line that starts with a P/N reuses that vault item. The same name in two places is
the same item (a bolt in two sub-assemblies), so the tree is a DAG; the preview marks it
"used in 2 places" (the duplicate bubble, answer 10) and you rename a line to make two items.
A paste from a spreadsheet with columns level, name, qty, type reads the same way, and so does a
CSV file with those columns (:func:`parse_table`).

Example::

    Shop cart
      Frame x2
        Side rail x2
        Cross bar
      Deck plate
      Caster 4 in x4 cots
"""

from __future__ import annotations

import copy
import csv
import io
import os
import re
from dataclasses import dataclass, field

from heuretes_freecad import numbering, pdm

KINDS = ("part", "assembly", "cots")
INDENT = 2
_QTY = re.compile(r"^(?:[x×]\s*(\d+)|(\d+)\s*[x×])$", re.IGNORECASE)
_COTS = {"cots", "(cots)", "[cots]", "bought", "purchased"}
_CODE_CHARS = re.compile(r"[^A-Z0-9]+")
DRAWING_KIND = "drawing"


@dataclass
class Issue:
    level: str  # "error" (stops Create) | "warning" | "info"
    text: str
    line: int = 0  # 1-based line of the typed structure, 0 for the whole


@dataclass
class Node:
    """One item of the structure. ``children`` are (Node, quantity per parent)."""

    name: str
    kind: str = "part"
    pn: str = ""  # an existing vault item's number ("" for a new item)
    children: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # every line it appears on
    uses: int = 0  # how many parents hold it

    @property
    def key(self) -> str:
        return self.pn or self.name.lower()

    @property
    def existing(self) -> bool:
        return bool(self.pn)

    @property
    def wants_drawing(self) -> bool:
        return not self.existing and self.kind != "cots"


@dataclass
class Plan:
    root: Node | None = None
    nodes: dict = field(default_factory=dict)  # key -> Node, in first-seen order
    issues: list = field(default_factory=list)
    numbers: dict = field(default_factory=dict)  # key -> P/N (preview or taken)
    drawing_numbers: dict = field(default_factory=dict)  # key -> the drawing's P/N
    document_types: dict = field(default_factory=dict)  # key -> code; "drawing" -> code

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.level == "error"]

    @property
    def ok(self) -> bool:
        return self.root is not None and not self.errors

    @property
    def duplicates(self) -> list:
        """Names used in more than one place (one item, the duplicate bubble)."""
        return [n.name for n in self.nodes.values() if n.uses > 1]

    @property
    def new_items(self) -> list:
        return [n for n in self.nodes.values() if not n.existing]


# ----- parsing ------------------------------------------------------------------------------------


def _split_line(text: str):
    """(name, qty, kind, pn) of one typed line (without its indentation)."""
    words = text.strip().split()
    qty, kind, pn = 1, None, ""
    rest = []
    for word in words:
        match = _QTY.match(word)
        if match and (rest or pn):
            qty = int(match.group(1) or match.group(2))
            continue
        if word.lower() in _COTS:
            kind = "cots"
            continue
        if not rest and not pn and numbering.is_part_number(word):
            pn = pdm.strip_revision(word.upper())
            continue
        rest.append(word)
    # "Side rail x 2": a bare number after x
    cleaned = []
    skip = False
    for i, word in enumerate(rest):
        if skip:
            skip = False
            continue
        if word.lower() in ("x", "×") and i + 1 < len(rest) and rest[i + 1].isdigit():
            qty = int(rest[i + 1])
            skip = True
            continue
        cleaned.append(word)
    name = " ".join(cleaned).strip()
    return name, qty, kind, pn


def _level(raw: str):
    """(indent level, text) of a raw line; tabs count as one level."""
    spaces = 0
    for ch in raw:
        if ch == "\t":
            spaces += INDENT
        elif ch == " ":
            spaces += 1
        else:
            break
    return spaces // INDENT, raw.strip()


def looks_like_table(text: str) -> bool:
    """A spreadsheet paste: tab-separated rows whose first cell is a level number (after an
    optional header row)."""
    rows = [r for r in (text or "").splitlines() if r.strip()]
    if not rows or not any("\t" in r for r in rows):
        return False
    body = rows[1:] if not rows[0].split("\t")[0].strip().lstrip("-").isdigit() else rows
    return bool(body) and all(r.split("\t")[0].strip().lstrip("-").isdigit() for r in body)


def table_to_text(rows, issues=None) -> str:
    """Rows [level, name, qty, type] (a spreadsheet paste or CSV) as the indented text."""
    issues = issues if issues is not None else []
    cleaned = []
    for n, row in enumerate(rows, 1):
        cells = [str(c).strip() for c in row] + ["", "", "", ""]
        if not any(cells[:4]):
            continue
        if n == 1 and not cells[0].lstrip("-").isdigit():
            continue  # the header row
        if not cells[0].lstrip("-").isdigit():
            issues.append(Issue("error", f"row {n}: the level must be a number, not {cells[0]!r}", n))
            continue
        cleaned.append((int(cells[0]), cells[1], cells[2], cells[3]))
    if not cleaned:
        return ""
    base = min(level for level, *_rest in cleaned)
    lines = []
    for level, name, qty, kind in cleaned:
        words = [name]
        if qty and qty.strip() not in ("", "1"):
            words.append(f"x{qty.strip()}")
        if kind.strip().lower() in ("cots", "bought", "purchased"):
            words.append("cots")
        lines.append(" " * INDENT * (level - base) + " ".join(words))
    return "\n".join(lines)


def parse_table(text: str) -> str:
    """A CSV or tab-separated table (level, name, qty, type) as the indented text."""
    sample = text[:2048]
    dialect = csv.excel_tab if "\t" in sample else csv.excel
    return table_to_text(list(csv.reader(io.StringIO(text), dialect)))


def read_structure_file(path: str) -> str:
    """The text of a structure file: CSV and TSV tables become the indented text."""
    with open(path, encoding="utf-8-sig") as handle:
        text = handle.read()
    if path.lower().endswith((".csv", ".tsv")) or looks_like_table(text):
        return parse_table(text)
    return text


def parse_structure(text: str) -> Plan:
    """The typed (or pasted) structure as a Plan: one rooted DAG, with the parse issues."""
    plan = Plan()
    if looks_like_table(text or ""):
        text = parse_table(text)
    stack = []  # [(level, node)] from the root down to the current parent
    roots = []
    for n, raw in enumerate((text or "").splitlines(), 1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        level, body = _level(raw)
        name, qty, kind, pn = _split_line(body)
        if not name and not pn:
            plan.issues.append(Issue("error", f"line {n}: no name", n))
            continue
        if qty < 1:
            plan.issues.append(Issue("error", f"line {n}: the quantity must be at least 1", n))
            qty = 1
        key = pn or name.lower()
        node = plan.nodes.get(key)
        if node is None:
            node = Node(name or pn, kind or "part", pn)
            plan.nodes[key] = node
        elif kind == "cots" and node.kind != "cots" and not node.children:
            node.kind = "cots"
        node.lines.append(n)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if level == 0 or not stack:
            if level > 0:
                plan.issues.append(Issue("error", f"line {n}: indented, but nothing above it to go under", n))
            roots.append(node)
            stack = [(0, node)]
            continue
        parent_level, parent = stack[-1]
        if level > parent_level + 1:
            plan.issues.append(Issue("warning", f"line {n}: indented more than one level below {parent.name}; "
                                     "read as one level", n))
        if node is parent or any(node is p for _l, p in stack):
            plan.issues.append(Issue("error", f"line {n}: {node.name} cannot hold itself", n))
            continue
        parent.children.append((node, qty))
        node.uses += 1
        if parent.kind == "cots":
            plan.issues.append(Issue("error", f"line {parent.lines[0]}: {parent.name} is a bought part; "
                                     "it cannot hold items", parent.lines[0]))
        parent.kind = "assembly" if parent.kind != "cots" else parent.kind
        stack.append((level, node))
    if not roots:
        if not plan.errors:
            plan.issues.append(Issue("error", "Type the structure: the top assembly first, its items indented under it"))
        return plan
    if len(roots) > 1:
        names = ", ".join(r.name for r in roots[:4])
        plan.issues.append(Issue("error", f"One top assembly only: {names} are all at the left margin. Indent the "
                                 "others under it", roots[1].lines[0]))
    plan.root = roots[0]
    if plan.root is not None and not plan.root.children and not plan.errors:
        plan.issues.append(Issue("error", f"{plan.root.name} needs at least one item indented under it",
                                 plan.root.lines[0]))
    if plan.root is not None and plan.root.existing:
        plan.issues.append(Issue("error", f"line {plan.root.lines[0]}: the top assembly must be new; "
                                 f"{plan.root.pn} exists already", plan.root.lines[0]))
    if plan.root is not None and plan.root.kind == "cots":
        plan.issues.append(Issue("error", "the top assembly cannot be a bought part", plan.root.lines[0]))
    for node in plan.nodes.values():
        if node.uses > 1:
            plan.issues.append(Issue("info", f"{node.name} is used in {node.uses} places: one item, as typed. "
                                     "Rename a line to make two items", node.lines[1] if len(node.lines) > 1 else 0))
    return plan


# ----- checks against the vault ------------------------------------------------------------------


def check(plan: Plan, vault=None, program: str = "", retired: dict | None = None, library=None) -> Plan:
    """Check the plan against the vault: reused P/Ns exist and are not obsolete; the program and the
    document types are not retired. ``retired`` is {"program": {codes}, "document_type": {codes}} (from
    vault_setup.load_settings(...).retired). Adds Issues; returns the plan."""
    plan.issues = [i for i in plan.issues if not getattr(i, "_check", False)]
    found = []
    items = getattr(vault, "items", {}) if vault is not None else {}
    lib_items = getattr(library, "items", {}) if library is not None else {}
    for node in plan.nodes.values():
        if not node.existing:
            continue
        item = (items.get(node.pn) if vault is not None else None) or lib_items.get(node.pn)
        if (vault is not None or library is not None) and item is None:
            where = "this vault or the standard library" if library is not None else "this vault"
            found.append(Issue("error", f"line {node.lines[0]}: {node.pn} is not an item of {where}", node.lines[0]))
        elif item is not None:
            if getattr(item, "obsolete", False):
                found.append(Issue("error", f"line {node.lines[0]}: {node.pn} is obsolete", node.lines[0]))
            if item.item_type == "drawing":
                found.append(Issue("error", f"line {node.lines[0]}: {node.pn} is a drawing, not an item to use",
                                   node.lines[0]))
            node.kind = item.item_type if item.item_type in KINDS else node.kind
            if node.children:
                found.append(Issue("warning", f"line {node.lines[0]}: {node.pn} exists; the items typed under it "
                                   "are not added to it (its structure is its own file's)", node.lines[0]))
    retired = retired or {}
    if program and program.upper() in {str(c).upper() for c in retired.get("program", ())}:
        found.append(Issue("error", f"program {program} is retired; pick another in the vault's settings"))
    for code in plan.document_types.values():
        if code in {str(c) for c in retired.get("document_type", ())}:
            found.append(Issue("error", f"document type {code} is retired; change it in Vault Settings"))
    for issue in found:
        issue._check = True  # noqa: SLF001 -- dropped on the next check
    plan.issues.extend(found)
    return plan


# ----- the number plan ---------------------------------------------------------------------------


def plan_numbers(plan: Plan, vault_root: str, program: str, drawings: bool = True, library_root: str = "") -> Plan:
    """Fill ``plan.numbers`` and ``plan.drawing_numbers`` as a preview: the vault's numbering is read
    and the reservations simulated on a copy, so nothing is written and consecutive items get
    consecutive numbers. Numbers are flat, one parent each (family numbers collide, plan spike).
    Drawings take the drawing document type (03) until PDM-07's drawing key exists (question 11)."""
    from heuretes_freecad import vault_setup  # noqa: PLC0415
    from heuretes_freecad.sources import JsonNumberSource  # noqa: PLC0415

    plan.numbers, plan.drawing_numbers, plan.document_types = {}, {}, {}
    program = numbering.normalize_program(program)
    source = JsonNumberSource(vault_setup.numbering_path(vault_root))
    data = copy.deepcopy(source._load())
    types = vault_setup._vault_document_types(source, data)
    codes = {kind: vault_setup.document_type_for_item("part" if kind == "cots" else kind, types, data)
             for kind in KINDS}
    codes[DRAWING_KIND] = vault_setup.document_type_for_item(DRAWING_KIND, types, data)
    lib_source = lib_data = lib_program = lib_code = None
    if library_root:
        lib_source = JsonNumberSource(vault_setup.numbering_path(library_root))
        lib_data = copy.deepcopy(lib_source._load())
        programs = list((lib_data.get("programs") or {}).keys())
        if programs:
            lib_program = programs[0]
            lib_types = vault_setup._vault_document_types(lib_source, lib_data)
            lib_code = vault_setup.document_type_for_item("part", lib_types, lib_data)
    for node in order_of_work(plan):
        if node.kind == "cots" and lib_program:
            code = lib_code
            plan.document_types[node.key] = code
            plan.numbers[node.key] = pdm.strip_revision(
                lib_source._issue(lib_data, lib_program, code, None, None, write=True)[0])
            continue
        code = codes[node.kind]
        plan.document_types[node.key] = code
        plan.numbers[node.key] = pdm.strip_revision(source._issue(data, program, code, None, None, write=True)[0])
    if drawings:
        plan.document_types[DRAWING_KIND] = codes[DRAWING_KIND]
        for node in order_of_work(plan):
            if node.wants_drawing:
                plan.drawing_numbers[node.key] = pdm.strip_revision(
                    source._issue(data, program, codes[DRAWING_KIND], None, None, write=True)[0])
    return plan


def order_of_work(plan: Plan) -> list:
    """The new items, children before the assemblies that hold them (depth first, each once); existing
    items are left out (they are used, not made)."""
    out, seen = [], set()

    def visit(node):
        if node.key in seen:
            return
        seen.add(node.key)
        for child, _qty in node.children:
            visit(child)
        if not node.existing:
            out.append(node)

    if plan.root is not None:
        visit(plan.root)
    return out


# ----- the roll-up and the preview ---------------------------------------------------------------


def roll_up(plan: Plan) -> dict:
    """Q_t of every item under the root (sum over paths of the product of quantities), by key."""
    totals = {}

    def walk(node, mult):
        for child, qty in node.children:
            totals[child.key] = totals.get(child.key, 0) + mult * qty
            walk(child, mult * qty)

    if plan.root is not None:
        walk(plan.root, 1)
    return totals


def summary(plan: Plan) -> dict:
    """What Create makes: counts of parts, assemblies, bought parts and drawings, and the pieces in all
    (Q_t over the leaves), as the preview's "N parts in all"."""
    totals = roll_up(plan)
    pieces = sum(q for key, q in totals.items() if not plan.nodes[key].children)
    new = plan.new_items
    out = {
        "parts": sum(1 for n in new if n.kind == "part"),
        "assemblies": sum(1 for n in new if n.kind == "assembly"),
        "cots": sum(1 for n in new if n.kind == "cots"),
        "existing": sum(1 for n in plan.nodes.values() if n.existing),
        "drawings": sum(1 for n in new if n.wants_drawing),
        "pieces": pieces,
        "files": len(new) + sum(1 for n in new if n.wants_drawing),
    }
    out["text"] = f"{pieces} part{'s' if pieces != 1 else ''} in all"
    return out


def preview_rows(plan: Plan) -> list:
    """The preview, depth first as typed: rows {"depth", "name", "kind", "qty", "pn", "drawing", "duplicate",
    "existing", "total"} (``duplicate``: the item is used in more than one place)."""
    rows = []
    totals = roll_up(plan)

    def walk(node, depth, qty, path):
        rows.append({
            "depth": depth, "name": node.name, "kind": node.kind, "qty": qty, "key": node.key,
            "pn": node.pn or plan.numbers.get(node.key, ""), "drawing": plan.drawing_numbers.get(node.key, ""),
            "duplicate": node.uses > 1, "uses": node.uses, "existing": node.existing,
            "total": totals.get(node.key, 1), "wants_drawing": node.wants_drawing,
        })
        if node.key in path:
            return
        for child, q in node.children:
            walk(child, depth + 1, q, path | {node.key})

    if plan.root is not None:
        walk(plan.root, 0, None, frozenset())
    return rows


def project_code(name: str, taken=()) -> str:
    """A project code from its name: letters and digits, dashes between words, at most 16, unique
    among ``taken`` (a numbered suffix)."""
    code = _CODE_CHARS.sub("-", str(name or "").upper()).strip("-")[:16].rstrip("-") or "PROJECT"
    taken = {str(t).upper() for t in taken}
    candidate, n = code, 2
    while candidate in taken:
        candidate = f"{code[:13].rstrip('-')}-{n}"
        n += 1
    return candidate


def safe_stem(name: str, taken=()) -> str:
    """A file stem for a staged file: the name with only letters, digits, dashes and underscores."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip()).strip("_") or "item"
    candidate, n = stem, 2
    while candidate in taken:
        candidate = f"{stem}_{n}"
        n += 1
    return candidate


def comment_for(node: Node, project_name: str) -> str:
    """The check-in comment of a new file."""
    what = {"part": "part", "assembly": "assembly", "cots": "bought part"}.get(node.kind, node.kind)
    return f"New Project {project_name}: {what} {node.name}"


def drawing_comment(node: Node, project_name: str) -> str:
    return f"New Project {project_name}: drawing of {node.name}"


def example_text() -> str:
    """The placeholder the panel shows (the cart of the spike)."""
    return os.linesep.join([
        "Shop cart", "  Frame x2", "    Side rail x2", "    Cross bar", "  Deck plate", "  Caster 4 in x4 cots",
    ])
