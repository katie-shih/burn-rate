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


# ---------------------------------------------------------------- recommendations


@dataclass
class Recommendation:
    """One short, actionable thing to try, and the number behind it."""

    action: str      # the imperative, in a handful of words
    because: str     # one clause citing a figure from this user's own data


def recommendations(frame: pd.DataFrame, limit: int = 3) -> List[Recommendation]:
    """Up to `limit` short ways to use fewer tokens.

    This is the action-shaped sibling of savings_suggestions() above. Those
    explain where the money went and show their arithmetic at length; these
    say what to do about it in one line each. They read the same numbers from
    burnrate.analysis, so the two cannot disagree about the facts.

    THE ORDER IS FIXED, not sorted by a dollar figure, and that is deliberate.
    Only some of these have a saving that can honestly be computed: the
    cheaper-model one does, but "cache reads are 98% of your tokens" is an
    amount AT STAKE, not an amount recoverable — you cannot stop a model
    re-reading its own context. Sorting the two together would imply a
    measured saving where there is none. The order below reflects how much
    each typically moves the number, and the dashboard says so rather than
    dressing it up as a calculation.

    A rule that does not apply to this data stays silent, so a quiet month
    produces fewer recommendations rather than filler.
    """
    if frame.empty:
        return []

    rules = (
        _rec_fresh_sessions,
        _rec_split_long_sessions,
        _rec_cheaper_model,
        _rec_trim_reasoning,
    )
    found = [rule(frame) for rule in rules]
    return [rec for rec in found if rec is not None][:limit]


def _rec_fresh_sessions(frame: pd.DataFrame) -> Optional[Recommendation]:
    """Every call re-reads the whole conversation, so context length compounds."""
    mix = analysis.token_mix(frame)
    row = mix[mix["kind"] == "Cache read"]
    if row.empty:
        return None

    share = float(row["token_share"].iloc[0])
    if share < 0.5:
        return None

    return Recommendation(
        action="Start a fresh session when you switch tasks",
        because=(
            f"Cache reads are {share:.0%} of your tokens — every message re-reads "
            "the whole conversation, so a long session keeps paying for its own history."
        ),
    )


def _rec_split_long_sessions(frame: pd.DataFrame) -> Optional[Recommendation]:
    """Reuses the same comparison as the long-session suggestion above."""
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

    return Recommendation(
        action="Break long sessions into shorter ones",
        because=(
            f"Your longer sessions cost {long_rate / short_rate:.1f}x more per "
            "message than your shorter ones."
        ),
    )


def _rec_cheaper_model(frame: pd.DataFrame) -> Optional[Recommendation]:
    """Anthropic rows only — Codex cannot run a Claude model."""
    claude_only = frame[frame["provider"] == "anthropic"]
    if claude_only.empty:
        return None

    actual = float(claude_only["estimated_cost"].sum())
    models_used = set(claude_only["model"].dropna().astype(str))
    if actual <= 0 or models_used == {"claude-sonnet-5"} or not models_used:
        return None

    alternative = analysis.cost_if_all_on_model(claude_only, "claude-sonnet-5")
    if pd.isna(alternative) or alternative >= actual:
        return None

    return Recommendation(
        action="Use a smaller model for routine work",
        because=(
            f"The same Claude tokens on Sonnet 5 would have cost "
            f"${actual - alternative:,.2f} less."
        ),
    )


def _rec_trim_reasoning(frame: pd.DataFrame) -> Optional[Recommendation]:
    """Thinking tokens are billed as output, so they are not free."""
    output = float(frame["output_tokens"].sum())
    reasoning = float(frame["reasoning_tokens"].sum())
    if output <= 0:
        return None

    share = reasoning / output
    if share < 0.2:
        return None

    return Recommendation(
        action="Lower the reasoning effort on simple tasks",
        because=(
            f"Thinking is {share:.0%} of your output tokens, and it is billed "
            "at the same rate as the reply."
        ),
    )
