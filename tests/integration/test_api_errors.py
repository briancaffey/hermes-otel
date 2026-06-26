"""Integration tests for API error / retry telemetry (issue #28).

A failed provider API request must close the in-flight ``api.{model}`` span as
ERROR (with a recorded exception event + retry metadata) instead of leaving it
to end OK via the orphan sweep.
"""

import pytest
from hermes_otel.hooks import (
    on_api_request_error,
    on_post_api_request,
    on_post_llm_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_session_end,
    on_session_start,
)
from opentelemetry.trace import StatusCode


def _spans_by_name(spans, name):
    return [s for s in spans if s.name == name]


def _span_by_name(spans, name):
    matches = _spans_by_name(spans, name)
    if not matches:
        raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")
    return matches[0]


def _has_exception_event(span):
    return any(e.name == "exception" for e in span.events)


def _exception_event(span):
    for e in span.events:
        if e.name == "exception":
            return e
    raise AssertionError("no exception event on span")


def _pre_api(task_id="a1", session_id="s1", model="gpt-4"):
    on_pre_api_request(
        task_id=task_id,
        session_id=session_id,
        platform="cli",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=3,
        tool_count=0,
        approx_input_tokens=100,
        request_char_count=400,
        max_tokens=1024,
    )


def _post_api_ok(task_id="a1", session_id="s1", model="gpt-4"):
    on_post_api_request(
        task_id=task_id,
        session_id=session_id,
        platform="cli",
        model=model,
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.4,
        finish_reason="stop",
        message_count=3,
        response_model=model,
        usage={"prompt_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        assistant_content_chars=20,
        assistant_tool_call_count=0,
    )


class TestInFlightSpanEndsError:
    def test_api_span_closed_as_error_with_metadata(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        _pre_api()
        on_api_request_error(
            task_id="a1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            api_duration=1.25,
            status_code=429,
            retry_count=2,
            max_retries=5,
            retryable=True,
            reason="rate limited",
            error={"type": "RateLimitError", "message": "429 Too Many Requests"},
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hi",
            assistant_response="",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        spans = exporter.get_finished_spans()
        api_spans = _spans_by_name(spans, "api.gpt-4")
        # Exactly one api span — closed by the error handler, NOT duplicated by
        # the orphan sweep.
        assert len(api_spans) == 1
        api = api_spans[0]

        assert api.status.status_code == StatusCode.ERROR
        attrs = dict(api.attributes)
        assert attrs["error.type"] == "RateLimitError"
        assert attrs["http.response.status_code"] == 429
        assert attrs["gen_ai.response.status_code"] == 429
        assert attrs["hermes.retry.count"] == 2
        assert attrs["hermes.max_retries"] == 5
        assert attrs["hermes.retryable"] is True
        assert attrs["llm.response.duration_ms"] == 1250.0

        # Exception recorded as an OTel "exception" event.
        assert _has_exception_event(api)
        ev = dict(_exception_event(api).attributes)
        assert ev["exception.type"] == "RateLimitError"
        assert "429" in ev["exception.message"]


class TestRetryThenSuccess:
    def test_errored_attempt_and_successful_attempt_under_one_turn(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        # Attempt 1 → retryable error.
        _pre_api(task_id="a1")
        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=503,
            retry_count=1,
            max_retries=5,
            retryable=True,
            error={"type": "ServiceUnavailable", "message": "503"},
        )
        # Attempt 2 → success (same task id, reused key).
        _pre_api(task_id="a1")
        _post_api_ok(task_id="a1")
        on_post_llm_call(
            session_id="s1",
            user_message="hi",
            assistant_response="ok",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        api_spans = _spans_by_name(exporter.get_finished_spans(), "api.gpt-4")
        assert len(api_spans) == 2
        statuses = {s.status.status_code for s in api_spans}
        assert StatusCode.ERROR in statuses
        assert StatusCode.OK in statuses
        # Same turn → both attempts share the LLM parent (one trace).
        assert len({s.context.trace_id for s in api_spans}) == 1


class TestFatalErrorPropagatesToRoot:
    def test_root_agent_span_carries_error_type(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        _pre_api()
        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=401,
            retryable=False,
            error={"type": "AuthenticationError", "message": "invalid api key"},
        )
        # Fatal: the turn ends incomplete.
        on_session_end(
            session_id="s1", completed=False, interrupted=False, model="gpt-4", platform="cli"
        )

        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        assert attrs["error.type"] == "AuthenticationError"
        # Incomplete turn → root is ERROR (pre-existing behavior, now annotated).
        assert agent.status.status_code == StatusCode.ERROR


class TestFallbackSpanWhenNoInFlight:
    def test_creates_visible_error_span(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        # No pre_api_request for this task → no in-flight span to close.
        on_api_request_error(
            task_id="ghost",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=500,
            retryable=False,
            error={"type": "InternalServerError", "message": "boom"},
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        spans = exporter.get_finished_spans()
        api = _span_by_name(spans, "api.gpt-4")
        assert api.status.status_code == StatusCode.ERROR
        assert dict(api.attributes)["error.type"] == "InternalServerError"
        assert _has_exception_event(api)

    def test_fallback_without_model_or_task(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        # Truly minimal payload must not raise and should still surface a span.
        on_api_request_error(error={"type": "Timeout", "message": "no response"})
        api = _span_by_name(exporter.get_finished_spans(), "api.error")
        assert api.status.status_code == StatusCode.ERROR


class TestFanOut:
    def test_errored_span_reaches_all_backends(self, two_exporter_pipeline):
        exporter_a, exporter_b, _ = two_exporter_pipeline
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        _pre_api()
        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=500,
            retryable=True,
            error={"type": "ServerError", "message": "500"},
        )
        for exp in (exporter_a, exporter_b):
            api = _span_by_name(exp.get_finished_spans(), "api.gpt-4")
            assert api.status.status_code == StatusCode.ERROR


class TestMetrics:
    def _metrics(self, metric_reader):
        data = metric_reader.get_metrics_data()
        return {
            m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }

    def test_error_and_retry_metrics(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=429,
            retry_count=1,
            retryable=True,
            error={"type": "RateLimitError", "message": "429"},
        )

        metrics = self._metrics(metric_reader)
        assert "hermes.api.error.count" in metrics
        dp = list(metrics["hermes.api.error.count"].data.data_points)[0]
        assert dp.value == 1
        assert dp.attributes["error_type"] == "RateLimitError"
        assert dp.attributes["status_class"] == "4xx"
        assert dp.attributes["retryable"] == "true"

        assert "hermes.retry.count" in metrics
        rdp = list(metrics["hermes.retry.count"].data.data_points)[0]
        assert rdp.value == 1

    def test_non_retryable_does_not_count_retry(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=400,
            retryable=False,
            error={"type": "BadRequest", "message": "400"},
        )

        metrics = self._metrics(metric_reader)
        assert "hermes.api.error.count" in metrics
        # No retry attempt was made → retry counter not emitted.
        assert "hermes.retry.count" not in metrics


class TestNetworkErrorNoStatusCode:
    def test_status_class_network(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        on_api_request_error(
            task_id="a1",
            session_id="s1",
            model="gpt-4",
            provider="openai",
            status_code=None,
            retryable=True,
            error={"type": "ConnectionError", "message": "connection refused"},
        )
        data = metric_reader.get_metrics_data()
        metrics = {
            m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        dp = list(metrics["hermes.api.error.count"].data.data_points)[0]
        assert dp.attributes["status_class"] == "network"
