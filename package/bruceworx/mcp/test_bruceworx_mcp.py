# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the BruceWorx MCP server (bruceworx_mcp.py) and the live KiCad helper.

    /opt/homebrew/bin/python3 -m unittest -v package/bruceworx/mcp/test_bruceworx_mcp.py

* Protocol: initialize, tools/list, tools/call, errors, against a fake bridge.
* The tool schemas agree with the bridge's argument specs (src/Mod/BruceWorx/bruceworx_bridge.py).
* KiCad file tools on a copy of KiCad's Arduino_Mega template (skipped without KiCad).
* End to end, headless: the server starts FreeCADCmd and an agent creates, queries and exports
  a part, then drives a test vault (skipped without a FreeCAD build; BW_MCP_SKIP_E2E=1 skips).
* Live KiCad: skipped unless KiCad runs with its API on and a board open.

Nothing here writes the user's FreeCAD config or FreeCAD's bridge.json: every run uses its
own temporary BW_BRIDGE_DIR and BW_MCP_CACHE.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import bruceworx_mcp as mcp  # noqa: E402

TEMPLATE = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/template/Arduino_Mega"
FREECAD_CMD = mcp.paths()["freecad_cmd"]
REAL_CFG = os.path.expanduser("~/Library/Preferences/FreeCAD/v1-1/user.cfg")
CHECKLIST = os.path.expanduser("~/Developer/BruceWorx-checklist")


