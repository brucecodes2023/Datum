# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fitting size tables and layouts (BW-ROU-05): the thread data against ASME B1.20.1 and ISO 228-1,
the families, styles, sizes and threads offered, each layout's ports, and the words."""

import math
import unittest

from bruceworx import fittings as ft

INCH = 25.4


def close(a, b, places=6):
    return all(abs(x - y) < 10 ** -places for x, y in zip(a, b))


class ThreadTests(unittest.TestCase):
    def test_npt_from_asme_b1_20_1(self):
        # Pipe OD at the thread (D), threads per inch, effective thread length L2.
        for name, d, tpi, l2 in (("1/8 NPT", 0.405, 27, 0.2639), ("1/4 NPT", 0.540, 18, 0.4018),
                                 ("3/8 NPT", 0.675, 18, 0.4078), ("1/2 NPT", 0.840, 14, 0.5337)):
            t = ft.thread(name)
            self.assertAlmostEqual(t.major, d * INCH, places=6)
            self.assertEqual(t.tpi, tpi)
            self.assertAlmostEqual(t.length, l2 * INCH, places=6)
            self.assertTrue(t.tapered)
            self.assertLess(t.tap, t.major)
            # Taper 1 in 16 on the diameter.
            self.assertAlmostEqual(t.major - ft.npt_taper_diameter(t, 16.0), 1.0, places=9)

    def test_bspp_from_iso_228_1(self):
        for name, d, tpi in (("1/8 BSPP", 9.728, 28), ("1/4 BSPP", 13.157, 19), ("3/8 BSPP", 16.662, 19),
                             ("1/2 BSPP", 20.955, 14)):
            t = ft.thread(name)
            self.assertAlmostEqual(t.major, d)
            self.assertEqual(t.tpi, tpi)
            self.assertFalse(t.tapered)
            self.assertAlmostEqual(ft.npt_taper_diameter(t, 10.0), d)
        self.assertAlmostEqual(ft.thread("M5").pitch, 0.8)
        with self.assertRaises(ValueError):
            ft.thread("3/4 NPT")


class TableTests(unittest.TestCase):
    def test_families_styles_sizes(self):
        self.assertEqual(ft.FAMILIES, ("Push-to-connect", "Hose barb", "Threaded", "Compression"))
        self.assertEqual(ft.styles(ft.PTC), ["Straight", "Elbow", "Tee", "Bulkhead"])
        self.assertEqual(ft.styles(ft.THREADED), ["Elbow", "Tee", "Adapter", "Plug"])
        self.assertEqual(ft.styles(ft.COMPRESSION), ["Straight union", "Elbow"])
        self.assertEqual(ft.sizes(ft.PTC), ["4 mm", "6 mm", "8 mm", "10 mm", "12 mm", "5/32 in", "1/4 in", "3/8 in"])
        self.assertEqual(ft.sizes(ft.BARB)[:6], ["1/8 in", "3/16 in", "1/4 in", "5/16 in", "3/8 in", "1/2 in"])
        self.assertEqual(ft.sizes(ft.BARB)[6:], ["3 mm", "4 mm", "5 mm", "6 mm", "8 mm", "10 mm", "12 mm"])
        self.assertEqual(ft.sizes(ft.COMPRESSION), ["1/8 in", "1/4 in", "3/8 in", "1/2 in", "4 mm", "6 mm", "8 mm",
                                                     "10 mm", "12 mm"])
        self.assertEqual(len(ft.sizes(ft.THREADED)), 8)  # 1/8..1/2 NPT and BSPP
        with self.assertRaises(ValueError):
            ft.sizes("Flare")

    def test_thread_choices(self):
        self.assertEqual(ft.threads(ft.PTC, "Straight", "4 mm")[0], "M5")
        self.assertNotIn("M5", ft.threads(ft.PTC, "Elbow", "10 mm"))
        self.assertIn("1/4 NPT", ft.threads(ft.PTC, "Straight", "6 mm"))
        self.assertEqual(ft.threads(ft.PTC, "Tee", "6 mm"), [])
        self.assertEqual(ft.threads(ft.BARB, "Straight", "6 mm")[0], ft.NO_THREAD)
        self.assertEqual(ft.default_thread(ft.PTC, "Straight", "6 mm"), "1/8 BSPP")
        self.assertEqual(ft.default_thread(ft.BARB, "Elbow", "6 mm"), ft.NO_THREAD)

    def test_sizes_are_sensible(self):
        for s in ft.PTC_SIZES.values():
            self.assertGreater(s.body, s.od + 4.0)  # room for the collet and the release ring
            self.assertGreater(s.arm, s.body / 2.0)
        for b in ft.BARB_SIZES.values():
            self.assertGreater(b.crest, b.hose_id)
            self.assertLess(b.bore, b.hose_id)
            self.assertGreaterEqual(b.length, 8.0)
        for c in ft.COMPRESSION_SIZES.values():
            self.assertGreater(c.nut, c.od * 1.5)
            self.assertGreater(c.insertion, c.od * 0.5)
        self.assertAlmostEqual(ft.COMPRESSION_SIZES["1/4 in"].nut, 9 / 16 * INCH)


