# SPDX-License-Identifier: LGPL-2.1-or-later
"""HEU_* environment variables. BW_* is still read, then written, so older scripts keep working."""
import os


def getenv(name, default=None):
    """``HEU_<name>`` if it is set, else ``BW_<name>``, else ``default``."""
    heu = "HEU_" + name
    if heu in os.environ:
        return os.environ[heu]
    bw = "BW_" + name
    if bw in os.environ:
        return os.environ[bw]
    return default
