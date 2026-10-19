# Burn Rate 🔥

A dashboard that shows how much AI you've used and what it would have cost at
API prices. It runs on your own computer and your data never leaves it.

If you use Claude through a subscription or a university plan, you don't get an
itemised bill — so it's hard to know whether you're using a little or a lot.
Burn Rate reads Claude Code's local logs and puts a dollar figure on it.

**Those dollar figures are estimates, not bills.** They're *API-equivalent
value*: what the same usage would have cost at pay-as-you-go prices.

---

## What you need

- A Mac or Linux machine
- Claude Code, with some usage history (Burn Rate reads `~/.claude/projects/`)
- No Python required — the installer below brings its own

## Install

**1. Install `uv`** (a Python installer and package manager). One command, no
password needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal afterwards so it can find `uv`.

**2. Get Burn Rate and start it:**

```bash
git clone <this-repo-url> burn-rate && cd burn-rate && uv run streamlit run app.py
```

The first run takes a minute while `uv` downloads Python and the packages.
Your browser opens at <http://localhost:8501>.

That's it. There's no configuration, no API key, and no account to create.

> **No logs yet?** If you've never used Claude Code, there's nothing to read.
> Flip **Use my real logs** off in the sidebar to explore with sample data.

---

## What you'll see

| Section | What it tells you |
|---|---|
| **This month so far** | Estimated value, API calls, sessions, output tokens |
| **Month-end forecast** | Projected total at your current daily rate |
| **Daily trend** | Which days were expensive, and which were quiet |
| **Where it goes** | Broken down by model and by project |
| **Tokens vs. dollars** | Why "total tokens" is a misleading headline |
| **Cost per session** | Every working session, most expensive first |
| **Where you could save** | Findings with the arithmetic behind them |

The one most people find surprising is **Tokens vs. dollars**. Cache reads are
usually ~97% of your tokens but only ~50% of your cost, because re-reading
cached context is 90% cheaper than fresh input. A chart of "tokens used" is
really a chart of your caching behaviour.

## What it does *not* cover

Only **Claude Code** usage. Claude chat (claude.ai), Cowork, and other
platforms aren't included, because none of them keep usage records on your
machine.

---

## Your privacy

- Everything runs locally. No analytics, no telemetry, nothing uploaded.
- **Conversation text is never read.** The adapter takes token counts,
  timestamps, model names and folder names — and nothing else.
- No API keys or accounts are involved.

## Checking the numbers yourself

Costs are computed from `prices.yaml`, a plain text file you can read and edit.
It lists five prices for every model — input, output, the two kinds of cache
write, and cache read — and the dashboard never calls the network while running.

To update those prices:

```bash
uv run python scripts/refresh_prices.py
```

That downloads [LiteLLM's public price
list](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json),
keeps the models Anthropic sells directly, and rewrites `prices.yaml`. It
prints every price that changed before writing, and refuses to write at all if
the download looks wrong — so a bad fetch can't quietly wipe working prices.
Add `--dry-run` to see the changes without writing. It sends nothing about your
usage; it asks for a public file and that is the whole conversation.

`prices.yaml` is committed to git on purpose, so any price change shows up as a
reviewable diff rather than silently moving your numbers.

To verify the maths against your own logs:

```bash
uv run python scripts/accuracy_check.py
```

This re-reads the raw log files with a completely separate piece of code, does
the arithmetic from scratch, and compares. It prints every multiplication so
you can check it with a calculator.

To run the test suite:

```bash
uv run pytest
```

## Adding another data source

Each source has a small "adapter" in `burnrate/adapters/` that converts it into
one shared table format. To add one, write a file there that returns that
format — nothing else in the project needs to change. See
[`burnrate/schema.py`](burnrate/schema.py) for the columns and
[`fake.py`](burnrate/adapters/fake.py) for the simplest example.

## Two things about Claude Code's logs

Both will silently corrupt your numbers if you write your own version of this:

1. **One API call is written as several log lines** — one per content block,
   each repeating the same token counts. Counting every line overstates usage
   by about 100%. Deduplicate on `requestId`.
2. **Claude Code uses the 1-hour cache**, which costs 2.00x the input price,
   not the 1.25x of the 5-minute cache. The split is in
   `usage.cache_creation`.
3. **Cache reads are not a fixed share of the input price.** Most models charge
   10% of input, but `claude-opus-5-5` and `claude-sonnet-5-5` charge 5% and
   `claude-fable-5-1` charges 2.5%. Since cache reads are over 90% of the
   tokens in a typical session, assuming 10% everywhere is the single most
   expensive mistake available. Look the price up per model.
