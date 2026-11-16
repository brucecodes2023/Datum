# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loaded when this folder is on FreeCAD's Mod path.

BruceWorx installs this add-on with the normal build as ``Mod/HeuretesHooks``
(see CMakeLists.txt), so ``import heuretes_freecad``, the part-number macro and
BruceWorx → Assign Part Number… are available by default.

Off switch: ``BaseApp/Preferences/BruceWorx/LoadHooks`` (the "Hooks add-on"
box on Preferences → BruceWorx). Off takes effect on the next start: the
folder is taken back off ``sys.path`` and InitGui.py adds nothing. When on,
it also starts the observer that keeps computed part properties current
(``bruceworx.partprops.install_observer``).
``--disable-addon BruceWorxHooks`` also works.

Headless use does not need any of this. Run hooks.py under FreeCADCmd; see
README.md.
"""

# FreeCAD execs this file inside its loader: names defined at the top of this
# file are not visible inside functions defined here, so everything lives in
# one function that imports what it uses. __file__ is not relied on; the
# loader has just put this folder on sys.path.


def _bw_hooks_init():
    import os
    import sys

    here = None
    for entry in sys.path:
        if os.path.isfile(os.path.join(entry, "bruceworx", "numbering.py")) and os.path.isfile(
            os.path.join(entry, "Init.py")
        ):
            here = entry
            break
    if here is None:
        return
    enabled = True
    try:
        import FreeCAD

        enabled = bool(
            FreeCAD.ParamGet("User parameter:BaseApp/Preferences/BruceWorx").GetBool(
                "LoadHooks", True
            )
        )
    except Exception:
        pass
    if enabled:
        if here not in sys.path:
            sys.path.insert(0, here)
        # Keep computed part properties (material, mass) current.
        try:
            from heuretes_freecad import partprops

            partprops.install_observer()
        except Exception as exc:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"BruceWorx part properties observer not started: {exc}\n")
    else:
        while here in sys.path:
            sys.path.remove(here)


_bw_hooks_init()
