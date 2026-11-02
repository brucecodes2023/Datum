# SPDX-License-Identifier: LGPL-2.1-or-later
"""Global Definitions of a study: parameters, named selections and local mesh sizes (roadmap SIM-07 phase P2).

Headless (FreeCADCmd runs all of it); the Model Builder (bruceworx_sim_model, bruceworx_sim_panes) is built on it.

Parameters
    One holder object per study, ``Parameters`` (an App::FeaturePython without a proxy, in the study's group). Each
    parameter is a dynamic property of the holder: App::PropertyFloat when it has no unit, else the quantity property
    of its unit (Length, Force, Pressure, ...), so FreeCAD's own expression engine evaluates it: the holder's
    property carries the parameter's expression ("2*t+1 mm", "F_load/4"), the engine parses it, checks the units,
    refuses a cycle and re-evaluates it on a recompute of the holder. The table (unit, description) is a hidden
    PropertyMap on the holder; no spreadsheet is involved (a spreadsheet would put a cell grid and an alias layer
    between the study and its numbers, and its aliases are not unit-checked).

    A Settings field takes an expression too. The field stores the expression text (hidden ``BW_Expr`` map on the
    object that owns the property) and the object property gets the evaluated value; ``apply(study)`` evaluates every
    stored expression through the holder (``evalExpression``: names, units and functions of FreeCAD's engine) and writes
    the values that changed. Fields are not bound with ``setExpression``: a recompute of a Netgen mesh object would
    re-mesh, and a Python-side write keeps degC, rpm and whole-number fields working.

Named selections
    ``Selection`` objects (App::FeaturePython, property ``References`` = PropertyLinkSubList) in the study's group.
    A support, load or local size points at one with its hidden ``BW_Selection`` link; ``sync(study)`` copies the
    selection's references into that object's own ``References``, which the FEM writers read. Several objects share one.
    An object may use several selections and have faces of its own beside them (``set_links``): its ``References`` are
    the union, duplicates removed (hidden ``BW_Selections``, ``BW_OwnFaces``).

Local mesh sizes
    ``LocalSize`` objects (``Size`` mm, ``References`` or a named selection) feed Netgen: ``write_size_file`` samples
    points on the selected faces, edges or bodies into a Netgen ``.msz`` local-size file, which the study's Netgen mesh
    object reads through its ``MeshSizeFile`` property (added to FemMeshShapeNetgenObject for this).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

ROLE_PROP = "BW_Role"
EXPR_PROP = "BW_Expr"
LINK_PROP = "BW_Selection"  # the first selection (older documents have only this)
LINKS_PROP = "BW_Selections"  # all of them
OWN_PROP = "BW_OwnFaces"  # the object's own faces beside them
META_PROP = "BW_ParamMeta"
ORDER_PROP = "BW_ParamOrder"
HOLDER_NAME = "Parameters"
QUANTITY_PROPS = ("Length", "Force", "Pressure", "Angle", "Mass", "Area", "Volume", "Speed", "Acceleration", "Frequency",
                  "Temperature", "Time", "Density", "Power", "Moment", "ThermalTransferCoefficient")
MAX_SIZE_POINTS = 60000
_IDENT = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)")


class DefsError(ValueError):
    """A definition that cannot be made; the text says why (shown under the field)."""


# ----- small helpers ----------------------------------------------------------------------------------------------


def _unit_text(unit):
    return {"rpm": "1/min", "degC": "K", "L": ""}.get(unit, unit)


def _is_unit(token):
    import FreeCAD

    try:
        FreeCAD.Units.Quantity("1 " + token)
        return True
    except Exception:
        return False


def internal(name):
    """The holder property that stores parameter ``name``: the name itself, or name_ when the name is also a unit
    (t = tonne, h = hour, m, s, N ...), which FreeCAD's expression parser would read as the unit."""
    return name + "_" if _is_unit(name) else name


def _after_number(text, at):
    before = text[:at].rstrip()
    return bool(before) and (before[-1].isdigit() or before[-1] == ".")


def rewrite(analysis, text):
    """The expression with each parameter that is also a unit name written as its holder property (2*t+1 mm -> 2*t_+1 mm).
    A unit name right after a number is the unit ('2 t' stays 2 tonnes)."""
    names = {n for n in parameter_names(analysis) if internal(n) != n}
    if not names:
        return text

    def sub(m):
        return internal(m.group(1)) if m.group(1) in names and not _after_number(m.string, m.start()) else m.group(0)

    return _IDENT.sub(sub, text)


