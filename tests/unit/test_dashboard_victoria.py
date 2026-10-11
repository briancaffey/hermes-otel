"""Victoria dashboard adapter (#366): the Jaeger-compatible API under
``/select/jaeger`` with string-typed tags, the Prometheus reader on
VictoriaMetrics, LogsQL rows from VictoriaLogs. Shapes recorded from
VictoriaTraces v0.12.0 / VictoriaLogs v1.53.0 / VictoriaMetrics v1.153.0 after
one real Hermes turn on 2026-10-11."""

from __future__ import annotations

import io
from typing import Any, Dict, List
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import jaeger
from hermes_otel.dashboard.backends import victoria as vt
from hermes_otel.dashboard.backends.base import (
    BackendError,
    ConfigError,
    LogFilter,
    StructuredFilter,
)

NS = 1_000_000_000
T0 = 1_791_750_400
TRACE = "08730d4f310e0b0bab3a0400f24485b6"
CFG = {
    "type": "victoria",
    "endpoint": "http://localhost:10428",
    "metrics_endpoint": "http://localhost:8428",
    "logs_endpoint": "http://localhost:9428",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    jaeger._API_PROBE_CACHE.clear()
    yield
    backends.clear_caches()
    jaeger._API_PROBE_CACHE.clear()


def _adapter(**extra: Any) -> vt.VictoriaAdapter:
    return vt.VictoriaAdapter({**CFG, **extra})


def _tag(k: str, v: str) -> Dict[str, Any]:
    return {"key": k, "type": "string", "value": v}


def _span(
    sid: str, name: str, start_us: int, dur_us: int, parent: str | None, tags: Dict[str, str]
):
    return {
        "traceID": TRACE,
        "spanID": sid,
        "operationName": name,
        "startTime": start_us,
        "duration": dur_us,
        "references": (
            [{"refType": "CHILD_OF", "traceID": TRACE, "spanID": parent}] if parent else []
        ),
        "tags": [
            _tag("span.kind", "internal"),
            _tag("error", "false"),
            *(_tag(k, v) for k, v in tags.items()),
        ],
        "logs": [],
        "processID": "p1",
    }


ROOT = _span(
    "ca7f75c318529100",
    "agent",
    T0 * 1_000_000,
    3_977_695,
    None,
    {
        "hermes.session_id": "20261011_162743_c194ca",
        "gen_ai.usage.input_tokens": "23571",
        "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
        "hermes.session.completed": "true",
    },
)
TOOL = _span(
    "aaaa000000000001",
    "tool.terminal",
    T0 * 1_000_000 + 1_500_000,
    1_120_770,
    "ca7f75c318529100",
    {"tool.name": "terminal"},
)
FAILED = _span(
    "aaaa000000000002",
    "tool.read_file",
    T0 * 1_000_000 + 3_000_000,
    3_595,
    "ca7f75c318529100",
    {"tool.name": "read_file"},
)
FAILED["tags"] = [t for t in FAILED["tags"] if t["key"] != "error"] + [_tag("error", "true")]
TRACE_DOC = {
    "data": [
        {
            "traceID": TRACE,
            "spans": [TOOL, FAILED, ROOT],
            "processes": {
                "p1": {"serviceName": "hermes-agent", "tags": [_tag("hermes.profile", "default")]}
            },
            "warnings": None,
        }
    ],
    "errors": None,
    "limit": 0,
    "offset": 0,
    "total": 1,
}


class TestSetup:
    def test_registered_and_mounted_on_the_select_prefix(self):
        assert backends.find_adapter_class("victoria") is vt.VictoriaAdapter
        a = _adapter()
        assert a.query_url == "http://localhost:10428"
        assert a.api_base == "http://localhost:10428/select/jaeger"
        assert a.query_api == "v1"
        assert a.prometheus_url == "http://localhost:8428"
        assert a.logs_url == "http://localhost:9428"
        st = a.status()
        assert st["metrics"] is True and st["logs"] is True
        assert st["query_api"] == "jaeger-compatible"
        assert (
            st["prometheus_url"] == "http://localhost:8428"
            and st["logs_url"] == "http://localhost:9428"
        )

    def test_unnamed_stores_switch_capabilities_off(self):
        a = vt.VictoriaAdapter({"type": "victoria", "endpoint": "http://localhost:10428"})
        assert a.supports_metrics is False and a.supports_logs is False
        with pytest.raises(ConfigError):
            a.metric_names(T0, T0 + 1)
        with pytest.raises(ConfigError):
            a.loggers(T0, T0 + 1)

    def test_explicit_read_urls_win_and_full_urls_are_reduced_to_origins(self):
        a = _adapter(
            prometheus_url="http://vm.internal:8428/api/v1",
            logs_url="http://vl.internal:9428/select/logsql/query",
        )
        assert a.prometheus_url == "http://vm.internal:8428"
        assert a.logs_url == "http://vl.internal:9428"

    def test_no_trace_url_and_basic_auth(self, monkeypatch):
        assert _adapter().trace_url(TRACE) is None
        monkeypatch.setenv("VU", "u")
        monkeypatch.setenv("VP", "p")
        assert _adapter(user_env="VU", password_env="VP")._headers() == {
            "Authorization": "Basic dTpw"
        }


class TestHelpers:
    def test_rfc3339_round_trip(self):
        ns = 1_791_750_470_202_258_944
        assert vt.rfc3339_to_ns("2026-10-11T20:27:50.202258944Z") == ns
        assert vt.ns_to_rfc3339(ns) == "2026-10-11T20:27:50.202258944Z"
        assert vt.rfc3339_to_ns("2026-10-11T20:27:50Z") == 1_791_750_470 * NS
        assert vt.rfc3339_to_ns("garbage") == 0

    def test_logsql_str(self):
        assert vt.logsql_str('a "b" \\ c') == '"a \\"b\\" \\\\ c"'


class TestTraces:
    def test_search_hits_the_prefix_and_coerces_string_tags(self, monkeypatch):
        calls: List[str] = []

        def fake(url, headers=None, timeout=10.0):
            calls.append(url)
            return TRACE_DOC

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        assert calls[0].startswith("http://localhost:10428/select/jaeger/api/traces?")
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)
        assert q["service"] == ["hermes-agent"] and q["limit"] == [str(vt._DEFAULT_SEARCH_FETCH)]
        row = page["traces"][0]
        assert row["rootTraceName"] == "agent" and row["spanCount"] == 3
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.input_tokens"] == {"intValue": "23571"}
        assert "status" not in attrs  # error="false" is not an error

    def test_error_tag_true_is_an_error(self, monkeypatch):
        bad = dict(
            ROOT, tags=[t for t in ROOT["tags"] if t["key"] != "error"] + [_tag("error", "true")]
        )
        doc = {
            "data": [
                {"traceID": TRACE, "spans": [bad], "processes": TRACE_DOC["data"][0]["processes"]}
            ]
        }
        monkeypatch.setattr(jaeger, "http_get_json", lambda url, headers=None, timeout=10.0: doc)
        row = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)["traces"][0]
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    def test_detail_status_and_typed_attributes(self, monkeypatch):
        monkeypatch.setattr(
            jaeger, "http_get_json", lambda url, headers=None, timeout=10.0: TRACE_DOC
        )
        detail = _adapter().get_trace(TRACE)
        spans = {s["spanId"]: s for s in detail["batches"][0]["scopeSpans"][0]["spans"]}
        assert spans["ca7f75c318529100"]["status"]["code"] == 1
        assert spans["aaaa000000000002"]["status"]["code"] == 2
        attrs = {x["key"]: x["value"] for x in spans["ca7f75c318529100"]["attributes"]}
        assert attrs["hermes.session.completed"] == {"boolValue": True}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_162743_c194ca"}


