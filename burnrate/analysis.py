"""The calculations behind the dashboard.

Kept out of app.py so they can be tested. Every function takes a table in the
common format (see burnrate.schema) and returns a smaller table or a number.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from burnrate import schema

# Friendly names for the token columns, used in charts.
TOKEN_LABELS = {
    "input_tokens": "Input",
    "output_tokens": "Output",
    "cache_write_5m_tokens": "Cache write (5 min)",
    "cache_write_1h_tokens": "Cache write (1 hour)",
    "cache_read_tokens": "Cache read",
}

COST_LABELS = {
    "input_cost": "Input",
    "output_cost": "Output",
    "cache_write_5m_cost": "Cache write (5 min)",
    "cache_write_1h_cost": "Cache write (1 hour)",
    "cache_read_cost": "Cache read",
}


def month_slice(frame: pd.DataFrame, today: Optional[date] = None) -> pd.DataFrame:
    """Just the rows from the current calendar month."""
    today = today or date.today()
    start = pd.Timestamp(today.year, today.month, 1)
    return frame[frame["date"] >= start]


def daily_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """Cost and tokens per day, with days of no usage filled in as zero.

    The gaps matter: a trend line that silently skips quiet days makes usage
    look steadier than it was.
    """
    if frame.empty:
        return pd.DataFrame(columns=["date", "estimated_cost", "total_tokens", "calls"])

    working = frame.copy()
    working["total_tokens"] = schema.total_tokens(working)

    daily = (
        working.groupby("date")
        .agg(
            estimated_cost=("estimated_cost", "sum"),
            total_tokens=("total_tokens", "sum"),
            calls=("model", "size"),
        )
        .reset_index()
    )

    every_day = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
    return (
        daily.set_index("date")
        .reindex(every_day, fill_value=0)
        .rename_axis("date")
        .reset_index()
    )


def by_column(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Totals grouped by any column — model, project, product, whatever.

    `share` is share of DOLLARS. A share of tokens would rank rows by how
    much context they re-read, which is a fact about caching rather than
    about where the money went.
    """
    if frame.empty:
        return pd.DataFrame(
            columns=[column, "estimated_cost", "total_tokens", "output_tokens",
                     "calls", "sessions", "share"]
        )

    working = frame.copy()
    working["total_tokens"] = schema.total_tokens(frame)

    grouped = (
        working.groupby(column, observed=True)
        .agg(
            estimated_cost=("estimated_cost", "sum"),
            total_tokens=("total_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            calls=("model", "size"),
            sessions=("session_id", "nunique"),
        )
        .reset_index()
        .sort_values("estimated_cost", ascending=False)
    )
    total = grouped["estimated_cost"].sum()
    grouped["share"] = grouped["estimated_cost"] / total if total else 0.0
    return grouped


def by_session(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per working session, most expensive first."""
    if frame.empty:
        return pd.DataFrame(
            columns=["session_id", "date", "project", "model", "calls", "estimated_cost"]
        )

    working = frame.copy()
    working["cache_read_tokens_"] = working["cache_read_tokens"]

    sessions = (
        working.groupby("session_id")
        .agg(
            date=("date", "min"),
            project=("project", "first"),
            model=("model", lambda s: ", ".join(sorted(set(s.dropna())))),
            calls=("model", "size"),
            output_tokens=("output_tokens", "sum"),
            cache_read_tokens=("cache_read_tokens_", "sum"),
            estimated_cost=("estimated_cost", "sum"),
        )
        .reset_index()
        .sort_values("estimated_cost", ascending=False)
    )
    sessions["cost_per_call"] = sessions["estimated_cost"] / sessions["calls"]
    return sessions


def token_mix(frame: pd.DataFrame) -> pd.DataFrame:
    """Tokens and dollars side by side, per token type.

    This is the table that shows why 'total tokens' is a misleading headline:
    one column is dominated by cache reads, the other usually isn't.
    """
    from burnrate.pricing import breakdown_frame

    if frame.empty:
        return pd.DataFrame(columns=["kind", "tokens", "cost", "token_share", "cost_share"])

    detailed = breakdown_frame(frame)

    rows = []
    for token_column, label in TOKEN_LABELS.items():
        cost_column = token_column.replace("_tokens", "_cost")
        rows.append(
            {
                "kind": label,
                "tokens": int(frame[token_column].sum()),
                "cost": float(detailed[cost_column].sum()),
            }
        )

    mix = pd.DataFrame(rows)
    total_tokens = mix["tokens"].sum() or 1
    total_cost = mix["cost"].sum() or 1
    mix["token_share"] = mix["tokens"] / total_tokens
    mix["cost_share"] = mix["cost"] / total_cost
    return mix


def token_mix_long(frame: pd.DataFrame) -> pd.DataFrame:
    """token_mix reshaped for a two-row stacked bar: tokens above, cost below.

    Putting both measures on one normalised axis is the whole point — it makes
    '97% of tokens, 50% of cost' a single glance instead of a comparison
    between two pie charts, which people are famously bad at.
    """
    mix = token_mix(frame)
    if mix.empty:
        return pd.DataFrame(columns=["measure", "kind", "value", "share", "display"])

    order = {label: index for index, label in enumerate(TOKEN_LABELS.values())}
    mix = mix.sort_values("kind", key=lambda s: s.map(order))

    rows = []
    for measure, value_column, share_column in (
        ("Share of tokens", "tokens", "token_share"),
        ("Share of cost", "cost", "cost_share"),
    ):
        running = 0.0
        for _, row in mix.iterrows():
            share = float(row[share_column])
            rows.append(
                {
                    "measure": measure,
                    "kind": row["kind"],
                    "order": order[row["kind"]],
                    "value": float(row[value_column]),
                    "share": share,
                    # Each segment's span and midpoint along the 0–1 axis,
                    # worked out here rather than left to the chart library.
                    # Doing it explicitly means the bars and their labels sit on
                    # one ordinary scale, instead of a stacked scale for the
                    # bars and a second one for the text that has to be kept in
                    # agreement with it.
                    "start": running,
                    "end": running + share,
                    "mid": running + share / 2,
                    "display": (
                        f"{int(row['tokens']):,} tokens"
                        if value_column == "tokens"
                        else f"${row['cost']:,.2f}"
                    ),
                }
            )
            running += share
    return pd.DataFrame(rows)


@dataclass
class Forecast:
    """A month-end projection, plus everything needed to explain it."""

    month_to_date: float
    days_elapsed: int
    days_in_month: int
    projected_total: float
    active_days: int

    @property
    def daily_average(self) -> float:
        return self.month_to_date / self.days_elapsed if self.days_elapsed else 0.0

    @property
    def is_reliable(self) -> bool:
        """A few days of data cannot forecast a month. Say so rather than pretend."""
        return self.days_elapsed >= 7 and self.active_days >= 3

    def explain(self) -> str:
        return (
            f"${self.month_to_date:,.2f} spent over {self.days_elapsed} days "
            f"= ${self.daily_average:,.2f}/day, projected across "
            f"{self.days_in_month} days."
        )


def forecast_month_end(frame: pd.DataFrame, today: Optional[date] = None) -> Forecast:
    """Project the month's total from the daily rate so far.

    Deliberately simple: month-to-date divided by days elapsed, times days in
    the month. It assumes the rest of the month looks like the start of it,
    which is a real assumption and often wrong — the dashboard says so.
    """
    today = today or date.today()
    this_month = month_slice(frame, today)

    month_to_date = float(this_month["estimated_cost"].sum())
    days_elapsed = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    active_days = int(this_month["date"].nunique()) if not this_month.empty else 0

    projected = (
        month_to_date / days_elapsed * days_in_month if days_elapsed else 0.0
    )

    return Forecast(
        month_to_date=month_to_date,
        days_elapsed=days_elapsed,
        days_in_month=days_in_month,
        projected_total=projected,
        active_days=active_days,
    )


def cost_if_all_on_model(frame: pd.DataFrame, model: str) -> float:
    """What the same tokens would have cost on a different model.

    A rough comparison, not a promise: a cheaper model might need more turns to
    do the same job, which would eat the saving.
    """
    from burnrate.pricing import PriceBook

    prices = PriceBook.load()
    if not prices.knows(model) or frame.empty:
        return float("nan")

    return float(
        prices.breakdown(
            model,
            input_tokens=int(frame["input_tokens"].sum()),
            output_tokens=int(frame["output_tokens"].sum()),
            cache_write_5m_tokens=int(frame["cache_write_5m_tokens"].sum()),
            cache_write_1h_tokens=int(frame["cache_write_1h_tokens"].sum()),
            cache_read_tokens=int(frame["cache_read_tokens"].sum()),
        ).total
    )


# ---------------------------------------------------------------- periods

# The quick ranges offered above the dashboard. "Total" means everything
# loaded; "Custom" is handled by the date pickers rather than here.
PERIODS = ("Day", "Week", "Month", "Total", "Custom")

PERIOD_DAYS = {"Day": 1, "Week": 7, "Month": 30}


def period_slice(
    frame: pd.DataFrame,
    period: str,
    today: Optional[date] = None,
) -> pd.DataFrame:
    """The rows inside one of the quick ranges.

    The windows are ROLLING, not calendar: "Week" means the last 7 days
    including today, not Monday-to-now. A calendar week would make Monday
    morning show almost nothing and Sunday night show a full week, which
    reads as a usage collapse every seven days rather than as an artefact of
    the calendar.

    "Month" here is the last 30 days for the same reason. The month-to-date
    figure the hero shows is a genuine calendar month — those are different
    questions, and month_slice() answers the other one.
    """
    if period == "Total" or frame.empty:
        return frame

    days = PERIOD_DAYS.get(period)
    if days is None:                       # "Custom", or anything unexpected
        return frame

    today = today or date.today()
    # Inclusive of today, so Day = today only, Week = today and the 6 before.
    start = pd.Timestamp(today) - pd.Timedelta(days=days - 1)
    return frame[frame["date"] >= start]


@dataclass(frozen=True)
class HeadlineStats:
    """The four tiles across the top."""

    last_7_cost: float
    last_30_cost: float
    daily_average: float
    month_to_date_cost: float
    active_days: int


def headline_stats(
    frame: pd.DataFrame,
    today: Optional[date] = None,
) -> HeadlineStats:
    """Recent spend at three zoom levels, plus the calendar month.

    The average is per ACTIVE day, not per calendar day. Dividing by calendar
    days would quietly answer a different question -- "what does an average
    day cost, including the ones I didn't work?" -- and produce a number that
    falls every weekend without any change in behaviour.
    """
    today = today or date.today()
    if frame.empty:
        return HeadlineStats(0.0, 0.0, 0.0, 0.0, 0)

    def spend(days: int) -> float:
        start = pd.Timestamp(today) - pd.Timedelta(days=days - 1)
        return float(frame.loc[frame["date"] >= start, "estimated_cost"].sum())

    active_days = int(frame["date"].nunique())
    total = float(frame["estimated_cost"].sum())

    return HeadlineStats(
        last_7_cost=spend(7),
        last_30_cost=spend(30),
        daily_average=total / active_days if active_days else 0.0,
        month_to_date_cost=float(month_slice(frame, today)["estimated_cost"].sum()),
        active_days=active_days,
    )


# ---------------------------------------------------------------- breakdowns

# The columns the breakdown tables show, in order. `reasoning` sits apart from
# the rest on purpose: it is already inside `output`, so it is shown for
# interest and never added in. See burnrate.schema.REPORTED_COLUMNS.
BREAKDOWN_COLUMNS = [
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "sessions",
    "estimated_cost",
]


def _breakdown(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    """Shared body of the two breakdown tables.

    `cached` folds the three cache columns together, which is safe HERE and
    nowhere else: this table reports counts, and the cost column beside it was
    computed before the fold, from the three columns priced separately. The
    chart rule about never merging cache kinds is about PRICING them, and no
    pricing happens here.
    """
    if frame.empty:
        return pd.DataFrame(columns=[by] + BREAKDOWN_COLUMNS)

    working = frame.copy()
    working["cached_tokens"] = (
        working["cache_write_5m_tokens"]
        + working["cache_write_1h_tokens"]
        + working["cache_read_tokens"]
    )
    working["total_tokens"] = schema.total_tokens(frame)

    grouped = (
        working.groupby(by, observed=True)
        .agg(
            total_tokens=("total_tokens", "sum"),
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            cached_tokens=("cached_tokens", "sum"),
            reasoning_tokens=("reasoning_tokens", "sum"),
            sessions=("session_id", "nunique"),
            estimated_cost=("estimated_cost", "sum"),
        )
        .reset_index()
    )
    return grouped


def daily_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per day, newest first."""
    return _breakdown(frame, "date").sort_values("date", ascending=False)


def project_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per project, most expensive first."""
    return _breakdown(frame, "project").sort_values("estimated_cost", ascending=False)


def by_product(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per tool, for the summary cards.

    `share` is share of DOLLARS, not of tokens. Tokens would rank the tools by
    how much context they re-read, which is close to meaningless as a measure
    of how much a tool is being used.
    """
    if frame.empty:
        return pd.DataFrame(columns=["product", "estimated_cost", "share", "models", "calls"])

    grouped = (
        frame.groupby("product", observed=True)
        .agg(
            estimated_cost=("estimated_cost", "sum"),
            models=("model", "nunique"),
            calls=("model", "size"),
            sessions=("session_id", "nunique"),
        )
        .reset_index()
        .sort_values("estimated_cost", ascending=False)
    )
    total = grouped["estimated_cost"].sum()
    grouped["share"] = grouped["estimated_cost"] / total if total else 0.0
    return grouped
