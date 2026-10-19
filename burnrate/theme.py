"""Colour, type and chart styling, in one place.

The look is an oxblood ground with cream type and wide-tracked geometric sans,
taken from a fashion-conference reference. It is a DARK theme, which CLAUDE.md
deferred on purpose:

    "Real dark mode means re-deriving every hue against the dark surface and
    re-running the check — worth doing deliberately, not as a side effect."

This is that deliberate piece of work. Every hue below was derived against the
oxblood surface by searching LCh space for the set with the largest minimum
perceptual distance, then checked. It was not picked by eye.

RUN THE CHECK AFTER CHANGING ANY COLOUR HERE:

    uv run python scripts/check_palette.py

That script is not decoration. Run against the previous light palette it found
eight real failures, including two series that were nearly identical to a
colourblind reader and three that were too faint to see against white.

Three rules worth knowing before changing anything:

1. **Colour follows the thing, not its position.** "Cache read" is always the
   same blush, whether it's first in the chart or filtered out entirely. If
   colours were assigned by rank, filtering would repaint the survivors and
   quietly mislead anyone who'd learned the chart.
2. **One series, one colour.** Shading bars darker-where-bigger just restates
   the bar length in a second channel and wastes it.
3. **Text never wears the series colour.** Labels use ink; the coloured mark
   next to them carries the identity.
"""

import base64
import os

import altair as alt

# -- surfaces and ink ----------------------------------------------------
#
# The oxblood is sampled from the reference image, where it sits between
# #5b2424 and #5f2929 across the field. The chart surface is one step lighter
# so a card lifts off the page without needing a border.

PAGE = "#5d2627"           # the plane behind everything
SURFACE = "#672b2c"        # the chart's own background
INK = "#efeae0"            # primary text, a warm cream
INK_SECONDARY = "#d4d0c6"
INK_MUTED = "#bcaea5"      # axis labels — still clears 4.5:1, which the
                           # previous light theme's axis labels did not
GRID = "#73393a"           # hairline gridlines, barely there
BASELINE = "#814748"       # axis rules
BORDER = "rgba(239,234,224,0.14)"

# Status colours, re-stepped for a dark ground: on oxblood these have to be
# light to register at all, where their light-theme counterparts were dark.
GOOD = "#9ed6a6"
WARNING = "#f1cd7c"
CRITICAL = "#f0918f"

# -- categorical series, in fixed slot order -----------------------------
#
# Five, not eight. The dashboard uses exactly five categorical colours (the
# token types) plus one accent, and eight mutually-distinct hues on this ground
# could only reach a minimum separation of 11.6 — below the bar. Five reach
# 15.9. Shipping three colours nobody uses, at the cost of making the five that
# are used harder to tell apart, would be a bad trade.
#
# If a sixth categorical series is ever needed, re-run the derivation rather
# than adding a hue by eye. The search is the thing that keeps this honest.

SERIES = [
    "#ece8d7",  # 1 bone
    "#f1cd7c",  # 2 gold
    "#de8891",  # 3 blush
    "#8fdee8",  # 4 aqua
    "#8d9ee0",  # 5 lilac
]

# The single-series charts (daily trend, by model, by project) use this.
#
# It is the same bone that means "Input" in the token stack. They never share
# a chart and the token stack is always legended, so nothing is ambiguous in
# practice — but it is a genuine overlap rather than a free choice, and the
# alternative was a sixth hue that the search kept placing almost on top of the
# bone anyway. Worth knowing before adding a chart that shows both at once.
ACCENT = SERIES[0]

# Token types get fixed colours so filtering never repaints them.
# Order matches burnrate.analysis.TOKEN_LABELS.
#
# The assignment is not arbitrary: it is ordered so that the pairs which TOUCH
# in the stacked bar are the ones furthest apart perceptually. Adjacent pairs
# here clear 20.7, well above the 15 the check demands.
TOKEN_COLORS = {
    "Input": SERIES[0],                 # bone
    "Output": SERIES[4],                # lilac
    "Cache write (5 min)": SERIES[1],   # gold
    "Cache write (1 hour)": SERIES[3],  # aqua
    "Cache read": SERIES[2],            # blush
}

# Ink for a label sitting *inside* a coloured segment. On a dark theme every
# series colour is light, so all five take dark ink — the reverse of the light
# theme, where it varied per fill.
INK_ON_FILL = "#2b1314"

TOKEN_LABEL_INK = {name: INK_ON_FILL for name in TOKEN_COLORS}

TOKEN_ORDER = list(TOKEN_COLORS)

# -- type ----------------------------------------------------------------
#
# Jost, a geometric sans close to the reference, under the SIL Open Font
# License. It is VENDORED into this repo rather than loaded from Google Fonts:
# an @import would call Google on every page load, which would break the
# promise in CLAUDE.md that nothing leaves this machine. It also means the
# dashboard looks the same offline and on a classmate's laptop.
#
# One variable-font file covers every weight we use, at 26KB.

FONT_FAMILY = "Jost"
FONT_STACK = f"'{FONT_FAMILY}', 'Futura', 'Avenir Next', system-ui, sans-serif"
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fonts", "Jost-variable-latin.woff2")

# The reference's defining typographic move: light weight, wide tracking,
# sentence case. Tracking is in em so it scales with the text.
TRACKING_BODY = "0.04em"
TRACKING_HEADING = "0.14em"
TRACKING_LABEL = "0.18em"


