"""LGTM / Tempo dashboard adapter: metrics via Prometheus, logs via Loki (#194).

Shapes recorded from grafana/otel-lgtm 0.34.0 (Prometheus 3.14, Loki 3.7.7)
and the k3s Prometheus 3.13 after real Hermes turns.
"""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard.backends import _loki, _prometheus
from hermes_otel.dashboard.backends import tempo as tp
from hermes_otel.dashboard.backends.base import LogFilter

NAMES = {
    "status": "success",
    "data": [
        "hermes_token_usage_total",
        "hermes_tool_duration_milliseconds_bucket",
        "hermes_tool_duration_milliseconds_sum",
        "up",
    ],
}
RANGE = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"model": "nvidia/nemotron-3-nano-omni"},
                "values": [[1790294400, "17078.75"], [1790298000, "0"]],
            },
            {
                "metric": {"model": "nvidia/nemotron-3-super-120b-a12b"},
                "values": [[1790294400, "29417.33"]],
            },
        ],
    },
}
STREAMS = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {
                    "scope_name": "hermes_otel",
                    "severity_text": "INFO",
                    "severity_number": "9",
                    "hermes_session_id": "20260926_103854_9e9061",
                    "service_name": "hermes-agent",
                },
                "values": [
                    [
                        "1790433574196187904",
                        "[hermes-otel] session 20260926_103854_9e9061 finalized",
                    ]
                ],
            },
            {
                "stream": {
                    "scope_name": "cli",
                    "detected_level": "warn",
                    "trace_id": "34e136ea93b0dd60798d5413b58ad69f",
                },
                "values": [
                    ["1790433574648965120", "CLI cleanup"],
                    ["1790433500000000000", "older line"],
                ],
            },
        ],
    },
}
LOGGERS = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {"metric": {"scope_name": "cli"}, "value": [1790433600, "2"]},
            {"metric": {"scope_name": "hermes_otel"}, "value": [1790433600, "6"]},
            {"metric": {}, "value": [1790433600, "9"]},
        ],
    },
}


@pytest.fixture()
def fake_http(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=10.0):
        calls.append(url)
        path = _urlparse.urlparse(url).path
        if path.endswith("/loki/api/v1/query_range"):
            return STREAMS
        if path.endswith("/loki/api/v1/query"):
            return LOGGERS
        if path.endswith("/label/__name__/values"):
            return NAMES
        if path.endswith("/api/v1/query_range"):
            return RANGE
        raise AssertionError(url)

    monkeypatch.setattr(_prometheus, "http_get_json", fake_get)
    monkeypatch.setattr(_loki, "http_get_json", fake_get)
    return calls


def _query_of(url):
    return dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))


class TestWiring:
    def test_lgtm_defaults_to_stack_ports_on_the_tempo_host(self):
        a = tp.TempoAdapter({"type": "lgtm", "endpoint": "http://lgtm.example:4318/v1/traces"})
        st = a.status()
        assert (st["metrics"], st["logs"]) == (True, True)
        assert (
            st["prometheus_url"] == "http://lgtm.example:9090"
            and st["loki_url"] == "http://lgtm.example:3100"
        )

    def test_plain_tempo_has_no_metrics_or_logs_unless_pointed_at_them(self):
        a = tp.TempoAdapter({"type": "tempo", "endpoint": "http://tempo:4318/v1/traces"})
        assert (a.supports_metrics, a.supports_logs) == (False, False)
        with pytest.raises(tp.HTTPException):
            a.metric_names(0, 1)
        b = tp.TempoAdapter(
            {
                "type": "tempo",
                "endpoint": "http://tempo:4318/v1/traces",
                "prometheus_url": "https://prom.lan/",
            }
        )
        assert (b.supports_metrics, b.supports_logs) == (
            True,
            False,
        ) and b.prometheus_url == "https://prom.lan"

    def test_off_disables_a_stack_signal(self):
        a = tp.TempoAdapter(
            {
                "type": "lgtm",
                "endpoint": "https://lgtm.lan/v1/traces",
                "query_port": 443,
                "loki_url": "off",
            }
        )
        assert a.supports_metrics is True and a.supports_logs is False


