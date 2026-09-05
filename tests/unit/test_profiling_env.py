"""Tests for the HERMES_* profiling feature-flag helpers in profiling_env.py."""

import pytest

from hermes_otel import profiling_env as env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no HERMES_* profiling flag leaks in from the real environment."""
    for name in (
        "HERMES_CPU_TRACE",
        "HERMES_CPU_SYSTEM_WIDE",
        "HERMES_GPU_SYSTEM_WIDE",
        "HERMES_TOOL_TRACE",
        "HERMES_PLOT_PROFILING",
        "HERMES_GPU_VENDOR",
    ):
        monkeypatch.delenv(name, raising=False)


class TestFlagParsing:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "On"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("HERMES_CPU_TRACE", value)
        assert env.cpu_trace() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "garbage"])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("HERMES_CPU_TRACE", value)
        assert env.cpu_trace() is False

    def test_unset_is_falsy(self):
        assert env.cpu_trace() is False

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_TRACE", "  true  ")
        assert env.cpu_trace() is True


class TestIndividualFlags:
    def test_cpu_system_wide(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        assert env.cpu_system_wide() is True

    def test_gpu_system_wide(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_SYSTEM_WIDE", "true")
        assert env.gpu_system_wide() is True

    def test_tool_trace(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        assert env.tool_trace() is True

    def test_plot_profiling(self, monkeypatch):
        monkeypatch.setenv("HERMES_PLOT_PROFILING", "true")
        assert env.plot_profiling() is True


class TestGpuVendor:
    def test_defaults_to_auto(self):
        assert env.gpu_vendor() == "auto"

    def test_override(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_VENDOR", "AMD")
        assert env.gpu_vendor() == "amd"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_VENDOR", " nvidia ")
        assert env.gpu_vendor() == "nvidia"


class TestHardwareTraceEnabled:
    def test_false_when_nothing_set(self):
        assert env.hardware_trace_enabled() is False

    def test_true_when_cpu_trace_only(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_TRACE", "true")
        assert env.hardware_trace_enabled() is True

    def test_true_when_cpu_system_wide_only(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        assert env.hardware_trace_enabled() is True

    def test_true_when_gpu_system_wide_only(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_SYSTEM_WIDE", "true")
        assert env.hardware_trace_enabled() is True

    def test_false_when_only_tool_trace_set(self, monkeypatch):
        # tool_trace() alone does not require the out-of-process poller.
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        assert env.hardware_trace_enabled() is False


class TestAnyEnabled:
    def test_false_when_nothing_set(self):
        assert env.any_enabled() is False

    def test_true_when_only_tool_trace_set(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        assert env.any_enabled() is True

    def test_true_when_only_hardware_trace_set(self, monkeypatch):
        monkeypatch.setenv("HERMES_GPU_SYSTEM_WIDE", "true")
        assert env.any_enabled() is True

    def test_true_when_everything_set(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_TRACE", "true")
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        monkeypatch.setenv("HERMES_GPU_SYSTEM_WIDE", "true")
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        assert env.any_enabled() is True
