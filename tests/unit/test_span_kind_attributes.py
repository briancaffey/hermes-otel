"""Every span carries both span-kind conventions with their own vocabularies
(#382): OpenInference's uppercase values for Phoenix and Traceloop's lowercase
``agent`` / ``tool`` / ``task`` / ``unknown`` for openllmetry-aware consumers
such as opentelemetry-mcp, whose ``traceloop.span.kind == "tool"`` filter found
nothing while the plugin wrote ``TOOL`` into that attribute."""

from __future__ import annotations

import pytest

CASES = [
    ("agent", "AGENT", "agent"),
    ("tool", "TOOL", "tool"),
    ("llm", "LLM", "task"),
    ("general", "GENERAL", "task"),
    ("nonsense", "GENERAL", "unknown"),
]


@pytest.mark.parametrize("kind, openinference, traceloop", CASES)
def test_both_span_kind_conventions(inmemory_otel_setup, kind, openinference, traceloop):
    exporter, plugin = inmemory_otel_setup
    plugin.start_span(f"probe.{kind}", f"k-{kind}", kind=kind, session_id="s")
    plugin.end_span(f"k-{kind}")
    (span,) = exporter.get_finished_spans()
    assert span.attributes["openinference.span.kind"] == openinference
    assert span.attributes["traceloop.span.kind"] == traceloop


def test_traceloop_vocabulary_is_lowercase_traceloop_values():
    from hermes_otel.tracer import HermesOTelPlugin

    allowed = {"workflow", "task", "agent", "tool", "unknown"}
    assert set(HermesOTelPlugin._TRACELOOP_KIND_MAP.values()) <= allowed
    assert set(HermesOTelPlugin._TRACELOOP_KIND_MAP) == set(HermesOTelPlugin._KIND_MAP)
