"""Zipkin dashboard adapter (#364): request grammar, roots-only re-check, card
and detail shapes. Fixtures are the API v2 answers recorded from
ghcr.io/openzipkin-contrib/zipkin-otel:0.3.0 (Zipkin 3.6.0) after one real
Hermes turn on 2026-10-11, trimmed to the tags the assertions use."""

from __future__ import annotations

from typing import Any, Dict, List
from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import zipkin as zk
from hermes_otel.dashboard.backends.base import BackendError, StructuredFilter

TRACE = "458756ab19446de4c59834744a410f4f"
ROOT_US = 1791745290614894


def _span(
    sid: str,
    name: str,
    ts: int,
    dur: int,
    parent: str | None = None,
    tags: Dict[str, str] | None = None,
    annotations: List[Dict[str, Any]] | None = None,
    kind: str | None = None,
) -> Dict[str, Any]:
    sp: Dict[str, Any] = {
        "traceId": TRACE,
        "id": sid,
        "name": name,
        "timestamp": ts,
        "duration": dur,
        "localEndpoint": {"serviceName": "hermes-agent"},
        "tags": {"otel.status_code": "OK", "otel.scope.name": "hermes-otel-plugin", **(tags or {})},
    }
    if parent:
        sp["parentId"] = parent
    if annotations:
        sp["annotations"] = annotations
    if kind:
        sp["kind"] = kind
    return sp


ROOT = _span(
    "cdc6eb0532b848d0",
    "agent",
    ROOT_US,
    4248414,
    tags={
        "hermes.session_id": "20261011_150127_88b8b9",
        "session.id": "20261011_150127_88b8b9",
        "gen_ai.usage.input_tokens": "25744",
        "gen_ai.usage.total_tokens": "25915",
        "hermes.turn.number": "1",
        "hermes.session.completed": "true",
        "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
        "service.instance.id": "ee2bfedf-266b-4ed2-81ec-cea4f33c67f4",
    },
)
LLM = _span(
    "09ad1eeb8ec31a34",
    "llm.nvidia/nemotron-3-super-120b-a12b",
    ROOT_US + 4945,
    4242810,
    parent="cdc6eb0532b848d0",
    tags={"llm.model_name": "nvidia/nemotron-3-super-120b-a12b", "openinference.span.kind": "LLM"},
)
TOOL = _span(
    "f73558393b58e9fb",
    "tool.terminal",
    ROOT_US + 1481253,
    1202405,
    parent="09ad1eeb8ec31a34",
    tags={"tool.name": "terminal", "openinference.span.kind": "TOOL"},
    annotations=[
        {"timestamp": ROOT_US + 1500000, "value": '"tool.started":{"command":"ls"}'},
        {"timestamp": ROOT_US + 2600000, "value": "tool.finished"},
    ],
    kind="CLIENT",
)
FAILED_TOOL = _span(
    "572020c657816387",
    "tool.read_file",
    ROOT_US + 3374820,
    3349,
    parent="09ad1eeb8ec31a34",
    tags={
        "tool.name": "read_file",
        "otel.status_code": "ERROR",
        "error": "No such file: nope.txt",
        "llm.response.duration_ms": "1442.3",
    },
)
TRACE_SPANS = [LLM, TOOL, FAILED_TOOL, ROOT]  # Zipkin answers in no particular order


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    yield
    backends.clear_caches()


def _adapter(**extra: Any) -> zk.ZipkinAdapter:
    return zk.ZipkinAdapter({"type": "zipkin", "endpoint": "http://localhost:9411", **extra})


