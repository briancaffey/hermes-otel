"""``hermes_otel.testing`` (#411): the supported seam tests build on."""

from __future__ import annotations

from hermes_otel import live_store, redaction, span_tracker, testing, tracer
from hermes_otel.hooks import get_tracer, on_session_end, on_session_start
from hermes_otel.plugin_config import HermesOtelConfig


class TestSingleton:
    def test_install_returns_the_previous_and_clears_the_parent_stack(self):
        a = testing.build_inmemory_plugin()
        b = testing.build_inmemory_plugin()
        try:
            assert testing.install_singleton(a.plugin) is None
            span_tracker._PARENT_STACK.set(["stale"])
            assert testing.install_singleton(b.plugin) is a.plugin
            assert span_tracker._PARENT_STACK.get() is None
            assert get_tracer() is b.plugin
        finally:
            a.shutdown()
            b.shutdown()

    def test_reset_state_forgets_everything(self, tmp_path):
        pipeline = testing.build_inmemory_plugin()
        testing.install_singleton(pipeline.plugin)
        span_tracker._PARENT_STACK.set(["x"])
        store = live_store.LiveStore(db_path=str(tmp_path / "live.db"))
        live_store._LIVE_STORE = store
        redaction._hermes_resolved, redaction._hermes_redactor = True, (lambda s: s)
        testing.reset_state()
        assert tracer._tracer is None
        assert span_tracker._PARENT_STACK.get() is None
        assert live_store._LIVE_STORE is None
        assert redaction._hermes_resolved is False and redaction._hermes_redactor is None
        pipeline.shutdown()


class TestInMemoryPipeline:
    def test_spans_only_by_default(self):
        p = testing.build_inmemory_plugin(exporters=2)
        try:
            testing.install_singleton(p.plugin)
            on_session_start(session_id="s", model="m", platform="cli")
            on_session_end(
                session_id="s", completed=True, interrupted=False, model="m", platform="cli"
            )
            names = [s.name for s in p.span_exporter.get_finished_spans()]
            assert names == ["agent"]
            assert [s.name for s in p.span_exporters[1].get_finished_spans()] == ["agent"]
            assert p.metric_reader is None and p.log_exporter is None
            assert p.plugin._span_processors and p.plugin.is_enabled
        finally:
            p.shutdown()

    def test_metrics_and_logs_are_wired_on_request(self):
        cfg = HermesOtelConfig(capture_logs=True, log_events=True, dashboard_live=False)
        p = testing.build_inmemory_plugin(metrics=True, logs=True, config=cfg, profile_name="seam")
        try:
            testing.install_singleton(p.plugin)
            assert p.plugin.profile_name == "seam"
            assert p.plugin._instruments and p.plugin._meter is not None
            assert p.plugin._logger_provider is not None
            on_session_start(session_id="s", model="m", platform="cli")
            on_session_end(
                session_id="s", completed=True, interrupted=False, model="m", platform="cli"
            )
            data = p.metric_reader.get_metrics_data()
            metric_names = {
                m.name
                for rm in data.resource_metrics
                for sm in rm.scope_metrics
                for m in sm.metrics
            }
            assert "hermes.session.count" in metric_names
            assert p.log_exporter.get_finished_logs()  # the turn events landed
            # No Hermes redactor lookup inside a test pipeline…
            assert redaction._hermes_resolved is True and redaction._hermes_redactor is None
        finally:
            p.shutdown()
        # …and the previous values are restored on shutdown.
        assert redaction._hermes_resolved is False
