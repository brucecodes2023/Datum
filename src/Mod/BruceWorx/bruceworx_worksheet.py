# SPDX-License-Identifier: LGPL-2.1-or-later
"""The design worksheet (roadmap MOD-15) and the spec that drives the model (MOD-17).

A worksheet is a text file, ``<name>.bwsheet``: a short header, Markdown text with LaTeX math, and fenced
``calc`` blocks (plan chapter 29, "The design", answers to its questions taken as recommended):

    ---
    bw-worksheet: 1
    title: Bracket spec
    describes: Bracket.FCStd        # the model whose Parameters sheet bare names come from
    results: {"t": "4.2 mm"}        # written at save, so a revision shows what was computed
    ---
    ## Load
    ```calc
    F = 300 N                                   # typed in the worksheet: a spec input
    t = sqrt(6 * F * L / (b * sigma_allow))     in mm  => Parameters
    size = F > 250 N ? <<M5x0.8>> : <<M4x0.7>>  => Parameters
    S = sigma_allow * b * t^2 / (6 * F * L)     check S >= 1 "bends"
    z_min = 2 / sin(alpha)^2                    warn z_min > Zs "sun undercut"
    ```

A calc line is ``name = expression``, then optionally ``in <unit>`` (how it is shown), ``=> Parameters``
(write it into the model's Parameters sheet), and ``check``/``warn <condition> "<label>"``. The
expression is FreeCAD's own grammar (what works in a Pad's length works here), after three rewrites
the plan's spike called for: a number followed by a product of units (``0.4 N*m``) becomes
``(0.4 * 1 N * 1 m)``; ``rpm`` becomes ``/ 1 min``; a name that is a unit (``m``, ``T``) is refused with a
suggestion. A line reads names defined above it, then the Parameters sheet's aliases of the model it
describes (``Zr``), then explicit paths (``Motor.HoldingTorque``). Each line is evaluated on its own:
a failing line spoils only the lines that read it ("depends on line k").

The model never reads the worksheet: ``=> Parameters`` writes the line into the Parameters sheet (bare
model names stay names, worksheet-only names become their values), so the model opens and recomputes
without it, and the sheet's own dependents (dimensions, joints, Hole sizes, drawings) follow (MOD-17).
With live on, a recompute of the model re-evaluates the worksheet.

``outputs`` in the header names files made from the model (3MF, STL, STEP, DXF); they are rewritten when
the object's shape changes, after a worksheet change (``apply``) or a live re-evaluation.

Not here yet: the worksheet tab with rendered math (plan step 4), PDF (step 6), the vault item type and
its out-of-date state (step 7).
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field

FORMAT_KEY = "bw-worksheet"
SUFFIX = ".bwsheet"
PREFIX = "ws_"  # worksheet values on the context VarSet: "ws_<name>", clear of its own properties
TOLERANCE = 1e-9  # relative, for "a == b" in a check
CONTEXT_DOC = "BWWorksheetContext"

# FreeCAD's unit names (Base/Unit and the expression grammar): a name cannot be one of these
UNIT_NAMES = frozenset("""
nm um µm mm cm dm m km in ft thou mil yd mi
ug mg g kg t lb oz st cwt
s min h
mA A kA mV V kV
uK mK K
mol mmol cd
N kN MN mN lbf
Pa kPa MPa GPa psi ksi
J kJ mJ eV Ws kWh VAs CV
W kW mW VA
Hz kHz MHz GHz
F mF uF µF nF pF C T G Wb H mH uH nH
S mS uS Ohm kOhm MOhm
l ml
deg rad gon ° M
""".split())
CONSTANTS = frozenset({"pi", "e"})
KEYWORDS = ("in", "check", "warn")
DISPLAY_ALIASES = {"rpm": "1/min", "Nm": "N*m"}
# how a quantity is shown when its line names no unit (the header's "units" can change these)
DEFAULT_UNITS = {"Length": "mm", "Moment": "N*m", "Pressure": "MPa", "Stress": "MPa", "Force": "N",
                 "Mass": "kg", "Angle": "deg", "Power": "W"}
SUGGEST = {"m": "m_n", "T": "T_in", "N": "N_teeth", "A": "A_s", "F": "F_load", "s": "s_1", "t": "t_wall",
           "g": "g_0", "l": "l_1", "h": "h_1", "C": "C_dyn", "S": "S_f", "H": "H_1", "G": "G_mod", "M": "M_b",
           "e": "e_1", "pi": "pi_1", "K": "K_t", "V": "V_in", "W": "W_1", "J": "J_1"}

_IDENT = re.compile(r"[A-Za-z_µ°][A-Za-z0-9_]*")
_NUMBER = re.compile(r"(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")
_UNIT_TOKEN = re.compile(r"([A-Za-zµ°][A-Za-z0-9]*)(\^-?\d+)?")
_LABEL = re.compile(r'\s*"([^"]*)"\s*$')


class WorksheetError(Exception):
    pass


# --------------------------------------------------------------------------- the file


@dataclass
class CalcLine:
    number: int  # 1-based over every calc line of the worksheet
    source: str
    file_line: int = 0
    name: str = ""
    expr: str = ""
    unit: str = ""
    export: bool = False
    checks: list = field(default_factory=list)  # [(kind "check"|"warn", condition, label)]
    error: str = ""  # a syntax error found while parsing


@dataclass
class Block:
    kind: str  # "text" or "calc"
    text: str
    lines: list = field(default_factory=list)


@dataclass
class Document:
    header: dict
    blocks: list

    def calc_lines(self):
        return [line for b in self.blocks if b.kind == "calc" for line in b.lines]


def _header_value(text):
    text = text.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except ValueError:
        pass
    cut = re.search(r"\s#\s", text)
    return (text[:cut.start()] if cut else text).strip()


def parse(text):
    """The worksheet in ``text`` as a Document (header, text and calc blocks)."""
    lines = text.splitlines()
    header, i = {}, 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for raw in lines[1:j]:
                    if ":" in raw and not raw.lstrip().startswith("#"):
                        key, value = raw.split(":", 1)
                        header[key.strip()] = _header_value(value)
                i = j + 1
                break
    blocks, text_lines, count = [], [], 0
    while i < len(lines):
        if lines[i].strip().startswith("```calc"):
            if text_lines:
                blocks.append(Block("text", "\n".join(text_lines)))
                text_lines = []
            start, body = i, []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                body.append(lines[i])
                i += 1
            calc = []
            for k, src in enumerate(body):
                line = parse_line(src, count + 1)
                if line is not None:
                    count += 1
                    line.file_line = start + k + 2
                    calc.append(line)
            blocks.append(Block("calc", "\n".join(body), calc))
            i += 1  # the closing fence
            continue
        text_lines.append(lines[i])
        i += 1
    if text_lines:
        blocks.append(Block("text", "\n".join(text_lines)))
    return Document(header, blocks)


def _header_line(key, value):
    if isinstance(value, (dict, list)) or (isinstance(value, str) and (not value or value != value.strip()
                                                                      or re.search(r"[#:{}\[\]\"]", value))):
        value = json.dumps(value, ensure_ascii=False)
    return f"{key}: {value}"


def set_header(text, key, value):
    """``text`` with one header line set (added before the closing ``---``, or a header made), the rest of
    the file exactly as it was."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for k in range(1, j):
                    if re.match(rf"\s*{re.escape(key)}\s*:", lines[k]):
                        lines[k] = _header_line(key, value)
                        return "\n".join(lines)
                lines.insert(j, _header_line(key, value))
                return "\n".join(lines)
    return "\n".join(["---", _header_line(FORMAT_KEY, 1), _header_line(key, value), "---"] + lines)


