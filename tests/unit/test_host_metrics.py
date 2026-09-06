"""Unit tests for the in-process host metrics sampler (host_metrics.py).

psutil is stubbed with a fake module so the CPU math is exercised
deterministically; the GPU probe is stubbed at the gpu_probe boundary.
"""

import threading
import types
from types import SimpleNamespace

import pytest

from hermes_otel import host_metrics as hm


class _FakeProc:
    def __init__(self, pid, user=0.0, system=0.0, children=None):
        self.pid = pid
        self._times = (user, system)
        self._children = children or []

    def cpu_times(self):
        return SimpleNamespace(user=self._times[0], system=self._times[1])

    def children(self, recursive=False):
        return list(self._children)

    def advance(self, user=0.0, system=0.0):
        self._times = (self._times[0] + user, self._times[1] + system)


class _NoSuchProcess(Exception):
    pass


def _fake_psutil(root, ncpu=4, sys_user=20.0, sys_system=5.0):
    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = _NoSuchProcess
    mod.AccessDenied = type("AccessDenied", (Exception,), {})
    mod.ZombieProcess = type("ZombieProcess", (Exception,), {})
    mod.Process = lambda pid: root
    mod.cpu_count = lambda logical=True: ncpu
    mod.cpu_times_percent = lambda interval=None: SimpleNamespace(user=sys_user, system=sys_system)
    return mod


@pytest.fixture()
def clock(monkeypatch):
    """Deterministic perf_counter for the sampler."""
    state = {"t": 100.0}
    monkeypatch.setattr(hm.time, "perf_counter", lambda: state["t"])
    return state


class TestAvailability:
    def test_unavailable_without_psutil(self, monkeypatch):
        monkeypatch.setattr(hm, "psutil", None)
        s = hm.HostMetricsSampler(interval_ms=100)
        assert s.available is False
        assert s.start() is False
        assert s.running is False
        s.stop()  # safe

    def test_cpu_readings_zero_without_psutil(self, monkeypatch):
        monkeypatch.setattr(hm, "psutil", None)
        s = hm.HostMetricsSampler()
        assert s._process_cpu() == {"user": 0.0, "system": 0.0}
        assert s._system_cpu() == {"user": 0.0, "system": 0.0}


class TestProcessTreeCpu:
    def test_delta_over_tree_normalised_by_cores(self, monkeypatch, clock):
        child = _FakeProc(2, user=1.0)
        root = _FakeProc(1, user=10.0, system=2.0, children=[child])
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root, ncpu=4))
        s = hm.HostMetricsSampler(interval_ms=1000, gpu_vendor="off")
        s._open()  # primes the baseline at t=100
        clock["t"] = 101.0
        root.advance(user=1.0, system=0.5)  # 1.5 cpu-s over 1 s wall on 4 cores
        child.advance(user=0.5)  # + 0.5 cpu-s
        sample = s.sample_once()
        assert sample.process_cpu["user"] == pytest.approx((1.0 + 0.5) / 4)
        assert sample.process_cpu["system"] == pytest.approx(0.5 / 4)
        assert sample.process_cpu_total == pytest.approx(2.0 / 4)
        assert sample.system_cpu == {"user": 0.20, "system": 0.05}
        assert sample.gpus == [] and sample.gpu_utilization is None

    def test_new_child_has_no_baseline_and_is_clamped(self, monkeypatch, clock):
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root, ncpu=1))
        s = hm.HostMetricsSampler(interval_ms=1000, gpu_vendor="off")
        s._open()
        newcomer = _FakeProc(3, user=50.0)  # lots of prior CPU, never seen before
        root._children.append(newcomer)
        clock["t"] = 100.1
        root.advance(user=5.0)  # 5 cpu-s in 0.1 s on 1 core -> clamped to 1.0
        sample = s.sample_once()
        assert sample.process_cpu["user"] == 1.0

    def test_root_gone_reads_zero(self, monkeypatch, clock):
        root = _FakeProc(1)
        fake = _fake_psutil(root)
        monkeypatch.setattr(hm, "psutil", fake)
        s = hm.HostMetricsSampler(gpu_vendor="off")
        s._open()

        def _gone(recursive=False):
            raise _NoSuchProcess()

        root.children = _gone
        assert s._process_cpu() == {"user": 0.0, "system": 0.0}


