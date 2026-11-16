# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault dashboard as one snapshot (BW-PDM-10, plan section 7.11, approved 24 Oct 2026).

The Start page shows it whenever a vault is open. Everything on it is a function of the journal
(the replayed :class:`bruceworx.pdm_folder.FolderVault`), your work folder's state and the time:

    D = f(J, W, t, a) = (counts, projects, queues_a, changes, activity_[t - days, t], health)

so two Macs with the same journal show the same page, apart from My work and the sync line.
:func:`snapshot` returns a JSON-ready dict (a few kB); the page draws it and stores nothing.
No FreeCAD import; nothing here writes (the workspace is opened read-only).

Decisions (plan questions, 24 Oct): "me" is every actor with the same user part (bruce@mac and
bruce@mbp are one person; locks stay per Mac). Everyone's locks and activity show. The feed covers
14 days by default and hides check-outs, undone check-outs and BOM records unless show_all. A project is a program, the first field of the part number, named from numbering.json.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time

from heuretes_freecad import pdm

TYPES = (("part", "Parts"), ("assembly", "Assemblies"), ("drawing", "Drawings"), ("cots", "Bought (COTS)"))
QUEUE_ROWS = 3  # rows per My work queue
MAX_PROJECTS = 12  # more top assemblies than this: the BOMs are not recorded yet, so hide them
FOLD_SECONDS = 600  # events of one actor and operation within 10 min fold into one line
FEED_FIRST = 8  # feed lines on the page at first; Show older adds FEED_PAGE
FEED_PAGE = 50
STALE_LOCK_DAYS = 14
SYNC_WARN_SECONDS = 600  # "still syncing" turns orange after 10 minutes
FUTURE_SLACK_SECONDS = 300
QUIET_OPS = ("checkout", "undo_checkout", "set_bom")  # shown only with show_all
FEED_OPS = ("checkin", "submit", "approve", "reject", "retire", "raise_ecr", "decide_ecr", "open_eco",
            "release_eco", "create_item", "create_project", "update_project", "renumber") + QUIET_OPS
_CONFLICT = re.compile(r"( \d+| \(.*conflicted copy.*\)|\.sync-conflict-.*)\.jsonl$", re.IGNORECASE)


# ----- small helpers ------------------------------------------------------------------------------


def user_of(actor: str) -> str:
    """The person: the part of ``user@mac`` before the @."""
    return (actor or "").split("@")[0]


def same_person(actor: str, me: str) -> bool:
    return bool(actor) and user_of(actor) == user_of(me)


def device_of(actor: str) -> str:
    return (actor or "").partition("@")[2]


def who(actor: str, me: str) -> str:
    """"You" for any of your Macs, else the user part ("alex")."""
    return "You" if same_person(actor, me) else (user_of(actor) or actor)


def parse_time(text):
    try:
        when = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


def age_text(seconds: float) -> str:
    """"3 min", "5 h", "2 d"."""
    seconds = max(0, seconds)
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


def num(n: int) -> str:
    """1 412, with a thin space for thousands as the mock writes them."""
    return f"{n:,}".replace(",", "\u2009")


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def is_conflict_copy(name: str) -> bool:
    """``bruce@mac 2.jsonl``, ``x (conflicted copy ...).jsonl``, ``x.sync-conflict-....jsonl``."""
    return bool(_CONFLICT.search(name))


def storage_name(root: str) -> str:
    """Where the vault folder lives: "iCloud Drive", "Dropbox" ... or the folder."""
    low = root.replace("\\", "/").lower()
    if "/mobile documents/com~apple~clouddocs" in low or "/icloud" in low:
        return "iCloud Drive"
    for name in ("Dropbox", "OneDrive", "Google Drive", "Box"):
        if f"/{name.lower()}" in low:
            return name
    home = os.path.expanduser("~")
    text = "~" + root[len(home):] if root.startswith(home + os.sep) else root
    parts = text.split(os.sep)
    return text if len(text) <= 40 or len(parts) <= 3 else os.sep.join(["\u2026"] + parts[-2:])


# ----- counts -------------------------------------------------------------------------------------


def tiles(vault) -> list:
    """One tile per item type: total and the count in each lifecycle state."""
    out = {t: {"type": t, "title": title, "total": 0, "states": dict.fromkeys(pdm.STATES, 0)} for t, title in TYPES}
    for item in vault.items.values():
        tile = out.get(item.item_type)
        if tile is not None:
            tile["total"] += 1
            tile["states"][item.state] += 1
    return list(out.values())


