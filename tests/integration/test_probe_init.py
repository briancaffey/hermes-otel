"""#481: init starts the reachability probe off the hook thread; results land
on the plugin and in the log; ``probe_backends: false`` skips it."""

from __future__ import annotations

import logging
from unittest.mock import patch

from _helpers import clear_backend_env

from hermes_otel import probe
from hermes_otel.backend_config import BackendConfig
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin

_BACKENDS = (
    BackendConfig(type="jaeger", endpoint="http://jaeger:4318/v1/traces"),
    BackendConfig(type="otlp", name="dead", endpoint="http://dead:4318/v1/traces"),
)


def _fake_resolve(monkeypatch, unresolved=()):
    monkeypatch.setattr(
        probe,
        "resolve",
        lambda h, p: (None, "name does not resolve") if h in unresolved else ((2, (h, p)), ""),
    )


def _init(monkeypatch, **cfg):
    clear_backend_env(monkeypatch)
    plugin = HermesOTelPlugin(config=HermesOtelConfig(backends=_BACKENDS, **cfg))
    with (
        patch("hermes_otel.pipeline.OTLPSpanExporter"),
        patch("hermes_otel.pipeline.OTLPMetricExporter"),
        patch("hermes_otel.pipeline.trace.set_tracer_provider"),
        patch("hermes_otel.pipeline.metrics.set_meter_provider"),
    ):
        assert plugin.init() is True
    return plugin


class TestProbeAtInit:
    def test_results_and_log_lines(self, monkeypatch, caplog, real_backend_probe):
        _fake_resolve(monkeypatch)

        def fake(address, timeout_s):
            host = address[1][0]
            return (host == "jaeger", 1.5, "" if host == "jaeger" else "Connection refused")

        monkeypatch.setattr(probe, "connect", fake)
        with caplog.at_level(logging.INFO, logger="hermes_otel"):
            plugin = _init(monkeypatch)
            assert plugin.pipeline.probe_thread is not None
            plugin.pipeline.probe_thread.join(5.0)
        assert [(r.backend, r.ok) for r in plugin.pipeline.probe_results] == [
            ("Jaeger", True),
            ("dead", False),
        ]
        msgs = [r.getMessage() for r in caplog.records]
        assert any("✓ Jaeger reachable at jaeger:4318" in m for m in msgs)
        warn = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "⚠ dead unreachable at dead:4318 (Connection refused)" in r.getMessage() for r in warn
        )

    def test_init_does_not_wait_for_the_connects(self, monkeypatch, real_backend_probe):
        import threading

        _fake_resolve(monkeypatch)
        gate = threading.Event()

        def blocked(address, timeout_s):
            gate.wait(5.0)
            return True, 1.0, ""

        monkeypatch.setattr(probe, "connect", blocked)
        plugin = _init(monkeypatch)  # returns while the probe is still blocked
        assert plugin.pipeline.probe_results == []
        gate.set()
        plugin.pipeline.probe_thread.join(5.0)
        assert len(plugin.pipeline.probe_results) == 2

    def test_off_by_config(self, monkeypatch, real_backend_probe):
        called = []
        monkeypatch.setattr(probe, "resolve", lambda h, p: called.append(h) or (None, "x"))
        plugin = _init(monkeypatch, probe_backends=False)
        assert (
            plugin.pipeline.probe_thread is None
            and plugin.pipeline.probe_results == []
            and not called
        )

    def test_unresolvable_backend_is_reported_from_the_init_thread(
        self, monkeypatch, caplog, real_backend_probe
    ):
        _fake_resolve(monkeypatch, unresolved=("dead",))
        monkeypatch.setattr(probe, "connect", lambda *a, **k: (True, 1.0, ""))
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            plugin = _init(monkeypatch)
            plugin.pipeline.probe_thread.join(5.0)
        assert [(r.backend, r.ok, r.error) for r in plugin.pipeline.probe_results] == [
            ("Jaeger", True, ""),
            ("dead", False, "name does not resolve"),
        ]

    def test_reinit_resets_results(self, monkeypatch, real_backend_probe):
        _fake_resolve(monkeypatch)
        monkeypatch.setattr(probe, "connect", lambda *a, **k: (True, 1.0, ""))
        plugin = _init(monkeypatch)
        plugin.pipeline.probe_thread.join(5.0)
        assert plugin.pipeline.probe_results
        plugin.shutdown()
        assert plugin.pipeline is None
