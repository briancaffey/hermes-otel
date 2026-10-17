"""Gigapipe dashboard adapter (#370): the LGTM adapter on one port plus the
four Gigapipe detours (cards from the detail, pre-1.0 detail JSON, counters via
query_range on the 15 s rollup, label-only Loki streams). Shapes recorded from
ghcr.io/metrico/gigapipe v5.5.3 after one real Hermes turn on 2026-10-11."""

from __future__ import annotations

import copy
from typing import Any, Dict, List
from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import _prometheus, tempo
from hermes_otel.dashboard.backends import gigapipe as gp
from hermes_otel.dashboard.backends.base import BackendError, LogFilter, StructuredFilter

NS = 1_000_000_000
T0 = 1_791_752_700
TRACE = "65c73f64f88cc17e1ee86943ad7cfcda"


def _adapter(**extra: Any) -> gp.GigapipeAdapter:
    return gp.GigapipeAdapter({"type": "gigapipe", "endpoint": "http://localhost:3110", **extra})


def _sv(k: str, v: Any) -> Dict[str, Any]:
    return {"key": k, "value": {"stringValue": str(v)}}


def _raw_span(
    sid: str, name: str, start_ns: int, dur_ns: int, parent: str | None = None, status=1, **attrs
):
    sp = {
        "traceID": TRACE.upper(),
        "traceId": TRACE,
        "spanID": sid.upper(),
        "spanId": sid,
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + dur_ns),
        "serviceName": "hermes-agent",
        "attributes": [_sv(k, v) for k, v in attrs.items()],
        "events": [],
        "status": {"code": status},
    }
    if parent:
        sp["parentSpanId"] = parent
    return sp


ROOT = _raw_span(
    "ee08c713fe6c52f0",
    "agent",
    T0 * NS,
    3_787_687_000,
    **{
        "hermes.session_id": "20261011_170751_bc8409",
        "gen_ai.usage.input_tokens": 23504,
        "gen_ai.usage.total_tokens": 23650,
        "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
        "hermes.session.completed": "true",
    },
)
TOOL = _raw_span(
    "aaaa000000000001",
    "tool.terminal",
    T0 * NS + 1_200_000_000,
    900_000_000,
    "ee08c713fe6c52f0",
    **{"tool.name": "terminal"},
)
READ = _raw_span(
    "aaaa000000000002",
    "tool.read_file",
    T0 * NS + 2_900_000_000,
    5_000_000,
    "ee08c713fe6c52f0",
    status=2,
    **{"tool.name": "read_file"},
)
READ["events"] = [
    {
        "name": "tool.failed",
        "timeUnixNano": str(T0 * NS + 2_901_000_000),
        "attributes": [_sv("reason", "missing")],
    }
]
DETAIL = {
    "resourceSpans": [
        {
            "resource": {"attributes": [_sv("collector", "gigapipe")]},
            "instrumentationLibrarySpans": [{"spans": [TOOL, READ, ROOT]}],
        }
    ]
}
SEARCH_ROW = {
    "traceID": TRACE,
    "rootServiceName": "hermes-agent",
    "rootTraceName": "agent",
    "startTimeUnixNano": str(T0 * NS),
    "durationMs": 3787,
    "spanSet": {
        "spans": [
            {
                "spanID": "ee08c713fe6c52f0",
                "startTimeUnixNano": str(T0 * NS),
                "durationNanos": "3787687000",
                "attributes": [],
            }
        ],
        "matched": 1,
    },
    "spanSets": [
        {
            "spans": [
                {
                    "spanID": "ee08c713fe6c52f0",
                    "startTimeUnixNano": str(T0 * NS),
                    "durationNanos": "3787687000",
                    "attributes": [],
                }
            ],
            "matched": 1,
        }
    ],
}


def _capture(monkeypatch, routes: Dict[str, Any]):
    calls: List[str] = []

    def fake(url, headers=None, timeout=10.0):
        calls.append(url)
        path = _urlparse.urlsplit(url).path
        for prefix, ans in routes.items():
            if path.startswith(prefix):
                if isinstance(ans, Exception):
                    raise ans
                # the adapter rewrites the rows it gets; hand out copies
                return copy.deepcopy(ans)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(tempo, "http_get_json", fake)
    monkeypatch.setattr(gp, "http_get_json", fake)
    monkeypatch.setattr(_prometheus, "http_get_json", fake)
    return calls


