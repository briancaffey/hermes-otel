"""Tests for the GPU probe (gpu_probe.py).

Neither amdsmi nor pynvml are installed in CI, so the AMD/NVIDIA SDK calls are
exercised via fake modules injected into sys.modules. The ``rocm-smi`` CLI
fallback is exercised by stubbing ``subprocess.run``.
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermes_otel import gpu_probe as poller


# --------------------------------------------------------------------------- #
# _amd_cli_snapshot (rocm-smi CLI fallback parsing)
# --------------------------------------------------------------------------- #
class TestAmdCliSnapshot:
    def test_parses_busy_power_vram_for_multiple_gpus(self, monkeypatch):
        stdout = "\n".join(
            [
                "GPU[0]          : GPU use (%): 35.5",
                "GPU[0]          : VRAM Total Used Memory (B): 1073741824",
                "GPU[0]          : Average Socket Power (W): 175.5",
                "GPU[1]          : GPU use (%): 12.0",
                "GPU[1]          : VRAM Total Used Memory (B): 536870912",
                "GPU[1]          : Average Socket Power (W): 90.0",
            ]
        )
        monkeypatch.setattr(
            poller.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout)
        )
        snap = poller._amd_cli_snapshot()
        assert snap[0]["busy_pct"] == 35.5
        assert snap[0]["vram_used_mb"] == pytest.approx(1024.0)
        assert snap[0]["power_w"] == 175.5
        assert snap[1]["busy_pct"] == 12.0
        assert snap[1]["vram_used_mb"] == pytest.approx(512.0)
        assert snap[1]["power_w"] == 90.0

    def test_decimal_busy_percent_not_corrupted(self, monkeypatch):
        # Regression guard: an earlier implementation stripped all non-digit
        # characters, which mangled "35.5" into "355".
        stdout = "GPU[0]          : GPU use (%): 35.5"
        monkeypatch.setattr(
            poller.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout)
        )
        snap = poller._amd_cli_snapshot()
        assert snap[0]["busy_pct"] == 35.5

    def test_unparsable_value_leaves_field_none(self, monkeypatch):
        stdout = "GPU[0]          : GPU use (%): not-a-number"
        monkeypatch.setattr(
            poller.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout)
        )
        snap = poller._amd_cli_snapshot()
        assert snap[0]["busy_pct"] is None

    def test_unmatched_lines_are_skipped(self, monkeypatch):
        stdout = "some unrelated line\nanother line"
        monkeypatch.setattr(
            poller.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout)
        )
        assert poller._amd_cli_snapshot() == {}

    def test_subprocess_failure_returns_empty(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("rocm-smi not found")

        monkeypatch.setattr(poller.subprocess, "run", _raise)
        assert poller._amd_cli_snapshot() == {}

    def test_subprocess_timeout_returns_empty(self, monkeypatch):
        import subprocess as sp

        def _raise(*a, **k):
            raise sp.TimeoutExpired(cmd="rocm-smi", timeout=2)

        monkeypatch.setattr(poller.subprocess, "run", _raise)
        assert poller._amd_cli_snapshot() == {}


# --------------------------------------------------------------------------- #
# _amd_gpu_stats (Python API + lazy CLI fallback)
# --------------------------------------------------------------------------- #
def _fake_amdsmi(activity=None, vram=None, metrics=None):
    """Build a fake amdsmi module. Each of activity/vram/metrics is either a
    dict returned for every handle, or a callable(handle) -> dict, or an
    exception class/instance to raise."""
    mod = types.ModuleType("amdsmi")

    def _make(spec):
        def fn(handle):
            if isinstance(spec, BaseException):
                raise spec
            if isinstance(spec, type) and issubclass(spec, BaseException):
                raise spec("boom")
            if callable(spec) and not isinstance(spec, dict):
                return spec(handle)
            return spec

        return fn

    mod.amdsmi_get_gpu_activity = _make(activity)
    mod.amdsmi_get_gpu_vram_usage = _make(vram)
    mod.amdsmi_get_gpu_metrics_info = _make(metrics)
    return mod


class TestAmdGpuStats:
    def test_all_fields_from_python_api_no_cli_fallback(self, monkeypatch):
        fake = _fake_amdsmi(
            activity={"gfx_activity": 42.0},
            vram={"vram_used": 1234.0},
            metrics={"current_socket_power": 150.0},
        )
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        cli_mock = MagicMock()
        monkeypatch.setattr(poller, "_amd_cli_snapshot", cli_mock)

        stats = poller._amd_gpu_stats(["handle0"])

        assert stats == [{"busy_pct": 42.0, "power_w": 150.0, "vram_used_mb": 1234.0}]
        cli_mock.assert_not_called()

    def test_missing_field_triggers_cli_fallback_once_for_multiple_gpus(self, monkeypatch):
        # power is always missing from the Python API for both GPUs.
        fake = _fake_amdsmi(
            activity={"gfx_activity": 10.0},
            vram={"vram_used": 500.0},
            metrics={"current_socket_power": None},
        )
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        cli_mock = MagicMock(
            return_value={
                0: {"busy_pct": None, "power_w": 111.0, "vram_used_mb": None},
                1: {"busy_pct": None, "power_w": 222.0, "vram_used_mb": None},
            }
        )
        monkeypatch.setattr(poller, "_amd_cli_snapshot", cli_mock)

        stats = poller._amd_gpu_stats(["handle0", "handle1"])

        assert cli_mock.call_count == 1  # shared across both GPUs, not once each
        assert stats[0] == {"busy_pct": 10.0, "power_w": 111.0, "vram_used_mb": 500.0}
        assert stats[1] == {"busy_pct": 10.0, "power_w": 222.0, "vram_used_mb": 500.0}

    def test_python_api_exception_leaves_field_none(self, monkeypatch):
        fake = _fake_amdsmi(
            activity=RuntimeError, vram={"vram_used": 1.0}, metrics={"current_socket_power": 1.0}
        )
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        monkeypatch.setattr(poller, "_amd_cli_snapshot", MagicMock(return_value={}))

        stats = poller._amd_gpu_stats(["handle0"])

        assert stats[0]["busy_pct"] is None


# --------------------------------------------------------------------------- #
# _nvidia_gpu_stats
# --------------------------------------------------------------------------- #
def _fake_pynvml(util_gpu=55, mem_used_bytes=2 * 1024 * 1024, power_mw=90000, raise_on=()):
    mod = types.ModuleType("pynvml")

    def util(handle):
        if "util" in raise_on:
            raise RuntimeError("boom")
        return SimpleNamespace(gpu=util_gpu)

    def mem(handle):
        if "mem" in raise_on:
            raise RuntimeError("boom")
        return SimpleNamespace(used=mem_used_bytes)

    def power(handle):
        if "power" in raise_on:
            raise RuntimeError("boom")
        return power_mw

    mod.nvmlDeviceGetUtilizationRates = util
    mod.nvmlDeviceGetMemoryInfo = mem
    mod.nvmlDeviceGetPowerUsage = power
    return mod


class TestNvidiaGpuStats:
    def test_normal_path(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml())
        stats = poller._nvidia_gpu_stats(["handle0"])
        assert stats == [{"busy_pct": 55.0, "power_w": 90.0, "vram_used_mb": 2.0}]

    def test_each_field_failure_is_isolated(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(raise_on=("util", "power")))
        stats = poller._nvidia_gpu_stats(["handle0"])
        assert stats[0]["busy_pct"] is None
        assert stats[0]["power_w"] is None
        assert stats[0]["vram_used_mb"] == 2.0


# --------------------------------------------------------------------------- #
# _amd_init / _amd_shutdown / _nvidia_init / _nvidia_shutdown
# --------------------------------------------------------------------------- #
class TestAmdInitShutdown:
    def test_returns_none_when_amdsmi_not_installed(self, monkeypatch):
        # sys.modules[name] = None is the standard sentinel that forces
        # ImportError regardless of whether the real package is actually
        # installed (e.g. on an AMD GPU host running this suite).
        monkeypatch.setitem(sys.modules, "amdsmi", None)
        assert poller._amd_init() is None

    def test_returns_handles_when_present(self, monkeypatch):
        fake = types.ModuleType("amdsmi")
        fake.amdsmi_init = MagicMock()
        fake.amdsmi_get_processor_handles = MagicMock(return_value=["h0"])
        fake.amdsmi_shut_down = MagicMock()
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        assert poller._amd_init() == ["h0"]

    def test_returns_none_and_shuts_down_when_no_handles(self, monkeypatch):
        fake = types.ModuleType("amdsmi")
        fake.amdsmi_init = MagicMock()
        fake.amdsmi_get_processor_handles = MagicMock(return_value=[])
        fake.amdsmi_shut_down = MagicMock()
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        assert poller._amd_init() is None
        fake.amdsmi_shut_down.assert_called_once()

    def test_shutdown_calls_amdsmi_shut_down(self, monkeypatch):
        fake = types.ModuleType("amdsmi")
        fake.amdsmi_shut_down = MagicMock()
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        poller._amd_shutdown(["h0"])
        fake.amdsmi_shut_down.assert_called_once()

    def test_shutdown_swallows_exception(self, monkeypatch):
        fake = types.ModuleType("amdsmi")
        fake.amdsmi_shut_down = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setitem(sys.modules, "amdsmi", fake)
        poller._amd_shutdown(["h0"])  # must not raise


class TestNvidiaInitShutdown:
    def test_returns_none_when_pynvml_not_installed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pynvml", None)
        assert poller._nvidia_init() is None

    def test_returns_handles_when_present(self, monkeypatch):
        fake = types.ModuleType("pynvml")
        fake.nvmlInit = MagicMock()
        fake.nvmlDeviceGetCount = MagicMock(return_value=2)
        fake.nvmlDeviceGetHandleByIndex = lambda i: f"h{i}"
        monkeypatch.setitem(sys.modules, "pynvml", fake)
        assert poller._nvidia_init() == ["h0", "h1"]

    def test_returns_none_when_count_zero(self, monkeypatch):
        fake = types.ModuleType("pynvml")
        fake.nvmlInit = MagicMock()
        fake.nvmlDeviceGetCount = MagicMock(return_value=0)
        fake.nvmlShutdown = MagicMock()
        monkeypatch.setitem(sys.modules, "pynvml", fake)
        assert poller._nvidia_init() is None
        fake.nvmlShutdown.assert_called_once()

    def test_shutdown_calls_nvml_shutdown(self, monkeypatch):
        fake = types.ModuleType("pynvml")
        fake.nvmlShutdown = MagicMock()
        monkeypatch.setitem(sys.modules, "pynvml", fake)
        poller._nvidia_shutdown(["h0"])
        fake.nvmlShutdown.assert_called_once()


# --------------------------------------------------------------------------- #
# _detect_gpu_vendor / _query_gpus / _shutdown_gpu_vendor
# --------------------------------------------------------------------------- #
class TestDetectGpuVendor:
    def test_forced_amd(self, monkeypatch):
        monkeypatch.setattr(poller, "_amd_init", lambda: ["h0"])
        monkeypatch.setattr(
            poller, "_nvidia_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        assert poller._detect_gpu_vendor("amd") == ("amd", ["h0"])

    def test_forced_amd_no_handles(self, monkeypatch):
        monkeypatch.setattr(poller, "_amd_init", lambda: None)
        assert poller._detect_gpu_vendor("amd") == (None, None)

    def test_forced_nvidia(self, monkeypatch):
        monkeypatch.setattr(poller, "_nvidia_init", lambda: ["h0"])
        assert poller._detect_gpu_vendor("nvidia") == ("nvidia", ["h0"])

    def test_auto_prefers_amd(self, monkeypatch):
        monkeypatch.setattr(poller, "_amd_init", lambda: ["amd0"])
        monkeypatch.setattr(
            poller, "_nvidia_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        assert poller._detect_gpu_vendor() == ("amd", ["amd0"])

    def test_auto_falls_back_to_nvidia(self, monkeypatch):
        monkeypatch.setattr(poller, "_amd_init", lambda: None)
        monkeypatch.setattr(poller, "_nvidia_init", lambda: ["nv0"])
        assert poller._detect_gpu_vendor() == ("nvidia", ["nv0"])

    def test_auto_no_gpu_found(self, monkeypatch):
        monkeypatch.setattr(poller, "_amd_init", lambda: None)
        monkeypatch.setattr(poller, "_nvidia_init", lambda: None)
        assert poller._detect_gpu_vendor() == (None, None)


class TestQueryAndShutdownDispatch:
    def test_query_gpus_dispatches_amd(self, monkeypatch):
        mock = MagicMock(return_value=[{"busy_pct": 1.0}])
        monkeypatch.setattr(poller, "_amd_gpu_stats", mock)
        assert poller._query_gpus("amd", ["h0"]) == [{"busy_pct": 1.0}]
        mock.assert_called_once_with(["h0"])

    def test_query_gpus_dispatches_nvidia(self, monkeypatch):
        mock = MagicMock(return_value=[{"busy_pct": 2.0}])
        monkeypatch.setattr(poller, "_nvidia_gpu_stats", mock)
        assert poller._query_gpus("nvidia", ["h0"]) == [{"busy_pct": 2.0}]

    def test_query_gpus_unknown_vendor_returns_empty(self):
        assert poller._query_gpus(None, None) == []

    def test_shutdown_dispatches_amd(self, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(poller, "_amd_shutdown", mock)
        poller._shutdown_gpu_vendor("amd", ["h0"])
        mock.assert_called_once_with(["h0"])

    def test_shutdown_dispatches_nvidia(self, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(poller, "_nvidia_shutdown", mock)
        poller._shutdown_gpu_vendor("nvidia", ["h0"])
        mock.assert_called_once_with(["h0"])

    def test_shutdown_unknown_vendor_is_noop(self):
        poller._shutdown_gpu_vendor(None, None)  # must not raise


# --------------------------------------------------------------------------- #
# _aggregate_gpu
# --------------------------------------------------------------------------- #
class TestAggregateGpu:
    def test_empty_list(self):
        assert poller._aggregate_gpu([]) == (0.0, 0.0, 0.0)

    def test_averages_busy_sums_power_and_vram(self):
        per_gpu = [
            {"busy_pct": 10.0, "power_w": 100.0, "vram_used_mb": 1000.0},
            {"busy_pct": 30.0, "power_w": 200.0, "vram_used_mb": 2000.0},
        ]
        gfx, power, vram = poller._aggregate_gpu(per_gpu)
        assert gfx == 20.0
        assert power == 300.0
        assert vram == 3000.0

    def test_none_fields_excluded_from_aggregate(self):
        per_gpu = [
            {"busy_pct": None, "power_w": 100.0, "vram_used_mb": None},
            {"busy_pct": 40.0, "power_w": None, "vram_used_mb": 500.0},
        ]
        gfx, power, vram = poller._aggregate_gpu(per_gpu)
        assert gfx == 40.0  # only one non-None busy_pct
        assert power == 100.0
        assert vram == 500.0

    def test_all_none_yields_zero(self):
        per_gpu = [{"busy_pct": None, "power_w": None, "vram_used_mb": None}]
        assert poller._aggregate_gpu(per_gpu) == (0.0, 0.0, 0.0)

    # --------------------------------------------------------------------------- #
    # _cpu_seconds / _clamp_pct
    # --------------------------------------------------------------------------- #

    def test_off_skips_detection(self, monkeypatch):
        monkeypatch.setattr(
            poller, "_amd_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        monkeypatch.setattr(
            poller, "_nvidia_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        assert poller._detect_gpu_vendor("off") == (None, None)


# --------------------------------------------------------------------------- #
# GpuProbe (stateful wrapper used by the host-metrics sampler)
# --------------------------------------------------------------------------- #
class TestGpuProbe:
    def test_no_vendor_reads_empty_and_close_is_safe(self, monkeypatch):
        monkeypatch.setattr(poller, "_detect_gpu_vendor", lambda forced="auto": (None, None))
        probe = poller.GpuProbe("auto")
        assert probe.open() is None
        assert probe.available is False
        assert probe.read() == []
        probe.close()
        probe.close()

    def test_open_is_idempotent_and_read_dispatches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            poller,
            "_detect_gpu_vendor",
            lambda forced="auto": calls.append(forced) or ("nvidia", ["h0"]),
        )
        monkeypatch.setattr(
            poller,
            "_query_gpus",
            lambda vendor, handles: [{"busy_pct": 40.0, "power_w": 100.0, "vram_used_mb": 10.0}],
        )
        probe = poller.GpuProbe("nvidia")
        assert probe.open() == "nvidia"
        assert probe.open() == "nvidia"
        assert calls == ["nvidia"]
        assert probe.read()[0]["busy_pct"] == 40.0

    def test_read_swallows_sdk_errors(self, monkeypatch):
        monkeypatch.setattr(poller, "_detect_gpu_vendor", lambda forced="auto": ("amd", ["h0"]))
        monkeypatch.setattr(
            poller, "_query_gpus", lambda v, h: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        probe = poller.GpuProbe()
        probe.open()
        assert probe.read() == []

    def test_close_releases_vendor(self, monkeypatch):
        released = []
        monkeypatch.setattr(poller, "_detect_gpu_vendor", lambda forced="auto": ("amd", ["h0"]))
        monkeypatch.setattr(poller, "_shutdown_gpu_vendor", lambda v, h: released.append((v, h)))
        probe = poller.GpuProbe()
        probe.open()
        probe.close()
        assert released == [("amd", ["h0"])]
        assert probe.available is False
