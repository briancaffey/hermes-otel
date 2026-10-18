"""Tests for the pure helper functions of the hooks package."""

from hermes_otel.hooks._common import session_id_from_turn_id
from hermes_otel.hooks.approval import classify_approval_choice
from hermes_otel.hooks.llm import count_user_turns
from hermes_otel.hooks.session import cron_job_id_from_session, detect_session_kind
from hermes_otel.hooks.tools import (
    classify_block_provenance,
    extract_tool_result_status,
    resolve_tool_identity,
)
from hermes_otel.hooks.usage import genai_metric_dims, normalize_usage, usage_attributes


class TestSessionIdFromTurnId:
    def test_extracts_session_id_head(self):
        assert session_id_from_turn_id("20260626_x:task7:hex") == "20260626_x"

    def test_session_placeholder_is_empty(self):
        # turn_context uses "session" when the agent has no real session_id.
        assert session_id_from_turn_id("session:task:hex") == ""

    def test_no_colon_returns_empty(self):
        assert session_id_from_turn_id("nocolon") == ""

    def test_non_string_returns_empty(self):
        assert session_id_from_turn_id(None) == ""
        assert session_id_from_turn_id(12345) == ""


class TestClassifyApprovalChoice:
    def test_grants(self):
        for c in ("once", "session", "always"):
            v = classify_approval_choice(c)
            assert v == {"choice": c, "granted": True, "timed_out": False, "decided_by": ""}

    def test_deny_is_not_granted_not_timeout(self):
        assert classify_approval_choice("deny") == {
            "choice": "deny",
            "granted": False,
            "timed_out": False,
            "decided_by": "",
        }

    def test_timeout_flagged(self):
        v = classify_approval_choice("TimeOut")
        assert v["timed_out"] is True and v["granted"] is False

    def test_empty_or_none(self):
        assert classify_approval_choice(None)["choice"] == ""
        assert classify_approval_choice("")["granted"] is False

    def test_smart_approve_is_grant_by_aux_llm(self):
        v = classify_approval_choice("smart_approve")
        assert v["granted"] is True
        assert v["decided_by"] == "aux_llm"

    def test_smart_deny_not_granted(self):
        v = classify_approval_choice("smart_deny")
        assert v["granted"] is False
        assert v["decided_by"] == "aux_llm"

    def test_smart_escalate_not_granted(self):
        v = classify_approval_choice("smart_escalate")
        assert v["granted"] is False
        assert v["decided_by"] == "aux_llm"

    def test_explicit_decided_by_wins(self):
        assert classify_approval_choice("once", decided_by="aux_llm")["decided_by"] == "aux_llm"

    def test_human_choice_decided_by_empty(self):
        assert classify_approval_choice("once")["decided_by"] == ""


class TestResolveToolIdentity:
    def test_path_key_wins(self):
        t, c = resolve_tool_identity({"path": "/tmp/foo", "target": "/other"})
        assert t == "/tmp/foo"
        assert c is None

    def test_file_path_fallback(self):
        t, _ = resolve_tool_identity({"file_path": "/etc/hosts"})
        assert t == "/etc/hosts"

    def test_target_fallback(self):
        t, _ = resolve_tool_identity({"target": "widget"})
        assert t == "widget"

    def test_url_fallback(self):
        t, _ = resolve_tool_identity({"url": "https://example.com"})
        assert t == "https://example.com"

    def test_uri_fallback(self):
        t, _ = resolve_tool_identity({"uri": "file:///x"})
        assert t == "file:///x"

    def test_command_key(self):
        _, c = resolve_tool_identity({"command": "ls -la"})
        assert c == "ls -la"

    def test_cmd_fallback(self):
        _, c = resolve_tool_identity({"cmd": "pwd"})
        assert c == "pwd"

    def test_both_target_and_command(self):
        t, c = resolve_tool_identity({"path": "/x", "command": "cat"})
        assert t == "/x"
        assert c == "cat"

    def test_none_args(self):
        assert resolve_tool_identity(None) == (None, None)

    def test_non_dict_args(self):
        assert resolve_tool_identity("not-a-dict") == (None, None)

    def test_empty_values_ignored(self):
        t, c = resolve_tool_identity({"path": "  ", "command": ""})
        assert t is None
        assert c is None

    def test_non_string_values_ignored(self):
        t, c = resolve_tool_identity({"path": 42, "command": [1, 2]})
        assert t is None
        assert c is None


