"""Integration tests for sub-agent (delegation) span linking.

Issue #27: a delegated child agent must be modeled as a span nested under the
parent's turn, and the child's own root span must rejoin the parent trace so
the whole multi-agent run is one connected tree (in-process delegation) — or,
when only a SpanContext is available (cross-process), attached via a span link.
"""

import pytest
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
    on_subagent_start,
    on_subagent_stop,
)
from opentelemetry.trace import StatusCode


def _spans_by_name(spans, name):
    return [s for s in spans if s.name == name]


def _span_by_name(spans, name):
    matches = _spans_by_name(spans, name)
    if not matches:
        raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")
    return matches[0]


def _span_where(spans, name, attr, value):
    for s in spans:
        if s.name == name and dict(s.attributes).get(attr) == value:
            return s
    raise ValueError(f"No '{name}' span with {attr}={value!r}")


def _parent_span_id(span):
    return span.parent.span_id if span.parent is not None else None


# ── Helpers to drive parent turn state up to "an API request is in flight" ──


def _open_parent_turn(session_id="parent", model="gpt-4"):
    on_session_start(session_id=session_id, model=model, platform="cli")
    on_pre_llm_call(
        session_id=session_id,
        user_message="delegate something",
        conversation_history=[],
        is_first_turn=True,
        model=model,
        platform="cli",
    )
    on_pre_api_request(
        task_id="papi",
        session_id=session_id,
        platform="cli",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=3,
        tool_count=1,
        approx_input_tokens=100,
        request_char_count=400,
        max_tokens=1024,
    )


def _close_parent_turn(session_id="parent", model="gpt-4"):
    on_post_api_request(
        task_id="papi",
        session_id=session_id,
        platform="cli",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=1.0,
        finish_reason="stop",
        message_count=3,
        response_model=model,
        usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        assistant_content_chars=200,
        assistant_tool_call_count=1,
    )
    on_post_llm_call(
        session_id=session_id,
        user_message="delegate something",
        assistant_response="done",
        conversation_history=[],
        model=model,
        platform="cli",
    )
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model=model, platform="cli"
    )


def _run_child(child_session_id, model="gpt-4"):
    """Simulate a delegated child agent's own hook sequence (in-process)."""
    on_session_start(session_id=child_session_id, model=model, platform="cli")
    on_pre_llm_call(
        session_id=child_session_id,
        user_message="child work",
        conversation_history=[],
        is_first_turn=True,
        model=model,
        platform="cli",
    )
    on_post_llm_call(
        session_id=child_session_id,
        user_message="child work",
        assistant_response="child result",
        conversation_history=[],
        model=model,
        platform="cli",
    )
    on_session_end(
        session_id=child_session_id,
        completed=True,
        interrupted=False,
        model=model,
        platform="cli",
    )