class TestSetup:
    def test_registered_and_every_api_on_one_port(self):
        assert backends.find_adapter_class("gigapipe") is gp.GigapipeAdapter
        assert backends.find_adapter_class("qryn") is gp.GigapipeAdapter
        a = _adapter()
        assert a.query_url == "http://localhost:3110"
        assert a.prometheus_url == "http://localhost:3110" and a.loki_url == "http://localhost:3110"
        assert a.supports_metrics and a.supports_logs
        assert a.trace_url(TRACE) is None
        assert set(gp.GigapipeAdapter.filter_support) == set(backends.base.FILTER_KEYS)

    def test_default_port_and_explicit_siblings(self):
        a = gp.GigapipeAdapter(
            {
                "type": "gigapipe",
                "endpoint": "http://gp/v1/traces",
                "loki_url": "off",
                "prometheus_url": "http://prom:9090",
            }
        )
        assert a.query_url == "http://gp:3100"
        assert a.prometheus_url == "http://prom:9090" and a.loki_url is None
        assert a.supports_logs is False
        assert _adapter(ui_url="http://localhost:3001/explore").trace_url(TRACE) is not None


class TestNormalizeTrace:
    def test_reshape_coerce_and_group_by_service(self):
        out = gp.normalize_trace(DETAIL)
        assert len(out["batches"]) == 1
        batch = out["batches"][0]
        assert batch["resource"]["attributes"] == [
            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
        ]
        spans = {s["spanId"]: s for s in batch["scopeSpans"][0]["spans"]}
        root = spans["ee08c713fe6c52f0"]
        assert root["traceId"] == TRACE and root["parentSpanId"] is None and root["kind"] == 1
        attrs = {a["key"]: a["value"] for a in root["attributes"]}
        assert attrs["gen_ai.usage.input_tokens"] == {"intValue": "23504"}
        assert attrs["hermes.session.completed"] == {"boolValue": True}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_170751_bc8409"}
        assert spans["aaaa000000000001"]["parentSpanId"] == "ee08c713fe6c52f0"
        read = spans["aaaa000000000002"]
        assert read["status"] == {"code": 2}
        assert read["events"] == [
            {
                "name": "tool.failed",
                "timeUnixNano": str(T0 * NS + 2_901_000_000),
                "attributes": [{"key": "reason", "value": {"stringValue": "missing"}}],
            }
        ]

    def test_empty_or_odd_input(self):
        assert gp.normalize_trace({})["batches"] == []
        assert gp.normalize_trace(None)["batches"] == []


class TestTraces:
    def test_cards_come_from_the_detail(self, monkeypatch):
        calls = _capture(
            monkeypatch, {"/api/search": {"traces": [SEARCH_ROW]}, "/api/traces/": DETAIL}
        )
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        assert calls[0].startswith("http://localhost:3110/api/search?")
        assert any(c == f"http://localhost:3110/api/traces/{TRACE}" for c in calls)
        row = page["traces"][0]
        assert row["rootTraceName"] == "agent" and row["spanCount"] == 3
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "23650"}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_170751_bc8409"}
        assert "status" not in attrs

    def test_widened_card_is_the_first_matching_span(self, monkeypatch):
        _capture(monkeypatch, {"/api/search": {"traces": [SEARCH_ROW]}, "/api/traces/": DETAIL})
        rows = _adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        assert rows[0]["rootTraceName"] == "tool.terminal"
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}
        rows = _adapter().search(
            StructuredFilter(status="error", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        assert rows[0]["rootTraceName"] == "tool.read_file"
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    def test_detail_not_found(self, monkeypatch):
        _capture(monkeypatch, {"/api/traces/": BackendError(404, "", "not_found")})
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace("0" * 32)
        assert ei.value.kind == "not_found"
        _capture(monkeypatch, {"/api/traces/": {"resourceSpans": []}})
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace(TRACE)
        assert ei.value.kind == "not_found"

    def test_detail_shape(self, monkeypatch):
        _capture(monkeypatch, {"/api/traces/": DETAIL})
        d = _adapter().get_trace(TRACE)
        assert d["span_count"] == 3 and d["truncated"] is False and "batches" in d


class TestMetrics:
    def test_counter_via_query_range_on_the_rollup(self, monkeypatch):
        start, end = T0, T0 + 600
        answer = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {
                            "__name__": "hermes_token_usage_total",
                            "token_type": "input",
                            "instance": "gw",
                        },
                        "values": [[start - 120, "100"], [start + 30, "100"], [start + 90, "105"]],
                    },
                    {
                        "metric": {
                            "__name__": "hermes_token_usage_total",
                            "token_type": "input",
                            "instance": "one",
                        },
                        "values": [[start + 400, "17078"], [start + 460, "17078"]],
                    },
                    {
                        "metric": {
                            "__name__": "hermes_token_usage_total",
                            "token_type": "output",
                            "instance": "one",
                        },
                        "values": [[start + 400, "42"]],
                    },
                ],
            },
        }
        calls = _capture(monkeypatch, {"/api/v1/query_range": answer})
        out = _adapter().metrics_query(
            "hermes_token_usage_total", start, end, 300, group_by="token_type"
        )
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)
        assert q["query"] == ["hermes_token_usage_total"] and q["step"] == [str(gp._ROLLUP_STEP_S)]
        assert q["start"] == [str(start - gp.LOOKBEHIND_S)] and q["end"] == [str(end)]
        assert out["cumulative"] is True and out["agg"] == "sum"
        assert out["series"]["input"] == [5.0, 17078.0, None]
        assert out["series"]["output"] == [None, 42.0, None]

    def test_gauges_take_the_tempo_adapter_path(self, monkeypatch):
        calls = _capture(
            monkeypatch,
            {
                "/api/v1/query_range": {
                    "status": "success",
                    "data": {"resultType": "matrix", "result": []},
                }
            },
        )
        out = _adapter().metrics_query("system_cpu_utilization", T0, T0 + 60, 60, agg="max")
        assert out.get("cumulative") is not True and "query_range" in calls[0]
        assert (
            "last_over_time" in _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)["query"][0]
        )


