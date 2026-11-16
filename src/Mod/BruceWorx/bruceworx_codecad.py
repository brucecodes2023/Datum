# SPDX-License-Identifier: LGPL-2.1-or-later
"""Code-CAD parts (roadmap AUT-05): a build123d or CadQuery script kept as a parametric feature.

A code part is a Part::FeaturePython that holds a script (CodeCadScript) and shows the shape the
script makes. The script names its output with ``result = <shape or list>`` or ``show_object(shape,
name)``; one part per shape, all sharing the script (CodeCadGroup) and one run of it.

Dimensions. The script declares each one with a default::

    plate_t = param("plate_t", 4, "Plate thickness")     # a length, mm
    tilt = param.angle("tilt", 30)                        # degrees
    holes = param.count("holes", 6)                       # a whole number
    ratio = param.number("ratio", 0.5)                    # no unit

The first run adds any name the Parameters sheet (bruceworx_parameters) does not have yet. Each
declared name becomes a property of the part, bound to the sheet by an expression, so an edit in
the Parameters tab recomputes the part through FreeCAD's own dependency graph; typing a plain value
on the part itself unbinds that one dimension from the sheet.

Kernel bridge. build123d and CadQuery wrap OCCT through OCP; FreeCAD wraps the same OCCT through
TopoShape. The two bindings do not share objects, so a shape crosses as BREP text: exact, and well
under a millisecond for a small part.

Trust. A script is Python and runs again on recompute, so only scripts written on this Mac run
by themselves: make() and set_script() record the script's SHA-256 in a private file in the user's
application data. A part whose script is not on record (a file from someone else, or a script
changed outside Heuretes FreeCAD) keeps the shape saved in the file and says "Not trusted" until the user
approves it (trust_document). Dimension values are numbers, so they never need approval.

Speed. Results are cached by script and dimension values, so parts that share a script run it
once, and going back to earlier values costs nothing. preload() imports the libraries off the
main thread so the first run does not pay for it.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from heuretes_env import getenv
import re
import threading
import time
import traceback
import uuid
from collections import OrderedDict

SCRIPT = "CodeCadScript"
OUTPUT = "CodeCadOutput"
GROUP = "CodeCadGroup"
PARAMS = "CodeCadParams"
STATUS = "CodeCadStatus"
SECONDS = "CodeCadSeconds"
PROP_GROUP = "Code CAD"
DIM_GROUP = "Dimensions"
FILENAME = "<code-cad>"
UP_TO_DATE = "Up to date"
NOT_TRUSTED = "Not trusted: this script was not written on this Mac. Approve it to rebuild."
CACHE_SIZE = 48
# kind -> (property type, unit written to the sheet)
KINDS = {"length": ("App::PropertyDistance", "mm"), "angle": ("App::PropertyAngle", "deg"),
         "count": ("App::PropertyIntegerConstraint", ""), "number": ("App::PropertyFloat", "")}
COUNT_RANGE = (-1000000, 1000000, 1)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CELL = re.compile(r"^[A-Za-z]{1,2}\d+$")
_BUILDERS = {"BuildPart": "part", "BuildSketch": "sketch", "BuildLine": "line"}  # build123d builder -> result

_cache = OrderedDict()  # (script hash, ((name, value), ...)) -> Result
_stats = {"runs": 0, "hits": 0}


class CodeCadError(Exception):
    """Something about a code part worth one plain sentence."""


class ScriptError(CodeCadError):
    """The script failed. ``line`` is the line of the script (1-based) or None; ``text`` the traceback."""

    def __init__(self, short, text, line=None):
        super().__init__(short)
        self.short, self.text, self.line = short, text, line


# ----- dimensions a script declares ----------------------------------------------------------------


class Decl:
    def __init__(self, name, kind, default, comment):
        self.name, self.kind, self.default, self.comment = name, kind, default, comment

    def cell(self):
        """What goes into the Parameters sheet for the default: "4 mm", "30 deg", "6"."""
        unit = KINDS[self.kind][1]
        text = str(int(self.default)) if self.kind == "count" else format(float(self.default), ".12g")
        return f"{text} {unit}".strip()


def valid_name(name):
    """A dimension name: an identifier that a spreadsheet alias may take (not a cell address)."""
    return isinstance(name, str) and bool(_NAME.match(name)) and not _CELL.match(name)


class Param:
    """``param`` in a script: declares a dimension and returns its current value."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.declared = OrderedDict()
        self.used = {}

    def _take(self, kind, name, default, comment):
        if not valid_name(name):
            raise CodeCadError(f"{name!r} is not a dimension name: use letters, digits and _ (not a cell "
                               "address like A1)")
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise CodeCadError(f"{name}: the default must be a number, not {type(default).__name__}")
        known = self.declared.get(name)
        if known is not None and known.kind != kind:
            raise CodeCadError(f"{name} is declared twice, as a {known.kind} and as a {kind}")
        self.declared[name] = Decl(name, kind, default, str(comment or ""))
        value = self.values.get(name, default)
        value = int(round(value)) if kind == "count" else float(value)
        self.used[name] = value
        return value

    def __call__(self, name, default, comment=""):
        return self._take("length", name, default, comment)

    def length(self, name, default, comment=""):
        return self._take("length", name, default, comment)

    def angle(self, name, default, comment=""):
        return self._take("angle", name, default, comment)

    def count(self, name, default, comment=""):
        return self._take("count", name, default, comment)

    def number(self, name, default, comment=""):
        return self._take("number", name, default, comment)


