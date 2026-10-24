# SPDX-License-Identifier: LGPL-2.1-or-later
"""The release checklist (BW-PDM-05): the automatic lines, your ticks, and the record."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import release_check as rc  # noqa: E402
from bruceworx import pdm_workspace as pw  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402

PART = "BW-01-01001-001"
BOLT = "BW-01-01002-001"
DWG = "BW-03-01001-001"
ASSY = "BW-02-01001-001"
ME = "bruce@mac"


def make_fcstd(path, text="v1", links=(), title=None):
    """A minimal .FCStd. ``title``: the template's EditableTexts, as a dict."""
    xlinks = "".join(f'<XLink file="{name}" name="Body"/>' for name in links)
    template = ""
    if title is not None:
        items = "".join(f'<Item key="{k}" value="{v}"/>' for k, v in title.items())
        template = (
            '<ObjectData Count="1"><Object name="Template"><Properties Count="1">'
            f'<Property name="EditableTexts" type="App::PropertyMap" status="1"><Map count="{len(title)}">{items}'
            "</Map></Property></Properties></Object></ObjectData>"
        )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", f"<Document><Properties>{xlinks}</Properties>{template}</Document>")
        archive.writestr("payload.txt", text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-release-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Test", actor=ME)
        self.ws = pw.Workspace(self.vault)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def add(self, pn, kind="part", drawing_of=None, **file):
        self.vault.create_item(pn, kind, pn.lower(), drawing_of=drawing_of)
        self.vault.checkout(pn)
        self.vault.checkin(pn, make_fcstd(os.path.join(self.src, pn + ".FCStd"), **file), comment="first")

    def release(self, pn):
        self.vault.submit(pn)
        self.vault.approve(pn, note="released")

    def lines(self, pn, **kw):
        return {line.key: line for line in rc.check_item(self.ws, pn, **kw)}


class TitleBlockTests(Base):
    def test_reads_editable_texts(self):
        path = make_fcstd(os.path.join(self.tmp, "d.FCStd"), title={"part_number": PART, "scale": "1:1"})
        fields = rc.title_block_fields(path)
        self.assertEqual(fields, {"part_number": PART, "scale": "1:1"})
        self.assertEqual(rc.title_block_part_number(fields), ("part_number", PART))

    def test_stock_asme_field_and_no_template(self):
        self.assertEqual(rc.title_block_part_number({"drawing_number": " X-1 "}), ("drawing_number", "X-1"))
        self.assertEqual(rc.title_block_part_number({"scale": "1:1"}), (None, None))
        self.assertEqual(rc.title_block_fields(make_fcstd(os.path.join(self.tmp, "p.FCStd"))), {})
        self.assertEqual(rc.title_block_fields(os.path.join(self.tmp, "missing.FCStd")), {})

    def test_value_may_carry_the_revision(self):
        self.assertTrue(rc.shows_part_number(PART, [PART]))
        self.assertTrue(rc.shows_part_number(PART + "-02", [PART]))
        self.assertTrue(rc.shows_part_number(PART + "-A", [PART]))
        self.assertFalse(rc.shows_part_number(PART + "-O", [PART]))  # not a revision letter
        self.assertFalse(rc.shows_part_number(BOLT, [PART]))
        self.assertFalse(rc.shows_part_number("", [PART]))


class ItemLineTests(Base):
    def test_part_with_its_drawing_passes_automatic_lines(self):
        self.add(PART)
        self.add(DWG, "drawing", drawing_of=PART, title={"part_number": PART + "-01"})
        lines = self.lines(PART)
        self.assertTrue(lines[rc.CHECKED_IN].ok)
        self.assertTrue(lines[rc.DRAWING].ok, lines[rc.DRAWING].detail)
        self.assertTrue(lines[rc.COMPONENTS].skipped)
        self.assertFalse(lines[rc.REVIEWED].passed)  # your tick
        self.assertEqual(rc.blockers(list(lines.values()), note=""),
                         ["Tick “Drawing reviewed against the model”", "Write a release note"])
        rc.apply(list(lines.values()), reviewed=True)
        self.assertEqual(rc.blockers(list(lines.values()), note="First release"), [])

    def test_missing_or_wrong_drawing_blocks(self):
        self.add(PART)
        self.assertIn("no drawing", self.lines(PART)[rc.DRAWING].detail)
        self.add(DWG, "drawing", drawing_of=PART, title={"part_number": BOLT})
        line = self.lines(PART)[rc.DRAWING]
        self.assertFalse(line.ok)
        self.assertIn(BOLT, line.detail)

    def test_title_block_without_pn_field_is_left_to_you(self):
        self.add(PART)
        self.add(DWG, "drawing", drawing_of=PART, title={"scale": "1:1"})
        line = self.lines(PART)[rc.DRAWING]
        self.assertTrue(line.ok)
        self.assertIn("check it by eye", line.detail)

    def test_drawing_checked_out_blocks(self):
        self.add(PART)
        self.add(DWG, "drawing", drawing_of=PART, title={"part_number": PART})
        self.vault.checkout(DWG)
        line = self.lines(PART)[rc.DRAWING]
        self.assertFalse(line.ok)
        self.assertIn("checked out to you", line.detail)

    def test_drawing_item_checks_its_own_title_block(self):
        self.add(PART)
        self.add(DWG, "drawing", drawing_of=PART, title={"drawing_number": DWG})
        lines = self.lines(DWG)
        self.assertTrue(lines[rc.DRAWING].ok)
        self.assertEqual(lines[rc.DRAWING].text, "Title block shows this P/N")

    def test_local_changes_and_checkout_block_line_one(self):
        self.add(PART)
        self.ws.checkout(PART)
        self.assertIn("check it in first", self.lines(PART)[rc.CHECKED_IN].detail)
        make_fcstd(self.ws.path(PART), "edited")
        self.vault.undo_checkout(PART)  # lock gone, the edited copy stays
        self.assertIn("changes that are not in the vault", self.lines(PART)[rc.CHECKED_IN].detail)

    def test_nothing_checked_in(self):
        self.vault.create_item(PART)
        line = self.lines(PART)[rc.CHECKED_IN]
        self.assertEqual((line.ok, line.detail), (False, "nothing checked in yet"))

    def test_cots_and_no_drawing_assembly_skip_the_drawing(self):
        self.add(BOLT, "cots")
        lines = self.lines(BOLT)
        self.assertTrue(lines[rc.DRAWING].skipped and lines[rc.REVIEWED].skipped)
        self.add(ASSY, "assembly")
        self.assertFalse(self.lines(ASSY)[rc.DRAWING].passed)
        lines = self.lines(ASSY, no_drawing=True)
        self.assertTrue(lines[rc.DRAWING].skipped)
        self.assertEqual(rc.blockers(list(lines.values()), note="ok"), [])

    def test_no_drawing_only_applies_to_assemblies(self):
        self.add(PART)
        self.assertFalse(self.lines(PART, no_drawing=True)[rc.DRAWING].passed)


class ComponentTests(Base):
    def setUp(self):
        super().setUp()
        self.add(PART)
        self.add(BOLT, "cots")
        self.add(ASSY, "assembly", links=[PART + ".FCStd", BOLT + ".FCStd", "outside.FCStd"])

    def test_unreleased_components_are_listed_with_their_reason(self):
        self.release(BOLT)
        line = self.lines(ASSY, no_drawing=True)[rc.COMPONENTS]
        self.assertFalse(line.ok)
        self.assertEqual(line.entries, [[PART, "WIP 01"], ["outside", "not in the vault"]])
        self.assertEqual(rc.components_of(self.ws, ASSY), [PART, BOLT, "outside"])
        lines = rc.apply(rc.check_item(self.ws, ASSY, no_drawing=True), waiver="")
        self.assertIn("Give the reason for releasing with unreleased components", rc.blockers(lines, note="n"))
        lines = rc.apply(lines, waiver="Plate releases next week")
        self.assertEqual(rc.blockers(lines, note="n"), [])
        recorded = rc.record(lines, no_drawing=True, version="abc")
        self.assertEqual(recorded[rc.COMPONENTS]["reason"], "Plate releases next week")
        self.assertEqual(recorded[rc.COMPONENTS]["list"][0], [PART, "WIP 01"])
        self.assertEqual((recorded["no_drawing"], recorded["version"]), (True, "abc"))
        self.assertEqual(recorded[rc.DRAWING], {"skipped": True, "detail": "marked as having no drawing"})

    def test_items_of_the_same_release_count_as_released(self):
        self.vault.set_bom(ASSY, [(PART, 2), (BOLT, 4)], missing=[{"name": "outside", "qty": 1}])
        line = self.lines(ASSY, batch=(PART, BOLT, "outside"), no_drawing=True)[rc.COMPONENTS]
        self.assertEqual(line.entries, [["outside", "not in the vault"]])  # files outside are never "in the batch"



class WhereUsedTests(Base):
    TOP = "BW-02-01009-001"

    def setUp(self):
        super().setUp()
        self.add(PART)
        self.add(ASSY, "assembly", links=[PART + ".FCStd"])
        self.add(self.TOP, "assembly", links=["sub/" + ASSY + ".FCStd"])

    def test_file_links_directly_or_through_a_sub_assembly(self):
        self.release(ASSY)
        rows = rc.where_used(self.ws, PART)  # no BOM recorded anywhere: the links are all there is
        self.assertEqual([(r["part_number"], r["released"], r["via"]) for r in rows],
                         [(ASSY, True, "file link"), (self.TOP, False, "file link")])  # released first
        self.assertEqual(rows[0]["revision"], "A")
        self.assertEqual(rc.where_used(self.ws, self.TOP), [])
        self.assertEqual(rc.retire_blockers(self.ws, PART),
                         [f"{ASSY} rev A (released) still uses it (file link); change that item through an ECR first"])
        self.assertEqual(rc.retire_blockers(self.ws, self.TOP), [])

    def test_a_recorded_bom_replaces_the_links(self):
        self.add(BOLT, "cots")
        self.vault.set_bom(ASSY, [(BOLT, 4)])  # the walker found no PART: its link is stale
        self.release(ASSY)
        self.assertEqual(rc.where_used(self.ws, PART), [])
        rows = rc.where_used(self.ws, BOLT)
        self.assertEqual([(r["part_number"], r["revision"], r["via"], r["quantity"]) for r in rows],
                         [(ASSY, "A", "BOM", 4), (self.TOP, "01", "file link", 0)])
        self.assertEqual(rows[0]["path"], [f"{ASSY}-A", f"{BOLT}-01"])
        self.assertEqual(rc.components_of(self.ws, ASSY), [BOLT])

    def test_an_obsolete_user_does_not_block(self):
        self.release(ASSY)
        self.vault.retire(ASSY, "replaced")
        self.assertEqual([(r["part_number"], r["state"], r["released"]) for r in rc.where_used(self.ws, PART)],
                         [(ASSY, "Obsolete", False), (self.TOP, "WIP", False)])


class ReadOnlyTests(Base):
    def test_released_work_copies_are_made_read_only(self):
        self.add(PART)
        self.add(BOLT)
        path = self.ws.checkout(PART)
        self.ws.checkin(PART, "done")
        os.chmod(path, 0o644)  # made writable behind the vault's back
        self.release(PART)
        self.ws.checkout(BOLT)  # in work, checked out: left alone
        self.assertEqual(rc.lock_released(self.ws, [PART, BOLT, "missing"]), [path])
        self.assertFalse(os.access(path, os.W_OK))
        self.assertTrue(os.access(self.ws.path(BOLT), os.W_OK))
        self.assertEqual(rc.lock_released(self.ws, [PART]), [])  # already read-only

if __name__ == "__main__":
    unittest.main()
