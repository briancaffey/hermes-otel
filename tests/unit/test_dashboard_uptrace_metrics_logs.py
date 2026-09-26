"""Uptrace 2.x dashboard adapter: traces, metrics and logs over /internal/v1 (#194).

Shapes recorded from Uptrace v2.0.2 after real Hermes turns.
"""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard.backends import uptrace as up
from hermes_otel.dashboard.backends.base import LogFilter, StructuredFilter

CFG = {
    "type": "uptrace",
    "endpoint": "https://uptrace.lan/v1/traces",
    "query_port": 443,
    "user_token": "u",
}

AGENT = {
    "id": "1c25137352",
    "traceId": "03868e38b99ed06e70d95b46b1a57b82",
    "projectId": 1,
    "type": "funcs",
    "system": "funcs",
    "kind": "internal",
    "name": "agent",
    "displayName": "agent",
    "time": 1790296506235.509,
    "duration": 51161.67,
    "statusCode": "ok",
    "attrs": {
        "service_name": "hermes-agent",
        "hermes_session_id": "20260924_203458_4a4dea",
        "llm_model_name": "nvidia/nemotron-3-nano-omni",
    },
}
API = {
    **AGENT,
    "id": "1a51f5bdfd",
    "parentId": "916d5769b2",
    "name": "api.nvidia/nemotron-3-nano-omni",
    "displayName": "api.nvidia/nemotron-3-nano-omni",
    "duration": 50829.313,
    "attrs": {
        "service_name": "hermes-agent",
        "gen_ai_usage_input_tokens": 13247,
        "gen_ai_request_model": "nvidia/nemotron-3-nano-omni",
    },
}
LOG = {
    "id": "0",
    "traceId": "18d8e4f42830cef80039c4d8c5aff4db",
    "standalone": True,
    "type": "log",
    "system": "log:info",
    "kind": "internal",
    "name": "",
    "eventName": "log",
    "displayName": "[hermes-otel] session 20260924_203458_4a4dea finalized",
    "time": 1790432584864.811,
    "duration": 0,
    "statusCode": "unset",
    "attrs": {
        "log_severity": "INFO",
        "otel_library_name": "hermes_otel",
        "hermes_session_id": "20260924_203458_4a4dea",
        "service_name": "hermes-agent",
    },
}
LOG_IN_SPAN = {
    **LOG,
    "standalone": False,
    "traceId": "03868e38b99ed06e70d95b46b1a57b82",
    "system": "log:warn",
    "attrs": {"otel_library_name": "cli"},
}
CATALOG = {
    "metrics": [
        {"name": "hermes_token_usage", "instrument": "counter", "unit": "{token}"},
        {"name": "hermes_tool_duration", "instrument": "histogram", "unit": "ms"},
        {"name": "gen_ai_client_token_usage", "instrument": "histogram"},
    ]
}
TIMESERIES = {
    "columns": [],
    "hasMore": False,
    "query": [
        {
            "disabled": False,
            "error": "",
            "id": "1",
            "query": "$m group by model",
            "type": "selector",
        }
    ],
    "timeseries": [
        {
            "name": "x+nvidia/nemotron-3-nano-omni",
            "metric": "$m group by model",
            "unit": "{token}",
            "attrs": {"fingerprint": 249464673053286, "model": "nvidia/nemotron-3-nano-omni"},
            "time": [1790294400000, 1790298000000],
            "value": [26748, None],
        },
        {
            "name": "y+nvidia/nemotron-3-super",
            "metric": "$m group by model",
            "unit": "{token}",
            "attrs": {"fingerprint": 2, "model": "nvidia/nemotron-3-super"},
            "time": [1790294400000, 1790298000000],
            "value": [42342, 100],
        },
    ],
}
QUERY_ERROR = {
    "query": [{"error": 'counter instrument does not support "avg"', "type": "selector"}],
    "timeseries": [],
}
LOGGERS = {
    "hasMore": False,
    "items": [
        {"value": "aiohttp.access", "count": 24712},
        {"value": "hermes_otel", "count": 26},
        {"value": "", "count": 3},
    ],
}