def load_bridge():
    path = os.path.join(REPO, "src", "Mod", "BruceWorx", "bruceworx_bridge.py")
    spec = importlib.util.spec_from_file_location("bruceworx_bridge_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBridge:
    """A bridge on a free port answering from a dict; writes bridge.json like FreeCAD does."""

    def __init__(self, folder, answers):
        self.answers = answers
        self.calls = []
        self.token = "secret"
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(2)
        port = self.server.getsockname()[1]
        with open(os.path.join(folder, "bridge.json"), "w", encoding="utf-8") as handle:
            json.dump({"port": port, "token": self.token, "pid": os.getpid(), "version": "test"}, handle)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            reader = conn.makefile("rb")
            for raw in reader:
                req = json.loads(raw)
                self.calls.append(req)
                if req.get("token") != self.token:
                    reply = {"id": req.get("id"), "ok": False, "error": "bad token"}
                elif req["tool"] in self.answers:
                    reply = {"id": req["id"], "ok": True, "result": self.answers[req["tool"]]}
                else:
                    reply = {"id": req["id"], "ok": False, "error": f"ToolError: no {req['tool']}"}
                conn.sendall((json.dumps(reply) + "\n").encode())

    def close(self):
        self.server.close()


def rpc(server, method, params=None, req_id=1):
    message = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = mcp.paths()
        cfg.update(gui_dir=self.tmp, target="gui", cache=os.path.join(self.tmp, "cache"))
        self.server = mcp.Server(out=io.StringIO(), cfg=cfg)

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_initialize_echoes_known_version(self):
        result = rpc(self.server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "bruceworx")
        result = rpc(self.server, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")

    def test_notification_gets_no_answer(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping_and_unknown_method(self):
        self.assertEqual(rpc(self.server, "ping")["result"], {})
        self.assertEqual(rpc(self.server, "resources/read")["error"]["code"], -32601)
        self.assertEqual(self.server.handle({"id": 1})["error"]["code"], -32600)

    def test_tools_list_is_well_formed(self):
        tools = rpc(self.server, "tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), len(set(names)))
        for tool in tools:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertFalse(any(k.startswith("_") for k in tool))
            for req in tool["inputSchema"].get("required", []):
                self.assertIn(req, tool["inputSchema"]["properties"], tool["name"])
        for name in ("create_sketch", "pad", "run_python", "vault_checkin", "kicad_export_step",
                     "kicad_move_footprint", "screenshot"):
            self.assertIn(name, names)

    def test_schemas_match_the_bridge(self):
        bridge = load_bridge()
        freecad = {t["name"]: t for t in mcp.TOOLS if t["_target"] == "freecad"}
        self.assertEqual(set(freecad), set(bridge.SPECS))
        kinds = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean",
                 "array": "array", "object": "object"}
        for name, (_kind, spec) in bridge.SPECS.items():
            schema = freecad[name]["inputSchema"]
            self.assertEqual(set(schema["properties"]), set(spec), name)
            required = {arg for arg, (_t, req, _d) in spec.items() if req}
            self.assertEqual(set(schema.get("required", [])), required, name)
            for arg, (kind, _req, _default) in spec.items():
                if kind in kinds:
                    self.assertEqual(schema["properties"][arg].get("type"), kinds[kind], f"{name}.{arg}")

    def test_unknown_tool_is_a_params_error(self):
        self.assertEqual(rpc(self.server, "tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)

    def test_call_is_forwarded_with_the_token(self):
        fake = FakeBridge(self.tmp, {"list_documents": [{"name": "A"}]})
        try:
            result = rpc(self.server, "tools/call", {"name": "list_documents", "arguments": {}})["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"]), [{"name": "A"}])
            self.assertEqual(fake.calls[-1]["token"], "secret")
            self.assertEqual(fake.calls[-1]["tool"], "list_documents")
            failed = rpc(self.server, "tools/call", {"name": "pad", "arguments": {"sketch": "S"}})["result"]
            self.assertTrue(failed["isError"])
            self.assertIn("no pad", failed["content"][0]["text"])
        finally:
            fake.close()

    def test_screenshot_becomes_an_image(self):
        png = base64.b64encode(b"\x89PNG fake").decode()
        fake = FakeBridge(self.tmp, {"screenshot": {"png_base64": png, "width": 10, "height": 10}})
        try:
            result = rpc(self.server, "tools/call", {"name": "screenshot", "arguments": {}})["result"]
            self.assertEqual(result["content"][0], {"type": "image", "data": png, "mimeType": "image/png"})
        finally:
            fake.close()

    def test_no_freecad_with_target_gui(self):
        result = rpc(self.server, "tools/call", {"name": "list_documents", "arguments": {}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("no FreeCAD window", result["content"][0]["text"])

    def test_stale_bridge_file_is_ignored(self):
        with open(os.path.join(self.tmp, "bridge.json"), "w", encoding="utf-8") as handle:
            json.dump({"port": 1, "token": "t", "pid": 999999}, handle)
        self.assertIsNone(mcp.read_state(self.tmp))

    def test_serve_loop(self):
        out = io.StringIO()
        server = mcp.Server(out=out, cfg=self.server.cfg)
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "{not json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        server.serve(io.StringIO(lines))
        answers = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([a.get("id") for a in answers], [1, None, 2])
        self.assertEqual(answers[1]["error"]["code"], -32700)


class TestSexpr(unittest.TestCase):
    def test_parse(self):
        tree = mcp.parse_sexpr('(a (b "x \\"y\\"") (c 1 2) d)')
        self.assertEqual(tree, ["a", ["b", 'x "y"'], ["c", "1", "2"], "d"])
        with self.assertRaises(ValueError):
            mcp.parse_sexpr("(a (b)")

    def test_resolve_models(self):
        models = tempfile.mkdtemp()
        os.makedirs(os.path.join(models, "Lib.3dshapes"))
        open(os.path.join(models, "Lib.3dshapes", "Part.step"), "w").close()
        text = ('(model "${KICAD9_3DMODEL_DIR}/Lib.3dshapes/Part.wrl")\n'
                '(model "${KICAD6_3DMODEL_DIR}/Lib.3dshapes/Gone.wrl")')
        out, resolved, missing = mcp.resolve_models(text, "/board", models)
        self.assertIn(os.path.join(models, "Lib.3dshapes", "Part.step"), out)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(missing, ["${KICAD6_3DMODEL_DIR}/Lib.3dshapes/Gone.wrl"])

    def test_drc_summary(self):
        report = {"violations": [{"type": "clearance", "severity": "error", "description": "d",
                                  "items": [{"description": "a"}]}],
                  "unconnected_items": [{"type": "unconnected_items", "severity": "error"}]}
        out = mcp.summarize_drc(report)
        self.assertEqual(out["violations"], 1)
        self.assertEqual(out["unconnected"], 1)
        self.assertEqual(out["by_severity"], {"error": 2})


@unittest.skipUnless(os.path.isdir(TEMPLATE) and os.path.exists(mcp.paths()["kicad_cli"]), "KiCad not installed")
class TestKiCadFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.dir = os.path.join(cls.tmp, "Arduino_Mega")
        shutil.copytree(TEMPLATE, cls.dir)
        cls.board = os.path.join(cls.dir, "Arduino_Mega.kicad_pcb")
        cls.server = mcp.Server(out=io.StringIO())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, name, **args):
        result = rpc(self.server, "tools/call", {"name": name, "arguments": args})["result"]
        self.assertFalse(result["isError"], result["content"][0].get("text"))
        return result

    def test_board_info(self):
        info = json.loads(self.call("kicad_board_info", path=self.board)["content"][0]["text"])
        self.assertEqual(info["footprints"], 13)
        self.assertIn("J1", info["references"])
        self.assertEqual(info["copper_layers"], 2)
        self.assertAlmostEqual(info["outline_bbox_mm"]["size"][0], 101.6, places=2)
        self.assertAlmostEqual(info["outline_bbox_mm"]["size"][1], 53.34, places=2)

    def test_export_step_resolves_models(self):
        before = open(self.board, encoding="utf-8").read()
        out = os.path.join(self.tmp, "board.step")
        info = json.loads(self.call("kicad_export_step", path=self.board, out=out, silkscreen=True)
                          ["content"][0]["text"])
        self.assertGreater(info["bytes"], 100000)
        self.assertGreater(info["models_resolved"], 0)
        self.assertEqual(info["models_missing"], [])
        self.assertEqual(open(self.board, encoding="utf-8").read(), before)  # the board is not touched

    def test_render(self):
        result = self.call("kicad_render", path=self.board, width=400, height=300)
        image = result["content"][1]
        self.assertEqual(image["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\x89PNG"))

    def test_drc_and_bom(self):
        drc = json.loads(self.call("kicad_drc", path=self.board)["content"][0]["text"])
        self.assertIn("by_type", drc)
        bom = json.loads(self.call("kicad_bom", path=self.board)["content"][0]["text"])
        self.assertGreater(bom["rows"], 0)

    def test_bad_path(self):
        result = rpc(self.server, "tools/call", {"name": "kicad_board_info", "arguments": {"path": "/nope.kicad_pcb"}})
        self.assertTrue(result["result"]["isError"])


def _kicad_live_ready():
    python = mcp.paths()["kicad_python"]
    if not os.path.exists(python):
        return False
    try:
        return bool(mcp.kicad_live(mcp.paths(), "kicad_live_status", {}, timeout=20).get("open_boards"))
    except Exception:
        return False


@unittest.skipUnless(_kicad_live_ready(), "KiCad is not running with its API on and a board open")
class TestKiCadLive(unittest.TestCase):
    def test_list_outline_select(self):
        cfg = mcp.paths()
        listed = mcp.kicad_live(cfg, "kicad_list_footprints", {})
        self.assertGreater(listed["count"], 0)
        outline = mcp.kicad_live(cfg, "kicad_board_outline", {})
        self.assertTrue(outline["items"])
        ref = listed["footprints"][0]["reference"]
        self.assertEqual(mcp.kicad_live(cfg, "kicad_select_footprints", {"references": [ref]})["selected"], [ref])

    def test_unknown_reference(self):
        with self.assertRaisesRegex(mcp.ToolFailure, "no footprint"):
            mcp.kicad_live(mcp.paths(), "kicad_move_footprint", {"reference": "NOPE999", "x": 0, "y": 0})


@unittest.skipIf(os.environ.get("BW_MCP_SKIP_E2E") == "1" or not os.path.exists(FREECAD_CMD),
                 "no FreeCAD build (or BW_MCP_SKIP_E2E=1)")
class TestHeadlessEndToEnd(unittest.TestCase):
    """The plan's acceptance: an agent creates, queries and exports a part headless."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bw-mcp-e2e-")
        cfg_copy = os.path.join(cls.tmp, "user.cfg")
        shutil.copyfile(os.environ.get("BW_FREECAD_USER_CFG", REAL_CFG), cfg_copy)  # read-only use
        env = dict(os.environ, BW_MCP_CACHE=os.path.join(cls.tmp, "cache"), BW_FREECAD_USER_CFG=cfg_copy,
                   BW_BRIDGE_DIR=os.path.join(cls.tmp, "no-gui"), BW_MCP_TARGET="headless",
                   BW_PDM_ACTOR="claude@test")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "bruceworx_mcp.py")], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, text=True, env=env)
        cls.next_id = 0
        cls.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"}})
        cls.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        cls.proc.wait(60)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def send(cls, method, params):
        cls.next_id += 1
        cls.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": cls.next_id, "method": method, "params": params}) + "\n")
        cls.proc.stdin.flush()
        return json.loads(cls.proc.stdout.readline())

    def call(self, _tool, ok=True, **args):
        result = self.send("tools/call", {"name": _tool, "arguments": args})["result"]
        text = result["content"][0].get("text", "")
        self.assertEqual(result["isError"], not ok, f"{_tool}: {text}")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def test_1_part(self):
        self.assertEqual(self.call("new_document", name="Bracket")["doc"], "Bracket")
        body = self.call("new_body", label="Bracket")["body"]
        sketch = self.call("create_sketch", geometry=[{"rectangle": [0, 0, 60, 30]}, {"circle": [15, 15, 4]},
                                                      {"circle": [45, 15, 4]}])
        self.assertEqual((sketch["closed_wires"], sketch["open_wires"]), (3, 0))
        pad = self.call("pad", sketch=sketch["sketch"], length=8)
        self.assertGreater(pad["body_volume_mm3"], 0)
        self.call("fillet", object=pad["feature"], edges=["Edge1", "Edge3"], radius=2)
        tree = self.call("object_tree")
        self.assertEqual([n["name"] for n in tree["tree"]], [body])
        measured = self.call("measure", object=body)
        self.assertAlmostEqual(measured["bbox"]["size"][2], 8.0, places=6)
        step = os.path.join(self.tmp, "bracket.step")
        exported = self.call("export_step", path=step)
        self.assertTrue(os.path.getsize(step) > 1000)
        self.assertEqual(exported["objects"], [body])
        out = self.call("run_python", code="print(doc.Name)\nresult = round(doc.getObject('Body').Shape.Volume, 3)\n"
                                            "doc.getObject('Body').Label = 'Renamed'")
        self.assertEqual(out["stdout"], "Bracket\n")
        self.assertAlmostEqual(float(out["result"]), measured["volume_mm3"], places=2)
        self.assertEqual(self.call("undo")["undone"], ["Claude: run_python"])
        self.assertEqual(self.call("get_properties", object=body)["label"], "Bracket")
        self.assertIn("needs the FreeCAD window", self.call("screenshot", ok=False))
        saved = self.call("save", path=os.path.join(self.tmp, "Bracket.FCStd"))
        self.assertTrue(os.path.exists(saved["file"]))

    def test_2_patterns_and_properties(self):
        self.call("new_document", name="Pat")
        self.call("new_body")
        sk = self.call("create_sketch", geometry=[{"rectangle": [0, 0, 10, 10]}])
        pad = self.call("pad", sketch=sk["sketch"], length=2)
        sk2 = self.call("create_sketch", plane=f"{pad['feature']}:Face6", geometry=[{"circle": [2, 2, 1]}])
        hole = self.call("pocket", sketch=sk2["sketch"], through_all=True)
        lin = self.call("rectangular_pattern", object=hole["feature"], direction=[1, 0, 0], count=3, spacing=3)
        self.assertEqual(lin["engine"], "PartDesign::LinearPattern")
        self.call("set_property", object=pad["feature"], property="Length", value="4 mm")
        props = self.call("get_properties", object=pad["feature"])
        self.assertEqual(props["properties"]["Length"]["value"], "4.00 mm")
        self.call("run_python", code="b = doc.addObject('Part::Box', 'Box')\ndoc.recompute()")
        circ = self.call("circular_pattern", object="Box", axis="Z", center=[30, 0, 0], count=4)
        self.assertEqual(circ["instances"], 3)
        self.assertIn("type must be one of", self.call("add_joint", ok=False, type="Hinge", ref1="a.Face1",
                                                       ref2="b.Face1"))

    def test_3_assembly(self):
        part = os.path.join(self.tmp, "Block.FCStd")
        self.call("new_document", name="Block")
        self.call("run_python", code=f"b = doc.addObject('PartDesign::Body','Body')\n"
                                     f"x = b.newObject('PartDesign::AdditiveBox','Box')\ndoc.recompute()\n"
                                     f"doc.saveAs({part!r})")
        self.call("new_document", name="Asm")
        self.assertIn("save Asm first", self.call("add_component", ok=False, path=part))
        self.call("save", path=os.path.join(self.tmp, "Asm.FCStd"))
        first = self.call("add_component", path=part)
        self.assertTrue(first["grounded"])
        second = self.call("add_component", path=part, placement=[30, 0, 0])
        self.assertFalse(second["grounded"])
        joint = self.call("add_joint", type="fixed", ref1=f"{first['component']}.Face6",
                          ref2=f"{second['component']}.Face5")
        self.assertEqual(joint["type"], "Fixed")

    def test_4_vault(self):
        sys.path.insert(0, os.path.join(REPO, "addons", "BruceWorx"))
        from bruceworx import vault_setup

        root = os.path.join(self.tmp, "Vault")
        vault_setup.create_vault(vault_setup.VaultSetup(name="Test", folder=root), actor="claude@test")
        source = os.path.join(self.tmp, "to-import")
        os.makedirs(source)
        files = sorted(f for f in os.listdir(CHECKLIST) if f.endswith(".FCStd"))[:3] if os.path.isdir(CHECKLIST) else []
        for name in files:
            shutil.copyfile(os.path.join(CHECKLIST, name), os.path.join(source, name))
        if not files:
            shutil.copyfile(os.path.join(self.tmp, "Bracket.FCStd"), os.path.join(source, "Bracket.FCStd"))
        plan = self.call("vault_import", folder=source, program="BW", vault=root)
        self.assertTrue(plan["dry_run"])
        self.assertTrue(all(f["part_number"] for f in plan["files"] if f["include"]))
        done = self.call("vault_import", folder=source, program="BW", dry_run=False, vault=root)
        self.assertTrue(done["imported"], done)
        pn = done["imported"][0]["part_number"]
        status = self.call("vault_status", vault=root)
        self.assertEqual(status["me"], "claude@test")
        self.assertIn(pn, [i["part_number"] for i in status["items"]])
        nxt = self.call("vault_next_part_number", program="BW", document_type="01", vault=root)
        self.assertTrue(nxt["next"].startswith("BW-01-"))
        out = self.call("vault_checkout", part_number=pn, vault=root)
        self.assertTrue(out["status"]["writable"])
        self.assertEqual(self.call("vault_my_work", vault=root)["checked_out"][0]["part_number"], pn)
        self.assertIn("comment", self.call("vault_checkin", ok=False, part_number=pn, comment="  ", vault=root))
        self.call("vault_undo_checkout", part_number=pn, vault=root)
        self.call("vault_checkout", part_number=pn, vault=root)
        checked = self.call("vault_checkin", part_number=pn, comment="Checked in by the MCP test", vault=root)
        self.assertEqual(checked["status"]["status"], "up_to_date")
        csv_text = self.call("vault_settings_csv", vault=root)["csv"]
        self.assertIn("program", csv_text)
        sources = [os.path.join(source, f) for f in os.listdir(source)]
        self.assertTrue(all(os.path.exists(p) for p in sources))  # originals stay


if __name__ == "__main__":
    unittest.main()
