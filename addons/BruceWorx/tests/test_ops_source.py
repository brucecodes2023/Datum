# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ops API numbering reader (BW-PDM-03, FCB-P2-03) against a local stub server.

The stub implements ``POST /api/part-numbers`` as documented in
docs/pdm-openapi.yaml and listens on 127.0.0.1 only. Nothing here reaches the
network or a real Ops deployment.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bruceworx.numbering import PN_FORMAT, assign_part_numbers  # noqa: E402
from fakes import FakeDoc, FakeObj, FakeShape  # noqa: E402
from bruceworx.sources import (  # noqa: E402
    OpsApiError,
    OpsApiNumberSource,
    open_number_source,
)

TOKEN_ENV = "BW_TEST_OPS_TOKEN"
TOKEN = "stub-token"


class StubOps:
    """The documented contract, in memory: next parent per program+type, next sub per parent."""

    def __init__(self):
        self.requests = []  # (path, headers, body)
        self.next_parent = {}
        self.next_sub = {}
        self.reply = None  # optional override: (status, body bytes)
        self.legacy_revision = ""  # reply in the old P/N-REV form when set

    def handle(self, path, headers, body):
        self.requests.append((path, dict(headers), body))
        if self.reply is not None:
            return self.reply
        if path != "/api/part-numbers":
            return 404, json.dumps({"error": "no such endpoint"}).encode()
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return 401, json.dumps({"error": "bad token"}).encode()
        request = json.loads(body)
        key = (request["program"], request["document_type"])
        if request.get("parent") is None:
            parent = self.next_parent.get(key, 1001)
            self.next_parent[key] = parent + 1
            sub = 1
            self.next_sub[key + (parent,)] = 2
        else:
            parent = int(request["parent"])
            if key + (parent,) not in self.next_sub:
                return 409, json.dumps({"error": f"unknown parent {request['parent']}"}).encode()
            sub = self.next_sub[key + (parent,)]
            self.next_sub[key + (parent,)] = sub + 1
        number = f"{key[0]}-{key[1]}-{parent:05d}-{sub:03d}"
        if self.legacy_revision:
            number += "-" + self.legacy_revision
        return 201, json.dumps({"part_number": number}).encode()


class OpsStubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stub = cls.stub_holder = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 -- http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                status, payload = stub["ops"].handle(self.path, self.headers, body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}/api"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.ops = self.stub_holder["ops"] = StubOps()
        self._token = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = TOKEN
        self.addCleanup(self._restore_token)
        self.source = OpsApiNumberSource(self.base_url, token_env=TOKEN_ENV, timeout=5)

    def _restore_token(self):
        if self._token is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = self._token

    def test_new_parent_then_next_sub(self):
        self.assertEqual(self.source.allocate("le", "1"), "LE-01-01001-001")
        self.assertEqual(self.source.allocate("LE", "01", parent="1001"), "LE-01-01001-002")
        self.assertEqual(self.source.allocate("LE", "01"), "LE-01-01002-001")
        path, headers, body = self.ops.requests[1]
        self.assertEqual(path, "/api/part-numbers")
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"program": "LE", "document_type": "01", "parent": "01001"})
        self.assertEqual(json.loads(self.ops.requests[0][2])["parent"], None)

    def test_no_token_sends_nothing(self):
        os.environ.pop(TOKEN_ENV, None)
        with self.assertRaises(OpsApiError) as caught:
            self.source.allocate("LE", "01")
        self.assertIn(TOKEN_ENV, str(caught.exception))
        self.assertEqual(self.ops.requests, [])

    def test_refusal_carries_the_status_and_error(self):
        with self.assertRaises(OpsApiError) as caught:
            self.source.allocate("LE", "01", parent="04000")
        self.assertIn("409", str(caught.exception))
        self.assertIn("unknown parent 04000", str(caught.exception))
        os.environ[TOKEN_ENV] = "wrong"
        with self.assertRaises(OpsApiError) as caught:
            self.source.allocate("LE", "01")
        self.assertIn("401", str(caught.exception))

    def test_old_reply_with_revision_migrates_to_the_pn(self):
        self.ops.legacy_revision = "A"
        self.assertEqual(self.source.allocate("LE", "01"), "LE-01-01001-001")

    def test_bad_replies_are_errors_not_numbers(self):
        for status, body in (
            (201, b"not json"),
            (201, json.dumps({"number": "LE-01-01001-001"}).encode()),
            (201, json.dumps({"part_number": "LE-1001"}).encode()),
            (201, json.dumps({"part_number": "XX-01-01001-001"}).encode()),
            (201, json.dumps({"part_number": "LE-02-01001-001"}).encode()),
        ):
            self.ops.reply = (status, body)
            with self.assertRaises(OpsApiError, msg=body):
                self.source.allocate("LE", "01")
        self.ops.reply = (201, json.dumps({"part_number": "LE-01-01009-002"}).encode())
        with self.assertRaises(OpsApiError):
            self.source.allocate("LE", "01", parent="01001")  # a sub under another drawing

    def test_revision_and_preview_are_refused_without_a_request(self):
        with self.assertRaises(ValueError):
            self.source.allocate("LE", "01", revision="A")
        with self.assertRaises(NotImplementedError):
            self.source.preview("LE", "01")
        self.assertEqual(self.ops.requests, [])

    def test_unreachable_ops_is_an_error(self):
        closed = OpsApiNumberSource("http://127.0.0.1:9/api", token_env=TOKEN_ENV, timeout=2)
        with self.assertRaises(OpsApiError) as caught:
            closed.allocate("LE", "01")
        self.assertIn("cannot reach Ops", str(caught.exception))

    def test_numbering_file_selects_ops_and_keeps_its_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "numbering.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "source": "ops", "base_url": self.base_url, "token_env": TOKEN_ENV,
                    "format": PN_FORMAT,
                    "document_types": {"01": {"name": "Part"}, "02": {"name": "Assembly", "retired": True}},
                    "programs": {"LE": {"counters": {}}, "OW": {"retired": True}},
                }, handle)
            source = open_number_source(path)
            self.assertIsInstance(source, OpsApiNumberSource)
            self.assertEqual(source.programs(), ["LE"])
            self.assertEqual(source.document_types(), ["01"])
            self.assertEqual(source.allocate("LE", "01"), "LE-01-01001-001")

    def test_assign_labels_from_ops(self):
        left = FakeObj("Left", shape=FakeShape())
        right = FakeObj("Right", shape=FakeShape())
        doc = FakeDoc("Asm", "Asm", [left, right])
        result = assign_part_numbers(doc, self.source, "LE", "01")
        self.assertEqual((left.Label, right.Label), ("LE-01-01001-001", "LE-01-01001-002"))
        self.assertEqual(doc.Label, "LE-01-01001-001")
        self.assertEqual(result["sub"], "001")


if __name__ == "__main__":
    unittest.main()
