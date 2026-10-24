# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCADCmd check: schema properties on a real part, computed mass and
material, the Assembly BOM printing them, and the vault writing the part's
LifecycleState (on each transition, and when the file is opened).

    FreeCADCmd addons/BruceWorx/tests/freecad_partprops_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _material(name):
    import Materials  # noqa: PLC0415

    for material in Materials.MaterialManager().Materials.values():
        if material.Name == name:
            return material
    raise LookupError(name)


def run() -> None:
    import FreeCAD  # noqa: PLC0415

    from bruceworx import partprops  # noqa: PLC0415
    from bruceworx.partprops import load_schema  # noqa: PLC0415

    schema = load_schema(user_layer=False)
    doc = FreeCAD.newDocument("PartPropsSmoke")
    part = doc.addObject("App::Part", "Part")
    box = doc.addObject("Part::Box", "Plate")
    box.Length, box.Width, box.Height = 100, 50, 10  # 50 000 mm^3
    part.addObject(box)
    box.ShapeMaterial = _material("Aluminum-6061-T6")
    doc.recompute()

    report = schema.apply(part)
    assert not report["conflicts"], report
    assert part.getTypeIdOfProperty("Mass") == "App::PropertyMass"
    assert part.MaterialName == "Aluminum-6061-T6", part.MaterialName
    mass = part.Mass.getValueAs("kg").Value
    density = FreeCAD.Units.Quantity(box.ShapeMaterial.getPhysicalValue("Density")).getValueAs("kg/mm^3").Value
    assert abs(mass - 50000 * density) < 1e-9, (mass, density)
    assert 0.1 < mass < 0.2, mass  # aluminium plate, about 0.135 kg
    assert "ReadOnly" in part.getEditorMode("PartNumber")
    assert schema.missing(part, "submit") == ["Description"], schema.missing(part, "submit")
    part.PartNumber = "LE-01-01001-001"
    part.Revision = "01"
    part.Description = "Base plate"
    part.Finish = "Clear anodise"

    import UtilsAssembly  # noqa: PLC0415

    assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
    link = assembly.newObject("App::Link", "PlateLink")
    link.LinkedObject = part
    bom = UtilsAssembly.getBomGroup(assembly).newObject("Assembly::BomObject", "BOM")
    bom.columnsNames = ["Index", "Name", "Quantity"] + schema.bom_columns() + [".LifecycleState"]
    doc.recompute()
    header = [bom.getContents(f"{chr(65 + i)}1") for i in range(len(bom.columnsNames))]
    row = {h.lstrip("'"): bom.getContents(f"{chr(65 + i)}2").lstrip("'") for i, h in enumerate(header)}
    print("BOM row", row)
    assert row[".PartNumber"] == "LE-01-01001-001", row
    assert row[".Revision"] == "01", row
    assert row[".MaterialName"] == "Aluminum-6061-T6", row
    assert row[".Mass"].lstrip("=").replace(" ", "") in ("135g", "0.135kg"), row  # a unit expression
    assert row[".LifecycleState"] == "WIP", row  # an enumeration prints its text

    # Material picked after apply: the computed values follow without
    # applying again. FreeCAD's "Default" material means none: no mass.
    partprops.install_observer()
    part2 = doc.addObject("App::Part", "Part2")
    box2 = doc.addObject("Part::Box", "Bracket")  # 10 mm cube, 1000 mm^3
    part2.addObject(box2)
    doc.recompute()
    default = box2.ShapeMaterial
    assert default.Name == "Default", default.Name
    schema.apply(part2)
    assert part2.MaterialName == "" and part2.Mass.Value == 0, (part2.MaterialName, part2.Mass)
    box2.ShapeMaterial = _material("Aluminum-6061-T6")
    doc.recompute()
    assert part2.MaterialName == "Aluminum-6061-T6", part2.MaterialName
    assert abs(part2.Mass.getValueAs("kg").Value - 1000 * density) < 1e-12, part2.Mass
    box2.Length = 20
    doc.recompute()
    assert abs(part2.Mass.getValueAs("kg").Value - 2000 * density) < 1e-12, part2.Mass
    assert "Touched" not in part2.State, part2.State
    box2.ShapeMaterial = default
    doc.recompute()
    assert part2.MaterialName == "" and part2.Mass.Value == 0, (part2.MaterialName, part2.Mass)
    FreeCAD.closeDocument(doc.Name)
    lifecycle_state(schema)
    print("BW_PARTPROPS_SMOKE PASS")


def lifecycle_state(schema) -> None:
    """LifecycleState: read-only, FreeCAD's obj.State untouched, written by the
    vault on each transition and taken from the vault when a file opens."""
    import FreeCAD  # noqa: PLC0415

    from bruceworx import partprops  # noqa: PLC0415
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="bw-state-smoke-")
    prefs = FreeCAD.ParamGet(partprops.PREFS_PATH)
    old_path = prefs.GetString("VaultPath", "")
    try:
        doc = FreeCAD.newDocument("StateSmoke")
        part = doc.addObject("App::Part", "Plate")
        schema.apply(part, compute=False)
        part.PartNumber = "LE-01-01001-001"
        assert part.LifecycleState == "WIP", part.LifecycleState
        assert part.getEnumerationsOfProperty("LifecycleState") == ["WIP", "In Review", "Released", "Obsolete"]
        assert "ReadOnly" in part.getEditorMode("LifecycleState")
        assert isinstance(part.State, list), part.State  # FreeCAD's status list is not hidden
        doc.recompute()

        vault = FolderVault.init(os.path.join(tmp, "vault"), "Smoke", actor="smoke@test")
        partprops.attach_vault(vault)
        vault.create_item("LE-01-01001-001", "part", "Base plate")
        vault.submit("LE-01-01001-001")
        assert part.LifecycleState == "In Review", part.LifecycleState
        assert "Touched" not in part.State, part.State  # a state change needs no recompute
        path = os.path.join(tmp, "plate.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)

        # Approved while the file was closed: opening it takes the vault's record.
        vault.approve("LE-01-01001-001", note="released")
        prefs.SetString("VaultPath", vault.root)
        partprops.install_observer()
        doc = FreeCAD.openDocument(path)
        part = doc.getObject("Plate")
        assert part.LifecycleState == "Released", part.LifecycleState
        assert "ReadOnly" in part.getEditorMode("LifecycleState")
        FreeCAD.closeDocument(doc.Name)
    finally:
        prefs.SetString("VaultPath", old_path)
        shutil.rmtree(tmp, ignore_errors=True)


try:
    run()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    print("BW_PARTPROPS_SMOKE FAIL")
    sys.exit(1)
