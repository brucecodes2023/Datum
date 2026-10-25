# SPDX-License-Identifier: LGPL-2.1-or-later
"""A scratch folder vault with N items and about 4.6 N journal events (PDM-10 plan, spike_gen.py).

74 % parts, 11 % assemblies with BOMs, 17 % drawings, 4 % COTS; events spread over 120 days with
two actors (bruce@mac does most, alex@mbp holds a few locks); releases, ECRs and ECOs (some
released), items in review, locks and work copies. FreeCAD-free; used by the dashboard tests and,
as ``python3 vault_fixture.py <folder> <items>``, to make the scratch vaults the GUI checks open.
"""

import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm_folder  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402
from bruceworx.pdm_workspace import Workspace  # noqa: E402

ME = "bruce@mac"
OTHER = "alex@mbp"


def make_vault(folder, n_items, seed=7, days=120, name="Spike", tops=3):
    """Make the vault; returns its FolderVault (as bruce@mac). The clock is simulated, then put back."""
    rng = random.Random(seed)
    clock = [dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)]
    step = dt.timedelta(days=days) / (n_items * 4.6)

    def fake_now():
        clock[0] += step * rng.uniform(0.2, 1.8)
        return clock[0].isoformat(timespec="microseconds")

    real_now, real_fsync = pdm_folder._now, os.fsync
    pdm_folder._now = fake_now
    os.fsync = lambda fd: None  # speed only
    try:
        return _generate(folder, n_items, rng, name, tops)
    finally:
        pdm_folder._now, os.fsync = real_now, real_fsync


def _generate(folder, n_items, rng, name, tops):
    v = FolderVault.init(folder, name, actor=ME)
    src = os.path.join(os.path.dirname(os.path.abspath(folder)), "src-" + os.path.basename(folder))
    os.makedirs(src, exist_ok=True)
    n_asm, n_dwg, n_cots = n_items * 11 // 100, n_items * 17 // 100, n_items * 4 // 100
    n_part = n_items - n_asm - n_dwg - n_cots
    parts = [f"VX-01-{10000 + i:05d}-001" for i in range(n_part)]
    cots = [f"VX-05-{10000 + i:05d}-001" for i in range(n_cots)]
    asms = [f"VX-02-{10000 + i:05d}-001" for i in range(n_asm)]
    for pn in parts:
        v.create_item(pn, "part", f"Part {pn[6:11]}")
    for pn in cots:
        v.create_item(pn, "cots", f"Bought {pn[6:11]}")
    for pn in asms:
        v.create_item(pn, "assembly", f"Assembly {pn[6:11]}")
    dwgs = []
    for i, of in enumerate(rng.sample(parts + asms, n_dwg)):
        pn = f"VX-03-{10000 + i:05d}-001"
        v.create_item(pn, "drawing", f"Drawing of {of}", drawing_of=of)
        dwgs.append(pn)

    def edit(pn, text):
        path = os.path.join(src, pn + ".FCStd")
        with open(path, "wb") as handle:
            handle.write(f"{pn} {text} {rng.random()}\n".encode() * 20)
        v.checkout(pn)
        v.checkin(pn, path, comment=text)

    filed = parts + asms + dwgs
    for pn in filed:
        edit(pn, "first version")
    for pn in rng.sample(filed, len(filed) // 2):
        edit(pn, "second version")
    # BOMs: each assembly 4-8 parts and COTS, plus up to two earlier assemblies nothing uses yet;
    # the last ``tops`` take what is left, so the vault has that many top assemblies.
    unused = []
    for i, pn in enumerate(asms):
        lines = [(c, rng.randint(1, 4)) for c in rng.sample(parts + cots, rng.randint(4, 8))]
        last = i >= len(asms) - tops
        share = -(-len(unused) // (len(asms) - i)) if last else 2
        taken, unused = unused[:share], unused[share:]
        v.set_bom(pn, lines + [(c, 1) for c in taken])
        if not last:
            unused.append(pn)
    for pn in rng.sample(parts + dwgs, len(filed) // 4):
        v.submit(pn)
        v.approve(pn, note="first release")
    for pn in rng.sample([p for p in filed if v.state_of(p) == "WIP"], max(3, n_items // 33)):
        v.submit(pn)
    released = [p for p in parts if v.state_of(p) == "Released" and not v.drawings_of(p)]
    ecrs = []
    for k in range(max(2, n_items // 60)):
        if len(released) < 2:
            break
        items = rng.sample(released, 2)
        for p in items:
            released.remove(p)
        ecrs.append(v.raise_ecr(None, f"Problem {k + 1}: hole too close to the edge", items).number)
    approved = ecrs[: len(ecrs) * 3 // 4]
    for num in approved:
        v.decide_ecr(num, True, reason="agreed")
    for j, num in enumerate(approved[: len(approved) * 2 // 3]):
        eco = v.open_eco(None, [num])
        if j % 2 == 0:
            for pn in eco.items:
                edit(pn, f"change for {eco.number}")
                v.submit(pn)
            v.release_eco(eco.number, note="released together")
    wip = [p for p in filed if v.state_of(p) == "WIP" and not v.locks.get(p)]
    mine = rng.sample(wip, max(2, n_items // 20))
    work = Workspace(v)
    for pn in mine:
        work.checkout(pn, with_links=False)
    for pn in rng.sample([p for p in filed if p not in mine], min(len(filed) // 5, 200)):
        try:
            work.get_latest(pn)
        except Exception:  # noqa: BLE001 -- e.g. no file yet
            pass
    other = FolderVault(v.ref_path, actor=OTHER)
    for pn in rng.sample([p for p in wip if p not in mine], max(1, n_items // 100)):
        other.checkout(pn)
    v.refresh()
    return v


def more_work(v, src):
    """A few more operations at the real time: check-ins, a submit, a BOM, another Mac's lock."""
    os.makedirs(src, exist_ok=True)
    wip = sorted(pn for pn, item in v.items.items() if item.state == "WIP" and not v.locks.get(pn)
                 and item.item_type != "cots")
    for i, pn in enumerate(wip[:3]):
        path = os.path.join(src, pn + ".FCStd")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{pn} more {i}\n")
        v.checkout(pn)
        v.checkin(pn, path, comment="slot 2 mm wider")
    v.submit(wip[0])
    asm = next(pn for pn in wip if v.items[pn].item_type == "assembly")
    v.set_bom(asm, [(wip[1], 3)])
    FolderVault(v.ref_path, actor=OTHER).checkout(wip[4])


if __name__ == "__main__":
    vault = make_vault(sys.argv[1], int(sys.argv[2]), name=sys.argv[3] if len(sys.argv) > 3 else "Spike")
    print(f"{vault.ref_path}: {len(vault.items)} items, {len(vault.events)} events")
