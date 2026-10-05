"""Tests for the common data format.

The point of these: if an adapter ever produces something off-format, it should
fail here rather than quietly producing a wrong dashboard.
"""

import pandas as pd
import pytest

from burnrate import schema
from burnrate.adapters import fake


def test_empty_frame_matches_the_schema():
    frame = schema.empty_frame()
    assert list(frame.columns) == schema.COLUMNS
    assert len(frame) == 0
    schema.validate(frame)


def test_fake_data_matches_the_schema():
    frame = fake.generate(days=30)
    schema.validate(frame)
    assert len(frame) > 0
    assert frame["source"].eq("fake").all()
    assert frame["is_estimate"].all()


def test_fake_data_is_deterministic():
    """Same seed, same data — so tests and screenshots stay stable."""
    a = fake.generate(days=14, seed=7)
    b = fake.generate(days=14, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_fake_data_looks_like_real_claude_code_usage():
    """Cache reads should dwarf fresh input. If not, the fake data is misleading."""
    frame = fake.generate(days=30)
    assert frame["cache_read_tokens"].sum() > frame["input_tokens"].sum() * 50


def test_missing_column_is_rejected():
    frame = fake.generate(days=3).drop(columns=["cache_read_tokens"])
    with pytest.raises(schema.SchemaError, match="cache_read_tokens"):
        schema.coerce(frame)


def test_negative_tokens_are_rejected():
    frame = fake.generate(days=3)
    frame.loc[0, "output_tokens"] = -1
    with pytest.raises(schema.SchemaError, match="negative"):
        schema.validate(frame)


def test_missing_token_counts_become_zero_not_nan():
    """A log line with no cache_read used no cache — it isn't an unknown amount."""
    frame = fake.generate(days=3)
    frame.loc[0, "cache_read_tokens"] = None
    coerced = schema.coerce(frame)
    assert coerced.loc[0, "cache_read_tokens"] == 0
