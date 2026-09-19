"""Lifecycle and shared-state fixes from #105.

Covers: ``shutdown()`` + idempotent ``init()``, the per-session aggregator
leak through read helpers and the orphan sweep, the sweep leaving the caller's
ContextVar stack alone, per-backend-wins header precedence, the locks on the
shared state classes, and the ``HERMES_HOME``-aware debug log.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from hermes_otel import hooks
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.session_state import SessionState
from hermes_otel.span_tracker import SpanTracker
from hermes_otel.tracer import HermesOTelPlugin

_NO_BACKEND_ENV = (
    "OTEL_PHOENIX_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_LANGFUSE_ENDPOINT",
    "LANGSMITH_TRACING",
    "OTEL_SIGNOZ_ENDPOINT",
    "OTEL_JAEGER_ENDPOINT",
    "OTEL_TEMPO_ENDPOINT",
)


@pytest.fixture
def live_only_plugin(monkeypatch, tmp_path):
    """A plugin that init()s in live-only mode: no network, real pipeline."""
    for var in _NO_BACKEND_ENV:
        monkeypatch.delenv(var, raising=False)
    import hermes_otel.live_store as ls

    monkeypatch.setattr(ls, "_default_db_path", lambda: str(tmp_path / "live.db"))
    import hermes_otel.tracer as tracer_mod

    plugin = HermesOTelPlugin(config=HermesOtelConfig(dashboard_live=True, capture_logs=True))
    tracer_mod._tracer = plugin
    yield plugin
    plugin.shutdown()


class TestShutdownAndReinit:
    def test_shutdown_resets_state_and_is_idempotent(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="s1", model="m", platform="cli")
        hooks.on_pre_tool_call(tool_name="bash", args={}, task_id="t1", session_id="s1")
        assert plugin.spans.active_count() == 2
        assert plugin.sessions.has("s1")

        plugin.shutdown()

        assert plugin.is_enabled is False
        assert plugin.spans.active_count() == 0
        assert plugin.sessions.active_count() == 0
        assert plugin._span_processors == [] and plugin._metric_readers == []
        # Open spans were ended, not dropped.
        assert sorted(s.name for s in exporter.get_finished_spans()) == ["agent", "tool.bash"]
        plugin.shutdown()  # second call is a no-op, never raises
        # After shutdown, hooks are inert.
        assert plugin.start_span("x", "k") is not None
        assert plugin.spans.active_count() == 0

    def test_reinit_replaces_the_pipeline_without_stacking(self, live_only_plugin):
        plugin = live_only_plugin
        root = logging.getLogger()
        level_before = root.level
        assert plugin.init() is True
        first_provider = plugin._tracer_provider
        first_handler = plugin._live_log_handler
        assert first_provider is not None and first_handler in root.handlers

        assert plugin.init() is True  # reload / reconfigure
        assert plugin.is_enabled
        assert plugin._tracer_provider is not first_provider
        # The first live-log handler is gone; exactly one of ours is attached.
        ours = [h for h in root.handlers if type(h).__name__ == "_LiveLogHandler"]
        assert ours == [plugin._live_log_handler]
        # The tracer in use belongs to the new provider, not the once-only global.
        assert plugin.tracer is not None
        span = plugin.start_span("probe", "probe", session_id="s")
        assert span.is_recording()
        plugin.end_span("probe")

        plugin.shutdown()
        assert not any(type(h).__name__ == "_LiveLogHandler" for h in root.handlers)
        assert root.level == level_before

    def test_atexit_registers_shutdown_once(self, live_only_plugin, monkeypatch):
        import hermes_otel.tracer as tracer_mod

        registered = []
        # The SDK's processors register their own atexit hooks through the same
        # module; count only ours.
        monkeypatch.setattr(
            tracer_mod.atexit, "register", lambda fn, *a, **k: registered.append(fn)
        )
        assert live_only_plugin.init() is True
        assert live_only_plugin.init() is True
        ours = [f for f in registered if getattr(f, "__self__", None) is live_only_plugin]
        assert [f.__name__ for f in ours] == ["shutdown"]


class TestPerSessionLeak:
    def test_correlation_helper_is_read_only(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        attrs = hooks._correlation_attributes(plugin, "ghost", {})
        assert attrs == {"correlation.id": "ghost"}
        assert not plugin.sessions.has("ghost")

    def test_session_start_pins_the_correlation_id(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="s1", model="m", platform="cli", correlation_id="corr-1")
        assert plugin.sessions.peek("s1").correlation_id == "corr-1"
        hooks.on_pre_tool_call(tool_name="bash", args={}, task_id="t1", session_id="s1")
        hooks.on_post_tool_call(
            tool_name="bash", args={}, result="ok", task_id="t1", session_id="s1"
        )
        tool = next(s for s in exporter.get_finished_spans() if s.name == "tool.bash")
        assert tool.attributes["correlation.id"] == "corr-1"

    def test_subagent_start_does_not_mint_a_parent_aggregator(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        hooks.on_subagent_start(
            parent_session_id="never-started", child_session_id="c1", child_role="leaf", goal="g"
        )
        assert not plugin.sessions.has("never-started")
        assert plugin.spans.get_subagent("c1") is not None
        hooks.on_subagent_stop(parent_session_id="never-started", child_session_id="c1")
        assert plugin.spans.get_subagent("c1") is None

    def test_api_error_without_a_session_span_creates_nothing(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        hooks.on_api_request_error(
            task_id="t", session_id="ghost", error_type="RateLimitError", error_message="429"
        )
        assert not plugin.sessions.has("ghost")

    def test_orphan_sweep_drops_the_aggregator(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        hooks.on_session_start(session_id="dead", model="m", platform="cli")
        plugin.register_turn("dead", started_at=time.perf_counter() - 10.0)
        assert plugin.sessions.has("dead")
        assert plugin.sweep_expired_turns() == ["dead"]
        assert not plugin.sessions.has("dead")
        assert plugin.spans.get_current_parent("dead") is None
        assert plugin.spans.active_count() == 0

    def test_gateway_churn_leaves_no_state(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        for i in range(200):
            sid = f"s{i}"
            hooks.on_session_start(session_id=sid, model="m", platform="cli")
            hooks.on_subagent_start(parent_session_id=sid, child_session_id=f"c{i}", goal="g")
            hooks.on_subagent_stop(parent_session_id=sid, child_session_id=f"c{i}")
            hooks.on_session_end(
                session_id=sid, completed=True, interrupted=False, model="m", platform="cli"
            )
        assert plugin.sessions.active_count() == 0
        assert plugin.spans.active_count() == 0
        assert plugin.spans._session_parent_stacks == {}
        assert plugin.spans._subagent_registry == {}


class TestOrphanSweepContextStack:
    def test_expiring_one_session_keeps_the_callers_context_parent(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        hooks.on_session_start(session_id="old", model="m", platform="cli")
        hooks.on_session_start(session_id="live", model="m", platform="cli")
        live_root = plugin.spans.get_current_parent("live")
        # Back-date "old" only now: on_session_start sweeps too, and it must not
        # have collected "old" before "live" pushed its own parent.
        plugin.register_turn("old", started_at=time.perf_counter() - 10.0)
        # The sweep runs inside "live"'s context (e.g. from its pre_llm_call).
        assert plugin.sweep_expired_turns() == ["old"]
        assert plugin.spans.get_current_parent(None) is live_root  # ContextVar fallback intact
        assert plugin.spans.get_current_parent("live") is live_root
        assert plugin.spans.get_session_root("old") is None


class TestHeaderPrecedence:
    def test_per_backend_header_wins_over_global(self):
        plugin = HermesOTelPlugin(
            config=HermesOtelConfig(headers={"X-Both": "global", "X-Global": "g"})
        )
        merged = plugin._merge_headers({"X-Both": "backend", "Authorization": "Basic x"})
        assert merged == {"X-Global": "g", "X-Both": "backend", "Authorization": "Basic x"}
        assert plugin._merge_headers(None) == {"X-Both": "global", "X-Global": "g"}


class TestSharedStateLocks:
    def test_tracker_and_sessions_survive_concurrent_hooks(self):
        tracker = SpanTracker()
        sessions = SessionState()
        errors = []

        def worker(n):
            try:
                for i in range(300):
                    sid = f"s{n}-{i % 7}"
                    tracker.push_parent(object(), session_id=sid)
                    sessions.get_or_create(sid).turn_summary.api_call_count += 1
                    sessions.next_turn(sid)
                    tracker.register_skill_span(sid, "k", f"skill:{sid}:k")
                    tracker.pop_skill_spans(sid)
                    tracker.pop_parent(session_id=sid)
                    sessions.pop(sid)
            except Exception as e:  # pragma: no cover — the assertion below reports it
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert tracker._session_parent_stacks == {}
        assert sessions.active_count() == 0

    def test_lock_is_reentrant_for_end_all(self):
        tracker = SpanTracker()

        class _Span:
            def set_attribute(self, *a):
                pass

            def set_status(self, *a):
                pass

            def end(self):
                pass

        tracker.start_span("a", _Span())
        tracker.end_all()
        assert tracker.active_count() == 0


class TestDebugLog:
    def test_path_follows_hermes_home_and_writes_once_opened(self, monkeypatch, tmp_path):
        import hermes_otel.debug_utils as du

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(du, "_DEBUG_ENABLED", True)
        du.close_debug_log()
        assert du.debug_log_path() == str(tmp_path / "plugins" / "hermes_otel" / "debug.log")
        du.debug_log("one")
        du.debug_log("two")
        du.close_debug_log()
        assert (tmp_path / "plugins" / "hermes_otel" / "debug.log").read_text().splitlines() == [
            "one",
            "two",
        ]
