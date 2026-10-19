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
import re

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

# The stat tiles invert the page: beige ground, oxblood type. Inverting is
# not free -- a pairing that reads well one way round can fail the other --
# so these are checked by scripts/check_palette.py like everything else.
TILE_SURFACE = "#ece8d7"      # beige, the same bone as the chart accent
TILE_INK = "#5d2627"          # oxblood type on it
TILE_INK_MUTED = "#7a4041"    # the tile's small-caps label

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



def _markdown_safe(html: str) -> str:
    """Flatten the indentation of an HTML block before st.markdown sees it.

    Markdown treats any line indented four spaces or more as a literal code
    block, so a nicely indented <div> renders as escaped source on the page
    instead of as markup. textwrap.dedent does not save you here: it removes
    only the COMMON prefix, and one flush-left line (the opening <style>) is
    enough to make that prefix zero while every other line stays indented.

    Indentation is meaningless to both CSS and HTML, so the fix is simply to
    strip it from every line.
    """
    return "\n".join(line.lstrip() for line in html.splitlines()).strip()


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

    return _markdown_safe(f"""
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
      [data-testid="stDataFrame"] {{
        font-variant-numeric: tabular-nums;
        border-radius: 6px;
        overflow: hidden;   /* without this the square grid shows through */
      }}
      [data-testid="stMetricValue"] {{
        font-size: 1.6rem;
        font-weight: 300;
        letter-spacing: {TRACKING_BODY};
        color: {INK};
      }}

      hr, [data-testid="stDivider"] {{ border-color: {BORDER}; }}

      /* Period selector, drawn as a segmented control rather than radio
         buttons. Streamlit has no segmented widget, so this is a radio group
         with the circles hidden and the labels made into pills. The selected
         one is found with :has(input:checked) — the real radio input is still
         there underneath, so keyboard and screen-reader behaviour is
         unchanged, and the control still works if :has() is unsupported (it
         just looks like radio buttons again). */
      [data-testid="stRadio"] [role="radiogroup"] {{
        gap: 0.35rem;
        flex-wrap: wrap;
      }}
      [data-testid="stRadio"] [role="radiogroup"] label {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 999px;
        padding: 0.28rem 1rem;
        margin: 0;
        cursor: pointer;
      }}
      /* The radio circle. It is a nested div, not the span that wraps the
         input, so hiding the span alone leaves the dot on screen. */
      [data-testid="stRadio"] [role="radiogroup"] label > div > div:first-child {{
        display: none;
      }}
      /* The option text is a <p>, which the global paragraph colour above
         would otherwise paint cream-on-cream once the pill is filled. */
      [data-testid="stRadio"] [role="radiogroup"] label p {{
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: {TRACKING_LABEL};
        color: {INK_SECONDARY} !important;
        margin: 0;
      }}
      [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {{
        background: {INK};
        border-color: {INK};
      }}
      [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) p {{
        color: {PAGE} !important;
      }}

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
    """)


# -- chart specs ---------------------------------------------------------

BAR_SIZE = 22       # cap bar thickness; the leftover band is deliberate air
CORNER = 4          # rounded data-end, square at the baseline
SURFACE_GAP = 2     # gap separating touching marks, in the surface colour
PANEL_CORNER = 6    # the panel itself — matches the tiles and cards in CSS


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
        # cornerRadius rounds the view's own background rectangle, which is
        # the shaded panel behind the marks. Same radius as the HTML cards so
        # a chart and a tile do not disagree by two pixels.
        .configure_view(stroke=None, fill=SURFACE, cornerRadius=PANEL_CORNER)
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


def scroll_rail(sections) -> str:
    """A fixed rail on the left showing how far down the page you are.

    `sections` is a list of (anchor_id, label). The ids are the ones Streamlit
    already generates for its headings, so the links need nothing added to the
    markup they point at.

    HOW THE POSITION INDICATOR WORKS, since it looks like magic:

    Streamlit strips <script> out of st.markdown, so there is no JavaScript
    available and no scroll listener to attach. This instead uses a CSS
    scroll-driven animation: `section[data-testid="stMain"]` is the element
    that actually scrolls (not the window, which is why `scroll(root)` would
    silently do nothing), so it publishes a named timeline and the fill
    animates along it. No script, no polling, and it cannot fall out of sync
    with the scrollbar because it IS the scrollbar position.

    Browsers without scroll-driven animations show the rail with a full-height
    track and no fill -- still a working table of contents, just not a
    position indicator. The links work regardless.

    The rail hides below 1100px, where the centred content would sit under it.
    """
    links = "\n".join(
        f'<a href="#{anchor}">{label}</a>' for anchor, label in sections
    )

    # dedent + strip: st.markdown reads an indented block as literal code,
    # which renders the rail as escaped HTML instead of a rail.
    return _markdown_safe(f"""
    <style>
      /* The scroller publishes a timeline the rail can animate along. */
      section[data-testid="stMain"] {{
        scroll-timeline-name: --page;
        scroll-timeline-axis: y;
      }}
      .stApp {{ timeline-scope: --page; }}

      .burn-rail {{
        position: fixed;
        left: 1.25rem;
        top: 50%;
        transform: translateY(-50%);
        display: flex;
        gap: 0.7rem;
        z-index: 90;
        pointer-events: none;
      }}
      .burn-rail-track {{
        width: 2px;
        background: {BORDER};
        border-radius: 2px;
        position: relative;
        overflow: hidden;
      }}
      .burn-rail-fill {{
        position: absolute;
        top: 0; left: 0;
        width: 100%;
        height: 0%;
        background: {INK};
        animation: burn-rail-grow linear both;
        animation-timeline: --page;
      }}
      @keyframes burn-rail-grow {{
        from {{ height: 0%; }}
        to   {{ height: 100%; }}
      }}

      .burn-rail-links {{
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        pointer-events: auto;
      }}
      .burn-rail-links a {{
        font-size: 0.62rem;
        text-transform: uppercase;
        /* Tighter than the label tracking used elsewhere: the rail is sized
           by its longest entry, and generous tracking on "RECOMMENDATIONS"
           is what pushes it into the content. */
        letter-spacing: 0.1em;
        color: {INK_MUTED};
        text-decoration: none;
        padding: 0.3rem 0;
        white-space: nowrap;
      }}
      .burn-rail-links a:hover,
      .burn-rail-links a:focus {{
        color: {INK};
        outline: none;
      }}

      /* Make room for the rail. Streamlit's content block has 80px of left
         padding by default, which is less than the rail is wide, so without
         this the labels sit on top of the numbers.
         The breakpoint is 1250px rather than the rail's own width: the
         "Where it goes" panels are a fixed ~870px, so the rail may only
         appear where there is room for BOTH. */
      @media (min-width: 1250px) {{
        [data-testid="stMainBlockContainer"] {{ padding-left: 12rem !important; }}
      }}
      @media (max-width: 1249px) {{
        .burn-rail {{ display: none; }}
      }}
    </style>

    <div class="burn-rail">
      <div class="burn-rail-track"><div class="burn-rail-fill"></div></div>
      <div class="burn-rail-links">{links}</div>
    </div>
    """)


