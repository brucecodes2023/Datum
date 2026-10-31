# SPDX-License-Identifier: LGPL-2.1-or-later
"""BW-PDM-02 contract: every transition of the spec, and every refusal that guards it.

TRANSITIONS is the table in the plan (section "PDM-02 spec, as built"): each row
names the journal operation and the endpoint in docs/pdm-openapi.yaml. The
lifecycle rows run against the reference model (pdm.Vault) and the folder vault;
the file and lock rows only against the folder vault (the model has no files).
The last class checks that the table, the OpenAPI file and the folder vault's
journal operations name the same operations.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm  # noqa: E402
from bruceworx.pdm import IN_REVIEW, OBSOLETE, RELEASED, WIP, LifecycleError  # noqa: E402
from bruceworx.pdm_folder import FILE_OPS, LIFECYCLE_OPS, FolderVault  # noqa: E402

OPENAPI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "pdm-openapi.yaml")

PART = "LE-01-01001-001"
DWG = "LE-03-01001-001"
ASSY = "LE-02-02001-001"
# The where-used figure of the PDM-06 plan: a pedestal, its bracket, and hardware.
PED = "LE-02-02010-001"
BRK = "LE-02-02011-001"
RISER = "LE-01-01010-001"
PLATE = "LE-01-01011-001"
BOLT = "LE-09-09001-001"
RIVET = "LE-09-09002-001"

# id, journal operation, endpoint, from, to
TRANSITIONS = (
    ("T1", "create_item", "POST /items", "(new)", "rev 01, WIP"),
    ("T2", "submit", "POST /items/{pn}/submit", "WIP", "In Review"),
    ("T3", "reject", "POST /items/{pn}/reject", "In Review, rev 01, 02, ...", "next number in WIP; old one superseded"),
    ("T4", "reject", "POST /items/{pn}/reject", "In Review, ECO letter", "WIP, same letter"),
    ("T5", "approve", "POST /items/{pn}/approve", "In Review, pre-release", "Released as A; file frozen"),
    ("T6", "raise_ecr", "POST /ecrs", "released items", "ECR Open"),
    ("T7", "decide_ecr", "POST /ecrs/{number}/decision", "ECR Open", "ECR Approved"),
    ("T8", "decide_ecr", "POST /ecrs/{number}/decision", "ECR Open", "ECR Rejected"),
    ("T9", "open_eco", "POST /ecos", "Released, approved ECR", "next letter in WIP, for the item and its drawings"),
    ("T10", "submit", "POST /items/{pn}/submit", "WIP, ECO letter", "In Review"),
    ("T11", "release_eco", "POST /ecos/{number}/release", "every ECO revision In Review",
     "all Released; older letters superseded; ECO Released"),
    ("T12", "retire", "POST /items/{pn}/retire", "Released, unused", "Obsolete"),
    ("F1", "checkout", "POST /items/{pn}/checkout", "WIP, unlocked", "locked by me"),
    ("F2", "checkin", "POST /items/{pn}/checkin", "locked by me", "new version; unlocked (or kept)"),
    ("F3", "undo_checkout", "POST /items/{pn}/undo-checkout", "locked by me", "unlocked"),
    ("F4", "undo_checkout", "POST /items/{pn}/undo-checkout", "locked by someone else, force", "unlocked"),
    ("B1", "set_bom", "PUT /items/{pn}/revisions/{rev}/bom", "WIP revision", "its BOM replaced"),
    ("T13", "set_item_type", "PUT /items/{pn}/type", "part or cots, not Obsolete", "cots or part"),
    ("T15", "set_category", "PUT /items/{pn}/category", "any item, any state", "category set or cleared"),
    ("T14", "add_configuration", "POST /items/{pn}/configurations", "part or assembly, not Obsolete",
     "configuration item sharing its revisions"),
    ("P1", "create_project", "POST /projects", "(new), an assembly item", "project Open, naming its top"),
    ("P2", "update_project", "PATCH /projects/{code}", "project", "renamed, re-pointed, or Closed"),
)
# Operations in the OpenAPI file that are not journal transitions (reads, and Ops' side).
READS = {"status", "allocate_part_number", "bom_of", "where_used", "receive_released_bom"}


class _Lifecycle:
    """Lifecycle rows T1..T12 and B1, with their guards. Subclasses give make()."""

    def make(self):
        raise NotImplementedError

    def setUp(self):
        self.v = self.make()

    def released(self, pn=PART, kind="part", **kw):
        if pn not in self.v.items:
            self.v.create_item(pn, kind, **kw)
        self.v.submit(pn)
        return self.v.approve(pn, note="released")

    def eco(self, items, n=1):
        self.v.raise_ecr(f"ECR-{n:04d}", "change", items)
        self.v.decide_ecr(f"ECR-{n:04d}", True, "agreed")
        return self.v.open_eco(f"ECO-{n:04d}", [f"ECR-{n:04d}"])

    # --- transitions ------------------------------------------------------

    def test_T1_create(self):
        item = self.v.create_item(PART, "part", "Base plate")
        self.assertEqual((item.latest.code, item.latest.state, item.state), ("01", WIP, WIP))
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        self.assertEqual(self.v.items[DWG].drawing_of, PART)

    def test_T2_submit(self):
        self.v.create_item(PART)
        self.assertEqual(self.v.submit(PART).state, IN_REVIEW)

    def test_T3_reject_pre_release(self):
        self.v.create_item(PART)
        self.v.submit(PART)
        nxt = self.v.reject(PART, "too thin")
        item = self.v.items[PART]
        self.assertEqual((nxt.code, nxt.state), ("02", WIP))
        self.assertTrue(item.revisions[0].superseded)
        self.assertEqual(item.revisions[0].state, IN_REVIEW)  # kept as it was, flagged
        self.assertEqual(item.revisions[0].record("reject")["reason"], "too thin")

    def test_T4_reject_eco_letter(self):
        self.released()
        self.eco([PART])
        self.v.submit(PART)
        rev = self.v.reject(PART, "wrong material")
        self.assertEqual((rev.code, rev.state, rev.superseded), ("B", WIP, False))
        self.assertEqual(len(self.v.items[PART].revisions), 2)

    def test_T5_approve(self):
        self.v.create_item(PART)
        self.v.submit(PART)
        rev = self.v.approve(PART, note="first release")
        self.assertEqual((rev.code, rev.state, rev.editable), ("A", RELEASED, False))
        self.assertEqual(self.v.items[PART].released.code, "A")
        self.assertEqual(rev.record("approve")["note"], "first release")

    def test_T6_T7_raise_and_approve_ecr(self):
        self.released()
        ecr = self.v.raise_ecr("ECR-0001", "M5 clearance", [PART])
        self.assertEqual(ecr.status, "Open")
        self.assertEqual(self.v.decide_ecr("ECR-0001", True, "agreed").status, "Approved")

    def test_T8_reject_ecr(self):
        self.released()
        self.v.raise_ecr("ECR-0001", "M5 clearance", [PART])
        ecr = self.v.decide_ecr("ECR-0001", False, "works as is")
        self.assertEqual((ecr.status, ecr.reason), ("Rejected", "works as is"))
        with self.assertRaises(LifecycleError):
            self.v.open_eco("ECO-0001", ["ECR-0001"])

    def test_T9_open_eco_opens_part_and_drawing(self):
        self.v.create_item(PART)
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        self.released(PART)
        self.released(DWG)
        eco = self.eco([PART])
        self.assertEqual(eco.items, [PART, DWG])
        for pn in (PART, DWG):
            latest = self.v.items[pn].latest
            self.assertEqual((latest.code, latest.state, latest.eco), ("B", WIP, "ECO-0001"))
            self.assertEqual(self.v.items[pn].released.code, "A")  # A stays current until B releases

    def test_T10_T11_submit_and_release_eco(self):
        self.released()
        eco = self.eco([PART])
        self.assertEqual(self.v.submit(PART).state, IN_REVIEW)
        self.v.release_eco(eco.number, note="thicker plate")
        item = self.v.items[PART]
        self.assertEqual((item.released.code, item.latest.state), ("B", RELEASED))
        self.assertTrue(item.revisions[0].superseded)
        self.assertEqual(self.v.ecos[eco.number].status, "Released")

    def test_T12_retire(self):
        self.released()
        self.v.retire(PART, "superseded by LE-01-01002-001")
        item = self.v.items[PART]
        self.assertEqual((item.state, item.obsolete, item.revisions[-1].state), (OBSOLETE, True, OBSOLETE))
        self.assertIsNone(item.released)
        with self.assertRaises(LifecycleError):
            self.v.retire(PART, "again")
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr("ECR-0009", "change it anyway", [PART])  # obsolete items no longer change
        for refused in (self.v.submit, self.v.approve, self.v.reject):
            with self.assertRaises(LifecycleError):
                refused(PART)

    def test_T13_item_type(self):
        self.v.create_item(PART, description="Socket head cap screw M3x40 (COTS)")
        self.v.set_item_type(PART, "cots")
        self.assertEqual(self.v.items[PART].item_type, "cots")
        self.assertIn("item type: part -> cots", self.v.items[PART].latest.history)
        with self.assertRaises(LifecycleError):
            self.v.set_item_type(PART, "cots")  # nothing to change
        self.released()  # a released item may change type too: its file is not touched
        self.v.set_item_type(PART, "part")
        self.assertEqual(self.v.items[PART].item_type, "part")
        with self.assertRaises(ValueError):
            self.v.set_item_type(PART, "assembly")
        self.v.create_item(ASSY, "assembly")
        with self.assertRaises(LifecycleError):
            self.v.set_item_type(ASSY, "cots")
        self.v.retire(PART, "replaced")
        with self.assertRaises(LifecycleError):
            self.v.set_item_type(PART, "cots")

    def test_T15_category(self):
        self.v.create_item(PART, description="Housing")
        self.assertEqual(self.v.items[PART].category, "")
        self.v.set_category(PART, "  Connectors  ")
        self.assertEqual(self.v.items[PART].category, "Connectors")
        self.released()  # a released item is filed too; its state is untouched
        self.v.set_category(PART, "Terminals & seals")
        self.assertEqual((self.v.items[PART].category, self.v.items[PART].state), ("Terminals & seals", RELEASED))
        self.v.set_category(PART, "")
        self.assertEqual(self.v.items[PART].category, "")
        with self.assertRaises(LifecycleError):
            self.v.set_category("LE-01-09999-001", "Connectors")  # unknown item
        with self.assertRaises(ValueError):
            self.v.set_category(PART, "x" * 61)
        self.v.set_category(PART, "Connectors")
        self.v.retire(PART, "replaced")  # obsolete items can still be filed
        self.v.set_category(PART, "Retired")
        self.assertEqual(self.v.items[PART].category, "Retired")

    def test_T14_configuration(self):
        self.v.create_item(PART, description="Bracket")
        short, long_ = PART + "-S", PART + "-L"
        self.v.add_configuration(short, PART, "Short", "Bracket 50")
        self.v.add_configuration(long_, PART, "Long", "Bracket 120")
        item = self.v.items[short]
        self.assertEqual((item.configuration_of, item.configuration, item.item_type, item.description),
                         (PART, "Short", "part", "Bracket 50"))
        self.assertEqual([c.part_number for c in self.v.configurations_of(PART)], [short, long_])
        self.v.add_configuration(short, PART, "Short 50")  # a renamed row: same item, new name
        self.assertEqual((self.v.items[short].configuration, self.v.items[short].description), ("Short 50", "Bracket 50"))
        for refused in (lambda: self.v.add_configuration(PART, PART, "Self"),
                        lambda: self.v.add_configuration(short, long_, "Nested"),
                        lambda: self.v.add_configuration(PART + "-X", PART, ""),
                        lambda: self.v.add_configuration(PART + "-X", "LE-00-00000-000", "Nowhere"),
                        lambda: self.v.submit(short), lambda: self.v.set_item_type(short, "cots")):
            with self.assertRaises(LifecycleError):
                refused()
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(short, 1), (long_, 2)])
        self.released()  # the file's release is its configurations' release
        self.assertEqual((self.v.items[short].state, self.v.items[short].released.code), (RELEASED, "A"))
        self.assertEqual(self.v.explode(ASSY), {short: 1, long_: 2})
        self.v.submit(ASSY)
        self.v.approve(ASSY, note="released")
        with self.assertRaises(LifecycleError):
            self.v.retire(PART, "replaced")  # the assembly uses its Long configuration
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr(None, "longer", [long_])  # change the file's item
        self.v.create_item(DWG, "drawing", drawing_of=PART)

    def test_T14_retire_with_configurations(self):
        self.v.create_item(PART)
        self.v.add_configuration(PART + "-S", PART, "Short")
        self.released()
        self.v.retire(PART, "replaced")
        self.assertEqual(self.v.items[PART + "-S"].state, OBSOLETE)
        with self.assertRaises(LifecycleError):
            self.v.add_configuration(PART + "-L", PART, "Long")  # obsolete

    def test_P1_create_project(self):
        self.v.create_item(ASSY, "assembly", "Cart")
        project = self.v.create_project("CART", "Shop cart", "le", ASSY, units="in", drawing_profile="in")
        self.assertEqual((project.code, project.program, project.top, project.units, project.status),
                         ("CART", "LE", ASSY, "in", "Open"))
        self.assertEqual(project.records[0]["op"], "create_project")
        self.assertEqual(self.v.projects_of(ASSY), [project])
        with self.assertRaisesRegex(LifecycleError, "already exists"):
            self.v.create_project("CART", "Again", "LE", ASSY)
        self.v.create_item(PART, "part")
        with self.assertRaisesRegex(LifecycleError, "an assembly"):
            self.v.create_project("P2", "Part top", "LE", PART)
        with self.assertRaisesRegex(LifecycleError, "unknown item"):
            self.v.create_project("P3", "No top", "LE", "LE-02-09999-001")
        with self.assertRaisesRegex(LifecycleError, "project name"):
            self.v.create_project("P4", "", "LE", ASSY)
        with self.assertRaisesRegex(LifecycleError, "program"):
            self.v.create_project("P5", "No program", "", ASSY)
        with self.assertRaisesRegex(ValueError, "units"):
            self.v.create_project("P6", "Yards", "LE", ASSY, units="yd")
        with self.assertRaisesRegex(LifecycleError, "code"):
            self.v.create_project("", "No code", "LE", ASSY)

    def test_P2_update_project(self):
        self.v.create_item(ASSY, "assembly", "Cart")
        self.v.create_item(PED, "assembly", "Pedestal")
        self.v.create_project("CART", "Shop cart", "LE", ASSY)
        project = self.v.update_project("CART", name="Shop cart Mk II", top=PED, units="in")
        self.assertEqual((project.name, project.top, project.units), ("Shop cart Mk II", PED, "in"))
        self.assertEqual(project.records[-1]["op"], "update_project")
        self.assertEqual(self.v.update_project("CART", status="Closed").status, "Closed")
        self.assertEqual(self.v.projects_of(PED), [])  # closed projects are not offered
        self.assertEqual(self.v.update_project("CART", status="Open").status, "Open")
        with self.assertRaisesRegex(LifecycleError, "unknown project"):
            self.v.update_project("NOPE", name="x")
        with self.assertRaisesRegex(LifecycleError, "nothing to change"):
            self.v.update_project("CART")
        with self.assertRaisesRegex(ValueError, "Open or Closed"):
            self.v.update_project("CART", status="Done")
        with self.assertRaisesRegex(LifecycleError, "an assembly"):
            self.v.create_item(PART, "part")
            self.v.update_project("CART", top=PART)

    def test_B1_bom(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 4)])
        self.assertEqual(self.v.explode(ASSY), {PART: 4})
        self.assertEqual(self.v.where_used(PART), {ASSY})

    # --- guards: each refusal changes nothing ------------------------------

    def test_create_guards(self):
        self.v.create_item(PART)
        with self.assertRaises(LifecycleError):
            self.v.create_item(PART)
        with self.assertRaises(LifecycleError):
            self.v.create_item(DWG, "drawing")  # names no part
        with self.assertRaises(LifecycleError):
            self.v.create_item(DWG, "drawing", drawing_of="LE-01-09999-001")
        self.assertEqual(sorted(self.v.items), [PART])

    def test_review_guards(self):
        self.v.create_item(PART)
        for refused in (self.v.approve, self.v.reject):  # not in review
            with self.assertRaises(LifecycleError):
                refused(PART)
        self.v.submit(PART)
        with self.assertRaises(LifecycleError):
            self.v.submit(PART)  # already in review
        self.v.approve(PART, note="released")
        for refused in (self.v.submit, self.v.approve, self.v.reject):  # released: read-only
            with self.assertRaises(LifecycleError):
                refused(PART)
        self.assertEqual([r.code for r in self.v.items[PART].revisions], ["A"])

    def test_eco_revision_releases_only_with_its_eco(self):
        self.released()
        self.eco([PART])
        self.v.submit(PART)
        with self.assertRaises(LifecycleError):
            self.v.approve(PART, note="released")
        self.assertEqual(self.v.items[PART].latest.state, IN_REVIEW)

    def test_ecr_guards(self):
        self.v.create_item(PART)
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr("ECR-0001", "change", [PART])  # not released
        self.assertEqual(dict(self.v.ecrs), {})
        self.released()
        self.v.raise_ecr("ECR-0001", "change", [PART])
        with self.assertRaises(LifecycleError):
            self.v.open_eco("ECO-0001", ["ECR-0001"])  # still Open
        self.v.decide_ecr("ECR-0001", True, "agreed")
        with self.assertRaises(LifecycleError):
            self.v.decide_ecr("ECR-0001", False, "agreed")  # decided once
        self.assertEqual(self.v.ecrs["ECR-0001"].status, "Approved")

    def test_open_eco_is_all_or_nothing(self):
        self.v.create_item(PART)
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        self.v.create_item(ASSY, "assembly")
        for pn in (PART, DWG, ASSY):
            self.released(pn)
        self.eco([PART])  # PART and DWG now have B in work
        self.v.raise_ecr("ECR-0002", "also the assembly", [ASSY, PART])
        self.v.decide_ecr("ECR-0002", True, "agreed")
        with self.assertRaises(LifecycleError):
            self.v.open_eco("ECO-0002", ["ECR-0002"])
        self.assertEqual([r.code for r in self.v.items[ASSY].revisions], ["A"])
        self.assertNotIn("ECO-0002", self.v.ecos)

    def test_release_eco_guards(self):
        self.v.create_item(PART)
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        self.released(PART)
        self.released(DWG)
        eco = self.eco([PART])
        self.v.submit(PART)
        with self.assertRaises(LifecycleError):
            self.v.release_eco(eco.number, note="released")  # drawing not in review
        self.assertEqual(self.v.items[PART].released.code, "A")
        self.v.submit(DWG)
        self.v.release_eco(eco.number, note="released")
        with self.assertRaises(LifecycleError):
            self.v.release_eco(eco.number, note="released")  # released once

    def test_retire_guards(self):
        self.v.create_item(PART)
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        with self.assertRaises(LifecycleError):
            self.v.retire(PART, "superseded by LE-01-01002-001")  # never released
        self.released()
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 1)])
        self.released(ASSY)
        with self.assertRaises(LifecycleError):
            self.v.retire(PART, "superseded by LE-01-01002-001")  # a released assembly uses it
        self.assertEqual(self.v.items[PART].state, RELEASED)
        self.released(DWG)
        self.eco([DWG])
        with self.assertRaises(LifecycleError):
            self.v.retire(DWG, "not needed")  # its next letter is in work under an ECO
        self.assertEqual(self.v.items[DWG].latest.state, WIP)

    def test_unreleased_user_does_not_block_retire(self):
        self.released()
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 1)])
        self.v.retire(PART, "superseded by LE-01-01002-001")
        self.assertEqual(self.v.items[PART].state, OBSOLETE)

    def test_bom_guards(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 1)])
        with self.assertRaises(LifecycleError):
            self.v.set_bom(ASSY, [("LE-01-09999-001", 1)])
        with self.assertRaises(ValueError):
            self.v.set_bom(ASSY, [(PART, 0)])
        with self.assertRaises(LifecycleError):
            self.v.set_bom(PART, [(ASSY, 1)])  # cycle
        self.assertEqual(self.v.bom[ASSY], [(PART, 1)])
        self.assertNotIn(PART, self.v.bom)


    # --- PDM-02 A3, A9: required texts and refusals are rules of the engine ---

    def test_every_decision_needs_its_text(self):
        self.v.create_item(PART)
        self.v.submit(PART)
        for refused in (lambda: self.v.reject(PART, "  "), lambda: self.v.approve(PART, note="")):
            with self.assertRaisesRegex(LifecycleError, "required"):
                refused()
        self.assertEqual(self.v.items[PART].latest.state, IN_REVIEW)
        self.v.approve(PART, note="first release")
        self.v.raise_ecr("ECR-0001", "M5 clearance", [PART])
        with self.assertRaisesRegex(LifecycleError, "reason for the decision"):
            self.v.decide_ecr("ECR-0001", True, "")
        self.assertEqual(self.v.ecrs["ECR-0001"].status, "Open")
        self.v.decide_ecr("ECR-0001", True, "agreed")
        eco = self.v.open_eco(None, ["ECR-0001"])
        self.v.submit(PART)
        with self.assertRaisesRegex(LifecycleError, "release note"):
            self.v.release_eco(eco.number)
        self.assertEqual(self.v.ecos[eco.number].status, "Open")
        self.v.release_eco(eco.number, note="thicker")
        with self.assertRaisesRegex(LifecycleError, "reason for retiring"):
            self.v.retire(PART, " ")
        self.assertEqual(self.v.items[PART].state, RELEASED)

    def test_change_request_guards(self):
        self.released()
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr(None, "  ", [PART])  # no problem
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr(None, "change", [])  # no items
        ecr = self.v.raise_ecr(None, "change", [PART])
        self.assertEqual(ecr.number, "ECR-0001")
        with self.assertRaises(LifecycleError):
            self.v.raise_ecr("ECR-0001", "again", [PART])  # number taken
        self.v.decide_ecr(ecr.number, True, "agreed")
        with self.assertRaises(LifecycleError):
            self.v.open_eco(None, [])  # no ECR
        self.assertEqual(self.v.open_eco(None, [ecr.number]).number, "ECO-0001")
        with self.assertRaises(LifecycleError):
            self.v.open_eco("ECO-0001", [ecr.number])  # number taken
        self.assertEqual(list(self.v.ecrs), ["ECR-0001"])

    # --- PDM-06: the BOM per revision and where-used over revisions --------

    def pedestal(self):
        """Figure "where-used as reverse reachability": Pedestal A holds 2 brackets (4 bolts
        each) and 2 bolts; an ECO then replaces the bracket's bolts with rivets (B)."""
        for pn, kind in ((PED, "assembly"), (BRK, "assembly"), (RISER, "part"), (PLATE, "part"),
                         (BOLT, "cots"), (RIVET, "cots")):
            self.v.create_item(pn, kind)
        self.v.set_bom(BRK, [(PLATE, 1, 1), (BOLT, 4, 2)])
        self.v.set_bom(PED, [(RISER, 1), (BRK, 2), (BOLT, 2)])
        for pn in (RISER, PLATE, BOLT, RIVET, BRK, PED):
            self.released(pn)
        eco = self.eco([BRK, PED])
        self.v.set_bom(BRK, [(PLATE, 1), (RIVET, 4)])
        self.v.set_bom(PED, [(RISER, 1), (BRK, 2)])
        for pn in (BRK, PED):
            self.v.submit(pn)
        self.v.release_eco(eco.number, note="rivets")

    def test_where_used_of_a_bolt_lists_every_assembly_revision_that_uses_it(self):
        self.pedestal()
        uses = self.v.where_used_revisions(BOLT)
        self.assertEqual([(u.part_number, u.revision, u.quantity, u.exact) for u in uses],
                         [(PED, "A", 10, True), (BRK, "A", 4, True)])
        self.assertEqual(uses[0].path, [(PED, "A"), (BOLT, "A")])  # the direct line is the shortest path
        self.assertFalse(any(u.current for u in uses))  # both superseded by B
        self.assertEqual(self.v.where_used(BOLT), {BRK, PED})
        self.assertEqual(self.v.where_used(BOLT, released_only=True), set())
        self.assertEqual([(u.part_number, u.revision) for u in self.v.where_used_revisions(RIVET)],
                         [(PED, "B"), (BRK, "B")])
        self.assertEqual(self.v.explode(PED, "A"), {RISER: 1, BRK: 2, PLATE: 2, BOLT: 10})
        self.assertEqual(self.v.flattened(PED), {RISER: 1, PLATE: 2, RIVET: 8})  # latest: B
        rows = self.v.indented(PED, "A")
        self.assertEqual([(r["level"], r["part_number"], r["revision"], r["qty"], r["total"]) for r in rows],
                         [(1, RISER, "A", 1, 1), (1, BRK, "A", 2, 2), (2, PLATE, "A", 1, 2), (2, BOLT, "A", 4, 8),
                          (1, BOLT, "A", 2, 2)])
        self.assertEqual(rows[2]["find"], 1)
        self.v.retire(BOLT, "replaced by rivets")  # no current released revision uses it
        self.assertEqual(self.v.items[BOLT].state, OBSOLETE)

    def test_release_pins_child_revisions(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        self.v.submit(PART)
        self.v.reject(PART, "too thin")  # 01 rejected; work goes on at 02
        self.v.set_bom(ASSY, [(PART, 3)])  # sees 02
        self.assertEqual(self.v.bom_of(ASSY)[0].rev, "02")
        self.released(PART)  # 02 releases as A
        self.released(ASSY)
        self.assertEqual(self.v.bom_of(ASSY)[0].rev, "A")
        self.assertEqual([(u.part_number, u.revision) for u in self.v.where_used_revisions(PART, "A")],
                         [(ASSY, "A")])
        self.assertEqual(self.v.where_used_revisions(PART, "02")[0].revision, "A")  # A answers to 02
        with self.assertRaisesRegex(LifecycleError, "frozen"):
            self.v.set_bom(ASSY, [(PART, 4)])
        self.assertEqual(self.v.newer_children(ASSY), [])
        eco = self.eco([PART])
        self.v.submit(PART)
        self.v.release_eco(eco.number, note="thicker")
        self.assertEqual(self.v.newer_children(ASSY), [(PART, "A", "B")])  # "newer child released"
        self.assertEqual(self.v.explode(ASSY), {PART: 3})
        uses = self.v.where_used_revisions(PART, "B")
        self.assertEqual([(u.part_number, u.revision, u.exact, u.quantity) for u in uses],
                         [(ASSY, "A", False, 3)])  # may pick up B; built with A
        self.assertEqual(self.v.where_used(PART, released_only=True), {ASSY})

    def test_every_bom_line_is_a_vault_item_before_release(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 1)], missing=[{"name": "washer.FCStd", "qty": 2}])
        self.v.submit(ASSY)
        with self.assertRaisesRegex(LifecycleError, "not vault items: washer.FCStd"):
            self.v.approve(ASSY, note="first")
        self.assertEqual(self.v.items[ASSY].latest.state, IN_REVIEW)
        self.assertEqual(self.v.indented(ASSY)[-1]["missing"], True)

    def test_a_cycle_is_refused_with_its_path(self):
        self.v.create_item(PART, "assembly")
        self.v.create_item(ASSY, "assembly")
        self.v.set_bom(ASSY, [(PART, 1)])
        with self.assertRaisesRegex(LifecycleError, f"{PART} -> {ASSY} -> {PART}"):
            self.v.set_bom(PART, [(ASSY, 1)])
        self.assertEqual(self.v.bom_of(PART), [])


