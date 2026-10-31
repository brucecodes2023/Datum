# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulation > Validation (SIM-05): pick the fast or the slow set, run it in the background, get the report.

The cases run in a separate headless FreeCAD (a QProcess of FreeCADCmd with a throwaway profile), so the window
stays live and a crashing mesher cannot take the document down. The child prints progress lines
("BWVAL<TAB>fraction<TAB>text") and writes the results as JSON; this side shows the progress, writes the LaTeX
report from the JSON and compiles it with the study reports' Tectonic path (bruceworx_sim_gui._compile_async).
"""

import json
import os
import shutil
import sys
import tempfile
import time

import FreeCAD as App

import bruceworx_sim as sim
import bruceworx_validation as val
from bruceworx_slots import slot

COMMAND = "BruceWorx_Validation"
HERE = os.path.dirname(os.path.abspath(__file__))
_state = {"installed": False, "job": None, "dialog": None, "ids": None, "folder": None}  # ids, folder: set by the tests


def _alive(widget):
    try:
        import shiboken6

        return widget is not None and shiboken6.isValid(widget)
    except Exception:
        return widget is not None


def freecadcmd():
    """The headless FreeCAD next to this one (bin/FreeCADCmd of the home), or None."""
    home = App.getHomePath()
    for rel in (os.path.join("bin", "FreeCADCmd"), os.path.join("..", "bin", "FreeCADCmd"), os.path.join("..", "..", "bin", "FreeCADCmd")):
        path = os.path.normpath(os.path.join(home, rel))
        if os.path.isfile(path):
            return path
    return None


class Job:
    """One validation run in the background. ``progress(fraction, text)`` and ``finished(info)`` are called on the GUI
    thread; info = {"run": the results dict or None, "files": the report's {"dir", "tex", "pdf", "message"}, "message"}."""

    def __init__(self, set_name="fast", ids=None, folder=None, progress=None, finished=None, compile=True):
        self.set_name, self.ids, self.folder = set_name, list(ids or []), folder
        self.progress_cb, self.finished_cb, self.compile = progress, finished, compile
        self.process = None
        self.tmp = None
        self.out_json = None
        self.buffer = ""
        self.fraction, self.text = 0.0, "Starting…"
        self.started = time.time()
        self.info = None
        self.cancelled = False

    # -- starting
    def start(self):
        from PySide import QtCore

        exe = freecadcmd()
        if exe is None:
            return self._finish({"run": None, "files": None, "message": "FreeCADCmd was not found next to Vector Labs"})
        self.tmp = tempfile.mkdtemp(prefix="bw-validation-job-")
        self.out_json = os.path.join(self.tmp, "run.json")
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("FREECAD_USER_HOME", os.path.join(self.tmp, "home"))
        env.insert("BW_VALIDATION_SET", self.set_name)
        env.insert("BW_VALIDATION_IDS", ",".join(self.ids))
        env.insert("BW_VALIDATION_OUT", self.out_json)
        os.makedirs(os.path.join(self.tmp, "home"), exist_ok=True)
        process = QtCore.QProcess()
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        process.readyRead.connect(slot(self._read))
        process.finished.connect(slot(self._done))
        process.errorOccurred.connect(slot(self._failed))
        self.process = process
        args = ["-u", os.path.join(self.tmp, "user.cfg"), "-s", os.path.join(self.tmp, "system.cfg"),
                "-c", "import bruceworx_validation; bruceworx_validation.main()"]
        self._say(0.0, f"Starting the {self.set_name} set…")
        process.start(exe, args)
        return self

    def _say(self, fraction, text):
        self.fraction, self.text = fraction, text
        if self.progress_cb:
            self.progress_cb(fraction, text)

    def _read(self):
        if not _alive(self.process):
            return
        self.buffer += bytes(self.process.readAll()).decode("utf-8", "replace")
        *lines, self.buffer = self.buffer.split("\n")
        for line in lines:
            if line.startswith("BWVAL\t"):
                parts = line.rstrip().split("\t", 2)
                try:
                    self._say(min(1.0, float(parts[1])), parts[2] if len(parts) > 2 else "")
                except ValueError:
                    pass

    def _failed(self, error):
        from PySide import QtCore

        if self.process is not None and self.process.state() == QtCore.QProcess.NotRunning and self.info is None:
            self._finish({"run": None, "files": None, "message": f"The validation run did not start ({error})"})

    def _done(self, code=0, _status=None):
        self._read()
        if self.cancelled:
            return self._finish({"run": None, "files": None, "message": "Cancelled"})
        run = None
        if os.path.isfile(self.out_json):
            try:
                with open(self.out_json, encoding="utf-8") as handle:
                    run = json.load(handle)
            except ValueError:
                run = None
        if run is None:
            tail = bytes(self.process.readAll()).decode("utf-8", "replace")[-300:] if _alive(self.process) else ""
            return self._finish({"run": None, "files": None, "message": f"The validation run ended without results (exit {code}). {tail}"})
        folder = self.folder or (sim.report_folder(App.ActiveDocument) if App.ActiveDocument else tempfile.gettempdir())
        files = val.write_report(run, folder, compile=False)
        counts = run.get("counts", {})
        summary = (f"{counts.get('PASS', 0)} pass, {counts.get('FAIL', 0)} fail, {counts.get('GAP', 0)} known gap, "
                   f"{counts.get('ERROR', 0)} error in {run.get('seconds', 0):.0f} s")
        info = {"run": run, "files": files, "message": f"Validation ({self.set_name}): {summary}. Report source: {files['tex']}"}
        if not self.compile:
            return self._finish(info)
        import bruceworx_sim_gui as sg

        kind, path = sim.find_tex()
        if kind is None:
            info["message"] += " (no LaTeX compiler found: the source is written)"
            return self._finish(info)
        self._say(1.0, "Compiling the report…")

        def compiled(done_files):
            info["files"] = done_files
            info["message"] = f"Validation ({self.set_name}): {summary}. " + (
                f"Report: {done_files['pdf']}" if done_files.get("pdf") else done_files.get("message", ""))
            self._finish(info)

        sg._compile_async(files, kind, path, compiled)

    def _finish(self, info):
        self.info = info
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
        _state["job"] = None
        self._say(1.0, info["message"])
        if self.finished_cb:
            self.finished_cb(info)
        return self

    def cancel(self):
        from PySide import QtCore

        if self.process is not None and _alive(self.process) and self.process.state() != QtCore.QProcess.NotRunning:
            self.cancelled = True
            self.process.kill()

    def running(self):
        return self.info is None


def run_validation(set_name="fast", ids=None, folder=None, progress=None, finished=None, compile=True):
    """Starts a validation run in the background; returns the Job (one at a time)."""
    job = _state.get("job")
    if job is not None and job.running():
        raise val.ValidationError("a validation run is already going")
    job = Job(set_name, ids, folder, progress, finished, compile)
    _state["job"] = job
    return job.start()


def wait(job, timeout=900.0):
    """Pumps the GUI until the job is finished (tests); returns its info or None on a timeout."""
    import FreeCADGui as Gui

    end = time.time() + timeout
    while job.running():
        if time.time() > end:
            return None
        if job.process is not None and _alive(job.process):
            job.process.waitForReadyRead(50)
        Gui.updateGui()
    Gui.updateGui()
    return job.info


# ----- the dialog -------------------------------------------------------------------------------------------


def open_dialog():
    """The Validation window: the set to run, a progress bar, Run / Cancel / Open report."""
    from PySide import QtCore, QtGui, QtWidgets

    import FreeCADGui as Gui

    old = _state.get("dialog")
    if _alive(old):
        old.show()
        old.raise_()
        return old
    dialog = QtWidgets.QDialog(Gui.getMainWindow())
    dialog.setWindowTitle("Validation")
    dialog.setMinimumWidth(460)
    lay = QtWidgets.QVBoxLayout(dialog)
    intro = QtWidgets.QLabel("Runs the published benchmarks and closed forms on this build (CalculiX, Netgen C3D10 meshes) "
                             "and writes a report: reference, result, error and a pass mark per case, with the source of each.")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    fast = QtWidgets.QRadioButton("Fast set: coarse meshes, the whole set in a few minutes")
    slow = QtWidgets.QRadioButton("Slow set: three finer meshes per case, with the convergence plots")
    fast.setChecked(True)
    lay.addWidget(fast)
    lay.addWidget(slow)
    bar = QtWidgets.QProgressBar()
    bar.setRange(0, 1000)
    lay.addWidget(bar)
    label = QtWidgets.QLabel("")
    label.setWordWrap(True)
    lay.addWidget(label)
    buttons = QtWidgets.QHBoxLayout()
    run_button = QtWidgets.QPushButton("Run")
    cancel_button = QtWidgets.QPushButton("Cancel run")
    cancel_button.setEnabled(False)
    open_button = QtWidgets.QPushButton("Open report")
    open_button.setEnabled(False)
    close_button = QtWidgets.QPushButton("Close")
    for b in (run_button, cancel_button, open_button):
        buttons.addWidget(b)
    buttons.addStretch(1)
    buttons.addWidget(close_button)
    lay.addLayout(buttons)
    state = {"report": None}

    def on_progress(fraction, text):
        if _alive(bar):
            bar.setValue(int(1000 * fraction))
            label.setText(text)

    def on_finished(info):
        if not _alive(dialog):
            return
        run_button.setEnabled(True)
        cancel_button.setEnabled(False)
        files = info.get("files") or {}
        state["report"] = files.get("pdf") or files.get("tex")
        open_button.setEnabled(bool(state["report"]))
        label.setText(info["message"])
        App.Console.PrintMessage(f"[validation] {info['message']}\n")

    def on_run():
        try:
            job = run_validation("slow" if slow.isChecked() else "fast", ids=_state.get("ids"), folder=_state.get("folder"),
                                 progress=on_progress, finished=on_finished)
        except val.ValidationError as exc:
            label.setText(str(exc))
            return
        run_button.setEnabled(False)
        cancel_button.setEnabled(True)
        open_button.setEnabled(False)
        dialog._job = job

    def on_cancel():
        job = _state.get("job")
        if job is not None:
            job.cancel()

    def on_open():
        if state["report"]:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(state["report"]))

    run_button.clicked.connect(slot(on_run))
    cancel_button.clicked.connect(slot(on_cancel))
    open_button.clicked.connect(slot(on_open))
    close_button.clicked.connect(slot(dialog.close))
    dialog._parts = {"fast": fast, "slow": slow, "bar": bar, "label": label, "run": run_button, "cancel": cancel_button,
                     "open": open_button, "close": close_button}
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    _state["dialog"] = dialog
    dialog.show()
    return dialog


class _Command:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_Validation.svg"), "MenuText": "Validation",
                "ToolTip": "Run the published benchmark and closed-form cases on this build (fast or slow set) and write the report"}

    def IsActive(self):
        return True

    def Activated(self):
        open_dialog()


def install():
    import FreeCADGui as Gui

    if _state["installed"]:
        return
    Gui.addCommand(COMMAND, _Command())
    _state["installed"] = True
