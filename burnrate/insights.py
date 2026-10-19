"""Where the money is going, and where it could be saved.

Each rule looks at the data and either fires with a dollar figure attached or
stays quiet. A suggestion with no number behind it is just an opinion, so every
one of these shows its arithmetic.
"""

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from burnrate import analysis


@dataclass
class Suggestion:
    """One finding, with the number that justifies it."""

    title: str
    detail: str
    dollars: Optional[float] = None
    caveat: Optional[str] = None


def savings_suggestions(frame: pd.DataFrame) -> List[Suggestion]:
    """Every rule that fires, biggest opportunity first."""
    if frame.empty:
        return []

    found = [
        _cache_reads_dominate(frame),
        _long_sessions_cost_more_per_call(frame),
        _cheaper_model_comparison(frame),
        _one_project_dominates(frame),
    ]
    live = [s for s in found if s is not None]
    live.sort(key=lambda s: s.dollars if s.dollars is not None else -1, reverse=True)
    return live


def _cache_reads_dominate(frame: pd.DataFrame) -> Optional[Suggestion]:
    """Cache reads are cheap per token but there are an enormous number of them."""
    mix = analysis.token_mix(frame)
    row = mix[mix["kind"] == "Cache read"]
    if row.empty:
        return None

    cost = float(row["cost"].iloc[0])
    cost_share = float(row["cost_share"].iloc[0])
    token_share = float(row["token_share"].iloc[0])

    if cost_share < 0.30:
        return None

    return Suggestion(
        title="Re-reading context is your biggest single cost",
        detail=(
            f"Cache reads are {token_share:.0%} of your tokens but "
            f"{cost_share:.0%} of your cost — ${cost:,.2f}. Every API call re-reads "
            "the whole conversation so far, so a long session pays for its own "
            "history over and over. Starting a fresh session when you switch "
            "tasks is the single most effective change available."
        ),
        dollars=cost,
        caveat="This is the cost of context, not waste — some of it buys real continuity.",
    )


def _long_sessions_cost_more_per_call(frame: pd.DataFrame) -> Optional[Suggestion]:
    """Long sessions should cost more per call, because context keeps growing."""
    sessions = analysis.by_session(frame)
    if len(sessions) < 4:
        return None

    median_calls = sessions["calls"].median()
    long_ones = sessions[sessions["calls"] > median_calls]
    short_ones = sessions[sessions["calls"] <= median_calls]
    if long_ones.empty or short_ones.empty:
        return None

    long_rate = float(long_ones["cost_per_call"].mean())
    short_rate = float(short_ones["cost_per_call"].mean())
    if short_rate <= 0 or long_rate <= short_rate * 1.2:
        return None

    # What the long sessions would have cost at the short-session rate.
    extra = (long_ones["cost_per_call"] - short_rate) * long_ones["calls"]
    potential = float(extra.sum())

    return Suggestion(
        title="Your longer sessions cost more per message",
        detail=(
            f"Sessions above the median length cost ${long_rate:.3f} per API call, "
            f"against ${short_rate:.3f} for shorter ones — "
            f"{long_rate / short_rate:.1f}x as much. If the long ones had run at the "
            f"short-session rate they would have cost ${potential:,.2f} less."
        ),
        dollars=potential,
        caveat=(
            "Long sessions are not automatically wasteful — splitting work that "
            "genuinely needs shared context would just make you re-explain it."
        ),
    )


def _cheaper_model_comparison(frame: pd.DataFrame) -> Optional[Suggestion]:
    """What the identical tokens would have cost on a cheaper model.

    Anthropic usage only. Pricing OpenAI tokens at Sonnet rates would produce
    a number, but not an action: Codex cannot run a Claude model, so "this
    would have been cheaper on Sonnet" is not advice anyone can take. The
    comparison is restricted to the usage that could actually move.
    """
    claude_only = frame[frame["provider"] == "anthropic"]
    if claude_only.empty:
        return None

    actual = float(claude_only["estimated_cost"].sum())
    if actual <= 0:
        return None

    models_used = set(claude_only["model"].dropna().astype(str))
    if models_used == {"claude-sonnet-5"} or not models_used:
        return None

    alternative = analysis.cost_if_all_on_model(claude_only, "claude-sonnet-5")
    if pd.isna(alternative) or alternative >= actual:
        return None

    saving = actual - alternative
    # Only mention the restriction when there is something to restrict.
    scope = (
        "Your Claude usage cost"
        if len(frame) != len(claude_only)
        else "Your usage cost"
    )
    return Suggestion(
        title="The same tokens on Sonnet 5 would have cost less",
        detail=(
            f"{scope} ${actual:,.2f}. The identical token counts priced at "
            f"Sonnet 5 rates come to ${alternative:,.2f} — a difference of "
            f"${saving:,.2f} ({saving / actual:.0%})."
        ),
        dollars=saving,
        caveat=(
            "A rough comparison, not a promise. A cheaper model may need more "
            "turns to finish the same job, which eats into the saving. "
            "Covers Claude usage only — Codex cannot run a Claude model."
        ),
    )


def _one_project_dominates(frame: pd.DataFrame) -> Optional[Suggestion]:
    """Knowing where the money goes is worth saying even when nothing is wrong."""
    projects = analysis.by_column(frame, "project")
    if len(projects) < 2:
        return None

    total = float(projects["estimated_cost"].sum())
    top = projects.iloc[0]
    share = float(top["estimated_cost"]) / total if total else 0

    if share < 0.40:
        return None

    return Suggestion(
        title=f"'{top['project']}' accounts for most of your spend",
        detail=(
            f"${float(top['estimated_cost']):,.2f} of ${total:,.2f} ({share:.0%}) "
            f"across {int(top['sessions'])} session(s). Worth knowing which work "
            "is actually expensive before trying to trim anything."
        ),
        dollars=float(top["estimated_cost"]),
        caveat="This is where the money went, not necessarily where it was wasted.",
    )
