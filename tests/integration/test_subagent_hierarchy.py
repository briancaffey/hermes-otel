"""Integration tests for sub-agent (delegation) span linking.

Issue #27: a delegated child agent must be modeled as a span nested under the
parent's turn, and the child's own root span must rejoin the parent trace so
the whole multi-agent run is one connected tree (in-process delegation) — or,
when only a SpanContext is available (cross-process), attached via a span link.
"""

import time

import pytest
from _helpers import parent_span_id as _parent_span_id
from _helpers import span_by_name as _span_by_name
from _helpers import span_where as _span_where
from opentelemetry.trace import StatusCode

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
        assert "child-1" in plugin.spans._subagent_registry
        on_subagent_stop(
            parent_session_id="parent", child_session_id="child-1", child_status="completed"
        )
        assert "child-1" not in plugin.spans._subagent_registry


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
        plugin.spans._subagent_registry["child-x"]["span"] = None

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
            m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
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
    """A delegation whose child never returns is closed by its own TTL
    (``subagent_span_ttl_ms``), not by the parent's turn sweep (#392)."""

    def test_parent_turn_sweep_leaves_the_delegation_open(self, inmemory_otel_setup):
        from hermes_otel.plugin_config import HermesOtelConfig

        exporter, plugin = inmemory_otel_setup
        # Small TTL + a back-dated start (relative to perf_counter, whose epoch
        # is arbitrary) so the sweep is deterministic on any machine/CI runner.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        on_session_start(session_id="parent", model="gpt-4", platform="cli")
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_goal="g",
        )
        plugin.register_turn("parent", started_at=time.perf_counter() - 10.0)
        assert "parent" in plugin.sweep_expired_turns()

        names = [s.name for s in exporter.get_finished_spans()]
        assert "agent" in names and "subagent.researcher" not in names
        assert plugin.spans.has_span("subagent:c1")

    def test_unfinished_delegation_is_closed_by_its_own_ttl(self, inmemory_otel_setup):
        from hermes_otel.plugin_config import HermesOtelConfig

        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(subagent_span_ttl_ms=1_000)
        on_session_start(session_id="parent", model="gpt-4", platform="cli")
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="c1",
            child_role="researcher",
            child_goal="g",
        )
        record = plugin.spans.get_subagent("c1")
        plugin.spans.register_subagent("c1", record, started_at=time.perf_counter() - 10.0)
        assert plugin.sweep_expired_delegations() == ["c1"]

        span = _span_by_name(exporter.get_finished_spans(), "subagent.researcher")
        assert span.attributes["hermes.subagent.swept"] is True
        assert span.status.status_code != StatusCode.ERROR
        assert plugin.spans.get_subagent("c1") is None
        assert plugin.sweep_expired_delegations() == []


