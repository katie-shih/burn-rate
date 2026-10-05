# CLAUDE.md — Burn Rate

Guidance for Claude Code when working in this repo. Read this first, every session.

## What we're building

A local dashboard that answers: **"How much AI did I use, on what, and what would it have cost at API prices?"**

Katie is an MIT Sloan student using Claude through MIT's Claude Enterprise plan, MIT's Parley
platform, and personal subscriptions. Almost nothing here is a real bill — the dollar figures are
**API-equivalent value**: what the same usage *would* have cost if bought à la carte. The point is
to see whether the subscriptions are being used well, not to reconcile an invoice.

Everything runs on her laptop. No data leaves the machine.

## How to work with Katie

She is new to coding. So:

1. **Explain before you build.** Plain language, no jargon. Say what you're about to make and why.
2. **Propose a plan and wait for approval** before any big change. Small fixes don't need a plan.
3. **Use fake data** until she explicitly says to point at her real logs.
4. **Suggest a git commit** at each checkpoint (see Checkpoints below).
5. **Update the Progress section** at the bottom of this file at the end of every session.
6. Show the math before writing code that depends on it.

## Tech stack

- Python + Streamlit, run locally (`streamlit run app.py`)
- pandas for the data work, PyYAML for config
- git from day one
- A classmate should be able to install it from the README alone

**Setup note:** the Mac's system Python is 3.9.6, which is old enough to cause pain. Install a newer
Python (3.11 or 3.12) before the first `pip install`, and use a virtual environment (`.venv`).

## Scope — this sprint (~9–10 hours)

**Must have**
- Read Claude Code's local usage logs (`~/.claude/projects/*/*.jsonl`)
- Total tokens over time
- Breakdown by model and by project
- Estimated cost of that usage
- Dashboard with: month-to-date totals, daily trend, breakdown by model and by token type
  (input / output / cache write / cache read), cost per session, and a month-end forecast
- Labels that make clear what's **estimated** vs. **actually billed**
- At least one "here's where you could save" suggestion

**Stretch — only one, only if the must-haves are done:** Parley API usage logging.

**Out of scope:** anomaly detection, budget alerts, per-task logging, admin API connections,
subscription cost tracking (deliberately dropped — the question is how usage sits *inside* the
subscriptions, not what the subscriptions cost).

## The common data format

Every data source gets its own small **adapter** that converts raw logs into this one table.
Nothing downstream knows or cares where a row came from.

| Column | Meaning |
|---|---|
| `date` | Day of the usage (local time) |
| `provider` | e.g. `anthropic` |
| `product` | e.g. `claude_code`, `parley` |
| `project` | Which folder/workspace the usage happened in |
| `model` | e.g. `claude-opus-5` |
| `session_id` | Groups rows into one working session |
| `input_tokens` | Fresh input tokens (not cached) |
| `output_tokens` | Tokens the model generated |
| `cache_write_tokens` | Tokens written into the prompt cache |
| `cache_read_tokens` | Tokens read back out of the cache |
| `estimated_cost` | Dollars, from the formula below |
| `source` | Which adapter produced the row |
| `is_estimate` | `True` = computed by us, `False` = a real billed figure |

One row = one API call. That's the finest grain available, and everything else (sessions, days,
months) is a grouping of those rows.

**Two changes from the original column list, both deliberate:**
- Cache is **two columns**, not one. Cache writes and reads are priced about 12x apart, so merging
  them would blow the 5% accuracy target. The dashboard can still show one combined "cached"
  number — it just adds the two.
- Added **`project`**. "Break usage down by project" is a must-have and there was nowhere to put
  the project name. Hiding it inside `session_id` would have been a trap for later.

## The cost formula, in plain English

API prices are quoted **per million tokens**. For every row in the table:

1. **Input cost** = input_tokens ÷ 1,000,000 × the model's input price
2. **Output cost** = output_tokens ÷ 1,000,000 × the model's output price
3. **Cache write cost** = cache_write_tokens ÷ 1,000,000 × input price × **1.25**
   (writing to the cache costs 25% more than normal input)
