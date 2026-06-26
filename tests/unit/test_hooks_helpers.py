"""Tests for pure helper functions in hooks.py and helpers.py."""

import pytest
from hermes_otel.helpers import truncate_string
from hermes_otel.hooks import (
    _detect_session_kind,
    _genai_metric_dims,
    _normalize_usage,
    _to_int,
    _usage_attributes,
)


class TestTruncateString:
    def test_normal_string(self):
        assert truncate_string("hello") == "hello"

    def test_integer_input(self):
        assert truncate_string(42) == "42"

    def test_none_input(self):
        assert truncate_string(None) == "None"

    def test_truncates_at_default_max(self):
        long = "x" * 1500
        result = truncate_string(long)
        assert len(result) == 1003  # 1000 + "..."
        assert result.endswith("...")

    def test_truncates_at_custom_max(self):
        result = truncate_string("abcdefghij", max_len=5)
        assert result == "abcde..."

    def test_exact_max_len_not_truncated(self):
        result = truncate_string("abcde", max_len=5)
        assert result == "abcde"

    def test_unserializable_object(self):
        class BadStr:
            def __str__(self):
                raise RuntimeError("nope")

        result = truncate_string(BadStr())
        assert result == "<unserializable>"

    def test_empty_string(self):
        assert truncate_string("") == ""


class TestToInt:
    def test_int_input(self):
        assert _to_int(42) == 42

    def test_float_input(self):
        assert _to_int(3.7) == 3

    def test_string_integer(self):
        assert _to_int("42") == 42

    def test_string_float(self):
        assert _to_int("3.7") == 3

    def test_empty_string(self):
        assert _to_int("") == 0

    def test_whitespace_string(self):
        assert _to_int("  ") == 0

    def test_none_returns_zero(self):
        assert _to_int(None) == 0

    def test_bool_true_returns_zero(self):
        assert _to_int(True) == 0

    def test_bool_false_returns_zero(self):
        assert _to_int(False) == 0

    def test_non_numeric_string(self):
        assert _to_int("abc") == 0

    def test_negative_int(self):
        assert _to_int(-5) == -5

    def test_zero(self):
        assert _to_int(0) == 0

    def test_list_returns_zero(self):
        assert _to_int([1, 2]) == 0


class TestDetectSessionKind:
    def test_explicit_session_type(self):
        assert _detect_session_kind("api_server", {"session_type": "cron"}) == "cron"

    def test_explicit_session_type_session(self):
        assert _detect_session_kind("api_server", {"session_type": "session"}) == "session"

    def test_origin_fallback(self):
        assert _detect_session_kind("cli", {"origin": "webhook"}) == "webhook"

    def test_run_type_fallback(self):
        assert _detect_session_kind("cli", {"run_type": "scheduled"}) == "scheduled"

    def test_platform_contains_cron(self):
        assert _detect_session_kind("cron_runner", {}) == "cron"

    def test_source_contains_cron(self):
        assert _detect_session_kind("cli", {"source": "cron_job"}) == "cron"

    def test_trigger_contains_cron(self):
        assert _detect_session_kind("cli", {"trigger": "my_cron"}) == "cron"

    def test_job_id_present(self):
        assert _detect_session_kind("cli", {"job_id": "j123"}) == "cron"

    def test_cron_job_id_present(self):
        assert _detect_session_kind("cli", {"cron_job_id": "cj456"}) == "cron"

    def test_default_is_session(self):
        assert _detect_session_kind("cli", {}) == "session"

    def test_session_type_takes_priority(self):
        # session_type wins even when job_id is also present
        assert _detect_session_kind("cron", {"session_type": "manual", "job_id": "j1"}) == "manual"


class TestNormalizeUsageReasoning:
    def test_reasoning_tokens_parsed(self):
        totals = _normalize_usage(
            {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 15}
        )
        assert totals["reasoning_tokens"] == 15

    def test_reasoning_absent_defaults_zero(self):
        totals = _normalize_usage({"input_tokens": 100, "output_tokens": 50})
        assert totals["reasoning_tokens"] == 0

    def test_reasoning_not_added_to_total(self):
        # Reasoning is a subset of output, so it must never inflate the total.
        totals = _normalize_usage(
            {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30}
        )
        assert totals["total_tokens"] == 150  # 100 + 50, reasoning excluded


class TestGenAIMetricDims:
    def test_default_operation_is_chat(self):
        dims = _genai_metric_dims("gpt-4", "openai")
        assert dims["gen_ai.operation.name"] == "chat"
        assert dims["gen_ai.provider.name"] == "openai"
        assert dims["gen_ai.request.model"] == "gpt-4"

    def test_response_model_and_operation_override(self):
        dims = _genai_metric_dims("gpt-4", "openai", "gpt-4o", operation="invoke_agent")
        assert dims["gen_ai.operation.name"] == "invoke_agent"
        assert dims["gen_ai.response.model"] == "gpt-4o"

    def test_no_high_cardinality_keys(self):
        dims = _genai_metric_dims("gpt-4", "openai", "gpt-4")
        assert "session_id" not in dims
        assert "task_id" not in dims

    def test_empty_provider_and_model_omitted(self):
        dims = _genai_metric_dims("", "")
        assert dims == {"gen_ai.operation.name": "chat"}


class TestUsageAttributesReasoning:
    def test_reasoning_dual_convention_attributes(self):
        attrs = _usage_attributes(
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
        attrs = _usage_attributes(
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
        # _usage_attributes uses .get for reasoning so older totals dicts work.
        attrs = _usage_attributes(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        )
        assert "gen_ai.usage.reasoning.output_tokens" not in attrs
