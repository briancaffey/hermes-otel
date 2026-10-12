"""Kanban hook callbacks (#31): observer-only, fail-open, additive payloads."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes_otel.hooks import (
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_worker_exited,
    on_kanban_worker_spawned,
    on_kanban_worker_stale_claim,
)
from hermes_otel.hooks.kanban import kanban_identity

ALL = (
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_task_blocked,
    on_kanban_worker_spawned,
    on_kanban_worker_exited,
    on_kanban_worker_stale_claim,
)


class TestObserverContract:
    @pytest.mark.parametrize("hook", ALL)
    def test_returns_none_and_accepts_any_payload(self, mock_tracer, hook):
        assert hook() is None
        assert hook(task_id=None, board=None, run_id=None) is None
        assert hook(task_id="t", board="b", assignee="a", run_id=1, future_field={"x": 1}) is None
        assert hook(task_id=object(), run_id=object(), worker_pid="pid", exit_code="1") is None

    @pytest.mark.parametrize("hook", ALL)
    def test_disabled_tracer_touches_nothing(self, mock_tracer, hook):
        mock_tracer.is_enabled = False
        assert hook(task_id="t", board="b") is None
        mock_tracer.start_span.assert_not_called()
        mock_tracer.record_metric.assert_not_called()

    @pytest.mark.parametrize("hook", ALL)
    def test_a_raising_tracer_fails_open(self, hook):
        with patch("hermes_otel.tracer.get_tracer", side_effect=RuntimeError("boom")):
            assert hook(task_id="t") is None

    def test_every_transition_opens_closes_and_counts(self, mock_tracer):
        on_kanban_task_claimed(task_id="t", board="b", assignee="a", run_id=2)
        assert mock_tracer.start_span.call_count == 1
        assert mock_tracer.end_span.call_count == 1
        kwargs = mock_tracer.start_span.call_args.kwargs
        assert kwargs["name"] == "kanban.claim"
        assert kwargs["session_id"] is None
        assert kwargs["parent"] is not None  # the invalid span: a root of its own
        mock_tracer.record_metric.assert_called_once_with(
            "kanban_task_count", 1, {"outcome": "claimed", "board": "b"}
        )

    def test_unknown_board_label(self, mock_tracer):
        on_kanban_task_claimed(task_id="t")
        assert mock_tracer.record_metric.call_args.args[2] == {
            "outcome": "claimed",
            "board": "unknown",
        }


class TestIdentity:
    def test_clips_and_stringifies(self):
        attrs = kanban_identity("t" * 500, "board", None, 7, "claimed")
        assert len(attrs["hermes.kanban.task_id"]) <= 203
        assert attrs["hermes.kanban.run_id"] == "7"
        assert "hermes.kanban.assignee" not in attrs
        assert attrs["hermes.kanban.event"] == "claimed"

    def test_zero_run_id_is_kept_and_blank_dropped(self):
        assert kanban_identity("t", None, None, 0, "x")["hermes.kanban.run_id"] == "0"
        assert "hermes.kanban.run_id" not in kanban_identity("t", None, None, "", "x")


class TestWorkerSideParenting:
    def test_uses_the_single_active_sessions_current_parent(self, mock_tracer):
        root, parent = MagicMock(), MagicMock()
        mock_tracer.spans.single_active_session = lambda: ("s1", root)
        mock_tracer.spans.get_current_parent = lambda sid=None: parent if sid == "s1" else None
        on_kanban_task_completed(task_id="t", board="b", run_id=1, summary="done")
        kwargs = mock_tracer.start_span.call_args.kwargs
        assert kwargs["session_id"] == "s1"
        assert kwargs["parent"] is parent
        root.set_attribute.assert_any_call("hermes.kanban.outcome", "completed")
        root.set_attribute.assert_any_call("hermes.kanban.task_id", "t")

    def test_two_active_sessions_stay_unattributed(self, mock_tracer):
        mock_tracer.spans.single_active_session = lambda: None
        on_kanban_task_blocked(task_id="t", reason="r")
        kwargs = mock_tracer.start_span.call_args.kwargs
        assert kwargs["session_id"] is None
        assert kwargs["parent"] is not None

    def test_blocked_is_ok_and_warns_in_the_event(self, mock_tracer):
        on_kanban_task_blocked(task_id="t", board="b", reason="needs input")
        assert mock_tracer.end_span.call_args.kwargs["status"] == "ok"
        attrs = mock_tracer.start_span.call_args.kwargs["attributes"]
        assert attrs["hermes.kanban.reason"] == "needs input"
        assert attrs["hermes.kanban.outcome"] == "blocked"


class TestExitStatus:
    @pytest.mark.parametrize(
        "kind,status",
        [
            ("signaled", "error"),
            ("nonzero_exit", "error"),
            ("clean_exit", "ok"),
            ("rate_limited", "ok"),
            (None, "ok"),
        ],
    )
    def test_exit_kind_maps_to_status(self, mock_tracer, kind, status):
        on_kanban_worker_exited(task_id="t", exit_kind=kind)
        assert mock_tracer.end_span.call_args.kwargs["status"] == status
        attrs = mock_tracer.start_span.call_args.kwargs["attributes"]
        assert attrs["hermes.kanban.exit_kind"] == (kind or "unknown")
