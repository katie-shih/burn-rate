"""The cost formula.

Prices are quoted per MILLION tokens. For one row of usage:

    input cost          = input_tokens          / 1e6 * input price
    output cost         = output_tokens         / 1e6 * output price
    cache write (5 min) = cache_write_5m_tokens / 1e6 * 5m cache write price
    cache write (1 hr)  = cache_write_1h_tokens / 1e6 * 1h cache write price
    cache read          = cache_read_tokens     / 1e6 * cache read price
    total               = the five added together

That is the whole thing. Everything else in this file is bookkeeping around it.

All five prices are looked up per model in prices.yaml. Earlier versions stored
only input and output and worked the three cache prices out by multiplying the
input price — 1.25x for a 5-minute cache write, 2.00x for an hour, 0.10x for a
read. The two write multipliers hold for every Claude model, but the read one
does not: claude-opus-5-5 and claude-sonnet-5-5 charge 5% of input, and
claude-fable-5-1 charges 2.5%. Since cache reads are by far the largest token
line in Claude Code, assuming 10% overcharged those models two- and four-fold.
Reading the real price for each model leaves no rule to be wrong about.

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
    """Raised when we cannot price a model.

    Covers both "never heard of it" and "listed, but missing some of its five
    prices". Both mean the same thing downstream: we must not invent a number.
    """


# Every price we know about, and the token column each one applies to.
PRICE_FIELDS = {
    "input_per_mtok": "input_tokens",
    "output_per_mtok": "output_tokens",
    "cache_write_5m_per_mtok": "cache_write_5m_tokens",
    "cache_write_1h_per_mtok": "cache_write_1h_tokens",
    "cache_read_per_mtok": "cache_read_tokens",
}

# Input and output are universal: every model charges for both, so a model
# lacking either is broken rather than merely different.
ALWAYS_REQUIRED = ("input_per_mtok", "output_per_mtok")


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
        self.fetched: Optional[str] = data.get("fetched")
        self.source: Optional[str] = data.get("source")
        self.upstream: Optional[str] = data.get("upstream")

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
        counts = {
            "input_per_mtok": input_tokens,
            "output_per_mtok": output_tokens,
            "cache_write_5m_per_mtok": cache_write_5m_tokens,
            "cache_write_1h_per_mtok": cache_write_1h_tokens,
            "cache_read_per_mtok": cache_read_tokens,
        }
        # A price is only needed where there are tokens to charge for it.
        # See prices_for() for why that is not a loophole.
        price = self.prices_for(
            model, require=[field for field, count in counts.items() if count]
        )
        per_million = lambda tokens: tokens / TOKENS_PER_MILLION

        def cost(field: str) -> float:
            return per_million(counts[field]) * price.get(field, 0.0)

        return CostBreakdown(
            input_cost=cost("input_per_mtok"),
            output_cost=cost("output_per_mtok"),
            cache_write_5m_cost=cost("cache_write_5m_per_mtok"),
            cache_write_1h_cost=cost("cache_write_1h_per_mtok"),
            cache_read_cost=cost("cache_read_per_mtok"),
        )

    def prices_for(self, model: str, require=None) -> Dict[str, float]:
        """The prices a model publishes, erroring if a needed one is absent.

        Not every model has all five prices, and that is a fact about the
        vendors rather than a gap in the data. The 5-minute / 1-hour cache
        split is Anthropic's design: no OpenAI model has a 1-hour cache write
        price, and most have no cache write price at all. Demanding five
        prices from everyone would make the file unable to describe OpenAI.

        So a price is required only where there are tokens to charge for it.
        `require` names the price fields this call actually needs; anything
        absent and unneeded is left out of the returned dict and multiplies
        nothing. That keeps the real guarantee intact -- we still never invent
        a price for tokens we are charging for -- while letting a model say
        "I have no such tier" instead of "I am broken".

        Nothing is derived or defaulted. A quietly wrong price is worse than a
        visible gap, because it still produces a confident-looking dollar
        figure that nothing downstream can tell was invented.
        """
        if model not in self._models:
            raise UnknownModelError(
                "No price for model %r. Known models: %s. "
                "Add it to prices.yaml or run scripts/refresh_prices.py."
                % (model, ", ".join(self.known_models) or "none")
            )

        entry = self._models[model]
        available = {
            field: float(entry[field]) for field in PRICE_FIELDS if field in entry
        }

        needed = set(ALWAYS_REQUIRED) | set(require or ())
        missing = [field for field in PRICE_FIELDS if field in needed and field not in available]
        if missing:
            raise UnknownModelError(
                "prices.yaml has model %r but is missing %s, and this usage "
                "has tokens in %s. Run scripts/refresh_prices.py to rewrite "
                "the file, or add the missing price(s) by hand."
                % (
                    model,
                    ", ".join(missing),
                    ", ".join(PRICE_FIELDS[field] for field in missing),
                )
            )

        return available

    def provider_of(self, model: str) -> Optional[str]:
        """Which vendor sells this model, as recorded in prices.yaml."""
        entry = self._models.get(model) or {}
        provider = entry.get("provider")
        return str(provider) if provider else None

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
