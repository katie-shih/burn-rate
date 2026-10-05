"""Success criterion #1: do our cost estimates match a hand calculation?

This script deliberately does NOT use burnrate's adapter or pricing code. It
re-reads the raw log files with plain Python and does the arithmetic from
scratch, then compares against what the real code produces. If the two ever
disagree by more than 5%, something is wrong and the dashboard can't be trusted.

Everything is printed with the arithmetic shown, so you can check it with a
calculator yourself.

    uv run python scripts/accuracy_check.py
"""

import glob
import json
import os
import sys
from collections import defaultdict

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from burnrate.adapters import claude_code          # noqa: E402
from burnrate.pricing import add_estimated_cost    # noqa: E402

LOG_ROOT = os.path.expanduser("~/.claude/projects")
PRICES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prices.yaml"
)
TOLERANCE = 0.05
SESSIONS_TO_CHECK = 3


def hand_calculation(session_id, prices):
    """Add up one session's tokens from the raw logs, then price them by hand.

    Plain Python. No pandas, no burnrate code. One row per requestId, because
    Claude Code writes one log line per content block and repeats the usage.
    """
    totals = defaultdict(int)
    seen = set()
    model = None

    for path in sorted(glob.glob(os.path.join(LOG_ROOT, "*", "*.jsonl"))):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "assistant":
                    continue
                if entry.get("sessionId") != session_id:
                    continue

                usage = (entry.get("message") or {}).get("usage")
                if not isinstance(usage, dict):
                    continue

                request_id = entry.get("requestId")
                if request_id in seen:
                    continue
                seen.add(request_id)

                model = model or (entry.get("message") or {}).get("model")
                creation = usage.get("cache_creation") or {}

                totals["calls"] += 1
                totals["input"] += usage.get("input_tokens", 0) or 0
                totals["output"] += usage.get("output_tokens", 0) or 0
                totals["write_5m"] += creation.get("ephemeral_5m_input_tokens", 0) or 0
                totals["write_1h"] += creation.get("ephemeral_1h_input_tokens", 0) or 0
                totals["read"] += usage.get("cache_read_input_tokens", 0) or 0

    if model is None:
        return None

    entry = prices["models"][model]
    input_price = entry["input_per_mtok"]
    output_price = entry["output_per_mtok"]
    multipliers = prices["cache_multipliers"]

    lines = [
        ("input", totals["input"], input_price, 1.0),
        ("output", totals["output"], output_price, 1.0),
        ("cache write 5m", totals["write_5m"], input_price, multipliers["write_5m"]),
        ("cache write 1h", totals["write_1h"], input_price, multipliers["write_1h"]),
        ("cache read", totals["read"], input_price, multipliers["read"]),
    ]

    priced = [(label, tokens, price, mult, tokens / 1e6 * price * mult)
              for label, tokens, price, mult in lines]

    return {
        "model": model,
        "calls": totals["calls"],
        "lines": priced,
        "total": sum(row[4] for row in priced),
    }


def main():
    with open(PRICES_PATH, encoding="utf-8") as handle:
        prices = yaml.safe_load(handle)

    report = claude_code.LoadReport()
    frame = add_estimated_cost(claude_code.load(LOG_ROOT, report))

    if frame.empty:
        print("No Claude Code logs found at", LOG_ROOT)
        return 1

    print("=" * 72)
    print("ACCURACY CHECK — estimated cost vs. a hand calculation")
    print("=" * 72)
    print(f"Loaded: {report.summary()}")
    for warning in report.warnings:
        print(f"  warning: {warning}")

    by_session = (
        frame.groupby("session_id")["estimated_cost"].sum().sort_values(ascending=False)
    )
    chosen = list(by_session.head(SESSIONS_TO_CHECK).index)

    failures = 0
    for number, session_id in enumerate(chosen, start=1):
        manual = hand_calculation(session_id, prices)
        ours = float(by_session[session_id])

        print("\n" + "-" * 72)
        print(f"SESSION {number} of {len(chosen)}:  {session_id}")
        print("-" * 72)

        if manual is None:
            print("  Could not re-read this session from the raw logs.")
            failures += 1
            continue

        print(f"  model: {manual['model']}    API calls: {manual['calls']:,}")
        print()
        print(f"  {'piece':<16}{'tokens':>14}  {'$/Mtok':>8} {'x':>6}    {'cost':>10}")
        for label, tokens, price, mult, cost in manual["lines"]:
            print(f"  {label:<16}{tokens:>14,}  {price:>8.2f} {mult:>6.2f}    ${cost:>9.4f}")
        print(f"  {'':<16}{'':>14}  {'':>8} {'':>6}    {'-' * 10}")
        print(f"  {'hand total':<16}{'':>14}  {'':>8} {'':>6}    ${manual['total']:>9.4f}")
        print(f"  {'Burn Rate says':<16}{'':>14}  {'':>8} {'':>6}    ${ours:>9.4f}")

        if manual["total"] == 0:
            difference = 0.0
        else:
            difference = abs(ours - manual["total"]) / manual["total"]

        verdict = "PASS" if difference <= TOLERANCE else "FAIL"
        if verdict == "FAIL":
            failures += 1
        print(f"\n  difference: {difference:.4%}   (tolerance {TOLERANCE:.0%})   -> {verdict}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {failures} of {len(chosen)} sessions FAILED the 5% check.")
        return 1
    print(f"RESULT: all {len(chosen)} sessions agree within {TOLERANCE:.0%}. "
          "Criterion #1 met.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
