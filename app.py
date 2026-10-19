"""Burn Rate — a local dashboard for AI token usage and API-equivalent cost.

Run it with:  uv run streamlit run app.py

Layout notes, since they're deliberate:
  - One hero number, at the top. Everything else is secondary to it.
  - Filters live in the sidebar only, so every chart shows the same slice.
  - Charts carry their chrome lightly: hairline grid, muted axis text, no boxes.
  - Colour comes from burnrate.theme and is fixed per entity, never per rank.
"""

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from burnrate import analysis, insights, schema, theme
from burnrate import adapters
from burnrate.pricing import PriceBook, add_estimated_cost, unpriced_models

st.set_page_config(page_title="Burn Rate", page_icon="🔥", layout="wide")

MONEY = "${:,.2f}"


def escape_money(text: str) -> str:
    """Streamlit markdown reads a pair of $ as LaTeX, which mangles prices."""
    return text.replace("$", r"\$")


# All styling comes from burnrate/theme.py, so colours and type live in one
# place rather than being scattered through the layout.
st.markdown(theme.page_css(), unsafe_allow_html=True)


# How each `product` value is written for a human. One place, so a tool is
# never called "claude_code" in one panel and "Claude Code" in another.
PRODUCT_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}

# Where each tool's usage is read from, for the scope note.
PRODUCT_SOURCES = {
    "claude_code": "`~/.claude/projects`",
    "codex": "`~/.codex/sessions`",
}


# ---------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def load_real():
    frame, report = adapters.load_all()
    return add_estimated_cost(frame), report


# ---------------------------------------------------------------- controls
#
# Everything lives on the page itself; there is no sidebar. The toggle has to
# be declared before the data is loaded, because its value decides what gets
# loaded, and a Streamlit widget's value only exists once the widget has run.

st.title("Burn Rate")
st.markdown(
    f"""<div style="font-size:0.95rem;color:{theme.INK_SECONDARY};
                    letter-spacing:{theme.TRACKING_BODY};
                    margin:-0.6rem 0 0.9rem 0;">
          Track your token usage
        </div>""",
    unsafe_allow_html=True,
)

# The rail's order has to match the page's. These ids are the anchors
# Streamlit generates from the subheadings below.
SECTIONS = [
    ("burn-rate", "Overview"),
    ("by-tool", "By tool"),
    ("where-it-goes", "Where it goes"),
    ("daily-trend", "Daily trend"),
    ("breakdown", "Breakdown"),
    ("recommendations", "Recommendations"),
]
st.markdown(theme.scroll_rail(SECTIONS), unsafe_allow_html=True)

frame, report = load_real()

if frame.empty:
    st.warning(
        "No usage logs found — looked for Claude Code in `~/.claude/projects` "
        "and Codex in `~/.codex/sessions`."
    )
    st.stop()

min_date, max_date = frame["date"].min().date(), frame["date"].max().date()

# The period selector lives at the top of the page, not in the sidebar: it is
# the control you reach for most, and it changes every number below it.
# "Custom" reveals the date pickers, so the fine-grained control is still
# there without two time controls competing to be the source of truth.
period = st.radio(
    "Period", analysis.PERIODS, index=2, horizontal=True,
    label_visibility="collapsed",
)

custom_range = None
if period == "Custom":
    picked = st.date_input(
        "Date range", value=(min_date, max_date),
        min_value=min_date, max_value=max_date,
    )
    if isinstance(picked, tuple) and len(picked) == 2:
        custom_range = picked

# Filters sit in an expander, closed by default. They are worth having but
# they are not what you came to read, and a row of filled-in multiselects at
# the top of the page competes with the numbers for attention.
products = sorted(frame["product"].dropna().unique())
projects = sorted(frame["project"].dropna().unique())

with st.expander("Filters"):
    tool_col, project_col = st.columns(2)
    with tool_col:
        # Only offer the tool filter when there is more than one tool to
        # choose between — a filter with a single option is furniture.
        if len(products) > 1:
            picked_products = st.multiselect(
                "Tools", products, default=products,
                format_func=lambda name: PRODUCT_LABELS.get(name, name),
            )
            if picked_products:
                frame = frame[frame["product"].isin(picked_products)]
        else:
            st.caption(
                f"Only one tool in these logs: "
                f"{PRODUCT_LABELS.get(products[0], products[0])}."
            )
    with project_col:
        chosen = st.multiselect("Projects", projects, default=projects)
        if chosen:
            frame = frame[frame["project"].isin(chosen)]

# `scoped` is everything the tool and project filters allow, across ALL time.
# The period slice is applied after it, because some figures must ignore the
# period to mean anything: "last 30 days" inside a one-day period would just
# be that day, and the month-end forecast needs the whole calendar month
# whatever window is on screen.
scoped = frame

