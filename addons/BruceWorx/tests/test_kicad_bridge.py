# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad bridge: board reader, model-path resolver, kicad-cli wrappers.

Board tests run on copies of KiCad's templates in a temporary folder; the
kicad-cli tests are skipped when KiCad is not installed."""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import kicad_bridge as kb  # noqa: E402

TEMPLATE = os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega")
HAVE_TEMPLATE = os.path.isfile(os.path.join(TEMPLATE, "Arduino_Mega.kicad_pcb"))
HAVE_CLI = kb.find_cli() is not None and HAVE_TEMPLATE

SMALL_BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
 (general (thickness 1.6))
 (title_block (title "Test \\"board\\""))
 (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (setup (stackup (layer "F.Mask" (type "Top Solder Mask") (color "Black")) (copper_finish "ENIG"))
        (aux_axis_origin 0 0))
 (footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 15 25)
   (property "Reference" "H1") (property "Value" "MountingHole")
   (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2)))
 (footprint "Connector_USB:USB_C_Receptacle" (layer "B.Cu") (at 40 30 90)
   (property "Reference" "J1") (property "Value" "USB_C") (property "MPN" "TYPE-C-31-M-12")
   (attr smd)
   (pad "" np_thru_hole circle (at 2 0) (size 0.65 0.65) (drill 0.65))
   (pad "" np_thru_hole circle (at 0 5) (size 2.5 2.5) (drill 2.5))
   (model "${KICAD9_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C.wrl"))
 (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu") (at 20 30)
   (property "Reference" "R1") (property "Value" "10k") (attr smd dnp)
   (model "${KIPRJMOD}/3d/local.step") (model "models/rel.step") (model "${NOT_SET}/x.step"))
 (gr_line (start 10 20) (end 50 20) (layer "Edge.Cuts"))
 (gr_line (start 50 20) (end 50 40) (layer "Edge.Cuts"))
 (gr_arc (start 50 40) (mid 47.071 47.071) (end 40 50) (layer "Edge.Cuts"))
 (gr_line (start 40 50) (end 10 50) (layer "Edge.Cuts"))
 (gr_line (start 10 50) (end 10 20) (layer "Edge.Cuts"))
)
"""


class SexprTests(unittest.TestCase):
    def test_parse_nested_and_escapes(self):
        root = kb.parse_sexpr('(a (b "x \\"y\\" z") (c 1 2) d)')
        self.assertEqual(root[0], "a")
        self.assertEqual(kb.value(root, "b"), 'x "y" z')
        self.assertEqual(kb.find(root, "c"), ["c", "1", "2"])
        self.assertEqual(root[-1], "d")

    def test_unbalanced(self):
        with self.assertRaises(kb.KiCadError):
            kb.parse_sexpr("(a (b)")


