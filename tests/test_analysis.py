"""Tests for the dashboard calculations."""

from datetime import date

import pandas as pd
import pytest

from burnrate import analysis, insights, schema
from burnrate.adapters import fake
from burnrate.pricing import add_estimated_cost


@pytest.fixture
def frame():
    return add_estimated_cost(fake.generate(days=60, end=date(2026, 10, 5), seed=1))


def test_daily_totals_fills_in_quiet_days(frame):
    daily = analysis.daily_totals(frame)
    span = (daily["date"].max() - daily["date"].min()).days + 1

    assert len(daily) == span, "every calendar day in the range should appear"
    assert (daily["estimated_cost"] == 0).any(), "fake data has quiet days"
    assert daily["estimated_cost"].sum() == pytest.approx(frame["estimated_cost"].sum())


def test_totals_survive_every_grouping(frame):
    """However you slice it, the money has to add up to the same number."""
    total = frame["estimated_cost"].sum()

    assert analysis.by_column(frame, "model")["estimated_cost"].sum() == pytest.approx(total)
    assert analysis.by_column(frame, "project")["estimated_cost"].sum() == pytest.approx(total)
    assert analysis.by_session(frame)["estimated_cost"].sum() == pytest.approx(total)
    assert analysis.token_mix(frame)["cost"].sum() == pytest.approx(total)


def test_token_mix_separates_tokens_from_dollars(frame):
    """The whole point: cache reads are most of the tokens, not most of the cost."""
    mix = analysis.token_mix(frame)
    cache_read = mix[mix["kind"] == "Cache read"].iloc[0]

    assert cache_read["token_share"] > 0.80
    assert cache_read["cost_share"] < cache_read["token_share"]


def test_forecast_scales_the_daily_rate_to_the_full_month():
    """A fixed, hand-checkable case: $10 over 10 days in a 31-day month."""
    rows = []
    for day in range(1, 11):
        rows.append(
            {
                "date": date(2026, 10, day),
                "provider": "anthropic",
                "product": "claude_code",
                "project": "p",
                "model": "claude-opus-5",
                "session_id": f"s{day}",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_5m_tokens": 0,
                "cache_write_1h_tokens": 0,
                "cache_read_tokens": 0,
                "reasoning_tokens": 0,
                "estimated_cost": 1.0,
                "source": "test",
                "is_estimate": True,
            }
        )
    frame = schema.coerce(pd.DataFrame(rows))

    forecast = analysis.forecast_month_end(frame, today=date(2026, 10, 10))

    assert forecast.month_to_date == pytest.approx(10.0)
    assert forecast.days_elapsed == 10
    assert forecast.days_in_month == 31
    assert forecast.daily_average == pytest.approx(1.0)
    assert forecast.projected_total == pytest.approx(31.0)
    assert forecast.is_reliable


def test_forecast_admits_when_it_has_too_little_data():
    """Two days of data cannot forecast a month, and shouldn't pretend to."""
    frame = add_estimated_cost(fake.generate(days=2, end=date(2026, 10, 2), seed=3))
    forecast = analysis.forecast_month_end(frame, today=date(2026, 10, 2))
    assert not forecast.is_reliable


def test_month_slice_excludes_last_month(frame):
    this_month = analysis.month_slice(frame, today=date(2026, 10, 5))
    assert (this_month["date"] >= pd.Timestamp(2026, 10, 1)).all()
    assert len(this_month) < len(frame)


def test_cost_on_another_model_is_a_real_comparison(frame):
    """Sonnet 5 is cheaper than Opus 5, so the same tokens must cost less."""
    on_sonnet = analysis.cost_if_all_on_model(frame, "claude-sonnet-5")
    on_opus = analysis.cost_if_all_on_model(frame, "claude-opus-5")

    assert on_sonnet < on_opus
    assert analysis.cost_if_all_on_model(frame, "no-such-model") != \
        analysis.cost_if_all_on_model(frame, "no-such-model")  # NaN != NaN


def test_empty_data_does_not_crash_anything():
    empty = schema.empty_frame()

    assert analysis.daily_totals(empty).empty
    assert analysis.by_column(empty, "model").empty
    assert analysis.by_session(empty).empty
    assert analysis.token_mix(empty).empty
    assert insights.savings_suggestions(empty) == []
    assert analysis.forecast_month_end(empty).projected_total == 0.0


def test_suggestions_all_carry_a_number(frame):
    found = insights.savings_suggestions(frame)

    assert found, "fake data is cache-heavy, so at least one rule should fire"
    for suggestion in found:
        assert suggestion.title and suggestion.detail
        assert suggestion.dollars is None or suggestion.dollars > 0
    # Sorted by size of the opportunity.
    amounts = [s.dollars for s in found if s.dollars is not None]
    assert amounts == sorted(amounts, reverse=True)


def test_cache_read_suggestion_fires_on_cache_heavy_data(frame):
    titles = [s.title for s in insights.savings_suggestions(frame)]
    assert any("Re-reading context" in t for t in titles)


