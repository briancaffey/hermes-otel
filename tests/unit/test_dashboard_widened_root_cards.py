"""A widened search without a kind (a tool filter) cards the trace's ROOT on
the adapters that build their own span-level cards (#558): Phoenix, SigNoz,
OpenObserve and Uptrace each look the page's roots up in one more query and
keep the matched span as the card's span set. With a kind the matched span
stays the card and no root query is made."""

from __future__ import annotations

from typing import Any, Dict, List

from hermes_otel.dashboard.backends import openobserve, phoenix, signoz
from hermes_otel.dashboard.backends import uptrace as up
from hermes_otel.dashboard.backends.base import StructuredFilter

NS = 1_000_000_000
TOOL_FILTER = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False)
KIND_FILTER = StructuredFilter(name_prefix="tool.", roots_only=False)


def _shown(row: Dict[str, Any]) -> str:
    return row["spanSets"][0]["spans"][0]["name"]


class _Answers:
    """A fake transport answering each call with the next canned answer."""

    def __init__(self, *answers: Any):
        self.answers = list(answers)
        self.calls: List[Any] = []

    def post(self, url, body, headers=None, timeout=None):
        self.calls.append(body)
        return self.answers.pop(0) if self.answers else {}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.answers.pop(0) if self.answers else {}


# ── SigNoz ─────────────────────────────────────────────────────────────


def _signoz_rows(*rows: Dict[str, Any]) -> Dict[str, Any]:
    return {"data": {"result": [{"list": [{"data": r} for r in rows]}]}}


SZ_TOOL = {
    "traceID": "t1",
    "spanID": "c1",
    "parentSpanID": "r1",
    "name": "tool.terminal",
    "serviceName": "hermes-agent",
    "durationNano": 50_000_000,
    "timestamp": "2026-10-05T10:00:03Z",
    "tool.name": "terminal",
}
SZ_ROOT = {
    "traceID": "t1",
    "spanID": "r1",
    "parentSpanID": "",
    "name": "agent",
    "serviceName": "hermes-agent",
    "durationNano": 9_000_000_000,
    "timestamp": "2026-10-05T10:00:00Z",
}


def _signoz():
    return signoz.SigNozAdapter(
        {"type": "signoz", "endpoint": "http://localhost:3301", "api_key": "k"}
    )