def unrewrite(analysis, text):
    names = [n for n in parameter_names(analysis) if internal(n) != n]
    for n in names:
        text = re.sub(rf"(?<![\w.]){re.escape(n)}_(?![\w(])", n, text)
    return text


def role(obj):
    return str(getattr(obj, ROLE_PROP, "")) if obj is not None and ROLE_PROP in obj.PropertiesList else ""


def _hide(obj, prop):
    try:
        obj.setPropertyStatus(prop, "Hidden")
    except Exception:
        pass


def _objects(analysis, wanted):
    return [o for o in getattr(analysis, "Group", []) or [] if role(o) == wanted]


def _new_holder(analysis, kind, name, label):
    doc = analysis.Document
    obj = doc.addObject("App::FeaturePython", name)
    obj.Label = label
    obj.addProperty("App::PropertyString", ROLE_PROP, "BW", "[BW] What this object is for")
    setattr(obj, ROLE_PROP, kind)
    _hide(obj, ROLE_PROP)
    analysis.addObject(obj)
    return obj


# ----- parameters --------------------------------------------------------------------------------------------------


@dataclass
class Parameter:
    name: str
    expression: str
    unit: str
    value: float  # in ``unit`` (None when the expression fails)
    text: str  # "10 mm", "2.5"
    description: str
    error: str = ""


def holder(analysis, create=False):
    found = _objects(analysis, "parameters")
    if found:
        return found[0]
    if not create:
        return None
    obj = _new_holder(analysis, "parameters", HOLDER_NAME, "Parameters")
    obj.addProperty("App::PropertyMap", META_PROP, "BW", "[BW] Unit and description of each parameter")
    obj.addProperty("App::PropertyStringList", ORDER_PROP, "BW", "[BW] The parameters in table order")
    _hide(obj, META_PROP)
    _hide(obj, ORDER_PROP)
    return obj


def _meta(h):
    out = {}
    for k, v in dict(getattr(h, META_PROP, {}) or {}).items():
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = {}
    return out


def _set_meta(h, meta):
    setattr(h, META_PROP, {k: json.dumps(v) for k, v in meta.items()})


def _expressions(obj):
    return {path: str(expr) for path, expr in (getattr(obj, "ExpressionEngine", None) or [])}


def check_name(analysis, name, existing=False):
    name = str(name).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        raise DefsError(f"'{name}' is not a name: letters, digits and _, not starting with a digit")
    h = holder(analysis)
    if not existing:
        if (h is not None and name in [p.name for p in parameters(analysis)]):
            raise DefsError(f"a parameter named '{name}' exists")
        if internal(name) in (h.PropertiesList if h is not None else ()) or name in ("Label", "Label2", "Name", "Proxy", "Shape", "Visibility"):
            raise DefsError(f"'{name}' is reserved")
    return name


def property_type(unit):
    """The holder property type for a unit text ('' -> Float); raises DefsError for a unit with no property type."""
    import FreeCAD

    text = _unit_text(unit)
    if unit in ("", "degC", "L") or not text:
        return "App::PropertyFloat"
    try:
        q = FreeCAD.Units.Quantity("1 " + text)
    except Exception:
        raise DefsError(f"'{unit}' is not a unit") from None
    return "App::Property" + _quantity_kind(q)


_KIND_CACHE = {}


def _quantity_kind(q):
    key = str(q.Unit)
    if key in _KIND_CACHE:
        return _KIND_CACHE[key]
    import FreeCAD

    scratch = FreeCAD.newDocument("BW_unitprobe", "BW_unitprobe", True)
    try:
        obj = scratch.addObject("App::FeaturePython", "P")
        for kind in QUANTITY_PROPS:
            try:
                obj.addProperty("App::Property" + kind, "probe", "P", "d")
            except Exception:
                continue
            try:
                obj.probe = q
                _KIND_CACHE[key] = kind
                return kind
            except Exception:
                obj.removeProperty("probe")
    finally:
        FreeCAD.closeDocument(scratch.Name)
    raise DefsError(f"parameters in {q.Unit} are not supported (length, force, pressure, angle, mass, area, volume, speed, "
                    "acceleration, frequency, temperature, time, density, power, torque, film coefficient or none)")