class LayoutTests(unittest.TestCase):
    def test_every_fitting_lays_out(self):
        count = 0
        for family, style, size, thread in ft.all_fittings():
            for choice in [thread] + ft.threads(family, style, size):
                lay = ft.layout(family, style, size, choice or None)
                count += 1
                self.assertTrue(lay.solids)
                self.assertTrue(lay.ports)
                names = [p.name for p in lay.ports]
                self.assertEqual(names, sorted(names))
                self.assertEqual(len(set(names)), len(names))
                for p in lay.ports:
                    self.assertAlmostEqual(math.sqrt(sum(c * c for c in p.direction)), 1.0)
                    self.assertGreater(p.tube_od, 0.0)
                    self.assertGreaterEqual(p.engagement, 0.0)
                expected = 3 if style == "Tee" else (1 if style == "Plug" else 2)
                self.assertEqual(len(lay.ports), expected, (family, style, size, choice))
        self.assertGreater(count, 200)

    def test_ptc_elbow_ports(self):
        lay = ft.layout(ft.PTC, "Elbow", "6 mm", "1/8 BSPP")
        tube, thread = lay.port("1"), lay.port("2")
        self.assertEqual((tube.type, tube.size, tube.tube_od), ("Push-to-connect", "6 mm", 6.0))
        self.assertEqual(tube.direction, (1.0, 0.0, 0.0))
        self.assertEqual(thread.type, "BSPP")
        self.assertEqual(thread.direction, (0.0, 0.0, -1.0))
        self.assertTrue(close(thread.origin, (0.0, 0.0, -8.0)))
        self.assertAlmostEqual(tube.engagement, 17.0)  # OD + 11 mm insertion
        self.assertIs(lay.mount, thread)
        # The elbow's tube axis is above the hex, its mouth beyond the body.
        self.assertGreater(tube.origin[2], 0.0)
        self.assertGreater(tube.origin[0], ft.PTC_SIZES["6 mm"].body / 2.0)

    def test_barb_and_threaded_ports(self):
        barb = ft.layout(ft.BARB, "Straight", "1/4 in", "1/4 NPT")
        self.assertEqual([p.type for p in barb.ports], ["Barb", "NPT"])
        self.assertAlmostEqual(barb.port("1").engagement, ft.BARB_SIZES["1/4 in"].length)
        both = ft.layout(ft.BARB, "Straight", "6 mm")
        self.assertEqual([p.type for p in both.ports], ["Barb", "Barb"])
        self.assertEqual(both.mount.name, "1")  # no thread: placed by port 1
        tee = ft.layout(ft.THREADED, "Tee", "3/8 NPT")
        self.assertEqual([p.direction for p in tee.ports], [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)])
        self.assertTrue(all(p.type == "NPT" and p.size == "3/8 NPT" for p in tee.ports))
        self.assertTrue(close(tee.port("2").origin, (ft.THREADED_CENTRE["3/8"], 0.0, 0.0)))
        plug = ft.layout(ft.THREADED, "Plug", "1/4 BSPP")
        self.assertEqual(len(plug.ports), 1)
        union = ft.layout(ft.COMPRESSION, "Straight union", "8 mm")
        self.assertEqual([p.direction for p in union.ports], [(0.0, 0.0, -1.0), (0.0, 0.0, 1.0)])
        self.assertAlmostEqual(union.port("1").origin[2], -union.port("2").origin[2])

    def test_bad_choices_are_refused(self):
        with self.assertRaises(ValueError):
            ft.layout(ft.PTC, "Plug", "6 mm")
        with self.assertRaises(ValueError):
            ft.layout(ft.PTC, "Straight", "7 mm")
        with self.assertRaises(ValueError):
            ft.layout(ft.PTC, "Straight", "10 mm", "M5")

    def test_words(self):
        self.assertEqual(ft.describe(ft.PTC, "Elbow", "6 mm", "1/8 BSPP"),
                         "Push-to-connect male elbow, 6 mm tube x 1/8 BSPP")
        self.assertEqual(ft.describe(ft.BARB, "Straight", "6 mm", ft.NO_THREAD), "Hose barb straight, 6 mm hose ID both ends")
        self.assertEqual(ft.describe(ft.THREADED, "Plug", "1/4 NPT"), "Pipe fitting, hex head plug, 1/4 NPT")
        self.assertEqual(ft.describe(ft.COMPRESSION, "Elbow", "1/4 in"), "Compression union elbow, 1/4 in tube")
        self.assertEqual(ft.type_code(ft.PTC, "Elbow", "6 mm", "1/8 BSPP"), "PTC-ELBOW-6MM-1/8BSPP")
        self.assertEqual(ft.type_code(ft.COMPRESSION, "Straight union", "1/2 in"), "CMP-STRAIGHTUNION-1/2IN")


if __name__ == "__main__":
    unittest.main()
