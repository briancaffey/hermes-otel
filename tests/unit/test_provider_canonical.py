"""#475: provider ids map to the conventions' well-known spellings; the raw id stays."""

from __future__ import annotations

import pytest

from hermes_otel.hooks.attributes import (
    PROVIDER_CANONICAL,
    _metric_model_labels,
    _model_attributes,
    _provider_attributes,
    canonical_provider,
)

GEN_AI_WELL_KNOWN = {
    "openai",
    "gcp.gen_ai",
    "gcp.vertex_ai",
    "gcp.gemini",
    "anthropic",
    "cohere",
    "azure.ai.inference",
    "azure.ai.openai",
    "ibm.watsonx.ai",
    "aws.bedrock",
    "perplexity",
    "x_ai",
    "deepseek",
    "groq",
    "mistral_ai",
}
OPENINFERENCE_WELL_KNOWN = {
    "anthropic",
    "aws",
    "azure",
    "cerebras",
    "cohere",
    "deepseek",
    "fireworks",
    "google",
    "groq",
    "meta",
    "minimax",
    "mistralai",
    "moonshot",
    "ollama",
    "openai",
    "oracle",
    "perplexity",
    "together",
    "typesafe",
    "xai",
    "zai",
}


class TestTable:
    def test_every_target_is_a_well_known_value(self):
        """The map may only produce spellings the conventions define."""
        for raw, (gen_ai, oi) in PROVIDER_CANONICAL.items():
            assert gen_ai is None or gen_ai in GEN_AI_WELL_KNOWN, raw
            assert oi is None or oi in OPENINFERENCE_WELL_KNOWN, raw
            assert gen_ai or oi, raw

    @pytest.mark.parametrize(
        "raw,gen_ai,oi",
        [
            ("xai", "x_ai", "xai"),
            ("xai-oauth", "x_ai", "xai"),
            ("bedrock", "aws.bedrock", "aws"),
            ("vertex", "gcp.vertex_ai", "google"),
            ("gemini", "gcp.gemini", "google"),
            ("azure-foundry", "azure.ai.inference", "azure"),
            ("openai-api", "openai", "openai"),
            ("openai-codex", "openai", "openai"),
            ("anthropic", "anthropic", "anthropic"),
            ("kimi-coding", "kimi-coding", "moonshot"),
            ("minimax-cn", "minimax-cn", "minimax"),
        ],
    )
    def test_mapped_ids(self, raw, gen_ai, oi):
        assert canonical_provider(raw) == (raw, gen_ai, oi)

    @pytest.mark.parametrize("raw", ["openrouter", "nvidia", "custom", "lmstudio", "nous", "moa"])
    def test_unmapped_ids_pass_through(self, raw):
        assert canonical_provider(raw) == (raw, raw, raw)

    def test_case_and_whitespace_only_affect_the_lookup(self):
        assert canonical_provider(" XAI ") == (" XAI ", "x_ai", "xai")

    def test_empty(self):
        assert canonical_provider(None) == ("", "", "")
        assert canonical_provider("") == ("", "", "")


class TestAttributes:
    def test_span_attributes_carry_both_spellings_and_the_raw_id(self):
        attrs = _model_attributes("grok-4", "xai")
        assert attrs["gen_ai.provider.name"] == attrs["gen_ai.system"] == "x_ai"
        assert attrs["llm.provider"] == "xai"
        assert attrs["hermes.provider"] == "xai"

    def test_provider_only(self):
        assert _provider_attributes("bedrock") == {
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.system": "aws.bedrock",
            "hermes.provider": "bedrock",
        }
        assert _provider_attributes("") == {}

    def test_unmapped_id_is_the_same_everywhere(self):
        attrs = _model_attributes("m", "openrouter")
        assert {
            attrs[k]
            for k in ("gen_ai.provider.name", "gen_ai.system", "llm.provider", "hermes.provider")
        } == {"openrouter"}

    def test_metric_label_matches_gen_ai_provider_name(self):
        assert _metric_model_labels("m", "vertex") == {"model": "m", "provider": "gcp.vertex_ai"}
        assert _metric_model_labels("m", "nvidia") == {"model": "m", "provider": "nvidia"}
        assert _metric_model_labels(None, None) == {}