def _translate(exc):
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else "invalid expression"
    m = re.search(r"Property '([^']+)' not found", text)
    if m:
        return f"unknown name '{m.group(1)}'"
    if "Unit mismatch" in text:
        return "the units do not match (" + text.split(":", 1)[-1].strip() + ")"
    if "cyclic" in text.lower():
        return "the expression refers to itself (a cycle)"
    if "Failed to parse" in text:
        return "cannot read the expression"
    return text


def parameters(analysis):
    h = holder(analysis)
    if h is None:
        return []
    meta = _meta(h)
    exprs = _expressions(h)
    out = []
    for name in list(getattr(h, ORDER_PROP, []) or []):
        if internal(name) not in h.PropertiesList:
            continue
        info = meta.get(name, {})
        unit = info.get("unit", "")
        value = getattr(h, internal(name))
        error = ""
        if h.State and "Invalid" in h.State:
            error = ""  # the holder reports one state for all; per parameter errors come from edit_parameter
        number = _quantity_number(value, unit)
        text = f"{number:.6g}{(' ' + unit) if unit else ''}"
        expr = exprs.get(internal(name))
        expr = (info.get("expr") or unrewrite(analysis, expr)) if expr is not None else None
        out.append(Parameter(name, expr if expr is not None else text.replace(" degC", "") if not unit else text, unit, number,
                             text, info.get("desc", ""), error))
    return out


def _quantity_number(value, unit):
    if hasattr(value, "getValueAs"):
        return float(value.getValueAs(_unit_text(unit))) if unit not in ("", "L") else float(value.Value)
    return float(value)


def parameter_names(analysis):
    h = holder(analysis)
    if h is None:
        return []
    return [n for n in list(getattr(h, ORDER_PROP, []) or []) if internal(n) in h.PropertiesList]


def has_identifier(analysis, text):
    """True when the text names a parameter or any word that is not a unit (so it is an expression, not a number)."""
    names = set(parameter_names(analysis))
    text = str(text)
    for m in _IDENT.finditer(text):
        token = m.group(1)
        if token in names and not (_is_unit(token) and _after_number(text, m.start())):
            return True
        if token in names or token in ("rpm", "degC", "L", "C", "K") or _is_unit(token):
            continue
        return True
    return False


def _commit_holder(h, doc):
    doc.recompute([h])
    if h.State and "Invalid" in h.State:
        raise DefsError("the parameters cannot be evaluated: " + _holder_error(h))


def _holder_error(h):
    try:
        return str(h.getStatusString())
    except Exception:
        return "see the Report view"


def add_parameter(analysis, name, expression, unit="", description=""):
    """Adds a parameter (one undo step). ``expression`` is a number, a quantity or an expression using other parameters."""
    name = check_name(analysis, name)
    ptype = property_type(unit)
    doc = analysis.Document
    doc.openTransaction(f"Add parameter {name}")
    try:
        h = holder(analysis, create=True)
        h.addProperty(ptype, internal(name), "Parameters", description or name)
        order = list(getattr(h, ORDER_PROP))
        order.append(name)
        setattr(h, ORDER_PROP, order)
        meta = _meta(h)
        meta[name] = {"unit": unit, "desc": description}
        _set_meta(h, meta)
        _assign_parameter(analysis, h, name, expression, unit)
    except Exception as exc:
        doc.abortTransaction()
        raise exc if isinstance(exc, DefsError) else DefsError(_translate(exc)) from None
    doc.commitTransaction()
    apply(analysis)
    return next(p for p in parameters(analysis) if p.name == name)


def _assign_parameter(analysis, h, name, expression, unit):
    import FreeCAD

    text = str(expression).strip()
    prop = internal(name)
    if not text:
        raise DefsError(f"{name}: a value or an expression is expected")
    if not has_identifier(analysis, text):  # a plain number or quantity
        if unit == "degC":
            text = text.replace("degC", "").replace("°C", "").strip()
        try:
            h.setExpression(prop, None)
            if h.getTypeIdOfProperty(prop) == "App::PropertyFloat":
                setattr(h, prop, float(text.replace(",", ".")))
            else:
                setattr(h, prop, text if re.search(r"[A-Za-z/]", text) else f"{float(text.replace(',', '.'))} {_unit_text(unit)}")
        except Exception as exc:
            raise DefsError(f"{name}: '{text}' is not a value in {unit or 'plain numbers'}"
                            + (f" ({exc})" if str(exc) else "")) from None
        doc = h.Document
        _commit_holder(h, doc)
        _remember_text(h, name, None)
        return
    engine_text = rewrite(analysis, text)
    try:
        value = h.evalExpression(engine_text)
    except Exception as exc:
        raise DefsError(f"{name}: {_translate(exc)}") from None
    _check_unit(value, unit, name)
    try:
        h.setExpression(prop, engine_text)
    except Exception as exc:
        raise DefsError(f"{name}: {_translate(exc)}") from None
    try:
        _commit_holder(h, h.Document)
    except DefsError as exc:
        raise DefsError(f"{name}: {exc}") from None
    _remember_text(h, name, text)