def test_cheaper_model_suggestion_ignores_other_vendors():
    """Pricing OpenAI tokens at Sonnet rates is a number, not an action.

    Codex cannot run a Claude model, so the comparison has to be restricted to
    the usage that could actually move. If it ever stops being restricted, the
    suggested saving silently inflates by whatever Codex cost.
    """
    rows = add_estimated_cost(fake.generate(days=10, seed=3))

    # One very expensive OpenAI row, which must not enter the comparison.
    codex_row = rows.iloc[[0]].copy()
    codex_row["provider"] = "openai"
    codex_row["product"] = "codex"
    codex_row["source"] = "codex"
    codex_row["model"] = "gpt-6.1-sol"
    codex_row["input_tokens"] = 50_000_000
    codex_row["cache_read_tokens"] = 0
    codex_row["cache_write_5m_tokens"] = 0
    codex_row["cache_write_1h_tokens"] = 0
    codex_row = add_estimated_cost(schema.coerce(codex_row))

    claude_only = [
        s for s in insights.savings_suggestions(rows)
        if s.title.startswith("The same tokens on Sonnet 5")
    ]
    mixed = [
        s for s in insights.savings_suggestions(
            schema.coerce(pd.concat([rows, codex_row], ignore_index=True))
        )
        if s.title.startswith("The same tokens on Sonnet 5")
    ]

    assert claude_only and mixed, "the suggestion should appear in both cases"
    assert mixed[0].dollars == pytest.approx(claude_only[0].dollars), (
        "adding $100 of Codex usage must not change the Claude-only saving"
    )
    assert "Codex cannot run a Claude model" in mixed[0].caveat


def test_cheaper_model_suggestion_is_absent_with_no_claude_usage():
    rows = add_estimated_cost(fake.generate(days=5, seed=4))
    rows["provider"] = "openai"
    rows["model"] = "gpt-6.1-sol"
    rows = add_estimated_cost(schema.coerce(rows))

    titles = [s.title for s in insights.savings_suggestions(rows)]
    assert not any(t.startswith("The same tokens on Sonnet 5") for t in titles)


# ---- reasoning tokens: reported, never totalled -------------------------


def test_reasoning_is_never_added_to_a_total():
    """The whole reason reasoning_tokens sits outside TOKEN_COLUMNS.

    Thinking tokens are already inside output_tokens. If they ever leaked
    into the summed columns, every total on the dashboard would inflate and
    nothing would look obviously wrong.
    """
    assert "reasoning_tokens" not in schema.TOKEN_COLUMNS
    assert "reasoning_tokens" in schema.REPORTED_COLUMNS

    rows = add_estimated_cost(fake.generate(days=5, seed=9))
    rows.loc[:, "reasoning_tokens"] = rows["output_tokens"]  # the extreme case

    before = schema.total_tokens(rows).sum()
    rows.loc[:, "reasoning_tokens"] = 0
    after = schema.total_tokens(rows).sum()

    assert before == after, "total_tokens must not depend on reasoning_tokens"


def test_reasoning_larger_than_output_is_rejected():
    """An adapter reading the wrong field must fail loudly, not silently."""
    rows = add_estimated_cost(fake.generate(days=3, seed=10))
    rows.loc[0, "reasoning_tokens"] = int(rows.loc[0, "output_tokens"]) + 1

    with pytest.raises(schema.SchemaError) as caught:
        schema.validate(rows)
    assert "reasoning" in str(caught.value)


def test_breakdown_cached_column_is_the_three_cache_kinds():
    rows = add_estimated_cost(fake.generate(days=4, seed=11))
    table = analysis.daily_breakdown(rows)

    expected = int(
        rows["cache_write_5m_tokens"].sum()
        + rows["cache_write_1h_tokens"].sum()
        + rows["cache_read_tokens"].sum()
    )
    assert int(table["cached_tokens"].sum()) == expected
    # And the totals still agree with the schema's own sum.
    assert int(table["total_tokens"].sum()) == int(schema.total_tokens(rows).sum())


def test_breakdowns_preserve_the_money():
    """However the table is grouped, the dollars must still add up."""
    rows = add_estimated_cost(fake.generate(days=12, seed=12))
    total = rows["estimated_cost"].sum()

    for table in (analysis.daily_breakdown(rows), analysis.project_breakdown(rows)):
        assert table["estimated_cost"].sum() == pytest.approx(total)


# ---- period slicing ----------------------------------------------------


def test_period_windows_are_rolling_and_inclusive_of_today():
    """Day means today; Week means today plus the six before it.

    Rolling rather than calendar, so a Monday morning doesn't read as a
    collapse in usage just because a calendar week restarted.
    """
    today = date(2026, 10, 19)
    rows = add_estimated_cost(fake.generate(days=60, end=today, seed=13))

    day = analysis.period_slice(rows, "Day", today)
    week = analysis.period_slice(rows, "Week", today)
    month = analysis.period_slice(rows, "Month", today)
    total = analysis.period_slice(rows, "Total", today)

    assert day["date"].nunique() <= 1
    assert week["date"].min() >= pd.Timestamp(today) - pd.Timedelta(days=6)
    assert month["date"].min() >= pd.Timestamp(today) - pd.Timedelta(days=29)
    assert len(total) == len(rows)
    # Each window contains the next one down.
    assert len(day) <= len(week) <= len(month) <= len(total)


