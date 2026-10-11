"""Per-backend stripping of the turn's token roll-up from the agent root (#327).

Opik and LangWatch compute trace-level usage by summing ``gen_ai.usage.*``
over every span, so the roll-up the plugin puts on the ``agent`` root
doubled their trace totals (and Opik's estimated cost). The fix is bound
to one backend's exporter: that backend's copy of an AGENT-kind span loses
its usage attributes, every other exporter still gets them.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig, HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin, _RootUsageStrippingExporter, strip_root_usage

ROLLUP = {
    "openinference.span.kind": "AGENT",
    "traceloop.span.kind": "AGENT",
    "gen_ai.usage.input_tokens": 14280,
    "gen_ai.usage.output_tokens": 120,
    "gen_ai.usage.total_tokens": 14400,
    "gen_ai.usage.cache_read.input_tokens": 1000,
    "llm.token_count.prompt": 14280,
    "llm.token_count.completion": 120,
    "llm.token_count.total": 14400,
    "hermes.cost.usage": 0.0123,
    "hermes.cost.status": "priced",
    "hermes.turn.tools": "terminal",
    "llm.model_name": "gpt-4o-mini",
}
API = {
    "openinference.span.kind": "LLM",
    "gen_ai.usage.input_tokens": 14280,
    "gen_ai.usage.output_tokens": 120,
    "gen_ai.usage.total_tokens": 14400,
    "llm.token_count.total": 14400,
}


def _two_exporters():
    """A provider fanning out to a stripped exporter (A) and a plain one (B)."""
    a, b = InMemorySpanExporter(), InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "t"}))
    provider.add_span_processor(SimpleSpanProcessor(_RootUsageStrippingExporter(a)))
    provider.add_span_processor(SimpleSpanProcessor(b))
    return a, b, provider


def _emit_turn(provider: TracerProvider) -> None:
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("agent", attributes=ROLLUP) as root:
        root.add_event("turn.started")
        with tracer.start_as_current_span(
            "llm.gpt-4o-mini", attributes={"openinference.span.kind": "LLM"}
        ):
            with tracer.start_as_current_span("api.gpt-4o-mini", attributes=API):
                pass
    provider.force_flush()


class TestStrippingExporter:
    def test_agent_root_loses_usage_for_that_exporter_only(self):
        a, b, provider = _two_exporters()
        _emit_turn(provider)
        try:
            stripped = {s.name: dict(s.attributes) for s in a.get_finished_spans()}
            plain = {s.name: dict(s.attributes) for s in b.get_finished_spans()}
        finally:
            provider.shutdown()
        root_a, root_b = stripped["agent"], plain["agent"]
        assert not [k for k in root_a if k.startswith(("gen_ai.usage.", "llm.token_count."))]
        assert root_b["gen_ai.usage.total_tokens"] == 14400
        assert root_b["llm.token_count.prompt"] == 14280
        # Everything that is not a token count stays on the stripped copy.
        assert root_a["hermes.cost.usage"] == 0.0123
        assert root_a["hermes.cost.status"] == "priced"
        assert root_a["hermes.turn.tools"] == "terminal"
        assert root_a["llm.model_name"] == "gpt-4o-mini"
        assert root_a["openinference.span.kind"] == "AGENT"
        # The api span keeps its own numbers on both sides; they are the truth.
        assert stripped["api.gpt-4o-mini"]["gen_ai.usage.total_tokens"] == 14400
        assert plain["api.gpt-4o-mini"] == stripped["api.gpt-4o-mini"]
        assert stripped["llm.gpt-4o-mini"] == plain["llm.gpt-4o-mini"]

    def test_rebuilt_root_keeps_identity_timing_events_and_status(self):
        a, b, provider = _two_exporters()
        _emit_turn(provider)
        try:
            ra = next(s for s in a.get_finished_spans() if s.name == "agent")
            rb = next(s for s in b.get_finished_spans() if s.name == "agent")
            child = next(s for s in a.get_finished_spans() if s.name == "llm.gpt-4o-mini")
        finally:
            provider.shutdown()
        assert ra.get_span_context().span_id == rb.get_span_context().span_id
        assert ra.get_span_context().trace_id == rb.get_span_context().trace_id
        assert child.parent.span_id == ra.get_span_context().span_id
        assert ra.start_time == rb.start_time and ra.end_time == rb.end_time
        assert [e.name for e in ra.events] == ["turn.started"]
        assert ra.status.status_code == rb.status.status_code
        assert ra.kind == rb.kind
        assert ra.resource.attributes["service.name"] == "t"
        assert ra.instrumentation_scope.name == "t"

    def test_batches_without_a_rollup_pass_through_unchanged(self):
        a, b, provider = _two_exporters()
        tracer = provider.get_tracer("t")
        with tracer.start_as_current_span(
            "tool.terminal", attributes={"openinference.span.kind": "TOOL"}
        ):
            pass
        # An agent span that carries no usage (a turn with no API call) is
        # not rebuilt either.
        with tracer.start_as_current_span("agent", attributes={"openinference.span.kind": "AGENT"}):
            pass
        provider.force_flush()
        try:
            names_a = [s.name for s in a.get_finished_spans()]
            names_b = [s.name for s in b.get_finished_spans()]
        finally:
            provider.shutdown()
        assert names_a == names_b == ["tool.terminal", "agent"]

    def test_strip_helper_is_pure_and_order_preserving(self):
        a, b, provider = _two_exporters()
        _emit_turn(provider)
        try:
            spans = list(b.get_finished_spans())
        finally:
            provider.shutdown()
        out = strip_root_usage(spans)
        assert [s.name for s in out] == [s.name for s in spans]
        assert out[0] is spans[0]  # api span: same object
        assert dict(spans[-1].attributes)["gen_ai.usage.total_tokens"] == 14400  # input untouched
        assert "gen_ai.usage.total_tokens" not in dict(out[-1].attributes)
        assert strip_root_usage([]) == []

    def test_wrapper_delegates_shutdown_and_flush(self):
        inner = InMemorySpanExporter()
        w = _RootUsageStrippingExporter(inner)
        assert w.force_flush() is True
        w.shutdown()
        assert inner._stopped is True


class TestResolvePreset:
    def test_opik_and_langwatch_default_off(self):
        assert (
            backends.resolve(BackendConfig(type="opik", endpoint="http://x:5173")).root_usage
            is False
        )
        assert (
            backends.resolve(
                BackendConfig(type="langwatch", api_key="k", endpoint="http://x:5560")
            ).root_usage
            is False
        )

    def test_other_types_default_on(self):
        assert backends.resolve(BackendConfig(type="phoenix", endpoint="http://x:6006")).root_usage
        assert (
            backends.resolve(
                BackendConfig(type="langfuse", public_key="p", secret_key="s", endpoint="http://x")
            ).root_usage
            is True
        )
        assert backends.resolve(BackendConfig(type="otlp", endpoint="http://x:4318")).root_usage

    def test_explicit_entry_value_wins_both_ways(self):
        assert (
            backends.resolve(
                BackendConfig(type="opik", endpoint="http://x:5173", root_usage=True)
            ).root_usage
            is True
        )
        assert (
            backends.resolve(
                BackendConfig(type="phoenix", endpoint="http://x:6006", root_usage=False)
            ).root_usage
            is False
        )

    def test_yaml_entry_parses_the_bool(self, tmp_path):
        from hermes_otel.plugin_config import load_config

        cfg_file = tmp_path / "hermes_otel.yaml"
        cfg_file.write_text(
            "backends:\n"
            "  - type: opik\n"
            "    endpoint: http://x:5173\n"
            "    root_usage: true\n"
            "  - type: phoenix\n"
            "    endpoint: http://x:6006\n"
            "    root_usage: 'false'\n"
            "  - type: otlp\n"
            "    endpoint: http://x:4318\n"
        )
        cfg = load_config(path=cfg_file)
        by_type = {b.type: b for b in cfg.backends}
        assert by_type["opik"].root_usage is True
        assert by_type["phoenix"].root_usage is False
        assert by_type["otlp"].root_usage is None


class TestPipelineWiring:
    def test_only_the_preset_backend_gets_the_wrapper(self, monkeypatch):
        for var in list(os.environ):
            if var.startswith(("OTEL_", "LANGFUSE_", "LANGSMITH_", "PHOENIX_")):
                monkeypatch.delenv(var, raising=False)
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="opik", endpoint="http://localhost:5173"),
                BackendConfig(type="phoenix", endpoint="http://localhost:6006"),
            ),
            dashboard_live=False,
        )
        plugin = HermesOTelPlugin(config=cfg)
        seen = []

        import hermes_otel.tracer as tracer_mod

        real_bsp = tracer_mod.BatchSpanProcessor

        def _spy(exporter, **kwargs):
            seen.append(exporter)
            return real_bsp(exporter, **kwargs)

        with (
            patch("hermes_otel.tracer.BatchSpanProcessor", side_effect=_spy),
            patch("hermes_otel.tracer.trace.set_tracer_provider"),
        ):
            assert plugin.init() is True
        try:
            inners = [getattr(e, "_inner", None) for e in seen]
            assert len(inners) == 2
            assert isinstance(inners[0], _RootUsageStrippingExporter)  # opik
            assert not isinstance(inners[1], _RootUsageStrippingExporter)  # phoenix
        finally:
            plugin.shutdown()