class TestExtractToolResultStatus:
    def test_explicit_status_string(self):
        assert extract_tool_result_status({"status": "timeout"}) == "timeout"

    def test_explicit_status_wins_over_error(self):
        assert extract_tool_result_status({"status": "blocked", "error": "x"}) == "blocked"

    def test_explicit_status_lowercased(self):
        assert extract_tool_result_status({"status": "Error"}) == "error"

    def test_error_field_maps_to_error(self):
        assert extract_tool_result_status({"error": "boom"}) == "error"

    def test_empty_error_is_ignored(self):
        assert extract_tool_result_status({"error": ""}) is None

    def test_whitespace_error_is_ignored(self):
        assert extract_tool_result_status({"error": "   "}) is None

    def test_timeout_flag(self):
        assert extract_tool_result_status({"timeout": True}) == "timeout"

    def test_blocked_flag(self):
        assert extract_tool_result_status({"blocked": True}) == "blocked"

    def test_non_dict_returns_none(self):
        assert extract_tool_result_status("ok") is None
        assert extract_tool_result_status(None) is None
        assert extract_tool_result_status(42) is None

    def test_empty_dict_returns_none(self):
        assert extract_tool_result_status({}) is None


class TestClassifyBlockProvenance:
    def test_deny_rule(self):
        msg = (
            "BLOCKED: this command matches the user-defined deny rule "
            "'*grc-app*' (approvals.deny in config.yaml)."
        )
        assert classify_block_provenance(msg) == "deny_rule"

    def test_hardline(self):
        assert classify_block_provenance("BLOCKED (hardline): fork bomb.") == "hardline"

    def test_stdin_password_guard(self):
        # The guard's message fragment, not the tool name that triggers it.
        msg = "BLOCKED: piping detected. Do not pipe passwords into the password prompt."
        assert classify_block_provenance(msg) == "stdin_password_guard"

    def test_unclassifiable_blocked_is_not_a_floor(self):
        # Over-matching was the #78 review's second finding: human denials,
        # approval timeouts and plugin/guardrail vetoes also arrive as
        # BLOCKED-prefixed text. They must NOT be attributed to a floor.
        for msg in (
            "BLOCKED: User denied this command. Do not retry.",
            "BLOCKED: Command timed out without user response.",
            "BLOCKED: notify-failed, no delivery channel.",
            "BLOCKED: gateway refused delivery.",
            "BLOCKED: something else entirely",
        ):
            assert classify_block_provenance(msg) is None, msg

    def test_ordinary_error_is_not_a_floor(self):
        assert classify_block_provenance("Command denied: flagged as dangerous.") is None

    def test_command_failure_is_not_a_floor(self):
        assert classify_block_provenance("command not found: foo") is None

    def test_empty_and_none(self):
        assert classify_block_provenance("") is None
        assert classify_block_provenance(None) is None