# ----- projects (top assemblies until PDM-09) -------------------------------------------------------


def top_assemblies(vault, bom=None) -> list:
    """Assemblies that no other item's current BOM uses (and that are not obsolete)."""
    bom = vault.bom if bom is None else bom
    used = {child for lines in bom.values() for child, _qty in lines}
    return sorted(pn for pn, item in vault.items.items()
                  if item.item_type == "assembly" and pn not in used and not item.obsolete)


def closures(vault, tops, bom=None) -> dict:
    """{top: set of P/N} reached through the current BOMs, top included: one memoised walk, so all
    tops together cost O(N + L) (the plan's roll-up). A cycle in a damaged journal is cut."""
    bom = vault.bom if bom is None else bom
    memo, active = {}, set()

    def below(pn):
        if pn in memo:
            return memo[pn]
        active.add(pn)
        found = {pn}
        for child, _qty in bom.get(pn, ()):
            if child not in active:
                found |= below(child)
        active.discard(pn)
        memo[pn] = found
        return found

    return {top: below(top) for top in tops}


def projects(vault) -> dict:
    """{"label", "total", "hidden", "rows"}: one row per program, the first field of the part number.

    The name comes from the vault's numbering.json. A program listed there with no parts yet is still
    a row. Every item of the program counts once, drawings included."""
    from heuretes_freecad import vault_trees as vt

    names = vt.program_names(getattr(vault, "root", "") or "")
    codes = set(names)
    for pn in vault.items:
        code = vt.program_code(pn)
        if code:
            codes.add(code)
    rows = []
    for code in sorted(codes):
        pns = [pn for pn in vault.items if vt.program_code(pn) == code]
        drawing_count = sum(1 for pn in pns if vault.items[pn].item_type == "drawing")
        states = dict.fromkeys(pdm.STATES, 0)
        for pn in pns:
            states[vault.items[pn].state] += 1
        name = names.get(code) or ""
        everything = set(pns)
        open_ecos = sorted(eco.number for eco in getattr(vault, "ecos", {}).values()
                           if eco.status == "Open" and set(eco.items) & everything)
        rows.append({
            "program": code, "part_number": code, "code": code,
            "name": name or code, "description": f"{code}  {name}".strip() if name else code,
            "display": code, "revision": "", "state": "", "units": "",
            "items": len(pns), "drawings": drawing_count, "states": states, "ecos": open_ecos, "at": "",
        })
    return {"label": "program", "total": len(rows), "hidden": False, "rows": rows}


# ----- My work --------------------------------------------------------------------------------------


def _row(st, status=None) -> dict:
    return {"pn": st.part_number, "display": st.display, "description": st.description,
            "status": status or st.label, "code": st.code, "path": st.path, "kind": st.item_type or "part"}


def _weekday_text(when, now) -> str:
    local, today = when.astimezone(now.tzinfo), now
    days = (today.date() - local.date()).days
    if days <= 0:
        return f"{local:%H:%M}"
    if days == 1:
        return "yesterday"
    if days < 7:
        return local.strftime("%a")
    return f"{local.day} {local.strftime('%b')}"


