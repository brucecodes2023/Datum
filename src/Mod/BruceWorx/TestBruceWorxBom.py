# SPDX-License-Identifier: LGPL-2.1-or-later
"""The assembly BOM walker, the Assembly BOM's indented merge, and where-used over revisions (PDM-06).

Headless: builds the PDM-06 spike's files (a bolt, a plate and a riser; a bracket with a plate
and a link array of four bolts; a top with the riser, two brackets and two loose bolts),
imports them into a scratch folder vault, records the BOMs the walker reads, releases, and
replaces the bracket's bolts by rivets through an ECO. ``BW_BOM_DIR`` keeps the files there.
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_bom as bb


def _part(folder, name, label, shape):
    import Part

    doc = App.newDocument(name)
    part = doc.addObject("App::Part", name)
    part.Label = label
    solid = doc.addObject("Part::Feature", name + "Solid")
    solid.Shape = shape
    part.addObject(solid)
    doc.recompute()
    doc.saveAs(os.path.join(folder, name + ".FCStd"))
    return doc, part


def _assembly(folder, name, label, children):
    """children: [(target, "link", n) or (target, "array", n)]."""
    doc = App.newDocument(name)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    asm.Label = label
    doc.saveAs(os.path.join(folder, name + ".FCStd"))
    for target, mode, n in children:
        if mode == "array":
            link = asm.newObject("App::Link", target.Name + "Array")
            link.LinkedObject = target
            link.ElementCount = n
        else:
            for i in range(n):
                link = asm.newObject("App::Link", f"{target.Name}{i}")
                link.LinkedObject = target
    doc.recompute()
    doc.save()
    return doc, asm


def build_spike(folder):
    """The spike's five files in folder; returns {name: object} with the documents left open."""
    import Part

    os.makedirs(folder, exist_ok=True)
    out = {}
    for name, label, shape in (("bolt", "M5x12 bolt", Part.makeCylinder(2.5, 12)),
                               ("rivet", "Rivet 4 mm", Part.makeCylinder(2, 8)),
                               ("plate", "Bracket plate", Part.makeBox(40, 30, 4)),
                               ("riser", "Riser", Part.makeBox(10, 10, 80))):
        out[name] = _part(folder, name, label, shape)[1]
    out["bracket"] = _assembly(folder, "bracket", "Bracket assy",
                               [(out["plate"], "link", 1), (out["bolt"], "array", 4)])[1]
    out["top"] = _assembly(folder, "top", "Pedestal assy",
                           [(out["riser"], "link", 1), (out["bracket"], "link", 2), (out["bolt"], "link", 2)])[1]
    return out


def bom_cells(asm, detail=True):
    """The Assembly BOM's rows [(index, name, qty)] (Assembly::BomObject in a BOM group)."""
    group = asm.newObject("Assembly::BomGroup", "BOMs")
    bom = group.newObject("Assembly::BomObject", "BOM")
    bom.columnsNames = ["Index", "Name", "Quantity"]
    bom.detailSubAssemblies = detail
    bom.detailParts = True
    bom.onlyParts = False
    asm.Document.recompute()
    rows, r = [], 2
    while r < 60:
        try:
            cells = [str(bom.get(f"{c}{r}")) for c in "ABC"]
        except Exception:
            break
        rows.append((cells[0], cells[1], int(float(cells[2]))))
        r += 1
    asm.Document.removeObject(bom.Name)
    asm.Document.removeObject(group.Name)
    return rows


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


class _Base(unittest.TestCase):
    def setUp(self):
        keep = os.environ.get("BW_BOM_DIR")
        self.tmp = keep or tempfile.mkdtemp(prefix="bw-bom-")
        if keep:
            shutil.rmtree(keep, ignore_errors=True)
            os.makedirs(keep)
        else:
            self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(close_all)
        self.src = os.path.join(self.tmp, "src")
        self.objs = build_spike(self.src)


