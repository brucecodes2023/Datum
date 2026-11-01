# SPDX-License-Identifier: LGPL-2.1-or-later
"""The dialogs of the part's Simulations tab (PDM-14): New simulation of this part, Import study file."""

from __future__ import annotations

import os


def _session():
    import bruceworx_vaultclient as vc

    return vc, vc.session()


def new_simulation_dialog(part: str) -> str:
    from PySide import QtWidgets
    import FreeCADGui

    vc, s = _session()
    if s is None:
        return ""
    text, ok = QtWidgets.QInputDialog.getText(FreeCADGui.getMainWindow(), "New simulation",
                                              f"What does the simulation of {part} study?\n(for example: Spin stress)")
    if not ok or not text.strip():
        return ""
    return _make(vc, s, lambda: __import__("bruceworx_sim_vault").new_simulation(s, part, text.strip()))


def import_study_dialog(part: str) -> str:
    from PySide import QtWidgets
    import FreeCADGui

    vc, s = _session()
    if s is None:
        return ""
    path, _flt = QtWidgets.QFileDialog.getOpenFileName(FreeCADGui.getMainWindow(), "Import study file", "",
                                                       "Study files (*.FCStd *.vlsim)")
    if not path:
        return ""
    default = os.path.splitext(os.path.basename(path))[0]
    text, ok = QtWidgets.QInputDialog.getText(FreeCADGui.getMainWindow(), "Import study",
                                              f"Description of this simulation of {part}:", text=default)
    if not ok or not text.strip():
        return ""
    return _make(vc, s, lambda: __import__("bruceworx_sim_vault").import_study(s, part, path, text.strip()))


def _make(vc, s, fn) -> str:
    import FreeCAD
    from PySide import QtWidgets
    import FreeCADGui

    try:
        key = fn()
    except Exception as exc:  # noqa: BLE001
        QtWidgets.QMessageBox.warning(FreeCADGui.getMainWindow(), "Simulation", f"Not made: {exc}")
        return ""
    vc.changed()
    FreeCAD.Console.PrintMessage(f"BruceWorx vault: made simulation {key}\n")
    try:
        import bruceworx_vlsim

        bruceworx_vlsim.open(s.work.path(key))
    except Exception as exc:  # noqa: BLE001
        FreeCAD.Console.PrintWarning(f"BruceWorx vault: {key} is made but did not open ({exc})\n")
    return key