class TestPrometheus:
    def test_names_drop_histogram_buckets_and_pass_match(self, fake_http):
        a = tp.TempoAdapter(
            {
                "type": "lgtm",
                "endpoint": "http://h:4318/v1/traces",
                "metrics_match": '{__name__=~"hermes_.*"}',
            }
        )
        assert [n["name"] for n in a.metric_names(100, 200)] == [
            "hermes_token_usage_total",
            "hermes_tool_duration_milliseconds_sum",
            "up",
        ]
        q = _query_of(fake_http[-1])
        assert (q["start"], q["end"], q["match[]"]) == ("100", "200", '{__name__=~"hermes_.*"}')

    def test_counter_query_uses_increase_and_lands_on_the_grid(self, fake_http):
        a = tp.TempoAdapter({"type": "lgtm", "endpoint": "http://h:4318/v1/traces"})
        start, end = 1790294400 - 3600, 1790298000 + 3600
        out = a.metrics_query("hermes_token_usage_total", start, end, 3600, group_by="model")
        q = _query_of(fake_http[-1])
        assert q["query"] == "sum by (model) (increase(hermes_token_usage_total[3600s]))"
        assert (q["start"], q["end"], q["step"]) == (str(start), str(end), "3600")
        assert out["cumulative"] is True and out["bucketS"] == 3600 and len(out["buckets"]) == 4
        assert out["series"]["nvidia/nemotron-3-nano-omni"] == [None, 17078.75, 0.0, None]
        assert out["series"]["nvidia/nemotron-3-super-120b-a12b"] == [None, 29417.33, None, None]

    def test_gauge_and_aggregate_verbs(self):
        assert (
            _prometheus.promql_for("hermes_tool_duration_milliseconds_sum", 60, None, "sum")
            == "sum (increase(hermes_tool_duration_milliseconds_sum[60s]))"
        )
        assert (
            _prometheus.promql_for("some_gauge", 60, "gen_ai.request.model", "avg")
            == "avg by (gen_ai_request_model) (last_over_time(some_gauge[60s]))"
        )
        assert (
            _prometheus.promql_for("some_gauge", 60, None, "max")
            == "max (last_over_time(some_gauge[60s]))"
        )
        assert _prometheus.promql_for("c_total", 60, None, "last") == "sum (increase(c_total[60s]))"


class TestLoki:
    def test_logql_from_filter(self):
        f = LogFilter(
            trace_id="abc", session="s1", min_level=30, logger="hermes_otel", text='say "hi"'
        )
        assert (
            _loki.logql_for(f)
            == '{service_name=~".+"} | trace_id="abc" | hermes_session_id="s1" | scope_name="hermes_otel" | severity_number >= 13 |= "say \\"hi\\""'
        )
        assert _loki.logql_for(LogFilter(), '{job="x"}') == '{job="x"}'
        assert [_loki.otel_severity_for(l) for l in (10, 20, 25, 40, 50, 0)] == [5, 9, 9, 17, 21, 1]

    def test_search_flattens_streams_newest_first_and_honours_limit(self, fake_http):
        a = tp.TempoAdapter({"type": "lgtm", "endpoint": "http://h:4318/v1/traces"})
        logs = a.logs_search(LogFilter(text="session"), 100, 200, 2)
        q = _query_of(fake_http[-1])
        assert (q["start"], q["end"], q["limit"], q["direction"]) == (
            str(100 * 10**9),
            str(200 * 10**9),
            "2",
            "backward",
        )
        assert q["query"] == '{service_name=~".+"} |= "session"'
        assert [(l["level"], l["logger"], l["time_unix_nano"]) for l in logs] == [
            ("WARN", "cli", 1790433574648965120),
            ("INFO", "hermes_otel", 1790433574196187904),
        ]
        assert (
            logs[0]["trace_id"] == "34e136ea93b0dd60798d5413b58ad69f"
            and logs[0]["session_id"] is None
        )
        assert logs[1]["session_id"] == "20260926_103854_9e9061" and logs[1]["trace_id"] is None

    def test_loggers_count_by_scope_and_skip_unnamed(self, fake_http):
        a = tp.TempoAdapter(
            {
                "type": "lgtm",
                "endpoint": "http://h:4318/v1/traces",
                "loki_selector": '{job="hermes"}',
            }
        )
        assert a.loggers(1790430000, 1790433600) == [
            {"logger": "hermes_otel", "count": 6},
            {"logger": "cli", "count": 2},
        ]
        q = _query_of(fake_http[-1])
        assert (
            q["query"] == 'sum by (scope_name) (count_over_time({job="hermes"} [3600s]))'
            and q["time"] == "1790433600"
        )
