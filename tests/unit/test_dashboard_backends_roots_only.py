"""Tests for the ``roots_only`` filter on each dashboard backend adapter.

These are backend-only tests — no live containers, no FastAPI router.
Each adapter is exercised with a mocked HTTP layer so we can assert
both (a) the on-the-wire query shape the adapter sends, and
(b) the normalized response it produces.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

# The dashboard backends package isn't a normal Python package (flat
# plugin layout), so point sys.path at the ``dashboard/`` folder the
# Hermes plugin loader uses at runtime.
_HERE = Path(__file__).resolve().parent.parent.parent  # plugin root
_DASHBOARD = _HERE / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))


# ``fastapi`` is a dashboard-side runtime dependency, not a plugin
# dev dep. Stub just enough of it so the adapters import cleanly in
# isolation. HTTPException is the only symbol they touch.
if "fastapi" not in sys.modules:
    _fastapi_stub = types.ModuleType("fastapi")

    class _StubHTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _fastapi_stub.HTTPException = _StubHTTPException  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _fastapi_stub


from backends.base import StructuredFilter  # noqa: E402

# ── Phoenix ────────────────────────────────────────────────────────────


@pytest.fixture()
def phoenix_adapter():
    from backends.phoenix import PhoenixAdapter

    return PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})


def _phoenix_project_list_response() -> Dict[str, Any]:
    return {
        "data": {
            "projects": {
                "edges": [{"node": {"id": "UHJvamVjdDox", "name": "default", "hasTraces": True}}]
            }
        }
    }


def _phoenix_spans_response(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "data": {
            "node": {
                "name": "default",
                "spans": {"edges": [{"node": sp} for sp in spans]},
            }
        }
    }


def _phoenix_span(
    name: str,
    parent_id: str | None = None,
    trace_id: str = "tr1",
    span_id: str = "sp1",
    num_spans: int = 1,
) -> Dict[str, Any]:
    return {
        "spanId": span_id,
        "name": name,
        "latencyMs": 10.0,
        "statusCode": "OK",
        "statusMessage": None,
        "startTime": "2026-01-01T00:00:00+00:00",
        "endTime": "2026-01-01T00:00:00.010000+00:00",
        "parentId": parent_id,
        "spanKind": "agent",
        "attributes": "{}",
        "context": {"traceId": trace_id, "spanId": span_id},
        "input": None,
        "output": None,
        "tokenCountTotal": None,
        "tokenCountPrompt": None,
        "tokenCountCompletion": None,
        "numChildSpans": 0,
        "trace": {"numSpans": num_spans},
    }


class TestPhoenixRootsOnly:
    def test_request_sets_roots_only_and_orphan_flags(self, phoenix_adapter):
        """Adapter must send rootsOnly=true + orphanAsRoot=false
        to Phoenix when the filter's roots_only is True."""
        calls: List[Dict[str, Any]] = []

        def _fake_post(url: str, body: Any, headers=None, timeout=None):
            calls.append(body)
            if "projects(first" in body.get("query", ""):
                return _phoenix_project_list_response()
            return _phoenix_spans_response([_phoenix_span("agent")])

        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            phoenix_adapter.search(StructuredFilter(roots_only=True), 0, 1_000_000, 10)

        # Find the spans query (second call).
        spans_call = [c for c in calls if "SearchSpans" in c.get("query", "")][0]
        variables = spans_call.get("variables") or {}
        assert variables.get("rootsOnly") is True
        assert variables.get("orphanAsRoot") is False

    def test_request_flips_flags_when_roots_only_false(self, phoenix_adapter):
        calls: List[Dict[str, Any]] = []

        def _fake_post(url: str, body: Any, headers=None, timeout=None):
            calls.append(body)
            if "projects(first" in body.get("query", ""):
                return _phoenix_project_list_response()
            return _phoenix_spans_response([_phoenix_span("agent")])

        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            phoenix_adapter.search(StructuredFilter(roots_only=False), 0, 1_000_000, 10)

        spans_call = [c for c in calls if "SearchSpans" in c.get("query", "")][0]
        variables = spans_call.get("variables") or {}
        assert variables.get("rootsOnly") is False
        assert variables.get("orphanAsRoot") is True

    def test_client_filter_drops_non_roots_even_if_phoenix_returns_them(self, phoenix_adapter):
        """Belt-and-suspenders: if Phoenix somehow leaks a span with a
        parentId through the rootsOnly filter, the adapter drops it."""
        spans = [
            _phoenix_span("agent", parent_id=None, trace_id="t1", span_id="s1"),
            _phoenix_span(
                "api.gpt-4",
                parent_id="some-parent-id",
                trace_id="t2",
                span_id="s2",
            ),
            _phoenix_span("cron", parent_id=None, trace_id="t3", span_id="s3"),
        ]

        def _fake_post(url: str, body: Any, headers=None, timeout=None):
            if "projects(first" in body.get("query", ""):
                return _phoenix_project_list_response()
            return _phoenix_spans_response(spans)

        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            result = phoenix_adapter.search(StructuredFilter(roots_only=True), 0, 1_000_000, 10)

        trace_names = [t["rootTraceName"] for t in result["traces"]]
        assert "api.gpt-4" not in trace_names
        assert trace_names == ["agent", "cron"]

    def test_client_filter_keeps_non_roots_when_roots_only_false(self, phoenix_adapter):
        spans = [
            _phoenix_span("agent", parent_id=None, trace_id="t1", span_id="s1"),
            _phoenix_span(
                "api.gpt-4",
                parent_id="parent-x",
                trace_id="t2",
                span_id="s2",
            ),
        ]

        def _fake_post(url: str, body: Any, headers=None, timeout=None):
            if "projects(first" in body.get("query", ""):
                return _phoenix_project_list_response()
            return _phoenix_spans_response(spans)

        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            result = phoenix_adapter.search(StructuredFilter(roots_only=False), 0, 1_000_000, 10)

        trace_names = [t["rootTraceName"] for t in result["traces"]]
        assert "agent" in trace_names
        assert "api.gpt-4" in trace_names


