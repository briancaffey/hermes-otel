"""Integration tests: per-tool CPU/GPU utilization attributes on tool spans.

The host-metrics sampler is replaced by a fake whose ``window()`` returns
canned stats, so the tests assert the hook wiring (window bounds, rounding,
absence when off) without a sampler thread.
"""

import time

from hermes_otel.hooks import on_post_tool_call, on_pre_tool_call
from hermes_otel.host_metrics import WindowStats


class _FakeSampler:
    running = True

    def __init__(self, stats):
        self.stats = stats
        self.calls = []

    def window(self, start, end):
        self.calls.append((start, end))
        return self.stats


def _tool_call(session_id="s1", task_id="t1"):
    on_pre_tool_call(
        tool_name="terminal", args={"command": "ls"}, task_id=task_id, session_id=session_id
    )
    on_post_tool_call(
        tool_name="terminal",
        args={"command": "ls"},
        result="ok",
        task_id=task_id,
        session_id=session_id,
    )


class TestToolUtilizationAttributes:
    def test_absent_when_host_metrics_off(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        assert plugin.host_metrics is None
        _tool_call()
        (span,) = exporter.get_finished_spans()
        assert not [k for k in span.attributes if k.startswith("hermes.tool.cpu")]

    def test_cpu_and_gpu_attributes_from_window(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        fake = _FakeSampler(
            WindowStats(samples=3, cpu_avg=0.123456, cpu_peak=0.5, gpu_avg=0.25, gpu_peak=0.75)
        )
        plugin._host_metrics = fake
        before = time.perf_counter()
        _tool_call()
        after = time.perf_counter()
        (span,) = exporter.get_finished_spans()
        assert span.attributes["hermes.tool.cpu.utilization.avg"] == 0.1235
        assert span.attributes["hermes.tool.cpu.utilization.peak"] == 0.5
        assert span.attributes["hermes.tool.gpu.utilization.avg"] == 0.25
        assert span.attributes["hermes.tool.gpu.utilization.peak"] == 0.75
        ((start, end),) = fake.calls
        assert before <= start <= end <= after

    def test_gpu_attributes_omitted_without_gpu(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        plugin._host_metrics = _FakeSampler(WindowStats(1, 0.2, 0.2, None, None))
        _tool_call()
        (span,) = exporter.get_finished_spans()
        assert span.attributes["hermes.tool.cpu.utilization.avg"] == 0.2
        assert "hermes.tool.gpu.utilization.avg" not in span.attributes

    def test_no_samples_in_window_means_no_attributes(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        plugin._host_metrics = _FakeSampler(None)
        _tool_call()
        (span,) = exporter.get_finished_spans()
        assert "hermes.tool.cpu.utilization.avg" not in span.attributes

    def test_sampler_errors_never_break_the_hook(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        class _Broken(_FakeSampler):
            def window(self, start, end):
                raise RuntimeError("boom")

        plugin._host_metrics = _Broken(None)
        _tool_call()
        (span,) = exporter.get_finished_spans()
        assert span.name == "tool.terminal"

    def test_post_without_pre_has_no_window(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        fake = _FakeSampler(WindowStats(1, 0.2, 0.2, None, None))
        plugin._host_metrics = fake
        on_post_tool_call(
            tool_name="terminal", args={}, result="ok", task_id="orphan", session_id="s1"
        )
        assert fake.calls == []
