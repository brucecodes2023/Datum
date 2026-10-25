# SPDX-License-Identifier: LGPL-2.1-or-later
"""MOD-15 mock: render the worksheet's formulas to SVG with matplotlib mathtext.

The same renderer the worksheet tab would use (Computer Modern, in-process, no TeX). Needs matplotlib:
run it with the pixi env's python (render.sh does). Writes <out>/math/<key>.svg.
"""

import io
import os
import sys

import matplotlib

matplotlib.use("Agg")
from matplotlib import mathtext  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402

matplotlib.rcParams["mathtext.fontset"] = "cm"

FORMULAS = {
    # gear train
    "i": r"$i = 1 + \frac{Z_r}{Z_s}$",
    "nout": r"$n_{out} = \frac{n_{in}}{i}$",
    # geometry
    "a": r"$a = \frac{m_n\,(Z_s + Z_p)}{2}$",
    "ar": r"$a_r = \frac{m_n\,(Z_r - Z_p)}{2}$",
    "coax": r"$a = a_r$",
    # tooth checks
    "asm": r"$\frac{Z_s + Z_r}{N}$",
    "nb": r"$2a\,\sin\frac{\pi}{N} > d_{a,p} = m_n\,(Z_p + 2)$",
    "uc": r"$z_{min} = \frac{2}{\sin^2\alpha}$",
    # torque
    "tout": r"$T_{out} = \eta\; i\; T_{in}$",
    "ft": r"$F_t = \frac{T_{in}}{N\,r_s},\quad r_s = \frac{m_n Z_s}{2}$",
    "sig": r"$\sigma_F = \frac{F_t}{b\,m_n\,Y}$",
    "sf": r"$S = \frac{\sigma_{allow}}{\sigma_F}$",
    # text section (LaTeX)
    "willis": r"$\frac{n_s - n_c}{n_r - n_c} = -\frac{Z_r}{Z_s}$",
    "willis2": r"$n_r = 0 \;\Rightarrow\; n_c = \frac{n_s}{1 + Z_r/Z_s} = \frac{n_s}{i}$",
    # inline bits
    "Tin": r"$T_{in}$", "i5": r"$i = 5$", "Zs": r"$Z_s$", "Zr": r"$Z_r$", "x030": r"$x_s \geq 0.30$",
    "ri": r"$r_s$",
    # stale figure
    "i_s": r"$i = 1 + \frac{Z_r}{Z_s}$",
}

SIZES = {"Tin": 13, "i5": 13, "Zs": 13, "Zr": 13, "x030": 13, "ri": 13}


def main(out):
    os.makedirs(os.path.join(out, "math"), exist_ok=True)
    for key, tex in FORMULAS.items():
        buf = io.BytesIO()
        mathtext.math_to_image(tex, buf, prop=FontProperties(size=SIZES.get(key, 15)), dpi=72, format="svg")
        with open(os.path.join(out, "math", key + ".svg"), "wb") as f:
            f.write(buf.getvalue())
    print(len(FORMULAS), "formulas ->", os.path.join(out, "math"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
