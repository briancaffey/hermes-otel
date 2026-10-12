"""Aspire Dashboard adapter (#369): the read API's OTLP JSON, client-side
filters, roots, the window and the cursor, .NET severity spellings. Shapes
recorded from mcr.microsoft.com/dotnet/aspire-dashboard:13.6.1 after one real
Hermes turn on 2026-10-11."""

from __future__ import annotations

from typing import Any, Dict, List
from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import aspire as asp
from hermes_otel.dashboard.backends.base import BackendError, LogFilter, StructuredFilter

NS = 1_000_000_000
T0 = 1_791_751_500
TRACE = "ab43116e1d6d4a1a449db547c36ca808"


def _adapter(**extra: Any) -> asp.AspireAdapter:
    return asp.AspireAdapter(
        {"type": "aspire", "endpoint": "http://localhost:4418", "query_port": 18888, **extra}
    )


def _attr(k: str, v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    return {"key": k, "value": {"stringValue": str(v)}}


def _span(
    sid: str, name: str, start_ns: int, dur_ns: int, parent: str | None = None, status=1, **attrs
):
    sp = {
        "traceId": TRACE,
        "spanId": sid,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + dur_ns),
        "attributes": [_attr(k, v) for k, v in attrs.items()],
        "status": {"message": "", "code": status},
    }
    if parent:
        sp["parentSpanId"] = parent
    return sp


ROOT = _span(
    "65a98a08103c9da7",
    "agent",
    T0 * NS,
    4_029_000_000,
    **{
        "hermes.session_id": "20261011_164619_149142",
        "gen_ai.usage.total_tokens": 23690,
        "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
        "input.value": "read the file hermes_otel.yaml",
    },
)
TOOL = _span(
    "aaaa000000000001",
    "tool.terminal",
    T0 * NS + 1_500_000_000,
    1_100_000_000,
    "65a98a08103c9da7",
    **{"tool.name": "terminal"},
)
FAILED = _span(
    "aaaa000000000002",
    "tool.read_file",
    T0 * NS + 3_000_000_000,
    3_000_000,
    "65a98a08103c9da7",
    status=2,
    **{"tool.name": "read_file"},
)
RESOURCE = {
    "attributes": [_attr("service.name", "hermes-agent"), _attr("service.instance.id", "f26a")]
}


def _traces_doc(*spans):
    return {
        "data": {
            "resourceSpans": [
                {
                    "resource": RESOURCE,
                    "scopeSpans": [{"scope": {"name": "hermes-otel-plugin"}, "spans": list(spans)}],
                }
            ]
        },
        "totalCount": 1,
        "returnedCount": 1,
    }


def _log(
    ts_ns: int,
    body: str,
    num: int = 9,
    text: str = "Information",
    scope: str = "cli",
    trace: bool = True,
    **attrs,
):
    rec = {
        "timeUnixNano": str(ts_ns),
        "severityNumber": num,
        "severityText": text,
        "body": {"stringValue": body},
        "attributes": [_attr("aspire.log_id", 42), *(_attr(k, v) for k, v in attrs.items())],
    }
    if trace:
        rec["traceId"] = TRACE
        rec["spanId"] = "65a98a08103c9da7"
    return scope, rec


def _logs_doc(*scoped):
    by_scope: Dict[str, List[Dict[str, Any]]] = {}
    for scope, rec in scoped:
        by_scope.setdefault(scope, []).append(rec)
    return {
        "data": {
            "resourceLogs": [
                {
                    "resource": RESOURCE,
                    "scopeLogs": [
                        {"scope": {"name": s}, "logRecords": recs} for s, recs in by_scope.items()
                    ],
                }
            ]
        },
        "totalCount": len(scoped),
        "returnedCount": len(scoped),
    }


RESOURCES = [
    {"name": "hermes-agent", "displayName": "hermes-agent-f26a", "hasTraces": True},
    {"name": "hermes-agent", "displayName": "hermes-agent-ab54", "hasTraces": True},
    {"name": "other", "displayName": "other-1", "hasTraces": True},
]


def _capture(monkeypatch, answers: Dict[str, Any]):
    calls: List[str] = []
    answers = {"/api/telemetry/resources": RESOURCES, **answers}

    def fake(url, headers=None, timeout=10.0):
        calls.append(url)
        path = _urlparse.urlsplit(url).path
        ans = answers.get(path)
        if isinstance(ans, Exception):
            raise ans
        return ans if ans is not None else {}

    monkeypatch.setattr(asp, "http_get_json", fake)
    return calls


