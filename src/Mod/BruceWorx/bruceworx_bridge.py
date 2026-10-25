# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Claude Code bridge inside FreeCAD (AUT-03, plan approved 23 Oct 2026).

A TCP server on 127.0.0.1 that the BruceWorx MCP server
(package/bruceworx/mcp/bruceworx_mcp.py) talks to. Protocol: one JSON object
per line::

    -> {"id": 1, "token": "...", "tool": "object_tree", "args": {"doc": "Unnamed"}}
    <- {"id": 1, "ok": true, "result": {...}}      or  {"id": 1, "ok": false, "error": "..."}

* Address and token: <UserAppData>/BruceWorx/bridge.json ({port, token, pid,
  version, headless}), mode 600, removed when FreeCAD quits. The port is 47820,
  or any free port when that one is taken.
* Consent, once per FreeCAD session: the first call shows "Allow Claude Code to
  control FreeCAD for this session?". Calls wait for the answer (up to two
  minutes), then run or are refused. Allowed, a "Claude connected" chip sits in
  the status bar; a click disconnects, and the next call asks again. Deny refuses
  every call until BruceWorx > Allow Claude Code.
* Every call goes to the Report view (reads as log lines, changes as messages)
  and to <UserAppData>/BruceWorx/bridge.log. Modelling calls and run_python are
  one undo step each (bruceworx_bridge_tools).
