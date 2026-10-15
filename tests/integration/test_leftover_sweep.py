"""#477: child spans still open when their turn or session ends are ended,
marked ``hermes.span.ended_by=sweep`` with a ``hermes.span.sweep_reason``,
counted in ``hermes.span.swept.count``, and exported before the root.

Before, a tool whose ``post_tool_call`` never fired stayed open past
``on_session_end``: never exported until shutdown, and then with no marker.
"""

from __future__ import annotations

from _helpers import get_metric, metric_points
from helpers.turns import api_request_kwargs
from opentelemetry.trace import StatusCode

from hermes_otel.hooks import (
    on_post_tool_call,
    on_pre_api_request,
    on_pre_tool_call,
    on_session_end,
    on_session_finalize,
    on_session_start,
)
from hermes_otel.span_tracker import SWEEP_REASONS, sweep_attributes


def _tool(session_id: str, call_id: str) -> None:
    on_pre_tool_call(
        session_id=session_id,
        tool_name="terminal",
        args={"command": "sleep 30"},
        task_id="t1",
        platform="cli",
        model="m",
        tool_call_id=call_id,
    )


def _end(session_id: str) -> None:
    on_session_end(
        session_id=session_id, completed=False, interrupted=True, model="m", platform="cli"
    )


def _swept(spans):
    return {
        s.name: s.attributes.get("hermes.span.sweep_reason")
        for s in spans
        if s.attributes.get("hermes.span.ended_by") == "sweep"
    }


class TestTurnEnd:
    def test_open_tool_is_ended_marked_and_exported_before_the_root(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        _end("s1")
        spans = exporter.get_finished_spans()
        assert [s.name for s in spans] == ["tool.terminal", "agent"]
        tool, root = spans
        assert tool.attributes["hermes.span.ended_by"] == "sweep"
        assert tool.attributes["hermes.span.sweep_reason"] == "turn_end"
        assert tool.status.status_code == StatusCode.OK
        assert tool.parent.span_id == root.context.span_id
        assert "hermes.span.ended_by" not in root.attributes
        assert plugin.spans.active_count() == 0

    def test_a_normally_ended_tool_carries_no_marker(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        on_post_tool_call(
            session_id="s1",
            tool_name="terminal",
            args={"command": "sleep 30"},
            result="ok",
            task_id="t1",
            platform="cli",
            model="m",
            tool_call_id="c1",
        )
        _end("s1")
        assert _swept(exporter.get_finished_spans()) == {}

    def test_an_api_span_cut_mid_stream_is_swept(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        on_pre_api_request(**api_request_kwargs("s1", "t1"))
        _end("s1")
        swept = _swept(exporter.get_finished_spans())
        assert list(swept.values()) == ["turn_end"]
        assert next(iter(swept)).startswith("api.")

    def test_counted_per_kind_and_reason(self, inmemory_otel_with_metrics):
        _, reader, _plugin = inmemory_otel_with_metrics
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        _tool("s1", "c2")
        _end("s1")
        (point,) = metric_points(get_metric(reader, "hermes.span.swept.count"))
        assert point.value == 2
        assert dict(point.attributes) == {
            "span_kind": "terminal",
            "reason": "turn_end",
            "profile": _plugin.profile_name,
        }

    def test_nothing_to_sweep_records_nothing(self, inmemory_otel_with_metrics):
        _, reader, _plugin = inmemory_otel_with_metrics
        on_session_start(session_id="s1", model="m", platform="cli")
        _end("s1")
        assert get_metric(reader, "hermes.span.swept.count") is None

    def test_another_sessions_children_are_left_alone(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_start(session_id="a", model="m", platform="cli")
        on_session_start(session_id="b", model="m", platform="cli")
        _tool("a", "ca")
        _tool("b", "cb")
        _end("a")
        assert _swept(exporter.get_finished_spans()) == {"tool.terminal": "turn_end"}
        assert plugin.spans.has_span("terminal:cb")


class TestOtherReasons:
    def test_session_finalize_sweeps_with_session_end(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        on_session_finalize(session_id="s1", platform="cli", reason="exit")
        assert _swept(exporter.get_finished_spans()) == {"tool.terminal": "session_end"}

    def test_ttl_sweep_says_ttl(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        plugin.spans.register_turn("s1", started_at=-10_000.0)
        assert plugin.sweep_expired_turns() == ["s1"]
        assert _swept(exporter.get_finished_spans()) == {"tool.terminal": "ttl"}

    def test_shutdown_says_shutdown(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_start(session_id="s1", model="m", platform="cli")
        _tool("s1", "c1")
        plugin.shutdown()
        swept = _swept(exporter.get_finished_spans())
        assert swept == {"tool.terminal": "shutdown", "agent": "shutdown"}

    def test_every_reason_is_documented(self):
        for reason in SWEEP_REASONS:
            assert sweep_attributes(reason)["hermes.span.sweep_reason"] == reason
