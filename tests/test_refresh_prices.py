"""Tests for the price refresh script.

None of these touch the network. They feed the script small hand-written
versions of the LiteLLM file, so we can check it does the right thing with
input we control -- including input that is wrong.

The script rewrites the file every cost number depends on, so the thing most
worth testing is what it REFUSES to do.
"""

import json

import pytest
import yaml

from scripts import refresh_prices as refresh


def entry(input_cost, output_cost, write_5m, write_1h, read, **extra):
    """One LiteLLM-shaped model entry. Prices are per token, as in the real file."""
    record = {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": input_cost,
        "output_cost_per_token": output_cost,
        "cache_creation_input_token_cost": write_5m,
        "cache_creation_input_token_cost_above_1hr": write_1h,
        "cache_read_input_token_cost": read,
    }
    record.update(extra)
    return record


OPUS_5 = entry(5e-06, 2.5e-05, 6.25e-06, 1e-05, 5e-07)


# ---- converting per-token prices to per-million ------------------------


def test_per_token_becomes_per_million():
    assert refresh.per_million(5e-06) == 5.0
    assert refresh.per_million(2.5e-05) == 25.0
    assert refresh.per_million(5e-07) == 0.5


def test_floating_point_dust_is_rounded_away():
    """6.25e-06 x 1,000,000 can land on 6.249999999999999 in binary floats."""
    assert refresh.per_million(6.25e-06) == 6.25
    assert refresh.per_million(1.25e-07) == 0.125


# ---- picking out the right models --------------------------------------


def test_only_models_sold_direct_by_the_vendor_are_kept():
    """LiteLLM lists the same model once per platform, at different prices.

    Bedrock, Vertex and Azure copies are keyed differently and priced
    differently. Claude Code talks to Anthropic directly and Codex talks to
    OpenAI directly, so those copies would be the wrong prices under names our
    logs never use.
    """
    raw = {
        "claude-opus-5": OPUS_5,
        "gpt-6.1-sol": dict(OPUS_5, litellm_provider="openai"),
        "anthropic.claude-opus-5": dict(OPUS_5, litellm_provider="bedrock"),
        "vertex_ai/claude-opus-5": dict(OPUS_5, litellm_provider="vertex_ai"),
        "azure/gpt-6.1-sol": dict(OPUS_5, litellm_provider="azure"),
        "mistral-large": dict(OPUS_5, litellm_provider="mistral"),
    }
    models, _, _ = refresh.extract_models(raw)
    assert sorted(models) == ["claude-opus-5", "gpt-6.1-sol"]


def test_responses_api_models_are_kept():
    """OpenAI serves its CODING models through the Responses API.

    Every gpt-*-codex model is mode="responses", so accepting only "chat"
    would drop exactly the models Codex is most likely to be running.
    """
    raw = {"gpt-5.3-codex": dict(OPUS_5, litellm_provider="openai", mode="responses")}
    models, skipped, _ = refresh.extract_models(raw)
    assert list(models) == ["gpt-5.3-codex"]
    assert skipped == []


def test_models_that_are_not_text_are_ignored():
    """Images and audio are priced per image or per second, not per token."""
    raw = {
        "claude-opus-5": OPUS_5,
        "gpt-image-2": dict(OPUS_5, litellm_provider="openai", mode="image_generation"),
        "gpt-4o-tts": dict(OPUS_5, litellm_provider="openai", mode="audio_speech"),
    }
    models, skipped, not_text = refresh.extract_models(raw)
    assert list(models) == ["claude-opus-5"]
    assert not_text == 2
    # Counted, not listed as problems -- their absence is by design.
    assert skipped == []


def test_fine_tune_pricing_templates_are_ignored():
    """"ft:gpt-4o-2024-11-20" is a price template, not a model id.

    A real fine-tune reports itself as "ft:gpt-4o-2024-11-20:someorg::abc123",
    so the template key could never match a log line anyway.
    """
    raw = {
        "claude-opus-5": OPUS_5,
        "ft:gpt-4o-2024-11-20": dict(OPUS_5, litellm_provider="openai"),
    }
    models, _, _ = refresh.extract_models(raw)
    assert list(models) == ["claude-opus-5"]


def test_all_five_prices_are_extracted():
    models, skipped, _ = refresh.extract_models({"claude-opus-5": OPUS_5})
    assert skipped == []
    assert models["claude-opus-5"] == {
        "provider": "anthropic",
        "input_per_mtok": 5.0,
        "output_per_mtok": 25.0,
        "cache_write_5m_per_mtok": 6.25,
        "cache_write_1h_per_mtok": 10.0,
        "cache_read_per_mtok": 0.5,
    }


def test_a_model_missing_input_or_output_is_skipped_not_half_priced():
    """Half a price list is more dangerous than none -- it still adds up."""
    partial = dict(OPUS_5)
    del partial["output_cost_per_token"]

    models, skipped, _ = refresh.extract_models(
        {"claude-opus-5": OPUS_5, "claude-partial": partial}
    )

    assert list(models) == ["claude-opus-5"]
    assert any("output_cost_per_token" in line for line in skipped)


