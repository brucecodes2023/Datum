# SPDX-License-Identifier: LGPL-2.1-or-later
"""Status bar as quiet text readouts (roadmap §13.3, step B, marker 12).

The prompt stays on the left. On the right, as plain text: Report, units,
navigation style, view size, vault. Report, units and view size are drawn by
Gui/MainWindow.cpp ([BW]); this module adds the rest:

* the navigation indicator (Tux) reads "Navigation Revit", without its icon;
* the vault readout reads "Vault <name>" from the folder vault's ref file
  (``<name>.bwvault``, see addons/BruceWorx/bruceworx/pdm_folder.py). It is
  hidden until Preferences/BruceWorx/VaultPath names a vault folder or ref
  file. The check-out state of the open part is not shown yet: that needs
  the journal replayed, which is not cheap enough for the status bar.

Only while Preferences/BruceWorx/StatusReadouts is on (the BruceWorx pack
turns it on). Turning it off gives the Tux indicator back its icon and text.
"""

from __future__ import annotations

import json
import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
VIEW_PATH = "User parameter:BaseApp/Preferences/View"
REF_SUFFIX = ".bwvault"
VAULT_FORMAT = "bruceworx.vault"


def navigation_label(indicator_text: str, style: str = "") -> str:
    """Readout text for the navigation style: Tux's name, else the class name."""
    name = (indicator_text or "").strip()
    if not name:
        name = (style or "").replace("Gui::", "").replace("NavigationStyle", "").strip()
    return f"Navigation {name}" if name else "Navigation"


def vault_label(path: str):
    """(text, tooltip) for the vault readout, or None when no vault is set.

    Reads only the small ref file, so it is cheap enough to call on a timer.
    """
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return "Vault offline", f"Vault not found: {path}"
    try:
        ref = path
        if os.path.isdir(path):
            refs = sorted(n for n in os.listdir(path) if n.endswith(REF_SUFFIX))
            if not refs:
                return "Vault not set up", f"No {REF_SUFFIX} file in {path}"
            ref = os.path.join(path, refs[0])
        with open(ref, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return "Vault unreadable", path
    if not isinstance(data, dict) or data.get("format") != VAULT_FORMAT:
        return "Vault unreadable", f"{ref} is not a {VAULT_FORMAT} file"
    name = str(data.get("name") or "").strip()
    return (f"Vault {name}" if name else "Vault"), os.path.dirname(ref)


# ----- GUI --------------------------------------------------------------------

_installed = False
_observer = None
_groups = []  # the observers live on these wrappers; keep them
_filter = None
_vault = None
VAULT_SUFFIX = []  # callables returning text appended to the vault readout


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def _status_bar():
    import FreeCADGui

    mw = FreeCADGui.getMainWindow()
    return mw.statusBar() if mw else None


def _indicator():
    from PySide import QtWidgets

    bar = _status_bar()
    return bar.findChild(QtWidgets.QPushButton, "NavigationIndicator") if bar else None


def _tux_reset():
    """Let Tux write its own text and icon again."""
    try:
        import NavigationIndicatorGui

        NavigationIndicatorGui.setCurrent()
    except Exception:
        pass


def update(*_args) -> None:
    """Bring the navigation and vault readouts in line with the preferences."""
    import FreeCAD
    from PySide import QtGui, QtWidgets

    global _vault
    bar = _status_bar()
    if bar is None:
        return
    on = _prefs().GetBool("StatusReadouts", False)

    indicator = _indicator()
    if indicator is not None:
        _tux_reset()
        if on:
            style = FreeCAD.ParamGet(VIEW_PATH).GetString("NavigationStyle", "")
            indicator.setText(navigation_label(indicator.text(), style))
            indicator.setIcon(QtGui.QIcon())

    if _vault is None:
        _vault = QtWidgets.QLabel(bar)
        _vault.setObjectName("vaultReadout")
        bar.addPermanentWidget(_vault)  # last, after Tux moved its widgets
    label = vault_label(_prefs().GetString("VaultPath", "")) if on else None
    if label is None:
        _vault.hide()
    else:
        suffix = ""
        for extra in VAULT_SUFFIX:  # " · 2 checked out · 1 newer" (bruceworx_vaultclient)
            try:
                suffix += extra()
            except Exception:
                pass
        _vault.setText(label[0] + suffix)
        _vault.setToolTip(label[1])
        _vault.show()


def install() -> None:
    """Run once the other modules (Tux) have filled the status bar."""
    global _installed, _observer
    if _installed:
        return
    import FreeCAD
    from PySide import QtCore

    class _Observer:
        # Tux observes the same group; run after it has written its text.
        def onChange(self, _grp, name):
            if name in ("StatusReadouts", "VaultPath", "NavigationStyle"):
                QtCore.QTimer.singleShot(0, update)

    class _Filter(QtCore.QObject):
        # Tux writes its text again on a language change and from its menu.
        def eventFilter(self, obj, event):
            if event.type() in (QtCore.QEvent.LanguageChange, QtCore.QEvent.MouseButtonRelease):
                QtCore.QTimer.singleShot(0, update)
            return False

    _observer = _Observer()
    for path in (PREFS_PATH, VIEW_PATH):
        group = FreeCAD.ParamGet(path)
        group.Attach(_observer)
        _groups.append(group)

    def _first():
        global _filter
        indicator = _indicator()
        if indicator is not None:
            _filter = _Filter()
            indicator.installEventFilter(_filter)
            if indicator.menu() is not None:
                indicator.menu().aboutToHide.connect(lambda: QtCore.QTimer.singleShot(0, update))
        update()

    QtCore.QTimer.singleShot(0, _first)
    _installed = True
