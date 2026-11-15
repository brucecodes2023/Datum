# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless check for bruceworx.api: padded body, tree summary, STEP export.

Run with FreeCADCmd (it imports this file as a module)::

    FreeCADCmd addons/BruceWorx/tests/freecad_api_smoke.py

Does not start the Keystone bridge. Do not set BW_BRIDGE_AUTOALLOW.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_api():
    """Import ``bruceworx.api`` from the source tree next to this test.

    FreeCADCmd has already loaded the installed hooks add-on
    (Mod/BruceWorxHooks). A build from before ``api.py`` was added does not
    contain it. Putting this source package on ``bruceworx.__path__`` resolves
    the import without copying into the build.
    """
    import bruceworx  # noqa: PLC0415

    package = os.path.join(_ROOT, "bruceworx")
    if package not in bruceworx.__path__:
        bruceworx.__path__.insert(0, package)
    from bruceworx import api  # noqa: PLC0415

    return api


def run() -> None:
    import FreeCAD  # noqa: PLC0415

    from bruceworx.freecad_io import import_step  # noqa: PLC0415

    api = _load_api()

    length, width, height = 20.0, 10.0, 5.0
    expected = length * width * height
    doc = FreeCAD.newDocument("ApiPart")
    try:
        made = api.create_padded_body(
            doc, length=length, width=width, height=height, name="Body"
        )
        body, sketch, pad = made["body"], made["sketch"], made["pad"]
        _check_feature(body, sketch, pad, expected)
        _check_tree(api.tree_summary(doc), body.Name)
        with tempfile.TemporaryDirectory(prefix="bruceworx-api-") as directory:
            step = os.path.join(directory, "part.step")
            result = api.export_step(doc, step)
            _check_step(result, step, body.Name, pad.Name, expected, import_step)
    finally:
        FreeCAD.closeDocument(doc.Name)
    print("BW_API PASS", flush=True)


def _check_feature(body, sketch, pad, expected: float) -> None:
    if body.TypeId != "PartDesign::Body":
        raise RuntimeError(f"body type {body.TypeId}")
    if sketch.TypeId != "Sketcher::SketchObject":
        raise RuntimeError(f"sketch type {sketch.TypeId}")
    if pad.TypeId != "PartDesign::Pad":
        raise RuntimeError(f"pad type {pad.TypeId}")
    group = list(getattr(body, "Group", []) or [])
    if sketch not in group or pad not in group:
        raise RuntimeError(f"body group {[obj.Name for obj in group]}")
    profile = getattr(pad, "Profile", None)
    # Profile is a link to the sketch, or (sketch, sub-elements).
    linked = profile[0] if isinstance(profile, tuple) else profile
    if linked != sketch:
        raise RuntimeError(f"pad profile is {getattr(linked, 'Name', profile)!r}")
    shape = body.Shape
    if shape.isNull() or not shape.Solids:
        raise RuntimeError("body has no solid")
    if abs(shape.Volume - expected) > 0.01:
        raise RuntimeError(f"volume {shape.Volume} != {expected}")


def _check_tree(summary, body_name: str) -> None:
    nodes = list(_walk(summary))
    roots = [node for node in summary if node["name"] == body_name]
    if len(roots) != 1 or roots[0]["type"] != "PartDesign::Body":
        raise RuntimeError(f"body missing from tree: {summary!r}")
    types = {node["type"] for node in nodes}
    for needed in ("Sketcher::SketchObject", "PartDesign::Pad"):
        if needed not in types:
            raise RuntimeError(f"tree missing {needed}: {summary!r}")


def _walk(nodes):
    for node in nodes:
        if set(node) != {"name", "type", "children"}:
            raise RuntimeError(f"tree node keys {set(node)}: {node!r}")
        if not isinstance(node["children"], list):
            raise RuntimeError(f"children is not a list: {node!r}")
        yield node
        yield from _walk(node["children"])


def _check_step(result, step, body_name, pad_name, expected, import_step) -> None:
    if body_name not in result["objects"]:
        raise RuntimeError(f"export missed the body: {result!r}")
    if pad_name in result["objects"]:
        raise RuntimeError(f"pad exported apart from its body: {result!r}")
    header = open(step, "rb").read(80)
    if b"ISO-10303-21" not in header:
        raise RuntimeError(f"STEP header missing: {header!r}")
    import FreeCAD  # noqa: PLC0415

    imported = FreeCAD.newDocument("ApiImport")
    try:
        import_step(step, imported)
        imported.recompute()
        volumes = []
        for obj in imported.Objects:
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull() or not shape.Solids:
                continue
            volumes.append(shape.Volume)
        if not any(abs(volume - expected) / expected < 0.01 for volume in volumes):
            names = [obj.Name for obj in imported.Objects]
            raise RuntimeError(f"STEP import volumes {volumes} objects {names}")
    finally:
        FreeCAD.closeDocument(imported.Name)


def _entry() -> int:
    try:
        import FreeCAD  # noqa: F401, PLC0415
    except ImportError:
        print("freecad_api_smoke.py must be run with FreeCADCmd", file=sys.stderr)
        return 1
    try:
        run()
    except Exception:  # noqa: BLE001 — report and fail the process
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__" or __name__ == "freecad_api_smoke":
    sys.exit(_entry())
