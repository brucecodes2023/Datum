# SPDX-License-Identifier: LGPL-2.1-or-later
"""App init for the BruceWorx wire routing add-on (roadmap track WIR).

FreeCAD execs this file; the module directory is already on ``sys.path``.
On by default. Off switch: ``BaseApp/Preferences/BruceWorx/WireRouting``
(the "Wire routing" box on Preferences → BruceWorx), next start. Off stops
the live follow and removes the menu commands; wires already in a file
still load and recompute.
"""


def _bw_wire_init():
    try:
        from wire_routing import objects

        if objects.enabled():
            objects.install_follower()
    except Exception as exc:  # Part not built, or a stripped-down install
        import FreeCAD

        FreeCAD.Console.PrintLog(f"BruceWorx wire routing was not loaded: {exc}\n")


_bw_wire_init()
