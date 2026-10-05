"""Fake usage data, for building the dashboard without touching real logs.

One row = one API call, which is the same grain the real Claude Code adapter
will produce. The numbers are shaped like real Claude Code usage: tiny fresh
input, big cache reads, moderate cache writes.

    from burnrate.adapters import fake
    df = fake.generate()
"""

import random
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from burnrate import schema

PROJECTS = ["burn-rate", "mba-advisor", "personal-site"]

# model -> how often it shows up, roughly
MODEL_MIX = [
    ("claude-opus-5", 0.55),
    ("claude-sonnet-5", 0.35),
    ("claude-haiku-4-5", 0.10),
]


def generate(
    days: int = 60,
    end: Optional[date] = None,
    seed: int = 20261005,
) -> pd.DataFrame:
    """Make a table of fake usage ending today (or at `end`).

    Deterministic: the same seed always gives the same data, so tests and
    screenshots stay stable.
    """
    rng = random.Random(seed)
    end = end or date.today()
    models = [name for name, _ in MODEL_MIX]
    weights = [weight for _, weight in MODEL_MIX]

    rows: List[dict] = []

    for day_offset in range(days):
        day = end - timedelta(days=days - 1 - day_offset)

        # Weekends are quieter, and some days have no usage at all.
        is_weekend = day.weekday() >= 5
        if rng.random() < (0.45 if is_weekend else 0.12):
            continue

        for _ in range(rng.randint(1, 2 if is_weekend else 4)):
            rows.extend(_fake_session(rng, day, models, weights))

    frame = pd.DataFrame(rows)
    if frame.empty:
        return schema.empty_frame()
    return schema.coerce(frame)


def _fake_session(rng: random.Random, day: date, models, weights) -> List[dict]:
    """One working session: a run of API calls that share a session_id."""
    project = rng.choice(PROJECTS)
    model = rng.choices(models, weights=weights, k=1)[0]
    session_id = "fake-%s-%04d" % (day.isoformat(), rng.randint(0, 9999))

    calls = rng.randint(4, 60)
    rows = []

    # The conversation grows as the session goes on, so cache reads grow too.
    context = rng.randint(8_000, 25_000)

    for _ in range(calls):
        context += rng.randint(500, 4_000)

        rows.append(
            {
                "date": day,
                "provider": "anthropic",
                "product": "claude_code",
                "project": project,
                "model": model,
                "session_id": session_id,
                # Fresh input is small — almost everything is served from cache.
                "input_tokens": rng.randint(4, 120),
                "output_tokens": rng.randint(60, 2_500),
                "cache_write_tokens": rng.randint(400, 6_000),
                "cache_read_tokens": int(context * rng.uniform(0.85, 1.0)),
                "estimated_cost": None,  # filled in by burnrate.pricing
                "source": "fake",
                "is_estimate": True,
            }
        )

    return rows
