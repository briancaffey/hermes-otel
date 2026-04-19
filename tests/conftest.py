"""Shared fixtures for the hermes-otel test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

import pytest

# Ensure the plugin package is importable
PLUGIN_ROOT = Path(__file__).parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def _reset_otel_state(monkeypatch, tmp_path_factory):
    """Reset plugin singletons and the ContextVar parent stack between tests.

    Resetting the tracer singleton to ``None`` transitively resets all
    per-session aggregators (they live on ``tracer.sessions``). The only
    state that escapes the tracer is the ``_PARENT_STACK`` ContextVar —
    pytest runs tests in the same context, so it would otherwise bleed
    between tests.

    Also redirects ``DEFAULT_CONFIG_PATH`` to a nonexistent temp location
    so tests never pick up the user's real ``~/.hermes/plugins/
    hermes_otel/config.yaml`` (which can define backends and change the
    init() code path unexpectedly).
    """
    import hermes_otel.plugin_config as plugin_config_mod
    import hermes_otel.tracer as tracer_mod

    fake_path = tmp_path_factory.mktemp("isolated-config") / "nonexistent.yaml"
    monkeypatch.setattr(plugin_config_mod, "DEFAULT_CONFIG_PATH", fake_path)

    def _reset():
        tracer_mod._tracer = None
        tracer_mod._PARENT_STACK.set(None)

    _reset()
    yield
    _reset()


def _build_inmemory_plugin(n_exporters: int = 1):
    """Wire a ``HermesOTelPlugin`` to N ``InMemorySpanExporter``s.

    Bypasses :meth:`HermesOTelPlugin.init` (which is a once-per-process
    operation against OTel's global ``TracerProvider``) and instead
    constructs a fresh ``TracerProvider`` with N ``SimpleSpanProcessor``s,
    one per exporter. Each test thus gets its own isolated pipeline.

    Returns ``(exporters, plugin, provider)``. The caller registers the
    plugin as the module singleton (so hook callbacks find it) and is
    responsible for ``provider.shutdown()`` on teardown.
    """
    from hermes_otel.tracer import HermesOTelPlugin
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporters = [InMemorySpanExporter() for _ in range(n_exporters)]
    resource = Resource.create({"service.name": "hermes-otel-test"})
    provider = TracerProvider(resource=resource)
    processors = []
    for exp in exporters:
        proc = SimpleSpanProcessor(exp)
        provider.add_span_processor(proc)
        processors.append(proc)

    plugin = HermesOTelPlugin()
    plugin.tracer = provider.get_tracer("hermes-otel-test")
    plugin._initialized = True
    plugin._span_processors = processors
    plugin._span_processor = processors[0]
    return exporters, plugin, provider


def _install_as_singleton(plugin) -> None:
    """Make hooks' ``get_tracer()`` return ``plugin``."""
    import hermes_otel.tracer as tracer_mod

    tracer_mod._tracer = plugin


@pytest.fixture()
def inmemory_otel_setup():
    """Single ``InMemorySpanExporter`` + a plugin wired to use it.

    Returns ``(exporter, plugin)``. Tests inspect exporter.get_finished_spans()
    to assert on what would have been sent to a real OTLP collector.
    """
    exporters, plugin, provider = _build_inmemory_plugin(n_exporters=1)
    _install_as_singleton(plugin)
    try:
        yield exporters[0], plugin
    finally:
        exporters[0].clear()
        provider.shutdown()


@pytest.fixture()
def two_exporter_pipeline() -> Tuple:
    """Two ``InMemorySpanExporter``s + a plugin fanning out to both.

    Returns ``(exporter_a, exporter_b, plugin)``. Each ``span.end()``
    lands in both exporters — mirrors the multi-backend fan-out in
    production where each backend gets its own ``BatchSpanProcessor``.
    """
    exporters, plugin, provider = _build_inmemory_plugin(n_exporters=2)
    _install_as_singleton(plugin)
    try:
        yield exporters[0], exporters[1], plugin
    finally:
        for exp in exporters:
            exp.clear()
        provider.shutdown()


@pytest.fixture()
def inmemory_otel_with_metrics():
    """``inmemory_otel_setup`` plus an attached ``InMemoryMetricReader``.

    Returns ``(span_exporter, metric_reader, plugin)``.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource

    exporters, plugin, trace_provider = _build_inmemory_plugin(n_exporters=1)

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        resource=Resource.create({"service.name": "hermes-otel-test"}),
        metric_readers=[metric_reader],
    )
    plugin._meter = meter_provider.get_meter("hermes-otel-test")
    plugin._meter_provider = meter_provider
    plugin._metric_reader = metric_reader
    plugin._create_metric_instruments()

    _install_as_singleton(plugin)
    try:
        yield exporters[0], metric_reader, plugin
    finally:
        exporters[0].clear()
        trace_provider.shutdown()
        meter_provider.shutdown()


# Re-export helpers so test modules can build their own variations
# without duplicating the OTel-SDK wiring boilerplate.
__all__: List[str] = [
    "_build_inmemory_plugin",
    "_install_as_singleton",
]
