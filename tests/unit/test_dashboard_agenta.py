"""Agenta dashboard adapter (#371): the TracingQuery it builds, the card and
detail shapes from Agenta's nested attribute objects. Shapes recorded from the
OSS stack (api v0.122.x) after one real Hermes turn on 2026-10-11."""

from __future__ import annotations

import copy
from typing import Any, Dict, List

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import agenta as ag
from hermes_otel.dashboard.backends.base import BackendError, ConfigError, StructuredFilter
from hermes_otel.dashboard.backends.timeconv import ns_to_rfc3339

NS = 1_000_000_000
TRACE = "43ee4400ec42281d784993ce6d26cb27"
T0 = 1_791_754_699  # 2026-10-11T21:38:19Z


def _adapter(**extra: Any) -> ag.AgentaAdapter:
    return ag.AgentaAdapter(
        {"type": "agenta", "endpoint": "http://localhost:3060", "api_key": "ak", **extra}
    )


def _span(
    sid: str,
    name: str,
    start: str,
    end: str,
    parent: str | None,
    span_type: str,
    status="STATUS_CODE_OK",
    **attrs,
):
    return {
        "trace_id": TRACE,
        "span_id": sid,
        "parent_id": parent,
        "trace_type": "invocation",
        "span_type": span_type,
        "span_kind": "SPAN_KIND_INTERNAL",
        "span_name": name,
        "start_time": start,
        "end_time": end,
        "status_code": status,
        "attributes": {
            "ag": {
                "type": {"span": span_type, "trace": "invocation"},
                "metrics": {"tokens": {"cumulative": {"total": 23653}}},
                "data": {"inputs": {"prompt": "big"}},
                "meta": {"tool": {"name": "terminal"}},
                "session": {"id": "20261011_173817_3c11b6"},
            },
            **attrs,
        },
    }


ROOT = _span(
    "36db3ca3f8aec389",
    "agent",
    "2026-10-11T21:38:19.000000Z",
    "2026-10-11T21:38:22.884000Z",
    None,
    "agent",
    hermes={"session_id": "20261011_173817_3c11b6", "turn": {"number": 1}},
    gen_ai={"usage": {"total_tokens": 23653, "input_tokens": 23490}},
    llm={"model_name": "nvidia/nemotron-3-super-120b-a12b"},
    input={"value": "read hermes_otel.yaml"},
)
TOOL = _span(
    "d7e786e4c9365323",
    "tool.terminal",
    "2026-10-11T21:38:21.382649Z",
    "2026-10-11T21:38:22.294476Z",
    "36db3ca3f8aec389",
    "tool",
    tool={"name": "terminal"},
)
FAILED = _span(
    "aaaa000000000002",
    "tool.read_file",
    "2026-10-11T21:38:22.500000Z",
    "2026-10-11T21:38:22.503000Z",
    "36db3ca3f8aec389",
    "tool",
    status="STATUS_CODE_ERROR",
    tool={"name": "read_file"},
)


def _capture(monkeypatch, answers: List[Any]):
    calls: List[Dict[str, Any]] = []
    queue = list(answers)

    def fake(url, body, headers=None, timeout=10.0):
        calls.append({"url": url, "body": copy.deepcopy(body), "headers": headers})
        ans = queue.pop(0) if queue else {"count": 0, "spans": []}
        if isinstance(ans, Exception):
            raise ans
        return copy.deepcopy(ans)

    monkeypatch.setattr(ag, "http_post_json", fake)
    return calls