class TestMetrics:
    def test_prometheus_reader_on_the_metrics_store(self, monkeypatch):
        seen: Dict[str, Any] = {}
        monkeypatch.setattr(
            vt,
            "_prom_metric_names",
            lambda base, s, e, match, headers=None: seen.update(names=(base, match, headers))
            or [{"name": "x"}],
        )
        monkeypatch.setattr(
            vt,
            "_prom_metrics_query",
            lambda base, name, s, e, b, group_by=None, agg="sum", headers=None: seen.update(
                query=(base, name, group_by, agg)
            )
            or {"name": name},
        )
        a = _adapter(metrics_match='{__name__=~"hermes_.*"}', user="u", password="p")
        assert a.metric_names(T0, T0 + 60) == [{"name": "x"}]
        assert seen["names"] == (
            "http://localhost:8428",
            '{__name__=~"hermes_.*"}',
            {"Authorization": "Basic dTpw"},
        )
        assert (
            a.metrics_query(
                "hermes_token_usage_total", T0, T0 + 60, 60, group_by="token_type", agg="sum"
            )["name"]
            == "hermes_token_usage_total"
        )
        assert seen["query"] == (
            "http://localhost:8428",
            "hermes_token_usage_total",
            "token_type",
            "sum",
        )


ROW = {
    "_msg": "API call #1: model=nvidia/nemotron-3-super-120b-a12b",
    "_stream": '{service.name="hermes-agent"}',
    "_stream_id": "0000000000000000f1c3d08708ccc0e6c532624dc5cdc71c",
    "_time": "2026-10-11T20:27:46.123456789Z",
    "code.line.number": "120",
    "hermes.session_id": "20261011_162743_c194ca",
    "scope.name": "agent.auxiliary_client",
    "service.name": "hermes-agent",
    "severity_number": "9",
    "severity_text": "INFO",
    "span_id": "ca7f75c318529100",
    "trace_id": TRACE,
}


