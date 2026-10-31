# SPDX-License-Identifier: LGPL-2.1-or-later
"""New Project and the Structure view (BW-PDM-09 steps 5, 7 and 8).

Headless acceptance (plan section 5): a project typed as six lines becomes numbered part, assembly
and drawing files checked in as WIP 01 in inch; changing one part marks exactly its drawing and the
drawings of the assemblies above it out of date. ``BW_PROJECT_DIR`` keeps the scratch vault there.
The GUI tests (the Vault tab) run only in a GUI FreeCAD.
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import FreeCAD as App

CART = "Shop cart\n  Frame x2\n    Side rail x2\n    Cross bar\n  Deck plate\n  Caster 4 in x4 cots\n"
IMPERIAL_DECIMAL = 3


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


def _unit_index(doc):
    value = doc.UnitSystem
    if isinstance(value, int):
        return value
    return doc.getEnumerationsOfProperty("UnitSystem").index(value)


class _Vault(unittest.TestCase):
    def setUp(self):
        from bruceworx import vault_setup as vs

        keep = os.environ.get("BW_PROJECT_DIR")
        self.tmp = os.path.realpath(keep or tempfile.mkdtemp(prefix="bw-project-"))  # FreeCAD writes links by real path
        if keep:
            shutil.rmtree(keep, ignore_errors=True)
            os.makedirs(keep)
        else:
            self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(close_all)
        close_all()
        os.environ["BW_PDM_ACTOR"] = "accept@mac"
        setup = vs.VaultSetup(name="Cart", folder=os.path.join(self.tmp, "vault"))
        setup.numbering.programs = [vs.ProgramSetup("DM", "Demo")]
        setup.standards = {"units": "in", "default_sheet": "ANSI_B_Landscape"}
        self.vault = vs.create_vault(setup, actor="accept@mac")
        from bruceworx.pdm_workspace import Workspace

        self.work = Workspace(self.vault)
        self.s = SimpleNamespace(vault=self.vault, work=self.work, root=self.vault.root)

    def create(self, text=CART, **kw):
        import bruceworx_project as bp
        from bruceworx import project_setup as ps

        plan = ps.parse_structure(text)
        ps.plan_numbers(plan, self.vault.root, "DM")
        ps.check(plan, self.vault, "DM")
        self.assertTrue(plan.ok, [i.text for i in plan.issues])
        options = bp.Options(name=kw.pop("name", "Shop cart"), program="DM", units=kw.pop("units", "in"), **kw)
        return plan, bp.create_project(self.vault, self.work, plan, options)


class TestAcceptance(_Vault):
    def test_six_lines_become_numbered_files_in_inch(self):
        import bruceworx_bom as bb
        from bruceworx import product_tree as pt
        from bruceworx import vault_trees as vt
        from bruceworx.pdm_workspace import linked_files

        plan, report = self.create()
        self.assertEqual(report.failed, [], report.failed)
        self.assertTrue(report.ok, report.text())
        pn = {name: number for name, number, _kind in report.items}
        dwg = dict(report.drawings)
        self.assertEqual(sorted(pn), ["Caster 4 in", "Cross bar", "Deck plate", "Frame", "Shop cart", "Side rail"])
        self.assertEqual(sorted(dwg), ["Cross bar", "Deck plate", "Frame", "Shop cart", "Side rail"])  # no drawing for COTS
        self.assertTrue(pn["Shop cart"].startswith("DM-02-") and pn["Side rail"].startswith("DM-01-"))
        self.assertTrue(all(d.startswith("DM-03-") for d in dwg.values()))  # question 11: type 03 for now
        self.assertEqual(report.top, pn["Shop cart"])
        # the vault: items, the project, every file WIP 01 with one version
        v = self.vault
        for number in list(pn.values()) + list(dwg.values()):
            item = v.items[number]
            self.assertEqual((item.latest.code, item.state), ("01", "WIP"), number)
            self.assertEqual(len(v.versions[number]), 1, number)
            self.assertNotIn(number, v.locks)
        self.assertEqual(v.items[pn["Caster 4 in"]].item_type, "cots")
        self.assertEqual(v.items[pn["Frame"]].item_type, "assembly")
        self.assertEqual(v.items[dwg["Frame"]].drawing_of, pn["Frame"])
        project = v.projects[report.project]
        self.assertEqual((project.code, project.name, project.program, project.top, project.units),
                         ("SHOP-CART", "Shop cart", "DM", pn["Shop cart"], "in"))
        self.assertEqual(v.projects_of(pn["Shop cart"]), [project])
        # drawings recorded what they were made from (the closure); parts have no fingerprint yet (empty)
        rail = v.latest_version(pn["Side rail"])
        self.assertIsNone(rail.fingerprint)
        self.assertEqual(v.latest_version(dwg["Side rail"]).made_from, {pn["Side rail"]: rail.sha256})
        self.assertEqual(set(v.latest_version(dwg["Shop cart"]).made_from),
                         {pn[n] for n in ("Shop cart", "Frame", "Side rail", "Cross bar", "Deck plate", "Caster 4 in")})
        # the files: links by number, inch, a page with the P/N in the title block
        work = self.work
        for number in list(pn.values()) + list(dwg.values()):
            self.assertTrue(os.path.exists(work.path(number)), number)
        self.assertEqual(sorted(linked_files(work.path(pn["Frame"]))),
                         sorted([pn["Side rail"] + ".FCStd", pn["Cross bar"] + ".FCStd"]))
        self.assertEqual(linked_files(work.path(dwg["Side rail"])), [pn["Side rail"] + ".FCStd"])
        top = App.openDocument(work.path(pn["Shop cart"]))
        self.assertEqual(_unit_index(top), IMPERIAL_DECIMAL)
        rows = {}
        for row in bb.walk(top):
            rows[row["name"]] = rows.get(row["name"], 0) + row["qty"]
        self.assertEqual(rows, {pn["Frame"] + ".FCStd": 2, pn["Deck plate"] + ".FCStd": 1, pn["Caster 4 in"] + ".FCStd": 4})
        self.assertEqual(bb.top_assembly(top).PartNumber, pn["Shop cart"])
        self.assertTrue(any(o.TypeId == "App::FeaturePython" and "ObjectToGround" in o.PropertiesList for o in top.Objects))
        close_all()
        drawing = App.openDocument(work.path(dwg["Side rail"]))
        self.assertEqual(_unit_index(drawing), IMPERIAL_DECIMAL)
        page = next(o for o in drawing.Objects if o.isDerivedFrom("TechDraw::DrawPage"))
        self.assertEqual(getattr(page, "BWModel").Name, "Model")
        self.assertFalse(any(v.isDerivedFrom("TechDraw::DrawProjGroup") for v in page.Views))  # blank (answer 8)
        self.assertTrue(os.path.basename(page.Template.Template).startswith("ANSI_B_Landscape"))
        self.assertEqual(page.Template.EditableTexts.get("part_number"), dwg["Side rail"])
        self.assertEqual(page.PartNumber, dwg["Side rail"])
        close_all()
        # the Structure view's model: every drawing current, the caster needs none
        s = vt.structure(v)
        tree = pt.Tree(v, s)
        for name in ("Shop cart", "Frame", "Side rail", "Cross bar", "Deck plate"):
            self.assertEqual(tree.cell(pn[name])["state"], pt.CURRENT, name)
        self.assertEqual(tree.cell(pn["Caster 4 in"])["state"], pt.NOT_NEEDED)
        self.assertEqual([r["pn"] for r in tree.children(pn["Shop cart"])], [pn["Frame"], pn["Deck plate"], pn["Caster 4 in"]])
        self.assertEqual(tree.children(pn["Shop cart"])[0]["source"], "bom")  # the writer records each assembly's BOM
        self.assertEqual([l.as_dict() for l in v.bom_of(pn["Frame"])],
                         [{"child": pn["Side rail"], "qty": 2, "rev": "01"}, {"child": pn["Cross bar"], "qty": 1, "rev": "01"}])
        self.assertEqual(pt.roots(v, s)[0]["key"], "program:" + vt.program_code(pn["Shop cart"]))  # projects by program
        self.assertEqual(pt.roll_up(s["bom"], pn["Shop cart"])[pn["Side rail"]], 4)
        # no staging, no backups left behind
        self.assertFalse(os.path.exists(os.path.join(v.root, ".import")))
        self.assertEqual([n for n in os.listdir(work.root) if n.endswith((".FCBak", ".FCStd1"))], [])

    def test_changing_one_part_marks_its_drawings_out_of_date(self):
        """Acceptance, second half: the side rail changes; its drawing, the frame's and the cart's are
        out of date, the deck plate's and the cross bar's stay current."""
        import Part

        import bruceworx_project as bp
        from bruceworx import product_tree as pt
        from bruceworx import vault_trees as vt

        _plan, report = self.create()
        self.assertTrue(report.ok, report.text())
        pn = {name: number for name, number, _kind in report.items}
        dwg = dict(report.drawings)
        v, work = self.vault, self.work
        work.checkout(pn["Side rail"], with_links=False)
        doc = App.openDocument(work.path(pn["Side rail"]))
        part = doc.getObject("Part")
        box = doc.addObject("Part::Feature", "Rail")
        box.Shape = Part.makeBox(1.0 * 25.4, 1.0 * 25.4, 24.0 * 25.4)  # a 1 x 1 x 24 in rail
        part.addObject(box)
        doc.recompute()
        doc.save()
        extras = bp.checkin_extras(self.s, pn["Side rail"])
        self.assertIn("fingerprint", extras)
        self.assertAlmostEqual(extras["fingerprint"]["volume"], 24 * 25.4 ** 3, delta=1.0)
        close_all()
        work.checkin(pn["Side rail"], "rail modelled", **extras)
        self.assertEqual(v.latest_version(pn["Side rail"]).fingerprint, extras["fingerprint"])
        tree = pt.Tree(v, vt.structure(v))
        states = {name: tree.cell(pn[name])["state"] for name in pn}
        self.assertEqual(states, {"Side rail": pt.OUT_OF_DATE, "Frame": pt.OUT_OF_DATE, "Shop cart": pt.OUT_OF_DATE,
                                  "Cross bar": pt.CURRENT, "Deck plate": pt.CURRENT, "Caster 4 in": pt.NOT_NEEDED})
        self.assertEqual(tree.row(pn["Shop cart"])["counts"]["out_of_date"], 2)  # the frame and the rail below it
        self.assertEqual(tree.out_of_date(pn["Shop cart"]), [dwg["Shop cart"], dwg["Frame"], dwg["Side rail"]])
        found = tree.cell(pn["Frame"])["states"][dwg["Frame"]]
        self.assertEqual(found["changed"], [(pn["Side rail"], "geometry")])
        # a description-only change on the plate: metadata only
        work.checkout(pn["Deck plate"], with_links=False)
        doc = App.openDocument(work.path(pn["Deck plate"]))
        doc.getObject("Part").Description = "Deck plate, 3/4 in plywood"
        doc.save()
        close_all()
        work.checkin(pn["Deck plate"], "description", **bp.checkin_extras(self.s, pn["Deck plate"]))
        tree = pt.Tree(v, vt.structure(v))
        # an empty part has no fingerprint at either version: the change counts as geometry (nothing to compare)
        self.assertEqual(tree.cell(pn["Deck plate"])["state"], pt.OUT_OF_DATE)
        # update the rail's drawing from the new rail: current again, with its views (Insert Views)
        work.checkout(dwg["Side rail"], with_links=True)
        doc = App.openDocument(work.path(dwg["Side rail"]))
        page = next(o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage"))
        group = bp.add_views(page, vault_root=v.root)
        self.assertEqual(len(group.Views), 3)
        doc.save()
        extras = bp.checkin_extras(self.s, dwg["Side rail"])
        self.assertEqual(extras["made_from"], {pn["Side rail"]: v.latest_version(pn["Side rail"]).sha256})
        close_all()
        work.checkin(dwg["Side rail"], "views from the modelled rail", **extras)
        tree = pt.Tree(v, vt.structure(v))
        self.assertEqual(tree.cell(pn["Side rail"])["state"], pt.CURRENT)
        self.assertEqual(tree.cell(pn["Frame"])["state"], pt.OUT_OF_DATE)
        # the release checklist warns, and only warns
        from bruceworx import release_check as rc

        lines = {line.key: line for line in rc.check_item(work, pn["Frame"])}
        self.assertTrue(lines[rc.DRAWING_CURRENT].warning)
        self.assertNotIn(rc.TEXTS[rc.DRAWING_CURRENT], " ".join(rc.blockers(list(lines.values()))))

    def test_reused_item_duplicate_and_create_drawing(self):
        import bruceworx_project as bp
        from bruceworx import product_tree as pt
        from bruceworx import vault_trees as vt

        _plan, first = self.create("Base\n  Leg x4\n", name="Base")
        self.assertTrue(first.ok, first.text())
        leg = dict((n, p) for n, p, _k in first.items)["Leg"]
        text = f"Bench\n  Seat\n    Slat x3\n  Back\n    Slat x2\n  {leg} x4\n"
        plan, report = self.create(text, name="Bench", units="mm")
        self.assertEqual(plan.duplicates, ["Slat"])
        self.assertTrue(report.ok, report.text())
        pn = {name: number for name, number, _kind in report.items}
        self.assertEqual(sorted(pn), ["Back", "Bench", "Seat", "Slat"])  # the slat once, the leg reused
        v = self.vault
        self.assertEqual(v.projects[report.project].units, "mm")
        s = vt.structure(v)
        self.assertEqual(pt.roll_up(s["bom"], pn["Bench"]), {pn["Seat"]: 1, pn["Back"]: 1, pn["Slat"]: 5, leg: 4})
        rows = pt.roots(v, s)  # programs first (both projects number under one), then every assembly
        self.assertEqual([r["kind"] for r in rows], ["program"] * (len(rows) - 4) + ["assembly"] * 4)
        # Create drawing (the Structure view's row menu): a numbered drawing, WIP 01, made from the item
        self.assertEqual(len(v.drawings_of(leg)), 1)
        number = bp.new_drawing_item(v, self.work, leg, units="mm")
        self.assertTrue(number.startswith("DM-03-"))
        self.assertEqual(v.items[number].drawing_of, leg)
        self.assertEqual(v.latest_version(number).made_from, {leg: v.latest_version(leg).sha256})
        self.assertEqual(len(v.drawings_of(leg)), 2)
        from bruceworx.pdm_workspace import linked_files

        self.assertEqual(linked_files(self.work.path(number)), [leg + ".FCStd"])  # by name: resolves in the work folder

    def test_a_structure_that_fails_to_verify_puts_nothing_in_the_vault(self):
        import bruceworx_project as bp
        from bruceworx import project_setup as ps

        plan = ps.parse_structure("Top\n  Rail x2\n")
        ps.plan_numbers(plan, self.vault.root, "DM")
        real = bp.verify
        bp.verify = lambda *a, **k: (_ for _ in ()).throw(ValueError("counts differ"))
        try:
            with self.assertRaisesRegex(ValueError, "counts differ"):
                bp.create_project(self.vault, self.work, plan, bp.Options(name="Top", program="DM", units="in"))
        finally:
            bp.verify = real
        self.assertEqual(self.vault.items, {})
        self.assertEqual(self.vault.projects, {})
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, ".import")))
        from bruceworx.sources import JsonNumberSource
        from bruceworx import vault_setup as vs

        self.assertEqual(JsonNumberSource(vs.numbering_path(self.vault.root)).preview("DM", "01"), "DM-01-01001-001")


class TestHelpers(unittest.TestCase):
    def test_structure_tab_texts(self):
        import bruceworx_structure as bs

        self.assertEqual(bs.chip_text("Out of date", 0), "Out of date")
        self.assertEqual(bs.chip_text("Out of date", 3), "Out of date  3")
        row = {"display": "DM-01-01001-001-01", "description": "Side rail", "qty": 2, "state": "WIP", "source": "links",
               "lock": ""}
        self.assertEqual(bs.item_text(row), "DM-01-01001-001-01  Side rail  ×2")
        self.assertEqual(bs.state_text(row), "WIP  · from links")
        self.assertEqual(bs.item_text(dict(row, qty=1, description="")), "DM-01-01001-001-01")

    def test_sheet_and_units(self):
        import bruceworx_project as bp

        self.assertEqual(bp.sheet_for("in"), "ANSI_B_Landscape")
        self.assertEqual(bp.sheet_for("mm", SimpleNamespace(default_sheet="ISO_A3_Landscape")), "ISO_A3_Landscape")
        doc = App.newDocument("UnitsCheck")
        try:
            self.assertEqual(bp.apply_units(doc, "in"), "in")
            self.assertEqual(_unit_index(doc), IMPERIAL_DECIMAL)
            bp.apply_units(doc, "mm")
            self.assertEqual(_unit_index(doc), 0)
        finally:
            App.closeDocument(doc.Name)


@unittest.skipUnless(App.GuiUp, "the Vault tab needs the GUI")
class TestStructureTab(_Vault):
    def test_tab_shows_the_project(self):
        import bruceworx_structure as bs
        import bruceworx_vaultclient as vc
        from bruceworx import product_tree as pt
        from bruceworx import vault_trees as vt

        _plan, report = self.create()
        pn = {name: number for name, number, _kind in report.items}
        prefs = App.ParamGet(vc.PREFS_PATH)
        before = prefs.GetString("VaultPath", "")
        prefs.SetString("VaultPath", self.vault.root)
        self.addCleanup(prefs.SetString, "VaultPath", before)
        vc._open_session(self.vault.root)
        self.addCleanup(vc._open_session, before)
        tab = bs.classes()(None)
        try:
            self.assertTrue(tab.set_root("program:" + vt.program_code(pn["Shop cart"])))
            self.assertEqual(tab.model.rowCount(), 1)
            top = tab.model.item(0, 0)
            self.assertEqual(top.data(bs.PN_ROLE), pn["Shop cart"])
            self.assertEqual(top.rowCount(), 3)  # frame, plate, caster; the frame's children wait for a click
            frame = top.child(0, 0)
            self.assertEqual(frame.rowCount(), 1)
            self.assertEqual(frame.child(0, 0).text(), bs.PLACEHOLDER)
            tab._expanded(frame.index())
            self.assertEqual([frame.child(i, 0).data(bs.PN_ROLE) for i in range(frame.rowCount())],
                             [pn["Side rail"], pn["Cross bar"]])
            self.assertEqual(top.child(2, 2).text(), pt.LABELS[pt.NOT_NEEDED])
            self.assertEqual(tab.chips["needs_drawing"].text(), "Needs a drawing")
            self.assertTrue(tab.badge.isHidden() or tab.badge.text() == "in")  # inch vault, inch project: no badge
            tab.set_view("list")
            self.assertEqual(tab.model.rowCount(), 6)  # the cart, then every item under it once
            tab.set_chip("out_of_date")
            self.assertEqual(tab.model.rowCount(), 0)
        finally:
            vc.remove_listener(tab.refresh)
            tab.deleteLater()

    def test_new_project_panel_previews_and_reports_duplicates(self):
        import bruceworx_project as bp
        import bruceworx_vaultclient as vc
        from PySide import QtWidgets

        prefs = App.ParamGet(vc.PREFS_PATH)
        before = prefs.GetString("VaultPath", "")
        prefs.SetString("VaultPath", self.vault.root)
        self.addCleanup(prefs.SetString, "VaultPath", before)
        vc._open_session(self.vault.root)
        self.addCleanup(vc._open_session, before)
        panel = bp.classes()["NewProjectPanel"](vc.session())
        try:
            self.assertEqual(len(panel.form), 3)
            self.assertEqual(panel.units.currentData(), "in")  # the vault's default
            self.assertEqual(panel.sheet.currentData(), "ANSI_B_Landscape")
            self.assertFalse(panel.views.isChecked())  # blank drawings by default (answer 8)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            panel.modifyStandardButtons(buttons)
            self.assertEqual(panel.ok_button().text(), "Create project")
            self.assertFalse(panel.ok_button().isEnabled())
            panel.name_edit.setText("Shop cart")
            self.assertEqual(panel.code_edit.text(), "SHOP-CART")
            panel.text.setPlainText("Shop cart\n  Left\n    Bolt x4\n  Right\n    Bolt x4\n")
            panel.preview()
            self.assertTrue(panel.ok_button().isEnabled())
            self.assertEqual(panel.tree.topLevelItemCount(), 1)
            top = panel.tree.topLevelItem(0)
            self.assertTrue(top.text(1).startswith("DM-02-"))
            bolt = top.child(0).child(0)
            self.assertIn("used in 2 places", bolt.text(0))  # the duplicate bubble (answer 10)
            self.assertIn("8 parts in all", panel.summary.text())
            self.assertEqual(panel.options().units, "in")
            panel.text.setPlainText("Top\n")
            panel.preview()
            self.assertFalse(panel.ok_button().isEnabled())
            self.assertIn("at least one item", panel.problems.text())
        finally:
            panel.release()


if __name__ == "__main__":
    unittest.main()