class TestSetup:
    def test_registered_declares_filters_and_capabilities(self):
        assert backends.find_adapter_class("aspire") is asp.AspireAdapter
        assert set(asp.AspireAdapter.filter_support) == set(backends.base.FILTER_KEYS)
        a = _adapter()
        assert a.query_url == "http://localhost:18888"
        st = a.status()
        assert st["metrics"] is False and st["logs"] is True
        assert st["query_url"] == "http://localhost:18888"

    def test_default_ui_port_and_trace_url(self):
        a = asp.AspireAdapter({"type": "aspire", "endpoint": "http://aspire:18890"})
        assert a.query_url == "http://aspire:18888"
        assert a.trace_url(TRACE) == f"http://aspire:18888/traces/detail/{TRACE}"
        assert (
            _adapter(ui_url="https://aspire.example/").trace_url(TRACE)
            == f"https://aspire.example/traces/detail/{TRACE}"
        )

    def test_api_key_header(self, monkeypatch):
        monkeypatch.setenv("AK", "k")
        assert _adapter(api_key_env="AK")._headers() == {"x-api-key": "k"}
        assert _adapter()._headers() == {}


class TestHelpers:
    def test_level_for(self):
        assert asp.level_for(9, "Information") == "INFO"
        assert asp.level_for(13, "Warning") == "WARN"
        assert asp.level_for(17, "Error") == "ERROR"
        assert asp.level_for(21, "Critical") == "FATAL"
        assert asp.level_for(5, "Debug") == "DEBUG"
        assert asp.level_for(0, "Warning") == "WARN"
        assert asp.level_for(None, None) == "INFO"

    def test_otlp_value_and_attrs(self):
        assert asp.otlp_value({"intValue": "7"}) == 7
        assert asp.otlp_value({"boolValue": True}) is True
        assert asp.attrs_dict([_attr("a", 1), _attr("b", "x"), {"nope": 1}]) == {"a": 1, "b": "x"}


class TestSearch:
    def test_request_and_card(self, monkeypatch):
        calls = _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(TOOL, FAILED, ROOT)})
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        assert calls[0] == "http://localhost:18888/api/telemetry/resources"
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[1]).query)
        # every instance of the service, never the bare name (ambiguous with two)
        assert q["resource"] == ["hermes-agent-ab54", "hermes-agent-f26a"]
        assert q["limit"] == [str(asp._DEFAULT_SEARCH_FETCH)]
        row = page["traces"][0]
        assert row["traceID"] == TRACE and row["spanCount"] == 3
        assert row["rootTraceName"] == "agent" and row["rootServiceName"] == "hermes-agent"
        assert row["startTimeUnixNano"] == str(T0 * NS) and row["durationMs"] == 4029
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "23690"}
        assert "status" not in attrs

    def test_window_is_applied_client_side(self, monkeypatch):
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(ROOT)})
        assert _adapter().search(StructuredFilter(), T0 - 7200, T0 - 3600, 10)["traces"] == []

    def test_filters_on_the_root(self, monkeypatch):
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(TOOL, FAILED, ROOT)})
        a = _adapter()
        w = (T0 - 60, T0 + 60, 10)
        assert a.search(StructuredFilter(attr_equals={"tool.name": "terminal"}), *w)["traces"] == []
        assert (
            len(
                a.search(
                    StructuredFilter(attr_equals={"hermes.session_id": "20261011_164619_149142"}),
                    *w,
                )["traces"]
            )
            == 1
        )
        assert a.search(StructuredFilter(status="error"), *w)["traces"] == []
        assert len(a.search(StructuredFilter(status="ok"), *w)["traces"]) == 1
        assert len(a.search(StructuredFilter(free_text="HERMES_OTEL.yaml"), *w)["traces"]) == 1
        assert a.search(StructuredFilter(free_text="nope"), *w)["traces"] == []
        assert len(a.search(StructuredFilter(min_duration_ms=4000), *w)["traces"]) == 1
        assert a.search(StructuredFilter(min_duration_ms=5000), *w)["traces"] == []
        assert len(a.search(StructuredFilter(name_regex="agent"), *w)["traces"]) == 1

    def test_widened_search_cards_the_first_matching_span(self, monkeypatch):
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(FAILED, TOOL, ROOT)})
        rows = _adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        assert [r["rootTraceName"] for r in rows] == ["tool.terminal"]
        rows = _adapter().search(
            StructuredFilter(status="error", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        assert [r["rootTraceName"] for r in rows] == ["tool.read_file"]
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    def test_orphans_are_skipped_when_roots_only(self, monkeypatch):
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(TOOL, FAILED)})
        assert _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)["traces"] == []

    def test_unknown_service_is_an_empty_page_without_a_traces_call(self, monkeypatch):
        calls = _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(ROOT)})
        page = _adapter().search(StructuredFilter(service="nope"), T0 - 60, T0 + 60, 10)
        assert page["traces"] == []
        assert len(calls) == 1  # only the resources listing

    def test_unknown_resource_404_is_an_empty_page(self, monkeypatch):
        _capture(
            monkeypatch,
            {"/api/telemetry/traces": BackendError(404, "Backend returned 404: ", "not_found")},
        )
        assert _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)["traces"] == []

    def test_paging_cursor(self, monkeypatch):
        older = dict(
            ROOT,
            traceId="1" * 32,
            spanId="b" * 16,
            startTimeUnixNano=str((T0 - 100) * NS),
            endTimeUnixNano=str((T0 - 99) * NS),
        )
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(ROOT, older)})
        page = _adapter().search(StructuredFilter(before_ns=(T0 + 1) * NS), T0 - 3600, T0 + 60, 1)
        assert [r["traceID"] for r in page["traces"]] == [TRACE]
        assert page["has_more"] is True and page["next_before_ns"] == T0 * NS


