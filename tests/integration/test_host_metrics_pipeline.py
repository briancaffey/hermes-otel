"""Integration tests: host metrics reach the OTel metrics pipeline.

The sampler is not started (no thread); readings are injected into its ring so
the observable-instrument callbacks and the live-store mirror can be asserted
deterministically.
"""

from hermes_otel.host_metrics import GpuReading, Sample
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


def _points(metric):
    return list(metric.data.data_points) if metric is not None else []


def _sample(at=1.0, proc=(0.25, 0.05), sys=(0.5, 0.1), gpus=()):
    return Sample(
        at=at,
        wall_ns=1_700_000_000_000_000_000,
        process_cpu={"user": proc[0], "system": proc[1]},
        system_cpu={"user": sys[0], "system": sys[1]},
        gpus=list(gpus),
    )


def _enable(plugin, **overrides):
    plugin.config = HermesOtelConfig(host_metrics=True, host_metrics_gpu="off", **overrides)
    plugin._create_host_metric_instruments()
    return plugin._host_metrics


class TestObservableInstruments:
    def test_no_instruments_when_host_metrics_off(self, inmemory_otel_with_metrics):
        _exporter, reader, plugin = inmemory_otel_with_metrics
        assert plugin._host_metrics is None
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

    def test_instruments_registered_once_per_meter(self, inmemory_otel_with_metrics):
        _exporter, reader, plugin = inmemory_otel_with_metrics
        sampler = _enable(plugin)
        again = plugin._ensure_host_sampler()
        assert again is sampler  # same sampler object reused


class TestLifecycle:
    def test_host_metrics_property_reflects_running_state(self, inmemory_otel_with_metrics):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        sampler = _enable(plugin)
        assert plugin.host_metrics is None  # created but not started
        sampler._thread = type("T", (), {"is_alive": lambda self: True})()
        assert plugin.host_metrics is sampler
        sampler._thread = None
        plugin.stop_host_metrics()
        plugin.stop_host_metrics()

    def test_start_logs_warning_without_psutil(self, inmemory_otel_with_metrics, monkeypatch):
        import hermes_otel.host_metrics as hm

        _exporter, _reader, plugin = inmemory_otel_with_metrics
        monkeypatch.setattr(hm, "psutil", None)
        plugin.config = HermesOtelConfig(host_metrics=True)
        plugin._start_host_metrics()
        assert plugin.host_metrics is None

    def test_start_runs_thread_when_available(self, inmemory_otel_with_metrics):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        plugin.config = HermesOtelConfig(
            host_metrics=True, host_metrics_gpu="off", host_metrics_interval_ms=50
        )
        plugin._start_host_metrics()
        try:
            assert plugin.host_metrics is not None
            assert plugin.host_metrics.running
        finally:
            plugin.stop_host_metrics()
        assert plugin.host_metrics is None


class TestLiveStoreMirror:
    def test_readings_are_mirrored_when_live_store_active(
        self, inmemory_otel_with_metrics, monkeypatch
    ):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        seen = []

        class _Store:
            def add_metric(self, name, value, attrs, ts):
                seen.append((name, value, ts))

        import hermes_otel.live_store as ls

        monkeypatch.setattr(ls, "get_live_store", lambda: _Store())
        plugin._live_active = True
        plugin._mirror_host_sample(_sample(gpus=[GpuReading(0, 0.75, None, None)]))
        assert seen == [
            ("process.cpu.utilization", 0.3, 1_700_000_000_000_000_000),
            ("system.cpu.utilization", 0.6, 1_700_000_000_000_000_000),
            ("hw.gpu.utilization", 0.75, 1_700_000_000_000_000_000),
        ]

    def test_mirror_is_noop_when_live_inactive(self, inmemory_otel_with_metrics, monkeypatch):
        _exporter, _reader, plugin = inmemory_otel_with_metrics
        plugin._live_active = False
        plugin._mirror_host_sample(_sample())  # must not raise
