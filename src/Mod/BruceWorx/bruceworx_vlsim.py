# SPDX-License-Identifier: LGPL-2.1-or-later
"""The .vlsim simulation file (PDM-14, 1 Nov 2026).

A simulation file is a FreeCAD document zip saved as ``<name>.vlsim``: the same format inside as an
``.FCStd``, so FreeCAD reads and writes it with its own code. Only the door needed a handle:

* ``FreeCAD.openDocument`` does not look at the extension, so a script opens a .vlsim as any document.
* File > Open, Open Recent, the command line and a Finder double-click ask the registered import type for
  the module that opens the extension: ``register()`` adds ``*.vlsim`` here and ``open()`` opens the file as
  a normal document (an already open one is brought forward, as for an .FCStd).
* Save keeps the extension because ``Document::save`` writes to the name the document has; Save As keeps it
  because ``Document::saveAs`` accepts ``.vlsim`` next to ``.FCStd`` (src/App/Document.cpp, checkFileName).
* Finder opens it with Vector Labs: make-vectorlabs.sh lists the extension in CFBundleDocumentTypes and
  Gui/GuiApplication.cpp hands the QFileOpenEvent to ``open()``.

The study inside is untouched by any of this.
"""

from __future__ import annotations

import os

SUFFIX = ".vlsim"
FILTER = "Vector Labs simulation (*.vlsim)"


def is_vlsim(path) -> bool:
    return str(path or "").lower().endswith(SUFFIX)


def register() -> None:
    """Tell FreeCAD that this module opens ``*.vlsim`` (File > Open lists it, Finder double-clicks use it)."""
    import FreeCAD

    if "bruceworx_vlsim" in (FreeCAD.getImportType("vlsim") or []):  # once, however often it is called
        return
    FreeCAD.addImportType(FILTER, "bruceworx_vlsim")


def open(filename, *args):  # noqa: A001 -- the name FreeCAD's open handlers have
    """Open ``filename`` as a normal document. Returns the document."""
    import FreeCAD

    path = os.path.realpath(filename)
    for doc in FreeCAD.listDocuments().values():
        if doc.FileName and os.path.realpath(doc.FileName) == path:
            try:
                FreeCAD.setActiveDocument(doc.Name)
                if FreeCAD.GuiUp:
                    import FreeCADGui

                    FreeCADGui.setActiveDocument(doc.Name)
            except Exception:  # noqa: BLE001 -- bringing it forward is a courtesy
                pass
            return doc
    return FreeCAD.openDocument(filename)


def insert(filename, docname):
    """Importing a study into another document makes no sense: open it."""
    raise ValueError("a .vlsim is a document of its own: open it with File > Open")
