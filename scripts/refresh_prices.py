"""Rewrite prices.yaml from LiteLLM's public price list.

Run it like this:

    uv run python scripts/refresh_prices.py              # fetch and write
    uv run python scripts/refresh_prices.py --dry-run    # show changes, write nothing
    uv run python scripts/refresh_prices.py --from-file x.json   # use a local copy

WHAT THIS DOES, in plain language
---------------------------------
LiteLLM keeps a big JSON file listing what every AI model costs. We download it,
keep only the models Anthropic and OpenAI sell directly (what Claude Code and
Codex actually talk to), convert the numbers into the shape prices.yaml wants,
and write the file.

Three things worth knowing:

1. LiteLLM quotes prices PER TOKEN (like 0.000005). We store them PER MILLION
   TOKENS (like 5.00), because that is how the vendors publish them and how the
   cost formula in burnrate/pricing.py is written. So every number gets
   multiplied by a million on the way in.

2. We take the prices straight from the file -- input, output, the two kinds of
   cache write, and cache read. We do NOT derive the cache prices by
   multiplying the input price. That used to be how prices.yaml worked, and it
   was wrong for three models: most models charge 10% of the input price for a
   cache read, but claude-opus-5-5 and claude-sonnet-5-5 charge 5%, and
   claude-fable-5-1 charges 2.5%. Reading the real number avoids the guess.

3. Not every model has every price, and that is a fact about the vendors. The
   5-minute / 1-hour cache write split is Anthropic's: no OpenAI model has a
   1-hour price. A missing optional price is written out as ABSENT, never as
   zero -- zero would silently price those tokens as free, while absent makes
   burnrate.pricing refuse to price usage that lands in that column.

PRIVACY: this sends nothing about your usage. It asks GitHub for a public file
and that is the whole conversation. Nothing else in Burn Rate touches the network.
"""

import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.request

import yaml