def dump(doc):
    """The worksheet as text again (header first; blocks as they were written)."""
    out = ["---"]
    header = dict(doc.header)
    header.setdefault(FORMAT_KEY, 1)
    for key in [FORMAT_KEY] + [k for k in header if k != FORMAT_KEY]:
        out.append(_header_line(key, header[key]))
    out.append("---")
    for block in doc.blocks:
        if block.kind == "calc":
            out += ["```calc", block.text, "```"] if block.text else ["```calc", "```"]
        else:
            out.append(block.text)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- one calc line


def _segments(text):
    """[(kind, start, end)]: the parts of ``text`` that are code, a <<string>>, or a "string"."""
    out, i, start, n = [], 0, 0, len(text)
    while i < n:
        if text.startswith("<<", i):
            j = text.find(">>", i + 2)
            j = n if j < 0 else j + 2
        elif text[i] == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j + 1
        else:
            i += 1
            continue
        if i > start:
            out.append(("code", start, i))
        out.append(("string", i, j))
        i = start = j
    if start < n:
        out.append(("code", start, n))
    return out


def _strip_comment(src):
    for kind, a, b in _segments(src):
        if kind == "code":
            m = re.search(r"(^|\s)#(\s|$)", src[a:b])
            if m:
                return src[:a + m.start()].rstrip()
    return src.rstrip()