# ----- running a script ------------------------------------------------------------------------------


class Result:
    def __init__(self, shapes, declared, used, stdout, stderr, seconds):
        self.shapes = shapes        # [(label or None, Part.Shape)]
        self.declared = declared    # OrderedDict name -> Decl
        self.used = used            # name -> value the run used
        self.stdout, self.stderr, self.seconds = stdout, stderr, seconds
        self.cached = False


def available():
    """None when the kernel binding is there, else why code-CAD cannot run in this FreeCAD."""
    try:
        import OCP  # noqa: F401
    except ImportError as exc:
        return ("code-CAD is not available in this FreeCAD: its Python has no OCP, the kernel binding "
                f"build123d and CadQuery need ({exc}). The build environment installs it (pixi.toml: ocp, "
                "build123d, cadquery)")
    return None


def script_hash(code):
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def _script_line(tb_or_exc):
    """The last line of the script a traceback passed through."""
    if isinstance(tb_or_exc, SyntaxError) and tb_or_exc.filename == FILENAME:
        return tb_or_exc.lineno
    line = None
    for frame in traceback.extract_tb(tb_or_exc.__traceback__):
        if frame.filename == FILENAME:
            line = frame.lineno
    return line


def _ocp_shapes(value, label, out):
    """Collect (label, OCP TopoDS_Shape) from what a script made: build123d or CadQuery objects, or lists."""
    from OCP.TopoDS import TopoDS_Shape

    if value is None:
        return
    if isinstance(value, TopoDS_Shape):
        if not value.IsNull() and not any(value.IsSame(shape) for _label, shape in out):
            out.append((label, value))
        return
    kind = type(value).__name__
    if kind in _BUILDERS:  # build123d: with BuildPart() as p: ... -> p.part
        return _ocp_shapes(getattr(value, _BUILDERS[kind]), label, out)
    if hasattr(value, "toCompound"):  # cadquery.Assembly
        return _ocp_shapes(value.toCompound(), label, out)
    if hasattr(value, "vals") and hasattr(value, "plane"):  # cadquery.Workplane: what is on the stack
        import cadquery

        shapes = [v for v in value.vals() if isinstance(v, cadquery.Shape)]
        if len(shapes) > 1:
            return _ocp_shapes(cadquery.Compound.makeCompound(shapes), label, out)
        return _ocp_shapes(shapes[0] if shapes else None, label, out)
    if isinstance(getattr(value, "wrapped", None), TopoDS_Shape):  # build123d.Shape, cadquery.Shape
        return _ocp_shapes(value.wrapped, label or getattr(value, "label", None) or None, out)
    if isinstance(value, (list, tuple)):
        for item in value:
            _ocp_shapes(item, label, out)
        return
    raise CodeCadError(f"{kind} is not a shape: give a build123d or CadQuery object (or a list of them)")


