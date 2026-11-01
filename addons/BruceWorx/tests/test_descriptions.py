# SPDX-License-Identifier: LGPL-2.1-or-later
"""set_description (2 Nov 2026): a clearer description without a new version."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx.pdm import LifecycleError  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402

ME = "bruce@mac"


class DescriptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-desc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "v"), "Test", actor=ME)
        self.vault.create_item("VL-01-01001-001", "part", "VL-P01-110 Impeller")
        self.vault.create_item("VL-03-01001-001", "drawing", "VL-P01-110-DRW Impeller drawing", "VL-01-01001-001")

    def test_a_wip_item_gets_a_clearer_description_without_a_new_version(self):
        self.vault.set_description("VL-03-01001-001", "Impeller — detail drawing (VL-P01-110)")
        item = self.vault.items["VL-03-01001-001"]
        self.assertEqual(item.description, "Impeller — detail drawing (VL-P01-110)")
        self.assertEqual(item.latest.code, "01")
        self.assertEqual(self.vault.versions.get("VL-03-01001-001", []), [])
        self.assertIn("description:", item.latest.history[-1])

    def test_it_replays(self):
        self.vault.set_description("VL-03-01001-001", "Impeller — detail drawing (VL-P01-110)")
        again = FolderVault(self.vault.ref_path, actor=ME)
        self.assertEqual(again.items["VL-03-01001-001"].description, "Impeller — detail drawing (VL-P01-110)")
        self.assertEqual(again.problems, [])

    def test_spaces_are_tidied_and_empty_or_same_is_refused(self):
        self.vault.set_description("VL-03-01001-001", "  Impeller   detail  ")
        self.assertEqual(self.vault.items["VL-03-01001-001"].description, "Impeller detail")
        with self.assertRaises(ValueError):
            self.vault.set_description("VL-03-01001-001", "   ")
        with self.assertRaises(LifecycleError):
            self.vault.set_description("VL-03-01001-001", "Impeller detail")

    def test_a_released_item_waits_for_its_next_revision(self):
        self.vault.model.items["VL-01-01001-001"].latest.state = "Released"
        with self.assertRaises(LifecycleError):
            self.vault.set_description("VL-01-01001-001", "Impeller, Ti-6Al-4V")


if __name__ == "__main__":
    unittest.main()
