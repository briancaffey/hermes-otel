"""Backend cards carry the whole-trace span count, or none at all (#179)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

_DASHBOARD = Path(__file__).resolve().parent.parent.parent / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))
try:  # the real FastAPI when the dev extra has it; a stub otherwise
    import fastapi  # noqa: F401
except ImportError:
    _stub = types.ModuleType("fastapi")

    class _StubHTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _stub.HTTPException = _StubHTTPException  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

from backends.base import StructuredFilter  # noqa: E402
from backends.langfuse import LangfuseAdapter  # noqa: E402
from backends.openobserve import OpenObserveAdapter  # noqa: E402
from backends.phoenix import PhoenixAdapter  # noqa: E402


class TestPhoenix:
    def test_span_count_is_the_trace_total_not_matched_spans(self):
        with patch("backends.top_level_config", return_value={}):
            a = PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})
        a._project_id_cache = "P1"
        node = {
            "spanId": "s1",
            "name": "agent",
            "latencyMs": 100,
            "startTime": "2026-09-20T08:00:00+00:00",
            "endTime": "2026-09-20T08:00:00.1+00:00",
            "parentId": None,
            "attributes": "{}",
            "context": {"traceId": "t1", "spanId": "s1"},
            "numChildSpans": 1,
            "trace": {"numSpans": 5},
        }
        with patch.object(
            a, "_gql", return_value={"node": {"name": "p", "spans": {"edges": [{"node": node}]}}}
        ):
            out = a.search(StructuredFilter(), 0, 10, 50)
        (t,) = out["traces"]
        assert t["spanCount"] == 5
        assert len(t["spanSets"][0]["spans"]) == 1


class TestOpenObserve:
    def _adapter(self) -> OpenObserveAdapter:
        return OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )

    def test_grouped_count_query_fills_span_count(self):
        a = self._adapter()
        calls: list = []

        def fake_post(url, body, headers=None, timeout=None):
            calls.append(body["query"]["sql"])
            if "GROUP BY trace_id" in body["query"]["sql"]:
                return {"hits": [{"trace_id": "t1", "n": 7}, {"trace_id": "t2", "n": 3}]}
            return {
                "hits": [
                    {"trace_id": "t1", "span_id": "a", "operation_name": "agent", "start_time": 1},
                    {"trace_id": "t2", "span_id": "b", "operation_name": "agent", "start_time": 2},
                ]
            }

        with patch("backends.openobserve.http_post_json", side_effect=fake_post):
            out = a.search(StructuredFilter(), 0, 10, 50)
        by_id = {t["traceID"]: t for t in out["traces"]}
        assert by_id["t1"]["spanCount"] == 7 and by_id["t2"]["spanCount"] == 3
        assert any("GROUP BY trace_id" in s and "'t1'" in s for s in calls)

    def test_failed_count_query_leaves_span_count_unset(self):
        a = self._adapter()

        def fake_post(url, body, headers=None, timeout=None):
            if "GROUP BY trace_id" in body["query"]["sql"]:
                raise RuntimeError("boom")
            return {"hits": [{"trace_id": "t1", "span_id": "a", "operation_name": "agent"}]}

        with patch("backends.openobserve.http_post_json", side_effect=fake_post):
            out = a.search(StructuredFilter(), 0, 10, 50)
        assert "spanCount" not in out["traces"][0]


class TestLangfuse:
    def _adapter(self) -> LangfuseAdapter:
        return LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3000",
                "public_key": "pk",
                "secret_key": "sk",
            }
        )

    def test_observation_count_and_cost_come_from_the_list(self):
        item: Dict[str, Any] = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-09-20T08:00:00.000Z",
            "latency": 1.5,
            "totalCost": 0.0123,
            "observations": ["o1", "o2", "o3"],
        }
        with patch("backends.langfuse.http_get_json", return_value={"data": [item]}):
            out = self._adapter().search(StructuredFilter(), 0, 10, 50)
        (t,) = out["traces"]
        assert t["spanCount"] == 3
        attrs = {a["key"]: a["value"] for a in t["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["hermes.cost.usage"] == {"doubleValue": 0.0123}
        # No token count or model is claimed: the list endpoint does not carry them.
        assert "gen_ai.usage.total_tokens" not in attrs and "llm.model_name" not in attrs

    def test_zero_cost_is_not_reported_as_a_cost(self):
        item = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-09-20T08:00:00.000Z",
            "totalCost": 0,
        }
        with patch("backends.langfuse.http_get_json", return_value={"data": [item]}):
            out = self._adapter().search(StructuredFilter(), 0, 10, 50)
        attrs = {a["key"] for a in out["traces"][0]["spanSets"][0]["spans"][0]["attributes"]}
        assert "hermes.cost.usage" not in attrs
        assert "spanCount" not in out["traces"][0]


def test_lookback_cap_allows_a_year():
    # plugin_api needs the real FastAPI (APIRouter, Query); read the source.
    src = (_DASHBOARD / "plugin_api.py").read_text(encoding="utf-8")
    assert "lookback_hours: float = Query(1.0, gt=0, le=8760)" in src


def test_langfuse_maps_the_generic_session_key():
    a = LangfuseAdapter(
        {
            "type": "langfuse",
            "endpoint": "http://localhost:3000",
            "public_key": "pk",
            "secret_key": "sk",
        }
    )
    f = StructuredFilter(attr_equals={"hermes.session_id": "s-9"})
    assert a._list_params(f, 0, 10, 5)["sessionId"] == "s-9"


def test_backend_cards_carry_the_session_id():
    from backends import openobserve, phoenix, tempo

    assert "hermes.session_id" in phoenix._CARD_ATTR_KEYS
    assert ".hermes.session_id" in tempo._CARD_SELECT_ATTRS
    assert "hermes.session_id" in openobserve._CARD_ATTR_KEYS
    row = {"operation_name": "agent", "hermes_session_id": "s-1", "hermes_turn_number": "2"}
    attrs = openobserve._row_to_card_attrs(row)
    assert attrs["hermes.session_id"] == "s-1" and attrs["hermes.turn.number"] == 2


class TestTraceUrls:
    def test_phoenix_links_to_the_project_trace_page(self):
        with patch("backends.top_level_config", return_value={}):
            a = PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})
        a._project_id_cache = "UHJvamVjdDoy"
        assert a.trace_url("abc") == "http://localhost:6006/projects/UHJvamVjdDoy/traces/abc"

    def test_langfuse_links_through_the_keys_project(self):
        a = LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3000",
                "public_key": "pk",
                "secret_key": "sk",
            }
        )
        with patch("backends.langfuse.http_get_json", return_value={"data": [{"id": "proj-1"}]}):
            assert a.trace_url("abc") == "http://localhost:3000/project/proj-1/traces/abc"
        with patch("backends.langfuse.http_get_json", side_effect=RuntimeError("down")):
            b = LangfuseAdapter(
                {
                    "type": "langfuse",
                    "endpoint": "http://localhost:3000",
                    "public_key": "pk",
                    "secret_key": "sk",
                }
            )
            assert b.trace_url("abc") is None

    def test_openobserve_offers_no_link(self):
        a = OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )
        assert a.trace_url("abc") is None
