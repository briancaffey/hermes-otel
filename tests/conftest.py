"""Shared fixtures for the hermes-otel test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

import pytest

# Ensure the plugin package is importable
PLUGIN_ROOT = Path(__file__).parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def _reset_otel_state(monkeypatch, tmp_path_factory):
    """Reset plugin singletons between tests (``hermes_otel.testing.reset_state``)
    and redirect every config location and the live store to temp paths, so a
    test never picks up the developer's real config or writes into the tree."""
    import hermes_otel.live_store as live_store_mod
    import hermes_otel.plugin_config as plugin_config_mod
    from hermes_otel.testing import reset_state

    isolated = tmp_path_factory.mktemp("isolated-config")
    monkeypatch.setattr(plugin_config_mod, "DEFAULT_CONFIG_PATH", isolated / "nonexistent.yaml")
    monkeypatch.setattr(
        plugin_config_mod, "DURABLE_CONFIG_PATH", isolated / "nonexistent-durable.yaml"
    )
    monkeypatch.delenv(plugin_config_mod.CONFIG_PATH_ENV, raising=False)
    live_dir = tmp_path_factory.mktemp("live-store")
    monkeypatch.setattr(live_store_mod, "_default_db_path", lambda: str(live_dir / "live.db"))
    monkeypatch.delenv("HERMES_OTEL_LIVE_DB", raising=False)

    reset_state()
    yield
    reset_state()


@pytest.fixture(autouse=True)
def _shutdown_initialised_plugins(monkeypatch):
    """Shut down every plugin a test ``init()``-ed itself, singleton or not.

    ``init()`` registers the plugin's ``shutdown`` with ``atexit``, so a plugin
    a test built and dropped kept its exporter threads until interpreter exit,
    where that hook and the SDK's own ``MeterProvider`` exit hook shut the same
    provider ("shutdown can only be called once", printed after the summary).
    ``shutdown()`` is idempotent, so a test that already shut its plugin down
    (or ``reset_state`` above) costs nothing here."""
    from hermes_otel.tracer import HermesOTelPlugin

    plugins = []
    real_init = HermesOTelPlugin.init

    def init(self, *args, **kwargs):
        if self not in plugins:
            plugins.append(self)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(HermesOTelPlugin, "init", init)
    yield
    for plugin in plugins:
        plugin.shutdown()


def _build_inmemory_plugin(n_exporters: int = 1):
    """``(exporters, plugin, provider)`` from :func:`hermes_otel.testing.build_inmemory_plugin`
    (kept for the fixtures below)."""
    from hermes_otel.testing import build_inmemory_plugin

    pipeline = build_inmemory_plugin(exporters=n_exporters)
    return pipeline.span_exporters, pipeline.plugin, pipeline.tracer_provider


def _install_as_singleton(plugin) -> None:
    from hermes_otel.testing import install_singleton

    install_singleton(plugin)


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
    from hermes_otel.testing import build_inmemory_plugin

    pipeline = build_inmemory_plugin(metrics=True)
    _install_as_singleton(pipeline.plugin)
    try:
        yield pipeline.span_exporter, pipeline.metric_reader, pipeline.plugin
    finally:
        pipeline.shutdown()


@pytest.fixture(autouse=True)
def _no_backend_probe(monkeypatch):
    """The reachability probe (#481) never runs in tests unless a test opts in
    with ``real_backend_probe``: the fake hostnames the suites use would hit
    the resolver and the network (``phoenix`` resolves on some LANs)."""
    from hermes_otel import probe

    monkeypatch.setattr(probe, "start_probe", lambda *a, **k: None)


@pytest.fixture()
def real_backend_probe(monkeypatch):
    """Restore the real ``start_probe`` for a test that exercises it."""
    from hermes_otel import probe

    monkeypatch.setattr(probe, "start_probe", probe.__dict__["_real_start_probe"])
