"""Tests for all 8 hook callbacks in hooks.py with mocked tracer."""

from unittest.mock import MagicMock, patch

import pytest

import hermes_otel.hooks as hooks_mod
from hermes_otel.hooks import (
    on_session_start,
    on_session_end,
    on_pre_tool_call,
    on_post_tool_call,
    on_pre_llm_call,
    on_post_llm_call,
    on_pre_api_request,
    on_post_api_request,
)


@pytest.fixture()
def mock_tracer():
    """Create a mock tracer and patch get_tracer() to return it.

    ``spans._active_spans`` is a real dict so ``"session:..." in tracer``
    checks in hooks.py behave correctly (MagicMock's __contains__ always
    returns False).
    """
    from hermes_otel.plugin_config import HermesOtelConfig
    tracer = MagicMock()
    tracer.is_enabled = True
    tracer.spans = MagicMock()
    tracer.spans._active_spans = {}
    tracer.config = HermesOtelConfig()
    with patch("hermes_otel.hooks.get_tracer", return_value=tracer):
        yield tracer


@pytest.fixture()
def disabled_tracer():
    """Create a disabled mock tracer."""
    from hermes_otel.plugin_config import HermesOtelConfig
    tracer = MagicMock()
    tracer.is_enabled = False
    tracer.config = HermesOtelConfig()
    with patch("hermes_otel.hooks.get_tracer", return_value=tracer):
        yield tracer


