"""The supported test seam (#411).

Tests used to reach into the package's private names (the tracer singleton,
the context-var parent stack, the live-store singleton, the redaction cache)
and the package kept aliases that existed only for them. This module is the
one place tests go instead; it ships with the plugin so a downstream
project's tests can use it too.

* :func:`reset_state` — forget the plugin singleton and every process-wide
  cache a previous test may have filled.
* :func:`install_singleton` — make the hooks' ``get_tracer()`` return a plugin.
* :func:`build_inmemory_plugin` — a ``HermesOTelPlugin`` exporting to in-memory
  span exporters, and optionally an in-memory metric reader and log exporter,
  without touching the OTel globals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import patch

from .plugin_config import HermesOtelConfig
from .tracer import HermesOTelPlugin, metric_views


def reset_state() -> None:
    """Forget the plugin singleton, the parent-stack ContextVar, the live-store
    singleton and the redaction cache. Shuts down a previous plugin's exporter
    threads and host sampler so nothing retries against a dead endpoint."""
    from . import live_store_registry as _ls
    from . import redaction as _red
    from . import span_tracker as _st
    from . import tracer as _tr

    current = _tr._tracer
    if current is not None:
        try:
            current.stop_host_metrics()
        except Exception:
            pass
        for obj in (
            list(getattr(current, "_metric_readers", []))
            + list(getattr(current, "_span_processors", []))
            + [getattr(current, "_logger_provider", None)]
        ):
            try:
                if obj is not None:
                    obj.shutdown()
            except Exception:
                pass
        handler = getattr(current, "_live_log_handler", None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)
    _tr._tracer = None
    _st._PARENT_STACK.set(None)
    store = _ls._LIVE_STORE
    if store is not None:
        try:
            store.close()
        except Exception:
            pass
    _ls._LIVE_STORE = None
    _red._hermes_resolved = False
    _red._hermes_redactor = None
    from .hooks import kanban as _kb

    _kb._clear_traceparent()


def install_singleton(plugin: Optional[HermesOTelPlugin]) -> Optional[HermesOTelPlugin]:
    """Make the hooks' ``get_tracer()`` return ``plugin``; returns the previous one.
    The parent-stack ContextVar is cleared so the new plugin starts from no turn."""
    from . import span_tracker as _st
    from . import tracer as _tr

    previous = _tr._tracer
    _tr._tracer = plugin
    _st._PARENT_STACK.set(None)
    return previous


@dataclass
class InMemoryPipeline:
    """What :func:`build_inmemory_plugin` wires: the plugin and its sinks."""

    plugin: HermesOTelPlugin
    span_exporters: List[Any]
    tracer_provider: Any
    metric_reader: Any = None
    meter_provider: Any = None
    log_exporter: Any = None
    _restore: List[Any] = field(default_factory=list)

    @property
    def span_exporter(self) -> Any:
        return self.span_exporters[0]

    def shutdown(self) -> None:
        for exp in self.span_exporters:
            try:
                exp.clear()
            except Exception:
                pass
        for provider in (self.meter_provider, self.tracer_provider):
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception:
                    pass
        if self.log_exporter is not None:
            from . import log_handler as _lh

            try:
                _lh.uninstall_handler(None)
            except Exception:
                pass
        for fn in self._restore:
            fn()


def build_inmemory_plugin(
    *,
    exporters: int = 1,
    metrics: bool = False,
    logs: bool = False,
    config: Optional[HermesOtelConfig] = None,
    service_name: str = "hermes-otel-test",
    profile_name: Optional[str] = None,
) -> InMemoryPipeline:
    """A ``HermesOTelPlugin`` exporting to ``exporters`` in-memory span
    exporters, plus an in-memory metric reader (``metrics``) and log exporter
    (``logs``), on providers of its own: the OTel globals are never touched,
    so every test gets an isolated pipeline. Call :func:`install_singleton`
    to make the hooks use it, and ``pipeline.shutdown()`` afterwards."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    resource = Resource.create({"service.name": service_name})
    span_exporters = [InMemorySpanExporter() for _ in range(exporters)]
    provider = TracerProvider(resource=resource)
    processors = []
    for exp in span_exporters:
        proc = SimpleSpanProcessor(exp)
        provider.add_span_processor(proc)
        processors.append(proc)

    plugin = HermesOTelPlugin(config=config)
    if profile_name is not None:
        plugin.profile_name = profile_name
    plugin.tracer = provider.get_tracer(service_name)
    plugin._tracer_provider = provider
    plugin._initialized = True
    plugin._span_processors = processors
    pipeline = InMemoryPipeline(
        plugin=plugin, span_exporters=span_exporters, tracer_provider=provider
    )

    if metrics:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        reader = InMemoryMetricReader()
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
            views=metric_views(plugin.config.metrics_histogram),
        )
        plugin._meter_provider = meter_provider
        plugin._meter = meter_provider.get_meter(service_name)
        plugin._metric_readers = [reader]
        plugin._create_metric_instruments()
        pipeline.metric_reader = reader
        pipeline.meter_provider = meter_provider

    if logs:
        from opentelemetry.sdk._logs import export as _log_export
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

        from . import log_handler as _lh
        from . import redaction as _red

        # The 1.35 floor spells it InMemoryLogExporter; newer SDKs deprecate that name.
        exporter_cls = getattr(_log_export, "InMemoryLogRecordExporter", None) or getattr(
            _log_export, "InMemoryLogExporter"
        )
        log_exporter = exporter_cls()
        # No Hermes redactor lookup inside a test pipeline (restored on shutdown).
        resolved, redactor = _red._hermes_resolved, _red._hermes_redactor
        _red._hermes_resolved, _red._hermes_redactor = True, None
        pipeline._restore.append(lambda: setattr(_red, "_hermes_resolved", resolved))
        pipeline._restore.append(lambda: setattr(_red, "_hermes_redactor", redactor))
        with patch.object(
            _lh,
            "build_log_processors",
            return_value=[(SimpleLogRecordProcessor(log_exporter), object())],
        ):
            plugin._init_logs_pipeline(resource, [])
        pipeline.log_exporter = log_exporter

    return pipeline
