# SPDX-License-Identifier: LGPL-2.1-or-later
"""Old import name for heuretes_freecad. Kept so existing scripts still run."""
import sys

import heuretes_freecad as _pkg

sys.modules[__name__] = _pkg
