"""Unit tests for API-error/retry pure helpers and fail-open handler paths."""

import pytest

from hermes_otel.hooks.llm import http_status_class


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
