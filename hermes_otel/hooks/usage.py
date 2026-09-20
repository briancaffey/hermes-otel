"""Token usage: normalization, dual-convention attributes and the usage metrics."""

from __future__ import annotations

from typing import Any, Dict

from ..helpers import to_int, truncate_string
from ._common import _as_dict

# Canonical token-total field order. Used when iterating or copying.
_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def _normalize_usage(usage: dict) -> Dict[str, int]:
    """Parse a raw hermes ``usage`` dict into canonical token totals.

    Hermes exposes ``output_tokens``; some providers use ``completion_tokens``.
    Similarly ``input_tokens`` vs ``prompt_tokens``. Total is derived from
    the reported value or sum(prompt, completion) when absent. ``reasoning_tokens``
    is a *subset* of ``completion_tokens`` (the thinking portion of the output),
    not an additive bucket, so it is never folded into ``total_tokens``. Returns
    all canonical fields, zero-filled.
    """
    if not isinstance(usage, dict):
        usage = _as_dict(usage)
    completion = to_int(usage.get("output_tokens") or usage.get("completion_tokens", 0))
    prompt = to_int(usage.get("prompt_tokens") or usage.get("input_tokens", 0))
    total = to_int(usage.get("total_tokens", 0)) or (prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_read_tokens": to_int(usage.get("cache_read_tokens")),
        "cache_write_tokens": to_int(usage.get("cache_write_tokens")),
        "reasoning_tokens": to_int(usage.get("reasoning_tokens")),
    }


def _usage_attributes(totals: Dict[str, int]) -> Dict[str, Any]:
    """Build dual-convention OTel attributes from canonical token totals.

    Emits both the OTel GenAI convention (``gen_ai.usage.*`` — recognised
    by Langfuse) and the OpenInference convention (``llm.token_count.*``
    — recognised by Phoenix). Cache attrs are included only when non-zero
    so low-traffic spans don't get cluttered with zero fields.
    """
    prompt = totals["prompt_tokens"]
    completion = totals["completion_tokens"]
    total = totals["total_tokens"]
    cache_read = totals["cache_read_tokens"]
    cache_write = totals["cache_write_tokens"]
    reasoning = totals.get("reasoning_tokens", 0)

    attrs: Dict[str, Any] = {
        # OpenInference (Phoenix)
        "llm.token_count.prompt": prompt,
        "llm.token_count.completion": completion,
        "llm.token_count.total": total,
        # OTel GenAI (Langfuse)
        "gen_ai.usage.input_tokens": prompt,
        "gen_ai.usage.output_tokens": completion,
        "gen_ai.usage.total_tokens": total,
    }
    if cache_read:
        attrs["llm.token_count.prompt_details.cache_read"] = cache_read
        # Current OTel GenAI spelling, plus the pre-existing alias for
        # backwards compatibility with dashboards created before this change.
        attrs["gen_ai.usage.cache_read.input_tokens"] = cache_read
        attrs["gen_ai.usage.cache_read_input_tokens"] = cache_read
    if cache_write:
        attrs["llm.token_count.prompt_details.cache_write"] = cache_write
        attrs["gen_ai.usage.cache_creation.input_tokens"] = cache_write
        attrs["gen_ai.usage.cache_creation_input_tokens"] = cache_write
    if reasoning:
        # Reasoning ("thinking") tokens are a subset of the output/completion
        # count, surfaced as a breakdown. OpenInference (Phoenix) reads
        # ``completion_details.reasoning``; OTel GenAI uses
        # ``gen_ai.usage.reasoning.output_tokens``.
        attrs["llm.token_count.completion_details.reasoning"] = reasoning
        attrs["gen_ai.usage.reasoning.output_tokens"] = reasoning
    return attrs


_USAGE_METRIC_LABELS = (
    ("prompt_tokens", "input"),
    ("completion_tokens", "output"),
    ("cache_read_tokens", "cacheRead"),
    ("cache_write_tokens", "cacheCreation"),
    ("reasoning_tokens", "reasoning"),
)