class TestLogs:
    def test_logql_uses_gigapipe_labels(self):
        a = _adapter()
        q = a._logql(
            LogFilter(
                trace_id="t",
                span_id="s",
                session="sid",
                min_level=30,
                text="API call",
                event_name="e",
            )
        )
        assert q.startswith('{service_name=~".+"}')
        assert '| trace_id="t"' in q and '| span_id="s"' in q and '| hermes_session_id="sid"' in q
        assert '| level=~"WARN|WARNING|ERROR|CRITICAL|FATAL"' in q
        assert '| event_name="e"' in q and '|= "API call"' in q
        assert "severity_number" not in q and "scope_name" not in q
        assert '| event_name!=""' in a._logql(LogFilter(events_only=True))
        assert '| level=~"ERROR|CRITICAL|FATAL"' in a._logql(LogFilter(min_level=40))

    def test_rows_from_label_only_streams(self, monkeypatch):
        answer = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {
                            "service_name": "hermes-agent",
                            "level": "WARN",
                            "trace_id": TRACE,
                            "span_id": "ee08c713fe6c52f0",
                            "hermes_session_id": "s1",
                            "code_line_number": "7",
                            "hermes_log_attribution": "single_session",
                        },
                        "values": [[str(T0 * NS + 20), "careful"], [str(T0 * NS + 10), "hello"]],
                    }
                ],
            },
        }
        calls = _capture(monkeypatch, {"/loki/api/v1/query_range": answer})
        rows = _adapter().logs_search(LogFilter(trace_id=TRACE), T0 - 60, T0 + 60, 50)
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)
        assert (
            q["direction"] == ["backward"]
            and q["limit"] == ["50"]
            and 'trace_id="' in q["query"][0]
        )
        assert [r["body"] for r in rows] == ["careful", "hello"]
        row = rows[0]
        assert row["level"] == "WARN" and row["severity_number"] == 13
        assert (
            row["logger"] == ""
            and row["trace_id"] == TRACE
            and row["span_id"] == "ee08c713fe6c52f0"
        )
        assert row["session_id"] == "s1"
        assert row["attributes"]["hermes.log.attribution"] == "single_session"
        assert row["attributes"]["code.line.number"] == "7"

    def test_logger_filter_and_loggers_are_unsupported(self, monkeypatch):
        _capture(monkeypatch, {})
        assert _adapter().logs_search(LogFilter(logger="cli"), T0, T0 + 1, 5) == []
        assert _adapter().loggers(T0, T0 + 1) == []


class TestConcurrentDetails:
    """A build that ignores ``select()`` lists cards without attributes; each
    card's detail is fetched, concurrently, and a failed one is left as listed (#413)."""

    @staticmethod
    def _ids(n):
        return [f"{i:032x}" for i in range(1, n + 1)]

    def test_every_card_gets_its_detail(self, monkeypatch):
        rows = [dict(SEARCH_ROW, traceID=t) for t in self._ids(3)]
        calls = _capture(monkeypatch, {"/api/search": {"traces": rows}, "/api/traces/": DETAIL})
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        fetched = sorted(c.rsplit("/", 1)[1] for c in calls if "/api/traces/" in c)
        assert fetched == self._ids(3)
        assert sorted(r["traceID"] for r in page["traces"]) == self._ids(3)
        assert all(r["spanCount"] == 3 and r["rootTraceName"] == "agent" for r in page["traces"])

    def test_a_failing_detail_leaves_that_card_as_listed(self, monkeypatch):
        rows = [dict(SEARCH_ROW, traceID=t) for t in self._ids(2)]
        bad = self._ids(2)[1]
        _capture(
            monkeypatch,
            {
                "/api/search": {"traces": rows},
                f"/api/traces/{bad}": BackendError(404, "", "not_found"),
                "/api/traces/": DETAIL,
            },
        )
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        by_id = {r["traceID"]: r for r in page["traces"]}
        assert by_id[self._ids(2)[0]]["spanCount"] == 3
        assert by_id[bad]["spanSets"][0]["spans"][0]["attributes"] == []
