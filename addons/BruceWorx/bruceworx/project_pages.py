# SPDX-License-Identifier: LGPL-2.1-or-later
"""The project pages of the vault dashboard (BW-PDM-15, 1 Nov 2026).

A project is a program, the first field of the part number. For every project this builds, from the
journal and the structure only (never by opening a document):

* a *card* for the All projects view: top assembly (and where its picture is), counts, the WIP/released
  bar, files missing a drawing, checked out to you, the last check-in;
* a *page*: header, tab counts, Overview (count tiles, product structure, needs attention, recent files),
  Drawings, Simulations, Changes and History.

``build`` returns a JSON-ready dict that ``vault_dashboard.snapshot`` stores as ``project_pages``.
Pictures are not read here: a picture is named ``{"sha", "object"}`` (the latest version's SHA-256 and
file) and the page loads it lazily from the picture cache or the thumbnail saved in the file.
No FreeCAD import; nothing here writes.

Simulations come from PDM-14's ``vault_trees.simulations(structure, scope=None)``; until it exists the
Simulations tab is empty ("No simulations yet").
"""

from __future__ import annotations

import json
import os
import time

from bruceworx import pdm
from bruceworx import vault_trees as vt

PAGE_TABS = (("overview", "Overview"), ("files", "Files"), ("drawings", "Drawings"),
             ("simulations", "Simulations"), ("changes", "Changes"), ("history", "History"))
STRUCTURE_ROWS = 14  # assemblies listed on the Overview before "and n more"
RECENT_FILES = 5
HISTORY_LINES = 120
HISTORY_DAYS = 365
ATTENTION_NAMES = 3  # names listed under an attention line
STALE_LOCK_DAYS = 14
_LINKS = {}  # object name -> {child: qty} read from an assembly's file: a file never changes once stored


# ----- small helpers -------------------------------------------------------------------------------


def when_text(at, now) -> str:
    """"31 Oct 17:00" in the local time zone."""
    from bruceworx import vault_dashboard as vd

    when = vd.parse_time(at)
    if when is None:
        return ""
    local = when.astimezone(now.tzinfo) if now is not None and now.tzinfo else when.astimezone()
    return f"{local.day} {local.strftime('%b')} {local:%H:%M}"


def split_name(description: str, code: str) -> tuple:
    """(title, sub) of a program called "Vector Labs - Propulsion" -> ("Propulsion", "Vector Labs"),
    "Overwatch" -> ("Overwatch", ""), "" -> (code, "")."""
    text = (description or "").strip()
    if not text:
        return code, ""
    head, sep, tail = text.partition(" - ")
    return (tail.strip(), head.strip()) if sep and tail.strip() else (head.strip(), "")


def tab_label(code: str, description: str) -> str:
    """"VL · Propulsion": the project's tab along the top."""
    title, sub = split_name(description, code)
    return f"{code} · {title}"


def structure_of(vault) -> dict:
    """vault_trees.structure with the links of assemblies cached per stored file, so a snapshot after a
    check-in re-reads only the new file's links."""
    def links(pn):
        version = vault.latest_version(pn) if hasattr(vault, "latest_version") else None
        key = getattr(version, "object_name", "") if version else ""
        if key in _LINKS:
            return _LINKS[key]
        found = vt._links_of(vault, pn)
        if key and found:
            _LINKS[key] = found
        return found

    return vt.structure(vault, links=links)