class TestSigNoz:
    def test_tool_filter_cards_the_root_from_one_more_query(self, monkeypatch):
        wire = _Answers(_signoz_rows(SZ_TOOL), _signoz_rows(SZ_ROOT))
        monkeypatch.setattr(signoz, "http_post_json", wire.post)
        row = _signoz().search(TOOL_FILTER, 0, 2_000_000_000, 10)["traces"][0]
        assert row["rootTraceName"] == "agent" and row["durationMs"] == 9000
        assert _shown(row) == "tool.terminal"
        q = wire.calls[1]["compositeQuery"]["builderQueries"]["A"]
        items = {i["key"]["key"]: i for i in q["filters"]["items"]}
        assert items["traceID"]["op"] == "in" and items["traceID"]["value"] == ["t1"]
        assert items["parentSpanID"]["value"] == ""

    def test_kind_keeps_the_matched_span_and_asks_for_no_roots(self, monkeypatch):
        wire = _Answers(_signoz_rows(SZ_TOOL))
        monkeypatch.setattr(signoz, "http_post_json", wire.post)
        row = _signoz().search(KIND_FILTER, 0, 2_000_000_000, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and len(wire.calls) == 1

    def test_a_missing_root_keeps_the_matched_card(self, monkeypatch):
        wire = _Answers(_signoz_rows(SZ_TOOL), _signoz_rows())
        monkeypatch.setattr(signoz, "http_post_json", wire.post)
        row = _signoz().search(TOOL_FILTER, 0, 2_000_000_000, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"


# ── OpenObserve ────────────────────────────────────────────────────────

T0 = 1_791_750_000
OO_TOOL = {
    "trace_id": "t1",
    "span_id": "c1",
    "reference_parent_span_id": "r1",
    "operation_name": "tool.terminal",
    "service_name": "hermes-agent",
    "start_time": (T0 + 3) * NS,
    "duration": 50_000,
    "tool_name": "terminal",
}
OO_ROOT = {
    "trace_id": "t1",
    "span_id": "r1",
    "operation_name": "agent",
    "service_name": "hermes-agent",
    "start_time": T0 * NS,
    "duration": 9_000_000,
}


def _oo():
    return openobserve.OpenObserveAdapter(
        {
            "type": "openobserve",
            "endpoint": "http://localhost:5080/api/default/v1/traces",
            "user": "u",
            "password": "p",
        }
    )


class TestOpenObserve:
    def test_tool_filter_cards_the_root_from_one_more_query(self, monkeypatch):
        count = {"hits": [{"trace_id": "t1", "n": 4}]}
        wire = _Answers({"hits": [OO_TOOL]}, {"hits": [OO_ROOT]}, count)
        monkeypatch.setattr(openobserve, "http_post_json", wire.post)
        row = _oo().search(TOOL_FILTER, T0 - 60, T0 + 60, 10)["traces"][0]
        assert row["rootTraceName"] == "agent" and row["durationMs"] == 9000
        assert row["startTimeUnixNano"] == str(T0 * NS) and row["spanCount"] == 4
        assert _shown(row) == "tool.terminal"
        sql = wire.calls[1]["query"]["sql"]
        assert "trace_id IN ('t1')" in sql and "reference_parent_span_id IS NULL" in sql

    def test_kind_keeps_the_matched_span_and_asks_for_no_roots(self, monkeypatch):
        wire = _Answers({"hits": [OO_TOOL]}, {"hits": []})
        monkeypatch.setattr(openobserve, "http_post_json", wire.post)
        row = _oo().search(KIND_FILTER, T0 - 60, T0 + 60, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"
        assert len(wire.calls) == 2 and "GROUP BY" in wire.calls[1]["query"]["sql"]


# ── Uptrace ────────────────────────────────────────────────────────────

UP_TOOL = {
    "id": "c1",
    "traceId": "t1",
    "parentId": "r1",
    "name": "tool.terminal",
    "time": 3000.0,
    "duration": 50,
    "attrs": {"tool_name": "terminal"},
}
UP_ROOT = {
    "id": "r1",
    "traceId": "t1",
    "name": "agent",
    "time": 1000.0,
    "duration": 9000,
    "attrs": {"service_name": "hermes-agent"},
}


def _uptrace():
    a = up.UptraceAdapter({"type": "uptrace", "dsn": "http://project-secret@localhost:14318"})
    a.token = "t"
    return a


def _uptrace_wire(monkeypatch, roots: List[Dict[str, Any]]) -> List[str]:
    """Spans for the search, ``roots`` for the root lookup (``_trace_id in``),
    an empty answer for anything else (the dialect probe)."""
    calls: List[str] = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        if "_trace_id" in url:
            return {"spans": roots}
        if "query=" in url:
            return {"spans": [UP_TOOL]}
        return {}

    monkeypatch.setattr(up, "http_get_json", get)
    return calls


class TestUptrace:
    def test_tool_filter_cards_the_root_from_one_more_query(self, monkeypatch):
        a = _uptrace()
        calls = _uptrace_wire(monkeypatch, [UP_ROOT])
        row = a.search(TOOL_FILTER, 0, 10, 10)["traces"][0]
        assert row["rootTraceName"] == "agent" and row["durationMs"] == 9000
        assert row["rootServiceName"] == "hermes-agent"
        assert _shown(row) == "tool.terminal"
        assert sum("_trace_id+in+%28%22t1%22%29" in c for c in calls) == 1

    def test_kind_keeps_the_matched_span_and_asks_for_no_roots(self, monkeypatch):
        a = _uptrace()
        calls = _uptrace_wire(monkeypatch, [UP_ROOT])
        row = a.search(KIND_FILTER, 0, 10, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"
        assert not any("_trace_id" in c for c in calls)


# ── Phoenix ────────────────────────────────────────────────────────────


def _px_node(sid: str, name: str, start: str, latency_ms: int, parent=None) -> Dict[str, Any]:
    return {
        "spanId": sid,
        "name": name,
        "latencyMs": latency_ms,
        "startTime": start,
        "parentId": parent,
        "attributes": '{"tool": {"name": "terminal"}}' if parent else "{}",
        "context": {"traceId": "t1", "spanId": sid},
        "trace": {"numSpans": 3},
    }


def _px_answer(*nodes: Dict[str, Any]) -> Dict[str, Any]:
    return {"data": {"node": {"name": "p", "spans": {"edges": [{"node": n} for n in nodes]}}}}


class TestPhoenix:
    def _adapter(self, monkeypatch):
        a = phoenix.PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})
        a._project_id_cache = "P1"
        monkeypatch.setattr(a, "_supports_root_args", lambda: False)
        return a

    def test_tool_filter_cards_the_root_from_one_more_query(self, monkeypatch):
        a = self._adapter(monkeypatch)
        tool = _px_node("c1", "tool.terminal", "2026-10-05T10:00:03+00:00", 50, parent="r1")
        root = _px_node("r1", "agent", "2026-10-05T10:00:00+00:00", 9000)
        wire = _Answers(_px_answer(tool), _px_answer(root))
        monkeypatch.setattr(phoenix, "http_post_json", wire.post)
        row = a.search(TOOL_FILTER, 0, 1_800_000_000, 10)["traces"][0]
        assert row["rootTraceName"] == "agent" and row["durationMs"] == 9000
        assert row["spanCount"] == 3 and _shown(row) == "tool.terminal"
        fc = wire.calls[1]["variables"]["filterCondition"]
        assert fc == "parent_id is None and trace_id in ['t1']"

    def test_kind_keeps_the_matched_span_and_asks_for_no_roots(self, monkeypatch):
        a = self._adapter(monkeypatch)
        tool = _px_node("c1", "tool.terminal", "2026-10-05T10:00:03+00:00", 50, parent="r1")
        wire = _Answers(_px_answer(tool))
        monkeypatch.setattr(phoenix, "http_post_json", wire.post)
        row = a.search(KIND_FILTER, 0, 1_800_000_000, 10)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and len(wire.calls) == 1
