"""The one table format that every data source converts into.

Every adapter (Claude Code logs, Parley, anything later) produces a pandas
DataFrame with exactly these columns. Nothing downstream needs to know or care
where a row came from.
"""

from typing import List

import pandas as pd

# The columns, in display order. Keep this list and CLAUDE.md in agreement.
COLUMNS: List[str] = [
    "date",                 # day of the usage, local time
    "provider",             # e.g. "anthropic"
    "product",              # e.g. "claude_code", "parley"
    "project",              # which folder/workspace the usage happened in
    "model",                # e.g. "claude-opus-5"
    "session_id",           # groups rows into one working session
    "input_tokens",           # fresh input tokens (NOT the cached ones)
    "output_tokens",          # tokens the model generated
    "cache_write_5m_tokens",  # written into the 5-minute cache  (costs 1.25x input)
    "cache_write_1h_tokens",  # written into the 1-hour cache    (costs 2.00x input)
    "cache_read_tokens",      # read back out of the cache       (costs 0.10x input)
    "reasoning_tokens",       # thinking tokens — ALREADY INSIDE output_tokens
    "estimated_cost",       # dollars, from burnrate.pricing
    "source",               # which adapter produced this row
    "is_estimate",          # True = we computed it, False = a real billed figure
]

# The columns that are BILLED, and therefore the ones that may be added up.
# reasoning_tokens is deliberately not here — see REPORTED_COLUMNS below.
TOKEN_COLUMNS: List[str] = [
    "input_tokens",
    "output_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "cache_read_tokens",
]

# Counts we report but never add to a total, because they are already counted
# somewhere else. reasoning_tokens (Claude Code's `thinking_tokens`, Codex's
# `reasoning_output_tokens`) is a SUBSET of output_tokens: the model thinking
# before it answers. It is interesting — it tells you how much of the output
# you paid for was deliberation rather than reply — but adding it to a total
# bills the same tokens twice.
#
# Keeping it out of TOKEN_COLUMNS is what stops that happening by accident:
# total_tokens() and the cost formula both work off TOKEN_COLUMNS alone.
REPORTED_COLUMNS: List[str] = [
    "reasoning_tokens",
]

ALL_COUNT_COLUMNS: List[str] = TOKEN_COLUMNS + REPORTED_COLUMNS

TEXT_COLUMNS: List[str] = [
    "provider",
    "product",
    "project",
    "model",
    "session_id",
    "source",
]


class SchemaError(ValueError):
    """Raised when a DataFrame does not match the common format."""


def empty_frame() -> pd.DataFrame:
    """An empty table with the right columns and types.

    Useful as a starting point, and as the thing an adapter returns when it
    finds no data at all.
    """
    frame = pd.DataFrame({name: pd.Series(dtype="object") for name in COLUMNS})
    return coerce(frame)


def coerce(frame: pd.DataFrame) -> pd.DataFrame:
    """Force a table into the right column order and types.

    Adapters call this last, so they can build rows loosely and still hand back
    something consistent.
    """
    missing = [name for name in COLUMNS if name not in frame.columns]
    if missing:
        raise SchemaError(
            "These required columns are missing: " + ", ".join(missing)
        )

    frame = frame.loc[:, COLUMNS].copy()

    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()

    for name in ALL_COUNT_COLUMNS:
        # Missing token counts mean zero — a log line that omits "cache_read"
        # used no cache, it didn't use an unknown amount.
        frame[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0).astype("int64")

    # Cost is allowed to be NaN: that means "we could not price this row",
    # which is different from "this row was free". Never fill it with zero.
    frame["estimated_cost"] = pd.to_numeric(frame["estimated_cost"], errors="coerce")

    for name in TEXT_COLUMNS:
        frame[name] = frame[name].astype("string")

    frame["is_estimate"] = frame["is_estimate"].astype("boolean")

    return frame.reset_index(drop=True)


def validate(frame: pd.DataFrame) -> pd.DataFrame:
    """Check a table really is in the common format. Returns it unchanged.

    Call this in tests and after loading, so a broken adapter fails loudly
    and early instead of producing a quietly wrong dashboard.
    """
    if list(frame.columns) != COLUMNS:
        raise SchemaError(
            "Columns are wrong.\n"
            "  expected: " + ", ".join(COLUMNS) + "\n"
            "  found:    " + ", ".join(map(str, frame.columns))
        )

    for name in ALL_COUNT_COLUMNS:
        if (frame[name] < 0).any():
            raise SchemaError("Column '%s' contains negative token counts." % name)

    # reasoning_tokens is a subset of output_tokens. If an adapter ever reports
    # more thinking than output, it has misread the log — and the mistake would
    # be invisible downstream, because reasoning never enters a total.
    too_much = frame["reasoning_tokens"] > frame["output_tokens"]
    if too_much.any():
        raise SchemaError(
            "%d row(s) report more reasoning_tokens than output_tokens. "
            "Reasoning is a subset of output, so this means an adapter is "
            "reading the wrong field." % int(too_much.sum())
        )

    if frame["model"].isna().any():
        raise SchemaError("Some rows have no model, so they cannot be priced.")

    return frame


def total_tokens(frame: pd.DataFrame) -> pd.Series:
    """All token kinds added together.

    Careful with this number: cache reads usually dwarf everything else, so a
    chart of 'total tokens' says much more about caching than about work done.
    """
    return frame[TOKEN_COLUMNS].sum(axis=1)


def cache_write_tokens(frame: pd.DataFrame) -> pd.Series:
    """Both kinds of cache write added together, for display only.

    Never price off this — the two kinds cost 1.25x and 2.00x of input.
    """
    return frame["cache_write_5m_tokens"] + frame["cache_write_1h_tokens"]
