"""SigNoz adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

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


class TestSigNoz:
    def _adapter(self):
        return signoz.SigNozAdapter(
            {"type": "signoz", "endpoint": "http://localhost:3301", "api_key": "k"}
        )

    def test_search_parses_rows_into_cards_and_pages(self, monkeypatch):
        rows = [
            {
                "traceID": "t1",
                "spanID": "s1",
                "name": "agent",
                "serviceName": "hermes-agent",
                "durationNano": 2_500_000_000,
                "timestamp": "2026-10-05T10:00:03Z",
                "hasError": False,
                "llm.model_name": "gpt-4o-mini",
                "gen_ai.usage.total_tokens": 42,
                "hermes.session_id": "s-1",
            },
            {
                "traceID": "t1",
                "spanID": "s2",
                "name": "api.m",
                "serviceName": "hermes-agent",
                "durationNano": 1_000,
                "timestamp": "2026-10-05T10:00:02Z",
            },
            {
                "traceID": "t2",
                "spanID": "s3",
                "name": "agent",
                "serviceName": "hermes-agent",
                "durationNano": 1_000_000,
                "timestamp": "2026-10-05T10:00:01Z",
                "hasError": True,
            },
        ]
        seen = {}

        def fake(url, body, headers=None, timeout=None):
            seen["body"] = body
            return {"data": {"result": [{"list": [{"data": r} for r in rows]}]}}

        monkeypatch.setattr(signoz, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 1)
        q = seen["body"]["compositeQuery"]["builderQueries"]["A"]
        assert q["limit"] == 2  # one beyond the page in roots-only mode
        assert {"key": "llm.model_name", "type": "tag", "dataType": "string"} in q["selectColumns"]
        items = {i["key"]["key"]: i for i in q["filters"]["items"]}
        assert items["name"]["op"] == "regex" and items["name"]["value"] == "^agent"
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert out["has_more"] is True and out["next_before_ns"]
        attrs = {
            a["key"]: a["value"] for a in out["traces"][0]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["llm.model_name"] == {"stringValue": "gpt-4o-mini"}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "42"}
        assert attrs["hermes.session_id"] == {"stringValue": "s-1"}
        assert out["traces"][0]["durationMs"] == 2500

    def test_status_ok_is_sent_and_rows_limit_widens_without_roots(self):
        a = self._adapter()
        body = a._build_query_body(StructuredFilter(status="ok", roots_only=False), 0, 1, 10)
        q = body["compositeQuery"]["builderQueries"]["A"]
        items = {i["key"]["key"]: i for i in q["filters"]["items"]}
        assert items["hasError"]["value"] is False and q["limit"] == 41

    def test_get_trace_does_not_retry_unreachable_and_finds_nothing(self, monkeypatch):
        calls = []

        # GET /api/v1/traces/{id} is the span list on every build seen (v0.119
        # answers the SPA page to the POST, #297); an unreachable GET is final.
        def fake_get(url, headers=None, timeout=None):
            calls.append("get")
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(signoz, "http_get_json", fake_get)
        monkeypatch.setattr(signoz, "http_post_json", lambda *a, **k: calls.append("post"))
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().get_trace("t")
        assert calls == ["get"]

        # A GET refused as a request falls back to the POST form once.
        calls.clear()

        def rejected(url, headers=None, timeout=None):
            calls.append("get")
            raise BackendError(502, "Backend returned 405: method not allowed")

        monkeypatch.setattr(signoz, "http_get_json", rejected)
        monkeypatch.setattr(
            signoz, "http_post_json", lambda *a, **k: (calls.append("post"), {"spans": []})[1]
        )
        with pytest.raises(BackendError) as exc:
            self._adapter().get_trace("t")
        assert exc.value.kind == "not_found" and calls == ["get", "post"]

    def test_catalog_is_fetched_once_per_instance_and_counters_are_cumulative(self, monkeypatch):
        gets = []
        catalog = {
            "data": {"attributeKeys": [{"key": "hermes.token.usage", "type": "Sum"}]},
        }

        def fake_get(url, headers=None, timeout=None):
            gets.append(url)
            return catalog

        monkeypatch.setattr(signoz, "http_get_json", fake_get)
        monkeypatch.setattr(
            signoz, "http_post_json", lambda *a, **k: {"data": {"result": [{"series": []}]}}
        )
        a = self._adapter()
        a.metric_names(0, 1)
        out = a.metrics_query("hermes.token.usage", 0, 60, 60)
        a.metrics_query("hermes.token.usage", 0, 60, 60)
        # one autocomplete search per plugin namespace, then cached
        assert len(gets) == len(signoz._CATALOG_NAMESPACES) and out["cumulative"] is True

    def test_status_message_survives_the_row_shape(self):
        cols = ["__time", "SpanId", "TraceId", "ServiceName", "Name", "HasError", "StatusMessage"]
        otlp = signoz._signoz_trace_to_otlp(
            [{"columns": cols, "events": [[1790463141232, "s", "t", "svc", "agent", True, "boom"]]}]
        )
        span = otlp["batches"][0]["scopeSpans"][0]["spans"][0]
        assert span["status"] == {"code": 2, "message": "boom"}
