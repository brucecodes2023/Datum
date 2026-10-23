# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx hooks command line.

Geometry commands open the document themselves. They are meant to run under
FreeCADCmd, which also treats loose arguments as files to open, so prefer one
of:

    FreeCADCmd -- addons/BruceWorx/hooks.py export-step --input a.FCStd --output a.step
    BW_HOOKS_ARGS='export-step --input a.FCStd --output a.step' \\
        FreeCADCmd addons/BruceWorx/hooks.py

``next`` and ``preview`` also run under plain Python.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import sys
import traceback

from bruceworx import freecad_io
from bruceworx.config_path import resolve_config_path
from bruceworx.numbering import assign_part_numbers, parse_part_number
from bruceworx.sources import open_number_source
from bruceworx.step_export import export_step
from bruceworx.tree import document_tree

COMMANDS = ("export-step", "list-tree", "rename", "next", "preview", "vault")
VAULT_ACTIONS = (
    "init", "status", "create", "checkout", "checkin", "undo-checkout",
    "submit", "approve", "reject", "fetch", "sync", "import", "settings",
)
_ENTRY_NAMES = {"hooks.py", "hooks"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bw-hooks",
        description="Drive FreeCAD headlessly and issue BruceWorx part numbers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export-step", help="Export solids from an FCStd file to STEP")
    export.add_argument("--input", required=True, help="Source .FCStd document")
    export.add_argument("--output", required=True, help="Destination .step or .stp file")
    export.add_argument(
        "--objects",
        default="",
        help="Comma-separated object Name or Label values (default: solid roots)",
    )

    listing = sub.add_parser("list-tree", help="Write the document tree as JSON")
    listing.add_argument("--input", required=True, help="Source .FCStd document")
    listing.add_argument(
        "--output",
        default="",
        help="Write the tree JSON here (stdout BW_RESULT is a short pointer)",
    )

    rename = sub.add_parser(
        "rename",
        help="Set Labels and the document Label from the part-number source",
    )
    rename.add_argument("--input", required=True, help="Source .FCStd document")
    rename.add_argument("--program", required=True, help="Program code, for example LE")
    rename.add_argument(
        "--document-type",
        "--dt",
        required=True,
        help="2-digit document type, for example 01",
    )
    rename.add_argument("--config", default="", help="Numbering JSON (default: local config)")
    rename.add_argument("--revision", default="", help="Override the program's revision")
    rename.add_argument(
        "--parent",
        default="",
        help="Number under this parent drawing instead of taking a new one",
    )
    rename.add_argument(
        "--objects",
        default="",
        help="Comma-separated object Name or Label values (default: solid roots)",
    )
    rename.add_argument(
        "--output",
        default="",
        help="Save as this .FCStd (default: <directory>/<part-number>.FCStd)",
    )

    nxt = sub.add_parser("next", help="Allocate the next part number (no FreeCAD)")
    nxt.add_argument("--program", required=True)
    nxt.add_argument("--document-type", "--dt", required=True)
    nxt.add_argument("--config", default="")
    nxt.add_argument("--revision", default="")
    nxt.add_argument("--parent", default="", help="Existing parent drawing (PPPPP)")

    preview = sub.add_parser("preview", help="Show the next part number without allocating")
    preview.add_argument("--program", required=True)
    preview.add_argument("--document-type", "--dt", required=True)
    preview.add_argument("--config", default="")
    preview.add_argument("--revision", default="")
    preview.add_argument("--parent", default="", help="Existing parent drawing (PPPPP)")

    vault = sub.add_parser("vault", help="Folder PDM vault (no FreeCAD needed)")
    vault.add_argument("action", choices=VAULT_ACTIONS)
    vault.add_argument("--vault", required=True, help="Vault folder or its .bwvault ref file")
    vault.add_argument("--name", default="", help="init: vault name (default: folder name)")
    vault.add_argument("--pn", default="", help="Part number, without revision")
    vault.add_argument("--type", default="part", help="create: part, assembly, drawing or cots")
    vault.add_argument("--description", default="")
    vault.add_argument("--drawing-of", default="", help="create: the part a drawing documents")
    vault.add_argument("--file", default="", help="checkin: the saved .FCStd; sync: the .FCStd to update")
    vault.add_argument("--workspace", default="", help="checkout/fetch: copy the file here")
    vault.add_argument("--comment", default="", help="checkin comment or reject reason")
    vault.add_argument("--actor", default="", help="Who is acting (default: user@host)")
    # init: numbering and company properties (vault_setup); without them init makes a bare vault.
    vault.add_argument("--program", default="", help="init: program codes, e.g. BW,LE; import: the program")
    vault.add_argument("--document-types", default="", help='init: e.g. "01=Part,02=Assembly,03=Drawing"')
    vault.add_argument("--first-parent", type=int, default=None, help="init: first PPPPP (default 1001)")
    vault.add_argument("--first-revision", default="", help="init: revision in new numbers (default A)")
    vault.add_argument("--properties-file", default="", help="init: company part properties (JSON list or layer)")
    vault.add_argument("--from", dest="source_folder", default="", help="import: folder of .FCStd files")
    vault.add_argument("--dry-run", action="store_true",
                       help="import: show the plan, change nothing; settings --import: check only")
    # settings: the vault's programs, document types and company properties as one CSV file.
    vault.add_argument("--export", dest="export_csv", default="", help="settings: write the tables to this CSV")
    vault.add_argument("--import", dest="import_csv", default="",
                       help="settings: add, rename or retire rows from this CSV")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    try:
        args = build_parser().parse_args(_command_argv(argv))
        if args.command == "export-step":
            return _export_step(args)
        if args.command == "list-tree":
            return _list_tree(args)
        if args.command == "rename":
            return _rename(args)
        if args.command == "next":
            return _number_command(args, allocate=True)
        if args.command == "preview":
            return _number_command(args, allocate=False)
        if args.command == "vault":
            return _vault_command(args)
        print(f"error: unknown command {args.command}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        if exc.code:
            print(exc.code, file=sys.stderr)
            return 1
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 — CLI boundary
        traceback.print_exc()
        return 1


def split_entrypoint_argv(argv: list[str]) -> list[str]:
    """Strip FreeCADCmd / hooks.py noise off a full ``sys.argv``.

    Accepts an already-stripped command list as well.
    """
    args = list(argv)
    if args and os.path.basename(args[0]) not in _ENTRY_NAMES and args[0] not in COMMANDS:
        if args[0] not in {"-h", "--help"} and not args[0].startswith("-"):
            args = args[1:]
    if args and args[0] == "--":
        args = args[1:]
    if args and os.path.basename(args[0]) in _ENTRY_NAMES:
        args = args[1:]
    if args and args[0] == "--":
        args = args[1:]
    return args


def emit(payload: dict, output: str | None = None) -> None:
    """Write pretty JSON to ``output`` if given, and one ``BW_RESULT`` line."""
    if output:
        path = os.path.abspath(output)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    print(f"BW_RESULT {line}", flush=True)


def _command_argv(argv: list[str] | None) -> list[str]:
    if argv is not None:
        return list(argv)
    env = os.environ.get("BW_HOOKS_ARGS")
    if env:
        return shlex.split(env)
    return split_entrypoint_argv(sys.argv)


def _export_step(args) -> int:
    freecad_io.require_freecad()
    _require_fcstd(args.input)
    doc = freecad_io.open_document(args.input)
    try:
        names = _object_names(args.objects)
        chosen = None
        if names:
            from bruceworx.numbering import part_objects  # noqa: PLC0415

            chosen = part_objects(doc, names)
        result = export_step(doc, args.output, objects=chosen)
    finally:
        freecad_io.close_document(doc)
    emit(
        {
            "ok": True,
            "command": "export-step",
            "input": os.path.abspath(args.input),
            "output": result["output"],
            "objects": result["objects"],
        }
    )
    return 0


def _list_tree(args) -> int:
    freecad_io.require_freecad()
    _require_fcstd(args.input)
    doc = freecad_io.open_document(args.input)
    try:
        tree = document_tree(doc)
    finally:
        freecad_io.close_document(doc)
    if args.output:
        emit_path = os.path.abspath(args.output)
        directory = os.path.dirname(emit_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as handle:
            json.dump(tree, handle, indent=2)
            handle.write("\n")
        emit(
            {
                "ok": True,
                "command": "list-tree",
                "output": emit_path,
                "roots": len(tree["tree"]),
            }
        )
        return 0
    payload = {"ok": True, "command": "list-tree"}
    payload.update(tree)
    emit(payload)
    return 0


def _rename(args) -> int:
    freecad_io.require_freecad()
    _require_fcstd(args.input)
    if args.output:
        _require_fcstd_name(args.output)
    source = open_number_source(resolve_config_path(args.config or None, write=True))
    doc = freecad_io.open_document(args.input)
    try:
        names = _object_names(args.objects)
        targets = None
        if names:
            from bruceworx.numbering import part_objects  # noqa: PLC0415

            targets = part_objects(doc, names)
        assigned = assign_part_numbers(
            doc,
            source,
            args.program,
            args.document_type,
            objects=targets,
            revision=args.revision or None,
            parent=args.parent or None,
        )
        output = args.output or _default_rename_output(args.input, assigned["document_label"])
        saved = freecad_io.save_document(doc, output)
    finally:
        freecad_io.close_document(doc)
    assigned["ok"] = True
    assigned["command"] = "rename"
    assigned["output"] = saved
    emit(assigned)
    return 0


def _number_command(args, *, allocate: bool) -> int:
    path = resolve_config_path(args.config or None, write=allocate)
    source = open_number_source(path)
    revision = args.revision or None
    parent = args.parent or None
    if allocate:
        number = source.allocate(args.program, args.document_type, revision, parent)
        command = "next"
    else:
        number = source.preview(args.program, args.document_type, revision, parent)
        command = "preview"
    parsed = parse_part_number(number)
    emit(
        {
            "ok": True,
            "command": command,
            "program": parsed["program"],
            "document_type": parsed["document_type"],
            "parent": parsed["parent"],
            "sub": parsed["sub"],
            "part_number": number,
            "config": path,
        }
    )
    return 0


def _vault_command(args) -> int:
    from bruceworx.pdm_folder import FolderVault  # noqa: PLC0415

    actor = args.actor or None
    if args.action == "init":
        name = args.name or os.path.basename(os.path.abspath(args.vault))
        payload = {"ok": True, "command": "vault init"}
        if _has_setup_options(args):
            from bruceworx import vault_setup  # noqa: PLC0415

            setup = _vault_setup(args, name)
            vault = vault_setup.create_vault(setup, actor=actor)
            payload["numbering"] = vault_setup.numbering_path(vault.root)
            payload["example"] = vault_setup.example_number(setup.numbering)
        else:
            vault = FolderVault.init(args.vault, name, actor=actor)
        payload.update({"ref": vault.ref_path, "vault_id": vault.ref["vault_id"]})
        emit(payload)
        return 0
    if args.action == "settings":
        return _vault_settings(args)
    vault = FolderVault(args.vault, actor=actor)
    if freecad_io.freecad_available():
        # Inside FreeCAD: open parts follow their lifecycle state.
        from bruceworx import partprops  # noqa: PLC0415

        partprops.attach_vault(vault)
    payload = {"ok": True, "command": f"vault {args.action}"}
    if args.action == "sync":
        return _vault_sync(vault, args, payload)
    if args.action == "import":
        return _vault_import(vault, args, payload)
    if args.action != "status":
        if not args.pn:
            raise ValueError(f"vault {args.action} needs --pn")
        payload["part_number"] = args.pn
    if args.action == "create":
        vault.create_item(args.pn, args.type, args.description, args.drawing_of or None)
    elif args.action == "checkout":
        payload["path"] = vault.checkout(args.pn, workspace=args.workspace or None)
    elif args.action == "checkin":
        if not args.file:
            raise ValueError("vault checkin needs --file")
        payload["sha256"] = vault.checkin(args.pn, args.file, args.comment).sha256
    elif args.action == "undo-checkout":
        vault.undo_checkout(args.pn)
    elif args.action == "submit":
        vault.submit(args.pn)
    elif args.action == "approve":
        vault.approve(args.pn)
    elif args.action == "reject":
        vault.reject(args.pn, args.comment)
    elif args.action == "fetch":
        payload["path"] = vault.fetch(args.pn, args.workspace or os.getcwd())
    if args.action == "status":
        payload.update(vault.status())
    elif args.pn in vault.items:
        payload["revision"] = vault.items[args.pn].latest.code
        payload["state"] = vault.items[args.pn].state
    emit(payload)
    return 0


def _has_setup_options(args) -> bool:
    return bool(args.program or args.document_types or args.first_parent is not None
                or args.first_revision or args.properties_file)


def _vault_setup(args, name: str):
    """A vault_setup.VaultSetup from ``vault init`` options; defaults for the rest."""
    from bruceworx import vault_setup  # noqa: PLC0415

    numbering = vault_setup.NumberingSetup()
    if args.program:
        numbering.programs = [vault_setup.ProgramSetup(code.strip())
                              for code in args.program.split(",") if code.strip()]
    if args.document_types:
        types = {}
        for pair in args.document_types.split(","):
            code, sep, label = pair.partition("=")
            if not sep:
                raise ValueError(f"--document-types entry {pair!r} must be CODE=Name, e.g. 01=Part")
            types[code.strip()] = label.strip()
        numbering.document_types = types
    if args.first_parent is not None:
        numbering.first_parent = args.first_parent
    if args.first_revision:
        numbering.first_revision = args.first_revision
    properties = []
    if args.properties_file:
        with open(args.properties_file, encoding="utf-8") as handle:
            raw = json.load(handle)
        properties = raw.get("properties", []) if isinstance(raw, dict) else raw
        if not isinstance(properties, list):
            raise ValueError(f"{args.properties_file}: expected a list of properties")
    return vault_setup.VaultSetup(name=name, folder=args.vault, numbering=numbering, properties=properties)


def _vault_import(vault, args, payload) -> int:
    """Scan a folder, plan numbers, and (unless --dry-run) import into the vault.
    Under FreeCADCmd the part number is also written into each imported copy."""
    from bruceworx import vault_setup  # noqa: PLC0415
    from bruceworx.macro import sole_program  # noqa: PLC0415

    if not args.source_folder:
        raise ValueError("vault import needs --from <folder>")
    program = args.program or sole_program(open_number_source(vault_setup.numbering_path(vault.root)))
    candidates = vault_setup.scan_for_import(args.source_folder)
    vault_setup.plan_numbers(candidates, vault.root, program)
    for c in candidates:
        why = "; ".join(i.text for i in c.issues)
        print(f"{'+' if c.include else '-'} {c.proposed_part_number or '-':22} {c.kind:9} {c.path}"
              + (f"  ({why})" if why else ""))
    payload.update({"program": program.upper(), "dry_run": bool(args.dry_run),
                    "candidates": [dataclasses.asdict(c) for c in candidates]})
    if args.dry_run:
        emit(payload)
        return 0
    writer = freecad_io.write_part_number if freecad_io.freecad_available() else None
    report = vault_setup.run_import(vault, candidates, program, writer=writer)
    payload.update({"imported": report.imported, "skipped": report.skipped, "failed": report.failed,
                    "part_numbers_written": writer is not None})
    payload["ok"] = not report.failed
    emit(payload)
    return 1 if report.failed else 0


def _vault_settings(args) -> int:
    """Export the vault's tables to a CSV, or check and apply a CSV (vault_setup.save_settings)."""
    from bruceworx import vault_setup  # noqa: PLC0415
    from bruceworx.pdm_folder import find_ref_file  # noqa: PLC0415

    root = os.path.dirname(find_ref_file(os.path.expanduser(args.vault)))
    payload = {"ok": True, "command": "vault settings", "vault": root}
    if bool(args.export_csv) == bool(args.import_csv):
        raise ValueError("vault settings needs --export FILE or --import FILE")
    if args.export_csv:
        vault_setup.write_csv_file(args.export_csv, vault_setup.load_settings(root))
        payload["export"] = os.path.abspath(args.export_csv)
        emit(payload)
        return 0
    settings, issues = vault_setup.read_csv_file(args.import_csv)
    if not vault_setup._errors(issues):
        issues += vault_setup.settings_change_issues(root, settings)
    for issue in issues:
        print(f"{issue.level}: {issue.text}")
    errors = vault_setup._errors(issues)
    payload.update({"import": os.path.abspath(args.import_csv), "dry_run": bool(args.dry_run),
                    "issues": [dataclasses.asdict(i) for i in issues], "saved": False})
    if not errors and not args.dry_run:
        vault_setup.save_settings(root, settings)
        payload["saved"] = True
    payload["ok"] = not errors
    emit(payload)
    return 1 if errors else 0


def _vault_sync(vault, args, payload) -> int:
    """Write the vault's lifecycle states into a saved document (FreeCAD needed)."""
    freecad_io.require_freecad()
    if not args.file:
        raise ValueError("vault sync needs --file")
    _require_fcstd(args.file)
    from bruceworx import partprops  # noqa: PLC0415

    doc = freecad_io.open_document(args.file)
    try:
        changed = partprops.sync_states(doc, vault.states())
        if changed:
            doc.save()
    finally:
        freecad_io.close_document(doc)
    payload.update({"file": os.path.abspath(args.file), "changed": changed})
    emit(payload)
    return 0


def _default_rename_output(input_path: str, part_number: str) -> str:
    """File name FreeCAD will show as the document label after the next open.

    On load, FreeCAD sets Document.Label from the file name and discards the
    label stored in the file. Saving as ``<part-number>.FCStd`` is what makes
    the document name stick.
    """
    directory = os.path.dirname(os.path.abspath(input_path))
    return os.path.join(directory, part_number + ".FCStd")


def _object_names(text: str) -> list[str] | None:
    if not text.strip():
        return None
    return [part.strip() for part in text.split(",") if part.strip()]


def _require_fcstd(path: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    _require_fcstd_name(path)


def _require_fcstd_name(path: str) -> None:
    if not str(path).lower().endswith(".fcstd"):
        raise ValueError(f"document path must end in .FCStd, got {path!r}")