def _remember_text(h, name, text):
    """The expression as the user typed it (the engine rewrites spacing)."""
    meta = _meta(h)
    info = dict(meta.get(name, {}))
    if text:
        info["expr"] = text
    else:
        info.pop("expr", None)
    meta[name] = info
    _set_meta(h, meta)


def _check_unit(value, unit, label):
    import FreeCAD

    want = FreeCAD.Units.Quantity("1 " + _unit_text(unit)).Unit if _unit_text(unit) else FreeCAD.Units.Unit()
    got = value.Unit if hasattr(value, "Unit") else FreeCAD.Units.Unit()
    if got != want:
        have = str(got) if str(got) not in ("", "Unit: (0,0,0,0,0,0,0,0)") else "a plain number"
        need = unit or "a plain number"
        raise DefsError(f"{label}: expected {need}, the expression gives {have}")


def edit_parameter(analysis, name, expression=None, unit=None, description=None):
    """Changes a parameter's expression, unit or description (one undo step) and re-evaluates every field that uses it."""
    h = holder(analysis)
    if h is None or name not in parameter_names(analysis):
        raise DefsError(f"no parameter named '{name}'")
    meta = _meta(h)
    info = dict(meta.get(name, {}))
    old = next(p for p in parameters(analysis) if p.name == name)
    expr = old.expression if expression is None else expression
    new_unit = old.unit if unit is None else unit
    doc = analysis.Document
    doc.openTransaction(f"Edit parameter {name}")
    try:
        if new_unit != old.unit:
            ptype = property_type(new_unit)
            h.setExpression(internal(name), None)
            if ptype != h.getTypeIdOfProperty(internal(name)):
                desc = info.get("desc", name)
                h.removeProperty(internal(name))
                h.addProperty(ptype, internal(name), "Parameters", desc)
        info["unit"] = new_unit
        if description is not None:
            info["desc"] = description
        meta[name] = info
        _set_meta(h, meta)
        _assign_parameter(analysis, h, name, expr, new_unit)
        _refresh_dependents(analysis)
    except Exception as exc:
        doc.abortTransaction()
        raise exc if isinstance(exc, DefsError) else DefsError(_translate(exc)) from None
    doc.commitTransaction()
    apply(analysis)
    return next(p for p in parameters(analysis) if p.name == name)


def _refresh_dependents(analysis):
    """Re-evaluates the parameters that depend on others (a recompute of the holder does) so reading them is current."""
    h = holder(analysis)
    if h is not None:
        _commit_holder(h, analysis.Document)