def _top_level_keywords(src):
    """[(keyword, start, end)] of "in", "check", "warn" and "=>" outside brackets and strings. An "in" is
    the display unit only when a unit follows it up to the next keyword (``3 in + 2 mm in in``: the
    first is the inch)."""
    found, depth = [], 0
    for kind, a, b in _segments(src):
        if kind != "code":
            continue
        i = a
        while i < b:
            ch = src[i]
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif depth == 0 and src.startswith("=>", i):
                found.append(("=>", i, i + 2))
                i += 2
                continue
            elif depth == 0 and (ch.isalpha()) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_.#")):
                m = _IDENT.match(src, i)
                word = m.group(0)
                if word in KEYWORDS and (i == 0 or src[i - 1].isspace()) and (m.end() >= len(src) or src[m.end()].isspace()):
                    found.append((word, i, m.end()))
                i = m.end()
                continue
            i += 1
    out = []
    for k, (word, start, end) in enumerate(found):
        if word == "in":
            stop = next((s for w, s, _e in found[k + 1:] if w != "in"), len(src))
            if not _UNIT_TEXT.fullmatch(src[end:stop].strip()) or any(w == "in" for w, _s, _e in out):
                continue
        out.append((word, start, end))
    return out


_UNIT_TEXT = re.compile(r"(1\s*/\s*)?[A-Za-zµ°][A-Za-z0-9]*(\^-?\d+)?(\s*[*/]\s*[A-Za-zµ°][A-Za-z0-9]*(\^-?\d+)?)*")


def _assignment(text):
    """(name, expression) when ``text`` is "name = expression", else None."""
    for kind, a, b in _segments(text):
        if kind != "code":
            continue
        for m in re.finditer(r"=", text[a:b]):
            k = a + m.start()
            prev, nxt = text[k - 1:k], text[k + 1:k + 2]
            if prev in "<>!=" or nxt in "=>":
                continue
            name = text[:k].strip()
            return (name, text[k + 1:].strip()) if name else None
        return None
    return None


def parse_line(src, number=0):
    """A calc line, or None for a blank or comment line."""
    text = _strip_comment(src)
    if not text.strip():
        return None
    line = CalcLine(number, src.rstrip())
    marks = _top_level_keywords(text)
    first = marks[0][1] if marks else len(text)
    head = text[:first].strip()
    tails = [(word, text[end:(marks[k + 1][1] if k + 1 < len(marks) else len(text))].strip())
             for k, (word, _s, end) in enumerate(marks)]
    if head:
        got = _assignment(head)
        if got is None:
            line.error = "a calc line is: name = expression"
            return line
        line.name, line.expr = got
        why = name_problem(line.name)
        if why:
            line.error = why
            return line
    elif not tails or tails[0][0] not in ("check", "warn"):
        line.error = "a calc line is: name = expression"
        return line
    for word, body in tails:
        if word == "in":
            line.unit = body
        elif word == "=>":
            if body != "Parameters":
                line.error = '"=>" writes to Parameters only: "=> Parameters"'
            line.export = True
        else:
            m = _LABEL.search(body)
            label = m.group(1) if m else ""
            cond = body[:m.start()].strip() if m else body
            if not cond:
                line.error = f"{word} needs a condition"
            line.checks.append((word, cond, label))
    if line.export and not line.name:
        line.error = "only a named line can be written to Parameters"
    return line


def name_problem(name):
    """Why ``name`` cannot name a value, or ""."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        return f"'{name}' is not a name: letters, digits and _ only, not starting with a digit"
    if name in UNIT_NAMES or name in CONSTANTS:
        hint = SUGGEST.get(name, f"{name}_1")
        what = "a unit" if name in UNIT_NAMES else "a constant"
        return f"'{name}' is {what}; call it '{hint}' instead"
    if name in KEYWORDS:
        return f"'{name}' is a worksheet word"
    if re.fullmatch(r"[A-Za-z]{1,2}\d+", name):
        return f"'{name}' looks like a spreadsheet cell; add a letter or _"
    return ""


# --------------------------------------------------------------------------- rewrites and names


def rewrite(expr):
    """The spike's gaps closed: "0.4 N*m" -> "(0.4 * 1 N * 1 m)", "600 rpm" -> "(600 / 1 min)"."""
    out = []
    for kind, a, b in _segments(expr):
        part = expr[a:b]
        out.append(part if kind == "string" else _rewrite_code(part))
    return "".join(out)


