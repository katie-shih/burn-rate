"""Check the palette in burnrate/theme.py is actually safe to use.

    uv run python scripts/check_palette.py

Run this after changing ANY colour. theme.py claims the palette is validated
rather than chosen by eye; this script is what makes that claim checkable.

WHAT IT CHECKS, and why each one has teeth
------------------------------------------

1. SERIES COLOURS ARE TELLABLE APART, including by colourblind readers.
   Two series that look alike turn a chart into a guessing game. The contract
   in CLAUDE.md is about ADJACENT pairs -- the ones that touch in a stacked
   bar and sit next to each other in a legend -- so those are the ones that
   must pass. Every other pair is measured too and reported, because a pair
   that is far apart in the legend can still end up side by side once a
   filter is applied.

2. MARKS SHOW UP AGAINST THE BACKGROUND.
   This is a separate question from (1) and gets missed constantly. A dark
   red bar on a dark red ground can be perfectly distinguishable from every
   other series and still be invisible, because distinguishability is about
   hue and visibility is about lightness. WCAG asks for 3:1 for anything
   graphical.

3. TEXT IS READABLE.
   4.5:1 for body text, 3:1 for large text, against whatever it sits on.
   Includes the label ink used INSIDE coloured segments, which is the easiest
   place for contrast to quietly fail.

Exit code is 0 when everything passes and 1 when anything fails, so it can be
wired into a check later.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from burnrate import theme                                      # noqa: E402
from burnrate.colour import contrast_ratio, delta_e, worst_delta_e  # noqa: E402

VISIONS = ("normal", "protanopia", "deuteranopia", "tritanopia")

# How far apart two colours must look. CIEDE2000 units: ~2 is the threshold of
# visibility, ~10 reads as a clear difference. 15 leaves room for small marks,
# thin lines and cheap screens, where everything is harder to tell apart than
# in a swatch.
MIN_DELTA_ADJACENT = 15.0

# A softer bar for pairs that are not adjacent. They should still be separable,
# but a near-miss there is a note rather than a failure.
MIN_DELTA_ANY = 10.0

# WCAG: graphical objects need 3:1, body text 4.5:1.
MIN_CONTRAST_MARK = 3.0
MIN_CONTRAST_TEXT = 4.5
MIN_CONTRAST_LARGE_TEXT = 3.0


def heading(text: str) -> None:
    print("\n" + "=" * 74)
    print(text)
    print("=" * 74)


def check_series_separation() -> int:
    """Every adjacent pair, under every kind of vision."""
    heading("1. SERIES COLOURS — can they be told apart?")
    print(f"Threshold: CIEDE2000 >= {MIN_DELTA_ADJACENT:g} for adjacent pairs, "
          "under all four vision types.\n")

    failures = 0
    print(f"  {'pair':<22}{'normal':>9}{'protan':>9}{'deutan':>9}{'tritan':>9}   worst")
    for index in range(len(theme.SERIES) - 1):
        first, second = theme.SERIES[index], theme.SERIES[index + 1]
        scores = [delta_e(first, second, vision) for vision in VISIONS]
        worst = min(scores)
        verdict = "ok" if worst >= MIN_DELTA_ADJACENT else "FAIL"
        if verdict == "FAIL":
            failures += 1
        label = f"{index + 1}-{index + 2}"
        print(f"  {label:<22}" + "".join(f"{s:>9.1f}" for s in scores)
              + f"   {worst:.1f} {verdict}")

    # Non-adjacent pairs: reported, not gated.
    worst_any = None
    for a, b in itertools.combinations(range(len(theme.SERIES)), 2):
        if b == a + 1:
            continue
        score, vision = worst_delta_e(theme.SERIES[a], theme.SERIES[b])
        if worst_any is None or score < worst_any[0]:
            worst_any = (score, f"{a + 1}-{b + 1}", vision)

    if worst_any:
        score, pair, vision = worst_any
        note = "ok" if score >= MIN_DELTA_ANY else "worth a look"
        print(f"\n  Closest non-adjacent pair: {pair} at {score:.1f} "
              f"under {vision} — {note}")

    return failures


def check_mark_visibility() -> int:
    """Series colours against the surfaces they are drawn on."""
    heading("2. MARKS — do they show up against the background?")
    print(f"Threshold: WCAG contrast >= {MIN_CONTRAST_MARK:g}:1 against the chart surface.\n")

    failures = 0
    print(f"  {'series':<10}{'hex':<12}{'vs surface':>12}{'vs page':>10}")
    for index, colour in enumerate(theme.SERIES, start=1):
        against_surface = contrast_ratio(colour, theme.SURFACE)
        against_page = contrast_ratio(colour, theme.PAGE)
        verdict = "ok" if against_surface >= MIN_CONTRAST_MARK else "FAIL"
        if verdict == "FAIL":
            failures += 1
        print(f"  {index:<10}{colour:<12}{against_surface:>11.2f}:1"
              f"{against_page:>9.2f}:1   {verdict}")

    return failures


def check_text() -> int:
    """Every ink/background pairing the dashboard actually uses."""
    heading("3. TEXT — is it readable?")

    pairs = [
        ("body text on surface", theme.INK, theme.SURFACE, MIN_CONTRAST_TEXT),
        ("body text on page", theme.INK, theme.PAGE, MIN_CONTRAST_TEXT),
        ("secondary on surface", theme.INK_SECONDARY, theme.SURFACE, MIN_CONTRAST_TEXT),
        ("secondary on page", theme.INK_SECONDARY, theme.PAGE, MIN_CONTRAST_TEXT),
        # Axis labels are small but sparse; WCAG still asks 4.5 for them.
        ("axis labels on surface", theme.INK_MUTED, theme.SURFACE, MIN_CONTRAST_TEXT),
        ("hero number on page", theme.INK, theme.PAGE, MIN_CONTRAST_LARGE_TEXT),
        # The stat tiles run the palette backwards: dark type on a beige
        # ground. Contrast is symmetric, but the MUTED pairing is not -- a
        # muted ink chosen against the dark surface can be too light here.
        ("tile value on beige", theme.TILE_INK, theme.TILE_SURFACE, MIN_CONTRAST_TEXT),
        ("tile label on beige", theme.TILE_INK_MUTED, theme.TILE_SURFACE,
         MIN_CONTRAST_TEXT),
        ("beige tile against page", theme.TILE_SURFACE, theme.PAGE,
         MIN_CONTRAST_MARK),
    ]

    failures = 0
    print(f"  {'what':<26}{'ink':<11}{'on':<11}{'ratio':>8}  need")
    for label, ink, background, needed in pairs:
        ratio = contrast_ratio(ink, background)
        verdict = "ok" if ratio >= needed else "FAIL"
        if verdict == "FAIL":
            failures += 1
        print(f"  {label:<26}{ink:<11}{background:<11}{ratio:>7.2f}:1  "
              f"{needed:g}  {verdict}")

    print("\n  Labels sitting INSIDE a coloured segment:")
    print(f"  {'segment':<26}{'ink':<11}{'on fill':<11}{'ratio':>8}  need")
    for name, fill in theme.TOKEN_COLORS.items():
        ink = theme.TOKEN_LABEL_INK[name]
        ratio = contrast_ratio(ink, fill)
        verdict = "ok" if ratio >= MIN_CONTRAST_TEXT else "FAIL"
        if verdict == "FAIL":
            failures += 1
        print(f"  {name:<26}{ink:<11}{fill:<11}{ratio:>7.2f}:1  "
              f"{MIN_CONTRAST_TEXT:g}  {verdict}")

    return failures


def check_chrome() -> int:
    """Gridlines and rules: visible enough to read, quiet enough to recede."""
    heading("4. CHROME — gridlines present but not shouting")

    failures = 0
    for label, colour in (("gridlines", theme.GRID), ("axis rules", theme.BASELINE)):
        ratio = contrast_ratio(colour, theme.SURFACE)
        # Deliberately two-sided. Chrome that disappears is useless; chrome
        # that competes with the data is worse.
        if ratio < 1.12:
            verdict, failures = "FAIL — invisible", failures + 1
        elif ratio > 4.5:
            verdict, failures = "FAIL — too loud", failures + 1
        else:
            verdict = "ok"
        print(f"  {label:<14}{colour:<11}{ratio:>6.2f}:1 against surface   {verdict}")

    return failures


def main() -> int:
    print("=" * 74)
    print("PALETTE CHECK — burnrate/theme.py")
    print("=" * 74)
    print(f"surface {theme.SURFACE}   page {theme.PAGE}   ink {theme.INK}")

    failures = (
        check_series_separation()
        + check_mark_visibility()
        + check_text()
        + check_chrome()
    )

    print("\n" + "=" * 74)
    if failures:
        print(f"RESULT: {failures} check(s) FAILED. Fix the palette, do not ship it.")
        print("=" * 74)
        return 1
    print("RESULT: the palette passes. Safe to ship.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
