"""Integration tests verifying parent-child span nesting via InMemorySpanExporter."""

import threading

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


class TestCaptureConversationHistory:
    """When ``capture_conversation_history`` is on, the llm span's
    input.value carries the whole message list instead of just the last
    user turn. Useful because the api.* spans don't expose message-level
    detail.
    """

    def _drive_llm_call(self, session_id, conversation_history, user_message="hi"):
        on_session_start(
            session_id=session_id, model="gpt-4", platform="cli",
        )
        on_pre_llm_call(
            session_id=session_id, user_message=user_message,
            conversation_history=conversation_history, is_first_turn=True,
            model="gpt-4", platform="cli",
        )
        on_post_llm_call(
            session_id=session_id, user_message=user_message,
            assistant_response="done", conversation_history=conversation_history,
            model="gpt-4", platform="cli",
        )
        on_session_end(
            session_id=session_id, completed=True, interrupted=False,
            model="gpt-4", platform="cli",
        )

    def test_default_off_uses_user_message(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        history = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "now do it again"},
        ]
        self._drive_llm_call("s1", history, user_message="now do it again")

        llm = _span_by_name(exporter.get_finished_spans(), "llm.gpt-4")
        attrs = dict(llm.attributes)
        assert attrs.get("input.value") == "now do it again"
        assert "hermes.conversation.message_count" not in attrs
        assert "input.mime_type" not in attrs

    def test_enabled_captures_full_history(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig
        plugin.config = HermesOtelConfig(capture_conversation_history=True)

        history = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "an earlier assistant reply"},
            {"role": "tool", "content": "tool output from before"},
            {"role": "user", "content": "now do it again"},
        ]
        self._drive_llm_call("s2", history)

        llm = _span_by_name(exporter.get_finished_spans(), "llm.gpt-4")
        attrs = dict(llm.attributes)
        input_value = attrs.get("input.value", "")
        assert "an earlier assistant reply" in input_value
        assert "tool output from before" in input_value
        assert attrs.get("hermes.conversation.message_count") == 4
        assert attrs.get("input.mime_type") == "application/json"

    def test_respects_max_chars_clip(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig
        plugin.config = HermesOtelConfig(
            capture_conversation_history=True,
            conversation_history_max_chars=200,
        )

        history = [{"role": "user", "content": "x" * 5000}]
        self._drive_llm_call("s3", history)

        llm = _span_by_name(exporter.get_finished_spans(), "llm.gpt-4")
        attrs = dict(llm.attributes)
        assert len(attrs["input.value"]) <= 200
        assert attrs["input.value"].endswith("...")

    def test_empty_history_falls_back_to_user_message(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig
        plugin.config = HermesOtelConfig(capture_conversation_history=True)

        self._drive_llm_call("s4", [], user_message="solo turn")

        llm = _span_by_name(exporter.get_finished_spans(), "llm.gpt-4")
        attrs = dict(llm.attributes)
        assert attrs.get("input.value") == "solo turn"
        assert "hermes.conversation.message_count" not in attrs


class TestContinuationTurnSynthesizesAgent:
    """hermes fires on_session_start only on the first turn of a session.
    Turns 2+ must still produce an ``agent`` root — not a loose ``llm.*``
    root — so the UX in the backend UI stays consistent.
    """

    def test_turn_without_session_start_synthesizes_agent(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        session_id = "continuation-s1"

        # No on_session_start — this is turn 2+.
        on_pre_llm_call(
            session_id=session_id, user_message="follow up",
            conversation_history=[], is_first_turn=False,
            model="gpt-4", platform="cli",
        )
        on_pre_api_request(
            task_id="api-c1", session_id=session_id, platform="cli",
            model="gpt-4", provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=1, tool_count=0,
            approx_input_tokens=10, request_char_count=20, max_tokens=100,
        )
        on_post_api_request(
            task_id="api-c1", session_id=session_id, platform="cli",
            model="gpt-4", provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.01, finish_reason="stop",
            message_count=1, response_model="gpt-4",
            usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            assistant_content_chars=1, assistant_tool_call_count=0,
        )
        on_post_llm_call(
            session_id=session_id, user_message="follow up",
            assistant_response="done", conversation_history=[],
            model="gpt-4", platform="cli",
        )
        on_session_end(
            session_id=session_id, completed=True, interrupted=False,
            model="gpt-4", platform="cli",
        )

        spans = exporter.get_finished_spans()
        # agent + llm + api = 3 spans.
        assert len(spans) == 3
        # One trace, rooted at agent.
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1

        agent = _span_by_name(spans, "agent")
        llm = _span_by_name(spans, "llm.gpt-4")
        api = _span_by_name(spans, "api.gpt-4")

        assert _parent_span_id(agent) is None
        assert _parent_span_id(llm) == agent.context.span_id
        assert _parent_span_id(api) == llm.context.span_id
        # The synthesized agent is tagged so it's easy to filter/audit.
        assert dict(agent.attributes).get("hermes.session.synthesized") is True


class TestCrossThreadNesting:
    """hermes-agent dispatches hooks on worker threads; the session-keyed
    parent stack must preserve agent > llm > api > tool nesting even when
    every hook fires on a different thread (where the ContextVar stack is
    empty).
    """

    def _run_on_thread(self, fn):
        t = threading.Thread(target=fn)
        t.start()
        t.join()

    def test_full_tree_survives_per_hook_threads(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        session_id = "cross-thread"

        self._run_on_thread(lambda: on_session_start(
            session_id=session_id, model="gpt-4", platform="cli",
        ))
        self._run_on_thread(lambda: on_pre_llm_call(
            session_id=session_id, user_message="hi", conversation_history=[],
            is_first_turn=True, model="gpt-4", platform="cli",
        ))
        self._run_on_thread(lambda: on_pre_api_request(
            task_id="api1", session_id=session_id, platform="cli",
            model="gpt-4", provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=1, tool_count=1,
            approx_input_tokens=10, request_char_count=20, max_tokens=100,
        ))
        self._run_on_thread(lambda: on_pre_tool_call(
            tool_name="bash", args={}, task_id="t1", session_id=session_id,
        ))
        self._run_on_thread(lambda: on_post_tool_call(
            tool_name="bash", args={}, result="ok",
            task_id="t1", session_id=session_id,
        ))
        self._run_on_thread(lambda: on_post_api_request(
            task_id="api1", session_id=session_id, platform="cli",
            model="gpt-4", provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.01, finish_reason="stop",
            message_count=1, response_model="gpt-4",
            usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            assistant_content_chars=1, assistant_tool_call_count=1,
        ))
        self._run_on_thread(lambda: on_post_llm_call(
            session_id=session_id, user_message="hi", assistant_response="ok",
            conversation_history=[], model="gpt-4", platform="cli",
        ))
        self._run_on_thread(lambda: on_session_end(
            session_id=session_id, completed=True, interrupted=False,
            model="gpt-4", platform="cli",
        ))

        spans = exporter.get_finished_spans()
        assert len(spans) == 4

        # One single trace (all spans share a trace_id).
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1, (
            f"expected 1 trace, got {len(trace_ids)}: {[s.name for s in spans]}"
        )

        agent = _span_by_name(spans, "agent")
        llm = _span_by_name(spans, "llm.gpt-4")
        api = _span_by_name(spans, "api.gpt-4")
        tool = _span_by_name(spans, "tool.bash")

        assert _parent_span_id(agent) is None
        assert _parent_span_id(llm) == agent.context.span_id
        assert _parent_span_id(api) == llm.context.span_id
        assert _parent_span_id(tool) == api.context.span_id
