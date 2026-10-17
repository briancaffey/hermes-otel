"""Integration tests for metrics recording via InMemoryMetricReader."""

import pytest
from _helpers import get_metric as _get_metric
from _helpers import get_metric_value as _get_metric_value
from _helpers import metric_points as _points

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.pipeline import create_instruments
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

    # Prompt-cache counters. Fixtures mirror what Hermes actually sends
    # (agent/api_request_hooks.py:_usage_summary_for_api_request_hook):
    # ``input_tokens`` is the uncached portion and ``prompt_tokens`` is the
    # whole prompt (input + cache_read + cache_write).

    @staticmethod
    def _cache_points(metric_reader):
        tokens = {
            p.attributes["cache_result"]: p.value
            for p in _points(_get_metric(metric_reader, "hermes.prompt_cache.tokens"))
        }
        observations = {
            p.attributes["cache_result"]: p.value
            for p in _points(_get_metric(metric_reader, "hermes.prompt_cache.observations"))
        }
        return tokens, observations

    def test_prompt_cache_hit_and_miss_split_whole_prompt(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(
            usage={
                "input_tokens": 30,
                "output_tokens": 5,
                "cache_read_tokens": 70,
                "cache_write_tokens": 10,
                "prompt_tokens": 110,
                "total_tokens": 115,
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        # miss = 30 uncached + 10 written; hit + miss == prompt_tokens
        assert tokens == {"hit": 70, "miss": 40}
        assert observations == {"hit": 1}

    def test_prompt_cache_fully_cached_request_reports_100_percent(
        self, inmemory_otel_with_metrics
    ):
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(
            usage={
                "input_tokens": 0,
                "output_tokens": 5,
                "cache_read_tokens": 1000,
                "prompt_tokens": 1000,
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        assert tokens == {"hit": 1000}  # no miss point at all
        assert observations == {"hit": 1}

    def test_prompt_cache_cold_request_with_cache_write_is_an_observed_miss(
        self, inmemory_otel_with_metrics
    ):
        # Observed live (Hermes v0.21.3, claude-sonnet-4.5 via OpenRouter): the
        # first call of a session writes the cache and reads nothing. The write
        # proves the provider reports cache accounting, so this is a miss, not
        # "unknown".
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(
            usage={
                "input_tokens": 10,
                "output_tokens": 42,
                "cache_read_tokens": 0,
                "cache_write_tokens": 15409,
                "prompt_tokens": 15419,
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        assert tokens == {"miss": 15419}
        assert observations == {"miss": 1}

    def test_prompt_cache_skipped_when_provider_reports_no_cache_accounting(
        self, inmemory_otel_with_metrics
    ):
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(
            usage={
                "input_tokens": 100,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "prompt_tokens": 100,
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        assert tokens == {} and observations == {}
        # The plain token counter still records the request.
        assert _get_metric_value(metric_reader, "hermes.token.usage") == 105

    def test_prompt_cache_explicit_zero_via_available_fields_is_a_miss(
        self, inmemory_otel_with_metrics
    ):
        # Forward-compat: NousResearch/hermes-agent#108249 proposes an
        # ``available_fields`` side channel so an explicit zero can be told
        # apart from an absent field.
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(
            usage={
                "input_tokens": 100,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "prompt_tokens": 100,
                "available_fields": {"cache_read_tokens": True},
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        assert tokens == {"miss": 100}
        assert observations == {"miss": 1}

    def test_prompt_cache_session_regression_matches_token_usage(self, inmemory_otel_with_metrics):
        # Two-call session captured live on 2026-09-16 (claude-sonnet-4.5 via
        # OpenRouter). The weighted hit rate from the new counters must equal
        # cacheRead / input from the pre-existing hermes.token.usage counter.
        _, metric_reader, _ = inmemory_otel_with_metrics

        _api_call(  # cold: cache write only
            usage={
                "input_tokens": 10,
                "output_tokens": 120,
                "cache_read_tokens": 0,
                "cache_write_tokens": 15409,
                "prompt_tokens": 15419,
            }
        )
        _api_call(  # warm: 98.6% of the prompt served from cache
            usage={
                "input_tokens": 5,
                "output_tokens": 18,
                "cache_read_tokens": 15409,
                "cache_write_tokens": 212,
                "prompt_tokens": 15626,
            }
        )

        tokens, observations = self._cache_points(metric_reader)
        assert tokens == {"hit": 15409, "miss": 15419 + 217}
        assert observations == {"miss": 1, "hit": 1}

        usage = _get_metric(metric_reader, "hermes.token.usage")
        by_type = {}
        for p in _points(usage):
            by_type[p.attributes["token_type"]] = (
                by_type.get(p.attributes["token_type"], 0) + p.value
            )
        assert tokens["hit"] + tokens["miss"] == by_type["input"]
        assert tokens["hit"] == by_type["cacheRead"]
        assert tokens["hit"] / (tokens["hit"] + tokens["miss"]) == pytest.approx(0.4963, abs=1e-4)

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
            # Provider must be the LLM provider from the API call ("openai"),
            # not the session platform ("cli").
            assert dp.attributes.get("gen_ai.provider.name") == "openai"

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


class TestClaudeCodeCompat:
    """``metric_name_compat`` (#485): the reader sees the ``claude_code.*``
    twins with Claude Code's labels, the twins replace (``claude_code``) or
    accompany (``both``) their ``hermes.*`` originals, and nothing else moves.
    """

    @staticmethod
    def _switch(plugin, mode, **more):
        plugin.config = HermesOtelConfig(metric_name_compat=mode, **more)
        plugin.pipeline.instruments = create_instruments(plugin.pipeline.meter, plugin.config)

    @staticmethod
    def _labels(metric_reader, name):
        return [dict(dp.attributes) for dp in _points(_get_metric(metric_reader, name))]

    @pytest.fixture
    def priced(self, monkeypatch):
        from hermes_otel.hooks import api_call

        monkeypatch.setattr(
            api_call,
            "_estimate_cost",
            lambda *a, **k: {"status": "estimated", "source": "test", "usd": 0.25},
        )

    def test_off_exports_no_claude_code_instrument(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call()
        assert _get_metric(metric_reader, "claude_code.token.usage") is None
        assert _get_metric(metric_reader, "claude_code.session.count") is None
        assert _get_metric_value(metric_reader, "hermes.token.usage") == 150

    def test_claude_code_mode_replaces_the_four_twins(self, inmemory_otel_with_metrics, priced):
        _, metric_reader, plugin = inmemory_otel_with_metrics
        self._switch(plugin, "claude_code")
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call(
            usage={
                "prompt_tokens": 1000,
                "output_tokens": 50,
                "cache_read_tokens": 600,
                "cache_write_tokens": 100,
                "reasoning_tokens": 20,
            }
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        # The hermes.* twins are not exported ...
        for name in (
            "hermes.session.count",
            "hermes.token.usage",
            "hermes.cost.usage",
            "hermes.active_time.total",
        ):
            assert _get_metric(metric_reader, name) is None, name
        # ... the claude_code.* instruments are, with Claude Code's labels ...
        tokens = {
            dp.attributes["type"]: dp.value
            for dp in _points(_get_metric(metric_reader, "claude_code.token.usage"))
        }
        assert tokens == {"input": 300, "output": 50, "cacheRead": 600, "cacheCreation": 100}
        for labels in self._labels(metric_reader, "claude_code.token.usage"):
            assert labels["model"] == "gpt-4" and labels["query_source"] == "main"
            assert "token_type" not in labels and "provider" not in labels
            assert "session.id" not in labels  # opt-in only
        assert self._labels(metric_reader, "claude_code.session.count") == [
            {"profile": plugin.profile_name, "start_type": "fresh"}
        ]
        assert _get_metric_value(metric_reader, "claude_code.cost.usage") == 0.25
        assert self._labels(metric_reader, "claude_code.cost.usage") == [
            {"profile": plugin.profile_name, "model": "gpt-4", "query_source": "main"}
        ]
        active = _points(_get_metric(metric_reader, "claude_code.active_time.total"))
        assert len(active) == 1 and active[0].value >= 0
        assert dict(active[0].attributes) == {"profile": plugin.profile_name, "type": "cli"}
        # ... and every other hermes.* / gen_ai.* instrument is untouched.
        assert _get_metric_value(metric_reader, "hermes.model.usage") == 1
        assert _get_metric(metric_reader, "gen_ai.client.token.usage") is not None

    def test_both_mode_exports_both_families(self, inmemory_otel_with_metrics):
        _, metric_reader, plugin = inmemory_otel_with_metrics
        self._switch(plugin, "both")
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call()
        assert _get_metric_value(metric_reader, "hermes.token.usage") == 150
        assert _get_metric_value(metric_reader, "claude_code.token.usage") == 150
        assert _get_metric_value(metric_reader, "hermes.session.count") == 1
        assert _get_metric_value(metric_reader, "claude_code.session.count") == 1

    def test_claude_code_units(self, inmemory_otel_with_metrics, priced):
        _, metric_reader, plugin = inmemory_otel_with_metrics
        self._switch(plugin, "both")
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call()
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        units = {
            n: _get_metric(metric_reader, n).unit
            for n in (
                "claude_code.token.usage",
                "claude_code.cost.usage",
                "claude_code.active_time.total",
                "claude_code.session.count",
            )
        }
        assert units == {
            "claude_code.token.usage": "tokens",
            "claude_code.cost.usage": "USD",
            "claude_code.active_time.total": "s",
            "claude_code.session.count": "{session}",
        }

    def test_session_id_label_on_opt_in(self, inmemory_otel_with_metrics):
        _, metric_reader, plugin = inmemory_otel_with_metrics
        self._switch(plugin, "both", metric_name_compat_session_id=True)
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _api_call()
        for labels in self._labels(metric_reader, "claude_code.token.usage"):
            assert labels["session.id"] == "s1"
        assert self._labels(metric_reader, "claude_code.session.count")[0]["session.id"] == "s1"
        # never on the hermes.* twin
        for labels in self._labels(metric_reader, "hermes.token.usage"):
            assert "session.id" not in labels

    def test_resumed_session_counts_as_resume(self, inmemory_otel_with_metrics):
        from hermes_otel.hooks import on_pre_llm_call

        _, metric_reader, plugin = inmemory_otel_with_metrics
        self._switch(plugin, "both")
        # No on_session_start in this process: the root is synthesised on the
        # first pre_llm_call (a resumed / gateway / cron turn).
        on_pre_llm_call(
            session_id="s9",
            user_message="again",
            conversation_history=[],
            is_first_turn=False,
            model="gpt-4",
            platform="cli",
        )
        assert self._labels(metric_reader, "claude_code.session.count") == [
            {"profile": plugin.profile_name, "start_type": "resume"}
        ]
