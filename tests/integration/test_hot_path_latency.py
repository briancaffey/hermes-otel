"""Hook-thread latency guards (#91).

Three things used to run synchronously inside the hook thread, that is inside
the agent loop: the turn-end force-flush of every backend, a LangSmith HTTP
round-trip per span start and end, and a SQLite commit per live-store row.
These tests put a collector that never answers behind each path and assert
the hook returns promptly anyway, so a regression is visible.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

import hermes_otel.tracer as tracer_mod
from hermes_otel.hooks import on_session_end, on_session_start
from hermes_otel.langsmith_backend import LangSmithBackend
from hermes_otel.live_store import LiveStore
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin

# Generous bounds: the point is "milliseconds, not seconds", on a loaded CI box.
TURN_END_BUDGET_S = 0.5
PER_SPAN_BUDGET_S = 0.02


class _StuckExporter(SpanExporter):
    """An exporter that blocks for ``stall`` seconds: an unreachable collector."""

    def __init__(self, stall: float = 3.0) -> None:
        self.stall = stall
        self.calls = 0

    def export(self, spans):
        self.calls += 1
        time.sleep(self.stall)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        time.sleep(min(self.stall, timeout_millis / 1000))
        return False


@pytest.fixture()
def stuck_plugin():
    """A plugin whose only backend never answers."""
    exporter = _StuckExporter()
    provider = TracerProvider()
    processor = BatchSpanProcessor(exporter, schedule_delay_millis=60_000)
    provider.add_span_processor(processor)
    plugin = HermesOTelPlugin()
    plugin.config = HermesOtelConfig(
        force_flush_on_session_end=True, force_flush_wait_ms=100, dashboard_live=False
    )
    plugin.tracer = provider.get_tracer("test")
    plugin._span_processors = [processor]
    plugin._initialized = True
    tracer_mod._tracer = plugin
    try:
        yield exporter, plugin
    finally:
        plugin._flush_pending.clear()
        ex = plugin._flush_executor
        if ex is not None:
            ex.shutdown(wait=False)
        plugin._initialized = False


class TestTurnEndFlush:
    def test_session_end_returns_without_waiting_for_the_collector(self, stuck_plugin):
        exporter, plugin = stuck_plugin
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        t0 = time.perf_counter()
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        elapsed = time.perf_counter() - t0
        # Bounded by force_flush_wait_ms (100 ms here), not by the stuck collector.
        assert elapsed < TURN_END_BUDGET_S, f"on_session_end took {elapsed:.2f}s on the hook thread"
        assert elapsed >= 0.09, "the hook should have waited its budget for the stuck flush"
        plugin.flush_wait(timeout_s=5)
        assert not plugin._flush_pending.is_set()

    def test_wait_is_short_when_the_flush_is_fast(self, stuck_plugin):
        exporter, plugin = stuck_plugin
        plugin.config = HermesOtelConfig(
            force_flush_on_session_end=True, force_flush_wait_ms=2000, dashboard_live=False
        )
        fast = MagicMock()
        fast.force_flush.return_value = True
        plugin._span_processors = [fast]
        on_session_start(session_id="s3", model="gpt-4", platform="cli")
        t0 = time.perf_counter()
        on_session_end(
            session_id="s3", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert time.perf_counter() - t0 < 0.3  # returned as soon as the flush finished
        fast.force_flush.assert_called_once()

    def test_zero_wait_returns_immediately(self, stuck_plugin):
        exporter, plugin = stuck_plugin
        plugin.config = HermesOtelConfig(
            force_flush_on_session_end=True, force_flush_wait_ms=0, dashboard_live=False
        )
        on_session_start(session_id="s4", model="gpt-4", platform="cli")
        t0 = time.perf_counter()
        on_session_end(
            session_id="s4", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert time.perf_counter() - t0 < 0.05
        plugin.flush_wait(timeout_s=5)

    def test_flushes_coalesce_while_one_is_queued(self, stuck_plugin):
        exporter, plugin = stuck_plugin
        # A processor whose flush takes a while (a queue with spans behind a
        # stuck collector); an empty BatchSpanProcessor returns instantly.
        slow = MagicMock()
        slow.force_flush.side_effect = lambda timeout_millis=0: time.sleep(0.3)
        plugin._span_processors = [slow]
        assert plugin.flush_async() is True
        assert plugin.flush_async() is False  # already queued: no-op
        plugin.flush_wait(timeout_s=5)
        assert slow.force_flush.call_count == 1
        assert plugin.flush_async() is True
        plugin.flush_wait(timeout_s=5)
        assert slow.force_flush.call_count == 2

    def test_background_flush_covers_metric_and_log_providers_off_the_hook_thread(
        self, stuck_plugin
    ):
        # Since #233 the background flush also flushes the metric and log
        # providers: a one-shot ``hermes -z`` never reaches the 60 s metric
        # tick nor atexit, so this flush is the only metric export it gets.
        # It still runs on the worker thread, never on the hook thread.
        exporter, plugin = stuck_plugin
        plugin._meter_provider = MagicMock()
        plugin._logger_provider = MagicMock()
        plugin.flush_async()
        plugin.flush_wait(timeout_s=5)
        plugin._meter_provider.force_flush.assert_called_once()
        plugin._logger_provider.force_flush.assert_called_once()
        # Shutdown still flushes them synchronously (a second call).
        plugin._force_flush(timeout_millis=10)
        assert plugin._meter_provider.force_flush.call_count == 2
        assert plugin._logger_provider.force_flush.call_count == 2

    def test_no_flush_when_disabled(self, stuck_plugin):
        exporter, plugin = stuck_plugin
        plugin.config = HermesOtelConfig(force_flush_on_session_end=False, dashboard_live=False)
        on_session_start(session_id="s2", model="gpt-4", platform="cli")
        on_session_end(
            session_id="s2", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert not plugin._flush_pending.is_set()


class TestLangSmithQueue:
    def test_span_start_and_end_do_not_wait_on_http(self):
        gate = threading.Event()

        def stuck_urlopen(req, timeout=None):
            gate.wait(5)
            resp = MagicMock()
            resp.read.return_value = b""
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        backend = LangSmithBackend(api_key="k", endpoint="https://api.smith.test", project="p")
        with patch("hermes_otel.langsmith_backend.urllib.request.urlopen", stuck_urlopen):
            t0 = time.perf_counter()
            for i in range(20):
                run = backend.start_span(f"tool.t{i}", f"tool:t{i}", kind="tool")
                backend.end_span(run, attributes={"output.value": "ok"})
            elapsed = time.perf_counter() - t0
            assert elapsed < 20 * PER_SPAN_BUDGET_S, f"20 spans took {elapsed:.3f}s"
            assert backend._queue.qsize() >= 39  # queued, not sent
            gate.set()
            assert backend.flush(timeout=10)
        backend.shutdown()


class TestLiveStoreBatching:
    def test_writes_do_not_commit_on_the_calling_thread(self, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        try:
            t0 = time.perf_counter()
            for i in range(500):
                store.add_span(
                    {
                        "trace_id": f"{i:032x}",
                        "span_id": f"{i:016x}",
                        "name": "tool.x",
                        "start_time_unix_nano": time.time_ns(),
                        "end_time_unix_nano": time.time_ns(),
                        "attributes": {"i": i},
                    }
                )
            elapsed = time.perf_counter() - t0
            assert elapsed < 500 * PER_SPAN_BUDGET_S, f"500 add_span took {elapsed:.3f}s"
            # This process reads its own writes (flush on read) …
            assert store.stats()["spans"] == 500
            # … and another connection sees them once the writer has committed.
            store.flush()
            other = LiveStore(db_path=str(tmp_path / "live.db"))
            try:
                assert other.stats()["spans"] == 500
            finally:
                other.close()
        finally:
            store.close()

    def test_writer_thread_commits_without_a_read(self, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        store.flush_interval_s = 0.05
        try:
            store.add_metric("hermes.session.count", 1, {"platform": "cli"}, time.time_ns())
            other = LiveStore(db_path=str(tmp_path / "live.db"))
            try:
                deadline = time.perf_counter() + 2.0
                while time.perf_counter() < deadline and other.stats()["metrics"] == 0:
                    time.sleep(0.02)
                assert other.stats()["metrics"] == 1
            finally:
                other.close()
        finally:
            store.close()

    def test_close_flushes_pending_rows(self, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        store.add_span({"name": "a"})
        store.close()
        other = LiveStore(db_path=str(tmp_path / "live.db"))
        try:
            assert [s["name"] for s in other.spans()] == ["a"]
        finally:
            other.close()
