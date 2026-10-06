"""Uptrace 2.x dashboard adapter: traces, metrics and logs over /internal/v1 (#194, #268).

Row shapes recorded from Uptrace v2.1.0-beta.5 after real Hermes turns (the
2.1 dialect: one route per signal, ``time_start``/``time_end``, ``system[]``,
attribute keys with a ``::type`` suffix); the 2.0 fallback keeps the shapes
recorded from v2.0.2 in #243.
"""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest
from fastapi import HTTPException

from hermes_otel.dashboard.backends import uptrace as up
from hermes_otel.dashboard.backends.base import LogFilter, StructuredFilter

CFG = {
    "type": "uptrace",
    "endpoint": "https://uptrace.lan/v1/traces",
    "query_port": 443,
    "user_token": "u",
}
TRACE = "cffdc3054b6c2324f0fbf484e6271304"
SESSION = "20261004_165306_f8181b"

AGENT = {
    "id": "a89db6c78bbfbea4",
    "traceId": TRACE,
    "projectId": 1,
    "type": "",
    "system": "funcs",
    "kind": "internal",
    "name": "agent",
    "displayName": "agent",
    "time": 1791147194898.957,
    "duration": 10369.973,
    "statusCode": "ok",
    "attrs": {
        "service_name::str": "hermes-agent",
        "hermes_session_id::str": SESSION,
        "llm_model_name::str": "openai/gpt-4o-mini",
        "hermes_turn_number::int": 1,
    },
    "events": [],
    "logs": [],
    "links": [],
}
API = {
    **AGENT,
    "id": "a4db4a604c3af1aa",
    "parentId": "f22904e1e54cf407",
    "name": "api.openai/gpt-4o-mini",
    "displayName": "api.openai/gpt-4o-mini",
    "duration": 902.545,
    "attrs": {
        "service_name::str": "hermes-agent",
        "gen_ai_usage_input_tokens::int": 12711,
        "gen_ai_request_model::str": "openai/gpt-4o-mini",
    },
}
LOG = {
    "id": "7541fb626d6c0bf8",
    "traceId": "18db6f4d95c910e61fdb3ca489cb09ed",
    "parentId": "08b842929ec0a6ff",
    "projectId": 1,
    "groupId": "1",
    "type": "log",
    "system": "log:warn",
    "kind": "internal",
    "name": "",
    "eventName": "log",
    "displayName": "plain warn log line from p4 probe",
    "time": 1791148356862.962,
    "duration": 0,
    "statusCode": "unset",
    "attrs": {
        "log_severity::str": "WARN",
        "otel_library_name::str": "hermes.api",
        "hermes_session_id::str": "p4-live-session-0001",
        "hermes_log_attribution::str": "context",
        "code_function_name::str": "probe",
        "service_name::str": "hermes-agent",
    },
}
EVENT = {
    **LOG,
    "id": "49584777a3766d16",
    "parentId": "08b842929ec0a6fd",
    "system": "log:info",
    "displayName": "hermes.tool.call for p4-live-session-0001",
    "attrs": {
        "log_severity::str": "INFO",
        "otel_library_name::str": "hermes.otel.events",
        "hermes_session_id::str": "p4-live-session-0001",
        "hermes_log_attribution::str": "context",
        "event_name::str": "hermes.tool.call",
        "hermes_tool_name::str": "terminal",
        "service_name::str": "hermes-agent",
    },
}
STANDALONE = {
    **LOG,
    "id": "0",
    "standalone": True,
    "parentId": "0",
    "system": "log:info",
    "displayName": "CLI cleanup calling memory shutdown",
    "attrs": {"log_severity::str": "INFO", "otel_library_name::str": "cli"},
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
            "query": "$m group by model::str",
            "type": "selector",
        }
    ],
    "timeseries": [
        {
            "name": "x+nvidia/nemotron-3-nano-omni",
            "metric": "$m group by model",
            "unit": "{token}",
            "attrs": {"model::str": "nvidia/nemotron-3-nano-omni"},
            "time": [1790294400000, 1790298000000],
            "value": [26748, None],
        },
        {
            "name": "y+nvidia/nemotron-3-super",
            "metric": "$m group by model",
            "unit": "{token}",
            "attrs": {"model::str": "nvidia/nemotron-3-super"},
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
        {"value": "hermes_otel", "count": 18},
        {"value": "tools.registry", "count": 14},
        {"value": "", "count": 3},
    ],
}
NON_JSON = HTTPException(status_code=502, detail="Backend returned non-JSON: Expecting value")