def _numbering(root: str) -> dict:
    try:
        with open(os.path.join(root or "", "numbering.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def first_number(data: dict, code: str) -> str:
    """"OW-01-01001": where the part numbers of a program start."""
    entry = (data.get("programs") or {}).get(code) or {}
    counters = entry.get("counters") or {}
    dtype = sorted(counters)[0] if counters else "01"
    try:
        first = int(data.get("first_parent", 1001))
    except (TypeError, ValueError):
        first = 1001
    return f"{code}-{dtype}-{first:05d}"


def pattern(code: str) -> str:
    return f"{code}-xx-xxxxx"


def picture_ref(vault, pn):
    """{"sha", "object"} of the latest version of ``pn`` (nothing is read), or None."""
    versions = vault.versions.get(pn) or []
    if not versions:
        return None
    last = versions[-1]
    return {"pn": pn, "sha": last.sha256, "object": os.path.join(vault.root, last.object_name),
            "name": last.filename}


def _state_word(info) -> str:
    return {"WIP": "WIP", "In Review": "IN REVIEW", "Released": "RELEASED", "Obsolete": "OBSOLETE"}.get(
        info.get("state"), str(info.get("state", "")).upper())


# ----- the events of each project --------------------------------------------------------------------


def event_codes(event, vault, prog) -> set:
    """The programs an event concerns: those of the parts it names, of the parts of the ECR or ECO it
    names, or the program of a project record."""
    args = event.args
    codes = set()
    for key in ("part_number", "new", "parent", "old"):
        value = args.get(key)
        if isinstance(value, str) and value:
            code = prog.get(value) or vt.program_code(value)
            if code:
                codes.add(code)
    number = args.get("number")
    if number:
        change = vault.ecrs.get(number) or vault.ecos.get(number)
        for pn in (change.items if change is not None else ()):
            code = prog.get(pn)
            if code:
                codes.add(code)
    if event.op in ("create_project", "update_project"):
        project = vault.model.projects.get(args.get("code")) if hasattr(vault, "model") else None
        program = args.get("program") or (project.program if project is not None else "")
        if program:
            codes.add(program)
    return codes


# ----- simulations (PDM-14) ----------------------------------------------------------------------------


def simulation_rows(s, code, source=None, pns=None) -> list:
    """The project's simulations from PDM-14's ``vault_trees.simulations(structure, scope=None)``.
    ``source`` replaces that function (tests); no function yet: no rows. Each row is normalised to
    {pn, display, description, state, of, of_display, out_of_date, status, raw}."""
    fn = source if source is not None else getattr(vt, "simulations", None)
    if fn is None:
        return []
    try:
        # The API's scope takes a set of part numbers: the project's own items.
        scope = set(pns) if pns is not None else {pn for pn in s["items"] if vt.program_code(pn) == code}
        rows = fn(s, scope)
    except Exception:  # noqa: BLE001 -- an API still changing under us: show none rather than break the page
        return []
    out = []
    for raw in rows or []:
        raw = dict(raw)
        pn = raw.get("pn") or raw.get("part_number") or ""
        of = raw.get("of") or raw.get("simulation_of") or raw.get("target") or ""
        stale = raw.get("out_of_date")
        if stale is None:
            stale = raw.get("changed")  # PDM-14: "part changed" is the Geometry column's out of date
        if stale is None and raw.get("judgement"):
            stale = raw["judgement"] == "part changed"
        info = s["items"].get(pn) or {}
        out.append({"pn": pn, "display": raw.get("display") or info.get("display") or pn,
                    "description": raw.get("description") or info.get("description") or "",
                    "state": raw.get("state") or info.get("state") or "",
                    "of": of, "of_display": (s["items"].get(of) or {}).get("description") or of,
                    "out_of_date": bool(stale), "study": raw.get("study") or "",
                    "status": raw.get("status") or raw.get("result") or "",
                    "raw": raw})
    return out


# ----- one project -------------------------------------------------------------------------------------


def _tree_rows(s, vault, pns, sizes, me_rows=None) -> list:
    """The assemblies of a project as an indented tree: roots (no assembly of the project uses them)
    first by size, below each its sub-assemblies."""
    asm = {pn for pn in pns if s["items"][pn]["type"] == "assembly" and not s["items"][pn].get("obsolete")}
    used = {c for pn in asm for c, _q in s["bom"].get(pn, ()) if c in asm}
    out, seen = [], set()

    def size(pn):
        if pn not in sizes:
            sizes[pn] = len(vt.contents(s, pn))
        return sizes[pn]

    def walk(pn, depth):
        if pn in seen:
            return
        seen.add(pn)
        out.append((pn, depth))
        for child in sorted({c for c, _q in s["bom"].get(pn, ()) if c in asm}, key=lambda c: (-size(c), c)):
            walk(child, depth + 1)

    for pn in sorted(asm - used, key=lambda p: (-size(p), p)):
        walk(pn, 0)
    for pn in sorted(asm - seen):  # an assembly only in a cycle
        walk(pn, 0)
    return out


def _drawing_text(s, pn) -> tuple:
    """(text, level) of an item's drawing status."""
    info = s["items"][pn]
    drawings = s["drawings"].get(pn) or []
    if drawings:
        return ("Drawing" if len(drawings) == 1 else f"{len(drawings)} drawings"), "ok"
    if vt.bought(info):
        return "COTS", "secondary"
    return "No drawing", "warn"


def _retire_reason(vault, pn) -> str:
    item = vault.items.get(pn)
    if item is None:
        return ""
    for rev in reversed(item.revisions):
        record = rev.record("retire")
        if record and record.get("reason"):
            return str(record["reason"])
    return ""


def page(vault, s, code, pns, ctx) -> dict:
    """The page of one project. ``ctx``: now, me, sims (callable), changes, events (this project's), names."""
    from bruceworx import vault_dashboard as vd

    now, me = ctx["now"], ctx["me"]
    items = s["items"]
    pns = sorted(pns)
    live = [pn for pn in pns if not items[pn].get("obsolete")]
    by_type = {}
    for pn in pns:
        by_type.setdefault(items[pn]["type"], []).append(pn)
    parts = [pn for pn in by_type.get("part", []) + by_type.get("cots", []) if not items[pn].get("obsolete")]
    bought = [pn for pn in parts if vt.bought(items[pn])]
    made = [pn for pn in parts if pn not in bought]
    assemblies = [pn for pn in by_type.get("assembly", []) if not items[pn].get("obsolete")]
    drawings = [pn for pn in by_type.get("drawing", []) if not items[pn].get("obsolete")]
    sims = ctx["sims"](code)
    missing = [pn for pn in live if vt.missing_drawing(s, pn)]
    states = dict.fromkeys(pdm.STATES, 0)
    for pn in pns:
        states[items[pn]["state"]] += 1
    released_drawings = sum(1 for pn in drawings if items[pn]["state"] == pdm.RELEASED)
    sizes = {}
    tree = _tree_rows(s, vault, pns, sizes)
    tops = [pn for pn, depth in tree if depth == 0]

    if not drawings:
        drawing_note = "None yet"
    elif not released_drawings:
        drawing_note = "All WIP, none released" if all(items[d]["state"] == pdm.WIP for d in drawings) \
            else "None released"
    else:
        drawing_note = f"{released_drawings} released"
    asm_names = ", ".join(items[pn]["description"] or pn for pn in assemblies[:3]) + ("…" if len(assemblies) > 3 else "")
    top_missing = [pn for pn in tops if pn in missing]
    missing_note = ("Includes " + ("the top assembly" if len(top_missing) == 1 else f"both top assemblies"
                                    if len(top_missing) == 2 else f"{len(top_missing)} top assemblies")) \
        if top_missing else ("Every part and assembly has one" if not missing else "")
    counts = [
        {"key": "parts", "title": "Parts", "value": len(parts),
         "note": f"{len(made)} made · {len(bought)} bought (COTS)", "query": "type:part"},
        {"key": "assemblies", "title": "Assemblies", "value": len(assemblies), "note": asm_names,
         "query": "type:assembly"},
        {"key": "drawings", "title": "Drawings", "value": len(drawings), "note": drawing_note, "tab": "drawings"},
        {"key": "missing", "title": "Missing a drawing", "value": len(missing), "note": missing_note,
         "scope": "view:missing", "level": "warn" if missing else "ok"},
    ]

    structure_rows = []
    for pn, depth in tree:
        info = items[pn]
        n = len(vault.versions.get(pn) or [])
        text, level = _drawing_text(s, pn)
        structure_rows.append({
            "pn": pn, "depth": depth, "name": info["description"] or pn,
            "sub": f"{pn} · {sizes.get(pn, 0)} items" + (f" · v{n}" if n else ""),
            "state": info["state"], "state_word": _state_word(info), "drawing": text, "drawing_level": level,
            "picture": picture_ref(vault, pn)})

    attention = []
    if missing:
        names = ", ".join(items[pn]["description"] or pn for pn in missing[:ATTENTION_NAMES])
        attention.append({"key": "missing", "level": "warn", "title": f"{len(missing)} made item"
                          f"{'s' if len(missing) != 1 else ''} without a drawing",
                          "detail": names + ("…" if len(missing) > ATTENTION_NAMES else ""),
                          "scope": "view:missing"})
    if live and not any(items[pn]["state"] == pdm.RELEASED for pn in live):
        small = [pn for pn, _d in tree if s["bom"].get(pn)]
        hint = ""
        if small:
            pick = min(small, key=lambda p: (sizes.get(p, 0), p))
            hint = f" Submit {items[pick]['description'] or pick} first?"
        wip = states[pdm.WIP]
        attention.append({"key": "nothing_released", "level": "info", "title": "Nothing released",
                          "detail": (f"All {len(live)} files are WIP." if wip == len(live) else
                                     f"{len(live)} files, none released.") + hint, "query": "state:wip"})
    voided = [pn for pn in pns if items[pn].get("obsolete")]
    if voided:
        first = voided[0]
        reason = _retire_reason(vault, first) or items[first]["description"]
        attention.append({"key": "voided", "level": "info",
                          "title": f"{len(voided)} voided file{'s' if len(voided) != 1 else ''}",
                          "detail": f"{items[first]['display']} · {reason}".strip(" ·") +
                                    (f" and {len(voided) - 1} more" if len(voided) > 1 else ""),
                          "query": "state:obsolete"})
    stale = []
    lock_times = getattr(vault, "lock_times", {})
    for pn in live:
        holder = vault.locks.get(pn)
        when = vd.parse_time(lock_times.get(pn, "")) if holder else None
        if when is not None and (now - when).total_seconds() > STALE_LOCK_DAYS * 86400:
            stale.append(f"{items[pn]['description'] or pn} ({vd.who(holder, me).lower() if holder else ''})")
    if stale:
        attention.append({"key": "stale_locks", "level": "warn", "title": f"{len(stale)} checked out for over "
                          f"{STALE_LOCK_DAYS} days", "detail": ", ".join(stale[:ATTENTION_NAMES]),
                          "query": "lock:any"})
    waiting = [pn for pn in live if items[pn]["state"] == pdm.IN_REVIEW]
    if waiting:
        attention.append({"key": "in_review", "level": "info", "title": f"{len(waiting)} waiting for review",
                          "detail": ", ".join((items[pn]["description"] or pn) for pn in waiting[:ATTENTION_NAMES]),
                          "query": "state:review"})
    old_sims = [r for r in sims if r["out_of_date"]]
    if old_sims:
        attention.append({"key": "sims_old", "level": "warn", "title": f"{len(old_sims)} simulation"
                          f"{'s' if len(old_sims) != 1 else ''} out of date",
                          "detail": ", ".join(r["description"] or r["display"] for r in old_sims[:ATTENTION_NAMES])
                          + ("…" if len(old_sims) > ATTENTION_NAMES else ""), "tab": "simulations"})

    # Recent files: the last check-in of each file, newest first.
    recent, seen = [], set()
    for event in reversed(ctx["events"]):
        pn = event.args.get("part_number")
        if event.op != "checkin" or pn in seen or pn not in items:
            continue
        seen.add(pn)
        n = len(vault.versions.get(pn) or [])
        recent.append({"pn": pn, "name": items[pn]["description"] or pn, "kind": items[pn]["type"],
                       "sub": f"v{n} · {when_text(event.at, now)}", "at": event.at, "picture": picture_ref(vault, pn)})
        if len(recent) == RECENT_FILES:
            break

    drawing_rows = []
    for pn in drawings:
        info = items[pn]
        of = vt.drawing_owner(s, pn) or ""
        last = (vault.versions.get(pn) or [None])[-1]
        drawing_rows.append({"pn": pn, "name": info["description"] or pn, "display": info["display"],
                             "of": of, "of_name": (items.get(of) or {}).get("description") or of,
                             "state": info["state"], "state_word": _state_word(info),
                             "sub": f"v{len(vault.versions.get(pn) or [])} · {when_text(last.at, now) if last else ''}",
                             "picture": picture_ref(vault, pn)})

    mine = set(pns)
    change_rows = []
    for change in ctx["changes"]:
        if mine & set(change["items"]):
            touched = sorted(mine & set(change["items"]))
            change_rows.append({"kind": change["kind"], "number": change["number"], "state": change["state"],
                                "title": change.get("title", ""), "at": change.get("at", ""),
                                "items": len(touched),
                                "sub": f"{len(touched)} of its files" if len(touched) != len(change["items"])
                                else f"{len(touched)} file{'s' if len(touched) != 1 else ''}"})

    history = vd.activity(ctx["events"], me, now, HISTORY_DAYS, False, False, HISTORY_LINES, vault, "")
    history.pop("bars", None)
    n_open = sum(1 for c in change_rows if c["state"] == "Open")

    description = ctx["descriptions"].get(code, "")
    title, sub = split_name(ctx["names"].get(code, ""), code)
    return {
        "code": code, "title": title, "sub": sub,
        "full_title": " · ".join(x for x in (sub, title) if x) if sub else title,
        "description": description, "pattern": pattern(code), "files": len(pns),
        "counts": counts, "structure": structure_rows, "structure_more": max(0, len(structure_rows) - STRUCTURE_ROWS),
        "attention": attention, "recent": recent, "drawings": drawing_rows, "simulations": sims,
        "changes": change_rows, "history": history, "open_changes": n_open,
        "tab_counts": {"files": len(pns), "drawings": len(drawings), "simulations": len(sims),
                       "changes": len(change_rows), "history": None, "overview": None},
        "tops": tops, "pns": pns,
    }


# ----- all projects ------------------------------------------------------------------------------------


def codes_of(vault, names) -> list:
    codes = set(names)
    for pn in vault.items:
        code = vt.program_code(pn)
        if code:
            codes.add(code)
    return sorted(codes)


def build(vault, now, me, structure=None, simulations=None, changes=None) -> dict:
    """Everything the project tabs, cards and pages draw."""
    from bruceworx import vault_dashboard as vd

    started = time.perf_counter()
    s = structure if structure is not None else structure_of(vault)
    names = vt.program_names(vault.root)
    data = _numbering(vault.root)
    prog = {pn: vt.program_code(pn) for pn in vault.items}
    codes = codes_of(vault, names)
    by_code = {code: [] for code in codes}
    for pn, code in prog.items():
        if code:
            by_code[code].append(pn)
    projects = getattr(vault, "projects", None)
    descriptions = {}
    for project in (projects.values() if isinstance(projects, dict) else []):
        if getattr(project, "program", "") and project.description:
            descriptions.setdefault(project.program, project.description)
    events = {code: [] for code in codes}
    for event in vault.events:
        for code in event_codes(event, vault, prog):
            if code in events:
                events[code].append(event)
    rows = changes if changes is not None else vault.changes()
    sims_cache = {}

    def sims(code):
        if code not in sims_cache:
            sims_cache[code] = simulation_rows(s, code, simulations, by_code[code])
        return sims_cache[code]

    ctx = {"now": now, "me": me, "sims": sims, "changes": rows, "names": names, "descriptions": descriptions}
    pages, cards, tabs = {}, [], []
    for code in codes:
        ctx["events"] = events[code]
        pns = by_code[code]
        pg = page(vault, s, code, pns, ctx)
        pages[code] = pg
        tabs.append({"code": code, "label": tab_label(code, names.get(code, "")), "files": len(pns)})
        cards.append(card(vault, s, code, pns, pg, events[code], ctx, first_number(data, code)))
    return {"tabs": tabs, "cards": cards, "pages": pages, "build_ms": round((time.perf_counter() - started) * 1000, 1)}


def card(vault, s, code, pns, pg, events, ctx, first) -> dict:
    """The All projects card of one project."""
    from bruceworx import vault_dashboard as vd

    items, now, me = s["items"], ctx["now"], ctx["me"]
    states = dict.fromkeys(pdm.STATES, 0)
    for pn in pns:
        states[items[pn]["state"]] += 1
    mine = sum(1 for pn in pns if vault.locks.get(pn) and vd.same_person(vault.locks[pn], me))
    last = next((e for e in reversed(events) if e.op == "checkin" and e.args.get("part_number") in items), None)
    last_text = ""
    if last is not None:
        pn = last.args["part_number"]
        last_text = f"{when_text(last.at, now)} · {items[pn]['description'] or pn}"
    top = pg["tops"][0] if pg["tops"] else ""
    counts = {c["key"]: c["value"] for c in pg["counts"]}
    title, sub = split_name(ctx["names"].get(code, ""), code)
    return {
        "code": code, "title": title, "sub": sub or ctx["descriptions"].get(code, ""),
        "empty": not pns, "first_number": first,
        "parts": counts["parts"], "assemblies": counts["assemblies"], "drawings": counts["drawings"],
        "files": len(pns), "states": states, "missing": counts["missing"], "mine": mine,
        "last": last_text, "top": top, "picture": picture_ref(vault, top) if top else None,
    }


# ----- tagging the all-projects feed ---------------------------------------------------------------------


def tag_activity(feed, vault) -> None:
    """Add ``project`` (the program) to each line of a dashboard activity feed, in place."""
    for group in feed.get("groups", []):
        for line in group["rows"]:
            target = line.get("target") or ""
            code = vt.program_code(target)
            if not code and target:
                change = vault.ecrs.get(target) or vault.ecos.get(target)
                if change is not None and change.items:
                    code = vt.program_code(change.items[0])
            line["project"] = code
