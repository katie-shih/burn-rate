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

from burnrate.adapters import claude_code, codex   # noqa: E402
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

    # Each line has its own published price, read straight out of prices.yaml.
    # Nothing is derived from the input price any more.
    lines = [
        ("input", totals["input"], input_price),
        ("output", totals["output"], entry["output_per_mtok"]),
        ("cache write 5m", totals["write_5m"], entry["cache_write_5m_per_mtok"]),
        ("cache write 1h", totals["write_1h"], entry["cache_write_1h_per_mtok"]),
        ("cache read", totals["read"], entry["cache_read_per_mtok"]),
    ]

    # The "vs in" column is printed for information only -- it shows how each
    # price compares to fresh input, which is what makes the cache discount
    # legible. It takes no part in the arithmetic below.
    priced = [
        (label, tokens, price, price / input_price, tokens / 1e6 * price)
        for label, tokens, price in lines
    ]

    return {
        "model": model,
        "calls": totals["calls"],
        "lines": priced,
        "total": sum(row[4] for row in priced),
    }


CODEX_ROOT = os.path.expanduser("~/.codex")


def codex_hand_calculation(prices):
    """Add up every Codex session from the raw rollouts, then price by hand.

    Plain Python again: no pandas, no burnrate code. The thing worth proving
    here is the cache subtraction. Codex reports `input_tokens` INCLUSIVE of
    cached tokens, so fresh input is `input_tokens - cached_input_tokens`.
    Getting that backwards prices cached tokens at up to 20x their real rate,
    and it is the opposite of how Claude Code reports the same field.

    Only `usage` is read. `turn_token_usage` and `thread_token_usage` are
    running totals, and summing either would multiply the answer.
    """
    files = []
    for pattern in ("sessions/**/rollout-*.jsonl", "archived_sessions/**/rollout-*.jsonl",
                    "archived_sessions/rollout-*.jsonl"):
        files.extend(glob.glob(os.path.join(CODEX_ROOT, pattern), recursive=True))

    by_model = defaultdict(lambda: {"calls": 0, "fresh": 0, "cached": 0,
                                    "out": 0, "write": 0})
    seen = set()

    for path in sorted(set(files)):
        model = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue

                # turn_context sets the model for the records that follow it.
                if entry.get("type") == "turn_context" and payload.get("model"):
                    model = payload["model"]
                    continue
                if entry.get("type") != "token_usage_record" or model is None:
                    continue

                response_id = payload.get("response_id")
                if response_id is not None:
                    if response_id in seen:
                        continue
                    seen.add(response_id)

                usage = payload.get("usage") or {}
                cached = usage.get("cached_input_tokens", 0) or 0
                bucket = by_model[model]
                bucket["calls"] += 1
                bucket["fresh"] += (usage.get("input_tokens", 0) or 0) - cached
                bucket["cached"] += cached
                bucket["out"] += usage.get("output_tokens", 0) or 0
                bucket["write"] += usage.get("cache_write_input_tokens", 0) or 0

    results = {}
    for model, totals in by_model.items():
        entry = (prices.get("models") or {}).get(model)
        if entry is None:
            continue
        lines = [
            ("input (fresh)", totals["fresh"], entry["input_per_mtok"]),
            ("output", totals["out"], entry["output_per_mtok"]),
            ("cache write", totals["write"], entry.get("cache_write_5m_per_mtok", 0)),
            ("cache read", totals["cached"], entry.get("cache_read_per_mtok", 0)),
        ]
        priced = [(label, tokens, price, tokens / 1e6 * price)
                  for label, tokens, price in lines]
        results[model] = {
            "calls": totals["calls"],
            "lines": priced,
            "total": sum(row[3] for row in priced),
        }
    return results


def check_codex(prices):
    """Compare the Codex adapter against the hand calculation above.

    Returns the number of failures, or None when Codex is not installed.
    """
    report = codex.LoadReport()
    frame = add_estimated_cost(codex.load(CODEX_ROOT, report))
    if frame.empty:
        return None

    manual = codex_hand_calculation(prices)
    if not manual:
        return None

    ours = frame.groupby("model")["estimated_cost"].sum()

    print("\n" + "=" * 72)
    print("CODEX — estimated cost vs. a hand calculation")
    print("=" * 72)
    print(f"Loaded: {report.summary()}")

    failures = 0
    for model in sorted(manual):
        rows = manual[model]
        theirs = float(ours.get(model, 0.0))

        print("\n" + "-" * 72)
        print(f"MODEL: {model}    API calls: {rows['calls']:,}")
        print("-" * 72)
        print(f"  {'piece':<16}{'tokens':>14}  {'$/Mtok':>8}    {'cost':>10}")
        for label, tokens, price, cost in rows["lines"]:
            print(f"  {label:<16}{tokens:>14,}  {price:>8.2f}    ${cost:>9.4f}")
        print(f"  {'':<16}{'':>14}  {'':>8}    {'-' * 10}")
        print(f"  {'hand total':<16}{'':>14}  {'':>8}    ${rows['total']:>9.4f}")
        print(f"  {'Burn Rate says':<16}{'':>14}  {'':>8}    ${theirs:>9.4f}")

        difference = 0.0 if rows["total"] == 0 else abs(theirs - rows["total"]) / rows["total"]
        verdict = "PASS" if difference <= TOLERANCE else "FAIL"
        if verdict == "FAIL":
            failures += 1
        print(f"\n  difference: {difference:.4%}   (tolerance {TOLERANCE:.0%})   -> {verdict}")

    return failures


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
        print(f"  {'piece':<16}{'tokens':>14}  {'$/Mtok':>8} {'vs in':>6}    {'cost':>10}")
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

    codex_failures = check_codex(prices)

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {failures} of {len(chosen)} Claude Code sessions "
              "FAILED the 5% check.")
        return 1
    if codex_failures:
        print(f"RESULT: {codex_failures} Codex model(s) FAILED the 5% check.")
        return 1

    print(f"RESULT: all {len(chosen)} Claude Code sessions agree within "
          f"{TOLERANCE:.0%}. Criterion #1 met.")
    if codex_failures == 0:
        print("        Codex agrees too, including the cache subtraction.")
    else:
        print("        Codex not checked — no Codex logs on this machine.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
