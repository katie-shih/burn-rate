"""Tests for the Codex adapter.

The four tests that matter are the ones named after the traps in
burnrate/adapters/codex.py. Each is built from a rollout file written here by
hand, shaped exactly like the real ones, so the trap is reproduced rather than
described.

Three of the four are places where Codex behaves the OPPOSITE way to Claude
Code. That is the point: "how does this tool count tokens" is a question each
adapter has to answer for itself.
"""

import json

import pandas as pd
import pytest

from burnrate import schema
from burnrate.adapters import codex
from burnrate.pricing import add_estimated_cost


# ---- building fake rollout files ---------------------------------------


def usage(input_tokens, cached=0, output=0, reasoning=0, cache_write=0):
    """A usage block shaped like Codex's, with its own invariants intact.

    Note total_tokens = input + output, with `cached` already inside `input`
    and `reasoning` already inside `output` — that is what the real files do,
    and it is what makes the traps traps.
    """
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }


def write_rollout(tmp_path, session_id="sess-1", cwd="/Users/k/projects/demo",
                  turns=(), meta_model=None, day="19"):
    """Write one rollout file. `turns` is a list of (model, [usage, ...])."""
    root = tmp_path / "sessions" / "2026" / "10" / day
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-2026-10-{day}T16-00-00-{session_id}.jsonl"

    lines = [{
        "timestamp": f"2026-10-{day}T20:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "cwd": cwd,
            "base_instructions": {
                "text": "a prompt that must never be read",
                "provenance": {"model": meta_model} if meta_model else {},
            },
        },
    }]

    clock = 1
    for turn_number, (model, usages) in enumerate(turns):
        turn_id = f"turn-{turn_number}"
        lines.append({
            "timestamp": f"2026-10-{day}T20:00:{clock:02d}.000Z",
            "type": "turn_context",
            "payload": {"turn_id": turn_id, "cwd": cwd, "model": model},
        })
        clock += 1
        # Running totals, exactly as Codex writes them.
        thread = usage(0)
        for call_number, u in enumerate(usages):
            for key in thread:
                thread[key] += u[key]
            lines.append({
                "timestamp": f"2026-10-{day}T20:00:{clock:02d}.000Z",
                "type": "token_usage_record",
                "payload": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "response_id": f"resp-{turn_number}-{call_number}",
                    "usage": u,
                    "turn_token_usage": dict(thread),
                    "thread_token_usage": dict(thread),
                },
            })
            clock += 1
        # A content line, to prove we ignore everything that isn't usage.
        lines.append({
            "timestamp": f"2026-10-{day}T20:00:{clock:02d}.000Z",
            "type": "response_item",
            "payload": {"text": "secret conversation content"},
        })
        clock += 1

    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


# ---- trap 1: input includes cached -------------------------------------


def test_input_tokens_are_made_exclusive_of_cache(tmp_path):
    """TRAP 1. Codex reports input INCLUSIVE of cache; our schema wants it exclusive.

    This is the reverse of Claude Code. Not subtracting would price cached
    tokens — which are 90%+ of a session — at the full input rate.
    """
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(30_000, cached=29_000, output=50)])])

    frame = codex.load(str(tmp_path))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["input_tokens"] == 1_000, "fresh input must be 30,000 - 29,000"
    assert row["cache_read_tokens"] == 29_000
    # The two together must come back to what Codex reported, or tokens have
    # been lost rather than reclassified.
    assert row["input_tokens"] + row["cache_read_tokens"] == 30_000


def test_more_cache_than_input_is_clamped_and_warned(tmp_path):
    """A negative token count must never reach the table."""
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(100, cached=500, output=10)])])

    report = codex.LoadReport()
    frame = codex.load(str(tmp_path), report)

    assert frame.iloc[0]["input_tokens"] == 0
    assert any("cached tokens than input" in w for w in report.warnings)
    schema.validate(frame)  # negative counts would fail here


# ---- trap 2: cumulative rollups ----------------------------------------


def test_cumulative_rollups_are_ignored(tmp_path):
    """TRAP 2. Only `usage` is per-call; the other two blocks are running totals.

    Four calls of 1,000 fresh input each must total 4,000 — not the 10,000 you
    get by summing a cumulative column (1,000 + 2,000 + 3,000 + 4,000).
    """
    calls = [usage(1_000, output=10) for _ in range(4)]
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", calls)])

    frame = codex.load(str(tmp_path))

    assert len(frame) == 4
    assert frame["input_tokens"].sum() == 4_000
    assert frame["output_tokens"].sum() == 40


