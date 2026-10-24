# SPDX-License-Identifier: LGPL-2.1-or-later
"""Move a folder vault to another place, such as an external SSD (BW-PDM-04, 23 Oct).

The vault is copied, not moved, and nothing at the old place changes until the copy is
proven equal: every file is compared by SHA-256, and the copy is opened and must replay to
the same state (items, versions, locks). The copy is written under ``<name>.moving`` and
renamed when it checks out, so an interrupted move never leaves a half vault that looks
whole. Switching FreeCAD to the new place and putting the old copy in the Trash are the
caller's steps (bruceworx_vaultwizard.py), after this returns.

No FreeCAD import.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from bruceworx.pdm_folder import REF_SUFFIX, FolderVault, find_ref_file, sha256_of

MOVING_SUFFIX = ".moving"
SKIP_NAMES = (".DS_Store",)
SPACE_MARGIN = 1.1  # ask for 10 % more free space than the vault uses


@dataclass
class MovePlan:
    source: str
    target: str  # the vault's new folder
    files: int = 0
    size: int = 0
    issues: list = field(default_factory=list)  # [(level, text)]

    @property
    def ok(self):
        return not any(level == "error" for level, _ in self.issues)


def _walk(root):
    for current, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith(".tmp"):
                continue
            yield os.path.join(current, name)


def vault_root(path: str) -> str:
    return os.path.dirname(find_ref_file(path))


def plan_move(source: str, destination_parent: str, free_bytes=None) -> MovePlan:
    """What a move of the vault at source into destination_parent would do, and what stops it.

    free_bytes(path) returns the free space there (shutil.disk_usage by default; tests pass one).
    """
    source = vault_root(source)
    target = os.path.join(os.path.abspath(destination_parent), os.path.basename(source.rstrip(os.sep)))
    plan = MovePlan(source, target)
    for path in _walk(source):
        plan.files += 1
        plan.size += os.path.getsize(path)
    real_source = os.path.realpath(source)
    real_target = os.path.realpath(target)
    if real_target == real_source:
        plan.issues.append(("error", "That is where the vault already is."))
    elif (real_target + os.sep).startswith(real_source + os.sep):
        plan.issues.append(("error", "The new place is inside the vault."))
    if os.path.exists(target) and os.listdir(target):
        plan.issues.append(("error", f"{target} already exists and is not empty."))
    parent = os.path.abspath(destination_parent)
    if not os.path.isdir(parent):
        plan.issues.append(("error", f"{parent} does not exist."))
    elif not os.access(parent, os.W_OK):
        plan.issues.append(("error", f"{parent} cannot be written to."))
    else:
        free = (free_bytes or (lambda p: shutil.disk_usage(p).free))(parent)
        if free < plan.size * SPACE_MARGIN:
            plan.issues.append(("error", f"Not enough space: the vault needs {plan.size / 1e6:.0f} MB, "
                                         f"{free / 1e6:.0f} MB is free there."))
    try:
        missing = FolderVault(find_ref_file(source)).missing_objects()
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the dialog
        plan.issues.append(("error", f"The vault cannot be read: {exc}"))
    else:
        if missing:
            plan.issues.append(("error", f"{len(missing)} file(s) have not finished downloading to this Mac "
                                         "(iCloud); wait for them, then move."))
    return plan


def _same_state(a: FolderVault, b: FolderVault) -> bool:
    def snapshot(v):
        return (
            sorted(v.model.items),
            {pn: [x.sha256 for x in versions] for pn, versions in v.versions.items()},
            dict(v.locks),
        )

    return snapshot(a) == snapshot(b)


def move_vault(plan: MovePlan, progress=None) -> str:
    """Copy and verify; returns the new vault folder. The old one is left as it was.

    progress(done_bytes, total_bytes, path) is called after each file and may return False to stop
    (the partial copy is removed).
    """
    if not plan.ok:
        raise ValueError("; ".join(text for level, text in plan.issues if level == "error"))
    staging = plan.target + MOVING_SUFFIX
    if os.path.exists(staging):
        shutil.rmtree(staging)
    done = 0
    try:
        for path in _walk(plan.source):
            rel = os.path.relpath(path, plan.source)
            dest = os.path.join(staging, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)  # keeps read-only work files read-only
            if sha256_of(dest) != sha256_of(path):
                raise OSError(f"{rel} did not copy correctly")
            done += os.path.getsize(path)
            if progress is not None and progress(done, plan.size, rel) is False:
                raise InterruptedError("move stopped")
        old = FolderVault(find_ref_file(plan.source))
        new = FolderVault(find_ref_file(staging), actor=old.actor)
        if not _same_state(old, new):
            raise OSError("the copy does not replay to the same vault state")
        if os.path.exists(plan.target):
            os.rmdir(plan.target)  # empty (plan_move refused a non-empty one)
        os.replace(staging, plan.target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return plan.target


def ref_in(folder: str) -> str:
    """The ref file of the vault in folder (for setting BruceWorx/VaultPath to the new place)."""
    names = [n for n in os.listdir(folder) if n.endswith(REF_SUFFIX)]
    return os.path.join(folder, names[0]) if names else ""
