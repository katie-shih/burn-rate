"""Colour and chart styling, in one place.

The palette is a validated set, not a personal preference. It was checked with a
colourblind-safety validator: every adjacent pair of series colours stays
distinguishable under protanopia, deuteranopia and tritanopia.

Three rules worth knowing before changing anything here:

1. **Colour follows the thing, not its position.** "Cache read" is always the
   same pink, whether it's first in the chart or filtered out entirely. If
   colours were assigned by rank, filtering would repaint the survivors and
   quietly mislead anyone who'd learned the chart.
2. **One series, one colour.** Shading bars darker-where-bigger just restates
   the bar length in a second channel and wastes it.
3. **Text never wears the series colour.** Labels use ink; the coloured mark
   next to them carries the identity. Several of these hues are too light to
   read as text.
"""

import altair as alt

# -- surfaces and ink ----------------------------------------------------

SURFACE = "#fcfcfb"        # the chart's own background
PAGE = "#f9f9f7"           # the plane behind the cards
INK = "#0b0b0b"            # primary text
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"      # axis labels
GRID = "#e1e0d9"           # hairline gridlines
BASELINE = "#c3c2b7"       # axis rules
BORDER = "rgba(11,11,11,0.10)"

GOOD = "#0ca30c"
WARNING = "#fab219"
CRITICAL = "#d03b3b"

# -- categorical series, in fixed slot order -----------------------------

SERIES = [
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
]

ACCENT = SERIES[0]

# Token types get fixed colours so filtering never repaints them.
# Order matches burnrate.analysis.TOKEN_LABELS.
TOKEN_COLORS = {
    "Input": SERIES[0],
    "Output": SERIES[1],
    "Cache write (5 min)": SERIES[2],
    "Cache write (1 hour)": SERIES[3],
    "Cache read": SERIES[4],
}

# Ink for a label sitting *inside* a coloured segment, chosen per fill so it
# always clears contrast. Light fills take dark ink, dark fills take white.
TOKEN_LABEL_INK = {
    "Input": "#ffffff",
    "Output": "#ffffff",
    "Cache write (5 min)": INK,
    "Cache write (1 hour)": INK,
    "Cache read": INK,
}

TOKEN_ORDER = list(TOKEN_COLORS)

# -- chart specs ---------------------------------------------------------

BAR_SIZE = 22       # cap bar thickness; the leftover band is deliberate air
CORNER = 4          # rounded data-end, square at the baseline
SURFACE_GAP = 2     # white gap separating touching marks


def token_scale() -> alt.Scale:
    """Fixed colour mapping for token types."""
    return alt.Scale(domain=TOKEN_ORDER, range=[TOKEN_COLORS[k] for k in TOKEN_ORDER])


def style(chart: alt.Chart) -> alt.Chart:
    """Recessive chrome: hairline solid grid, muted axis text, no outer box."""
    return (
        chart.configure_view(stroke=None, fill=SURFACE)
        .configure_axis(
            labelColor=INK_MUTED,
            titleColor=INK_SECONDARY,
            labelFontSize=11,
            titleFontSize=11,
            titleFontWeight="normal",
            grid=True,
            gridColor=GRID,
            gridWidth=1,
            gridDash=[],          # solid, never dashed
            domainColor=BASELINE,
            tickColor=BASELINE,
            labelFont="system-ui, -apple-system, sans-serif",
            titleFont="system-ui, -apple-system, sans-serif",
        )
        .configure_legend(
            labelColor=INK_SECONDARY,
            titleColor=INK_SECONDARY,
            labelFontSize=11,
            titleFontSize=11,
            symbolType="square",
            symbolSize=90,
            labelFont="system-ui, -apple-system, sans-serif",
            titleFont="system-ui, -apple-system, sans-serif",
        )
        .configure_text(font="system-ui, -apple-system, sans-serif")
    )
