"""Tests for the standalone CPU/GPU metrics poller (metrics_poller.py).

metrics_poller.py deliberately imports nothing from the hermes plugin so it can
run as an isolated subprocess, but it is still importable as a module here for
unit testing. Neither amdsmi nor pynvml are installed in CI, so the AMD/NVIDIA
SDK calls are exercised via fake modules injected into sys.modules.
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermes_otel import metrics_poller as poller


# --------------------------------------------------------------------------- #
# _flag
# --------------------------------------------------------------------------- #
class TestFlag:
    def test_truthy(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_TRACE", "true")
        assert poller._flag("HERMES_CPU_TRACE") is True

    def test_falsy(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_TRACE", "false")
        assert poller._flag("HERMES_CPU_TRACE") is False

    def test_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_CPU_TRACE", raising=False)
        assert poller._flag("HERMES_CPU_TRACE") is False


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
        monkeypatch.setenv("HERMES_GPU_VENDOR", "amd")
        monkeypatch.setattr(poller, "_amd_init", lambda: ["h0"])
        monkeypatch.setattr(
            poller, "_nvidia_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        assert poller._detect_gpu_vendor() == ("amd", ["h0"])

    def test_forced_amd_no_handles(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_VENDOR", "amd")
        monkeypatch.setattr(poller, "_amd_init", lambda: None)
        assert poller._detect_gpu_vendor() == (None, None)

    def test_forced_nvidia(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_VENDOR", "nvidia")
        monkeypatch.setattr(poller, "_nvidia_init", lambda: ["h0"])
        assert poller._detect_gpu_vendor() == ("nvidia", ["h0"])

    def test_auto_prefers_amd(self, monkeypatch):
        monkeypatch.delenv("HERMES_GPU_VENDOR", raising=False)
        monkeypatch.setattr(poller, "_amd_init", lambda: ["amd0"])
        monkeypatch.setattr(
            poller, "_nvidia_init", lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        )
        assert poller._detect_gpu_vendor() == ("amd", ["amd0"])

    def test_auto_falls_back_to_nvidia(self, monkeypatch):
        monkeypatch.delenv("HERMES_GPU_VENDOR", raising=False)
        monkeypatch.setattr(poller, "_amd_init", lambda: None)
        monkeypatch.setattr(poller, "_nvidia_init", lambda: ["nv0"])
        assert poller._detect_gpu_vendor() == ("nvidia", ["nv0"])

    def test_auto_no_gpu_found(self, monkeypatch):
        monkeypatch.delenv("HERMES_GPU_VENDOR", raising=False)
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
class TestCpuSecondsAndClamp:
    def test_cpu_seconds_sums_user_and_system(self):
        proc = SimpleNamespace(cpu_times=lambda: SimpleNamespace(user=1.5, system=2.5))
        assert poller._cpu_seconds(proc) == 4.0

    @pytest.mark.parametrize(
        "value,expected",
        [(-5.0, 0.0), (0.0, 0.0), (50.0, 50.0), (100.0, 100.0), (105.0, 100.0)],
    )
    def test_clamp_pct(self, value, expected):
        assert poller._clamp_pct(value) == expected


# --------------------------------------------------------------------------- #
# sample_cpu
# --------------------------------------------------------------------------- #
class _FakeNoSuchProcess(Exception):
    def __init__(self, pid=None):
        self.pid = pid
        super().__init__(f"no such process: {pid}")


def _fake_psutil_module(cpu_count=1, cpu_percent=0.0, cpu_percent_raises=False):
    """psutil is an optional dependency and is not installed in this test
    environment (nor CI) — metrics_poller.psutil is None here. sample_cpu /
    sample_system_cpu's real logic can only be exercised by swapping in a
    fake module for the whole ``psutil`` name, not by patching attributes on
    ``None``."""
    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = _FakeNoSuchProcess
    mod.cpu_count = lambda logical=True: cpu_count
    if cpu_percent_raises:

        def _cpu_percent(interval=None):
            raise RuntimeError("boom")

        mod.cpu_percent = _cpu_percent
    else:
        mod.cpu_percent = lambda interval=None: cpu_percent
    return mod


class _FakeProc:
    def __init__(self, pid, user, system, name="proc", cmdline=None, children=None):
        self.pid = pid
        self._user = user
        self._system = system
        self._name = name
        self._cmdline = cmdline or [name]
        self._children = children or []

    def cpu_times(self):
        return SimpleNamespace(user=self._user, system=self._system)

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline

    def children(self, recursive=True):
        return self._children


class TestSampleCpu:
    def test_psutil_none_returns_zero(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", None)
        assert poller.sample_cpu({}, object()) == 0.0

    def test_root_none_returns_zero(self):
        assert poller.sample_cpu({}, None) == 0.0

    def test_first_call_primes_baseline(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module())
        root = _FakeProc(pid=100, user=0.0, system=0.0)
        monkeypatch.setattr(poller.time, "time", lambda: 1000.0)
        state = {}
        assert poller.sample_cpu(state, root) == 0.0
        assert state["prev"] == {100: 0.0}
        assert state["wall"] == 1000.0

    def test_second_call_returns_delta_based_pct(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_count=2))
        root = _FakeProc(pid=100, user=1.0, system=0.0)
        times = iter([1000.0, 1001.0])
        monkeypatch.setattr(poller.time, "time", lambda: next(times))

        state = {}
        poller.sample_cpu(state, root)  # priming call

        root._user = 2.0  # 1 extra CPU-second consumed over the elapsed 1s
        pct = poller.sample_cpu(state, root)
        # 1.0s delta / 1.0s elapsed / 2 cores * 100 = 50.0%
        assert pct == 50.0

    def test_contributors_populated_for_descendants(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_count=1))
        child = _FakeProc(pid=101, user=0.0, system=0.0, name="child")
        root = _FakeProc(pid=100, user=0.0, system=0.0, children=[child])
        times = iter([1000.0, 1001.0])
        monkeypatch.setattr(poller.time, "time", lambda: next(times))

        state = {}
        poller.sample_cpu(state, root)

        child._user = 0.5
        contributors = []
        poller.sample_cpu(state, root, contributors)
        assert any(pid == 101 and name == "child" for pid, _pct, name, _cmd in contributors)

    def test_excludes_self_pid_from_children(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_count=1))
        self_pid = os.getpid()
        # The poller subprocess appears in root.children(recursive=True); it
        # must be excluded so its own overhead isn't attributed to hermes.
        noisy_self = _FakeProc(pid=self_pid, user=99.0, system=99.0, name="self")
        root = _FakeProc(pid=100, user=0.0, system=0.0, children=[noisy_self])
        times = iter([1000.0, 1001.0])
        monkeypatch.setattr(poller.time, "time", lambda: next(times))

        state = {}
        poller.sample_cpu(state, root)
        assert self_pid not in state["prev"]

    def test_no_such_process_returns_zero(self, monkeypatch):
        fake_psutil = _fake_psutil_module()
        monkeypatch.setattr(poller, "psutil", fake_psutil)

        class _DyingProc(_FakeProc):
            def children(self, recursive=True):
                raise fake_psutil.NoSuchProcess(pid=self.pid)

        root = _DyingProc(pid=100, user=0.0, system=0.0)
        assert poller.sample_cpu({}, root) == 0.0


# --------------------------------------------------------------------------- #
# sample_system_cpu
# --------------------------------------------------------------------------- #
class TestSampleSystemCpu:
    def test_psutil_none_returns_zero(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", None)
        assert poller.sample_system_cpu() == 0.0

    def test_returns_psutil_cpu_percent(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_percent=33.3))
        assert poller.sample_system_cpu() == 33.3

    def test_exception_returns_zero(self, monkeypatch):
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_percent_raises=True))
        assert poller.sample_system_cpu() == 0.0


# --------------------------------------------------------------------------- #
# _open_append
# --------------------------------------------------------------------------- #
class TestOpenAppend:
    def test_new_file_gets_header(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fh, writer = poller._open_append(path, ["a", "b"])
        fh.close()
        assert (tmp_path / "out.csv").read_text().splitlines() == ["a,b"]

    def test_existing_nonempty_file_keeps_single_header(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fh, writer = poller._open_append(path, ["a", "b"])
        writer.writerow([1, 2])
        fh.close()

        fh2, writer2 = poller._open_append(path, ["a", "b"])
        writer2.writerow([3, 4])
        fh2.close()

        lines = (tmp_path / "out.csv").read_text().splitlines()
        assert lines == ["a,b", "1,2", "3,4"]


# --------------------------------------------------------------------------- #
# main() — fast, deterministic exit paths only (not the sampling loop itself)
# --------------------------------------------------------------------------- #
class TestMain:
    def test_too_few_args_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["metrics_poller.py", "onlyone"])
        assert poller.main() == 1
        assert "usage:" in capsys.readouterr().err

    def test_no_signals_enabled_returns_0_without_looping(self, monkeypatch, tmp_path):
        for name in ("HERMES_CPU_TRACE", "HERMES_CPU_SYSTEM_WIDE", "HERMES_GPU_SYSTEM_WIDE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(poller.signal, "signal", MagicMock())
        monkeypatch.setattr(
            sys, "argv", ["metrics_poller.py", str(tmp_path), "0.1", str(os.getpid())]
        )
        assert poller.main() == 0

    def test_invalid_interval_and_pid_fall_back_to_defaults(self, monkeypatch, tmp_path):
        for name in ("HERMES_CPU_TRACE", "HERMES_CPU_SYSTEM_WIDE", "HERMES_GPU_SYSTEM_WIDE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(poller.signal, "signal", MagicMock())
        monkeypatch.setattr(
            sys, "argv", ["metrics_poller.py", str(tmp_path), "not-a-float", "not-an-int"]
        )
        assert poller.main() == 0

    def test_one_full_tick_writes_system_wide_cpu_csv(self, monkeypatch, tmp_path):
        """Exercise the writer setup + one loop iteration + cleanup, stopping
        the loop after exactly one tick by having the mocked time.sleep flip
        _RUNNING off, instead of actually waiting on a real interval."""
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        monkeypatch.delenv("HERMES_CPU_TRACE", raising=False)
        monkeypatch.delenv("HERMES_GPU_SYSTEM_WIDE", raising=False)
        monkeypatch.setattr(poller, "psutil", _fake_psutil_module(cpu_percent=12.5))
        monkeypatch.setattr(poller.signal, "signal", MagicMock())
        monkeypatch.setattr(poller, "_RUNNING", True)

        def _stop_after_one_tick(seconds):
            poller._RUNNING = False

        monkeypatch.setattr(poller.time, "sleep", _stop_after_one_tick)
        monkeypatch.setattr(
            sys, "argv", ["metrics_poller.py", str(tmp_path), "0.01", str(os.getpid())]
        )

        assert poller.main() == 0

        csv_path = tmp_path / "cpu_system_wide.csv"
        lines = csv_path.read_text().splitlines()
        assert lines[0] == "timestamp,start_time_unix_nano,cpu_pct"
        assert len(lines) == 2  # header + exactly one sampled row
        assert lines[1].endswith(",12.5")