def _record_usage_metrics(tracer, totals: Dict[str, int], base_attrs: Dict[str, Any]) -> None:
    """Record one ``token_usage`` metric per non-zero canonical field."""
    for key, label in _USAGE_METRIC_LABELS:
        v = totals.get(key, 0)
        if v:
            tracer.record_metric("token_usage", v, {**base_attrs, "token_type": label})


def _record_prompt_cache_metrics(
    tracer,
    totals: Dict[str, int],
    available_fields: Any,
    base_attrs: Dict[str, Any],
) -> None:
    """Record cache hit/miss token counters when the provider reported cache usage.

    Hermes' ``prompt_tokens`` is the *whole* prompt — uncached input + cache
    reads + cache writes (``CanonicalUsage.prompt_tokens`` in
    ``agent/usage_pricing.py``). Hits are the cache reads; misses are the rest
    of the prompt (uncached input and cache writes), so ``hit + miss ==
    prompt_tokens`` and the weighted token hit rate is ``hit / (hit + miss)``.

    A provider that reports *any* cache accounting (a read or a write) is
    treated as supporting it, so a cold request that only wrote to the cache is
    an observed miss rather than "unknown". A request with neither is skipped —
    Hermes collapses "field absent" and "explicit zero" to ``0`` — unless the
    forward-compatible ``usage.available_fields`` side channel (proposed in
    NousResearch/hermes-agent#108249, not yet shipped) says the provider
    reported ``cache_read_tokens``.
    """
    cache_read = totals["cache_read_tokens"]
    cache_write = totals["cache_write_tokens"]
    cache_read_available = isinstance(available_fields, dict) and bool(
        available_fields.get("cache_read_tokens")
    )
    if not (cache_read or cache_write or cache_read_available):
        return

    cache_miss = max(0, totals["prompt_tokens"] - cache_read)
    if cache_read:
        tracer.record_metric(
            "prompt_cache_tokens", cache_read, {**base_attrs, "cache_result": "hit"}
        )
    if cache_miss:
        tracer.record_metric(
            "prompt_cache_tokens", cache_miss, {**base_attrs, "cache_result": "miss"}
        )
    tracer.record_metric(
        "prompt_cache_observations",
        1,
        {**base_attrs, "cache_result": "hit" if cache_read else "miss"},
    )


def _genai_metric_dims(
    model: Any,
    provider: Any,
    response_model: Any = None,
    operation: str = "chat",
) -> Dict[str, Any]:
    """Low-cardinality dimension set for the OTel GenAI spec metrics.

    Only stable, bounded dimensions (operation, provider, model names) — never
    per-call IDs like session_id — so the spec metrics stay aggregatable and
    match generic OTel-GenAI dashboards.
    """
    dims: Dict[str, Any] = {"gen_ai.operation.name": operation}
    prov = truncate_string(provider, 120) if provider else ""
    if prov:
        dims["gen_ai.provider.name"] = prov
    if model:
        dims["gen_ai.request.model"] = truncate_string(model, 200)
    if response_model:
        dims["gen_ai.response.model"] = truncate_string(response_model, 200)
    return dims


# Canonical-field -> OTel GenAI token.type value. The spec enumerates only
# ``input`` and ``output``; cache/reasoning buckets are subsets already counted
# in those, so they are intentionally not split into the spec metric.
_GENAI_TOKEN_TYPES = (
    ("prompt_tokens", "input"),
    ("completion_tokens", "output"),
)


def _record_genai_token_usage(
    tracer, metric_name: str, totals: Dict[str, int], dims: Dict[str, Any]
) -> None:
    """Record a GenAI-spec token-usage histogram point per non-zero type."""
    for key, token_type in _GENAI_TOKEN_TYPES:
        v = totals.get(key, 0)
        if v:
            tracer.record_metric(metric_name, v, {**dims, "gen_ai.token.type": token_type})