def parameter_users(analysis, name):
    """[(object label, property)] of the fields and parameters whose expression names ``name``."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w(])")
    out = []
    h = holder(analysis)
    if h is not None:
        for prop, expr in _expressions(h).items():
            if prop != internal(name) and pattern.search(unrewrite(analysis, expr)):
                out.append((h.Label, prop.rstrip("_") if prop.endswith("_") else prop))
    for obj in _expression_objects(analysis):
        for prop, entry in _stored(obj).items():
            if pattern.search(entry.get("expr", "")):
                out.append((obj.Label, prop))
    return out


def remove_parameter(analysis, name):
    h = holder(analysis)
    if h is None or name not in parameter_names(analysis):
        raise DefsError(f"no parameter named '{name}'")
    users = parameter_users(analysis, name)
    if users:
        raise DefsError(f"'{name}' is used by " + ", ".join(f"{label} {prop}" for label, prop in users[:5])
                        + ("…" if len(users) > 5 else ""))
    doc = analysis.Document
    doc.openTransaction(f"Remove parameter {name}")
    try:
        h.setExpression(internal(name), None)
        h.removeProperty(internal(name))
        setattr(h, ORDER_PROP, [n for n in getattr(h, ORDER_PROP) if n != name])
        meta = _meta(h)
        meta.pop(name, None)
        _set_meta(h, meta)
    except Exception as exc:
        doc.abortTransaction()
        raise DefsError(str(exc)) from None
    doc.commitTransaction()


# ----- fields that use parameters ----------------------------------------------------------------------------------


def _expression_objects(analysis):
    """Objects of the study that may carry field expressions: the study, its members and the definitions."""
    out = [analysis]
    for obj in getattr(analysis, "Group", []) or []:
        if EXPR_PROP in obj.PropertiesList:
            out.append(obj)
    return [o for o in out if EXPR_PROP in o.PropertiesList]


def _stored(obj):
    if EXPR_PROP not in obj.PropertiesList:
        return {}
    out = {}
    for k, v in dict(getattr(obj, EXPR_PROP) or {}).items():
        try:
            out[k] = json.loads(v)
        except Exception:
            pass
    return out


def field_expression(obj, prop):
    """The expression a field was set with, or ''."""
    return _stored(obj).get(prop, {}).get("expr", "")


def field_error(obj, prop):
    return _stored(obj).get(prop, {}).get("error", "")


def evaluate(analysis, expression, unit="", label="value", integer=False):
    """The number an expression gives in ``unit`` ('' = plain, degC, rpm, L, mm...). Raises DefsError with the reason
    (unknown name, wrong unit, cycle)."""
    import FreeCAD

    h = holder(analysis)
    if h is None:
        # no parameters yet: only units and numbers work; the engine still reports unknown names
        h = _scratch_holder()
    try:
        value = h.evalExpression(rewrite(analysis, str(expression).strip()))
    except Exception as exc:
        raise DefsError(f"{label}: {_translate(exc)}") from None
    return _to_unit(value, unit, label)


_SCRATCH = {}


def _scratch_holder():
    """An object to evaluate plain expressions on when the study has no parameters yet (a hidden scratch document)."""
    import FreeCAD

    doc = _SCRATCH.get("doc")
    if doc is None or doc.Name not in FreeCAD.listDocuments():
        doc = FreeCAD.newDocument("BW_exprprobe", "BW_exprprobe", True)
        _SCRATCH["doc"] = doc
    return doc.getObject("P") or doc.addObject("App::FeaturePython", "P")


def _to_unit(value, unit, label):
    import FreeCAD

    is_q = hasattr(value, "Unit") and hasattr(value, "getValueAs")
    plain = (not is_q) or value.Unit == FreeCAD.Units.Unit()
    if unit in ("", "L"):
        if not plain:
            raise DefsError(f"{label}: a plain number is expected, the expression gives {value.Unit}")
        return float(value.Value if is_q else value)
    if plain:
        return float(value.Value if is_q else value)  # a plain number is taken in the field's unit
    if unit == "degC":
        if value.Unit != FreeCAD.Units.Quantity("1 K").Unit:
            raise DefsError(f"{label}: a temperature is expected, the expression gives {value.Unit}")
        return float(value.getValueAs("K")) - 273.15
    want = FreeCAD.Units.Quantity("1 " + _unit_text(unit))
    if value.Unit != want.Unit:
        raise DefsError(f"{label}: expected a value in {unit}, the expression gives {value.Unit}")
    return float(value.getValueAs(_unit_text(unit)))


def bind(obj, prop, expression, unit="", kind="float", label=""):
    """Remembers that ``obj.prop`` is the expression (the caller has written the value). One undo step with its caller."""
    if EXPR_PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyMap", EXPR_PROP, "BW", "[BW] Expressions of this object's fields (Parameters)")
        _hide(obj, EXPR_PROP)
    stored = _stored(obj)
    stored[prop] = {"expr": str(expression).strip(), "unit": unit, "kind": kind, "label": label or prop}
    setattr(obj, EXPR_PROP, {k: json.dumps(v) for k, v in stored.items()})


def unbind(obj, prop):
    """The field takes a plain value again."""
    stored = _stored(obj)
    if prop in stored:
        stored.pop(prop)
        setattr(obj, EXPR_PROP, {k: json.dumps(v) for k, v in stored.items()})


def _write_number(obj, prop, number, unit, kind):
    current = getattr(obj, prop)
    if kind == "int":
        number = int(round(number))
        if int(getattr(obj, prop)) != number:
            setattr(obj, prop, number)
        return
    if hasattr(current, "getValueAs"):
        text = f"{number + 273.15!r} K" if unit == "degC" else f"{number!r} {_unit_text(unit)}"
        setattr(obj, prop, text)
    else:
        setattr(obj, prop, float(number))


def apply(analysis):
    """Evaluates every stored field expression and writes the values that changed. Returns [(object name, property)]
    written. An expression that fails (a parameter was removed or edited into a wrong unit) keeps the old value and
    records the reason (``field_error``)."""
    changed = []
    for obj in _expression_objects(analysis):
        stored = _stored(obj)
        dirty = False
        for prop, entry in stored.items():
            try:
                number = evaluate(analysis, entry["expr"], entry.get("unit", ""), entry.get("label", prop))
                unit = entry.get("unit", "")
                old = getattr(obj, prop)
                old_number = _quantity_number(old, unit) if not isinstance(old, int) or isinstance(old, bool) else float(old)
                if entry.get("kind") == "int":
                    number = float(int(round(number)))
                if abs(old_number - number) > 1e-12 * max(1.0, abs(number)):
                    _write_number(obj, prop, number, unit, entry.get("kind", "float"))
                    changed.append((obj.Name, prop))
                if entry.get("error"):
                    entry["error"] = ""
                    dirty = True
            except Exception as exc:
                if entry.get("error") != str(exc):
                    entry["error"] = str(exc)
                    dirty = True
        if dirty:
            setattr(obj, EXPR_PROP, {k: json.dumps(v) for k, v in stored.items()})
    return changed


# ----- named selections --------------------------------------------------------------------------------------------


def selections(analysis):
    return _objects(analysis, "selection")


def _ensure_refs(obj, doc_text):
    if "References" not in obj.PropertiesList:
        obj.addProperty("App::PropertyLinkSubList", "References", "Selection", doc_text)


def make_selection(analysis, label="Selection", refs=None):
    """A named selection in the study. ``refs``: [(object or name, 'Face3' | ['Face3', ...])]."""
    doc = analysis.Document
    doc.openTransaction("Add named selection")
    try:
        obj = _new_holder(analysis, "selection", "Selection", label)
        _ensure_refs(obj, "[BW] The faces, edges or bodies of this named selection")
        if refs:
            obj.References = _normalise_refs(doc, refs)
    except Exception as exc:
        doc.abortTransaction()
        raise DefsError(str(exc)) from None
    doc.commitTransaction()
    return obj


def _normalise_refs(doc, refs):
    out = []
    for obj, subs in refs:
        if isinstance(obj, str):
            obj = doc.getObject(obj)
        if obj is None:
            raise DefsError("no such object in the selection")
        out.append((obj, [subs] if isinstance(subs, str) else list(subs)))
    return out


def selection_named(analysis, label):
    for obj in selections(analysis):
        if obj.Label == label or obj.Name == label:
            return obj
    return None


def selection_users(analysis, sel):
    """The objects that point at the named selection."""
    return [o for o in getattr(analysis, "Group", []) or []
            if any(x.Name == sel.Name for x in linked_all(o))]


def linked_all(obj):
    """The named selections an object uses, in order (the several-selection list; an older document has the single
    ``BW_Selection`` link only, which counts as a list of one)."""
    if LINKS_PROP in obj.PropertiesList:
        return [x for x in getattr(obj, LINKS_PROP) or [] if x is not None]
    one = getattr(obj, LINK_PROP, None) if LINK_PROP in obj.PropertiesList else None
    return [one] if one is not None else []


def linked(obj):
    """The first named selection an object uses, or None."""
    found = linked_all(obj)
    return found[0] if found else None


def own_faces(obj):
    """The object's own extra faces next to its named selections: [(object, [subs])]."""
    if OWN_PROP in obj.PropertiesList:
        return list(getattr(obj, OWN_PROP) or [])
    return []