def test_custom_period_does_not_slice():
    """'Custom' is handled by the date pickers, not by period_slice."""
    rows = add_estimated_cost(fake.generate(days=20, seed=14))
    assert len(analysis.period_slice(rows, "Custom")) == len(rows)


# ---- headline stats ----------------------------------------------------


def test_daily_average_uses_active_days_not_calendar_days():
    """Dividing by calendar days answers a different question.

    It would make the average fall every weekend without any change in how
    the tools were used.
    """
    today = date(2026, 10, 19)
    rows = add_estimated_cost(fake.generate(days=40, end=today, seed=15))
    stats = analysis.headline_stats(rows, today)

    active = rows["date"].nunique()
    assert stats.active_days == active
    assert stats.daily_average == pytest.approx(
        rows["estimated_cost"].sum() / active
    )
    # Fewer active days than calendar days means a higher average than the
    # naive calculation would give.
    assert stats.daily_average > rows["estimated_cost"].sum() / 40


def test_headline_windows_nest():
    today = date(2026, 10, 19)
    rows = add_estimated_cost(fake.generate(days=60, end=today, seed=16))
    stats = analysis.headline_stats(rows, today)
    assert stats.last_7_cost <= stats.last_30_cost


def test_headline_stats_on_an_empty_frame():
    stats = analysis.headline_stats(schema.empty_frame())
    assert stats.active_days == 0
    assert stats.daily_average == 0.0


def test_product_shares_sum_to_one():
    rows = add_estimated_cost(fake.generate(days=10, seed=17))
    codex = rows.iloc[[0]].copy()
    codex["product"] = "codex"
    codex["provider"] = "openai"
    codex["model"] = "gpt-6.1-sol"
    combined = add_estimated_cost(
        schema.coerce(pd.concat([rows, codex], ignore_index=True))
    )

    products = analysis.by_product(combined)
    assert len(products) == 2
    assert products["share"].sum() == pytest.approx(1.0)


# ---- recommendations ---------------------------------------------------


def test_recommendations_are_capped_and_grounded():
    """Short, actionable, and never more than asked for."""
    rows = add_estimated_cost(fake.generate(days=40, seed=21))
    recs = insights.recommendations(rows)

    assert 0 < len(recs) <= 3
    for rec in recs:
        assert rec.action and rec.because
        # An imperative, not a paragraph.
        assert len(rec.action) < 60
        # Every one cites a figure from the data rather than offering an opinion.
        assert any(ch.isdigit() for ch in rec.because)


def test_recommendation_limit_is_respected():
    rows = add_estimated_cost(fake.generate(days=40, seed=22))
    assert len(insights.recommendations(rows, limit=1)) == 1
    assert len(insights.recommendations(rows, limit=2)) == 2


def test_no_recommendations_for_an_empty_frame():
    assert insights.recommendations(schema.empty_frame()) == []


def test_a_rule_stays_quiet_when_it_does_not_apply():
    """Silence is the right answer when the data does not support a rule.

    Filler advice with no number behind it is exactly what the rest of this
    project avoids, so a rule that cannot fire must produce nothing rather
    than something generic.
    """
    rows = add_estimated_cost(fake.generate(days=20, seed=23))

    # No cache reads at all -> the "fresh session" rule has nothing to say.
    no_cache = rows.copy()
    no_cache["cache_read_tokens"] = 0
    no_cache = add_estimated_cost(schema.coerce(no_cache))
    actions = [r.action for r in insights.recommendations(no_cache)]
    assert not any("fresh session" in a for a in actions)

    # No thinking tokens -> the reasoning rule has nothing to say.
    no_reasoning = rows.copy()
    no_reasoning["reasoning_tokens"] = 0
    no_reasoning = add_estimated_cost(schema.coerce(no_reasoning))
    actions = [r.action for r in insights.recommendations(no_reasoning)]
    assert not any("reasoning" in a.lower() for a in actions)


def test_cheaper_model_recommendation_ignores_other_vendors():
    """Same scoping as the long-form suggestion: Codex cannot run Claude."""
    rows = add_estimated_cost(fake.generate(days=10, seed=24))
    rows["provider"] = "openai"
    rows["model"] = "gpt-6.1-sol"
    openai_only = add_estimated_cost(schema.coerce(rows))

    actions = [r.action for r in insights.recommendations(openai_only)]
    assert not any("smaller model" in a for a in actions)


def test_recommendations_agree_with_the_long_form_suggestions():
    """The two engines read the same numbers, so they must not contradict.

    If the long-form rule says long sessions cost more, the short rule has to
    agree — otherwise the page would argue with itself.
    """
    rows = add_estimated_cost(fake.generate(days=40, seed=25))

    long_form = [s.title for s in insights.savings_suggestions(rows)]
    short_form = [r.action for r in insights.recommendations(rows, limit=4)]

    said_long_sessions = any("longer sessions" in t for t in long_form)
    recs_long_sessions = any("Break long sessions" in a for a in short_form)
    assert said_long_sessions == recs_long_sessions