4. **Cache read cost** = cache_read_tokens ÷ 1,000,000 × input price × **0.10**
   (reading from the cache costs 10% of normal input — a 90% discount)
5. **Total** = add all four together.

**Worked example** — one session on Claude Opus 5 ($5.00 input / $25.00 output per million):

| Piece | Tokens | Math | Cost |
|---|---|---|---|
| Input | 50,000 | 0.05 × $5.00 | $0.25 |
| Output | 8,000 | 0.008 × $25.00 | $0.20 |
| Cache write | 120,000 | 0.12 × $5.00 × 1.25 | $0.75 |
| Cache read | 900,000 | 0.90 × $5.00 × 0.10 | $0.45 |
| **Total** | | | **$1.65** |

Note how cache reads dominate the token count but barely touch the cost. Any chart that plots
"tokens" without separating cache reads will be misleading — say so on the chart.

**Two things to verify against real logs before trusting any number:**
- That `input_tokens` in the log already *excludes* cached tokens (if it doesn't, we'd double-count).
- Whether any entries use the 1-hour cache, which costs 2.0x input instead of 1.25x.

## Where prices come from

A script fetches current prices from Anthropic's public pricing page and writes them to a local
`prices.yaml` (model, input price, output price, cache multipliers, date fetched). The dashboard
**only ever reads `prices.yaml`** — it never calls the network while running.

This keeps prices current without breaking the privacy rule: the fetch sends no usage data, just
asks for a public page, and the result is a plain text file Katie can read and correct by hand.
`prices.yaml` is committed to git so the numbers are versioned.

## Privacy and secrets — non-negotiable

- Everything stays on this machine. No analytics, no telemetry, no uploads.
- **Never store conversation text.** Counts and metadata only — if a field might contain prompt or
  response content, drop it in the adapter, not later.
- No API keys or secrets in code. Use a local `.env`, and make sure `.gitignore` excludes `.env`,
  `.venv/`, `data/`, and any exported real logs.

## Repo layout (proposed)

```
burn-rate/
  app.py                  # the Streamlit dashboard
  burnrate/
    schema.py             # the common format, in one place
    pricing.py            # the cost formula
    adapters/
      claude_code.py      # reads ~/.claude/projects/*/*.jsonl
      fake.py             # generated sample data for development
  scripts/refresh_prices.py
  prices.yaml
  tests/
  README.md               # install instructions for a classmate
  CLAUDE.md               # this file
```

## Success criteria

1. Estimated cost for three real Claude Code sessions is **within 5%** of a hand calculation from
   the raw logs. (Do this check with a written-out comparison Katie can read, not just a test pass.)
2. Katie can answer "how much AI did I use last month, on which models, and what would it have
   cost?" in **under a minute**.
3. The dashboard shows at least **one number she didn't already know**.
4. *Stretch:* a classmate installs it from the README alone.

## Checkpoints (suggest a commit at each)

1. After the accuracy check passes — the formula is trustworthy.
2. After the dashboard works end to end.
3. At the end of the sprint — README finished, repo clean.

---

## Progress

**Session 1 — 2026-10-05**
- Agreed on scope, the common data format, and the cost formula. Wrote this file.
- Decisions made: split cached tokens into write/read columns; fetch prices into a local
  `prices.yaml`; drop subscription cost tracking; stretch goal is Parley API logging.
- Confirmed Claude Code logs exist at `~/.claude/projects/` (3 project folders).
- Built the foundation: git repo, `uv` for Python (3.14) and packages, `schema.py`, `pricing.py`,
  the fake-data adapter, and 15 tests — all passing. The CLAUDE.md worked example ($1.65) is a
  test, so the docs and the code can't drift apart.
- Added a `project` column to the schema (see note above).
- Day-to-day commands: `uv run pytest` and `uv run streamlit run app.py`.
- **Next:** write the real Claude Code adapter (`adapters/claude_code.py`), then do the accuracy
  check against three real sessions — that's checkpoint 1.