def _from_ocp(shape):
    import Part
    from OCP.BRepTools import BRepTools

    data = io.BytesIO()
    BRepTools.Write_s(shape, data)
    out = Part.Shape()
    out.importBrepFromString(data.getvalue().decode("ascii"))
    if out.isNull():
        raise CodeCadError("a shape came through empty")
    if not out.Placement.isIdentity():
        # Where the script put the shape (Pos(...) * shape) arrives as the shape's placement, and the part's own
        # Placement would replace it. Move the geometry there instead; the part's Placement stays the user's.
        import FreeCAD

        matrix = out.Placement.toMatrix()
        out.Placement = FreeCAD.Placement()
        out = out.transformed(matrix, True)
    return out


def run(code, values=None):
    """Run a script with the given dimension values (others take their defaults). Not cached."""
    missing = available()
    if missing:
        raise CodeCadError(missing)
    param = Param(values)
    shown = []

    def show_object(obj, name=None, *_args, **_kwargs):
        shown.append((obj, name))

    def show(*objects, names=None, **_kwargs):
        for index, obj in enumerate(objects):
            shown.append((obj, names[index] if names and index < len(names) else None))

    namespace = {"__name__": "__code_cad__", "param": param, "show_object": show_object, "show": show}
    out, err = io.StringIO(), io.StringIO()
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(code, FILENAME, "exec"), namespace)
            shapes = []
            for obj, name in shown:
                _ocp_shapes(obj, name, shapes)
            _ocp_shapes(namespace.get("result"), None, shapes)
            if not shapes:
                raise CodeCadError("the script made no shape: end it with result = <shape> (a build123d or "
                                   "CadQuery object, or a list of them), or call show_object(shape)")
            converted = [(label, _from_ocp(shape)) for label, shape in shapes]
    except CodeCadError as exc:
        if isinstance(exc, ScriptError):
            raise
        line = _script_line(exc)
        raise ScriptError(str(exc), str(exc), line) from None
    except BaseException as exc:
        line = _script_line(exc)
        text = traceback.format_exc(limit=8).strip()
        for label, stream in (("stdout", out), ("stderr", err)):
            if stream.getvalue():
                text += f"\n{label}:\n" + stream.getvalue()[-4000:]
        last = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
        short = f"line {line}: {last}" if line else last
        raise ScriptError(short, text, line) from None
    _stats["runs"] += 1
    return Result(converted, param.declared, param.used, out.getvalue()[-20000:], err.getvalue()[-8000:],
                  time.perf_counter() - start)


def _key(code, used):
    return script_hash(code), tuple(sorted((name, round(float(value), 9)) for name, value in used.items()))


def run_cached(code, values=None):
    """run(), remembered by script and by the values of the dimensions the script declares."""
    values = dict(values or {})
    digest = script_hash(code)
    for key, result in reversed(_cache.items()):
        if key[0] != digest:
            continue
        wanted = tuple(sorted((name, round(float(values.get(name, decl.default)), 9))
                              for name, decl in result.declared.items()))
        if wanted == key[1]:
            _cache.move_to_end(key)
            _stats["hits"] += 1
            result.cached = True
            return result
    result = run(code, values)
    _cache[_key(code, result.used)] = result
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    result.cached = False
    return result


def clear_cache():
    _cache.clear()


def stats():
    return dict(_stats, cached=len(_cache))


def preload():
    """Import the kernel binding and build123d on a background thread, so the first run is quick."""
    def work():
        try:
            import OCP  # noqa: F401
            import build123d  # noqa: F401
        except Exception:
            pass

    thread = threading.Thread(target=work, name="codecad-preload", daemon=True)
    thread.start()
    return thread


