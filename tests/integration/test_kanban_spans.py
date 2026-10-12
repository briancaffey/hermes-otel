"""Kanban lifecycle spans (#31) through the real pipeline.

The dispatcher's claim roots each run's trace and the worker's turn joins it
through TRACEPARENT (#388); worker-side
transitions land in the one in-flight turn's trace, next to the
``kanban_complete`` / ``kanban_block`` tool span, and stamp that turn's root
with the card.
"""

from __future__ import annotations

import pytest
from _helpers import get_metric
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
    def test_claim_roots_the_run_and_spawn_nests_under_it(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        assert on_kanban_task_claimed(**CARD) is None
        assert on_kanban_worker_spawned(**CARD, worker_pid=4242, workspace_path="/tmp/ws") is None
        spans = exporter.get_finished_spans()
        claim, spawn = _one(spans, "kanban.claim"), _one(spans, "kanban.spawn")
        assert _parent_span_id(claim) is None
        assert _parent_span_id(spawn) == claim.context.span_id  # one trace per run (#388)
        for s in (claim, spawn):
            assert s.attributes["hermes.kanban.task_id"] == "t_abc123"
            assert s.attributes["hermes.kanban.board"] == "default"
            assert s.attributes["hermes.kanban.assignee"] == "minimal"
            assert s.attributes["hermes.kanban.run_id"] == "4"
            assert s.attributes["hermes.span_kind"] == "kanban"
        assert claim.context.trace_id == spawn.context.trace_id
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


class TestTracePerRun:
    """One kanban task run is one trace: dispatcher claim -> worker turn -> exit (#388)."""

    def test_claim_hands_its_context_to_the_worker_and_spawn_takes_it_back(
        self, inmemory_otel_setup, monkeypatch
    ):
        import os

        from hermes_otel.helpers import parse_traceparent

        exporter, _ = inmemory_otel_setup
        monkeypatch.delenv("TRACEPARENT", raising=False)
        on_kanban_task_claimed(**CARD)
        claim = _one(exporter.get_finished_spans(), "kanban.claim")
        trace_id, span_id, _ = parse_traceparent(os.environ["TRACEPARENT"])
        assert (trace_id, span_id) == (claim.context.trace_id, claim.context.span_id)
        on_kanban_worker_spawned(**CARD, worker_pid=7)
        assert "TRACEPARENT" not in os.environ

    def test_a_users_own_traceparent_is_left_alone(self, inmemory_otel_setup, monkeypatch):
        import os

        mine = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
        monkeypatch.setenv("TRACEPARENT", mine)
        on_kanban_worker_spawned(**CARD, worker_pid=7)  # no claim of ours to clean up
        assert os.environ["TRACEPARENT"] == mine

    def test_a_claim_that_never_spawned_does_not_leak_into_the_next(
        self, inmemory_otel_setup, monkeypatch
    ):
        import os

        exporter, _ = inmemory_otel_setup
        monkeypatch.delenv("TRACEPARENT", raising=False)
        on_kanban_task_claimed(**CARD)
        first = os.environ["TRACEPARENT"]
        on_kanban_task_claimed(**{**CARD, "task_id": "t_next", "run_id": 5})
        assert os.environ["TRACEPARENT"] != first
        claims = [s for s in exporter.get_finished_spans() if s.name == "kanban.claim"]
        assert len({c.context.trace_id for c in claims}) == 2

    def test_worker_root_joins_the_claim_and_exit_closes_the_run(
        self, inmemory_otel_setup, monkeypatch
    ):
        import os

        exporter, _ = inmemory_otel_setup
        monkeypatch.delenv("TRACEPARENT", raising=False)
        on_kanban_task_claimed(**CARD)
        handed = os.environ["TRACEPARENT"]
        on_kanban_worker_spawned(**CARD, worker_pid=7)

        # The worker process: its environment carries the card and the context.
        monkeypatch.setenv("HERMES_KANBAN_TASK", CARD["task_id"])
        monkeypatch.setenv("TRACEPARENT", handed)
        on_session_start(session_id="w1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="w1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        on_kanban_worker_exited(**CARD, worker_pid=7, exit_kind="clean_exit", outcome="completed")

        spans = exporter.get_finished_spans()
        claim = _one(spans, "kanban.claim")
        root = _one(spans, "agent")
        exit_span = _one(spans, "kanban.worker_exit")
        assert root.context.trace_id == claim.context.trace_id
        assert _parent_span_id(root) == claim.context.span_id
        assert _parent_span_id(exit_span) == claim.context.span_id
        # the run is forgotten once it exits: a later transition starts afresh
        on_kanban_worker_exited(**CARD, worker_pid=7, exit_kind="clean_exit")
        later = [s for s in exporter.get_finished_spans() if s.name == "kanban.worker_exit"][-1]
        assert _parent_span_id(later) is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "garbage",
            "01-" + "ab" * 16 + "-" + "cd" * 8 + "-01",
            "00-" + "0" * 32 + "-" + "cd" * 8 + "-01",
        ],
    )
    def test_a_malformed_or_foreign_context_falls_back_to_a_new_trace(
        self, inmemory_otel_setup, monkeypatch, value
    ):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_x")
        monkeypatch.setenv("TRACEPARENT", value)
        on_session_start(session_id="w2", model="gpt-4", platform="cli")
        on_session_end(
            session_id="w2", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert _parent_span_id(_one(exporter.get_finished_spans(), "agent")) is None

    def test_traceparent_outside_a_worker_is_ignored(self, inmemory_otel_setup, monkeypatch):
        exporter, _ = inmemory_otel_setup
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.setenv("TRACEPARENT", "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01")
        on_session_start(session_id="u1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="u1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert _parent_span_id(_one(exporter.get_finished_spans(), "agent")) is None


class TestBoardHealth:
    """``on_kanban_dispatch_tick`` / ``on_kanban_task_updated`` as counters (#397)."""

    class _Result:
        spawned = [("t1", "a", "/ws/1"), ("t2", "b", "/ws/2")]
        reclaimed = 1
        promoted = 0
        crashed = ["t3"]
        skipped_locked = False
        memory_pressure = "rss above cap"
        auto_blocked = []

    def _points(self, reader, name):
        if reader.get_metrics_data() is None:  # nothing recorded at all
            return {}
        return {
            tuple(sorted(dict(p.attributes).items())): p.value
            for p in _metric_points(get_metric(reader, name))
        }

    def test_a_tick_counts_its_outcome_and_each_non_zero_kind(self, inmemory_otel_with_metrics):
        from hermes_otel.hooks import on_kanban_dispatch_tick

        exporter, reader, _ = inmemory_otel_with_metrics
        on_kanban_dispatch_tick(
            board="b1", profile_name="p", dry_run=False, outcome="ok", result=self._Result()
        )
        ticks = self._points(reader, "hermes.kanban.dispatch.count")
        assert any(("outcome", "ok") in k and ("board", "b1") in k for k in ticks)
        kinds = {
            dict(k)["kind"]: v
            for k, v in self._points(reader, "hermes.kanban.dispatch.result").items()
        }
        assert kinds == {"spawned": 2, "reclaimed": 1, "crashed": 1, "memory_pressure": 1}
        assert not exporter.get_finished_spans()  # counts only, no span

    def test_a_dry_run_is_not_counted(self, inmemory_otel_with_metrics):
        from hermes_otel.hooks import on_kanban_dispatch_tick

        _, reader, _ = inmemory_otel_with_metrics
        on_kanban_dispatch_tick(board="b1", dry_run=True, outcome="ok", result=self._Result())
        assert not self._points(reader, "hermes.kanban.dispatch.count")

    def test_an_idle_tick_with_no_result(self, inmemory_otel_with_metrics):
        from hermes_otel.hooks import on_kanban_dispatch_tick

        _, reader, _ = inmemory_otel_with_metrics
        on_kanban_dispatch_tick(board=None, outcome="idle", result=None)
        ticks = self._points(reader, "hermes.kanban.dispatch.count")
        assert any(("outcome", "idle") in k and ("board", "default") in k for k in ticks)
        assert not self._points(reader, "hermes.kanban.dispatch.result")

    def test_task_updated_counts_field_names(self, inmemory_otel_with_metrics):
        from hermes_otel.hooks import on_kanban_task_updated

        _, reader, _ = inmemory_otel_with_metrics
        on_kanban_task_updated(
            task_id="t1", board="b1", changed_fields=["priority", "assignee", ""]
        )
        fields = {dict(k)["field"] for k in self._points(reader, "hermes.kanban.task.updated")}
        assert fields == {"priority", "assignee"}
