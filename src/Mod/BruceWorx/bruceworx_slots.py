# SPDX-License-Identifier: LGPL-2.1-or-later
"""Signal receivers for objects that are not QObjects (a PySide 6.8.3 bug, found 26 Oct 2026).

A plain Python object (a panel, a dialog helper, a controller) that connects two or more widget
signals to its own bound methods and is then garbage collected makes PySide drop EVERY connection
in the application whose receiver is a plain function or lambda: menu actions, timers, the studio
shading's observer timer, the bridge's server. (Reproduced: two bound-method connections and a
``del`` are enough; one connection, a QObject subclass, or a closure as the receiver are fine.)

``slot(self.method)`` returns a closure PySide can keep. The closure holds the bound method, so
the object lives as long as one of its senders does: the same lifetime a bound-method connection
gave it before (controllers with no other owner stay up through their timers), without the
receiver-destroyed path the bug is in. The signal's arguments are trimmed to what the method takes.
``TestBruceWorx`` checks that no plain class in this folder connects a bound method directly.
"""

from __future__ import annotations

_VARARGS = 0x04


def slot(method):
    """A receiver for ``method`` (a bound method of a plain Python object) safe to connect."""
    function = getattr(method, "__func__", None)
    if getattr(method, "__self__", None) is None or function is None:
        return method  # a plain function or a lambda: PySide keeps those itself
    code = function.__code__
    takes_all = bool(code.co_flags & _VARARGS)
    count = max(0, code.co_argcount - 1)  # without self

    def receiver(*args):
        return method(*(args if takes_all else args[:count]))

    receiver.__name__ = f"slot_{function.__name__}"
    receiver.__wrapped__ = method
    return receiver