# ----- trust -------------------------------------------------------------------------------------------


def _trust_file():
    override = getenv("CODECAD_TRUST_FILE")
    if override:
        return override
    import FreeCAD

    return os.path.join(FreeCAD.getUserAppDataDir(), "BruceWorx", "codecad_trusted.json")


def _trusted_hashes():
    try:
        with open(_trust_file(), encoding="utf-8") as handle:
            return set(json.load(handle).get("sha256", []))
    except (OSError, ValueError):
        return set()


def trusted(code):
    return script_hash(code) in _trusted_hashes()


def trust(code):
    """Record that this script was written or approved on this Mac."""
    digest = script_hash(code)
    hashes = _trusted_hashes()
    if digest in hashes:
        return
    hashes.add(digest)
    path = _trust_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        json.dump({"sha256": sorted(hashes)}, handle)
    os.replace(tmp, path)


# ----- the feature -------------------------------------------------------------------------------------


class CodeCadProxy:
    """The Part::FeaturePython proxy. Saved files name this class: keep its module and name."""

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def execute(self, obj):
        code = getattr(obj, SCRIPT, "")
        if not code.strip():
            return
        if not trusted(code):
            _set(obj, STATUS, NOT_TRUSTED)  # the shape saved in the file stays
            return
        try:
            result = run_cached(code, values_of(obj))
        except ScriptError as exc:
            _set(obj, STATUS, "Error: " + exc.short)
            raise RuntimeError(exc.short) from None
        index = getattr(obj, OUTPUT, 0)
        if index >= len(result.shapes):
            text = f"the script no longer makes this shape (it makes {len(result.shapes)})"
            _set(obj, STATUS, "Error: " + text)
            raise RuntimeError(text)
        placement = obj.Placement
        obj.Shape = result.shapes[index][1]
        obj.Placement = placement
        _set(obj, STATUS, UP_TO_DATE)
        _set(obj, SECONDS, 0.0 if result.cached else round(result.seconds, 4))


class CodeCadView:
    """View provider proxy; the GUI side (icon, menu, editor) is in bruceworx_codecad_gui."""

    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def getIcon(self):
        try:
            import bruceworx_codecad_gui

            return bruceworx_codecad_gui.icon_for(getattr(self, "Object", None))
        except Exception:
            return ""

    def setupContextMenu(self, vobj, menu):
        try:
            import bruceworx_codecad_gui

            bruceworx_codecad_gui.context_menu(vobj.Object, menu)
        except Exception:
            pass

    def doubleClicked(self, vobj):
        try:
            import bruceworx_codecad_gui

            return bruceworx_codecad_gui.edit(vobj.Object)
        except Exception:
            return False


def _set(obj, prop, value):
    if getattr(obj, prop, None) != value:
        setattr(obj, prop, value)


def is_code_part(obj):
    try:
        return obj is not None and hasattr(obj, SCRIPT) and hasattr(obj, GROUP)
    except Exception:
        return False


def group(obj):
    """The parts that share obj's script, in output order."""
    gid = getattr(obj, GROUP, "")
    parts = [o for o in obj.Document.Objects if is_code_part(o) and getattr(o, GROUP, None) == gid]
    return sorted(parts, key=lambda o: getattr(o, OUTPUT, 0))


def code_parts(doc):
    return [o for o in doc.Objects if is_code_part(o)]


def values_of(obj):
    """The part's dimension values: name -> number (mm, degrees, count or plain)."""
    out = {}
    for name in getattr(obj, PARAMS, []) or []:
        value = getattr(obj, name, None)
        if value is None:
            continue
        out[name] = float(getattr(value, "Value", value))
    return out


