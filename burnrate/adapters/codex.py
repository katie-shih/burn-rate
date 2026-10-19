"""Reads Codex's local session logs.

Codex writes one JSON object per line into
``~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl``, plus
``~/.codex/archived_sessions/`` for finished ones. We only look at
``token_usage_record`` lines, and only at their numbers — never at any message
content.

FOUR THINGS WILL QUIETLY RUIN YOUR NUMBERS HERE, and three of them are the
opposite of how Claude Code behaves. All four were verified against real logs.

1. ``input_tokens`` INCLUDES the cached tokens.
   This is the reverse of Claude Code, where ``input_tokens`` excludes them.
   The proof is in the file: ``total_tokens`` equals ``input_tokens`` plus
   ``output_tokens`` exactly, so the cached count must already be inside the
   input count. Fresh input is therefore
   ``input_tokens - cached_input_tokens``. Skipping that subtraction counted
   97,152 cached tokens as fresh input in a single session — pricing them at
   20x their real rate.

   The general lesson: "does input include cache?" is a property of each tool,
   not of the industry. Every new adapter has to check it again.

2. ``turn_token_usage`` and ``thread_token_usage`` are CUMULATIVE.
   Each record carries three usage blocks. Only ``usage`` is that call's own
   numbers; the other two are running totals for the turn and the whole
   session so far. Summing either one multiplies the session's real usage.
   Checked: the per-call ``usage`` values add up to exactly the final
   ``thread_token_usage``.

3. ``reasoning_output_tokens`` is a SUBSET of ``output_tokens``.
   Adding it on would bill reasoning twice. The same ``total = input + output``
   arithmetic proves it is already counted.

4. The model is NOT in the usage record.
   It lives in the ``turn_context`` line, which appears before the calls it
   governs. We walk the file in order and remember the current model, which
   also means a model switch mid-session is attributed correctly — and that
   does happen: one real session used gpt-6.1-sol and another gpt-5.6-sol.

ON CACHE WRITES: OpenAI has one cache write tier, not Anthropic's 5-minute and
1-hour split, so ``cache_write_input_tokens`` goes into the 5-minute column.
That is not a fudge — ``prices.yaml`` fills that column's price from OpenAI's
own ``cache_creation`` price, so the arithmetic uses OpenAI's real number. The
column name is Anthropic's vocabulary; the price is OpenAI's. The 1-hour
column stays at zero for every Codex row, which is why it does not matter that
no OpenAI model publishes a 1-hour price.
"""

import glob
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterator, List, Optional

import pandas as pd

from burnrate import schema

DEFAULT_LOG_ROOT = os.path.expanduser("~/.codex")

# Where rollout files live under the Codex home, newest layout first.
ROLLOUT_GLOBS = (
    os.path.join("sessions", "**", "rollout-*.jsonl"),
    os.path.join("archived_sessions", "**", "rollout-*.jsonl"),
    os.path.join("archived_sessions", "rollout-*.jsonl"),
)

# Codex can compress older rollouts. We do not decompress them, but we count
# them so the dashboard can say data is being left out rather than pretending
# it has everything.
COMPRESSED_SUFFIXES = (".jsonl.zst", ".jsonl.zstd")


@dataclass
class LoadReport:
    """What happened during a load. Surfaced in the dashboard, not hidden.

    Deliberately the same shape as the Claude Code adapter's report, so the
    dashboard can show either without caring which one it got.
    """

    files_read: int = 0
    lines_read: int = 0
    usage_records: int = 0
    duplicate_entries_dropped: int = 0
    compressed_files_skipped: int = 0
    rows: int = 0
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows:,} API calls from {self.files_read} Codex session(s); "
            f"dropped {self.duplicate_entries_dropped:,} duplicate records"
        )


def find_log_files(log_root: str = DEFAULT_LOG_ROOT) -> List[str]:
    """Every Codex rollout file, de-duplicated and in a stable order."""
    if not os.path.isdir(log_root):
        return []
    found = []
    for pattern in ROLLOUT_GLOBS:
        found.extend(glob.glob(os.path.join(log_root, pattern), recursive=True))
    return sorted(set(found))


def find_compressed_files(log_root: str = DEFAULT_LOG_ROOT) -> List[str]:
    """Compressed rollouts, which we cannot read yet."""
    if not os.path.isdir(log_root):
        return []
    found = []
    for suffix in COMPRESSED_SUFFIXES:
        found.extend(
            glob.glob(os.path.join(log_root, "**", "rollout-*" + suffix), recursive=True)
        )
    return sorted(set(found))