class TestSetup:
    def test_registered_and_configured(self):
        assert backends.find_adapter_class("agenta") is ag.AgentaAdapter
        assert set(ag.AgentaAdapter.filter_support) == set(backends.base.FILTER_KEYS)
        a = _adapter()
        assert a.api_url == "http://localhost:3060/api"
        assert a._headers() == {"Authorization": "ApiKey ak"}
        st = a.status()
        assert (
            st["metrics"] is False
            and st["logs"] is False
            and st["query_url"] == "http://localhost:3060/api"
        )
        assert a.trace_url(TRACE) is None
        assert (
            _adapter(ui_url="http://localhost:3060/w/x/p/y/observability/").trace_url(TRACE)
            == "http://localhost:3060/w/x/p/y/observability"
        )

    def test_cloud_default_and_env_key(self, monkeypatch):
        monkeypatch.setenv("OTEL_AGENTA_API_KEY", "envkey")
        a = ag.AgentaAdapter({"type": "agenta", "region": "eu"})
        assert a.api_url == "https://eu.cloud.agenta.ai/api"
        assert a._headers() == {"Authorization": "ApiKey envkey"}

    def test_missing_key_is_a_config_error(self, monkeypatch):
        monkeypatch.delenv("OTEL_AGENTA_API_KEY", raising=False)
        monkeypatch.delenv("AGENTA_API_KEY", raising=False)
        a = ag.AgentaAdapter({"type": "agenta", "endpoint": "http://localhost:3060"})
        with pytest.raises(ConfigError):
            a.search(StructuredFilter(), T0, T0 + 1, 5)


class TestHelpers:
    def test_flatten_and_iso(self):
        from hermes_otel.dashboard.backends.shapes import flatten_dotted

        assert flatten_dotted(
            {"gen_ai": {"usage": {"total_tokens": 1}}, "session_id": "s", "e": {}}, keep_empty=True
        ) == {
            "gen_ai.usage.total_tokens": 1,
            "session_id": "s",
            "e": {},
        }

    def test_attrs_drop_agenta_copies_but_keep_metrics(self):
        attrs = ag.AgentaAdapter._attrs(ROOT)
        assert attrs["gen_ai.usage.total_tokens"] == 23653
        assert attrs["ag.metrics.tokens.cumulative.total"] == 23653
        assert attrs["ag.type.span"] == "agent"
        assert not any(k.startswith(("ag.data", "ag.meta")) for k in attrs)


