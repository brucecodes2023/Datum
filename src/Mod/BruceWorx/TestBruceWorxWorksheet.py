# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the design worksheet engine (bruceworx_worksheet, MOD-15) and the spec that drives the model (MOD-17).

Headless: FreeCADCmd -t TestBruceWorxWorksheet. Documents and files are throwaway; no vault."""

import os
import shutil
import tempfile
import unittest

import FreeCAD

import bruceworx_worksheet as ws

GEARBOX = """---
bw-worksheet: 1
title: Planetary gearbox 5:1
describes: Gearbox
---
## Gear train
Single stage, ring fixed, carrier out.

```calc
n_in = 600 rpm                       in rpm
i = 1 + Zr / Zs                      check i == 5 "target 5"
n_out = n_in / i                     in rpm
a = m_n * (Zs + Zp) / 2              in mm  => Parameters
a_r = m_n * (Zr - Zp) / 2            in mm  check a == a_r "coaxial"
z_min = 2 / sin(alpha)^2             warn z_min > Zs "sun undercut"
```
With the ring fixed, \\[ n_c = \\frac{n_s}{1 + Z_r/Z_s} \\]
"""


class TestFile(unittest.TestCase):
    def test_header_blocks_and_round_trip(self):
        doc = ws.parse(GEARBOX)
        self.assertEqual(doc.header["title"], "Planetary gearbox 5:1")
        self.assertEqual(doc.header["describes"], "Gearbox")
        self.assertEqual([b.kind for b in doc.blocks], ["text", "calc", "text"])
        lines = doc.calc_lines()
        self.assertEqual([l.name for l in lines], ["n_in", "i", "n_out", "a", "a_r", "z_min"])
        self.assertEqual(lines[3].file_line, 13)
        again = ws.parse(ws.dump(doc))
        self.assertEqual([l.source for l in again.calc_lines()], [l.source for l in lines])
        self.assertEqual(again.header, doc.header)
        doc.header["results"] = {"a": "15 mm"}
        self.assertEqual(ws.parse(ws.dump(doc)).header["results"], {"a": "15 mm"})

    def test_save_keeps_the_text_as_written(self):
        text = "---\ntitle: Gears: 5:1   # my note\ndescribes: Gearbox\n---\n# keep me\n```calc\nx = 2   # two\n```\n"
        out = ws.set_header(text, "results", {"x": "2"})
        self.assertEqual(out, text.replace("describes: Gearbox\n", 'describes: Gearbox\nresults: {"x": "2"}\n'))
        again = ws.set_header(out, "results", {"x": "3"})
        self.assertEqual(again.count("results:"), 1)
        self.assertIn('results: {"x": "3"}', again)
        self.assertTrue(ws.set_header("no header\n", "results", {}).startswith("---\nbw-worksheet: 1\nresults: {}\n---\nno header"))

    def test_line_parts(self):
        line = ws.parse_line('a = m_n * (Zs + Zp) / 2   in mm  => Parameters  check a == a_r "coaxial"  # note')
        self.assertEqual((line.name, line.expr, line.unit, line.export), ("a", "m_n * (Zs + Zp) / 2", "mm", True))
        self.assertEqual(line.checks, [("check", "a == a_r", "coaxial")])
        self.assertEqual(line.error, "")
        inch = ws.parse_line("w = 3 in + 2 mm in in")
        self.assertEqual((inch.expr, inch.unit), ("3 in + 2 mm", "in"))
        only = ws.parse_line('warn z_min > Zs "undercut"')
        self.assertEqual((only.name, only.checks), ("", [("warn", "z_min > Zs", "undercut")]))
        doc_path = ws.parse_line("T_m = Motor#Drive.Torque * 2")
        self.assertEqual(doc_path.expr, "Motor#Drive.Torque * 2")
        self.assertIsNone(ws.parse_line("   # just a comment"))
        self.assertIsNone(ws.parse_line(""))

    def test_bad_names_and_lines(self):
        self.assertIn("m_n", ws.parse_line("m = 1 mm").error)
        self.assertIn("T_in", ws.parse_line("T = 2 N*m").error)
        self.assertIn("cell", ws.parse_line("A1 = 2").error)
        self.assertIn("name = expression", ws.parse_line("2 + 2").error)
        self.assertIn("Parameters", ws.parse_line("x = 2 => Elsewhere").error)

    def test_rewrites(self):
        self.assertEqual(ws.rewrite("0.4 N*m"), "(0.4 * 1 N * 1 m)")
        self.assertEqual(ws.rewrite("2 * 0.4 N * m + T_x"), "2 * (0.4 * 1 N * 1 m) + T_x")
        self.assertEqual(ws.rewrite("600 rpm"), "(600 / 1 min)")
        self.assertEqual(ws.rewrite("2 N * L"), "2 N * L")  # L is a name: not a unit product
        self.assertEqual(ws.rewrite("<<0.4 N*m>>"), "<<0.4 N*m>>")
        self.assertEqual(ws.rewrite("1 kg/m^3"), "1 kg/m^3")

    def test_refs(self):
        kinds = {(r.text, r.kind) for r in ws.refs("sqrt(Zr) * 2 mm + Motor.Torque + Doc#Obj.L + pi + x_1")}
        self.assertEqual(kinds, {("sqrt", "func"), ("Zr", "name"), ("mm", "unit"), ("Motor", "object"),
                                 ("Doc", "object"), ("pi", "const"), ("x_1", "name")})
        self.assertEqual([r.text for r in ws.refs("<<Zr>> + 1e3 + a")], ["a"])


def settle(model):
    """Recompute; in the app the worksheet follows on a timer (bruceworx_worksheet.LIVE_DELAY_MS), so wait."""
    model.recompute()
    if FreeCAD.GuiUp:
        import time

        import FreeCADGui

        end = time.time() + 3 * ws.LIVE_DELAY_MS / 1000.0
        while time.time() < end:
            FreeCADGui.updateGui()
            time.sleep(0.02)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-ws-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.docs = []

    def tearDown(self):
        for ws_ in ws.open_worksheets():
            ws.unregister(ws_)
        for doc in self.docs:
            if doc.Name in FreeCAD.listDocuments():
                FreeCAD.closeDocument(doc.Name)

    def new_doc(self, name):
        doc = FreeCAD.newDocument(name)
        self.docs.append(doc)
        return doc

    def sheet(self, doc, values):
        import bruceworx_parameters as bp

        sheet = bp.make_sheet(doc)
        for row, (alias, content) in enumerate(values, start=2):
            sheet.set(f"A{row}", alias)
            sheet.set(f"B{row}", content)
            sheet.setAlias(f"B{row}", alias)
        doc.recompute()
        return sheet


class TestEvaluate(Base):
    def gearbox(self):
        model = self.new_doc("Gearbox")
        self.sheet(model, [("Zs", "12"), ("Zp", "18"), ("Zr", "48"), ("m_n", "1 mm"), ("alpha", "20 deg")])
        return model, ws.Worksheet(GEARBOX)

    def test_gearbox_numbers(self):
        _model, sheet = self.gearbox()
        res = {r.line.name: r for r in sheet.evaluate()}
        self.assertTrue(all(r.ok for r in res.values()), [(r.line.name, r.message) for r in res.values()])
        self.assertEqual(res["i"].shown, "5")
        self.assertEqual(res["i"].checks, [("check", "target 5", True, "")])
        self.assertEqual(res["n_in"].shown, "600 rpm")
        self.assertEqual(res["n_out"].shown, "120 rpm")
        self.assertEqual(res["a"].shown, "15 mm")
        self.assertEqual(res["a_r"].checks, [("check", "coaxial", True, "")])
        # 2 / sin(20 deg)^2 = 17.097: a 12-tooth sun undercuts at 20 deg, so the warning shows
        self.assertAlmostEqual(ws._number(res["z_min"].value), 2 / (0.3420201433256687 ** 2), 9)
        self.assertEqual(res["z_min"].checks, [("warn", "sun undercut", False, "")])
        self.assertEqual(res["i"].inputs, {"Zr": 48, "Zs": 12})

    def test_errors_spoil_only_their_dependents(self):
        self.new_doc("Gearbox")
        sheet = ws.Worksheet("""```calc
x = 2 mm
y = nope * 2
z = y + 1 mm
w = x * 3
v = 1 mm + 1 N
u = w in MPa
```""")
        res = {r.line.name: r for r in sheet.evaluate()}
        self.assertEqual(res["x"].shown, "2 mm")
        self.assertEqual(res["y"].status, "error")
        self.assertIn("nope", res["y"].message)
        self.assertEqual((res["z"].status, res["z"].message), ("depends", "depends on line 2"))
        self.assertEqual(res["w"].shown, "6 mm")
        self.assertEqual(res["v"].status, "error")
        self.assertIn("Unit mismatch", res["v"].message)
        self.assertEqual(res["u"].status, "error")
        self.assertIn("MPa", res["u"].message)

    def test_units_and_text(self):
        sheet = ws.Worksheet("""---