def _rewrite_code(text):
    out, i = [], 0
    for m in _NUMBER.finditer(text):
        if m.start() < i or (m.start() > 0 and (text[m.start() - 1].isalnum() or text[m.start() - 1] in "_.")):
            continue
        j = m.end()
        k = j
        while k < len(text) and text[k] == " ":
            k += 1
        if text.startswith("rpm", k) and not (text[k + 3:k + 4].isalnum() or text[k + 3:k + 4] == "_"):
            out += [text[i:m.start()], f"({m.group(0)} / 1 min)"]
            i = k + 3
            continue
        units, pos = [], k
        while True:
            u = _UNIT_TOKEN.match(text, pos)
            if not u or u.group(1) not in UNIT_NAMES:
                break
            units.append(u.group(0))
            pos = u.end()
            star = re.match(r"\s*\*\s*", text[pos:])
            if not star:
                break
            nxt = _UNIT_TOKEN.match(text, pos + star.end())
            if not nxt or nxt.group(1) not in UNIT_NAMES:
                break
            pos += star.end()
        if len(units) >= 2:
            out += [text[i:m.start()], "(" + " * ".join([m.group(0)] + [f"1 {u}" for u in units]) + ")"]
            i = pos
    out.append(text[i:])
    return "".join(out)


@dataclass
class Ref:
    start: int
    end: int
    text: str
    kind: str  # "name" (to look up), "object" (Obj.prop, Doc#Obj.prop), "func", "unit", "const"


def refs(expr):
    """The identifiers of an expression and what each is."""
    out = []
    for kind, a, b in _segments(expr):
        if kind != "code":
            continue
        for m in _IDENT.finditer(expr, a, b):
            s, e, word = m.start(), m.end(), m.group(0)
            prev = expr[s - 1] if s > 0 else ""
            if prev and (prev in ".#" or prev.isalnum() or prev == "_"):
                continue  # a property after a dot, or the tail of a number like 1e3
            rest = expr[e:b].lstrip()
            if rest.startswith("("):
                kind_ = "func"
            elif expr[e:e + 1] in (".", "#"):
                kind_ = "object"
            elif word in CONSTANTS:
                kind_ = "const"
            elif word in UNIT_NAMES:
                kind_ = "unit"  # a name cannot be a unit (name_problem), so this is one
            else:
                kind_ = "name"
            out.append(Ref(s, e, word, kind_))
    return out


def substitute(expr, mapping):
    """``expr`` with each Ref replaced by ``mapping(ref)`` (None leaves it)."""
    out, i = [], 0
    for ref in refs(expr):
        new = mapping(ref)
        if new is None:
            continue
        out += [expr[i:ref.start], new]
        i = ref.end
    out.append(expr[i:])
    return "".join(out)


def _split_eq(cond):
    """(left, right) of a top-level "a == b", else None."""
    depth = 0
    for kind, a, b in _segments(cond):
        if kind != "code":
            continue
        for k in range(a, b - 1):
            ch = cond[k]
            depth += ch in "([" and 1 or 0
            depth -= ch in ")]" and 1 or 0
            if depth == 0 and cond.startswith("==", k):
                return cond[:k].strip(), cond[k + 2:].strip()
    return None


# --------------------------------------------------------------------------- evaluation (FreeCAD)


@dataclass
class Result:
    line: CalcLine
    status: str = "ok"  # "ok", "error", "depends"
    value: object = None
    shown: str = ""
    message: str = ""
    checks: list = field(default_factory=list)  # [(kind, label, passed, message)]
    reads: list = field(default_factory=list)  # bare names it read
    inputs: dict = field(default_factory=dict)  # model alias -> value it had

    @property
    def ok(self):
        return self.status == "ok"


def _context():
    import FreeCAD

    doc = FreeCAD.listDocuments().get(CONTEXT_DOC)
    if doc is None:
        try:
            doc = FreeCAD.newDocument(CONTEXT_DOC, hidden=True, temp=True)
        except TypeError:
            doc = FreeCAD.newDocument(CONTEXT_DOC)
    return doc


def literal(value):
    """A value as an expression FreeCAD reads back: 15 mm -> "(15 * 1 mm)", a text -> "<<M4x0.7>>"."""
    if isinstance(value, str):
        return f"<<{value}>>"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    sig = tuple(value.Unit.Signature)
    v = repr(float(value.Value))
    if not any(sig):
        return v
    names = ("mm", "kg", "s", "A", "K", "mol", "cd", "deg")
    num = [f"1 {n}" + (f"^{p}" if p != 1 else "") for n, p in zip(names, sig) if p > 0]
    den = [f"1 {n}" + (f"^{-p}" if p != -1 else "") for n, p in zip(names, sig) if p < 0]
    text = " * ".join([v] + num)
    if den:
        text += " / (" + " * ".join(den) + ")"
    return f"({text})"


