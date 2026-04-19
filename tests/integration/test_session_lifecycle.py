"""Integration tests for a complete session lifecycle with token aggregation
and session I/O roll-up."""

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
)


def _span_by_name(spans, name):
    for s in spans:
        if s.name == name:
            return s
    raise ValueError(f"No span named '{name}'")


class TestFullSessionLifecycle:
    def test_complete_session_with_token_aggregation(self, inmemory_otel_setup):
        """Simulate a complete session: start -> llm -> api -> tool -> api end -> llm end -> end.
        Verify token roll-up on the session span."""
        exporter, _ = inmemory_otel_setup

        # Session starts
        on_session_start(session_id="s1", model="gpt-4", platform="api_server")

        # First LLM turn
        on_pre_llm_call(
            session_id="s1",
            user_message="What files are here?",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="api_server",
        )

        # API request 1 (triggers tool call)
        on_pre_api_request(
            task_id="api1",
            session_id="s1",
            platform="api_server",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=3,
            tool_count=0,
            approx_input_tokens=200,
            request_char_count=1000,
            max_tokens=1024,
        )
        on_post_api_request(
            task_id="api1",
            session_id="s1",
            platform="api_server",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.8,
            finish_reason="tool_calls",
            message_count=3,
            response_model="gpt-4",
            usage={
                "prompt_tokens": 200,
                "output_tokens": 30,
                "total_tokens": 230,
                "cache_read_tokens": 50,
            },
            assistant_content_chars=100,
            assistant_tool_call_count=1,
        )

        # Tool executes
        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="tool1")
        on_post_tool_call(
            tool_name="bash", args={"cmd": "ls"}, result="file.txt\nREADME.md", task_id="tool1"
        )

        # API request 2 (final response)
        on_pre_api_request(
            task_id="api2",
            session_id="s1",
            platform="api_server",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=2,
            message_count=5,
            tool_count=0,
            approx_input_tokens=300,
            request_char_count=1500,
            max_tokens=1024,
        )
        on_post_api_request(
            task_id="api2",
            session_id="s1",
            platform="api_server",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=2,
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={
                "prompt_tokens": 300,
                "output_tokens": 80,
                "total_tokens": 380,
                "cache_read_tokens": 100,
            },
            assistant_content_chars=300,
            assistant_tool_call_count=0,
        )

        # LLM turn ends
        on_post_llm_call(
            session_id="s1",
            user_message="What files are here?",
            assistant_response="I found file.txt and README.md",
            conversation_history=[],
            model="gpt-4",
            platform="api_server",
        )

        # Session ends
        on_session_end(
            session_id="s1",
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="api_server",
        )

        spans = exporter.get_finished_spans()
        # Expect: 2 api spans + 1 tool span + 1 llm span + 1 session span = 5
        assert len(spans) == 5

        session_span = _span_by_name(spans, "agent")
        attrs = dict(session_span.attributes)

        # Token roll-up: 200+300=500 prompt, 30+80=110 completion, 230+380=610 total
        assert attrs["llm.token_count.prompt"] == 500
        assert attrs["llm.token_count.completion"] == 110
        assert attrs["llm.token_count.total"] == 610
        assert attrs["gen_ai.usage.input_tokens"] == 500
        assert attrs["gen_ai.usage.output_tokens"] == 110

        # Cache roll-up: 50+100=150 cache read
        assert attrs["llm.token_count.prompt_details.cache_read"] == 150
        assert attrs["gen_ai.usage.cache_read_input_tokens"] == 150

        # Session I/O: first input and last output
        assert attrs["input.value"] == "What files are here?"
        assert "file.txt" in attrs["output.value"]

    def test_module_state_cleaned_after_session_end(self, inmemory_otel_setup):
        """Verify that per-session aggregators are popped at session end."""
        exporter, plugin = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="test",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=1,
            tool_count=0,
            approx_input_tokens=100,
            request_char_count=500,
            max_tokens=512,
        )
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.3,
            finish_reason="stop",
            message_count=1,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            assistant_content_chars=50,
            assistant_tool_call_count=0,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="test",
            assistant_response="ok",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id="s1",
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="cli",
        )

        # PerSession aggregator popped — no lingering state.
        assert plugin.sessions.peek("s1") is None

    def test_interrupted_session(self, inmemory_otel_setup):
        """An interrupted session should still export with status ok."""
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="s1",
            completed=False,
            interrupted=True,
            model="gpt-4",
            platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        from opentelemetry.trace import StatusCode

        assert spans[0].status.status_code == StatusCode.OK