* Calls run on the GUI thread (QTcpSocket's readyRead).
* Headless (FreeCADCmd, started by the MCP server when no FreeCAD window is
  open): serve_headless() runs a plain socket loop. Nobody can answer a prompt
  there, so calls are refused unless BW_BRIDGE_AUTOALLOW=1, which the GUI
  never honours.

Environment, for tests and the headless job: BW_BRIDGE_DIR (state folder),
BW_BRIDGE_PORT (port; 0 = any free one), BW_BRIDGE_AUTOALLOW (headless only),
BW_BRIDGE_PARENT_PID (headless: quit when that process is gone).

Preferences/BruceWorx/ClaudeBridge (default on) turns it off. The pure parts
(framing, token check, consent states, argument checks) are tested in
TestBruceWorxBridge.py.
"""

from __future__ import annotations

import datetime as _dt
import hmac
import json
import os
import secrets
import time

from bruceworx_slots import slot

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "ClaudeBridge"
PROTOCOL = 1
DEFAULT_PORT = 47820
STATE_FILE = "bridge.json"
LOG_FILE = "bridge.log"
LOG_LIMIT = 1 << 20  # bridge.log rolls over to bridge.log.1 at 1 MB
MAX_LINE = 32 << 20  # a request line longer than this is refused
CONSENT_TIMEOUT_MS = 120000
CHIP_NAME = "claudeChip"
DIALOG_TOOLS = ("dialogs", "answer_dialog")  # may run while another call waits behind a dialog
DIALOG_NAME = "BruceWorxBridgeConsent"
MENU_ACTION = "BruceWorxAllowClaude"

NOT_ALLOWED = "not allowed in FreeCAD yet: answer the prompt in FreeCAD (Allow Claude Code to control FreeCAD)"
DENIED = "not allowed in FreeCAD: the user chose Deny for this session (BruceWorx > Allow Claude Code undoes it)"
HEADLESS_REFUSED = "not allowed: this headless FreeCAD was not started with BW_BRIDGE_AUTOALLOW=1"


# ----- pure helpers ------------------------------------------------------------------


class ArgError(ValueError):
    """A call's tool name or arguments are not valid."""


class LineBuffer:
    """Splits a byte stream into newline-terminated lines (UTF-8 text)."""

    def __init__(self, limit=MAX_LINE):
        self.limit = limit
        self._data = b""

    def feed(self, data: bytes) -> list:
        self._data += data
        lines = []
        while True:
            cut = self._data.find(b"\n")
            if cut < 0:
                break
            raw, self._data = self._data[:cut], self._data[cut + 1:]
            raw = raw.strip()
            if raw:
                lines.append(raw.decode("utf-8", "replace"))
        if len(self._data) > self.limit:
            self._data = b""
            raise ValueError("request line too long")
        return lines


def encode(message: dict) -> bytes:
    """One response line."""
    return (json.dumps(message, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def response(req_id, ok, result=None, error=None) -> dict:
    out = {"id": req_id, "ok": bool(ok)}
    if ok:
        out["result"] = result
    else:
        out["error"] = str(error)
    return out


def parse_request(line: str, token: str):
    """(request, None) for a well-formed request with the right token, else (None, error response)."""
    try:
        req = json.loads(line)
    except ValueError as exc:
        return None, response(None, False, error=f"not JSON: {exc}")
    if not isinstance(req, dict):
        return None, response(None, False, error="a request is a JSON object")
    req_id = req.get("id")
    given = req.get("token")
    if not isinstance(given, str) or not hmac.compare_digest(given.encode(), str(token).encode()):
        return None, response(req_id, False, error="bad token")
    tool = req.get("tool")
    if not isinstance(tool, str) or not tool:
        return None, response(req_id, False, error="no tool named")
    args = req.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, response(req_id, False, error="args must be an object")
    return {"id": req_id, "tool": tool, "args": args}, None


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Consent:
    """Session consent: "ask" -> (prompt) -> "asking" -> "allowed" | "denied".

    decide() says what to do with a call: "run", "prompt" (show the dialog, hold the
    call), "wait" (dialog already showing, hold the call) or "refuse".
    """

    ASK, ASKING, ALLOWED, DENIED = "ask", "asking", "allowed", "denied"

    def __init__(self, state=ASK):
        self.state = state

    def decide(self) -> str:
        if self.state == self.ALLOWED:
            return "run"
        if self.state == self.DENIED:
            return "refuse"
        if self.state == self.ASKING:
            return "wait"
        self.state = self.ASKING
        return "prompt"

    def answer(self, allow: bool) -> None:
        self.state = self.ALLOWED if allow else self.DENIED

    def timed_out(self) -> None:
        """Nobody answered: held calls are refused; the next call asks again."""
        if self.state == self.ASKING:
            self.state = self.ASK

    def revoke(self) -> None:
        """The chip was clicked (or Allow was undone): the next call asks again."""
        self.state = self.ASK

    @property
    def allowed(self) -> bool:
        return self.state == self.ALLOWED


# Argument specs: tool -> (kind, {arg: (type, required, default)}). The MCP server's
# input schemas say the same with descriptions; test_bruceworx_mcp.py checks they agree.
# Kinds: read, model (one undo step), file (no undo step), python, vault_read, vault_write.
_DOC = ("string", False, None)
_VAULT = ("string", False, None)
SPECS = {
    "list_documents": ("read", {}),
    "object_tree": ("read", {"doc": _DOC}),
    "get_properties": ("read", {"object": ("string", True, None), "doc": _DOC}),
    "selection": ("read", {}),
    # Dialog boxes (24 Oct 2026, the user's wish): see and answer a dialog FreeCAD shows, also while
    # another call waits behind it. Never the bridge's own consent prompt.
    "dialogs": ("read", {}),
    "answer_dialog": ("ui", {"button": ("string", True, None), "title": ("string", False, None),
                             "text": ("string", False, None)}),
    "measure": ("read", {"object": ("string", True, None), "doc": _DOC}),
    "screenshot": ("read", {"width": ("integer", False, 800), "height": ("integer", False, 600),
                            "view": ("string", False, "")}),
    "new_document": ("file", {"name": ("string", False, "Unnamed"), "label": ("string", False, None)}),
    "open_document": ("file", {"path": ("string", True, None)}),
    "new_body": ("model", {"label": ("string", False, None), "doc": _DOC}),
    "create_sketch": ("model", {
        "body": ("string", False, None), "plane": ("string", False, "XY"),
        "geometry": ("array", True, None), "auto_constraints": ("boolean", False, True),
        "dimensions": ("array", False, None), "label": ("string", False, None), "doc": _DOC}),
    "pad": ("model", {"sketch": ("string", True, None), "length": ("number", False, 10.0),
                      "through_all": ("boolean", False, False), "reversed": ("boolean", False, False),
                      "midplane": ("boolean", False, False), "label": ("string", False, None), "doc": _DOC}),
    "pocket": ("model", {"sketch": ("string", True, None), "depth": ("number", False, 5.0),
                         "through_all": ("boolean", False, False), "reversed": ("boolean", False, False),
                         "midplane": ("boolean", False, False), "label": ("string", False, None), "doc": _DOC}),
    "revolve": ("model", {"sketch": ("string", True, None), "axis": ("string", False, "V_Axis"),
                          "angle": ("number", False, 360.0), "groove": ("boolean", False, False),
                          "label": ("string", False, None), "doc": _DOC}),
    "hole": ("model", {"sketch": ("string", True, None), "diameter": ("number", True, None),
                       "depth": ("number", False, 10.0), "through_all": ("boolean", False, False),
                       "label": ("string", False, None), "doc": _DOC}),
    "fillet": ("model", {"object": ("string", True, None), "edges": ("array", True, None),
                         "radius": ("number", True, None), "doc": _DOC}),
    "chamfer": ("model", {"object": ("string", True, None), "edges": ("array", True, None),
                          "size": ("number", True, None), "doc": _DOC}),
    "rectangular_pattern": ("model", {
        "object": ("string", True, None), "direction": ("array", False, None),
        "count": ("integer", True, None), "spacing": ("number", True, None),
        "direction2": ("array", False, None), "count2": ("integer", False, 1),
        "spacing2": ("number", False, 0.0), "doc": _DOC}),
    "circular_pattern": ("model", {
        "object": ("string", True, None), "axis": ("any", False, "Z"), "center": ("array", False, None),
        "count": ("integer", True, None), "angle": ("number", False, 360.0), "doc": _DOC}),
    "set_property": ("model", {"object": ("string", True, None), "property": ("string", True, None),
                               "value": ("any", True, None), "doc": _DOC}),
    "set_parameter": ("model", {"alias": ("string", True, None), "value": ("any", True, None),
                                "spreadsheet": ("string", False, None), "doc": _DOC}),
    "add_component": ("model", {"path": ("string", True, None), "assembly": ("string", False, None),
                                "placement": ("array", False, None), "doc": _DOC}),
    "add_joint": ("model", {"type": ("string", True, None), "ref1": ("string", True, None),
                            "ref2": ("string", True, None), "vertex1": ("string", False, None),
                            "vertex2": ("string", False, None), "keep_position": ("boolean", False, False),
                            "assembly": ("string", False, None), "doc": _DOC}),
    "recompute": ("model", {"doc": _DOC}),
    "undo": ("file", {"steps": ("integer", False, 1), "doc": _DOC}),
    "save": ("file", {"path": ("string", False, None), "doc": _DOC}),
    "export_step": ("file", {"path": ("string", True, None), "objects": ("array", False, None), "doc": _DOC}),
    "run_python": ("python", {"code": ("string", True, None), "reset": ("boolean", False, False)}),
    "vault_status": ("vault_read", {"part_number": ("string", False, None), "vault": _VAULT}),
    "vault_my_work": ("vault_read", {"vault": _VAULT}),
    "vault_next_part_number": ("vault_read", {"program": ("string", True, None),
                                              "document_type": ("string", True, None), "vault": _VAULT}),
    "vault_assign_part_number": ("model", {"program": ("string", True, None),
                                           "document_type": ("string", True, None),
                                           "objects": ("array", False, None), "doc": _DOC, "vault": _VAULT}),
    "vault_checkout": ("vault_write", {"part_number": ("string", True, None), "vault": _VAULT}),
    "vault_checkin": ("vault_write", {"part_number": ("string", True, None), "comment": ("string", True, None),
                                      "keep_checked_out": ("boolean", False, False), "vault": _VAULT}),
    "vault_undo_checkout": ("vault_write", {"part_number": ("string", True, None), "vault": _VAULT}),
    "vault_get_latest": ("vault_write", {"part_number": ("string", True, None), "vault": _VAULT}),
    "vault_import": ("vault_write", {"folder": ("string", True, None), "program": ("string", True, None),
                                     "dry_run": ("boolean", False, True), "vault": _VAULT}),
    "vault_settings_csv": ("vault_read", {"path": ("string", False, None), "vault": _VAULT}),
}
READ_KINDS = ("read", "vault_read")
_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate(tool: str, args: dict) -> dict:
    """The arguments with defaults filled in; ArgError for an unknown tool or a bad argument."""
    if tool not in SPECS:
        raise ArgError(f"unknown tool {tool!r}")
    _kind, spec = SPECS[tool]
    unknown = sorted(set(args) - set(spec))
    if unknown:
        raise ArgError(f"{tool}: unknown argument(s) {', '.join(unknown)}; it takes {', '.join(sorted(spec)) or 'none'}")
    out = {}
    for name, (kind, required, default) in spec.items():
        value = args.get(name)
        if value is None:
            if required:
                raise ArgError(f"{tool}: {name} is required")
            out[name] = default
            continue
        if kind != "any":
            ok = isinstance(value, _TYPES[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))
            if kind == "integer" and isinstance(value, float) and value.is_integer():
                value, ok = int(value), True
            if not ok:
                raise ArgError(f"{tool}: {name} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")
        out[name] = value
    return out


def kind_of(tool: str) -> str:
    return SPECS[tool][0] if tool in SPECS else "read"


def summarize_args(args: dict, limit: int = 200) -> str:
    """Arguments for the log, cut to ``limit`` characters."""
    try:
        text = json.dumps(args, separators=(",", ":"), default=str, ensure_ascii=False)
    except Exception:
        text = repr(args)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def log_line(tool: str, args: dict, ok: bool, size: int, error: str = "", when=None) -> str:
    when = when or _dt.datetime.now().astimezone()
    stamp = when.isoformat(timespec="seconds")
    if ok:
        status = "ok"
    else:
        status = "error: " + error.splitlines()[0][:200] if error.strip() else "error"
    return f"{stamp} {tool} {summarize_args(args)} -> {status} ({size} bytes)"


def write_state(folder: str, data: dict) -> str:
    """bridge.json in ``folder``, readable by the user only."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, STATE_FILE)
    tmp = path + f".{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def held_by_other(data, my_pid, alive=None) -> bool:
    """True when ``data`` (a read bridge.json) belongs to another FreeCAD window that is still running.

    A second FreeCAD (a test run on a copy of the preferences shares the same folder) must not take
    the bridge from the first: overwriting bridge.json cut Claude Code off from the user's window,
    and the second one's quit then removed the file (24 Oct 2026).
    """
    if not isinstance(data, dict) or data.get("headless"):
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or pid == my_pid:
        return False
    if alive is None:
        def alive(p):
            try:
                os.kill(p, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True
    return bool(alive(pid))


def read_state(folder: str):
    try:
        with open(os.path.join(folder, STATE_FILE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def remove_state(folder: str, token: str) -> None:
    """Remove bridge.json if it is still ours (another FreeCAD may have written its own)."""
    path = os.path.join(folder, STATE_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            if json.load(handle).get("token") != token:
                return
        os.remove(path)
    except (OSError, ValueError):
        pass


def env_port(default=DEFAULT_PORT) -> int:
    text = os.environ.get("BW_BRIDGE_PORT", "").strip()
    try:
        return int(text) if text else default
    except ValueError:
        return default


def env_autoallow(gui_up: bool) -> bool:
    """BW_BRIDGE_AUTOALLOW=1, honoured only without the GUI."""
    return (not gui_up) and os.environ.get("BW_BRIDGE_AUTOALLOW", "") == "1"


# ----- the dispatcher (FreeCAD side, shared by both transports) ----------------------


def state_dir() -> str:
    explicit = os.environ.get("BW_BRIDGE_DIR", "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    import FreeCAD

    return os.path.join(FreeCAD.getUserAppDataDir(), "BruceWorx")


def version_text() -> str:
    try:
        import FreeCAD

        v = FreeCAD.Version()
        return f"{v[0]}.{v[1]}.{v[2]}"
    except Exception:
        return ""


class Dispatcher:
    """Checks, runs and logs calls. The transports feed it lines and send what it returns."""

    def __init__(self, token: str, folder: str, headless: bool, consent: Consent):
        self.token = token
        self.folder = folder
        self.headless = headless
        self.consent = consent
        self.tools = None  # bruceworx_bridge_tools, imported on first use

    def hello(self, req_id) -> dict:
        """The one call that needs no consent: who is listening, and whether calls will run."""
        return response(req_id, True, {
            "protocol": PROTOCOL, "freecad": version_text(), "pid": os.getpid(),
            "headless": self.headless, "consent": self.consent.state,
        })

    def run(self, req: dict) -> dict:
        """Run an accepted request (consent already given) and log it."""
        tool, args, req_id = req["tool"], req["args"], req["id"]
        started = time.monotonic()
        try:
            clean = validate(tool, args)
            if self.tools is None:
                import bruceworx_bridge_tools

                self.tools = bruceworx_bridge_tools
            result = self.tools.call(tool, clean, headless=self.headless)
            out = response(req_id, True, result)
        except Exception as exc:  # every failure goes back to the caller as text
            text = getattr(exc, "bridge_text", None) or f"{type(exc).__name__}: {exc}"
            out = response(req_id, False, error=text)
        self.log(tool, args, out, time.monotonic() - started)
        return out

    def refuse(self, req: dict, text: str) -> dict:
        out = response(req["id"], False, error=text)
        self.log(req["tool"], req["args"], out, 0.0)
        return out

    def log(self, tool, args, out, seconds) -> None:
        size = len(encode(out))
        line = log_line(tool, args, out["ok"], size, out.get("error", ""))
        try:
            import FreeCAD

            text = f"Claude bridge: {line} [{seconds:.2f} s]\n"
            if kind_of(tool) in READ_KINDS and out["ok"]:
                FreeCAD.Console.PrintLog(text)
            elif out["ok"]:
                FreeCAD.Console.PrintMessage(text)
            else:
                FreeCAD.Console.PrintWarning(text)
        except Exception:
            pass
        append_log(self.folder, line)


def append_log(folder: str, line: str) -> None:
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, LOG_FILE)
        if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _state_data(port, token, headless) -> dict:
    return {
        "port": port, "token": token, "pid": os.getpid(), "version": version_text(),
        "protocol": PROTOCOL, "headless": headless,
        "started": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ----- headless transport (FreeCADCmd) ----------------------------------------------------


def serve_headless(folder=None, port=None, stop=None) -> None:
    """Serve until the parent process (BW_BRIDGE_PARENT_PID) is gone or ``stop()`` is true."""
    import selectors
    import socket

    import FreeCAD

    folder = folder or state_dir()
    port = env_port(0) if port is None else port
    token = new_token()
    consent = Consent(Consent.ALLOWED if env_autoallow(bool(FreeCAD.GuiUp)) else Consent.DENIED)
    disp = Dispatcher(token, folder, True, consent)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", port))
    except OSError:
        server.bind(("127.0.0.1", 0))
    server.listen(4)
    server.setblocking(False)
    port = server.getsockname()[1]
    write_state(folder, _state_data(port, token, True))
    FreeCAD.Console.PrintMessage(f"Claude bridge (headless) on 127.0.0.1:{port}\n")
    parent = int(os.environ.get("BW_BRIDGE_PARENT_PID", "0") or 0)
    sel = selectors.DefaultSelector()
    sel.register(server, selectors.EVENT_READ, None)
    buffers = {}
    try:
        while True:
            if stop is not None and stop():
                break
            if parent and not _alive(parent):
                break
            for key, _mask in sel.select(timeout=1.0):
                if key.data is None:
                    conn, _addr = server.accept()
                    conn.setblocking(True)
                    sel.register(conn, selectors.EVENT_READ, "client")
                    buffers[conn] = LineBuffer()
                    continue
                conn = key.fileobj
                try:
                    data = conn.recv(1 << 16)
                except OSError:
                    data = b""
                if not data:
                    sel.unregister(conn)
                    buffers.pop(conn, None)
                    conn.close()
                    continue
                try:
                    lines = buffers[conn].feed(data)
                except ValueError as exc:
                    conn.sendall(encode(response(None, False, error=str(exc))))
                    continue
                for line in lines:
                    out = _handle_line(disp, line, headless_refusal=HEADLESS_REFUSED)
                    try:
                        conn.sendall(encode(out))
                    except OSError:
                        break
    finally:
        sel.close()
        server.close()
        remove_state(folder, token)


def _handle_line(disp: Dispatcher, line: str, headless_refusal: str) -> dict:
    req, error = parse_request(line, disp.token)
    if req is None:
        return error
    if req["tool"] == "_hello":
        return disp.hello(req["id"])
    if not disp.consent.allowed:
        return disp.refuse(req, headless_refusal)
    return disp.run(req)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ----- GUI transport -------------------------------------------------------------------------

_bridge = None  # the running _GuiBridge
_observer = None
_group = None


def _qt():
    from PySide import QtCore, QtWidgets

    try:
        from PySide import QtNetwork
    except ImportError:
        from PySide6 import QtNetwork
    return QtCore, QtWidgets, QtNetwork


def enabled() -> bool:
    try:
        import FreeCAD

        return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))
    except Exception:
        return True


class _GuiBridge:
    """QTcpServer, the consent prompt and the status-bar chip."""

    def __init__(self):
        QtCore, _QtWidgets, QtNetwork = _qt()
        self.folder = state_dir()
        self.token = new_token()
        self.consent = Consent()
        self.disp = Dispatcher(self.token, self.folder, False, self.consent)
        self.server = QtNetwork.QTcpServer()
        self.sockets = {}  # socket -> LineBuffer
        self.queue = []  # (socket, line) waiting to run, in arrival order
        self.held = []  # (socket, request) waiting for the consent answer
        self.busy = False
        self.dialog = None
        self.chip = None
        self.timer = None
        host = QtNetwork.QHostAddress(QtNetwork.QHostAddress.LocalHost)
        port = env_port()
        if not self.server.listen(host, port):
            if not self.server.listen(host, 0):
                raise RuntimeError(self.server.errorString())
        self.port = int(self.server.serverPort())
        self.server.newConnection.connect(slot(self._accept))
        import FreeCAD

        other = read_state(self.folder)
        if held_by_other(other, os.getpid()):
            # Another FreeCAD window has the bridge: leave its bridge.json alone.
            FreeCAD.Console.PrintLog(f"Claude bridge: FreeCAD {other.get('pid')} has it; this window is not reachable\n")
            return
        write_state(self.folder, _state_data(self.port, self.token, False))
        FreeCAD.Console.PrintLog(f"Claude bridge listening on 127.0.0.1:{self.port}\n")

    # --- sockets ---------------------------------------------------------------------
    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self.sockets[sock] = LineBuffer()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock):
        self.sockets.pop(sock, None)
        self.queue = [(s, line) for s, line in self.queue if s is not sock]
        self.held = [(s, r) for s, r in self.held if s is not sock]
        try:
            sock.deleteLater()
        except Exception:
            pass

    def _read(self, sock):
        buffer = self.sockets.get(sock)
        if buffer is None:
            return
        try:
            lines = buffer.feed(bytes(sock.readAll().data()))
        except ValueError as exc:
            self._send(sock, response(None, False, error=str(exc)))
            return
        for line in lines:
            if self.busy and self._dialog_call(sock, line):
                continue  # answered now: the running call waits behind the dialog it opened
            self.queue.append((sock, line))
        self._drain()

    def _dialog_call(self, sock, line) -> bool:
        """A dialogs / answer_dialog call while another call runs (a dialog's own event loop brought it
        in): run it at once, if this session is already allowed. True when it was handled."""
        req, _error = parse_request(line, self.token)
        if req is None or req.get("tool") not in DIALOG_TOOLS:
            return False
        if self.consent.decide() != "run":
            self._send(sock, self.disp.refuse(req, NOT_ALLOWED))
            return True
        self._send(sock, self.disp.run(req))
        return True

    def _drain(self):
        # A call that processes events can bring the next readyRead in; it waits in the queue.
        if self.busy:
            return
        self.busy = True
        try:
            while self.queue:
                sock, line = self.queue.pop(0)
                self._handle(sock, line)
        finally:
            self.busy = False

    def _handle(self, sock, line):
        req, error = parse_request(line, self.token)
        if req is None:
            self._send(sock, error)
            return
        if req["tool"] == "_hello":
            self._send(sock, self.disp.hello(req["id"]))
            return
        if self.held:  # keep the order: everything waits behind a held call
            self.held.append((sock, req))
            return
        decision = self.consent.decide()
        if decision == "run":
            self._send(sock, self.disp.run(req))
        elif decision == "refuse":
            self._send(sock, self.disp.refuse(req, DENIED))
        else:
            self.held.append((sock, req))
            if decision == "prompt":
                self._prompt()

    def _send(self, sock, message):
        if sock in self.sockets:
            sock.write(encode(message))
            sock.flush()

    # --- consent ---------------------------------------------------------------------
    def _prompt(self):
        QtCore, QtWidgets, _QtNetwork = _qt()
        import FreeCADGui

        box = QtWidgets.QMessageBox(FreeCADGui.getMainWindow())
        box.setObjectName(DIALOG_NAME)
        box.setWindowTitle("Claude Code")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText("Allow Claude Code to control FreeCAD for this session?")
        box.setInformativeText(
            "Claude Code can then read and change open documents, run Python in FreeCAD and use the "
            "vault. Every call is listed in the Report view. Click “Claude connected” in the "
            "status bar to disconnect."
        )
        allow = box.addButton("Allow", QtWidgets.QMessageBox.AcceptRole)
        allow.setObjectName("allow")
        deny = box.addButton("Deny", QtWidgets.QMessageBox.RejectRole)
        deny.setObjectName("deny")
        box.setDefaultButton(deny)
        box.setWindowModality(QtCore.Qt.ApplicationModal)
        box.buttonClicked.connect(lambda button: self._answered(button is allow or button.objectName() == "allow"))
        box.rejected.connect(lambda: self._answered(False) if self.consent.state == Consent.ASKING else None)
        self.dialog = box
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(slot(self._timed_out))
        self.timer.start(CONSENT_TIMEOUT_MS)
        box.open()  # not exec(): no nested event loop inside a socket handler

    def _close_prompt(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.hide()
            dialog.deleteLater()

    def _answered(self, allow):
        if self.consent.state != Consent.ASKING:
            return
        self.consent.answer(allow)
        self._close_prompt()
        import FreeCAD

        FreeCAD.Console.PrintMessage(
            "Claude bridge: Claude Code is allowed for this session.\n" if allow
            else "Claude bridge: Claude Code was denied for this session.\n")
        self._show_chip(allow)
        self._release()

    def _timed_out(self):
        self.consent.timed_out()
        self._close_prompt()
        held, self.held = self.held, []
        for sock, req in held:
            self._send(sock, self.disp.refuse(req, NOT_ALLOWED))

    def _release(self):
        """Run (or refuse) the calls that waited for the answer, in order."""
        held, self.held = self.held, []
        self.busy = True
        try:
            for sock, req in held:
                if self.consent.allowed:
                    self._send(sock, self.disp.run(req))
                else:
                    self._send(sock, self.disp.refuse(req, DENIED))
        finally:
            self.busy = False
        self._drain()

    def allow(self):
        """BruceWorx > Allow Claude Code: allow without waiting for a call."""
        if self.consent.state == Consent.ASKING:
            self._answered(True)
            return
        self.consent.answer(True)
        self._show_chip(True)

    def revoke(self):
        """The chip: disconnect. Open connections are closed; the next call asks again."""
        self.consent.revoke()
        if self.disp.tools is not None:
            self.disp.tools.reset_session()
        self._show_chip(False)
        for sock in list(self.sockets):
            sock.disconnectFromHost()
        import FreeCAD

        FreeCAD.Console.PrintMessage("Claude bridge: disconnected; the next call asks again.\n")

    # --- chip ----------------------------------------------------------------------------
    def _show_chip(self, on):
        _QtCore, QtWidgets, _QtNetwork = _qt()
        import FreeCADGui

        bar = FreeCADGui.getMainWindow().statusBar()
        if self.chip is None:
            self.chip = QtWidgets.QPushButton("Claude connected", bar)
            self.chip.setObjectName(CHIP_NAME)
            self.chip.setFlat(True)
            self.chip.setToolTip("Claude Code can control FreeCAD in this session. Click to disconnect.")
            self.chip.clicked.connect(slot(self.revoke))
            bar.addPermanentWidget(self.chip)
        self.chip.setVisible(bool(on))

    # --- stop ------------------------------------------------------------------------------
    def stop(self):
        self._close_prompt()
        for sock, req in self.held:
            self._send(sock, self.disp.refuse(req, "the bridge was turned off"))
        self.held = []
        for sock in list(self.sockets):
            sock.disconnectFromHost()
        self.server.close()
        if self.chip is not None:
            self.chip.hide()
            self.chip.deleteLater()
            self.chip = None
        remove_state(self.folder, self.token)


def start():
    global _bridge
    if _bridge is None:
        try:
            _bridge = _GuiBridge()
        except Exception as exc:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"Claude bridge did not start: {exc}\n")
    return _bridge


def stop():
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


def bridge():
    return _bridge


def _allow_command():
    if not enabled():
        import FreeCAD

        FreeCAD.Console.PrintWarning("Claude bridge is off (Preferences > BruceWorx).\n")
        return
    running = start()
    if running is not None:
        running.allow()


def install() -> None:
    """Start the bridge (when ClaudeBridge is on) and follow the switch."""
    global _observer, _group
    import FreeCAD
    from PySide import QtCore, QtGui, QtWidgets

    if _observer is not None:
        return

    class _Observer:
        def onChange(self, _grp, name):
            if name == PREF_NAME:
                QtCore.QTimer.singleShot(0, lambda: start() if enabled() else stop())

    _observer = _Observer()
    _group = FreeCAD.ParamGet(PREFS_PATH)
    _group.Attach(_observer)
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(stop)
    try:
        import FreeCADGui
        import bruceworx_menu

        action = QtGui.QAction("Allow Claude Code", FreeCADGui.getMainWindow())
        action.setToolTip("Let Claude Code control FreeCAD in this session without waiting for its first call.")
        action.triggered.connect(lambda _checked=False: _allow_command())
        bruceworx_menu.add_action(action, MENU_ACTION)
    except Exception:
        pass
    if enabled():
        QtCore.QTimer.singleShot(0, start)
