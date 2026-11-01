# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-05: the validation runner and its report.

cases.json (Resources/Validation) holds one entry per case: the parameters of its geometry builder
(bruceworx_validation_cases), the reference values with units, the tolerance, the citation and the licence.
``run_set("fast")`` builds, meshes (coarse), solves and reads every case; ``run_set("slow")`` runs each case on
a series of three finer meshes and keeps the convergence of its primary quantity. Both return a plain dict
(JSON-able) that ``report_source`` turns into LaTeX ("Simulation > Validation" compiles it with the study
reports' Tectonic path).

Case kinds: "fe" runs here. Other kinds ("cfd", SIM-06) are skipped unless a runner is registered with
``register_runner(kind, function)``; the function gets (case, size, work_dir, progress) and returns the same
result dict as ``run_fe_case``. cases.json's top-level "cfd" list is the place for those entries.
"""

import datetime
import json
import math
import os
import shutil
import tempfile
import time

import bruceworx_sim as sim

CASES_NAME = os.path.join("Resources", "Validation", "cases.json")
SETS = ("fast", "slow")
ENVELOPE = 0.08  # a known gap must stay within this of its reference (a regression guard, not a pass)
_RUNNERS = {}


class ValidationError(Exception):
    pass


# ----- the cases -----------------------------------------------------------------------------------------------------


def cases_path():
    """cases.json beside this module (installed), or in the source tree next to it."""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.join(here, "..", "..", "..", "src", "Mod", "BruceWorx")):
        path = os.path.normpath(os.path.join(base, CASES_NAME))
        if os.path.isfile(path):
            return path
    raise ValidationError(f"{CASES_NAME} not found beside {here}")


def load_document(path=None):
    with open(path or cases_path(), encoding="utf-8") as handle:
        return json.load(handle)


def load_cases(path=None, kind=None):
    """The case dicts (cases.json 'cases' then its 'cfd' entries), of one kind when given."""
    doc = load_document(path)
    cases = list(doc.get("cases", [])) + list(doc.get("cfd", []))
    return [c for c in cases if kind is None or c.get("kind", "fe") == kind]


def register_runner(kind, function):
    """Lets another module (the CFD cases) run its cases: function(case, size, work_dir, progress) -> result dict."""
    _RUNNERS[kind] = function


def runner_for(kind):
    """The runner of a case kind; the "cfd" cases register themselves (bruceworx_cfd_wash) on first use."""
    if kind == "cfd" and "cfd" not in _RUNNERS:
        try:
            import bruceworx_cfd_wash

            bruceworx_cfd_wash.register()
        except ImportError:
            pass
    return _RUNNERS.get(kind)


def sizes_for(case, set_name):
    mesh = case.get("mesh") or {}
    if set_name == "fast":
        return [float(mesh["fast"])]
    return [float(s) for s in mesh["slow"]]


def in_set(case, set_name):
    return set_name in case.get("sets", SETS)


# ----- comparing -----------------------------------------------------------------------------------------------------


def relative_error(result, reference):
    """(result - reference) / |reference|; against 1 for a zero reference."""
    scale = abs(reference) if reference else 1.0
    return (result - reference) / scale


def evaluate(case, values):
    """The comparison rows of a case from its measured values {key: number}: reference, result, error, mark."""
    rows = []
    default = float(case.get("tolerance", 0.03))
    for ref in case["references"]:
        tolerance = float(ref.get("tolerance", default))
        result = values.get(ref["key"])
        row = {"key": ref["key"], "label": ref.get("label", ref["key"]), "unit": ref.get("unit", ""),
               "reference": ref["value"], "result": result, "tolerance": tolerance,
               "informative": bool(ref.get("informative")), "derived": bool(ref.get("derived")),
               "error": None, "passed": False}
        if result is not None and math.isfinite(result):
            row["error"] = relative_error(result, ref["value"])
            row["passed"] = abs(row["error"]) <= tolerance
        rows.append(row)
    return rows


def case_passed(rows):
    """All the marked (not informative) rows are within tolerance."""
    marked = [r for r in rows if not r["informative"]]
    return bool(marked) and all(r["passed"] for r in marked)


def worst_error(rows):
    errors = [abs(r["error"]) for r in rows if r["error"] is not None and not r["informative"]]
    return max(errors) if errors else None


def mark(case, rows, error=None):
    """'PASS', 'FAIL', 'GAP' (a known gap, within its envelope) or 'ERROR'."""
    if error:
        return "ERROR"
    if case_passed(rows):
        return "PASS"
    if case.get("status") == "known_gap":
        worst = worst_error(rows)
        return "GAP" if worst is not None and worst <= float(case.get("envelope", ENVELOPE)) else "FAIL"
    return "FAIL"


# ----- running -------------------------------------------------------------------------------------------------------


def solve_setup(setup, work_dir, progress=None):
    """sim.solve for a built case: mesh, write, patch the input, CalculiX, read, then the nonlinear run if it has one."""
    analysis = setup.analysis
    fea = sim.prepare_solve(analysis, work_dir, progress)
    if setup.patch:
        setup.patch(fea.inp_file_name)
    code = fea.ccx_run()
    if code:
        raise sim.StudyError(f"CalculiX finished with exit code {code}: {(fea.ccx_stderr or '')[-300:]}")
    result = sim.finish_solve(fea, progress)
    if setup.nonlinear:
        import bruceworx_sim_eigen as eigen

        eigen.solve_nonlinear(analysis, fea, progress)
    stats = sim.mesh_stats(sim.members(analysis)["mesh"])
    return result, stats


def run_fe_case(case, size, work_dir, progress=None):
    """Builds, solves and reads one 'fe' case on a mesh of `size` mm; returns its result dict (never raises)."""
    import FreeCAD as App

    import bruceworx_validation_cases as builders

    out = {"id": case["id"], "title": case.get("title", ""), "kind": case.get("kind", "fe"), "size": size, "nodes": None,
           "elements": None, "seconds": 0.0, "values": {}, "rows": [], "error": None, "mark": "ERROR"}
    t0 = time.time()
    doc = None
    try:
        setup = builders.BUILDERS[case["builder"]](case["params"], size)
        doc = setup.doc
        folder = os.path.join(work_dir, f"{case['id']}_{size:g}")
        os.makedirs(folder, exist_ok=True)
        result, stats = solve_setup(setup, folder, progress)
        out["nodes"], out["elements"] = stats.get("nodes"), stats.get("elements")
        out["values"] = {k: v for k, v in setup.measure(setup.analysis, result).items() if isinstance(v, (int, float))}
        out["rows"] = evaluate(case, out["values"])
        out["mark"] = mark(case, out["rows"])
    except Exception as exc:  # a failing case is a result, not a crash of the set
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["rows"] = evaluate(case, {})
        out["mark"] = "ERROR"
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
    out["seconds"] = time.time() - t0
    return out


def run_case(case, set_name="fast", work_dir=None, progress=None):
    """One case in a set: its result dict, with a 'series' of the three meshes in the slow set (the finest is the result)."""
    runner = run_fe_case if case.get("kind", "fe") == "fe" else runner_for(case.get("kind"))
    if runner is None:
        return {"id": case["id"], "title": case.get("title", ""), "kind": case.get("kind"), "mark": "SKIPPED",
                "error": f"no runner for the kind {case.get('kind')!r}", "rows": [], "seconds": 0.0, "size": None,
                "nodes": None, "elements": None, "values": {}}
    work_dir = work_dir or tempfile.mkdtemp(prefix="bw-validation-")
    if set_name == "slow" and case.get("slow_params"):  # e.g. a finer time step for the finer meshes
        case = dict(case, params=dict(case["params"], **case["slow_params"]))
    runs = [runner(case, size, work_dir, progress) for size in sizes_for(case, set_name)]
    result = dict(runs[-1])
    result["seconds"] = sum(r["seconds"] for r in runs)
    if set_name == "slow":
        primary = case.get("primary") or case["references"][0]["key"]
        result["series"] = [{"size": r["size"], "nodes": r["nodes"], "elements": r["elements"], "seconds": r["seconds"],
                             "value": r["values"].get(primary),
                             "error": next((row["error"] for row in r["rows"] if row["key"] == primary), None),
                             "mark": r["mark"]} for r in runs]
        result["primary"] = primary
    return result


def ccx_text():
    path = sim.find_ccx()
    return (sim.ccx_version(path) or "") if path else ""


def run_set(set_name="fast", ids=None, progress=None, work_dir=None, cancel=None, cases=None, path=None):
    """Runs a set; returns {"set", "date", "ccx", "seconds", "results": [...], "counts": {...}}.

    progress(text, fraction) is called before each case; cancel() returning True stops after the running case."""
    if set_name not in SETS:
        raise ValidationError(f"unknown set {set_name!r} (fast or slow)")
    cases = [c for c in (cases if cases is not None else load_cases(path)) if in_set(c, set_name) and (not ids or c["id"] in ids)]
    own_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="bw-validation-")
    t0 = time.time()
    results = []
    try:
        for index, case in enumerate(cases):
            if cancel and cancel():
                break
            if progress:
                progress(f"{case['id']}: {case.get('title', '')} ({index + 1} of {len(cases)})", index / max(1, len(cases)))
            results.append(run_case(case, set_name, work_dir, None))
        if progress:
            progress("Done", 1.0)
    finally:
        if own_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    counts = {}
    for r in results:
        counts[r["mark"]] = counts.get(r["mark"], 0) + 1
    by_id = {c["id"]: c for c in cases}
    return {"set": set_name, "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "ccx": ccx_text(),
            "seconds": time.time() - t0, "results": results, "counts": counts,
            "cases": {i: {k: c.get(k) for k in ("title", "tolerance", "citation", "licence", "setup", "note", "status",
                                                "source_check", "diameter_check")}
                      for i, c in by_id.items()}}


# ----- the report ----------------------------------------------------------------------------------------------------

e = sim.tex_escape


def _num(value, digits=5):
    if value is None:
        return "--"
    text = f"{value:.{digits}g}"
    if "e" in text:
        mant, exp = text.split("e")
        return rf"${mant}\times10^{{{int(exp)}}}$"
    return text


def _pct(value):
    return "--" if value is None else f"{100 * value:+.2f}\\,\\%"


def _mark_tex(mark_):
    colors = {"PASS": "markPass", "FAIL": "markFail", "GAP": "markGap", "ERROR": "markFail", "SKIPPED": "black!50"}
    return rf"\textcolor{{{colors.get(mark_, 'black')}}}{{\textbf{{{mark_}}}}}"


def convergence_plot(result, case_id):
    """A pgfplots axis of the primary quantity's error against the node count, or ''."""
    series = [s for s in result.get("series", []) if s.get("error") is not None and s.get("nodes")]
    if len(series) < 2:
        return ""
    pts = " ".join(f"({s['nodes']},{100 * abs(s['error']):.4f})" for s in series)
    sizes = ", ".join(f"{s['size']:g}" for s in series)
    return "\n".join([
        r"\begin{center}", r"\begin{tikzpicture}",
        r"\begin{axis}[width=0.62\linewidth,height=4.4cm,xmode=log,ymin=0,enlarge y limits={upper,value=0.2},xlabel={nodes},"
        r"ylabel={$|$error$|$ (\%)},title={\small " + e(f"{case_id}, {result.get('primary', '')}") + r"},grid=both,"
        r"tick label style={font=\footnotesize},label style={font=\footnotesize},scaled y ticks=false,"
        r"yticklabel style={/pgf/number format/fixed,/pgf/number format/precision=3}]",
        rf"\addplot[mark=*,thick,blue!60!black] coordinates {{{pts}}};",
        r"\end{axis}", r"\end{tikzpicture}",
        rf"{{\footnotesize mesh sizes {sizes} mm}}", r"\end{center}"])


def report_source(run):
    """The LaTeX source of a validation report from run_set's dict: the table of reference, result, error and pass mark
    per case, the citation per case and, for the slow set, the convergence plots."""
    counts = run.get("counts", {})
    results = run.get("results", [])
    cases = run.get("cases", {})
    lines = [
        r"\documentclass[10pt,a4paper]{article}",
        "% Vector Labs validation report (BruceWorx SIM-05). Compile with tectonic, latexmk -pdf or pdflatex.",
        r"\usepackage{iftex}",
        r"\ifXeTeX\usepackage{fontspec}\else\usepackage[utf8]{inputenc}\usepackage[T1]{fontenc}\usepackage{lmodern}\fi",
        r"\usepackage[margin=20mm]{geometry}",
        r"\usepackage{graphicx}", r"\usepackage{booktabs}", r"\usepackage{xcolor}", r"\usepackage{pgfplots}",
        r"\pgfplotsset{compat=1.16}",
        r"\definecolor{markPass}{HTML}{3FA34D}", r"\definecolor{markFail}{HTML}{D91C1C}", r"\definecolor{markGap}{HTML}{F2A007}",
        r"\setlength{\parindent}{0pt}\setlength{\parskip}{4pt}\sloppy\emergencystretch=3em",
        r"\begin{document}",
        rf"{{\Large\bfseries Validation report: {e(run['set'])} set}}\\[2pt]",
        rf"{{\small\color{{black!60}} Vector Labs, {e(run['date'])}; {e(run.get('ccx') or 'CalculiX')}; "
        rf"{run.get('seconds', 0):.0f}\,s}}",
        "",
        r"\section*{Summary}",
        "Each case builds the published model from its source's numbers, solves it with CalculiX on C3D10 Netgen meshes "
        "and compares the quantities the source states. The error is (result $-$ reference) / $|$reference$|$; a case passes "
        r"when every marked row is within its tolerance. \textit{Informative} rows are shown but not marked. "
        "Verification (V) cases compare with closed forms and run in the unit tests; benchmark (B) cases use published "
        "reference solutions; gap (G) cases are closed forms for classes with no open reference.",
        "",
        rf"\textbf{{Result:}} {counts.get('PASS', 0)} pass, {counts.get('FAIL', 0)} fail, {counts.get('GAP', 0)} known gap, "
        rf"{counts.get('ERROR', 0)} error, {counts.get('SKIPPED', 0)} skipped, of {len(results)} cases.", "",
        r"\begin{tabular}{@{}lp{0.5\linewidth}rrrl@{}}", r"\toprule",
        r"Case & Title & Nodes & Mesh (mm) & Time (s) & Mark\\", r"\midrule"]
    for r in results:
        lines.append(rf"{e(r['id'])} & {e(r.get('title', ''))} & {r.get('nodes') or '--'} & "
                     rf"{('%g' % r['size']) if r.get('size') else '--'} & {r.get('seconds', 0):.0f} & {_mark_tex(r['mark'])}\\")
    lines += [r"\bottomrule", r"\end{tabular}", ""]
    for r in results:
        info = cases.get(r["id"], {})
        lines.append(rf"\section*{{{e(r['id'])}: {e(r.get('title', ''))}}}")
        if r.get("error"):
            lines += [rf"\textcolor{{markFail}}{{\textbf{{{_mark_tex(r['mark'])}}}}}: {e(r['error'])}", ""]
        lines += [r"\begin{tabular}{@{}p{0.34\linewidth}rrrrl@{}}", r"\toprule", r"Quantity & Reference & Result & Error & Tol. & Mark\\", r"\midrule"]
        for row in r.get("rows", []):
            unit = f"\\,{e(row['unit'])}" if row["unit"] and row["unit"] != "-" else ""
            status = "info" if row["informative"] else ("pass" if row["passed"] else "FAIL")
            mark_ = {"info": r"\textit{info}", "pass": r"\textcolor{markPass}{pass}", "FAIL": r"\textcolor{markFail}{\textbf{FAIL}}"}[status]
            label = e(row["label"]) + (r" \textit{(derived)}" if row["derived"] else "")
            lines.append(rf"{label} & {_num(row['reference'])}{unit} & {_num(row['result'])} & {_pct(row['error'])} & "
                         rf"{100 * row['tolerance']:g}\,\% & {mark_}\\")
        lines += [r"\bottomrule", r"\end{tabular}", ""]
        cit = info.get("citation") or {}
        lines.append(rf"\textbf{{Source.}} {e(cit.get('document', ''))}; {e(cit.get('location', ''))}." +
                     (rf" \texttt{{{e(cit['url'])}}}." if cit.get("url") else ""))
        if info.get("licence"):
            lines.append(rf"\textbf{{Licence.}} {e(info['licence'])}.")
        if info.get("setup"):
            lines.append(rf"\textbf{{Model.}} {e(info['setup'])}")
        if info.get("note"):
            lines.append(rf"\textbf{{Note.}} {e(info['note'])}")
        if r.get("series"):
            lines += ["", r"\textbf{Mesh convergence} (primary quantity " + e(r.get("primary", "")) + r"):", r"\begin{tabular}{@{}rrrrl@{}}",
                      r"\toprule Mesh (mm) & Nodes & Value & Error & \\", r"\midrule"]
            for s in r["series"]:
                lines.append(rf"{s['size']:g} & {s['nodes'] or '--'} & {_num(s['value'])} & {_pct(s['error'])} & {_mark_tex(s['mark'])}\\")
            lines += [r"\bottomrule", r"\end{tabular}", convergence_plot(r, r["id"])]
        lines.append("")
    lines += [r"\end{document}", ""]
    return "\n".join(lines)


def report_paths(run, folder=None):
    folder = folder or tempfile.gettempdir()
    stem = f"Validation_{run['set']}_" + run["date"].replace(" ", "_").replace(":", "")
    directory = os.path.join(folder, stem + "_report")
    return {"dir": directory, "tex": os.path.join(directory, "report.tex"), "built": os.path.join(directory, "report.pdf"),
            "target": os.path.join(folder, stem + ".pdf")}


def write_report(run, folder=None, compile=True):
    """report.tex and, when a LaTeX compiler is there, the PDF: {"dir", "tex", "pdf" or None, "message"}."""
    paths = report_paths(run, folder)
    os.makedirs(paths["dir"], exist_ok=True)
    with open(paths["tex"], "w", encoding="utf-8") as handle:
        handle.write(report_source(run))
    out = dict(paths)
    out["pdf"], out["message"] = None, f"LaTeX source written: {paths['tex']}"
    if compile:
        out["pdf"], out["message"] = sim.compile_report(paths)
    with open(os.path.join(paths["dir"], "results.json"), "w", encoding="utf-8") as handle:
        json.dump({k: v for k, v in run.items()}, handle, indent=1, default=str)
    return out


# ----- the background job ---------------------------------------------------------------------------------------------


def main():
    """The entry of the headless child the Validation command starts (FreeCADCmd -c): BW_VALIDATION_SET, BW_VALIDATION_IDS
    (comma-separated, empty for all) and BW_VALIDATION_OUT (the results JSON). Progress goes to stdout as
    'BWVAL<TAB>fraction<TAB>text'."""
    set_name = os.environ.get("BW_VALIDATION_SET", "fast")
    ids = [i for i in os.environ.get("BW_VALIDATION_IDS", "").split(",") if i]
    out = os.environ.get("BW_VALIDATION_OUT")

    def progress(text, fraction):
        print(f"BWVAL\t{fraction:.4f}\t{text}", flush=True)

    run = run_set(set_name, ids or None, progress)
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(run, handle, default=str)
    print(f"BWVAL\t1.0\tdone: {run['counts']}", flush=True)
