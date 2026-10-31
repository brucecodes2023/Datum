# SPDX-License-Identifier: LGPL-2.1-or-later
"""A vault item's simulation study reports, offered as release attachments (SIM-01 / PDM-05; 30 Oct 2026).

Vector Labs' Simulation workspace writes a study's report beside the document as
``<document>_<study>_report.pdf`` (bruceworx_sim.report_paths). For a vault file the document is the
item's work copy ``<workspace>/<part number>.FCStd``, so its reports are ``<part number>_*_report.pdf``
in the workspace folder. Approve and Release ECO (bruceworx_changes, and ``vault approve
--attach-reports``) offer them, ticked, as attachments: the vault copies them in with the release
record (write-once, by SHA-256), so a release package carries them.

FreeCAD-free: it only looks at file names.
"""

from __future__ import annotations

import datetime
import os

REPORT_SUFFIX = "_report.pdf"


def reports_beside(path: str) -> list[str]:
    """The study reports of the document at ``path``, newest first: ``<stem>_<study>_report.pdf`` files in
    its folder (one per study; a study's report is replaced when it is written again)."""
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    if not stem or not os.path.isdir(folder):
        return []
    prefix = stem + "_"
    found = [os.path.join(folder, name) for name in os.listdir(folder)
             if name.startswith(prefix) and name.endswith(REPORT_SUFFIX) and len(name) > len(prefix) + len(REPORT_SUFFIX)
             and os.path.isfile(os.path.join(folder, name))]
    return sorted(found, key=lambda p: (-os.path.getmtime(p), p))


def item_reports(workspace, pn: str) -> list[str]:
    """The study reports of a vault item's work copy (``workspace.path(pn)``), newest first; none for a folder
    item (a KiCad project) or an item without a work copy."""
    try:
        if workspace.is_folder(pn):
            return []
    except Exception:  # noqa: BLE001 -- an item the ECAD module does not know: a plain file
        pass
    return reports_beside(workspace.path(pn))


def offered(workspace, pns) -> list[str]:
    """The study reports of several items (an ECO's), each once, item by item."""
    out = []
    for pn in pns:
        for path in item_reports(workspace, pn):
            if path not in out:
                out.append(path)
    return out


def label(path: str) -> str:
    """'Bracket_Static_report.pdf (2026-10-30 16:24)' for a list."""
    when = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    return f"{os.path.basename(path)} ({when})"
