"""on_session_finalize / on_session_reset (#29): the session's true end.

Hermes fires on_session_end per turn and on_session_finalize once, at the
end of the session; on_session_reset announces the session that replaces it.
A session stays a set of per-turn traces grouped by session id; these hooks
close what is still open, record the session's size and length, drop the
per-session state, and chain the replacing session to the old one.
"""

from __future__ import annotations

import time

import pytest

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_session_end,
    on_session_finalize,
    on_session_reset,
    on_session_start,
)


def _turn(session_id: str, is_first_turn: bool, *, end: bool = True) -> None:
    if is_first_turn:
        on_session_start(session_id=session_id, model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id=session_id,
        user_message="hello",
        conversation_history=[],
        is_first_turn=is_first_turn,
        model="gpt-4",
        platform="cli",
    )
    on_pre_api_request(
        task_id=f"api-{session_id}",
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=10,
        request_char_count=40,
        max_tokens=0,
    )
    on_post_api_request(
        task_id=f"api-{session_id}",
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.1,
        finish_reason="stop",
        message_count=1,
        response_model="gpt-4",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )
    on_post_llm_call(
        session_id=session_id,
        user_message="hello",
        assistant_response="hi",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    if end:
        on_session_end(
            session_id=session_id, completed=True, interrupted=False, model="gpt-4", platform="cli"
        )


def _roots(exporter):
    return [s for s in exporter.get_finished_spans() if s.name in ("agent", "cron")]


class TestFinalize:
    def test_multi_turn_session_finalizes_once_with_its_size(self, inmemory_otel_with_metrics):
        exporter, reader, plugin = inmemory_otel_with_metrics
        _turn("s1", True)
        _turn("s1", False)
        _turn("s1", False)
        on_session_finalize(session_id="s1", platform="cli", reason="session_boundary")

        roots = _roots(exporter)
        assert [r.attributes["hermes.turn.number"] for r in roots] == [1, 2, 3]
        # No extra root was opened or closed by finalize.
        assert len(roots) == 3

        points = {}
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    for dp in m.data.data_points:
                        points.setdefault(m.name, []).append(dp)
        turns = points["hermes.session.turns"][0]
        assert turns.sum == 3 and turns.count == 1
        assert dict(turns.attributes) == {"platform": "cli", "reason": "session_boundary"}
        duration = points["hermes.session.duration"][0]
        assert duration.count == 1 and 0 <= duration.sum < 60
        assert dict(duration.attributes)["reason"] == "session_boundary"

    def test_state_is_dropped_so_a_returning_id_starts_at_turn_one(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _turn("s1", True)
        _turn("s1", False)
        assert plugin.sessions.turn_number("s1") == 2
        on_session_finalize(session_id="s1", platform="cli", reason="shutdown")
        assert plugin.sessions.turn_number("s1") == 0
        assert plugin.sessions.peek("s1") is None
        assert plugin.sessions.last_finalized == "s1"
        # A continuation turn without on_session_start starts fresh at 1.
        _turn("s1", False)
        assert _roots(exporter)[-1].attributes["hermes.turn.number"] == 1

    def test_finalize_closes_a_turn_still_in_flight(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _turn("s1", True, end=False)  # no on_session_end: the root is still open
        assert plugin.spans.has_span("session:s1")
        on_session_finalize(session_id="s1", platform="cli", reason="expired")
        assert not plugin.spans.has_span("session:s1")
        root = _roots(exporter)[-1]
        assert root.attributes["hermes.session.finalize_reason"] == "expired"
        assert root.attributes["hermes.turn.final_status"] == "finalized"
        assert root.status.is_ok

    def test_finalize_of_an_unknown_session_is_quiet(self, inmemory_otel_with_metrics):
        exporter, reader, plugin = inmemory_otel_with_metrics
        on_session_finalize(session_id="never-seen", platform="telegram", reason="expired")
        assert not exporter.get_finished_spans()
        data = reader.get_metrics_data()  # None when nothing was recorded at all
        names = {
            m.name
            for rm in (data.resource_metrics if data else [])
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "hermes.session.turns" not in names

    def test_finalize_fails_open(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_finalize()  # no session id at all
        on_session_finalize(session_id=None, unexpected="kwarg")
        assert not exporter.get_finished_spans()


class TestReset:
    def test_gateway_reset_finalizes_old_and_chains_new(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _turn("old", True)
        _turn("old", False)
        on_session_reset(
            session_id="new",
            reason="new_session",
            platform="telegram",
            old_session_id="old",
            new_session_id="new",
        )
        assert plugin.sessions.turn_number("old") == 0
        assert plugin.sessions.previous_session("new") == "old"

        _turn("new", True)
        root = _roots(exporter)[-1]
        assert root.attributes["hermes.session.previous_id"] == "old"
        assert root.attributes["hermes.turn.number"] == 1
        old_roots = [r for r in _roots(exporter) if r.attributes["hermes.session_id"] == "old"]
        assert len(root.links) == 1
        assert root.links[0].context.trace_id == old_roots[-1].context.trace_id
        assert root.links[0].context.span_id == old_roots[-1].context.span_id
        assert dict(root.links[0].attributes) == {"hermes.link": "previous_session"}

    def test_cli_reset_names_only_the_new_session(self, inmemory_otel_setup):
        """The CLI finalizes the old session, then fires reset with the new id only."""
        exporter, plugin = inmemory_otel_setup
        _turn("old", True)
        on_session_finalize(session_id="old", platform="cli", reason="session_boundary")
        on_session_reset(session_id="new", reason="new_session", platform="cli")
        assert plugin.sessions.previous_session("new") == "old"
        _turn("new", True)
        root = _roots(exporter)[-1]
        assert root.attributes["hermes.session.previous_id"] == "old"
        assert len(root.links) == 1

    def test_reset_with_an_open_turn_closes_it_with_the_reset_reason(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _turn("old", True, end=False)
        on_session_reset(
            session_id="new", old_session_id="old", new_session_id="new", reason="new_session"
        )
        root = _roots(exporter)[-1]
        assert root.attributes["hermes.session.reset_reason"] == "new_session"
        assert root.attributes["hermes.turn.final_status"] == "reset"

    def test_reset_without_any_known_old_session_only_records_nothing(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_reset(session_id="fresh", reason="new_session", platform="cli")
        assert plugin.sessions.previous_session("fresh") is None
        _turn("fresh", True)
        root = _roots(exporter)[-1]
        assert "hermes.session.previous_id" not in root.attributes
        assert root.links == ()

    def test_reset_fails_open(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        on_session_reset()
        on_session_reset(session_id="x", old_session_id="x", new_session_id="x")
        assert not exporter.get_finished_spans()


class TestFirstSeenClock:
    def test_duration_measures_from_the_first_turn(self, inmemory_otel_with_metrics, monkeypatch):
        exporter, reader, plugin = inmemory_otel_with_metrics
        _turn("s1", True)
        # Back-date the first turn by an hour.
        plugin.sessions._first_seen["s1"] = time.time() - 3600
        on_session_finalize(session_id="s1", platform="cli", reason="idle")
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "hermes.session.duration":
                        dp = list(m.data.data_points)[0]
                        assert 3599 <= dp.sum <= 3700
                        return
        pytest.fail("hermes.session.duration not recorded")
