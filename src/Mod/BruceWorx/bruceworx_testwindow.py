# SPDX-License-Identifier: LGPL-2.1-or-later
"""Window placement for test runs started by the agents' launcher.

When the environment variable BW_WINDOW is "left" (or "right"), the main window
takes that half of the screen's available area once it is shown, so a test
FreeCAD does not cover the Terminal on the other half. FreeCAD restores its
saved window geometry after start-up, so the placement is applied again a
moment later. Without the variable nothing happens: a user's own FreeCAD keeps
its window as it was.

Test steps are narrated in a log the person watching can follow in the
terminal: the launcher sets BW_TEST_LOG (and BW_TEST, the test's name, or the
script's file name is used) and a test script calls ``say("Pad: set the length
to 20 mm, expect one undo step")`` before each step; each call appends one
timestamped line. Outside a test run ``say`` does nothing.
"""

from __future__ import annotations

import os

ENV = "BW_WINDOW"
TEST_ENV = "BW_TEST"
SIDES = ("left", "right")


def half(area, side):
    """(x, y, w, h) of the ``side`` half of ``area`` (x, y, w, h)."""
    x, y, w, h = area
    left_w = w // 2
    if side == "right":
        return x + left_w, y, w - left_w, h
    return x, y, left_w, h


def test_name(argv, env=""):
    """The caption's title: BW_TEST, else the first script's file name ("check_tab.py" -> "check tab")."""
    if env.strip():
        return env.strip()
    for arg in argv[1:]:
        if arg.endswith((".py", ".FCMacro")):
            base = os.path.splitext(os.path.basename(arg))[0]
            return base.replace("_", " ").replace("-", " ")
    return "test run"


LOG_ENV = "BW_TEST_LOG"
_step = 0


def log_line(when, test, step, text):
    """One narration line: "10:52:03  check tab · step 3  Select the Boss pad…"."""
    return f"{when}  {test} · step {step}  {text}"


def say(text, title=None):
    """Narrate the step the test is about to take, in the shared test log (test runs only)."""
    global _step
    import sys
    import time

    path = os.environ.get(LOG_ENV, "").strip()
    if not path:
        return
    _step += 1
    head = title or test_name(sys.argv, os.environ.get(TEST_ENV, ""))
    try:
        with open(path, "a", encoding="utf-8") as log:
            log.write(log_line(time.strftime("%H:%M:%S"), head, _step, text) + "\n")
    except OSError:
        pass


def place():
    import FreeCADGui
    from PySide import QtCore

    side = os.environ.get(ENV, "").strip().lower()
    mw = FreeCADGui.getMainWindow()
    if side not in SIDES or mw is None:
        return
    screen = mw.screen() if hasattr(mw, "screen") else None
    if screen is None:
        return
    g = screen.availableGeometry()
    x, y, w, h = half((g.x(), g.y(), g.width(), g.height()), side)
    if mw.isMaximized() or mw.isFullScreen():
        mw.showNormal()
    mw.setGeometry(QtCore.QRect(x, y, w, h))


def install():
    from PySide import QtCore

    if os.environ.get(ENV, "").strip().lower() not in SIDES:
        return
    for delay in (0, 1500, 4000):  # after show, and after FreeCAD restores its saved geometry
        QtCore.QTimer.singleShot(delay, place)
