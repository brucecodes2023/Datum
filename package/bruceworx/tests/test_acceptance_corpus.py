# SPDX-License-Identifier: LGPL-2.1-or-later
"""Manifest and runner for the acceptance corpus. No FreeCAD build.

    python3 -m unittest package/bruceworx/tests/test_acceptance_corpus.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "package" / "bruceworx" / "acceptance_corpus.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("acceptance_corpus", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def write_manifest(directory: Path, entries: list[dict]) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


class ManifestTests(unittest.TestCase):
    def test_committed_manifest_lists_purpose_and_known_files(self):
        entries = runner.load_manifest(runner.DEFAULT_MANIFEST)
        by_id = {entry["id"]: entry for entry in entries}
        self.assertIn("upstream-as1-ac-214", by_id)
        self.assertEqual(by_id["upstream-as1-ac-214"]["kind"], "step")
        self.assertTrue(by_id["upstream-as1-ac-214"]["purpose"])
        self.assertTrue((REPO / by_id["upstream-as1-ac-214"]["path"]).is_file())
        self.assertTrue((REPO / by_id["upstream-pad-native"]["path"]).is_file())
        self.assertEqual(by_id["le-01-01001-step"]["kind"], "step")

    def test_rejects_path_that_leaves_the_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_manifest(
                Path(directory),
                [{
                    "id": "escape",
                    "path": "../secret.stp",
                    "kind": "step",
                    "purpose": "should not be read",
                }],
            )
            with self.assertRaises(ValueError):
                runner.load_manifest(path)


class RunnerTests(unittest.TestCase):
    def test_real_tree_reports_missing_without_failing(self):
        entries = runner.load_manifest(runner.DEFAULT_MANIFEST)
        report = runner.run_checks(entries, REPO)
        by_id = {row["id"]: row for row in report["results"]}
        self.assertEqual(by_id["upstream-as1-ac-214"]["status"], "ok")
        self.assertEqual(by_id["upstream-geometry-out"]["status"], "ok")
        self.assertEqual(by_id["upstream-pad-native"]["status"], "ok")
        self.assertEqual(by_id["le-01-01001-native"]["status"], "missing")
        self.assertEqual(by_id["le-01-01001-step"]["status"], "missing")
        self.assertEqual(report["counts"]["failed"], 0)
        self.assertEqual(report["exit_code"], 0)

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [{
                "id": "gone",
                "path": "no-such-part.stp",
                "kind": "step",
                "purpose": "absent on purpose",
            }]
            report = runner.run_checks(entries, root)
        self.assertEqual(report["results"][0]["status"], "missing")
        self.assertEqual(report["exit_code"], 0)

    def test_empty_step_fails_and_nonempty_step_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.stp").write_bytes(b"")
            (root / "solid.stp").write_text("ISO-10303-21;\n", encoding="utf-8")
            (root / "plate.FCStd").write_bytes(b"")
            entries = [
                {"id": "empty", "path": "empty.stp", "kind": "step", "purpose": "empty step"},
                {"id": "solid", "path": "solid.stp", "kind": "step", "purpose": "tiny step"},
                {"id": "plate", "path": "plate.FCStd", "kind": "native", "purpose": "presence only"},
            ]
            report = runner.run_checks(entries, root)
        by_id = {row["id"]: row for row in report["results"]}
        self.assertEqual(by_id["empty"]["status"], "failed")
        self.assertEqual(by_id["empty"]["detail"], "STEP is empty")
        self.assertEqual(by_id["solid"]["status"], "ok")
        self.assertEqual(by_id["plate"]["status"], "ok")
        self.assertEqual(report["exit_code"], 1)

    def test_directory_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "not-a-step.stp").mkdir()
            entries = [{
                "id": "dir",
                "path": "not-a-step.stp",
                "kind": "step",
                "purpose": "a directory where a file should be",
            }]
            report = runner.run_checks(entries, root)
        self.assertEqual(report["results"][0]["status"], "failed")
        self.assertEqual(report["results"][0]["detail"], "not a file")
        self.assertEqual(report["exit_code"], 1)

    def test_command_prints_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_manifest(
                root,
                [{
                    "id": "gone",
                    "path": "missing.stp",
                    "kind": "step",
                    "purpose": "reported, not a crash",
                }],
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = runner.main(["--manifest", str(manifest), "--root", str(root)])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("missing.stp  (not in the tree)", text)
        self.assertIn("acceptance: 0 ok, 1 missing, 0 failed", text)


if __name__ == "__main__":
    unittest.main()
