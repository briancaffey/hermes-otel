"""#474: ``HERMES_OTEL_TRACEPARENT`` parents every turn root under an outer span.

The kanban hand-off (#388) reads plain ``TRACEPARENT`` only inside a worker.
Any other launcher (a CI job, a script, another agent) exports the
plugin-prefixed variable instead, and the turn joins its trace.
"""

from __future__ import annotations

import logging
import os

import pytest
from _helpers import parent_span_id

from hermes_otel.hooks import on_session_end, on_session_start
from hermes_otel.hooks.kanban import on_kanban_task_claimed, on_kanban_worker_spawned
from hermes_otel.hooks.propagation import REMOTE_PARENT_ENV, REMOTE_STATE_ENV, remote_parent
from hermes_otel.plugin_config import HermesOtelConfig

TRACE = "ab" * 16
SPAN = "cd" * 8
OUTER = f"00-{TRACE}-{SPAN}-01"


def _turn(session_id: str) -> None:
    on_session_start(session_id=session_id, model="gpt-4", platform="cli")
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model="gpt-4", platform="cli"
    )


def _root(exporter, session_id: str):
    return next(
        s
        for s in exporter.get_finished_spans()
        if s.name == "agent" and s.attributes.get("session.id") == session_id
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (REMOTE_PARENT_ENV, REMOTE_STATE_ENV, "TRACEPARENT", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(var, raising=False)


class TestJoin:
    def test_root_joins_the_outer_trace(self, inmemory_otel_setup, monkeypatch):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        _turn("s1")
        root = _root(exporter, "s1")
        assert root.context.trace_id == int(TRACE, 16)
        assert parent_span_id(root) == int(SPAN, 16)
        assert root.parent.is_remote is True
        assert root.attributes["hermes.trace.remote_parent"] is True

    def test_every_turn_of_the_process_joins(self, inmemory_otel_setup, monkeypatch):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        _turn("a")
        _turn("b")
        assert {s.context.trace_id for s in exporter.get_finished_spans()} == {int(TRACE, 16)}

    def test_tracestate_is_carried(self, inmemory_otel_setup, monkeypatch):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        monkeypatch.setenv(REMOTE_STATE_ENV, "vendor=abc,other=1")
        _turn("s1")
        root = _root(exporter, "s1")
        assert root.context.trace_state.get("vendor") == "abc"
        assert root.context.trace_state.get("other") == "1"

    def test_without_the_variable_nothing_changes(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _turn("s1")
        root = _root(exporter, "s1")
        assert parent_span_id(root) is None
        assert "hermes.trace.remote_parent" not in root.attributes

    def test_plain_traceparent_outside_a_worker_is_still_ignored(
        self, inmemory_otel_setup, monkeypatch
    ):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv("TRACEPARENT", OUTER)
        _turn("s1")
        assert parent_span_id(_root(exporter, "s1")) is None


class TestEdges:
    @pytest.mark.parametrize(
        "value",
        ["garbage", "01-" + TRACE + "-" + SPAN + "-01", "00-" + "0" * 32 + "-" + SPAN + "-01"],
    )
    def test_malformed_value_warns_once_and_starts_its_own_trace(
        self, inmemory_otel_setup, monkeypatch, caplog, value
    ):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv(REMOTE_PARENT_ENV, value)
        import hermes_otel.hooks.propagation as prop

        monkeypatch.setattr(prop, "_warned_invalid", None)
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            _turn("s1")
            _turn("s2")
        root = _root(exporter, "s1")
        assert parent_span_id(root) is None
        assert "hermes.trace.remote_parent" not in root.attributes
        warnings = [r for r in caplog.records if REMOTE_PARENT_ENV in r.getMessage()]
        assert len(warnings) == 1
        # Never echo the value itself: it may be a mangled secret.
        assert value not in warnings[0].getMessage()

    def test_config_off_ignores_the_variable(self, inmemory_otel_setup, monkeypatch):
        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(remote_parent=False)
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        _turn("s1")
        root = _root(exporter, "s1")
        assert parent_span_id(root) is None
        assert "hermes.trace.remote_parent" not in root.attributes

    def test_kanban_hand_off_wins_inside_a_worker(self, inmemory_otel_setup, monkeypatch):
        """A worker that also inherited HERMES_OTEL_TRACEPARENT from the
        dispatcher's launcher still joins the claim span (the nearer parent)."""
        exporter, _ = inmemory_otel_setup
        card = dict(task_id="t1", board="main", assignee="w", run_id=1)
        on_kanban_task_claimed(**card)
        handed = os.environ["TRACEPARENT"]
        on_kanban_worker_spawned(**card, worker_pid=7)
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t1")
        monkeypatch.setenv("TRACEPARENT", handed)
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        _turn("w1")
        root = _root(exporter, "w1")
        claim = next(s for s in exporter.get_finished_spans() if s.name == "kanban.claim")
        assert parent_span_id(root) == claim.context.span_id
        assert "hermes.trace.remote_parent" not in root.attributes

    def test_helper_returns_a_remote_sampled_context(self, monkeypatch):
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER)
        ctx = remote_parent().get_span_context()
        assert ctx.is_remote and ctx.trace_flags.sampled
        monkeypatch.setenv(REMOTE_PARENT_ENV, OUTER[:-2] + "00")
        assert not remote_parent().get_span_context().trace_flags.sampled
        monkeypatch.setenv(REMOTE_PARENT_ENV, "   ")
        assert remote_parent() is None
