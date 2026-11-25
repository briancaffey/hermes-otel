"""Integration tests verifying parent-child span nesting via InMemorySpanExporter."""

import json
import threading
import time

import pytest
from _helpers import parent_span_id as _parent_span_id
from _helpers import span_by_name as _span_by_name

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_auxiliary_call,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_auxiliary_call,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
    on_transform_llm_output,
)
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.testing import build_inmemory_plugin, install_singleton


class TestSessionContainsLlm:
    def test_llm_is_child_of_session(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
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

        spans = exporter.get_finished_spans()
        assert len(spans) == 2

        session_span = _span_by_name(spans, "agent")
        llm_span = _span_by_name(spans, "llm.gpt-4")

        assert _parent_span_id(llm_span) == session_span.context.span_id


class TestLlmContainsApi:
    def test_api_is_child_of_llm(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
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
            message_count=5,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
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
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=200,
            assistant_tool_call_count=0,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
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
            session_id="s1",
            user_message="run ls",
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
            message_count=5,
            tool_count=1,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={"cmd": "ls"}, result="file.txt", task_id="tool1")
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=1.0,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=200,
            assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="run ls",
            assistant_response="here are files",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
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
            session_id="s1",
            user_message="hello",
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
            message_count=5,
            tool_count=1,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="tool1")
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=100,
            assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="done",
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
            session_id="s1",
            user_message="run both",
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
            message_count=5,
            tool_count=2,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
        )
        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1")
        on_post_tool_call(tool_name="bash", args={}, result="ok1", task_id="tool1")
        on_pre_tool_call(tool_name="file_read", args={}, task_id="tool2")
        on_post_tool_call(tool_name="file_read", args={}, result="ok2", task_id="tool2")
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=1.0,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 200, "output_tokens": 100, "total_tokens": 300},
            assistant_content_chars=200,
            assistant_tool_call_count=2,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="run both",
            assistant_response="done",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
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
            session_id=session_id,
            model="gpt-4",
            platform="cli",
        )
        on_pre_llm_call(
            session_id=session_id,
            user_message=user_message,
            conversation_history=conversation_history,
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_post_llm_call(
            session_id=session_id,
            user_message=user_message,
            assistant_response="done",
            conversation_history=conversation_history,
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id=session_id,
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="cli",
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
        # Still valid JSON (#650): the message's content is clipped, not the string.
        assert json.loads(attrs["input.value"])[0]["content"].endswith("...")
        assert json.loads(attrs["gen_ai.input.messages"])[0]["role"] == "user"
        assert attrs["hermes.preview.input.truncated"] is True

    def test_long_history_drops_oldest_messages_and_stays_valid_json(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        from hermes_otel.plugin_config import HermesOtelConfig

        plugin.config = HermesOtelConfig(
            capture_conversation_history=True,
            conversation_history_max_chars=2_000,
        )
        history = [{"role": "user", "content": f"turn {i} " + "x" * 200} for i in range(40)]
        self._drive_llm_call("s5", history)

        attrs = dict(_span_by_name(exporter.get_finished_spans(), "llm.gpt-4").attributes)
        hermes = json.loads(attrs["input.value"])
        genai = json.loads(attrs["gen_ai.input.messages"])
        dropped = attrs["hermes.conversation.dropped_messages"]
        assert dropped > 0 and len(hermes) == len(genai) == 40 - dropped + 1
        assert hermes[-1] == history[-1]
        assert attrs["hermes.conversation.message_count"] == 40
        assert attrs["hermes.preview.input.original_chars"] == len(json.dumps(history))

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
            session_id=session_id,
            user_message="follow up",
            conversation_history=[],
            is_first_turn=False,
            model="gpt-4",
            platform="cli",
        )
        on_pre_api_request(
            task_id="api-c1",
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
            request_char_count=20,
            max_tokens=100,
        )
        on_post_api_request(
            task_id="api-c1",
            session_id=session_id,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.01,
            finish_reason="stop",
            message_count=1,
            response_model="gpt-4",
            usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            assistant_content_chars=1,
            assistant_tool_call_count=0,
        )
        on_post_llm_call(
            session_id=session_id,
            user_message="follow up",
            assistant_response="done",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id=session_id,
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="cli",
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

        self._run_on_thread(
            lambda: on_session_start(
                session_id=session_id,
                model="gpt-4",
                platform="cli",
            )
        )
        self._run_on_thread(
            lambda: on_pre_llm_call(
                session_id=session_id,
                user_message="hi",
                conversation_history=[],
                is_first_turn=True,
                model="gpt-4",
                platform="cli",
            )
        )
        self._run_on_thread(
            lambda: on_pre_api_request(
                task_id="api1",
                session_id=session_id,
                platform="cli",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                message_count=1,
                tool_count=1,
                approx_input_tokens=10,
                request_char_count=20,
                max_tokens=100,
            )
        )
        self._run_on_thread(
            lambda: on_pre_tool_call(
                tool_name="bash",
                args={},
                task_id="t1",
                session_id=session_id,
            )
        )
        self._run_on_thread(
            lambda: on_post_tool_call(
                tool_name="bash",
                args={},
                result="ok",
                task_id="t1",
                session_id=session_id,
            )
        )
        self._run_on_thread(
            lambda: on_post_api_request(
                task_id="api1",
                session_id=session_id,
                platform="cli",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                api_duration=0.01,
                finish_reason="stop",
                message_count=1,
                response_model="gpt-4",
                usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                assistant_content_chars=1,
                assistant_tool_call_count=1,
            )
        )
        self._run_on_thread(
            lambda: on_post_llm_call(
                session_id=session_id,
                user_message="hi",
                assistant_response="ok",
                conversation_history=[],
                model="gpt-4",
                platform="cli",
            )
        )
        self._run_on_thread(
            lambda: on_session_end(
                session_id=session_id,
                completed=True,
                interrupted=False,
                model="gpt-4",
                platform="cli",
            )
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 4

        # One single trace (all spans share a trace_id).
        trace_ids = {s.context.trace_id for s in spans}
        assert (
            len(trace_ids) == 1
        ), f"expected 1 trace, got {len(trace_ids)}: {[s.name for s in spans]}"

        agent = _span_by_name(spans, "agent")
        llm = _span_by_name(spans, "llm.gpt-4")
        api = _span_by_name(spans, "api.gpt-4")
        tool = _span_by_name(spans, "tool.bash")

        assert _parent_span_id(agent) is None
        assert _parent_span_id(llm) == agent.context.span_id
        assert _parent_span_id(api) == llm.context.span_id
        assert _parent_span_id(tool) == api.context.span_id


class TestDisabledFamiliesReparent:
    """``disable_spans`` (#479): a disabled family's spans are gone and what
    nested under them parents to the nearest recording ancestor."""

    @staticmethod
    def _turn():
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
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
            message_count=5,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
        )
        on_pre_tool_call(
            tool_name="terminal", args={"command": "ls"}, task_id="t1", session_id="s1"
        )
        on_post_tool_call(
            tool_name="terminal", args={"command": "ls"}, result="ok", task_id="t1", session_id="s1"
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
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50},
            assistant_content_chars=200,
            assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

    @pytest.fixture()
    def spans_with(self):
        built = []

        def build(*families):
            pipe = build_inmemory_plugin(config=HermesOtelConfig(disable_spans=families))
            install_singleton(pipe.plugin)
            built.append(pipe)
            self._turn()
            spans = pipe.span_exporter.get_finished_spans()
            by_id = {s.context.span_id: s.name for s in spans}
            return {s.name: by_id.get(_parent_span_id(s)) for s in spans}

        try:
            yield build
        finally:
            for pipe in built:
                pipe.shutdown()

    def test_default_tree(self, spans_with):
        assert spans_with() == {
            "agent": None,
            "llm.gpt-4": "agent",
            "api.gpt-4": "llm.gpt-4",
            "tool.terminal": "api.gpt-4",
        }

    def test_llm_disabled_api_parents_to_the_root(self, spans_with):
        assert spans_with("llm") == {
            "agent": None,
            "api.gpt-4": "agent",
            "tool.terminal": "api.gpt-4",
        }

    def test_api_disabled_tool_parents_to_the_llm(self, spans_with):
        assert spans_with("api") == {
            "agent": None,
            "llm.gpt-4": "agent",
            "tool.terminal": "llm.gpt-4",
        }

    def test_llm_and_api_disabled_tool_parents_to_the_root(self, spans_with):
        assert spans_with("llm", "api") == {"agent": None, "tool.terminal": "agent"}

    def test_tool_disabled_drops_only_the_tool_span(self, spans_with):
        assert spans_with("tool") == {
            "agent": None,
            "llm.gpt-4": "agent",
            "api.gpt-4": "llm.gpt-4",
        }


def _api_call(session_id, task_id, turn_id, tool_calls, content_chars=1, finish_reason=None):
    on_pre_api_request(
        task_id=task_id,
        turn_id=turn_id,
        session_id=session_id,
        platform="slack",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=1,
        approx_input_tokens=10,
        request_char_count=20,
        max_tokens=100,
    )
    on_post_api_request(
        task_id=task_id,
        turn_id=turn_id,
        session_id=session_id,
        platform="slack",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.01,
        finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
        message_count=1,
        response_model="gpt-4",
        usage={"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        assistant_content_chars=content_chars,
        assistant_tool_call_count=tool_calls,
    )


def _live_turn(session_id, turn_id):
    on_pre_llm_call(
        session_id=session_id,
        turn_id=turn_id,
        user_message="hello",
        conversation_history=[],
        is_first_turn=False,
        model="gpt-4",
        platform="slack",
    )
    _api_call(session_id, f"api-{turn_id}", turn_id, 0)
    on_post_llm_call(
        session_id=session_id,
        turn_id=turn_id,
        user_message="hello",
        assistant_response="hi",
        conversation_history=[],
        model="gpt-4",
        platform="slack",
    )
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model="gpt-4", platform="slack"
    )


def _final_text(session_id, turn_id):
    on_transform_llm_output(
        response_text="done",
        session_id=session_id,
        model="gpt-4",
        platform="slack",
        turn_id=turn_id,
    )


class TestForkTurnGetsItsOwnRoot:
    """A background review fork shares its parent's session id and fires no
    pre_llm_call or on_session_end, only API and tool hooks."""

    def test_fork_spans_share_one_root_closed_at_the_final_reply(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork", "t-live")
        _api_call("s-fork", "api-r1", "t-review", 1)
        on_pre_tool_call(
            tool_name="skill_view",
            args={},
            task_id="api-r1",
            tool_call_id="c1",
            session_id="s-fork",
        )
        on_post_tool_call(
            tool_name="skill_view",
            args={},
            result="{}",
            task_id="api-r1",
            tool_call_id="c1",
            session_id="s-fork",
        )
        _api_call("s-fork", "api-r2", "t-review", 0)
        assert not any(s.attributes.get("hermes.turn.fork") for s in exporter.get_finished_spans())
        _final_text("s-fork", "t-review")

        spans = exporter.get_finished_spans()
        fork_root = next(s for s in spans if s.attributes.get("hermes.turn.fork"))
        fork_spans = [s for s in spans if s.context.trace_id == fork_root.context.trace_id]
        assert sorted(s.name for s in fork_spans) == [
            "agent",
            "api.gpt-4",
            "api.gpt-4",
            "tool.skill_view",
        ]
        assert all(
            _parent_span_id(s) == fork_root.context.span_id for s in fork_spans if s != fork_root
        )
        assert fork_root.attributes["hermes.turn.exit_reason"] == "fork_final_reply"
        assert fork_root.attributes["hermes.turn.final_status"] == "completed"
        assert fork_root.attributes["gen_ai.usage.total_tokens"] == 4
        assert len({s.context.trace_id for s in spans}) == 2

    def test_next_live_turn_closes_an_open_fork_and_roots_itself(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork2", "t-1")
        _api_call("s-fork2", "api-r1", "t-review", 1)
        _live_turn("s-fork2", "t-2")

        spans = exporter.get_finished_spans()
        roots = [s for s in spans if _parent_span_id(s) is None]
        assert [r.attributes.get("hermes.turn.fork") for r in roots] == [None, True, None]
        # Its one request had finished: the fork's loop had stopped, nothing was cut.
        assert roots[1].attributes["hermes.turn.final_status"] == "completed"
        assert roots[1].attributes["hermes.turn.exit_reason"] == "fork_no_reply"
        assert len(spans) == 3 * 2 + 2

    def test_only_the_forks_own_final_text_closes_its_root(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork3", "t-1")
        _api_call("s-fork3", "api-r1", "t-review", 0, content_chars=0)
        _api_call("s-fork3", "api-r2", "t-review", 0, content_chars=40, finish_reason="length")
        _api_call("s-fork3", "api-r3", "t-review", 0)
        _final_text("s-fork3", "t-other")
        _final_text("s-fork3", "t-review")

        spans = exporter.get_finished_spans()
        fork_root = next(s for s in spans if s.attributes.get("hermes.turn.fork"))
        fork_spans = [s for s in spans if s.context.trace_id == fork_root.context.trace_id]
        assert sorted(s.name for s in fork_spans) == ["agent"] + ["api.gpt-4"] * 3
        assert fork_root.attributes["hermes.turn.exit_reason"] == "fork_final_reply"

    def test_turn_announced_under_another_session_is_not_a_fork(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_llm_call(
            session_id="s-before",
            turn_id="t-rotated",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="slack",
        )
        _api_call("s-after", "api-x", "t-rotated", 0)

        assert not any(s.attributes.get("hermes.turn.fork") for s in exporter.get_finished_spans())

    def test_next_turn_after_a_fork_cut_mid_request_roots_itself(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork4", "t-1")
        on_pre_api_request(
            task_id="api-r1",
            turn_id="t-review",
            session_id="s-fork4",
            platform="slack",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=1,
            tool_count=1,
            approx_input_tokens=10,
            request_char_count=20,
            max_tokens=100,
        )
        _live_turn("s-fork4", "t-2")

        agents = [s for s in exporter.get_finished_spans() if s.name == "agent"]
        assert [a.attributes.get("hermes.turn.fork") for a in agents] == [None, True, None]
        assert _parent_span_id(agents[2]) is None
        assert len({a.context.trace_id for a in agents}) == 3
        # A request was in flight: Hermes cut the fork.
        assert agents[1].attributes["hermes.turn.final_status"] == "interrupted"
        assert agents[1].attributes["hermes.turn.exit_reason"] == "fork_superseded"

    def test_fork_without_a_final_reply_ends_at_its_last_span(self, inmemory_otel_setup):
        """Hermes fires transform_llm_output only for a non-empty reply. The root the
        next turn finds idle ends where the fork's last span did, not after the wait."""
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork5", "t-1")
        _api_call("s-fork5", "api-r1", "t-review", 0, content_chars=0)
        time.sleep(0.3)
        _live_turn("s-fork5", "t-2")

        spans = exporter.get_finished_spans()
        fork_root = next(s for s in spans if s.attributes.get("hermes.turn.fork"))
        fork_api = next(
            s
            for s in spans
            if s.name == "api.gpt-4" and s.context.trace_id == fork_root.context.trace_id
        )
        assert fork_root.attributes["hermes.turn.final_status"] == "completed"
        assert fork_root.attributes["hermes.turn.exit_reason"] == "fork_no_reply"
        assert fork_root.end_time >= fork_api.end_time
        assert (fork_root.end_time - fork_root.start_time) / 1e9 < 0.25
        assert fork_root.attributes["gen_ai.usage.total_tokens"] == 2

    def test_late_auxiliary_call_still_links_to_the_live_turn(self, inmemory_otel_setup):
        """A fork's turn id must not replace the session's latest: titling for the
        live turn that starts after the fork ended links to the live root."""
        exporter, _ = inmemory_otel_setup
        _live_turn("s-fork6", "t-1")
        _api_call("s-fork6", "api-r1", "t-review", 0)
        _final_text("s-fork6", "t-review")
        aux = dict(
            aux_task="title",
            session_id="s-fork6",
            model="gpt-4o-mini",
            provider="openai",
            api_request_id="aux-1",
            turn_id="t-1",
        )
        on_pre_auxiliary_call(**aux)
        on_post_auxiliary_call(**aux, status="ok")

        spans = exporter.get_finished_spans()
        live_root = next(
            s for s in spans if s.name == "agent" and not s.attributes.get("hermes.turn.fork")
        )
        aux_span = next(s for s in spans if s.name == "api.gpt-4o-mini")
        assert [link.attributes["hermes.link"] for link in aux_span.links] == ["turn_root"]
        assert aux_span.links[0].context.span_id == live_root.context.span_id
