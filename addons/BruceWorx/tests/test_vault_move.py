# SPDX-License-Identifier: LGPL-2.1-or-later
"""Moving a vault to another place (BW-PDM-04, 23 Oct)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_pdm_workspace import ME, PART, make_fcstd  # noqa: E402

from bruceworx import pdm_workspace as ws  # noqa: E402
from bruceworx import vault_move as vm  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402

PLENTY = lambda _path: 10**12  # noqa: E731


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-move-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "here", "Acme"), "Acme", actor=ME)
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        self.vault.create_item(PART, "part", "plate")
        self.vault.checkout(PART)
        self.vault.checkin(PART, make_fcstd(os.path.join(src, "p.FCStd")), comment="first")
        self.work = ws.Workspace(self.vault)
        self.work.checkout(PART)  # a writable work file and a lock come along
        self.ssd = os.path.join(self.tmp, "SSD")
        os.makedirs(self.ssd)

    def test_move_copies_verifies_and_keeps_the_old_vault(self):
        before = sorted(os.listdir(self.vault.root))
        plan = vm.plan_move(self.vault.root, self.ssd, PLENTY)
        self.assertTrue(plan.ok, plan.issues)
        seen = []
        new_root = vm.move_vault(plan, progress=lambda done, total, rel: seen.append(rel))
        self.assertEqual(new_root, os.path.join(self.ssd, "Acme"))
        self.assertTrue(seen)
        moved = FolderVault(vm.ref_in(new_root), actor=ME)
        self.assertEqual(moved.locks, {PART: ME})
        self.assertEqual([v.sha256 for v in moved.versions[PART]], [v.sha256 for v in self.vault.versions[PART]])
        self.assertEqual(ws.Workspace(moved).status(PART).code, ws.CHECKED_OUT)
        self.assertEqual(sorted(os.listdir(self.vault.root)), before)
        self.assertFalse(os.path.exists(new_root + vm.MOVING_SUFFIX))

    def test_refusals(self):
        self.assertFalse(vm.plan_move(self.vault.root, os.path.dirname(self.vault.root), PLENTY).ok)
        self.assertFalse(vm.plan_move(self.vault.root, os.path.join(self.vault.root, "work"), PLENTY).ok)
        self.assertFalse(vm.plan_move(self.vault.root, os.path.join(self.tmp, "nowhere"), PLENTY).ok)
        self.assertFalse(vm.plan_move(self.vault.root, self.ssd, lambda _p: 10).ok)
        os.makedirs(os.path.join(self.ssd, "Acme", "something"))
        self.assertFalse(vm.plan_move(self.vault.root, self.ssd, PLENTY).ok)

    def test_files_still_downloading_block_the_move(self):
        obj = os.path.join(self.vault.root, self.vault.latest_version(PART).object_name)
        os.remove(obj)
        plan = vm.plan_move(self.vault.root, self.ssd, PLENTY)
        self.assertFalse(plan.ok)
        self.assertTrue(any("downloading" in text for _level, text in plan.issues))

    def test_stop_leaves_nothing_behind(self):
        plan = vm.plan_move(self.vault.root, self.ssd, PLENTY)
        with self.assertRaises(InterruptedError):
            vm.move_vault(plan, progress=lambda *_a: False)
        self.assertEqual(os.listdir(self.ssd), [])


if __name__ == "__main__":
    unittest.main()
