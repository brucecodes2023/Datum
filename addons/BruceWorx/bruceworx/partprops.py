# SPDX-License-Identifier: LGPL-2.1-or-later
"""Part properties from a schema, so new properties need no code (BW-ASM-04, PDM-02).

The properties every part carries (P/N, revision, description, material,
mass, ...) are listed in JSON, not in code. Layers are merged in order:

1. ``config/part_properties.json`` -- built in, ships with BruceWorx.
2. A company or vault layer, for example ``<vault>/part_properties.json``.
3. A user layer, ``~/.config/bruceworx/part_properties.json``.

A later layer may add properties, and may change an existing property's
label, doc, default, ``bom`` flag or ``required`` stage. It may not change a
property's type: that would make old files unreadable, so it is refused.
``renamed`` maps an old property name to its new one; applying the schema
moves the value across.

Applying never deletes a property the schema does not list, so a file made
with a newer or larger schema keeps its extra values when an older one opens
it.

The Assembly BOM reads a column named ``.<Property>`` from each part
(``BomObject.cpp``), so :meth:`Schema.bom_columns` is all the BOM needs.

Computed values (material name, mass) follow the part: a document observer
(:func:`install_observer`, started by the add-on's Init.py) refreshes them
when a body's material, shape or a part's contents change, so ``apply`` is
needed once, not after every change. FreeCAD's "Default" material counts as
no material: no name and no mass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from bruceworx.config_path import config_dir

SCHEMA_ID = "bruceworx.part-properties"
BUILTIN_NAME = "part_properties.json"
USER_LAYER = os.path.join("~", ".config", "bruceworx", BUILTIN_NAME)

# Schema type -> FreeCAD property type. Every one of these is a type the
# Assembly BOM can print.
FREECAD_TYPES = {
    "string": "App::PropertyString",
    "int": "App::PropertyInteger",
    "float": "App::PropertyFloat",
    "bool": "App::PropertyBool",
    "enum": "App::PropertyEnumeration",
    "length": "App::PropertyLength",
    "mass": "App::PropertyMass",
    "quantity": "App::PropertyQuantity",
}
STAGES = ("submit", "release")
SOURCES = ("user", "pdm", "computed:mass", "computed:material")
_OVERRIDABLE = ("doc", "default", "bom", "required", "readonly", "values")


class SchemaError(ValueError):
    """A schema layer that cannot be used."""


@dataclass
class PropertyDef:
    name: str
    type: str
    doc: str = ""
    default: object = None
    bom: bool = False
    required: str | None = None  # lifecycle stage that needs a value
    source: str = "user"
    readonly: bool = False
    values: list = field(default_factory=list)  # enum choices
    layer: str = ""

    @property
    def freecad_type(self) -> str:
        return FREECAD_TYPES[self.type]

    @classmethod
    def from_json(cls, raw: dict, layer: str) -> "PropertyDef":
        try:
            name, kind = raw["name"], raw["type"]
        except KeyError as exc:
            raise SchemaError(f"{layer}: property needs {exc.args[0]!r}") from exc
        if not str(name).isidentifier():
            raise SchemaError(f"{layer}: {name!r} is not a valid property name")
        if kind not in FREECAD_TYPES:
            raise SchemaError(f"{layer}: {name} has unknown type {kind!r} ({', '.join(FREECAD_TYPES)})")
        prop = cls(name, kind, layer=layer)
        prop._update(raw, layer)
        if prop.source not in SOURCES:
            raise SchemaError(f"{layer}: {name} has unknown source {prop.source!r}")
        if kind == "enum" and not prop.values:
            raise SchemaError(f"{layer}: enum {name} needs values")
        return prop

    def _update(self, raw: dict, layer: str):
        for key in _OVERRIDABLE + ("source",):
            if key in raw:
                setattr(self, key, raw[key])
        if self.required is not None and self.required not in STAGES:
            raise SchemaError(f"{layer}: {self.name} required stage must be one of {STAGES}")
        self.bom = bool(self.bom)
        self.readonly = bool(self.readonly)


class Schema:
    def __init__(self, group: str = "BruceWorx"):
        self.group = group
        self.properties: dict[str, PropertyDef] = {}
        self.renamed: dict[str, str] = {}
        self.layers: list[str] = []

    # ----- building -------------------------------------------------------

    def add_layer(self, raw: dict, layer: str) -> "Schema":
        if raw.get("schema") != SCHEMA_ID:
            raise SchemaError(f"{layer} is not a {SCHEMA_ID} file")
        for old, new in (raw.get("renamed") or {}).items():
            self.renamed[old] = new
            if old in self.properties and new not in self.properties:
                prop = self.properties.pop(old)
                prop.name = new
                self.properties[new] = prop
        for entry in raw.get("properties", []):
            name = entry.get("name")
            existing = self.properties.get(name)
            if existing is None:
                self.properties[name] = PropertyDef.from_json(entry, layer)
                continue
            if entry.get("type", existing.type) != existing.type:
                raise SchemaError(
                    f"{layer}: {name} is {existing.type} in {existing.layer}; a layer cannot change its type"
                )
            existing._update(entry, layer)
        self.layers.append(layer)
        return self

    # ----- reading ----------------------------------------------------------

    def bom_columns(self) -> list[str]:
        """Assembly BOM column names (``.Property`` reads that property)."""
        return ["." + p.name for p in self.properties.values() if p.bom]

    def values(self, obj) -> dict:
        return {name: getattr(obj, name, None) for name in self.properties}

    def missing(self, obj, stage: str) -> list[str]:
        """Properties without a value that ``stage`` needs (``submit``
        needs the submit ones; ``release`` needs both)."""
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        wanted = STAGES[: STAGES.index(stage) + 1]
        out = []
        for prop in self.properties.values():
            if prop.required in wanted and _empty(getattr(obj, prop.name, None)):
                out.append(prop.name)
        return out

    # ----- applying ---------------------------------------------------------

    def apply(self, obj, compute: bool = True) -> dict:
        """Give ``obj`` every schema property; returns what changed.

        Adds missing properties with their defaults, carries renamed values
        over, refreshes computed values. Never removes or retypes a property
        it finds; a type clash is reported and left alone.
        """
        report = {"added": [], "renamed": [], "computed": [], "conflicts": []}
        present = set(obj.PropertiesList)
        for old, new in self.renamed.items():
            if old in present and new not in present and new in self.properties:
                value = getattr(obj, old)
                prop = self.properties[new]
                if obj.getTypeIdOfProperty(old) == prop.freecad_type:
                    self._add(obj, prop)
                    setattr(obj, new, value)
                    obj.removeProperty(old)
                    report["renamed"].append(f"{old}->{new}")
                    present = set(obj.PropertiesList)
        for prop in self.properties.values():
            if prop.name in present:
                have = obj.getTypeIdOfProperty(prop.name)
                if have != prop.freecad_type:
                    report["conflicts"].append(f"{prop.name} is {have}, schema says {prop.freecad_type}")
                    continue
                if prop.type == "enum":
                    _set_enum(obj, prop, keep=True)
            else:
                self._add(obj, prop)
                report["added"].append(prop.name)
            obj.setEditorMode(prop.name, ["ReadOnly"] if prop.readonly else [])
        watch(self)
        if compute:
            conflicted = _conflicted(report)
            computed = [p for p in self.properties.values() if p.source.startswith("computed:")]
            report["computed"] = refresh(obj, [p for p in computed if p.name not in conflicted])
        return report

    def _add(self, obj, prop: PropertyDef):
        obj.addProperty(prop.freecad_type, prop.name, self.group, prop.doc)
        if prop.type == "enum":
            _set_enum(obj, prop, keep=False)
        elif prop.default is not None:
            setattr(obj, prop.name, prop.default)


def load_schema(extra_layers: list[str] | None = None, user_layer: bool = True) -> Schema:
    """Built-in layer, then ``extra_layers`` (e.g. a vault's), then the user's."""
    paths = [os.path.join(config_dir(), BUILTIN_NAME)] + list(extra_layers or [])
    user = os.path.expanduser(USER_LAYER)
    if user_layer and os.path.isfile(user):
        paths.append(user)
    schema = None
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            try:
                raw = json.load(handle)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{path}: {exc}") from exc
        if schema is None:
            schema = Schema(raw.get("group", "BruceWorx"))
        schema.add_layer(raw, path)
    return schema


# ----- computed values ---------------------------------------------------------


def _bodies(obj) -> list:
    """Objects whose shape counts: ``obj`` itself if it carries a material,
    otherwise its direct children that do (an App::Part's bodies)."""
    if "ShapeMaterial" in getattr(obj, "PropertiesList", ()):
        return [obj]
    return [child for child in getattr(obj, "Group", None) or [] if "ShapeMaterial" in child.PropertiesList]


def _density_kg_per_mm3(material) -> float | None:
    try:
        text = material.getPhysicalValue("Density")
    except Exception:  # noqa: BLE001 -- no density on this material
        return None
    import FreeCAD  # noqa: PLC0415

    return FreeCAD.Units.Quantity(str(text)).getValueAs("kg/mm^3").Value


def _chosen(material) -> bool:
    """False for FreeCAD's "Default" placeholder: no material picked yet."""
    return getattr(material, "Name", "Default") != "Default"


def computed_mass(obj):
    """Mass of the bodies that have a material; 0 kg when none has one (the
    "Default" placeholder's density is not the part's). None: no bodies."""
    bodies = _bodies(obj)
    if not bodies:
        return None
    total = 0.0
    for body in bodies:
        shape = getattr(body, "Shape", None)
        if shape is None or shape.isNull() or not shape.Solids or not _chosen(body.ShapeMaterial):
            continue
        density = _density_kg_per_mm3(body.ShapeMaterial)
        if density is None:
            continue
        total += shape.Volume * density
    return f"{total} kg"


def computed_material(obj):
    names = {body.ShapeMaterial.Name for body in _bodies(obj) if _chosen(body.ShapeMaterial)}
    if not names:
        return "" if _bodies(obj) else None
    return names.pop() if len(names) == 1 else "Mixed: " + ", ".join(sorted(names))


COMPUTED = {"computed:mass": computed_mass, "computed:material": computed_material}

# Computed properties of every schema applied (or loaded by the observer):
# name -> (PropertyDef, property group). The observer refreshes these.
_WATCHED: dict[str, tuple[PropertyDef, str]] = {}


def watch(schema: Schema):
    """Have the observer keep ``schema``'s computed properties current."""
    for prop in schema.properties.values():
        if prop.source.startswith("computed:"):
            _WATCHED[prop.name] = (prop, schema.group)


def refresh(obj, props=None) -> list[str]:
    """Recompute the computed properties ``obj`` carries (default: every
    watched one) and set those whose value changed. Returns the names that
    have a value. A property of another type or group is left alone."""
    out = []
    for prop in list(props if props is not None else (p for p, _group in _WATCHED.values())):
        if prop.name not in obj.PropertiesList or obj.getTypeIdOfProperty(prop.name) != prop.freecad_type:
            continue
        value = COMPUTED[prop.source](obj)
        if value is None:
            continue
        out.append(prop.name)
        if not _same(getattr(obj, prop.name), value):
            setattr(obj, prop.name, value)
    return out


def _same(current, value) -> bool:
    """Whether ``current`` already holds ``value`` (a mass as "<x> kg")."""
    if isinstance(value, str) and value.endswith(" kg") and hasattr(current, "getValueAs"):
        try:
            have, want = current.getValueAs("kg").Value, float(value[:-3])
        except (ValueError, TypeError):
            return False
        return abs(have - want) <= 1e-12 * max(1.0, abs(want))
    return type(current) is type(value) and current == value


class _Observer:
    """Refreshes computed properties when a body's material or shape, or a
    part's contents, change. Owners are the changed object and the objects
    whose Group holds it (an App::Part and its bodies)."""

    WATCH = ("ShapeMaterial", "Shape", "Group")

    def __init__(self):
        self.busy = False

    def slotChangedObject(self, obj, prop):
        if prop not in self.WATCH or self.busy or not _WATCHED:
            return
        try:
            doc = obj.Document
            if doc.Restoring or doc.Transacting:
                return  # file load, undo/redo: the saved values come back too
            owners = [obj] + [p for p in obj.InList if obj in (getattr(p, "Group", None) or [])]
            self.busy = True
            for owner in owners:
                present = set(owner.PropertiesList)
                props = [p for name, (p, group) in _WATCHED.items()
                         if name in present and owner.getGroupOfProperty(name) == group]
                if props:
                    refresh(owner, props)
        except Exception as exc:  # noqa: BLE001 -- never break the edit that triggered this
            import FreeCAD  # noqa: PLC0415

            FreeCAD.Console.PrintLog(f"BruceWorx part properties: refresh failed: {exc}\n")
        finally:
            self.busy = False


_OBSERVER = None


def install_observer():
    """Start refreshing computed properties (once per session). Watches the
    built-in and user layers now, and every schema applied later."""
    global _OBSERVER
    if _OBSERVER is not None:
        return _OBSERVER
    import FreeCAD  # noqa: PLC0415

    try:
        watch(load_schema())
    except (OSError, SchemaError) as exc:
        FreeCAD.Console.PrintWarning(f"BruceWorx part properties: {exc}\n")
    _OBSERVER = _Observer()
    FreeCAD.addDocumentObserver(_OBSERVER)
    return _OBSERVER


def _set_enum(obj, prop: PropertyDef, keep: bool):
    current = getattr(obj, prop.name, None) if keep else None
    setattr(obj, prop.name, list(prop.values))
    if current in prop.values:
        setattr(obj, prop.name, current)
    elif prop.default in prop.values:
        setattr(obj, prop.name, prop.default)


def _conflicted(report) -> set:
    return {entry.split(" ", 1)[0] for entry in report["conflicts"]}


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
