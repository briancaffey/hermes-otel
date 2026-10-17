"""``common_attributes: {team: otel}`` reaches all three signals of the
in-memory pipeline (#480): every exported span, every metric point of the
plugin's instruments and every log record and event carry ``team=otel``,
while the default config exports exactly what it did before."""

import logging

import pytest

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.testing import build_inmemory_plugin, install_singleton

COMMON = {"team": "otel", "cost_center": "research"}


def _turn():
    on_session_start(session_id="s1", model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id="s1",
        user_message="hi",
        conversation_history=[],
        is_first_turn=True,
        model="gpt-4",
        platform="cli",
    )
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
        usage={"prompt_tokens": 10, "output_tokens": 5},
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )
    on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, task_id="t1", session_id="s1")
    on_post_tool_call(
        tool_name="terminal", args={"command": "ls"}, result="ok", task_id="t1", session_id="s1"
    )
    logging.getLogger("tests.common_attributes").warning("a forwarded stdlib record")
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


def _points(reader):
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name.startswith("otel.sdk."):
                    continue  # the SDK's own self-metrics, not the plugin's
                for point in metric.data.data_points:
                    yield metric.name, dict(point.attributes)


@pytest.fixture()
def three_signals(monkeypatch):
    from hermes_otel import redaction

    monkeypatch.setattr(redaction, "_hermes_resolved", True)
    monkeypatch.setattr(redaction, "_hermes_redactor", None)

    def build(**config):
        pipe = build_inmemory_plugin(
            metrics=True,
            logs=True,
            config=HermesOtelConfig(capture_logs=True, log_events=True, **config),
        )
        install_singleton(pipe.plugin)
        built.append(pipe)
        return pipe

    built = []
    try:
        yield build
    finally:
        for pipe in built:
            pipe.shutdown()


class TestAllThreeSignals:
    def test_every_span_carries_the_keys(self, three_signals):
        pipe = three_signals(common_attributes=COMMON)
        _turn()
        spans = pipe.span_exporter.get_finished_spans()
        assert {s.name for s in spans} >= {"agent", "api.gpt-4", "tool.terminal"}
        for span in spans:
            for key, value in COMMON.items():
                assert span.attributes[key] == value, span.name
            assert key not in dict(span.resource.attributes)

    def test_every_metric_point_carries_the_keys(self, three_signals):
        pipe = three_signals(common_attributes=COMMON)
        _turn()
        points = list(_points(pipe.metric_reader))
        assert {n for n, _ in points} >= {
            "hermes.session.count",
            "hermes.message.count",
            "hermes.token.usage",
            "hermes.tool.duration",
        }
        for name, attrs in points:
            for key, value in COMMON.items():
                assert attrs.get(key) == value, name
        labels = {n: a for n, a in points}
        # The series identity now includes the common keys (test_metric_identity
        # pins the default shape): a changed value would start a new series.
        assert labels["hermes.session.count"] == {
            "platform": "cli",
            "profile": "default",
            **COMMON,
        }

    def test_every_log_record_and_event_carries_the_keys(self, three_signals):
        pipe = three_signals(common_attributes=COMMON)
        _turn()
        records = [r.log_record for r in pipe.log_exporter.get_finished_logs()]
        events = {r.event_name for r in records if r.event_name}
        bodies = [r.body for r in records if not r.event_name]
        assert {"hermes.turn.start", "hermes.tool.call", "hermes.turn.end"} <= events
        assert "a forwarded stdlib record" in bodies
        for rec in records:
            for key, value in COMMON.items():
                assert rec.attributes[key] == value, (rec.event_name, rec.body)

    def test_default_config_stamps_nothing(self, three_signals):
        pipe = three_signals()
        _turn()
        for span in pipe.span_exporter.get_finished_spans():
            assert "team" not in span.attributes
        for name, attrs in _points(pipe.metric_reader):
            assert "team" not in attrs, name
        for r in pipe.log_exporter.get_finished_logs():
            assert "team" not in (r.log_record.attributes or {})
