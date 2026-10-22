# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the navigation pill. No GUI.

Run in FreeCAD (FreeCADCmd -t TestBruceWorxNavPill) or with plain python3.
"""

import math
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_navpill as nav

# Camera at -Y looking along +Y (FreeCAD front view): rotate -Z onto +Y.
FRONT = nav.qaxis((1.0, 0.0, 0.0), math.pi / 2)


def close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def conjugate(q):
    return (-q[0], -q[1], -q[2], q[3])


class TestPlacement(unittest.TestCase):
    def test_centred_at_the_bottom(self):
        self.assertEqual(nav.place(1000, 600, 260, 34), (370, 600 - 12 - 34))

    def test_centred_when_panels_leave_room(self):
        self.assertEqual(nav.place(2000, 900, 260, 34, 288, 1668), (870, 854))

    def test_centred_between_panels_when_view_centre_collides(self):
        x, _y = nav.place(1000, 600, 260, 34, 288, 620)
        self.assertGreaterEqual(x, 288 + nav.PANEL_GAP)
        self.assertLessEqual(x + 260, 620 - nav.PANEL_GAP)

    def test_stays_inside_the_view(self):
        x, y = nav.place(200, 20, 260, 34, 150, 180)
        self.assertEqual((x, y), (0, 0))

    def test_above_a_bottom_panel(self):
        self.assertEqual(nav.place(1000, 600, 260, 34, free_bottom=450)[1], 450 - 12 - 34)


class TestCamera(unittest.TestCase):
    def test_quaternion_rotates_vectors(self):
        self.assertTrue(close(nav.view_direction(FRONT), (0.0, 1.0, 0.0)))
        self.assertTrue(close(nav.qrotate(FRONT, (0.0, 1.0, 0.0)), (0.0, 0.0, 1.0)))

    def test_orbit_keeps_the_focal_point_and_distance(self):
        pos = (0.0, -100.0, 0.0)
        q, p = nav.orbit(FRONT, pos, 100.0, 37, -21)
        self.assertTrue(close(nav.focal_point(q, p, 100.0), (0.0, 0.0, 0.0), 1e-6))
        self.assertAlmostEqual(math.dist(p, (0.0, 0.0, 0.0)), 100.0, places=6)

    def test_orbit_drag_right_moves_camera_left(self):
        _q, p = nav.orbit(FRONT, (0.0, -100.0, 0.0), 100.0, 10, 0)
        self.assertLess(p[0], 0.0)
        self.assertAlmostEqual(p[2], 0.0)

    def test_orbit_drag_down_shows_the_top(self):
        q, p = nav.orbit(FRONT, (0.0, -100.0, 0.0), 100.0, 0, 10)
        self.assertGreater(p[2], 0.0)
        self.assertLess(nav.view_direction(q)[2], 0.0)

    def test_orbit_turntable_keeps_the_horizon(self):
        q, _p = nav.orbit(FRONT, (0.0, -100.0, 0.0), 100.0, 80, 0)
        self.assertAlmostEqual(nav.qrotate(q, (1.0, 0.0, 0.0))[2], 0.0)

    def test_orbit_about_a_pivot_keeps_it_in_place_on_screen(self):
        pos, pivot = (0.0, -100.0, 0.0), (30.0, 20.0, -10.0)
        q, p = nav.orbit(FRONT, pos, 100.0, 37, -21, pivot=pivot)
        self.assertAlmostEqual(math.dist(p, pivot), math.dist(pos, pivot), places=6)
        # Same place in camera space, so the pivot stays put on screen.
        before = nav.qrotate(conjugate(FRONT), nav._sub(pivot, pos))
        after = nav.qrotate(conjugate(q), nav._sub(pivot, p))
        self.assertTrue(close(before, after))

    def test_orbit_about_an_off_centre_pivot_moves_the_focal_point(self):
        pos, pivot = (0.0, -100.0, 0.0), (30.0, 20.0, -10.0)
        q, p = nav.orbit(FRONT, pos, 100.0, 40, 0, pivot=pivot)
        self.assertFalse(close(nav.focal_point(q, p, 100.0), (0.0, 0.0, 0.0), 1e-3))
        same = nav.orbit(FRONT, pos, 100.0, 40, 0)
        self.assertEqual(same, nav.orbit(FRONT, pos, 100.0, 40, 0, pivot=(0.0, 0.0, 0.0)))

    def test_orbit_center_preference(self):
        self.assertEqual(nav.orbit_center_mode(0), nav.CENTER_FOCAL)
        self.assertEqual(nav.orbit_center_mode(1), nav.CENTER_ROTATION)
        self.assertEqual(nav.orbit_center_mode(7), nav.CENTER_ROTATION)

    def test_inventor_pixel_counts_from_the_bottom_in_device_pixels(self):
        self.assertEqual(nav.inventor_pixel(10, 0, 600), (10, 599))
        self.assertEqual(nav.inventor_pixel(10, 599, 600), (10, 0))
        self.assertEqual(nav.inventor_pixel(10, 100, 600, 2.0), (20, 998))

    def test_pan_moves_the_model_with_the_pointer(self):
        p = nav.pan(FRONT, (0.0, -100.0, 0.0), 10, 0, 0.5)
        self.assertTrue(close(p, (-5.0, -100.0, 0.0)))
        p = nav.pan(FRONT, (0.0, -100.0, 0.0), 0, 10, 0.5)
        self.assertTrue(close(p, (0.0, -100.0, 5.0)))

    def test_units_per_pixel(self):
        self.assertAlmostEqual(nav.units_per_pixel(True, 200.0, 50.0, 800, 400), 0.5)
        self.assertAlmostEqual(nav.units_per_pixel(True, 200.0, 50.0, 400, 800), 0.5)
        upp = nav.units_per_pixel(False, math.pi / 2, 100.0, 1000, 500)
        self.assertAlmostEqual(upp, 2 * 100.0 / 500)

    def test_zoom(self):
        self.assertLess(nav.zoom_factor(-10), 1.0)
        self.assertGreater(nav.zoom_factor(10), 1.0)
        self.assertEqual(nav.zoom_factor(0), 1.0)
        pos, fd = nav.zoom_perspective(FRONT, (0.0, -100.0, 0.0), 100.0, 0.5)
        self.assertAlmostEqual(fd, 50.0)
        self.assertTrue(close(pos, (0.0, -50.0, 0.0)))


class TestSmallRules(unittest.TestCase):
    def test_modes_toggle(self):
        self.assertEqual(nav.next_mode(None, "orbit"), "orbit")
        self.assertEqual(nav.next_mode("orbit", "pan"), "pan")
        self.assertIsNone(nav.next_mode("pan", "pan"))

    def test_as_is_shows_the_shaded_icon(self):
        self.assertEqual(nav.style_icon_index(0, 7), 5)
        self.assertEqual(nav.style_icon_index(2, 7), 2)
        self.assertEqual(nav.style_icon_index(0, 1), 0)

    def test_camera_command_switches_away(self):
        self.assertEqual(nav.camera_command("Orthographic"), "Std_PerspectiveCamera")
        self.assertEqual(nav.camera_command("Perspective"), "Std_OrthographicCamera")

    def test_theme_names(self):
        self.assertTrue(nav.is_dark_theme("BruceWorx Dark"))
        self.assertFalse(nav.is_dark_theme("BruceWorx"))
        self.assertTrue(nav.is_bruceworx_theme("BruceWorx Dark"))
        self.assertFalse(nav.is_bruceworx_theme("FreeCAD Light"))

    def test_glyphs_are_svg_in_the_given_colour(self):
        for name in nav.GLYPHS:
            svg = nav.glyph_svg(name, "#6e6e73")
            ET.fromstring(svg)
            self.assertIn('stroke="#6e6e73"', svg)

    def test_preference_checkbox_default_on(self):
        here = os.path.dirname(os.path.abspath(__file__))
        ui = ET.parse(os.path.join(here, "preferences-bruceworx.ui"))
        for widget in ui.iter("widget"):
            if widget.get("name") == nav.PREF_NAME:
                props = {p.get("name"): p for p in widget.findall("property")}
                self.assertEqual(props["checked"].findtext("bool"), "true")
                self.assertEqual(props["prefPath"].findtext("cstring"), "BruceWorx")
                return
        self.fail("NavigationPill checkbox missing")

    def test_orbit_centre_combo_defaults_to_the_rotation_centre(self):
        here = os.path.dirname(os.path.abspath(__file__))
        ui = ET.parse(os.path.join(here, "preferences-bruceworx.ui"))
        for widget in ui.iter("widget"):
            if widget.get("name") == nav.CENTER_PREF:
                props = {p.get("name"): p for p in widget.findall("property")}
                self.assertEqual(int(props["currentIndex"].findtext("number")), nav.CENTER_ROTATION)
                self.assertEqual(props["prefPath"].findtext("cstring"), "BruceWorx")
                self.assertEqual(len(widget.findall("item")), 2)
                return
        self.fail("NavPillOrbitCenter combo missing")

    def test_stylesheet_block_present(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for qss in (
            os.path.join(here, "..", "..", "Gui", "Stylesheets", "BruceWorx.qss"),  # source tree
            os.path.join(here, "..", "..", "share", "Gui", "Stylesheets", "BruceWorx.qss"),  # build tree
        ):
            if os.path.exists(qss):
                with open(qss, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertTrue("#BruceWorxNavPill" in text, qss)
                block = text[text.index("#BruceWorxNavPill") :]
                self.assertTrue("@IconPlateColor" in block, qss)
                return
        self.skipTest("BruceWorx.qss not next to this copy of the module")


if __name__ == "__main__":
    unittest.main()
