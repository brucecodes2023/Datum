# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-04: Netgen volume meshing in a worker process with a time limit.

Netgen runs inside one C++ call and cannot be interrupted, and on some solids (the T_HUB=1.9 bladed impeller) it never
returns.  The shape and the mesh settings are written to a temporary directory, a child FreeCADCmd meshes them, and the
mesh comes back as a .unv file (keeps second order).  The parent polls the child and kills it (the whole process group)
when its limit is up, when the study is cancelled, or when the parent itself is gone (a small shell watchdog kills the group).  Nothing is left behind: the
temporary directory is removed in every case.

Time limit (``time_limit``): the study's ``MeshTimeLimit`` (s) when it is above 0, else
``max(120 s, 0.0007 s x estimated nodes)`` (20 x the 3.5e-5 s per node Netgen needs on the impeller), capped at 3600 s.
The tries of one meshing share a total of 4 x that limit; each try gets what is left of it, at most the limit.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

MIN_LIMIT_S = 120.0
MAX_LIMIT_S = 3600.0
SECONDS_PER_NODE = 7e-4
TOTAL_FACTOR = 4.0
WORKER_MIN_NODES = 20000  # below this the study meshes in this process (a worker costs ~1 s)
TICK_S = 5.0

# Test hooks: a function (job dict) -> None that may change the job (for example set "test_sleep"), and a replacement poll step.
TEST_HOOK = None

PUMP = None  # a callable run every poll while a worker meshes: the GUI sets it to process events (the Cancel button)
_cancel = threading.Event()
_current = {"proc": None}


class MeshCancelled(Exception):
    pass


def cancel():
    """Asks the running worker to stop: it is killed and ``run_worker`` raises MeshCancelled.  Safe from any thread."""
    _cancel.set()
    kill(_current.get("proc"))


def kill(proc):
    """Kills the child and its process group; harmless when it is gone."""
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def time_limit(analysis=None, est_nodes=0):
    """The seconds one try may take (rule in the module docstring)."""
    try:
        if analysis is not None and "MeshTimeLimit" in analysis.PropertiesList and float(analysis.MeshTimeLimit) > 0:
            return float(analysis.MeshTimeLimit)
    except Exception:
        pass
    return min(MAX_LIMIT_S, max(MIN_LIMIT_S, SECONDS_PER_NODE * float(est_nodes or 0)))


def gmsh_binary():
    """The gmsh executable (FEM preference, then PATH, then next to the application), or None."""
    try:
        import FreeCAD

        path = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").GetString("gmshBinaryPath", "")
        if path and os.path.isfile(path):
            return path
        home = FreeCAD.getHomePath()
        for cand in (os.path.join(home, "bin", "gmsh"), os.path.join(home, "Contents", "bin", "gmsh")):
            if os.path.isfile(cand):
                return cand
    except Exception:
        pass
    return shutil.which("gmsh")


def _freecadcmd():
    import FreeCAD

    home = FreeCAD.getHomePath()
    for cand in (os.path.join(home, "bin", "FreeCADCmd"), os.path.join(os.path.dirname(sys.executable), "FreeCADCmd")):
        if os.path.isfile(cand):
            return cand
    raise RuntimeError("FreeCADCmd not found next to the application")


def _plain(value):
    value = getattr(value, "Value", value)
    return value if isinstance(value, (int, float, str, bool)) or value is None else None


def netgen_settings(mesh, props, second_order):
    """The study's Netgen settings as JSON-able values."""
    out = {}
    for name in tuple(props) + ("MeshSizeFile",):
        if name in mesh.PropertiesList:
            val = _plain(getattr(mesh, name))
            if val is not None:
                out[name] = val
    out["SecondOrder"] = bool(second_order)
    return out


_CHILD = """\
import sys
sys.path.insert(0, %r)
import bruceworx_sim_meshworker as w
w.child_main(%r)
"""


