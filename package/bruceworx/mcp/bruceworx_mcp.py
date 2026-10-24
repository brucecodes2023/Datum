#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx MCP server: FreeCAD and KiCad tools for Claude Code (AUT-03).

Claude Code starts this script (see .mcp.json at the repository root) and speaks MCP to
it: JSON-RPC 2.0, one message per line on stdin/stdout. Python 3.9+ standard library only.

* FreeCAD tools are forwarded to the bridge inside FreeCAD (src/Mod/BruceWorx/
  bruceworx_bridge.py), found through <FreeCAD user data>/BruceWorx/bridge.json. When no
  FreeCAD window runs a bridge, a headless FreeCADCmd is started with the same bridge, on a
  private copy of the user's user.cfg, and stopped when this server exits.
* KiCad file tools run kicad-cli here (board info, STEP export, render, DRC, BOM).
* Live KiCad tools talk to a running KiCad through its IPC API with kicad-python (kipy),
  run by kicad_live.py under the repository's .tools/kicad-venv Python.

Environment (defaults in brackets):
  BW_FREECAD_CMD       FreeCADCmd for the headless job [<repo>/build/release/bin/FreeCADCmd]
  BW_FREECAD_USER_CFG  the user's config, copied once [~/Library/Preferences/FreeCAD/v1-1/user.cfg]
  BW_MCP_CACHE         private files: user.cfg copy, headless state and log [~/Library/Caches/bruceworx-mcp]
  BW_BRIDGE_DIR        where FreeCAD's bridge.json is [~/Library/Application Support/FreeCAD/v1-1/BruceWorx]
  BW_MCP_TARGET        auto (the window if there is one, else headless), gui or headless [auto]
  BW_KICAD_CLI         kicad-cli [/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli]
  BW_KICAD_3DMODELS    KiCad's 3D model library [/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels]
  BW_KICAD_PYTHON      Python with kipy [<repo>/.tools/kicad-venv/bin/python]

Run with --selftest to list the tools and call a harmless one.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

SERVER_NAME = "bruceworx"
SERVER_VERSION = "1.0.0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
HOME = os.path.expanduser("~")
CALL_TIMEOUT = 330.0  # seconds: the consent prompt waits up to two minutes


def _env(name, default):
    value = os.environ.get(name, "").strip()
    return os.path.expanduser(value) if value else default


def _freecad_cmd():
    local = os.path.join(REPO, "build", "release", "bin", "FreeCADCmd")
    fallback = "/Users/brucetokar/Developer/FreeCAD_BruceWorx/build/release/bin/FreeCADCmd"
    return _env("BW_FREECAD_CMD", local if os.path.exists(local) else fallback)