class TestQuery:
    def test_conditions_and_windowing(self, monkeypatch):
        calls = _capture(monkeypatch, [{"count": 0, "spans": []}])
        f = StructuredFilter(
            name_regex="agent",
            name_prefix="age",
            attr_equals={"hermes.session_id": "s1", "tool.name": "terminal"},
            status="error",
            free_text="hello",
            raw="ag.type.span=agent",
            before_ns=(T0 + 30) * NS + 500_000,
        )
        _adapter().search(f, T0 - 60, T0 + 60, 10)
        body = calls[0]["body"]
        assert calls[0]["url"] == "http://localhost:3060/api/spans/query"
        assert calls[0]["headers"] == {"Authorization": "ApiKey ak"}
        assert body["formatting"] == {"focus": "span", "format": "agenta"}
        assert body["windowing"]["oldest"] == ns_to_rfc3339((T0 - 60) * NS, digits=6)
        assert body["windowing"]["newest"] == ns_to_rfc3339(
            (T0 + 30) * NS + 500_000, digits=6
        )  # the cursor tightens newest
        assert body["windowing"]["limit"] == 11 and body["windowing"]["order"] == "descending"
        conds = body["filtering"]["conditions"]
        assert body["filtering"]["operator"] == "and"
        assert {"field": "parent_id", "operator": "is", "value": None} in conds
        assert {"field": "span_name", "operator": "is", "value": "agent"} in conds
        assert {"field": "span_name", "operator": "startswith", "value": "age"} in conds
        assert {
            "field": "attributes",
            "operator": "eq",
            "key": "hermes.session_id",
            "value": "s1",
        } in conds
        assert {
            "field": "attributes",
            "operator": "eq",
            "key": "tool.name",
            "value": "terminal",
        } in conds
        assert {
            "field": "attributes",
            "operator": "eq",
            "key": "ag.type.span",
            "value": "agent",
        } in conds
        assert {"field": "status_code", "operator": "is", "value": "STATUS_CODE_ERROR"} in conds
        assert not any(c.get("operator") == "or" for c in conds)
        assert {
            "field": "attributes",
            "operator": "contains",
            "key": "input.value",
            "value": "hello",
        } in conds
        conds2 = calls[1]["body"]["filtering"]["conditions"]
        assert {
            "field": "attributes",
            "operator": "contains",
            "key": "output.value",
            "value": "hello",
        } in conds2
        assert conds2[:-1] == conds[:-1]

    def test_free_text_merges_both_targets(self, monkeypatch):
        other = copy.deepcopy(ROOT)
        other["trace_id"] = "2" * 32
        other["span_id"] = "bbbb000000000001"
        other["start_time"] = "2026-10-11T21:38:25.000000Z"
        other["end_time"] = "2026-10-11T21:38:26.000000Z"
        calls = _capture(
            monkeypatch,
            [
                {"count": 1, "spans": [ROOT]},
                {"count": 2, "spans": [ROOT, other]},
                {"count": 2, "spans": [ROOT, other]},
            ],
        )
        rows = _adapter().search(StructuredFilter(free_text="big"), T0 - 60, T0 + 60, 10)["traces"]
        assert [r["traceID"] for r in rows] == ["2" * 32, TRACE]  # newest first, ROOT counted once
        assert calls[2]["body"]["filtering"]["conditions"][0]["field"] == "trace_id"

    def test_status_ok_and_widened_fetch(self, monkeypatch):
        calls = _capture(monkeypatch, [{"count": 0, "spans": []}])
        _adapter().search(StructuredFilter(status="ok", roots_only=False), T0, T0 + 60, 10)
        body = calls[0]["body"]
        assert body["windowing"]["limit"] == 44
        assert {"field": "status_code", "operator": "is_not", "value": "STATUS_CODE_ERROR"} in body[
            "filtering"
        ]["conditions"]
        assert not any(c.get("field") == "parent_id" for c in body["filtering"]["conditions"])

    def test_no_filters_means_no_filtering_block_except_roots(self, monkeypatch):
        calls = _capture(monkeypatch, [{"count": 0, "spans": []}])
        _adapter().search(StructuredFilter(roots_only=False), T0, T0 + 60, 10)
        assert "filtering" not in calls[0]["body"]