# -- tool marks ----------------------------------------------------------
#
# The real Claude and OpenAI brand marks, vendored in burnrate/logos/ — see
# SOURCE.md there for provenance and the trademark note. Like the font, they
# are read off disk rather than fetched, so nothing goes out over the network
# while the dashboard is running.

LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos")

# Anything with no logo file gets a plain square, so a newly added adapter
# produces a tidy tile rather than a ragged, mark-less heading.
FALLBACK_MARK = (
    '<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.6">'
    '<rect x="4.5" y="4.5" width="15" height="15" rx="2"/>'
    "</svg>"
)


def tool_mark(product: str, size: int = 18) -> str:
    """The brand mark for one tool, sized and recoloured to match its tile.

    `currentColor` is the point: the file carries the shape and this sets the
    ink, so a mark never needs editing when the palette changes. Simple Icons
    paths have no fill of their own, which is what makes that work.
    """
    path = os.path.join(LOGO_DIR, f"{product}.svg")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            svg = handle.read()
    except OSError:
        return FALLBACK_MARK.format(size=size)

    # The file is 24x24 with no sizing or colour of its own; add both.
    svg = svg.replace(
        "<svg ",
        f'<svg width="{size}" height="{size}" fill="currentColor" ',
        1,
    )
    # The <title> would show as a tooltip duplicating the label beside it.
    return re.sub(r"<title>.*?</title>", "", svg, flags=re.S)


def tool_tile(product: str, label: str, share: str, detail: str) -> str:
    """A square beige tile for one tool.

    Square because the row is capped at four across: with two tools and a full
    row to fill, a tile stretched to half the page would be a letterbox with a
    percentage floating in it.
    """
    return _markdown_safe(f"""
    <div style="background:{TILE_SURFACE};border-radius:6px;
                aspect-ratio:1/1;padding:1rem 1.1rem;
                display:flex;flex-direction:column;justify-content:space-between;">
      <div style="display:flex;align-items:center;gap:0.45rem;color:{TILE_INK_MUTED};">
        {tool_mark(product)}
        <!-- The colour has to be inline. page_css sets `span {{ color: INK }}`
             globally, and a rule that targets the span beats a colour merely
             inherited from the parent div — which on a beige tile means cream
             text on cream. -->
        <span style="font-size:0.68rem;text-transform:uppercase;
                     letter-spacing:{TRACKING_LABEL};
                     color:{TILE_INK_MUTED};">{label}</span>
      </div>
      <div>
        <div style="font-size:2.1rem;font-weight:300;line-height:1.1;
                    color:{TILE_INK};">{share}</div>
        <div style="font-size:0.76rem;color:{TILE_INK_MUTED};margin-top:0.3rem;">
          {detail}
        </div>
      </div>
    </div>
    """)


def recommendation_card(number: int, action: str, because: str) -> str:
    """One numbered recommendation.

    Beige like the stat tiles, because these are things to act on rather than
    things to read past. Every piece of text carries its colour inline: page_css
    styles `span`, `p` and `div` globally, and a rule that targets an element
    beats a colour inherited from its parent — which on a beige card means
    cream on cream.
    """
    return _markdown_safe(f"""
    <div style="background:{TILE_SURFACE};border-radius:{PANEL_CORNER}px;
                padding:1rem 1.1rem;height:100%;">
      <div style="font-size:0.7rem;color:{TILE_INK_MUTED};
                  letter-spacing:{TRACKING_LABEL};">{number:02d}</div>
      <div style="font-size:1.0rem;font-weight:400;line-height:1.35;
                  color:{TILE_INK};margin:0.35rem 0 0.4rem 0;">{action}</div>
      <div style="font-size:0.8rem;line-height:1.45;
                  color:{TILE_INK_MUTED};">{because}</div>
    </div>
    """)
