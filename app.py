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

from burnrate import analysis, insights, theme
from burnrate.adapters import claude_code, fake
from burnrate.pricing import PriceBook, add_estimated_cost, unpriced_models

st.set_page_config(page_title="Burn Rate", page_icon="🔥", layout="wide")

MONEY = "${:,.2f}"


def escape_money(text: str) -> str:
    """Streamlit markdown reads a pair of $ as LaTeX, which mangles prices."""
    return text.replace("$", r"\$")


def compact(number: float) -> str:
    """12,900 -> 12.9K. Keeps big numbers readable in a small tile."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            return f"{number / limit:,.1f}{suffix}"
    return f"{number:,.0f}"


st.markdown(
    f"""
    <style>
      /* Columns of numbers line up; the hero number keeps proportional figures. */
      [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
      [data-testid="stMetricValue"] {{ font-size: 1.6rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def load_real():
    report = claude_code.LoadReport()
    return add_estimated_cost(claude_code.load(report=report)), report


@st.cache_data(show_spinner=False)
def load_fake():
    return add_estimated_cost(fake.generate(days=60)), None


# ---------------------------------------------------------------- sidebar

st.sidebar.title("🔥 Burn Rate")

use_real = st.sidebar.toggle(
    "Use my real logs", value=True,
    help="Off = made-up sample data, for trying things out safely.",
)

frame, report = load_real() if use_real else load_fake()

if frame.empty:
    st.title("Burn Rate")
    st.warning(
        "No Claude Code usage logs found in `~/.claude/projects`. "
        "Turn off **Use my real logs** in the sidebar to explore with sample data."
    )
    st.stop()

min_date, max_date = frame["date"].min().date(), frame["date"].max().date()
picked = st.sidebar.date_input(
    "Date range", value=(min_date, max_date),
    min_value=min_date, max_value=max_date,
)
if isinstance(picked, tuple) and len(picked) == 2:
    start, end = picked
    frame = frame[
        (frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))
    ]

projects = sorted(frame["project"].dropna().unique())
chosen = st.sidebar.multiselect("Projects", projects, default=projects)
if chosen:
    frame = frame[frame["project"].isin(chosen)]

if frame.empty:
    st.title("Burn Rate")
    st.warning("Nothing matches those filters. Widen the date range or pick a project.")
    st.stop()

prices = PriceBook.load()
st.sidebar.divider()
st.sidebar.caption(
    f"Prices from `prices.yaml`, fetched {prices.fetched} from LiteLLM's "
    "public price list, which tracks Anthropic's published API pricing. "
    "Refresh with `uv run python scripts/refresh_prices.py`.\n\n"
    "Everything stays on this machine. No conversation text is ever read, "
    "and Streamlit's usage statistics are switched off."
)
if report is not None:
    st.sidebar.caption(report.summary())
    for warning in report.warnings:
        st.sidebar.warning(warning, icon="⚠️")


# ---------------------------------------------------------------- header

today = date.today()
forecast = analysis.forecast_month_end(frame, today)
this_month = analysis.month_slice(frame, today)

st.title("Burn Rate")

missing = unpriced_models(frame)
if missing:
    st.error(
        "No price for: " + ", ".join(missing) + ". Those rows are excluded from "
        "every total below. Add them to `prices.yaml`.",
        icon="🚫",
    )

hero, tiles = st.columns([1.1, 2])

with hero:
    # Inline styles, because Streamlit's own rules out-specify a plain class.
    st.markdown(
        f"""
        <div style="font-size:0.8rem;color:{theme.INK_SECONDARY};
                    text-transform:uppercase;letter-spacing:0.07em;">
          {today:%B} so far
        </div>
        <div style="font-size:3.4rem;font-weight:600;line-height:1.05;
                    color:{theme.INK};margin:0.15rem 0 0.35rem 0;">
          {MONEY.format(forecast.month_to_date)}
        </div>
        <div style="font-size:0.88rem;color:{theme.INK_SECONDARY};">
          of API-equivalent value — not a bill
        </div>
        """,
        unsafe_allow_html=True,
    )

with tiles:
    a, b, c = st.columns(3)
    a.metric("API calls", compact(len(this_month)))
    b.metric("Sessions", compact(this_month["session_id"].nunique()))
    c.metric("Output tokens", compact(int(this_month["output_tokens"].sum())))

    if forecast.is_reliable:
        st.metric(
            f"Projected total for {today:%B}",
            MONEY.format(forecast.projected_total),
            delta=MONEY.format(forecast.projected_total - forecast.month_to_date)
            + " still to come",
            delta_color="off",
        )
        st.caption(escape_money("Forecast: " + forecast.explain()))
    else:
        st.caption(
            f"**No month-end forecast yet.** Only {forecast.days_elapsed} day(s) into "
            f"{today:%B} with {forecast.active_days} active day(s) — too little to "
            "project from. A forecast appears after a week of data."
        )

st.info(
    "**These are estimates, not bills.** Every figure is *API-equivalent value*: "
    "what this usage would have cost at pay-as-you-go API prices. You paid through "
    "MIT's Claude Enterprise plan, so none of it was billed to you directly.\n\n"
    "**Scope: Claude Code only.** Usage from Claude chat, Cowork and MIT's Parley "
    "is not included — none of it is logged on this machine.",
    icon="ℹ️",
)

st.divider()

# ---------------------------------------------------------------- trend

st.subheader("Daily trend")

daily = analysis.daily_totals(frame)
quiet_days = int((daily["estimated_cost"] == 0).sum())

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
st.caption(
    f"{quiet_days} of {len(daily)} days in this range had no Claude Code usage. "
    "Quiet days are shown as gaps rather than skipped, so the trend isn't flattered."
)

st.divider()

# ---------------------------------------------------------------- where it goes

st.subheader("Where it goes")

left, right = st.columns(2)

for column, heading, target in (
    ("model", "By model", left),
    ("project", "By project", right),
):
    grouped = analysis.by_column(frame, column)
    with target:
        st.markdown(f"**{heading}**")

        # A one-bar bar chart is just a number wearing a costume.
        if len(grouped) == 1:
            only = grouped.iloc[0]
            st.metric(str(only[column]), MONEY.format(only["estimated_cost"]))
            st.caption(
                f"All {int(only['calls']):,} API calls in this range used "
                f"`{only[column]}`. Nothing to compare it against."
            )
            continue

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
                y=alt.Y(f"{column}:N", title=None, sort="-x"),
                tooltip=[
                    alt.Tooltip(f"{column}:N", title=heading[3:].capitalize()),
                    alt.Tooltip("estimated_cost:Q", title="Value", format="$,.2f"),
                    alt.Tooltip("calls:Q", title="API calls", format=","),
                    alt.Tooltip("sessions:Q", title="Sessions", format=","),
                ],
            )
            .properties(height=max(120, 46 * len(grouped)))
        )
        st.altair_chart(theme.style(bars), width="stretch")