class BoardInfoTests(unittest.TestCase):
    def test_small_board(self):
        info = kb.board_info(SMALL_BOARD)
        self.assertEqual(info.title, 'Test "board"')
        self.assertEqual(info.copper_layers, 4)
        self.assertEqual(info.copper_finish, "ENIG")
        self.assertEqual(info.mask_color, "Black")
        # The arc bulges to (50 - 10 + 10*cos45 ...): its far point is the arc's extreme.
        xmin, ymin, xmax, ymax = info.bbox
        self.assertEqual((xmin, ymin, xmax), (10, 20, 50))
        self.assertAlmostEqual(ymax, 50, places=2)
        self.assertAlmostEqual(info.width, 40)
        # aux origin 0,0 counts as unset: origin is the outline's bottom-left corner.
        self.assertEqual(info.origin, (10, ymax))
        refs = {fp.ref: fp for fp in info.footprints}
        self.assertEqual(refs["J1"].side, "bottom")
        self.assertEqual(refs["J1"].lib_id, "Connector_USB:USB_C_Receptacle")
        self.assertTrue(refs["R1"].dnp)
        # H1 (MountingHole library) plus J1's 2.5 mm unplated hole; J1's 0.65 mm peg is not a hole.
        holes = {(h.ref, h.source): h for h in info.holes}
        self.assertEqual(set(holes), {("H1", "footprint"), ("J1", "npth")})
        # J1 is turned 90 degrees: its local (0, 5) lands at (+5, 0) from the footprint.
        j1 = holes[("J1", "npth")]
        self.assertAlmostEqual(j1.x, 45)
        self.assertAlmostEqual(j1.y, 30)
        sx, sy = info.to_step(15, 25)
        self.assertAlmostEqual(sx, 5)
        self.assertAlmostEqual(sy, ymax - 25)

    def test_origin_choices(self):
        info = kb.board_info(SMALL_BOARD)
        self.assertEqual(kb.resolve_origin(info, "page"), (0, 0))
        self.assertEqual(kb.resolve_origin(info, [3, 4]), (3, 4))
        cx, cy = kb.resolve_origin(info, "center")
        self.assertAlmostEqual(cx, 30)
        with self.assertRaises(ValueError):
            kb.resolve_origin(info, "nowhere")

    def test_rotate_matches_kicad(self):
        x, y = kb.rotate(1, 0, 90)  # local +x turns to screen-up (-y) for +90
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, -1)

    def test_arc_extremes(self):
        pts = kb._arc_points((1, 0), (0, 1), (-1, 0))  # half circle through the top
        self.assertAlmostEqual(max(p[1] for p in pts), 1)
        self.assertAlmostEqual(min(p[1] for p in pts), 0)

    @unittest.skipUnless(HAVE_TEMPLATE, "KiCad templates not installed")
    def test_arduino_mega(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = _copy_template(tmp)
            info = kb.board_info(board)
        self.assertAlmostEqual(info.width, 101.6)
        self.assertAlmostEqual(info.height, 53.34)
        self.assertEqual(info.copper_layers, 2)
        self.assertEqual(info.origin, (100.0, 100.0))  # the board's drill origin
        self.assertEqual(len(info.footprints), 13)
        self.assertEqual(len(info.holes), 6)
        # The Arduino Mega hole pattern, from the board's corner.
        holes = sorted((round(info.to_step(h.x, h.y)[0], 2), round(info.to_step(h.x, h.y)[1], 2))
                       for h in info.holes)
        self.assertEqual(holes, [(13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56),
                                 (90.17, 50.8), (96.52, 2.54)])


class ModelPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.models = os.path.join(self.tmp, "3dmodels", "Connector_USB.3dshapes")
        os.makedirs(self.models)
        open(os.path.join(self.models, "USB_C.step"), "w").close()  # STEP only, like KiCad 10
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.project, "3d"))
        open(os.path.join(self.project, "3d", "local.step"), "w").close()
        self.env = kb.kicad_env(self.project, models_dir=os.path.join(self.tmp, "3dmodels"))
        self.env.pop("NOT_SET", None)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_resolve(self):
        text, report = kb.resolve_models(SMALL_BOARD, self.project, self.env)
        found = {r["model"]: r for r in report}
        usb = found["${KICAD9_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C.wrl"]
        self.assertTrue(usb["found"])
        self.assertEqual(usb["resolved"], os.path.join(self.models, "USB_C.step"))
        self.assertTrue(found["${KIPRJMOD}/3d/local.step"]["found"])
        self.assertEqual(found["models/rel.step"]["resolved"], os.path.join(self.project, "models", "rel.step"))
        self.assertFalse(found["models/rel.step"]["found"])
        self.assertFalse(found["${NOT_SET}/x.step"]["found"])
        self.assertIn(f'(model "{os.path.join(self.models, "USB_C.step")}"', text)
        self.assertIn('(model "${NOT_SET}/x.step")', text)  # unknown variables stay as they were
        kb.parse_sexpr(text)  # still a valid board

    def test_prepare_board_leaves_the_original(self):
        board = os.path.join(self.project, "small.kicad_pcb")
        with open(board, "w") as handle:
            handle.write(SMALL_BOARD)
        with open(os.path.join(self.project, "small.kicad_pro"), "w") as handle:
            handle.write("{}")
        before = _sha(board)
        work = os.path.join(self.tmp, "work")
        copy, report = kb.prepare_board(board, work, self.env)
        self.assertEqual(_sha(board), before)
        self.assertEqual(copy, os.path.join(work, "small.kicad_pcb"))
        self.assertTrue(os.path.isfile(os.path.join(work, "small.kicad_pro")))
        self.assertEqual(len(report), 4)
        with self.assertRaises(kb.KiCadError):
            kb.prepare_board(board, self.project, self.env)