# ---- trap 3: reasoning is inside output --------------------------------


def test_reasoning_tokens_are_not_billed_twice(tmp_path):
    """TRAP 3. reasoning_output_tokens is a SUBSET of output_tokens."""
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(500, output=1_000, reasoning=400)])])

    frame = codex.load(str(tmp_path))

    assert frame.iloc[0]["output_tokens"] == 1_000, "not 1,400"


# ---- trap 4: the model lives elsewhere ---------------------------------


def test_model_comes_from_turn_context(tmp_path):
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(100, output=10)])])
    frame = codex.load(str(tmp_path))
    assert frame.iloc[0]["model"] == "gpt-6.1-sol"


def test_a_model_switch_mid_session_is_attributed_correctly(tmp_path):
    """TRAP 4. Usage records carry no model, so order is what resolves it.

    Real sessions do switch models, so getting this wrong misattributes cost
    between models that differ in price by 2x.
    """
    write_rollout(tmp_path, turns=[
        ("gpt-6.1-sol", [usage(100, output=10)]),
        ("gpt-5.6-sol", [usage(200, output=20)]),
    ])

    frame = codex.load(str(tmp_path)).sort_values("model").reset_index(drop=True)

    assert list(frame["model"]) == ["gpt-5.6-sol", "gpt-6.1-sol"]
    assert frame.loc[frame["model"] == "gpt-5.6-sol", "input_tokens"].item() == 200
    assert frame.loc[frame["model"] == "gpt-6.1-sol", "input_tokens"].item() == 100


def test_usage_with_no_model_is_skipped_loudly(tmp_path):
    """An unattributable row is dropped, and the report says so."""
    write_rollout(tmp_path, turns=[(None, [usage(100, output=10)])])

    report = codex.LoadReport()
    frame = codex.load(str(tmp_path), report)

    assert frame.empty
    assert any("no model" in w for w in report.warnings)


# ---- the rest ----------------------------------------------------------


def test_duplicate_response_ids_are_dropped(tmp_path):
    """A resumed session can replay lines; one API call must count once."""
    path = write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(100, output=10)])])
    # Replay every line a second time, as a resume would.
    path.write_text(path.read_text() + path.read_text())

    report = codex.LoadReport()
    frame = codex.load(str(tmp_path), report)

    assert len(frame) == 1
    assert report.duplicate_entries_dropped == 1


def test_project_comes_from_the_working_directory(tmp_path):
    write_rollout(tmp_path, cwd="/Users/k/code/burn-rate",
                  turns=[("gpt-6.1-sol", [usage(100, output=10)])])
    assert codex.load(str(tmp_path)).iloc[0]["project"] == "burn-rate"


def test_output_matches_the_common_format(tmp_path):
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(100, cached=50, output=10)])])
    frame = codex.load(str(tmp_path))

    schema.validate(frame)
    row = frame.iloc[0]
    assert row["provider"] == "openai"
    assert row["product"] == "codex"
    assert row["source"] == "codex"
    assert bool(row["is_estimate"]) is True
    # Codex has one cache write tier, so the 1-hour column is always empty.
    assert row["cache_write_1h_tokens"] == 0


def test_no_conversation_text_is_kept(tmp_path):
    """Privacy is not a policy here, it is a property of the output.

    The fixture contains a prompt and a response_item. Neither may appear in
    any cell of the table.
    """
    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(100, output=10)])])

    frame = codex.load(str(tmp_path))
    everything = frame.to_csv(index=False)

    assert "secret conversation content" not in everything
    assert "must never be read" not in everything


def test_missing_codex_directory_is_not_an_error(tmp_path):
    frame = codex.load(str(tmp_path / "no-codex-here"))
    assert frame.empty
    schema.validate(frame)


def test_rows_can_be_priced_and_combined_with_claude_code(tmp_path):
    """The whole point of the common format: sources add up."""
    from burnrate.adapters import fake

    write_rollout(tmp_path, turns=[("gpt-6.1-sol", [usage(1_000_000, output=1_000_000)])])
    codex_rows = add_estimated_cost(codex.load(str(tmp_path)))

    # gpt-6.1-sol: $2.00 input, $10.00 output per million.
    assert codex_rows["estimated_cost"].sum() == pytest.approx(12.00)

    combined = schema.coerce(pd.concat([codex_rows, add_estimated_cost(fake.generate(days=3))]))
    schema.validate(combined)
    assert set(combined["product"]) >= {"codex"}
    assert combined["estimated_cost"].notna().all()
