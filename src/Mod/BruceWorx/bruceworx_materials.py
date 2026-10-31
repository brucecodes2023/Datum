# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Vector Labs material library (SIM-01): engineering materials with yield strengths.

The cards live in ``Resources/Materials/VectorLabs/{Metals,Plastics,Printed,Composites}``
next to this module and are registered with FreeCAD's Material module as a
read-only workbench library named ``Vector Labs`` (Init.py, so headless too).
Every card has density, E, nu, yield and UTS (LinearElastic), k, cp and alpha
(Thermal) and a colour (BasicRendering). A top-level ``BruceWorx:`` block,
which FreeCAD ignores, records the strength basis (typical or minimum), the
source, the elongation and, for printed materials, the Z (inter-layer)
values. Brittle materials have no yield: their YieldStrength is the tensile
strength and ``Brittle`` is true.

The UUIDs in :data:`CARDS` are permanent: parts store only the UUID, and the
vault material library (PDM-11) starts from these cards. Never change one;
add a new card instead.
"""

import math
import os

import FreeCAD as App

LIBRARY_NAME = "Vector Labs"
HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = os.path.join(HERE, "Resources", "Materials", "VectorLabs")
MODULES_PARAM = "User parameter:BaseApp/Preferences/Mod/Material/Resources/Modules"

# UUID -> card file (relative to LIBRARY_DIR). Permanent; TestBruceWorxMaterials checks the folder against it.
CARDS = {
    "6f3c6c6f-6af1-4f66-a59f-6190e2c25cb1": "Metals/Aluminium 5052-H32.FCMat",
    "6c52eca0-5547-4e8f-b587-bf487bb48f6b": "Metals/Aluminium 6061-T6.FCMat",
    "97bc836b-78b5-4975-b617-bcc56dd23d7c": "Metals/Aluminium 7075-T6.FCMat",
    "7b4a7da3-9a5b-4352-aed9-8181775ddc43": "Metals/Steel 1018 cold drawn.FCMat",
    "09615489-e7e5-4f73-9abf-02000284d99b": "Metals/Steel 1045 cold drawn.FCMat",
    "42d667a8-a2e8-446a-a230-1c058f07d174": "Metals/Steel 4140 annealed.FCMat",
    "8ba55f06-61a1-4200-ad73-19cb5af18f45": "Metals/Steel 4140 pre-hardened.FCMat",
    "f4544451-88ee-4470-9735-814a0d89f589": "Metals/Steel mild CR sheet.FCMat",
    "2f8225ce-a970-43f5-8220-98c32f6277a7": "Metals/Stainless 304 annealed.FCMat",
    "37956f74-f181-45ac-a492-22fb6af07382": "Metals/Stainless 316 annealed.FCMat",
    "35847d69-da21-4b21-a13e-cd747d89f27f": "Metals/Brass C360 half hard.FCMat",
    "b4724f5a-ceea-46cc-8827-bdc5b94eb37e": "Plastics/POM-C acetal copolymer.FCMat",
    "aa3ad6d8-8b66-47a7-b6bd-cbe012cbf747": "Plastics/POM-H Delrin 150.FCMat",
    "409524fe-7bec-47e7-bb46-580e86212f7e": "Plastics/PA6 extruded dry.FCMat",
    "5dab282d-d097-4a54-9c98-f86ccf188c62": "Plastics/UHMW-PE.FCMat",
    "4f180985-c664-4d3e-9252-153d3ecb4642": "Plastics/PC sheet.FCMat",
    "9dce0679-67fa-44ea-a20f-4a5361603bc4": "Plastics/Acrylic PMMA cast.FCMat",
    "a060657a-e531-47f0-80c9-aadd713f072a": "Printed/PLA printed.FCMat",
    "b4e991ce-42af-48ad-acbc-40e7f1de3a52": "Printed/PETG printed.FCMat",
    "aa50ed2d-8318-46ba-bb9a-5b93dd0f8b34": "Printed/PETG HF printed.FCMat",
    "88dae26a-37a2-4691-953a-3ba452740878": "Printed/ABS printed.FCMat",
    "24bfcb39-147f-4350-b5a6-eb4efeb70ee8": "Printed/ASA printed.FCMat",
    "a58fcf89-3829-4817-bae6-94401d6f7160": "Printed/PA12-CF printed.FCMat",
    "3e80c57d-cc7d-4110-b63e-d8e4df8053b3": "Printed/PA6-CF printed.FCMat",
    "8141dd8d-acbd-4658-94c3-e4891a30b1cd": "Printed/PC printed.FCMat",
    "59c6600a-84f6-411c-9803-d0f317c75d9f": "Printed/TPU 95A printed.FCMat",
    "a219f075-45f7-4963-8f78-123d102e03b3": "Composites/Carbon fibre plate QI.FCMat",
    "5b903760-2960-4bec-b622-20683a6b91e4": "Composites/G10-FR4 laminate.FCMat",
    # Added 30 Oct (SIM-01, second batch)
    "68fc1d8d-0c01-42ec-bd1c-7759270cb76d": "Metals/Titanium Ti-6Al-4V annealed.FCMat",
    "6707b2ec-c050-4f03-acf2-856f4453f018": "Metals/Stainless 17-4PH H1150.FCMat",
    "cd8cd9f3-25a1-43fa-880a-e49821b97b0e": "Metals/Steel A36 structural.FCMat",
    "23d6c827-c09d-4e35-958b-f60ecb5bc41e": "Printed/PETG-CF printed.FCMat",
    "6eed5199-f3b1-4c53-af4c-e2d1713f6f2d": "Printed/ASA-CF printed.FCMat",
}
FAMILIES = ("Metals", "Plastics", "Printed", "Composites")


OLD_LIBRARY_NAME = "Keystone"  # the library's name before the Vector Labs rename (31 Oct)


def _drop_old_registration():
    """Remove the "Keystone" registration an older profile holds (it would list the same cards twice,
    or point at a folder that no longer exists). Only when its ModuleDir is this workbench's own
    Materials/Vector Labs or Materials/VectorLabs folder, never a library the user added by that name."""
    modules = App.ParamGet(MODULES_PARAM)
    if OLD_LIBRARY_NAME not in modules.GetGroups():
        return
    old_dir = modules.GetGroup(OLD_LIBRARY_NAME).GetString("ModuleDir", "")
    tail = os.path.normpath(old_dir).replace(os.sep, "/")
    if (not old_dir or tail.endswith("Resources/Materials/Keystone")
            or tail.endswith("Resources/Materials/VectorLabs") or not os.path.isdir(old_dir)):
        modules.RemGroup(OLD_LIBRARY_NAME)


def register_library():
    """Make LIBRARY_DIR the read-only material library "Vector Labs" (the Material
    module reads Resources/Modules/<name>/ModuleDir). Rewritten on every start,
    so it follows the build or install folder. A MaterialManager made earlier
    needs ``refresh()``."""
    if not os.path.isdir(LIBRARY_DIR):
        return False
    _drop_old_registration()
    group = App.ParamGet(f"{MODULES_PARAM}/{LIBRARY_NAME}")
    if group.GetString("ModuleDir", "") != LIBRARY_DIR:
        group.SetString("ModuleDir", LIBRARY_DIR)
    icon = os.path.join(HERE, "preferences-bruceworx.svg")
    if group.GetString("ModuleIcon", "") != icon:
        group.SetString("ModuleIcon", icon)
    if not group.GetBool("ModuleReadOnly", False):
        group.SetBool("ModuleReadOnly", True)
    return True


def manager():
    """A Materials.MaterialManager that lists the Vector Labs library."""
    import Materials

    mm = Materials.MaterialManager()
    if not any(lib[0] == LIBRARY_NAME for lib in mm.MaterialLibraries):
        register_library()
        mm.refresh()
    return mm


def _uuid_for(key):
    if key in CARDS:
        return key
    wanted = str(key).strip().lower()
    for uuid, rel in CARDS.items():
        if os.path.splitext(os.path.basename(rel))[0].lower() == wanted:
            return uuid
    return None


def material(key):
    """The Materials.Material for a Vector Labs UUID or card name (the file name
    FreeCAD shows, case-insensitive); None when there is no such card."""
    uuid = _uuid_for(key)
    if uuid is None:
        return None
    return manager().getMaterial(uuid)


def _mpa(value):
    """A Base.Quantity (or None/NaN) in MPa, or None."""
    try:
        number = float(value.getValueAs("MPa"))
    except Exception:
        return None
    return None if math.isnan(number) else number


def _number(value, unit):
    try:
        number = float(value.getValueAs(unit)) if unit else float(value)
    except Exception:
        return None
    return None if math.isnan(number) else number


def extra(uuid):
    """The card's BruceWorx block (basis, source, Z strength, ...) as a dict."""
    import yaml

    rel = CARDS.get(uuid)
    if rel is None:
        return {}
    with open(os.path.join(LIBRARY_DIR, rel), encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("BruceWorx", {}) or {}


_HDT_CACHE = {}


def hdt_of(uuid):
    """The heat deflection temperature (deg C) a plastic or printed card records, or None (metals,
    composites and TPU have none). The Vector Labs cards give the value at the heavier load (1.8 MPa)."""
    if uuid not in CARDS:
        return None
    if uuid not in _HDT_CACHE:
        _HDT_CACHE[uuid] = _leading_number(extra(uuid).get("HeatDeflectionTemperature"))
    return _HDT_CACHE[uuid]


def _leading_number(text):
    try:
        return float(str(text).split()[0])
    except (ValueError, IndexError):
        return None


def values(mat):
    """The engineering values of a Vector Labs Materials.Material as a dict:
    density kg/m^3, E/G/yield/uts MPa, nu, elongation %, k W/m/K, cp J/kg/K,
    alpha 1/K, hdt (heat deflection temperature, deg C, plastics and printed), plus basis, brittle, source and (printed) z_strength MPa."""

    def get(name):
        try:
            return mat.getPhysicalValue(name)
        except Exception:
            return None

    block = extra(mat.UUID)
    out = {
        "uuid": mat.UUID,
        "name": mat.Name,
        "family": block.get("Family", ""),
        "library": getattr(mat, "LibraryName", ""),
        "density": _number(get("Density"), "kg/m^3"),
        "E": _mpa(get("YoungsModulus")),
        "nu": _number(get("PoissonRatio"), None),
        "G": _mpa(get("ShearModulus")),
        "yield": _mpa(get("YieldStrength")),
        "uts": _mpa(get("UltimateTensileStrength")),
        "elongation": _leading_number(block.get("Elongation")),
        "k": _number(get("ThermalConductivity"), "W/m/K"),
        "cp": _number(get("SpecificHeat"), "J/kg/K"),
        "alpha": _number(get("ThermalExpansionCoefficient"), "1/K"),
        "basis": block.get("Basis", ""),
        "yield_basis": block.get("YieldBasis", ""),
        "brittle": bool(block.get("Brittle", False)),
        "source": block.get("Source", ""),
        "z_strength": _leading_number(block.get("ZTensileStrength")),
        "hdt": _leading_number(block.get("HeatDeflectionTemperature")),
        "hdt_basis": block.get("HDTBasis", ""),
        "hdt_source": block.get("HDTSource", ""),
    }
    return out


def card(key):
    """values() of a Vector Labs card by UUID or name; None when there is none."""
    mat = material(key)
    return values(mat) if mat is not None else None


def cards():
    """values() of every Vector Labs card, by family (Metals, Plastics, Printed, Composites) then name."""
    mm = manager()
    found = []
    for uuid in CARDS:
        try:
            found.append(values(mm.getMaterial(uuid)))
        except Exception as exc:
            App.Console.PrintWarning(f"Vector Labs material {CARDS[uuid]} did not load: {exc}\n")
    order = {name: i for i, name in enumerate(FAMILIES)}
    found.sort(key=lambda v: (order.get(v["family"], len(order)), v["name"].lower()))
    return found
