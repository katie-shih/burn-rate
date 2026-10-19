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
