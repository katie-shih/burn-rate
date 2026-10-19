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


@pytest.mark.parametrize(
    "model, read_price, share_of_input",
    [
        ("claude-opus-5", 0.50, 0.100),
        ("claude-sonnet-5", 0.20, 0.100),
        ("claude-opus-5-5", 0.20, 0.050),
        ("claude-sonnet-5-5", 0.10, 0.050),
        ("claude-fable-5-1", 0.25, 0.025),
    ],
)
def test_cache_read_price_is_read_per_model_not_assumed(
    prices: PriceBook, model: str, read_price: float, share_of_input: float
):
    """Cache reads are NOT a fixed 10% of input, and assuming so overcharges.

    This is the regression test for a real bug. prices.yaml used to store only
    input and output prices and derive cache reads as 10% of input. That is
    right for most models but wrong for three: the two "5-5" models charge 5%
    and Fable 5.1 charges 2.5%. claude-sonnet-5-5 had no hand-written override,
    so it was billed at $0.20/MTok against a real price of $0.10 -- double.

    Cache reads are the largest token line in Claude Code by a wide margin
    (over 90% of tokens in a typical session), so an error here moves the
    headline number more than an error anywhere else.
    """
    parts = prices.breakdown(model, cache_read_tokens=1_000_000)
    assert parts.cache_read_cost == pytest.approx(read_price)

    listed = prices.prices_for(model)
    ratio = listed["cache_read_per_mtok"] / listed["input_per_mtok"]
    assert ratio == pytest.approx(share_of_input)


def test_cache_write_multipliers_do_hold_for_every_claude_model(prices: PriceBook):
    """The counterpart to the test above: writes really are a fixed multiple.

    Every Claude model charges exactly 1.25x input for a 5-minute cache write
    and 2.00x for a 1-hour one. That is why the old multiplier approach looked
    sound -- it was true for four of the five prices. This test pins the
    asymmetry down: if a future model ever breaks it, we find out here rather
    than in a dollar figure nobody can check.

    Anthropic only. The 5m/1h split is Anthropic's design, and no OpenAI model
    publishes a 1-hour price at all.
    """
    claude = [m for m in prices.known_models if prices.provider_of(m) == "anthropic"]
    assert claude, "prices.yaml has no Anthropic models"

    for model in claude:
        p = prices.prices_for(model)
        assert p["cache_write_5m_per_mtok"] == pytest.approx(
            p["input_per_mtok"] * 1.25
        ), f"{model} breaks the 1.25x rule for 5-minute cache writes"
        assert p["cache_write_1h_per_mtok"] == pytest.approx(
            p["input_per_mtok"] * 2.00
        ), f"{model} breaks the 2.00x rule for 1-hour cache writes"


def test_every_model_has_input_and_output_prices(prices: PriceBook):
    """A half-priced model would quietly understate cost, so there are none.

    Only input and output are universal. The cache prices are per-vendor: see
    PriceBook.prices_for for why an absent one is a fact rather than a gap.
    """
    assert prices.known_models, "prices.yaml has no models at all"
    for model in prices.known_models:
        p = prices.prices_for(model)
        assert p["input_per_mtok"] > 0
        assert p["output_per_mtok"] > 0


def test_no_openai_model_has_a_one_hour_cache_price(prices: PriceBook):
    """The fact that forced the schema decision, pinned down.

    If OpenAI ever adds a 1-hour cache tier, this test fails and we should
    start reading it rather than leaving the column at zero for Codex rows.
    """
    openai = [m for m in prices.known_models if prices.provider_of(m) == "openai"]
    assert openai, "prices.yaml has no OpenAI models"

    with_1h = [
        m for m in openai if "cache_write_1h_per_mtok" in prices.prices_for(m)
    ]
    assert with_1h == []


def test_a_codex_shaped_row_prices_without_a_one_hour_price(prices: PriceBook):
    """The whole point of requiring prices only where tokens are.

    Codex rows always have 0 in the 1-hour column, so a model with no 1-hour
    price must still price cleanly.
    """
    parts = prices.breakdown(
        "gpt-6.1-sol",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_1h_tokens=0,
    )
    assert parts.cache_write_1h_cost == 0.0
    assert parts.total > 0


def test_tokens_in_a_column_with_no_price_are_an_error(prices: PriceBook):
    """...and the loophole stays shut.

    The moment a Codex row actually HAS 1-hour cache tokens, we must refuse to
    price it rather than silently charging those tokens nothing.
    """
    with pytest.raises(UnknownModelError) as caught:
        prices.cost("gpt-6.1-sol", cache_write_1h_tokens=1_000)

    message = str(caught.value)
    assert "cache_write_1h_per_mtok" in message
    assert "cache_write_1h_tokens" in message


def test_a_model_missing_a_price_is_an_error_not_a_guess():
    """Better a visible gap than a confident-looking invented number."""
    book = PriceBook(
        {"models": {"claude-half-priced": {"input_per_mtok": 5.0,
                                           "output_per_mtok": 25.0}}}
    )
    with pytest.raises(UnknownModelError) as caught:
        book.cost("claude-half-priced", cache_read_tokens=1_000_000)

    message = str(caught.value)
    assert "cache_read_per_mtok" in message
    assert "refresh_prices" in message


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