class TestGpuConversion:
    def test_vendor_units_to_semconv(self):
        r = hm._gpu_reading(2, {"busy_pct": 37.5, "vram_used_mb": 1024.0, "power_w": 180.0})
        assert r.index == 2
        assert r.utilization == 0.375
        assert r.memory_bytes == 1024.0 * 1024 * 1024
        assert r.power_w == 180.0

    def test_none_fields_stay_none(self):
        r = hm._gpu_reading(0, {"busy_pct": None, "vram_used_mb": None, "power_w": None})
        assert (r.utilization, r.memory_bytes, r.power_w) == (None, None, None)

    def test_sample_gpu_utilization_averages_devices(self, monkeypatch, clock):
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler()
        monkeypatch.setattr(
            s._probe,
            "read",
            lambda: [
                {"busy_pct": 20.0, "power_w": 1.0, "vram_used_mb": 1.0},
                {"busy_pct": None, "power_w": 1.0, "vram_used_mb": 1.0},
                {"busy_pct": 60.0, "power_w": 1.0, "vram_used_mb": 1.0},
            ],
        )
        s._open()
        sample = s.sample_once()
        assert sample.gpu_utilization == pytest.approx(0.4)


class TestRingAndWindow:
    def _sampler(self, monkeypatch, **kw):
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler(gpu_vendor="off", **kw)
        s._open()
        return s, root

    def test_ring_is_bounded(self, monkeypatch, clock):
        s, _root = self._sampler(monkeypatch, max_samples=3)
        for i in range(10):
            clock["t"] = 100.0 + i
            s.sample_once()
        assert len(s._ring) == 3
        assert s.latest().at == 109.0

    def test_default_ring_size_scales_with_interval(self):
        assert hm.HostMetricsSampler(interval_ms=1000)._ring.maxlen == 600
        assert hm.HostMetricsSampler(interval_ms=100)._ring.maxlen == 6000
        assert hm.HostMetricsSampler(interval_ms=10)._ring.maxlen == 12_000  # 50 ms floor
        assert hm.HostMetricsSampler(interval_ms=60_000)._ring.maxlen == 600

    def test_window_avg_peak_and_none_when_empty(self, monkeypatch, clock):
        s, root = self._sampler(monkeypatch)
        # Fake psutil root has 4 cores; give the tree 0.4 / 0.8 / 0.2 utilization.
        for i, cpu in enumerate((1.6, 3.2, 0.8)):
            clock["t"] = 101.0 + i
            root.advance(user=cpu)
            s.sample_once()
        assert s.latest() is not None
        w = s.window(101.0, 103.0)
        assert w.samples == 3
        assert w.cpu_avg == pytest.approx((0.4 + 0.8 + 0.2) / 3)
        assert w.cpu_peak == pytest.approx(0.8)
        assert w.gpu_avg is None and w.gpu_peak is None
        assert s.window(102.5, 102.9) is None  # nothing inside
        assert s.window(102.0, 102.0).samples == 1  # inclusive bounds

    def test_window_stops_scanning_before_start(self, monkeypatch, clock):
        s, root = self._sampler(monkeypatch)
        for i in range(5):
            clock["t"] = 101.0 + i
            root.advance(user=0.4)
            s.sample_once()
        assert s.window(104.0, 200.0).samples == 2

    def test_on_sample_callback_receives_each_reading(self, monkeypatch, clock):
        seen = []
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler(gpu_vendor="off", on_sample=seen.append)
        s._open()
        s.sample_once()
        assert len(seen) == 1 and isinstance(seen[0], hm.Sample)

    def test_callback_errors_do_not_break_sampling(self, monkeypatch, clock):
        def boom(_s):
            raise RuntimeError("boom")

        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler(gpu_vendor="off", on_sample=boom)
        s._open()
        assert s.sample_once() is not None


class TestThreadLifecycle:
    def test_start_stop_idempotent_and_thread_is_daemon(self, monkeypatch):
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler(interval_ms=50, gpu_vendor="off")
        assert s.start() is True
        assert s.start() is True
        assert s.running is True
        assert s._thread.daemon is True
        assert s._thread.name == "hermes-otel-host-metrics"
        s.stop()
        s.stop()
        assert s.running is False
        assert threading.active_count() >= 1

    def test_interval_floor(self):
        assert hm.HostMetricsSampler(interval_ms=1).interval_s == 0.05

    def test_sampler_survives_a_failing_tick(self, monkeypatch):
        root = _FakeProc(1)
        monkeypatch.setattr(hm, "psutil", _fake_psutil(root))
        s = hm.HostMetricsSampler(interval_ms=50, gpu_vendor="off")
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            raise RuntimeError("tick failed")

        monkeypatch.setattr(s, "sample_once", flaky)
        s.start()
        s._stop.wait(0.2)
        s.stop()
        assert calls["n"] >= 1