# The raw-file URL. The github.com/.../blob/... link shows a web page; this one
# returns the actual JSON.
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# The human-readable page, recorded in prices.yaml so the file says where it came from.
LITELLM_PAGE = (
    "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json"
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_PATH = os.path.join(REPO_ROOT, "prices.yaml")

TOKENS_PER_MILLION = 1_000_000

# The vendors we price. Claude Code talks to Anthropic; Codex talks to OpenAI.
PROVIDERS = ("anthropic", "openai")

# The kinds of model we price: text in, text out, charged per token.
#
# "responses" matters as much as "chat". OpenAI serves its coding models
# through the Responses API, so every gpt-*-codex model is listed as
# mode="responses". Accepting only "chat" would silently drop exactly the
# models Codex is most likely to use.
#
# Everything else -- image_generation, audio_transcription, audio_speech,
# realtime, embedding -- is priced per image or per second, not per token, and
# our cost formula would be meaningless for it.
TEXT_MODES = ("chat", "responses")

# LiteLLM's field name -> ours.
#
# Input and output are REQUIRED: every model charges for both, so a model
# missing either is broken and gets skipped rather than half-priced.
#
# The three cache prices are OPTIONAL, because not every vendor has every
# tier. The 5-minute / 1-hour cache split is Anthropic's design -- no OpenAI
# model publishes a 1-hour cache write price, and most publish no cache write
# price at all. A model that omits one is saying "no such tier", not "broken",
# and burnrate.pricing only demands a price where there are tokens to charge
# for it.
REQUIRED_FIELD_MAP = [
    ("input_cost_per_token", "input_per_mtok"),
    ("output_cost_per_token", "output_per_mtok"),
]

OPTIONAL_FIELD_MAP = [
    ("cache_creation_input_token_cost", "cache_write_5m_per_mtok"),
    ("cache_creation_input_token_cost_above_1hr", "cache_write_1h_per_mtok"),
    ("cache_read_input_token_cost", "cache_read_per_mtok"),
]

FIELD_MAP = REQUIRED_FIELD_MAP + OPTIONAL_FIELD_MAP

OUR_FIELDS = [ours for _, ours in FIELD_MAP]
REQUIRED_FIELDS = [ours for _, ours in REQUIRED_FIELD_MAP]


def fetch(url: str) -> dict:
    """Download the price list."""
    print(f"Fetching {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_local(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def per_million(per_token: float) -> float:
    """Per-token price -> per-million-token price.

    Rounded to 6 decimal places only to clean up floating-point dust: 6.25e-06
    times a million can land on 6.249999999999999 otherwise. No real price has
    anything like that much precision.
    """
    return round(per_token * TOKENS_PER_MILLION, 6)


def extract_models(raw: dict, providers=PROVIDERS) -> tuple[dict, list[str], int]:
    """Pull out the models these vendors sell directly.

    LiteLLM lists the same model many times over -- once for the vendor's own
    API, and again for Amazon Bedrock, Google Vertex, Azure, GitHub Copilot
    and so on, each with its own key and its own prices. We want only the
    direct ones, which are exactly the entries whose `litellm_provider` is the
    bare vendor name. Those also have plain keys like "claude-opus-5" and
    "gpt-6.1-sol", which is precisely what Claude Code and Codex write into
    their logs, so no name translation is needed.

    Returns the models we could price, and a list of complaints about any we
    could not.
    """
    models = {}
    skipped = []
    not_text = 0
    wanted = set(providers)

    for name, entry in sorted(raw.items()):
        if not isinstance(entry, dict):
            continue
        provider = entry.get("litellm_provider")
        if provider not in wanted:
            continue
        if name.startswith("ft:"):
            # Fine-tune PRICING TEMPLATES, not model ids. A real fine-tune
            # reports itself as "ft:gpt-4o-2024-11-20:someorg::abc123", which
            # would never match this key anyway, so keeping them only adds
            # rows that can never be looked up.
            not_text += 1
            continue
        if entry.get("mode") not in TEXT_MODES:
            # Images, audio and realtime are priced by the image or the
            # second. Counted, not listed: there are dozens and their absence
            # is by design, so listing them would bury the real problems.
            not_text += 1
            continue

        priced = {}
        missing = []
        for theirs, ours in FIELD_MAP:
            value = entry.get(theirs)
            if value is None:
                # Required fields are a problem; optional ones just mean the
                # vendor has no such tier, so we leave the field out entirely
                # rather than writing a zero that would price tokens at free.
                if ours in REQUIRED_FIELDS:
                    missing.append(theirs)
                continue
            priced[ours] = per_million(float(value))

        if missing:
            skipped.append(f"{name}: no {', '.join(missing)}")
            continue

        bad = [field for field, value in priced.items() if value <= 0]
        if bad:
            skipped.append(f"{name}: price of zero or less for {', '.join(bad)}")
            continue

        priced["provider"] = provider
        models[name] = priced

    return models, skipped, not_text


def sanity_check(models: dict) -> list[str]:
    """Flag anything that looks wrong before we overwrite a working file.

    These are not style rules -- each one has caught, or would catch, a real
    kind of mistake:

    * Output should cost more than input. It always has, for every Claude model.
    * A cache read should be cheaper than fresh input, since that discount is
      the entire reason caching exists.
    * A cache write should cost more than fresh input, for the same reason in
      reverse: you pay a premium up front to save later.
    """
    warnings = []
    for name, p in sorted(models.items()):
        # Each rule only applies when the prices it compares are both present.
        # A model with no cache write price is not breaking the rule about
        # cache writes; it simply has no cache writes to charge for.
        def has(*fields):
            return all(field in p for field in fields)

        if has("output_per_mtok", "input_per_mtok"):
            if p["output_per_mtok"] <= p["input_per_mtok"]:
                warnings.append(f"{name}: output is not dearer than input")
        if has("cache_read_per_mtok", "input_per_mtok"):
            if p["cache_read_per_mtok"] >= p["input_per_mtok"]:
                warnings.append(f"{name}: cache read is not cheaper than input")
        if has("cache_write_5m_per_mtok", "input_per_mtok"):
            if p["cache_write_5m_per_mtok"] <= p["input_per_mtok"]:
                warnings.append(f"{name}: 5m cache write is not dearer than input")
        if has("cache_write_1h_per_mtok", "cache_write_5m_per_mtok"):
            if p["cache_write_1h_per_mtok"] <= p["cache_write_5m_per_mtok"]:
                warnings.append(f"{name}: 1h cache write is not dearer than the 5m one")
    return warnings


def read_existing(path: str) -> dict:
    """The prices prices.yaml is currently PRODUCING, for the before/after report.

    This resolves the old file's shape rather than reading it literally. Older
    versions stored only input and output, and worked the three cache prices out
    by multiplying the input price (1.25x, 2.00x, 0.10x), with the odd per-model
    override. Comparing against the literal file would report every cache price
    as "newly set" and bury the one number that genuinely moves.

    So: if a cache price is written down, use it; otherwise work out what the
    old file would have charged. Then the report shows real changes only.
    """
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    models = data.get("models") or {}
    multipliers = data.get("cache_multipliers") or {}
    write_5m = float(multipliers.get("write_5m", 1.25))
    write_1h = float(multipliers.get("write_1h", 2.00))
    read = float(multipliers.get("read", 0.10))

    # The `cache_multipliers` block is what makes a file old-style: it is the
    # thing cache prices used to be derived FROM. In a file that has one, an
    # absent cache price means "derive it". In a file that doesn't, an absent
    # cache price means "this vendor has no such tier", and filling it in from
    # a multiplier would invent a price nobody published — then report its
    # removal as a change on the next run.
    derived = bool(multipliers)

    effective = {}
    for name, entry in models.items():
        if "input_per_mtok" not in entry or "output_per_mtok" not in entry:
            continue
        input_price = float(entry["input_per_mtok"])
        fallback = {
            "cache_write_5m_per_mtok": input_price * write_5m,
            "cache_write_1h_per_mtok": input_price * write_1h,
            "cache_read_per_mtok": input_price * read,
        } if derived else {}

        prices = {}
        for field in OUR_FIELDS:
            value = entry.get(field, fallback.get(field))
            if value is not None:
                prices[field] = round(float(value), 6)
        effective[name] = prices

    return effective


def report_changes(old: dict, new: dict) -> None:
    """Say exactly what this run would change. Nothing moves silently."""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))

    changed = []
    for name in sorted(set(old) & set(new)):
        for field in OUR_FIELDS:
            before = old[name].get(field)
            after = new[name].get(field)
            if before is None and after is None:
                continue
            if before is None:
                changed.append(f"  {name:<28} {field:<24} (not set)  ->  ${after:g}")
            elif after is None:
                changed.append(f"  {name:<28} {field:<24} ${float(before):g}  ->  (no such tier)")
            elif abs(float(before) - after) > 1e-9:
                changed.append(
                    f"  {name:<28} {field:<24} ${float(before):g}  ->  ${after:g}"
                )

    print()
    by_provider = collections.Counter(p.get("provider", "?") for p in new.values())
    breakdown = ", ".join(f"{count} {prov}" for prov, count in sorted(by_provider.items()))
    print(f"{len(new)} models priced ({breakdown}).")

    if added:
        print(f"\nAdded ({len(added)}):")
        for name in added:
            print(f"  {name}")
    if removed:
        print(f"\nREMOVED ({len(removed)}) -- these will no longer have a price:")
        for name in removed:
            print(f"  {name}")
    if changed:
        print(f"\nPrice changes ({len(changed)}):")
        for line in changed:
            print(line)
    if not (added or removed or changed):
        print("\nNo changes: prices.yaml already matches the source.")