def my_work(vault, workspace, me: str, now, last_at=None, changes=None) -> dict:
    """The queues: checked out to me, waiting for review, newer in the vault, ECOs open; at most
    QUEUE_ROWS rows each, with the full count. ``workspace`` is read-only (pdm_workspace)."""
    last_at = last_at or {}
    work = workspace.my_work() if workspace is not None else None
    queues = []

    checked = [_row(st, "Modified locally" if st.code == "modified" else "Checked out to me")
               for st in (work.checked_out if work else [])]
    # Your other Macs: the lock is theirs (the file is writable there only).
    for pn, holder in sorted(vault.locks.items()):
        if holder != me and same_person(holder, me) and pn in vault.items:
            item = vault.items[pn]
            checked.append({"pn": pn, "display": pdm.display_part_number(pn, item.latest.code),
                            "description": item.description, "status": f"Checked out to you on {device_of(holder)}",
                            "code": "checked_out_device", "path": "", "kind": item.item_type})
    queues.append(("checked_out", "Checked out to me", checked))

    # Anything in review: the vault has no reviewers yet (PDM-05), so everyone sees every review.
    if work is not None:
        review = [_row(st) for st in work.in_review]
    else:
        review = [{"pn": pn, "display": pdm.display_part_number(pn, item.latest.code), "path": "",
                   "description": item.description, "code": "in_review", "kind": item.item_type}
                  for pn, item in sorted(vault.items.items()) if item.state == pdm.IN_REVIEW]
    for row in review:
        pn = row["pn"]
        since = parse_time(last_at.get(("submit", pn), ""))
        row["status"] = "In review" + (f" since {_weekday_text(since, now)}" if since else "")
        row["chip"] = "rev"
        row["at"] = last_at.get(("submit", pn), "")
    review.sort(key=lambda r: r["at"], reverse=True)
    queues.append(("in_review", "Waiting for review", review))

    queues.append(("newer", "Newer in the vault", [_row(st, "Newer version in the vault")
                                                    for st in (work.newer if work else [])]))
    ecos = []
    for change in changes if changes is not None else vault.changes():
        if change["kind"] != "ECO" or change["state"] != "Open":
            continue
        total, pending = len(change["items"]), len(change.get("pending") or [])
        ecos.append({"pn": change["number"], "display": change["number"], "description": change.get("title", ""),
                     "status": f"{total - pending} of {total} in review", "code": "change", "path": "",
                     "kind": "change"})
    queues.append(("ecos_open", "ECOs open", ecos))
    out = [{"key": key, "title": title, "total": len(rows), "rows": rows[:QUEUE_ROWS]}
           for key, title, rows in queues]
    return {"total": sum(q["total"] for q in out), "queues": out}


# ----- changes ---------------------------------------------------------------------------------------


def changes(vault, rows=None) -> dict:
    """Open ECRs (to decide), approved ECRs without an ECO yet, open ECOs (what they wait for)."""
    out = []
    for change in rows if rows is not None else vault.changes():
        kind, state = change["kind"], change["state"]
        if kind == "ECR" and state == "Open":
            waits = "to decide"
        elif kind == "ECR" and state == "Approved" and not change.get("ecos"):
            waits = "no ECO yet"
        elif kind == "ECO" and state == "Open":
            total, pending = len(change["items"]), len(change.get("pending") or [])
            waits = f"{total - pending} of {total} in review" if pending else "ready to release"
        else:
            continue
        out.append({"kind": kind, "number": change["number"], "title": change.get("title", ""),
                    "state": state, "waits": waits, "at": change.get("at", "")})
    # ECOs first, then ECRs; the newest number first within each.
    ecos = sorted((r for r in out if r["kind"] == "ECO"), key=lambda r: r["number"], reverse=True)
    ecrs = sorted((r for r in out if r["kind"] == "ECR"), key=lambda r: r["number"], reverse=True)
    return {"total": len(out), "rows": ecos + ecrs}


# ----- activity ----------------------------------------------------------------------------------------


def _target(event):
    args = event.args
    return args.get("part_number") or args.get("new") or args.get("number") or args.get("parent") or args.get("name") or ""


def _verb(event, count, vault, me):
    """The line's text after the actor: "released X rev A", "checked in 6 files"."""
    op, target = event.op, _target(event)
    item = vault.items.get(target) if vault is not None else None
    many = count > 1
    if op == "checkin":
        return f"checked in {plural(count, 'file')}" if many else f"checked in {target}"
    if op == "create_item":
        return f"created {plural(count, 'item')}" if many else f"created {target}"
    if op == "checkout":
        return f"checked out {plural(count, 'file')}" if many else f"checked out {target}"
    if op == "undo_checkout":
        return f"undid {plural(count, 'check-out')}" if many else f"undid the check-out of {target}"
    if op == "set_bom":
        return f"recorded {plural(count, 'BOM')}" if many else f"recorded the BOM of {target}"
    if op == "submit":
        return f"submitted {plural(count, 'item')} for review" if many else f"submitted {target} for review"
    if op == "approve":
        return f"released {plural(count, 'item')}" if many else f"released {target} rev {pdm.first_release()}"
    if op == "reject":
        return f"rejected {plural(count, 'item')}" if many else f"rejected {target}"
    if op == "retire":
        return f"retired {plural(count, 'item')}" if many else f"retired {target}"
    if op == "raise_ecr":
        return f"raised {target}"
    if op == "decide_ecr":
        return f"{'approved' if event.args.get('approved') else 'rejected'} {target}"
    if op == "open_eco":
        return f"opened {target}"
    if op == "release_eco":
        return f"released {target}"
    if op == "renumber":  # PDM-12
        args = event.args
        reason = f": {args['reason']}" if args.get("reason") else ""
        return f"renumbered {args.get('old', '')} -> {args.get('new', '')}{reason}"
    if op == "create_project":
        return f"created project {event.args.get('code', '')} ({event.args.get('name', '')})".replace(" ()", "")
    if op == "update_project":
        status = event.args.get("status")
        return f"{'closed' if status == 'Closed' else 'reopened' if status else 'changed'} project {event.args.get('code', '')}"
    return f"{op} {target}".strip()