def _capture(monkeypatch, answer: Any):
    calls: List[str] = []

    def fake(url: str, headers=None, timeout=10.0):
        calls.append(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(zk, "http_get_json", fake)
    return calls


def _params(url: str) -> Dict[str, List[str]]:
    return _urlparse.parse_qs(_urlparse.urlsplit(url).query)


class TestSetup:
    def test_registered_and_declares_every_filter(self):
        cls = backends.find_adapter_class("zipkin")
        assert cls is zk.ZipkinAdapter
        assert set(cls.filter_support) == set(backends.base.FILTER_KEYS)

    def test_query_url_from_endpoint_origin(self):
        a = _adapter()
        assert a.query_url == "http://localhost:9411"
        assert a.default_service == "hermes-agent"
        st = a.status()
        assert st["query_url"] == "http://localhost:9411"
        assert st["metrics"] is False and st["logs"] is False

    def test_query_port_override_and_full_traces_endpoint(self):
        a = zk.ZipkinAdapter(
            {
                "type": "zipkin",
                "endpoint": "https://zipkin.example.com/v1/traces",
                "query_port": 443,
            }
        )
        assert a.query_url == "https://zipkin.example.com:443"

    def test_trace_url_is_the_ui_route(self):
        assert _adapter().trace_url(TRACE) == f"http://localhost:9411/zipkin/traces/{TRACE}"

    def test_bearer_from_env(self, monkeypatch):
        monkeypatch.setenv("ZK_KEY", "tok")
        a = _adapter(api_key_env="ZK_KEY")
        assert a._headers() == {"Authorization": "Bearer tok"}


class TestTagHelpers:
    def test_coerce_numbers_bools_and_ids(self):
        assert zk.coerce_tag("gen_ai.usage.input_tokens", "25744") == 25744
        assert zk.coerce_tag("llm.response.duration_ms", "1442.3") == 1442.3
        assert zk.coerce_tag("hermes.session.completed", "true") is True
        assert zk.coerce_tag("hermes.session.failed", "false") is False
        assert zk.coerce_tag("hermes.session_id", "20261011") == "20261011"
        assert zk.coerce_tag("service.instance.id", "1234") == "1234"
        assert zk.coerce_tag("llm.model_name", "nvidia/x") == "nvidia/x"

    def test_parse_annotation(self):
        assert zk.parse_annotation("tool.finished") == ("tool.finished", {})
        assert zk.parse_annotation('"tool.started":{"command":"ls"}') == (
            "tool.started",
            {"command": "ls"},
        )
        assert zk.parse_annotation('"broken":{') == ('"broken":{', {})

    def test_annotation_query_grammar(self):
        assert (
            zk.annotation_query({"tool.name": "terminal", "otel.status_code": "ERROR"})
            == "tool.name=terminal and otel.status_code=ERROR"
        )


class TestSearchRequest:
    def test_window_service_limit_and_tags(self, monkeypatch):
        calls = _capture(monkeypatch, [])
        f = StructuredFilter(
            attr_equals={"llm.model_name": "nvidia/nemotron-3-super-120b-a12b"},
            status="error",
            min_duration_ms=250,
            name_regex="agent",
            raw="tool.name=terminal",
        )
        _adapter().search(f, 1_000, 4_600, 25)
        p = _params(calls[0])
        assert calls[0].startswith("http://localhost:9411/api/v2/traces?")
        assert p["serviceName"] == ["hermes-agent"]
        assert p["endTs"] == ["4600000"]
        assert p["lookback"] == ["3600000"]
        assert p["limit"] == ["26"]
        assert p["spanName"] == ["agent"]
        assert p["minDuration"] == ["250000"]
        assert p["annotationQuery"] == [
            "llm.model_name=nvidia/nemotron-3-super-120b-a12b"
            " and tool.name=terminal and otel.status_code=ERROR"
        ]

    def test_cursor_tightens_end_ts(self, monkeypatch):
        calls = _capture(monkeypatch, [])
        f = StructuredFilter(before_ns=2_000_500_000_000)
        _adapter().search(f, 1_000, 4_600, 10)
        p = _params(calls[0])
        assert p["endTs"] == ["2000500"]
        assert p["lookback"] == ["1000500"]
        assert "annotationQuery" not in p

    def test_explicit_service_wins(self, monkeypatch):
        calls = _capture(monkeypatch, [])
        _adapter().search(StructuredFilter(service="other"), 0, 10, 5)
        assert _params(calls[0])["serviceName"] == ["other"]


class TestSearchRows:
    def test_card_from_root_with_coerced_tags(self, monkeypatch):
        _capture(monkeypatch, [TRACE_SPANS])
        page = _adapter().search(StructuredFilter(), 0, 10_000_000_000, 10)
        assert page["has_more"] is False and page["next_before_ns"] is None
        rows = page["traces"]
        assert len(rows) == 1
        row = rows[0]
        assert row["traceID"] == TRACE
        assert row["spanCount"] == 4
        assert row["rootServiceName"] == "hermes-agent"
        assert row["rootTraceName"] == "agent"
        assert row["startTimeUnixNano"] == str(ROOT_US * 1000)
        assert row["durationMs"] == 4248
        attrs = {a["key"]: a["value"] for a in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.input_tokens"] == {"intValue": "25744"}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_150127_88b8b9"}
        assert attrs["name"] == {"stringValue": "agent"}
        assert "status" not in attrs

    def test_roots_only_drops_a_trace_whose_match_is_a_child(self, monkeypatch):
        # annotationQuery matched tool.terminal (a child); the root carries no
        # tool.name, so roots-only must drop the trace.
        _capture(monkeypatch, [TRACE_SPANS])
        f = StructuredFilter(attr_equals={"tool.name": "terminal"})
        assert _adapter().search(f, 0, 10_000_000_000, 10)["traces"] == []

    def test_roots_only_keeps_a_root_match(self, monkeypatch):
        _capture(monkeypatch, [TRACE_SPANS])
        f = StructuredFilter(attr_equals={"hermes.session_id": "20261011_150127_88b8b9"})
        assert len(_adapter().search(f, 0, 10_000_000_000, 10)["traces"]) == 1

    def test_widened_prefix_search_cards_the_first_matching_span(self, monkeypatch):
        _capture(monkeypatch, [TRACE_SPANS])
        f = StructuredFilter(name_prefix="tool.", roots_only=False)
        rows = _adapter().search(f, 0, 10_000_000_000, 10)["traces"]
        assert [r["rootTraceName"] for r in rows] == ["tool.terminal"]
        attrs = {a["key"]: a["value"] for a in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}

    def test_error_root_is_flagged(self, monkeypatch):
        bad_root = dict(ROOT, tags={**ROOT["tags"], "otel.status_code": "ERROR", "error": "boom"})
        _capture(monkeypatch, [[bad_root]])
        rows = _adapter().search(StructuredFilter(), 0, 10_000_000_000, 10)["traces"]
        attrs = {a["key"]: a["value"] for a in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    def test_orphan_trace_is_skipped_when_roots_only(self, monkeypatch):
        _capture(monkeypatch, [[TOOL, FAILED_TOOL]])  # parents outside the window
        assert _adapter().search(StructuredFilter(), 0, 10_000_000_000, 10)["traces"] == []

    def test_paging_keeps_strictly_older_and_reports_has_more(self, monkeypatch):
        older = dict(ROOT, traceId="0" * 31 + "1", id="a" * 16, timestamp=ROOT_US - 10_000_000)
        older_spans = [dict(older)]
        _capture(monkeypatch, [TRACE_SPANS, older_spans])
        f = StructuredFilter(before_ns=(ROOT_US + 1) * 1000)
        page = _adapter().search(f, 0, 10_000_000_000, 1)
        assert [r["traceID"] for r in page["traces"]] == [TRACE]
        assert page["has_more"] is True
        assert page["next_before_ns"] == ROOT_US * 1000


class TestDetail:
    def test_otlp_batches_status_events_kinds(self, monkeypatch):
        calls = _capture(monkeypatch, TRACE_SPANS)
        detail = _adapter().get_trace(TRACE)
        assert calls[0] == f"http://localhost:9411/api/v2/trace/{TRACE}"
        assert detail["span_count"] == 4 and detail["truncated"] is False
        assert len(detail["batches"]) == 1
        batch = detail["batches"][0]
        assert batch["resource"]["attributes"] == [
            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
        ]
        spans = {sp["spanId"]: sp for sp in batch["scopeSpans"][0]["spans"]}
        root = spans["cdc6eb0532b848d0"]
        assert root["parentSpanId"] is None
        assert root["kind"] == 1
        assert root["startTimeUnixNano"] == str(ROOT_US * 1000)
        assert root["endTimeUnixNano"] == str((ROOT_US + 4248414) * 1000)
        assert root["status"] == {"code": 1, "message": ""}
        tool = spans["f73558393b58e9fb"]
        assert tool["parentSpanId"] == "09ad1eeb8ec31a34"
        assert tool["kind"] == 3
        assert [e["name"] for e in tool["events"]] == ["tool.started", "tool.finished"]
        assert tool["events"][0]["timeUnixNano"] == str((ROOT_US + 1500000) * 1000)
        assert tool["events"][0]["attributes"] == [
            {"key": "command", "value": {"stringValue": "ls"}}
        ]
        failed = spans["572020c657816387"]
        assert failed["status"] == {"code": 2, "message": "No such file: nope.txt"}
        attrs = {a["key"]: a["value"] for a in failed["attributes"]}
        assert attrs["llm.response.duration_ms"] == {"doubleValue": 1442.3}
        assert "events" not in failed

    def test_unknown_trace_is_not_found(self, monkeypatch):
        _capture(monkeypatch, BackendError(404, "Backend returned 404: ", "not_found"))
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace("0" * 32)
        assert ei.value.kind == "not_found" and ei.value.status_code == 404

    def test_empty_answer_is_not_found(self, monkeypatch):
        _capture(monkeypatch, [])
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace("0" * 32)
        assert ei.value.kind == "not_found"

    def test_other_errors_propagate(self, monkeypatch):
        _capture(monkeypatch, BackendError(502, "Backend unreachable: refused"))
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace(TRACE)
        assert ei.value.kind == "backend"