def render_yaml(models: dict, fetched: str) -> str:
    """Build prices.yaml as text.

    Written out by hand rather than with yaml.dump so the explanatory comments
    survive. This file is meant to be read and corrected by a human.
    """
    lines = [
        "# API prices, in US dollars per MILLION tokens.",
        "#",
        "# This file is the single source of truth for every cost number in the dashboard.",
        "# The app never calls the network -- it only reads this file.",
        "# To update it, run:  uv run python scripts/refresh_prices.py",
        "#",
        "# You can also edit it by hand. If a number here looks wrong, fix it --",
        "# nothing else needs to change.",
        "#",
        "# Prices are listed per model rather than derived from the input price. The",
        "# cache WRITE prices are a steady 1.25x and 2.00x of input across every Claude",
        "# model, but cache READS are not: most charge 10% of the input price, while",
        "# claude-opus-5-5 and claude-sonnet-5-5 charge 5% and claude-fable-5-1 charges",
        "# 2.5%. Listing the real number for each leaves no rule to be wrong about.",
        "#",
        "# A model only lists the prices its vendor actually publishes. The 5-minute /",
        "# 1-hour cache write split is Anthropic's design -- no OpenAI model has a",
        "# 1-hour price, and most have no cache write price at all. An absent price",
        "# means 'no such tier', not 'free': burnrate.pricing refuses to price usage",
        "# that has tokens in a column whose price is missing.",
        "",
        f'fetched: "{fetched}"',
        f'source: "{LITELLM_PAGE}"',
        'note: "Generated by scripts/refresh_prices.py. LiteLLM is a third-party list'
        " that tracks vendors' published pricing; it is committed to git so any"
        ' change to these numbers shows up as a reviewable diff."',
        "",
        "models:",
    ]

    # Grouped by vendor, then by name, so the file reads like a price list
    # rather than an alphabetical jumble of two vendors.
    ordered = sorted(models, key=lambda n: (models[n].get("provider", ""), n))
    last_provider = None
    for name in ordered:
        p = models[name]
        provider = p.get("provider", "")
        if provider != last_provider:
            lines.append("")
            lines.append(f"  # ---- {provider} " + "-" * (58 - len(provider)))
            last_provider = provider
        lines.append(f"  {name}:")
        lines.append(f"    provider: {provider}")
        for field in OUR_FIELDS:
            if field in p:
                lines.append(f"    {field}: {p[field]:g}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change, but do not write prices.yaml",
    )
    parser.add_argument(
        "--from-file",
        metavar="PATH",
        help="read the price list from a local JSON file instead of downloading it",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=PRICES_PATH,
        help="where to write (defaults to prices.yaml at the repo root)",
    )
    args = parser.parse_args(argv)

    try:
        raw = load_local(args.from_file) if args.from_file else fetch(LITELLM_URL)
    except Exception as error:  # noqa: BLE001 -- show the user the real problem
        print(f"\nCould not read the price list: {error}", file=sys.stderr)
        print(
            "prices.yaml has NOT been touched, so the dashboard still works "
            "with the prices it already had.",
            file=sys.stderr,
        )
        return 1

    models, skipped, not_text = extract_models(raw)

    if not models:
        print(
            "\nFound no priceable models in that file. Refusing to overwrite "
            "prices.yaml with nothing.",
            file=sys.stderr,
        )
        return 1

    if not_text:
        print(
            f"\nIgnored {not_text} image / audio / realtime models "
            "(priced per image or per second, not per token)."
        )

    if skipped:
        print(f"\nSkipped {len(skipped)} text models with no usable prices:")
        for line in skipped:
            print(f"  {line}")

    warnings = sanity_check(models)
    if warnings:
        print("\nWARNING -- these prices look odd. Check them before trusting any cost:")
        for line in warnings:
            print(f"  {line}")

    report_changes(read_existing(args.out), models)

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    text = render_yaml(models, dt.date.today().isoformat())
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)

    print(f"\nWrote {args.out}")
    print("Now run:  uv run pytest  &&  uv run python scripts/accuracy_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
