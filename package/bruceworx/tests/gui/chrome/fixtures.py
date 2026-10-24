# SPDX-License-Identifier: LGPL-2.1-or-later
"""Make the files the chrome checks open (run with FreeCADCmd): fixtures.py <folder>.

Plate.FCStd (the sample plate, part number LE-01-01001), Bracket.FCStd (a box in
a Part, no part number) and an empty folder vault ChromeVault.
"""

import os
import sys

import FreeCAD as App

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
out = os.environ.get("BWT_FIXTURES") or sys.argv[-1]
os.makedirs(out, exist_ok=True)

import bwmodel  # noqa: E402

doc = App.newDocument("Plate")
part = bwmodel.build(doc)[0]
try:
    from bruceworx.partprops import load_schema

    load_schema().apply(part)
    part.PartNumber = "LE-01-01001"
    part.Revision = "02"
    part.Description = "Mounting plate, 80 x 50"
except Exception as exc:
    print("partprops not applied:", exc)
doc.recompute()
doc.saveAs(os.path.join(out, "Plate.FCStd"))
App.closeDocument(doc.Name)

doc = App.newDocument("Bracket")
part = doc.addObject("App::Part", "Part")
box = doc.addObject("Part::Box", "Box")
part.addObject(box)
doc.recompute()
doc.saveAs(os.path.join(out, "Bracket.FCStd"))
App.closeDocument(doc.Name)

vault = os.path.join(out, "ChromeVault")
if not os.path.isdir(vault) or not any(n.endswith(".bwvault") or "vault" in n for n in os.listdir(vault)):
    try:
        from bruceworx.pdm_folder import FolderVault

        FolderVault.init(vault, "ChromeVault", actor="tester")
    except Exception as exc:
        print("vault not created:", exc)
print("fixtures done", sorted(os.listdir(out)))
