"""Jaeger adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

import json
from urllib import parse as _urlparse

import pytest
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import (
    jaeger,
    langfuse,
    openobserve,
    phoenix,
    signoz,
    tempo,
    uptrace,
)
from hermes_otel.dashboard.backends.base import (
    BackendError,
    StructuredFilter,
)

MODULES = {
    "phx": phoenix,
    "lf": langfuse,
    "jg": jaeger,
    "lgtm": tempo,
    "sz": signoz,
    "up": uptrace,
    "oo": openobserve,
}


# ── declarations and helpers ───────────────────────────────────────────


class TestJaeger:
    def _adapter(self):
        return jaeger.JaegerAdapter({"type": "jaeger", "endpoint": "http://localhost:16686"})

    @staticmethod
    def _trace(tid, root_tags, child_tags, root_name="agent", start=1_000_000):
        def tag(k, v):
            return {"key": k, "type": "string", "value": v}

        return {
            "traceID": tid,
            "spans": [
                {
                    "traceID": tid,
                    "spanID": "root",
                    "operationName": root_name,
                    "startTime": start,
                    "duration": 5000,
                    "references": [],
                    "tags": [tag(k, v) for k, v in root_tags.items()],
                    "processID": "p1",
                },
                {
                    "traceID": tid,
                    "spanID": "child",
                    "operationName": "tool.terminal",
                    "startTime": start + 10,
                    "duration": 100,
                    "references": [{"refType": "CHILD_OF", "traceID": tid, "spanID": "root"}],
                    "tags": [tag(k, v) for k, v in child_tags.items()],
                    "processID": "p1",
                },
            ],
            "processes": {"p1": {"serviceName": "hermes-agent", "tags": []}},
        }

    def test_roots_only_requires_the_root_itself_to_match(self, monkeypatch):
        data = {
            "data": [
                self._trace("t-root-has-it", {"tool.name": "terminal"}, {}),
                self._trace("t-only-child-has-it", {}, {"tool.name": "terminal"}),
            ]
        }
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: data)
        f = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=True)
        out = self._adapter().search(f, 0, 10, 10)
        assert [t["traceID"] for t in out["traces"]] == ["t-root-has-it"]
        widened = self._adapter().search(
            StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False), 0, 10, 10
        )
        assert len(widened["traces"]) == 2

    def test_widened_search_cards_are_the_matched_spans(self, monkeypatch):
        data = {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [
                        {
                            "spanID": "r",
                            "operationName": "agent",
                            "startTime": 1_000_000,
                            "duration": 9_000_000,
                            "tags": [],
                            "references": [],
                        },
                        {
                            "spanID": "c",
                            "operationName": "tool.terminal",
                            "startTime": 2_000_000,
                            "duration": 50_000,
                            "tags": [{"key": "tool.name", "type": "string", "value": "terminal"}],
                            "references": [{"refType": "CHILD_OF", "spanID": "r", "traceID": "t1"}],
                        },
                    ],
                    "processes": {},
                }
            ]
        }
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: data)
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 10
        )
        row = page["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and row["durationMs"] == 50
        assert row["spanSets"][0]["spans"][0]["name"] == "tool.terminal"
        # roots-only keeps the root as the card
        page = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 10)
        assert page["traces"][0]["rootTraceName"] == "agent"

    def test_name_prefix_is_checked_on_the_rows_and_cursor_bounds_the_query(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {
                "data": [
                    self._trace("cron-trace", {}, {}, root_name="cron", start=3_000_000),
                    self._trace("agent-trace", {}, {}, root_name="agent", start=2_000_000),
                ]
            }

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        out = self._adapter().search(
            StructuredFilter(name_prefix="agent", before_ns=2_500_000_000), 0, 10, 5
        )
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert (q["limit"], q["end"], q["service"]) == ("6", "2500000", "hermes-agent")
        assert [t["traceID"] for t in out["traces"]] == ["agent-trace"]

    def test_status_comes_from_the_otel_tags_and_bad_numbers_do_not_crash(self, monkeypatch):
        t = self._trace("t", {"otel.status_code": "ERROR", "otel.status_description": "boom"}, {})
        t["spans"][0]["startTime"] = "not-a-number"
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: {"data": [t]})
        detail = self._adapter().get_trace("t")
        root = detail["batches"][0]["scopeSpans"][0]["spans"][0]
        assert root["status"] == {"code": 2, "message": "boom"}
        assert root["startTimeUnixNano"] == "0" and detail["span_count"] == 2
        card = self._adapter().search(StructuredFilter(), 0, 10, 5)["traces"][0]
        attrs = {a["key"]: a["value"] for a in card["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    # ── Jaeger v2: the v3 query API (#245) ──────────────────────────
    @staticmethod
    def _v3_result(*traces):
        """OTLP JSON as ``/api/v3/traces`` answers it: one resourceSpans per
        trace, hex ids, ``status.code`` 1/2, attributes as AnyValues."""
        rs = []
        for tid, spans in traces:
            rs.append(
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": tid,
                                    "spanId": sid,
                                    "parentSpanId": parent,
                                    "name": name,
                                    "startTimeUnixNano": str(start),
                                    "endTimeUnixNano": str(start + dur),
                                    "attributes": [
                                        {"key": k, "value": {"stringValue": v}}
                                        for k, v in attrs.items()
                                    ],
                                    "status": status,
                                }
                                for sid, parent, name, start, dur, attrs, status in spans
                            ]
                        }
                    ],
                }
            )
        return {"result": {"resourceSpans": rs}}

    def _v3_fake(self, monkeypatch, search_result, trace_result=None):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/api/services"):
                # Jaeger v2: the classic routes are 404
                raise BackendError(404, "Backend returned 404: 404 page not found", "not_found")
            if url.endswith("/api/v3/services"):
                return {"services": ["jaeger", "hermes-agent"]}
            if "/api/v3/traces?" in url:
                if isinstance(search_result, Exception):
                    raise search_result
                return search_result
            if "/api/v3/traces/" in url:
                if isinstance(trace_result, Exception):
                    raise trace_result
                return trace_result
            raise AssertionError(f"unexpected v1 call {url}")

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        return calls

    def test_v3_is_probed_once_and_search_speaks_the_v3_grammar(self, monkeypatch):
        t_root = (
            "a" * 32,
            [
                (
                    "r1",
                    None,
                    "agent",
                    2_000_000_000_000,
                    5_000_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
                (
                    "c1",
                    "r1",
                    "tool.terminal",
                    2_000_000_000_010,
                    100_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
            ],
        )
        t_child = (
            "b" * 32,
            [
                (
                    "r2",
                    None,
                    "agent",
                    1_000_000_000_000,
                    5_000_000,
                    {},
                    {"code": 2, "message": "boom"},
                ),
                (
                    "c2",
                    "r2",
                    "tool.terminal",
                    1_000_000_000_010,
                    100_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
            ],
        )
        calls = self._v3_fake(monkeypatch, self._v3_result(t_root, t_child))
        a = self._adapter()
        out = a.search(StructuredFilter(attr_equals={"tool.name": "terminal"}), 0, 3000, 5)
        assert calls[0].endswith("/api/services") and calls[1].endswith("/api/v3/services")
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(calls[2]).query))
        assert q["query.serviceName"] == "hermes-agent" and q["query.searchDepth"] == "6"
        assert q["query.startTimeMin"] == "1970-01-01T00:00:00.000000000Z"
        assert q["query.startTimeMax"] == "1970-01-01T00:50:00.000000000Z"
        assert json.loads(q["query.attributes"]) == {"tool.name": "terminal"}
        # roots-only re-checks the root: trace b matches only through its child
        assert [t["traceID"] for t in out["traces"]] == ["a" * 32]
        row = out["traces"][0]
        assert (
            row["spanCount"] == 2
            and row["durationMs"] == 5
            and row["rootServiceName"] == "hermes-agent"
        )
        # the probe is cached: a second search makes no services call
        a.search(StructuredFilter(status="error"), 0, 3000, 5)
        assert sum(u.endswith("/api/v3/services") for u in calls) == 1
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(calls[-1]).query))
        assert json.loads(q["query.attributes"]) == {"error": "true"}
        # status comes from the OTLP status on v3 rows
        out = a.search(StructuredFilter(), 0, 3000, 5)
        by_id = {t["traceID"]: t for t in out["traces"]}
        attrs = {
            x["key"]: x["value"] for x in by_id["b" * 32]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["status"] == {"stringValue": "error"}

    def test_v3_empty_search_is_a_page_and_an_unknown_trace_is_not_found(self, monkeypatch):
        self._v3_fake(
            monkeypatch,
            BackendError(404, "Backend returned 404: No traces found", "not_found"),
            BackendError(404, "Backend returned 404: No traces found", "not_found"),
        )
        a = self._adapter()
        page = a.search(StructuredFilter(), 0, 3000, 5)
        assert page == {"traces": [], "next_before_ns": None, "has_more": False}
        with pytest.raises(BackendError) as exc:
            a.get_trace("f" * 32)
        assert exc.value.kind == "not_found"

    def test_v3_detail_passes_resource_spans_through(self, monkeypatch):
        t = (
            "c" * 32,
            [
                ("r1", None, "agent", 2_000_000_000_000, 5_000_000, {}, {"code": 1}),
                (
                    "c1",
                    "r1",
                    "api.x",
                    2_000_000_000_010,
                    100_000,
                    {"llm.model_name": "m"},
                    {"code": 1},
                ),
            ],
        )
        self._v3_fake(monkeypatch, {}, self._v3_result(t))
        det = self._adapter().get_trace("c" * 32)
        assert det["span_count"] == 2 and det["truncated"] is False
        spans = det["batches"][0]["scopeSpans"][0]["spans"]
        assert spans[1]["parentSpanId"] == "r1" and spans[1]["status"] == {"code": 1}
        assert self._adapter().trace_url("c" * 32) == "http://localhost:16686/trace/" + "c" * 32

    def test_query_api_pin_skips_the_probe_and_v1_stays_v1(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/api/v3/services"):
                # Jaeger 1.x all-in-one serves this too (snake_case params):
                # the classic API must still win when it answers
                return {"services": ["hermes-agent"]}
            return {"data": []}

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        pinned = jaeger.JaegerAdapter(
            {"type": "jaeger", "endpoint": "http://localhost:16686", "query_api": "v1"}
        )
        pinned.search(StructuredFilter(), 0, 10, 5)
        assert "/api/traces?" in calls[-1] and not any("v3" in u for u in calls)
        # auto on a v1 server: one classic probe answers, no v3 probe, cached
        a = self._adapter()
        a.search(StructuredFilter(), 0, 10, 5)
        a.search(StructuredFilter(), 0, 10, 5)
        assert sum(u.endswith("/api/services") for u in calls) == 1
        assert not any(u.endswith("/api/v3/services") for u in calls)
        assert all("/api/traces?" in u for u in calls if "?" in u)
        assert a.status()["query_api"] == "v1"
        # an unreachable server is reported, never guessed
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        monkeypatch.setattr(
            jaeger,
            "http_get_json",
            lambda *a, **k: (_ for _ in ()).throw(
                BackendError(502, "Backend unreachable", "backend")
            ),
        )
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)

    def test_default_service_follows_the_plugin_resource(self, monkeypatch):
        monkeypatch.setattr(
            backends, "top_level_config", lambda: {"resource_attributes": {"service.name": "bot"}}
        )
        assert self._adapter().default_service == "bot"
        assert (
            jaeger.JaegerAdapter({"type": "jaeger", "service_name": "svc"}).default_service == "svc"
        )
