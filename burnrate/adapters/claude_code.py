"""Reads Claude Code's local usage logs.

Claude Code writes one JSON object per line into
``~/.claude/projects/<mangled-path>/<session-id>.jsonl``. We only look at
``assistant`` entries, and only at their ``usage`` numbers — never at any
message content.

Two things about this format will quietly ruin your cost numbers if you miss
them. Both were found by inspecting real logs, and both are handled here:

1. ONE API CALL IS LOGGED AS SEVERAL LINES.
   A reply containing text plus two tool calls is written as three entries
   (``apiBlockIndex`` 0, 1, 2) that each repeat the *same* usage object. Adding
   them up double-counts. In Katie's logs, 422 entries represented only 212
   real API calls — naively counting every line overstated tokens by 104%.
   We therefore keep one row per ``requestId``.

2. CLAUDE CODE USES THE 1-HOUR CACHE, NOT THE 5-MINUTE ONE.
   Cache writes are split under ``usage.cache_creation`` into
   ``ephemeral_5m_input_tokens`` and ``ephemeral_1h_input_tokens``. They cost
   1.25x and 2.00x the input price. In Katie's logs, 100% of cache writes were
   1-hour, so assuming the cheaper kind would have under-counted badly.

Also worth knowing: ``input_tokens`` already EXCLUDES cached tokens, so the
four token counts can be added without double-counting.

``usage.output_tokens_details.thinking_tokens`` is carried through as
``reasoning_tokens``. It is part of ``output_tokens``, not an addition to it,
so it is reported and never summed — see burnrate.schema.REPORTED_COLUMNS.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterator, List, Optional

import pandas as pd

from burnrate import schema

DEFAULT_LOG_ROOT = os.path.expanduser("~/.claude/projects")

# Pricing assumes ordinary requests. If a log ever says otherwise, we want to
# know rather than silently misprice it.
EXPECTED_SPEED = {"standard", None}
EXPECTED_SERVICE_TIER = {"standard", None}


@dataclass
class LoadReport:
    """What happened during a load. Surfaced in the dashboard, not hidden."""

    files_read: int = 0
    lines_read: int = 0
    assistant_entries: int = 0
    duplicate_entries_dropped: int = 0
    rows: int = 0
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows:,} API calls from {self.files_read} log file(s); "
            f"dropped {self.duplicate_entries_dropped:,} duplicate log lines"
        )


def find_log_files(log_root: str = DEFAULT_LOG_ROOT) -> List[str]:
    """Every session log under the Claude Code projects folder."""
    if not os.path.isdir(log_root):
        return []
    found = []
    for folder, _subfolders, filenames in os.walk(log_root):
        for name in sorted(filenames):
            if name.endswith(".jsonl"):
                found.append(os.path.join(folder, name))
    return sorted(found)


def load(
    log_root: str = DEFAULT_LOG_ROOT,
    report: Optional[LoadReport] = None,
) -> pd.DataFrame:
    """Read every Claude Code log into the common format.

    Pass a LoadReport if you want the counts and warnings back.
    """
    report = report if report is not None else LoadReport()
    files = find_log_files(log_root)
    report.files_read = len(files)

    seen_request_ids = set()
    rows: List[Dict] = []

    for path in files:
        for entry in _read_entries(path, report):
            row = _row_from_entry(entry, report)
            if row is None:
                continue

            # One API call can be logged as several lines. Keep the first.
            request_id = entry.get("requestId") or (entry.get("message") or {}).get("id")
            if request_id is not None:
                if request_id in seen_request_ids:
                    report.duplicate_entries_dropped += 1
                    continue
                seen_request_ids.add(request_id)

            rows.append(row)

    report.rows = len(rows)

    if not rows:
        return schema.empty_frame()

    frame = pd.DataFrame(rows)
    frame["date"] = _to_local_dates(frame["timestamp"])
    frame = frame.drop(columns=["timestamp"])
    return schema.coerce(frame)


def _read_entries(path: str, report: LoadReport) -> Iterator[Dict]:
    """Yield the assistant entries that carry usage numbers."""
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as error:
        report.warnings.append(f"Could not open {os.path.basename(path)}: {error}")
        return

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
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            if not isinstance((entry.get("message") or {}).get("usage"), dict):
                continue
            report.assistant_entries += 1
            yield entry


def _row_from_entry(entry: Dict, report: LoadReport) -> Optional[Dict]:
    """Turn one assistant entry into one row of the common format."""
    message = entry.get("message") or {}
    usage = message.get("usage") or {}

    model = message.get("model")
    if not model:
        return None

    timestamp = entry.get("timestamp")
    if not timestamp:
        return None

    # Pricing assumes standard speed and tier. Fast mode and batch cost
    # differently, so say so loudly rather than quietly reporting a wrong number.
    speed = usage.get("speed")
    if speed not in EXPECTED_SPEED:
        _warn_once(report, f"Some calls used speed={speed!r}, which is priced differently.")
    tier = usage.get("service_tier")
    if tier not in EXPECTED_SERVICE_TIER:
        _warn_once(report, f"Some calls used service_tier={tier!r}, which is priced differently.")

    cache_creation = usage.get("cache_creation") or {}
    write_5m = cache_creation.get("ephemeral_5m_input_tokens")
    write_1h = cache_creation.get("ephemeral_1h_input_tokens")

    if write_5m is None and write_1h is None:
        # Older logs only have the combined number and don't say which cache.
        # Attribute it to the 1-hour cache, which is what Claude Code uses, and
        # say so — guessing the cheaper one would understate the cost.
        total_write = usage.get("cache_creation_input_tokens", 0) or 0
        write_5m, write_1h = 0, total_write
        if total_write:
            _warn_once(
                report,
                "Some entries don't say which cache was written to; "
                "assumed the 1-hour cache (the more expensive one).",
            )

    return {
        "timestamp": timestamp,
        "provider": "anthropic",
        "product": "claude_code",
        "project": _project_name(entry.get("cwd")),
        "model": model,
        "session_id": entry.get("sessionId"),
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_write_5m_tokens": write_5m or 0,
        "cache_write_1h_tokens": write_1h or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        # Thinking is reported inside output, not beside it, so this is a
        # SUBSET of output_tokens and must never be added to a total.
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
            "thinking_tokens", 0
        ) or 0,
        "estimated_cost": None,  # filled in by burnrate.pricing
        "source": "claude_code",
        "is_estimate": True,  # nobody billed this; it's API-equivalent value
    }


def _project_name(cwd: Optional[str]) -> str:
    """The folder Claude Code was working in, e.g. 'burn-rate'."""
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
