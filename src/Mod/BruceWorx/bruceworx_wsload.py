# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loads the commands the workspace groups borrow from other workbenches.

The BruceWorx custom toolbars (bruceworx_workspaces) name this module next to
each command. When FreeCAD meets a command that is not registered yet, it
imports the named module and looks again; importing this one imports every
module listed under "load" in workspaces.json (PartGui, SurfaceGui, the
Assembly command modules, ...), and calls "module:function" entries (Part's
boolean tools register through BOPTools.addCommands). A missing module is
skipped.
"""

import importlib

import bruceworx_workspaces

for _entry in bruceworx_workspaces.definition().get("load", []):
    # "module" imports it; "module:function" also calls a function of it.
    _name, _, _function = _entry.partition(":")
    try:
        _module = importlib.import_module(_name)
        if _function:
            getattr(_module, _function)()
    except Exception:
        pass