class ModelContractTests(_Lifecycle, unittest.TestCase):
    def make(self):
        return pdm.Vault()


class _Folder:
    def make(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-contract-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        return FolderVault.init(os.path.join(self.tmp, "vault"), "Contract", actor="me@mac")

    def reopen(self, actor="me@mac"):
        return FolderVault(self.v.ref_path, actor=actor)


class FolderContractTests(_Folder, _Lifecycle, unittest.TestCase):
    """The same rows on a folder; every one also survives a reopen (journal replay)."""

    def tearDown(self):
        again = self.reopen()
        self.assertEqual(again.problems, [])
        self.assertEqual(again.states(), self.v.states())
        super().tearDown()

    def write_fcstd(self, name, links=()):
        path = os.path.join(self.tmp, name)
        xlinks = "".join(f'<XLink file="{link}" name="Body"/>' for link in links)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", f"<Document><Properties>{xlinks}</Properties></Document>")
        return path

    def check_in(self, pn, links=()):
        self.v.checkout(pn)
        return self.v.checkin(pn, self.write_fcstd(pn + ".FCStd", links), "first")

    def test_A10_a_released_file_link_blocks_retire_until_a_bom_is_recorded(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        self.v.create_item(PED, "assembly")
        self.check_in(PART)
        self.check_in(ASSY, [PART + ".FCStd"])  # no BOM recorded: the link is all there is
        self.check_in(PED, ["sub/" + PART + ".FCStd"])
        self.v.set_bom(PED, [])  # its BOM says it holds nothing: a stale link
        for pn in (PART, ASSY, PED):
            self.released(pn)
        with self.assertRaisesRegex(LifecycleError, rf"{ASSY} rev A \(file link\)"):
            self.v.retire(PART, "replaced")
        self.v.retire(ASSY, "replaced")
        self.v.retire(PART, "replaced")  # PED's BOM is what counts for PED

    def test_released_boms_go_to_the_outbox(self):
        self.v.create_item(PART)
        self.v.create_item(ASSY, "assembly")
        version = self.check_in(ASSY, [PART + ".FCStd"])
        self.v.set_bom(ASSY, [(PART, 2, 1)], sha256=version.sha256)
        self.released(PART)
        self.assertEqual(self.v.outbox(), [])  # a part has no BOM; WIP BOMs stay in the vault
        self.released(ASSY)
        eco = self.eco([ASSY])
        self.v.set_bom(ASSY, [(PART, 3, 1)])
        self.v.submit(ASSY)
        self.v.release_eco(eco.number, note="three")
        rows = self.reopen().outbox()
        self.assertEqual([(r["part_number"], r["revision"], r["eco"], r["lines"][0]["qty"]) for r in rows],
                         [(ASSY, "A", None, 2), (ASSY, "B", "ECO-0001", 3)])
        self.assertEqual(rows[0]["key"], f"{ASSY}@A:{version.sha256}")
        self.assertEqual(rows[0]["lines"][0], {"child": PART, "qty": 2, "find": 1, "rev": "A", "description": ""})
        self.assertTrue(self.v.outbox_path.endswith(os.path.join("outbox", "me@mac.jsonl")))

    def test_older_journals_replay_without_the_newer_rules(self):
        lines = [
            ("create_item", {"part_number": PART, "item_type": "part", "description": "", "drawing_of": None}),
            ("create_item", {"part_number": ASSY, "item_type": "assembly", "description": "", "drawing_of": None}),
            ("set_bom", {"parent": ASSY, "lines": [[PART, 4]]}),  # 23 Oct form: no revision
            ("submit", {"part_number": PART}),
            ("reject", {"part_number": PART, "reason": ""}),  # no reason then
            ("submit", {"part_number": PART}),
            ("approve", {"part_number": PART}),  # no note then
        ]
        with open(self.v.journal_path, "a", encoding="utf-8") as handle:
            for seq, (op, args) in enumerate(lines, 1):
                handle.write(json.dumps({"id": f"old-{seq}", "actor": "me@mac", "seq": seq,
                                         "at": f"2026-10-20T10:00:{seq:02d}+00:00", "op": op, "args": args}) + "\n")
        self.v = self.reopen()
        self.assertEqual(self.v.problems, [])
        self.assertEqual(self.v.items[PART].released.code, "A")
        self.assertEqual([line.as_dict() for line in self.v.bom_of(ASSY)], [{"child": PART, "qty": 4, "rev": "01"}])
        self.assertEqual(self.v.where_used_revisions(PART)[0].revision, "01")
        self.v.submit(ASSY)
        with self.assertRaisesRegex(LifecycleError, "release note"):
            self.v.approve(ASSY, note="")  # new operations follow the rules


class FileContractTests(_Folder, unittest.TestCase):
    """F1..F4 and their guards (folder vault only)."""

    def setUp(self):
        self.v = self.make()
        self.v.create_item(PART)
        self.file = os.path.join(self.tmp, "part.FCStd")
        self.write(b"v1")

    def write(self, data):
        with open(self.file, "wb") as handle:
            handle.write(data)

    def test_F1_F2_checkout_and_checkin(self):
        self.v.checkout(PART)
        self.assertEqual(self.v.locks[PART], "me@mac")
        version = self.v.checkin(PART, self.file, "first")
        self.assertEqual((version.revision, version.comment), ("01", "first"))
        self.assertNotIn(PART, self.v.locks)
        self.v.checkout(PART)
        self.write(b"v2")
        self.v.checkin(PART, self.file, "second", keep_lock=True)
        self.assertEqual(self.v.locks[PART], "me@mac")
        self.assertEqual(len(self.v.versions[PART]), 2)
        unchanged = self.v.checkin(PART, self.file, "same content")
        self.assertEqual(len(self.v.versions[PART]), 2)  # no new version, lock released
        self.assertEqual(unchanged.sha256, self.v.latest_version(PART).sha256)
        self.assertNotIn(PART, self.v.locks)

    def test_F2_checkin_records_made_from_and_fingerprint(self):
        """PDM-09: a drawing's check-in says what it was made from; a part's its geometry."""
        self.v.checkout(PART)
        print_ = {"volume": 1234.5678, "area": 99.5, "centre": [1.0, 2.0, 3.0], "box": [0, 0, 0, 1, 2, 3]}
        version = self.v.checkin(PART, self.file, "shape", fingerprint=print_)
        self.assertEqual(version.fingerprint, print_)
        self.assertIsNone(version.made_from)
        self.v.create_item(DWG, "drawing", drawing_of=PART)
        self.v.checkout(DWG)
        self.write(b"drawing")
        version = self.v.checkin(DWG, self.file, "views", made_from={PART: version.sha256})
        self.assertEqual(version.made_from, {PART: self.v.latest_version(PART).sha256})
        self.assertIsNone(version.fingerprint)
        again = self.reopen()
        self.assertEqual(again.latest_version(DWG).made_from, version.made_from)
        self.assertEqual(again.latest_version(PART).fingerprint, print_)
        self.assertEqual(again.version_with(PART, version.made_from[PART]).fingerprint, print_)
        # a version checked in without them (every version before PDM-09) reads as None
        with open(self.v.journal_path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        plain = next(l for l in lines if l["op"] == "checkin" and l["args"]["part_number"] == PART)
        self.assertIn("fingerprint", plain["args"])
        self.assertNotIn("made_from", plain["args"])

    def test_F3_undo_checkout(self):
        self.v.checkout(PART)
        self.v.undo_checkout(PART)
        self.assertNotIn(PART, self.v.locks)
        self.assertEqual(self.v.versions.get(PART, []), [])

    def test_F4_break_someone_elses_lock(self):
        other = self.reopen("other@mac")
        other.checkout(PART)
        self.v.refresh()
        with self.assertRaises(LifecycleError):
            self.v.checkout(PART)  # locked by someone else
        with self.assertRaises(LifecycleError):
            self.v.undo_checkout(PART)  # not without force
        self.assertEqual(self.v.locks[PART], "other@mac")
        self.v.undo_checkout(PART, force=True)
        self.assertNotIn(PART, self.v.locks)
        self.assertEqual(self.reopen().locks, {})

    def test_file_guards(self):
        with self.assertRaises(LifecycleError):
            self.v.checkin(PART, self.file, "first")  # not checked out
        self.v.checkout(PART)
        with self.assertRaisesRegex(LifecycleError, "comment is required"):
            self.v.checkin(PART, self.file, "  ")
        with self.assertRaises(LifecycleError):
            self.v.submit(PART)  # the file in review must be the one in the vault
        self.v.checkin(PART, self.file, "first")
        self.v.submit(PART)
        with self.assertRaises(LifecycleError):
            self.v.checkout(PART)  # In Review is not WIP
        self.v.approve(PART, note="released")
        with self.assertRaises(LifecycleError):
            self.v.checkout(PART)  # Released is read-only
        frozen = self.v.released_version(PART)
        self.assertEqual(frozen.sha256, self.v.latest_version(PART).sha256)
        self.assertEqual(self.reopen().released_version(PART).sha256, frozen.sha256)


def openapi_operations(path=OPENAPI):
    """{operationId: "METHOD /path"} from the OpenAPI file (no YAML library needed)."""
    found, current, method = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^  (/\S*):\s*$", line)
            if match:
                current, method = match.group(1), None
                continue
            if re.match(r"^\S", line):
                current = None
                continue
            match = re.match(r"^    (get|post|put|patch|delete):\s*$", line)
            if match and current:
                method = match.group(1).upper()
                continue
            match = re.match(r"^      operationId:\s*(\S+)\s*$", line)
            if match and current and method:
                found[match.group(1)] = f"{method} {current}"
    return found


class SpecTests(unittest.TestCase):
    def test_every_transition_has_its_endpoint(self):
        operations = openapi_operations()
        for row_id, op, endpoint, _from, _to in TRANSITIONS:
            self.assertEqual(operations.get(op), endpoint, f"{row_id} {op}")

    def test_spec_table_and_journal_name_the_same_operations(self):
        journal = set(LIFECYCLE_OPS) | set(FILE_OPS)
        table = {row[1] for row in TRANSITIONS}
        spec = set(openapi_operations()) - READS
        self.assertEqual(table, journal)
        self.assertEqual(spec, journal)

    def test_every_row_has_a_test(self):
        names = " ".join(dir(ModelContractTests) + dir(FileContractTests))
        for row_id, *_rest in TRANSITIONS:
            self.assertRegex(names, rf"test_(\w+_)?{row_id}_", row_id)


if __name__ == "__main__":
    unittest.main()