def merge_refs(*lists):
    """Several reference lists as one, duplicates removed, an object's sub-elements kept together in first-seen
    order: [(object, [subs])]."""
    order, grouped = [], {}
    for refs in lists:
        for base, subs in refs:
            if base.Name not in grouped:
                grouped[base.Name] = (base, [])
                order.append(base.Name)
            seen = grouped[base.Name][1]
            for sub in ([subs] if isinstance(subs, str) else list(subs)):
                if sub not in seen:
                    seen.append(sub)
    return [grouped[n] for n in order]


def union_refs(obj):
    """What the object's faces are: its named selections' references, then its own extra faces, duplicates removed."""
    return merge_refs(*[list(sel.References) for sel in linked_all(obj)], own_faces(obj))


def _ensure_link_props(obj):
    if "References" not in obj.PropertiesList:
        raise DefsError(f"{obj.Label} has no faces to take from a selection")
    if LINK_PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyLink", LINK_PROP, "BW", "[BW] The (first) named selection this object uses")
        _hide(obj, LINK_PROP)
    if LINKS_PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyLinkList", LINKS_PROP, "BW", "[BW] The named selections this object uses")
        _hide(obj, LINKS_PROP)
    if OWN_PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyLinkSubList", OWN_PROP, "BW", "[BW] Faces of its own, next to the named selections")
        _hide(obj, OWN_PROP)


