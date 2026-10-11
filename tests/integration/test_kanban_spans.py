"""Kanban lifecycle spans (#31) through the real pipeline.

Dispatcher-side transitions are roots of their own traces; worker-side
transitions land in the one in-flight turn's trace, next to the
``kanban_complete`` / ``kanban_block`` tool span, and stamp that turn's root
with the card.
"""

from __future__ import annotations

from _helpers import metric_points as _metric_points
from _helpers import one as _one
from _helpers import parent_span_id as _parent_span_id

from hermes_otel.hooks import (
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_worker_exited,
    on_kanban_worker_spawned,
    on_kanban_worker_stale_claim,
    on_post_tool_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)

SID = "20260626_010101_abc123"
CARD = dict(
    task_id="t_abc123", board="default", assignee="minimal", run_id=4, profile_name="default"
)


def _worker_turn(exporter, finish, tool):
    """A worker's turn that ends the card from inside the kanban tool."""
    on_session_start(session_id=SID, model="gpt-4", platform="cli")
    on_pre_tool_call(tool_name=tool, args={}, task_id="t1", tool_call_id="tc1", session_id=SID)
    result = finish()
    on_post_tool_call(
        tool_name=tool,
        args={},
        result='{"ok": true}',
        task_id="t1",
        tool_call_id="tc1",
        session_id=SID,
    )
    on_session_end(session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="cli")
    return exporter.get_finished_spans(), result


class TestDispatcherSide:
    def test_claim_and_spawn_are_roots_with_the_card_identity(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        assert on_kanban_task_claimed(**CARD) is None
        assert on_kanban_worker_spawned(**CARD, worker_pid=4242, workspace_path="/tmp/ws") is None
        spans = exporter.get_finished_spans()
        claim, spawn = _one(spans, "kanban.claim"), _one(spans, "kanban.spawn")
        for s in (claim, spawn):
            assert _parent_span_id(s) is None
            assert s.attributes["hermes.kanban.task_id"] == "t_abc123"
            assert s.attributes["hermes.kanban.board"] == "default"
            assert s.attributes["hermes.kanban.assignee"] == "minimal"
            assert s.attributes["hermes.kanban.run_id"] == "4"
            assert s.attributes["hermes.span_kind"] == "kanban"
        assert claim.context.trace_id != spawn.context.trace_id
        assert claim.attributes["hermes.kanban.event"] == "claimed"
        assert spawn.attributes["hermes.kanban.worker_pid"] == 4242
        assert not any("workspace" in k for k in spawn.attributes)

    def test_dispatcher_spans_never_nest_under_an_unrelated_turn(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="telegram")
        on_kanban_task_claimed(**CARD)
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
        )
        spans = exporter.get_finished_spans()
        claim, root = _one(spans, "kanban.claim"), _one(spans, "agent")
        assert _parent_span_id(claim) is None
        assert claim.context.trace_id != root.context.trace_id

    def test_a_killed_worker_is_an_error_and_a_clean_exit_is_not(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_kanban_worker_exited(
            **CARD,
            worker_pid=1,
            exit_kind="signaled",
            exit_code=-15,
            outcome="timed_out",
            retry_status="retry",
        )
        on_kanban_worker_exited(
            **CARD, worker_pid=2, exit_kind="clean_exit", exit_code=0, outcome="completed"
        )
        spans = exporter.get_finished_spans()
        killed = next(s for s in spans if s.attributes.get("hermes.kanban.exit_kind") == "signaled")
        clean = next(
            s for s in spans if s.attributes.get("hermes.kanban.exit_kind") == "clean_exit"
        )
        assert killed.name == "kanban.worker_exit"
        assert killed.status.status_code.name == "ERROR"
        assert killed.attributes["hermes.kanban.exit_code"] == -15
        assert killed.attributes["hermes.kanban.outcome"] == "timed_out"
        assert killed.attributes["hermes.kanban.retry_status"] == "retry"
        assert clean.status.status_code.name != "ERROR"

    def test_stale_claim(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_kanban_worker_stale_claim(
            **CARD, worker_pid=7, heartbeat_stale=True, retry_status="retry"
        )
        s = _one(exporter.get_finished_spans(), "kanban.stale_claim")
        assert s.attributes["hermes.kanban.heartbeat_stale"] is True
        assert s.status.status_code.name != "ERROR"


class TestWorkerSide:
    def test_complete_nests_under_the_tool_span_and_stamps_the_root(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        spans, ret = _worker_turn(
            exporter,
            lambda: on_kanban_task_completed(**CARD, summary="schema designed"),
            "kanban_complete",
        )
        assert ret is None
        done, tool, root = (
            _one(spans, "kanban.complete"),
            _one(spans, "tool.kanban_complete"),
            _one(spans, "agent"),
        )
        # Tool spans are never parents; the transition sits next to the tool span
        # under the worker's current parent (its llm span, or the turn root here).
        assert _parent_span_id(done) == root.context.span_id
        assert tool.context.trace_id == done.context.trace_id
        assert done.context.trace_id == root.context.trace_id
        assert done.attributes["hermes.kanban.summary"] == "schema designed"
        assert done.attributes["hermes.kanban.outcome"] == "completed"
        assert root.attributes["hermes.kanban.task_id"] == "t_abc123"
        assert root.attributes["hermes.kanban.run_id"] == "4"
        assert root.attributes["hermes.kanban.outcome"] == "completed"

    def test_block_is_ok_with_the_outcome_and_reason(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        spans, _ = _worker_turn(
            exporter, lambda: on_kanban_task_blocked(**CARD, reason="needs a human"), "kanban_block"
        )
        blocked, root = _one(spans, "kanban.block"), _one(spans, "agent")
        assert blocked.status.status_code.name != "ERROR"
        assert blocked.attributes["hermes.kanban.reason"] == "needs a human"
        assert blocked.attributes["hermes.kanban.outcome"] == "blocked"
        assert root.attributes["hermes.kanban.outcome"] == "blocked"

    def test_a_human_completing_from_the_cli_gets_a_root_span(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_kanban_task_completed(**CARD, summary="closed by hand")
        s = _one(exporter.get_finished_spans(), "kanban.complete")
        assert _parent_span_id(s) is None

    def test_summary_follows_the_preview_policy(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig

        plugin.config = HermesOtelConfig(capture_previews=False)
        on_kanban_task_completed(**CARD, summary="secret text")
        s = _one(exporter.get_finished_spans(), "kanban.complete")
        assert "hermes.kanban.summary" not in s.attributes
        assert s.attributes["hermes.kanban.outcome"] == "completed"


class TestMetricsAndFanOut:
    def test_transition_counter_by_outcome_and_board(self, inmemory_otel_with_metrics):
        _, reader, _ = inmemory_otel_with_metrics
        on_kanban_task_claimed(**CARD)
        on_kanban_task_completed(**CARD, summary="x")
        on_kanban_task_blocked(**{**CARD, "board": "ops"}, reason="y")
        metric = None
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "hermes.kanban.task.count":
                        metric = m
        assert metric is not None, "hermes.kanban.task.count not recorded"
        points = {
            (p.attributes["outcome"], p.attributes["board"]): p.value
            for p in _metric_points(metric)
        }
        assert points == {
            ("claimed", "default"): 1,
            ("completed", "default"): 1,
            ("blocked", "ops"): 1,
        }

    def test_fans_out_to_every_exporter(self, two_exporter_pipeline):
        exporter_a, exporter_b, _ = two_exporter_pipeline
        on_kanban_task_claimed(**CARD)
        for exporter in (exporter_a, exporter_b):
            _one(exporter.get_finished_spans(), "kanban.claim")
