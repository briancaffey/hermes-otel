"""Integration tests for human-in-the-loop approval spans (issue #30)."""

from hermes_otel.hooks import (
    on_post_approval_response,
    on_post_tool_call,
    on_pre_approval_request,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)

SID = "20260626_010101_abc123"
TURN = f"{SID}:task7:deadbeef"


def _parent_span_id(span):
    return span.parent.span_id if span.parent is not None else None


def _one(spans, name):
    matches = [s for s in spans if s.name == name]
    assert len(matches) == 1, f"expected one {name!r}, got {[s.name for s in spans]}"
    return matches[0]


def _approve(choice, tool_call_id="tc1", pattern_key="rm_rf", command="rm -rf /tmp/x"):
    on_pre_approval_request(
        command=command,
        description="Delete a directory tree",
        pattern_key=pattern_key,
        pattern_keys=[pattern_key, "danger"],
        session_key="agent:main:telegram:dm:5490634439",
        surface="gateway",
        turn_id=TURN,
        tool_call_id=tool_call_id,
    )
    on_post_approval_response(
        command=command,
        pattern_key=pattern_key,
        session_key="agent:main:telegram:dm:5490634439",
        surface="gateway",
        choice=choice,
        turn_id=TURN,
        tool_call_id=tool_call_id,
    )


class TestApprovalSpanFlow:
    def test_full_flow_grant_under_turn_correlated_to_tool(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        on_pre_tool_call(
            tool_name="bash", args={"command": "rm -rf /tmp/x"}, task_id="tc1", session_id=SID
        )
        _approve("once", tool_call_id="tc1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="tc1", session_id=SID)
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )

        spans = exporter.get_finished_spans()
        approval = _one(spans, "approval.rm_rf")
        agent = _one(spans, "agent")
        attrs = dict(approval.attributes)
        assert attrs["hermes.approval.choice"] == "once"
        assert attrs["hermes.approval.granted"] is True
        assert attrs["hermes.approval.timed_out"] is False
        assert attrs["hermes.approval.surface"] == "gateway"
        assert attrs["gen_ai.tool.call.id"] == "tc1"  # correlates to the tool call
        assert "hermes.approval.command" in attrs
        assert attrs["hermes.approval.duration_ms"] >= 0
        # Parented within the turn (under the agent root in this minimal flow).
        assert _parent_span_id(approval) == agent.context.span_id

    def test_deny_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        _approve("deny")
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        attrs = dict(_one(exporter.get_finished_spans(), "approval.rm_rf").attributes)
        assert attrs["hermes.approval.choice"] == "deny"
        assert attrs["hermes.approval.granted"] is False
        assert attrs["hermes.approval.timed_out"] is False

    def test_timeout_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        _approve("timeout")
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        attrs = dict(_one(exporter.get_finished_spans(), "approval.rm_rf").attributes)
        assert attrs["hermes.approval.timed_out"] is True
        assert attrs["hermes.approval.granted"] is False

    def test_post_without_pre_does_not_raise(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        # No pre_approval_request — must not raise, just no span / no duration.
        on_post_approval_response(
            pattern_key="rm_rf", choice="once", turn_id=TURN, tool_call_id="tc1"
        )
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        assert [s for s in exporter.get_finished_spans() if s.name == "approval.rm_rf"] == []


class TestApprovalPrivacy:
    def test_command_suppressed_when_capture_previews_off(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig

        plugin.config = HermesOtelConfig(capture_previews=False)
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        _approve("once", command="rm -rf /secret")
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        attrs = dict(_one(exporter.get_finished_spans(), "approval.rm_rf").attributes)
        # The privacy kill-switch suppresses the command/description previews,
        # but the decision metadata still flows.
        assert "hermes.approval.command" not in attrs
        assert "hermes.approval.description" not in attrs
        assert attrs["hermes.approval.choice"] == "once"
        assert attrs["hermes.approval.pattern_key"] == "rm_rf"


class TestApprovalFanout:
    def test_fans_out_to_both_backends(self, two_exporter_pipeline):
        exporter_a, exporter_b, _ = two_exporter_pipeline
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        _approve("session")
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        for exp in (exporter_a, exporter_b):
            assert any(s.name == "approval.rm_rf" for s in exp.get_finished_spans())


class TestApprovalMetrics:
    def test_count_and_duration_recorded(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        _approve("once")
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )

        data = metric_reader.get_metrics_data()
        metrics = {
            m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "hermes.approval.count" in metrics
        assert "hermes.approval.duration" in metrics
        count_pt = next(iter(metrics["hermes.approval.count"].data.data_points))
        assert count_pt.attributes.get("choice") == "once"
        assert count_pt.attributes.get("pattern_key") == "rm_rf"
