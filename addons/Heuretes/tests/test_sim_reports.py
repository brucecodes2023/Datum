# SPDX-License-Identifier: LGPL-2.1-or-later
"""A vault item's simulation study reports as release attachments (bruceworx.sim_reports; SIM-01 / PDM-05,
30 Oct 2026): found beside the work copy, newest first, and attached by ``vault approve --attach-reports``."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_pdm_folder  # noqa: E402
from heuretes_freecad import pdm_workspace, sim_reports  # noqa: E402
from heuretes_freecad.pdm_folder import FolderVault  # noqa: E402

PART = test_pdm_folder.PART


def touch(path, data=b"%PDF-1.5", mtime=None):
    with open(path, "wb") as handle:
        handle.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestReportsBeside(test_pdm_folder._TempDir, unittest.TestCase):
    def test_reports_of_a_document_newest_first(self):
        doc = touch(os.path.join(self.tmp, "BW-01-01001-001.FCStd"), b"doc")
        now = time.time()
        old = touch(os.path.join(self.tmp, "BW-01-01001-001_Static_report.pdf"), mtime=now - 100)
        new = touch(os.path.join(self.tmp, "BW-01-01001-001_Modal_report.pdf"), mtime=now)
        touch(os.path.join(self.tmp, "BW-01-01001-0010_Static_report.pdf"))  # another item's (no "_" after the stem)
        touch(os.path.join(self.tmp, "BW-01-01001-001_report.pdf"))  # no study name
        touch(os.path.join(self.tmp, "BW-01-01001-001_Static_report.tex"))
        os.makedirs(os.path.join(self.tmp, "BW-01-01001-001_Static_report"))  # the LaTeX folder
        self.assertEqual(sim_reports.reports_beside(doc), [new, old])
        self.assertIn("BW-01-01001-001_Modal_report.pdf (", sim_reports.label(new))
        self.assertEqual(sim_reports.reports_beside(os.path.join(self.tmp, "nothing", "x.FCStd")), [])


class TestAttachReports(test_pdm_folder._TempDir, unittest.TestCase):
    # the CLI helpers of test_pdm_folder.CliTests, without running its tests again
    DWG = test_pdm_folder.CliTests.DWG
    _run = test_pdm_folder.CliTests._run
    _add_drawing = test_pdm_folder.CliTests._add_drawing
    _in_review = test_pdm_folder.CliTests._in_review
    _journal = test_pdm_folder.CliTests._journal

    def test_approve_attaches_the_study_reports(self):
        self._in_review()
        vault = FolderVault(self.vault_dir, actor="bruce@mac")
        ws = pdm_workspace.Workspace(vault)
        os.makedirs(os.path.dirname(ws.path(PART)), exist_ok=True)
        report = touch(os.path.join(os.path.dirname(ws.path(PART)), PART + "_Bracket_report.pdf"))
        self.assertEqual(sim_reports.item_reports(ws, PART), [report])
        self.assertEqual(sim_reports.offered(ws, [PART, PART]), [report])
        code, result = self._run("approve", "--pn", PART, "--note", "First release", "--reviewed", "--attach-reports")
        self.assertEqual((code, result["revision"]), (0, "A"))
        self.assertEqual(result["attached_reports"], [os.path.basename(report)])
        args = self._journal()[-1]["args"]
        self.assertEqual([a["filename"] for a in args["attachments"]], [os.path.basename(report)])


if __name__ == "__main__":
    unittest.main()