def _detail(event, events, vault):
    """The grey text after the line: a description, a problem or the shared comment."""
    op, target = event.op, _target(event)
    if op == "checkin":
        comments = {e.args.get("comment", "") for e in events}
        comment = comments.pop() if len(comments) == 1 else ""
        return f"“{comment}”" if comment else ""
    if op == "raise_ecr":
        return event.args.get("problem", "")
    if op == "renumber":  # the file versions it wrote, P/N and the start of the SHA-256
        files = event.args.get("files") or {}
        return ("new file versions: " + ", ".join(f"{pn} ({rec.get('sha256', '')[:8]})"
                                                  for pn, rec in sorted(files.items()))) if files else ""
    if op in ("open_eco", "release_eco", "decide_ecr") and vault is not None:
        eco = vault.ecos.get(target)
        if eco is not None:
            return "; ".join(vault.ecrs[n].problem for n in eco.ecrs if n in vault.ecrs)
        ecr = vault.ecrs.get(target)
        return ecr.problem if ecr is not None else ""
    if len(events) == 1 and vault is not None and target in vault.items:
        return vault.items[target].description
    return ""


ICONS = {"checkin": "up", "approve": "released", "release_eco": "released", "submit": "review", "reject": "reject",
         "retire": "retire", "raise_ecr": "change", "decide_ecr": "change", "open_eco": "change",
         "create_item": "plus", "create_project": "plus", "checkout": "lock", "undo_checkout": "lock",
         "set_bom": "bom", "renumber": "dot"}


def fold(events, gap=FOLD_SECONDS) -> list:
    """Runs of one actor and operation, each within ``gap`` seconds of the last: [[event, ...]].
    ``events`` are in time order; ECR/ECO steps never fold (each names its own change)."""
    groups, last_when = [], None
    for event in events:
        when = parse_time(event.at)
        if groups and when is not None and last_when is not None:
            head = groups[-1][-1]
            if head.actor == event.actor and head.op == event.op and event.op not in (
                    "raise_ecr", "decide_ecr", "open_eco", "release_eco", "create_project", "renumber") \
                    and 0 <= (when - last_when).total_seconds() <= gap:
                groups[-1].append(event)
                last_when = when
                continue
        groups.append([event])
        last_when = when
    return groups


def day_label(day, today) -> str:
    delta = (today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def activity(events, me: str, now, days: int = 14, show_all: bool = False, mine_only: bool = False,
             limit: int = FEED_FIRST, vault=None, created: str = "") -> dict:
    """The feed: lines of folded events, newest first, grouped by day, plus a count per day for the
    bars above it. ``limit`` caps the lines (Show older adds FEED_PAGE). ``created`` (the ref
    file's) adds "Vault created" when it falls in the range."""
    tz = now.tzinfo or _dt.timezone.utc
    since = now - _dt.timedelta(days=days)
    since_key = since.astimezone(_dt.timezone.utc).isoformat()
    ops = FEED_OPS if show_all else tuple(op for op in FEED_OPS if op not in QUIET_OPS)
    shown = [e for e in events if e.at >= since_key and e.op in ops and (not mine_only or same_person(e.actor, me))]
    today = now.astimezone(tz).date()
    per_day = {today - _dt.timedelta(days=i): 0 for i in range(days)}
    for event in shown:
        when = parse_time(event.at)
        if when is not None:
            day = when.astimezone(tz).date()
            if day in per_day:
                per_day[day] += 1
    lines = []
    for group in fold(shown):
        head, last = group[0], group[-1]
        when = parse_time(last.at)
        lines.append({"at": last.at, "actor": head.actor, "who": who(head.actor, me), "op": head.op,
                      "icon": ICONS.get(head.op, "dot"), "text": _verb(head, len(group), vault, me),
                      "detail": _detail(head, group, vault), "count": len(group), "target": _target(head),
                      "time": when.astimezone(tz).strftime("%H:%M") if when else "",
                      "day": when.astimezone(tz).date().isoformat() if when else ""})
    created_when = parse_time(created) if created else None
    if created_when is not None and created_when >= since and not mine_only:
        lines.insert(0, {"at": created, "actor": "", "who": "", "op": "create_vault", "icon": "plus",
                         "text": "Vault created", "detail": "", "count": 1, "target": "",
                         "time": created_when.astimezone(tz).strftime("%H:%M"),
                         "day": created_when.astimezone(tz).date().isoformat()})
    lines.reverse()
    more = len(lines) > limit
    lines = lines[:limit]
    groups = []
    for line in lines:
        label = day_label(_dt.date.fromisoformat(line["day"]), today) if line["day"] else ""
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "rows": []})
        groups[-1]["rows"].append(line)
    return {"days": days, "show_all": show_all, "mine_only": mine_only, "events": len(shown),
            "bars": [per_day[d] for d in sorted(per_day)], "groups": groups, "older": more}