def dimensions(obj):
    """[{name, kind, value, unit, expression}] for a part, in the script's order."""
    exprs = {path.lstrip("."): expr for path, expr in (getattr(obj, "ExpressionEngine", None) or [])}
    types = {ptype: (kind, unit) for kind, (ptype, unit) in KINDS.items()}
    out = []
    for name in getattr(obj, PARAMS, []) or []:
        if not hasattr(obj, name):
            continue
        kind, unit = types.get(obj.getTypeIdOfProperty(name), ("number", ""))
        value = getattr(obj, name)
        value = getattr(value, "Value", value)
        out.append({"name": name, "kind": kind, "value": int(value) if kind == "count" else round(float(value), 9),
                    "unit": unit, "expression": exprs.get(name)})
    return out


def _sheet_values(sheet):
    """Numbers the Parameters sheet holds: alias -> value in mm, degrees or plain."""
    out = {}
    if sheet is None:
        return out
    import bruceworx_parameters as P

    for _cell, alias in P.sheet_aliases(sheet):
        try:
            value = sheet.get(alias)
        except Exception:
            continue
        value = getattr(value, "Value", value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[alias] = float(value)
    return out


def _add_base_props(obj):
    obj.addProperty("App::PropertyString", SCRIPT, PROP_GROUP, "The script that makes this shape", 1)
    obj.addProperty("App::PropertyInteger", OUTPUT, PROP_GROUP, "Which of the script's shapes this is", 1 | 4)
    obj.addProperty("App::PropertyString", GROUP, PROP_GROUP, "The parts that share one script", 1 | 4)
    obj.addProperty("App::PropertyStringList", PARAMS, PROP_GROUP, "Dimensions the script declares", 1 | 4)
    obj.addProperty("App::PropertyString", STATUS, PROP_GROUP, "Up to date, not trusted, or the error", 1 | 8)
    obj.addProperty("App::PropertyFloat", SECONDS, PROP_GROUP, "How long the last run of the script took", 1 | 8)
    obj.setEditorMode(SCRIPT, 2)  # long text: shown in the script editor, not the property list


def _sync_sheet(doc, declared):
    """Add the declared names the Parameters sheet lacks; returns (sheet, names added)."""
    import bruceworx_parameters as P

    if not declared:
        return P.find_sheet(doc), []
    sheet = P.find_sheet(doc) or P.make_sheet(doc)
    have = {alias for _cell, alias in P.sheet_aliases(sheet)}
    added = []
    for decl in declared.values():
        if decl.name in have:
            continue
        row = P.next_row(sheet.getUsedCells())
        sheet.set(f"A{row}", decl.name)
        sheet.set(f"B{row}", decl.cell())
        try:
            sheet.setAlias(f"B{row}", decl.name)
        except Exception as exc:
            sheet.clear(f"A{row}:B{row}")
            raise CodeCadError(f"{decl.name} cannot be a parameter name ({exc}): names of units such as mm, "
                               "T or g are taken") from None
        if decl.comment:
            sheet.set(f"C{row}", decl.comment)
        added.append(decl.name)
    if added:
        sheet.recompute()
        if sheet.Document is not doc:
            sheet.Document.recompute()
    return sheet, added


def _sync_props(obj, declared, sheet, values):
    """Give the part one property per declared dimension, bound to the sheet; drop the ones no longer declared."""
    import bruceworx_parameters as P

    ref = P.sheet_ref(sheet, obj.Document)
    old = list(getattr(obj, PARAMS, []) or [])
    for name in old:
        decl = declared.get(name)
        if hasattr(obj, name) and (decl is None or obj.getTypeIdOfProperty(name) != KINDS[decl.kind][0]):
            obj.setExpression(name, None)
            obj.removeProperty(name)
    for decl in declared.values():
        if not hasattr(obj, decl.name):
            obj.addProperty(KINDS[decl.kind][0], decl.name, DIM_GROUP, decl.comment or decl.name)
            value = values.get(decl.name, decl.default)
            if decl.kind == "count":
                setattr(obj, decl.name, (int(round(value)),) + COUNT_RANGE)
            else:
                setattr(obj, decl.name, float(value))
            obj.setExpression(decl.name, f"{ref}.{decl.name}")
        elif decl.name not in old:
            raise CodeCadError(f"{decl.name} cannot be a dimension name: the part already has a property "
                               "of that name")
    setattr(obj, PARAMS, list(declared))


def _new_part(doc, gid, index):
    import FreeCAD

    obj = doc.addObject("Part::FeaturePython", "CodeCad")
    CodeCadProxy(obj)
    _add_base_props(obj)
    setattr(obj, GROUP, gid)
    setattr(obj, OUTPUT, index)
    if FreeCAD.GuiUp:
        CodeCadView(obj.ViewObject)
    return obj


_TAKEN = {"Label", "Label2", "Shape", "Placement", "Visibility", "Proxy", "ExpressionEngine", "Name", "Document",
          "TypeId", "State", "ViewObject", SCRIPT, OUTPUT, GROUP, PARAMS, STATUS, SECONDS}


def _check_names(declared):
    for name in declared:
        if name in _TAKEN:
            raise CodeCadError(f"{name} cannot be a dimension name: every part has a property of that name")


def _apply(doc, parts, code, result, name):
    """Make the group match a finished run: script, dimensions, one part per shape, shapes."""
    _check_names(result.declared)
    sheet, added = _sync_sheet(doc, result.declared)
    gid = getattr(parts[0], GROUP) if parts else uuid.uuid4().hex
    while len(parts) < len(result.shapes):
        parts.append(_new_part(doc, gid, len(parts)))
    for index, obj in enumerate(parts):
        _sync_props(obj, result.declared, sheet, result.used)
        setattr(obj, SCRIPT, code)
        if index < len(result.shapes):
            label, shape = result.shapes[index]
            placement = obj.Placement
            obj.Shape = shape
            obj.Placement = placement
            if name is not None:
                obj.Label = name if len(result.shapes) == 1 else f"{name} {index + 1}"
            elif label and obj.Label.startswith("CodeCad"):
                obj.Label = str(label)
    trust(code)
    doc.recompute()
    return parts, added


def make(doc, code, name=None):
    """New code part(s) from a script written here. Raises ScriptError and leaves the document alone
    when the script fails. Returns (parts, result, parameter names added to the sheet)."""
    import bruceworx_parameters as P

    result = run_cached(code, _sheet_values(P.find_sheet(doc)))
    parts, added = _apply(doc, [], code, result, name)
    return parts, result, added


def set_script(obj, code, name=None):
    """Replace the script of obj's group. Dimensions keep their current values."""
    import bruceworx_parameters as P

    if not is_code_part(obj):
        raise CodeCadError(f"{obj.Label} is not a code part")
    doc = obj.Document
    values = _sheet_values(P.find_sheet(doc))
    values.update(values_of(obj))
    result = run_cached(code, values)
    parts, added = _apply(doc, group(obj), code, result, name)
    return parts, result, added


def rebuild(obj):
    """Run the script again even when nothing changed (the cache is skipped)."""
    code = getattr(obj, SCRIPT, "")
    digest = script_hash(code)
    for key in [k for k in _cache if k[0] == digest]:
        del _cache[key]
    for part in group(obj):
        part.touch()
    obj.Document.recompute()


def untrusted(doc):
    """The code parts of a document whose scripts are not on record here."""
    return [o for o in code_parts(doc) if getattr(o, SCRIPT, "").strip() and not trusted(getattr(o, SCRIPT))]


def trust_document(doc):
    """The user approved the document's scripts: record them and rebuild."""
    parts = untrusted(doc)
    for obj in parts:
        trust(getattr(obj, SCRIPT))
    for obj in parts:
        obj.touch()
    if parts:
        doc.recompute()
    return parts


def to_solid(obj):
    """A plain copy of the part's shape that no script drives; the code part is removed."""
    doc = obj.Document
    plain = doc.addObject("Part::Feature", obj.Name + "_solid")
    plain.Shape = obj.Shape
    plain.Label = obj.Label
    doc.removeObject(obj.Name)
    return plain
