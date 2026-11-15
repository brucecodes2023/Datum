# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless acceptance corpus check (BW-FND-05, early slice).

From the repo root:

    python3 package/bruceworx/acceptance_corpus.py
    pixi run acceptance

The manifest lists each corpus file and what it is for. A missing file is
reported and does not fail the run. A STEP that is present must be non-empty.
This does not open FreeCAD or import geometry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "corpus" / "manifest.json"
KINDS = ("step", "native")
REQUIRED = ("id", "path", "kind", "purpose")


def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest has no entries")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("manifest entry is not an object")
        for key in REQUIRED:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"manifest entry missing {key}")
        if entry["kind"] not in KINDS:
            raise ValueError(f"{entry['id']}: kind must be step or native")
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"{entry['id']}: path must stay inside the repo")
        if entry["id"] in seen:
            raise ValueError(f"duplicate id {entry['id']}")
        seen.add(entry["id"])
    return entries


def check_entry(entry: dict, root: Path) -> dict:
    path = root / entry["path"]
    result = {
        "id": entry["id"],
        "path": entry["path"],
        "kind": entry["kind"],
        "purpose": entry["purpose"],
        "status": "ok",
        "detail": "",
    }
    if not path.exists():
        result["status"] = "missing"
        result["detail"] = "not in the tree"
        return result
    if not path.is_file():
        result["status"] = "failed"
        result["detail"] = "not a file"
        return result
    size = path.stat().st_size
    if entry["kind"] == "step" and size == 0:
        result["status"] = "failed"
        result["detail"] = "STEP is empty"
        return result
    result["detail"] = f"{size} bytes"
    return result


def run_checks(entries: list[dict], root: Path) -> dict:
    results = [check_entry(entry, root) for entry in entries]
    counts = {
        status: sum(row["status"] == status for row in results)
        for status in ("ok", "missing", "failed")
    }
    return {
        "results": results,
        "counts": counts,
        "exit_code": 1 if counts["failed"] else 0,
    }


def format_report(report: dict) -> str:
    lines = [
        f"{row['status']:7}  {row['kind']:6}  {row['path']}  ({row['detail']})"
        for row in report["results"]
    ]
    counts = report["counts"]
    lines.append(
        "acceptance: {ok} ok, {missing} missing, {failed} failed".format(**counts)
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the BruceWorx acceptance corpus.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        entries = load_manifest(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"acceptance: manifest error: {exc}", file=sys.stderr)
        return 2
    report = run_checks(entries, args.root)
    sys.stdout.write(format_report(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
