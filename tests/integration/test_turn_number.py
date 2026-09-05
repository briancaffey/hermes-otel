"""Integration tests for the ``hermes.turn.number`` span attribute.

One user prompt is one turn. ``pre_llm_call`` fires once per prompt, so it
numbers the turn; every span opened afterwards in that turn (api / tool) and
the turn's session root carry the same number.
"""

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.session_state import SessionState


def _turn(session_id: str, is_first_turn: bool, tools=("terminal",)):
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
        tool_count=len(tools),
        approx_input_tokens=10,
        request_char_count=40,
        max_tokens=100,
    )
    for i, name in enumerate(tools):
        on_pre_tool_call(
            tool_name=name, args={"command": "ls"}, task_id=f"t{i}", session_id=session_id
        )
        on_post_tool_call(
            tool_name=name,
            args={"command": "ls"},
            result="ok",
            task_id=f"t{i}",
            session_id=session_id,
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
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        assistant_content_chars=2,
        assistant_tool_call_count=len(tools),
    )
    on_post_llm_call(
        session_id=session_id,
        user_message="hello",
        assistant_response="done",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model="gpt-4", platform="cli"
    )


def _turn_numbers(spans):
    return {s.name: s.attributes.get("hermes.turn.number") for s in spans}


class TestTurnNumberOnSpans:
    def test_first_turn_stamps_every_span_with_1(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        _turn("s1", is_first_turn=True)
        numbers = _turn_numbers(exporter.get_finished_spans())
        assert numbers == {
            "agent": 1,
            "llm.gpt-4": 1,
            "api.gpt-4": 1,
            "tool.terminal": 1,
        }

    def test_continuation_turns_increment(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        _turn("s1", is_first_turn=True)
        exporter.clear()
        _turn("s1", is_first_turn=False)
        assert set(_turn_numbers(exporter.get_finished_spans()).values()) == {2}
        exporter.clear()
        _turn("s1", is_first_turn=False)
        assert set(_turn_numbers(exporter.get_finished_spans()).values()) == {3}

    def test_sessions_are_counted_independently(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        _turn("a", is_first_turn=True)
        _turn("b", is_first_turn=True)
        _turn("a", is_first_turn=False)
        by_session = {}
        for s in exporter.get_finished_spans():
            if s.name == "agent":
                by_session.setdefault(s.attributes.get("session.id"), []).append(
                    s.attributes["hermes.turn.number"]
                )
        assert by_session == {"a": [1, 2], "b": [1]}

    def test_is_first_turn_resets_a_reused_session_id(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        _turn("s1", is_first_turn=True)
        _turn("s1", is_first_turn=False)
        exporter.clear()
        _turn("s1", is_first_turn=True)
        assert set(_turn_numbers(exporter.get_finished_spans()).values()) == {1}

    def test_tool_before_any_llm_call_has_no_turn_attribute(self, inmemory_otel_setup):
        exporter, _plugin = inmemory_otel_setup
        on_pre_tool_call(tool_name="terminal", args={}, task_id="t0", session_id="s1")
        on_post_tool_call(tool_name="terminal", args={}, result="ok", task_id="t0", session_id="s1")
        (span,) = exporter.get_finished_spans()
        assert "hermes.turn.number" not in span.attributes


class TestSessionStateTurnCounter:
    def test_next_turn_and_lookup(self):
        st = SessionState()
        assert st.turn_number("s") == 0
        assert st.next_turn("s") == 1
        assert st.next_turn("s") == 2
        assert st.turn_number("s") == 2
        assert st.next_turn("s", reset=True) == 1

    def test_counter_survives_pop_of_the_per_turn_aggregator(self):
        st = SessionState()
        st.next_turn("s")
        st.pop("s")
        assert st.turn_number("s") == 1
        assert st.next_turn("s") == 2

    def test_empty_session_id_is_ignored(self):
        st = SessionState()
        assert st.next_turn("") == 0
        assert st.turn_number("") == 0

    def test_counters_are_bounded_oldest_evicted_first(self):
        st = SessionState()
        st._MAX_TURN_COUNTERS = 3
        for sid in ("a", "b", "c", "d"):
            st.next_turn(sid)
            st.pop(sid)
        assert st.turn_number("a") == 0
        assert st.turn_number("d") == 1

    def test_clear_resets_counters(self):
        st = SessionState()
        st.next_turn("s")
        st.clear()
        assert st.turn_number("s") == 0
