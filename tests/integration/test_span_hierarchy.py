"""Integration tests verifying parent-child span nesting via InMemorySpanExporter."""

import pytest

from hermes_otel.hooks import (
    on_pre_tool_call,
    on_post_tool_call,
    on_pre_llm_call,
    on_post_llm_call,
    on_pre_api_request,
    on_post_api_request,
    on_session_start,
    on_session_end,
)


def _span_by_name(spans, name):
    """Find a span by name from the exported list."""
    for s in spans:
        if s.name == name:
            return s
    raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")


def _parent_span_id(span):
    """Extract the parent span ID from a span, or None."""
    if span.parent is not None:
        return span.parent.span_id
    return None


class TestSessionContainsLlm:
    def test_llm_is_child_of_session(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1", user_message="hello", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        )
        on_post_llm_call(
            session_id="s1", user_message="hello", assistant_response="hi",
            conversation_history=[], model="gpt-4", platform="cli",
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False,
            model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 2

        session_span = _span_by_name(spans, "agent")
        llm_span = _span_by_name(spans, "llm.gpt-4")

        assert _parent_span_id(llm_span) == session_span.context.span_id


class TestLlmContainsApi:
    def test_api_is_child_of_llm(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_pre_llm_call(
            session_id="s1", user_message="hello", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        )
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=0,
            approx_input_tokens=500, request_char_count=2000, max_tokens=1024,
        )
        on_post_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.5, finish_reason="stop",
            message_count=5, response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=200, assistant_tool_call_count=0,
        )
        on_post_llm_call(
            session_id="s1", user_message="hello", assistant_response="hi",
            conversation_history=[], model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 2

        llm_span = _span_by_name(spans, "llm.gpt-4")
        api_span = _span_by_name(spans, "api.gpt-4")

        assert _parent_span_id(api_span) == llm_span.context.span_id


class TestApiContainsTool:
    def test_tool_is_child_of_api(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        # LLM turn starts, then API call, then tool, then API ends, then LLM ends
        on_pre_llm_call(
            session_id="s1", user_message="run ls", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        )
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=1,
            approx_input_tokens=500, request_char_count=2000, max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={"cmd": "ls"}, result="file.txt", task_id="tool1")
        on_post_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=1.0, finish_reason="stop",
            message_count=5, response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=200, assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="s1", user_message="run ls", assistant_response="here are files",
            conversation_history=[], model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 3

        api_span = _span_by_name(spans, "api.gpt-4")
        tool_span = _span_by_name(spans, "tool.bash")

        assert _parent_span_id(tool_span) == api_span.context.span_id


class TestFullHierarchy:
    def test_four_level_nesting(self, inmemory_otel_setup):
        """Session -> LLM -> API -> Tool, all correctly nested."""
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1", user_message="hello", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        )
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=1,
            approx_input_tokens=500, request_char_count=2000, max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="tool1")
        on_post_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.5, finish_reason="stop",
            message_count=5, response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=100, assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="s1", user_message="hello", assistant_response="done",
            conversation_history=[], model="gpt-4", platform="cli",
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False,
            model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 4

        session = _span_by_name(spans, "agent")
        llm = _span_by_name(spans, "llm.gpt-4")
        api = _span_by_name(spans, "api.gpt-4")
        tool = _span_by_name(spans, "tool.bash")

        # Verify parent chain: tool -> api -> llm -> session
        assert _parent_span_id(tool) == api.context.span_id
        assert _parent_span_id(api) == llm.context.span_id
        assert _parent_span_id(llm) == session.context.span_id
        assert _parent_span_id(session) is None  # root span


class TestMultipleToolsUnderApi:
    def test_sibling_tools_share_api_parent(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_pre_llm_call(
            session_id="s1", user_message="run both", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        )
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=2,
            approx_input_tokens=500, request_char_count=2000, max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={}, result="ok1", task_id="tool1")
        on_pre_tool_call(tool_name="file_read", args={}, task_id="tool2")
        on_post_tool_call(tool_name="file_read", args={}, result="ok2", task_id="tool2")
        on_post_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=1.0, finish_reason="stop",
            message_count=5, response_model="gpt-4",
            usage={"prompt_tokens": 200, "output_tokens": 100, "total_tokens": 300},
            assistant_content_chars=200, assistant_tool_call_count=2,
        )
        on_post_llm_call(
            session_id="s1", user_message="run both", assistant_response="done",
            conversation_history=[], model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 4

        api_span = _span_by_name(spans, "api.gpt-4")
        tool_spans = [s for s in spans if s.name.startswith("tool.")]
        assert len(tool_spans) == 2

        for tool_span in tool_spans:
            assert _parent_span_id(tool_span) == api_span.context.span_id
