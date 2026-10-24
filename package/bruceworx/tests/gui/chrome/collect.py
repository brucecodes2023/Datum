"""Errors and warnings of one run, per step: collect.py <freecad log> <stdout file> <results txt>.

Reads FreeCAD's --log-file (lines "Err: ", "Wrn: ", ...) and the process output
(Python tracebacks Qt printed), splits them at the "[bwtest] step" markers and
appends one FAIL line per step that logged an error or warning to the results
file ("PASS no-errors:<step>" otherwise). Lines with [bwtest-expected] were
made on purpose and are skipped, as are errors in a step named "*expect-error". Prints the FAIL lines.
"""

import re
import sys

PREFIX = re.compile(r"^(Msg|Wrn|Err|Log|Crt|Ntf|Tme): ")
# Known noise that is not BruceWorx's (documented in the README).
IGNORE = (
    "3DconnexionNavlib",
)


def entries(path):
    kind, text = None, []
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return
    for line in lines:
        m = PREFIX.match(line.lstrip("﻿"))
        if m:
            if kind:
                yield kind, "\n".join(text)
            kind, text = m.group(1), [line.lstrip("﻿")[len(m.group(0)):]]
        elif kind:
            text.append(line)
    if kind:
        yield kind, "\n".join(text)


def main(log, out, results):
    step = "startup"
    seen = {}  # step -> [problems]
    order = ["startup"]
    for kind, text in entries(log):
        if kind == "Msg" and "[bwtest] step " in text:
            step = text.split("[bwtest] step ", 1)[1].strip().split(":", 1)[-1]
            if step not in order:
                order.append(step)
            continue
        if kind not in ("Wrn", "Err", "Crt"):
            continue
        if "[bwtest-expected]" in text or any(i in text for i in IGNORE):
            continue
        if kind == "Err" and step.endswith("expect-error"):
            continue  # the step breaks a feature on purpose
        seen.setdefault(step, []).append(f"{kind}: {text.strip()[:400]}")
    try:
        output = open(out, encoding="utf-8", errors="replace").read()
    except OSError:
        output = ""
    try:
        logtext = open(log, encoding="utf-8", errors="replace").read()
    except OSError:
        logtext = ""
    for block in output.split("Traceback (most recent call last):")[1:]:
        lines = [l for l in block.strip().splitlines() if l.strip() and not l.startswith("[bwtest]")]
        # The exception line: the first unindented line after the frames.
        last = next((l for l in lines if not l.startswith(" ")), lines[-1] if lines else "")
        if last and last in logtext:
            continue  # the Report view has it already (pinned on its step)
        seen.setdefault("stderr", []).append("Traceback: " + " | ".join(lines[-3:])[:400])
    if "stderr" in seen:
        order.append("stderr")
    lines = []
    for s in order:
        if s == "end":
            continue
        probs = seen.get(s, [])
        if probs:
            uniq = list(dict.fromkeys(probs))
            lines.append(f"FAIL no-errors:{s}: {len(probs)} error/warning line(s): " + " || ".join(uniq[:6]).replace("\n", " ⏎ "))
        else:
            lines.append(f"PASS no-errors:{s}")
    with open(results, "a") as f:
        for line in lines:
            f.write(line + "\n")
    for line in lines:
        if line.startswith("FAIL"):
            print(line)


if __name__ == "__main__":
    main(*sys.argv[1:4])