def _params(url):
    return _urlparse.parse_qs(_urlparse.urlparse(url).query)


@pytest.fixture(autouse=True)
def _fresh_dialect(monkeypatch):
    # No developer config: the log pin would otherwise follow the real
    # ``resource_attributes.service.name`` of this machine's hermes_otel.yaml.
    monkeypatch.setattr("hermes_otel.dashboard.backends.top_level_config", lambda: {})
    up._DIALECT_CACHE.clear()
    yield
    up._DIALECT_CACHE.clear()


@pytest.fixture()
def fake_http(monkeypatch):
    """A 2.1 server: the probe answers JSON and every signal has its own route."""
    calls = []

    def fake_get(url, headers=None, timeout=10.0):
        parsed = _urlparse.urlparse(url)
        path, q = parsed.path, _params(url)
        if path == "/internal/v1/logs/1/systems":
            return {"systems": [{"groupCount": 1, "system": "log:info"}]}
        calls.append((url, headers))
        if path == f"/internal/v1/traces/1/{TRACE}":
            return {"id": TRACE, "spans": [AGENT, API], "logs": []}
        if path == "/internal/v1/spans/1":
            return {"count": 2, "spans": [API, AGENT]}
        if path == "/internal/v1/logs/1":
            return {"count": 3, "spans": [LOG, EVENT, STANDALONE]}
        if path == "/internal/v1/tracing/1/attributes/otel_library_name::str":
            return LOGGERS
        if path == "/internal/v1/metrics/1":
            return CATALOG
        if path == "/internal/v1/metrics/1/timeseries":
            return (
                QUERY_ERROR
                if "avg($m)" in q["query"][0] and q["metric"][0] == "hermes_token_usage"
                else TIMESERIES
            )
        raise AssertionError(url)

    monkeypatch.setattr(up, "http_get_json", fake_get)
    return calls


@pytest.fixture()
def fake_http_20(monkeypatch):
    """A 2.0 server: no per-signal routes (the probe gets the SPA), everything under /tracing/."""
    calls = []

    def _bare(sp):
        return {**sp, "attrs": up.plain_attrs(sp["attrs"])}

    def fake_get(url, headers=None, timeout=10.0):
        parsed = _urlparse.urlparse(url)
        path, q = parsed.path, _params(url)
        if path == "/internal/v1/logs/1/systems":
            raise NON_JSON
        calls.append((url, headers))
        if path == f"/internal/v1/tracing/1/traces/{TRACE}/spans":
            return {"id": TRACE, "spans": [_bare(AGENT), _bare(API)]}
        if path == "/internal/v1/tracing/1/spans":
            if any(s.startswith("log:") for s in q.get("system", [])):
                return {"count": 2, "spans": [_bare(LOG), _bare(STANDALONE)]}
            return {"count": 2, "spans": [_bare(API), _bare(AGENT)]}
        if path == "/internal/v1/tracing/1/attributes/otel_library_name":
            return LOGGERS
        raise AssertionError(url)

    monkeypatch.setattr(up, "http_get_json", fake_get)
    return calls


@pytest.fixture()
def adapter():
    return up.UptraceAdapter(CFG)


class TestSetup:
    def test_query_url_token_and_status(self, adapter):
        st = adapter.status()
        assert st["query_url"] == "https://uptrace.lan:443" and st["project_id"] == 1
        assert (st["metrics"], st["logs"], st["auth_required"]) == (True, True, False)
        assert "api_dialect" not in st  # nothing probed yet: status() never talks to the server

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

    def test_plain_attrs_strips_the_type_suffix(self):
        assert up.plain_attrs({"a::str": "x", "b::int": 1, "c": 2, "d::[]str": []}) == {
            "a": "x",
            "b": 1,
            "c": 2,
            "d": [],
        }
        assert up.plain_attrs(None) == {}