st.divider()

# ---------------------------------------------------------------- tokens vs cost

st.subheader("Tokens are not dollars")
st.markdown(
    "The two bars below show the same usage measured two ways. They disagree on "
    "purpose: counting **tokens** tells you about caching, counting **dollars** "
    "tells you about cost. Reading the top bar as if it were the bottom one is "
    "the easiest mistake to make with this data."
)

mix_long = analysis.token_mix_long(frame)
mix_long = mix_long[mix_long["value"] > 0]

ROW_ORDER = ["Share of tokens", "Share of cost"]

base = alt.Chart(mix_long).encode(
    y=alt.Y("measure:N", title=None, sort=ROW_ORDER),
    x=alt.X(
        "start:Q",
        title=None,
        scale=alt.Scale(domain=[0, 1], nice=False),
        axis=alt.Axis(format=".0%", tickCount=5),
    ),
)

segments = base.mark_bar(
    height=52,
    stroke=theme.SURFACE,      # the 2px surface gap between segments
    strokeWidth=theme.SURFACE_GAP,
).encode(
    x2=alt.X2("end:Q"),
    color=alt.Color(
        "kind:N",
        scale=theme.token_scale(),
        legend=alt.Legend(title=None, orient="top", columns=3, labelLimit=200),
    ),
    tooltip=[
        alt.Tooltip("measure:N", title="Measured as"),
        alt.Tooltip("kind:N", title="Token type"),
        alt.Tooltip("display:N", title="Amount"),
        alt.Tooltip("share:Q", title="Share", format=".1%"),
    ],
)

# Label only segments with room for the text — a clipped label is worse than none.
#
# Note: this hides small labels with opacity rather than filtering the rows out.
# Filtering would remove those rows from the stack, so the remaining labels would
# be positioned against a *different* stack than the bars and drift off their
# segments. Same-rows-different-opacity keeps both layers on identical geometry.
labels = base.mark_text(fontSize=12, fontWeight="bold").encode(
    x=alt.X("mid:Q", title=None, scale=alt.Scale(domain=[0, 1], nice=False)),
    text=alt.Text("share:Q", format=".0%"),
    opacity=alt.condition(alt.datum.share >= 0.10, alt.value(1), alt.value(0)),
    color=alt.Color(
        "kind:N",
        scale=alt.Scale(
            domain=theme.TOKEN_ORDER,
            range=[theme.TOKEN_LABEL_INK[k] for k in theme.TOKEN_ORDER],
        ),
        legend=None,
    ),
)

# Two things worth knowing about this chart:
#
# resolve_scale(color="independent") — layers share their colour scale by
# default, so without this the labels get painted in their own segment's colour:
# pink text on pink. They render perfectly and are completely invisible.
#
# height=260 — Streamlit stretches charts with Vega's "fit" autosize, which
# counts the legend and axis band inside this number. It has to cover all three,
# not just the plot, or the bars collapse into each other.
st.altair_chart(
    theme.style(
        (segments + labels)
        .resolve_scale(color="independent")
        .properties(height=260)
    ),
    width="stretch",
)

mix = analysis.token_mix(frame)
with st.expander("See the numbers behind that chart"):
    st.dataframe(
        pd.DataFrame(
            {
                "Token type": mix["kind"],
                "Tokens": mix["tokens"].map("{:,}".format),
                "Estimated value": mix["cost"].map(MONEY.format),
                "% of tokens": mix["token_share"].map("{:.1%}".format),
                "% of cost": mix["cost_share"].map("{:.1%}".format),
            }
        ),
        hide_index=True,
        width="stretch",
    )

st.divider()

# ---------------------------------------------------------------- sessions

st.subheader("Cost per session")

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

# ---------------------------------------------------------------- savings

st.subheader("Where you could save")

found = insights.savings_suggestions(frame)
if not found:
    st.caption("Nothing stands out in this range. Try widening the date filter.")

for suggestion in found:
    with st.container(border=True):
        headline = suggestion.title
        if suggestion.dollars is not None:
            headline += f" — {MONEY.format(suggestion.dollars)}"
        st.markdown(f"**{escape_money(headline)}**")
        st.markdown(escape_money(suggestion.detail))
        if suggestion.caveat:
            st.caption(escape_money(suggestion.caveat))

st.divider()
st.caption(
    "Burn Rate runs entirely on your machine. It reads token counts and metadata "
    "from Claude Code's local logs and never stores, sends, or even parses "
    "conversation text."
)