def test_a_missing_cache_price_means_no_such_tier_not_broken():
    """No OpenAI model has a 1-hour cache write price, and that is fine.

    The field is left OUT rather than written as zero. Zero would price those
    tokens as free; absent makes burnrate.pricing refuse to price usage that
    lands in that column at all.
    """
    openai_shaped = dict(OPUS_5, litellm_provider="openai")
    del openai_shaped["cache_creation_input_token_cost_above_1hr"]

    models, skipped, _ = refresh.extract_models({"gpt-6.1-sol": openai_shaped})

    assert skipped == []
    priced = models["gpt-6.1-sol"]
    assert "cache_write_1h_per_mtok" not in priced
    assert priced["cache_read_per_mtok"] == 0.5


def test_nonsense_prices_are_skipped():
    models, skipped, _ = refresh.extract_models(
        {"claude-free": entry(0, 2.5e-05, 6.25e-06, 1e-05, 5e-07)}
    )
    assert models == {}
    assert skipped


# ---- the sanity check --------------------------------------------------


def test_sane_prices_produce_no_warnings():
    models, _, _ = refresh.extract_models({"claude-opus-5": OPUS_5})
    assert refresh.sanity_check(models) == []


@pytest.mark.parametrize(
    "broken, expected",
    [
        (entry(5e-06, 1e-06, 6.25e-06, 1e-05, 5e-07), "output"),
        (entry(5e-06, 2.5e-05, 6.25e-06, 1e-05, 9e-06), "cache read"),
        (entry(5e-06, 2.5e-05, 1e-06, 1e-05, 5e-07), "5m cache write"),
        (entry(5e-06, 2.5e-05, 6.25e-06, 1e-06, 5e-07), "1h cache write"),
    ],
)
def test_prices_that_break_a_known_rule_are_flagged(broken, expected):
    """Output beats input, writes cost more than input, reads cost less.

    These have held for every Claude model ever published. A source that
    violates one is far more likely to be broken than to be news.
    """
    models, _, _ = refresh.extract_models({"claude-odd": broken})
    warnings = refresh.sanity_check(models)
    assert any(expected in line for line in warnings), warnings


# ---- the before/after report -------------------------------------------


def test_old_style_file_is_compared_on_what_it_actually_charged(tmp_path):
    """The old file derived cache prices from multipliers instead of listing them.

    Read literally, every cache price would look "newly set" and the one number
    that genuinely changes would be lost in the noise. read_existing resolves
    the multipliers so the report shows real changes only.
    """
    old = tmp_path / "prices.yaml"
    old.write_text(
        yaml.safe_dump(
            {
                "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.00, "read": 0.10},
                "models": {
                    "claude-opus-5": {"input_per_mtok": 5.0, "output_per_mtok": 25.0},
                    "claude-fable-5-1": {
                        "input_per_mtok": 10.0,
                        "output_per_mtok": 50.0,
                        "cache_read_per_mtok": 0.25,  # a hand-written override
                    },
                },
            }
        )
    )

    effective = refresh.read_existing(str(old))

    # Derived from the multipliers, because the file did not list them.
    assert effective["claude-opus-5"]["cache_write_5m_per_mtok"] == 6.25
    assert effective["claude-opus-5"]["cache_write_1h_per_mtok"] == 10.0
    assert effective["claude-opus-5"]["cache_read_per_mtok"] == 0.5
    # The override wins over the multiplier where one was written down.
    assert effective["claude-fable-5-1"]["cache_read_per_mtok"] == 0.25


def test_missing_file_is_not_an_error(tmp_path):
    assert refresh.read_existing(str(tmp_path / "nope.yaml")) == {}


# ---- writing the file --------------------------------------------------


def test_written_file_reloads_into_a_working_pricebook(tmp_path):
    """End to end: what the script writes must be what PriceBook can read."""
    from burnrate.pricing import PriceBook

    source = tmp_path / "litellm.json"
    source.write_text(json.dumps({"claude-opus-5": OPUS_5}))
    out = tmp_path / "prices.yaml"

    assert refresh.main(["--from-file", str(source), "--out", str(out)]) == 0

    book = PriceBook.load(str(out))
    assert book.known_models == ["claude-opus-5"]

    # The CLAUDE.md worked example, priced from the generated file.
    total = book.cost(
        "claude-opus-5",
        input_tokens=50_000,
        output_tokens=8_000,
        cache_write_5m_tokens=40_000,
        cache_write_1h_tokens=80_000,
        cache_read_tokens=900_000,
    )
    assert total == pytest.approx(1.95)


def test_an_empty_source_never_overwrites_a_good_file(tmp_path):
    """A bad download must not wipe prices the dashboard is still using."""
    out = tmp_path / "prices.yaml"
    out.write_text("models:\n  claude-opus-5:\n    input_per_mtok: 5.0\n")
    before = out.read_text()

    source = tmp_path / "litellm.json"
    source.write_text(json.dumps({"mistral-large": dict(OPUS_5, litellm_provider="mistral")}))

    assert refresh.main(["--from-file", str(source), "--out", str(out)]) == 1
    assert out.read_text() == before


def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "litellm.json"
    source.write_text(json.dumps({"claude-opus-5": OPUS_5}))
    out = tmp_path / "prices.yaml"

    assert refresh.main(
        ["--from-file", str(source), "--out", str(out), "--dry-run"]
    ) == 0
    assert not out.exists()