def set_links(obj, sels, extra=None):
    """Makes ``obj`` use the named selections ``sels`` (a list; empty: its own list again, keeping the faces it has)
    plus its own extra faces ``extra`` ([(object, [subs])], None: as they are). Its References become the union."""
    sels = [x for x in sels or [] if x is not None]
    if not sels:
        if LINKS_PROP in obj.PropertiesList and getattr(obj, LINKS_PROP):
            setattr(obj, LINKS_PROP, [])
        if LINK_PROP in obj.PropertiesList:
            setattr(obj, LINK_PROP, None)
        if OWN_PROP in obj.PropertiesList and getattr(obj, OWN_PROP):
            setattr(obj, OWN_PROP, [])
        return
    _ensure_link_props(obj)
    names = []
    for x in sels:  # a selection once
        if x.Name not in names:
            names.append(x.Name)
    unique = [next(x for x in sels if x.Name == n) for n in names]
    setattr(obj, LINKS_PROP, unique)
    setattr(obj, LINK_PROP, unique[0])
    if extra is not None:
        setattr(obj, OWN_PROP, merge_refs(extra))
    _copy_refs(obj)


def link(obj, sel):
    """Makes ``obj`` use the named selection ``sel`` alone (None: its own list again, keeping the faces it has)."""
    if sel is None:
        set_links(obj, [])
        return
    set_links(obj, [sel], extra=[])


def _refs_key(refs):
    return [(o.Name, tuple(s)) for o, s in refs]


def _copy_refs(obj):
    refs = union_refs(obj)
    if _refs_key(refs) != _refs_key(list(obj.References)):
        obj.References = refs
        return True
    return False


def sync(analysis):
    """Writes the union of each object's named selections and extra faces into its References. Returns the objects written."""
    written = []
    for obj in getattr(analysis, "Group", []) or []:
        if linked_all(obj) and "References" in obj.PropertiesList and _copy_refs(obj):
            written.append(obj)
    return written


def remove_selection(analysis, sel):
    """Removes a named selection; the objects that used it keep their faces as their own list."""
    doc = analysis.Document
    doc.openTransaction("Delete named selection")
    try:
        for user in selection_users(analysis, sel):
            # the faces stay: those of the removed selection become the object's own, the other selections remain
            rest = [x for x in linked_all(user) if x.Name != sel.Name]
            own = merge_refs(own_faces(user), list(sel.References))
            if rest:
                set_links(user, rest, extra=own)
            else:
                set_links(user, [])
        doc.removeObject(sel.Name)
    except Exception as exc:
        doc.abortTransaction()
        raise DefsError(str(exc)) from None
    doc.commitTransaction()


# ----- local mesh sizes --------------------------------------------------------------------------------------------


def local_sizes(analysis):
    return _objects(analysis, "localsize")


def make_local_size(analysis, size="2 mm", refs=None, selection=None, label="Local size"):
    """A local mesh size node: ``size`` on ``refs`` (faces, edges or solids) or on a named selection."""
    doc = analysis.Document
    doc.openTransaction("Add local mesh size")
    try:
        obj = _new_holder(analysis, "localsize", "LocalSize", label)
        obj.addProperty("App::PropertyLength", "Size", "Mesh", "[BW] The element size on the selection")
        obj.Size = size
        _ensure_refs(obj, "[BW] The faces, edges or bodies the size applies to")
        if selection is not None:
            sels = list(selection) if isinstance(selection, (list, tuple)) else [selection]
            set_links(obj, sels, extra=_normalise_refs(doc, refs) if refs else [])
        elif refs:
            obj.References = _normalise_refs(doc, refs)
    except Exception as exc:
        doc.abortTransaction()
        raise DefsError(str(exc)) from None
    doc.commitTransaction()
    return obj