class TestWalker(_Base):
    def test_walk_counts_items_not_bodies(self):
        rows = bb.walk(self.objs["top"].Document)
        self.assertEqual([(r["name"], r["qty"]) for r in rows],
                         [("riser.FCStd", 1), ("bracket.FCStd", 1), ("bracket.FCStd", 1),
                          ("bolt.FCStd", 1), ("bolt.FCStd", 1)])
        self.assertEqual(rows[0]["keys"][0], "riser")
        rows = bb.walk(self.objs["bracket"].Document)
        self.assertEqual([(r["name"], r["qty"]) for r in rows], [("plate.FCStd", 1), ("bolt.FCStd", 4)])

    def test_an_inserted_board_is_its_vault_item(self):
        """An inserted KiCad board (in-file App::Part with BoardSource) is keyed by its PartNumber; an ordinary
        in-file part still is not a vault item."""
        import Part

        doc = self.objs["bracket"].Document
        asm = doc.getObject("Assembly")
        for name, board in (("Board", True), ("Loose", False)):
            part = doc.addObject("App::Part", name)
            solid = doc.addObject("Part::Feature", name + "Solid")
            solid.Shape = Part.makeBox(10, 10, 1.6)
            part.addObject(solid)
            part.addProperty("App::PropertyString", "PartNumber", "BruceWorx")
            part.PartNumber = "BW-01-01099-001" if board else "BW-01-01098-001"
            if board:
                part.addProperty("App::PropertyFile", "BoardSource", "Board")
                part.BoardSource = "/tmp/hub.kicad_pcb"
            asm.addObject(part)
        doc.recompute()
        rows = {r["name"]: r for r in bb.walk(doc)}
        self.assertEqual(rows["Board (in this file)"]["keys"], ["BW-01-01099-001"])
        self.assertEqual(rows["Loose (in this file)"]["keys"], [])

    def test_hidden_array_elements_and_stale_link_elements(self):
        doc = self.objs["bracket"].Document
        array = doc.getObject("boltArray")
        array.setElementVisible("1", False)
        doc.recompute()
        self.assertEqual(list(array.VisibilityList), [True, False, True, True])
        self.assertEqual(bb.walk(doc)[-1]["qty"], 3)
        doc.removeObject(array.Name)
        doc.recompute()
        self.assertEqual([r["name"] for r in bb.walk(doc)], ["plate.FCStd"])  # left-over elements are not lines

    def test_indented_assembly_bom_keeps_levels_apart(self):
        rows = bom_cells(self.objs["top"], detail=True)
        names = [(i, n, q) for i, n, q in rows]
        self.assertIn(("2.2", "M5x12 bolt", 4), names)  # inside the bracket: 4, not 4 + 2
        self.assertIn(("3", "M5x12 bolt", 2), names)  # the top's own 2 bolts keep their row
        self.assertIn(("2", "Bracket assy", 2), names)
        top_only = bom_cells(self.objs["top"], detail=False)
        self.assertEqual([(n, q) for i, n, q in top_only if "." not in i],
                         [("Riser", 1), ("Bracket assy", 2), ("M5x12 bolt", 2)])


