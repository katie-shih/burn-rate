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
| `cache_write_5m_tokens` | Written into the 5-minute cache — costs 1.25x input |
| `cache_write_1h_tokens` | Written into the 1-hour cache — costs 2.00x input |
| `cache_read_tokens` | Read back out of the cache — costs 0.10x input |
| `estimated_cost` | Dollars, from the formula below |
| `source` | Which adapter produced the row |
| `is_estimate` | `True` = computed by us, `False` = a real billed figure |

One row = one API call. That's the finest grain available, and everything else (sessions, days,
months) is a grouping of those rows.

**Changes from the original column list, all deliberate:**
- Cache is **three columns**, not one. The three kinds cost 1.25x, 2.00x and 0.10x of the input
  price — up to 20x apart — so merging them would blow the 5% accuracy target. The dashboard can
  still show one combined "cached" number; it just adds them up (`schema.cache_write_tokens`).
- Added **`project`**. "Break usage down by project" is a must-have and there was nowhere to put
  the project name. Hiding it inside `session_id` would have been a trap for later.

## The cost formula, in plain English

API prices are quoted **per million tokens**. For every row in the table:

1. **Input cost** = input_tokens ÷ 1,000,000 × the model's input price
2. **Output cost** = output_tokens ÷ 1,000,000 × the model's output price
3. **Cache write (5 min)** = cache_write_5m_tokens ÷ 1,000,000 × input price × **1.25**
4. **Cache write (1 hour)** = cache_write_1h_tokens ÷ 1,000,000 × input price × **2.00**
5. **Cache read** = cache_read_tokens ÷ 1,000,000 × input price × **0.10**
   (reading from the cache costs 10% of normal input — a 90% discount)
6. **Total** = add all five together.

**Worked example** — one session on Claude Opus 5 ($5.00 input / $25.00 output per million):

| Piece | Tokens | Math | Cost |
|---|---|---|---|
| Input | 50,000 | 0.05 × $5.00 | $0.25 |
| Output | 8,000 | 0.008 × $25.00 | $0.20 |
| Cache write, 5 min | 40,000 | 0.04 × $5.00 × 1.25 | $0.25 |
| Cache write, 1 hour | 80,000 | 0.08 × $5.00 × 2.00 | $0.80 |
| Cache read | 900,000 | 0.90 × $5.00 × 0.10 | $0.45 |
| **Total** | | | **$1.95** |

Note how cache reads dominate the token count but take under a quarter of the cost. Any chart that
plots "tokens" without separating cache reads will be misleading — say so on the chart.

## What the real logs turned out to say

Both open questions are now answered, and one answer was the opposite of the obvious guess. These
are facts about the log format, checked against 9 real log files:

- **`input_tokens` already excludes cached tokens.** No double-counting. (Typical value: 2.)
- **Claude Code uses the 1-hour cache, 100% of the time** — so cache writes cost **2.00x** input,
  not 1.25x. Assuming the cheaper cache would have under-counted that piece by 37.5%.
- **One API call is written to the log as several lines** — one per content block, each repeating
  the *same* usage numbers, sharing a `requestId` and a `message.id` and differing only in
  `apiBlockIndex`. Counting every line **overstated total tokens by 104%**. The adapter keeps one
  row per `requestId`.
- Everything so far is `speed: standard` and `service_tier: standard`. Fast mode and batch are
  priced differently, so the adapter warns if that ever changes rather than quietly misreporting.
- `cwd` gives the project name. Note `mba-advisor/mba_materials` shows up as its own project
  (`mba_materials`) because it's a subfolder — decide later whether to merge it into `mba-advisor`.

## Where prices come from

A script fetches current prices from Anthropic's public pricing page and writes them to a local
`prices.yaml` (model, input price, output price, cache multipliers, date fetched). The dashboard
**only ever reads `prices.yaml`** — it never calls the network while running.

This keeps prices current without breaking the privacy rule: the fetch sends no usage data, just
asks for a public page, and the result is a plain text file Katie can read and correct by hand.
`prices.yaml` is committed to git so the numbers are versioned.

## Chart and design rules

Colours live in `burnrate/theme.py`, never inline in `app.py`. Four rules, and they
are not stylistic preferences:

