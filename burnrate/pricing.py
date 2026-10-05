"""The cost formula.

Prices are quoted per MILLION tokens. For one row of usage:

    input cost          = input_tokens          / 1e6 * input_price
    output cost         = output_tokens         / 1e6 * output_price
    cache write (5 min) = cache_write_5m_tokens / 1e6 * input_price * 1.25
    cache write (1 hr)  = cache_write_1h_tokens / 1e6 * input_price * 2.00
    cache read          = cache_read_tokens     / 1e6 * input_price * 0.10
    total               = the five added together

That is the whole thing. Everything else in this file is bookkeeping around it.

The two kinds of cache write are kept apart because they cost 1.25x and 2.00x —
a 60% difference. Claude Code turns out to use the 1-hour cache exclusively, so
guessing "5 minute" would have under-counted a large part of the bill.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd
import yaml

TOKENS_PER_MILLION = 1_000_000

DEFAULT_PRICES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prices.yaml"
)


class UnknownModelError(KeyError):
    """Raised when we have no price for a model."""


@dataclass(frozen=True)
class CostBreakdown:
    """The pieces of a cost, kept separate so the math can be checked by hand."""

    input_cost: float
    output_cost: float
    cache_write_5m_cost: float
    cache_write_1h_cost: float
    cache_read_cost: float

    @property
    def cache_write_cost(self) -> float:
        """Both kinds of cache write, for display."""
        return self.cache_write_5m_cost + self.cache_write_1h_cost

    @property
    def total(self) -> float:
        return (
            self.input_cost
            + self.output_cost
            + self.cache_write_5m_cost
            + self.cache_write_1h_cost
            + self.cache_read_cost
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "cache_write_5m_cost": self.cache_write_5m_cost,
            "cache_write_1h_cost": self.cache_write_1h_cost,
            "cache_read_cost": self.cache_read_cost,
            "total_cost": self.total,
        }


class PriceBook:
    """Prices loaded from prices.yaml, plus the formula that uses them."""

    def __init__(self, data: Dict):
        self._models: Dict[str, Dict] = data.get("models") or {}
        multipliers = data.get("cache_multipliers") or {}
        self.cache_write_multiplier: float = float(multipliers.get("write_5m", 1.25))
        self.cache_write_1h_multiplier: float = float(multipliers.get("write_1h", 2.00))
        self.cache_read_multiplier: float = float(multipliers.get("read", 0.10))
        self.fetched: Optional[str] = data.get("fetched")
        self.source: Optional[str] = data.get("source")

    # ---- loading -------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "PriceBook":
        path = path or DEFAULT_PRICES_PATH
        with open(path, "r", encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle) or {})

    @property
    def known_models(self) -> List[str]:
        return sorted(self._models)

    def knows(self, model: str) -> bool:
        return model in self._models

    # ---- the formula ---------------------------------------------------

    def breakdown(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_5m_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> CostBreakdown:
        """Cost of one row of usage, split into its pieces."""
        if model not in self._models:
            raise UnknownModelError(
                "No price for model %r. Known models: %s. "
                "Add it to prices.yaml or run scripts/refresh_prices.py."
                % (model, ", ".join(self.known_models) or "none")
            )

        entry = self._models[model]
        input_price = float(entry["input_per_mtok"])
        output_price = float(entry["output_per_mtok"])

        # A few models price cache reads as a flat rate rather than as a
        # multiple of the input price. prices.yaml can say so per model.
        if "cache_read_per_mtok" in entry:
            cache_read_price = float(entry["cache_read_per_mtok"])
        else:
            cache_read_price = input_price * self.cache_read_multiplier

        per_million = lambda tokens: tokens / TOKENS_PER_MILLION

        return CostBreakdown(
            input_cost=per_million(input_tokens) * input_price,
            output_cost=per_million(output_tokens) * output_price,
            cache_write_5m_cost=(
                per_million(cache_write_5m_tokens)
                * input_price
                * self.cache_write_multiplier
            ),
            cache_write_1h_cost=(
                per_million(cache_write_1h_tokens)
                * input_price
                * self.cache_write_1h_multiplier
            ),
            cache_read_cost=per_million(cache_read_tokens) * cache_read_price,
        )

    def cost(self, model: str, **tokens) -> float:
        """Just the total, when you don't need the pieces."""
        return self.breakdown(model, **tokens).total


def add_estimated_cost(
    frame: pd.DataFrame,
    prices: Optional[PriceBook] = None,
) -> pd.DataFrame:
    """Fill in the estimated_cost column for a whole table.

    Rows whose model we have no price for get NaN, not zero — an unpriceable
    row is not a free row, and the dashboard says so out loud. Check which
    models were skipped with `unpriced_models(frame)`.
    """
    prices = prices or PriceBook.load()
    frame = frame.copy()

    def cost_of(row) -> float:
        try:
            return prices.breakdown(
                model=row["model"],
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                cache_write_5m_tokens=int(row["cache_write_5m_tokens"]),
                cache_write_1h_tokens=int(row["cache_write_1h_tokens"]),
                cache_read_tokens=int(row["cache_read_tokens"]),
            ).total
        except UnknownModelError:
            return float("nan")

    if frame.empty:
        frame["estimated_cost"] = pd.Series(dtype="float64")
    else:
        frame["estimated_cost"] = frame.apply(cost_of, axis=1).astype("float64")
    return frame


def unpriced_models(frame: pd.DataFrame) -> List[str]:
    """Models in this table that we could not price. Show these to the user."""
    missing = frame.loc[frame["estimated_cost"].isna(), "model"]
    return sorted(set(missing.dropna().astype(str)))


COST_PIECES = [
    "input_cost",
    "output_cost",
    "cache_write_5m_cost",
    "cache_write_1h_cost",
    "cache_read_cost",
    "total_cost",
]


def breakdown_frame(
    frame: pd.DataFrame,
    prices: Optional[PriceBook] = None,
) -> pd.DataFrame:
    """The same table plus one column per cost piece.

    This is what the accuracy check prints, so every number can be compared
    against a hand calculation line by line.
    """
    prices = prices or PriceBook.load()
    pieces = []
    for _, row in frame.iterrows():
        try:
            parts = prices.breakdown(
                model=row["model"],
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                cache_write_5m_tokens=int(row["cache_write_5m_tokens"]),
                cache_write_1h_tokens=int(row["cache_write_1h_tokens"]),
                cache_read_tokens=int(row["cache_read_tokens"]),
            ).as_dict()
        except UnknownModelError:
            parts = {name: float("nan") for name in COST_PIECES}
        pieces.append(parts)

    return pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame(pieces)], axis=1
    )
