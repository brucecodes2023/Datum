# SPDX-License-Identifier: LGPL-2.1-or-later
"""HEU_* wins. BW_* is the fallback."""
import os
import unittest

from heuretes_freecad.env import getenv


class EnvTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("HEU_SMOKE_DIR", "BW_SMOKE_DIR")}
        os.environ.pop("HEU_SMOKE_DIR", None)
        os.environ.pop("BW_SMOKE_DIR", None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_bw_fallback_then_heu(self):
        os.environ["BW_SMOKE_DIR"] = "old"
        self.assertEqual(getenv("SMOKE_DIR"), "old")
        os.environ["HEU_SMOKE_DIR"] = "new"
        self.assertEqual(getenv("SMOKE_DIR"), "new")
        self.assertEqual(getenv("MISSING", "d"), "d")
