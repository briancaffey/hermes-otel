"""Integration tests for BatchSpanProcessor wiring (Phase 2 of concurrency PRD).

Two test strategies are used here because the OTel `trace.set_tracer_provider()`
is a once-only global — we can't call `plugin.init()` more than once per
process. So:

  * wiring tests (`TestBatchProcessorInstalled`) use a REAL `init()` path
    (shared across the class).
  * behavior tests construct a plugin + BatchSpanProcessor directly, bypassing
    init(), so each test gets an independent processor/exporter pair.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


class _RecordingExporter:
    """Fake OTLP exporter that records exports instead of POSTing."""

    def __init__(self):
        self.exported: list = []
        self.export_count = 0
        self.export_event = threading.Event()

    def export(self, spans):
        self.exported.extend(spans)
        self.export_count += 1
        self.export_event.set()
        from opentelemetry.sdk.trace.export import SpanExportResult
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30_000):
        return True


# ── Strategy A: real init() path, module-scoped (once per session) ─────────


@pytest.fixture(scope="module")
def real_init_plugin():
    """One real init() per test module — verifies the _init_otlp wiring."""
    import os
    # Clear env so Phoenix branch wins deterministically.
    for var in [
        "OTEL_PHOENIX_ENDPOINT",
        "LANGSMITH_TRACING",
        "OTEL_LANGFUSE_PUBLIC_API_KEY", "OTEL_LANGFUSE_SECRET_API_KEY",
        "OTEL_SIGNOZ_ENDPOINT", "OTEL_JAEGER_ENDPOINT",
    ]:
        os.environ.pop(var, None)
    os.environ["OTEL_PHOENIX_ENDPOINT"] = "http://fake-collector/v1/traces"

    cfg = HermesOtelConfig(
        span_batch_schedule_delay_ms=50,
        span_batch_max_export_batch_size=64,
        span_batch_export_timeout_ms=1000,
    )
    plugin = HermesOTelPlugin(config=cfg)

    # Stub the metric exporter so init doesn't spin up a background reader
    # that floods the test logs with retry messages aimed at fake-collector.
    with patch("hermes_otel.tracer.OTLPMetricExporter") as mock_metrics:
        mock_metrics.return_value.export.return_value = None
        assert plugin.init() is True

    yield plugin

    try:
        plugin._span_processor.shutdown()
    except Exception:
        pass
    os.environ.pop("OTEL_PHOENIX_ENDPOINT", None)


class TestBatchProcessorInstalled:
    def test_processor_is_batch_instance(self, real_init_plugin):
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        assert isinstance(real_init_plugin._span_processor, BatchSpanProcessor)

    def test_config_plumbed_through(self, real_init_plugin):
        assert real_init_plugin.config.span_batch_schedule_delay_ms == 50
        assert real_init_plugin.config.span_batch_max_export_batch_size == 64

    def test_atexit_registered(self, real_init_plugin):
        assert real_init_plugin._atexit_registered is True

    def test_register_atexit_is_idempotent(self, real_init_plugin):
        before = real_init_plugin._atexit_registered
        real_init_plugin._register_atexit_flush()
        assert real_init_plugin._atexit_registered == before


# ── Strategy B: build the pipeline manually to test batch behavior ─────────


def _build_batch_plugin(schedule_delay_ms: int = 50):
    """Construct a plugin wired to a recording exporter via BatchSpanProcessor.

    Does NOT call plugin.init() — avoids mutating global OTel state.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = _RecordingExporter()
    resource = Resource.create({"service.name": "hermes-otel-test"})
    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(
        exporter,
        max_queue_size=2048,
        schedule_delay_millis=schedule_delay_ms,
        max_export_batch_size=64,
        export_timeout_millis=1000,
    )
    provider.add_span_processor(processor)

    cfg = HermesOtelConfig(
        span_batch_schedule_delay_ms=schedule_delay_ms,
        force_flush_on_session_end=True,
    )
    plugin = HermesOTelPlugin(config=cfg)
    plugin.tracer = provider.get_tracer("hermes-otel-test")
    plugin._initialized = True
    plugin._span_processor = processor

    return exporter, plugin, provider


@pytest.fixture()
def batch_pipeline():
    exporter, plugin, provider = _build_batch_plugin()

    # Swap singleton so hook callbacks use our plugin.
    import hermes_otel.tracer as tracer_mod
    tracer_mod._tracer = plugin

    yield exporter, plugin

    try:
        plugin._span_processor.shutdown()
    except Exception:
        pass
    provider.shutdown()
    tracer_mod._tracer = None


