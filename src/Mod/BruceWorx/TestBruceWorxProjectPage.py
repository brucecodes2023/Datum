# SPDX-License-Identifier: LGPL-2.1-or-later
"""Project tabs, cards and pages on the vault dashboard (BW-PDM-15): helpers and the widgets, off screen."""

import datetime as dt
import os
import shutil
import tempfile
import unittest

import bruceworx_projectpage as pp


def make_vault(folder):
    from bruceworx.pdm_folder import FolderVault

    v = FolderVault.init(os.path.join(folder, "Workshop"), "Workshop", actor="bruce@mac")
    src = os.path.join(folder, "src")
    os.makedirs(src, exist_ok=True)
    part, asm, dwg, bolt = "DM-01-01001-001", "DM-02-01001-001", "DM-03-01001-001", "DM-05-01001-001"
    v.create_item(part, "part", "Plate")
    v.create_item(bolt, "cots", "Bolt")
    v.create_item(asm, "assembly", "Frame")
    v.create_item(dwg, "drawing", "Plate drawing", drawing_of=part)
    v.set_bom(asm, [(part, 2), (bolt, 4)])
    for pn in (part, asm, dwg):
        path = os.path.join(src, pn + ".FCStd")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(pn)
        v.checkout(pn)
        v.checkin(pn, path, "first")
    return v