# ── Tempo ──────────────────────────────────────────────────────────────


def _tempo_trace(trace_id: str, root_name: str, matched_names: List[str]) -> Dict[str, Any]:
    return {
        "traceID": trace_id,
        "rootServiceName": "hermes-agent",
        "rootTraceName": root_name,
        "startTimeUnixNano": "1",
        "durationMs": 10,
        "spanSets": [
            {
                "spans": [
                    {"spanID": f"sp-{i}", "name": n, "attributes": []}
                    for i, n in enumerate(matched_names)
                ],
                "matched": len(matched_names),
            }
        ],
    }


class TestTempoRootsOnly:
    def _build_adapter(self):
        from backends.tempo import TempoAdapter

        return TempoAdapter({"type": "lgtm", "endpoint": "http://localhost:4318/v1/traces"})

    def test_drops_traces_when_no_matched_span_is_root(self):
        adapter = self._build_adapter()
        # One trace where the matched span IS the root ("agent"), one
        # where the matched span is "api.gpt-4" but root is "cron".
        traces = [
            _tempo_trace("t-root-agent", "agent", ["agent"]),
            _tempo_trace("t-child-api", "cron", ["api.gpt-4"]),
        ]
        with patch(
            "backends.tempo.http_get_json",
            return_value={"traces": traces, "metrics": {}},
        ):
            result = adapter.search(StructuredFilter(roots_only=True), 0, 1, 50)

        assert [t["traceID"] for t in result["traces"]] == ["t-root-agent"]

    def test_keeps_all_traces_when_roots_only_false(self):
        adapter = self._build_adapter()
        traces = [
            _tempo_trace("t-root-agent", "agent", ["agent"]),
            _tempo_trace("t-child-api", "cron", ["api.gpt-4"]),
        ]
        with patch(
            "backends.tempo.http_get_json",
            return_value={"traces": traces, "metrics": {}},
        ):
            result = adapter.search(StructuredFilter(roots_only=False), 0, 1, 50)

        assert sorted(t["traceID"] for t in result["traces"]) == [
            "t-child-api",
            "t-root-agent",
        ]

    def test_traceql_select_appended(self):
        adapter = self._build_adapter()
        captured = {}

        def _fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            return {"traces": [], "metrics": {}}

        with patch("backends.tempo.http_get_json", side_effect=_fake_get):
            adapter.search(StructuredFilter(roots_only=True), 0, 1, 50)

        from urllib.parse import unquote_plus

        assert "select(" in unquote_plus(captured["url"])


# ── OpenObserve ────────────────────────────────────────────────────────


class TestOpenObserveRootsOnly:
    def _adapter(self):
        from backends.openobserve import OpenObserveAdapter

        return OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )

    def test_sql_includes_root_filter_when_roots_only(self):
        adapter = self._adapter()
        captured: List[Dict[str, Any]] = []

        def _fake_post(url, body, headers=None, timeout=None):
            captured.append(body)
            return {"hits": []}

        with patch("backends.openobserve.http_post_json", side_effect=_fake_post):
            adapter.search(StructuredFilter(roots_only=True), 0, 1, 50)

        # First attempt should include parent-id filter.
        first_sql = captured[0]["query"]["sql"]
        assert "reference_parent_span_id" in first_sql

    def test_sql_skips_root_filter_when_roots_only_false(self):
        adapter = self._adapter()
        captured: List[Dict[str, Any]] = []

        def _fake_post(url, body, headers=None, timeout=None):
            captured.append(body)
            return {"hits": []}

        with patch("backends.openobserve.http_post_json", side_effect=_fake_post):
            adapter.search(StructuredFilter(roots_only=False), 0, 1, 50)

        # Only one attempt, and it should NOT reference parent id.
        assert len(captured) == 1
        sql = captured[0]["query"]["sql"]
        assert "reference_parent_span_id" not in sql
        assert "reference IS NULL" not in sql