class TestBatchingBehavior:
    def test_end_span_does_not_flush_immediately(self, batch_pipeline):
        """end_span must NOT synchronously export — that's the whole point of batching."""
        exporter, plugin = batch_pipeline

        plugin.start_span(name="test", key="k1", kind="general")
        plugin.end_span("k1", status="ok")

        # Before the background worker wakes up, no export should have happened.
        time.sleep(0.001)
        assert exporter.export_count == 0, (
            "end_span synchronously triggered an export — BatchSpanProcessor "
            "is being force-flushed per span, defeating the queue."
        )

    def test_explicit_flush_drains_queue(self, batch_pipeline):
        exporter, plugin = batch_pipeline

        for i in range(5):
            plugin.start_span(name=f"test-{i}", key=f"k{i}", kind="general")
            plugin.end_span(f"k{i}", status="ok")

        assert exporter.export_count == 0
        plugin._force_flush()
        assert len(exporter.exported) == 5
        assert exporter.export_count >= 1

    def test_worker_eventually_exports_without_flush(self, batch_pipeline):
        """Within schedule_delay_millis the worker drains the queue on its own."""
        exporter, plugin = batch_pipeline

        plugin.start_span(name="test", key="k1", kind="general")
        plugin.end_span("k1", status="ok")

        assert exporter.export_event.wait(timeout=2.0), (
            "BatchSpanProcessor worker did not export within 2s"
        )
        assert len(exporter.exported) == 1


class TestSessionEndFlushes:
    def test_session_end_flushes_synchronously(self, batch_pipeline):
        """on_session_end must call _force_flush so UI sees the trace promptly."""
        from hermes_otel.hooks import on_session_end, on_session_start

        exporter, _ = batch_pipeline
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")

        # Flush was synchronous — no sleep.
        assert any(s.name == "agent" for s in exporter.exported), (
            f"agent span missing: {[s.name for s in exporter.exported]}"
        )

    def test_session_end_flush_opt_out(self, batch_pipeline):
        """With force_flush_on_session_end=False the worker handles it async."""
        from hermes_otel.hooks import on_session_end, on_session_start

        exporter, plugin = batch_pipeline
        # Frozen dataclass — use object.__setattr__ to toggle for this test.
        object.__setattr__(plugin.config, "force_flush_on_session_end", False)

        on_session_start(session_id="s_noflush", model="gpt-4", platform="cli")
        on_session_end(session_id="s_noflush", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")

        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any(s.name == "agent" for s in exporter.exported):
                break
            time.sleep(0.02)
        assert any(s.name == "agent" for s in exporter.exported)


class TestConcurrentSessions:
    """Two sessions driven on different threads must not cross-contaminate."""

    def test_two_threads_separate_trees(self, batch_pipeline):
        from hermes_otel.hooks import (
            on_post_api_request,
            on_post_tool_call,
            on_pre_api_request,
            on_pre_tool_call,
            on_session_end,
            on_session_start,
        )

        exporter, plugin = batch_pipeline

        def _run(session_id, tool_name):
            on_session_start(session_id=session_id, model="gpt-4", platform="cli")
            on_pre_api_request(
                task_id=f"api_{session_id}", session_id=session_id, platform="cli",
                model="gpt-4", provider="openai", base_url="", api_mode="chat",
                api_call_count=1, message_count=1, tool_count=1,
                approx_input_tokens=100, request_char_count=500, max_tokens=512,
            )
            on_pre_tool_call(tool_name=tool_name, args={}, task_id=f"t_{session_id}",
                             session_id=session_id)
            on_post_tool_call(tool_name=tool_name, args={}, result="ok",
                              task_id=f"t_{session_id}", session_id=session_id)
            on_post_api_request(
                task_id=f"api_{session_id}", session_id=session_id, platform="cli",
                model="gpt-4", provider="openai", base_url="", api_mode="chat",
                api_call_count=1, api_duration=0.1, finish_reason="stop",
                message_count=1, response_model="gpt-4",
                usage={"prompt_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                assistant_content_chars=20, assistant_tool_call_count=1,
            )
            on_session_end(session_id=session_id, completed=True, interrupted=False,
                           model="gpt-4", platform="cli")

        threads = [
            threading.Thread(target=_run, args=("A", "bash")),
            threading.Thread(target=_run, args=("B", "read")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        plugin._force_flush()

        # Each session emitted: agent + api + tool = 3 spans.
        assert len(exporter.exported) == 6, [s.name for s in exporter.exported]

        # Group by trace_id; each thread's spans must share one trace.
        traces: dict = {}
        for s in exporter.exported:
            traces.setdefault(s.context.trace_id, []).append(s)
        assert len(traces) == 2, f"expected 2 traces, got {len(traces)}"

        # Each trace must be internally consistent: tool parent is api, api parent is agent.
        for trace_spans in traces.values():
            names = {s.name for s in trace_spans}
            assert "agent" in names
            assert any(n.startswith("api.") for n in names)
            assert any(n.startswith("tool.") for n in names)