class TestDetectSessionKind:
    def test_explicit_session_type(self):
        assert detect_session_kind("api_server", {"session_type": "cron"}) == "cron"

    def test_explicit_session_type_session(self):
        assert detect_session_kind("api_server", {"session_type": "session"}) == "session"

    def test_origin_fallback(self):
        assert detect_session_kind("cli", {"origin": "webhook"}) == "webhook"

    def test_run_type_fallback(self):
        assert detect_session_kind("cli", {"run_type": "scheduled"}) == "scheduled"

    def test_platform_contains_cron(self):
        assert detect_session_kind("cron_runner", {}) == "cron"

    def test_source_contains_cron(self):
        assert detect_session_kind("cli", {"source": "cron_job"}) == "cron"

    def test_trigger_contains_cron(self):
        assert detect_session_kind("cli", {"trigger": "my_cron"}) == "cron"

    def test_cron_job_id_from_session(self):
        assert cron_job_id_from_session("cron_eaa1d152fa2e_20261013_210945") == "eaa1d152fa2e"
        assert cron_job_id_from_session("cron_my_job_20261013_210945") == "my_job"
        assert cron_job_id_from_session("20261013_210419_e2ebf6") == ""
        assert cron_job_id_from_session("cron_x") == ""
        assert cron_job_id_from_session(None) == ""

    def test_job_id_present(self):
        assert detect_session_kind("cli", {"job_id": "j123"}) == "cron"

    def test_cron_job_id_present(self):
        assert detect_session_kind("cli", {"cron_job_id": "cj456"}) == "cron"

    def test_default_is_session(self):
        assert detect_session_kind("cli", {}) == "session"

    def test_session_type_takes_priority(self):
        # session_type wins even when job_id is also present
        assert detect_session_kind("cron", {"session_type": "manual", "job_id": "j1"}) == "manual"


class TestNormalizeUsageReasoning:
    def test_reasoning_tokens_parsed(self):
        totals = normalize_usage({"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 15})
        assert totals["reasoning_tokens"] == 15

    def test_reasoning_absent_defaults_zero(self):
        totals = normalize_usage({"input_tokens": 100, "output_tokens": 50})
        assert totals["reasoning_tokens"] == 0

    def test_reasoning_not_added_to_total(self):
        # Reasoning is a subset of output, so it must never inflate the total.
        totals = normalize_usage({"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30})
        assert totals["total_tokens"] == 150  # 100 + 50, reasoning excluded


class TestGenAIMetricDims:
    def test_default_operation_is_chat(self):
        dims = genai_metric_dims("gpt-4", "openai")
        assert dims["gen_ai.operation.name"] == "chat"
        assert dims["gen_ai.provider.name"] == "openai"
        assert dims["gen_ai.request.model"] == "gpt-4"

    def test_response_model_and_operation_override(self):
        dims = genai_metric_dims("gpt-4", "openai", "gpt-4o", operation="invoke_agent")
        assert dims["gen_ai.operation.name"] == "invoke_agent"
        assert dims["gen_ai.response.model"] == "gpt-4o"

    def test_no_high_cardinality_keys(self):
        dims = genai_metric_dims("gpt-4", "openai", "gpt-4")
        assert "session_id" not in dims
        assert "task_id" not in dims

    def test_empty_provider_and_model_omitted(self):
        dims = genai_metric_dims("", "")
        assert dims == {"gen_ai.operation.name": "chat"}


class TestUsageAttributesReasoning:
    def test_reasoning_dual_convention_attributes(self):
        attrs = usage_attributes(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 15,
            }
        )
        # OpenInference (Phoenix) + OTel GenAI (Langfuse) spellings.
        assert attrs["llm.token_count.completion_details.reasoning"] == 15
        assert attrs["gen_ai.usage.reasoning.output_tokens"] == 15

    def test_zero_reasoning_omitted(self):
        attrs = usage_attributes(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
        assert "llm.token_count.completion_details.reasoning" not in attrs
        assert "gen_ai.usage.reasoning.output_tokens" not in attrs

    def test_missing_reasoning_key_tolerated(self):
        # usage_attributes uses .get for reasoning so older totals dicts work.
        attrs = usage_attributes(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        )
        assert "gen_ai.usage.reasoning.output_tokens" not in attrs


class TestCountUserTurns:
    def test_counts_user_messages_only(self):
        convo = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "tool", "content": "c"},
            {"role": "user", "content": "d"},
            "not a message",
        ]
        assert count_user_turns(convo) == 2
        assert count_user_turns(None) == 0
        assert count_user_turns("user") == 0
