"""Integration tests: host metrics reach the OTel metrics pipeline.

The sampler is not started (no thread); readings are injected into its ring so
the observable-instrument callbacks and the live-store mirror can be asserted
deterministically.
"""

from unittest.mock import patch

from _helpers import metric_points as _points

from hermes_otel.host_bridge import HostMetrics
from hermes_otel.host_metrics import GpuReading, Sample
from hermes_otel.pipeline import build_resource
from hermes_otel.plugin_config import HermesOtelConfig


def _metric(metric_reader, name):
    data = metric_reader.get_metrics_data()
    if data is None:
        return None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    return m
    return None


def _sample(at=1.0, proc=(0.25, 0.05), sys=(0.5, 0.1), gpus=()):
    return Sample(
        at=at,
        wall_ns=1_700_000_000_000_000_000,
        process_cpu={"user": proc[0], "system": proc[1]},
        system_cpu={"user": sys[0], "system": sys[1]},
        gpus=list(gpus),
    )


def _config(**overrides):
    return HermesOtelConfig(host_metrics=True, host_metrics_gpu="off", **overrides)


def _enable(plugin, **overrides):
    """Give ``plugin`` a HostMetrics on its meter, as ``init()`` does; returns the sampler."""
    plugin.config = _config(**overrides)
    plugin.host_metrics = HostMetrics(plugin.config, meter=plugin.pipeline.meter)
    return plugin.host_metrics.sampler


class TestObservableInstruments:
    def test_no_instruments_when_host_metrics_off(self, inmemory_otel_with_metrics):
        _exporter, reader, plugin = inmemory_otel_with_metrics
        assert plugin.host_metrics is None
        assert _metric(reader, "process.cpu.utilization") is None

    def test_cpu_gauges_by_mode(self, inmemory_otel_with_metrics):
        _exporter, reader, plugin = inmemory_otel_with_metrics
        sampler = _enable(plugin)
        assert _points(_metric(reader, "process.cpu.utilization")) == []  # no reading yet
        sampler._ring.append(_sample())
        proc = {
            p.attributes["cpu.mode"]: p.value
            for p in _points(_metric(reader, "process.cpu.utilization"))
        }
        host = {
            p.attributes["cpu.mode"]: p.value
            for p in _points(_metric(reader, "system.cpu.utilization"))
        }
        assert proc == {"user": 0.25, "system": 0.05}
        assert host == {"user": 0.5, "system": 0.1}
        assert _metric(reader, "process.cpu.utilization").unit == "1"

    def test_gpu_instruments_per_device_with_hw_attributes(self, inmemory_otel_with_metrics):
        _exporter, reader, plugin = inmemory_otel_with_metrics
        sampler = _enable(plugin)
        sampler._probe.vendor = "nvidia"
        sampler._ring.append(
            _sample(
                gpus=[
                    GpuReading(0, utilization=0.5, memory_bytes=2.0e9, power_w=150.0),
                    GpuReading(1, utilization=None, memory_bytes=None, power_w=90.0),
                ]
            )
        )
        util = _points(_metric(reader, "hw.gpu.utilization"))
        assert [(p.attributes["hw.id"], p.value) for p in util] == [("gpu0", 0.5)]
        assert util[0].attributes["hw.vendor"] == "nvidia"
        mem = _points(_metric(reader, "hw.gpu.memory.usage"))
        assert [(p.attributes["hw.id"], p.value) for p in mem] == [("gpu0", 2.0e9)]
        assert _metric(reader, "hw.gpu.memory.usage").unit == "By"
        power = _points(_metric(reader, "hw.power"))
        assert sorted((p.attributes["hw.id"], p.value) for p in power) == [
            ("gpu0", 150.0),
            ("gpu1", 90.0),
        ]
        assert all(p.attributes["hw.type"] == "gpu" for p in power)
        assert _metric(reader, "hw.power").unit == "W"

    def test_no_meter_registers_nothing(self):
        # The live-only pipeline has no meter: the sampler still exists for
        # the live-store mirror and the per-tool window attributes.
        host = HostMetrics(_config(), meter=None)
        assert host.sampler is not None and host.running is False


class TestLifecycle:
    def test_running_and_window_follow_the_sampler_thread(self, inmemory_otel_with_metrics):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        sampler = _enable(plugin)
        host = plugin.host_metrics
        assert host.running is False  # created but not started
        assert host.window(0.0, 1.0) is None  # the hooks see nothing until it runs
        sampler._thread = type("T", (), {"is_alive": lambda self: True})()
        assert host.running is True
        sampler._thread = None
        host.stop()
        host.stop()

    def test_start_logs_warning_without_psutil(self, monkeypatch, caplog):
        import hermes_otel.host_metrics as hm

        monkeypatch.setattr(hm, "psutil", None)
        host = HostMetrics(HermesOtelConfig(host_metrics=True, host_metrics_gpu="off"))
        with caplog.at_level("WARNING", logger="hermes_otel"):
            host.start()
        assert host.running is False
        assert any("psutil is not importable" in r.getMessage() for r in caplog.records)

    def test_start_runs_thread_when_available(self):
        host = HostMetrics(_config(host_metrics_interval_ms=50))
        host.start()
        try:
            assert host.running
        finally:
            host.stop()
        assert host.running is False

    def test_init_starts_the_sampler_and_shutdown_drops_it(self, monkeypatch):
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin(
            config=_config(dashboard_live=True, host_metrics_interval_ms=50, capture_logs=False)
        )
        with patch("hermes_otel.pipeline.trace.set_tracer_provider"):
            assert plugin.init() is True  # live-only: no meter, the sampler still runs
        try:
            assert plugin.host_metrics is not None and plugin.host_metrics.running
            assert plugin.host_metrics.live_active is True
        finally:
            plugin.shutdown()
        assert plugin.host_metrics is None and plugin.pipeline is None


class TestLiveStoreMirror:
    def test_readings_are_mirrored_when_live_store_active(
        self, inmemory_otel_with_metrics, monkeypatch
    ):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        seen = []

        class _Store:
            def add_metric(self, name, value, attrs, ts):
                seen.append((name, value, ts))

        import hermes_otel.live_store_registry as ls

        monkeypatch.setattr(ls, "get_live_store", lambda: _Store())
        host = HostMetrics(_config(), live_active=True)
        host.mirror_sample(_sample(gpus=[GpuReading(0, 0.75, None, None)]))
        assert seen == [
            ("process.cpu.utilization", 0.3, 1_700_000_000_000_000_000),
            ("system.cpu.utilization", 0.6, 1_700_000_000_000_000_000),
            ("hw.gpu.utilization", 0.75, 1_700_000_000_000_000_000),
        ]

    def test_mirror_is_noop_when_live_inactive(self):
        HostMetrics(_config(), live_active=False).mirror_sample(_sample())  # must not raise


class TestResourceHostName:
    def test_host_name_added_when_host_metrics_on(self):
        attrs = build_resource(HermesOtelConfig(host_metrics=True), "default", []).attributes
        assert attrs["host.name"]

    def test_configured_host_name_wins(self):
        cfg = HermesOtelConfig(host_metrics=True, resource_attributes={"host.name": "gpu-box-1"})
        assert build_resource(cfg, "default", []).attributes["host.name"] == "gpu-box-1"

    def test_absent_when_host_metrics_off(self):
        assert "host.name" not in build_resource(HermesOtelConfig(), "default", []).attributes
