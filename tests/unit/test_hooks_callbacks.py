"""Tests for all 8 hook callbacks in hooks.py with mocked tracer."""

from unittest.mock import MagicMock, patch

import pytest
from hermes_otel.hooks import (
    _kanban_context_from_kwargs_env,
    on_api_request_error,
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)

KANBAN_ENV_KEYS = (
    "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_RUN_ID",
    "HERMES_KANBAN_BOARD",
    "HERMES_TENANT",
    "HERMES_PROFILE",
    "HERMES_KANBAN_SOURCE_KIND",
)


@pytest.fixture()
def clean_kanban_env(monkeypatch):
    for key in KANBAN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def mock_tracer():
    """Create a mock tracer and patch get_tracer() to return it.

    ``spans._active_spans`` and ``sessions`` are real so hooks that
    reach into them (e.g. continuation-turn lazy session-span creation,
    per-session I/O / usage / tool-time buffering) behave as they would
    in production — and tests can inspect the resulting state rather
    than mocking every method chain.
    """
    from hermes_otel.plugin_config import HermesOtelConfig
    from hermes_otel.session_state import SessionState

    tracer = MagicMock()
    tracer.is_enabled = True
    tracer.spans = MagicMock()
    tracer.spans._active_spans = {}
    tracer.sessions = SessionState()
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
            "session_count",
            1,
            {
                "gen_ai.agent.name": "hermes-agent",
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.provider.name": "cli",
                "gen_ai.request.model": "gpt-4",
            },
        )

    def test_includes_session_attributes(self, mock_tracer, monkeypatch):
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        on_session_start(session_id="s1", model="gpt-4o", platform="telegram")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["correlation.id"] == "s1"
        assert attrs["gen_ai.conversation.id"] == "s1"
        assert attrs["gen_ai.agent.name"] == "hermes-agent"
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.request.model"] == "gpt-4o"
        assert attrs["gen_ai.provider.name"] == "telegram"

    def test_incoming_correlation_id_wins(self, mock_tracer):
        on_session_start(
            session_id="s1",
            model="gpt-4o",
            platform="telegram",
            correlation_id="corr-123",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["correlation.id"] == "corr-123"
        assert mock_tracer.sessions.peek("s1").correlation_id == "corr-123"

    def test_includes_cron_job_id(self, mock_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli", job_id="j123")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["hermes.cron.job_id"] == "j123"

    def test_agent_name_uses_profile_context(self, mock_tracer, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "/home/test/.hermes/profiles/engineer")
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["gen_ai.agent.name"] == "engineer"

    def test_agent_name_uses_default_profile_for_root_home(self, mock_tracer, monkeypatch):
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.setenv("HERMES_HOME", "/home/test/.hermes")
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["gen_ai.agent.name"] == "default"

    def test_noop_when_disabled(self, disabled_tracer):
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        disabled_tracer.start_span.assert_not_called()


class TestKanbanContextResolver:
    def test_env_only_context(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "42")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        monkeypatch.setenv("HERMES_TENANT", "hermes-otel")
        monkeypatch.setenv("HERMES_PROFILE", "engineer")

        attrs = _kanban_context_from_kwargs_env({})

        assert attrs == {
            "hermes.kanban.flow.kind": "worker",
            "hermes.kanban.task.id": "t_123",
            "hermes.kanban.run.id": "42",
            "hermes.kanban.board": "default",
            "hermes.kanban.tenant": "hermes-otel",
            "hermes.kanban.assignee": "engineer",
        }

    def test_kwargs_win_over_env(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "env-task")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "env-run")
        monkeypatch.setenv("HERMES_PROFILE", "env-profile")

        attrs = _kanban_context_from_kwargs_env(
            {
                "kanban_task_id": "kw-task",
                "kanban_run_id": "kw-run",
                "kanban_board": "board-a",
                "kanban_assignee": "researcher",
                "kanban_flow_kind": "recovery",
                "kanban_source_kind": "telegram",
            }
        )

        assert attrs["hermes.kanban.task.id"] == "kw-task"
        assert attrs["hermes.kanban.run.id"] == "kw-run"
        assert attrs["hermes.kanban.board"] == "board-a"
        assert attrs["hermes.kanban.assignee"] == "researcher"
        assert attrs["hermes.kanban.flow.kind"] == "recovery"
        assert attrs["hermes.kanban.source.kind"] == "telegram"

    def test_generic_hook_kwargs_do_not_imply_kanban_context(self, clean_kanban_env):
        attrs = _kanban_context_from_kwargs_env(
            {"task_id": "api-call-1", "run_id": "generic-run", "tenant": "project"}
        )
        assert attrs == {}

    def test_non_kanban_session_omits_context(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_PROFILE", "engineer")
        assert _kanban_context_from_kwargs_env({}) == {}

    def test_tenant_alone_does_not_imply_kanban_context(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_TENANT", "hermes-otel")
        monkeypatch.setenv("HERMES_PROFILE", "engineer")
        assert _kanban_context_from_kwargs_env({}) == {}

    def test_malformed_values_are_omitted(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "   ")
        assert _kanban_context_from_kwargs_env({"kanban_task_id": None}) == {}

    def test_long_whitespace_values_are_omitted(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", " " * 500)
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "\t" * 500)
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "\n" * 500)
        assert _kanban_context_from_kwargs_env({}) == {}

    def test_uses_only_canonical_dotted_keys(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
        attrs = _kanban_context_from_kwargs_env({})
        assert all(key.startswith("hermes.kanban.") for key in attrs)
        assert "kanban_task_id" not in attrs
        assert "hermes_kanban_task_id" not in attrs

    def test_canonical_concepts_are_not_dual_emitted(self, clean_kanban_env, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "43")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        attrs = _kanban_context_from_kwargs_env({})
        forbidden_aliases = {
            "kanban_task_id",
            "hermes_kanban_task_id",
            "kanban_run_id",
            "hermes_kanban_run_id",
            "kanban_board",
            "hermes_kanban_board",
        }
        assert forbidden_aliases.isdisjoint(attrs)


class TestOnSessionEnd:
    def test_pops_parent_and_ends_span(self, mock_tracer):
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        call_args = mock_tracer.end_span.call_args
        assert call_args[0][0] == "session:s1"

    def test_status_ok_when_completed(self, mock_tracer):
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "ok"

    def test_status_ok_when_interrupted(self, mock_tracer):
        on_session_end(
            session_id="s1", completed=False, interrupted=True, model="gpt-4", platform="cli"
        )
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "ok"

    def test_status_error_when_neither(self, mock_tracer):
        on_session_end(
            session_id="s1", completed=False, interrupted=False, model="gpt-4", platform="cli"
        )
        call_kwargs = mock_tracer.end_span.call_args[1]
        assert call_kwargs["status"] == "error"

    def test_rolls_up_session_usage(self, mock_tracer):
        ps = mock_tracer.sessions.get_or_create("s1")
        ps.usage.update(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_tokens": 20,
                "cache_write_tokens": 10,
            }
        )
        ps.usage_updated = True
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50
        assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 10
        # Verify cleanup — PerSession popped from registry.
        assert mock_tracer.sessions.peek("s1") is None

    def test_rolls_up_session_io(self, mock_tracer):
        ps = mock_tracer.sessions.get_or_create("s1")
        ps.io = {"input": "hello", "output": "world"}
        ps.io_captured = True
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["input.value"] == "hello"
        assert attrs["output.value"] == "world"
        assert mock_tracer.sessions.peek("s1") is None

    def test_noop_when_disabled(self, disabled_tracer):
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
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
        assert attrs["gen_ai.tool.name"] == "bash"
        assert attrs["gen_ai.tool.type"] == "function"
        assert attrs["gen_ai.tool.call.id"] == "t1"
        assert '"cmd"' in attrs["input.value"]

    def test_full_mcp_args_follow_preview_privacy_gate(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(
            capture_previews=False,
            capture_full_prompts=True,
        )
        on_pre_tool_call(
            tool_name="mcp_honeycomb_run_query",
            args={"secret": "token"},
            task_id="t1",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert "input.value" not in attrs
        assert "gen_ai.tool.call.arguments" not in attrs

    def test_records_start_time(self, mock_tracer):
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        assert mock_tracer.sessions.has_tool_start("bash:t1")

    def test_noop_when_disabled(self, disabled_tracer):
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        disabled_tracer.start_span.assert_not_called()


class TestOnPostToolCall:
    def test_ends_tool_span(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="output", task_id="t1")
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "bash:t1"

    def test_sets_output_attribute(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="file.txt", task_id="t1")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["output.value"] == "file.txt"

    def test_full_mcp_result_follows_preview_privacy_gate(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(
            capture_previews=False,
            capture_full_responses=True,
        )
        mock_tracer.sessions.record_tool_start("mcp_honeycomb_run_query:t1", 1000.0)
        on_post_tool_call(
            tool_name="mcp_honeycomb_run_query",
            args={},
            result='{"secret": "token"}',
            task_id="t1",
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "output.value" not in attrs
        assert "gen_ai.tool.call.result" not in attrs

    def test_status_ok_on_success(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        kw = mock_tracer.end_span.call_args[1]
        assert kw["status"] == "ok"

    def test_status_error_on_error_result(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result='{"error": "boom"}', task_id="t1")
        kw = mock_tracer.end_span.call_args[1]
        assert kw["status"] == "error"
        assert "boom" in (kw.get("error_message") or "")
        assert kw["attributes"]["error.type"] == "tool_error"

    def test_uses_explicit_error_type_when_present(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(
            tool_name="bash",
            args={},
            result='{"error": "boom", "error_type": "RateLimitError"}',
            task_id="t1",
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["error.type"] == "RateLimitError"

    def test_records_tool_duration_metric(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        mock_tracer.record_metric.assert_called_once()
        name, _value, attrs = mock_tracer.record_metric.call_args[0]
        assert name == "gen_ai.execute_tool.duration"
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == "bash"
        assert "gen_ai.conversation.id" not in attrs
        assert "gen_ai.provider.name" not in attrs

    def test_cleans_up_start_time(self, mock_tracer):
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        assert not mock_tracer.sessions.has_tool_start("bash:t1")

    def test_noop_when_disabled(self, disabled_tracer):
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        disabled_tracer.end_span.assert_not_called()


class TestOnPreLlmCall:
    def test_creates_llm_span(self, mock_tracer):
        # Pre-populate the session span so lazy-create is skipped (normal
        # first-turn flow: on_session_start runs before on_pre_llm_call).
        mock_tracer.spans._active_spans["session:s1"] = MagicMock()

        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        mock_tracer.start_span.assert_called_once()
        kw = mock_tracer.start_span.call_args[1]
        assert kw["name"] == "llm.gpt-4"
        assert kw["key"] == "llm:s1"
        assert kw["kind"] == "llm"
        assert kw["attributes"]["correlation.id"] == "s1"

    def test_reuses_session_correlation_id_on_child_span(self, mock_tracer):
        mock_tracer.spans._active_spans["session:s1"] = MagicMock()
        mock_tracer.sessions.get_or_create("s1").correlation_id = "corr-123"

        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["correlation.id"] == "corr-123"

    def test_session_state_uses_full_session_id_for_correlation(self, mock_tracer):
        long_session_id = "s" * 250
        mock_tracer.spans._active_spans[f"session:{long_session_id}"] = MagicMock()

        on_pre_llm_call(
            session_id=long_session_id,
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
            correlation_id="corr-123",
        )

        assert mock_tracer.sessions.peek(long_session_id).correlation_id == "corr-123"
        assert mock_tracer.sessions.peek(long_session_id[:200]) is None

    def test_pushes_parent(self, mock_tracer):
        mock_tracer.spans._active_spans["session:s1"] = MagicMock()

        span = MagicMock()
        mock_tracer.start_span.return_value = span
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        mock_tracer.spans.push_parent.assert_called_once_with(span, session_id="s1")

    def test_lazy_creates_session_span_on_continuation_turn(self, mock_tracer):
        """Turn 2+ has no active session span — hooks.py synthesizes one."""
        # No session span in _active_spans → lazy-create path fires.
        on_pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=False,
            model="gpt-4",
            platform="cli",
        )
        # Two start_span calls: agent (synthesized) + llm.gpt-4
        assert mock_tracer.start_span.call_count == 2
        first_kw = mock_tracer.start_span.call_args_list[0][1]
        assert first_kw["name"] == "agent"
        assert first_kw["key"] == "session:s1"
        assert first_kw["attributes"].get("hermes.session.synthesized") is True

    def test_captures_first_input_in_session_io(self, mock_tracer):
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        assert mock_tracer.sessions.peek("s1").io["input"] == "hello"

    def test_does_not_overwrite_existing_session_io(self, mock_tracer):
        ps = mock_tracer.sessions.get_or_create("s1")
        ps.io = {"input": "first", "output": ""}
        ps.io_captured = True
        on_pre_llm_call(
            session_id="s1",
            user_message="second",
            conversation_history=[],
            is_first_turn=False,
            model="gpt-4",
            platform="cli",
        )
        assert mock_tracer.sessions.peek("s1").io["input"] == "first"

    def test_returns_none(self, mock_tracer):
        result = on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        assert result is None

    def test_sender_id_not_captured_by_default(self, mock_tracer):
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="discord",
            sender_id="123456789012345678",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert "hermes.sender.id" not in attrs
        assert "user.id" not in attrs
        assert mock_tracer.sessions.peek("s1").sender_id == ""

    def test_sender_id_captured_as_platform_prefixed_user_id_when_enabled(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_sender_id=True)
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="discord",
            sender_id="123456789012345678",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["hermes.sender.id"] == "123456789012345678"
        assert attrs["user.id"] == "discord:123456789012345678"
        ps = mock_tracer.sessions.peek("s1")
        assert ps.sender_id == "123456789012345678"
        assert ps.user_id == "discord:123456789012345678"

    def test_empty_sender_id_is_ignored_when_enabled(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_sender_id=True)
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
            sender_id="",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert "hermes.sender.id" not in attrs
        assert "user.id" not in attrs
        assert mock_tracer.sessions.peek("s1").sender_id == ""

    def test_noop_when_disabled(self, disabled_tracer):
        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        disabled_tracer.start_span.assert_not_called()


class TestOnPostLlmCall:
    def test_pops_parent_and_ends_span(self, mock_tracer):
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "llm:s1"

    def test_captures_last_output_in_session_io(self, mock_tracer):
        ps = mock_tracer.sessions.get_or_create("s1")
        ps.io = {"input": "hello", "output": ""}
        ps.io_captured = True
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="goodbye",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        assert mock_tracer.sessions.peek("s1").io["output"] == "goodbye"

    def test_records_message_count_metric(self, mock_tracer):
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        mock_tracer.record_metric.assert_called_once_with(
            "message_count",
            1,
            {
                "gen_ai.agent.name": "hermes-agent",
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "cli",
                "gen_ai.request.model": "gpt-4",
            },
        )

    def test_noop_when_disabled(self, disabled_tracer):
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        disabled_tracer.end_span.assert_not_called()


class TestPerCategoryPreviewCaps:
    """preview_max_chars is the sole governor; per-category fields override it."""

    def test_tool_input_cap_governs(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(tool_input_preview_max_chars=10)
        on_pre_tool_call(tool_name="bash", args={"cmd": "x" * 200}, task_id="t1")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert len(attrs["input.value"]) <= 10

    def test_tool_output_cap_governs(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(tool_output_preview_max_chars=15)
        mock_tracer.sessions.record_tool_start("bash:t1", 1000.0)
        on_post_tool_call(tool_name="bash", args={}, result="y" * 200, task_id="t1")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert len(attrs["output.value"]) <= 15

    def test_llm_input_cap_governs(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(llm_input_preview_max_chars=20)
        on_pre_llm_call(
            session_id="s1",
            user_message="u" * 300,
            conversation_history=[],
            model="m",
            platform="cli",
            is_first_turn=True,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert len(attrs["input.value"]) <= 20

    def test_llm_output_cap_governs(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(llm_output_preview_max_chars=12)
        on_post_llm_call(
            session_id="s1",
            user_message="hi",
            assistant_response="r" * 300,
            conversation_history=[],
            model="m",
            platform="cli",
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert len(attrs["output.value"]) <= 12

    def test_preview_max_chars_fallback_when_specific_unset(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(preview_max_chars=25)
        on_pre_tool_call(tool_name="bash", args={"cmd": "z" * 200}, task_id="t2")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert len(attrs["input.value"]) <= 25

    def test_specific_cap_exceeds_global(self, mock_tracer):
        """A per-category cap larger than preview_max_chars is honored."""
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(
            preview_max_chars=50, tool_output_preview_max_chars=5000
        )
        mock_tracer.sessions.record_tool_start("bash:t3", 1000.0)
        long_result = "w" * 200
        on_post_tool_call(tool_name="bash", args={}, result=long_result, task_id="t3")
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        # Should be 200 (full), not clipped to 50
        assert len(attrs["output.value"]) == 200


class TestOnPreApiRequest:
    def test_creates_api_span(self, mock_tracer):
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="https://api.openai.com",
            api_mode="chat",
            api_call_count=1,
            message_count=5,
            tool_count=2,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
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
            max_tokens=0,
        )
        mock_tracer.spans.push_parent.assert_called_once_with(span, session_id="s1")

    def test_includes_metadata_attributes(self, mock_tracer):
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        # Hermes-specific request-shape metadata stays under hermes.* because
        # there is no equivalent GenAI semantic-convention field.
        assert attrs["hermes.api.mode"] == "chat"
        assert attrs["hermes.request.message_count"] == 10
        assert attrs["hermes.request.approx_input_tokens"] == 500
        assert attrs["gen_ai.request.max_tokens"] == 2048
        assert attrs["gen_ai.conversation.id"] == "s1"
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.request.model"] == "gpt-4"
        assert attrs["gen_ai.provider.name"] == "openai"

    def test_includes_server_attributes_from_base_url(self, mock_tracer):
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="https://api.openai.com:8443/v1",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["server.address"] == "api.openai.com"
        assert attrs["server.port"] == 8443

    def test_emits_inference_details_event_without_prompt_payload(self, mock_tracer):
        span = MagicMock()
        mock_tracer.start_span.return_value = span
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
            messages=[{"role": "user", "content": "secret"}],
        )
        event_name, event_attrs = span.add_event.call_args[0]
        assert event_name == "gen_ai.client.inference.operation.details"
        assert event_attrs["gen_ai.operation.name"] == "chat"
        assert event_attrs["gen_ai.request.model"] == "gpt-4"
        assert "gen_ai.input.messages" not in event_attrs

    def test_inference_details_event_excludes_full_prompt_payload_when_enabled(
        self, mock_tracer
    ):
        from hermes_otel.plugin_config import HermesOtelConfig

        span = MagicMock()
        mock_tracer.start_span.return_value = span
        mock_tracer.config = HermesOtelConfig(capture_full_prompts=True)
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
            messages=[{"role": "user", "content": "secret"}],
            system_prompt="private system prompt",
        )
        event_attrs = span.add_event.call_args[0][1]
        assert "gen_ai.input.messages" not in event_attrs
        assert "gen_ai.system_instructions" not in event_attrs

    def test_includes_standard_request_params(self, mock_tracer):
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
            temperature=0.2,
            top_p=0.9,
            stream=True,
            reasoning_effort="high",
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["gen_ai.request.max_tokens"] == 2048
        assert attrs["gen_ai.request.temperature"] == 0.2
        assert attrs["gen_ai.request.top_p"] == 0.9
        assert attrs["gen_ai.request.stream"] is True
        assert attrs["gen_ai.request.reasoning.level"] == "high"

    def test_includes_session_user_id_when_available(self, mock_tracer):
        ps = mock_tracer.sessions.get_or_create("s1")
        ps.sender_id = "U0B074344DP"
        ps.user_id = "slack:U0B074344DP"
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="slack",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=10,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=2048,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["hermes.sender.id"] == "U0B074344DP"
        assert attrs["user.id"] == "slack:U0B074344DP"

    def test_noop_when_disabled(self, disabled_tracer):
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
            max_tokens=0,
        )
        disabled_tracer.start_span.assert_not_called()


class TestOnApiRequestError:
    def test_ends_api_span_with_error_type(self, mock_tracer):
        error = TimeoutError("request timed out")
        on_api_request_error(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_mode="chat",
            error=error,
        )
        mock_tracer.spans.pop_parent.assert_called_once_with(session_id="s1")
        kw = mock_tracer.end_span.call_args[1]
        attrs = kw["attributes"]
        assert kw["status"] == "error"
        assert kw["error_message"] == "request timed out"
        assert attrs["error.type"] == "TimeoutError"
        assert attrs["server.address"] == "api.openai.com"
        assert attrs["gen_ai.operation.name"] == "chat"


class TestOnPostApiRequest:
    def _call_post_api(self, mock_tracer, usage=None, **overrides):
        defaults = dict(
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
            usage=usage or {},
            assistant_content_chars=100,
            assistant_tool_call_count=0,
        )
        defaults.update(overrides)
        on_post_api_request(**defaults)

    def test_pops_parent_and_ends_span(self, mock_tracer):
        self._call_post_api(mock_tracer)
        mock_tracer.spans.pop_parent.assert_called_once()
        mock_tracer.end_span.assert_called_once()
        assert mock_tracer.end_span.call_args[0][0] == "api:t1"

    def test_canonical_gen_ai_token_attributes(self, mock_tracer):
        usage = {
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }
        self._call_post_api(mock_tracer, usage=usage)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50
        assert "gen_ai.usage.total_tokens" not in attrs
        assert attrs["gen_ai.conversation.id"] == "s1"
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.response.model"] == "gpt-4"
        assert attrs["gen_ai.provider.name"] == "openai"

    def test_reasoning_token_attribute(self, mock_tracer):
        usage = {
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 170,
            "output_tokens_details": {"reasoning_tokens": 20},
        }
        self._call_post_api(mock_tracer, usage=usage)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.usage.reasoning.output_tokens"] == 20
        assert mock_tracer.sessions.peek("s1").usage["reasoning_output_tokens"] == 20

    def test_compaction_attr_only_when_explicit(self, mock_tracer):
        self._call_post_api(mock_tracer, conversation_compacted=True)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.conversation.compacted"] is True

    def test_no_compaction_attr_when_unknown(self, mock_tracer):
        self._call_post_api(mock_tracer)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "gen_ai.conversation.compacted" not in attrs

    def test_no_compaction_attr_when_explicit_false(self, mock_tracer):
        self._call_post_api(mock_tracer, conversation_compacted=False)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "gen_ai.conversation.compacted" not in attrs

    def test_time_to_first_chunk_attribute(self, mock_tracer):
        self._call_post_api(mock_tracer, time_to_first_chunk_ms=125)
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.response.time_to_first_chunk"] == 0.125

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
        assert attrs["gen_ai.usage.cache_read.input_tokens"] == 30
        assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 15

    def test_session_usage_rollup(self, mock_tracer):
        usage = {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._call_post_api(mock_tracer, usage=usage)
        ps = mock_tracer.sessions.peek("s1")
        assert ps.usage["prompt_tokens"] == 100
        assert ps.usage["completion_tokens"] == 50
        assert ps.usage["total_tokens"] == 150
        assert ps.usage_updated is True

    def test_session_usage_accumulates(self, mock_tracer):
        usage = {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._call_post_api(mock_tracer, usage=usage)
        self._call_post_api(mock_tracer, usage=usage, task_id="t2")
        assert mock_tracer.sessions.peek("s1").usage["prompt_tokens"] == 200

    def test_records_duration_attribute(self, mock_tracer):
        self._call_post_api(mock_tracer, api_duration=1.234)
        metric_names = [c[0][0] for c in mock_tracer.record_metric.call_args_list]
        assert "gen_ai.client.operation.duration" in metric_names

    def test_records_canonical_gen_ai_token_metrics(self, mock_tracer):
        usage = {
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cache_read_tokens": 30,
            "cache_write_tokens": 15,
        }
        self._call_post_api(mock_tracer, usage=usage)
        metric_calls = [c for c in mock_tracer.record_metric.call_args_list]
        metric_names = [c[0][0] for c in metric_calls]
        assert "gen_ai.client.token.usage" in metric_names
        assert "model_usage" in metric_names
        token_calls = [c for c in metric_calls if c[0][0] == "gen_ai.client.token.usage"]
        assert {c[0][2]["gen_ai.token.type"] for c in token_calls} == {"input", "output"}
        token_call = token_calls[0]
        assert token_call[0][2]["gen_ai.token.type"] == "input"
        assert token_call[0][2]["gen_ai.operation.name"] == "chat"
        assert token_call[0][2]["gen_ai.provider.name"] == "openai"

    def test_noop_when_disabled(self, disabled_tracer):
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
            usage={},
            assistant_content_chars=100,
            assistant_tool_call_count=0,
        )
        disabled_tracer.end_span.assert_not_called()


class TestFullCaptureFlags:
    """capture_full_prompts / capture_full_responses config flags."""

    def _pre_kwargs(self, **extra):
        base = dict(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=0,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=0,
        )
        base.update(extra)
        return base

    def _post_kwargs(self, **extra):
        base = dict(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.1,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4",
            usage={},
            assistant_content_chars=5,
            assistant_tool_call_count=0,
        )
        base.update(extra)
        return base

    def test_pre_skips_prompt_attrs_when_flag_off(self, mock_tracer):
        on_pre_api_request(
            **self._pre_kwargs(
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="you are helpful",
            )
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert "gen_ai.input.messages" not in attrs
        assert "gen_ai.system_instructions" not in attrs
        assert "input.value" not in attrs

    def test_pre_writes_full_prompt_when_flag_on(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_full_prompts=True)
        huge = "x" * 5000  # well past preview_max_chars (1200)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": huge},
        ]
        on_pre_api_request(**self._pre_kwargs(messages=messages, system_prompt="the-system-prompt"))
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["gen_ai.system_instructions"] == "the-system-prompt"
        assert attrs["input.mime_type"] == "application/json"
        # Full, untruncated payload round-trips
        import json as _json

        parsed = _json.loads(attrs["gen_ai.input.messages"])
        assert parsed == messages
        assert _json.loads(attrs["gen_ai.input.messages"]) == messages
        assert len(attrs["input.value"]) > 5000

    def test_pre_handles_empty_messages(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_full_prompts=True)
        on_pre_api_request(**self._pre_kwargs(messages=[], system_prompt=""))
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert "gen_ai.input.messages" not in attrs
        assert "gen_ai.system_instructions" not in attrs

    def test_post_skips_response_attrs_when_flag_off(self, mock_tracer):
        on_post_api_request(
            **self._post_kwargs(
                response_content="the full response",
                response_tool_calls=[],
            )
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "gen_ai.output.messages" not in attrs
        assert "output.value" not in attrs

    def test_post_writes_full_response_when_flag_on(self, mock_tracer):
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_full_responses=True)
        big_response = "answer " * 500  # > preview_max_chars
        on_post_api_request(
            **self._post_kwargs(response_content=big_response, response_tool_calls=[])
        )
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "gen_ai.output.messages" in attrs
        assert attrs["output.value"] == big_response
        assert attrs["output.mime_type"] == "text/plain"

    def test_post_serializes_simplenamespace_tool_calls(self, mock_tracer):
        from types import SimpleNamespace

        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_full_responses=True)
        tc = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="web_search", arguments='{"q":"x"}'),
        )
        on_post_api_request(**self._post_kwargs(response_content="", response_tool_calls=[tc]))
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        import json as _json

        parsed = _json.loads(attrs["gen_ai.output.messages"])
        assert parsed[0]["id"] == "call_1"
        assert parsed[0]["function"]["name"] == "web_search"
        # With no text content, the tool-call JSON stands in as output.value
        assert attrs["output.mime_type"] == "application/json"

    def test_flags_independent(self, mock_tracer):
        """Enabling one flag must not imply the other."""
        from hermes_otel.plugin_config import HermesOtelConfig

        mock_tracer.config = HermesOtelConfig(capture_full_prompts=True)
        on_post_api_request(**self._post_kwargs(response_content="hi", response_tool_calls=[]))
        attrs = mock_tracer.end_span.call_args[1]["attributes"]
        assert "gen_ai.output.messages" not in attrs


class TestKanbanSpanAttributes:
    def _set_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "43")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        monkeypatch.setenv("HERMES_TENANT", "hermes-otel")
        monkeypatch.setenv("HERMES_PROFILE", "engineer")

    def _assert_kanban_attrs(self, attrs):
        assert attrs["hermes.kanban.flow.kind"] == "worker"
        assert attrs["hermes.kanban.task.id"] == "t_123"
        assert attrs["hermes.kanban.run.id"] == "43"
        assert attrs["hermes.kanban.board"] == "default"
        assert attrs["hermes.kanban.tenant"] == "hermes-otel"
        assert attrs["hermes.kanban.assignee"] == "engineer"

    def test_session_llm_api_tool_and_error_spans_include_kanban_context(
        self, mock_tracer, clean_kanban_env, monkeypatch
    ):
        self._set_env(monkeypatch)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        self._assert_kanban_attrs(mock_tracer.start_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        self._assert_kanban_attrs(mock_tracer.start_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        self._assert_kanban_attrs(mock_tracer.end_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_pre_api_request(
            task_id="api1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=1,
            tool_count=0,
            approx_input_tokens=1,
            request_char_count=5,
            max_tokens=0,
        )
        self._assert_kanban_attrs(mock_tracer.start_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_api_request_error(
            task_id="api1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            error=RuntimeError("boom"),
        )
        self._assert_kanban_attrs(mock_tracer.end_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_post_api_request(
            task_id="api2",
            session_id="s1",
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
            usage={},
            assistant_content_chars=0,
            assistant_tool_call_count=0,
        )
        self._assert_kanban_attrs(mock_tracer.end_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1", session_id="s1")
        self._assert_kanban_attrs(mock_tracer.start_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_post_tool_call(
            tool_name="bash",
            args={},
            result="ok",
            task_id="tool1",
            session_id="s1",
        )
        self._assert_kanban_attrs(mock_tracer.end_span.call_args[1]["attributes"])
        mock_tracer.reset_mock()

        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        self._assert_kanban_attrs(mock_tracer.end_span.call_args[1]["attributes"])

    def test_session_reuses_saved_kanban_context_when_env_changes(
        self, mock_tracer, clean_kanban_env, monkeypatch
    ):
        self._set_env(monkeypatch)
        on_session_start(session_id="s1", model="gpt-4", platform="cli")

        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_999")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "99")
        mock_tracer.reset_mock()

        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1", session_id="s1")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert attrs["hermes.kanban.task.id"] == "t_123"
        assert attrs["hermes.kanban.run.id"] == "43"

    def test_non_kanban_spans_omit_kanban_context(self, mock_tracer, clean_kanban_env):
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert not any(key.startswith("hermes.kanban.") for key in attrs)

    def test_non_kanban_tool_and_api_spans_omit_kanban_context(
        self, mock_tracer, clean_kanban_env
    ):
        on_pre_tool_call(tool_name="bash", args={}, task_id="tool1", session_id="s1")
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert not any(key.startswith("hermes.kanban.") for key in attrs)
        mock_tracer.reset_mock()

        on_pre_api_request(
            task_id="api1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=1,
            tool_count=0,
            approx_input_tokens=1,
            request_char_count=5,
            max_tokens=0,
        )
        attrs = mock_tracer.start_span.call_args[1]["attributes"]
        assert not any(key.startswith("hermes.kanban.") for key in attrs)

    @pytest.mark.parametrize(
        ("callback", "event"),
        [
            (on_kanban_task_claimed, "claimed"),
            (on_kanban_task_completed, "completed"),
            (on_kanban_task_blocked, "blocked"),
        ],
    )
    def test_lifecycle_hooks_emit_short_spans(self, mock_tracer, clean_kanban_env, callback, event):
        callback(
            kanban_task_id="t_123",
            kanban_run_id="43",
            kanban_board="default",
            kanban_tenant="hermes-otel",
            kanban_assignee="engineer",
        )
        start_kwargs = mock_tracer.start_span.call_args[1]
        end_kwargs = mock_tracer.end_span.call_args[1]
        assert start_kwargs["name"] == f"kanban.task.{event}"
        assert start_kwargs["kind"] == "general"
        assert start_kwargs["attributes"]["hermes.kanban.lifecycle.event"] == event
        assert end_kwargs["attributes"]["hermes.kanban.lifecycle.event"] == event
        assert end_kwargs["status"] == "ok"
