# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run events of the simulation runners (SIM-07 P1): a tiny publish/subscribe hub.

The runners (meshing, CalculiX, OpenFOAM) call the functions below; the Simulation workspace's bottom pane
(bruceworx_sim_workspace) listens. Emitting is always harmless: with no listener nothing happens, a listener that
raises is dropped from that event with a warning in the log, and nothing here needs a GUI.

Events (``subscribe(listener)``: listener is any object, or callable(kind, payload)):
  stage_started(name)                  a stage began (Mesh, Write input, Solve, Read results, ...)
  stage_finished(name, seconds, ok)    it ended
  progress(name, fraction)             0..1 inside a stage
  log(line)                            one line of solver output
  message(level, text, node_key=None)  level "info" | "ok" | "warning" | "error"; node_key names the tree node
  residuals(dict)                      {field: latest value} while an iterative solver runs
"""

from __future__ import annotations

import re
import time

LEVELS = ("info", "ok", "warning", "error")
HISTORY_LIMIT = 2000

_listeners = []
_history = []  # (kind, payload) of the latest run, so a pane opened late can catch up


def subscribe(listener, replay=False):
    """Adds a listener: callable(kind, payload). With replay=True it first gets the kept history."""
    if listener not in _listeners:
        _listeners.append(listener)
    if replay:
        for kind, payload in list(_history):
            _call(listener, kind, payload)


def unsubscribe(listener):
    if listener in _listeners:
        _listeners.remove(listener)


def clear_history():
    del _history[:]


def history():
    return list(_history)


def _call(listener, kind, payload):
    try:
        listener(kind, payload)
    except Exception as exc:  # a broken pane must never break a run
        try:
            _listeners.remove(listener)
        except ValueError:
            pass
        try:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"Simulation event listener dropped: {exc}\n")
        except Exception:
            pass


def emit(kind, **payload):
    payload["time"] = time.time()
    _history.append((kind, payload))
    if len(_history) > HISTORY_LIMIT:
        del _history[: len(_history) - HISTORY_LIMIT]
    for listener in list(_listeners):
        _call(listener, kind, payload)


def stage_started(name):
    emit("stage_started", name=name)


def stage_finished(name, seconds=0.0, ok=True):
    emit("stage_finished", name=name, seconds=float(seconds), ok=bool(ok))


def progress(name, fraction):
    emit("progress", name=name, fraction=max(0.0, min(1.0, float(fraction))))


def log(line):
    emit("log", line=str(line).rstrip("\n"))


def message(level, text, node_key=None):
    emit("message", level=level if level in LEVELS else "info", text=str(text), node_key=node_key)


def residuals(values):
    emit("residuals", values=dict(values))


def run_started(label=""):
    """A new run: the panes empty their Progress and Log."""
    clear_history()
    emit("run_started", label=label)


def tee(progress, name=None):
    """A progress callback that also sends each text to the Log (and keeps ``progress`` working, or None)."""

    if getattr(progress, "bw_tee", False):
        return progress

    def say(text):
        log(text)
        if progress:
            progress(text)

    say.bw_tee = True
    return say


class stage:
    """``with events.stage("Mesh"):`` emits started and finished (ok False when the body raises)."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.time()
        stage_started(self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        stage_finished(self.name, time.time() - self.t0, exc_type is None)
        if exc_type is not None:
            message("error", f"{self.name} failed: {exc}")
        return False


def from_precheck(issues, node_key=None):
    """Pre-check findings [(kind, text)] to Messages: error and warning keep their level, anything else is info."""
    if not issues:
        message("ok", "Pre-check: ready to solve", node_key)
    for kind, text in issues:
        message(kind if kind in ("error", "warning") else "info", f"Pre-check: {text}", node_key)


_FOAM_STAGE = re.compile(r"@@ stage: (\S+)")
_FOAM_TIME = re.compile(r"^Time = (\S+?)s?$")
_FOAM_RES = re.compile(r"Solving for (\w+), Initial residual = ([-\d.eE+]+)")


class FoamFeed:
    """Turns an OpenFOAM case's log.run into events: the stages, the log lines and the residuals.

    ``poll()`` reads what was added since the last call (cheap; the airflow panel's timer calls it) and
    ``finish(result)`` closes the open stage. Everything is harmless if the files are missing."""

    def __init__(self, folder, log_name="log.run"):
        import os

        self.path = os.path.join(folder, log_name)
        self.folder = folder
        self.offset = 0
        self.current = None
        self.t0 = 0.0
        self.last_res = {}
        self.stage_offset = 0
        self.iteration = 0
        self.total = 0
        try:
            import json

            self.total = int(json.load(open(os.path.join(folder, "vectorlabs.json"))).get("iterations") or 0)
        except Exception:
            pass

    def poll(self):
        self._run_log()
        if self.current:
            self._stage_log()

    def _read_from(self, path, offset):
        try:
            with open(path, errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            return [], offset
        cut = chunk.rfind("\n")
        if cut < 0:
            return [], offset
        return chunk[: cut + 1].splitlines(), offset + len(chunk[: cut + 1].encode("utf-8", errors="replace"))

    def _run_log(self):
        lines, self.offset = self._read_from(self.path, self.offset)
        for line in lines:
            m = _FOAM_STAGE.search(line)
            if m:
                if self.current:
                    self._stage_log()  # the last lines of the stage that ends
                self._switch(m.group(1))
            elif not line.startswith("@@"):
                log(line)

    def _stage_log(self):
        import os

        lines, self.stage_offset = self._read_from(os.path.join(self.folder, "log." + self.current), self.stage_offset)
        found = {}
        for line in lines:
            log(line)
            t = _FOAM_TIME.match(line.strip())
            if t:
                try:
                    self.iteration = int(float(t.group(1)))
                    if self.total:
                        progress(self.current, self.iteration / self.total)
                except ValueError:
                    pass
            r = _FOAM_RES.search(line)
            if r:
                found[r.group(1)] = float(r.group(2))
        if found:
            self.last_res.update(found)
            residuals(found)

    def _switch(self, name):
        now = time.time()
        if self.current:
            stage_finished(self.current, now - self.t0, True)
        self.current, self.t0, self.stage_offset, self.iteration = name, now, 0, 0
        stage_started(name)

    def finish(self, result):
        self.poll()
        ok = result == "done"
        if self.current:
            stage_finished(self.current, time.time() - self.t0, ok)
            self.current = None
        message("ok" if ok else ("warning" if result == "stopped" else "error"), f"OpenFOAM run {result}")