if custom_range is not None:
    start, end = custom_range
    frame = frame[
        (frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))
    ]
elif period != "Custom":
    frame = analysis.period_slice(frame, period)

if frame.empty:
    st.warning(
        "Nothing matches those filters. Widen the period, or clear a filter above."
    )
    st.stop()

prices = PriceBook.load()

# Load warnings stay near the top: they say the numbers below are incomplete,
# which is no use to anyone once they have already read them.
if report is not None:
    for warning in report.warnings:
        st.warning(warning, icon="⚠️")


# ---------------------------------------------------------------- header

today = date.today()

missing = unpriced_models(frame)
if missing:
    st.error(
        "No price for: " + ", ".join(missing) + ". Those rows are excluded from "
        "every total below. Add them to `prices.yaml`.",
        icon="🚫",
    )

period_label = {
    "Day": "today",
    "Week": "last 7 days",
    "Month": "last 30 days",
    "Total": "all time",
    "Custom": "selected range",
}[period]

period_tokens = int(schema.total_tokens(frame).sum())
period_cost = float(frame["estimated_cost"].sum())
# Deliberately `scoped`: these four tiles are fixed windows, and they answer
# "how does recent usage compare" regardless of which period is on screen.
stats = analysis.headline_stats(scoped, today)


def hero_block(label: str, value: str, note: str) -> str:
    """One big number with its caption. Inline styles, because Streamlit's
    own rules out-specify a plain class."""
    return f"""
        <div style="font-size:0.72rem;color:{theme.INK_SECONDARY};
                    text-transform:uppercase;letter-spacing:{theme.TRACKING_LABEL};">
          {label}
        </div>
        <div style="font-size:2.9rem;font-weight:300;line-height:1.05;
                    letter-spacing:0.01em;
                    color:{theme.INK};margin:0.15rem 0 0.35rem 0;">
          {value}
        </div>
        <div style="font-size:0.84rem;color:{theme.INK_SECONDARY};">
          {note}
        </div>
    """


# Two hero numbers, shown as a pair on purpose. The design rule elsewhere in
# this project is one hero per screen, and this is a deliberate exception:
# the whole argument of the dashboard is that these two measures disagree, so
# showing one without the other is what the page is arguing against.
tokens_col, cost_col = st.columns(2)
with tokens_col:
    st.markdown(
        hero_block(
            f"Total tokens — {period_label}",
            f"{period_tokens:,}",
            "",
        ),
        unsafe_allow_html=True,
    )
with cost_col:
    st.markdown(
        hero_block(
            f"API-equivalent value — {period_label}",
            MONEY.format(period_cost),
            "what this would have cost at API prices",
        ),
        unsafe_allow_html=True,
    )

st.write("")

