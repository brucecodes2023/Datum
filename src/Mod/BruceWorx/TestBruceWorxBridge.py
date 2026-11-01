# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the Claude Code bridge (bruceworx_bridge, bruceworx_bridge_tools).

The pure parts (framing, token, consent, arguments, sketch constraints) and the
modelling tools on the main thread, headless. Run: FreeCADCmd -t TestBruceWorxBridge
"""

import json
import os
import socket
import stat
import tempfile
import threading
import unittest

import FreeCAD

import bruceworx_bridge as bb
import bruceworx_bridge_tools as tools


class TestFraming(unittest.TestCase):
    def test_lines_split_across_reads(self):
        buf = bb.LineBuffer()
        self.assertEqual(buf.feed(b'{"a":1}\n{"b"'), ['{"a":1}'])
        self.assertEqual(buf.feed(b':2}\n\n'), ['{"b":2}'])
        self.assertEqual(buf.feed(b""), [])

    def test_too_long_line_is_refused(self):
        buf = bb.LineBuffer(limit=10)
        with self.assertRaises(ValueError):
            buf.feed(b"x" * 20)
        self.assertEqual(buf.feed(b"ok\n"), ["ok"])

    def test_encode_is_one_line(self):
        line = bb.encode(bb.response(3, True, {"text": "a\nb"}))
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(json.loads(line)["result"]["text"], "a\nb")


class TestRequests(unittest.TestCase):
    def test_good_request(self):
        req, err = bb.parse_request('{"id":1,"token":"t","tool":"selection"}', "t")
        self.assertIsNone(err)
        self.assertEqual(req, {"id": 1, "tool": "selection", "args": {}})

    def test_bad_token(self):
        req, err = bb.parse_request('{"id":2,"token":"x","tool":"selection"}', "t")
        self.assertIsNone(req)
        self.assertEqual(err, {"id": 2, "ok": False, "error": "bad token"})
        req, err = bb.parse_request('{"id":2,"tool":"selection"}', "t")
        self.assertEqual(err["error"], "bad token")

    def test_malformed(self):
        self.assertIn("not JSON", bb.parse_request("{", "t")[1]["error"])
        self.assertIn("object", bb.parse_request("[1]", "t")[1]["error"])
        self.assertIn("no tool", bb.parse_request('{"id":1,"token":"t"}', "t")[1]["error"])
        self.assertIn("args", bb.parse_request('{"id":1,"token":"t","tool":"x","args":[]}', "t")[1]["error"])


class TestConsent(unittest.TestCase):
    def test_first_call_prompts_then_waits(self):
        c = bb.Consent()
        self.assertEqual(c.decide(), "prompt")
        self.assertEqual(c.decide(), "wait")
        c.answer(True)
        self.assertEqual(c.decide(), "run")
        self.assertTrue(c.allowed)

    def test_deny_refuses(self):
        c = bb.Consent()
        c.decide()
        c.answer(False)
        self.assertEqual(c.decide(), "refuse")
        self.assertEqual(c.decide(), "refuse")

    def test_revoke_asks_again(self):
        c = bb.Consent(bb.Consent.ALLOWED)
        c.revoke()
        self.assertEqual(c.decide(), "prompt")

    def test_timeout_asks_again(self):
        c = bb.Consent()
        c.decide()
        c.timed_out()
        self.assertEqual(c.decide(), "prompt")

    def test_remembered_allow_starts_allowed(self):
        self.assertEqual(bb.initial_consent(True).decide(), "run")
        c = bb.initial_consent(False)
        self.assertEqual(c.decide(), "prompt")
        r = bb.initial_consent(True)
        r.revoke()  # the chip: asks again
        self.assertEqual(r.decide(), "prompt")

    def test_autoallow_only_headless(self):
        old = os.environ.get("BW_BRIDGE_AUTOALLOW")
        os.environ["BW_BRIDGE_AUTOALLOW"] = "1"
        try:
            self.assertTrue(bb.env_autoallow(False))
            self.assertFalse(bb.env_autoallow(True))
        finally:
            if old is None:
                os.environ.pop("BW_BRIDGE_AUTOALLOW")
            else:
                os.environ["BW_BRIDGE_AUTOALLOW"] = old


class TestArguments(unittest.TestCase):
    def test_defaults_filled(self):
        args = bb.validate("pad", {"sketch": "Sketch"})
        self.assertEqual(args["length"], 10.0)
        self.assertFalse(args["reversed"])
        self.assertIsNone(args["doc"])

    def test_required_and_unknown(self):
        with self.assertRaisesRegex(bb.ArgError, "sketch is required"):
            bb.validate("pad", {})
        with self.assertRaisesRegex(bb.ArgError, "unknown argument"):
            bb.validate("pad", {"sketch": "S", "lenght": 3})
        with self.assertRaisesRegex(bb.ArgError, "unknown tool"):
            bb.validate("format_disk", {})

    def test_types(self):
        with self.assertRaisesRegex(bb.ArgError, "must be a number"):
            bb.validate("pad", {"sketch": "S", "length": "10"})
        with self.assertRaisesRegex(bb.ArgError, "must be a number"):
            bb.validate("pad", {"sketch": "S", "length": True})
        self.assertEqual(bb.validate("rectangular_pattern",
                                     {"object": "P", "count": 3.0, "spacing": 5})["count"], 3)
        with self.assertRaisesRegex(bb.ArgError, "must be an integer"):
            bb.validate("rectangular_pattern", {"object": "P", "count": 2.5, "spacing": 5})
        self.assertEqual(bb.validate("set_property", {"object": "a", "property": "b", "value": [1]})["value"], [1])

    def test_checkin_needs_comment_argument(self):
        with self.assertRaisesRegex(bb.ArgError, "comment is required"):
            bb.validate("vault_checkin", {"part_number": "LE-01-01001-001"})

    def test_every_tool_has_an_implementation(self):
        for name in bb.SPECS:
            self.assertTrue(callable(getattr(tools, "t_" + name, None)), name)

    def test_log_line(self):
        line = bb.log_line("pad", {"sketch": "S" * 500}, False, 42, "ToolError: boom\nmore")
        self.assertIn(" pad ", line)
        self.assertIn("error: ToolError: boom", line)
        self.assertNotIn("more", line)
        self.assertLess(len(line), 400)


class TestState(unittest.TestCase):
    def test_state_file_is_private_and_removed_only_by_its_owner(self):
        folder = tempfile.mkdtemp()
        path = bb.write_state(folder, {"port": 1, "token": "mine"})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        bb.remove_state(folder, "someone else")
        self.assertTrue(os.path.exists(path))
        bb.remove_state(folder, "mine")
        self.assertFalse(os.path.exists(path))


class TestAutoConstraints(unittest.TestCase):
    def test_rectangle_closes_and_squares(self):
        pts = [((0, 0), (10, 0)), ((10, 0), (10, 5)), ((10, 5), (0, 5)), ((0, 5), (0, 0))]
        out = tools.auto_constraints(pts, ["line"] * 4)
        coincident = [c for c in out if c[0] == "Coincident"]
        self.assertEqual(len(coincident), 4)
        self.assertIn(("Coincident", 0, 1, 3, 2), coincident)
        self.assertEqual(sorted(c[0] for c in out if c[0] != "Coincident"),
                         ["Horizontal", "Horizontal", "Vertical", "Vertical"])

    def test_circle_and_slanted_line_get_nothing(self):
        out = tools.auto_constraints([None, ((0, 0), (3, 4))], ["circle", "line"])
        self.assertEqual(out, [])


class TestModelling(unittest.TestCase):
    """The tool layer, headless, on the main thread (as the bridge runs it)."""

    def setUp(self):
        self.doc = tools.t_new_document({"name": "BridgeTest", "label": None})["doc"]

    def tearDown(self):
        FreeCAD.closeDocument(self.doc)

    def run_tool(self, tool, **args):
        return tools.call(tool, bb.validate(tool, args), headless=True)

    def test_box_by_sketch_and_pad(self):
        body = self.run_tool("new_body", doc=self.doc)["body"]
        sk = self.run_tool("create_sketch", doc=self.doc, body=body,
                           geometry=[{"rectangle": [0, 0, 20, 10]}, {"circle": [10, 5, 2]}])
        self.assertEqual(sk["closed_wires"], 2)
        self.assertEqual(sk["open_wires"], 0)
        pad = self.run_tool("pad", doc=self.doc, sketch=sk["sketch"], length=5)
        self.assertAlmostEqual(pad["body_volume_mm3"], 20 * 10 * 5 - 3.141592653589793 * 4 * 5, places=3)
        m = self.run_tool("measure", doc=self.doc, object=body)
        self.assertAlmostEqual(m["bbox"]["size"][2], 5, places=6)
        tree = self.run_tool("object_tree", doc=self.doc)["tree"]
        self.assertEqual(tree[0]["name"], body)
        self.assertIn(pad["feature"], [c["name"] for c in tree[0]["children"]])
        # one undo step per call
        undone = self.run_tool("undo", doc=self.doc)
        self.assertEqual(undone["undone"], ["Claude: pad"])
        self.assertIsNone(FreeCAD.getDocument(self.doc).getObject(pad["feature"]))

    def test_failed_call_is_rolled_back(self):
        body = self.run_tool("new_body", doc=self.doc)["body"]
        count = len(FreeCAD.getDocument(self.doc).Objects)
        with self.assertRaises(Exception):
            self.run_tool("create_sketch", doc=self.doc, body=body, geometry=[{"line": [0, 0, 1]}])
        with self.assertRaises(Exception):
            self.run_tool("run_python", code="App.ActiveDocument.addObject('Part::Box','B')\nraise RuntimeError('x')")
        self.assertEqual(len(FreeCAD.getDocument(self.doc).Objects), count)

    def test_run_python_output_and_result(self):
        out = self.run_tool("run_python", code="print('hi')\nresult = 6 * 7", reset=True)
        self.assertEqual(out["stdout"], "hi\n")
        self.assertEqual(out["result"], "42")
        with self.assertRaisesRegex(Exception, "ZeroDivisionError"):
            self.run_tool("run_python", code="1/0")

    def test_export_step(self):
        self.run_tool("run_python", code="b = doc.addObject('Part::Box','Box')\ndoc.recompute()")
        path = os.path.join(tempfile.mkdtemp(), "box.step")
        out = self.run_tool("export_step", doc=self.doc, path=path)
        self.assertGreater(out["bytes"], 1000)
        self.assertEqual(out["objects"], ["Box"])

    def test_screenshot_needs_window(self):
        import FreeCAD

        if FreeCAD.GuiUp:
            self.skipTest("a GUI FreeCAD has the window: the screenshot works")
        with self.assertRaisesRegex(Exception, "needs the FreeCAD window"):
            self.run_tool("screenshot")


class TestHeadlessServer(unittest.TestCase):
    """serve_headless in a thread: hello, token and refusal without BW_BRIDGE_AUTOALLOW."""

    def test_roundtrip(self):
        folder = tempfile.mkdtemp()
        done = []
        os.environ.pop("BW_BRIDGE_AUTOALLOW", None)
        thread = threading.Thread(target=bb.serve_headless, args=(folder, 0, lambda: bool(done)), daemon=True)
        thread.start()
        state = os.path.join(folder, bb.STATE_FILE)
        for _ in range(100):
            if os.path.exists(state):
                break
            threading.Event().wait(0.05)
        with open(state, encoding="utf-8") as handle:
            info = json.load(handle)
        conn = socket.create_connection(("127.0.0.1", info["port"]), timeout=10)
        reader = conn.makefile("rb")

        def ask(message):
            conn.sendall((json.dumps(message) + "\n").encode())
            return json.loads(reader.readline())

        hello = ask({"id": 1, "token": info["token"], "tool": "_hello"})
        self.assertTrue(hello["ok"])
        self.assertTrue(hello["result"]["headless"])
        self.assertEqual(ask({"id": 2, "token": "wrong", "tool": "list_documents"})["error"], "bad token")
        refused = ask({"id": 3, "token": info["token"], "tool": "list_documents"})
        self.assertFalse(refused["ok"])
        self.assertIn("BW_BRIDGE_AUTOALLOW", refused["error"])
        conn.close()
        done.append(True)
        thread.join(5)
        self.assertFalse(os.path.exists(state))
        with open(os.path.join(folder, bb.LOG_FILE), encoding="utf-8") as handle:
            self.assertIn("list_documents", handle.read())


class DialogTests(unittest.TestCase):
    def test_pick_by_title_or_first(self):
        found = [{"title": "Reload document"}, {"title": "Save changes?"}]
        self.assertEqual(tools.pick_dialog(found), 0)
        self.assertEqual(tools.pick_dialog(found, "save"), 1)
        self.assertIsNone(tools.pick_dialog(found, "export"))
        self.assertIsNone(tools.pick_dialog([]))

    def test_untitled_mac_box_matches_its_text(self):
        found = [{"title": "Reload document", "text": ""}, {"title": "", "text": "Save changes to Plate?"}]
        self.assertEqual(tools.pick_dialog(found, "plate"), 1)
        self.assertEqual(tools.pick_dialog([{"title": "Plate", "text": ""}] + found, "plate"), 0)

    def test_dialog_tools_are_specified(self):
        self.assertIn("dialogs", bb.SPECS)
        self.assertEqual(bb.kind_of("answer_dialog"), "ui")
        self.assertEqual(set(bb.DIALOG_TOOLS), {"dialogs", "answer_dialog"})


class HeldByOtherTests(unittest.TestCase):
    def test_another_live_window_keeps_it(self):
        self.assertTrue(bb.held_by_other({"pid": 10, "headless": False}, 20, alive=lambda p: True))

    def test_gone_or_own_or_headless(self):
        self.assertFalse(bb.held_by_other({"pid": 10, "headless": False}, 20, alive=lambda p: False))
        self.assertFalse(bb.held_by_other({"pid": 20, "headless": False}, 20, alive=lambda p: True))
        self.assertFalse(bb.held_by_other({"pid": 10, "headless": True}, 20, alive=lambda p: True))
        self.assertFalse(bb.held_by_other(None, 20))


if __name__ == "__main__":
    unittest.main()