- **Colour follows the thing, not its rank.** "Cache read" is always the same pink,
  even when filtered. Assigning colours by position means filtering repaints the
  survivors and misleads anyone who had learned the chart.
- **The palette is validated, not chosen by eye.** Every adjacent pair of series
  colours stays distinguishable under the three main kinds of colour blindness. If
  you change a hue, re-run the check rather than trusting your eyes.
- **One series, one colour.** Shading bars darker-where-bigger just restates the bar
  length and wastes the only free channel.
- **Text never wears the series colour.** Labels use ink; the coloured mark beside
  them carries the identity. Several of these hues are illegible as text.

Forms to avoid, each for a reason: no pie or donut charts (people compare angles
badly — part-to-whole belongs in a stacked bar); no one-bar bar charts (that's a
number, so show a stat tile — this happens live when only one model is in range);
no dual axes; exactly one hero number per screen.

The theme is pinned to light mode in `.streamlit/config.toml`. An automatic dark
flip would use colours nobody validated. Real dark mode means re-deriving every hue
against the dark surface and re-running the check — worth doing deliberately, not
as a side effect.

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
- Wrote the real Claude Code adapter and inspected the actual logs. Two findings changed the cost
  math — the 1-hour cache, and duplicate log lines per API call. See "What the real logs turned
  out to say" above. Split `cache_write_tokens` into 5m/1h columns as a result.
- **Checkpoint 1 passed.** `uv run python scripts/accuracy_check.py` compares three real sessions
  against an independent hand calculation: all three agree to four decimal places.
- **Checkpoint 2 passed.** Dashboard built and running (`uv run streamlit run app.py`): scope
  banner, month-to-date tiles, forecast, daily trend, by model / project, tokens-vs-dollars,
  cost per session, savings suggestions. Calculations live in `analysis.py` and `insights.py`
  so they're testable; `app.py` is only layout.
- README written and verified against the actual install steps.
- 34 tests passing. Day-to-day: `uv run pytest`, `uv run python scripts/accuracy_check.py`.
- Decided to build on Claude Code logs alone. Found that `~/Library/Application Support/Claude/
  plan-usage-history.json` holds plan-limit utilisation samples (likely % of 5-hour and weekly
  limits) — a possible second source, deferred for now.
- **Design pass done.** Added `burnrate/theme.py` (validated palette, chart chrome) and
  `.streamlit/config.toml` (pinned light theme, Streamlit telemetry off). Replaced the two
  donut charts with one two-row stacked bar — the tokens-vs-dollars comparison now reads in a
  single glance instead of asking you to compare two pies. The "by model" panel becomes a stat
  tile when only one model is in range. Added a hero number, compact number formatting, and
  rounded bar ends with hairline gridlines. See "Chart and design rules" above.
### Where things stand at the end of session 1

All must-haves are built. Checkpoints 1 and 2 both passed. 34 tests green.

**To start up again:**

```bash
uv run streamlit run app.py          # the dashboard
uv run pytest                        # the tests
uv run python scripts/accuracy_check.py   # prove the cost maths against real logs
```

**Open decisions, none urgent:**

1. **Stretch goal** — Parley API usage logging was the pick, but it has the most unknowns. The
   fallback with the most value for the least work is the `plan-usage-history.json` source noted
   above: it's actual plan utilisation rather than estimated value, which speaks directly to
   "am I using the subscription well?". It needs confirming that `fh` / `cw` really are percent
   of the 5-hour and weekly limits — that reading is an inference from the data's shape, not
   something verified.
2. **`mba_materials`** currently counts as its own project because it's a subfolder of
   `mba-advisor`. Fold them together, or leave them separate?
3. **Dark mode** is deliberately off. Turning it on means re-deriving every chart colour against
   the dark surface and re-running the colourblind check.

**Known soft spots, worth saying out loud:**

- The forecast is a plain daily run-rate and will swing wildly early in a month. It refuses to
  show a number until 7 days in, which is honest but blunt.
- The accuracy check and the real code share `prices.yaml` and the same deduplication rule, so
  they confirm the arithmetic, not the choice to deduplicate by `requestId`. That choice rests
  on the log structure (identical usage, one `message.id`, differing `apiBlockIndex`), which is
  reasoning rather than a test.
- Only one model appears in the logs so far, so the by-model view has never been exercised with
  real variety.