class TestDetail:
    def test_passes_otlp_batches_through(self, monkeypatch):
        calls = _capture(monkeypatch, {f"/api/telemetry/traces/{TRACE}": _traces_doc(ROOT, TOOL)})
        detail = _adapter().get_trace(TRACE)
        assert calls[0] == f"http://localhost:18888/api/telemetry/traces/{TRACE}"
        assert detail["span_count"] == 2 and detail["truncated"] is False
        assert detail["batches"][0]["resource"] == RESOURCE

    def test_not_found(self, monkeypatch):
        _capture(
            monkeypatch, {f"/api/telemetry/traces/{'0' * 32}": BackendError(404, "", "not_found")}
        )
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace("0" * 32)
        assert ei.value.kind == "not_found"
        _capture(monkeypatch, {f"/api/telemetry/traces/{TRACE}": {"data": {"resourceSpans": []}}})
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace(TRACE)
        assert ei.value.kind == "not_found"


class TestLogs:
    def test_request_params_and_records(self, monkeypatch):
        doc = _logs_doc(
            _log(T0 * NS + 10, "hello", **{"hermes.session_id": "s1", "code.line.number": 7}),
            _log(
                T0 * NS + 20,
                "careful",
                num=13,
                text="Warning",
                scope="agent.loop",
                trace=False,
                **{"event.name": "hermes.turn.end"},
            ),
        )
        calls = _capture(monkeypatch, {"/api/telemetry/logs": doc})
        rows = _adapter().logs_search(LogFilter(trace_id=TRACE, text="hello"), T0 - 60, T0 + 60, 50)
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[1]).query)
        assert q["resource"] == ["hermes-agent-ab54", "hermes-agent-f26a"]
        assert q["traceId"] == [TRACE] and q["search"] == ["hello"]
        assert q["limit"] == [str(asp._DEFAULT_LOGS_FETCH)]
        assert [r["body"] for r in rows] == ["careful", "hello"]  # newest first
        warn, hello = rows
        assert warn["level"] == "WARN" and warn["severity_number"] == 13
        assert warn["logger"] == "agent.loop"
        assert warn["trace_id"] is None and warn["event_name"] == "hermes.turn.end"
        assert hello["level"] == "INFO" and hello["session_id"] == "s1"
        assert hello["trace_id"] == TRACE and hello["span_id"] == "65a98a08103c9da7"
        assert hello["attributes"] == {"code.line.number": 7}  # aspire.log_id dropped

    def test_client_side_filters(self, monkeypatch):
        doc = _logs_doc(
            _log(T0 * NS + 10, "a", **{"hermes.session_id": "s1"}),
            _log(
                T0 * NS + 20, "b", num=13, text="Warning", scope="agent.loop", **{"event.name": "e"}
            ),
            _log((T0 + 7200) * NS, "late"),
        )
        _capture(monkeypatch, {"/api/telemetry/logs": doc})
        a = _adapter()
        w = (T0 - 60, T0 + 60, 50)
        assert [r["body"] for r in a.logs_search(LogFilter(), *w)] == [
            "b",
            "a",
        ]  # window drops "late"
        assert [r["body"] for r in a.logs_search(LogFilter(min_level=30), *w)] == ["b"]
        assert [r["body"] for r in a.logs_search(LogFilter(logger="cli"), *w)] == ["a"]
        assert [r["body"] for r in a.logs_search(LogFilter(session="s1"), *w)] == ["a"]
        assert [r["body"] for r in a.logs_search(LogFilter(events_only=True), *w)] == ["b"]
        assert [r["body"] for r in a.logs_search(LogFilter(event_name="e"), *w)] == ["b"]
        assert [r["body"] for r in a.logs_search(LogFilter(span_id="nope"), *w)] == []
        assert [r["body"] for r in a.logs_search(LogFilter(before_ns=T0 * NS + 20), *w)] == ["a"]

    def test_loggers(self, monkeypatch):
        _capture(
            monkeypatch,
            {
                "/api/telemetry/logs": _logs_doc(
                    _log(T0 * NS, "a"), _log(T0 * NS + 1, "b"), _log(T0 * NS + 2, "c", scope="x")
                )
            },
        )
        assert _adapter().loggers(T0 - 60, T0 + 60) == [
            {"logger": "cli", "count": 2},
            {"logger": "x", "count": 1},
        ]


class TestStatusOffTheSpan:
    def test_error_status_is_read_off_the_span_even_without_attributes(self, monkeypatch):
        # ``_status_code(attrs and card)`` handed the (empty) attribute dict to
        # the status reader whenever a span carried no attributes, so such a
        # failed root could never be marked error (#413).
        bare = _span("bbbb000000000001", "agent", T0 * NS, 1_000_000, status=2)
        _capture(monkeypatch, {"/api/telemetry/traces": _traces_doc(bare)})
        row = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)["traces"][0]
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}