# Widest window first, narrowing left to right, so the eye reads from the
# settled number towards the volatile one rather than the other way round.
tiles = [
    (f"{today:%B} to date", stats.month_to_date_cost),
    ("Last 30 days", stats.last_30_cost),
    ("Last 7 days", stats.last_7_cost),
    ("Per active day", stats.daily_average),
]
for (label, value), column in zip(tiles, st.columns(4)):
    with column:
        st.markdown(
            f"""
            <div style="background:{theme.TILE_SURFACE};
                        border:1px solid {theme.TILE_SURFACE};
                        border-radius:6px;padding:0.85rem 0.6rem;text-align:center;">
              <div style="font-size:0.7rem;color:{theme.TILE_INK_MUTED};
                          text-transform:uppercase;
                          letter-spacing:{theme.TRACKING_LABEL};">
                {label}
              </div>
              <div style="font-size:1.6rem;font-weight:300;line-height:1.3;
                          color:{theme.TILE_INK};margin-top:0.15rem;">
                {MONEY.format(value).replace("$", "&#36;")}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()

# ---------------------------------------------------------------- tools

# One card per tool. With two sources this is a small grid, and that is the
# honest picture — it grows on its own when an adapter is added.
by_product = analysis.by_product(frame)

if len(by_product) > 1:
    st.subheader("By tool")

    # Capped at four per row so the tiles stay square at any window width.
    # With two tools the row is half empty, which is the honest picture.
    PER_ROW = 4
    rows = [by_product[i:i + PER_ROW] for i in range(0, len(by_product), PER_ROW)]
    for chunk in rows:
        for row, column in zip(chunk.itertuples(), st.columns(PER_ROW)):
            with column:
                st.markdown(
                    theme.tool_tile(
                        product=row.product,
                        label=PRODUCT_LABELS.get(row.product, row.product),
                        share=f"{row.share:.1%}",
                        detail=(
                            MONEY.format(row.estimated_cost).replace("$", "&#36;")
                            + f"<br>{row.models} model(s) · {row.calls:,} calls"
                        ),
                    ),
                    unsafe_allow_html=True,
                )

    st.divider()

# ---------------------------------------------------------------- where it goes

st.subheader("Where it goes")

# The two panels line up with each other EXACTLY, which takes a little care.
#
# The obvious build — bars with their names on the y axis — cannot do it:
# Vega sizes the axis to whatever the longest label happens to be, so
# "claude-opus-5" gives one left margin and
# "demonstrate-your-ability-to-use-this" gives a much wider one, and the two
# panels' bars start at different x positions.
#
# So the names come OUT of the axis and become their own column, with a width
# we choose. Three panels of fixed width, identical in both sections, means
# the alignment is arithmetic rather than a hope.
LABEL_WIDTH = 170
BAR_WIDTH = 430
NUMBER_WIDTH = 240
PANEL_SPACING = 14
MAX_LABEL_CHARS = 24

NUMBER_COLUMNS = ["Tokens", "Value", "Share"]


def shorten(name: str) -> str:
    """Cut a name to fit the label column, with an ellipsis if it was cut."""
    text = str(name)
    if len(text) <= MAX_LABEL_CHARS:
        return text
    return text[: MAX_LABEL_CHARS - 1] + "\u2026"


for column, heading in (("model", "By model"), ("project", "By project")):
    grouped = analysis.by_column(frame, column)
    st.markdown(f"**{heading}**")

    # A one-bar bar chart is just a number wearing a costume.
    if len(grouped) == 1:
        only = grouped.iloc[0]
        st.metric(str(only[column]), MONEY.format(only["estimated_cost"]))
        st.caption(
            f"All {int(only['calls']):,} API calls in this range used "
            f"`{only[column]}`. Nothing to compare it against."
        )
        st.write("")
        continue

    grouped = grouped.copy()
    grouped["label"] = grouped[column].map(shorten)

    # Every panel is positioned by the FULL name, never the shortened one.
    # Two long names can shorten to the same string, and keying rows on the
    # display text would silently merge them into one row.
    order = grouped[column].astype(str).tolist()
    height = max(120, 46 * len(grouped))
    row = alt.Y(f"{column}:N", sort=order, axis=None, title=None)

    labels = (
        alt.Chart(grouped)
        .mark_text(align="right", fontSize=12, color=theme.INK_SECONDARY,
                   font=theme.FONT_STACK)
        .encode(
            x=alt.value(LABEL_WIDTH),
            y=row,
            text="label:N",
            tooltip=alt.Tooltip(f"{column}:N", title=heading[3:].capitalize()),
        )
        .properties(width=LABEL_WIDTH, height=height)
    )

    bars = (
        alt.Chart(grouped)
        .mark_bar(
            size=theme.BAR_SIZE,
            cornerRadiusTopRight=theme.CORNER,
            cornerRadiusBottomRight=theme.CORNER,
            color=theme.ACCENT,
        )
        .encode(
            x=alt.X(
                "estimated_cost:Q",
                title="Estimated value ($)",
                axis=alt.Axis(format="$,.2f", tickCount=4),
            ),
            y=row,
            tooltip=[
                alt.Tooltip(f"{column}:N", title=heading[3:].capitalize()),
                alt.Tooltip("estimated_cost:Q", title="Value", format="$,.2f"),
                alt.Tooltip("total_tokens:Q", title="Tokens", format=","),
                alt.Tooltip("calls:Q", title="API calls", format=","),
            ],
        )
        .properties(width=BAR_WIDTH, height=height)
    )

    # Long form: one record per (row, column), so the three number columns are
    # one nominal axis rather than three hand-placed text layers.
    numbers_long = pd.DataFrame(
        [
            {"name": str(record[column]), "metric": metric, "text": text}
            for _, record in grouped.iterrows()
            for metric, text in (
                ("Tokens", f"{int(record['total_tokens']):,}"),
                ("Value", MONEY.format(record["estimated_cost"])),
                ("Share", f"{record['share']:.1%}"),
            )
        ]
    )

    numbers = (
        alt.Chart(numbers_long)
        .mark_text(align="center", fontSize=12, color=theme.INK,
                   font=theme.FONT_STACK)
        .encode(
            x=alt.X(
                "metric:N",
                title=None,
                sort=NUMBER_COLUMNS,
                axis=alt.Axis(orient="top", labelAngle=0, domain=False,
                              ticks=False, grid=False, labelPadding=8),
            ),
            y=alt.Y("name:N", sort=order, axis=None, title=None),
            text="text:N",
        )
        .properties(width=NUMBER_WIDTH, height=height)
    )

    st.altair_chart(
        theme.style(alt.hconcat(labels, bars, numbers, spacing=PANEL_SPACING)),
        width="content",
    )
    st.write("")

st.divider()

# ---------------------------------------------------------------- trend

st.subheader("Daily trend")

daily = analysis.daily_totals(frame)

trend = (
    alt.Chart(daily)
    .mark_bar(
        size=theme.BAR_SIZE,
        cornerRadiusTopLeft=theme.CORNER,
        cornerRadiusTopRight=theme.CORNER,
        color=theme.ACCENT,
    )
    .encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%-d %b", tickCount=8)),
        y=alt.Y(
            "estimated_cost:Q",
            title="Estimated value ($)",
            axis=alt.Axis(format="$,.2f", tickCount=5),
        ),
        tooltip=[
            alt.Tooltip("date:T", title="Day", format="%A %-d %B"),
            alt.Tooltip("estimated_cost:Q", title="Estimated value", format="$,.2f"),
            alt.Tooltip("calls:Q", title="API calls", format=","),
            alt.Tooltip("total_tokens:Q", title="Tokens", format=","),
        ],
    )
    .properties(height=240)
)
st.altair_chart(theme.style(trend), width="stretch")

st.divider()

# ---------------------------------------------------------------- sessions

st.subheader("Breakdown")

daily_tab, project_tab, session_tab = st.tabs(
    ["Daily", "By project", "By session"]
)


def breakdown_table(table: pd.DataFrame, first_column: str, first_values) -> None:
    """Render one breakdown table with the token columns in a fixed order."""
    st.dataframe(
        pd.DataFrame(
            {
                first_column: first_values,
                "Total tokens": table["total_tokens"].map("{:,}".format),
                "Input": table["input_tokens"].map("{:,}".format),
                "Output": table["output_tokens"].map("{:,}".format),
                "Cached": table["cached_tokens"].map("{:,}".format),
                "Reasoning": table["reasoning_tokens"].map("{:,}".format),
                "Sessions": table["sessions"],
                "Estimated value": table["estimated_cost"].map(MONEY.format),
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "**Reasoning is already inside Output**, not an extra column to add on — "
        "it is the thinking the model did before replying. **Cached** folds the "
        "three cache kinds together for counting only; they were priced "
        "separately, at rates up to 20x apart."
    )


with daily_tab:
    daily_table = analysis.daily_breakdown(frame)
    breakdown_table(
        daily_table, "Date", daily_table["date"].dt.strftime("%Y-%m-%d")
    )

with project_tab:
    project_table = analysis.project_breakdown(frame)
    breakdown_table(project_table, "Project", project_table["project"])

with session_tab:
    sessions = analysis.by_session(frame)
    st.dataframe(
        pd.DataFrame(
            {
                "Date": sessions["date"].dt.strftime("%Y-%m-%d"),
                "Project": sessions["project"],
                "Model": sessions["model"],
                "API calls": sessions["calls"],
                "Output tokens": sessions["output_tokens"].map("{:,}".format),
                "Estimated value": sessions["estimated_cost"].map(MONEY.format),
                "Per call": sessions["cost_per_call"].map("${:,.3f}".format),
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        escape_money(
            f"{len(sessions):,} sessions, most expensive first. "
            f"Median ${sessions['estimated_cost'].median():,.2f}, "
            f"most expensive ${sessions['estimated_cost'].max():,.2f}."
        )
    )

st.divider()

# ---------------------------------------------------------------- recommendations

st.subheader("Recommendations")

recs = insights.recommendations(frame)

if not recs:
    st.caption(
        "Nothing stands out in this range — no rule had enough data to fire. "
        "Try a wider period."
    )
else:
    for number, (rec, column) in enumerate(zip(recs, st.columns(len(recs))), start=1):
        with column:
            st.markdown(
                theme.recommendation_card(number, rec.action, rec.because),
                unsafe_allow_html=True,
            )

    st.caption(
        "Ordered by how much each usually moves the number, not by a measured "
        "saving — only some of these have one that can honestly be calculated. "
        "None is a promise: a smaller model may need more turns, and splitting "
        "work that genuinely needs shared context just makes you re-explain it."
    )

st.divider()

provenance, privacy = st.columns(2)
with provenance:
    st.caption(
        f"Prices from `prices.yaml`, fetched {prices.fetched} from LiteLLM's "
        "public price list, which tracks Anthropic's and OpenAI's published API "
        "pricing. Refresh with `uv run python scripts/refresh_prices.py`."
    )
    if report is not None:
        st.caption(report.summary())
with privacy:
    st.caption(
        "Burn Rate runs entirely on your machine. It reads token counts and "
        "metadata from Claude Code's and Codex's local logs and never stores, "
        "sends, or even parses conversation text. Streamlit's usage statistics "
        "are switched off and no fonts or prices are fetched while it runs."
    )
