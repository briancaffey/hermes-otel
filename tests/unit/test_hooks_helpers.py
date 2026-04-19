"""Tests for pure helper functions in hooks.py and helpers.py."""

import pytest
from hermes_otel.helpers import truncate_string
from hermes_otel.hooks import _detect_session_kind, _to_int


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