class TestDialectDetection:
    def test_json_from_the_logs_route_means_2_1(self, adapter, fake_http):
        assert adapter._dialect()["start"] == "time_start"
        assert adapter.status()["api_dialect"] == "2.1"

    def test_a_json_error_still_counts_as_the_route_existing(self, monkeypatch):
        def fake_get(url, headers=None, timeout=10.0):
            raise HTTPException(
                status_code=502,
                detail='Backend returned 400: {"error": "ch: connection pool timeout"}',
            )

        monkeypatch.setattr(up, "http_get_json", fake_get)
        assert up.UptraceAdapter(CFG)._dialect() is up._DIALECTS["2.1"]

    def test_the_spa_means_2_0(self, adapter, fake_http_20):
        assert adapter._dialect() is up._DIALECTS["2.0"]
        assert adapter.status()["api_dialect"] == "2.0"

    def test_an_unreachable_server_is_reported_not_guessed(self, adapter, monkeypatch):
        def fake_get(url, headers=None, timeout=10.0):
            raise HTTPException(status_code=502, detail="Backend unreachable: refused")

        monkeypatch.setattr(up, "http_get_json", fake_get)
        with pytest.raises(HTTPException, match="unreachable"):
            adapter.logs_search(LogFilter(), 0, 60, 5)
        assert up._DIALECT_CACHE == {}

    def test_probed_once_per_server(self, monkeypatch):
        probes = []

        def fake_get(url, headers=None, timeout=10.0):
            if url.endswith("/systems?" + _urlparse.urlparse(url).query):
                probes.append(url)
                return {"systems": []}
            return {"spans": [], "metrics": []}

        monkeypatch.setattr(up, "http_get_json", fake_get)
        up.UptraceAdapter(CFG).logs_search(LogFilter(), 0, 60, 5)
        up.UptraceAdapter(CFG).metric_names(0, 60)
        assert len(probes) == 1 and probes[0].startswith(
            "https://uptrace.lan:443/internal/v1/logs/1/systems?time_start="
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
        assert url.startswith("https://uptrace.lan:443/internal/v1/spans/1?")
        assert p["query"] == [
            'where service_name = "hermes-agent" | where _name like "agent" | where _status_code = "ok" | where _duration >= 100ms'
        ]
        # Roots are kept client-side: four rows per requested trace, plus one
        # so has_more is exact.
        assert (p["time_start"], p["time_end"], p["system[]"], p["sort_dir"], p["limit"]) == (
            ["1790000000000"],
            ["1790500000000"],
            ["spans:all"],
            ["desc"],
            ["41"],
        )
        assert out["has_more"] is False and out["next_before_ns"] is None
        assert [t["rootTraceName"] for t in out["traces"]] == ["agent"]  # the api child is dropped
        t = out["traces"][0]
        assert (t["traceID"], t["rootServiceName"], t["durationMs"], t["startTimeUnixNano"]) == (
            TRACE,
            "hermes-agent",
            10369,
            "1791147194898957000",
        )
        keys = {a["key"] for a in t["spanSets"][0]["spans"][0]["attributes"]}
        assert {"llm.model_name", "status"} <= keys  # typed, underscored keys come back dotted

    def test_get_trace_builds_otlp_batches_with_dotted_attributes(self, adapter, fake_http):
        d = adapter.get_trace(TRACE)
        assert fake_http[-1][0] == f"https://uptrace.lan:443/internal/v1/traces/1/{TRACE}"
        spans = [s for b in d["batches"] for ss in b["scopeSpans"] for s in ss["spans"]]
        assert [(s["name"], s["parentSpanId"]) for s in spans] == [
            ("agent", None),
            ("api.openai/gpt-4o-mini", "f22904e1e54cf407"),
        ]
        api = spans[1]
        assert api["endTimeUnixNano"] == str(1791147194898957000 + 902545000)
        assert {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "12711"}} in api[
            "attributes"
        ]
        assert {"key": "hermes.turn.number", "value": {"intValue": "1"}} in spans[0]["attributes"]
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
        # The series' attrs come back typed (``model::str``); the label is still found.
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


def _core(row):
    """The pre-#268 row keys; the richer fields (span_id, severity_number, event_name, attributes) are asserted separately."""
    return {
        k: row.get(k)
        for k in ("level", "logger", "body", "time_unix_nano", "trace_id", "session_id")
    }


