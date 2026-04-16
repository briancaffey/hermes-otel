"""Shared fixtures for the hermes-otel test suite."""

import sys
from pathlib import Path

import pytest

# Ensure the plugin package is importable
PLUGIN_ROOT = Path(__file__).parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def _reset_otel_state():
    """Reset plugin singletons and module-level state between tests.

    The tracer module uses a global _tracer singleton, module-level dicts
    for per-session aggregation, and a ContextVar for the parent span
    stack. All must be cleared to prevent cross-test contamination.
    """
    import hermes_otel.tracer as tracer_mod
    import hermes_otel.hooks as hooks_mod

    def _reset():
        tracer_mod._tracer = None
        hooks_mod._SESSION_USAGE.clear()
        hooks_mod._SESSION_IO.clear()
        hooks_mod._TOOL_START_TIMES.clear()
        # Reset the parent stack ContextVar — pytest runs tests in the
        # same context, so the stack would otherwise bleed between tests.
        tracer_mod._PARENT_STACK.set(None)

    _reset()
    yield
    _reset()


@pytest.fixture()
def inmemory_otel_setup():
    """Create a HermesOTelPlugin wired to an InMemorySpanExporter.

    Returns (exporter, plugin). Tests can call exporter.get_finished_spans()
    to inspect exported spans without any network I/O.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import hermes_otel.tracer as tracer_mod
    from hermes_otel.tracer import HermesOTelPlugin

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "hermes-otel-test"})
    provider = TracerProvider(resource=resource)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)

    plugin = HermesOTelPlugin()
    plugin.tracer = provider.get_tracer("hermes-otel-test")
    plugin._initialized = True
    plugin._span_processor = processor

    # Patch the module singleton so get_tracer() returns our plugin
    tracer_mod._tracer = plugin

    yield exporter, plugin

    exporter.clear()
    provider.shutdown()
    tracer_mod._tracer = None


@pytest.fixture()
def inmemory_otel_with_metrics():
    """Like inmemory_otel_setup but also configures an InMemoryMetricReader.

    Returns (span_exporter, metric_reader, plugin).
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    import hermes_otel.tracer as tracer_mod
    from hermes_otel.tracer import HermesOTelPlugin

    # Spans
    span_exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "hermes-otel-test"})
    trace_provider = TracerProvider(resource=resource)
    processor = SimpleSpanProcessor(span_exporter)
    trace_provider.add_span_processor(processor)

    # Metrics
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    meter = meter_provider.get_meter("hermes-otel-test")

    plugin = HermesOTelPlugin()
    plugin.tracer = trace_provider.get_tracer("hermes-otel-test")
    plugin._initialized = True
    plugin._span_processor = processor

    # Wire up metric instruments
    plugin._meter = meter
    plugin._meter_provider = meter_provider
    plugin._metric_reader = metric_reader
    plugin._session_count = meter.create_counter("hermes.session.count")
    plugin._token_usage = meter.create_counter("hermes.token.usage")
    plugin._cost_usage = meter.create_counter("hermes.cost.usage")
    plugin._tool_duration = meter.create_histogram("hermes.tool.duration", unit="ms")
    plugin._message_count = meter.create_counter("hermes.message.count")
    plugin._model_usage = meter.create_counter("hermes.model.usage")

    tracer_mod._tracer = plugin

    yield span_exporter, metric_reader, plugin

    span_exporter.clear()
    trace_provider.shutdown()
    meter_provider.shutdown()
    tracer_mod._tracer = None