def run_worker(shape, settings, limit, label="", progress=None, kind="netgen", cancel_check=None):
    """Meshes ``shape`` in a child process.  Returns ``(FemMesh or None, info)``; ``info`` has ``status``
    ("ok", "timeout", "failed", "empty"), ``seconds``, ``text`` (what to tell the user).  Raises MeshCancelled."""
    import Fem

    t0 = time.time()
    _cancel.clear()
    tmp = tempfile.mkdtemp(prefix="bw-meshjob-")
    info = {"status": "failed", "seconds": 0.0, "text": "", "limit": limit, "label": label}
    proc = watchdog = None
    try:
        shape.exportBrep(os.path.join(tmp, "shape.brep"))
        job = {"kind": kind, "settings": settings, "out": "mesh.unv"}
        if TEST_HOOK:
            TEST_HOOK(job)
        with open(os.path.join(tmp, "job.json"), "w") as f:
            json.dump(job, f)
        with open(os.path.join(tmp, "run.py"), "w") as f:
            f.write(_CHILD % (os.path.dirname(os.path.abspath(__file__)), tmp))
        env = dict(os.environ, FREECAD_USER_HOME=os.path.join(tmp, "home"), QT_QPA_PLATFORM="offscreen")
        log = open(os.path.join(tmp, "child.log"), "w")
        proc = subprocess.Popen([_freecadcmd(), os.path.join(tmp, "run.py")], env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        _current["proc"] = proc
        # Netgen holds the interpreter lock, so the child cannot watch its parent itself: a shell does (the parent quit or crashed).
        watchdog = subprocess.Popen(["/bin/sh", "-c", _WATCHDOG, "sh", str(os.getpid()), str(proc.pid)], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        last = 0.0
        while proc.poll() is None:
            elapsed = time.time() - t0
            if _cancel.is_set() or (cancel_check and cancel_check()):
                kill(proc)
                raise MeshCancelled("Meshing cancelled")
            if elapsed > limit:
                kill(proc)
                info.update(status="timeout", seconds=round(elapsed, 1),
                            text=f"{label}: no mesh after {limit:.0f} s, worker killed")
                return None, info
            if progress and elapsed - last >= TICK_S:
                last = elapsed
                progress(f"{label}: meshing… {elapsed:.0f} s of {limit:.0f} s")
            time.sleep(0.05)
        log.close()
        if _cancel.is_set():
            raise MeshCancelled("Meshing cancelled")
        info["seconds"] = round(time.time() - t0, 1)
        out = os.path.join(tmp, "mesh.unv")
        if proc.returncode != 0 or not os.path.isfile(out):
            tail = ""
            try:
            if PUMP is not None:
                try:
                    PUMP()
                except Exception:
                    pass
                tail = open(os.path.join(tmp, "child.log")).read().strip().splitlines()[-1]
            except Exception:
                pass
            info.update(status="failed", text=f"{label}: the worker ended with code {proc.returncode} and no mesh {tail}".strip())
            return None, info
        fem = Fem.read(out)
        info["seconds"] = round(time.time() - t0, 1)
        if not fem.NodeCount or not fem.VolumeCount:
            info.update(status="empty", text=f"{label}: {fem.NodeCount} nodes, {fem.FaceCount} faces, no volume elements")
            return fem, info
        info.update(status="ok", text=f"{label}: {fem.NodeCount} nodes, {fem.VolumeCount} volume elements in {info['seconds']} s")
        return fem, info
    except MeshCancelled:
        raise
    except Exception as exc:
        info.update(status="failed", seconds=round(time.time() - t0, 1), text=f"{label}: {exc}")
        return None, info
    finally:
        if proc is not None and proc.poll() is None:
            kill(proc)
        _current["proc"] = None
        if watchdog is not None:
            watchdog.kill()
            watchdog.wait()
        shutil.rmtree(tmp, ignore_errors=True)


# ----- the child ------------------------------------------------------------------------------------


_WATCHDOG = 'while kill -0 "$1" 2>/dev/null && kill -0 "$2" 2>/dev/null; do sleep 2; done; kill -9 -- "-$2" 2>/dev/null'


def child_main(tmp):
    """Runs in the child FreeCADCmd: reads job.json and shape.brep, writes the mesh."""
    with open(os.path.join(tmp, "job.json")) as f:
        job = json.load(f)
    if job.get("test_sleep"):
        time.sleep(float(job["test_sleep"]))
    import Part

    shape = Part.Shape()
    shape.read(os.path.join(tmp, "shape.brep"))
    out = os.path.join(tmp, job["out"])
    if job.get("kind") == "gmsh":
        _gmsh(shape, job["settings"], tmp, out)
    else:
        _netgen(shape, job["settings"], out)
    sys.stdout.flush()
    os._exit(0)  # no teardown of the SMESH objects


def _netgen(shape, settings, out):
    import FreeCAD
    import ObjectsFem

    doc = FreeCAD.newDocument("BW_MeshWorker", "BW_MeshWorker", True)
    feature = doc.addObject("Part::Feature", "S")
    feature.Shape = shape
    doc.recompute()
    netgen = ObjectsFem.makeMeshNetgenLegacy(doc, "M")
    netgen.Shape = feature
    for name, value in settings.items():
        if name in netgen.PropertiesList:
            try:
                setattr(netgen, name, value)
            except Exception as exc:
                print(f"setting {name} failed: {exc}")
    netgen.touch()
    doc.recompute([netgen])
    netgen.FemMesh.write(out)


def _gmsh(shape, settings, tmp, out):
    binary = gmsh_binary()
    if not binary:
        raise RuntimeError("no gmsh binary")
    order = 2 if settings.get("SecondOrder") else 1
    size = float(settings.get("MaxSize", 5.0))
    cmd = [binary, "-3", "-order", str(order), "-clmax", str(size), "-format", "unv", "-o", out,
           os.path.join(tmp, "shape.brep")]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