class TestSingleDelegationOneTrace:
    def test_child_rejoins_parent_trace_as_one_tree(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        _open_parent_turn()
        on_pre_tool_call(
            tool_name="delegate_task",
            args={"goal": "research X"},
            task_id="deltool",
            session_id="parent",
        )
        on_subagent_start(
            parent_session_id="parent",
            parent_turn_id="turn-1",
            parent_subagent_id="root",
            child_session_id="child-1",
            child_subagent_id="sa-1",
            child_role="researcher",
            child_goal="research X thoroughly",
        )
        _run_child("child-1")
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="child-1",
            child_role="researcher",
            child_summary="found the answer",
            child_status="completed",
            duration_ms=4200,
        )
        on_post_tool_call(
            tool_name="delegate_task",
            args={"goal": "research X"},
            result="ok",
            task_id="deltool",
            session_id="parent",
        )
        _close_parent_turn()

        spans = exporter.get_finished_spans()

        # The headline assertion: the entire multi-agent run is ONE trace.
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1, f"expected 1 trace, got {len(trace_ids)}"

        delegation = _span_by_name(spans, "subagent.researcher")
        parent_api = _span_by_name(spans, "api.gpt-4")
        parent_agent = _span_where(spans, "agent", "session.id", "parent")
        child_agent = _span_where(spans, "agent", "hermes.session.is_subagent", True)

        # Delegation span nests under the parent's in-flight API span.
        assert _parent_span_id(delegation) == parent_api.context.span_id
        # The child's own root span nests under the delegation span.
        assert _parent_span_id(child_agent) == delegation.context.span_id
        # ...which traces back to the parent agent root.
        assert _parent_span_id(parent_agent) is None

        d_attrs = dict(delegation.attributes)
        assert d_attrs["hermes.subagent.role"] == "researcher"
        assert d_attrs["hermes.subagent.child_session_id"] == "child-1"
        assert d_attrs["hermes.subagent.parent_session_id"] == "parent"
        assert d_attrs["gen_ai.operation.name"] == "invoke_agent"
        assert d_attrs["gen_ai.agent.name"] == "researcher"
        assert "research X thoroughly" in d_attrs["hermes.subagent.goal"]
        assert d_attrs["openinference.span.kind"] == "AGENT"
        # subagent_stop annotations
        assert d_attrs["hermes.subagent.status"] == "completed"
        assert d_attrs["hermes.subagent.duration_ms"] == 4200.0
        assert "found the answer" in d_attrs["hermes.subagent.summary"]

        c_attrs = dict(child_agent.attributes)
        assert c_attrs["hermes.subagent.parent_session_id"] == "parent"
        assert c_attrs["hermes.subagent.role"] == "researcher"

    def test_registry_cleaned_up_after_stop(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="child-1",
            child_role="researcher",
            child_goal="g",
        )
        assert "child-1" in plugin._subagent_registry
        on_subagent_stop(
            parent_session_id="parent", child_session_id="child-1", child_status="completed"
        )
        assert "child-1" not in plugin._subagent_registry


class TestFailedDelegation:
    def test_error_status_marks_span_error(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="child-err",
            child_role="builder",
            child_goal="build",
        )
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="child-err",
            child_role="builder",
            child_summary="exploded",
            child_status="failed",
            duration_ms=10,
        )
        _close_parent_turn()

        delegation = _span_by_name(exporter.get_finished_spans(), "subagent.builder")
        assert delegation.status.status_code == StatusCode.ERROR
        assert dict(delegation.attributes)["hermes.subagent.status"] == "failed"