class TestOnSessionStart:
    def test_creates_agent_span(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="api_server")
        mock_tracer.start_span.assert_called_once()
        call_kwargs = mock_tracer.start_span.call_args[1]
        assert call_kwargs["name"] == "agent"
        assert call_kwargs["key"] == "session:s1"
        assert call_kwargs["kind"] == "agent"

    def test_creates_cron_span_when_cron(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli", session_type="cron")
        call_kwargs = mock_tracer.start_span.call_args[1]
        assert call_kwargs["name"] == "cron"

    def test_pushes_parent(self, mock_tracer):
        span = MagicMock()
        mock_tracer.start_span.return_value = span
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        mock_tracer.spans.push_parent.assert_called_once_with(span, session_id="s1")

    def test_records_session_count_metric(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        mock_tracer.record_metric.assert_called_once_with(
            "session_count", 1, {"session_id": "s1"}
        )

    def test_includes_session_attributes(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4o", platform="telegram")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["session_id"] == "s1"
        assert attrs["llm.model_name"] == "gpt-4o"
        assert attrs["llm.provider"] == "telegram"

    def test_includes_cron_job_id(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli", job_id="j123")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["hermes.cron.job_id"] == "j123"

    def test_noop_when_disabled(self, disabled_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        disabled_tracer.start_span.assert_not_called()


class TestOnSessionEnd:
    def test_pops_parent_and_ends_span(self, mock_tracer):
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        call_args = mock_tracer.end_span.call_args
        assert call_args[0][0] == "session:s1"

    def test_status_ok_when_completed(self, mock_tracer):
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "ok"

    def test_status_ok_when_interrupted(self, mock_tracer):
        on_session_end(session_id="s1", completed=False, interrupted=True,
                       model="gpt-4", platform="cli")
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "ok"

    def test_status_error_when_neither(self, mock_tracer):
        on_session_end(session_id="s1", completed=False, interrupted=False,
                       model="gpt-4", platform="cli")
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "error"

    def test_rolls_up_session_usage(self, mock_tracer):
        hooks_mod._SESSION_USAGE["s1"] = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cache_read_tokens": 20,
            "cache_write_tokens": 10,
        }
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["llm.token_count.prompt"] == 100
        assert attrs["llm.token_count.completion"] == 50
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50
        assert attrs["llm.token_count.prompt_details.cache_read"] == 20
        assert attrs["gen_ai.usage.cache_creation_input_tokens"] == 10
        # Verify cleanup
        assert "s1" not in hooks_mod._SESSION_USAGE

    def test_rolls_up_session_io(self, mock_tracer):
        hooks_mod._SESSION_IO["s1"] = {"input": "hello", "output": "world"}
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["input.value"] == "hello"
        assert attrs["output.value"] == "world"
        assert "s1" not in hooks_mod._SESSION_IO

    def test_noop_when_disabled(self, disabled_tracer):
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")
        disabled_tracer.end_span.assert_not_called()


class TestOnPreToolCall:
    def test_creates_tool_span(self, mock_tracer):
        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="t1")
        mock_tracer.start_span.assert_called_once()
        kw = mock_tracer.start_span.call_args[1]
        assert kw["name"] == "tool.bash"
        assert kw["key"] == "bash:t1"
        assert kw["kind"] == "tool"

    def test_sets_tool_attributes(self, mock_tracer):
        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="t1")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["tool.name"] == "bash"
        assert '"cmd"' in attrs["input.value"]

    def test_records_start_time(self, mock_tracer):
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        assert "bash:t1" in hooks_mod._TOOL_START_TIMES

    def test_noop_when_disabled(self, disabled_tracer):
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        disabled_tracer.start_span.assert_not_called()


class TestOnPostToolCall:
    def test_ends_tool_span(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result="output", task_id="t1")
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "bash:t1"

    def test_sets_output_attribute(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result="file.txt", task_id="t1")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["output.value"] == "file.txt"

    def test_status_ok_on_success(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        kw = mock_tracer.end_span.call_args[1]
        assert kw["status"] == "ok"

    def test_status_error_on_error_result(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result='{"error": "boom"}', task_id="t1")
        kw = mock_tracer.end_span.call_args[1]
        assert kw["status"] == "error"
        assert "boom" in (kw.get("error_message") or "")

    def test_records_tool_duration_metric(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        mock_tracer.record_metric.assert_called_once()
        name, value, attrs = mock_tracer.record_metric.call_args[0]
        assert name == "tool_duration"
        assert attrs["tool_name"] == "bash"

    def test_cleans_up_start_time(self, mock_tracer):
        hooks_mod._TOOL_START_TIMES["bash:t1"] = 1000.0
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        assert "bash:t1" not in hooks_mod._TOOL_START_TIMES

    def test_noop_when_disabled(self, disabled_tracer):
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        disabled_tracer.end_span.assert_not_called()


class TestOnPreLlmCall:
    def test_creates_llm_span(self, mock_tracer):
        # Pre-populate the session span so lazy-create is skipped (normal
        # first-turn flow: on_session_start runs before on_pre_llm_call).
        mock_tracer.spans._active_spans["session:s1"] = MagicMock()

        on_pre_llm_call(session_id="s1", user_message="hello",
                        conversation_history=[], is_first_turn=True,
                        model="gpt-4", platform="cli")
        mock_tracer.start_span.assert_called_once()
        kw = mock_tracer.start_span.call_args[1]
        assert kw["name"] == "llm.gpt-4"
        assert kw["key"] == "llm:s1"
        assert kw["kind"] == "llm"

    def test_pushes_parent(self, mock_tracer):
        mock_tracer.spans._active_spans["session:s1"] = MagicMock()

        span = MagicMock()
        mock_tracer.start_span.return_value = span
        on_pre_llm_call(session_id="s1", user_message="hello",
                        conversation_history=[], is_first_turn=True,
                        model="gpt-4", platform="cli")
        mock_tracer.spans.push_parent.assert_called_once_with(span, session_id="s1")

    def test_lazy_creates_session_span_on_continuation_turn(self, mock_tracer):
        """Turn 2+ has no active session span — hooks.py synthesizes one."""
        # No session span in _active_spans → lazy-create path fires.
        on_pre_llm_call(session_id="s1", user_message="hi",
                        conversation_history=[], is_first_turn=False,
                        model="gpt-4", platform="cli")
        # Two start_span calls: agent (synthesized) + llm.gpt-4
        assert mock_tracer.start_span.call_count == 2
        first_kw = mock_tracer.start_span.call_args_list[0][1]
        assert first_kw["name"] == "agent"
        assert first_kw["key"] == "session:s1"
        assert first_kw["attributes"].get("hermes.session.synthesized") is True

    def test_captures_first_input_in_session_io(self, mock_tracer):
        on_pre_llm_call(session_id="s1", user_message="hello",
                        conversation_history=[], is_first_turn=True,
                        model="gpt-4", platform="cli")
        assert hooks_mod._SESSION_IO["s1"]["input"] == "hello"

    def test_does_not_overwrite_existing_session_io(self, mock_tracer):
        hooks_mod._SESSION_IO["s1"] = {"input": "first", "output": ""}
        on_pre_llm_call(session_id="s1", user_message="second",
                        conversation_history=[], is_first_turn=False,
                        model="gpt-4", platform="cli")
        assert hooks_mod._SESSION_IO["s1"]["input"] == "first"

    def test_returns_none(self, mock_tracer):
        result = on_pre_llm_call(session_id="s1", user_message="hello",
                                 conversation_history=[], is_first_turn=True,
                                 model="gpt-4", platform="cli")
        assert result is None

    def test_noop_when_disabled(self, disabled_tracer):
        on_pre_llm_call(session_id="s1", user_message="hello",
                        conversation_history=[], is_first_turn=True,
                        model="gpt-4", platform="cli")
        disabled_tracer.start_span.assert_not_called()


class TestOnPostLlmCall:
    def test_pops_parent_and_ends_span(self, mock_tracer):
        on_post_llm_call(session_id="s1", user_message="hello",
                         assistant_response="hi", conversation_history=[],
                         model="gpt-4", platform="cli")
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "llm:s1"

    def test_captures_last_output_in_session_io(self, mock_tracer):
        hooks_mod._SESSION_IO["s1"] = {"input": "hello", "output": ""}
        on_post_llm_call(session_id="s1", user_message="hello",
                         assistant_response="goodbye", conversation_history=[],
                         model="gpt-4", platform="cli")
        assert hooks_mod._SESSION_IO["s1"]["output"] == "goodbye"

    def test_records_message_count_metric(self, mock_tracer):
        on_post_llm_call(session_id="s1", user_message="hello",
                         assistant_response="hi", conversation_history=[],
                         model="gpt-4", platform="cli")
        mock_tracer.record_metric.assert_called_once_with(
            "message_count", 1,
            {"session_id": "s1", "model": "gpt-4", "provider": "cli"}
        )

    def test_noop_when_disabled(self, disabled_tracer):
        on_post_llm_call(session_id="s1", user_message="hello",
                         assistant_response="hi", conversation_history=[],
                         model="gpt-4", platform="cli")
        disabled_tracer.end_span.assert_not_called()


class TestOnPreApiRequest:
    def test_creates_api_span(self, mock_tracer):
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="https://api.openai.com", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=2,
            approx_input_tokens=500, request_char_count=2000, max_tokens=1024,
        )
        mock_tracer.start_span.assert_called_once()
        kw = mock_tracer.start_span.call_args[1]
        assert kw["name"] == "api.gpt-4"
        assert kw["key"] == "api:t1"
        assert kw["kind"] == "llm"

    def test_pushes_parent(self, mock_tracer):
        span = MagicMock()
        mock_tracer.start_span.return_value = span
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=0,
            approx_input_tokens=500, request_char_count=2000, max_tokens=0,
        )
        mock_tracer.spans.push_parent.assert_called_once_with(span, session_id="s1")

    def test_includes_metadata_attributes(self, mock_tracer):
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=10, tool_count=0,
            approx_input_tokens=500, request_char_count=2000, max_tokens=2048,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["llm.model_name"] == "gpt-4"
        assert attrs["llm.provider"] == "openai"
        assert attrs["llm.request.message_count"] == 10
        assert attrs["llm.request.max_tokens"] == 2048

    def test_noop_when_disabled(self, disabled_tracer):
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=0,
            approx_input_tokens=500, request_char_count=2000, max_tokens=0,
        )
        disabled_tracer.start_span.assert_not_called()


class TestOnPostApiRequest:
    def _call_post_api(self, mock_tracer, usage=None, **overrides):
        defaults = dict(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.5, finish_reason="stop",
            message_count=5, response_model="gpt-4", usage=usage or {},
            assistant_content_chars=100, assistant_tool_call_count=0,
        )
        defaults.update(overrides)
        on_post_api_request(**defaults)

    def test_pops_parent_and_ends_span(self, mock_tracer):
        self._call_post_api(mock_tracer)
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "api:t1"

    def test_dual_convention_token_attributes(self, mock_tracer):
        usage = {
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }
        self._call_post_api(mock_tracer, usage=usage)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        # OpenInference (Phoenix)
        assert attrs["llm.token_count.prompt"] == 100
        assert attrs["llm.token_count.completion"] == 50
        assert attrs["llm.token_count.total"] == 150
        # OTel GenAI (Langfuse)
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50
        assert attrs["gen_ai.usage.total_tokens"] == 150

    def test_cache_token_attributes(self, mock_tracer):
        usage = {
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cache_read_tokens": 30,
            "cache_write_tokens": 15,
        }
        self._call_post_api(mock_tracer, usage=usage)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["llm.token_count.prompt_details.cache_read"] == 30
        assert attrs["gen_ai.usage.cache_read_input_tokens"] == 30
        assert attrs["llm.token_count.prompt_details.cache_write"] == 15
        assert attrs["gen_ai.usage.cache_creation_input_tokens"] == 15

    def test_session_usage_rollup(self, mock_tracer):
        usage = {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._call_post_api(mock_tracer, usage=usage)
        assert hooks_mod._SESSION_USAGE["s1"]["prompt_tokens"] == 100
        assert hooks_mod._SESSION_USAGE["s1"]["completion_tokens"] == 50
        assert hooks_mod._SESSION_USAGE["s1"]["total_tokens"] == 150

    def test_session_usage_accumulates(self, mock_tracer):
        usage = {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._call_post_api(mock_tracer, usage=usage)
        self._call_post_api(mock_tracer, usage=usage, task_id="t2")
        assert hooks_mod._SESSION_USAGE["s1"]["prompt_tokens"] == 200

    def test_records_duration_attribute(self, mock_tracer):
        self._call_post_api(mock_tracer, api_duration=1.234)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["llm.response.duration_ms"] == 1234.0

    def test_records_token_metrics(self, mock_tracer):
        usage = {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._call_post_api(mock_tracer, usage=usage)
        metric_calls = [c for c in mock_tracer.record_metric.call_args_list]
        metric_names = [c[0][0] for c in metric_calls]
        assert "token_usage" in metric_names
        assert "model_usage" in metric_names

    def test_noop_when_disabled(self, disabled_tracer):
        on_post_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, api_duration=0.5, finish_reason="stop",
            message_count=5, response_model="gpt-4", usage={},
            assistant_content_chars=100, assistant_tool_call_count=0,
        )
        disabled_tracer.end_span.assert_not_called()