def load(
    log_root: str = DEFAULT_LOG_ROOT,
    report: Optional[LoadReport] = None,
) -> pd.DataFrame:
    """Read every Codex session log into the common format."""
    report = report if report is not None else LoadReport()
    files = find_log_files(log_root)
    report.files_read = len(files)

    compressed = find_compressed_files(log_root)
    report.compressed_files_skipped = len(compressed)
    if compressed:
        _warn_once(
            report,
            f"{len(compressed)} compressed Codex rollout(s) were skipped, so "
            "older Codex usage is missing from these totals.",
        )

    seen_response_ids = set()
    rows: List[Dict] = []

    for path in files:
        for row, response_id in _rows_from_file(path, report):
            # One API call should appear once. Codex reuses response_id across
            # the turn/thread rollups, and a resumed session can replay lines.
            if response_id is not None:
                if response_id in seen_response_ids:
                    report.duplicate_entries_dropped += 1
                    continue
                seen_response_ids.add(response_id)
            rows.append(row)

    report.rows = len(rows)

    if not rows:
        return schema.empty_frame()

    frame = pd.DataFrame(rows)
    frame["date"] = _to_local_dates(frame["timestamp"])
    frame = frame.drop(columns=["timestamp"])
    return schema.coerce(frame)


def _rows_from_file(path: str, report: LoadReport) -> Iterator[tuple]:
    """Walk one rollout in order, yielding (row, response_id) pairs.

    Order matters: ``turn_context`` lines set the model for the usage records
    that follow them, so this cannot be done by filtering for one line type.
    """
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as error:
        report.warnings.append(f"Could not open {os.path.basename(path)}: {error}")
        return

    session_id: Optional[str] = None
    project = "unknown"
    model: Optional[str] = None

    with handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            report.lines_read += 1

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                report.warnings.append(
                    f"Skipped unreadable line {line_number} in {os.path.basename(path)}"
                )
                continue
            if not isinstance(entry, dict):
                continue

            kind = entry.get("type")
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue

            if kind == "session_meta":
                session_id = payload.get("session_id") or payload.get("id")
                project = _project_name(payload.get("cwd"))
                # Fallback only: the model that wrote the base instructions.
                # turn_context is authoritative and comes later.
                provenance = (payload.get("base_instructions") or {}).get("provenance")
                if isinstance(provenance, dict) and provenance.get("model"):
                    model = provenance["model"]
                continue

            if kind == "turn_context":
                if payload.get("model"):
                    model = payload["model"]
                if payload.get("cwd"):
                    project = _project_name(payload["cwd"])
                continue

            if kind != "token_usage_record":
                continue

            usage = payload.get("usage")
            if not isinstance(usage, dict):
                continue
            report.usage_records += 1

            row = _row_from_usage(
                usage=usage,
                timestamp=entry.get("timestamp"),
                session_id=payload.get("session_id") or session_id,
                project=project,
                model=model,
                report=report,
                path=path,
            )
            if row is not None:
                yield row, payload.get("response_id")


def _row_from_usage(
    usage: Dict,
    timestamp: Optional[str],
    session_id: Optional[str],
    project: str,
    model: Optional[str],
    report: LoadReport,
    path: str,
) -> Optional[Dict]:
    """Turn one token_usage_record into one row of the common format."""
    if not timestamp:
        return None
    if not model:
        _warn_once(
            report,
            f"Some Codex usage in {os.path.basename(path)} has no model and "
            "was skipped, so these totals are incomplete.",
        )
        return None

    total_input = _count(usage, "input_tokens")
    cached = _count(usage, "cached_input_tokens")

    # THE IMPORTANT LINE. Codex reports input INCLUSIVE of cache; the common
    # format wants it exclusive. See point 1 in the module docstring.
    fresh_input = total_input - cached
    if fresh_input < 0:
        # Never seen in real logs, but if the invariant ever breaks we must not
        # write a negative token count into the table.
        _warn_once(
            report,
            "Some Codex records report more cached tokens than input tokens, "
            "which breaks an assumption in the cost maths. Treated as zero "
            "fresh input; the real cost may be higher.",
        )
        fresh_input = 0

    return {
        "timestamp": timestamp,
        "provider": "openai",
        "product": "codex",
        "project": project,
        "model": model,
        "session_id": session_id,
        "input_tokens": fresh_input,
        # reasoning_output_tokens is already inside output_tokens. Adding it
        # would bill the same thinking twice. See point 3 above.
        "output_tokens": _count(usage, "output_tokens"),
        # OpenAI has one cache write tier; see "ON CACHE WRITES" above.
        "cache_write_5m_tokens": _count(usage, "cache_write_input_tokens"),
        "cache_write_1h_tokens": 0,
        "cache_read_tokens": cached,
        "estimated_cost": None,  # filled in by burnrate.pricing
        "source": "codex",
        "is_estimate": True,  # nobody billed this; it's API-equivalent value
    }


def _count(usage: Dict, name: str) -> int:
    """A token count, treating missing and null alike as zero."""
    value = usage.get(name)
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _project_name(cwd: Optional[str]) -> str:
    """The folder Codex was working in, e.g. 'burn-rate'."""
    if not cwd:
        return "unknown"
    return os.path.basename(os.path.normpath(cwd)) or "unknown"


def _to_local_dates(timestamps: pd.Series) -> pd.Series:
    """Log timestamps are UTC; the dashboard thinks in local days."""
    local_zone = datetime.now().astimezone().tzinfo
    parsed = pd.to_datetime(timestamps, utc=True, format="ISO8601")
    return parsed.dt.tz_convert(local_zone).dt.tz_localize(None).dt.normalize()


def _warn_once(report: LoadReport, message: str) -> None:
    if message not in report.warnings:
        report.warnings.append(message)