# ── SigNoz ─────────────────────────────────────────────────────────────


class TestSigNozRootsOnly:
    def _adapter(self):
        from backends.signoz import SigNozAdapter

        return SigNozAdapter(
            {
                "type": "signoz",
                "endpoint": "http://localhost:3301",
                "api_key": "test-key",
            }
        )

    def test_filter_items_include_parent_span_equals_empty(self):
        adapter = self._adapter()
        filters = adapter._build_filters(StructuredFilter(roots_only=True))
        items = filters["items"]
        has_parent_null = any(
            it["key"]["key"] == "parentSpanID" and it["op"] == "=" and it["value"] == ""
            for it in items
        )
        assert has_parent_null, f"parentSpanID filter missing: {items}"

    def test_filter_items_omit_parent_filter_when_roots_only_false(self):
        adapter = self._adapter()
        filters = adapter._build_filters(StructuredFilter(roots_only=False))
        items = filters["items"]
        assert not any(it["key"]["key"] == "parentSpanID" for it in items)


# ── Uptrace ────────────────────────────────────────────────────────────


class TestUptraceRootsOnly:
    def _adapter(self):
        from backends.uptrace import UptraceAdapter

        return UptraceAdapter(
            {
                "type": "uptrace",
                "dsn": "http://project-secret@localhost:14318",
            }
        )

    def test_uql_includes_parent_id_empty_clause(self):
        adapter = self._adapter()
        uql = adapter._build_uql(StructuredFilter(roots_only=True))
        assert 'where span.parent_id = ""' in uql

    def test_uql_omits_parent_clause_when_roots_only_false(self):
        adapter = self._adapter()
        uql = adapter._build_uql(StructuredFilter(roots_only=False))
        assert "parent_id" not in uql


# ── Jaeger ─────────────────────────────────────────────────────────────


