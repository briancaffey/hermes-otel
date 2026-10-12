"""Langfuse adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)

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
    split_applied_filters,
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


class TestLangfuse:
    def _adapter(self, **extra):
        return langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3000",
                "public_key": "pk",
                "secret_key": "sk",
                **extra,
            }
        )

    def test_card_reads_the_root_attributes_from_the_list_metadata(self, monkeypatch):
        item = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-10-07T01:13:00.607Z",
            "latency": 11.052,
            "totalCost": 0,
            "observations": ["a", "b"],
            "input": "Run echo",
            "output": "ok",
            "metadata": {
                "attributes": {
                    "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
                    "gen_ai.usage.total_tokens": "29149",
                    "hermes.session_id": "s1",
                }
            },
        }
        monkeypatch.setattr(langfuse, "http_get_json", lambda *a, **k: {"data": [item]})
        page = self._adapter().search(StructuredFilter(), 0, 10, 5)
        attrs = {
            a["key"]: a["value"] for a in page["traces"][0]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["llm.model_name"] == {"stringValue": "nvidia/nemotron-3-super-120b-a12b"}
        assert attrs["gen_ai.usage.total_tokens"] == {"stringValue": "29149"}
        assert attrs["hermes.session_id"] == {"stringValue": "s1"}
        assert attrs["input.value"] == {"stringValue": "Run echo"}

    def test_detail_restores_the_tool_prefix_langfuse_drops(self, monkeypatch):
        data = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-10-07T01:13:00.607Z",
            "observations": [
                {
                    "id": "r",
                    "type": "AGENT",
                    "name": "agent",
                    "startTime": "2026-10-07T01:13:00.607Z",
                    "endTime": "2026-10-07T01:13:11.000Z",
                },
                {
                    "id": "c",
                    "type": "TOOL",
                    "name": "terminal",
                    "parentObservationId": "r",
                    "startTime": "2026-10-07T01:13:04.411Z",
                    "endTime": "2026-10-07T01:13:05.696Z",
                },
            ],
        }
        monkeypatch.setattr(langfuse, "http_get_json", lambda *a, **k: data)
        detail = self._adapter().get_trace("t1")
        names = [
            sp["name"] for b in detail["batches"] for ss in b["scopeSpans"] for sp in ss["spans"]
        ]
        assert names == ["agent", "tool.terminal"]

    # ── Langfuse v4 (events_only): v2 observations + v2 metrics (#246) ──
    @staticmethod
    def _v4_obs(tid, oid, typ, name, start, latency_s, parent=None, root=False, level="DEFAULT"):
        return {
            "id": oid,
            "traceId": tid,
            "parentObservationId": parent,
            "isRootObservation": root,
            "type": typ,
            "name": name,
            "startTime": start,
            "endTime": start,
            "latency": latency_s,
            "level": level,
            "statusMessage": None,
            "sessionId": "sess-1",
            "environment": "default",
        }

    def _v4_fake(self, monkeypatch, observations, metrics_rows):
        seen = []

        def fake(url, headers=None, timeout=None):
            seen.append(url)
            if "/api/public/traces" in url:
                raise BackendError(
                    404,
                    "Backend returned 404: This endpoint is not available on deployments running in Langfuse v4 events_only mode.",
                    "not_found",
                )
            if "/api/public/v2/metrics" in url:
                return {"data": metrics_rows}
            if "/api/public/v2/observations" in url:
                q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
                rows = observations
                if q.get("traceId"):
                    rows = [o for o in rows if o["traceId"] == q["traceId"]]
                if q.get("type"):
                    rows = [o for o in rows if o["type"] == q["type"]]
                if q.get("sessionId"):
                    rows = [o for o in rows if o["sessionId"] == q["sessionId"]]
                if q.get("level"):
                    rows = [o for o in rows if o["level"] == q["level"]]
                return {"data": rows, "meta": {}}
            if "/api/public/projects" in url:
                return {"data": [{"id": "test-project"}]}
            raise AssertionError(url)

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        return seen

    def _v4_adapter(self):
        return langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3002",
                "public_key": "pk",
                "secret_key": "sk",
            }
        )

    def test_v4_is_detected_once_and_cards_join_the_metrics(self, monkeypatch):
        obs = [
            self._v4_obs(
                "t1", "r1", "AGENT", "agent", "2026-10-09T20:53:10.000Z", 19.094, root=True
            ),
            self._v4_obs(
                "t1", "g1", "GENERATION", "api.m", "2026-10-09T20:53:11.000Z", 1.1, parent="r1"
            ),
            self._v4_obs(
                "t1", "x1", "TOOL", "terminal", "2026-10-09T20:53:12.000Z", 1.0, parent="r1"
            ),
            self._v4_obs("t2", "r2", "AGENT", "agent", "2026-10-09T20:52:00.000Z", 2.0, root=True),
            self._v4_obs(
                "t2", "a2", "AGENT", "sub-agent", "2026-10-09T20:52:01.000Z", 1.0, parent="r2"
            ),
        ]
        metrics = [
            {
                "traceId": "t1",
                "providedModelName": "nvidia/m",
                "sum_totalTokens": 14183,
                "sum_inputTokens": 13985,
                "sum_outputTokens": 198,
                "sum_totalCost": 0,
                "count_count": 3,
            },
            {
                "traceId": "t1",
                "providedModelName": None,
                "sum_totalTokens": 0,
                "sum_inputTokens": 0,
                "sum_outputTokens": 0,
                "sum_totalCost": 0,
                "count_count": 2,
            },
            {
                "traceId": "t2",
                "providedModelName": None,
                "sum_totalTokens": 0,
                "sum_inputTokens": 0,
                "sum_outputTokens": 0,
                "sum_totalCost": 0.5,
                "count_count": 2,
            },
        ]
        seen = self._v4_fake(monkeypatch, obs, metrics)
        a = self._v4_adapter()
        out = a.search(StructuredFilter(), 1_790_000_000, 1_790_500_000, 10)
        assert sum("/api/public/traces" in u for u in seen) == 1  # probed once
        assert a.status()["query_api"] == "v4"
        rows = {t["traceID"]: t for t in out["traces"]}
        assert list(rows) == ["t1", "t2"]  # newest first, the nested AGENT is not a turn
        t1 = rows["t1"]
        attrs = {
            x["key"]: list(x["value"].values())[0]
            for x in t1["spanSets"][0]["spans"][0]["attributes"]
        }
        assert t1["durationMs"] == 19094 and t1["spanCount"] == 5
        assert (
            attrs["gen_ai.usage.total_tokens"] == "14183" and attrs["llm.model_name"] == "nvidia/m"
        )
        assert attrs["hermes.session_id"] == "sess-1"
        t2_attrs = {
            x["key"]: list(x["value"].values())[0]
            for x in rows["t2"]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert "gen_ai.usage.total_tokens" not in t2_attrs and t2_attrs["hermes.cost.usage"] == 0.5
        # the list sent the root type and the window
        q = dict(
            _urlparse.parse_qsl(
                _urlparse.urlparse([u for u in seen if "v2/observations" in u][0]).query
            )
        )
        assert q["type"] == "AGENT" and q["fromStartTime"].endswith("Z") and q["limit"] == "100"
        # per-request filter support: session is server-side on v4, model never

        applied, ignored = split_applied_filters(
            a, StructuredFilter(attr_equals={"hermes.session_id": "sess-1", "llm.model_name": "x"})
        )
        assert applied == ["session", "roots_only"] and ignored == ["model"]
        # a second search makes no probe
        a.search(StructuredFilter(attr_equals={"hermes.session_id": "sess-1"}), 0, 10, 5)
        assert sum("/api/public/traces" in u for u in seen) == 1
        assert "sessionId=sess-1" in seen[-2] or "sessionId=sess-1" in seen[-1]

    def test_v4_kind_filter_lists_the_typed_observations_one_card_per_trace(self, monkeypatch):
        obs = [
            self._v4_obs(
                "t1", "x2", "TOOL", "terminal", "2026-10-09T20:53:13.000Z", 1.0, parent="r1"
            ),
            self._v4_obs(
                "t1", "x1", "TOOL", "terminal", "2026-10-09T20:53:12.000Z", 1.0, parent="r1"
            ),
            self._v4_obs(
                "t3", "x9", "TOOL", "read_file", "2026-10-09T20:50:00.000Z", 1.0, parent="r3"
            ),
        ]
        self._v4_fake(monkeypatch, obs, [])
        out = self._v4_adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False),
            1_790_000_000,
            1_790_500_000,
            10,
        )
        assert [(t["traceID"], t["rootTraceName"]) for t in out["traces"]] == [
            ("t1", "tool.terminal"),
            ("t3", "tool.read_file"),
        ]

    def test_v4_detail_keeps_the_real_root_and_carries_the_totals(self, monkeypatch):
        obs = [
            self._v4_obs("t1", "r1", "AGENT", "agent", "2026-10-09T20:53:10.000Z", 19.0, root=True),
            self._v4_obs(
                "t1", "g1", "GENERATION", "api.m", "2026-10-09T20:53:11.000Z", 1.1, parent="r1"
            ),
            self._v4_obs(
                "t1",
                "x1",
                "TOOL",
                "terminal",
                "2026-10-09T20:53:12.000Z",
                1.0,
                parent="r1",
                level="ERROR",
            ),
        ]
        metrics = [
            {
                "traceId": "t1",
                "providedModelName": "nvidia/m",
                "sum_totalTokens": 100,
                "sum_inputTokens": 90,
                "sum_outputTokens": 10,
                "sum_totalCost": 0,
                "count_count": 3,
            }
        ]
        self._v4_fake(monkeypatch, obs, metrics)
        det = self._v4_adapter().get_trace("t1")
        spans = det["batches"][0]["scopeSpans"][0]["spans"]
        assert det["span_count"] == 3 and [sp["name"] for sp in spans] == [
            "agent",
            "api.m",
            "tool.terminal",
        ]
        root = spans[0]
        assert root["parentSpanId"] is None and not any(
            a["key"] == "synthetic" for a in root["attributes"]
        )
        root_attrs = {a["key"]: list(a["value"].values())[0] for a in root["attributes"]}
        assert (
            root_attrs["gen_ai.usage.total_tokens"] == "100"
            and root_attrs["llm.model_name"] == "nvidia/m"
        )
        assert spans[2]["status"]["code"] == 2 and spans[1]["parentSpanId"] == "r1"
        with pytest.raises(BackendError) as exc:
            self._v4_adapter().get_trace("missing")
        assert exc.value.kind == "not_found"

    def test_query_api_pin_skips_the_probe(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            return {"data": []}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        a = langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3002",
                "public_key": "pk",
                "secret_key": "sk",
                "query_api": "v4",
            }
        )
        a.search(StructuredFilter(), 0, 10, 5)
        assert all(
            "/api/public/v2/observations" in u or "/api/public/v2/metrics" in u for u in calls
        )
        assert a.status()["query_api"] == "v4"

    def test_page_size_is_capped_at_the_api_maximum(self, monkeypatch):
        urls = []

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            return {"data": []}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        self._adapter(query_api="v3").search(StructuredFilter(), 0, 10, 200)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(urls[0]).query))
        assert q["limit"] == "100"

    def test_kind_filter_widens_to_typed_observations(self, monkeypatch):
        urls = []
        obs = {
            "data": [
                {
                    "id": "o1",
                    "traceId": "t1",
                    "type": "TOOL",
                    "name": "terminal",
                    "startTime": "2026-10-07T01:13:07.640Z",
                    "latency": 0.054,
                    "input": {"command": "echo hi"},
                    "output": "hi",
                    "metadata": {"attributes": {"tool.name": "terminal"}},
                },
                {
                    "id": "o2",
                    "traceId": "t1",
                    "type": "TOOL",
                    "name": "terminal",
                    "startTime": "2026-10-07T01:13:06.879Z",
                    "latency": 0.03,
                },
            ]
        }

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            return obs

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        page = self._adapter(query_api="v3").search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 1791335492, 1791335683, 5
        )
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(urls[0]).query))
        assert "/api/public/observations?" in urls[0] and q["type"] == "TOOL"
        assert (q["fromStartTime"], q["toStartTime"]) == (
            "2026-10-07T01:11:32Z",
            "2026-10-07T01:14:43Z",
        )
        # one card per trace, named like the exporter's span, with the tool's I/O
        assert len(page["traces"]) == 1
        row = page["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and row["durationMs"] == 54
        attrs = {a["key"]: a["value"] for a in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}
        assert attrs["input.value"] == {"stringValue": '{"command": "echo hi"}'}

    def test_raw_page_and_limit_cannot_override_the_route(self):
        p = self._adapter()._list_params(
            StructuredFilter(raw="page=9 limit=5000 userId=u"), 0, 10, 7
        )
        assert (p["page"], p["limit"], p["userId"]) == (1, 7, "u")

    def test_duration_filter_walks_pages_and_latency_is_seconds(self, monkeypatch):
        pages = []

        def fake(url, headers=None, timeout=None):
            q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
            pages.append(q["page"])
            page = int(q["page"])
            items = [
                {
                    "id": f"p{page}-{i}",
                    "name": "agent",
                    "timestamp": f"2026-10-05T10:0{page}:0{i}.000Z",
                    "latency": 0.5 if i else 3.0,  # seconds: one slow trace per page
                }
                for i in range(3)
            ]
            return {"data": items}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        out = self._adapter(query_api="v3").search(StructuredFilter(min_duration_ms=1000), 0, 10, 2)
        assert pages == ["1", "2", "3"]
        assert [t["durationMs"] for t in out["traces"]] == [3000, 3000]
        assert out["has_more"] is True

    def test_a_wrong_key_on_the_projects_call_is_an_auth_error(self, monkeypatch):
        def fake(url, headers=None, timeout=None):
            raise BackendError(503, "Backend rejected the credentials (401)", "auth")

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        with pytest.raises(BackendError) as exc:
            self._adapter().trace_url("t")
        assert exc.value.kind == "auth"


# ── #290 leftovers: windows, readers, the home resolver, the status report ──