# ----- health ---------------------------------------------------------------------------------------------


def health(vault, me: str, now, read_ms=None, objects=None) -> dict:
    """iCloud (on this Mac, in the cloud only, still syncing), locks and their age, the journal."""
    objects = objects if objects is not None else vault.object_health()
    storage = storage_name(vault.root)
    syncing = set(objects["missing"])
    since = None
    if syncing:
        for pn, versions in vault.versions.items():
            for version in versions:
                if version.object_name in syncing:
                    when = parse_time(version.at)
                    if when is not None and (since is None or when < since):
                        since = when
    sync_age = (now - since).total_seconds() if since else 0
    cloud = len(objects["cloud"])
    lines = []
    listed = objects["listed"]
    where = "iCloud" if storage == "iCloud Drive" else "Files"
    if listed:
        text = (f"{where}: all {num(listed)} files listed, {num(objects['local'])} on this Mac"
                if cloud or syncing else f"{where}: all {num(listed)} files on this Mac")
    else:
        text = f"{storage} folder" + (", syncing on" if storage == "iCloud Drive" else "")
    lines.append({"key": "icloud", "level": "ok", "text": text,
                  "note": f"{cloud} in the cloud only" if cloud else ""})
    if syncing:
        lines.append({"key": "syncing", "level": "warn" if sync_age > SYNC_WARN_SECONDS else "ok",
                      "text": f"{plural(len(syncing), 'version')} still syncing from another Mac",
                      "note": f"since {age_text(sync_age)}" if since else ""})

    mine = [pn for pn, holder in vault.locks.items() if same_person(holder, me)]
    others = [pn for pn, holder in vault.locks.items() if not same_person(holder, me)]
    stale, oldest = [], None
    lock_times = getattr(vault, "lock_times", {})
    for pn, holder in vault.locks.items():
        when = parse_time(lock_times.get(pn, ""))
        if when is None:
            continue
        age = (now - when).total_seconds()
        oldest = age if oldest is None else max(oldest, age)
        if age > STALE_LOCK_DAYS * 86400:
            stale.append({"pn": pn, "holder": holder, "who": who(holder, me), "age": age_text(age)})
    if vault.locks:
        text = f"Locks: {len(mine)} held by you, " + (f"{len(others)} by others" if others else "none by others")
    else:
        text = "Locks: none held"
    if vault.items:  # a new, empty vault has nothing to lock: the line waits for the first item
        lines.append({"key": "locks", "level": "warn" if stale else "ok", "text": text,
                      "note": f"oldest {age_text(oldest)}" if oldest is not None else ""})

    folder = os.path.join(vault.root, "journal")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".jsonl"))
    except OSError:
        names = []
    copies = [n for n in names if is_conflict_copy(n)]
    devices = len(names) - len(copies)
    future_key = (now + _dt.timedelta(seconds=FUTURE_SLACK_SECONDS)).astimezone(_dt.timezone.utc).isoformat()
    future = [e for e in vault.events if e.at > future_key]
    problems = list(vault.problems)
    events = len(vault.events)
    if events:
        text = f"Journal: {num(events)} events from {plural(devices, 'Mac', 'Macs')}"
        found = []
        if problems:
            found.append(plural(len(problems), "problem"))
        if future:
            found.append(f"{plural(len(future), 'event')} dated in the future")
        text += ", " + (", ".join(found) if found else "no problems")
    else:
        text = "Journal: empty, ready"
    lines.append({"key": "journal", "level": "warn" if problems or future else "ok", "text": text,
                  "note": f"{read_ms / 1000:.2f} s to read" if read_ms is not None and events else ""})
    return {
        "storage": storage, "lines": lines, "cloud": cloud, "syncing": len(syncing),
        "sync_age": sync_age, "stale_locks": stale, "problems": problems, "conflict_copies": copies,
        "future_events": [{"at": e.at, "actor": e.actor, "op": e.op} for e in future[:20]],
        "missing": sorted(syncing)[:50], "level": "warn" if any(l["level"] == "warn" for l in lines) else "ok",
    }