class TestJaegerRootsOnly:
    def _adapter(self):
        from backends.jaeger import JaegerAdapter

        return JaegerAdapter({"type": "jaeger", "endpoint": "http://localhost:16686"})

    @staticmethod
    def _jaeger_trace(trace_id: str, root_has_parent: bool) -> Dict[str, Any]:
        refs = (
            [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": "parent"}]
            if root_has_parent
            else []
        )
        return {
            "traceID": trace_id,
            "spans": [
                {
                    "traceID": trace_id,
                    "spanID": "root",
                    "operationName": "agent",
                    "startTime": 1_000_000,
                    "duration": 1000,
                    "references": refs,
                    "tags": [],
                    "processID": "p1",
                }
            ],
            "processes": {"p1": {"serviceName": "hermes-agent", "tags": []}},
        }

    def test_drops_root_with_child_of_reference_when_roots_only(self):
        adapter = self._adapter()
        data = {
            "data": [
                self._jaeger_trace("t-real-root", root_has_parent=False),
                self._jaeger_trace("t-fake-root", root_has_parent=True),
            ]
        }
        with patch("backends.jaeger.http_get_json", return_value=data):
            result = adapter.search(StructuredFilter(roots_only=True), 0, 1, 50)

        assert [t["traceID"] for t in result["traces"]] == ["t-real-root"]

    def test_keeps_all_when_roots_only_false(self):
        adapter = self._adapter()
        data = {
            "data": [
                self._jaeger_trace("t-real-root", root_has_parent=False),
                self._jaeger_trace("t-fake-root", root_has_parent=True),
            ]
        }
        with patch("backends.jaeger.http_get_json", return_value=data):
            result = adapter.search(StructuredFilter(roots_only=False), 0, 1, 50)

        assert sorted(t["traceID"] for t in result["traces"]) == [
            "t-fake-root",
            "t-real-root",
        ]


# ── Default behavior across all adapters ──────────────────────────────


class TestStructuredFilterDefault:
    def test_roots_only_defaults_to_true(self):
        """Sanity: the StructuredFilter dataclass default must be True
        so adapters opt into root-only behavior by default."""
        assert StructuredFilter().roots_only is True


# ── Ordering: every adapter returns newest first ──────────────────────


class TestOrderingNewestFirst:
    """Regardless of whatever order the backend returned rows in, the
    adapter must re-sort by ``startTimeUnixNano DESC`` so the UI list
    starts with the most recent trace."""

    def test_tempo_sorts_oldest_input_newest_output(self):
        from backends.tempo import TempoAdapter

        adapter = TempoAdapter({"type": "lgtm", "endpoint": "http://localhost:4318/v1/traces"})
        # Backend returned in ascending order — adapter should flip.
        traces = [
            {
                "traceID": "t-old",
                "rootServiceName": "s",
                "rootTraceName": "agent",
                "startTimeUnixNano": "1000000000",
                "durationMs": 1,
                "spanSets": [{"spans": [{"name": "agent"}], "matched": 1}],
            },
            {
                "traceID": "t-new",
                "rootServiceName": "s",
                "rootTraceName": "agent",
                "startTimeUnixNano": "9000000000",
                "durationMs": 1,
                "spanSets": [{"spans": [{"name": "agent"}], "matched": 1}],
            },
        ]
        with patch("backends.tempo.http_get_json", return_value={"traces": traces}):
            result = adapter.search(StructuredFilter(), 0, 1, 10)
        assert [t["traceID"] for t in result["traces"]] == ["t-new", "t-old"]

    def test_phoenix_sends_sort_desc_on_start_time(self):
        from backends.phoenix import PhoenixAdapter

        adapter = PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})
        captured: List[Dict[str, Any]] = []

        def _fake_post(url, body, headers=None, timeout=None):
            captured.append(body)
            if "projects(first" in body.get("query", ""):
                return _phoenix_project_list_response()
            return _phoenix_spans_response([])

        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            adapter.search(StructuredFilter(), 0, 1, 10)

        spans_call = [c for c in captured if "SearchSpans" in c.get("query", "")][0]
        sort = (spans_call.get("variables") or {}).get("sort")
        assert sort == {"col": "startTime", "dir": "desc"}

    def test_signoz_query_orders_by_timestamp_desc(self):
        from backends.signoz import SigNozAdapter

        adapter = SigNozAdapter(
            {"type": "signoz", "endpoint": "http://localhost:3301", "api_key": "k"}
        )
        body = adapter._build_query_body(StructuredFilter(), 0, 1, 10)
        order = body["compositeQuery"]["builderQueries"]["A"]["orderBy"]
        assert order == [{"columnName": "timestamp", "order": "desc"}]

    def test_uptrace_uql_ends_with_order_by_desc(self):
        from backends.uptrace import UptraceAdapter

        adapter = UptraceAdapter({"type": "uptrace", "dsn": "http://secret@localhost:14318"})
        uql = adapter._build_uql(StructuredFilter())
        assert "order by span.time desc" in uql

    def test_openobserve_sql_has_order_by_desc(self):
        from backends.openobserve import OpenObserveAdapter

        adapter = OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )
        captured: List[Dict[str, Any]] = []

        def _fake_post(url, body, headers=None, timeout=None):
            captured.append(body)
            return {"hits": []}

        with patch("backends.openobserve.http_post_json", side_effect=_fake_post):
            adapter.search(StructuredFilter(), 0, 1, 10)

        assert any("ORDER BY _timestamp DESC" in c["query"]["sql"] for c in captured)

    def test_jaeger_sorts_client_side_desc(self):
        from backends.jaeger import JaegerAdapter

        adapter = JaegerAdapter({"type": "jaeger", "endpoint": "http://localhost:16686"})
        # Response in ascending order; adapter should flip to descending.
        data = {
            "data": [
                {
                    "traceID": "t-old",
                    "spans": [
                        {
                            "traceID": "t-old",
                            "spanID": "r1",
                            "operationName": "agent",
                            "startTime": 1_000_000,
                            "duration": 1000,
                            "references": [],
                            "tags": [],
                            "processID": "p1",
                        }
                    ],
                    "processes": {"p1": {"serviceName": "s", "tags": []}},
                },
                {
                    "traceID": "t-new",
                    "spans": [
                        {
                            "traceID": "t-new",
                            "spanID": "r2",
                            "operationName": "agent",
                            "startTime": 9_000_000,
                            "duration": 1000,
                            "references": [],
                            "tags": [],
                            "processID": "p1",
                        }
                    ],
                    "processes": {"p1": {"serviceName": "s", "tags": []}},
                },
            ]
        }
        with patch("backends.jaeger.http_get_json", return_value=data):
            result = adapter.search(StructuredFilter(), 0, 1, 10)
        assert [t["traceID"] for t in result["traces"]] == ["t-new", "t-old"]
