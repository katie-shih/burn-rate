"""Tests for the cost formula.

The first test is the important one: it is the worked example written out in
CLAUDE.md. If that example and this test ever disagree, one of them is wrong
and nothing else in the project can be trusted.
"""

import math

import pytest

from burnrate import schema
from burnrate.adapters import fake
from burnrate.pricing import PriceBook, UnknownModelError, add_estimated_cost, unpriced_models


@pytest.fixture
def prices() -> PriceBook:
    return PriceBook.load()


def test_worked_example_from_claude_md(prices: PriceBook):
    """One Opus 5 session: $5.00 input / $25.00 output per million tokens.

        input        50,000  ->  0.05  x  5.00          = $0.25
        output        8,000  ->  0.008 x 25.00          = $0.20
        write (5m)   40,000  ->  0.04  x  5.00 x 1.25   = $0.25
        write (1h)   80,000  ->  0.08  x  5.00 x 2.00   = $0.80
        cache read  900,000  ->  0.90  x  5.00 x 0.10   = $0.45
                                                  total = $1.95
    """
    parts = prices.breakdown(
        "claude-opus-5",
        input_tokens=50_000,
        output_tokens=8_000,
        cache_write_5m_tokens=40_000,
        cache_write_1h_tokens=80_000,
        cache_read_tokens=900_000,
    )

    assert parts.input_cost == pytest.approx(0.25)
    assert parts.output_cost == pytest.approx(0.20)
    assert parts.cache_write_5m_cost == pytest.approx(0.25)
    assert parts.cache_write_1h_cost == pytest.approx(0.80)
    assert parts.cache_read_cost == pytest.approx(0.45)
    assert parts.total == pytest.approx(1.95)


def test_the_two_cache_writes_are_not_interchangeable(prices: PriceBook):
    """Treating 1-hour cache writes as 5-minute ones understates them by 37.5%.

    Claude Code uses the 1-hour cache exclusively, so this is not hypothetical.
    """
    as_5m = prices.breakdown("claude-opus-5", cache_write_5m_tokens=1_000_000)
    as_1h = prices.breakdown("claude-opus-5", cache_write_1h_tokens=1_000_000)

    assert as_5m.total == pytest.approx(6.25)   # 5.00 x 1.25
    assert as_1h.total == pytest.approx(10.00)  # 5.00 x 2.00
    assert as_5m.total / as_1h.total == pytest.approx(0.625)


def test_cache_reads_are_cheap_relative_to_their_size(prices: PriceBook):
    """The headline insight: cache reads dominate tokens but not dollars."""
    parts = prices.breakdown(
        "claude-opus-5",
        input_tokens=50_000,
        output_tokens=8_000,
        cache_write_1h_tokens=120_000,
        cache_read_tokens=900_000,
    )
    share_of_tokens = 900_000 / (50_000 + 8_000 + 120_000 + 900_000)
    share_of_cost = parts.cache_read_cost / parts.total

    assert share_of_tokens > 0.80
    assert share_of_cost < 0.30


def test_zero_usage_costs_nothing(prices: PriceBook):
    assert prices.cost("claude-sonnet-5") == 0.0


def test_flat_rate_cache_read_override_is_used(prices: PriceBook):
    """Fable 5.1 prices cache reads at a flat $0.25/MTok, not 10% of input."""
    parts = prices.breakdown("claude-fable-5-1", cache_read_tokens=1_000_000)
    assert parts.cache_read_cost == pytest.approx(0.25)


def test_unknown_model_raises_with_a_helpful_message(prices: PriceBook):
    with pytest.raises(UnknownModelError) as caught:
        prices.cost("claude-does-not-exist", input_tokens=1_000)
    assert "prices.yaml" in str(caught.value)


def test_unknown_model_becomes_nan_not_zero():
    """An unpriceable row must never silently look free."""
    frame = fake.generate(days=3)
    frame.loc[0, "model"] = "claude-does-not-exist"

    priced = add_estimated_cost(frame)

    assert math.isnan(priced.loc[0, "estimated_cost"])
    assert unpriced_models(priced) == ["claude-does-not-exist"]
    assert priced["estimated_cost"].notna().sum() == len(frame) - 1


def test_total_cost_is_the_sum_of_its_rows():
    frame = add_estimated_cost(fake.generate(days=10))
    by_session = frame.groupby("session_id")["estimated_cost"].sum()
    assert by_session.sum() == pytest.approx(frame["estimated_cost"].sum())