def _number(value):
    if hasattr(value, "Value"):
        return float(value.Value)
    if isinstance(value, bool):
        return float(value)
    return float(value)


def shown(value, unit="", units=None):
    """How a value is shown: in ``unit`` if given (checked against its dimension), else by its type."""
    import FreeCAD

    if isinstance(value, str):
        return value
    if not hasattr(value, "Unit"):
        if unit:
            raise WorksheetError(f"a plain number has no unit to show in {unit}")
        return _fmt(_number(value))
    want = DISPLAY_ALIASES.get(unit, unit)
    if not want:
        table = dict(DEFAULT_UNITS)
        table.update(units or {})
        want = table.get(value.Unit.Type, "")
        if not want:
            if not any(value.Unit.Signature):
                return _fmt(value.Value)
            return value.UserString
    try:
        ref = FreeCAD.Units.Quantity("1 " + want)
    except Exception as exc:  # noqa: BLE001
        raise WorksheetError(f"'{unit or want}' is not a unit") from exc
    if tuple(ref.Unit.Signature) != tuple(value.Unit.Signature):
        kind = value.Unit.Type or "this quantity"
        raise WorksheetError(f"{kind} cannot be shown in {unit or want} ({ref.Unit.Type or 'another quantity'})")
    return f"{_fmt(value.Value / ref.Value)} {unit or want}"


def _fmt(x):
    if x == 0:
        return "0"
    if abs(x - round(x)) < 1e-9 * max(1.0, abs(x)):
        return str(int(round(x)))
    return f"{x:.4g}" if abs(x) < 1e-3 or abs(x) >= 1e6 else f"{x:.6g}"


def _same(a, b):
    try:
        x, y = _number(a), _number(b)
    except (TypeError, ValueError):
        return a == b
    if hasattr(a, "Unit") and hasattr(b, "Unit") and tuple(a.Unit.Signature) != tuple(b.Unit.Signature):
        raise WorksheetError("the two sides have different units")
    return abs(x - y) <= TOLERANCE * max(abs(x), abs(y), 1e-300) or x == y