class TestLinkFallbackCrossProcess:
    """When the live delegation span object isn't available (cross-process),
    the child root attaches a span LINK to the stashed SpanContext instead of
    nesting. We simulate cross-process by dropping the live span from the
    registry record but keeping its context.
    """

    def test_child_root_links_to_delegation_context(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="child-x",
            child_role="researcher",
            child_goal="g",
        )
        # The live delegation span and its context.
        delegation_span = plugin.spans.get_span("subagent:child-x")
        delegation_ctx = delegation_span.get_span_context()

        # Simulate cross-process: live span object is gone, only context remains.
        plugin._subagent_registry["child-x"]["span"] = None

        on_session_start(session_id="child-x", model="gpt-4", platform="cli")
        on_session_end(
            session_id="child-x", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        child_agent = _span_where(
            exporter.get_finished_spans(), "agent", "hermes.session.is_subagent", True
        )
        link_ctxs = [link.context.span_id for link in child_agent.links]
        assert delegation_ctx.span_id in link_ctxs


class TestNestedDelegation:
    def test_depth_two_single_trace(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        _open_parent_turn()
        # parent -> child
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="child-1",
            child_role="orchestrator",
            child_goal="coordinate",
        )
        on_session_start(session_id="child-1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="child-1",
            user_message="coordinate",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_pre_api_request(
            task_id="capi",
            session_id="child-1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=1,
            approx_input_tokens=10,
            request_char_count=20,
            max_tokens=100,
        )
        # child -> grandchild
        on_subagent_start(
            parent_session_id="child-1",
            child_session_id="grandchild-1",
            child_role="leaf",
            child_goal="do leaf work",
        )
        _run_child("grandchild-1")
        on_subagent_stop(
            parent_session_id="child-1",
            child_session_id="grandchild-1",
            child_status="completed",
            duration_ms=5,
        )
        on_post_api_request(
            task_id="capi",
            session_id="child-1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.1,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4",
            usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            assistant_content_chars=1,
            assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="child-1",
            user_message="coordinate",
            assistant_response="done",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id="child-1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="child-1",
            child_status="completed",
            duration_ms=20,
        )
        _close_parent_turn()

        spans = exporter.get_finished_spans()
        assert len({s.context.trace_id for s in spans}) == 1, "nested delegation must be one trace"

        child_delegation = _span_by_name(spans, "subagent.orchestrator")
        grandchild_delegation = _span_by_name(spans, "subagent.leaf")
        grandchild_agent = _span_where(
            spans, "agent", "hermes.subagent.parent_session_id", "child-1"
        )
        # grandchild delegation nests under the child's API span; grandchild
        # root nests under the grandchild delegation span.
        assert _parent_span_id(grandchild_agent) == grandchild_delegation.context.span_id
        assert child_delegation.context.span_id != grandchild_delegation.context.span_id


class TestConcurrentDelegations:
    def test_two_children_distinct_spans_same_parent(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c-a",
            child_role="alpha",
            child_goal="A",
        )
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c-b",
            child_role="beta",
            child_goal="B",
        )
        on_subagent_stop(
            parent_session_id="parent", child_session_id="c-a", child_status="completed"
        )
        on_subagent_stop(
            parent_session_id="parent", child_session_id="c-b", child_status="completed"
        )
        _close_parent_turn()

        spans = exporter.get_finished_spans()
        alpha = _span_by_name(spans, "subagent.alpha")
        beta = _span_by_name(spans, "subagent.beta")
        api = _span_by_name(spans, "api.gpt-4")
        assert alpha.context.span_id != beta.context.span_id
        assert _parent_span_id(alpha) == api.context.span_id
        assert _parent_span_id(beta) == api.context.span_id


class TestSubagentMetrics:
    def test_count_and_duration_recorded(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_session_start(session_id="parent", model="gpt-4", platform="cli")
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_goal="g",
        )
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_status="completed",
            duration_ms=3000,
        )

        data = metric_reader.get_metrics_data()
        metrics = {
            m.name: m
            for rm in data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "hermes.subagent.count" in metrics
        count_dp = list(metrics["hermes.subagent.count"].data.data_points)[0]
        assert count_dp.value == 1
        assert count_dp.attributes["role"] == "researcher"
        assert count_dp.attributes["status"] == "ok"

        assert "hermes.subagent.duration" in metrics
        dur_dp = list(metrics["hermes.subagent.duration"].data.data_points)[0]
        assert dur_dp.sum == 3000
        assert dur_dp.attributes["role"] == "researcher"


class TestSubagentFanOut:
    def test_delegation_span_reaches_all_backends(self, two_exporter_pipeline):
        exporter_a, exporter_b, _ = two_exporter_pipeline
        on_session_start(session_id="parent", model="gpt-4", platform="cli")
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_goal="g",
        )
        on_subagent_stop(
            parent_session_id="parent", child_session_id="c1", child_status="completed"
        )

        for exp in (exporter_a, exporter_b):
            names = [s.name for s in exp.get_finished_spans()]
            assert "subagent.researcher" in names


class TestOrphanedDelegationSwept:
    """A delegation whose child never returns must be finalized by the orphan
    sweep when the parent session times out (not left open forever)."""

    def test_unfinished_delegation_is_swept(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_start(session_id="parent", model="gpt-4", platform="cli")
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_goal="g",
        )
        # Back-date the parent turn so the sweep considers it expired.
        plugin.register_turn("parent", started_at=0.0)
        swept = plugin.sweep_expired_turns()
        assert "parent" in swept

        names = [s.name for s in exporter.get_finished_spans()]
        assert "subagent.researcher" in names