@pytest.fixture()
def fake_http(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=10.0):
        calls.append((url, headers))
        parsed = _urlparse.urlparse(url)
        q = _urlparse.parse_qs(parsed.query)
        if parsed.path.endswith("/traces/03868e38b99ed06e70d95b46b1a57b82/spans"):
            return {"id": "03868e38b99ed06e70d95b46b1a57b82", "spans": [AGENT, API]}
        if parsed.path.endswith("/tracing/1/spans"):
            if any(s.startswith("log:") for s in q.get("system", [])):
                return {"count": 2, "spans": [LOG, LOG_IN_SPAN]}
            return {"count": 2, "spans": [API, AGENT]}
        if parsed.path.endswith("/tracing/1/attributes/otel_library_name"):
            return LOGGERS
        if parsed.path.endswith("/metrics/1"):
            return CATALOG
        if parsed.path.endswith("/metrics/1/timeseries"):
            return (
                QUERY_ERROR
                if "avg($m)" in q["query"][0] and q["metric"][0] == "hermes_token_usage"
                else TIMESERIES
            )
        raise AssertionError(url)

    monkeypatch.setattr(up, "http_get_json", fake_get)
    return calls


def _params(url):
    return _urlparse.parse_qs(_urlparse.urlparse(url).query)


@pytest.fixture()
def adapter():
    return up.UptraceAdapter(CFG)


class TestSetup:
    def test_query_url_token_and_status(self, adapter):
        st = adapter.status()
        assert st["query_url"] == "https://uptrace.lan:443" and st["project_id"] == 1
        assert (st["metrics"], st["logs"], st["auth_required"]) == (True, True, False)

    def test_dsn_host_is_the_fallback_and_project_token_is_not_auth(self, monkeypatch):
        monkeypatch.delenv("UPTRACE_USER_TOKEN", raising=False)
        a = up.UptraceAdapter(
            {"type": "uptrace", "dsn": "http://project_secret@uptrace:14318?grpc=14317"}
        )
        assert (
            a.query_url == "http://uptrace:14318"
            and a.token is None
            and a.status()["auth_required"] is True
        )
        with pytest.raises(up.HTTPException):
            a.metric_names(0, 1)

    def test_user_token_env_fallback(self, monkeypatch):
        monkeypatch.setenv("UPTRACE_USER_TOKEN", "from-env")
        assert (
            up.UptraceAdapter({"type": "uptrace", "endpoint": "http://u:14318/v1/traces"}).token
            == "from-env"
        )


class TestTraces:
    def test_search_sends_uql_and_keeps_roots(self, adapter, fake_http):
        out = adapter.search(
            StructuredFilter(
                service="hermes-agent", name_regex="agent", status="ok", min_duration_ms=100
            ),
            1_790_000_000,
            1_790_500_000,
            10,
        )
        url, headers = fake_http[-1]
        assert headers == {"Authorization": "Bearer u"}
        p = _params(url)
        assert url.startswith("https://uptrace.lan:443/internal/v1/tracing/1/spans?")
        assert p["query"] == [
            'where service_name = "hermes-agent" | where _name like "agent" | where _status_code = "ok" | where _duration >= 100ms'
        ]
        assert (p["time_gte"], p["time_lt"], p["sort_by"], p["sort_desc"], p["limit"]) == (
            ["1790000000000"],
            ["1790500000000"],
            ["_time"],
            ["true"],
            ["40"],
        )
        assert [t["rootTraceName"] for t in out["traces"]] == ["agent"]  # the api child is dropped
        t = out["traces"][0]
        assert (t["traceID"], t["rootServiceName"], t["durationMs"], t["startTimeUnixNano"]) == (
            "03868e38b99ed06e70d95b46b1a57b82",
            "hermes-agent",
            51161,
            "1790296506235509000",
        )
        keys = {a["key"] for a in t["spanSets"][0]["spans"][0]["attributes"]}
        assert {"llm.model_name", "status"} <= keys  # underscored attrs come back dotted

    def test_get_trace_builds_otlp_batches_with_dotted_attributes(self, adapter, fake_http):
        d = adapter.get_trace("03868e38b99ed06e70d95b46b1a57b82")
        assert (
            fake_http[-1][0]
            == "https://uptrace.lan:443/internal/v1/tracing/1/traces/03868e38b99ed06e70d95b46b1a57b82/spans"
        )
        spans = [s for b in d["batches"] for ss in b["scopeSpans"] for s in ss["spans"]]
        assert [(s["name"], s["parentSpanId"]) for s in spans] == [
            ("agent", None),
            ("api.nvidia/nemotron-3-nano-omni", "916d5769b2"),
        ]
        api = spans[1]
        assert api["endTimeUnixNano"] == str(1790296506235509000 + 50829313000)
        assert {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "13247"}} in api[
            "attributes"
        ]
        assert d["batches"][0]["resource"]["attributes"] == [
            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
        ]


