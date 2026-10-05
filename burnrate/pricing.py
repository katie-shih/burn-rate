"""The cost formula.

Prices are quoted per MILLION tokens. For one row of usage:

    input cost       = input_tokens       / 1e6 * input_price
    output cost      = output_tokens      / 1e6 * output_price
    cache write cost = cache_write_tokens / 1e6 * input_price * 1.25
    cache read cost  = cache_read_tokens  / 1e6 * input_price * 0.10
    total            = the four added together

That is the whole thing. Everything else in this file is bookkeeping around it.
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
    """The four pieces of a cost, kept separate so the math can be checked."""

    input_cost: float
    output_cost: float
    cache_write_cost: float
    cache_read_cost: float

    @property
    def total(self) -> float:
        return (
            self.input_cost
            + self.output_cost
            + self.cache_write_cost
            + self.cache_read_cost
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "cache_write_cost": self.cache_write_cost,
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
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        long_cache: bool = False,
    ) -> CostBreakdown:
        """Cost of one row of usage, split into its four pieces.

        Set long_cache=True if the cache writes used the 1-hour cache, which
        costs 2.0x input instead of 1.25x. We default to False because Claude
        Code uses the 5-minute cache. (This is one of the things to confirm
        against real logs — see CLAUDE.md.)
        """
        if model not in self._models:
            raise UnknownModelError(
                "No price for model %r. Known models: %s. "
                "Add it to prices.yaml or run scripts/refresh_prices.py."
                % (model, ", ".join(self.known_models) or "none")
            )

        entry = self._models[model]
        input_price = float(entry["input_per_mtok"])
        output_price = float(entry["output_per_mtok"])

        write_multiplier = (
            self.cache_write_1h_multiplier if long_cache else self.cache_write_multiplier
        )

        # A few models price cache reads as a flat rate rather than as a
        # multiple of the input price. prices.yaml can say so per model.
        if "cache_read_per_mtok" in entry:
            cache_read_price = float(entry["cache_read_per_mtok"])
        else:
            cache_read_price = input_price * self.cache_read_multiplier

        return CostBreakdown(
            input_cost=input_tokens / TOKENS_PER_MILLION * input_price,
            output_cost=output_tokens / TOKENS_PER_MILLION * output_price,
            cache_write_cost=(
                cache_write_tokens / TOKENS_PER_MILLION * input_price * write_multiplier
            ),
            cache_read_cost=cache_read_tokens / TOKENS_PER_MILLION * cache_read_price,
        )

    def cost(self, model: str, **tokens) -> float:
        """Just the total, when you don't need the pieces."""
        return self.breakdown(model, **tokens).total


def add_estimated_cost(
    frame: pd.DataFrame,
    prices: Optional[PriceBook] = None,
    long_cache: bool = False,
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
                cache_write_tokens=int(row["cache_write_tokens"]),
                cache_read_tokens=int(row["cache_read_tokens"]),
                long_cache=long_cache,
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


def breakdown_frame(
    frame: pd.DataFrame,
    prices: Optional[PriceBook] = None,
    long_cache: bool = False,
) -> pd.DataFrame:
    """The same table plus one column per cost piece.

    This is what the accuracy check prints, so the four numbers can be compared
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
                cache_write_tokens=int(row["cache_write_tokens"]),
                cache_read_tokens=int(row["cache_read_tokens"]),
                long_cache=long_cache,
            ).as_dict()
        except UnknownModelError:
            nan = float("nan")
            parts = {
                "input_cost": nan,
                "output_cost": nan,
                "cache_write_cost": nan,
                "cache_read_cost": nan,
                "total_cost": nan,
            }
        pieces.append(parts)

    return pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame(pieces)], axis=1
    )
