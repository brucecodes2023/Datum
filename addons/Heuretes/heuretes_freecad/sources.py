# SPDX-License-Identifier: LGPL-2.1-or-later
"""Where the next part number comes from.

Two sources implement :class:`NumberSource`, so the macro, the rename script,
the CLI and the vault never branch on the transport:

* :class:`JsonNumberSource`, a local JSON counter (FCB-P2-04/05). A vault keeps
  one as ``<vault>/numbering.json``. This is the default.
* :class:`OpsApiNumberSource`, the BruceWorx Ops API reader (FCB-P2-03).

The numbering file picks the source with its ``source`` key, ``"json"`` when
absent (:func:`open_number_source`). Counters:

* ``next_parent`` is the next ``PPPPP`` for one program and document type.
* ``parents[<PPPPP>].next_sub`` is the next ``SSS`` under that parent.

Document type codes live in ``config/document_types.json`` (or an embedded
``document_types`` object). The shipped table is placeholder data for Bruce.

A program or document type entry with ``"retired": true`` (Vault Settings)
is not listed by :meth:`JsonNumberSource.programs` / ``document_types``, so
pickers stop offering it; numbers under it still allocate when it is named.

Revisions (BW-PDM-03). A file declaring ``format`` ``{PROG}-{DT}-{PPPPP}-{SSS}``
issues part numbers without a revision; the revision is item metadata
(bruceworx.pdm). A file declaring the old ``...-{SSS}-{REV}`` form issues
``P/N-REV`` with its program's ``revision`` as before, until
:func:`migrate_numbering_file` rewrites it: the format changes, each
program's ``revision`` moves to the ``migrated`` record, the counters stay.

Ops source, in the numbering file or as a spec dict::

    {
      "source": "ops",
      "base_url": "https://ops.example/api",
      "token_env": "BW_OPS_TOKEN"
    }

A vault's numbering.json may keep its ``programs`` and document-type tables
next to those keys; the pickers then list them, while numbers come from Ops.

HTTP contract (docs/pdm-openapi.yaml, ``POST /part-numbers``)::

    POST {base_url}/part-numbers
    {"program": "LE", "document_type": "01", "parent": null}
    -> {"part_number": "LE-01-01001-001"}

    POST {base_url}/part-numbers
    {"program": "LE", "document_type": "01", "parent": "01001"}
    -> {"part_number": "LE-01-01001-002"}

``parent`` null reserves the next parent drawing and its first sub-assembly.
A five-digit ``parent`` reserves only the next sub-assembly under that drawing.
A reply in the old ``P/N-REV`` form is accepted and its revision dropped.
Authentication is a bearer token read from the environment variable named by
``token_env`` (default ``BW_OPS_TOKEN``); without it nothing is sent. The
token only travels over https (plain http is allowed to this machine only).
No preview, program or document-type endpoint is documented, so
:meth:`OpsApiNumberSource.preview` raises and the lists come from the file.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

from heuretes_freecad.numbering import (
    DEFAULT_NEXT_PARENT,
    DEFAULT_NEXT_SUB,
    FORMATS,
    LEGACY_FORMAT,
    PN_FORMAT,
    format_part_number,
    normalize_document_type,
    normalize_parent,
    normalize_program,
    split_revision,
)

DEFAULT_TOKEN_ENV = "HEU_OPS_TOKEN"


def _named_env(name: str) -> str:
    """Read ``name``. A BW_* name also accepts HEU_*, and the other way around."""
    if name in os.environ:
        return os.environ[name]
    if name.startswith("BW_") and ("HEU_" + name[3:]) in os.environ:
        return os.environ["HEU_" + name[3:]]
    if name.startswith("HEU_") and ("BW_" + name[4:]) in os.environ:
        return os.environ["BW_" + name[4:]]
    return ""
DEFAULT_TIMEOUT = 10.0
MIGRATION_BACKUP_SUFFIX = ".pre-pdm03"


class NumberSource(ABC):
    """Reserve BruceWorx part numbers.

    Implementations must be safe for one caller at a time. The Ops API reader
    is the place for cross-process allocation.
    """

    @abstractmethod
    def allocate(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        """Reserve the next part number and return it.

        ``parent`` None takes the next parent drawing for ``program`` +
        ``document_type`` and issues sub-assembly 001. A parent drawing
        number takes the next sub-assembly under that parent and does not
        advance ``next_parent``.
        """

    @abstractmethod
    def preview(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        """Return the next part number without reserving it."""

    def programs(self) -> list[str]:
        """Program codes this source can issue. Empty if it cannot list them."""
        return []

    def document_types(self) -> list[str]:
        """Document type codes this source accepts. Empty if it cannot list them."""
        return []


class JsonNumberSource(NumberSource):
    """Local JSON counter. Single writer. Updates are replaced atomically."""

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)

    def allocate(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        data = self._load()
        formatted, _parent_key, _sub = self._issue(
            data, program, document_type, revision, parent, write=True
        )
        self._save(data)
        return formatted

    def preview(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        data = self._load()
        formatted, _parent_key, _sub = self._issue(
            data, program, document_type, revision, parent, write=False
        )
        return formatted

    def programs(self) -> list[str]:
        """Program codes offered for new numbers: retired ones (``"retired": true``,
        set by Vault Settings) are left out but still allocate when named."""
        return _listed(self._load()["programs"])

    def document_types(self) -> list[str]:
        """Document type codes offered for new numbers, without retired ones."""
        return _listed(self._document_types(self._load()))

    @property
    def issues_revision(self) -> bool:
        """True while the file is in the old ``P/N-REV`` form (not migrated yet)."""
        return _declared_format(self._load()) == LEGACY_FORMAT

    def _issue(self, data, program, document_type, revision, parent, write: bool):
        entry, key = self._program(data, program)
        code = self._known_document_type(data, document_type)
        if _declared_format(data) == LEGACY_FORMAT:
            rev = revision if revision is not None else entry.get("revision")
        elif revision is not None:
            raise ValueError(
                f"{self.path} issues part numbers without a revision ({PN_FORMAT}); "
                "the revision belongs to the item (01, 02, ... then A at release)"
            )
        else:
            rev = None
        counters = entry.setdefault("counters", {})
        bucket = counters.setdefault(
            code, {"next_parent": DEFAULT_NEXT_PARENT, "parents": {}}
        )
        parents = bucket.setdefault("parents", {})
        if parent is None:
            parent_number = _as_int(bucket.get("next_parent"), f"{key}/{code} next_parent")
            parent_key = normalize_parent(parent_number)
            if parent_key in parents:
                raise ValueError(
                    f"parent {parent_key} already exists under {key}/{code}; "
                    "next_parent is behind the issued drawings"
                )
            slot = {"next_sub": DEFAULT_NEXT_SUB}
            sub_number = DEFAULT_NEXT_SUB
            if write:
                parents[parent_key] = slot
                bucket["next_parent"] = parent_number + 1
                slot["next_sub"] = sub_number + 1
        else:
            parent_key = normalize_parent(parent)
            slot = parents.get(parent_key)
            if slot is None:
                known = ", ".join(sorted(parents)) or "(none)"
                raise KeyError(
                    f"unknown parent {parent_key} for {key}/{code}; known: {known}"
                )
            sub_number = _as_int(slot.get("next_sub"), f"{key}/{code}/{parent_key} next_sub")
            if write:
                slot["next_sub"] = sub_number + 1
        formatted = format_part_number(key, code, parent_key, sub_number, rev)
        return formatted, parent_key, sub_number

    def _known_document_type(self, data, document_type: str) -> str:
        code = normalize_document_type(document_type)
        table = self._document_types(data)
        if code not in table:
            known = ", ".join(sorted(table))
            raise KeyError(f"unknown document type {document_type!r}; known: {known}")
        return code

    def _document_types(self, data: dict) -> dict:
        return _document_types_of(data, self.path)

    def _load(self) -> dict:
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} must contain a JSON object")
        kind = data.get("source", "json")
        if kind != "json":
            raise ValueError(
                f"{self.path} declares source {kind!r}; open it with "
                "open_number_source(), which returns the Ops API reader (FCB-P2-03)."
            )
        declared = _declared_format(data)
        if declared not in FORMATS:
            raise ValueError(
                f"{self.path} format {declared!r} is not {PN_FORMAT!r} "
                f"(or the old {LEGACY_FORMAT!r})"
            )
        if "number_width" in data:
            raise ValueError(
                f"{self.path} uses removed key number_width; counters are "
                "next_parent per program+document type and next_sub per parent"
            )
        programs = data.get("programs")
        if not isinstance(programs, dict) or not programs:
            raise ValueError(f"{self.path} needs a non-empty programs object")
        return data

    def _program(self, data: dict, program: str) -> tuple[dict, str]:
        canonical = normalize_program(program)
        programs = data["programs"]
        key = None
        for name in programs:
            if str(name).upper() == canonical:
                key = name
                break
        if key is None:
            known = ", ".join(sorted(str(name) for name in programs))
            raise KeyError(f"unknown program {program!r}; known: {known}")
        entry = programs[key]
        if not isinstance(entry, dict):
            raise ValueError(f"program {key} must be an object")
        if "revision" not in entry and _declared_format(data) == LEGACY_FORMAT:
            raise ValueError(f"program {key} needs revision (or migrate the file to {PN_FORMAT})")
        if key != canonical:
            programs[canonical] = entry
            del programs[key]
            key = canonical
        return entry, key

    def _save(self, data: dict) -> None:
        _write_json(self.path, data)


class OpsApiError(RuntimeError):
    """The Ops API refused a request, or could not be reached."""


class OpsApiNumberSource(NumberSource):
    """Part numbers from the BruceWorx Ops API (FCB-P2-03).

    :meth:`allocate` posts to ``{base_url}/part-numbers`` (contract in the
    module docstring and docs/pdm-openapi.yaml) and returns the P/N, without a
    revision. :meth:`preview` has no documented endpoint and raises.
    ``programs`` and ``document_types`` are the codes the pickers offer; Ops
    has no listing endpoint, so they come from the numbering file
    (:func:`open_number_source` fills them), else they are empty.
    """

    def __init__(
        self,
        base_url: str,
        token_env: str = DEFAULT_TOKEN_ENV,
        timeout: float = DEFAULT_TIMEOUT,
        programs=None,
        document_types=None,
    ):
        if not base_url or not str(base_url).strip():
            raise ValueError("ops source needs base_url")
        self.base_url = str(base_url).strip().rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError(f"ops base_url {base_url!r} must be an https:// address")
        if parsed.scheme == "http" and not _loopback(parsed.hostname):
            raise ValueError(
                f"ops base_url {base_url!r} must use https: the bearer token would travel in clear text"
            )
        self.token_env = token_env or DEFAULT_TOKEN_ENV
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self._programs = sorted(programs or [])
        self._document_types = sorted(document_types or [])

    @property
    def endpoint(self) -> str:
        return self.base_url + "/part-numbers"

    def allocate(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        if revision is not None:
            raise ValueError(
                "Ops issues part numbers without a revision; the revision belongs to the item"
            )
        body = {
            "program": normalize_program(program),
            "document_type": normalize_document_type(document_type),
            "parent": normalize_parent(parent) if parent is not None else None,
        }
        reply = self._post(body)
        number = reply.get("part_number") if isinstance(reply, dict) else None
        if not isinstance(number, str):
            raise OpsApiError(f"Ops reply from {self.endpoint} has no part_number: {reply!r}")
        try:
            pn, _revision = split_revision(number)
        except ValueError as exc:
            raise OpsApiError(f"Ops issued {number!r}, which is not a part number: {exc}") from None
        fields = pn.split("-")
        if (fields[0], fields[1]) != (body["program"], body["document_type"]):
            raise OpsApiError(
                f"Ops issued {pn} for program {body['program']} type {body['document_type']}"
            )
        if body["parent"] is not None and fields[2] != body["parent"]:
            raise OpsApiError(f"Ops issued {pn} for parent drawing {body['parent']}")
        return pn

    def preview(
        self,
        program: str,
        document_type: str,
        revision: str | None = None,
        parent: str | None = None,
    ) -> str:
        raise NotImplementedError(
            "The Ops API has no documented preview of the next part number "
            f"(only POST {self.endpoint}). Request would be program={program!r}, "
            f"document_type={document_type!r}, parent={parent!r}."
        )

    def programs(self) -> list[str]:
        return list(self._programs)

    def document_types(self) -> list[str]:
        return list(self._document_types)

    def _post(self, body: dict) -> dict:
        token = _named_env(self.token_env).strip()
        if not token:
            raise OpsApiError(
                f"set {self.token_env} to your Ops API token; nothing was sent to {self.endpoint}"
            )
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # A local Ops (tests, a dev server) never goes through a proxy.
        handlers = [urllib.request.ProxyHandler({})] if _loopback(urllib.parse.urlsplit(self.endpoint).hostname) else []
        try:
            with urllib.request.build_opener(*handlers).open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise OpsApiError(
                f"Ops refused {body} at {self.endpoint}: HTTP {exc.code} {_error_text(exc)}"
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OpsApiError(f"cannot reach Ops at {self.endpoint}: {reason}") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpsApiError(f"Ops reply from {self.endpoint} is not JSON: {raw[:200]!r}") from None


def open_number_source(spec) -> NumberSource:
    """Build a source from a numbering file path or a spec dict.

    The file's (or dict's) ``source`` picks it: ``json`` (the default) is
    :class:`JsonNumberSource`; ``ops`` is :class:`OpsApiNumberSource` with the
    file's ``base_url``, ``token_env`` and ``timeout``, and the file's program
    and document-type tables (if any) as the codes the pickers offer.
    """
    if isinstance(spec, dict):
        kind = spec.get("source", "json")
        if kind == "json":
            path = spec.get("path")
            if not path:
                raise ValueError("json source spec needs path")
            return JsonNumberSource(path)
        if kind == "ops":
            return _ops_source(spec, spec.get("path"))
        raise ValueError(f"unknown numbering source {kind!r}")
    if isinstance(spec, (str, os.PathLike)):
        path = os.fspath(spec)
        kind = "json"
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                try:
                    data = json.load(handle)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}: {exc}") from None
            if isinstance(data, dict):
                kind = data.get("source", "json")
            if kind == "ops":
                return _ops_source(data, path)
            if kind != "json":
                raise ValueError(f"{path}: unknown numbering source {kind!r}")
        return JsonNumberSource(path)
    raise TypeError(f"numbering spec must be a path or dict, got {type(spec).__name__}")


def _ops_source(data: dict, path) -> OpsApiNumberSource:
    programs = data.get("programs") if isinstance(data.get("programs"), dict) else {}
    types = {}
    if "document_types" in data or (path and "programs" in data):
        try:
            types = _document_types_of(data, path or "")
        except (OSError, ValueError):
            types = {}
    return OpsApiNumberSource(
        base_url=data.get("base_url", ""),
        token_env=data.get("token_env", DEFAULT_TOKEN_ENV),
        timeout=data.get("timeout", DEFAULT_TIMEOUT),
        programs=_listed(programs),
        document_types=_listed(types),
    )


# ---------------------------------------------------------------------------
# Migration of numbering files written before BW-PDM-03.


def migrate_numbering_data(data: dict) -> tuple[dict, dict]:
    """``({...-{REV}} file) -> (migrated copy, report)``. Counters stay exactly as
    they were, so the next number is the one the old file would have issued,
    without its revision. Each program's ``revision`` moves to
    ``migrated.revisions``. A file already in the new form comes back unchanged
    with ``report["changed"]`` False.
    """
    if not isinstance(data, dict):
        raise ValueError("numbering data must be a JSON object")
    declared = _declared_format(data)
    if declared == PN_FORMAT:
        return data, {"changed": False, "from": PN_FORMAT, "to": PN_FORMAT, "revisions": {}}
    if declared != LEGACY_FORMAT:
        raise ValueError(f"cannot migrate format {declared!r}; expected {LEGACY_FORMAT!r}")
    out = json.loads(json.dumps(data))
    revisions = {}
    for name, entry in (out.get("programs") or {}).items():
        if isinstance(entry, dict) and "revision" in entry:
            revisions[str(name)] = entry.pop("revision")
    out["format"] = PN_FORMAT
    out["migrated"] = {
        "from": LEGACY_FORMAT,
        "to": PN_FORMAT,
        "by": "BW-PDM-03",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "revisions": revisions,
    }
    return out, {"changed": True, "from": LEGACY_FORMAT, "to": PN_FORMAT, "revisions": revisions}


def migrate_numbering_file(path, backup: bool = True) -> dict:
    """Rewrite a numbering file in the P/N form (see :func:`migrate_numbering_data`).

    The old file is kept once as ``<name>.pre-pdm03`` (never overwritten). The
    new file replaces the old one atomically. An Ops source file has no
    counters to migrate and is left alone. Returns a JSON-ready report.
    """
    path = os.fspath(path)
    with open(path, encoding="utf-8") as handle:
        original = handle.read()
    data = json.loads(original)
    report = {"path": path, "backup": ""}
    if isinstance(data, dict) and data.get("source", "json") != "json":
        report.update(changed=False, revisions={}, note=f"source {data.get('source')!r} keeps no counters")
        return report
    migrated, result = migrate_numbering_data(data)
    report.update(result)
    if not result["changed"]:
        return report
    if backup:
        target = path + MIGRATION_BACKUP_SUFFIX
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(original)
        report["backup"] = target
    _write_json(path, migrated)
    return report


def _coerce_document_types(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("document type table must be a JSON object")
    table = raw["types"] if isinstance(raw.get("types"), dict) else raw
    cleaned = {}
    for key, entry in table.items():
        if key in {"note", "placeholder", "source", "types"}:
            continue
        code = normalize_document_type(key)
        if not isinstance(entry, dict):
            raise ValueError(f"document type {code} must be an object")
        cleaned[code] = entry
    if not cleaned:
        raise ValueError("document type table is empty")
    return cleaned


def _retired(entry) -> bool:
    return isinstance(entry, dict) and bool(entry.get("retired"))


def _as_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    if value < 0:
        raise ValueError(f"{label} must be >= 0, got {value!r}")
    return value


def _declared_format(data: dict) -> str:
    return data.get("format", LEGACY_FORMAT)


def _listed(table) -> list[str]:
    """Codes of a programs or document-type table, without retired ones."""
    return sorted(str(code) for code, entry in (table or {}).items() if not _retired(entry))


def _document_types_of(data: dict, numbering_path: str) -> dict:
    if "document_types" in data:
        return _coerce_document_types(data["document_types"])
    ref = data.get("document_types_file", "document_types.json")
    path = ref if os.path.isabs(ref) else os.path.join(os.path.dirname(numbering_path), ref)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"document type table not found: {path}")
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return _coerce_document_types(raw)


def _write_json(path: str, data: dict) -> None:
    """Replace ``path`` atomically (single writer)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _error_text(exc) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 -- best effort for the message
        return str(getattr(exc, "reason", ""))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:200]
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return raw.strip()[:200]
