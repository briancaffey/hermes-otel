"""Integration tests for metrics recording via InMemoryMetricReader."""

import pytest
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_tool_call,
    on_session_start,
)


def _get_metric(metric_reader, name):
    """Extract a metric by name from the InMemoryMetricReader."""
    data = metric_reader.get_metrics_data()
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return metric
    return None


def _get_metric_value(metric_reader, name):
    """Get the total value of a counter metric."""
    metric = _get_metric(metric_reader, name)
    if metric is None:
        return None
    # Sum data points
    total = 0
    for dp in metric.data.data_points:
        total += dp.value
    return total


class TestSessionCountMetric:
    def test_session_count_increments(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_session_start(session_id="s1", model="gpt-4", platform="cli")

        value = _get_metric_value(metric_reader, "hermes.session.count")
        assert value == 1

    def test_multiple_sessions(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_start(session_id="s2", model="gpt-4", platform="cli")

        value = _get_metric_value(metric_reader, "hermes.session.count")
        assert value == 2


class TestTokenUsageMetric:
    def test_token_usage_recorded(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

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

        value = _get_metric_value(metric_reader, "hermes.token.usage")
        # 100 (input) + 50 (output) = 150
        assert value == 150


class TestToolDurationMetric:
    def test_tool_duration_recorded(self, inmemory_otel_with_metrics):
        span_exporter, metric_reader, _ = inmemory_otel_with_metrics

        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")

        metric = _get_metric(metric_reader, "hermes.tool.duration")
        assert metric is not None
        # Should have at least one data point
        assert len(metric.data.data_points) > 0


class TestModelUsageMetric:
    def test_model_usage_recorded(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

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

        value = _get_metric_value(metric_reader, "hermes.model.usage")
        assert value == 1