class TestLogs:
    def test_search_filters_and_records(self, adapter, fake_http):
        f = LogFilter(
            trace_id=TRACE,
            session=SESSION,
            min_level=30,
            logger="hermes_otel",
            text="finalized",
        )
        logs = adapter.logs_search(f, 0, 3600, 5)
        url = fake_http[-1][0]
        assert url.startswith("https://uptrace.lan:443/internal/v1/logs/1?")
        p = _params(url)
        assert p["system[]"] == ["log:warn", "log:error", "log:fatal", "log:panic"]
        assert p["query"] == [
            f'where service_name = "hermes-agent" | where _trace_id = "{TRACE}" | where hermes_session_id = "{SESSION}" | where otel_library_name = "hermes_otel"'
        ]
        assert (p["search"], p["limit"], p["sort_by"], p["sort_dir"]) == (
            ["finalized"],
            ["5"],
            ["_time"],
            ["desc"],
        )
        assert _core(logs[0]) == {
            "level": "WARN",
            "logger": "hermes.api",
            "body": "plain warn log line from p4 probe",
            "time_unix_nano": 1791148356862962000,
            "trace_id": "18db6f4d95c910e61fdb3ca489cb09ed",
            "session_id": "p4-live-session-0001",
        }
        # The row's span is Uptrace's ``parentId`` (the row's own ``id`` is its
        # storage key); attributes lose the type suffix and gain their dotted names.
        assert (logs[0]["span_id"], logs[0]["severity_number"], logs[0]["event_name"]) == (
            "08b842929ec0a6ff",
            13,
            None,
        )
        assert logs[0]["attributes"] == {
            "hermes.log.attribution": "context",
            "code.function.name": "probe",
        }
        assert (logs[1]["event_name"], logs[1]["attributes"]["hermes.tool.name"]) == (
            "hermes.tool.call",
            "terminal",
        )
        # A standalone row's trace id is synthetic: dropped, with its span.
        assert (logs[2]["trace_id"], logs[2]["span_id"], logs[2]["logger"]) == (None, None, "cli")

    def test_no_level_means_all_log_systems(self, adapter, fake_http):
        adapter.logs_search(LogFilter(), 0, 60, 10)
        p = _params(fake_http[-1][0])
        # Only the service scope remains (Uptrace's own lines share the project).
        assert p["system[]"] == ["log:all"] and "search" not in p
        assert p["query"] == ['where service_name = "hermes-agent"']
        assert up.log_systems_for(40) == [
            "log:error",
            "log:fatal",
            "log:panic",
        ] and up.log_systems_for(10) == list(up._LOG_SYSTEMS[1:])

    def test_event_filters_become_where_clauses(self, adapter, fake_http):
        adapter.logs_search(LogFilter(event_name="hermes.tool.call"), 0, 60, 10)
        assert _params(fake_http[-1][0])["query"] == [
            'where service_name = "hermes-agent" | where event_name = "hermes.tool.call"'
        ]
        adapter.logs_search(LogFilter(events_only=True), 0, 60, 10)
        assert _params(fake_http[-1][0])["query"] == [
            'where service_name = "hermes-agent" | where event_name exists'
        ]

    def test_loggers_from_attribute_values_scoped_to_the_service(self, adapter, fake_http):
        assert adapter.loggers(0, 60) == [
            {"logger": "hermes_otel", "count": 18},
            {"logger": "tools.registry", "count": 14},
        ]
        url = fake_http[-1][0]
        assert url.startswith(
            "https://uptrace.lan:443/internal/v1/tracing/1/attributes/otel_library_name::str?"
        )
        p = _params(url)
        assert (p["system[]"], p["space"], p["query"]) == (
            ["log:all"],
            ["logs"],
            ['where service_name = "hermes-agent"'],
        )


class TestLegacyDialect:
    """Uptrace 2.0: the same calls, spelled the way #243 recorded them."""

    def test_search_and_trace_paths(self, adapter, fake_http_20):
        out = adapter.search(
            StructuredFilter(service="hermes-agent"), 1_790_000_000, 1_790_500_000, 10
        )
        url = fake_http_20[-1][0]
        assert url.startswith("https://uptrace.lan:443/internal/v1/tracing/1/spans?")
        p = _params(url)
        assert (p["time_gte"], p["time_lt"], p["sort_desc"]) == (
            ["1790000000000"],
            ["1790500000000"],
            ["true"],
        )
        assert "system" not in p and [t["rootTraceName"] for t in out["traces"]] == ["agent"]
        adapter.get_trace(TRACE)
        assert (
            fake_http_20[-1][0]
            == f"https://uptrace.lan:443/internal/v1/tracing/1/traces/{TRACE}/spans"
        )

    def test_logs_and_loggers(self, adapter, fake_http_20):
        logs = adapter.logs_search(LogFilter(min_level=30, text="probe"), 0, 3600, 5)
        p = _params(fake_http_20[-1][0])
        assert p["system"] == ["log:warn", "log:error", "log:fatal", "log:panic"]
        assert (p["search"], p["sort_desc"], p["time_gte"]) == (["probe"], ["true"], ["0"])
        assert (logs[0]["level"], logs[0]["attributes"]["hermes.log.attribution"]) == (
            "WARN",
            "context",
        )
        assert adapter.loggers(0, 60)[0] == {"logger": "hermes_otel", "count": 18}
        url = fake_http_20[-1][0]
        assert "/internal/v1/tracing/1/attributes/otel_library_name?" in url
        p = _params(url)
        assert p["system"] == ["log:all"] and "space" not in p
