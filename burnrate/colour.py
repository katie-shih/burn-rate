"""Colour maths for checking a palette, with no dependencies beyond numpy.

This exists so "the palette is validated, not chosen by eye" is a statement
someone can run, rather than a claim in a comment. scripts/check_palette.py is
the thing you run; this file is the arithmetic behind it.

Three questions get answered here:

1. **Can two colours be told apart?** Measured as CIEDE2000, the standard
   perceptual distance between two colours. Roughly: under 2 is invisible to
   most people, around 10 is a clear difference, over 20 is unmistakable.
   Plain RGB distance is useless for this -- two colours can be far apart in
   RGB and look identical, or close in RGB and look nothing alike.

2. **Can they still be told apart by someone who is colourblind?** We simulate
   the three common kinds and ask question 1 again of the result.

3. **Does a colour show up against the background?** Measured as WCAG contrast
   ratio, which is about lightness rather than hue. A pure-red mark on a
   dark-red ground can be perfectly distinguishable from the other series and
   still be invisible, because it matches the background's lightness.
"""

from typing import Tuple

import numpy as np

# Dichromacy simulation matrices from Machado, Oliveira & Fernandes (2009),
# at full severity. They act on LINEAR rgb, which is why the gamma has to come
# off before they are applied and go back on afterwards.
#
# Protanopia and deuteranopia are the red/green kinds and are by far the most
# common (together roughly 8% of men). Tritanopia is the blue/yellow kind and
# is rare, but cheap to check.
DICHROMAT_MATRICES = {
    "protanopia": np.array([
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ]),
    "deuteranopia": np.array([
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ]),
    "tritanopia": np.array([
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ]),
}

# D65, the standard "daylight" reference white that sRGB is defined against.
WHITE_D65 = np.array([0.95047, 1.00000, 1.08883])

SRGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])


# ---- conversions -------------------------------------------------------


def hex_to_rgb(value: str) -> np.ndarray:
    """'#5d2627' -> array([0.365, 0.149, 0.153]), still gamma-encoded."""
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        raise ValueError(f"Not a hex colour: {value!r}")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0


def rgb_to_hex(rgb: np.ndarray) -> str:
    clipped = np.clip(rgb, 0.0, 1.0)
    return "#%02x%02x%02x" % tuple(int(round(channel * 255)) for channel in clipped)


def to_linear(rgb: np.ndarray) -> np.ndarray:
    """Undo the sRGB gamma curve.

    Screen values are not proportional to light. Every physical calculation --
    mixing, simulating colourblindness, measuring contrast -- has to happen on
    linear values or the answer is wrong.
    """
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def from_linear(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(
        linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055
    )


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB -> CIELAB, the space where distance means perceived difference."""
    xyz = SRGB_TO_XYZ @ to_linear(rgb) / WHITE_D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([
        116 * f[1] - 16,
        500 * (f[0] - f[1]),
        200 * (f[1] - f[2]),
    ])


def simulate(rgb: np.ndarray, kind: str) -> np.ndarray:
    """What this colour looks like to someone with that kind of colourblindness."""
    if kind == "normal":
        return rgb
    matrix = DICHROMAT_MATRICES[kind]
    return from_linear(matrix @ to_linear(rgb))


# ---- the measurements --------------------------------------------------


def relative_luminance(rgb: np.ndarray) -> float:
    """How much light the colour emits, 0 (black) to 1 (white)."""
    return float(np.dot([0.2126, 0.7152, 0.0722], to_linear(rgb)))


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast between two colours, from 1:1 (identical) to 21:1.

    The thresholds that matter:
      4.5  body text must clear this
      3.0  large text, and any graphical object like a bar or a line
    """
    first = relative_luminance(hex_to_rgb(a))
    second = relative_luminance(hex_to_rgb(b))
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def delta_e(a: str, b: str, vision: str = "normal") -> float:
    """CIEDE2000 distance between two colours, optionally through a simulation.

    The formula is long and unintuitive, and it is quoted rather than derived:
    it is the CIE's 2000 standard, built by fitting to what people actually
    report seeing. The shape of it -- weighting lightness, chroma and hue
    differently, with a correction term for blues -- is doing the work that
    makes plain Lab distance insufficient.
    """
    lab1 = to_lab(simulate(hex_to_rgb(a), vision))
    lab2 = to_lab(simulate(hex_to_rgb(b), vision))

    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_mean = (c1 + c2) / 2

    g = 0.5 * (1 - np.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7))) if c_mean > 0 else 0.0
    a1p, a2p = (1 + g) * a1, (1 + g) * a2

    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    dlp = l2 - l1
    dcp = c2p - c1p

    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dhp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp) / 2)

    lp_mean = (l1 + l2) / 2
    cp_mean = (c1p + c2p) / 2

    if c1p * c2p == 0:
        hp_mean = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_mean = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_mean = (h1p + h2p + 360) / 2
    else:
        hp_mean = (h1p + h2p - 360) / 2

    t = (
        1
        - 0.17 * np.cos(np.radians(hp_mean - 30))
        + 0.24 * np.cos(np.radians(2 * hp_mean))
        + 0.32 * np.cos(np.radians(3 * hp_mean + 6))
        - 0.20 * np.cos(np.radians(4 * hp_mean - 63))
    )

    d_theta = 30 * np.exp(-(((hp_mean - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cp_mean ** 7 / (cp_mean ** 7 + 25 ** 7)) if cp_mean > 0 else 0.0
    sl = 1 + (0.015 * (lp_mean - 50) ** 2) / np.sqrt(20 + (lp_mean - 50) ** 2)
    sc = 1 + 0.045 * cp_mean
    sh = 1 + 0.015 * cp_mean * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc

    return float(np.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dhp / sh) ** 2
        + rt * (dcp / sc) * (dhp / sh)
    ))


def worst_delta_e(a: str, b: str) -> Tuple[float, str]:
    """The hardest case: how close these two look under the worst vision type."""
    scores = [
        (delta_e(a, b, vision), vision)
        for vision in ("normal", "protanopia", "deuteranopia", "tritanopia")
    ]
    return min(scores)
