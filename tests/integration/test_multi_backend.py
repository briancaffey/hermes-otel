"""Integration tests for the multi-backend fan-out.

These tests bypass ``init()`` and wire the TracerProvider with multiple
``BatchSpanProcessor``s manually so we can attach two ``InMemorySpanExporter``s
and prove that every span lands in *all* configured exporters.

The ``_init_otlp_pipeline`` end of the wiring is exercised separately in
``test_pipeline_resolution`` below — that test patches the OTLP
exporters/processors and verifies that one BackendConfig per yaml entry
materializes one processor + one metric reader (when supported).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from hermes_otel.plugin_config import BackendConfig, HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin

# ── End-to-end fan-out: two real exporters, one provider ────────────────────


@pytest.fixture()
def two_exporter_pipeline():
    """Wire a HermesOTelPlugin to two InMemorySpanExporters.

    Returns ``(exporter_a, exporter_b, plugin)``. Both exporters are
    attached as separate ``SimpleSpanProcessor``s on the same
    TracerProvider, mirroring how the multi-backend pipeline attaches
    one ``BatchSpanProcessor`` per backend.
    """
    import hermes_otel.tracer as tracer_mod
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter_a = InMemorySpanExporter()
    exporter_b = InMemorySpanExporter()
    resource = Resource.create({"service.name": "hermes-otel-test"})
    provider = TracerProvider(resource=resource)
    proc_a = SimpleSpanProcessor(exporter_a)
    proc_b = SimpleSpanProcessor(exporter_b)
    provider.add_span_processor(proc_a)
    provider.add_span_processor(proc_b)

    plugin = HermesOTelPlugin()
    plugin.tracer = provider.get_tracer("hermes-otel-test")
    plugin._initialized = True
    plugin._span_processors = [proc_a, proc_b]
    plugin._span_processor = proc_a  # back-compat alias

    tracer_mod._tracer = plugin

    yield exporter_a, exporter_b, plugin

    exporter_a.clear()
    exporter_b.clear()
    provider.shutdown()
    tracer_mod._tracer = None


class TestSpanFanOut:
    def test_span_lands_in_every_exporter(self, two_exporter_pipeline):
        """A single span.end() must propagate to every attached processor."""
        exp_a, exp_b, plugin = two_exporter_pipeline

        plugin.start_span(name="fanout", key="k1", kind="general")
        plugin.end_span("k1", status="ok")

        spans_a = exp_a.get_finished_spans()
        spans_b = exp_b.get_finished_spans()
        assert len(spans_a) == 1
        assert len(spans_b) == 1
        assert spans_a[0].name == "fanout"
        assert spans_b[0].name == "fanout"
        # Same trace_id/span_id — it's the same logical span exported twice.
        assert spans_a[0].context.trace_id == spans_b[0].context.trace_id
        assert spans_a[0].context.span_id == spans_b[0].context.span_id

    def test_session_trace_complete_in_both_exporters(self, two_exporter_pipeline):
        """A nested session/api/tool tree must arrive intact in both backends."""
        from hermes_otel.hooks import (
            on_post_api_request,
            on_post_tool_call,
            on_pre_api_request,
            on_pre_tool_call,
            on_session_end,
            on_session_start,
        )

        exp_a, exp_b, _ = two_exporter_pipeline

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_api_request(
            task_id="api1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=1, tool_count=1,
            approx_input_tokens=10, request_char_count=20, max_tokens=100,
        )
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1", session_id="s1")
        on_post_tool_call(tool_name="bash", args={}, result="ok",
                          task_id="t1", session_id="s1")
        on_post_api_request(
            task_id="api1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.01, finish_reason="stop",
            message_count=1, response_model="gpt-4",
            usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            assistant_content_chars=1, assistant_tool_call_count=1,
        )
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")

        names_a = sorted(s.name for s in exp_a.get_finished_spans())
        names_b = sorted(s.name for s in exp_b.get_finished_spans())
        assert names_a == names_b
        assert "agent" in names_a
        assert any(n.startswith("api.") for n in names_a)
        assert any(n.startswith("tool.") for n in names_a)


class TestForceFlushFansOut:
    def test_force_flush_calls_every_processor(self):
        """``_force_flush`` must drain every processor, not just the first."""
        plugin = HermesOTelPlugin()
        proc1 = MagicMock()
        proc2 = MagicMock()
        proc3 = MagicMock()
        plugin._span_processors = [proc1, proc2, proc3]
        plugin._span_processor = proc1

        plugin._force_flush()

        for p in (proc1, proc2, proc3):
            p.force_flush.assert_called_once_with(timeout_millis=2000)

    def test_force_flush_falls_back_to_singular_alias(self):
        """Test fixtures populate ``_span_processor`` only — must still flush."""
        plugin = HermesOTelPlugin()
        proc = MagicMock()
        plugin._span_processors = []
        plugin._span_processor = proc

        plugin._force_flush()

        proc.force_flush.assert_called_once_with(timeout_millis=2000)


# ── Pipeline resolution: yaml backends → processors ────────────────────────


def _clear_backend_env(monkeypatch):
    for var in [
        "OTEL_PHOENIX_ENDPOINT", "OTEL_PROJECT_NAME",
        "LANGSMITH_TRACING", "LANGSMITH_API_KEY",
        "OTEL_LANGFUSE_PUBLIC_API_KEY", "OTEL_LANGFUSE_SECRET_API_KEY",
        "OTEL_LANGFUSE_ENDPOINT",
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
        "OTEL_SIGNOZ_ENDPOINT", "OTEL_SIGNOZ_INGESTION_KEY",
        "OTEL_JAEGER_ENDPOINT", "OTEL_TEMPO_ENDPOINT",
    ]:
        monkeypatch.delenv(var, raising=False)


class TestConfigBackendsRouting:
    def test_two_backends_each_get_their_own_processor(self, monkeypatch):
        """One BackendConfig per entry → one BatchSpanProcessor per entry."""
        _clear_backend_env(monkeypatch)
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="phoenix", endpoint="http://phoenix:6006/v1/traces"),
                BackendConfig(type="jaeger", endpoint="http://jaeger:4318/v1/traces"),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)

        with patch("hermes_otel.tracer.OTLPSpanExporter"), \
             patch("hermes_otel.tracer.OTLPMetricExporter"), \
             patch("hermes_otel.tracer.trace.set_tracer_provider"), \
             patch("hermes_otel.tracer.metrics.set_meter_provider"):
            assert plugin.init() is True

        assert len(plugin._span_processors) == 2
        # Phoenix supports metrics; Jaeger doesn't → exactly 1 metric reader.
        assert len(plugin._metric_readers) == 1
        # Singular aliases point at the first entry.
        assert plugin._span_processor is plugin._span_processors[0]

    def test_three_metrics_capable_backends(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="phoenix", endpoint="http://a/v1/traces"),
                BackendConfig(type="signoz", endpoint="http://b/v1/traces"),
                BackendConfig(type="otlp", endpoint="http://c/v1/traces", name="MyCollector"),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with patch("hermes_otel.tracer.OTLPSpanExporter"), \
             patch("hermes_otel.tracer.OTLPMetricExporter"), \
             patch("hermes_otel.tracer.trace.set_tracer_provider"), \
             patch("hermes_otel.tracer.metrics.set_meter_provider"):
            assert plugin.init() is True
        assert len(plugin._span_processors) == 3
        assert len(plugin._metric_readers) == 3

    def test_config_backends_takes_priority_over_env(self, monkeypatch):
        """When config.backends is set, env vars must not add a second backend."""
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://env-phoenix/v1/traces")
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="jaeger", endpoint="http://yaml-jaeger/v1/traces"),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with patch("hermes_otel.tracer.OTLPSpanExporter") as mock_exp, \
             patch("hermes_otel.tracer.OTLPMetricExporter"), \
             patch("hermes_otel.tracer.trace.set_tracer_provider"), \
             patch("hermes_otel.tracer.metrics.set_meter_provider"):
            assert plugin.init() is True
        # Exactly one trace exporter (jaeger) — env phoenix ignored.
        endpoints = [call.kwargs.get("endpoint") for call in mock_exp.call_args_list]
        assert endpoints == ["http://yaml-jaeger/v1/traces"]

    def test_invalid_backend_skipped_others_still_initialize(self, monkeypatch):
        """A bad entry (missing endpoint) is skipped; valid entries still initialize."""
        _clear_backend_env(monkeypatch)
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="phoenix"),  # no endpoint → raises
                BackendConfig(type="jaeger", endpoint="http://j/v1/traces"),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with patch("hermes_otel.tracer.OTLPSpanExporter"), \
             patch("hermes_otel.tracer.OTLPMetricExporter"), \
             patch("hermes_otel.tracer.trace.set_tracer_provider"), \
             patch("hermes_otel.tracer.metrics.set_meter_provider"):
            assert plugin.init() is True
        # Only jaeger survived.
        assert len(plugin._span_processors) == 1

    def test_all_backends_invalid_returns_false(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        cfg = HermesOtelConfig(
            backends=(BackendConfig(type="phoenix"),),  # missing endpoint
        )
        plugin = HermesOTelPlugin(config=cfg)
        assert plugin.init() is False
        assert plugin.is_enabled is False


class TestLangfuseBackendConfig:
    def test_langfuse_credentials_via_env(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="langfuse", base_url="https://langfuse.example.com"),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with patch("hermes_otel.tracer.OTLPSpanExporter") as mock_exp, \
             patch("hermes_otel.tracer.trace.set_tracer_provider"):
            assert plugin.init() is True
        # Endpoint derived from base_url
        assert mock_exp.call_args.kwargs["endpoint"] == \
            "https://langfuse.example.com/api/public/otel/v1/traces"
        # Auth header present
        headers = mock_exp.call_args.kwargs["headers"]
        assert headers is not None and "Authorization" in headers
        # Langfuse → no metrics reader
        assert len(plugin._metric_readers) == 0


class TestSecretResolution:
    def test_signoz_ingestion_key_via_named_env(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("MY_SIGNOZ_KEY", "sz-secret")
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(
                    type="signoz",
                    endpoint="http://signoz/v1/traces",
                    ingestion_key_env="MY_SIGNOZ_KEY",
                ),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with patch("hermes_otel.tracer.OTLPSpanExporter") as mock_exp, \
             patch("hermes_otel.tracer.OTLPMetricExporter"), \
             patch("hermes_otel.tracer.trace.set_tracer_provider"), \
             patch("hermes_otel.tracer.metrics.set_meter_provider"):
            assert plugin.init() is True
        headers = mock_exp.call_args_list[0].kwargs["headers"]
        assert headers["signoz-ingestion-key"] == "sz-secret"