def fake_panel(QtWidgets):
    """Stands in for the Files list: a widget that records what the project page asks of it."""

    class FakePanel(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.scopes, self.texts = [], []
            self.search = QtWidgets.QLineEdit(self)
            self.search.textChanged.connect(self.texts.append)
            self.chips, self.kind = {}, "all"

        def set_scope(self, key):
            self.scopes.append(key)

    return FakePanel()


class PureTests(unittest.TestCase):
    def test_tab_text(self):
        self.assertEqual(pp.tab_text("Files", 77), "Files 77")
        self.assertEqual(pp.tab_text("Overview", None), "Overview")
        self.assertEqual(pp.tab_text("Changes", 0), "Changes 0")

    def test_bar_text(self):
        self.assertEqual(pp.bar_text({"files": 77, "states": {"WIP": 77}}), "All 77 WIP · 0 released")
        self.assertEqual(pp.bar_text({"files": 10, "states": {"WIP": 4, "Released": 6}}), "6 of 10 released · 4 WIP")
        self.assertEqual(pp.bar_text({"files": 0, "states": {}}), "")

    def test_card_lines_and_notes(self):
        lines = pp.card_lines({"missing": 10, "mine": 0, "last": ""})
        self.assertEqual(lines[0], ("Missing a drawing", "10", "warn"))
        self.assertEqual(lines[2][1], "none yet")
        self.assertIn("OW-01-01001", pp.empty_note({"first_number": "OW-01-01001"}))

    def test_header_sub_and_targets(self):
        sub = pp.header_sub({"description": "90 N turbojet", "pattern": "VL-xx-xxxxx", "files": 77})
        self.assertEqual(sub, "90 N turbojet · part numbers VL-xx-xxxxx · 77 files")
        self.assertEqual(pp.attention_target({"tab": "simulations"}), ("tab", "simulations"))
        self.assertEqual(pp.attention_target({"scope": "view:missing"}), ("files", "view:missing", ""))

    def test_picture_file_without_a_file(self):
        self.assertEqual(pp.picture_file(None), "")
        self.assertEqual(pp.picture_file({"sha": "x", "object": "/nonexistent"}), "")


class WidgetTests(unittest.TestCase):
    def setUp(self):
        from PySide import QtWidgets

        self.QtWidgets = QtWidgets
        self.tmp = tempfile.mkdtemp(prefix="bw-pp-gui-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = make_vault(self.tmp)

    def dashboard(self):
        import bruceworx_dashboard as dash
        from bruceworx import vault_dashboard

        QtWidgets = self.QtWidgets
        outer = self

        class Page:
            files_panel = None
            files_layout = None

            def request_feed(self, _o):
                pass

            def checked_seconds(self):
                return 5.0

            def _ensure_files(self):
                if self.files_panel is None:
                    self.files_panel = outer.panel
                    self.files_layout = QtWidgets.QVBoxLayout(outer.holder)
                    self.files_layout.addWidget(self.files_panel)

        self.panel = fake_panel(QtWidgets)
        self.holder = QtWidgets.QWidget()
        self.page = Page()
        d = dash.classes()["Dashboard"](self.page)
        d.show()
        self.addCleanup(d.deleteLater)
        snap = vault_dashboard.snapshot(self.vault, None, now=dt.datetime.now().astimezone())
        d.fill(snap, 3.0)
        return d, snap

    def test_tabs_cards_and_a_project_page(self):
        d, snap = self.dashboard()
        self.assertNotIn("project_pages_error", snap)
        self.assertEqual(list(d.project_tabs.buttons), ["", "DM"])
        self.assertEqual(d.project_tabs.buttons["DM"].text(), "DM · DM")
        self.assertTrue(d.cards.isVisibleTo(d) and not d.project_page.isVisibleTo(d))
        self.assertFalse(d.tiles_box.isVisibleTo(d))  # the project cards replace the four totals tiles
        d.set_project("DM")
        self.assertTrue(d.project_page.isVisibleTo(d))
        self.assertFalse(d.cards.isVisibleTo(d))
        self.assertFalse(d.head_box.isVisibleTo(d))
        texts = {k: b.text() for k, b in d.project_page.tab_buttons.items()}
        self.assertEqual(texts["files"], "Files 4")
        self.assertEqual(texts["drawings"], "Drawings 1")
        self.assertEqual(texts["simulations"], "Simulations 0")
        for key, _t in pp.PAGE_TABS:
            if key != "files":
                d.project_page.set_tab(key)
                self.assertTrue(d.project_page.panes[key].isVisibleTo(d))
        d.set_project("")
        self.assertTrue(d.cards.isVisibleTo(d))

    def test_files_tab_moves_the_list_in_and_back(self):
        d, _snap = self.dashboard()
        d.set_project("DM")
        d.project_page.set_tab("files")
        self.assertEqual(self.panel.scopes[-1], "program:DM")
        self.assertIs(self.panel.parent(), d.project_page.panes["files"])
        d.project_page.to_files("view:missing")
        self.assertEqual(self.panel.scopes[-1], "view:missing")
        self.assertIn("program:DM", self.panel.search.text())
        d.project_page.set_tab("overview")
        self.assertIs(self.panel.parent(), self.holder)

    def test_simulations_tab_says_none_yet(self):
        d, _snap = self.dashboard()
        d.set_project("DM")
        labels = [lab.text() for lab in d.project_page.panes["simulations"].findChildren(self.QtWidgets.QLabel)]
        self.assertTrue(any(t.startswith("No simulations yet") for t in labels), labels)

    def test_simulation_rows_from_a_fake_list(self):
        d, snap = self.dashboard()
        from bruceworx import project_pages

        rows = project_pages.simulation_rows(
            self.structure(), "DM", lambda s, scope=None: [
                {"pn": "DM-04-1", "description": "Plate stress", "state": "WIP", "of": "DM-01-01001-001",
                 "out_of_date": True}])
        page = dict(snap["project_pages"]["pages"]["DM"], simulations=rows,
                    tab_counts=dict(snap["project_pages"]["pages"]["DM"]["tab_counts"], simulations=1))
        d.set_project("DM")
        d.project_page.fill(page)
        labels = [lab.text() for lab in d.project_page.panes["simulations"].findChildren(self.QtWidgets.QLabel)]
        self.assertIn("Plate stress", labels)
        self.assertIn("OUT OF DATE", labels)
        self.assertEqual(d.project_page.tab_buttons["simulations"].text(), "Simulations 1")

    def structure(self):
        from bruceworx import project_pages

        return project_pages.structure_of(self.vault)


if __name__ == "__main__":
    unittest.main()