class OptionTests(unittest.TestCase):
    def test_step_args(self):
        args = kb.step_args(kb.step_options(), (100, 100))
        for flag in ("--include-tracks", "--include-pads", "--include-zones", "--include-silkscreen",
                     "--include-soldermask", "--no-dnp"):
            self.assertIn(flag, args)
        self.assertNotIn("--include-inner-copper", args)
        self.assertEqual(args[-2:], ["--user-origin", "100.000000x100.000000mm"])
        args = kb.step_args(kb.step_options({"components": False, "silkscreen": False, "dnp": True}), (0, 0))
        self.assertIn("--no-components", args)
        self.assertNotIn("--include-silkscreen", args)
        self.assertNotIn("--no-dnp", args)
        with self.assertRaises(ValueError):
            kb.step_options({"colour": True})

    def test_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, "b.kicad_pcb")
            with open(board, "w") as handle:
                handle.write(SMALL_BOARD)
            key = kb.cache_key(board)
            self.assertEqual(key, kb.cache_key(board, {}))
            self.assertNotEqual(key, kb.cache_key(board, {"zones": False}))
            with open(board, "a") as handle:
                handle.write("\n")
            self.assertNotEqual(key, kb.cache_key(board))

    def test_summarize_drc(self):
        data = {"violations": [{"type": "a", "severity": "error"}, {"type": "a", "severity": "warning"},
                               {"type": "b", "severity": "warning"}],
                "unconnected_items": [{}], "schematic_parity": []}
        summary = kb.summarize_drc(data)
        self.assertEqual((summary["errors"], summary["warnings"], summary["unconnected"]), (1, 2, 1))
        self.assertEqual(summary["by_type"], {"a": 2, "b": 1})