def font_data_uri() -> str:
    """The vendored font as a data: URI, or '' if the file is missing.

    Embedding avoids needing Streamlit's static file serving turned on, and
    keeps the font working when the dashboard is opened from anywhere.
    """
    try:
        with open(FONT_PATH, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return ""
    return f"data:font/woff2;base64,{encoded}"


def page_css() -> str:
    """The whole page's styling, built from the constants above.

    Lives here rather than in app.py because CLAUDE.md says colours live in
    theme.py and never inline in the layout. app.py injects the result.
    """
    uri = font_data_uri()
    face = (
        f"""
      @font-face {{
        font-family: '{FONT_FAMILY}';
        src: url({uri}) format('woff2');
        font-weight: 100 900;
        font-style: normal;
        font-display: swap;
      }}
        """
        if uri
        else "/* vendored font missing — falling back to the system stack */"
    )

    return f"""
    <style>
      {face}

      html, body, [class*="st-"], button, input, select, textarea {{
        font-family: {FONT_STACK};
        letter-spacing: {TRACKING_BODY};
      }}

      /* Streamlit's icons are a LIGATURE font: the element's text is literally
         "keyboard_arrow_right" and the font turns it into a glyph. The rule
         above was catching them, so with Jost applied the ligature vanished
         and the icon's name rendered as overlapping words. Icons have to keep
         their own family, and no tracking — letter-spacing also breaks
         ligatures. */
      [data-testid="stIconMaterial"] {{
        font-family: 'Material Symbols Rounded', 'Material Icons' !important;
        letter-spacing: normal !important;
      }}

      .stApp {{ background: {PAGE}; color: {INK}; }}

      /* Headings carry the reference's wide tracking and light weight. */
      h1, h2, h3, h4 {{
        font-family: {FONT_STACK};
        font-weight: 300;
        letter-spacing: {TRACKING_HEADING};
        color: {INK};
      }}
      h1 {{ text-transform: uppercase; }}

      p, li, label, span, div {{ color: {INK}; }}

      /* Sidebar sits on the page colour, separated by a hairline not a slab. */
      [data-testid="stSidebar"] {{
        background: {PAGE};
        border-right: 1px solid {BORDER};
      }}

      /* Cards and tiles: the one-step-lighter surface, no heavy chrome. */
      [data-testid="stMetric"], [data-testid="stExpander"],
      [data-testid="stAlertContainer"] {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 6px;
      }}
      [data-testid="stMetricLabel"] {{
        text-transform: uppercase;
        letter-spacing: {TRACKING_LABEL};
        font-size: 0.7rem;
        color: {INK_SECONDARY};
      }}

      /* Columns of numbers line up; the hero number keeps proportional figures. */
      [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
      [data-testid="stMetricValue"] {{
        font-size: 1.6rem;
        font-weight: 300;
        letter-spacing: {TRACKING_BODY};
        color: {INK};
      }}

      hr, [data-testid="stDivider"] {{ border-color: {BORDER}; }}

      /* Filter pills.
         Streamlit fills these with primaryColor and sets the text to white,
         which on a cream primary makes them unreadable cream-on-cream. Both
         have to be overridden, and the inner span is what actually carries
         the fill — styling only the outer tag leaves the pill blank.
         stMultiSelectTagsContainer is the stable hook; the class names
         around it are generated and change between Streamlit versions. */
      [data-testid="stMultiSelectTagsContainer"] span {{
        background: {SURFACE} !important;
        color: {INK} !important;
        border: 1px solid {BORDER};
        letter-spacing: {TRACKING_BODY};
      }}
      [data-testid="stMultiSelectTagsContainer"] svg {{
        fill: {INK_SECONDARY} !important;
      }}
    </style>
    """


# -- chart specs ---------------------------------------------------------

BAR_SIZE = 22       # cap bar thickness; the leftover band is deliberate air
CORNER = 4          # rounded data-end, square at the baseline
SURFACE_GAP = 2     # gap separating touching marks, in the surface colour


def token_scale() -> alt.Scale:
    """Fixed colour mapping for token types."""
    return alt.Scale(domain=TOKEN_ORDER, range=[TOKEN_COLORS[k] for k in TOKEN_ORDER])


def style(chart: alt.Chart) -> alt.Chart:
    """Recessive chrome: hairline solid grid, muted axis text, no outer box.

    The padding is not cosmetic. Vega measures how much room an axis label
    needs using its own text metrics, and it does that before the vendored
    font has finished loading -- so it measures the fallback, then Jost
    renders wider and the first character of a long label gets cut off
    ("laude-opus-5"). The left padding buys back that difference, and the
    labelLimit caps a runaway name so one long label cannot squeeze the plot
    until the money axis collides with itself.
    """
    return (
        chart.properties(padding={"left": 18, "top": 6, "right": 8, "bottom": 6})
        .configure_view(stroke=None, fill=SURFACE)
        .configure_axis(
            labelColor=INK_MUTED,
            titleColor=INK_SECONDARY,
            labelFontSize=11,
            labelLimit=150,
            titleFontSize=11,
            titleFontWeight="normal",
            grid=True,
            gridColor=GRID,
            gridWidth=1,
            gridDash=[],          # solid, never dashed
            domainColor=BASELINE,
            tickColor=BASELINE,
            labelFont=FONT_STACK,
            titleFont=FONT_STACK,
        )
        .configure_legend(
            labelColor=INK_SECONDARY,
            titleColor=INK_SECONDARY,
            labelFontSize=11,
            titleFontSize=11,
            symbolType="square",
            symbolSize=90,
            labelFont=FONT_STACK,
            titleFont=FONT_STACK,
        )
        .configure_text(font=FONT_STACK)
    )