class TestLogs:
    def test_filters(self):
        f = LogFilter(
            trace_id="t",
            span_id="s",
            session="sid",
            min_level=30,
            logger="cli",
            text="it's (100%)",
            event_name="hermes.turn.end",
            before_ns=(T0 + 30) * NS,
        )
        parts = _adapter()._log_filters(f, T0, T0 + 60)
        assert (
            parts[0] == f"_time:[{vt.ns_to_rfc3339(T0 * NS)}, {vt.ns_to_rfc3339((T0 + 30) * NS)})"
        )
        assert 'trace_id:"t"' in parts and 'span_id:"s"' in parts
        assert 'hermes.session_id:"sid"' in parts and 'scope.name:"cli"' in parts
        assert "severity_number:>=13" in parts
        assert 'event.name:"hermes.turn.end"' in parts
        assert parts[-1] == '_msg:~"it\'s\\\\ \\\\(100%\\\\)"'
        assert "event.name:*" in _adapter()._log_filters(LogFilter(events_only=True), T0, T0 + 1)

    def test_rows_become_live_store_records(self, monkeypatch):
        a = _adapter()
        calls: List[str] = []

        def fake(query, limit=None, timeout=30.0):
            calls.append(query)
            return [
                ROW,
                dict(
                    ROW,
                    _time="2026-10-11T20:27:40Z",
                    severity_text="WARN",
                    severity_number="13",
                    trace_id="",
                    span_id="",
                    **{"event.name": "hermes.turn.end"},
                ),
            ]

        monkeypatch.setattr(a, "_logsql", fake)
        rows = a.logs_search(LogFilter(), T0, T0 + 600, 50)
        assert calls[0].endswith(f"| sort by (_time desc) | limit {50 + vt.LOG_PAGE_SLACK}")
        first, second = rows
        assert first["time_unix_nano"] == 1_791_750_466_123_456_789
        assert first["level"] == "INFO" and first["severity_number"] == 9
        assert first["logger"] == "agent.auxiliary_client"
        assert first["trace_id"] == TRACE and first["span_id"] == "ca7f75c318529100"
        assert first["session_id"] == "20261011_162743_c194ca" and first["event_name"] is None
        assert first["attributes"] == {"code.line.number": 120, "service.name": "hermes-agent"}
        assert (
            second["level"] == "WARN" and second["trace_id"] is None and second["span_id"] is None
        )
        assert second["event_name"] == "hermes.turn.end"

    def test_cursor_drops_rows_at_or_after_it(self, monkeypatch):
        a = _adapter()
        monkeypatch.setattr(
            a,
            "_logsql",
            lambda q, limit=None, timeout=30.0: [ROW, dict(ROW, _time="2026-10-11T20:27:40Z")],
        )
        rows = a.logs_search(LogFilter(before_ns=1_791_750_466_123_456_789), T0, T0 + 600, 50)
        assert len(rows) == 1 and rows[0]["time_unix_nano"] == 1_791_750_460 * NS

    def test_loggers(self, monkeypatch):
        a = _adapter()
        calls: List[str] = []
        monkeypatch.setattr(
            a,
            "_logsql",
            lambda q, limit=None, timeout=30.0: calls.append(q)
            or [{"scope.name": "hermes_otel", "n": "5"}, {"scope.name": "", "n": "9"}],
        )
        assert a.loggers(T0, T0 + 60) == [{"logger": "hermes_otel", "count": 5}]
        assert "| stats by (scope.name) count() n" in calls[0]


class TestLogsqlTransport:
    def test_posts_form_and_parses_ndjson(self, monkeypatch):
        seen: Dict[str, Any] = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["body"] = _urlparse.parse_qs(req.data.decode())
            return _Resp(b'{"_msg":"a"}\n\n{"_msg":"b"}\n')

        monkeypatch.setattr(_urlrequest, "urlopen", fake_urlopen)
        rows = _adapter()._logsql("* | limit 2", limit=2)
        assert [r["_msg"] for r in rows] == ["a", "b"]
        assert seen["url"] == "http://localhost:9428/select/logsql/query"
        assert seen["body"] == {"query": ["* | limit 2"], "limit": ["2"]}

    def test_http_errors(self, monkeypatch):
        def bad(req, timeout=0):
            raise _urlerror.HTTPError(
                req.full_url, 400, "Bad", {}, io.BytesIO(b"cannot parse query")
            )

        monkeypatch.setattr(_urlrequest, "urlopen", bad)
        with pytest.raises(BackendError) as ei:
            _adapter()._logsql("bad")
        assert ei.value.kind == "backend" and "cannot parse query" in ei.value.detail

        def unauth(req, timeout=0):
            raise _urlerror.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"no"))

        monkeypatch.setattr(_urlrequest, "urlopen", unauth)
        with pytest.raises(BackendError) as ei:
            _adapter()._logsql("*")
        assert ei.value.kind == "auth"
