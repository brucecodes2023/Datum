# SPDX-License-Identifier: LGPL-2.1-or-later
"""Links that resolve through the vault (BW-PDM-04, plan section 7.3, "Links")."""

import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_vault_setup import _Tmp, digest, make_fcstd  # noqa: E402

from heuretes_freecad import pdm_workspace as ws  # noqa: E402
from heuretes_freecad import vault_setup as vs  # noqa: E402

BODY = [("Body", "PartDesign::Body", {})]
ASSY = [("Assembly", "Assembly::AssemblyObject", {})]


class LinkTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.vault = vs.create_vault(vs.VaultSetup(name="Links", folder=self.path("vault")), actor="bruce@mac")
        self.src = self.path("src")
        make_fcstd(os.path.join(self.src, "plate.FCStd"), BODY)
        make_fcstd(os.path.join(self.src, "bolt.FCStd"), BODY)
        # Named so it sorts before its parts: import must still number the parts first.
        make_fcstd(os.path.join(self.src, "frame.FCStd"), ASSY, links=("plate.FCStd", "bolt.FCStd"))

    def test_rewrite_keeps_every_other_entry(self):
        path = os.path.join(self.src, "frame.FCStd")
        with zipfile.ZipFile(path) as archive:
            before = {n: archive.read(n) for n in archive.namelist() if n != "Document.xml"}
        changed = ws.rewrite_links(path, lambda old: "X-" + old if old == "plate.FCStd" else None)
        self.assertEqual(changed, 1)
        self.assertEqual(ws.linked_files(path), ["X-plate.FCStd", "bolt.FCStd"])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual({n: archive.read(n) for n in archive.namelist() if n != "Document.xml"}, before)

    def test_clear_link_errors(self):
        path = os.path.join(self.src, "saved.FCStd")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", (
                '<Document><Objects Count="3">'
                '<Object type="App::Link" name="A" id="1" Touched="1" Invalid="1" '
                'Error="Link not restored&#10;Linked object: Part&#10;Linked file: a.FCStd" />'
                '<Object type="Part::Box" name="B" id="2" Invalid="1" Error="Shape is null" />'
                '<Object type="App::Link" name="C" id="3" />'
                '</Objects></Document>'))
            archive.writestr("GuiDocument.xml", "<GuiDocument/>")
        self.assertEqual(ws.clear_link_errors(path), 1)
        with zipfile.ZipFile(path) as archive:
            text = archive.read("Document.xml").decode()
        self.assertIn('<Object type="App::Link" name="A" id="1" Touched="1" />', text)
        self.assertIn('Error="Shape is null"', text)  # other errors stay
        self.assertEqual(ws.clear_link_errors(path), 0)

    def test_import_rewrites_assembly_links_to_part_numbers(self):
        candidates = vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW")
        report = vs.run_import(self.vault, candidates, "BW")
        self.assertEqual(report.failed, [])
        numbers = {os.path.basename(p): key for p, key in report.imported}
        self.vault.refresh()
        work = ws.Workspace(self.vault)
        frame = work.get_latest(numbers["frame.FCStd"])
        self.assertEqual(ws.linked_files(frame),
                         [numbers["plate.FCStd"] + ".FCStd", numbers["bolt.FCStd"] + ".FCStd"])
        # The originals are untouched.
        self.assertEqual(ws.linked_files(os.path.join(self.src, "frame.FCStd")), ["plate.FCStd", "bolt.FCStd"])

    def test_older_revision_of_an_assembly_links_to_the_work_copies(self):
        candidates = vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW")
        numbers = {os.path.basename(p): key for p, key in vs.run_import(self.vault, candidates, "BW").imported}
        self.vault.refresh()
        work = ws.Workspace(self.vault)
        key = numbers["frame.FCStd"]
        first = self.vault.versions[key][0].sha256
        path = work.get_revision(key, first)
        self.assertEqual(ws.linked_files(path),
                         ["../" + numbers["plate.FCStd"] + ".FCStd", "../" + numbers["bolt.FCStd"] + ".FCStd"])


