# SPDX-License-Identifier: LGPL-2.1-or-later
"""A small stand-in for the Leonardo vault, to try t_leonardo.py before the real one exists.

Run with FreeCADCmd: standin_vault.py <folder>. Makes a folder vault "Leonardo" with
an adapter plate (checked in, WIP) and a sun gear (checked in, then checked out by
test-part, so it shows a lock). Never point it at the real vault.
"""

import os
import sys

import FreeCAD as App

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
folder = sys.argv[-1]
if "CloudDocs" in folder:
    raise SystemExit("refusing to write into iCloud Drive")

import bwmodel  # noqa: E402
from heuretes_freecad.partprops import load_schema  # noqa: E402
from heuretes_freecad.pdm_folder import FolderVault  # noqa: E402

vault = FolderVault.init(folder, "Leonardo", actor="test-part")
tmp = os.path.join(folder + "-src")
os.makedirs(tmp, exist_ok=True)


def save(doc, pn, description):
    part = [o for o in doc.Objects if o.TypeId == "App::Part"][0]
    load_schema().apply(part)
    part.PartNumber = pn
    part.Description = description
    doc.recompute()
    path = os.path.join(tmp, pn + ".FCStd")
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    return path


doc = App.newDocument("Plate")
bwmodel.build(doc)
plate = save(doc, "LE-01-00001", "Motor adapter plate")

doc = App.newDocument("Sun")
part = doc.addObject("App::Part", "Part")
part.Label = "Sun gear"
body = doc.addObject("PartDesign::Body", "Body")
part.addObject(body)
cyl = body.newObject("PartDesign::AdditiveCylinder", "Cylinder")
cyl.Radius, cyl.Height = 6.5, 8
sun = save(doc, "LE-01-00002", "Sun gear 12T m1")

for pn, kind, desc, path in (("LE-01-00001", "part", "Motor adapter plate", plate),
                             ("LE-01-00002", "part", "Sun gear 12T m1", sun)):
    vault.create_item(pn, kind, desc)
    vault.checkout(pn)
    vault.checkin(pn, path, "first version")
vault.create_item("LE-01-00010", "assembly", "NEMA 17 planetary gearbox 5:1")
vault.checkout("LE-01-00002")  # test-part is working on it
print("stand-in vault", folder, sorted(vault.items))
