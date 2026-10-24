# SPDX-License-Identifier: LGPL-2.1-or-later
"""Live KiCad tools for the BruceWorx MCP server (AUT-03 step 6).

Runs under the Python that has kicad-python (kipy), the repository's .tools/kicad-venv,
one call per process: reads {"tool", "args"} as JSON on stdin and prints one JSON line,
{"ok": true, "result": ...} or {"ok": false, "error": "..."}. Talks to the running KiCad
through its IPC API (Preferences > Plugins > Enable KiCad API).

Positions are in mm, angles in degrees, as KiCad shows them (y grows downwards).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

NM = 1e6
PCBNEW_APP = os.environ.get("BW_KICAD_PCBNEW_APP", "/Applications/KiCad/KiCad.app/Contents/Applications/pcbnew.app")
NOT_RUNNING = ("KiCad is not running, or its API is off. Start KiCad (or use kicad_open_board) and check "
               "Preferences > Plugins > Enable KiCad API.")


class Failure(Exception):
    pass


def _kicad(timeout_ms=4000):
    try:
        from kipy import KiCad
    except ImportError as exc:
        raise Failure(f"kicad-python (kipy) is not importable here: {exc}") from None
    try:
        kicad = KiCad(timeout_ms=timeout_ms)
        kicad.ping()
    except Exception as exc:
        raise Failure(f"{NOT_RUNNING} ({type(exc).__name__}: {exc})") from None
    return kicad


def _board(kicad):
    try:
        return kicad.get_board()
    except Exception as exc:
        raise Failure(f"no board is open in KiCad's PCB editor ({exc}); use kicad_open_board") from None


def _mm(v):
    return round(v / NM, 6)


def _pt(vec):
    return [_mm(vec.x), _mm(vec.y)]


def _open_pcbs(kicad):
    from kipy.proto.common.types import DocumentType

    docs = []
    for doc in kicad.get_open_documents(DocumentType.DOCTYPE_PCB):
        name = getattr(doc, "board_filename", "") or ""
        project = getattr(getattr(doc, "project", None), "path", "") or ""
        docs.append({"board": name, "project": project})
    return docs


def _ref(fp):
    try:
        return fp.reference_field.text.value
    except Exception:
        return "?"


def _footprint(board, fp):
    from kipy.proto.board.board_types_pb2 import BoardLayer

    out = {"reference": _ref(fp), "position_mm": _pt(fp.position),
           "rotation_deg": round(fp.orientation.degrees, 6),
           "side": "bottom" if fp.layer == BoardLayer.BL_B_Cu else "top", "locked": bool(fp.locked)}
    try:
        out["value"] = fp.value_field.text.value
    except Exception:
        pass
    try:
        lib = fp.definition.id
        out["footprint"] = f"{lib.library}:{lib.name}" if getattr(lib, "library", "") else str(lib)
    except Exception:
        pass
    return out


def _find(board, reference):
    matches = [fp for fp in board.get_footprints() if _ref(fp) == reference]
    if not matches:
        raise Failure(f"no footprint {reference!r} on the board")
    return matches[0]


# ----- tools ---------------------------------------------------------------------------------


def kicad_live_status(_args):
    kicad = _kicad()
    out = {"running": True, "version": str(kicad.get_version())}
    try:
        out["open_boards"] = _open_pcbs(kicad)
    except Exception as exc:
        out["open_boards_error"] = str(exc)
    return out


def kicad_open_board(args):
    path = os.path.abspath(os.path.expanduser(args["path"]))
    if not os.path.isfile(path):
        raise Failure(f"no file at {path}")
    try:
        kicad = _kicad(timeout_ms=2000)
        for doc in _open_pcbs(kicad):
            if doc["board"] and os.path.basename(doc["board"]) == os.path.basename(path):
                return {"opened": False, "already_open": True, "board": doc}
    except Failure:
        pass
    subprocess.run(["open", "-a", PCBNEW_APP, path], check=False, capture_output=True)
    deadline = time.monotonic() + float(args.get("wait", 90))
    last = ""
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            kicad = _kicad(timeout_ms=2000)
            for doc in _open_pcbs(kicad):
                if os.path.basename(doc["board"]) == os.path.basename(path):
                    return {"opened": True, "board": doc}
        except Failure as exc:
            last = str(exc)
    raise Failure(f"KiCad did not report {os.path.basename(path)} as open. {last or NOT_RUNNING}")


def kicad_list_footprints(args):
    board = _board(_kicad())
    pattern = re.compile(args["filter"]) if args.get("filter") else None
    out = [_footprint(board, fp) for fp in board.get_footprints() if pattern is None or pattern.search(_ref(fp))]
    out.sort(key=lambda f: (re.sub(r"\d+", "", f["reference"]), int(re.sub(r"\D", "", f["reference"]) or 0)))
    return {"board": board.name, "count": len(out), "footprints": out}


def kicad_select_footprints(args):
    board = _board(_kicad())
    wanted = [str(r) for r in args.get("references") or []]
    by_ref = {_ref(fp): fp for fp in board.get_footprints()}
    missing = [r for r in wanted if r not in by_ref]
    if missing:
        raise Failure(f"not on the board: {', '.join(missing)}")
    if not args.get("add"):
        board.clear_selection()
    board.add_to_selection([by_ref[r] for r in wanted])
    return {"selected": wanted}


def kicad_move_footprint(args):
    from kipy.geometry import Angle, Vector2

    board = _board(_kicad())
    fp = _find(board, str(args["reference"]))
    before = _footprint(board, fp)
    x, y = float(args["x"]), float(args["y"])
    if args.get("relative"):
        x += before["position_mm"][0]
        y += before["position_mm"][1]
    if fp.locked and not args.get("allow_locked"):
        raise Failure(f"{before['reference']} is locked in KiCad; pass allow_locked to move it anyway")
    commit = board.begin_commit()
    try:
        fp.position = Vector2.from_xy_mm(x, y)
        if args.get("rotation") is not None:
            angle = float(args["rotation"]) + (before["rotation_deg"] if args.get("relative") else 0.0)
            fp.orientation = Angle.from_degrees(angle)
        board.update_items([fp])
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, f"Claude: move {before['reference']}")
    after = _footprint(board, _find(board, before["reference"]))
    return {"before": before, "after": after, "saved": False}


def kicad_board_outline(_args):
    from kipy.proto.board.board_types_pb2 import BoardLayer

    board = _board(_kicad())
    items, xs, ys = [], [], []

    def add(kind, **values):
        items.append(dict(kind=kind, **values))
        for value in values.values():
            if isinstance(value, list) and value and isinstance(value[0], (int, float)) and len(value) == 2:
                xs.append(value[0])
                ys.append(value[1])

    for shape in board.get_shapes():
        if shape.layer != BoardLayer.BL_Edge_Cuts:
            continue
        name = type(shape).__name__
        if name == "BoardSegment":
            add("segment", start=_pt(shape.start), end=_pt(shape.end))
        elif name == "BoardArc":
            add("arc", start=_pt(shape.start), mid=_pt(shape.mid), end=_pt(shape.end))
        elif name == "BoardCircle":
            c, r = _pt(shape.center), _mm(shape.radius())
            add("circle", center=c, radius=r)
            xs.extend([c[0] - r, c[0] + r])
            ys.extend([c[1] - r, c[1] + r])
        elif name == "BoardRectangle":
            add("rectangle", top_left=_pt(shape.top_left), bottom_right=_pt(shape.bottom_right))
        elif name == "BoardPolygon":
            pts = []
            for poly in shape.polygons:
                for node in poly.outline:
                    point = getattr(node, "point", None)
                    if point is not None:
                        pts.append(_pt(point))
            items.append({"kind": "polygon", "points": pts})
            xs.extend(p[0] for p in pts)
            ys.extend(p[1] for p in pts)
        else:
            items.append({"kind": name})
    out = {"board": board.name, "items": items}
    if xs:
        out["bbox_mm"] = {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)],
                          "size": [round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6)]}
    return out


def kicad_refresh(args):
    kicad = _kicad()
    board = _board(kicad)
    if args.get("reload"):
        board.revert()
        return {"reloaded": True, "board": board.name}
    status = None
    try:
        status = kicad.run_action("common.Control.zoomFitScreen")
    except Exception as exc:
        status = f"zoom fit not available: {exc}"
    return {"reloaded": False, "board": board.name, "action": str(status).strip()}


def kicad_save_board(_args):
    board = _board(_kicad())
    board.save()
    return {"saved": True, "board": board.name}


TOOLS = {f.__name__: f for f in (kicad_live_status, kicad_open_board, kicad_list_footprints,
                                 kicad_select_footprints, kicad_move_footprint, kicad_board_outline,
                                 kicad_refresh, kicad_save_board)}


def main():
    try:
        request = json.loads(sys.stdin.read() or "{}")
        func = TOOLS.get(request.get("tool"))
        if func is None:
            raise Failure(f"unknown live KiCad tool {request.get('tool')!r}")
        reply = {"ok": True, "result": func(request.get("args") or {})}
    except Failure as exc:
        reply = {"ok": False, "error": str(exc)}
    except Exception as exc:
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(reply, default=str) + "\n")


if __name__ == "__main__":
    main()