class CheckInLinkTests(_Tmp):
    """Check-in: links that point outside the work folder (plan section 4)."""

    def setUp(self):
        super().setUp()
        self.vault = vs.create_vault(vs.VaultSetup(name="Links", folder=self.path("vault")), actor="bruce@mac")
        self.src = self.path("src")
        make_fcstd(os.path.join(self.src, "plate.FCStd"), BODY)
        make_fcstd(os.path.join(self.src, "frame.FCStd"), ASSY, links=("plate.FCStd",))
        report = vs.run_import(self.vault, vs.plan_numbers(vs.scan_for_import(self.src), self.vault.root, "BW"), "BW")
        self.numbers = {os.path.basename(p): key for p, key in report.imported}
        self.vault.refresh()
        self.work = ws.Workspace(self.vault)
        self.frame = self.numbers["frame.FCStd"]
        self.plate = self.numbers["plate.FCStd"]
        self.work.checkout(self.frame)
        self.calls = []

    def importer(self, paths, known):
        self.calls.append(list(paths))
        return vs.import_linked_files(self.vault, paths, vs.program_of(self.frame, self.vault.root), known)[1]

    def set_links(self, *links):
        """The user's edit: the assembly now links these files (as FreeCAD would write them)."""
        make_fcstd(self.work.path(self.frame), ASSY, links=links, salt="edit")

    def test_plain_part_number_links_need_nothing(self):
        self.assertEqual(self.work.link_fixes([self.frame]), [])
        self.assertEqual(self.work.fix_links([self.frame], self.importer), (0, []))
        self.assertEqual(self.calls, [])

    def test_outside_part_is_imported_and_the_link_rewritten(self):
        washer = make_fcstd(self.path("elsewhere", "washer.FCStd"), BODY)
        before = digest(washer)
        self.set_links(self.plate + ".FCStd", washer)
        fixes = self.work.link_fixes([self.frame])
        self.assertEqual([(f.link, f.target, f.needs_import) for f in fixes], [(washer, "", True)])
        changed, left = self.work.fix_links([self.frame], self.importer)
        self.assertEqual((changed, left), (1, []))
        new = next(pn for pn in self.vault.model.items if pn not in self.numbers.values())
        self.assertTrue(new.startswith("BW-01-"))
        self.assertEqual(ws.linked_files(self.work.path(self.frame)), [self.plate + ".FCStd", new + ".FCStd"])
        # The new part has a read-only work copy beside the assembly, so the link resolves.
        self.assertTrue(os.path.exists(self.work.path(new)))
        self.assertFalse(os.access(self.work.path(new), os.W_OK))
        self.assertEqual(digest(washer), before)  # the original is only read
        self.work.checkin(self.frame, "washer added")
        self.assertEqual(self.work.status(self.frame).code, ws.UP_TO_DATE)

    def test_vault_file_linked_by_a_path_is_pointed_at_its_number(self):
        self.set_links("../sam@mac/" + self.plate + ".FCStd", os.path.join(self.src, "plate.FCStd"))
        fixes = self.work.link_fixes([self.frame])
        self.assertEqual([f.target for f in fixes], [self.plate, self.plate])
        changed, left = self.work.fix_links([self.frame], self.importer)
        self.assertEqual((changed, left, self.calls), (2, [], []))
        self.assertEqual(ws.linked_files(self.work.path(self.frame)), [self.plate + ".FCStd"])

    def test_same_content_as_a_vault_version_is_not_imported_again(self):
        copy = self.path("copies", "plate copy.FCStd")
        os.makedirs(os.path.dirname(copy))
        with open(os.path.join(self.src, "plate.FCStd"), "rb") as a, open(copy, "wb") as b:
            b.write(a.read())
        self.set_links(copy)
        self.assertEqual(self.work.fix_links([self.frame], self.importer), (1, []))
        self.assertEqual(ws.linked_files(self.work.path(self.frame)), [self.plate + ".FCStd"])

    def test_missing_file_and_no_importer_leave_links_as_they_are(self):
        washer = make_fcstd(self.path("elsewhere", "washer.FCStd"), BODY)
        self.set_links("gone/Missing.FCStd", washer)
        changed, left = self.work.fix_links([self.frame])
        self.assertEqual(changed, 0)
        self.assertEqual([(f.link, f.exists) for f in left], [("gone/Missing.FCStd", False), (washer, True)])
        changed, left = self.work.fix_links([self.frame], self.importer)
        self.assertEqual((changed, [f.link for f in left]), (1, ["gone/Missing.FCStd"]))
        self.assertEqual(self.calls, [[os.path.realpath(washer)]])

    def test_outside_sub_assembly_brings_its_own_parts(self):
        nut = make_fcstd(self.path("elsewhere", "nut.FCStd"), BODY)
        sub = make_fcstd(self.path("elsewhere", "clamp.FCStd"), ASSY, links=("nut.FCStd", "../src/plate.FCStd"))
        self.set_links(sub)
        changed, left = self.work.fix_links([self.frame], self.importer)
        self.assertEqual((changed, left), (1, []))
        by_label = {self.vault.model.items[pn].description: pn for pn in self.vault.model.items}
        clamp, nut_pn = by_label["clamp"], by_label["nut"]
        self.assertEqual(ws.linked_files(self.work.path(self.frame)), [clamp + ".FCStd"])
        # The sub-assembly's vault copy links the new nut and the plate already in the vault.
        self.assertEqual(ws.linked_files(self.work.get_latest(clamp)), [nut_pn + ".FCStd", self.plate + ".FCStd"])
        self.assertTrue(os.path.exists(nut))

    def test_candidates_skip_known_and_vault_files(self):
        washer = make_fcstd(self.path("elsewhere", "washer.FCStd"), BODY, links=("../src/plate.FCStd",))
        known = self.work.known_files()
        found = vs.candidates_for_files([washer, self.work.path(self.plate)], known, self.vault.root)
        self.assertEqual([os.path.basename(c.path) for c in found], ["washer.FCStd"])

    def test_program_of(self):
        self.assertEqual(vs.program_of(self.frame, self.vault.root), "BW")
        self.assertEqual(vs.program_of("ZZ-01-01001-001", self.vault.root), "BW")
        self.assertEqual(vs.program_of("odd name", self.vault.root), "BW")


if __name__ == "__main__":
    unittest.main()
