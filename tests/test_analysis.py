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
