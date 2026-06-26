"""Integration: the live span processor + record_metric tap feed the store.

Mirrors conftest's in-memory plugin wiring but adds a ``_LiveSpanProcessor`` and
flips ``_live_active`` on — the same shape the zero-config (no-backend) path
produces in ``_init_otlp_pipeline``.
"""

import pytest
from hermes_otel import hooks
from hermes_otel.live_store import LiveStore, get_live_store
from hermes_otel.plugin_config import HermesOtelConfig


@pytest.fixture()
def live_plugin():
    import hermes_otel.live_store as ls
    import hermes_otel.tracer as tracer_mod
    from hermes_otel.tracer import HermesOTelPlugin, _LiveSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    ls._LIVE_STORE = LiveStore()  # fresh store for this test
    store = get_live_store()

    provider = TracerProvider(resource=Resource.create({"service.name": "live-test"}))
    provider.add_span_processor(_LiveSpanProcessor(store))

    plugin = HermesOTelPlugin()
    plugin.tracer = provider.get_tracer("live-test")
    plugin._initialized = True
    plugin._live_active = True
    plugin.config = HermesOtelConfig(dashboard_live=True)

    prev = tracer_mod._tracer
    tracer_mod._tracer = plugin
    try:
        yield store, plugin
    finally:
        tracer_mod._tracer = prev
        ls._LIVE_STORE = None
        provider.shutdown()


class TestLivePipeline:
    def test_full_turn_lands_in_store(self, live_plugin):
        store, _ = live_plugin
        hooks.on_session_start(session_id="s1", model="gpt-4", platform="telegram")
        hooks.on_pre_tool_call(
            tool_name="bash", args={"command": "ls"}, task_id="t1", session_id="s1"
        )
        hooks.on_post_tool_call(
            tool_name="bash", args={}, result="ok", task_id="t1", session_id="s1"
        )
        hooks.on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )

        spans = store.spans()
        names = sorted(s["name"] for s in spans)
        assert "agent" in names and "tool.bash" in names
        # span dicts carry the trace tree + attributes the dashboard renders
        agent = next(s for s in spans if s["name"] == "agent")
        assert len(agent["trace_id"]) == 32 and len(agent["span_id"]) == 16
        assert agent["status"] in ("OK", "UNSET")
        assert agent["attributes"].get("hermes.session.kind") == "session"
        # tool span is a child (has a parent in the same trace)
        tool = next(s for s in spans if s["name"] == "tool.bash")
        assert tool["parent_span_id"] is not None
        assert tool["trace_id"] == agent["trace_id"]

    def test_metrics_tapped_without_meterprovider(self, live_plugin):
        # No MeterProvider is wired (live-only), yet record_metric still feeds
        # the live store because the tap runs before the meter guard.
        store, plugin = live_plugin
        assert plugin._meter is None
        hooks.on_session_start(session_id="s2", model="gpt-4", platform="cli")
        names = [m["name"] for m in store.metrics()]
        assert "session_count" in names

    def test_incremental_cursor(self, live_plugin):
        store, _ = live_plugin
        hooks.on_session_start(session_id="s3", model="gpt-4", platform="cli")
        cur = store.cursor()
        assert store.spans(since=cur) == []
        hooks.on_session_end(
            session_id="s3", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        fresh = store.spans(since=cur)
        assert any(s["name"] == "agent" for s in fresh)
