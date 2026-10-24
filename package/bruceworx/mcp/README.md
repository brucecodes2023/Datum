# BruceWorx MCP server (FreeCAD and KiCad for Claude Code)

`bruceworx_mcp.py` is an MCP server (Model Context Protocol: JSON-RPC 2.0 over stdin/stdout)
that gives Claude Code tools for the BruceWorx FreeCAD build and for KiCad. Plan: AUT-03,
`docs/fusion-ui-plan/chapters/86-aut03-plan.tex`. Python 3.9+ standard library only.

## How Claude Code finds it

`.mcp.json` at the repository root registers it for this project:

```json
{"mcpServers": {"bruceworx": {"command": "/opt/homebrew/bin/python3",
                              "args": ["package/bruceworx/mcp/bruceworx_mcp.py"]}}}
```

Start Claude Code in the repository root; it asks once whether to use the project's
`bruceworx` server. Nothing is added to your global Claude Code settings. `/mcp` shows it.

## Using FreeCAD from Claude Code

* **FreeCAD open:** the BruceWorx bridge in FreeCAD (`src/Mod/BruceWorx/bruceworx_bridge.py`)
  listens on 127.0.0.1. The first call in a FreeCAD session asks
  *"Allow Claude Code to control FreeCAD for this session?"* (Allow / Deny; Deny is the
  default button). Calls wait for your answer (up to two minutes). Allowed, a
  **Claude connected** chip appears in the status bar; click it to disconnect, and the next
  call asks again. Deny refuses calls for the rest of the session; *BruceWorx > Allow
  Claude Code* allows it later. Switch: *Preferences > BruceWorx > Claude Code bridge*.
* **FreeCAD not open:** the server starts a headless `FreeCADCmd` with the same bridge, on a
  private copy of your preferences (`~/Library/Caches/bruceworx-mcp/user.cfg`, copied once;
  your real `user.cfg` is never written), and stops it when Claude Code exits. Screenshots
  need the window. `bridge_status` says which FreeCAD the calls reach.
* Every modelling call and every `run_python` is one undo step (*Edit > Undo*). Every call
  is listed in the Report view and in `<FreeCAD user data>/BruceWorx/bridge.log`.

## Tools

| Group | Tools |
|---|---|
| Status | `bridge_status` |
| Read | `list_documents`, `object_tree`, `get_properties`, `selection`, `measure`, `screenshot` |
| Documents | `new_document`, `open_document`, `recompute`, `undo`, `save`, `export_step` |
| Model | `new_body`, `create_sketch` (lines, circles, arcs, rectangles; auto constraints; dimensions), `pad`, `pocket`, `revolve`, `hole`, `fillet`, `chamfer`, `rectangular_pattern`, `circular_pattern`, `set_property`, `set_parameter`, `add_component`, `add_joint` |
| Python | `run_python` (stdout, stderr, `repr(result)`, traceback) |
| Vault | `vault_status`, `vault_my_work`, `vault_next_part_number`, `vault_assign_part_number`, `vault_checkout`, `vault_checkin` (comment required), `vault_undo_checkout`, `vault_get_latest`, `vault_import` (dry run by default), `vault_settings_csv` |
| KiCad files | `kicad_board_info`, `kicad_export_step`, `kicad_render`, `kicad_drc`, `kicad_bom` |
| KiCad live | `kicad_live_status`, `kicad_open_board`, `kicad_list_footprints`, `kicad_select_footprints`, `kicad_move_footprint`, `kicad_board_outline`, `kicad_refresh`, `kicad_save_board` |

Vault tools use the vault in *Preferences > BruceWorx > VaultPath* (or a `vault` argument) and
the actor pinned for that vault, as the Vault panel does.

## KiCad

* File tools run `kicad-cli` on a file; they need KiCad installed, not running. STEP export
  resolves `${KICADn_3DMODEL_DIR}` and `${KIPRJMOD}` in a temporary copy of the board and uses
  the STEP model when only that exists (KiCad ships STEP only); your board is not changed.
* Live tools talk to a running KiCad through its IPC API, with `kicad-python` (`kipy`) in
  `.tools/kicad-venv` (run by `kicad_live.py`). They need KiCad's API on
  (*Preferences > Plugins > Enable KiCad API*). Without KiCad they say so. KiCad's PCB editor
  can stall on startup while the Mac's display sleeps if it shows an info bar (for example
  "older file format"); open the board once by hand, or upgrade it, if `kicad_open_board`
  times out.

## Security

* The bridge listens on 127.0.0.1 only. `bridge.json` (port and a random token) is mode 600
  and removed when FreeCAD quits; a call without the token is refused.
* The first call per FreeCAD session needs your Allow; the chip disconnects at any time.
* `run_python` can do anything the Python console can; that is why the session prompt exists.
* The headless FreeCAD accepts calls without a prompt because only this server, started by
  your Claude Code, has its token (`BW_BRIDGE_AUTOALLOW=1`, honoured only without a window).

## Settings (environment)

`BW_FREECAD_CMD`, `BW_FREECAD_USER_CFG`, `BW_MCP_CACHE`, `BW_BRIDGE_DIR` (where FreeCAD's
`bridge.json` is), `BW_MCP_TARGET` (`auto`, `gui`, `headless`), `BW_KICAD_CLI`,
`BW_KICAD_3DMODELS`, `BW_KICAD_PYTHON`. The bridge itself reads `BW_BRIDGE_DIR`,
`BW_BRIDGE_PORT` (default 47820, else any free port) and, headless only, `BW_BRIDGE_AUTOALLOW`.

## Tests

```sh
/opt/homebrew/bin/python3 -m unittest -v package/bruceworx/mcp/test_bruceworx_mcp.py
build/release/bin/FreeCADCmd -u <copy of user.cfg> -t TestBruceWorxBridge
python3 package/bruceworx/mcp/bruceworx_mcp.py --selftest
```

The first covers the protocol (fake bridge), the schemas against the bridge, KiCad file tools
on a copy of KiCad's Arduino_Mega template, a headless end-to-end run (create, query and export
a part, patterns, an assembly, a test vault), and live KiCad when it runs with a board open.