class TestWhereUsedAcceptance(_Base):
    """Acceptance (plan 56-pdm06): where-used of a bolt lists every assembly revision that uses it."""

    def test_where_used_of_a_bolt(self):
        from bruceworx import pdm_workspace as pw
        from bruceworx import release_check as rc
        from bruceworx import vault_setup

        close_all()
        folder = os.path.join(self.tmp, "vault")
        os.environ["BW_PDM_ACTOR"] = "accept@mac"
        setup = vault_setup.VaultSetup(name="Accept", folder=folder)
        setup.numbering.programs = [vault_setup.ProgramSetup("LE")]
        vault = vault_setup.create_vault(setup)
        cands = vault_setup.scan_for_import(self.src)
        vault_setup.plan_numbers(cands, folder, "LE")
        report = vault_setup.run_import(vault, cands, "LE")
        pn = {os.path.splitext(os.path.basename(p))[0]: k for p, k in report.imported}
        self.assertEqual(sorted(pn), ["bolt", "bracket", "plate", "riser", "rivet", "top"])
        work = pw.Workspace(vault)
        for name in ("bracket", "top"):
            work.get_latest(pn[name])
        for name in ("bolt", "rivet", "plate", "riser"):
            work.get_latest(pn[name])

        hook = bb.CheckinBom()

        class S:  # what the hook needs of a vault-client session
            pass

        s = S()
        s.vault, s.work = vault, work

        def check_in(name):
            key = pn[name]
            if vault.locks.get(key) != vault.actor:
                work.checkout(key, with_links=False)
            data = hook.prepare(s, key, {})
            work.checkin(key, f"{name} with its BOM")
            hook.record(s, key, data, {})
            close_all()

        check_in("bracket")
        check_in("top")
        self.assertEqual(vault.explode(pn["top"]), {pn["riser"]: 1, pn["bracket"]: 2, pn["plate"]: 2, pn["bolt"]: 10})
        for name in ("bolt", "rivet", "plate", "riser", "bracket", "top"):
            vault.submit(pn[name])
            vault.approve(pn[name], note="first release")
        # ECO: the bracket's bolts become rivets; the top takes the new bracket.
        vault.raise_ecr(None, "Bolts loosen: rivet the bracket", [pn["bracket"], pn["top"]])
        vault.decide_ecr("ECR-0001", True, "agreed")
        eco = vault.open_eco(None, ["ECR-0001"])
        path = work.checkout(pn["bracket"], with_links=False)
        doc = App.openDocument(path)
        asm = bb.top_assembly(doc)
        rivet = App.openDocument(work.path(pn["rivet"])).getObject("rivet")
        doc.removeObject("boltArray")
        for extra in [o.Name for o in doc.Objects if o.Name.startswith("boltArray")]:
            doc.removeObject(extra)
        link = asm.newObject("App::Link", "rivetArray")
        link.LinkedObject = rivet
        link.ElementCount = 4
        doc.recompute()
        doc.save()
        close_all()
        check_in("bracket")
        work.checkout(pn["top"], with_links=False)
        check_in("top")
        for name in ("bracket", "top"):
            vault.submit(pn[name])
        vault.release_eco(eco.number, note="rivets")

        uses = vault.where_used_revisions(pn["bolt"])
        self.assertEqual(sorted((u.part_number, u.revision, u.quantity) for u in uses if u.exact),
                         sorted([(pn["bracket"], "A", 4), (pn["top"], "A", 10), (pn["top"], "B", 2)]))
        # Bracket B (rivets) does not use it; the top keeps its two loose bolts in B.
        rows = rc.where_used(work, pn["bolt"])
        self.assertEqual({(r["part_number"], r["revision"], r["via"], r["released"]) for r in rows},
                         {(pn["bracket"], "A", "BOM", False), (pn["top"], "A", "BOM", False),
                          (pn["top"], "B", "BOM", True)})
        with self.assertRaisesRegex(Exception, "still used by"):
            vault.retire(pn["bolt"], "use rivets everywhere")  # top B (released) still holds 2
        self.assertEqual(vault.explode(pn["top"], "B"),
                         {pn["riser"]: 1, pn["bracket"]: 2, pn["plate"]: 2, pn["rivet"]: 8, pn["bolt"]: 2})
        self.assertEqual(len(vault.outbox()), 4)  # bracket A, top A, bracket B, top B
        self.result = {"part_numbers": pn, "uses": [(u.part_number, u.revision, u.quantity, u.exact, u.path)
                                                    for u in uses]}
        out = os.environ.get("BW_BOM_DIR")
        if out:
            import json

            with open(os.path.join(out, "acceptance.json"), "w", encoding="utf-8") as handle:
                json.dump(self.result, handle, indent=2)


if __name__ == "__main__":
    unittest.main()