class Worksheet:
    """A worksheet: its text, the model it describes, and its last results."""

    _count = 0

    def __init__(self, text="", path="", model=None):
        self.path = path
        self._raw = text
        self.doc = parse(text)
        self._model = model  # a FreeCAD document, or None to find it from the header
        self.results = []
        self._written = {}  # output path -> fingerprint of the shape last written there
        Worksheet._count += 1
        self._varset_name = f"WS{Worksheet._count}"

    # ---- loading and saving

    @classmethod
    def load(cls, path, model=None):
        with open(path, encoding="utf-8") as fh:
            return cls(fh.read(), path=os.path.abspath(path), model=model)

    @property
    def text(self):
        """The file's text as written (a worksheet made from a parsed Document only: its dump)."""
        return self._raw if self._raw else dump(self.doc)

    def set_text(self, text):
        self._raw = text
        self.doc = parse(text)

    def save(self, path=None):
        """Write the file with the results in its header (evaluated first if needed)."""
        self.path = os.path.abspath(path or self.path)
        if not self.path:
            raise WorksheetError("the worksheet has no file yet")
        if not self.results:
            self.evaluate()
        results = {r.line.name: r.shown for r in self.results if r.line.name and r.ok}
        self.doc.header["results"] = results
        self._raw = set_header(self.text, "results", results)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self._raw if self._raw.endswith("\n") else self._raw + "\n")
        return self.path

    @property
    def title(self):
        return str(self.doc.header.get("title") or os.path.splitext(os.path.basename(self.path or "Worksheet"))[0])

    # ---- the model

    def model(self):
        """The FreeCAD document the worksheet describes (opened hidden from ``describes`` when needed)."""
        import FreeCAD

        if self._model is not None:
            try:
                if FreeCAD.listDocuments().get(self._model.Name) is self._model:
                    return self._model
            except Exception:  # noqa: BLE001 -- closed
                pass
            self._model = None
        what = str(self.doc.header.get("describes") or "").strip()
        if not what:
            return None
        docs = FreeCAD.listDocuments().values()
        path = what if os.path.isabs(what) else os.path.join(os.path.dirname(self.path or ""), what)
        for d in docs:
            if d.Name == what or d.Label == what or (d.FileName and os.path.realpath(d.FileName) == os.path.realpath(path)):
                self._model = d
                return d
        for d in docs:
            if any(getattr(o, "PartNumber", None) == what for o in d.Objects):
                self._model = d
                return d
        if os.path.isfile(path):
            try:
                self._model = FreeCAD.openDocument(path, hidden=True)
            except TypeError:
                self._model = FreeCAD.openDocument(path)
            return self._model
        return None

    def sheet(self):
        import bruceworx_parameters as bp

        return bp.find_sheet(self.model())

    # ---- evaluation

    def _varset(self):
        ctx = _context()
        vs = ctx.getObject(self._varset_name)
        if vs is None:
            vs = ctx.addObject("App::VarSet", self._varset_name)
        return vs

    def evaluate(self):
        """Every calc line's value, in order. Returns the results (also kept on ``self.results``)."""
        import bruceworx_parameters as bp

        vs = self._varset()
        sheet = self.sheet()
        model = self.model()
        aliases = {a for _c, a in bp.sheet_aliases(sheet)}
        sheet_path = f"{sheet.Document.Name}#{sheet.Name}" if sheet is not None else ""
        units = self.doc.header.get("units") if isinstance(self.doc.header.get("units"), dict) else {}
        defined, failed, results = {}, {}, []
        for line in self.doc.calc_lines():
            res = Result(line)
            results.append(res)
            if line.error:
                res.status, res.message = "error", line.error
                if line.name:
                    failed[line.name] = line.number
                continue
            try:
                texts = ([line.expr] if line.name else []) + [c for _k, c, _l in line.checks]
                for text in texts:
                    for ref in refs(rewrite(text)):
                        if ref.kind == "name" and ref.text not in res.reads:
                            res.reads.append(ref.text)
                bad = [failed[n] for n in res.reads if n in failed and n not in defined]
                if bad:
                    res.status, res.message = "depends", f"depends on line {min(bad)}"
                    if line.name:
                        failed[line.name] = min(bad)
                    continue

                def resolve(ref, res=res):
                    if ref.kind == "name":
                        if ref.text in defined:
                            return f"{vs.Name}.{PREFIX}{ref.text}"
                        if ref.text in aliases:
                            res.inputs[ref.text] = sheet.get(ref.text)
                            return f"{sheet_path}.{ref.text}"
                        raise WorksheetError(f"'{ref.text}' is not defined above, nor in the model's Parameters")
                    if ref.kind == "object" and model is not None:
                        rest = ref.end
                        expr_text = current
                        if expr_text[rest:rest + 1] == "." and (model.getObject(ref.text) or model.getObjectsByLabel(ref.text)):
                            obj = model.getObject(ref.text) or model.getObjectsByLabel(ref.text)[0]
                            return f"{model.Name}#{obj.Name}"
                    return None

                if line.name:
                    current = rewrite(line.expr)
                    value = vs.evalExpression(substitute(current, resolve))
                    res.value = value
                    res.shown = shown(value, line.unit, units)
                    prop = PREFIX + line.name
                    if prop not in vs.PropertiesList:
                        vs.addProperty("App::PropertyPythonObject", prop, "Worksheet")
                    setattr(vs, prop, value)
                    defined[line.name] = res
                    failed.pop(line.name, None)
                for kind, cond, label in line.checks:
                    current = rewrite(cond)
                    halves = _split_eq(current)
                    try:
                        if halves:
                            got = []
                            for half in halves:
                                current = half
                                got.append(vs.evalExpression(substitute(half, resolve)))
                            truth = _same(*got)
                        else:
                            truth = bool(_number(vs.evalExpression(substitute(current, resolve))))
                    except WorksheetError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        res.checks.append((kind, label or cond, False, _clean(exc)))
                        continue
                    passed = truth if kind == "check" else not truth
                    res.checks.append((kind, label or cond, passed, ""))
            except Exception as exc:  # noqa: BLE001 -- this line only
                res.status, res.message = "error", _clean(exc)
                if line.name:
                    failed[line.name] = line.number
                    defined.pop(line.name, None)
        self.results = results
        return results

    def value(self, name):
        for r in self.results:
            if r.line.name == name:
                return r.value
        raise KeyError(name)

    # ---- writing to the model

    def export(self):
        """Write the ``=> Parameters`` lines into the model's Parameters sheet. Returns the aliases changed.
        One undo step in the sheet's document; the files that read the sheet are rebuilt."""
        import bruceworx_parameters as bp

        if not self.results:
            self.evaluate()
        wanted = [r for r in self.results if r.line.export]
        if not wanted:
            return []
        model = self.model()
        if model is None:
            raise WorksheetError("the worksheet describes no open model ('describes' in its header)")
        bad = [f"line {r.line.number} ({r.line.name}): {r.message}" for r in wanted if not r.ok]
        if bad:
            raise WorksheetError("not written, a line has an error: " + "; ".join(bad))
        sheet = self.sheet()
        doc = sheet.Document if sheet is not None else model
        aliases = {a: c for c, a in bp.sheet_aliases(sheet)}
        exported = {r.line.name for r in wanted}
        values = {r.line.name: r.value for r in self.results if r.ok and r.line.name}
        origin = self.doc.header.get("pn") or self.title
        plan = []
        for r in wanted:
            content = self._cell_content(r, aliases, exported, values, doc)
            cell = aliases.get(r.line.name)
            old = sheet.getContents(cell) if (sheet is not None and cell) else None
            if old != content:
                plan.append((r.line.name, cell, content))
        if not plan:
            return []
        doc.openTransaction(f"Worksheet {self.title}: write Parameters")
        try:
            if sheet is None:
                sheet = bp.make_sheet(doc)
            for name, cell, content in plan:
                if cell is None:
                    row = bp.next_row(sheet.getUsedCells())
                    cell = f"B{row}"
                    sheet.set(f"A{row}", name)
                    sheet.set(cell, content)
                    sheet.setAlias(cell, name)
                    sheet.set(f"C{row}", f"from {origin}")
                else:
                    sheet.set(cell, content)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        doc.recompute()
        try:
            bp.update_dependents(sheet)
        except Exception:  # noqa: BLE001
            pass
        return [name for name, _c, _t in plan]

    def apply(self):
        """Evaluate, write the ``=> Parameters`` lines, rebuild the model and rewrite its outputs: what one
        change of a spec input sets off (MOD-17). Returns {"written": aliases, "outputs": paths}."""
        self.evaluate()
        written = self.export()
        model = self.model()
        if model is not None:
            model.recompute()
            self.evaluate()  # values read from the model after its rebuild
        return {"written": written, "outputs": self.write_outputs()}

    # ---- the files made from the model

    def outputs(self):
        """[(object, path)] of the header's ``outputs``: [{"object": "Body", "file": "out/plate.3mf"}, ...];
        a relative path is from the worksheet's folder (or the model's, before the worksheet is saved)."""
        model = self.model()
        found = []
        for spec in self.doc.header.get("outputs") or []:
            if not isinstance(spec, dict) or not spec.get("object") or not spec.get("file"):
                continue
            obj = None
            if model is not None:
                obj = model.getObject(spec["object"]) or (model.getObjectsByLabel(spec["object"]) or [None])[0]
            path = spec["file"]
            if not os.path.isabs(path):
                base = os.path.dirname(self.path) if self.path else os.path.dirname(getattr(model, "FileName", "") or "")
                path = os.path.join(base or os.getcwd(), path)
            found.append((obj, os.path.abspath(path), spec["object"]))
        return found

    def write_outputs(self, force=False):
        """Rewrite each output whose object's shape changed since it was last written here (all with
        ``force``). Returns the paths written; a failure is logged and the rest still go."""
        import FreeCAD

        written = []
        for obj, path, name in self.outputs():
            if obj is None:
                FreeCAD.Console.PrintWarning(f"BruceWorx worksheet: output {name}: no such object in the model\n")
                continue
            try:
                stamp = _fingerprint(obj)
                if not force and stamp is not None and self._written.get(path) == stamp and os.path.isfile(path):
                    continue
                write_file(obj, path)
                self._written[path] = stamp
                written.append(path)
            except Exception as exc:  # noqa: BLE001
                FreeCAD.Console.PrintWarning(f"BruceWorx worksheet: output {path}: {exc}\n")
        return written

    def _cell_content(self, res, aliases, exported, values, doc):
        """The sheet cell for a line: its expression with model names kept as aliases and worksheet-only
        names put in as their values, so the model recomputes without the worksheet."""
        line = res.line
        expr = rewrite(line.expr)
        refs_ = refs(expr)
        if not any(r.kind in ("name", "object") for r in refs_) and isinstance(res.value, str):
            return res.value  # a plain text such as a thread size: the cell holds it as text

        def to_sheet(ref):
            if ref.kind == "name":
                if ref.text in values:  # the worksheet's own: by name when it is written too, else its value
                    return ref.text if ref.text in exported else literal(values[ref.text])
                return ref.text  # a Parameters alias
            if ref.kind == "object":
                model = self.model()
                obj = model.getObject(ref.text) or (model.getObjectsByLabel(ref.text) or [None])[0]
                if obj is not None and model is not doc:
                    return f"{model.Name}#{obj.Name}"
                return obj.Name if obj is not None else None
            return None

        return "=" + substitute(expr, to_sheet)