# ----- the vault's settings, for the header and Get started -------------------------------------------


def settings(root: str) -> dict:
    """Units, programs, an example number and the drawing sheet, from the vault's own files."""
    out = {"units": "mm", "programs": [], "example": "", "sheet": ""}
    try:
        from heuretes_freecad import drawing_standards

        standards = drawing_standards.load(root)
        out["units"] = standards.units
        out["sheet"] = drawing_standards.sheet_size_text(standards.default_sheet)
        out["standard"] = standards.standard
    except Exception:  # noqa: BLE001 -- an older vault: defaults
        pass
    try:
        with open(os.path.join(root, "numbering.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        programs = sorted((data.get("programs") or {}).keys())
        out["programs"] = programs
        if programs:
            counters = data["programs"][programs[0]].get("counters") or {}
            dtype = sorted(counters)[0] if counters else "01"
            first = int(data.get("first_parent", 1001))
            out["example"] = f"{programs[0]}-{dtype}-{first:05d}-001"
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return out


# ----- the snapshot ---------------------------------------------------------------------------------------


def last_events(events) -> dict:
    """{(op, P/N or number): at} of the last event of each kind per target."""
    out = {}
    for event in events:
        out[(event.op, _target(event))] = event.at
    return out


def snapshot(vault, workspace=None, now=None, days: int = 14, show_all: bool = False, mine_only: bool = False,
             limit: int = FEED_FIRST, read_ms=None) -> dict:
    """Everything the dashboard draws, as a JSON-ready dict. ``vault`` is a FolderVault already read
    (its events kept); ``workspace`` a read-only pdm_workspace.Workspace of the pinned actor."""
    started = time.perf_counter()
    now = now or _dt.datetime.now().astimezone()
    me = vault.actor
    rows = vault.changes()
    last = last_events(vault.events)
    objects = vault.object_health()
    out = {
        "name": vault.ref.get("name") or os.path.basename(vault.root),
        "root": vault.root, "actor": me, "storage": storage_name(vault.root),
        "created": vault.ref.get("created", ""), "read_at": now.isoformat(),
        "items": len(vault.items), "empty": not vault.items,
        "settings": settings(vault.root),
        "tiles": tiles(vault),
        "projects": projects(vault),
        "my_work": my_work(vault, workspace, me, now, last, rows),
        "changes": changes(vault, rows),
        "activity": activity(vault.events, me, now, days, show_all, mine_only, limit, vault,
                             vault.ref.get("created", "")),
        "health": health(vault, me, now, read_ms, objects),
        "parts": {pn: {"rev": item.latest.code, "state": item.state} for pn, item in vault.items.items()},
    }
    try:  # PDM-15: the project tabs, cards and pages; a failure leaves the dashboard as it was
        from heuretes_freecad import project_pages

        project_pages.tag_activity(out["activity"], vault)
        out["project_pages"] = project_pages.build(vault, now, me, changes=rows)
    except Exception as exc:  # noqa: BLE001
        out["project_pages_error"] = f"{type(exc).__name__}: {exc}"
    out["build_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out


def read(path: str, actor: str = "", **options) -> dict:
    """Open the vault (a full replay) and its read-only workspace, then :func:`snapshot`."""
    from heuretes_freecad.pdm_folder import FolderVault
    from heuretes_freecad.pdm_workspace import Workspace

    started = time.perf_counter()
    vault = FolderVault(os.path.expanduser(path), actor=actor or None)
    read_ms = (time.perf_counter() - started) * 1000
    return snapshot(vault, Workspace(vault, read_only=True), read_ms=read_ms, **options)
