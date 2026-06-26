"""Integration tests for metrics recording via InMemoryMetricReader."""

import pytest
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.plugin_config import HermesOtelConfig


def _api_call(session_id="s1", model="gpt-4", provider="openai", api_duration=0.5, usage=None):
    """Fire a pre/post API request pair to drive metric recording."""
    on_pre_api_request(
        task_id="t1",
        session_id=session_id,
        platform="cli",
        model=model,
        provider=provider,
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
        session_id=session_id,
        platform="cli",
        model=model,
        provider=provider,
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=api_duration,
        finish_reason="stop",
        message_count=5,
        response_model=model,
        usage=usage or {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        assistant_content_chars=200,
        assistant_tool_call_count=0,
    )


def _points(metric):
    """All data points for a metric (histogram or counter)."""
    return list(metric.data.data_points) if metric is not None else []


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

        value = _get_metric_value(metric_reader, "hermes.token.usage")
        # 100 (input) + 50 (output) = 150
        assert value == 150

    def test_reasoning_token_type_recorded(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="o3",
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
            model="o3",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="o3",
            usage={
                "prompt_tokens": 100,
                "output_tokens": 80,
                "total_tokens": 180,
                "reasoning_tokens": 60,
            },
            assistant_content_chars=200,
            assistant_tool_call_count=0,
        )

        metric = _get_metric(metric_reader, "hermes.token.usage")
        assert metric is not None
        reasoning_points = [
            dp for dp in metric.data.data_points if dp.attributes.get("token_type") == "reasoning"
        ]
        assert len(reasoning_points) == 1
        assert reasoning_points[0].value == 60


class TestGenAISpecMetrics:
    def test_client_token_usage_dual_written(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        _api_call(usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150})

        # Custom metric still recorded.
        assert _get_metric_value(metric_reader, "hermes.token.usage") == 150

        # Spec metric (a histogram) recorded with matching input/output split.
        spec = _get_metric(metric_reader, "gen_ai.client.token.usage")
        assert spec is not None
        assert spec.unit == "{token}"
        by_type = {dp.attributes.get("gen_ai.token.type"): dp.sum for dp in _points(spec)}
        assert by_type == {"input": 100, "output": 50}

    def test_client_token_usage_dimensions_low_cardinality(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        _api_call()

        spec = _get_metric(metric_reader, "gen_ai.client.token.usage")
        for dp in _points(spec):
            keys = set(dp.attributes.keys())
            # GenAI-spec dims only — never per-call IDs like session_id.
            assert "session_id" not in keys
            assert "gen_ai.operation.name" in keys
            assert "gen_ai.provider.name" in keys
            assert "gen_ai.request.model" in keys

    def test_operation_duration_in_seconds(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        _api_call(api_duration=0.5)

        dur = _get_metric(metric_reader, "gen_ai.client.operation.duration")
        assert dur is not None
        assert dur.unit == "s"
        pts = _points(dur)
        assert len(pts) == 1
        # Recorded in seconds (0.5), NOT milliseconds.
        assert pts[0].sum == pytest.approx(0.5)
        assert pts[0].attributes.get("gen_ai.operation.name") == "chat"

    def test_tool_duration_stays_milliseconds(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")

        tool = _get_metric(metric_reader, "hermes.tool.duration")
        assert tool is not None
        # The hermes.* duration histogram keeps ms for backward compatibility.
        assert tool.unit == "ms"

    def test_agent_token_usage_on_session_end(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call(usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        agent = _get_metric(metric_reader, "gen_ai.agent.token.usage")
        assert agent is not None
        by_type = {dp.attributes.get("gen_ai.token.type"): dp.sum for dp in _points(agent)}
        assert by_type == {"input": 100, "output": 50}
        for dp in _points(agent):
            assert dp.attributes.get("gen_ai.operation.name") == "invoke_agent"

    def test_flag_disables_spec_metrics_only(self, inmemory_otel_with_metrics):
        _, metric_reader, plugin = inmemory_otel_with_metrics
        plugin.config = HermesOtelConfig(emit_genai_metrics=False)

        _api_call()

        # hermes.* still flows; gen_ai.* suppressed.
        assert _get_metric_value(metric_reader, "hermes.token.usage") == 150
        assert _get_metric(metric_reader, "gen_ai.client.token.usage") is None
        assert _get_metric(metric_reader, "gen_ai.client.operation.duration") is None


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

        value = _get_metric_value(metric_reader, "hermes.model.usage")
        assert value == 1
