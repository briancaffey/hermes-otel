"""First-chunk timing, stopped turns and verify nudges (#396)."""

from __future__ import annotations

import time

from _helpers import span_by_name as _span_by_name
from helpers.turns import api_request_kwargs, api_response_kwargs

from hermes_otel.hooks import (
    on_agent_loop_stopped,
    on_post_api_request,
    on_pre_api_request,
    on_pre_verify,
    on_session_end,
    on_session_start,
)
from hermes_otel.plugin_config import HermesOtelConfig


def _api(sid, **over):
    on_pre_api_request(**api_request_kwargs(sid, "t1"))
    on_post_api_request(**api_response_kwargs(sid, "t1", **over))


class TestFirstChunk:
    def test_streamed_call_carries_ttfc(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s", model="gpt-4", platform="cli")
        _api("s", started_at=1000.0, first_chunk_at=1000.35, api_duration=1.2)
        on_session_end(
            session_id="s", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        api = _span_by_name(exporter.get_finished_spans(), "api.gpt-4")
        assert api.attributes["hermes.api.ttfc_ms"] == 350.0

    def test_non_streamed_or_bad_values_carry_nothing(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s", model="gpt-4", platform="cli")
        _api("s", started_at=1000.0)  # no first chunk: not streamed
        on_session_end(
            session_id="s", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        api = _span_by_name(exporter.get_finished_spans(), "api.gpt-4")
        assert "hermes.api.ttfc_ms" not in api.attributes

    def test_seconds_helper(self):
        from hermes_otel.hooks.api_call import first_chunk_seconds

        assert first_chunk_seconds(10.0, 10.5) == 0.5
        assert first_chunk_seconds(10.0, 9.0) is None
        assert first_chunk_seconds(None, 9.0) is None
        assert first_chunk_seconds("x", 9.0) is None


class TestStoppedTurn:
    def test_stop_marks_the_root_and_the_sweep_ends_it_interrupted(
        self, inmemory_otel_setup, monkeypatch
    ):
        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:telegram:dm:1")
        on_session_start(session_id="g1", model="gpt-4", platform="telegram")
        on_agent_loop_stopped(
            session_key="agent:main:telegram:dm:1", platform="telegram", reason="stop_command"
        )
        plugin.register_turn("g1", started_at=time.perf_counter() - 10.0)
        plugin.sweep_expired_turns()
        root = _span_by_name(exporter.get_finished_spans(), "agent")
        assert root.attributes["hermes.turn.final_status"] == "interrupted"
        assert root.attributes["hermes.turn.stop_reason"] == "stop_command"

    def test_a_stopped_turn_that_ends_normally_keeps_its_end(
        self, inmemory_otel_setup, monkeypatch
    ):
        exporter, _ = inmemory_otel_setup
        monkeypatch.setenv("HERMES_SESSION_KEY", "k2")
        on_session_start(session_id="g2", model="gpt-4", platform="telegram")
        on_agent_loop_stopped(session_key="k2", reason="new_session")
        on_session_end(
            session_id="g2", completed=False, interrupted=True, model="gpt-4", platform="telegram"
        )
        root = _span_by_name(exporter.get_finished_spans(), "agent")
        assert root.attributes["hermes.turn.final_status"] == "interrupted"
        assert root.attributes["hermes.turn.stop_reason"] == "new_session"

    def test_an_unknown_key_does_nothing(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        assert on_agent_loop_stopped(session_key="nobody", reason="x") is None
        assert not exporter.get_finished_spans()

    def test_the_sweep_without_a_stop_is_still_timed_out(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)
        on_session_start(session_id="g3", model="gpt-4", platform="cli")
        plugin.register_turn("g3", started_at=time.perf_counter() - 10.0)
        plugin.sweep_expired_turns()
        root = _span_by_name(exporter.get_finished_spans(), "agent")
        assert root.attributes["hermes.turn.final_status"] == "timed_out"


class TestVerifyNudges:
    def test_counted_on_the_root_and_never_a_decision(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="v", model="gpt-4", platform="cli")
        for attempt in (0, 1):
            result = on_pre_verify(
                session_id="v",
                platform="cli",
                model="gpt-4",
                coding=True,
                attempt=attempt,
                final_response="done",
                changed_paths=["/secret/project/a.py"],
            )
            assert result is None  # a dict would keep Hermes's turn going
        on_session_end(
            session_id="v", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        root = _span_by_name(exporter.get_finished_spans(), "agent")
        assert root.attributes["hermes.turn.verify_nudges"] == 2
        assert not any("/secret" in str(v) for v in root.attributes.values())