units: {"Moment": "N*mm"}
---
```calc
T_in = 0.4 N*m
F_t = T_in / 15 mm          in N
T_x = T_in * 2
size = F_t > 20 N ? <<M5x0.8>> : <<M4x0.7>>
p = 2 N / 1 mm^2
```""")
        res = {r.line.name: r for r in sheet.evaluate()}
        self.assertTrue(all(r.ok for r in res.values()), [(r.line.name, r.message) for r in res.values()])
        self.assertEqual(res["T_in"].shown, "400 N*mm")  # the header's unit for a moment
        self.assertEqual(res["F_t"].shown, "26.6667 N")
        self.assertEqual(res["size"].shown, "M5x0.8")
        self.assertEqual(res["p"].shown, "2 MPa")

    def test_literal_reads_back(self):
        ctx = self.new_doc("Lit")
        vs = ctx.addObject("App::VarSet", "WSL")
        for text in ("15 mm", "0.4 N*m", "2 MPa", "600 / 1 min"):
            q = vs.evalExpression(ws.rewrite(text))
            back = vs.evalExpression(ws.literal(q))
            self.assertAlmostEqual(back.Value, q.Value, 9, text)
            self.assertEqual(tuple(back.Unit.Signature), tuple(q.Unit.Signature), text)
        self.assertEqual(vs.evalExpression(ws.literal("M4x0.7")), "M4x0.7")
        self.assertEqual(vs.evalExpression(ws.literal(2.5)), 2.5)

    def test_live_and_write_back(self):
        model, sheet = self.gearbox()
        ws.register(sheet)
        sheet.evaluate()
        self.assertEqual(sheet.export(), ["a"])
        params = model.getObject("Parameters")
        self.assertEqual(params.getContents(params.getCellFromAlias("a")), "=m_n * (Zs + Zp) / 2")
        self.assertEqual(params.get("a").Value, 15.0)
        self.assertEqual(sheet.export(), [])  # nothing changed
        # the model changes: the worksheet follows by itself; the sheet's own expression follows too
        params.set(params.getCellFromAlias("m_n"), "2 mm")
        settle(model)
        self.assertEqual(sheet.value("a").Value, 30.0)
        self.assertEqual(params.get("a").Value, 30.0)
        self.assertFalse(dict((r.line.name, r.checks) for r in sheet.results)["i"][0][2] is False)
        params.set(params.getCellFromAlias("Zr"), "60")
        settle(model)
        res = {r.line.name: r for r in sheet.results}
        self.assertEqual(res["i"].checks[0][2], False)  # 6, not the target 5
        self.assertEqual(res["a_r"].checks[0][2], False)  # no longer coaxial

    def test_save_writes_results(self):
        self.gearbox()
        sheet = ws.Worksheet(GEARBOX)
        path = sheet.save(os.path.join(self.tmp, "gear.bwsheet"))
        again = ws.Worksheet.load(path)
        self.assertEqual(again.doc.header["results"]["a"], "15 mm")
        self.assertEqual(again.doc.header["results"]["i"], "5")
        self.assertIn("\\frac{n_s}", open(path, encoding="utf-8").read())


class TestSpecDrivesModel(Base):
    """MOD-17: one spec input changes; the calculation, the Parameters sheet and the model follow."""

    SPEC = """---