class TestRows:
    def test_card_counts_and_typed_values(self, monkeypatch):
        calls = _capture(
            monkeypatch,
            [{"count": 1, "spans": [ROOT]}, {"count": 3, "spans": [ROOT, TOOL, FAILED]}],
        )
        page = _adapter().search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        # the second query counts the page's spans by trace id
        assert calls[1]["body"]["filtering"]["conditions"] == [
            {"field": "trace_id", "operator": "in", "value": [TRACE]}
        ]
        row = page["traces"][0]
        assert row["traceID"] == TRACE and row["spanCount"] == 3
        assert row["rootTraceName"] == "agent" and row["rootServiceName"] == "hermes-agent"
        assert row["startTimeUnixNano"] == str(T0 * NS)
        assert row["durationMs"] == 3884
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "23653"}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_173817_3c11b6"}
        assert "status" not in attrs and page["has_more"] is False

    def test_widened_rows_card_the_earliest_matching_span(self, monkeypatch):
        _capture(
            monkeypatch,
            [{"count": 2, "spans": [FAILED, TOOL]}, {"count": 3, "spans": [ROOT, TOOL, FAILED]}],
        )
        rows = _adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        assert [r["rootTraceName"] for r in rows] == ["tool.terminal"]
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}

    def test_widened_without_a_kind_cards_the_root_and_keeps_the_match(self, monkeypatch):
        # #558: a tool filter widens to any span, but the row is the trace's root.
        _capture(
            monkeypatch,
            [{"count": 2, "spans": [FAILED, TOOL]}, {"count": 3, "spans": [ROOT, TOOL, FAILED]}],
        )
        f = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False)
        row = _adapter().search(f, T0 - 60, T0 + 60, 10)["traces"][0]
        assert row["rootTraceName"] == "agent" and row["startTimeUnixNano"] == str(T0 * NS)
        assert row["durationMs"] == 3884 and row["spanCount"] == 3
        shown = row["spanSets"][0]["spans"][0]
        assert shown["name"] == "tool.terminal"
        attrs = {x["key"]: x["value"] for x in shown["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}
        # The root missing from the trace's spans: the matched span stays the card.
        _capture(
            monkeypatch,
            [{"count": 1, "spans": [TOOL]}, {"count": 2, "spans": [TOOL, FAILED]}],
        )
        row = _adapter().search(f, T0 - 60, T0 + 60, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"

    def test_error_card_and_min_duration_client_side(self, monkeypatch):
        _capture(monkeypatch, [{"count": 1, "spans": [FAILED]}, {"count": 1, "spans": [FAILED]}])
        rows = _adapter().search(
            StructuredFilter(status="error", roots_only=False), T0 - 60, T0 + 60, 10
        )["traces"]
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}
        _capture(monkeypatch, [{"count": 1, "spans": [ROOT]}])
        assert (
            _adapter().search(StructuredFilter(min_duration_ms=5000), T0 - 60, T0 + 60, 10)[
                "traces"
            ]
            == []
        )

    def test_paging_cursor(self, monkeypatch):
        older = copy.deepcopy(ROOT)
        older["trace_id"] = "1" * 32
        older["start_time"] = "2026-10-11T21:30:00.000000Z"
        older["end_time"] = "2026-10-11T21:30:01.000000Z"
        _capture(
            monkeypatch,
            [{"count": 2, "spans": [ROOT, older]}, {"count": 2, "spans": [ROOT, older]}],
        )
        page = _adapter().search(StructuredFilter(before_ns=(T0 + 1) * NS), T0 - 3600, T0 + 60, 1)
        assert [r["traceID"] for r in page["traces"]] == [TRACE]
        assert page["has_more"] is True and page["next_before_ns"] == T0 * NS


class TestDetail:
    def test_otlp_batch_from_flattened_spans(self, monkeypatch):
        calls = _capture(monkeypatch, [{"count": 3, "spans": [ROOT, TOOL, FAILED]}])
        d = _adapter().get_trace(TRACE)
        assert calls[0]["body"]["filtering"]["conditions"] == [
            {"field": "trace_id", "operator": "is", "value": TRACE}
        ]
        assert calls[0]["body"]["windowing"]["order"] == "ascending"
        assert d["span_count"] == 3 and d["truncated"] is False
        batch = d["batches"][0]
        assert batch["resource"]["attributes"] == [
            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
        ]
        spans = {s["spanId"]: s for s in batch["scopeSpans"][0]["spans"]}
        root = spans["36db3ca3f8aec389"]
        assert root["parentSpanId"] is None and root["kind"] == 1 and root["status"]["code"] == 1
        assert root["startTimeUnixNano"] == str(T0 * NS)
        tool = spans["d7e786e4c9365323"]
        assert tool["parentSpanId"] == "36db3ca3f8aec389"
        assert tool["startTimeUnixNano"] == "1791754701382649000"
        assert spans["aaaa000000000002"]["status"]["code"] == 2
        attrs = {x["key"]: x["value"] for x in root["attributes"]}
        assert attrs["hermes.turn.number"] == {"intValue": "1"}
        assert "ag.data.inputs.prompt" not in attrs and attrs["ag.type.span"] == {
            "stringValue": "agent"
        }

    def test_not_found_and_backend_errors(self, monkeypatch):
        _capture(monkeypatch, [{"count": 0, "spans": []}])
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace("0" * 32)
        assert ei.value.kind == "not_found"
        _capture(monkeypatch, [BackendError(503, "Backend rejected the credentials (401)", "auth")])
        with pytest.raises(BackendError) as ei:
            _adapter().get_trace(TRACE)
        assert ei.value.kind == "auth"
