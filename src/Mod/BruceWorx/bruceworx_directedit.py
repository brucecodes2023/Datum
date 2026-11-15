# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct edit on a dumb solid (roadmap MOD-04).

Early slice, headless: offset one planar face along its outward normal.
Part already does the kernel work (``Solid.offsetFaces``, next to
``Shape.defeaturing``, which this slice does not replace).

Still to come: move a face sideways, delete or replace a face, recognise a
hole, and run that on the LE-01001 STEP.
"""

from __future__ import annotations

import math


def offset_face(solid, face, distance):
    """Offset ``face`` of ``solid`` by ``distance`` along the outward normal.

    Positive distance grows the solid. On a planar face the volume changes by
    that face's area times ``distance``. Returns a new valid solid; ``solid``
    is left unchanged.

    ``face`` has to be one of ``solid.Faces`` (the same face, not a copy).
    """
    import Part

    shape = _solid(solid)
    target = _planar_face(shape, face)
    distance = _distance(distance)
    made = shape.offsetFaces((target,), distance)
    return _one_solid(made)


def _solid(solid):
    import Part

    if (
        isinstance(solid, Part.Shape)
        and not solid.isNull()
        and solid.ShapeType == "Solid"
        and solid.isValid()
        and len(solid.Solids) == 1
    ):
        return solid
    raise ValueError("offset_face takes one valid solid")


def _planar_face(solid, face):
    import Part

    if not isinstance(face, Part.Shape) or face.isNull() or face.ShapeType != "Face":
        raise ValueError("offset_face takes one face of the solid")
    for existing in solid.Faces:
        if existing.isSame(face):
            if not isinstance(existing.Surface, Part.Plane):
                raise ValueError("offset_face takes a planar face")
            return existing
    raise ValueError("that face is not a face of the solid")


def _distance(distance):
    try:
        distance = float(distance)
    except (TypeError, ValueError):
        raise ValueError("distance must be a number") from None
    if not math.isfinite(distance):
        raise ValueError("distance must be finite")
    return distance


def _one_solid(shape):
    if shape.isNull() or shape.ShapeType != "Solid" or not shape.isValid() or len(shape.Solids) != 1:
        raise ValueError("offsetting that face did not leave one valid solid")
    return shape