title: Bracket spec
describes: Bracket
---
```calc
F_load = {F}
t_plate = max(3 mm, ceil(sqrt(6 * F_load * L_arm / (b_plate * sigma_allow)) / 1 mm) * 1 mm)  => Parameters
size = F_load > 400 N ? <<M5x0.8>> : <<M4x0.7>>   => Parameters
gap = t_plate + 0.5 mm                             => Parameters
```"""

    def build(self):
        model = self.new_doc("Bracket")
        # test inputs only (not a material or a design rule)
        params = self.sheet(model, [("L_arm", "60 mm"), ("b_plate", "30 mm"), ("sigma_allow", "40 MPa")])
        body = model.addObject("PartDesign::Body", "Body")
        box = model.addObject("PartDesign::AdditiveBox", "Plate")
        body.addObject(box)
        box.Length, box.Width = 60, 30
        hole_src = model.addObject("Part::Cylinder", "Pin")
        hole_src.Radius = 2
        model.recompute()
        return model, params, box, hole_src

    def spec(self, force):
        return ws.Worksheet(self.SPEC.replace("{F}", force))

    def test_one_input_reaches_the_model(self):
        model, params, box, pin = self.build()
        sheet = self.spec("200 N")
        sheet.evaluate()
        self.assertEqual(sorted(sheet.export()), ["gap", "size", "t_plate"])
        box.setExpression("Height", "Parameters.t_plate")
        pin.setExpression("Height", "Parameters.gap")  # a second feature, standing in for a joint offset
        model.recompute()
        # sqrt(6 * 200 N * 60 mm / (30 mm * 40 MPa)) = 7.75 mm -> 8 mm
        self.assertEqual(box.Height.Value, 8.0)
        self.assertEqual(pin.Height.Value, 8.5)
        self.assertEqual(params.get("size"), "M4x0.7")
        # the worksheet's input changes: the calculation, the sheet and the model follow, no hand edits
        sheet.set_text(self.SPEC.replace("{F}", "450 N"))
        sheet.evaluate()
        self.assertEqual(sorted(sheet.export()), ["size", "t_plate"])  # gap's expression did not change
        model.recompute()
        self.assertEqual(box.Height.Value, 12.0)  # sqrt(6*450*60/(30*40)) = 11.6 -> 12
        self.assertEqual(pin.Height.Value, 12.5)
        self.assertEqual(params.get("size"), "M5x0.8")
        # by hand in the Parameters sheet, as Bruce would: the model follows and so does the worksheet
        ws.register(sheet)
        params.set(params.getCellFromAlias("sigma_allow"), "80 MPa")
        settle(model)
        self.assertEqual(box.Height.Value, 9.0)  # sqrt(6*450*60/(30*80)) = 8.2 -> 9
        self.assertEqual(sheet.value("t_plate").Value, 9.0)

    def test_hole_size_follows(self):
        model, params, _box, _pin = self.build()
        sheet = self.spec("450 N")
        sheet.evaluate()
        sheet.export()
        body = model.getObject("Body")
        hole = model.addObject("PartDesign::Hole", "Hole")
        body.addObject(hole)
        hole.ThreadType = "ISOMetricProfile"
        hole.setExpression("ThreadSize", "Parameters.size")
        model.recompute()
        self.assertEqual(hole.ThreadSize, "M5x0.8")
        sheet.set_text(self.SPEC.replace("{F}", "200 N"))
        sheet.evaluate()
        sheet.export()
        model.recompute()
        self.assertEqual(hole.ThreadSize, "M4x0.7")

    def test_simulation_input_and_print_files_follow(self):
        import bruceworx_printprep as pp

        model, params, box, _pin = self.build()
        text = self.SPEC.replace("F_load = {F}", "F_load = {F}   => Parameters").replace(
            "describes: Bracket", 'describes: Bracket\noutputs: [{"object": "Body", "file": "out/plate.3mf"}, '
            '{"object": "Body", "file": "out/plate.stl"}, {"object": "Body", "file": "out/plate.step"}, '
            '{"object": "Body", "file": "out/plate.dxf"}]')
        sheet = ws.Worksheet(text.replace("{F}", "200 N"), path=os.path.join(self.tmp, "spec.bwsheet"))
        sheet.evaluate()
        sheet.export()
        box.setExpression("Height", "Parameters.t_plate")
        try:
            import ObjectsFem

            analysis = ObjectsFem.makeAnalysis(model, "Analysis")
            force = ObjectsFem.makeConstraintForce(model, "Load")
            analysis.addObject(force)
            force.setExpression("Force", "Parameters.F_load")
        except Exception:  # noqa: BLE001 -- a build without FEM
            force = None
        report = sheet.apply()
        out = os.path.join(self.tmp, "out")
        self.assertEqual(sorted(os.path.basename(p) for p in report["outputs"]),
                         ["plate.3mf", "plate.dxf", "plate.step", "plate.stl"])

        def printed_height():
            info = pp.read_3mf(os.path.join(out, "plate.3mf"))
            obj = info["objects"][0]
            return round(obj["max"][2] - obj["min"][2], 6)

        self.assertEqual(printed_height(), 8.0)
        if force is not None:
            self.assertAlmostEqual(force.Force.getValueAs("N").Value, 200.0, 9)
        step_before = open(os.path.join(out, "plate.step"), encoding="latin-1").read()
        # the spec input changes: every file follows, with no hand edits
        sheet.set_text(text.replace("{F}", "450 N"))
        report = sheet.apply()
        self.assertEqual(len(report["outputs"]), 4)
        self.assertEqual(printed_height(), 12.0)
        self.assertNotEqual(open(os.path.join(out, "plate.step"), encoding="latin-1").read(), step_before)
        if force is not None:
            self.assertAlmostEqual(force.Force.getValueAs("N").Value, 450.0, 9)
        # nothing changed: nothing rewritten
        self.assertEqual(sheet.apply()["outputs"], [])
        # by hand in the Parameters sheet: the live worksheet rewrites the files too
        ws.register(sheet)
        params.set(params.getCellFromAlias("sigma_allow"), "80 MPa")
        settle(model)
        self.assertEqual(printed_height(), 9.0)

    def test_bridge_tools(self):
        import bruceworx_bridge_tools as bt

        _model, params, _box, _pin = self.build()
        path = os.path.join(self.tmp, "spec.bwsheet")
        text = self.spec("200 N").text
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        out = bt.t_worksheet_read({"path": path, "text": None})
        lines = {l["name"]: l for l in out["lines"]}
        self.assertEqual(lines["t_plate"]["value"], "8 mm")
        self.assertEqual(sorted(lines["t_plate"]["model_inputs"]), ["L_arm", "b_plate", "sigma_allow"])
        trial = bt.t_worksheet_read({"path": path, "text": text.replace("200 N", "450 N")})
        self.assertEqual({l["name"]: l["value"] for l in trial["lines"]}["t_plate"], "12 mm")
        self.assertIsNone(params.getCellFromAlias("t_plate"))  # a trial changes nothing
        self.assertIn("200 N", open(path, encoding="utf-8").read())
        done = bt.t_worksheet_apply({"path": path, "text": text.replace("200 N", "450 N"), "save": True})
        self.assertEqual(sorted(done["written"]), ["gap", "size", "t_plate"])
        self.assertEqual(params.get("t_plate").Value, 12.0)
        saved = open(path, encoding="utf-8").read()
        self.assertIn("450 N", saved)
        self.assertIn('"t_plate": "12 mm"', saved)
        with self.assertRaises(bt.ToolError):
            bt.t_worksheet_apply({"path": path, "text": text.replace("200 N", "200 mm"), "save": False})

    def test_a_line_in_error_writes_nothing(self):
        _model, params, _box, _pin = self.build()
        sheet = self.spec("200 mm")  # wrong unit for a force
        sheet.evaluate()
        with self.assertRaises(ws.WorksheetError):
            sheet.export()
        self.assertIsNone(params.getCellFromAlias("t_plate"))


if __name__ == "__main__":
    unittest.main()
