"""OpenObserve adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

from unittest.mock import patch
from urllib import parse as _urlparse

import pytest
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)
from helpers.dashboard import (
    NS,
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


class TestOpenObserve:
    def _adapter(self):
        return openobserve.OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )

    def test_trace_url_opens_the_trace_details_page(self):
        url = self._adapter().trace_url("abc123")
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
        assert url.startswith("http://localhost:5080/web/traces/trace-details?")
        assert (q["org_identifier"], q["stream"], q["trace_id"]) == ("default", "default", "abc123")
        assert int(q["from"]) < int(q["to"])

    def test_an_empty_root_query_is_the_answer(self, monkeypatch):
        sqls = []

        def fake(url, body, headers=None, timeout=None):
            sqls.append(body["query"]["sql"])
            return {"hits": []}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(name_prefix="api."), 0, 10, 5)
        assert out["traces"] == [] and len(sqls) == 1
        assert "operation_name LIKE 'api.%'" in sqls[0] and "reference_parent_span_id" in sqls[0]

    def test_a_rejected_column_falls_through_and_roots_are_kept_client_side(self, monkeypatch):
        sqls = []
        rows = [
            {"trace_id": "t1", "span_id": "r", "operation_name": "agent", "start_time": 20 * NS},
            {
                "trace_id": "t2",
                "span_id": "c",
                "operation_name": "api.m",
                "start_time": 10 * NS,
                "reference": '[{"refType":"CHILD_OF","spanId":"zz"}]',
            },
        ]

        def fake(url, body, headers=None, timeout=None):
            sql = body["query"]["sql"]
            sqls.append(sql)
            if "GROUP BY" in sql:
                return {"hits": []}
            if "reference" in sql:
                raise BackendError(502, "Backend returned 400: unknown field reference")
            return {"hits": rows}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert len([s for s in sqls if "GROUP BY" not in s]) == 3

    def test_unreachable_stops_the_attempt_chain(self, monkeypatch):
        calls = []

        def fake(url, body, headers=None, timeout=None):
            calls.append(1)
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert len(calls) == 1

    def test_like_patterns_escape_underscore_and_percent(self):
        where = self._adapter()._build_where(StructuredFilter(name_regex="tool_x%"))
        assert "operation_name LIKE '%tool\\_x\\%%'" in where

    def test_trace_detail_window_and_not_found(self, monkeypatch):
        seen = {}

        def fake(url, body, headers=None, timeout=None):
            seen["body"] = body
            return {"hits": []}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        a = self._adapter()
        with pytest.raises(BackendError) as exc:
            a.get_trace("abc")
        assert exc.value.kind == "not_found"
        q = seen["body"]["query"]
        assert q["end_time"] - q["start_time"] >= 90 * 86400 * 1_000_000

    def test_metric_samples_keep_the_newest_and_say_when_cut(self, monkeypatch):
        rows = [
            {"_timestamp": 1_700_000_000_000_000 + i * 1_000_000, "value": i, "__name__": "m"}
            for i in range(3)
        ]
        with patch.object(openobserve.OpenObserveAdapter, "_search", return_value=rows) as m:
            out = self._adapter().metrics_query("m", 1_700_000_000, 1_700_000_060, 60)
        assert "ORDER BY _timestamp DESC" in m.call_args[0][0]
        assert out["truncated"] is False and out["cumulative"] is False
        assert sum(v for v in out["series"]["_"] if v) == 3.0

    def test_metric_names_label_the_lifetime_count(self, monkeypatch):
        monkeypatch.setattr(
            openobserve,
            "http_get_json",
            lambda *a, **k: {"list": [{"name": "hermes_token_usage", "stats": {"doc_num": 9}}]},
        )
        assert self._adapter().metric_names(0, 1) == [
            {"name": "hermes_token_usage", "count": 9, "count_scope": "stream"}
        ]