def local_size_signature(analysis):
    """What the mesh depends on in the local sizes: each size, its selected elements and their geometry (rounded)."""
    out = []
    for obj in local_sizes(analysis):
        refs = []
        for base, subs in obj.References:
            for sub in subs:
                try:
                    shape = base.Shape.getElement(sub)
                    c = shape.CenterOfMass if hasattr(shape, "CenterOfMass") else shape.BoundBox.Center
                    refs.append((base.Name, sub, f"{c.x:.4g},{c.y:.4g},{c.z:.4g}", f"{getattr(shape, 'Area', 0):.4g}"))
                except Exception:
                    refs.append((base.Name, sub))
        out.append((obj.Name, f"{float(obj.Size.getValueAs('mm')):.6g}", sorted(refs)))
    return out


def _sample_face(face, h):
    import math

    pts = []
    u0, u1, v0, v1 = face.ParameterRange
    bb = face.BoundBox
    diag = max(bb.DiagonalLength, 1e-9)
    n = max(2, min(400, int(math.ceil(diag / h * 1.5))))
    for i in range(n + 1):
        for j in range(n + 1):
            u = u0 + (u1 - u0) * i / n
            v = v0 + (v1 - v0) * j / n
            try:
                if face.isInside(face.valueAt(u, v), 1e-6, True):
                    pts.append(face.valueAt(u, v))
            except Exception:
                continue
    return pts


def _sample_edge(edge, h):
    import math

    n = max(2, int(math.ceil(edge.Length / h * 2.0)))
    return [edge.valueAt(edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * i / n) for i in range(n + 1)]


def size_points(analysis):
    """[(x, y, z, h)] sampled on every local size node's elements (mm)."""
    out = []
    for obj in local_sizes(analysis):
        h = float(obj.Size.getValueAs("mm"))
        if h <= 0:
            continue
        for base, subs in obj.References:
            shape = getattr(base, "Shape", None)
            if shape is None or shape.isNull():
                continue
            for sub in subs:
                try:
                    el = shape.getElement(sub)
                except Exception:
                    continue
                if sub.startswith("Face"):
                    pts = _sample_face(el, h)
                elif sub.startswith("Edge"):
                    pts = _sample_edge(el, h)
                elif sub.startswith("Vertex"):
                    pts = [el.Point]
                elif sub.startswith("Solid"):
                    pts = [p for f in el.Faces for p in _sample_face(f, h)]
                else:
                    pts = []
                out += [(p.x, p.y, p.z, h) for p in pts]
                if len(out) > MAX_SIZE_POINTS:
                    raise DefsError(f"{obj.Label}: {h:g} mm on this selection needs more than {MAX_SIZE_POINTS} sample "
                                    "points; use a larger size or a smaller selection")
    return out


def write_size_file(analysis, folder):
    """Writes the Netgen local-size file for the study; returns its path, or '' when there is nothing to write."""
    points = size_points(analysis)
    if not points:
        return ""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{analysis.Name}_local.msz")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(points)}\n")
        for x, y, z, h in points:
            handle.write(f"{x:.6g} {y:.6g} {z:.6g} {h:.6g}\n")
        handle.write("0\n")
    return path


def prepare_mesh(analysis, mesh):
    """Before meshing: syncs named selections, writes the local-size file and points the Netgen object at it (or clears it).
    Raises DefsError when the build has no MeshSizeFile property but a local size is set."""
    sync(analysis)
    apply(analysis)
    active = [o for o in local_sizes(analysis) if len(o.References)]
    if not hasattr(mesh, "PropertiesList"):  # a stand-in mesh object (tests)
        return ""
    if "MeshSizeFile" not in mesh.PropertiesList:
        if active:
            raise DefsError("this build cannot mesh local sizes (the Netgen object has no MeshSizeFile)")
        return ""
    if not active:
        if mesh.MeshSizeFile:
            mesh.MeshSizeFile = ""
        return ""
    import tempfile

    path = write_size_file(analysis, os.path.join(tempfile.gettempdir(), "vectorlabs_localsize"))
    mesh.MeshSizeFile = path
    return path