def _fingerprint(obj):
    """A hash of what the object shows (None for a drawing page: always written)."""
    import hashlib

    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    # geometry only: the B-rep text changes when a writer has added its own data to the shape
    parts = [f"{shape.Volume:.9g}", f"{shape.Area:.9g}", str(len(shape.Faces)), str(len(shape.Edges))]
    parts += sorted(f"{v.X:.6f},{v.Y:.6f},{v.Z:.6f}" for v in shape.Vertexes)
    placement = getattr(obj, "Placement", None)
    if placement is not None:
        parts.append(str(placement.toMatrix().A))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def write_file(obj, path):
    """Write one object to ``path`` by its extension: .3mf (one print plate), .stl, .step/.stp, .dxf (a
    drawing page, or a sheet-metal flat)."""
    ext = os.path.splitext(path)[1].lower()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if ext == ".3mf":
        import bruceworx_printprep as pp

        pp.write_3mf(path, [obj])
    elif ext == ".stl":
        import bruceworx_printprep as pp
        import Mesh

        points, triangles, _closed = pp.mesh_of(obj.Shape)
        Mesh.Mesh([[points[i] for i in tri] for tri in triangles]).write(path)
    elif ext in (".step", ".stp"):
        import Import

        Import.export([obj], path)
    elif ext == ".dxf":
        if obj.isDerivedFrom("TechDraw::DrawPage"):
            import TechDraw

            TechDraw.writeDXFPage(obj, path)
        else:
            from bruceworx import flat_export

            flat_export.export_dxf(obj, path)
    else:
        raise WorksheetError(f"no writer for {ext} files")
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise WorksheetError(f"nothing was written to {path}")
    return path


