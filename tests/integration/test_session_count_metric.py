"""#473: ``hermes.session.count`` counts a session when this process first
roots it, whether through ``on_session_start`` or a root synthesised on
``pre_llm_call`` (resumed ``hermes -r``, gateway and cron turns).
"""

from __future__ import annotations

from _helpers import get_metric, get_metric_value, metric_points
from helpers.turns import run_turn

from hermes_otel.hooks import on_pre_llm_call, on_session_end

_HISTORY = [
    {"role": "user", "content": "one"},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "two"},
]


def _synthesised_turn(session_id: str, platform: str = "cli") -> None:
    """A continuation turn as Hermes delivers it: no on_session_start."""
    on_pre_llm_call(
        session_id=session_id,
        user_message="again",
        conversation_history=_HISTORY,
        model="gpt-4",
        platform=platform,
        is_first_turn=False,
    )
    on_session_end(
        session_id=session_id,
        completed=True,
        interrupted=False,
        model="gpt-4",
        platform=platform,
    )


class TestSessionCount:
    def test_real_session_start_counts_once(self, inmemory_otel_with_metrics):
        _, reader, _plugin = inmemory_otel_with_metrics
        run_turn("s1", first=True)
        (point,) = metric_points(get_metric(reader, "hermes.session.count"))
        assert point.value == 1
        assert point.attributes["platform"] == "cli"

    def test_resumed_session_in_a_fresh_process_is_counted(self, inmemory_otel_with_metrics):
        """``hermes -r``: the first thing this process sees is pre_llm_call."""
        _, reader, _plugin = inmemory_otel_with_metrics
        _synthesised_turn("resumed", platform="cli")
        assert get_metric_value(reader, "hermes.session.count") == 1

    def test_continuation_turns_count_nothing(self, inmemory_otel_with_metrics):
        """One session, four turns in one process: one session."""
        _, reader, _plugin = inmemory_otel_with_metrics
        run_turn("s1", first=True)
        for _ in range(3):
            _synthesised_turn("s1")
        assert get_metric_value(reader, "hermes.session.count") == 1

    def test_synthesised_then_real_start_counts_once(self, inmemory_otel_with_metrics):
        """Whichever root comes first counts; the other is a continuation."""
        _, reader, _plugin = inmemory_otel_with_metrics
        _synthesised_turn("s1")
        run_turn("s1", first=True)
        assert get_metric_value(reader, "hermes.session.count") == 1

    def test_two_sessions_two_points(self, inmemory_otel_with_metrics):
        _, reader, _plugin = inmemory_otel_with_metrics
        run_turn("a", first=True)
        _synthesised_turn("b", platform="telegram")
        points = metric_points(get_metric(reader, "hermes.session.count"))
        assert {p.attributes["platform"]: p.value for p in points} == {"cli": 1, "telegram": 1}