def paths():
    cache = _env("BW_MCP_CACHE", os.path.join(HOME, "Library", "Caches", "bruceworx-mcp"))
    return {
        "freecad_cmd": _freecad_cmd(),
        "user_cfg": _env("BW_FREECAD_USER_CFG", os.path.join(HOME, "Library", "Preferences", "FreeCAD", "v1-1", "user.cfg")),
        "cache": cache,
        "gui_dir": _env("BW_BRIDGE_DIR", os.path.join(HOME, "Library", "Application Support", "FreeCAD", "v1-1", "BruceWorx")),
        "target": os.environ.get("BW_MCP_TARGET", "auto").strip().lower() or "auto",
        "kicad_cli": _env("BW_KICAD_CLI", "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
        "kicad_models": _env("BW_KICAD_3DMODELS", "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels"),
        "kicad_python": _env("BW_KICAD_PYTHON", os.path.join(REPO, ".tools", "kicad-venv", "bin", "python")),
    }


class ToolFailure(Exception):
    """A tool call failed; the text goes back to Claude with isError."""


# ----- tool list -----------------------------------------------------------------------------

def _p(kind, desc, **extra):
    out = {"type": kind, "description": desc}
    out.update(extra)
    return out


_DOC = _p("string", "Document name or label; default: the active document.")
_OBJ = _p("string", "Object name or label (object_tree lists them).")
_VAULT = _p("string", "Vault folder or .bwvault file; default: Preferences/BruceWorx/VaultPath.")
_NUMS = {"type": "array", "items": {"type": "number"}}
_STRS = {"type": "array", "items": {"type": "string"}}
_BOARD = _p("string", "Path to a .kicad_pcb file.")


def _tool(name, target, desc, props=None, required=(), read_only=False):
    schema = {"type": "object", "properties": props or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    tool = {"name": name, "description": desc, "inputSchema": schema, "_target": target}
    tool["annotations"] = {"readOnlyHint": bool(read_only)}
    return tool


TOOLS = [
    # --- status
    _tool("bridge_status", "server", "Which FreeCAD the tools reach (the open window or a headless job), its "
          "version and whether this session is allowed; and whether kicad-cli and live KiCad are available. "
          "Never asks the user.", read_only=True),
    # --- FreeCAD: read
    _tool("list_documents", "freecad", "Open FreeCAD documents: name, label, file, object count, active.",
          read_only=True),
    _tool("object_tree", "freecad", "The model tree of a document: objects with name, label, type and children "
          "(bodies list their features; tip and links shown).", {"doc": _DOC}, read_only=True),
    _tool("get_properties", "freecad", "All properties of one object with type, group and value "
          "(lengths as text with units, vectors as [x,y,z], links as object names).",
          {"object": _OBJ, "doc": _DOC}, ["object"], read_only=True),
    _tool("selection", "freecad", "What is selected in the FreeCAD window (objects and sub-elements such as "
          "Face3).", read_only=True),
    _tool("measure", "freecad", "Volume, area, bounding box, centre of mass and (when a material is set) mass "
          "of an object's shape. Units: mm, mm^2, mm^3.", {"object": _OBJ, "doc": _DOC}, ["object"],
          read_only=True),
    _tool("screenshot", "freecad", "A PNG of the active 3D view. Needs the FreeCAD window (not headless).",
          {"width": _p("integer", "Pixels, default 800."), "height": _p("integer", "Pixels, default 600."),
           "view": _p("string", "Optional camera first: fit, iso, front, back, top, bottom, left, right.")},
          read_only=True),
    # --- FreeCAD: documents
    _tool("new_document", "freecad", "Create a document and make it active.",
          {"name": _p("string", "Internal name (default Unnamed)."), "label": _p("string", "Shown name.")}),
    _tool("open_document", "freecad", "Open an .FCStd file (or activate it when it is open).",
          {"path": _p("string", "Absolute path.")}, ["path"]),
    _tool("recompute", "freecad", "Recompute a document; lists objects that failed.", {"doc": _DOC}),
    _tool("undo", "freecad", "Undo the last change(s); every modelling call is one undo step.",
          {"steps": _p("integer", "How many steps, default 1."), "doc": _DOC}),
    _tool("save", "freecad", "Save a document, or save it as path.",
          {"path": _p("string", "Optional .FCStd path (Save As)."), "doc": _DOC}),
    _tool("export_step", "freecad", "Export objects (default: the solid parts) to a STEP file.",
          {"path": _p("string", "Output .step/.stp path."), "objects": dict(_STRS, description="Object names."),
           "doc": _DOC}, ["path"]),
    # --- FreeCAD: modelling
    _tool("new_body", "freecad", "Add a PartDesign body and make it active.",
          {"label": _p("string", "Optional label."), "doc": _DOC}),
    _tool("create_sketch", "freecad",
          "Add a sketch to a body from a list of geometry, in sketch coordinates (mm, degrees). Items: "
          "{\"line\": [x1,y1,x2,y2]}, {\"circle\": [cx,cy,r]}, {\"arc\": [cx,cy,r,start_deg,end_deg]}, "
          "{\"rectangle\": [x,y,width,height]} (four lines). With auto_constraints (default) touching ends "
          "become coincident and axis-parallel lines horizontal/vertical. Returns closed/open wire counts.",
          {"geometry": {"type": "array", "items": {"type": "object"}, "description": "Geometry items, see above."},
           "plane": _p("string", "XY (default), XZ, YZ, or a face: Object:FaceN."),
           "body": _p("string", "Body name; default: the active or only body."),
           "auto_constraints": _p("boolean", "Default true."),
           "dimensions": {"type": "array", "items": {"type": "object"},
                          "description": "Optional [{kind, geometry, value}], kind: distance (line length), "
                                         "distance_x, distance_y, radius, diameter, angle (deg); geometry is "
                                         "the index in this sketch's geometry list."},
           "label": _p("string", "Optional label."), "doc": _DOC}, ["geometry"]),
    _tool("pad", "freecad", "Extrude a sketch (PartDesign Pad).",
          {"sketch": _OBJ, "length": _p("number", "mm, default 10."), "through_all": _p("boolean", "Ignore length."),
           "reversed": _p("boolean", "Other direction."), "midplane": _p("boolean", "Symmetric to the sketch."),
           "label": _p("string", "Optional label."), "doc": _DOC}, ["sketch"]),
    _tool("pocket", "freecad", "Cut a sketch into the body (PartDesign Pocket).",
          {"sketch": _OBJ, "depth": _p("number", "mm, default 5."), "through_all": _p("boolean", "Cut through."),
           "reversed": _p("boolean", "Other direction."), "midplane": _p("boolean", "Symmetric."),
           "label": _p("string", "Optional label."), "doc": _DOC}, ["sketch"]),
    _tool("revolve", "freecad", "Revolve a sketch (PartDesign Revolution, or Groove to cut).",
          {"sketch": _OBJ, "axis": _p("string", "V_Axis (default) or H_Axis of the sketch, X, Y, Z, a sketch "
                                                "EdgeN, or Object:EdgeN."),
           "angle": _p("number", "Degrees, default 360."), "groove": _p("boolean", "Cut instead of add."),
           "label": _p("string", "Optional label."), "doc": _DOC}, ["sketch"]),
    _tool("hole", "freecad", "Simple holes at the circle centres of a sketch (PartDesign Hole, no thread).",
          {"sketch": _OBJ, "diameter": _p("number", "mm."), "depth": _p("number", "mm, default 10."),
           "through_all": _p("boolean", "Through all."), "label": _p("string", "Optional label."), "doc": _DOC},
          ["sketch", "diameter"]),
    _tool("fillet", "freecad", "Round edges. On a PartDesign feature or body: PartDesign Fillet; on other "
          "solids: Part Fillet.", {"object": _OBJ, "edges": dict(_STRS, description="Edge names, e.g. [\"Edge3\"]."),
                                    "radius": _p("number", "mm."), "doc": _DOC}, ["object", "edges", "radius"]),
    _tool("chamfer", "freecad", "Chamfer edges (PartDesign Chamfer or Part Chamfer).",
          {"object": _OBJ, "edges": dict(_STRS, description="Edge names."), "size": _p("number", "mm."),
           "doc": _DOC}, ["object", "edges", "size"]),
    _tool("rectangular_pattern", "freecad", "Rectangular pattern. A PartDesign feature: PartDesign Linear "
          "Pattern (directions along X, Y or Z). A body, part or component: BruceWorx linked pattern.",
          {"object": _OBJ, "direction": dict(_NUMS, description="[x,y,z], default [1,0,0]."),
           "count": _p("integer", "Instances in direction 1, seed included."),
           "spacing": _p("number", "mm between instances."),
           "direction2": dict(_NUMS, description="Default [0,1,0]."),
           "count2": _p("integer", "Default 1."), "spacing2": _p("number", "mm."), "doc": _DOC},
          ["object", "count", "spacing"]),
    _tool("circular_pattern", "freecad", "Circular pattern about an axis (PartDesign Polar Pattern for a "
          "feature, BruceWorx linked pattern otherwise).",
          {"object": _OBJ, "axis": {"description": "X, Y, Z (default Z) or [x,y,z].",
                                    "anyOf": [{"type": "string"}, _NUMS]},
           "center": dict(_NUMS, description="Point on the axis, default origin."),
           "count": _p("integer", "Instances, seed included."),
           "angle": _p("number", "Degrees, default 360 (full circle)."), "doc": _DOC}, ["object", "count"]),
    _tool("set_property", "freecad", "Set one property and recompute. Lengths take numbers (mm) or text "
          "with units (\"5 mm\"); vectors [x,y,z]; Placement {base:[x,y,z], axis:[x,y,z], angle_deg}; links an "
          "object name.", {"object": _OBJ, "property": _p("string", "Property name."),
                           "value": {"description": "New value."}, "doc": _DOC}, ["object", "property", "value"]),
    _tool("set_parameter", "freecad", "Set a spreadsheet cell by its alias (a design parameter) and recompute.",
          {"alias": _p("string", "Cell alias."), "value": {"description": "Number, or text such as \"12 mm\"."},
           "spreadsheet": _p("string", "Sheet name; default: any sheet with that alias."), "doc": _DOC},
          ["alias", "value"]),
    _tool("add_component", "freecad", "Insert a part file into the document's assembly as a linked component "
          "(an assembly is created when there is none; the first component is grounded).",
          {"path": _p("string", ".FCStd file of the part."), "assembly": _p("string", "Assembly name."),
           "placement": dict(_NUMS, description="Optional position [x,y,z] mm."), "doc": _DOC}, ["path"]),
    _tool("add_joint", "freecad", "Add an assembly joint between two elements. References are paths inside "
          "the assembly ending in an element, e.g. \"Base.Face6\" or \"Bracket.Edge3\"; vertex1/2 optionally "
          "pick the point (e.g. \"Vertex6\"), else the element's centre.",
          {"type": _p("string", "Fixed, Revolute, Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, "
                                "Angle, RackPinion, Screw, Gears or Belt."),
           "ref1": _p("string", "First reference."), "ref2": _p("string", "Second reference."),
           "vertex1": _p("string", "Optional."), "vertex2": _p("string", "Optional."),
           "keep_position": _p("boolean", "Keep the parts where they are."),
           "assembly": _p("string", "Assembly name."), "doc": _DOC}, ["type", "ref1", "ref2"]),
    # --- FreeCAD: Python
    _tool("run_python", "freecad", "Run Python inside FreeCAD (App, Gui, doc = active document are defined; "
          "names persist between calls). Returns stdout, stderr and repr(result) if the code sets a variable "
          "named result; the traceback on error. One undo step.",
          {"code": _p("string", "Python source."), "reset": _p("boolean", "Start with fresh names.")}, ["code"]),
    # --- vault
    _tool("vault_status", "freecad", "Vault status: every item (or one part number) with lifecycle state, "
          "revision, who has it checked out and the work copy.",
          {"part_number": _p("string", "Optional P/N."), "vault": _VAULT}, read_only=True),
    _tool("vault_my_work", "freecad", "My check-outs, my items in review and items with a newer version.",
          {"vault": _VAULT}, read_only=True),
    _tool("vault_next_part_number", "freecad", "Preview the next part number (nothing is reserved).",
          {"program": _p("string", "Program code."), "document_type": _p("string", "Document type code."),
           "vault": _VAULT}, ["program", "document_type"], read_only=True),
    _tool("vault_assign_part_number", "freecad", "Reserve part numbers and write them into the document "
          "(labels of the solid parts, or the named objects, and the document label), like BruceWorx > "
          "Assign Part Number.", {"program": _p("string", "Program code."),
                                  "document_type": _p("string", "Document type code."),
                                  "objects": dict(_STRS, description="Optional object names."),
                                  "doc": _DOC, "vault": _VAULT}, ["program", "document_type"]),
    _tool("vault_checkout", "freecad", "Check out an item: take the lock and put the latest version, writable, "
          "in my work folder. Returns the work copy path.", {"part_number": _p("string", "P/N."),
                                                             "vault": _VAULT}, ["part_number"]),
    _tool("vault_checkin", "freecad", "Check in my work copy as a new version (an open document of that file "
          "is saved first). A comment is required.",
          {"part_number": _p("string", "P/N."), "comment": _p("string", "What changed (required)."),
           "keep_checked_out": _p("boolean", "Keep the lock."), "vault": _VAULT}, ["part_number", "comment"]),
    _tool("vault_undo_checkout", "freecad", "Release my lock and put back the version I started from.",
          {"part_number": _p("string", "P/N."), "vault": _VAULT}, ["part_number"]),
    _tool("vault_get_latest", "freecad", "Replace my read-only work copy with the latest version.",
          {"part_number": _p("string", "P/N."), "vault": _VAULT}, ["part_number"]),
    _tool("vault_import", "freecad", "Import the .FCStd files of a folder into the vault with new part "
          "numbers. dry_run (default true) only shows the plan; originals are never changed.",
          {"folder": _p("string", "Folder to import."), "program": _p("string", "Program code for new numbers."),
           "dry_run": _p("boolean", "Default true."), "vault": _VAULT}, ["folder", "program"]),
    _tool("vault_settings_csv", "freecad", "The vault's programs, document types and part properties as CSV "
          "(the Vault Settings table); optionally written to path.",
          {"path": _p("string", "Optional .csv path."), "vault": _VAULT}, read_only=True),
    # --- KiCad files (kicad-cli)
    _tool("kicad_board_info", "kicad", "Board facts from a .kicad_pcb file: size of the Edge.Cuts outline, "
          "thickness, copper layers, footprint count and references, nets, tracks, vias, zones.",
          {"path": _BOARD}, ["path"], read_only=True),
    _tool("kicad_export_step", "kicad", "Export the board to STEP with kicad-cli. 3D model paths "
          "(${KICADn_3DMODEL_DIR}, ${KIPRJMOD}) are resolved in a temporary copy, and STEP models stand in "
          "for missing VRML ones. Your board file is not changed.",
          {"path": _BOARD, "out": _p("string", "Output .step path."),
           "silkscreen": _p("boolean", "Include silkscreen as faces."),
           "soldermask": _p("boolean", "Include soldermask as faces."),
           "copper": _p("boolean", "Include tracks, pads and zones."),
           "components": _p("boolean", "Include component models (default true)."),
           "board_only": _p("boolean", "Board body only.")}, ["path", "out"]),
    _tool("kicad_render", "kicad", "Render the board to a PNG with kicad-cli (KiCad's 3D viewer); returns "
          "the image.", {"path": _BOARD, "out": _p("string", "Optional .png path (default: a temp file)."),
                         "side": _p("string", "top (default), bottom, left, right, front, back."),
                         "rotate": _p("string", "Optional 'X,Y,Z' degrees, e.g. '-45,0,45'."),
                         "width": _p("integer", "Default 1600."), "height": _p("integer", "Default 900."),
                         "quality": _p("string", "basic (default) or high.")}, ["path"], read_only=True),
    _tool("kicad_drc", "kicad", "Run KiCad's design rules check and summarise the violations.",
          {"path": _BOARD, "schematic_parity": _p("boolean", "Also compare with the schematic.")}, ["path"],
          read_only=True),
    _tool("kicad_bom", "kicad", "Bill of materials from a schematic (.kicad_sch, or the board's project) as CSV.",
          {"path": _p("string", "A .kicad_sch, .kicad_pro or .kicad_pcb path.")}, ["path"], read_only=True),
    # --- KiCad live (kipy)
    _tool("kicad_live_status", "kicad_live", "Whether KiCad is running with its API on, its version and the "
          "open board.", read_only=True),
    _tool("kicad_open_board", "kicad_live", "Open a board in KiCad's PCB editor (starts it if needed) and wait "
          "until the API sees it.", {"path": _BOARD}, ["path"]),
    _tool("kicad_list_footprints", "kicad_live", "Footprints on the open board: reference, value, library id, "
          "position (mm), rotation, side, locked.",
          {"filter": _p("string", "Optional regular expression on the reference.")}, read_only=True),
    _tool("kicad_select_footprints", "kicad_live", "Select footprints by reference in the PCB editor.",
          {"references": dict(_STRS, description="References, e.g. [\"J1\", \"U3\"]."),
           "add": _p("boolean", "Add to the selection instead of replacing it.")}, ["references"]),
    _tool("kicad_move_footprint", "kicad_live", "Move (and optionally rotate) one footprint; one undo step in "
          "KiCad. Not saved until kicad_save_board.",
          {"reference": _p("string", "Reference, e.g. J1."), "x": _p("number", "mm."), "y": _p("number", "mm."),
           "rotation": _p("number", "Optional degrees."), "relative": _p("boolean", "x, y (and rotation) are "
                                                                                     "offsets."),
           "allow_locked": _p("boolean", "Move it even if it is locked in KiCad.")},
          ["reference", "x", "y"]),
    _tool("kicad_board_outline", "kicad_live", "The Edge.Cuts outline of the open board: segments, arcs, "
          "circles, rectangles and polygons in mm, and the bounding box.", read_only=True),
    _tool("kicad_refresh", "kicad_live", "Redraw and zoom to fit; with reload, reread the board from disk "
          "(unsaved KiCad changes are lost).", {"reload": _p("boolean", "Reload from disk.")}),
    _tool("kicad_save_board", "kicad_live", "Save the open board in KiCad.", {}),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def public_tools():
    return [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]


# ----- the FreeCAD bridge ------------------------------------------------------------------------


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def read_state(folder):
    """bridge.json in folder, or None when absent, unreadable or its FreeCAD is gone."""
    try:
        with open(os.path.join(folder, "bridge.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("port") or not data.get("token"):
        return None
    if not _alive(data.get("pid")):
        return None
    return data


class Connection:
    """One TCP connection to a bridge; calls are serialised."""

    def __init__(self, state, timeout=CALL_TIMEOUT):
        self.state = state
        self.token = state["token"]
        self.sock = socket.create_connection(("127.0.0.1", int(state["port"])), timeout=5)
        self.sock.settimeout(timeout)
        self.reader = self.sock.makefile("rb")
        self.next_id = 0
        self.lock = threading.Lock()

    def call(self, tool, args):
        with self.lock:
            self.next_id += 1
            req_id = self.next_id
            line = json.dumps({"id": req_id, "token": self.token, "tool": tool, "args": args}) + "\n"
            self.sock.sendall(line.encode("utf-8"))
            while True:
                raw = self.reader.readline()
                if not raw:
                    raise ConnectionError("FreeCAD closed the connection")
                reply = json.loads(raw)
                if reply.get("id") == req_id or reply.get("id") is None:
                    return reply

    def close(self):
        try:
            self.reader.close()
            self.sock.close()
        except OSError:
            pass


class FreeCADLink:
    """Finds the FreeCAD to talk to: the window's bridge, else a headless job it starts."""

    def __init__(self, cfg=None):
        self.cfg = cfg or paths()
        self.conn = None
        self.kind = None  # "gui" or "headless"
        self.job = None
        self.job_dir = None
        self.lock = threading.Lock()

    # --- headless job -------------------------------------------------------------------
    def _private_cfg(self):
        os.makedirs(self.cfg["cache"], exist_ok=True)
        copy = os.path.join(self.cfg["cache"], "user.cfg")
        if not os.path.exists(copy) and os.path.exists(self.cfg["user_cfg"]):
            shutil.copyfile(self.cfg["user_cfg"], copy)
            os.chmod(copy, 0o600)
        return copy

    def _start_headless(self):
        cmd = self.cfg["freecad_cmd"]
        if not os.path.exists(cmd):
            raise ToolFailure(f"FreeCAD is not running and FreeCADCmd was not found at {cmd} (set BW_FREECAD_CMD)")
        self.job_dir = os.path.join(self.cfg["cache"], "headless", str(os.getpid()))
        os.makedirs(self.job_dir, exist_ok=True)
        try:
            os.remove(os.path.join(self.job_dir, "bridge.json"))
        except OSError:
            pass
        launcher = os.path.join(self.job_dir, "bw_mcp_headless.py")
        with open(launcher, "w", encoding="utf-8") as handle:
            handle.write("import bruceworx_bridge\nbruceworx_bridge.serve_headless()\n")
        env = dict(os.environ, BW_BRIDGE_DIR=self.job_dir, BW_BRIDGE_PORT="0", BW_BRIDGE_AUTOALLOW="1",
                   BW_BRIDGE_PARENT_PID=str(os.getpid()))
        log = open(os.path.join(self.job_dir, "freecad.log"), "wb")
        self.job = subprocess.Popen([cmd, "-u", self._private_cfg(), launcher], stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, env=env, cwd=self.job_dir)
        log.close()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = read_state(self.job_dir)
            if state and int(state.get("pid", 0)) == self.job.pid:
                return state
            if self.job.poll() is not None:
                break
            time.sleep(0.2)
        tail = ""
        try:
            with open(os.path.join(self.job_dir, "freecad.log"), "rb") as handle:
                tail = handle.read()[-1500:].decode("utf-8", "replace")
        except OSError:
            pass
        self.stop_headless()
        raise ToolFailure(f"the headless FreeCAD did not start. Its log ends:\n{tail}")

    def stop_headless(self):
        job, self.job = self.job, None
        if self.kind == "headless" and self.conn is not None:
            self.conn.close()
            self.conn, self.kind = None, None
        if job is not None and job.poll() is None:
            job.terminate()
            try:
                job.wait(5)
            except subprocess.TimeoutExpired:
                job.kill()
                job.wait(5)
        if self.job_dir:
            shutil.rmtree(self.job_dir, ignore_errors=True)

    # --- choosing ---------------------------------------------------------------------------
    def _gui_state(self):
        if self.cfg["target"] == "headless":
            return None
        return read_state(self.cfg["gui_dir"])

    def connect(self):
        """The connection to use now; switches to the window when one appears."""
        gui = self._gui_state()
        if gui is not None:
            if self.kind == "gui" and self.conn is not None and self.conn.token == gui["token"]:
                return self.conn
            try:
                conn = Connection(gui)
            except OSError:
                conn = None
            if conn is not None:
                if self.conn is not None:
                    self.conn.close()
                self.conn, self.kind = conn, "gui"
                return conn
        if self.cfg["target"] == "gui":
            raise ToolFailure("no FreeCAD window with the Claude bridge is running (BW_MCP_TARGET=gui)")
        if self.kind == "headless" and self.conn is not None and self.job is not None and self.job.poll() is None:
            return self.conn
        if self.conn is not None:
            self.conn.close()
            self.conn, self.kind = None, None
        if self.job is None or self.job.poll() is not None:
            state = self._start_headless()
        else:
            state = read_state(self.job_dir)
            if state is None:
                self.stop_headless()
                state = self._start_headless()
        self.conn, self.kind = Connection(state), "headless"
        return self.conn

    def call(self, tool, args):
        with self.lock:
            for attempt in (1, 2):
                conn = self.connect()
                try:
                    return conn.call(tool, args), self.kind
                except (OSError, ConnectionError, ValueError) as exc:
                    conn.close()
                    self.conn, self.kind = None, None
                    if attempt == 2:
                        raise ToolFailure(f"lost the connection to FreeCAD: {exc}") from None
            raise ToolFailure("unreachable")

    def status(self):
        with self.lock:
            gui = self._gui_state()
            out = {"target_setting": self.cfg["target"], "gui_bridge": None, "headless_job": None}
            if gui is not None:
                out["gui_bridge"] = {"pid": gui.get("pid"), "port": gui.get("port"), "version": gui.get("version")}
                try:
                    conn = Connection(gui, timeout=5)
                    out["gui_bridge"]["hello"] = conn.call("_hello", {}).get("result")
                    conn.close()
                except (OSError, ValueError) as exc:
                    out["gui_bridge"]["error"] = str(exc)
            if self.job is not None and self.job.poll() is None:
                out["headless_job"] = {"pid": self.job.pid}
            out["calls_go_to"] = "the FreeCAD window" if gui is not None else (
                "a headless FreeCADCmd (started on first call)" if self.cfg["target"] != "gui" else "nothing")
            out["freecad_cmd"] = self.cfg["freecad_cmd"]
            return out


# ----- KiCad files ---------------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse_sexpr(text):
    """A KiCad s-expression as nested lists; strings unquoted, other atoms kept as text."""
    stack, current = [], []
    pos, end = 0, len(text)
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"cannot parse near offset {pos}")
            break
        pos = m.end()
        if m.group(1):
            stack.append(current)
            current = []
        elif m.group(2):
            if not stack:
                raise ValueError("unbalanced ')'")
            done, current = current, stack.pop()
            current.append(done)
        elif m.group(3) is not None:
            current.append(m.group(3).replace('\\"', '"').replace("\\\\", "\\"))
        else:
            current.append(m.group(4))
    if stack:
        raise ValueError("unbalanced '('")
    return current[0] if len(current) == 1 else current


def _kids(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def _kid(node, name):
    found = _kids(node, name)
    return found[0] if found else None


def _xy(node):
    return (float(node[1]), float(node[2]))


def _edge_points(item):
    """Points that bound one Edge.Cuts graphic (for the outline's bounding box)."""
    kind = item[0]
    pts = []
    for key in ("start", "mid", "end"):
        sub = _kid(item, key)
        if sub is not None:
            pts.append(_xy(sub))
    if kind in ("gr_circle", "fp_circle"):
        c, e = _kid(item, "center"), _kid(item, "end")
        if c is not None and e is not None:
            (cx, cy), (ex, ey) = _xy(c), _xy(e)
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            pts = [(cx - r, cy - r), (cx + r, cy + r)]
    poly = _kid(item, "pts")
    if poly is not None:
        pts += [_xy(p) for p in _kids(poly, "xy")]
    return pts


def _layer(item):
    lay = _kid(item, "layer")
    return lay[1] if lay is not None and len(lay) > 1 else ""


def board_info(path):
    with open(path, encoding="utf-8") as handle:
        tree = parse_sexpr(handle.read())
    if not tree or tree[0] != "kicad_pcb":
        raise ToolFailure(f"{path} is not a .kicad_pcb file")
    general = _kid(tree, "general")
    thickness = _kid(general, "thickness") if general else None
    layers = _kid(tree, "layers")
    layer_names = [entry[1] for entry in layers[1:] if isinstance(entry, list) and len(entry) > 1] if layers else []
    copper = [n for n in layer_names if n.endswith(".Cu")]
    footprints = _kids(tree, "footprint") or _kids(tree, "module")
    refs = []
    sides = {"top": 0, "bottom": 0}
    for fp in footprints:
        ref = None
        for prop in _kids(fp, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = prop[2]
        if ref is None:
            for text in _kids(fp, "fp_text"):
                if len(text) > 2 and text[1] == "reference":
                    ref = text[2]
        refs.append(ref or "?")
        sides["bottom" if _layer(fp).startswith("B.") else "top"] += 1
    points = []
    for item in tree[1:]:
        if isinstance(item, list) and item and item[0].startswith("gr_") and _layer(item) == "Edge.Cuts":
            points += _edge_points(item)
    out = {
        "file": os.path.abspath(path),
        "thickness_mm": float(thickness[1]) if thickness else None,
        "copper_layers": len(copper), "layers": layer_names,
        "footprints": len(footprints), "top": sides["top"], "bottom": sides["bottom"],
        "references": sorted(refs, key=_ref_key),
        "nets": max(0, len(_kids(tree, "net")) - 1),
        "tracks": len(_kids(tree, "segment")) + len(_kids(tree, "arc")),
        "vias": len(_kids(tree, "via")), "zones": len(_kids(tree, "zone")),
    }
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        out["outline_bbox_mm"] = {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)],
                                  "size": [round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6)]}
    else:
        out["outline_bbox_mm"] = None
    title = _kid(tree, "title_block")
    if title is not None:
        out["title_block"] = {c[0]: c[1] for c in title[1:] if isinstance(c, list) and len(c) > 1}
    return out


def _ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref or "")
    return (m.group(1), int(m.group(2)) if m.group(2) else 0, ref)


_MODEL = re.compile(r'(\(model\s+)"([^"]*)"')
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def resolve_models(text, board_dir, models_dir):
    """The board text with 3D model paths made absolute; (text, resolved, missing).

    ${KICADn_3DMODEL_DIR} -> models_dir, ${KIPRJMOD} -> the board's folder; a .wrl that is
    not there is replaced by the .step (or .stp) next to it, since KiCad ships STEP only.
    """
    resolved, missing = [], []

    def fix(m):
        raw = m.group(2)

        def var(v):
            name = v.group(1)
            if re.fullmatch(r"KICAD\d*_3DMODEL_DIR", name):
                return models_dir
            if name == "KIPRJMOD":
                return board_dir
            return os.environ.get(name, v.group(0))

        path = _VAR.sub(var, raw)
        if not os.path.isabs(path) and "${" not in path:
            path = os.path.join(board_dir, path)
        if not os.path.exists(path):
            stem, ext = os.path.splitext(path)
            for alt in (".step", ".stp", ".STEP", ".wrl"):
                if alt.lower() != ext.lower() and os.path.exists(stem + alt):
                    path = stem + alt
                    break
        (resolved if os.path.exists(path) else missing).append(path if os.path.exists(path) else raw)
        return f'{m.group(1)}"{path}"'

    return _MODEL.sub(fix, text), resolved, missing


def _cli(cfg):
    cli = cfg["kicad_cli"]
    if not os.path.exists(cli):
        raise ToolFailure(f"kicad-cli not found at {cli} (set BW_KICAD_CLI)")
    return cli


def _run(cmd, timeout=600):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolFailure(f"{os.path.basename(cmd[0])} took longer than {timeout} s") from None
    return proc


def _board(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ToolFailure(f"no file at {path}")
    if not path.endswith(".kicad_pcb"):
        raise ToolFailure("path must be a .kicad_pcb file")
    return path


def kicad_export_step(cfg, args):
    board = _board(args["path"])
    out = os.path.abspath(os.path.expanduser(args["out"]))
    if not out.lower().endswith((".step", ".stp")):
        raise ToolFailure("out must end in .step or .stp")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(board, encoding="utf-8") as handle:
        text, resolved, missing = resolve_models(handle.read(), os.path.dirname(board), cfg["kicad_models"])
    with tempfile.TemporaryDirectory(prefix="bw-kicad-") as tmp:
        copy = os.path.join(tmp, os.path.basename(board))
        with open(copy, "w", encoding="utf-8") as handle:
            handle.write(text)
        project = os.path.splitext(board)[0] + ".kicad_pro"
        if os.path.exists(project):
            shutil.copyfile(project, os.path.join(tmp, os.path.basename(project)))
        cmd = [_cli(cfg), "pcb", "export", "step", "--force", "--subst-models", "-o", out]
        if args.get("silkscreen"):
            cmd.append("--include-silkscreen")
        if args.get("soldermask"):
            cmd.append("--include-soldermask")
        if args.get("copper"):
            cmd += ["--include-tracks", "--include-pads", "--include-zones"]
        if args.get("components") is False:
            cmd.append("--no-components")
        if args.get("board_only"):
            cmd.append("--board-only")
        proc = _run(cmd + [copy])
    if proc.returncode != 0 or not os.path.exists(out):
        raise ToolFailure(f"kicad-cli failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-3000:]}")
    return {"step": out, "bytes": os.path.getsize(out), "models_resolved": len(resolved),
            "models_missing": missing[:50], "log": proc.stdout[-1500:]}


def kicad_render(cfg, args):
    board = _board(args["path"])
    out = args.get("out")
    temp = not out
    if out:
        out = os.path.abspath(os.path.expanduser(out))
    else:
        fd, out = tempfile.mkstemp(prefix="bw-kicad-", suffix=".png")
        os.close(fd)
    cmd = [_cli(cfg), "pcb", "render", "-o", out, "--side", args.get("side") or "top",
           "-w", str(int(args.get("width") or 1600)), "-h", str(int(args.get("height") or 900)),
           "--quality", args.get("quality") or "basic"]
    if args.get("rotate"):
        cmd += ["--rotate", str(args["rotate"])]
    proc = _run(cmd + [board])
    if proc.returncode != 0 or not os.path.exists(out):
        raise ToolFailure(f"kicad-cli render failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-3000:]}")
    with open(out, "rb") as handle:
        data = handle.read()
    if temp:
        os.remove(out)
    return {"image": out if not temp else None, "bytes": len(data),
            "_png": data if out.lower().endswith(".png") or temp else None}


def summarize_drc(report):
    out = {"violations": 0, "unconnected": 0, "schematic_parity": 0, "by_severity": {}, "by_type": {},
           "first": []}
    for key, label in (("violations", "violations"), ("unconnected_items", "unconnected"),
                       ("schematic_parity", "schematic_parity")):
        items = report.get(key) or []
        out[label] = len(items)
        for item in items:
            sev = item.get("severity", "?")
            out["by_severity"][sev] = out["by_severity"].get(sev, 0) + 1
            typ = item.get("type", "?")
            out["by_type"][typ] = out["by_type"].get(typ, 0) + 1
            if len(out["first"]) < 25:
                out["first"].append({"type": typ, "severity": sev, "description": item.get("description", ""),
                                     "items": [i.get("description", "") for i in item.get("items", [])][:3]})
    return out


def kicad_drc(cfg, args):
    board = _board(args["path"])
    with tempfile.TemporaryDirectory(prefix="bw-kicad-") as tmp:
        report_path = os.path.join(tmp, "drc.json")
        cmd = [_cli(cfg), "pcb", "drc", "--format", "json", "--severity-all", "-o", report_path]
        if args.get("schematic_parity"):
            cmd.append("--schematic-parity")
        proc = _run(cmd + [board])
        if not os.path.exists(report_path):
            raise ToolFailure(f"kicad-cli drc failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-3000:]}")
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
    out = summarize_drc(report)
    out["board"] = board
    out["kicad_version"] = report.get("kicad_version")
    return out


def kicad_bom(cfg, args):
    path = os.path.abspath(os.path.expanduser(args["path"]))
    if path.endswith((".kicad_pcb", ".kicad_pro")):
        path = os.path.splitext(path)[0] + ".kicad_sch"
    if not os.path.isfile(path):
        raise ToolFailure(f"no schematic at {path}")
    with tempfile.TemporaryDirectory(prefix="bw-kicad-") as tmp:
        out = os.path.join(tmp, "bom.csv")
        proc = _run([_cli(cfg), "sch", "export", "bom", "-o", out, path])
        if proc.returncode != 0 or not os.path.exists(out):
            raise ToolFailure(f"kicad-cli bom failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-3000:]}")
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
    return {"schematic": path, "rows": max(0, len(text.strip().splitlines()) - 1), "csv": text[:30000]}


KICAD_FILE_TOOLS = {
    "kicad_board_info": lambda cfg, args: board_info(_board(args["path"])),
    "kicad_export_step": kicad_export_step,
    "kicad_render": kicad_render,
    "kicad_drc": kicad_drc,
    "kicad_bom": kicad_bom,
}


# ----- KiCad live (kipy in its own Python) --------------------------------------------------------------


def kicad_live(cfg, tool, args, timeout=120):
    python = cfg["kicad_python"]
    if not os.path.exists(python):
        raise ToolFailure("live KiCad is not set up: kicad-python (kipy) has no Python at "
                          f"{python} (set BW_KICAD_PYTHON); the kicad_* file tools still work")
    helper = os.path.join(HERE, "kicad_live.py")
    try:
        proc = subprocess.run([python, "-I", helper], input=json.dumps({"tool": tool, "args": args}),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolFailure(f"KiCad did not answer within {timeout} s") from None
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise ToolFailure(f"the KiCad helper failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-2000:]}")
    reply = json.loads(lines[-1])
    if not reply.get("ok"):
        raise ToolFailure(reply.get("error", "KiCad call failed"))
    return reply.get("result")


# ----- MCP ---------------------------------------------------------------------------------------------------


def _text(result):
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=1, ensure_ascii=False, default=str)


class Server:
    def __init__(self, out=None, cfg=None):
        self.cfg = cfg or paths()
        self.out = out or sys.stdout
        self.write_lock = threading.Lock()
        self.freecad = FreeCADLink(self.cfg)
        self.protocol = PROTOCOLS[0]

    # --- output ---------------------------------------------------------------------
    def send(self, message):
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def reply(self, req_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self.send(message)

    # --- requests -----------------------------------------------------------------------
    def handle(self, message):
        """Handle one JSON-RPC message; returns the response dict (or None for a notification)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        method = message.get("method")
        req_id = message.get("id")
        is_note = "id" not in message
        params = message.get("params") or {}
        if not isinstance(method, str):
            return None if is_note else {"jsonrpc": "2.0", "id": req_id,
                                         "error": {"code": -32600, "message": "Invalid Request"}}
        if is_note:
            return None  # notifications/initialized, notifications/cancelled, ...
        try:
            if method == "initialize":
                result = self.initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": public_tools()}
            elif method == "tools/call":
                result = self.call_tool(params)
            elif method in ("resources/list", "prompts/list"):
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except _ParamsError as exc:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
        except Exception as exc:  # a bug here must not kill the server
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def initialize(self, params):
        asked = params.get("protocolVersion")
        self.protocol = asked if asked in PROTOCOLS else PROTOCOLS[0]
        return {
            "protocolVersion": self.protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "title": "BruceWorx FreeCAD and KiCad", "version": SERVER_VERSION},
            "instructions": (
                "Tools for the BruceWorx FreeCAD fork and KiCad. FreeCAD tools reach the open FreeCAD window "
                "(the user is asked once per session to allow it) or, when none is open, a headless FreeCAD. "
                "Use object_tree to learn object names before modelling; every modelling call is one undo "
                "step. kicad_* tools work on files with kicad-cli; kicad_live/open/list/move tools need KiCad "
                "running with its API enabled."),
        }

    def call_tool(self, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise _ParamsError("arguments must be an object")
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            raise _ParamsError(f"Unknown tool: {name}")
        try:
            return self._run_tool(tool, args)
        except ToolFailure as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}

    def _run_tool(self, tool, args):
        name, target = tool["name"], tool["_target"]
        if target == "server":
            return _ok(self.status())
        if target == "kicad":
            result = KICAD_FILE_TOOLS[name](self.cfg, args)
            png = result.pop("_png", None) if isinstance(result, dict) else None
            content = [{"type": "text", "text": _text(result)}]
            if png:
                content.append({"type": "image", "data": base64.b64encode(png).decode("ascii"),
                                "mimeType": "image/png"})
            return {"content": content, "isError": False}
        if target == "kicad_live":
            return _ok(kicad_live(self.cfg, name, args))
        reply, kind = self.freecad.call(name, args)
        if not reply.get("ok"):
            return {"content": [{"type": "text", "text": f"FreeCAD ({kind}): {reply.get('error')}"}], "isError": True}
        result = reply.get("result")
        if name == "screenshot" and isinstance(result, dict) and "png_base64" in result:
            data = result.pop("png_base64")
            return {"content": [{"type": "image", "data": data, "mimeType": "image/png"},
                                {"type": "text", "text": _text(result)}], "isError": False}
        return _ok(result)

    def status(self):
        out = {"server": SERVER_VERSION, "freecad": self.freecad.status()}
        out["kicad_cli"] = self.cfg["kicad_cli"] if os.path.exists(self.cfg["kicad_cli"]) else None
        out["kicad_live"] = ("kipy available at " + self.cfg["kicad_python"]) if os.path.exists(
            self.cfg["kicad_python"]) else "unavailable: no kicad-python (kipy) Python"
        return out

    # --- loop ---------------------------------------------------------------------------------
    def serve(self, stream=None):
        stream = stream or sys.stdin
        workers = []
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(message, list):  # a batch: answer in order, on this thread
                answers = [a for a in (self.handle(m) for m in message) if a is not None]
                if answers:
                    self.send(answers)
                continue
            if isinstance(message, dict) and message.get("method") == "tools/call":
                worker = threading.Thread(target=self._answer, args=(message,), daemon=True)
                worker.start()
                workers.append(worker)
                workers = [w for w in workers if w.is_alive()]
                continue
            self._answer(message)
        for worker in workers:  # stdin closed: let running calls finish briefly
            worker.join(10)

    def _answer(self, message):
        answer = self.handle(message)
        if answer is not None:
            self.send(answer)

    def close(self):
        self.freecad.stop_headless()


class _ParamsError(Exception):
    pass


def _ok(result):
    return {"content": [{"type": "text", "text": _text(result)}], "isError": False}


def selftest():
    server = Server(out=sys.stderr)
    tools = public_tools()
    print(f"{len(tools)} tools:", ", ".join(t["name"] for t in tools))
    try:
        status = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "bridge_status", "arguments": {}}})
        print(status["result"]["content"][0]["text"])
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "list_documents", "arguments": {}}})
        print("list_documents:", listed["result"]["content"][0]["text"])
        return 0 if not listed["result"]["isError"] else 1
    finally:
        server.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    server = Server()

    def stop(_signum, _frame):
        server.close()
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