class TestDelegationLifecycle:
    """The delegation modes Hermes has grown since #27 (#392)."""

    def test_background_delegation_stops_in_a_later_turn(self, inmemory_otel_setup):
        """Top-level delegate_task returns at once; subagent_stop fires after the
        parent's turn N has ended, during turn N+1. The span stays open across
        the turn boundary and the turn N+1 sweep, then ends with its status."""
        from hermes_otel.plugin_config import HermesOtelConfig

        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="bg-1",
            child_role="leaf",
            child_goal="background work",
        )
        _close_parent_turn()  # turn N ends with the child still running
        turn_n_api = _span_by_name(exporter.get_finished_spans(), "api.gpt-4")

        _open_parent_turn()  # turn N+1, which then outlives the TTL
        plugin.register_turn("parent", started_at=time.perf_counter() - 10.0)
        plugin.sweep_expired_turns()
        _run_child("bg-1")
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="bg-1",
            child_role="leaf",
            child_summary="done later",
            child_status="completed",
            duration_ms=90_000,
        )

        delegation = _span_by_name(exporter.get_finished_spans(), "subagent.leaf")
        assert _parent_span_id(delegation) == turn_n_api.context.span_id
        assert delegation.context.trace_id == turn_n_api.context.trace_id
        attrs = dict(delegation.attributes)
        assert attrs["hermes.subagent.status"] == "completed"
        assert attrs["hermes.subagent.duration_ms"] == 90_000.0
        assert "hermes.subagent.swept" not in attrs
        assert plugin.spans.get_subagent("bg-1") is None
        assert plugin.sweep_expired_delegations() == []

    def test_batch_stops_in_a_different_order_than_starts(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _open_parent_turn()
        for i in (1, 2, 3):
            on_subagent_start(
                parent_session_id="parent",
                child_session_id=f"b-{i}",
                child_role=f"w{i}",
                child_goal="g",
            )
        for i, status in ((3, "completed"), (1, "failed"), (2, "completed")):
            on_subagent_stop(
                parent_session_id="parent",
                child_session_id=f"b-{i}",
                child_status=status,
                duration_ms=10,
            )
        _close_parent_turn()

        spans = exporter.get_finished_spans()
        status = {
            s.name: s.attributes["hermes.subagent.status"]
            for s in spans
            if s.name.startswith("subagent.")
        }
        assert status == {
            "subagent.w1": "failed",
            "subagent.w2": "completed",
            "subagent.w3": "completed",
        }
        assert _span_by_name(spans, "subagent.w1").status.status_code == StatusCode.ERROR
        assert not plugin.spans._subagent_registry

    def test_stop_after_a_restart_is_harmless(self, inmemory_otel_with_metrics):
        """A child whose parent process restarted arrives as ``unknown`` with no
        span or record in this process: count it, open nothing, keep nothing."""
        exporter, reader, plugin = inmemory_otel_with_metrics
        on_subagent_stop(
            parent_session_id="parent",
            child_session_id="ghost",
            child_role="leaf",
            child_status="unknown",
        )
        assert not [s for s in exporter.get_finished_spans() if s.name.startswith("subagent")]
        assert plugin.spans.get_subagent("ghost") is None
        assert plugin.sweep_expired_delegations() == []
        names = {
            m.name
            for rm in reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert any("subagent" in n for n in names)


class TestChildStatusMapping:
    """Every final status Hermes's delegate tool reports (#392)."""

    @pytest.mark.parametrize(
        "status, error",
        [
            ("completed", False),
            ("failed", True),
            ("error", True),
            ("timeout", True),
            ("stalled", True),  # the stall monitor gave up on a silent child
            ("interrupted", False),  # a user action, like an interrupted turn
            ("unknown", False),  # nothing known (a restart); never an error
        ],
    )
    def test_status(self, status, error):
        from hermes_otel.hooks.subagent import subagent_status_to_span_status

        assert subagent_status_to_span_status(status) == ("error" if error else "ok")


class TestPerAgentModels:
    """Which model each agent ran on, on the agent-level spans (#391)."""

    def test_child_on_its_own_model_is_not_inherited(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_parent_turn(model="big-model")
        on_subagent_start(
            parent_session_id="parent", child_session_id="c-m", child_role="leaf", child_goal="g"
        )
        _run_child("c-m", model="small-model")
        on_subagent_stop(
            parent_session_id="parent", child_session_id="c-m", child_status="completed"
        )
        _close_parent_turn(model="big-model")

        d = dict(_span_by_name(exporter.get_finished_spans(), "subagent.leaf").attributes)
        assert d["gen_ai.request.model"] == "small-model"
        assert d["hermes.subagent.model_inherited"] is False

    def test_child_on_the_parent_model_is_inherited(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_parent_turn(model="big-model")
        on_subagent_start(
            parent_session_id="parent", child_session_id="c-i", child_role="leaf", child_goal="g"
        )
        _run_child("c-i", model="big-model")
        on_subagent_stop(
            parent_session_id="parent", child_session_id="c-i", child_status="completed"
        )
        _close_parent_turn(model="big-model")

        d = dict(_span_by_name(exporter.get_finished_spans(), "subagent.leaf").attributes)
        assert d["hermes.subagent.model_inherited"] is True


class TestModelOverride:
    """``hermes.agent.model_override`` against the profile's ``model.default``."""

    @staticmethod
    def _fake_hermes_config(monkeypatch, cfg):
        import sys
        import types

        pkg = types.ModuleType("hermes_cli")
        mod = types.ModuleType("hermes_cli.config")
        mod.read_raw_config_readonly = lambda: cfg
        pkg.config = mod
        monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
        monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)

    @pytest.mark.parametrize(
        "cfg, model, expected",
        [
            ({"model": {"default": "big-model"}}, "big-model", False),
            ({"model": {"default": "big-model"}}, "small-model", True),
            ({"model": "big-model"}, "small-model", True),  # the shorthand spelling
            ({}, "small-model", None),  # no default configured: no claim
        ],
    )
    def test_root_says_whether_the_model_was_overridden(
        self, inmemory_otel_setup, monkeypatch, cfg, model, expected
    ):
        exporter, _ = inmemory_otel_setup
        self._fake_hermes_config(monkeypatch, cfg)
        _run_child("solo", model=model)  # any plain turn
        root = _span_where(exporter.get_finished_spans(), "agent", "session.id", "solo")
        assert root.attributes.get("hermes.agent.model_override") is expected

    def test_absent_outside_hermes(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_child("solo")
        root = _span_where(exporter.get_finished_spans(), "agent", "session.id", "solo")
        assert "hermes.agent.model_override" not in root.attributes


class TestAnnotateDelegationModel:
    """Edge cases of the rejoin annotation: nothing invented, never raises."""

    class _Span:
        def __init__(self, fail=False):
            self.attrs, self.fail = {}, fail

        def set_attribute(self, k, v):
            if self.fail:
                raise RuntimeError("ended")
            self.attrs[k] = v

    def test_no_child_model_sets_nothing(self):
        from hermes_otel.hooks.session import _annotate_delegation_model

        span = self._Span()
        _annotate_delegation_model(span, "", "big")
        assert span.attrs == {}

    def test_unknown_parent_model_sets_only_the_model(self):
        from hermes_otel.hooks.session import _annotate_delegation_model

        span = self._Span()
        _annotate_delegation_model(span, "small", "")
        assert span.attrs == {"gen_ai.request.model": "small"}

    def test_a_span_that_refuses_is_ignored(self):
        from hermes_otel.hooks.session import _annotate_delegation_model

        _annotate_delegation_model(self._Span(fail=True), "small", "big")


def _child_turn(child, *, model="gpt-4", prompt=100, completion=50):
    """A delegated child's turn with one API call of the given usage."""
    on_session_start(session_id=child, model=model, platform="subagent")
    on_pre_api_request(
        task_id=f"{child}-api",
        session_id=child,
        platform="subagent",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=prompt,
        request_char_count=10,
        max_tokens=0,
    )
    on_post_api_request(
        task_id=f"{child}-api",
        session_id=child,
        platform="subagent",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.1,
        finish_reason="stop",
        message_count=1,
        response_model=model,
        usage={"prompt_tokens": prompt, "completion_tokens": completion},
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )


def _end(child, model="gpt-4"):
    on_session_end(
        session_id=child, completed=True, interrupted=False, model=model, platform="subagent"
    )


class TestCostPerAgent:
    """What each delegated child cost, on its delegation span (#390)."""

    def test_two_concurrent_children_get_their_own_rollups(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_parent_turn()
        for cid, role in (("a", "small"), ("b", "big")):
            on_subagent_start(
                parent_session_id="parent", child_session_id=cid, child_role=role, child_goal="g"
            )
        _child_turn("a", model="m-small", prompt=10, completion=1)
        _child_turn("b", model="m-big", prompt=1000, completion=100)
        _end("b", "m-big")
        _end("a", "m-small")
        for cid in ("a", "b"):
            on_subagent_stop(
                parent_session_id="parent", child_session_id=cid, child_status="completed"
            )
        _close_parent_turn()

        spans = exporter.get_finished_spans()
        a = dict(_span_by_name(spans, "subagent.small").attributes)
        b = dict(_span_by_name(spans, "subagent.big").attributes)
        assert (
            a["hermes.subagent.usage.input_tokens"],
            a["hermes.subagent.usage.output_tokens"],
        ) == (10, 1)
        assert (
            b["hermes.subagent.usage.input_tokens"],
            b["hermes.subagent.usage.output_tokens"],
        ) == (1000, 100)
        assert a["hermes.subagent.api_call_count"] == b["hermes.subagent.api_call_count"] == 1
        # the parent root keeps only its own call (100/50 in _close_parent_turn)
        parent_root = _span_where(spans, "agent", "session.id", "parent")
        assert parent_root.attributes["gen_ai.usage.input_tokens"] == 100

    def test_nested_orchestrator_counts_each_grandchild_once(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _open_parent_turn()
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="orch",
            child_role="orchestrator",
            child_goal="g",
        )
        _child_turn("orch", prompt=20, completion=2)
        on_subagent_start(
            parent_session_id="orch", child_session_id="leaf", child_role="leaf", child_goal="g"
        )
        _child_turn("leaf", prompt=300, completion=30)
        _end("leaf")
        on_subagent_stop(
            parent_session_id="orch", child_session_id="leaf", child_status="completed"
        )
        _end("orch")
        on_subagent_stop(
            parent_session_id="parent", child_session_id="orch", child_status="completed"
        )
        _close_parent_turn()

        spans = exporter.get_finished_spans()
        leaf = dict(_span_by_name(spans, "subagent.leaf").attributes)
        orch = dict(_span_by_name(spans, "subagent.orchestrator").attributes)
        assert leaf["hermes.subagent.usage.input_tokens"] == 300
        assert orch["hermes.subagent.usage.input_tokens"] == 320  # its own 20 + the leaf's 300
        assert orch["hermes.subagent.api_call_count"] == 2
        assert plugin.sessions.pop_delegation("orch") is None
        assert plugin.sessions.pop_delegation("leaf") is None

    def test_cost_follows_the_root_rule(self):
        from hermes_otel.session_state import DelegationTotals

        t = DelegationTotals(cost_statuses=["estimated", "actual"], cost_usd=0.25)
        assert t.attributes()["hermes.subagent.cost.status"] == "estimated"
        assert t.attributes()["hermes.subagent.cost.usage"] == 0.25
        t.cost_statuses.append("unknown")
        assert t.attributes()["hermes.subagent.cost.status"] == "partial"
        assert "hermes.subagent.cost.usage" not in t.attributes()
        assert (
            DelegationTotals(cost_statuses=["included"]).attributes()["hermes.subagent.cost.status"]
            == "included"
        )
        assert (
            DelegationTotals(cost_statuses=["unknown"]).attributes()["hermes.subagent.cost.status"]
            == "unknown"
        )
        assert "hermes.subagent.cost.status" not in DelegationTotals().attributes()
