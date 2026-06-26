"""Unit tests for API-error/retry pure helpers and fail-open handler paths."""

import pytest
from hermes_otel.helpers import coerce_bool, http_status_class, to_optional_int


class TestToOptionalInt:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (429, 429),
            (500.0, 500),
            ("503", 503),
            ("  408 ", 408),
            (None, None),
            ("", None),
            ("   ", None),
            ("nope", None),
            (True, None),  # bools are not status codes
            (False, None),
        ],
    )
    def test_parsing(self, value, expected):
        assert to_optional_int(value) == expected


class TestCoerceBool:
    @pytest.mark.parametrize("value", [True, 1, "true", "True", "1", "yes", "on"])
    def test_truthy(self, value):
        assert coerce_bool(value) is True

    @pytest.mark.parametrize("value", [False, 0, "false", "0", "no", "off"])
    def test_falsey(self, value):
        assert coerce_bool(value) is False

    @pytest.mark.parametrize("value", [None, "maybe", "x"])
    def test_undecidable_is_none(self, value):
        assert coerce_bool(value) is None


class TestHttpStatusClass:
    @pytest.mark.parametrize(
        "code,expected",
        [
            (200, "2xx"),
            (301, "3xx"),
            (404, "4xx"),
            (429, "4xx"),
            (500, "5xx"),
            (503, "5xx"),
            (None, "network"),
            (0, "network"),
            ("", "network"),
            ("not-a-code", "network"),
            (999, "other"),
            (42, "other"),
        ],
    )
    def test_classes(self, code, expected):
        assert http_status_class(code) == expected


class TestHandlerFailOpen:
    def test_noop_when_disabled(self):
        from hermes_otel.hooks import on_api_request_error

        # Tracer uninitialized (reset fixture) → is_enabled False → silent no-op.
        on_api_request_error(
            task_id="t1",
            session_id="s1",
            model="gpt-4",
            error={"type": "RateLimitError", "message": "429"},
            status_code=429,
        )

    def test_accepts_unknown_kwargs(self, inmemory_otel_setup):
        from hermes_otel.hooks import on_api_request_error

        # Forward-compat: additive payload fields must not break the handler.
        on_api_request_error(
            task_id="t1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            error={"type": "Timeout", "message": "deadline exceeded"},
            status_code=None,
            retry_count=2,
            max_retries=5,
            retryable=True,
            future_field="ignored",
            telemetry_schema_version="hermes.observer.v1",
        )
