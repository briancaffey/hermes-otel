"""Metric series identity (#89, #79).

Metric labels stay bounded (no session ids, no free text) and every process
carries its own ``service.instance.id`` so two Hermes processes exporting to
one backend never share a series.
"""

import os
import re

from hermes_otel.helpers import package_version
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_approval_response,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_approval_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)

_FORBIDDEN_LABELS = {"session_id", "task_id", "tool_call_id", "turn_id", "pattern_key"}


def _all_points(metric_reader):
    data = metric_reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for point in metric.data.data_points:
                    yield metric.name, dict(point.attributes)


def _full_turn():
    on_session_start(session_id="s1", model="gpt-4", platform="cli")
    on_pre_llm_call(session_id="s1", user_message="hi", conversation_history=[], is_first_turn=True)
    on_pre_api_request(
        task_id="t1",
        session_id="s1",
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=10,
        request_char_count=10,
        max_tokens=10,
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
        api_duration=0.1,
        finish_reason="stop",
        message_count=1,
        response_model="gpt-4",
        usage={"prompt_tokens": 10, "output_tokens": 5, "cost": 0.001, "cache_read_tokens": 4},
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )
    on_post_llm_call(
        session_id="s1",
        user_message="hi",
        assistant_response="ok",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, task_id="t1", session_id="s1")
    on_post_tool_call(
        tool_name="terminal", args={"command": "ls"}, result="ok", task_id="t1", session_id="s1"
    )
    on_pre_approval_request(
        pattern_key="rm_rf",
        pattern_keys=["rm_rf"],
        command="rm -rf x",
        description="d",
        turn_id="s1:t1:abc",
        tool_call_id="tc1",
    )
    on_post_approval_response(
        pattern_key="rm_rf",
        pattern_keys=["rm_rf"],
        choice="once",
        turn_id="s1:t1:abc",
        tool_call_id="tc1",
    )
    on_session_end(
        session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
    )


class TestNoHighCardinalityLabels:
    def test_no_metric_carries_a_per_call_id_or_free_text(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        _full_turn()
        seen = list(_all_points(metric_reader))
        assert seen, "no metric points recorded"
        offenders = [(n, a) for n, a in seen if _FORBIDDEN_LABELS & set(a)]
        assert offenders == []

    def test_session_and_message_counters_keep_bounded_labels(self, inmemory_otel_with_metrics):
        _, metric_reader, _ = inmemory_otel_with_metrics
        _full_turn()
        labels = {n: a for n, a in _all_points(metric_reader)}
        assert labels["hermes.session.count"] == {"platform": "cli", "profile": "default"}
        assert set(labels["hermes.message.count"]) == {"model", "provider", "profile"}
        assert set(labels["hermes.approval.count"]) == {"choice", "profile"}


class TestResourceIdentity:
    def test_resource_has_process_identity(self):
        from hermes_otel.tracer import HermesOTelPlugin

        res = dict(HermesOTelPlugin()._build_resource().attributes)
        assert res["service.name"] == "hermes-agent"
        assert re.fullmatch(r"[0-9a-f-]{36}", res["service.instance.id"])
        assert res["process.pid"] == os.getpid()
        assert res.get("service.version") == package_version()
        # A plugin-dir install is not a pip distribution: the version must come
        # from the shipped plugin.yaml, never be None.
        assert re.fullmatch(r"\d+\.\d+\.\d+", res["service.version"])

    def test_user_resource_attributes_override_defaults(self):
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin(
            config=HermesOtelConfig(resource_attributes={"service.instance.id": "gateway-1"})
        )
        assert plugin._build_resource().attributes["service.instance.id"] == "gateway-1"

    def test_instance_id_is_stable_within_a_process(self):
        from hermes_otel import tracer

        assert re.fullmatch(r"[0-9a-f-]{36}", tracer._SERVICE_INSTANCE_ID)