def _clean(exc):
    text = str(exc).strip()
    text = re.sub(r"\s*in expression:.*$", "", text, flags=re.S)
    text = re.sub(r"Property '([^']+)' not found in '[^']+'", r"'\1' not found", text)
    return text.replace(PREFIX, "") or type(exc).__name__


# --------------------------------------------------------------------------- live


_state = {"open": [], "observer": None, "busy": False, "pending": set(), "timer": None}
LIVE_DELAY_MS = 200


def register(ws):
    """Keep ``ws`` live: it re-evaluates after its model recomputes."""
    if ws not in _state["open"]:
        _state["open"].append(ws)
    install()
    return ws


def unregister(ws):
    if ws in _state["open"]:
        _state["open"].remove(ws)
    try:
        import FreeCAD

        ctx = FreeCAD.listDocuments().get(CONTEXT_DOC)
        if ctx is not None and ctx.getObject(ws._varset_name) is not None:
            ctx.removeObject(ws._varset_name)
    except Exception:  # noqa: BLE001
        pass


def open_worksheets():
    return list(_state["open"])


listeners = []  # callables(ws) told after a live re-evaluation (the tab redraws)


def _refresh(names):
    if _state["busy"]:
        return
    _state["busy"] = True
    try:
        for ws in list(_state["open"]):
            try:
                model = ws._model
                sheet = ws.sheet() if model is not None else None
                if model is None or (model.Name not in names and (sheet is None or sheet.Document.Name not in names)):
                    continue
                ws.evaluate()
                if ws.doc.header.get("outputs"):
                    ws.write_outputs()
                for fn in list(listeners):
                    fn(ws)
            except Exception as exc:  # noqa: BLE001
                import FreeCAD

                FreeCAD.Console.PrintLog(f"BruceWorx worksheet: {exc}\n")
    finally:
        _state["busy"] = False


class _Observer:
    def slotRecomputedDocument(self, doc):
        import FreeCAD

        if _state["busy"] or doc.Name == CONTEXT_DOC or not _state["open"]:
            return
        if not FreeCAD.GuiUp:
            _refresh({doc.Name})
            return
        _state["pending"].add(doc.Name)
        if _state["timer"] is None:
            from PySide import QtCore

            timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.setInterval(LIVE_DELAY_MS)

            def run():
                names, _state["pending"] = set(_state["pending"]), set()
                _refresh(names)

            timer.timeout.connect(run)
            _state["timer"] = timer
        _state["timer"].start()

    def slotDeletedDocument(self, doc):
        for ws in list(_state["open"]):
            if ws._model is doc:
                ws._model = None


def install():
    import FreeCAD

    if _state["observer"] is None:
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])
    return _state["observer"]


def uninstall():
    import FreeCAD

    observer, _state["observer"] = _state["observer"], None
    if observer is not None:
        FreeCAD.removeDocumentObserver(observer)