class TestMetrics:
    def test_names_come_from_the_catalog(self, adapter, fake_http):
        assert adapter.metric_names(0, 60) == [
            {"name": "gen_ai_client_token_usage", "instrument": "histogram"},
            {"name": "hermes_token_usage", "instrument": "counter"},
            {"name": "hermes_tool_duration", "instrument": "histogram"},
        ]

    def test_counter_query_uses_the_alias_and_folds_uptraces_grid(self, adapter, fake_http):
        start, end = 1790294400 - 3600, 1790298000 + 3600
        out = adapter.metrics_query("hermes_token_usage", start, end, 3600, group_by="model")
        p = _params(fake_http[-1][0])
        assert (p["metric"], p["alias"], p["query"]) == (
            ["hermes_token_usage"],
            ["$m"],
            ["$m group by model"],
        )
        assert (
            out["instrument"] == "counter"
            and out["mql"] == "$m group by model"
            and out["agg"] == "sum"
        )
        assert out["series"]["nvidia/nemotron-3-nano-omni"] == [None, 26748.0, None, None]
        assert out["series"]["nvidia/nemotron-3-super"] == [None, 42342.0, 100.0, None]

    def test_mql_per_instrument(self):
        assert up.mql_for("counter", "avg", None) == "$m"  # counters only sum
        assert up.mql_for("histogram", "avg", "tool.name") == "avg($m) group by tool_name"
        assert up.mql_for("histogram", "max", None) == "max($m)"
        assert up.mql_for("gauge", "last", None) == "$m"
        assert up.mql_for("unknown", "sum", None) == "sum($m)"

    def test_query_errors_are_surfaced(self, adapter, fake_http, monkeypatch):
        monkeypatch.setitem(up._MQL["counter"], "avg", "avg($m)")
        with pytest.raises(up.HTTPException, match="does not support"):
            adapter.metrics_query("hermes_token_usage", 0, 7200, 60, agg="avg")


class TestLogs:
    def test_search_filters_and_records(self, adapter, fake_http):
        f = LogFilter(
            trace_id="03868e38b99ed06e70d95b46b1a57b82",
            session="20260924_203458_4a4dea",
            min_level=30,
            logger="hermes_otel",
            text="finalized",
        )
        logs = adapter.logs_search(f, 0, 3600, 5)
        p = _params(fake_http[-1][0])
        assert p["system"] == ["log:warn", "log:error", "log:fatal", "log:panic"]
        assert p["query"] == [
            'where _trace_id = "03868e38b99ed06e70d95b46b1a57b82" | where hermes_session_id = "20260924_203458_4a4dea" | where otel_library_name = "hermes_otel"'
        ]
        assert (p["search"], p["limit"], p["sort_by"]) == (["finalized"], ["5"], ["_time"])
        assert logs[0] == {
            "level": "INFO",
            "logger": "hermes_otel",
            "body": "[hermes-otel] session 20260924_203458_4a4dea finalized",
            "time_unix_nano": 1790432584864811000,
            "trace_id": None,
            "session_id": "20260924_203458_4a4dea",
        }
        assert (logs[1]["level"], logs[1]["logger"], logs[1]["trace_id"]) == (
            "WARN",
            "cli",
            "03868e38b99ed06e70d95b46b1a57b82",
        )

    def test_no_level_means_all_log_systems(self, adapter, fake_http):
        adapter.logs_search(LogFilter(), 0, 60, 10)
        p = _params(fake_http[-1][0])
        assert p["system"] == ["log:all"] and "query" not in p and "search" not in p
        assert up.log_systems_for(40) == [
            "log:error",
            "log:fatal",
            "log:panic",
        ] and up.log_systems_for(10) == list(up._LOG_SYSTEMS[1:])

    def test_loggers_from_attribute_values(self, adapter, fake_http):
        assert adapter.loggers(0, 60) == [
            {"logger": "aiohttp.access", "count": 24712},
            {"logger": "hermes_otel", "count": 26},
        ]
        p = _params(fake_http[-1][0])
        assert p["system"] == ["log:all"]