class MaterialTests(unittest.TestCase):
    def test_bodies(self):
        self.assertEqual(kb.classify_body("Arduino_Mega_PCB"), kb.FR4)
        self.assertEqual(kb.classify_body("Arduino_Mega_pad"), kb.COPPER)
        self.assertEqual(kb.classify_body("b_copper"), kb.COPPER)
        self.assertEqual(kb.classify_body("b_via"), kb.COPPER)
        self.assertEqual(kb.classify_body("b_soldermask001"), kb.MASK)
        self.assertEqual(kb.classify_body("b_silkscreen"), kb.SILK)
        self.assertIsNone(kb.classify_body("PinSocket_1x08_P2.54mm_Vertical"))

    def test_components(self):
        self.assertEqual(kb.classify_component("Connector_PinSocket_2.54mm"), kb.PBT)
        self.assertEqual(kb.classify_component("", "PinHeader_1x04"), kb.PBT)
        self.assertEqual(kb.classify_component("Package_SO"), kb.EPOXY)
        self.assertEqual(kb.classify_component("Capacitor_SMD"), kb.CERAMIC)
        self.assertEqual(kb.classify_component("Resistor_SMD"), kb.CERAMIC)
        self.assertEqual(kb.classify_component("", "R_0603_1608Metric"), kb.CERAMIC)
        self.assertEqual(kb.classify_component("Button_Switch_THT"), kb.PLASTIC)

    def test_metallic(self):
        self.assertTrue(kb.is_metallic((0.86, 0.74, 0.4)))  # gold
        self.assertTrue(kb.is_metallic((0.82, 0.82, 0.84)))  # tin
        self.assertFalse(kb.is_metallic((0.1, 0.1, 0.1)))  # black plastic
        self.assertFalse(kb.is_metallic((0.28, 0.44, 0.37)))  # solder mask

    def test_mass_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "vault.csv")
            user = os.path.join(tmp, "user.csv")
            with open(vault, "w") as handle:
                handle.write("key,grams\n# heavy parts\nTYPE-C-31-M-12,0.9\nR_0603_1608Metric,0.002\n")
            with open(user, "w") as handle:
                handle.write("type-c-31-m-12,1.1\n")
            table = kb.load_mass_table([vault, user, os.path.join(tmp, "missing.csv")])
        info = kb.board_info(SMALL_BOARD)
        refs = {fp.ref: fp for fp in info.footprints}
        self.assertEqual(kb.lookup_mass(table, refs["J1"]), 1.1)  # by MPN, the user's row wins
        self.assertEqual(kb.lookup_mass(table, refs["R1"]), 0.002)  # by footprint name
        self.assertIsNone(kb.lookup_mass(table, refs["H1"]))
        self.assertIsNone(kb.lookup_mass({}, refs["J1"]))

    def test_match_footprint(self):
        info = kb.board_info(SMALL_BOARD)
        x, y = info.to_step(20, 30)
        self.assertEqual(kb.match_footprint(info, x, y).ref, "R1")
        self.assertIsNone(kb.match_footprint(info, x + 1, y))
        self.assertIsNone(kb.match_footprint(info, x, y, side="bottom"))


@unittest.skipUnless(HAVE_CLI, "kicad-cli or KiCad templates not installed")
class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.board = _copy_template(cls.tmp)
        cls.before = _sha(cls.board)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def tearDown(self):
        self.assertEqual(_sha(self.board), self.before, "the board file was changed")
        leftovers = [n for n in os.listdir(os.path.dirname(self.board)) if n.endswith((".step", ".png", ".json"))]
        self.assertEqual(leftovers, [])

    def test_version(self):
        self.assertRegex(kb.cli_version(), r"^\d+\.\d+")

    def test_export_and_cache(self):
        cache = os.path.join(self.tmp, "cache")
        result = kb.cached_step(self.board, cache=cache)
        self.assertFalse(result.cached)
        self.assertEqual(result.missing_models, [])  # the connectors' models were found
        self.assertEqual(result.origin, (100.0, 100.0))
        with open(result.step, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("ISO-10303-21"))
        self.assertIn("PinSocket_2x18", text)
        self.assertIn("Arduino_Mega_PCB", text)
        again = kb.cached_step(self.board, cache=cache)
        self.assertTrue(again.cached)
        self.assertEqual(again.step, result.step)
        self.assertEqual(os.listdir(cache), [os.path.basename(result.step)])

    def test_render(self):
        out = kb.render(self.board, os.path.join(self.tmp, "r", "board.png"), width=320, height=200)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_drc(self):
        summary = kb.drc(self.board)
        self.assertEqual(summary["errors"], 0)
        self.assertIn("warnings", summary)

    def test_failure_is_reported(self):
        bad = os.path.join(self.tmp, "bad", "bad.kicad_pcb")
        os.makedirs(os.path.dirname(bad))
        with open(bad, "w") as handle:
            handle.write("(kicad_pcb (version 1) (garbage)")
        with self.assertRaises(kb.KiCadError):
            kb.export_step(bad, os.path.join(self.tmp, "bad.step"))


def _copy_template(tmp):
    dest = os.path.join(tmp, "Arduino_Mega")
    shutil.copytree(TEMPLATE, dest)
    return os.path.join(dest, "Arduino_Mega.kicad_pcb")


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


if __name__ == "__main__":
    unittest.main()
