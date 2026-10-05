"""Tests for the Claude Code log adapter.

These build their own tiny log files, so they pass on anyone's machine and
never touch real usage data.
"""

import json

import pytest

from burnrate import schema
from burnrate.adapters import claude_code
from burnrate.pricing import PriceBook


def write_log(folder, name, entries):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return path


def assistant_entry(
    request_id="req_1",
    block=0,
    session="sess-1",
    cwd="/Users/someone/code/my-project",
    model="claude-opus-5",
    timestamp="2026-10-01T15:30:00.000Z",
    input_tokens=2,
    output_tokens=1000,
    write_5m=0,
    write_1h=5000,
    cache_read=40000,
):
    """One assistant log line, shaped like the real thing."""
    return {
        "type": "assistant",
        "requestId": request_id,
        "apiBlockIndex": block,
        "sessionId": session,
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {
            "id": "msg_" + request_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": write_5m + write_1h,
                "cache_read_input_tokens": cache_read,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": write_5m,
                    "ephemeral_1h_input_tokens": write_1h,
                },
                "service_tier": "standard",
                "speed": "standard",
            },
        },
    }


def test_one_api_call_logged_as_three_lines_counts_once(tmp_path):
    """The big one: Claude Code logs one reply as one line per content block.

    All three lines repeat the same usage. Counting them all would roughly
    double every cost figure.
    """
    write_log(
        tmp_path / "proj",
        "session.jsonl",
        [assistant_entry(block=0), assistant_entry(block=1), assistant_entry(block=2)],
    )

    report = claude_code.LoadReport()
    frame = claude_code.load(str(tmp_path), report)

    assert len(frame) == 1
    assert report.assistant_entries == 3
    assert report.duplicate_entries_dropped == 2
    assert frame.loc[0, "output_tokens"] == 1000


def test_distinct_requests_are_all_kept(tmp_path):
    write_log(
        tmp_path / "proj",
        "session.jsonl",
        [assistant_entry(request_id="req_%d" % i, output_tokens=100 * i) for i in range(1, 6)],
    )

    frame = claude_code.load(str(tmp_path))

    assert len(frame) == 5
    assert frame["output_tokens"].sum() == 100 + 200 + 300 + 400 + 500


def test_cache_writes_keep_their_five_minute_and_one_hour_split(tmp_path):
    write_log(
        tmp_path / "proj",
        "session.jsonl",
        [assistant_entry(write_5m=1000, write_1h=4000)],
    )

    frame = claude_code.load(str(tmp_path))

    assert frame.loc[0, "cache_write_5m_tokens"] == 1000
    assert frame.loc[0, "cache_write_1h_tokens"] == 4000
    assert schema.cache_write_tokens(frame).iloc[0] == 5000


def test_old_style_log_without_the_split_assumes_the_expensive_cache(tmp_path):
    """If a log doesn't say which cache, don't guess the cheap one."""
    entry = assistant_entry()
    del entry["message"]["usage"]["cache_creation"]
    entry["message"]["usage"]["cache_creation_input_tokens"] = 7000
    write_log(tmp_path / "proj", "session.jsonl", [entry])

    report = claude_code.LoadReport()
    frame = claude_code.load(str(tmp_path), report)

    assert frame.loc[0, "cache_write_1h_tokens"] == 7000
    assert frame.loc[0, "cache_write_5m_tokens"] == 0
    assert any("1-hour" in w for w in report.warnings)


def test_project_comes_from_the_working_directory(tmp_path):
    write_log(
        tmp_path / "proj",
        "session.jsonl",
        [assistant_entry(cwd="/Users/someone/code/burn-rate")],
    )
    frame = claude_code.load(str(tmp_path))
    assert frame.loc[0, "project"] == "burn-rate"


def test_unusual_speed_or_tier_produces_a_warning(tmp_path):
    """Fast mode and batch are priced differently — don't silently misprice."""
    entry = assistant_entry()
    entry["message"]["usage"]["speed"] = "fast"
    write_log(tmp_path / "proj", "session.jsonl", [entry])

    report = claude_code.LoadReport()
    claude_code.load(str(tmp_path), report)

    assert any("speed" in w for w in report.warnings)


def test_non_assistant_and_broken_lines_are_skipped(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir(parents=True)
    (folder / "session.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "hello"}}),
                "{ this is not valid json",
                json.dumps({"type": "system"}),
                json.dumps(assistant_entry()),
            ]
        ),
        encoding="utf-8",
    )

    report = claude_code.LoadReport()
    frame = claude_code.load(str(tmp_path), report)

    assert len(frame) == 1
    assert any("unreadable" in w for w in report.warnings)


def test_no_logs_gives_an_empty_table_not_a_crash(tmp_path):
    frame = claude_code.load(str(tmp_path / "nothing-here"))
    assert len(frame) == 0
    schema.validate(frame)


def test_output_matches_the_common_format_and_can_be_priced(tmp_path):
    write_log(
        tmp_path / "proj",
        "session.jsonl",
        [assistant_entry(request_id="req_%d" % i) for i in range(1, 4)],
    )

    from burnrate.pricing import add_estimated_cost

    frame = add_estimated_cost(claude_code.load(str(tmp_path)))
    schema.validate(frame)

    assert frame["estimated_cost"].notna().all()
    assert frame["source"].eq("claude_code").all()
    assert frame["is_estimate"].all()

    # Check one row against the formula by hand: opus 5, $5 in / $25 out.
    expected = (
        2 / 1e6 * 5.00          # input
        + 1000 / 1e6 * 25.00    # output
        + 5000 / 1e6 * 5.00 * 2.00   # 1-hour cache write
        + 40000 / 1e6 * 5.00 * 0.10  # cache read
    )
    assert frame.loc[0, "estimated_cost"] == pytest.approx(expected)
